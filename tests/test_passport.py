"""Tests for the Passport system — JWT issuance, validation, revocation.

These tests enable USE_REAL_PASSPORT mode and exercise the full JWT flow.
"""

from __future__ import annotations

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
import agentcafe.cafe.passport as passport_module
from agentcafe.demo_backends.hotel import app as hotel_app
from agentcafe.demo_backends.lunch import app as lunch_app
from agentcafe.demo_backends.home_service import app as home_service_app

# ---------------------------------------------------------------------------
# Test-level config: a known signing secret and API key
# ---------------------------------------------------------------------------

TEST_SECRET = "test-secret-key-for-passport-testing-only"
TEST_API_KEY = "test-issuer-api-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _configure_real_passport(monkeypatch):
    """Enable real passport mode and set test signing secret for all tests in this file."""
    monkeypatch.setattr(router_module, "_use_real_passport", True)
    monkeypatch.setattr(passport_module, "_signing_secret", TEST_SECRET)
    monkeypatch.setattr(passport_module, "_issuer_api_key", TEST_API_KEY)
    yield


# Mock httpx client for happy-path tests that reach backends
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
    monkeypatch.setattr(router_module, "_http_client", mock_client)
    yield
    await mock_client.aclose()


# ---------------------------------------------------------------------------
# Helper — issue a passport via the API
# ---------------------------------------------------------------------------

async def _issue_passport(
    cafe_client,
    scopes: list[str],
    authorizations: list[dict] | None = None,
    duration_hours: float = 24.0,
    human_id: str = "test@example.com",
    agent_id: str = "test-agent",
) -> str:
    """Issue a passport and return the token string."""
    body = {
        "human_id": human_id,
        "agent_id": agent_id,
        "scopes": scopes,
        "authorizations": authorizations or [],
        "duration_hours": duration_hours,
    }
    resp = await cafe_client.post(
        "/passport/issue",
        json=body,
        headers={"X-Api-Key": TEST_API_KEY},
    )
    assert resp.status_code == 200, f"Issuance failed: {resp.json()}"
    return resp.json()["passport"]


# ---------------------------------------------------------------------------
# Issuance tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_passport_success(cafe_client):
    """Valid API key + valid request should return a signed JWT."""
    token = await _issue_passport(cafe_client, scopes=["stayright-hotels:*"])
    # Decode and verify structure
    payload = jwt.decode(token, TEST_SECRET, algorithms=["HS256"], audience="agentcafe")
    assert payload["iss"] == "agentcafe"
    assert payload["sub"] == "user:test@example.com"
    assert payload["agent_id"] == "test-agent"
    assert "stayright-hotels:*" in payload["scopes"]
    assert payload["human_consent"] is True
    assert "jti" in payload
    assert "exp" in payload


@pytest.mark.asyncio
async def test_issue_passport_bad_api_key(cafe_client):
    """Wrong API key should return 401."""
    resp = await cafe_client.post(
        "/passport/issue",
        json={"human_id": "x", "agent_id": "x", "scopes": [], "duration_hours": 1},
        headers={"X-Api-Key": "wrong-key"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_issue_passport_invalid_duration(cafe_client):
    """Duration > 24h should be rejected."""
    resp = await cafe_client.post(
        "/passport/issue",
        json={"human_id": "x", "agent_id": "x", "scopes": [], "duration_hours": 48},
        headers={"X-Api-Key": TEST_API_KEY},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_duration"


# ---------------------------------------------------------------------------
# Validation tests — real JWT through /cafe/order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_with_valid_jwt_read_action(cafe_client):
    """A JWT with the correct scope should pass for a read-only action."""
    token = await _issue_passport(
        cafe_client,
        scopes=["stayright-hotels:search-availability"],
    )
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200
    assert "results" in resp.json()


@pytest.mark.asyncio
async def test_order_with_wildcard_scope(cafe_client):
    """A JWT with service_id:* wildcard should match any action on that service."""
    token = await _issue_passport(
        cafe_client,
        scopes=["stayright-hotels:*"],
    )
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_order_with_missing_scope(cafe_client):
    """A JWT without the required scope should be rejected with 403."""
    token = await _issue_passport(
        cafe_client,
        scopes=["quickbite-delivery:browse-menu"],  # wrong service
    )
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "scope_missing"


@pytest.mark.asyncio
async def test_order_with_garbage_jwt(cafe_client):
    """A non-JWT string should be rejected with 401."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "not-a-jwt-token",
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "passport_invalid"


@pytest.mark.asyncio
async def test_order_write_action_without_authorization(cafe_client):
    """A JWT with scope but no authorization entry should fail for human-auth-required actions."""
    token = await _issue_passport(
        cafe_client,
        scopes=["stayright-hotels:book-room"],
        authorizations=[],  # no authorizations
    )
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "book-room",
        "passport": token,
        "inputs": {
            "room_id": "sr-austin-k420",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guest_name": "Test User",
            "guest_email": "test@example.com",
        },
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "human_auth_required"


@pytest.mark.asyncio
async def test_order_write_action_with_authorization(cafe_client):
    """A JWT with both scope and matching authorization should pass for write actions."""
    token = await _issue_passport(
        cafe_client,
        scopes=["stayright-hotels:book-room"],
        authorizations=[{
            "service_id": "stayright-hotels",
            "action_id": "book-room",
            "limits": {"valid_until": "2027-01-01"},
        }],
    )
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "book-room",
        "passport": token,
        "inputs": {
            "room_id": "sr-austin-k420",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guest_name": "Test User",
            "guest_email": "test@example.com",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["booking_id"] is not None


@pytest.mark.asyncio
async def test_order_authorization_expired_valid_until(cafe_client):
    """An authorization with an expired valid_until should be rejected."""
    token = await _issue_passport(
        cafe_client,
        scopes=["stayright-hotels:book-room"],
        authorizations=[{
            "service_id": "stayright-hotels",
            "action_id": "book-room",
            "limits": {"valid_until": "2020-01-01"},  # expired
        }],
    )
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "book-room",
        "passport": token,
        "inputs": {
            "room_id": "sr-austin-k420",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guest_name": "Test User",
            "guest_email": "test@example.com",
        },
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "human_auth_required"


# ---------------------------------------------------------------------------
# Revocation tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_and_reject(cafe_client):
    """Revoking a passport should cause subsequent orders to fail."""
    token = await _issue_passport(
        cafe_client,
        scopes=["stayright-hotels:search-availability"],
    )

    # Order should work before revocation
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200

    # Revoke
    revoke_resp = await cafe_client.post("/cafe/revoke", json={"passport": token})
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

    # Order should fail after revocation
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": token,
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "passport_revoked"


@pytest.mark.asyncio
async def test_revoke_garbage_token(cafe_client):
    """Revoking a non-JWT should return 400."""
    resp = await cafe_client.post("/cafe/revoke", json={"passport": "not-a-jwt"})
    assert resp.status_code == 400
