"""Revocation push delivery and integration config for jointly-verified services.

Implements Service Contract §B.2–B.5: queue revocation events, deliver them
to the service's POST /integration/revoke endpoint, and retry with exponential
backoff on failure.

For MVS (Human Memory), the integration config is hard-coded (spec §12.3).
When a second jointly-verified service is onboarded, this moves to the database.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

import aiosqlite
import httpx

logger = logging.getLogger("agentcafe.integration")

_DELIVERY_TIMEOUT = 10  # seconds
_MAX_ATTEMPTS = 10
_STANDARD_VERSION = "1.0"

# Exponential backoff schedule (seconds): 5, 15, 45, 135, 300 (clamped)
_BACKOFF_SCHEDULE = [5, 15, 45, 135, 300]


# ---------------------------------------------------------------------------
# MVS: Hard-coded HM configuration (spec §12.3)
# ---------------------------------------------------------------------------

_HM_CONFIG = {
    "service_id": "human-memory",
    "integration_base_url": "http://localhost:8001",
    "integration_auth_header": "",  # set during onboarding credential exchange
    "capabilities": {
        "revoke": True,
        "grant_status": False,
    },
}


async def get_integration_config(service_id: str, db=None) -> dict | None:
    """Return integration config for a service, or None if not configured.

    Queries ``service_integration_configs`` first (wizard-onboarded services).
    Falls back to hard-coded ``_HM_CONFIG`` for backward compatibility.
    """
    if db is not None:
        cursor = await db.execute(
            "SELECT * FROM service_integration_configs WHERE service_id = ?",
            (service_id,),
        )
        row = await cursor.fetchone()
        if row:
            from agentcafe.crypto import decrypt
            row = dict(row)
            return {
                "service_id": service_id,
                "integration_base_url": row["integration_base_url"],
                "integration_auth_header": decrypt(row["integration_auth_header"]),
                "capabilities": {
                    "account_check": bool(row["cap_account_check"]),
                    "account_create": bool(row["cap_account_create"]),
                    "link_complete": bool(row["cap_link_complete"]),
                    "unlink": bool(row["cap_unlink"]),
                    "revoke": bool(row["cap_revoke"]),
                    "grant_status": bool(row["cap_grant_status"]),
                },
                "identity_matching": row["identity_matching"],
                "has_direct_signup": bool(row["has_direct_signup"]),
            }

    # Fallback for HM during transition
    if service_id == _HM_CONFIG["service_id"]:
        return _HM_CONFIG
    return None


# ---------------------------------------------------------------------------
# Revocation queue + delivery
# ---------------------------------------------------------------------------

async def queue_revocation(
    db: aiosqlite.Connection,
    consent_ref: str,
    service_id: str,
    reason: str,
) -> str | None:
    """Queue a revocation for delivery to a service.

    Transitions the authorization grant to ``revoke_queued`` and inserts
    a ``revocation_deliveries`` row. Returns the correlation_id,
    or None if a delivery for this consent_ref+service_id already exists.
    """
    # Guard: don't create duplicate deliveries (I2/I3 fix)
    cursor = await db.execute(
        "SELECT id FROM revocation_deliveries "
        "WHERE consent_ref = ? AND service_id = ? AND status IN ('queued', 'delivered')",
        (consent_ref, service_id),
    )
    if await cursor.fetchone():
        return None

    now = datetime.now(timezone.utc).isoformat()
    correlation_id = f"rev_{uuid.uuid4()}"

    # Transition grant status
    await db.execute(
        "UPDATE authorization_grants "
        "SET grant_status = 'revoke_queued', revoked_at = ?, updated_at = ? "
        "WHERE consent_ref = ? AND service_id = ? AND grant_status = 'active'",
        (now, now, consent_ref, service_id),
    )

    # Insert delivery record (store revoked_at for stable retries — C3 fix)
    delivery_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO revocation_deliveries
           (id, consent_ref, service_id, correlation_id,
            status, attempts, created_at)
           VALUES (?, ?, ?, ?, 'queued', 0, ?)""",
        (delivery_id, consent_ref, service_id, correlation_id, now),
    )

    logger.info("Queued revocation: consent_ref=%s service=%s correlation=%s reason=%s",
                consent_ref, service_id, correlation_id, reason)
    return correlation_id


async def deliver_revocation(
    db: aiosqlite.Connection,
    delivery_id: str,
    reason: str = "human_revoked",
) -> bool:
    """Attempt to deliver a single queued revocation to the service.

    Returns True if delivery succeeded, False otherwise.
    """
    cursor = await db.execute(
        "SELECT rd.id, rd.consent_ref, rd.service_id, rd.correlation_id, "
        "       rd.attempts, rd.created_at, ag.revoked_at "
        "FROM revocation_deliveries rd "
        "LEFT JOIN authorization_grants ag "
        "  ON ag.consent_ref = rd.consent_ref AND ag.service_id = rd.service_id "
        "WHERE rd.id = ?",
        (delivery_id,),
    )
    row = await cursor.fetchone()
    if not row:
        logger.warning("Delivery not found: %s", delivery_id)
        return False

    service_id = row["service_id"]
    config = await get_integration_config(service_id, db)
    if not config or not config["capabilities"].get("revoke"):
        logger.warning("No revoke capability for service: %s", service_id)
        return False

    now = datetime.now(timezone.utc).isoformat()
    base_url = config["integration_base_url"].rstrip("/")
    url = f"{base_url}/integration/revoke"

    # Use the original revocation timestamp, not current time (C3 fix)
    revoked_at = row["revoked_at"] or row["created_at"]

    payload = {
        "standard_version": _STANDARD_VERSION,
        "consent_ref": row["consent_ref"],
        "revoked_at": revoked_at,
        "reason": reason,
        "correlation_id": row["correlation_id"],
    }

    # Build headers with integration credential (C1 fix)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    auth_header = config.get("integration_auth_header", "")
    if auth_header:
        headers["Authorization"] = auth_header

    try:
        async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code < 300:
            # Guard against non-JSON 2xx responses (C2 fix)
            try:
                body = resp.json()
            except (ValueError, KeyError):
                body = {}

            if body.get("acknowledged"):
                # Success — mark delivered
                await db.execute(
                    "UPDATE revocation_deliveries "
                    "SET status = 'delivered', delivered_at = ?, "
                    "    attempts = attempts + 1, last_attempt_at = ? "
                    "WHERE id = ?",
                    (now, now, delivery_id),
                )
                await db.execute(
                    "UPDATE authorization_grants "
                    "SET grant_status = 'revoke_delivered', updated_at = ? "
                    "WHERE consent_ref = ? AND service_id = ?",
                    (now, row["consent_ref"], service_id),
                )
                await db.commit()
                logger.info("Revocation delivered: correlation=%s service=%s",
                           row["correlation_id"], service_id)
                return True

        # Non-success response
        error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
    except httpx.RequestError as exc:
        error_msg = f"Connection error: {exc}"

    # Delivery failed — update attempts
    new_attempts = row["attempts"] + 1
    new_status = "failed" if new_attempts >= _MAX_ATTEMPTS else "queued"

    await db.execute(
        "UPDATE revocation_deliveries "
        "SET attempts = ?, last_attempt_at = ?, error_message = ?, status = ? "
        "WHERE id = ?",
        (new_attempts, now, error_msg, new_status, delivery_id),
    )
    await db.commit()

    if new_status == "failed":
        logger.error("Revocation delivery FAILED after %d attempts: "
                     "correlation=%s service=%s error=%s",
                     new_attempts, row["correlation_id"], service_id, error_msg)
    else:
        logger.warning("Revocation delivery attempt %d/%d failed: "
                      "correlation=%s service=%s error=%s",
                      new_attempts, _MAX_ATTEMPTS,
                      row["correlation_id"], service_id, error_msg)

    return False


def _backoff_seconds(attempts: int) -> int:
    """Return the backoff delay for the given attempt count."""
    idx = min(attempts, len(_BACKOFF_SCHEDULE) - 1)
    return _BACKOFF_SCHEDULE[idx]


async def attempt_pending_deliveries(db: aiosqlite.Connection) -> int:
    """Retry all queued deliveries whose backoff period has elapsed.

    Returns the count of successful deliveries.
    """
    now = datetime.now(timezone.utc)
    cursor = await db.execute(
        "SELECT id, attempts, last_attempt_at "
        "FROM revocation_deliveries WHERE status = 'queued'",
    )
    rows = await cursor.fetchall()

    delivered = 0
    for row in rows:
        # Check backoff
        if row["last_attempt_at"]:
            last = datetime.fromisoformat(row["last_attempt_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            wait = _backoff_seconds(row["attempts"])
            if now < last + timedelta(seconds=wait):
                continue  # not yet eligible for retry

        if await deliver_revocation(db, row["id"]):
            delivered += 1

    return delivered


# ---------------------------------------------------------------------------
# High-level helper: queue + attempt inline delivery
# ---------------------------------------------------------------------------

async def queue_jv_revocation(
    db: aiosqlite.Connection,
    consent_ref: str,
    reason: str,
) -> None:
    """Queue revocation delivery for all jointly-verified services with a grant.

    Called from revocation endpoints (cards, dashboard). For each service
    that has an active grant for this consent_ref:
    1. Transitions grant to revoke_queued
    2. Inserts delivery row
    3. Attempts immediate synchronous delivery
    4. If delivery fails, the background retry loop picks it up
    """
    cursor = await db.execute(
        "SELECT ag.service_id "
        "FROM authorization_grants ag "
        "WHERE ag.consent_ref = ? AND ag.grant_status = 'active'",
        (consent_ref,),
    )
    grants = await cursor.fetchall()

    for grant in grants:
        config = await get_integration_config(grant["service_id"], db)
        if not config or not config["capabilities"].get("revoke"):
            continue

        correlation_id = await queue_revocation(
            db, consent_ref, grant["service_id"], reason,
        )
        await db.commit()

        if not correlation_id:
            continue  # already queued

        # Attempt immediate delivery
        delivery_cursor = await db.execute(
            "SELECT id FROM revocation_deliveries WHERE correlation_id = ?",
            (correlation_id,),
        )
        delivery_row = await delivery_cursor.fetchone()
        if delivery_row:
            await deliver_revocation(db, delivery_row["id"], reason=reason)
