"""Menu assembly — reads published_services from the database and returns the locked Menu format."""

from __future__ import annotations

import json

import aiosqlite


async def get_full_menu(db: aiosqlite.Connection) -> dict:
    """Build the full AgentCafe Menu from all live published services.

    Returns the exact locked Menu format that agents consume.
    Includes quarantine_until and suspended_at per-action when set (ADR-025).
    """
    cursor = await db.execute(
        "SELECT menu_entry_json FROM published_services WHERE status = 'live' ORDER BY service_id"
    )
    rows = await cursor.fetchall()

    services = [json.loads(row[0]) for row in rows]

    # Enrich with quarantine/suspension status from proxy_configs
    security_cursor = await db.execute(
        "SELECT service_id, action_id, quarantine_until, suspended_at FROM proxy_configs"
    )
    security_rows = await security_cursor.fetchall()
    security_map: dict[str, dict[str, dict]] = {}
    for srow in security_rows:
        sid = srow["service_id"]
        aid = srow["action_id"]
        status = {}
        if srow["quarantine_until"]:
            status["quarantine_until"] = srow["quarantine_until"]
        if srow["suspended_at"]:
            status["suspended_at"] = srow["suspended_at"]
        if status:
            security_map.setdefault(sid, {})[aid] = status

    for service in services:
        sid = service.get("service_id", "")
        if sid in security_map:
            for action in service.get("actions", []):
                aid = action.get("action_id", "")
                if aid in security_map[sid]:
                    action["security_status"] = security_map[sid][aid]

    return {
        "cafe": "AgentCafe",
        "version": "1.0.0",
        "description": "The Menu. Browse freely. When you find what you need, present your Passport and order.",
        "order_endpoint": "POST /cafe/order",
        "order_format": {
            "service_id": "<slug>",
            "action_id": "<slug>",
            "passport": "<valid_passport_token>",
            "inputs": {},
        },
        "services": services,
    }
