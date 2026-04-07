"""Passport system — JWT-based agent authentication and authorization.

Implements Passport V1 (Phase 2) and Passport V2 Tier-1 read Passports.
See docs/passport/design.md (V1) and docs/passport/v2-spec.md (V2)."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agentcafe.db.engine import get_db
from agentcafe.keys import sign_passport_token, decode_passport_token

logger = logging.getLogger("agentcafe.passport")

passport_router = APIRouter(tags=["passport"])

class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""
    issuer_api_key: str = ""

_state = _State()


def configure_passport(signing_secret: str, issuer_api_key: str) -> None:
    """Set the signing secret and issuer API key. Called once at startup."""
    _state.signing_secret = signing_secret
    _state.issuer_api_key = issuer_api_key


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AuthorizationLimit(BaseModel):
    """Limits within an authorization entry."""
    valid_until: str | None = None
    # Service-specific limits are passed through as-is
    model_config = {"extra": "allow"}


class AuthorizationEntry(BaseModel):
    """A human-granted mandate for a specific service+action."""
    service_id: str
    action_id: str
    limits: dict | None = None


# ---------------------------------------------------------------------------
# IP-based rate limiter for /passport/register
# ---------------------------------------------------------------------------

_REGISTER_RATE_LIMIT = 30  # max requests per window
_REGISTER_RATE_WINDOW = 60  # window in seconds
_register_hits: dict[str, list[float]] = defaultdict(list)


def _check_register_rate_limit(client_ip: str) -> tuple[bool, int]:
    """Check if the client IP has exceeded the registration rate limit.

    Returns (allowed, retry_after_seconds). Cleans up expired entries.
    """
    now = time.monotonic()
    cutoff = now - _REGISTER_RATE_WINDOW
    hits = _register_hits[client_ip]
    # Prune old entries
    _register_hits[client_ip] = hits = [t for t in hits if t > cutoff]
    if len(hits) >= _REGISTER_RATE_LIMIT:
        oldest = hits[0]
        retry_after = int(oldest - cutoff) + 1
        return False, max(retry_after, 1)
    hits.append(now)
    return True, 0


class RegisterRequest(BaseModel):
    """Request body for POST /passport/register (Tier-1)."""
    agent_tag: str = Field(min_length=1, description="Required self-reported agent label. Used for audit trail and rate-limit grouping.")


class RegisterResponse(BaseModel):
    """Response body for POST /passport/register (Tier-1)."""
    passport: str
    expires_at: str
    tier: str = "read"
    agent_handle: str


class IssueRequest(BaseModel):
    """Request body for POST /passport/issue."""
    human_id: str
    agent_id: str
    scopes: list[str]
    authorizations: list[AuthorizationEntry] = []
    duration_hours: float = 24.0


class IssueResponse(BaseModel):
    """Response body for POST /passport/issue."""
    passport: str
    expires_at: str


class RevokeRequest(BaseModel):
    """Request body for POST /cafe/revoke."""
    passport: str


# ---------------------------------------------------------------------------
# JWT validation functions (replace _validate_passport_mvp)
# ---------------------------------------------------------------------------

def _check_scope(
    scopes: list[str], service_id: str, action_id: str
) -> bool:
    """Check if the requested service_id:action_id is covered by the token scopes.

    Supports exact match and {service_id}:* wildcard.
    """
    required = f"{service_id}:{action_id}"
    wildcard = f"{service_id}:*"
    return required in scopes or wildcard in scopes


def _check_authorization_entry(
    authorizations: list[dict], service_id: str, action_id: str
) -> bool:
    """Check if there is a matching authorization entry for this service+action.

    Also enforces the universal `valid_until` limit if present.
    """
    for auth in authorizations:
        if auth.get("service_id") == service_id and auth.get("action_id") == action_id:
            # Check valid_until if present
            limits = auth.get("limits") or {}
            valid_until = limits.get("valid_until")
            if valid_until:
                try:
                    deadline = datetime.fromisoformat(valid_until)
                    if deadline.tzinfo is None:
                        deadline = deadline.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) > deadline:
                        return False
                except (ValueError, TypeError):
                    return False
            return True
    return False


async def validate_passport_jwt(
    passport_token: str, service_id: str, action_id: str, human_auth_required: bool
) -> tuple[bool, str]:
    """Validate a JWT passport token and check scope + authorization.

    Supports both Tier-1 (read) and Tier-2 (write) Passports.
    Tier-1 tokens can access actions where human_auth_required=False.
    Tier-2 tokens follow full scope + authorization checks.

    Returns (success, error_code) where error_code is empty on success.
    """
    # Step 1: Decode and verify signature, expiry, issuer, audience (RS256 + HS256 legacy)
    try:
        payload = decode_passport_token(passport_token)
    except jwt.ExpiredSignatureError:
        return False, "passport_expired"
    except jwt.InvalidTokenError:
        return False, "passport_invalid"

    # Step 2: Check jti is not revoked (V1 per-token revocation)
    jti = payload.get("jti")
    if not jti:
        return False, "passport_invalid"

    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM revoked_jtis WHERE jti = ?", (jti,)
    )
    if await cursor.fetchone():
        return False, "passport_revoked"

    # Step 2b: Policy revocation check (V2 — instant revocation for all tiers)
    policy_id = payload.get("policy_id")
    if policy_id:
        cursor = await db.execute(
            "SELECT revoked_at FROM policies WHERE id = ?", (policy_id,)
        )
        policy_row = await cursor.fetchone()
        if policy_row and policy_row["revoked_at"] is not None:
            revoked_at = datetime.fromisoformat(policy_row["revoked_at"])
            token_iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
            if token_iat < revoked_at:
                return False, "policy_revoked"

    # Step 3: Tier check — Tier-1 (read) tokens cannot access write actions
    tier = payload.get("tier", "write")
    if tier == "read" and human_auth_required:
        return False, "tier_insufficient"

    # Step 4: Tier-1 tokens skip scope checks (they can access all read actions)
    if tier == "read":
        return True, ""

    # Step 5: Scope check (required for Tier-2 actions)
    scopes = payload.get("scopes", [])
    if not _check_scope(scopes, service_id, action_id):
        # Implicit read: write tokens may access read-only actions on the same service
        service_prefix = f"{service_id}:"
        if human_auth_required or not any(s.startswith(service_prefix) for s in scopes):
            return False, "scope_missing"

    # Step 6: Authorization check (only if human_auth_required)
    if human_auth_required:
        authorizations = payload.get("authorizations", [])
        if not _check_authorization_entry(authorizations, service_id, action_id):
            return False, "human_auth_required"

    return True, ""


# ---------------------------------------------------------------------------
# Tier-1 registration endpoint (V2)
# ---------------------------------------------------------------------------

_TIER1_LIFETIME_HOURS = 3


@passport_router.post("/passport/register", response_model=RegisterResponse)
async def register_agent(req: RegisterRequest, request: Request):
    """Register an agent and receive a Tier-1 read-only Passport.

    Requires a non-empty agent_tag. IP-based rate limiting enforced
    (default 30/min). The Cafe returns a rate-limited read Passport
    with a hashed agent handle for tracking.

    See docs/passport/v2-spec.md §3.1.
    """
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = _check_register_rate_limit(client_ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "message": f"Too many registration requests. Try again in {retry_after}s.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
    tag = req.agent_tag
    agent_handle = hashlib.sha256(
        f"agent:{tag}:{uuid.uuid4()}".encode()
    ).hexdigest()[:16]

    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=_TIER1_LIFETIME_HOURS)

    payload = {
        "iss": "agentcafe",
        "sub": f"agent:{agent_handle}",
        "aud": "agentcafe",
        "exp": exp,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "tier": "read",
        "granted_by": "self",
        "agent_tag": tag if tag else None,
    }

    token = sign_passport_token(payload)

    return RegisterResponse(
        passport=token,
        expires_at=exp.isoformat(),
        agent_handle=agent_handle,
    )


# ---------------------------------------------------------------------------
# V1 Issuance endpoint (will be superseded by V2 consent flow)
# ---------------------------------------------------------------------------

@passport_router.post("/passport/issue", response_model=IssueResponse)
async def issue_passport(
    req: IssueRequest,
    x_api_key: str = Header(alias="X-Api-Key"),
):
    """Issue a new JWT Passport.

    Protected by ISSUER_API_KEY for MVP. Phase 3 will integrate with
    the Company Onboarding Wizard and human consent flow.
    """
    if not _state.issuer_api_key or x_api_key != _state.issuer_api_key:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_api_key", "message": "Invalid or missing API key."},
        )

    if req.duration_hours <= 0 or req.duration_hours > 24:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_duration",
                "message": "duration_hours must be between 0 and 24.",
            },
        )

    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=req.duration_hours)

    payload = {
        "iss": "agentcafe",
        "sub": f"user:{req.human_id}",
        "aud": "agentcafe",
        "exp": exp,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "agent_id": req.agent_id,
        "scopes": req.scopes,
        "authorizations": [a.model_dump() for a in req.authorizations],
        "human_consent": True,
    }

    token = sign_passport_token(payload)

    return IssueResponse(
        passport=token,
        expires_at=exp.isoformat(),
    )


# ---------------------------------------------------------------------------
# Revocation endpoint
# ---------------------------------------------------------------------------

@passport_router.post("/cafe/revoke")
async def revoke_passport(
    req: RevokeRequest,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    """Revoke a Passport by adding its jti to the blacklist.

    Authentication required:
    - Admin: provide X-Api-Key header matching ISSUER_API_KEY
    - Self-revoke: the passport token must have a valid cryptographic signature
      (proof-of-possession — only the holder of a legitimately issued token can revoke it)
    """
    is_admin = bool(x_api_key and _state.issuer_api_key and x_api_key == _state.issuer_api_key)

    if is_admin:
        # Admin path — decode without strict verification (can revoke expired tokens)
        try:
            payload = decode_passport_token(
                req.passport,
                options={"verify_exp": False, "verify_aud": False, "require": []},
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_token", "message": "Could not decode the passport."},
            ) from exc
    else:
        # Self-revoke path — signature MUST be valid (proof-of-possession)
        try:
            payload = decode_passport_token(
                req.passport,
                options={"verify_exp": False, "verify_aud": False, "require": ["jti"]},
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized_revocation",
                    "message": "Revocation requires a valid token signature (self-revoke) or X-Api-Key header (admin).",
                },
            ) from exc

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_jti", "message": "Passport has no jti claim."},
        )

    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO revoked_jtis (jti, revoked_at) VALUES (?, ?)",
        (jti, datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()

    return {"status": "revoked", "jti": jti}
