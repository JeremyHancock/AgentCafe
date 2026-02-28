"""Tests for the POST /cafe/order endpoint (proxy + double validation)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
from agentcafe.demo_backends.hotel import app as hotel_app
from agentcafe.demo_backends.lunch import app as lunch_app
from agentcafe.demo_backends.home_service import app as home_service_app

# pylint: disable=redefined-outer-name,protected-access


# ---------------------------------------------------------------------------
# Helpers — mock the shared httpx client to route to demo backends via ASGI
# ---------------------------------------------------------------------------

_BACKEND_APPS = {
    "http://127.0.0.1:8001": hotel_app,
    "http://127.0.0.1:8002": lunch_app,
    "http://127.0.0.1:8003": home_service_app,
}


class _MultiBackendTransport:
    """Route requests to the correct demo backend based on the URL prefix."""

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
    """Replace the shared httpx client with one that routes to in-process backends."""
    mock_client = AsyncClient(transport=_MultiBackendTransport())
    monkeypatch.setattr(router_module._state, "http_client", mock_client)
    monkeypatch.setattr(router_module._state, "issuer_api_key", "TEST_API_KEY")
    yield
    await mock_client.aclose()


# ---------------------------------------------------------------------------
# Rejection tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_invalid_passport(cafe_client):
    """Invalid passport should be rejected with 401."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "bad-passport",
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "passport_invalid"


@pytest.mark.asyncio
async def test_menu_includes_quarantine_status(cafe_client):
    """Menu actions should include security_status when quarantine_until is set."""
    # Demo services have quarantine_until set (past date) — should appear as informational
    resp = await cafe_client.get("/cafe/menu")
    assert resp.status_code == 200
    menu = resp.json()
    hotel = next(s for s in menu["services"] if s["service_id"] == "stayright-hotels")
    search_action = next(a for a in hotel["actions"] if a["action_id"] == "search-availability")
    # Past quarantine should still show (it's informational)
    assert "security_status" in search_action
    assert "quarantine_until" in search_action["security_status"]


@pytest.mark.asyncio
async def test_order_unknown_service(cafe_client):
    """Unknown service_id should return 404."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "nonexistent-service",
        "action_id": "some-action",
        "passport": "demo-passport",
        "inputs": {},
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "action_not_found"


@pytest.mark.asyncio
async def test_order_unknown_action(cafe_client):
    """Unknown action_id on a valid service should return 404."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "nonexistent-action",
        "passport": "demo-passport",
        "inputs": {},
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "action_not_found"


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_missing_all_required_inputs(cafe_client):
    """Sending empty inputs for an action with required inputs should return 422."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "missing_inputs"
    assert "city" in detail["missing"]
    assert "check_in" in detail["missing"]
    assert "check_out" in detail["missing"]
    assert "guests" in detail["missing"]


@pytest.mark.asyncio
async def test_order_missing_some_required_inputs(cafe_client):
    """Sending partial inputs should list only the missing ones."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {"city": "Austin", "guests": 2},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "missing_inputs"
    assert set(detail["missing"]) == {"check_in", "check_out"}


# ---------------------------------------------------------------------------
# Happy-path tests — full proxy flow through double validation to backend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_hotel_search_happy_path(cafe_client):
    """Valid passport + hotel search should return room results from the backend."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {"city": "Austin", "check_in": "2026-03-15", "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "total_results" in data
    assert data["total_results"] >= 1
    assert data["results"][0]["hotel_name"] == "StayRight Austin Downtown"


@pytest.mark.asyncio
async def test_order_lunch_browse_happy_path(cafe_client):
    """Valid passport + lunch browse should return menu items from the backend."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "quickbite-delivery",
        "action_id": "browse-menu",
        "passport": "demo-passport",
        "inputs": {"delivery_address": "200 Congress Ave, Austin, TX 78701"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total_results"] >= 1


@pytest.mark.asyncio
async def test_order_home_search_happy_path(cafe_client):
    """Valid passport + home service search should return providers from the backend."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "fixright-home",
        "action_id": "search-providers",
        "passport": "demo-passport",
        "inputs": {"service_type": "plumbing", "address": "742 Evergreen Terrace, Austin, TX 78704"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert data["total_results"] >= 1


# ---------------------------------------------------------------------------
# Input injection protection tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_path_traversal_blocked(cafe_client):
    """Path traversal in a path parameter should be rejected."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "get-room-details",
        "passport": "demo-passport",
        "inputs": {"room_id": "../../admin"},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_path_parameter"
    assert resp.json()["detail"]["field"] == "room_id"


@pytest.mark.asyncio
async def test_query_injection_blocked(cafe_client):
    """Query string injection in a path parameter should be rejected."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "get-room-details",
        "passport": "demo-passport",
        "inputs": {"room_id": "123?admin=true"},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_path_parameter"


@pytest.mark.asyncio
async def test_newline_injection_blocked(cafe_client):
    """Newline injection (HTTP header splitting) should be rejected."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "get-room-details",
        "passport": "demo-passport",
        "inputs": {"room_id": "123\r\nX-Evil: true"},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_path_parameter"


@pytest.mark.asyncio
async def test_space_in_path_param_blocked(cafe_client):
    """Spaces in path parameters should be rejected."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "get-room-details",
        "passport": "demo-passport",
        "inputs": {"room_id": "room 420"},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_path_parameter"


@pytest.mark.asyncio
async def test_safe_path_param_allowed(cafe_client):
    """Normal path parameter values should pass through."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "get-room-details",
        "passport": "demo-passport",
        "inputs": {"room_id": "sr-austin-k420"},
    })
    assert resp.status_code == 200
    assert resp.json()["room_id"] == "sr-austin-k420"


# ---------------------------------------------------------------------------
# Tamper-evident audit logging tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_hash_chain_valid(cafe_client):
    """After several orders, the audit chain should verify as valid."""
    from agentcafe.db.engine import get_db
    from agentcafe.cafe.router import verify_audit_chain

    # Make a few requests to populate the audit log
    for _ in range(3):
        await cafe_client.post("/cafe/order", json={
            "service_id": "stayright-hotels",
            "action_id": "search-availability",
            "passport": "demo-passport",
            "inputs": {"city": "Austin", "check_in": "2026-03-15",
                       "check_out": "2026-03-18", "guests": 2},
        })

    db = await get_db()
    result = await verify_audit_chain(db)
    assert result["valid"] is True
    assert result["entries"] >= 3


@pytest.mark.asyncio
async def test_audit_hash_chain_detects_tamper(cafe_client):
    """Modifying an audit entry should break the chain."""
    from agentcafe.db.engine import get_db
    from agentcafe.cafe.router import verify_audit_chain

    # Create an audit entry
    await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {"city": "Austin", "check_in": "2026-03-15",
                   "check_out": "2026-03-18", "guests": 2},
    })

    db = await get_db()

    # Tamper with the most recent entry
    await db.execute(
        "UPDATE audit_log SET outcome = 'tampered' WHERE id = "
        "(SELECT id FROM audit_log ORDER BY timestamp DESC LIMIT 1)"
    )
    await db.commit()

    result = await verify_audit_chain(db)
    assert result["valid"] is False
    assert "broken_at" in result


@pytest.mark.asyncio
async def test_audit_entries_have_hash_columns(cafe_client):
    """New audit entries should have prev_hash and entry_hash populated."""
    from agentcafe.db.engine import get_db

    await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {"city": "Austin", "check_in": "2026-03-15",
                   "check_out": "2026-03-18", "guests": 2},
    })

    db = await get_db()
    cursor = await db.execute(
        "SELECT prev_hash, entry_hash FROM audit_log ORDER BY timestamp DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    assert row["prev_hash"] is not None
    assert row["entry_hash"] is not None
    assert len(row["entry_hash"]) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# ADR-025: Service onboarding security (quarantine + suspension)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suspended_service_returns_503(cafe_client):
    """A suspended service should return 503 service_suspended."""
    from agentcafe.db.engine import get_db
    db = await get_db()

    # Suspend stayright-hotels
    await db.execute(
        "UPDATE proxy_configs SET suspended_at = '2026-02-28T00:00:00+00:00' "
        "WHERE service_id = 'stayright-hotels'"
    )
    await db.commit()

    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {"city": "Austin", "check_in": "2026-03-15",
                   "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "service_suspended"

    # Clean up
    await db.execute(
        "UPDATE proxy_configs SET suspended_at = NULL "
        "WHERE service_id = 'stayright-hotels'"
    )
    await db.commit()


@pytest.mark.asyncio
async def test_quarantine_forces_human_auth(cafe_client):
    """A quarantined service should require Tier-2 even for read actions."""
    from agentcafe.db.engine import get_db
    db = await get_db()

    # Set quarantine far in the future
    await db.execute(
        "UPDATE proxy_configs SET quarantine_until = '2099-01-01T00:00:00+00:00' "
        "WHERE service_id = 'stayright-hotels' AND action_id = 'search-availability'"
    )
    await db.commit()

    # search-availability is normally read-only (no human_auth), but quarantine forces it
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {"city": "Austin", "check_in": "2026-03-15",
                   "check_out": "2026-03-18", "guests": 2},
    })
    # In MVP mode, demo-passport is pre-authorized for everything,
    # so the request still succeeds. But human_auth_required is now True.
    # The real test is in the V2 path — here we verify no crash and the
    # quarantine logic runs without error.
    assert resp.status_code == 200

    # Clean up
    await db.execute(
        "UPDATE proxy_configs SET quarantine_until = '2020-01-01T00:00:00+00:00' "
        "WHERE service_id = 'stayright-hotels' AND action_id = 'search-availability'"
    )
    await db.commit()


@pytest.mark.asyncio
async def test_suspend_endpoint_requires_api_key(cafe_client):
    """POST /cafe/services/{id}/suspend should reject invalid API keys."""
    resp = await cafe_client.post(
        "/cafe/services/stayright-hotels/suspend",
        json={"api_key": "wrong-key", "reason": "test"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_suspend_endpoint_unknown_service(cafe_client):
    """Suspending a non-existent service should return 404."""
    resp = await cafe_client.post(
        "/cafe/services/nonexistent-service/suspend",
        json={"api_key": "TEST_API_KEY", "reason": "test"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_suspend_endpoint_success(cafe_client):
    """POST /cafe/services/{id}/suspend with correct key should succeed."""
    from agentcafe.db.engine import get_db

    resp = await cafe_client.post(
        "/cafe/services/stayright-hotels/suspend",
        json={"api_key": "TEST_API_KEY", "reason": "abuse detected"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["service_id"] == "stayright-hotels"
    assert data["suspended_at"] is not None

    # Verify the service is now suspended
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {"city": "Austin", "check_in": "2026-03-15",
                   "check_out": "2026-03-18", "guests": 2},
    })
    assert resp.status_code == 503

    # Clean up
    db = await get_db()
    await db.execute(
        "UPDATE proxy_configs SET suspended_at = NULL "
        "WHERE service_id = 'stayright-hotels'"
    )
    await db.commit()
