"""Tests for the POST /cafe/order endpoint (proxy + double validation)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
from agentcafe.demo_backends.hotel import app as hotel_app
from agentcafe.demo_backends.lunch import app as lunch_app
from agentcafe.demo_backends.home_service import app as home_service_app


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
    monkeypatch.setattr(router_module, "_http_client", mock_client)
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
