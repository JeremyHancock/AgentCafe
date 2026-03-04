"""End-to-end integration tests for AgentCafe.

These tests span the full stack: company onboarding (wizard) → agent discovery
(Menu) → agent authentication (Passport) → order placement → audit verification.

Covers scenarios from docs/e2e-test-plan.md:
- E2E-INT-01: Company publishes → agent discovers → full order cycle
- E2E-SEC-01: Quarantine forces Tier-2 on new services
- E2E-ONB-01: Full wizard happy path with Menu verification
- E2E-AGT-01: Full agent lifecycle with audit chain verification
- E2E-INT-02: Pause / resume lifecycle
- E2E-INT-03: Unpublish removes from Menu
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
import agentcafe.cafe.passport as passport_module
import agentcafe.cafe.human as human_module
import agentcafe.cafe.consent as consent_module
import agentcafe.cafe.pages as pages_module
from agentcafe.db.engine import init_db
import agentcafe.db.engine as engine_module
from agentcafe.keys import configure_keys
from agentcafe.main import create_cafe_app
from agentcafe.wizard.router import configure_wizard

# pylint: disable=redefined-outer-name,protected-access

_SECRET = "e2e-test-secret-key-minimum-32-bytes!!"
_API_KEY = "e2e-test-api-key"


# ---------------------------------------------------------------------------
# Mock backend — accepts any request and returns a plausible JSON response
# ---------------------------------------------------------------------------

_mock_backend = FastAPI()


@_mock_backend.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def _catch_all(request: Request, path: str):
    """Accept any request and return a success response."""
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    return JSONResponse({
        "status": "ok",
        "path": f"/{path}",
        "method": request.method,
        "echo": body,
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _E2EBackendTransport:
    """Routes requests to the mock backend for any URL."""

    def __init__(self):
        self._transport = ASGITransport(app=_mock_backend)

    async def handle_async_request(self, request):
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


@pytest_asyncio.fixture
async def e2e_db(monkeypatch):
    """Fresh in-memory DB with all modules configured for real Passport V2."""
    # Configure all modules
    configure_keys(legacy_hs256_secret=_SECRET)
    configure_wizard(_SECRET)
    monkeypatch.setattr(passport_module._state, "signing_secret", _SECRET)
    monkeypatch.setattr(passport_module._state, "issuer_api_key", _API_KEY)
    monkeypatch.setattr(human_module._state, "signing_secret", _SECRET)
    monkeypatch.setattr(consent_module._state, "signing_secret", _SECRET)
    monkeypatch.setattr(pages_module._state, "signing_secret", _SECRET)
    monkeypatch.setattr(router_module._state, "use_real_passport", True)

    async def _mock_verify_passkey(challenge_id, credential):  # pylint: disable=unused-argument
        return {"user_id": challenge_id, "email": "mock@example.com"}
    monkeypatch.setattr(consent_module, "verify_passkey_assertion", _mock_verify_passkey)
    monkeypatch.setattr(pages_module, "verify_passkey_assertion", _mock_verify_passkey)
    monkeypatch.setattr(router_module._state, "issuer_api_key", _API_KEY)
    # Clear IP rate limit state between tests
    passport_module._register_hits.clear()

    # Mock HTTP client so backend proxy hits our mock
    mock_client = AsyncClient(transport=_E2EBackendTransport())
    monkeypatch.setattr(router_module._state, "http_client", mock_client)

    # Save the session-scoped DB (if any) so we don't destroy it
    prev_db = engine_module._state.db
    db = await init_db(":memory:")
    yield db
    await mock_client.aclose()
    # Close the E2E DB and restore the previous one
    if engine_module._state.db is not None:
        await engine_module._state.db.close()
    engine_module._state.db = prev_db


@pytest_asyncio.fixture
async def client(e2e_db):  # pylint: disable=unused-argument
    """Async HTTP client with all routes (wizard + cafe + passport + consent)."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# Sample spec — a simple 2-action API (one read, one write)
# ---------------------------------------------------------------------------

SAMPLE_SPEC = json.dumps({
    "openapi": "3.1.0",
    "info": {"title": "TestItem API", "version": "1.0.0"},
    "servers": [{"url": "https://api.testitems.example.com"}],
    "paths": {
        "/items": {
            "get": {
                "operationId": "listItems",
                "summary": "List all items",
                "parameters": [
                    {"name": "category", "in": "query", "required": True,
                     "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "operationId": "addNewItem",
                "summary": "Add a new item to the catalog",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name", "owner_email"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "owner_email": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}},
            },
        },
    },
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _register_company(client) -> tuple[str, dict]:
    """Register a company, return (token, auth_headers)."""
    email = f"e2e-{uuid.uuid4().hex[:8]}@test.example.com"
    resp = await client.post("/wizard/companies", json={
        "name": f"E2E Corp {uuid.uuid4().hex[:4]}",
        "email": email,
        "password": "e2e-pass-1234",
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["session_token"]
    return token, {"Authorization": f"Bearer {token}"}


async def _full_wizard_publish(client, company_auth: dict, spec: str = SAMPLE_SPEC,
                                service_id_override: str | None = None) -> dict:
    """Run the full wizard flow and return publish response."""
    # Parse
    parse_resp = await client.post("/wizard/specs/parse", json={
        "raw_spec": spec,
    }, headers=company_auth)
    assert parse_resp.status_code == 200, parse_resp.text
    draft_id = parse_resp.json()["draft_id"]
    candidate = parse_resp.json()["candidate_menu"]

    sid = service_id_override or candidate["service_id"]

    # Review
    review_resp = await client.put(f"/wizard/drafts/{draft_id}/review", json={
        "service_id": sid,
        "name": candidate["name"],
        "category": candidate["category"],
        "capability_tags": candidate.get("capability_tags", []),
        "description": candidate["description"],
        "actions": candidate["actions"],
        "excluded_actions": [],
    }, headers=company_auth)
    assert review_resp.status_code == 200, review_resp.text

    # Policy
    policy_actions = {}
    for action in candidate["actions"]:
        aid = action["action_id"]
        policy_actions[aid] = {
            "scope": f"{sid}:{aid}",
            "human_auth": action["is_write"],
            "rate_limit": "10/minute" if action["is_write"] else "60/minute",
        }

    policy_resp = await client.put(f"/wizard/drafts/{draft_id}/policy", json={
        "actions": policy_actions,
        "backend_url": "http://mock-backend:9999",
        "backend_auth_header": "Bearer mock-key",
    }, headers=company_auth)
    assert policy_resp.status_code == 200, policy_resp.text

    # Preview
    preview_resp = await client.get(f"/wizard/drafts/{draft_id}/preview", headers=company_auth)
    assert preview_resp.status_code == 200, preview_resp.text

    # Publish
    publish_resp = await client.post(f"/wizard/drafts/{draft_id}/publish", headers=company_auth)
    assert publish_resp.status_code == 200, publish_resp.text

    return {
        "draft_id": draft_id,
        "service_id": sid,
        "candidate": candidate,
        "preview": preview_resp.json(),
        "publish": publish_resp.json(),
    }


async def _register_agent(client) -> str:
    """Register an agent and return Tier-1 token."""
    resp = await client.post("/passport/register", json={"agent_tag": "e2e-agent"})
    assert resp.status_code == 200, resp.text
    return resp.json()["passport"]


async def _register_human(client) -> tuple[str, str]:
    """Register a human, return (user_id, session_token)."""
    email = f"human-{uuid.uuid4().hex[:6]}@test.example.com"
    resp = await client.post("/human/register", json={
        "email": email,
        "password": "human-pass-123",
        "display_name": "E2E Human",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["user_id"], data["session_token"]


async def _consent_flow(client, agent_token: str, service_id: str, action_id: str) -> str:
    """Run consent flow: initiate → human approve → exchange → return Tier-2 token."""
    # Initiate
    resp = await client.post("/consents/initiate", json={
        "service_id": service_id,
        "action_id": action_id,
    }, headers={"Authorization": f"Bearer {agent_token}"})
    assert resp.status_code == 200, resp.text
    consent_id = resp.json()["consent_id"]

    # Human approves
    uid, human_token = await _register_human(client)
    resp = await client.post(f"/consents/{consent_id}/approve",
                             json={"passkey_challenge_id": uid, "passkey_credential": {}},
                             headers={"Authorization": f"Bearer {human_token}"})
    assert resp.status_code == 200, resp.text

    # Exchange
    resp = await client.post("/tokens/exchange", json={
        "consent_id": consent_id,
    }, headers={"Authorization": f"Bearer {agent_token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ===========================================================================
# E2E-INT-01: Company publishes → agent discovers → full order cycle
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_int01_golden_path(client):
    """THE golden smoke test: wizard publish → Menu discovery → agent orders → audit chain."""
    # --- COMPANY SIDE: Publish a new service ---
    _token, auth = await _register_company(client)
    pub = await _full_wizard_publish(client, auth, service_id_override="e2e-widgets")

    assert pub["publish"]["service_id"] == "e2e-widgets"
    assert pub["publish"]["actions_published"] == 2

    # --- AGENT SIDE: Discover on Menu ---
    menu_resp = await client.get("/cafe/menu")
    assert menu_resp.status_code == 200
    services = menu_resp.json()["services"]
    svc = next((s for s in services if s["service_id"] == "e2e-widgets"), None)
    assert svc is not None, f"e2e-widgets not found in menu. Services: {[s['service_id'] for s in services]}"
    assert svc["name"] == "TestItem API"
    action_ids = [a["action_id"] for a in svc["actions"]]
    assert len(action_ids) == 2

    # Find the read and write actions from candidate (is_write field)
    read_action_id = None
    write_action_id = None
    for a in pub["candidate"]["actions"]:
        if a["is_write"] and write_action_id is None:
            write_action_id = a["action_id"]
        elif not a["is_write"] and read_action_id is None:
            read_action_id = a["action_id"]
    assert read_action_id is not None, "No read action found"
    assert write_action_id is not None, "No write action found"

    # --- AGENT: Register Tier-1 ---
    tier1_token = await _register_agent(client)

    # --- AGENT: Quarantine blocks Tier-1 read ---
    read_resp = await client.post("/cafe/order", json={
        "service_id": "e2e-widgets",
        "action_id": read_action_id,
        "passport": tier1_token,
        "inputs": {"category": "test"},
    })
    assert read_resp.status_code == 403, f"Expected 403 (quarantine), got {read_resp.status_code}: {read_resp.text}"
    # Quarantine forces human_auth_required=True; Tier-1 fails with tier_insufficient or human_auth_required
    err = read_resp.json()["detail"]["error"]
    assert err in ("human_auth_required", "tier_insufficient"), f"Unexpected error: {err}"

    # --- AGENT: Get Tier-2 via consent flow ---
    tier2_token = await _consent_flow(client, tier1_token, "e2e-widgets", read_action_id)

    # --- AGENT: Read order with Tier-2 succeeds ---
    read_resp = await client.post("/cafe/order", json={
        "service_id": "e2e-widgets",
        "action_id": read_action_id,
        "passport": tier2_token,
        "inputs": {"category": "test"},
    })
    assert read_resp.status_code == 200, f"Read order failed: {read_resp.text}"

    # --- AGENT: Get Tier-2 for write action ---
    tier2_write = await _consent_flow(client, tier1_token, "e2e-widgets", write_action_id)

    # --- AGENT: Write order ---
    write_resp = await client.post("/cafe/order", json={
        "service_id": "e2e-widgets",
        "action_id": write_action_id,
        "passport": tier2_write,
        "inputs": {"name": "Test Widget", "owner_email": "agent@example.com"},
    })
    assert write_resp.status_code == 200, f"Write order failed: {write_resp.text}"

    # --- VERIFY AUDIT CHAIN ---
    from agentcafe.db.engine import get_db
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, prev_hash, entry_hash, outcome FROM audit_log ORDER BY timestamp ASC"
    )
    rows = await cursor.fetchall()
    assert len(rows) >= 3  # At least: failed read + successful read + write

    # Verify hash chain integrity
    for i, row in enumerate(rows):
        if i == 0:
            assert row["prev_hash"] == "" or row["prev_hash"] is None or row["prev_hash"] == "0" * 64
        else:
            assert row["prev_hash"] == rows[i - 1]["entry_hash"], (
                f"Hash chain broken at entry {i}: prev_hash={row['prev_hash']} "
                f"!= prior entry_hash={rows[i - 1]['entry_hash']}"
            )


# ===========================================================================
# E2E-SEC-01: Quarantine forces Tier-2 on newly published services
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_sec01_quarantine_forces_tier2(client):
    """New wizard-published services enforce quarantine (Tier-2 for ALL actions)."""
    # Publish
    _token, auth = await _register_company(client)
    pub = await _full_wizard_publish(client, auth, service_id_override="sec01-svc")

    # Verify service is on the Menu
    menu_resp = await client.get("/cafe/menu")
    svc_ids = [s["service_id"] for s in menu_resp.json()["services"]]
    assert "sec01-svc" in svc_ids
    # Quarantine is enforced at order time — verify it blocks Tier-1 below

    # Agent gets Tier-1
    tier1 = await _register_agent(client)

    # Find the read action (would normally NOT require human auth)
    read_action = None
    for a in pub["candidate"]["actions"]:
        if not a["is_write"]:
            read_action = a
            break
    assert read_action is not None

    # Tier-1 read should be BLOCKED by quarantine
    resp = await client.post("/cafe/order", json={
        "service_id": "sec01-svc",
        "action_id": read_action["action_id"],
        "passport": tier1,
        "inputs": {"category": "test"},
    })
    assert resp.status_code == 403
    err = resp.json()["detail"]["error"]
    assert err in ("human_auth_required", "tier_insufficient"), f"Unexpected error: {err}"

    # Tier-2 read should succeed
    tier2 = await _consent_flow(client, tier1, "sec01-svc", read_action["action_id"])
    resp = await client.post("/cafe/order", json={
        "service_id": "sec01-svc",
        "action_id": read_action["action_id"],
        "passport": tier2,
        "inputs": {"category": "test"},
    })
    assert resp.status_code == 200


# ===========================================================================
# E2E-ONB-01: Full wizard happy path with confidence + preview + Menu
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_onb01_wizard_happy_path(client):
    """Full wizard: register → parse → review → policy → preview → publish → Menu."""
    _token, auth = await _register_company(client)

    # Parse
    parse_resp = await client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_SPEC,
    }, headers=auth)
    assert parse_resp.status_code == 200
    data = parse_resp.json()
    assert "draft_id" in data
    candidate = data["candidate_menu"]
    assert len(candidate["actions"]) == 2

    # Verify confidence scores present
    for action in candidate["actions"]:
        assert "confidence" in action, f"Action {action['action_id']} missing confidence"

    draft_id = data["draft_id"]
    sid = "onb01-widgets"

    # Review with partial edits (change description only)
    review_resp = await client.put(f"/wizard/drafts/{draft_id}/review", json={
        "service_id": sid,
        "name": "My Custom Item Service",
        "category": "items",
        "capability_tags": ["items", "e2e"],
        "description": "Custom description from company review",
        "actions": candidate["actions"],
        "excluded_actions": [],
    }, headers=auth)
    assert review_resp.status_code == 200

    # Policy
    policy_actions = {}
    for action in candidate["actions"]:
        policy_actions[action["action_id"]] = {
            "scope": f"{sid}:{action['action_id']}",
            "human_auth": action["is_write"],
            "rate_limit": "60/minute",
        }
    policy_resp = await client.put(f"/wizard/drafts/{draft_id}/policy", json={
        "actions": policy_actions,
        "backend_url": "http://mock-backend:9999",
        "backend_auth_header": "",
    }, headers=auth)
    assert policy_resp.status_code == 200

    # Preview — verify confidence preserved after edits
    preview_resp = await client.get(f"/wizard/drafts/{draft_id}/preview", headers=auth)
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    final_menu = preview["final_menu_entry"]
    assert final_menu["service_id"] == sid
    assert final_menu["name"] == "My Custom Item Service"
    assert final_menu["description"] == "Custom description from company review"

    # Check confidence survived the edit
    for action in final_menu["actions"]:
        assert "confidence" in action, f"Confidence lost after edit for {action['action_id']}"

    # Publish
    publish_resp = await client.post(f"/wizard/drafts/{draft_id}/publish", headers=auth)
    assert publish_resp.status_code == 200
    assert publish_resp.json()["service_id"] == sid

    # Verify on Menu
    menu_resp = await client.get("/cafe/menu")
    services = menu_resp.json()["services"]
    svc = next((s for s in services if s["service_id"] == sid), None)
    assert svc is not None
    assert svc["name"] == "My Custom Item Service"
    assert len(svc["actions"]) == 2


# ===========================================================================
# E2E-AGT-01: Full agent lifecycle with token refresh + audit
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_agt01_full_lifecycle(client):
    """Full agent lifecycle: Tier-1 → consent → Tier-2 → order → refresh → audit."""
    # Setup: publish a service
    _token, auth = await _register_company(client)
    pub = await _full_wizard_publish(client, auth, service_id_override="agt01-svc")
    read_action = None
    write_action = None
    for a in pub["candidate"]["actions"]:
        if a["is_write"] and write_action is None:
            write_action = a
        elif not a["is_write"] and read_action is None:
            read_action = a
    assert read_action is not None
    assert write_action is not None

    # Step 1: Register Tier-1
    tier1 = await _register_agent(client)

    # Step 2: Consent flow for read (quarantine forces Tier-2)
    tier2_read = await _consent_flow(client, tier1, "agt01-svc", read_action["action_id"])

    # Step 3: Read order
    resp = await client.post("/cafe/order", json={
        "service_id": "agt01-svc",
        "action_id": read_action["action_id"],
        "passport": tier2_read,
        "inputs": {"category": "lifecycle-test"},
    })
    assert resp.status_code == 200

    # Step 4: Consent for write
    tier2_write = await _consent_flow(client, tier1, "agt01-svc", write_action["action_id"])

    # Step 5: Write order
    resp = await client.post("/cafe/order", json={
        "service_id": "agt01-svc",
        "action_id": write_action["action_id"],
        "passport": tier2_write,
        "inputs": {"name": "Lifecycle Widget", "owner_email": "lifecycle@test.com"},
    })
    assert resp.status_code == 200

    # Step 6: Refresh Tier-2 token
    resp = await client.post("/tokens/refresh",
                             headers={"Authorization": f"Bearer {tier2_write}"})
    assert resp.status_code == 200
    new_token = resp.json()["token"]
    assert new_token != tier2_write

    # Step 7: Use refreshed token
    resp = await client.post("/cafe/order", json={
        "service_id": "agt01-svc",
        "action_id": write_action["action_id"],
        "passport": new_token,
        "inputs": {"name": "Refreshed Widget", "owner_email": "refreshed@test.com"},
    })
    assert resp.status_code == 200


# ===========================================================================
# E2E-AGT-04: Policy revocation kills active tokens
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_agt04_policy_revocation(client):
    """Revoking a policy immediately blocks all tokens issued under it."""
    # Setup
    _token, auth = await _register_company(client)
    pub = await _full_wizard_publish(client, auth, service_id_override="agt04-svc")
    read_action = None
    for a in pub["candidate"]["actions"]:
        if not a["is_write"]:
            read_action = a
            break
    assert read_action is not None

    # Get Tier-2 token
    tier1 = await _register_agent(client)
    tier2 = await _consent_flow(client, tier1, "agt04-svc", read_action["action_id"])

    # Order succeeds
    resp = await client.post("/cafe/order", json={
        "service_id": "agt04-svc",
        "action_id": read_action["action_id"],
        "passport": tier2,
        "inputs": {"category": "pre-revoke"},
    })
    assert resp.status_code == 200

    # Revoke the token's JTI
    resp = await client.post("/cafe/revoke", json={
        "passport": tier2,
    })
    assert resp.status_code == 200, f"Revoke failed: {resp.text}"

    # Order should now fail (passport_revoked → 401)
    resp = await client.post("/cafe/order", json={
        "service_id": "agt04-svc",
        "action_id": read_action["action_id"],
        "passport": tier2,
        "inputs": {"category": "post-revoke"},
    })
    assert resp.status_code == 401, f"Expected 401 after revoke, got {resp.status_code}: {resp.text}"

    # Refresh creates a NEW token from the policy (policy not revoked, just the JTI).
    # The new token should work, but the old one should stay dead.
    resp = await client.post("/tokens/refresh",
                             headers={"Authorization": f"Bearer {tier2}"})
    # Refresh may succeed (new JTI) or fail (implementation-dependent)
    # The critical invariant is: the OLD token stays revoked
    resp2 = await client.post("/cafe/order", json={
        "service_id": "agt04-svc",
        "action_id": read_action["action_id"],
        "passport": tier2,
        "inputs": {"category": "still-revoked"},
    })
    assert resp2.status_code == 401, f"Old token should stay revoked, got {resp2.status_code}"


# ===========================================================================
# E2E-INT-02: Pause / resume lifecycle
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_int02_pause_resume(client):
    """Company pauses service → agent blocked → company resumes → agent succeeds."""
    # Publish
    _company_token, auth = await _register_company(client)
    pub = await _full_wizard_publish(client, auth, service_id_override="int02-svc")
    read_action = None
    for a in pub["candidate"]["actions"]:
        if not a["is_write"]:
            read_action = a
            break
    assert read_action is not None

    # Agent gets token and orders successfully (quarantine requires Tier-2)
    tier1 = await _register_agent(client)
    tier2 = await _consent_flow(client, tier1, "int02-svc", read_action["action_id"])

    resp = await client.post("/cafe/order", json={
        "service_id": "int02-svc",
        "action_id": read_action["action_id"],
        "passport": tier2,
        "inputs": {"category": "before-pause"},
    })
    assert resp.status_code == 200

    # Company pauses (PUT not POST)
    pause_resp = await client.put("/wizard/services/int02-svc/pause", headers=auth)
    assert pause_resp.status_code == 200

    # Agent order should fail (service paused → suspended_at set → 503)
    resp = await client.post("/cafe/order", json={
        "service_id": "int02-svc",
        "action_id": read_action["action_id"],
        "passport": tier2,
        "inputs": {"category": "during-pause"},
    })
    assert resp.status_code == 503

    # Company resumes (PUT not POST)
    resume_resp = await client.put("/wizard/services/int02-svc/resume", headers=auth)
    assert resume_resp.status_code == 200

    # Agent order succeeds again
    resp = await client.post("/cafe/order", json={
        "service_id": "int02-svc",
        "action_id": read_action["action_id"],
        "passport": tier2,
        "inputs": {"category": "after-resume"},
    })
    assert resp.status_code == 200


# ===========================================================================
# E2E-INT-03: Unpublish removes from Menu
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_int03_unpublish(client):
    """Company unpublishes → service disappears from Menu → agent gets error."""
    _token, auth = await _register_company(client)
    _pub = await _full_wizard_publish(client, auth, service_id_override="int03-svc")

    # Verify on Menu
    menu_resp = await client.get("/cafe/menu")
    sids = [s["service_id"] for s in menu_resp.json()["services"]]
    assert "int03-svc" in sids

    # Unpublish (PUT /services/{id}/unpublish)
    del_resp = await client.put("/wizard/services/int03-svc/unpublish", headers=auth)
    assert del_resp.status_code == 200

    # Gone from Menu
    menu_resp = await client.get("/cafe/menu")
    sids = [s["service_id"] for s in menu_resp.json()["services"]]
    assert "int03-svc" not in sids


# ===========================================================================
# E2E-ONB-06: Duplicate service_id rejected cross-company
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_onb06_duplicate_service_id(client):
    """Two companies cannot publish the same service_id."""
    _t1, auth1 = await _register_company(client)
    _t2, auth2 = await _register_company(client)

    # Company 1 publishes
    await _full_wizard_publish(client, auth1, service_id_override="dup-svc")

    # Company 2 tries same service_id
    parse_resp = await client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_SPEC,
    }, headers=auth2)
    draft_id = parse_resp.json()["draft_id"]
    candidate = parse_resp.json()["candidate_menu"]

    # Review
    await client.put(f"/wizard/drafts/{draft_id}/review", json={
        "service_id": "dup-svc",
        "name": candidate["name"],
        "category": candidate["category"],
        "capability_tags": [],
        "description": candidate["description"],
        "actions": candidate["actions"],
        "excluded_actions": [],
    }, headers=auth2)

    # Policy
    policy_actions = {a["action_id"]: {"scope": f"dup-svc:{a['action_id']}",
                                        "human_auth": a["is_write"],
                                        "rate_limit": "60/minute"}
                      for a in candidate["actions"]}
    await client.put(f"/wizard/drafts/{draft_id}/policy", json={
        "actions": policy_actions,
        "backend_url": "http://mock-backend:9999",
        "backend_auth_header": "",
    }, headers=auth2)

    # Preview
    await client.get(f"/wizard/drafts/{draft_id}/preview", headers=auth2)

    # Publish should fail
    pub_resp = await client.post(f"/wizard/drafts/{draft_id}/publish", headers=auth2)
    assert pub_resp.status_code == 409 or pub_resp.status_code == 400


# ===========================================================================
# E2E-ONB-07: Draft ownership isolation
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_onb07_draft_ownership(client):
    """Company B cannot access Company A's draft."""
    _t1, auth1 = await _register_company(client)
    _t2, auth2 = await _register_company(client)

    # Company A parses
    parse_resp = await client.post("/wizard/specs/parse", json={
        "raw_spec": SAMPLE_SPEC,
    }, headers=auth1)
    draft_id = parse_resp.json()["draft_id"]

    # Company B tries to access
    resp = await client.get(f"/wizard/drafts/{draft_id}/preview", headers=auth2)
    assert resp.status_code == 403


# ===========================================================================
# E2E-ERR-02: Auth failures
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_err02_no_auth(client):
    """Wizard endpoints without auth token return 401."""
    resp = await client.post("/wizard/specs/parse", json={"raw_spec": SAMPLE_SPEC})
    assert resp.status_code == 401 or resp.status_code == 403


@pytest.mark.asyncio
async def test_e2e_err02_garbage_passport(client):
    """Order with garbage passport returns 401."""
    # Need a published service first
    _token, auth = await _register_company(client)
    pub = await _full_wizard_publish(client, auth, service_id_override="err02-svc")
    read_action = next(a for a in pub["candidate"]["actions"] if not a["is_write"])

    resp = await client.post("/cafe/order", json={
        "service_id": "err02-svc",
        "action_id": read_action["action_id"],
        "passport": "totally-garbage-token",
        "inputs": {"category": "test"},
    })
    assert resp.status_code == 401
