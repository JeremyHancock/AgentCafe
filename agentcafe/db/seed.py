"""Seed the three demo services into the database.

Menu entries are loaded from the Phase 0.2 design files (single source of truth).
Proxy configs are defined here (implementation details not in the design files).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger("agentcafe.seed")

# ---------------------------------------------------------------------------
# Demo company
# ---------------------------------------------------------------------------

DEMO_COMPANY = {
    "id": "demo-company-001",
    "name": "AgentCafe Demo Corp",
    "email": "demo@agentcafe.example.com",
    "website": "https://agentcafe.example.com",
}

# ---------------------------------------------------------------------------
# Menu entry file paths (relative to design_dir)
# ---------------------------------------------------------------------------

MENU_ENTRY_FILES = {
    "hotel": "services/hotel-booking/menu-entry.json",
    "lunch": "services/lunch-delivery/menu-entry.json",
    "home_service": "services/home-service-appointment/menu-entry.json",
}

# ---------------------------------------------------------------------------
# Proxy configs (implementation details — not in the design files)
# These map action_ids to backend routes, methods, scopes, and policies.
# ---------------------------------------------------------------------------

PROXY_CONFIGS = {
    "stayright-hotels": [
        {"action_id": "search-availability", "backend_path": "/availability/search", "backend_method": "POST", "scope": "stayright-hotels:search-availability", "human_auth": False, "rate_limit": "60/minute"},
        {"action_id": "get-room-details", "backend_path": "/rooms/{room_id}", "backend_method": "GET", "scope": "stayright-hotels:get-room-details", "human_auth": False, "rate_limit": "60/minute"},
        {"action_id": "book-room", "backend_path": "/bookings", "backend_method": "POST", "scope": "stayright-hotels:book-room", "human_auth": True, "rate_limit": "10/minute"},
        {"action_id": "cancel-booking", "backend_path": "/bookings/{booking_id}/cancel", "backend_method": "POST", "scope": "stayright-hotels:cancel-booking", "human_auth": True, "rate_limit": "10/minute"},
    ],
    "quickbite-delivery": [
        {"action_id": "browse-menu", "backend_path": "/menu/search", "backend_method": "POST", "scope": "quickbite-delivery:browse-menu", "human_auth": False, "rate_limit": "60/minute"},
        {"action_id": "place-order", "backend_path": "/orders", "backend_method": "POST", "scope": "quickbite-delivery:place-order", "human_auth": True, "rate_limit": "10/minute"},
        {"action_id": "track-order", "backend_path": "/orders/{order_id}/status", "backend_method": "GET", "scope": "quickbite-delivery:track-order", "human_auth": False, "rate_limit": "30/minute"},
        {"action_id": "cancel-order", "backend_path": "/orders/{order_id}/cancel", "backend_method": "POST", "scope": "quickbite-delivery:cancel-order", "human_auth": True, "rate_limit": "10/minute"},
    ],
    "fixright-home": [
        {"action_id": "search-providers", "backend_path": "/providers/search", "backend_method": "POST", "scope": "fixright-home:search-providers", "human_auth": False, "rate_limit": "60/minute"},
        {"action_id": "book-appointment", "backend_path": "/appointments", "backend_method": "POST", "scope": "fixright-home:book-appointment", "human_auth": True, "rate_limit": "10/minute"},
        {"action_id": "reschedule-appointment", "backend_path": "/appointments/{appointment_id}/reschedule", "backend_method": "POST", "scope": "fixright-home:reschedule-appointment", "human_auth": True, "rate_limit": "10/minute"},
        {"action_id": "cancel-appointment", "backend_path": "/appointments/{appointment_id}/cancel", "backend_method": "POST", "scope": "fixright-home:cancel-appointment", "human_auth": True, "rate_limit": "10/minute"},
    ],
}

# Backend URL key mapping (service_id → config property key)
BACKEND_URL_KEYS = {
    "stayright-hotels": "hotel",
    "quickbite-delivery": "lunch",
    "fixright-home": "home_service",
}


def _load_menu_entries(design_dir: str) -> list[dict]:
    """Load menu entries from Phase 0.2 JSON files.

    These files are the single source of truth for Menu content.
    """
    design_path = Path(design_dir)
    entries = []

    for rel_path in MENU_ENTRY_FILES.values():
        filepath = design_path / rel_path
        if not filepath.exists():
            raise FileNotFoundError(
                f"Design file not found: {filepath}\n"
                f"Expected design files in: {design_path}\n"
                f"Set CAFE_DESIGN_DIR env var or check that docs/design/ exists at the project root"
            )
        with open(filepath, encoding="utf-8") as f:
            entry = json.load(f)
        logger.info("Loaded menu entry from %s (%s)", rel_path, entry["service_id"])
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Seed function
# ---------------------------------------------------------------------------

async def seed_demo_data(db: aiosqlite.Connection, config) -> None:
    """Seed the demo company, three services, and proxy configs into the database.

    Menu entries are loaded from the Phase 0.2 design JSON files (single source of truth).
    Skips if data already exists (idempotent).
    """
    # Check if already seeded
    cursor = await db.execute(
        "SELECT COUNT(*) FROM published_services WHERE status = 'live'"
    )
    row = await cursor.fetchone()
    if row[0] >= 3:
        return

    # Load menu entries from design files
    menu_entries = _load_menu_entries(config.design_dir)

    now = datetime.now(timezone.utc).isoformat()

    # Upsert demo company
    await db.execute(
        """INSERT OR IGNORE INTO companies (id, name, email, website, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (DEMO_COMPANY["id"], DEMO_COMPANY["name"], DEMO_COMPANY["email"], DEMO_COMPANY["website"], now, now),
    )

    backend_urls = {
        "hotel": config.hotel_backend_url,
        "lunch": config.lunch_backend_url,
        "home_service": config.home_service_backend_url,
    }

    for menu in menu_entries:
        service_id = menu["service_id"]
        url_key = BACKEND_URL_KEYS[service_id]
        backend_url = backend_urls[url_key]

        # Insert published service
        await db.execute(
            """INSERT OR IGNORE INTO published_services
               (id, company_id, service_id, name, description, menu_entry_json, status, published_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'live', ?, ?)""",
            (
                str(uuid.uuid4()),
                DEMO_COMPANY["id"],
                service_id,
                menu["name"],
                menu["description"],
                json.dumps(menu),
                now,
                now,
            ),
        )

        # Insert proxy configs for each action
        proxy_configs = PROXY_CONFIGS[service_id]
        for pc in proxy_configs:
            await db.execute(
                """INSERT OR IGNORE INTO proxy_configs
                   (id, service_id, action_id, backend_url, backend_path, backend_method,
                    backend_auth_header, scope, human_auth_required, rate_limit, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    service_id,
                    pc["action_id"],
                    backend_url,
                    pc["backend_path"],
                    pc["backend_method"],
                    "",  # demo backends don't require auth
                    pc["scope"],
                    1 if pc["human_auth"] else 0,
                    pc["rate_limit"],
                    now,
                ),
            )

    await db.commit()
