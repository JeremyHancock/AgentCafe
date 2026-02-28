"""Consent flow — agent-initiated consent, human approval, token exchange/refresh.

Implements v2-spec.md §4 (Consent Flow), §5 (Consent Lifecycle),
§6 (Token Lifecycle), and §11 (API Endpoints).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from agentcafe.cafe.human import validate_human_session
from agentcafe.db.engine import get_db

logger = logging.getLogger("agentcafe.consent")

consent_router = APIRouter(tags=["consent"])

_DEFAULT_CONSENT_TTL_HOURS = 72
_MAX_CONSENT_TTL_HOURS = 168  # 7 days
_MAX_ACTIVE_TOKENS_PER_POLICY = 20

# Risk-tier token lifetime ceilings (v2-spec.md §6.2)
_RISK_TIER_CEILINGS = {
    "low": 3600,       # 60 minutes
    "medium": 900,     # 15 minutes
    "high": 300,       # 5 minutes
    "critical": 0,     # single-use (0 = one request only)
}
_RISK_TIER_DEFAULTS = {
    "low": 1800,       # 30 minutes
    "medium": 600,     # 10 minutes
    "high": 0,         # single-use
    "critical": 0,     # single-use
}


class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""

_state = _State()


def configure_consent(signing_secret: str) -> None:
    """Set the signing secret for Tier-2 token issuance. Called once at startup."""
    _state.signing_secret = signing_secret


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class InitiateRequest(BaseModel):
    """Request body for POST /consents/initiate."""
    service_id: str
    action_id: str
    requested_constraints: dict | None = None
    task_summary: str | None = None
    callback_url: str | None = None
    ttl_hours: float = Field(default=_DEFAULT_CONSENT_TTL_HOURS, gt=0, le=_MAX_CONSENT_TTL_HOURS)


class InitiateResponse(BaseModel):
    """Response body for POST /consents/initiate."""
    consent_id: str
    consent_url: str
    status: str = "pending"
    expires_at: str


class ConsentStatusResponse(BaseModel):
    """Response body for GET /consents/{consent_id}/status."""
    consent_id: str
    status: str
    policy_id: str | None = None
    expires_at: str


class ApproveRequest(BaseModel):
    """Request body for POST /consents/{consent_id}/approve."""
    token_lifetime_seconds: int | None = None


class ApproveResponse(BaseModel):
    """Response body for POST /consents/{consent_id}/approve."""
    consent_id: str
    status: str
    policy_id: str


class ExchangeRequest(BaseModel):
    """Request body for POST /tokens/exchange."""
    consent_id: str


class TokenResponse(BaseModel):
    """Response body for POST /tokens/exchange and POST /tokens/refresh."""
    token: str
    expires_at: str
    policy_id: str
    tier: str = "write"
    scopes: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_agent_passport(authorization: str) -> dict:
    """Extract and validate the agent's Passport from the Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_passport"})
    token = authorization[7:]
    try:
        payload = jwt.decode(
            token,
            _state.signing_secret,
            algorithms=["HS256"],
            issuer="agentcafe",
            audience="agentcafe",
            options={"require": ["exp", "iat", "jti", "sub", "iss", "aud"]},
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail={"error": "passport_expired"}) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail={"error": "passport_invalid"}) from exc


async def _count_active_tokens(db, policy_id: str) -> int:
    """Count non-expired active tokens for a policy."""
    now = datetime.now(timezone.utc).isoformat()
    # Clean up expired tokens while we're at it
    await db.execute(
        "DELETE FROM active_tokens WHERE expires_at < ?", (now,)
    )
    cursor = await db.execute(
        "SELECT COUNT(*) FROM active_tokens WHERE policy_id = ?", (policy_id,)
    )
    row = await cursor.fetchone()
    return row[0]


def _issue_tier2_token(
    policy_id: str,
    email: str,
    scopes: list[str],
    risk_tier: str,
    lifetime_seconds: int,
    agent_tag: str | None = None,
) -> tuple[str, str, str]:
    """Issue a Tier-2 write token. Returns (token, expires_at_iso, jti)."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=lifetime_seconds) if lifetime_seconds > 0 else now + timedelta(seconds=30)
    jti = str(uuid.uuid4())

    # Derive authorizations from scopes (format: "service_id:action_id")
    authorizations = []
    for scope in scopes:
        parts = scope.split(":", 1)
        if len(parts) == 2:
            authorizations.append({"service_id": parts[0], "action_id": parts[1]})

    payload = {
        "iss": "agentcafe",
        "sub": f"user:{email}",
        "aud": "agentcafe",
        "exp": exp,
        "iat": now,
        "jti": jti,
        "tier": "write",
        "granted_by": "human_consent",
        "policy_id": policy_id,
        "scopes": scopes,
        "authorizations": authorizations,
        "risk_tier": risk_tier,
        "agent_tag": agent_tag,
    }

    token = jwt.encode(payload, _state.signing_secret, algorithm="HS256")
    return token, exp.isoformat(), jti


# ---------------------------------------------------------------------------
# POST /consents/initiate — Agent requests consent
# ---------------------------------------------------------------------------

@consent_router.post("/consents/initiate", response_model=InitiateResponse)
async def initiate_consent(
    req: InitiateRequest,
    authorization: str = Header(default=""),
):
    """Agent initiates a consent request for a specific service action.

    Requires a valid Passport (Tier-1 or Tier-2) in the Authorization header.
    Returns a consent_id and consent_url for the human to approve.
    """
    _validate_agent_passport(authorization)

    db = await get_db()

    # Verify the service and action exist
    cursor = await db.execute(
        "SELECT 1 FROM proxy_configs WHERE service_id = ? AND action_id = ?",
        (req.service_id, req.action_id),
    )
    if not await cursor.fetchone():
        raise HTTPException(
            status_code=404,
            detail={"error": "action_not_found", "message": f"No action '{req.action_id}' for service '{req.service_id}'."},
        )

    consent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=req.ttl_hours)
    scope = f"{req.service_id}:{req.action_id}"

    await db.execute(
        """INSERT INTO consents
           (id, service_id, action_ids, requested_scopes, requested_constraints_json,
            task_summary, callback_url, status, expires_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (
            consent_id, req.service_id, req.action_id, scope,
            str(req.requested_constraints) if req.requested_constraints else None,
            req.task_summary, req.callback_url,
            expires_at.isoformat(), now.isoformat(), now.isoformat(),
        ),
    )
    await db.commit()

    consent_url = f"/consent/{consent_id}"

    return InitiateResponse(
        consent_id=consent_id,
        consent_url=consent_url,
        expires_at=expires_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /consents/{consent_id}/status — Agent polls consent status
# ---------------------------------------------------------------------------

@consent_router.get("/consents/{consent_id}/status", response_model=ConsentStatusResponse)
async def get_consent_status(consent_id: str):
    """Check the status of a consent request."""
    db = await get_db()

    cursor = await db.execute(
        "SELECT id, status, policy_id, expires_at FROM consents WHERE id = ?",
        (consent_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "consent_not_found"})

    # Check if expired
    if row["status"] == "pending":
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            await db.execute(
                "UPDATE consents SET status = 'expired', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), consent_id),
            )
            await db.commit()
            raise HTTPException(status_code=410, detail={"error": "consent_expired"})

    return ConsentStatusResponse(
        consent_id=row["id"],
        status=row["status"],
        policy_id=row["policy_id"],
        expires_at=row["expires_at"],
    )


# ---------------------------------------------------------------------------
# POST /consents/{consent_id}/approve — Human approves consent
# ---------------------------------------------------------------------------

@consent_router.post("/consents/{consent_id}/approve", response_model=ApproveResponse)
async def approve_consent(
    consent_id: str,
    req: ApproveRequest,
    authorization: str = Header(default=""),
):
    """Human approves a consent request, creating a policy.

    Requires a valid human session token (aud: human-dashboard).
    MVP: direct API call. Production: consent page UI with passkey.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_session"})
    human_payload = validate_human_session(authorization[7:])
    user_id = human_payload["user_id"]
    email = human_payload["sub"].removeprefix("user:")

    db = await get_db()

    # Load consent
    cursor = await db.execute(
        "SELECT * FROM consents WHERE id = ?", (consent_id,),
    )
    consent = await cursor.fetchone()
    if not consent:
        raise HTTPException(status_code=404, detail={"error": "consent_not_found"})

    if consent["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail={"error": "consent_not_pending", "message": f"Consent is '{consent['status']}', not 'pending'."},
        )

    # Check expiry
    expires_at = datetime.fromisoformat(consent["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        await db.execute(
            "UPDATE consents SET status = 'expired', updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), consent_id),
        )
        await db.commit()
        raise HTTPException(status_code=410, detail={"error": "consent_expired"})

    # Look up risk tier from proxy config
    cursor = await db.execute(
        "SELECT risk_tier FROM proxy_configs WHERE service_id = ? AND action_id = ?",
        (consent["service_id"], consent["action_ids"]),
    )
    proxy_row = await cursor.fetchone()
    risk_tier = proxy_row["risk_tier"] if proxy_row else "medium"

    # Determine token lifetime
    ceiling = _RISK_TIER_CEILINGS.get(risk_tier, 900)
    default = _RISK_TIER_DEFAULTS.get(risk_tier, 600)
    lifetime = req.token_lifetime_seconds if req.token_lifetime_seconds is not None else default
    if ceiling > 0 and lifetime > ceiling:
        lifetime = ceiling

    # Create policy
    policy_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    policy_expires = now + timedelta(days=30)

    await db.execute(
        """INSERT INTO policies
           (id, cafe_user_id, service_id, allowed_action_ids, scopes,
            risk_tier, max_token_lifetime_seconds, expires_at,
            revoked_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
        (
            policy_id, user_id, consent["service_id"],
            consent["action_ids"], consent["requested_scopes"],
            risk_tier, lifetime,
            policy_expires.isoformat(), now.isoformat(), now.isoformat(),
        ),
    )

    # Update consent with approval
    await db.execute(
        """UPDATE consents
           SET status = 'approved', cafe_user_id = ?, policy_id = ?, updated_at = ?
           WHERE id = ?""",
        (user_id, policy_id, now.isoformat(), consent_id),
    )
    await db.commit()

    logger.info("Consent %s approved by user %s → policy %s", consent_id, email, policy_id)

    return ApproveResponse(
        consent_id=consent_id,
        status="approved",
        policy_id=policy_id,
    )


# ---------------------------------------------------------------------------
# POST /tokens/exchange — Agent exchanges approved consent for Tier-2 token
# ---------------------------------------------------------------------------

@consent_router.post("/tokens/exchange", response_model=TokenResponse)
async def exchange_token(
    req: ExchangeRequest,
    authorization: str = Header(default=""),
):
    """Exchange an approved consent_id for a short-lived Tier-2 write token.

    Requires a valid agent Passport in the Authorization header.
    """
    agent_payload = _validate_agent_passport(authorization)
    agent_tag = agent_payload.get("agent_tag")

    db = await get_db()

    # Load consent
    cursor = await db.execute(
        "SELECT * FROM consents WHERE id = ?", (req.consent_id,),
    )
    consent = await cursor.fetchone()
    if not consent:
        raise HTTPException(status_code=404, detail={"error": "consent_not_found"})

    if consent["status"] == "expired":
        raise HTTPException(status_code=410, detail={"error": "consent_expired"})
    if consent["status"] != "approved":
        raise HTTPException(
            status_code=409,
            detail={"error": "consent_not_approved", "message": f"Consent is '{consent['status']}', not 'approved'."},
        )

    policy_id = consent["policy_id"]

    # Load policy
    cursor = await db.execute(
        "SELECT * FROM policies WHERE id = ?", (policy_id,),
    )
    policy = await cursor.fetchone()
    if not policy:
        raise HTTPException(status_code=500, detail={"error": "policy_missing"})

    if policy["revoked_at"] is not None:
        raise HTTPException(status_code=401, detail={"error": "policy_revoked"})

    # Enforce concurrent token cap
    active_count = await _count_active_tokens(db, policy_id)
    if active_count >= _MAX_ACTIVE_TOKENS_PER_POLICY:
        raise HTTPException(
            status_code=429,
            detail={"error": "policy_token_limit_reached", "message": f"Maximum {_MAX_ACTIVE_TOKENS_PER_POLICY} active tokens per policy."},
        )

    # Issue Tier-2 token
    scopes = policy["scopes"].split(",") if "," in policy["scopes"] else [policy["scopes"]]
    email = policy["cafe_user_id"]

    # Look up the actual email from cafe_users
    cursor = await db.execute(
        "SELECT email FROM cafe_users WHERE id = ?", (email,)
    )
    user_row = await cursor.fetchone()
    user_email = user_row["email"] if user_row else email

    lifetime = policy["max_token_lifetime_seconds"]
    risk_tier = policy["risk_tier"]

    token, expires_at, jti = _issue_tier2_token(
        policy_id=policy_id,
        email=user_email,
        scopes=scopes,
        risk_tier=risk_tier,
        lifetime_seconds=lifetime,
        agent_tag=agent_tag,
    )

    # Track active token
    await db.execute(
        "INSERT INTO active_tokens (jti, policy_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (jti, policy_id, expires_at, datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()

    return TokenResponse(
        token=token,
        expires_at=expires_at,
        policy_id=policy_id,
        scopes=scopes,
    )


# ---------------------------------------------------------------------------
# POST /tokens/refresh — Agent refreshes a Tier-2 token
# ---------------------------------------------------------------------------

@consent_router.post("/tokens/refresh", response_model=TokenResponse)
async def refresh_token(authorization: str = Header(default="")):
    """Refresh a Tier-2 token under the same policy. Non-consuming.

    The old token is NOT invalidated — it dies at expiry.
    Returns a new token under the same policy.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_passport"})

    token_str = authorization[7:]
    try:
        payload = jwt.decode(
            token_str,
            _state.signing_secret,
            algorithms=["HS256"],
            issuer="agentcafe",
            audience="agentcafe",
            options={"require": ["exp", "iat", "jti", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail={"error": "passport_expired"}) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail={"error": "passport_invalid"}) from exc

    if payload.get("tier") != "write":
        raise HTTPException(
            status_code=403,
            detail={"error": "tier_insufficient", "message": "Only Tier-2 (write) tokens can be refreshed."},
        )

    policy_id = payload.get("policy_id")
    if not policy_id:
        raise HTTPException(status_code=400, detail={"error": "no_policy_id"})

    db = await get_db()

    # Check policy is still active
    cursor = await db.execute(
        "SELECT * FROM policies WHERE id = ?", (policy_id,),
    )
    policy = await cursor.fetchone()
    if not policy:
        raise HTTPException(status_code=404, detail={"error": "policy_not_found"})

    if policy["revoked_at"] is not None:
        raise HTTPException(status_code=401, detail={"error": "policy_revoked"})

    # Check policy expiry
    policy_expires = datetime.fromisoformat(policy["expires_at"])
    if policy_expires.tzinfo is None:
        policy_expires = policy_expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > policy_expires:
        raise HTTPException(status_code=401, detail={"error": "policy_expired"})

    # Enforce concurrent token cap
    active_count = await _count_active_tokens(db, policy_id)
    if active_count >= _MAX_ACTIVE_TOKENS_PER_POLICY:
        raise HTTPException(
            status_code=429,
            detail={"error": "policy_token_limit_reached", "message": f"Maximum {_MAX_ACTIVE_TOKENS_PER_POLICY} active tokens per policy."},
        )

    # Issue new token
    scopes = payload.get("scopes", [])
    email = payload["sub"].removeprefix("user:")
    agent_tag = payload.get("agent_tag")
    risk_tier = policy["risk_tier"]
    lifetime = policy["max_token_lifetime_seconds"]

    new_token, expires_at, jti = _issue_tier2_token(
        policy_id=policy_id,
        email=email,
        scopes=scopes,
        risk_tier=risk_tier,
        lifetime_seconds=lifetime,
        agent_tag=agent_tag,
    )

    # Track active token
    await db.execute(
        "INSERT INTO active_tokens (jti, policy_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (jti, policy_id, expires_at, datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()

    return TokenResponse(
        token=new_token,
        expires_at=expires_at,
        policy_id=policy_id,
        scopes=scopes,
    )
