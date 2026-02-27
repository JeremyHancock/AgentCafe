"""Passport system — JWT-based agent authentication and authorization.

Implements the Passport System Design Document (Phase 2.0).
See docs/passport/design.md for the full specification.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from agentcafe.db.engine import get_db

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

    Returns (success, error_code) where error_code is empty on success.
    """
    # Step 1: Decode and verify signature, expiry, issuer, audience
    try:
        payload = jwt.decode(
            passport_token,
            _state.signing_secret,
            algorithms=["HS256"],
            issuer="agentcafe",
            audience="agentcafe",
            options={"require": ["exp", "iat", "jti", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        return False, "passport_expired"
    except jwt.InvalidTokenError:
        return False, "passport_invalid"

    # Step 2: Check jti is not revoked
    jti = payload.get("jti")
    if not jti:
        return False, "passport_invalid"

    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM revoked_jtis WHERE jti = ?", (jti,)
    )
    if await cursor.fetchone():
        return False, "passport_revoked"

    # Step 3: Scope check (required for ALL actions)
    scopes = payload.get("scopes", [])
    if not _check_scope(scopes, service_id, action_id):
        return False, "scope_missing"

    # Step 4: Authorization check (only if human_auth_required)
    if human_auth_required:
        authorizations = payload.get("authorizations", [])
        if not _check_authorization_entry(authorizations, service_id, action_id):
            return False, "human_auth_required"

    return True, ""


# ---------------------------------------------------------------------------
# Issuance endpoint
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

    token = jwt.encode(payload, _state.signing_secret, algorithm="HS256")

    return IssueResponse(
        passport=token,
        expires_at=exp.isoformat(),
    )


# ---------------------------------------------------------------------------
# Revocation endpoint
# ---------------------------------------------------------------------------

@passport_router.post("/cafe/revoke")
async def revoke_passport(req: RevokeRequest):
    """Revoke a Passport by adding its jti to the blacklist.

    Accepts the full passport token, extracts the jti, and blacklists it.
    """
    # Decode without full verification — we just need the jti
    try:
        payload = jwt.decode(
            req.passport,
            _state.signing_secret,
            algorithms=["HS256"],
            options={"verify_exp": False, "verify_aud": False},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_token", "message": "Could not decode the passport."},
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
