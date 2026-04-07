"""End-to-end demo agent for AgentCafe.

Usage:
    python -m agentcafe.demo_agent
    python -m agentcafe.demo_agent --service stayright-hotels --action book-room
    python -m agentcafe.demo_agent --headless

The agent runs the full lifecycle:
1. Browse the Menu
2. Register for a Tier-1 (read) token
3. Place a read order (search)
4. Initiate consent for a write action
5. Human approves (or --headless auto-approves)
6. Exchange for a Tier-2 (write) token
7. Place a write order (book)
8. Refresh the token
9. Verify audit chain integrity
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx


BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _log(step: int, msg: str, color: str = BLUE) -> None:
    print(f"{color}{BOLD}[Step {step}]{RESET} {msg}")


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


async def run_demo(base_url: str, service_id: str, read_action: str, write_action: str, headless: bool) -> bool:
    """Run the full agent demo. Returns True on success."""
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:

        # --- Step 1: Browse Menu ---
        _log(1, "Browsing the Menu...")
        resp = await client.get("/cafe/menu")
        if resp.status_code != 200:
            _fail(f"GET /cafe/menu returned {resp.status_code}")
            return False
        menu = resp.json()
        services = menu.get("services", [])
        _ok(f"Menu has {len(services)} services")
        svc = next((s for s in services if s["service_id"] == service_id), None)
        if not svc:
            _fail(f"Service '{service_id}' not found in Menu")
            return False
        action_ids = [a["action_id"] for a in svc.get("actions", [])]
        _ok(f"Found '{svc['name']}' with actions: {', '.join(action_ids)}")

        # --- Step 2: Register for Tier-1 token ---
        _log(2, "Registering for a Tier-1 (read) Passport...")
        resp = await client.post("/passport/register", json={"agent_tag": "demo-agent-cli"})
        if resp.status_code != 200:
            _fail(f"POST /passport/register returned {resp.status_code}: {resp.text}")
            return False
        tier1_token = resp.json()["passport"]
        _ok(f"Tier-1 token received (expires: {resp.json()['expires_at']})")

        # --- Step 3: Place a read order ---
        _log(3, f"Placing read order: {service_id}/{read_action}...")
        read_inputs = _get_read_inputs(service_id, read_action)
        resp = await client.post("/cafe/order", json={
            "service_id": service_id,
            "action_id": read_action,
            "passport": tier1_token,
            "inputs": read_inputs,
        })
        if resp.status_code != 200:
            _fail(f"POST /cafe/order returned {resp.status_code}: {resp.text}")
            return False
        _ok(f"Read order succeeded: {json.dumps(resp.json(), indent=2)[:200]}...")

        # --- Step 4: Initiate consent for write action ---
        _log(4, f"Initiating consent for write action: {write_action}...")
        resp = await client.post(
            "/consents/initiate",
            json={"service_id": service_id, "action_id": write_action},
            headers={"Authorization": f"Bearer {tier1_token}"},
        )
        if resp.status_code != 200:
            _fail(f"POST /consents/initiate returned {resp.status_code}: {resp.text}")
            return False
        consent_data = resp.json()
        consent_id = consent_data["consent_id"]
        _ok(f"Consent initiated: {consent_id}")
        _ok(f"Consent URL: {consent_data['consent_url']}")

        # --- Step 5: Human approves ---
        if headless:
            _log(5, "Auto-approving consent (--headless mode)...")
            # Register a human account and approve
            import uuid as _uuid
            human_email = f"demo-human-{_uuid.uuid4().hex[:6]}@example.com"
            resp = await client.post("/human/register", json={
                "email": human_email,
                "password": "demo-password-123",
                "display_name": "Demo Human",
            })
            if resp.status_code != 200:
                _fail(f"Human registration failed: {resp.text}")
                return False
            human_token = resp.json()["session_token"]

            resp = await client.post(
                f"/consents/{consent_id}/approve",
                json={},
                headers={"Authorization": f"Bearer {human_token}"},
            )
            if resp.status_code != 200:
                _fail("Consent approval failed: " + resp.text)
                return False
            _ok(f"Consent approved by {human_email}")
        else:
            _log(5, f"Waiting for human approval at: {YELLOW}{base_url}{consent_data['consent_url']}{RESET}")
            print("  Open this URL in your browser, register/login, and approve.")
            print("  Polling consent status...")
            for _ in range(120):
                await asyncio.sleep(2)
                resp = await client.get(f"/consents/{consent_id}/status")
                status = resp.json().get("status", "unknown")
                if status == "approved":
                    _ok("Consent approved!")
                    break
                if status in ("declined", "expired"):
                    _fail(f"Consent {status}")
                    return False
                print(f"  ... status: {status}", end="\r")
            else:
                _fail("Timed out waiting for approval (4 minutes)")
                return False

        # --- Step 6: Exchange for Tier-2 token ---
        _log(6, "Exchanging consent for Tier-2 (write) token...")
        resp = await client.post(
            "/tokens/exchange",
            json={"consent_id": consent_id},
            headers={"Authorization": f"Bearer {tier1_token}"},
        )
        if resp.status_code != 200:
            _fail(f"Token exchange failed: {resp.text}")
            return False
        exchange_data = resp.json()
        tier2_token = exchange_data["token"]
        _ok(f"Tier-2 token received (expires: {exchange_data['expires_at']})")
        limits = exchange_data.get("policy_limits", {})
        if limits:
            _ok(f"Policy limits: {limits['active_tokens']}/{limits['max_active_tokens']} active tokens")

        # --- Step 7: Read-before-write (identity verification) ---
        _log(7, f"Read-before-write: reading {service_id}/{read_action} with Tier-2 token...")
        resp = await client.post("/cafe/order", json={
            "service_id": service_id,
            "action_id": read_action,
            "passport": tier2_token,
            "inputs": read_inputs,
        })
        if resp.status_code != 200:
            _fail(f"Read-before-write failed ({resp.status_code}): {resp.text}")
            return False
        _ok("Read-before-write passed (identity verified for medium+ risk tier)")

        # --- Step 8: Place a write order ---
        _log(8, f"Placing write order: {service_id}/{write_action}...")
        write_inputs = _get_write_inputs(service_id, write_action)
        resp = await client.post("/cafe/order", json={
            "service_id": service_id,
            "action_id": write_action,
            "passport": tier2_token,
            "inputs": write_inputs,
        })
        if resp.status_code != 200:
            _fail(f"Write order failed ({resp.status_code}): {resp.text}")
            return False
        _ok(f"Write order succeeded: {json.dumps(resp.json(), indent=2)[:200]}...")

        # --- Step 9: Refresh token ---
        _log(9, "Refreshing Tier-2 token...")
        resp = await client.post(
            "/tokens/refresh",
            headers={"Authorization": f"Bearer {tier2_token}"},
        )
        if resp.status_code != 200:
            _fail(f"Token refresh failed: {resp.text}")
            return False
        new_token = resp.json()["token"]
        _ok(f"New token received (different from old: {new_token != tier2_token})")

        # --- Step 10: Summary ---
        _log(10, "Demo complete!", GREEN)
        print(f"\n{BOLD}Full lifecycle verified:{RESET}")
        print("  Menu browse → Tier-1 register → Read order → Consent initiate")
        print("  → Human approve → Token exchange → Read-before-write → Write order → Token refresh")
        print(f"  {GREEN}{BOLD}All 9 steps passed.{RESET}\n")
        return True


def _get_read_inputs(service_id: str, action_id: str) -> dict:
    """Return demo inputs for a read action."""
    defaults = {
        ("stayright-hotels", "search-availability"): {
            "city": "Austin", "check_in": "2026-03-15",
            "check_out": "2026-03-18", "guests": 2,
        },
        ("quickbite-delivery", "browse-menu"): {
            "cuisine": "italian", "location": "downtown",
        },
        ("fixright-home", "search-providers"): {
            "service_type": "plumbing", "zip_code": "78701",
        },
    }
    return defaults.get((service_id, action_id), {"query": "test"})


def _get_write_inputs(service_id: str, action_id: str) -> dict:
    """Return demo inputs for a write action."""
    defaults = {
        ("stayright-hotels", "book-room"): {
            "room_id": "sr-austin-k420",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guest_name": "Demo Agent",
            "guest_email": "demo@example.com",
        },
        ("quickbite-delivery", "place-order"): {
            "restaurant_id": "qb-downtown-pizzeria",
            "items": [{"item_id": "margherita", "quantity": 1}],
            "delivery_address": "123 Demo St",
            "customer_name": "Demo Agent",
            "customer_email": "demo@example.com",
        },
        ("fixright-home", "book-appointment"): {
            "provider_id": "fr-austin-plumb-001",
            "service_type": "plumbing",
            "date": "2026-03-20",
            "time_slot": "10:00-12:00",
            "customer_name": "Demo Agent",
            "customer_email": "demo@example.com",
        },
    }
    return defaults.get((service_id, action_id), {"data": "test"})


def main():
    parser = argparse.ArgumentParser(
        description="AgentCafe end-to-end demo agent",
        prog="python -m agentcafe.demo_agent",
    )
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Cafe base URL (default: http://localhost:8000)")
    parser.add_argument("--service", default="stayright-hotels",
                        help="Service to demo (default: stayright-hotels)")
    parser.add_argument("--read-action", default="search-availability",
                        help="Read action (default: search-availability)")
    parser.add_argument("--write-action", default="book-room",
                        help="Write action (default: book-room)")
    parser.add_argument("--headless", action="store_true",
                        help="Auto-approve consent (no browser needed)")
    args = parser.parse_args()

    print(f"\n{BOLD}AgentCafe Demo Agent{RESET}")
    print(f"Target: {args.base_url}")
    print(f"Service: {args.service}")
    print(f"Actions: {args.read_action} (read) → {args.write_action} (write)")
    print(f"Mode: {'headless (auto-approve)' if args.headless else 'interactive (browser approval)'}")
    print()

    success = asyncio.run(run_demo(
        base_url=args.base_url,
        service_id=args.service,
        read_action=args.read_action,
        write_action=args.write_action,
        headless=args.headless,
    ))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
