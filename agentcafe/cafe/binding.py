"""Gate 3: Identity binding and authorization grant resolution (ADR-031).

Resolves the human's service-side account and grant status before
artifact signing. Only invoked for ``integration_mode = 'jointly_verified'``.

Standard-mode actions skip this entirely.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite
from fastapi import HTTPException


@dataclass(frozen=True)
class BindingResult:
    """Resolved identity binding for a jointly-verified request."""

    service_account_id: str
    identity_binding: str  # broker_delegated | service_native | email_match


async def resolve_human_id(db: aiosqlite.Connection, passport_sub: str) -> str:
    """Extract the AC human ID from a Passport ``sub`` claim.

    Passport ``sub`` is ``"user:{email}"``. Looks up ``cafe_users.id``
    by email and returns the UUID.

    Raises 403 if the human is not found.
    """
    if not passport_sub.startswith("user:"):
        raise HTTPException(status_code=403, detail={
            "error": "invalid_passport_subject",
            "message": "Passport subject is not a human user.",
        })
    email = passport_sub[5:]  # strip "user:" prefix
    cursor = await db.execute(
        "SELECT id FROM cafe_users WHERE email = ?", (email,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail={
            "error": "human_not_found",
            "message": "No Cafe account found for the Passport holder.",
        })
    return row["id"]


async def resolve_binding(
    db: aiosqlite.Connection,
    ac_human_id: str,
    service_id: str,
    consent_ref: str,
) -> BindingResult:
    """Gate 3: Resolve identity binding and authorization grant.

    Checks both ``human_service_accounts`` (identity) and
    ``authorization_grants`` (authorization) for the given human + service.

    Returns a :class:`BindingResult` on success.
    Raises :class:`HTTPException` with structured error on failure.
    """
    # 1. Check authorization grant
    cursor = await db.execute(
        "SELECT grant_status FROM authorization_grants "
        "WHERE consent_ref = ? AND service_id = ?",
        (consent_ref, service_id),
    )
    grant_row = await cursor.fetchone()
    if grant_row and grant_row["grant_status"] in (
        "revoke_queued", "revoke_delivered", "revoke_honored",
    ):
        raise HTTPException(status_code=403, detail={
            "error": "grant_revoked",
            "message": "The authorization grant for this policy has been revoked.",
            "consent_ref": consent_ref,
        })

    # 2. Check identity binding
    cursor = await db.execute(
        "SELECT service_account_id, binding_status, identity_binding "
        "FROM human_service_accounts "
        "WHERE ac_human_id = ? AND service_id = ?",
        (ac_human_id, service_id),
    )
    binding_row = await cursor.fetchone()

    if not binding_row:
        raise HTTPException(status_code=403, detail={
            "error": "account_link_required",
            "message": (
                "No account binding exists for this human on this service. "
                "A consent approval will establish the binding automatically."
            ),
        })

    binding_status = binding_row["binding_status"]

    if binding_status == "deferred":
        raise HTTPException(status_code=503, detail={
            "error": "service_setup_pending",
            "message": "Account setup on the service is pending. Please retry shortly.",
            "retry_after_seconds": 30,
        })

    if binding_status == "unlinked":
        raise HTTPException(status_code=403, detail={
            "error": "binding_inactive",
            "message": "The account binding for this service has been unlinked.",
        })

    if binding_status != "active":
        raise HTTPException(status_code=403, detail={
            "error": "binding_status_invalid",
            "message": f"Unexpected binding status: {binding_status}",
        })

    # 3. Active binding — verify grant exists and is active
    if not grant_row:
        raise HTTPException(status_code=403, detail={
            "error": "grant_not_found",
            "message": "No authorization grant found for this consent reference.",
            "consent_ref": consent_ref,
        })

    if grant_row["grant_status"] != "active":
        raise HTTPException(status_code=403, detail={
            "error": "grant_not_active",
            "message": f"Grant status is '{grant_row['grant_status']}', expected 'active'.",
            "consent_ref": consent_ref,
        })

    return BindingResult(
        service_account_id=binding_row["service_account_id"],
        identity_binding=binding_row["identity_binding"],
    )
