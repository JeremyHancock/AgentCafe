"""Company Cards — standing policies for service-level relationships.

A Company Card lets a human pre-authorize a class of actions with a service,
subject to constraints (budget, duration, scope, excluded actions).
Once approved, the agent can repeatedly request tokens without human interaction.

See docs/strategy/strategic-review-briefing.md §8.1 and ADR-028.
"""

from __future__ import annotations

import logging
import secrets
import string
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from agentcafe.cafe.human import validate_human_session, verify_passkey_assertion
from agentcafe.db.engine import get_db
from agentcafe.keys import sign_passport_token, decode_passport_token

logger = logging.getLogger("agentcafe.cards")

cards_router = APIRouter(tags=["cards"])

_DEFAULT_CARD_DURATION_DAYS = 30
_MAX_CARD_DURATION_DAYS = 365
_MAX_ACTIVE_TOKENS_PER_CARD = 20

# Risk tiers that a card can cover automatically.
# High and critical always require per-action consent.
_CARD_COVERABLE_TIERS = {"low", "medium"}

# Risk-tier token lifetime ceilings (same as consent.py)
_RISK_TIER_CEILINGS = {
    "low": 3600,
    "medium": 900,
    "high": 300,
    "critical": 0,
}
_RISK_TIER_DEFAULTS = {
    "low": 1800,
    "medium": 600,
    "high": 0,
    "critical": 0,
}
_RISK_TIER_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_ACTIVATION_CODE_CHARS = string.ascii_uppercase + string.digits


class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""
    public_url: str = ""

_state = _State()


def configure_cards(signing_secret: str, public_url: str = "") -> None:
    """Set the signing secret for card-based token issuance. Called once at startup."""
    _state.signing_secret = signing_secret
    _state.public_url = public_url.rstrip("/")


def _generate_activation_code() -> str:
    """Generate an 8-char alphanumeric activation code."""
    return "".join(secrets.choice(_ACTIVATION_CODE_CHARS) for _ in range(8))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CardRequestBody(BaseModel):
    """Request body for POST /cards/request."""
    service_id: str
    suggested_scope: list[str] | None = None
    suggested_budget_cents: int | None = None
    suggested_budget_period: str | None = None
    suggested_duration_days: int | None = Field(default=None, gt=0, le=_MAX_CARD_DURATION_DAYS)
    callback_url: str | None = None


class CardRequestResponse(BaseModel):
    """Response body for POST /cards/request."""
    card_id: str
    consent_url: str
    activation_code: str
    activation_url: str
    status: str = "pending"
    expires_at: str


class CardStatusResponse(BaseModel):
    """Response body for GET /cards/{card_id}/status."""
    card_id: str
    status: str
    service_id: str
    expires_at: str


class CardApproveRequest(BaseModel):
    """Request body for POST /cards/{card_id}/approve."""
    allowed_action_ids: list[str] | None = None
    excluded_action_ids: list[str] | None = None
    budget_limit_cents: int | None = None
    budget_period: str | None = Field(default=None, pattern="^(daily|weekly|monthly)$")
    duration_days: int = Field(default=_DEFAULT_CARD_DURATION_DAYS, gt=0, le=_MAX_CARD_DURATION_DAYS)
    first_use_confirmation: bool = True
    passkey_challenge_id: str = Field(..., description="Challenge ID from /human/passkey/login/begin")
    passkey_credential: dict = Field(..., description="WebAuthn assertion credential")


class CardApproveResponse(BaseModel):
    """Response body for POST /cards/{card_id}/approve."""
    card_id: str
    status: str
    service_id: str
    expires_at: str


class CardEditRequest(BaseModel):
    """Request body for PATCH /cards/{card_id} — edit card constraints."""
    excluded_action_ids: list[str] | None = None
    budget_limit_cents: int | None = None
    budget_period: str | None = Field(default=None, pattern="^(daily|weekly|monthly)$")
    first_use_confirmation: bool | None = None


class CardSpendRequest(BaseModel):
    """Request body for POST /cards/{card_id}/report-spend."""
    amount_cents: int = Field(..., gt=0, description="Amount spent in cents")
    action_id: str | None = None
    description: str | None = None


class CardTokenRequest(BaseModel):
    """Request body for POST /cards/{card_id}/token."""
    action_id: str
    token_lifetime_seconds: int | None = None


class CardTokenResponse(BaseModel):
    """Response body for POST /cards/{card_id}/token."""
    token: str
    expires_at: str
    card_id: str
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
        return decode_passport_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail={"error": "passport_expired"}) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail={"error": "passport_invalid"}) from exc


async def _count_active_card_tokens(db, policy_id: str) -> int:
    """Count non-expired active tokens for a card's underlying policy."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "DELETE FROM active_tokens WHERE expires_at < ?", (now,)
    )
    cursor = await db.execute(
        "SELECT COUNT(*) FROM active_tokens WHERE policy_id = ?", (policy_id,)
    )
    row = await cursor.fetchone()
    return row[0]


def _issue_card_token(
    card_id: str,
    email: str,
    service_id: str,
    action_id: str,
    risk_tier: str,
    lifetime_seconds: int,
    agent_tag: str | None = None,
) -> tuple[str, str, str]:
    """Issue a Tier-2 write token under a Company Card. Returns (token, expires_at_iso, jti)."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=lifetime_seconds) if lifetime_seconds > 0 else now + timedelta(seconds=30)
    jti = str(uuid.uuid4())

    scope = f"{service_id}:{action_id}"
    payload = {
        "iss": "agentcafe",
        "sub": f"user:{email}",
        "aud": "agentcafe",
        "exp": exp,
        "iat": now,
        "jti": jti,
        "tier": "write",
        "granted_by": "company_card",
        "policy_id": card_id,
        "card_id": card_id,
        "scopes": [scope],
        "authorizations": [{"service_id": service_id, "action_id": action_id}],
        "risk_tier": risk_tier,
        "agent_tag": agent_tag,
    }

    token = sign_passport_token(payload)
    return token, exp.isoformat(), jti


# ---------------------------------------------------------------------------
# POST /cards/request — Agent requests a Company Card
# ---------------------------------------------------------------------------

@cards_router.post("/cards/request", response_model=CardRequestResponse)
async def request_card(
    req: CardRequestBody,
    authorization: str = Header(default=""),
):
    """Agent requests a Company Card for a service.

    Requires a valid Passport (Tier-1 or Tier-2) in the Authorization header.
    Returns a card_id and consent_url for the human to approve.
    """
    _validate_agent_passport(authorization)

    db = await get_db()

    # Verify service exists
    cursor = await db.execute(
        "SELECT DISTINCT service_id FROM proxy_configs WHERE service_id = ?",
        (req.service_id,),
    )
    if not await cursor.fetchone():
        raise HTTPException(
            status_code=404,
            detail={"error": "service_not_found", "message": f"No service '{req.service_id}' found."},
        )

    card_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    duration_days = req.suggested_duration_days or _DEFAULT_CARD_DURATION_DAYS
    expires_at = now + timedelta(days=duration_days)
    activation_code = _generate_activation_code()

    await db.execute(
        """INSERT INTO company_cards
           (id, cafe_user_id, service_id, allowed_action_ids, excluded_action_ids,
            budget_limit_cents, budget_period, budget_period_start,
            activation_code, status, expires_at, created_at, updated_at)
           VALUES (?, NULL, ?, ?, NULL, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (
            card_id, req.service_id,
            ",".join(req.suggested_scope) if req.suggested_scope else None,
            req.suggested_budget_cents, req.suggested_budget_period,
            now.isoformat() if req.suggested_budget_period else None,
            activation_code,
            expires_at.isoformat(), now.isoformat(), now.isoformat(),
        ),
    )
    await db.commit()

    base = _state.public_url
    consent_url = f"{base}/authorize/card/{card_id}"
    activation_url = f"{base}/activate?code={activation_code}"

    return CardRequestResponse(
        card_id=card_id,
        consent_url=consent_url,
        activation_code=activation_code,
        activation_url=activation_url,
        expires_at=expires_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /cards/{card_id}/status — Agent polls card status
# ---------------------------------------------------------------------------

@cards_router.get("/cards/{card_id}/status", response_model=CardStatusResponse)
async def get_card_status(card_id: str):
    """Check the status of a Company Card request."""
    db = await get_db()

    cursor = await db.execute(
        "SELECT id, status, service_id, expires_at FROM company_cards WHERE id = ?",
        (card_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    # Check if expired (both pending and active cards)
    if row["status"] in ("pending", "active"):
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            await db.execute(
                "UPDATE company_cards SET status = 'expired', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), card_id),
            )
            await db.commit()
            raise HTTPException(status_code=410, detail={"error": "card_expired"})

    return CardStatusResponse(
        card_id=row["id"],
        status=row["status"],
        service_id=row["service_id"],
        expires_at=row["expires_at"],
    )


async def _create_jv_grant_if_needed(
    db,
    user_id: str,
    service_id: str,
    card_id: str,
) -> None:
    """Create identity binding + authorization grant for jointly-verified services.

    Called during card approval. For standard-mode services, this is a no-op.
    Creates the human_service_accounts row (if missing) and the authorization grant.
    """
    cursor = await db.execute(
        "SELECT integration_mode FROM proxy_configs "
        "WHERE service_id = ? AND integration_mode = 'jointly_verified' LIMIT 1",
        (service_id,),
    )
    if not await cursor.fetchone():
        return

    now = datetime.now(timezone.utc).isoformat()

    # Ensure identity binding exists (same pattern as consent.py)
    cursor = await db.execute(
        "SELECT id, binding_status FROM human_service_accounts "
        "WHERE ac_human_id = ? AND service_id = ?",
        (user_id, service_id),
    )
    binding_row = await cursor.fetchone()

    if not binding_row:
        binding_id = str(uuid.uuid4())
        service_account_id = user_id  # MVS: AC user ID as service account ID
        await db.execute(
            """INSERT INTO human_service_accounts
               (id, ac_human_id, service_id, service_account_id,
                binding_method, binding_status, identity_binding,
                linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'broker_delegated', 'active', 'broker_delegated', ?, ?)""",
            (binding_id, user_id, service_id, service_account_id, now, now),
        )
        logger.info("Created service binding via card: user=%s service=%s",
                     user_id, service_id)
    elif binding_row["binding_status"] != "active":
        await db.execute(
            "UPDATE human_service_accounts SET binding_status = 'active', updated_at = ? "
            "WHERE id = ?",
            (now, binding_row["id"]),
        )

    # Create authorization grant
    grant_id = str(uuid.uuid4())
    await db.execute(
        """INSERT OR IGNORE INTO authorization_grants
           (id, ac_human_id, service_id, consent_ref, grant_status,
            granted_at, updated_at)
           VALUES (?, ?, ?, ?, 'active', ?, ?)""",
        (grant_id, user_id, service_id, card_id, now, now),
    )
    logger.info("Created card authorization grant: user=%s service=%s card=%s",
                 user_id, service_id, card_id)


# ---------------------------------------------------------------------------
# POST /cards/{card_id}/approve — Human approves a Company Card
# ---------------------------------------------------------------------------

@cards_router.post("/cards/{card_id}/approve", response_model=CardApproveResponse)
async def approve_card(
    card_id: str,
    req: CardApproveRequest,
    authorization: str = Header(default=""),
):
    """Human approves a Company Card request, activating the card.

    Requires a valid human session token AND a fresh passkey assertion.
    The human sets constraints (scope, budget, duration, excluded actions).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_session"})
    human_payload = validate_human_session(authorization[7:])
    user_id = human_payload["user_id"]

    # Verify passkey assertion
    passkey_user = await verify_passkey_assertion(
        req.passkey_challenge_id, req.passkey_credential,
    )
    if passkey_user["user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "passkey_user_mismatch",
                    "message": "Passkey does not belong to the session user."},
        )

    db = await get_db()

    # Load card
    cursor = await db.execute(
        "SELECT * FROM company_cards WHERE id = ?", (card_id,),
    )
    card = await cursor.fetchone()
    if not card:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    if card["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail={"error": "card_not_pending", "message": f"Card is '{card['status']}', not 'pending'."},
        )

    # Check expiry (of the pending request, not the card itself — card expiry resets on approve)
    now = datetime.now(timezone.utc)

    # Validate excluded actions exist for this service
    if req.excluded_action_ids:
        for action_id in req.excluded_action_ids:
            cursor = await db.execute(
                "SELECT 1 FROM proxy_configs WHERE service_id = ? AND action_id = ?",
                (card["service_id"], action_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_action", "message": f"Unknown action '{action_id}' for service '{card['service_id']}'."},
                )

    # Validate allowed actions exist for this service
    if req.allowed_action_ids:
        for action_id in req.allowed_action_ids:
            cursor = await db.execute(
                "SELECT 1 FROM proxy_configs WHERE service_id = ? AND action_id = ?",
                (card["service_id"], action_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_action", "message": f"Unknown action '{action_id}' for service '{card['service_id']}'."},
                )

    # Calculate card expiry from approval time
    card_expires = now + timedelta(days=req.duration_days)

    # Build scopes from allowed actions (or all service actions)
    if req.allowed_action_ids:
        action_ids_for_policy = req.allowed_action_ids
    else:
        # All actions for this service
        cursor = await db.execute(
            "SELECT action_id FROM proxy_configs WHERE service_id = ?",
            (card["service_id"],),
        )
        action_rows = await cursor.fetchall()
        action_ids_for_policy = [r["action_id"] for r in action_rows]

    # Exclude excluded actions from the policy scopes
    if req.excluded_action_ids:
        excluded_set = set(req.excluded_action_ids)
        action_ids_for_policy = [a for a in action_ids_for_policy if a not in excluded_set]

    scopes_csv = ",".join(f"{card['service_id']}:{a}" for a in action_ids_for_policy)
    action_ids_csv = ",".join(action_ids_for_policy)

    # Determine risk tier (highest among covered actions)
    risk_tier = "low"
    for aid in action_ids_for_policy:
        cursor = await db.execute(
            "SELECT risk_tier FROM proxy_configs WHERE service_id = ? AND action_id = ?",
            (card["service_id"], aid),
        )
        proxy_row = await cursor.fetchone()
        if proxy_row:
            candidate = proxy_row["risk_tier"] or "medium"
            if _RISK_TIER_ORDER.get(candidate, 1) > _RISK_TIER_ORDER.get(risk_tier, 0):
                risk_tier = candidate

    # Create a policy row (a card IS a multi-action policy)
    policy_id = str(uuid.uuid4())
    default_lifetime = _RISK_TIER_DEFAULTS.get(risk_tier, 600)

    await db.execute(
        """INSERT INTO policies
           (id, cafe_user_id, service_id, allowed_action_ids, scopes,
            constraints_json, risk_tier, max_token_lifetime_seconds,
            expires_at, revoked_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?)""",
        (
            policy_id, user_id, card["service_id"],
            action_ids_csv, scopes_csv,
            risk_tier, default_lifetime,
            card_expires.isoformat(), now.isoformat(), now.isoformat(),
        ),
    )

    # Activate card
    await db.execute(
        """UPDATE company_cards
           SET cafe_user_id = ?, status = 'active',
               allowed_action_ids = ?, excluded_action_ids = ?,
               budget_limit_cents = ?, budget_period = ?,
               budget_period_start = ?,
               first_use_confirmation = ?,
               policy_id = ?,
               expires_at = ?, updated_at = ?
           WHERE id = ?""",
        (
            user_id,
            ",".join(req.allowed_action_ids) if req.allowed_action_ids else None,
            ",".join(req.excluded_action_ids) if req.excluded_action_ids else None,
            req.budget_limit_cents, req.budget_period,
            now.isoformat() if req.budget_period else None,
            1 if req.first_use_confirmation else 0,
            policy_id,
            card_expires.isoformat(), now.isoformat(),
            card_id,
        ),
    )

    # Create authorization grant for jointly-verified services (ADR-031)
    await _create_jv_grant_if_needed(db, user_id, card["service_id"], card_id)

    await db.commit()

    logger.info("Card %s approved by user %s for service %s", card_id, user_id, card["service_id"])

    return CardApproveResponse(
        card_id=card_id,
        status="active",
        service_id=card["service_id"],
        expires_at=card_expires.isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /cards/{card_id}/token — Agent gets a Tier-2 token from an active card
# ---------------------------------------------------------------------------

@cards_router.post("/cards/{card_id}/token", response_model=CardTokenResponse)
async def get_card_token(
    card_id: str,
    req: CardTokenRequest,
    authorization: str = Header(default=""),
):
    """Exchange an active Company Card for a Tier-2 write token.

    Requires a valid agent Passport in the Authorization header.
    The requested action must be within the card's scope and risk tier coverage.
    """
    agent_payload = _validate_agent_passport(authorization)
    agent_tag = agent_payload.get("agent_tag")

    db = await get_db()

    # Load card
    cursor = await db.execute(
        "SELECT * FROM company_cards WHERE id = ?", (card_id,),
    )
    card = await cursor.fetchone()
    if not card:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    if card["status"] == "expired":
        raise HTTPException(status_code=410, detail={"error": "card_expired"})
    if card["status"] == "revoked":
        raise HTTPException(status_code=401, detail={"error": "card_revoked"})
    if card["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail={"error": "card_not_active", "message": f"Card is '{card['status']}', not 'active'."},
        )

    # Check card expiry
    expires_at = datetime.fromisoformat(card["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        await db.execute(
            "UPDATE company_cards SET status = 'expired', updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), card_id),
        )
        await db.commit()
        raise HTTPException(status_code=410, detail={"error": "card_expired"})

    # Check action exists and get risk tier
    cursor = await db.execute(
        "SELECT risk_tier FROM proxy_configs WHERE service_id = ? AND action_id = ?",
        (card["service_id"], req.action_id),
    )
    proxy_row = await cursor.fetchone()
    if not proxy_row:
        raise HTTPException(
            status_code=404,
            detail={"error": "action_not_found", "message": f"Unknown action '{req.action_id}' for service '{card['service_id']}'."},
        )

    risk_tier = proxy_row["risk_tier"] or "medium"

    # Check risk tier coverage — high/critical require per-action consent
    if risk_tier not in _CARD_COVERABLE_TIERS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "risk_tier_exceeds_card",
                "message": f"Action '{req.action_id}' has risk tier '{risk_tier}' which requires per-action consent, not a card.",
                "risk_tier": risk_tier,
            },
        )

    # Check action is in scope (allowed and not excluded)
    if card["excluded_action_ids"]:
        excluded = set(card["excluded_action_ids"].split(","))
        if req.action_id in excluded:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "action_excluded",
                    "message": f"Action '{req.action_id}' is excluded from this card. Per-action consent required.",
                },
            )

    if card["allowed_action_ids"]:
        allowed = set(card["allowed_action_ids"].split(","))
        if req.action_id not in allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "action_not_in_scope",
                    "message": f"Action '{req.action_id}' is not in this card's allowed scope.",
                },
            )

    # Check first-use confirmation
    if card["first_use_confirmation"] and not card["first_use_confirmed_at"]:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "first_use_confirmation_required",
                "message": "This card requires first-use confirmation before tokens can be issued.",
                "card_id": card_id,
                "confirm_url": f"/authorize/card/{card_id}/confirm",
            },
        )

    # Check budget
    if card["budget_limit_cents"] is not None and card["budget_period"]:
        current_spent, current_period_start = _check_and_reset_budget_period(card)
        # Persist budget period reset if it changed
        if current_spent != (card["budget_spent_cents"] or 0) or current_period_start != card["budget_period_start"]:
            await db.execute(
                "UPDATE company_cards SET budget_spent_cents = ?, budget_period_start = ?, updated_at = ? WHERE id = ?",
                (current_spent, current_period_start, datetime.now(timezone.utc).isoformat(), card_id),
            )
            await db.commit()
        if current_spent > card["budget_limit_cents"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "budget_exceeded",
                    "message": "Card budget has been exceeded for the current period.",
                },
            )

    # Check concurrent token cap
    active_count = await _count_active_card_tokens(db, card["policy_id"])
    if active_count >= _MAX_ACTIVE_TOKENS_PER_CARD:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "card_token_limit_reached",
                "message": f"Maximum {_MAX_ACTIVE_TOKENS_PER_CARD} active tokens per card.",
            },
        )

    # Look up email for the card owner
    cursor = await db.execute(
        "SELECT email FROM cafe_users WHERE id = ?", (card["cafe_user_id"],)
    )
    user_row = await cursor.fetchone()
    if not user_row:
        raise HTTPException(status_code=500, detail={"error": "card_user_not_found"})
    user_email = user_row["email"]

    # Determine token lifetime
    ceiling = _RISK_TIER_CEILINGS.get(risk_tier, 900)
    default = _RISK_TIER_DEFAULTS.get(risk_tier, 600)
    lifetime = req.token_lifetime_seconds if req.token_lifetime_seconds is not None else default
    if ceiling > 0 and lifetime > ceiling:
        lifetime = ceiling

    # Issue token
    token, token_expires_at, jti = _issue_card_token(
        card_id=card_id,
        email=user_email,
        service_id=card["service_id"],
        action_id=req.action_id,
        risk_tier=risk_tier,
        lifetime_seconds=lifetime,
        agent_tag=agent_tag,
    )

    # Track active token (uses the card's underlying policy_id for FK compat)
    await db.execute(
        "INSERT INTO active_tokens (jti, policy_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (jti, card["policy_id"], token_expires_at, datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()

    scope = f"{card['service_id']}:{req.action_id}"

    return CardTokenResponse(
        token=token,
        expires_at=token_expires_at,
        card_id=card_id,
        scopes=[scope],
    )


# ---------------------------------------------------------------------------
# GET /cards — Human lists their cards (the "Tab")
# ---------------------------------------------------------------------------

@cards_router.get("/cards")
async def list_cards(authorization: str = Header(default="")):
    """List the human's Company Cards (their Tab).

    Requires a valid human session token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_session"})
    human_payload = validate_human_session(authorization[7:])
    user_id = human_payload["user_id"]

    db = await get_db()

    cursor = await db.execute(
        """SELECT id, service_id, allowed_action_ids, excluded_action_ids,
                  max_risk_tier_covered, budget_limit_cents, budget_period,
                  budget_spent_cents, first_use_confirmation, first_use_confirmed_at,
                  status, expires_at, created_at
           FROM company_cards
           WHERE cafe_user_id = ? AND status IN ('active', 'pending')
           ORDER BY created_at DESC""",
        (user_id,),
    )
    rows = await cursor.fetchall()

    cards = []
    for row in rows:
        cards.append({
            "card_id": row["id"],
            "service_id": row["service_id"],
            "allowed_action_ids": row["allowed_action_ids"].split(",") if row["allowed_action_ids"] else None,
            "excluded_action_ids": row["excluded_action_ids"].split(",") if row["excluded_action_ids"] else None,
            "max_risk_tier_covered": row["max_risk_tier_covered"],
            "budget_limit_cents": row["budget_limit_cents"],
            "budget_period": row["budget_period"],
            "budget_spent_cents": row["budget_spent_cents"],
            "first_use_confirmation": bool(row["first_use_confirmation"]),
            "first_use_confirmed": row["first_use_confirmed_at"] is not None,
            "status": row["status"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        })

    return {"cards": cards}


# ---------------------------------------------------------------------------
# POST /cards/{card_id}/revoke — Human revokes a card
# ---------------------------------------------------------------------------

@cards_router.post("/cards/{card_id}/revoke")
async def revoke_card(
    card_id: str,
    authorization: str = Header(default=""),
):
    """Revoke a Company Card. Instantly invalidates all tokens issued under it.

    Requires a valid human session token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_session"})
    human_payload = validate_human_session(authorization[7:])
    user_id = human_payload["user_id"]

    db = await get_db()

    cursor = await db.execute(
        "SELECT id, cafe_user_id, status, policy_id FROM company_cards WHERE id = ?",
        (card_id,),
    )
    card = await cursor.fetchone()
    if not card:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    if card["cafe_user_id"] != user_id:
        raise HTTPException(status_code=403, detail={"error": "not_card_owner"})

    if card["status"] == "revoked":
        raise HTTPException(
            status_code=409,
            detail={"error": "already_revoked", "message": "Card is already revoked."},
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE company_cards SET status = 'revoked', revoked_at = ?, updated_at = ? WHERE id = ?",
        (now, now, card_id),
    )

    # Also revoke the underlying policy (instant token invalidation)
    if card["status"] == "active" and card["policy_id"]:
        await db.execute(
            "UPDATE policies SET revoked_at = ?, updated_at = ? WHERE id = ?",
            (now, now, card["policy_id"]),
        )

    await db.commit()

    # Push revocation to jointly-verified services (ADR-031)
    from agentcafe.cafe.integration import queue_jv_revocation
    await queue_jv_revocation(db, card_id, "human_revoked")

    logger.info("Card %s revoked by user %s", card_id, user_id)
    return {"card_id": card_id, "status": "revoked", "revoked_at": now}


# ---------------------------------------------------------------------------
# POST /cards/{card_id}/confirm-first-use — Human confirms first action
# ---------------------------------------------------------------------------

@cards_router.post("/cards/{card_id}/confirm-first-use")
async def confirm_first_use(
    card_id: str,
    authorization: str = Header(default=""),
):
    """Confirm first-use of a Company Card.

    After this, the card issues tokens without further human interaction
    (within constraints). Requires a valid human session token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_session"})
    human_payload = validate_human_session(authorization[7:])
    user_id = human_payload["user_id"]

    db = await get_db()

    cursor = await db.execute(
        "SELECT id, cafe_user_id, status, first_use_confirmation, first_use_confirmed_at FROM company_cards WHERE id = ?",
        (card_id,),
    )
    card = await cursor.fetchone()
    if not card:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    if card["cafe_user_id"] != user_id:
        raise HTTPException(status_code=403, detail={"error": "not_card_owner"})

    if card["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail={"error": "card_not_active", "message": f"Card is '{card['status']}', not 'active'."},
        )

    if card["first_use_confirmed_at"]:
        return {"card_id": card_id, "status": "already_confirmed"}

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE company_cards SET first_use_confirmed_at = ?, updated_at = ? WHERE id = ?",
        (now, now, card_id),
    )
    await db.commit()

    logger.info("Card %s first-use confirmed by user %s", card_id, user_id)
    return {"card_id": card_id, "status": "confirmed", "confirmed_at": now}


# ---------------------------------------------------------------------------
# PATCH /cards/{card_id} — Human edits card constraints
# ---------------------------------------------------------------------------

@cards_router.patch("/cards/{card_id}")
async def edit_card(
    card_id: str,
    req: CardEditRequest,
    authorization: str = Header(default=""),
):
    """Edit constraints on an active Company Card.

    Only the card owner can edit. Editable fields: excluded_action_ids,
    budget_limit_cents, budget_period, first_use_confirmation.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_session"})
    human_payload = validate_human_session(authorization[7:])
    user_id = human_payload["user_id"]
    db = await get_db()

    cursor = await db.execute("SELECT * FROM company_cards WHERE id = ?", (card_id,))
    card = await cursor.fetchone()
    if not card:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    if card["cafe_user_id"] != user_id:
        raise HTTPException(status_code=403, detail={"error": "not_card_owner"})

    if card["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail={"error": "card_not_active", "message": f"Card is '{card['status']}', not 'active'."},
        )

    updates = []
    params = []

    if req.excluded_action_ids is not None:
        # Validate excluded actions exist for this service
        for action_id in req.excluded_action_ids:
            cursor = await db.execute(
                "SELECT 1 FROM proxy_configs WHERE service_id = ? AND action_id = ?",
                (card["service_id"], action_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_action", "message": f"Unknown action '{action_id}'."},
                )
        updates.append("excluded_action_ids = ?")
        params.append(",".join(req.excluded_action_ids) if req.excluded_action_ids else None)

    if req.budget_limit_cents is not None:
        updates.append("budget_limit_cents = ?")
        params.append(req.budget_limit_cents)

    if req.budget_period is not None:
        updates.append("budget_period = ?")
        params.append(req.budget_period)
        # Reset budget period start when changing period
        updates.append("budget_period_start = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        updates.append("budget_spent_cents = 0")

    if req.first_use_confirmation is not None:
        updates.append("first_use_confirmation = ?")
        params.append(1 if req.first_use_confirmation else 0)

    if not updates:
        raise HTTPException(
            status_code=422,
            detail={"error": "no_changes", "message": "No editable fields provided."},
        )

    now = datetime.now(timezone.utc).isoformat()
    updates.append("updated_at = ?")
    params.append(now)
    params.append(card_id)

    await db.execute(
        f"UPDATE company_cards SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    await db.commit()

    logger.info("Card %s edited by user %s", card_id, user_id)
    return {"card_id": card_id, "status": "updated", "updated_at": now}


# ---------------------------------------------------------------------------
# POST /cards/{card_id}/report-spend — Track spending against a card's budget
# ---------------------------------------------------------------------------

@cards_router.post("/cards/{card_id}/report-spend")
async def report_spend(
    card_id: str,
    req: CardSpendRequest,
    authorization: str = Header(default=""),
):
    """Report spending against a Company Card's budget.

    Can be called by the system or an agent with a valid Passport.
    Increments budget_spent_cents and checks against the budget limit.
    """
    _validate_agent_passport(authorization)
    db = await get_db()

    cursor = await db.execute("SELECT * FROM company_cards WHERE id = ?", (card_id,))
    card = await cursor.fetchone()
    if not card:
        raise HTTPException(status_code=404, detail={"error": "card_not_found"})

    if card["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail={"error": "card_not_active", "message": f"Card is '{card['status']}', not 'active'."},
        )

    # Reset budget period if needed
    current_spent, current_period_start = _check_and_reset_budget_period(card)

    new_spent = current_spent + req.amount_cents
    budget_exceeded = False
    if card["budget_limit_cents"] is not None:
        budget_exceeded = new_spent > card["budget_limit_cents"]

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE company_cards
           SET budget_spent_cents = ?, budget_period_start = ?, updated_at = ?
           WHERE id = ?""",
        (new_spent, current_period_start or now, now, card_id),
    )
    await db.commit()

    result = {
        "card_id": card_id,
        "amount_cents": req.amount_cents,
        "budget_spent_cents": new_spent,
        "budget_limit_cents": card["budget_limit_cents"],
        "budget_exceeded": budget_exceeded,
    }
    if budget_exceeded:
        result["warning"] = "Budget limit exceeded for the current period."

    logger.info("Card %s spend reported: %d cents (total: %d)", card_id, req.amount_cents, new_spent)
    return result


# ---------------------------------------------------------------------------
# Budget helpers
# ---------------------------------------------------------------------------

def _check_and_reset_budget_period(card) -> tuple[int, str | None]:
    """Check if the budget period has rolled over, and return current values.

    Returns (budget_spent_cents, budget_period_start) — reset to 0 / now if
    the period has rolled over, otherwise the existing values from the card.
    Does NOT mutate the card row (sqlite3.Row is immutable).
    """
    spent = card["budget_spent_cents"] or 0
    period_start_str = card["budget_period_start"]

    if not card["budget_period"] or not period_start_str:
        return spent, period_start_str

    period_start = datetime.fromisoformat(period_start_str)
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    period_map = {
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
    }
    period_delta = period_map.get(card["budget_period"], timedelta(days=30))

    if now >= period_start + period_delta:
        # Period has rolled over — reset spending
        return 0, now.isoformat()
    return spent, period_start_str
