"""Tests for the Passport V2 consent flow.

Covers: human registration/login, consent initiation, approval,
token exchange, token refresh, policy revocation, concurrent token cap.
"""

from __future__ import annotations

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
import agentcafe.cafe.passport as passport_module
import agentcafe.cafe.human as human_module
import agentcafe.cafe.consent as consent_module
import agentcafe.cafe.pages as pages_module
from agentcafe.demo_backends.hotel import app as hotel_app
from agentcafe.demo_backends.lunch import app as lunch_app
from agentcafe.demo_backends.home_service import app as home_service_app

# pylint: disable=redefined-outer-name,protected-access

TEST_SECRET = "test-secret-key-for-consent-flow-testing"
TEST_API_KEY = "test-issuer-api-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _configure_modules(monkeypatch):
    """Configure all modules with test secrets."""
    monkeypatch.setattr(router_module._state, "use_real_passport", True)
    monkeypatch.setattr(passport_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(passport_module._state, "issuer_api_key", TEST_API_KEY)
    monkeypatch.setattr(human_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(consent_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(pages_module._state, "signing_secret", TEST_SECRET)
    yield


_BACKEND_APPS = {
    "http://127.0.0.1:8001": hotel_app,
    "http://127.0.0.1:8002": lunch_app,
    "http://127.0.0.1:8003": home_service_app,
}


class _MultiBackendTransport:
    def __init__(self):
        self._transports = {
            base: ASGITransport(app=app) for base, app in _BACKEND_APPS.items()
        }

    async def handle_async_request(self, request):
        url = str(request.url)
        for base, transport in self._transports.items():
            if url.startswith(base):
                return await transport.handle_async_request(request)
        raise RuntimeError(f"No backend transport for URL: {url}")

    async def aclose(self) -> None:
        for transport in self._transports.values():
            await transport.aclose()


@pytest_asyncio.fixture(autouse=True)
async def _mock_http_client(monkeypatch):
    mock_client = AsyncClient(transport=_MultiBackendTransport())
    monkeypatch.setattr(router_module._state, "http_client", mock_client)
    yield
    await mock_client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _register_agent(cafe_client) -> str:
    """Register an agent and return its Tier-1 token."""
    resp = await cafe_client.post("/passport/register", json={"agent_tag": "consent-test-bot"})
    assert resp.status_code == 200
    return resp.json()["passport"]


async def _register_human(cafe_client) -> tuple[str, str]:
    """Register a human and return (user_id, session_token)."""
    resp = await cafe_client.post("/human/register", json={
        "email": f"alice-{__import__('uuid').uuid4().hex[:6]}@example.com",
        "password": "secure-password-123",
        "display_name": "Alice",
    })
    assert resp.status_code == 200
    data = resp.json()
    return data["user_id"], data["session_token"]


async def _full_consent_flow(cafe_client, service_id="stayright-hotels", action_id="search-availability"):
    """Run the full consent flow and return (agent_token, tier2_token, policy_id)."""
    agent_token = await _register_agent(cafe_client)
    _user_id, human_session = await _register_human(cafe_client)

    # Initiate consent
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": service_id, "action_id": action_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    consent_id = resp.json()["consent_id"]

    # Human approves
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200
    policy_id = resp.json()["policy_id"]

    # Agent exchanges
    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    tier2_token = resp.json()["token"]

    return agent_token, tier2_token, policy_id


# ---------------------------------------------------------------------------
# Human account tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_human_register(cafe_client):
    """POST /human/register should create an account and return a session token."""
    resp = await cafe_client.post("/human/register", json={
        "email": "register-test@example.com",
        "password": "password123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "user_id" in data
    assert data["email"] == "register-test@example.com"
    assert "session_token" in data

    # Session token should have aud: human-dashboard
    payload = jwt.decode(data["session_token"], TEST_SECRET, algorithms=["HS256"], audience="human-dashboard")
    assert payload["aud"] == "human-dashboard"
    assert payload["user_id"] == data["user_id"]


@pytest.mark.asyncio
async def test_human_register_duplicate_email(cafe_client):
    """Registering the same email twice should return 409."""
    body = {"email": "dup-test@example.com", "password": "password123"}
    resp = await cafe_client.post("/human/register", json=body)
    assert resp.status_code == 200

    resp = await cafe_client.post("/human/register", json=body)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "email_exists"


@pytest.mark.asyncio
async def test_human_login(cafe_client):
    """POST /human/login should return a session token for valid credentials."""
    await cafe_client.post("/human/register", json={
        "email": "login-test@example.com", "password": "password123",
    })
    resp = await cafe_client.post("/human/login", json={
        "email": "login-test@example.com", "password": "password123",
    })
    assert resp.status_code == 200
    assert "session_token" in resp.json()


@pytest.mark.asyncio
async def test_human_login_bad_password(cafe_client):
    """Wrong password should return 401."""
    await cafe_client.post("/human/register", json={
        "email": "bad-pw@example.com", "password": "password123",
    })
    resp = await cafe_client.post("/human/login", json={
        "email": "bad-pw@example.com", "password": "wrong",
    })
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_credentials"


# ---------------------------------------------------------------------------
# Consent initiation tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initiate_consent(cafe_client):
    """POST /consents/initiate should create a pending consent."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert "consent_id" in data
    assert "consent_url" in data
    assert "expires_at" in data


@pytest.mark.asyncio
async def test_initiate_consent_bad_action(cafe_client):
    """Initiating consent for a nonexistent action should return 404."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "nonexistent"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_initiate_consent_no_passport(cafe_client):
    """Initiating consent without a Passport should return 401."""
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Consent status tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consent_status_pending(cafe_client):
    """A freshly created consent should have status 'pending'."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_consent_status_not_found(cafe_client):
    """Querying a nonexistent consent_id should return 404."""
    resp = await cafe_client.get("/consents/nonexistent-id/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Consent approval tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_consent(cafe_client):
    """Human approving a consent should create a policy."""
    agent_token = await _register_agent(cafe_client)
    _user_id, human_session = await _register_human(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert "policy_id" in data


@pytest.mark.asyncio
async def test_approve_consent_no_session(cafe_client):
    """Approving without a human session token should return 401."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_approve_consent_with_agent_token_rejected(cafe_client):
    """An agent Passport (aud: agentcafe) should be rejected for approval (requires aud: human-dashboard)."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    # Try to approve using agent token instead of human session
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_approve_already_approved(cafe_client):
    """Approving an already-approved consent should return 409."""
    agent_token = await _register_agent(cafe_client)
    _user_id, human_session = await _register_human(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    # First approval
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200

    # Second approval
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Token exchange tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exchange_returns_tier2_token(cafe_client):
    """Exchanging an approved consent should return a Tier-2 write token."""
    _agent_token, tier2_token, policy_id = await _full_consent_flow(cafe_client)

    payload = jwt.decode(tier2_token, TEST_SECRET, algorithms=["HS256"], audience="agentcafe")
    assert payload["tier"] == "write"
    assert payload["granted_by"] == "human_consent"
    assert payload["policy_id"] == policy_id
    assert "scopes" in payload


@pytest.mark.asyncio
async def test_exchange_pending_consent_rejected(cafe_client):
    """Exchanging a pending (not yet approved) consent should return 409."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "consent_not_approved"


@pytest.mark.asyncio
async def test_exchange_includes_policy_limits(cafe_client):
    """POST /tokens/exchange should include policy_limits in the response."""
    _agent_token, _tier2_token, _policy_id = await _full_consent_flow(cafe_client)
    # The _full_consent_flow already calls exchange — let's do another flow to check
    agent_token = await _register_agent(cafe_client)
    _user_id, human_session = await _register_human(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]
    await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "policy_limits" in data
    limits = data["policy_limits"]
    assert limits["active_tokens"] >= 1
    assert limits["max_active_tokens"] == 20


@pytest.mark.asyncio
async def test_refresh_includes_policy_limits(cafe_client):
    """POST /tokens/refresh should include policy_limits in the response."""
    _agent_token, tier2_token, _policy_id = await _full_consent_flow(cafe_client)
    resp = await cafe_client.post(
        "/tokens/refresh",
        headers={"Authorization": f"Bearer {tier2_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "policy_limits" in data
    assert data["policy_limits"]["active_tokens"] >= 2  # original + refreshed
    assert data["policy_limits"]["max_active_tokens"] == 20


@pytest.mark.asyncio
async def test_tier2_token_can_order_read_action(cafe_client):
    """A Tier-2 token should work for read actions covered by its scopes."""
    _agent_token, tier2_token, _policy_id = await _full_consent_flow(cafe_client)

    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": tier2_token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Token refresh tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_tier2_token(cafe_client):
    """Refreshing a Tier-2 token should return a new token under the same policy."""
    _agent_token, tier2_token, policy_id = await _full_consent_flow(cafe_client)

    resp = await cafe_client.post(
        "/tokens/refresh",
        headers={"Authorization": f"Bearer {tier2_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["policy_id"] == policy_id
    assert data["tier"] == "write"

    # New token should be different from the old one
    assert data["token"] != tier2_token


@pytest.mark.asyncio
async def test_refresh_tier1_token_rejected(cafe_client):
    """Refreshing a Tier-1 (read) token should return 403."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/tokens/refresh",
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "tier_insufficient"


@pytest.mark.asyncio
async def test_refresh_revoked_policy_rejected(cafe_client):
    """Refreshing a token under a revoked policy should return 401."""
    from agentcafe.db.engine import get_db
    from datetime import datetime, timezone

    _agent_token, tier2_token, policy_id = await _full_consent_flow(cafe_client)

    # Revoke the policy
    db = await get_db()
    await db.execute(
        "UPDATE policies SET revoked_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), policy_id),
    )
    await db.commit()

    resp = await cafe_client.post(
        "/tokens/refresh",
        headers={"Authorization": f"Bearer {tier2_token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "policy_revoked"


# ---------------------------------------------------------------------------
# Risk-tier token ceiling tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ceiling_caps_requested_lifetime(cafe_client):
    """Human requesting a lifetime above the risk-tier ceiling should be capped."""
    agent_token = await _register_agent(cafe_client)
    _user_id, human_session = await _register_human(cafe_client)

    # search-availability is low risk (ceiling=3600s). Request 7200s (2 hours).
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"token_lifetime_seconds": 7200},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200

    # Exchange and check the token expiry — should be capped at 3600s (1 hour), not 7200s
    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    token_data = resp.json()
    payload = jwt.decode(token_data["token"], TEST_SECRET, algorithms=["HS256"], audience="agentcafe")

    # Token lifetime should be at most ceiling (3600s for low risk)
    lifetime = payload["exp"] - payload["iat"]
    assert lifetime <= 3600


@pytest.mark.asyncio
async def test_high_risk_cancel_gets_short_ceiling(cafe_client):
    """Cancel actions (high risk) should get a short token ceiling (300s)."""
    agent_token = await _register_agent(cafe_client)
    _user_id, human_session = await _register_human(cafe_client)

    # cancel-booking is high risk (ceiling=300s)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "cancel-booking"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"token_lifetime_seconds": 1800},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200

    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    payload = jwt.decode(resp.json()["token"], TEST_SECRET, algorithms=["HS256"], audience="agentcafe")

    lifetime = payload["exp"] - payload["iat"]
    assert lifetime <= 300


# ---------------------------------------------------------------------------
# Full flow integration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_consent_flow_end_to_end(cafe_client):
    """Complete flow: register agent → register human → initiate → approve → exchange → order."""
    _agent_token, tier2_token, policy_id = await _full_consent_flow(
        cafe_client, service_id="stayright-hotels", action_id="search-availability"
    )

    # Use the Tier-2 token to make an order
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": tier2_token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200
    assert "results" in resp.json()

    # Consent status should show approved
    # Extract consent_id from the flow — re-run initiate to get a new one and check the old pattern
    # Instead, verify the token claims
    payload = jwt.decode(tier2_token, TEST_SECRET, algorithms=["HS256"], audience="agentcafe")
    assert payload["tier"] == "write"
    assert payload["policy_id"] == policy_id
    assert payload["granted_by"] == "human_consent"


# ---------------------------------------------------------------------------
# Identity verification tests (v2-spec.md §7 — read-before-write)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_without_prior_read_rejected(cafe_client):
    """A write action on a medium-risk service without a prior read should be rejected."""
    # Get a Tier-2 token for book-room (medium risk, human_identifier_field=guest_email)
    _agent_token, tier2_token, _policy_id = await _full_consent_flow(
        cafe_client, service_id="stayright-hotels", action_id="book-room"
    )

    # Try to book without a prior search — should be rejected
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "book-room",
        "passport": tier2_token,
        "inputs": {
            "room_id": "sr-austin-k420",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guest_name": "Jane Smith",
            "guest_email": "jane@example.com",
        },
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "read_before_write_required"


@pytest.mark.asyncio
async def test_write_after_read_succeeds(cafe_client):
    """A write action after a successful read on the same service should pass."""
    _agent_token, tier2_token, _policy_id = await _full_consent_flow(
        cafe_client, service_id="stayright-hotels", action_id="book-room"
    )

    # First do a read action (search) to establish read-before-write
    # Use the Tier-2 token for the read (implicit read access on same service)
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": tier2_token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200

    # Now the write should succeed (read-before-write satisfied)
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "book-room",
        "passport": tier2_token,
        "inputs": {
            "room_id": "sr-austin-k420",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guest_name": "Jane Smith",
            "guest_email": "jane@example.com",
        },
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_write_missing_identifier_field_rejected(cafe_client):
    """A write action missing the human_identifier_field should be rejected with 422."""
    _agent_token, tier2_token, _policy_id = await _full_consent_flow(
        cafe_client, service_id="stayright-hotels", action_id="book-room"
    )

    # Do a read first to satisfy read-before-write
    await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": tier2_token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })

    # Try to book without guest_email (the human_identifier_field)
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "book-room",
        "passport": tier2_token,
        "inputs": {
            "room_id": "sr-austin-k420",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guest_name": "Jane Smith",
        },
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "identity_field_missing"


@pytest.mark.asyncio
async def test_low_risk_read_skips_identity_check(cafe_client):
    """Low-risk read actions should skip identity verification entirely."""
    agent_token = await _register_agent(cafe_client)

    # search-availability is low risk — no read-before-write required
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": agent_token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Consent privacy enforcement tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_consent_enumeration_endpoint(cafe_client):
    """There must be no endpoint to list/enumerate consents. Privacy by design."""
    agent_token = await _register_agent(cafe_client)
    # Try various discovery patterns — all should 404 or 405
    for path in ["/consents", "/consents/", "/consents?agent_tag=demo"]:
        resp = await cafe_client.get(
            path, headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code in (404, 405), f"{path} returned {resp.status_code}"


# ---------------------------------------------------------------------------
# Consent page UI tests (server-rendered Jinja2 pages)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_page_renders(cafe_client):
    """GET /login should return an HTML page."""
    resp = await cafe_client.get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Sign in to AgentCafe" in resp.text


@pytest.mark.asyncio
async def test_register_page_renders(cafe_client):
    """GET /register should return an HTML page."""
    resp = await cafe_client.get("/register")
    assert resp.status_code == 200
    assert "Create your Cafe account" in resp.text


@pytest.mark.asyncio
async def test_login_form_bad_password(cafe_client):
    """POST /login with wrong password should show error on the login page."""
    # Register first
    await cafe_client.post("/human/register", json={
        "email": "pagetest@example.com",
        "password": "secure-password-123",
    })
    resp = await cafe_client.post("/login", data={
        "email": "pagetest@example.com",
        "password": "wrong-password",
    }, follow_redirects=False)
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text


@pytest.mark.asyncio
async def test_login_sets_session_cookie(cafe_client):
    """POST /login with correct credentials should set a session cookie and redirect."""
    email = f"cookietest-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    resp = await cafe_client.post("/login", data={
        "email": email,
        "password": "secure-password-123",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "cafe_session" in resp.cookies


@pytest.mark.asyncio
async def test_consent_page_redirects_without_session(cafe_client):
    """GET /consent/<id> without a session cookie should redirect to login."""
    resp = await cafe_client.get(
        "/authorize/fake-id",
        cookies={"cafe_session": ""},  # override any persisted session cookie
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/register" in resp.headers["location"]


@pytest.mark.asyncio
async def test_consent_page_renders_for_logged_in_user(cafe_client):
    """GET /consent/<id> with a valid session should render the consent page."""
    # Set up: register human, get session cookie
    email = f"consentpage-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    login_resp = await cafe_client.post("/login", data={
        "email": email,
        "password": "secure-password-123",
    }, follow_redirects=False)
    session_cookie = login_resp.cookies.get("cafe_session")

    # Create a consent via API
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    # View the consent page with session cookie
    resp = await cafe_client.get(
        f"/authorize/{consent_id}",
        cookies={"cafe_session": session_cookie},
    )
    assert resp.status_code == 200
    assert "Authorization Request" in resp.text
    assert "StayRight Hotels" in resp.text


@pytest.mark.asyncio
async def test_consent_page_approve_via_form(cafe_client):
    """POST /consent/<id>/approve should approve and show success page."""
    email = f"approveform-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    login_resp = await cafe_client.post("/login", data={
        "email": email,
        "password": "secure-password-123",
    }, follow_redirects=False)
    session_cookie = login_resp.cookies.get("cafe_session")

    # Create consent
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    # Approve via form
    resp = await cafe_client.post(
        f"/authorize/{consent_id}/approve",
        data={"token_lifetime_seconds": "900"},
        cookies={"cafe_session": session_cookie},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Authorization Approved" in resp.text

    # Verify the consent is now approved via API
    resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_consent_page_decline(cafe_client):
    """GET /consent/<id>/decline should decline and show decline page."""
    email = f"declinetest-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    login_resp = await cafe_client.post("/login", data={
        "email": email,
        "password": "secure-password-123",
    }, follow_redirects=False)
    session_cookie = login_resp.cookies.get("cafe_session")

    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.get(
        f"/authorize/{consent_id}/decline",
        cookies={"cafe_session": session_cookie},
    )
    assert resp.status_code == 200
    assert "Authorization Declined" in resp.text

    # Verify declined via API
    resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert resp.json()["status"] == "declined"
