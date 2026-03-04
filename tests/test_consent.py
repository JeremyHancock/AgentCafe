"""Tests for the Passport V2 consent flow.

Covers: human registration/login, consent initiation, approval,
token exchange, token refresh, policy revocation, concurrent token cap.
"""

from __future__ import annotations

import re

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
from agentcafe.keys import configure_keys, decode_passport_token

# pylint: disable=redefined-outer-name,protected-access

TEST_SECRET = "test-secret-key-for-consent-flow-testing"
TEST_API_KEY = "test-issuer-api-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _mock_verify_passkey(challenge_id, credential):  # pylint: disable=unused-argument
    """Test mock: challenge_id is the user_id, credential is ignored.

    Real passkey verification is tested in test_webauthn.py.
    This mock lets consent tests exercise the full flow without
    requiring actual WebAuthn ceremonies.
    """
    return {"user_id": challenge_id, "email": "mock@example.com"}


# Counter so each mock registration creates a unique user
_mock_reg_counter = 0


async def _mock_complete_passkey_registration(challenge_id, credential):  # pylint: disable=unused-argument
    """Test mock for complete_passkey_registration.

    Creates a real user in the DB so consent approval has a valid cafe_user_id.
    Uses challenge_id as the email prefix to make results deterministic.
    """
    # pylint: disable=global-statement
    global _mock_reg_counter
    _mock_reg_counter += 1
    import uuid as _uuid
    from agentcafe.db.engine import get_db as _get_db
    from agentcafe.cafe.human import _create_human_session_token

    user_id = str(_uuid.uuid4())
    email = f"activate-{_mock_reg_counter}@test.example.com"
    db = await _get_db()
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    await db.execute(
        """INSERT INTO cafe_users (id, email, display_name, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, email, "Test User", "", now, now),
    )
    await db.commit()
    session_token = _create_human_session_token(user_id, email)
    return {
        "user_id": user_id,
        "email": email,
        "session_token": session_token,
        "credential_id": "mock-cred-id",
    }


@pytest_asyncio.fixture(autouse=True)
async def _configure_modules(monkeypatch):
    """Configure all modules with test secrets."""
    monkeypatch.setattr(router_module._state, "use_real_passport", True)
    monkeypatch.setattr(passport_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(passport_module._state, "issuer_api_key", TEST_API_KEY)
    monkeypatch.setattr(human_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(consent_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(pages_module._state, "signing_secret", TEST_SECRET)
    # Mock passkey verification — consent tests don't need real WebAuthn
    monkeypatch.setattr(consent_module, "verify_passkey_assertion", _mock_verify_passkey)
    monkeypatch.setattr(pages_module, "verify_passkey_assertion", _mock_verify_passkey)
    # Mock passkey registration for activation flow tests
    monkeypatch.setattr(pages_module, "complete_passkey_registration", _mock_complete_passkey_registration)
    configure_keys(legacy_hs256_secret=TEST_SECRET)
    # Clear IP rate limit state between tests
    passport_module._register_hits.clear()
    pages_module._activate_hits.clear()
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
    user_id, human_session = await _register_human(cafe_client)

    # Initiate consent
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": service_id, "action_id": action_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    consent_id = resp.json()["consent_id"]

    # Human approves (passkey_challenge_id=user_id for mock verification)
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
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
    user_id, human_session = await _register_human(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
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
        json={"passkey_challenge_id": "dummy", "passkey_credential": {}},
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
        json={"passkey_challenge_id": "dummy", "passkey_credential": {}},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_approve_already_approved(cafe_client):
    """Approving an already-approved consent should return 409."""
    agent_token = await _register_agent(cafe_client)
    user_id, human_session = await _register_human(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    # First approval
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200

    # Second approval
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
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

    payload = decode_passport_token(tier2_token)
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
    user_id, human_session = await _register_human(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]
    await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
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
    user_id, human_session = await _register_human(cafe_client)

    # search-availability is low risk (ceiling=3600s). Request 7200s (2 hours).
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"token_lifetime_seconds": 7200, "passkey_challenge_id": user_id, "passkey_credential": {}},
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
    payload = decode_passport_token(token_data["token"])

    # Token lifetime should be at most ceiling (3600s for low risk)
    lifetime = payload["exp"] - payload["iat"]
    assert lifetime <= 3600


@pytest.mark.asyncio
async def test_high_risk_cancel_gets_short_ceiling(cafe_client):
    """Cancel actions (high risk) should get a short token ceiling (300s)."""
    agent_token = await _register_agent(cafe_client)
    user_id, human_session = await _register_human(cafe_client)

    # cancel-booking is high risk (ceiling=300s)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "cancel-booking"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"token_lifetime_seconds": 1800, "passkey_challenge_id": user_id, "passkey_credential": {}},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200

    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    payload = decode_passport_token(resp.json()["token"])

    lifetime = payload["exp"] - payload["iat"]
    assert lifetime <= 300


# ---------------------------------------------------------------------------
# Multi-action consent tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_action_consent_initiate(cafe_client):
    """POST /consents/initiate with action_ids list should create a multi-action consent."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={
            "service_id": "stayright-hotels",
            "action_ids": ["search-availability", "book-room"],
        },
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    consent_id = resp.json()["consent_id"]

    # Check status
    resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_multi_action_consent_full_flow(cafe_client):
    """Multi-action consent: approve → exchange → token has all scopes and authorizations."""
    agent_token = await _register_agent(cafe_client)
    user_id, human_session = await _register_human(cafe_client)

    # Initiate with two actions
    resp = await cafe_client.post(
        "/consents/initiate",
        json={
            "service_id": "stayright-hotels",
            "action_ids": ["search-availability", "book-room"],
        },
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    consent_id = resp.json()["consent_id"]

    # Human approves
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200
    _policy_id = resp.json()["policy_id"]

    # Exchange for token
    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    token_data = resp.json()
    payload = decode_passport_token(token_data["token"])

    # Token should have both scopes
    assert "stayright-hotels:search-availability" in payload["scopes"]
    assert "stayright-hotels:book-room" in payload["scopes"]

    # Token should have authorizations for both actions
    auth_actions = {a["action_id"] for a in payload["authorizations"]}
    assert "search-availability" in auth_actions
    assert "book-room" in auth_actions

    # Use token for the read action
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": token_data["token"],
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_multi_action_highest_risk_tier_wins(cafe_client):
    """Multi-action consent should use the HIGHEST risk tier among actions."""
    agent_token = await _register_agent(cafe_client)
    user_id, human_session = await _register_human(cafe_client)

    # search-availability is low risk, cancel-booking is high risk
    resp = await cafe_client.post(
        "/consents/initiate",
        json={
            "service_id": "stayright-hotels",
            "action_ids": ["search-availability", "cancel-booking"],
        },
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"token_lifetime_seconds": 7200, "passkey_challenge_id": user_id, "passkey_credential": {}},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200

    # Exchange and check — should be capped at high-risk ceiling (300s), not low (3600s)
    resp = await cafe_client.post(
        "/tokens/exchange",
        json={"consent_id": consent_id},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    payload = decode_passport_token(resp.json()["token"])
    lifetime = payload["exp"] - payload["iat"]
    assert lifetime <= 300, f"Expected <=300s (high risk ceiling), got {lifetime}s"


@pytest.mark.asyncio
async def test_multi_action_missing_action_rejected(cafe_client):
    """Initiating consent with a non-existent action in the list should return 404."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={
            "service_id": "stayright-hotels",
            "action_ids": ["search-availability", "nonexistent-action"],
        },
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 404
    assert "nonexistent-action" in resp.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_initiate_no_action_rejected(cafe_client):
    """Initiating consent with neither action_id nor action_ids should return 422."""
    agent_token = await _register_agent(cafe_client)

    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 422


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
    payload = decode_passport_token(tier2_token)
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

def _extract_csrf(html: str) -> str:
    """Extract the CSRF token from an HTML form response."""
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "CSRF token not found in HTML"
    return match.group(1)


async def _login_via_form(cafe_client, email: str, password: str) -> str | None:
    """Login via the form flow (GET page → extract CSRF → POST). Returns session cookie."""
    page = await cafe_client.get("/login")
    csrf = _extract_csrf(page.text)
    resp = await cafe_client.post("/login", data={
        "email": email,
        "password": password,
        "csrf_token": csrf,
    }, follow_redirects=False)
    return resp.cookies.get("cafe_session")


@pytest.mark.asyncio
async def test_login_page_renders(cafe_client):
    """GET /login should return an HTML page."""
    resp = await cafe_client.get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Sign in to AgentCafe" in resp.text
    assert "csrf_token" in resp.text


@pytest.mark.asyncio
async def test_register_page_renders(cafe_client):
    """GET /register should return an HTML page."""
    resp = await cafe_client.get("/register")
    assert resp.status_code == 200
    assert "Create your Cafe account" in resp.text


@pytest.mark.asyncio
async def test_login_form_bad_password(cafe_client):
    """POST /login with wrong password should show error on the login page."""
    await cafe_client.post("/human/register", json={
        "email": "pagetest@example.com",
        "password": "secure-password-123",
    })
    # GET page for CSRF token
    page = await cafe_client.get("/login")
    csrf = _extract_csrf(page.text)
    resp = await cafe_client.post("/login", data={
        "email": "pagetest@example.com",
        "password": "wrong-password",
        "csrf_token": csrf,
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
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")
    assert session_cookie is not None


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
    email = f"consentpage-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

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
    assert "stayright-hotels" in resp.text or "StayRight Hotels" in resp.text
    assert "Approve" in resp.text


@pytest.mark.asyncio
async def test_consent_page_approve_via_form(cafe_client):
    """POST /consent/<id>/approve with CSRF should approve and show success page."""
    email = f"approveform-{__import__('uuid').uuid4().hex[:6]}@example.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    user_id = reg.json()["user_id"]
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    # Create consent
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    # GET the consent page to get CSRF token
    page = await cafe_client.get(
        f"/authorize/{consent_id}",
        cookies={"cafe_session": session_cookie},
    )
    csrf = _extract_csrf(page.text)

    # Approve via form with CSRF token and passkey assertion
    resp = await cafe_client.post(
        f"/authorize/{consent_id}/approve",
        data={
            "token_lifetime_seconds": "900",
            "csrf_token": csrf,
            "passkey_challenge_id": user_id,
            "passkey_credential": "{}",
        },
        cookies={"cafe_session": session_cookie},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "approved" in resp.text.lower()

    # Verify the consent is now approved via API
    resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_consent_page_decline(cafe_client):
    """POST /consent/<id>/decline with CSRF should decline and show decline page."""
    email = f"declinetest-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    # GET the consent page to get CSRF token
    page = await cafe_client.get(
        f"/authorize/{consent_id}",
        cookies={"cafe_session": session_cookie},
    )
    csrf = _extract_csrf(page.text)

    # Decline via POST with CSRF token
    resp = await cafe_client.post(
        f"/authorize/{consent_id}/decline",
        data={"csrf_token": csrf},
        cookies={"cafe_session": session_cookie},
    )
    assert resp.status_code == 200
    assert "declined" in resp.text.lower()

    # Verify declined via API
    resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert resp.json()["status"] == "declined"


@pytest.mark.asyncio
async def test_decline_get_returns_405(cafe_client):
    """GET /authorize/{id}/decline should return 405 (method not allowed)."""
    resp = await cafe_client.get("/authorize/fake-id/decline")
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Human dashboard tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_redirects_without_session(cafe_client):
    """GET /dashboard without a session should redirect to login."""
    resp = await cafe_client.get(
        "/dashboard",
        cookies={"cafe_session": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_dashboard_empty_for_new_user(cafe_client):
    """Dashboard for a user with no policies should show empty state."""
    email = f"dash-empty-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    resp = await cafe_client.get(
        "/dashboard",
        cookies={"cafe_session": session_cookie},
    )
    assert resp.status_code == 200
    assert "Nothing here yet" in resp.text


@pytest.mark.asyncio
async def test_dashboard_shows_policy_after_approval(cafe_client):
    """After approving a consent, the dashboard should show the policy."""
    email = f"dash-policy-{__import__('uuid').uuid4().hex[:6]}@example.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    user_id = reg.json()["user_id"]
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    # Create and approve a consent via form
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    page = await cafe_client.get(
        f"/authorize/{consent_id}",
        cookies={"cafe_session": session_cookie},
    )
    csrf = _extract_csrf(page.text)

    await cafe_client.post(
        f"/authorize/{consent_id}/approve",
        data={
            "token_lifetime_seconds": "900",
            "csrf_token": csrf,
            "passkey_challenge_id": user_id,
            "passkey_credential": "{}",
        },
        cookies={"cafe_session": session_cookie},
        follow_redirects=False,
    )

    # Check dashboard
    resp = await cafe_client.get(
        "/dashboard",
        cookies={"cafe_session": session_cookie},
    )
    assert resp.status_code == 200
    assert "StayRight Hotels" in resp.text or "stayright-hotels" in resp.text
    assert "Active (1)" in resp.text
    assert "Revoke" in resp.text


@pytest.mark.asyncio
async def test_dashboard_revoke_policy(cafe_client):
    """POST /dashboard/revoke/<id> should revoke the policy and redirect."""
    email = f"dash-revoke-{__import__('uuid').uuid4().hex[:6]}@example.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    user_id = reg.json()["user_id"]
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    # Create and approve consent
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    page = await cafe_client.get(
        f"/authorize/{consent_id}",
        cookies={"cafe_session": session_cookie},
    )
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post(
        f"/authorize/{consent_id}/approve",
        data={
            "token_lifetime_seconds": "900",
            "csrf_token": csrf,
            "passkey_challenge_id": user_id,
            "passkey_credential": "{}",
        },
        cookies={"cafe_session": session_cookie},
        follow_redirects=False,
    )

    # Find the policy_id from the consent
    status_resp = await cafe_client.get(f"/consents/{consent_id}/status")
    policy_id = status_resp.json()["policy_id"]

    # Get dashboard page for CSRF token
    dash = await cafe_client.get(
        "/dashboard",
        cookies={"cafe_session": session_cookie},
    )
    csrf = _extract_csrf(dash.text)

    # Revoke
    resp = await cafe_client.post(
        f"/dashboard/revoke/{policy_id}",
        data={"csrf_token": csrf},
        cookies={"cafe_session": session_cookie},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "revoked=1" in resp.headers["location"]

    # Dashboard should now show the policy as revoked (follow the redirect URL)
    dash = await cafe_client.get(
        "/dashboard?revoked=1",
        cookies={"cafe_session": session_cookie},
    )
    assert "Revoked (1)" in dash.text
    assert "Policy revoked successfully" in dash.text


@pytest.mark.asyncio
async def test_dashboard_revoke_wrong_user_ignored(cafe_client):
    """Revoking another user's policy should silently redirect (no crash)."""
    email = f"dash-wrong-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    # Get CSRF from login page (empty dashboard has no forms)
    page = await cafe_client.get("/login")
    csrf = _extract_csrf(page.text)

    # Try revoking a non-existent/other-user policy
    resp = await cafe_client.post(
        "/dashboard/revoke/fake-policy-id",
        data={"csrf_token": csrf},
        cookies={"cafe_session": session_cookie},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/dashboard" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Consent webhook/callback tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_callback_fired_on_api_approve(cafe_client, monkeypatch):
    """When callback_url is set, _fire_consent_callback should be called on API approve."""
    from unittest.mock import AsyncMock
    import agentcafe.cafe.consent as cm  # pylint: disable=reimported

    mock_cb = AsyncMock()
    monkeypatch.setattr(cm, "_fire_consent_callback", mock_cb)

    agent_token = await _register_agent(cafe_client)
    user_id, human_session = await _register_human(cafe_client)

    # Initiate with a callback_url
    resp = await cafe_client.post(
        "/consents/initiate",
        json={
            "service_id": "stayright-hotels",
            "action_id": "search-availability",
            "callback_url": "https://agent.example.com/webhook",
        },
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    consent_id = resp.json()["consent_id"]

    # Approve via API
    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200
    policy_id = resp.json()["policy_id"]

    # Verify callback was fired
    mock_cb.assert_called_once_with(
        "https://agent.example.com/webhook", consent_id, "approved", policy_id,
    )


@pytest.mark.asyncio
async def test_callback_fired_on_form_decline(cafe_client, monkeypatch):
    """When callback_url is set, callback should fire on form-based decline."""
    from unittest.mock import AsyncMock
    import agentcafe.cafe.pages as pm  # pylint: disable=reimported

    mock_cb = AsyncMock()
    monkeypatch.setattr(pm, "_fire_consent_callback", mock_cb)

    email = f"cb-decline-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={
            "service_id": "stayright-hotels",
            "action_id": "search-availability",
            "callback_url": "https://agent.example.com/decline-hook",
        },
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    page = await cafe_client.get(
        f"/authorize/{consent_id}",
        cookies={"cafe_session": session_cookie},
    )
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post(
        f"/authorize/{consent_id}/decline",
        data={"csrf_token": csrf},
        cookies={"cafe_session": session_cookie},
    )
    assert resp.status_code == 200

    mock_cb.assert_called_once_with(
        "https://agent.example.com/decline-hook", consent_id, "declined",
    )


@pytest.mark.asyncio
async def test_no_callback_when_url_not_set(cafe_client, monkeypatch):
    """When no callback_url is provided, _fire_consent_callback should still be called but be a no-op."""
    from unittest.mock import AsyncMock
    import agentcafe.cafe.consent as cm  # pylint: disable=reimported

    mock_cb = AsyncMock()
    monkeypatch.setattr(cm, "_fire_consent_callback", mock_cb)

    agent_token = await _register_agent(cafe_client)
    user_id, human_session = await _register_human(cafe_client)

    # Initiate WITHOUT callback_url
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    consent_id = resp.json()["consent_id"]

    resp = await cafe_client.post(
        f"/consents/{consent_id}/approve",
        json={"passkey_challenge_id": user_id, "passkey_credential": {}},
        headers={"Authorization": f"Bearer {human_session}"},
    )
    assert resp.status_code == 200

    # callback_url will be None — function called but should be a no-op internally
    mock_cb.assert_called_once()
    assert mock_cb.call_args[0][0] is None  # first arg is callback_url


@pytest.mark.asyncio
async def test_fire_consent_callback_unit():
    """Unit test: _fire_consent_callback POSTs the correct payload."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from agentcafe.cafe.consent import _fire_consent_callback

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("agentcafe.cafe.consent.httpx.AsyncClient", return_value=mock_client_instance):
        await _fire_consent_callback(
            "https://example.com/hook", "consent-123", "approved", "policy-456",
        )

    mock_client_instance.post.assert_called_once_with(
        "https://example.com/hook",
        json={"consent_id": "consent-123", "status": "approved", "policy_id": "policy-456"},
    )


@pytest.mark.asyncio
async def test_fire_consent_callback_skips_when_no_url():
    """Unit test: _fire_consent_callback returns immediately when callback_url is None."""
    from unittest.mock import patch
    from agentcafe.cafe.consent import _fire_consent_callback

    with patch("agentcafe.cafe.consent.httpx.AsyncClient") as mock_client:
        await _fire_consent_callback(None, "consent-123", "approved")
        mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# Activation code flow tests (Sprint 3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initiate_returns_activation_code(cafe_client):
    """POST /consents/initiate should return an activation_code and activation_url."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "activation_code" in data
    assert len(data["activation_code"]) == 8
    assert data["activation_code"].isalnum()
    assert "activation_url" in data
    assert data["activation_code"] in data["activation_url"]


@pytest.mark.asyncio
async def test_activate_page_renders(cafe_client):
    """GET /activate should return the code entry form."""
    resp = await cafe_client.get("/activate")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "activation code" in resp.text.lower()
    assert "csrf_token" in resp.text


@pytest.mark.asyncio
async def test_activate_with_valid_code(cafe_client):
    """POST /activate with a valid code should show the register+approve form."""
    cafe_client.cookies.clear()  # ensure no stale session from prior tests
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]

    # Get CSRF
    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate", data={
        "code": code,
        "csrf_token": csrf,
    })
    assert resp.status_code == 200
    assert "Register" in resp.text
    assert "Approve" in resp.text
    assert "stayright" in resp.text.lower() or "StayRight" in resp.text


@pytest.mark.asyncio
async def test_activate_with_invalid_code(cafe_client):
    """POST /activate with a bad code should show error."""
    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate", data={
        "code": "ZZZZZZZZ",
        "csrf_token": csrf,
    })
    assert resp.status_code == 404
    assert "not found" in resp.text.lower() or "Code not found" in resp.text


@pytest.mark.asyncio
async def test_activate_with_short_code(cafe_client):
    """POST /activate with a code that's too short should show validation error."""
    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate", data={
        "code": "ABC",
        "csrf_token": csrf,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_activate_complete_registers_and_approves(cafe_client):
    """POST /activate/complete should register user and approve consent."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]
    consent_id = resp.json()["consent_id"]

    # Get CSRF from activate page
    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate/complete", data={
        "activation_code": code,
        "email": "newuser@test.example.com",
        "challenge_id": "test-challenge",
        "credential": "{}",
        "token_lifetime_seconds": "900",
        "csrf_token": csrf,
    })
    assert resp.status_code == 200
    assert "all set" in resp.text.lower() or "success" in resp.text.lower()

    # Verify consent is approved
    status_resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert status_resp.json()["status"] == "approved"
    assert status_resp.json()["policy_id"] is not None

    # Verify session cookie was set
    assert "cafe_session" in resp.cookies or "set-cookie" in resp.headers


@pytest.mark.asyncio
async def test_activate_complete_sets_session_cookie(cafe_client):
    """POST /activate/complete should set a cafe_session cookie."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]

    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate/complete", data={
        "activation_code": code,
        "email": "cookie-test@test.example.com",
        "challenge_id": "test-challenge",
        "credential": "{}",
        "token_lifetime_seconds": "900",
        "csrf_token": csrf,
    })
    assert resp.status_code == 200
    assert resp.cookies.get("cafe_session") is not None


@pytest.mark.asyncio
async def test_activate_decline(cafe_client):
    """POST /activate/decline should decline the consent."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]
    consent_id = resp.json()["consent_id"]

    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate/decline", data={
        "activation_code": code,
        "csrf_token": csrf,
    })
    assert resp.status_code == 200
    assert "declined" in resp.text.lower()

    # Verify consent is declined
    status_resp = await cafe_client.get(f"/consents/{consent_id}/status")
    assert status_resp.json()["status"] == "declined"


@pytest.mark.asyncio
async def test_activate_rate_limit(cafe_client):
    """POST /activate should enforce rate limiting after too many attempts."""
    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    # Exhaust the rate limit (10 attempts)
    for _ in range(10):
        await cafe_client.post("/activate", data={
            "code": "ZZZZZZZZ",
            "csrf_token": csrf,
        })
        # Re-fetch CSRF for next request
        page = await cafe_client.get("/activate")
        csrf = _extract_csrf(page.text)

    # 11th attempt should be rate-limited
    resp = await cafe_client.post("/activate", data={
        "code": "ZZZZZZZZ",
        "csrf_token": csrf,
    })
    assert resp.status_code == 429
    assert "too many" in resp.text.lower() or "wait" in resp.text.lower()


@pytest.mark.asyncio
async def test_activate_logged_in_user_redirects(cafe_client):
    """POST /activate with a valid code while logged in should redirect to consent page."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]
    consent_id = resp.json()["consent_id"]

    # Register and login
    email = f"activate-login-{__import__('uuid').uuid4().hex[:6]}@example.com"
    await cafe_client.post("/human/register", json={
        "email": email,
        "password": "secure-password-123",
    })
    session_cookie = await _login_via_form(cafe_client, email, "secure-password-123")

    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate", data={
        "code": code,
        "csrf_token": csrf,
    }, cookies={"cafe_session": session_cookie}, follow_redirects=False)
    assert resp.status_code == 303
    assert f"/authorize/{consent_id}" in resp.headers["location"]


@pytest.mark.asyncio
async def test_activate_complete_with_used_code_redirects(cafe_client):
    """POST /activate/complete with an already-used code should redirect."""
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]

    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    # First complete — should succeed
    resp = await cafe_client.post("/activate/complete", data={
        "activation_code": code,
        "email": "first@test.example.com",
        "challenge_id": "test-challenge",
        "credential": "{}",
        "token_lifetime_seconds": "900",
        "csrf_token": csrf,
    })
    assert resp.status_code == 200

    # Second complete with same code — consent is no longer pending
    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)
    resp = await cafe_client.post("/activate/complete", data={
        "activation_code": code,
        "email": "second@test.example.com",
        "challenge_id": "test-challenge-2",
        "credential": "{}",
        "token_lifetime_seconds": "900",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 303


@pytest.mark.asyncio
async def test_activate_complete_rejects_expired_consent(cafe_client):
    """POST /activate/complete should reject an expired consent even if status is still pending."""
    from agentcafe.db.engine import get_db as _get_db
    from datetime import datetime, timezone, timedelta

    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]
    consent_id = resp.json()["consent_id"]

    # Manually backdate expires_at to make the consent expired
    db = await _get_db()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await db.execute(
        "UPDATE consents SET expires_at = ? WHERE id = ?", (past, consent_id),
    )
    await db.commit()

    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate/complete", data={
        "activation_code": code,
        "email": "expired@test.example.com",
        "challenge_id": "test-challenge",
        "credential": "{}",
        "token_lifetime_seconds": "900",
        "csrf_token": csrf,
    }, follow_redirects=False)
    # Should redirect back — consent is expired
    assert resp.status_code == 303


@pytest.mark.asyncio
async def test_activate_lookup_rejects_expired_consent(cafe_client):
    """POST /activate with a valid code for an expired consent should show error."""
    from agentcafe.db.engine import get_db as _get_db
    from datetime import datetime, timezone, timedelta

    cafe_client.cookies.clear()
    agent_token = await _register_agent(cafe_client)
    resp = await cafe_client.post(
        "/consents/initiate",
        json={"service_id": "stayright-hotels", "action_id": "search-availability"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    code = resp.json()["activation_code"]
    consent_id = resp.json()["consent_id"]

    # Manually expire the consent
    db = await _get_db()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await db.execute(
        "UPDATE consents SET expires_at = ? WHERE id = ?", (past, consent_id),
    )
    await db.commit()

    page = await cafe_client.get("/activate")
    csrf = _extract_csrf(page.text)

    resp = await cafe_client.post("/activate", data={
        "code": code,
        "csrf_token": csrf,
    })
    # Expired consent should be treated as not found or show expiry error
    assert resp.status_code in (404, 410)
