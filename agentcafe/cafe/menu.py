"""Menu assembly — reads published_services from the database and returns the locked Menu format."""

from __future__ import annotations

import json

import aiosqlite


async def get_full_menu(db: aiosqlite.Connection) -> dict:
    """Build the full AgentCafe Menu from all live published services.

    Returns the exact locked Menu format that agents consume.
    """
    cursor = await db.execute(
        "SELECT menu_entry_json FROM published_services WHERE status = 'live' ORDER BY service_id"
    )
    rows = await cursor.fetchall()

    services = [json.loads(row[0]) for row in rows]

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
