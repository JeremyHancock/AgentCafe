"""Tests for the MCP Server Adapter — 4-tool LLM-native discovery pattern.

Covers: cafe.search, cafe.get_details, cafe.request_card, cafe.invoke,
error handling, HUMAN_AUTH_REQUIRED structured errors, and tool listing.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
import agentcafe.cafe.passport as passport_module
import agentcafe.cafe.human as human_module
import agentcafe.cafe.consent as consent_module
import agentcafe.cafe.cards as cards_module
import agentcafe.cafe.pages as pages_module
from agentcafe.demo_backends.hotel import app as hotel_app
from agentcafe.demo_backends.lunch import app as lunch_app
from agentcafe.demo_backends.home_service import app as home_service_app
from agentcafe.keys import configure_keys

from agentcafe.cafe.mcp_adapter import (
    cafe_search,
    cafe_get_details,
    cafe_request_card,
    cafe_invoke,
    mcp_server,
)

# pylint: disable=redefined-outer-name,protected-access

TEST_SECRET = "test-secret-key-for-mcp-testing-32b"
TEST_API_KEY = "test-issuer-api-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _mock_verify_passkey(challenge_id, credential):  # pylint: disable=unused-argument
    return {"user_id": challenge_id, "email": "mock@example.com"}


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
async def _configure_modules(monkeypatch):
    """Configure all modules with test secrets and seed demo data."""
    monkeypatch.setattr(router_module._state, "use_real_passport", True)
    monkeypatch.setattr(router_module._state, "issuer_api_key", TEST_API_KEY)
    monkeypatch.setattr(passport_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(passport_module._state, "issuer_api_key", TEST_API_KEY)
    monkeypatch.setattr(human_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(consent_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(cards_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(pages_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(cards_module, "verify_passkey_assertion", _mock_verify_passkey)
    configure_keys(legacy_hs256_secret=TEST_SECRET)
    passport_module._register_hits.clear()
    human_module._challenge_hits.clear()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _mock_http_client(monkeypatch, seeded_db):  # pylint: disable=unused-argument
    mock_client = AsyncClient(transport=_MultiBackendTransport())
    monkeypatch.setattr(router_module._state, "http_client", mock_client)
    yield
    await mock_client.aclose()


@pytest_asyncio.fixture(scope="module")
async def cafe_http():
    """Shared HTTP client for the Cafe app (for passport registration)."""
    from agentcafe.main import create_cafe_app
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _register_agent(cafe_http_client) -> str:
    """Register an agent via the API and return its Tier-1 passport."""
    resp = await cafe_http_client.post("/passport/register", json={"agent_tag": "mcp-test-bot"})
    assert resp.status_code == 200
    return resp.json()["passport"]


# ---------------------------------------------------------------------------
# cafe.search tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_results():
    """Search with empty query returns all actions."""
    result = await cafe_search()
    assert "results" in result
    assert len(result["results"]) > 0
    assert "hint" in result


@pytest.mark.asyncio
async def test_search_keyword_filter():
    """Search filters by keyword."""
    result = await cafe_search(query="hotel")
    assert len(result["results"]) > 0
    for r in result["results"]:
        assert "hotel" in r["service_id"]


@pytest.mark.asyncio
async def test_search_no_match():
    """Search with nonsense query returns no results."""
    result = await cafe_search(query="xyzzynonexistent")
    assert len(result["results"]) == 0


@pytest.mark.asyncio
async def test_search_max_results():
    """max_results caps output."""
    result = await cafe_search(max_results=2)
    assert len(result["results"]) <= 2


@pytest.mark.asyncio
async def test_search_results_have_required_fields():
    """Each result has the summary fields per ADR-029."""
    result = await cafe_search()
    for r in result["results"]:
        assert "service_id" in r
        assert "action_id" in r
        assert "name" in r
        assert "short_description" in r
        assert "risk_tier" in r
        assert "relevance" in r


# ---------------------------------------------------------------------------
# cafe.get_details tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_details_returns_service():
    """get_details returns the full service entry."""
    result = await cafe_get_details(service_id="stayright-hotels")
    assert result.get("service_id") == "stayright-hotels"
    assert "service" in result
    assert "actions" in result["service"]


@pytest.mark.asyncio
async def test_get_details_single_action():
    """get_details with action_id returns just that action."""
    result = await cafe_get_details(service_id="stayright-hotels", action_id="search-availability")
    assert result.get("service_id") == "stayright-hotels"
    assert "action" in result
    assert result["action"]["action_id"] == "search-availability"


@pytest.mark.asyncio
async def test_get_details_service_not_found():
    """get_details returns error for unknown service."""
    result = await cafe_get_details(service_id="nonexistent-service")
    assert result.get("error") == "service_not_found"


@pytest.mark.asyncio
async def test_get_details_action_not_found():
    """get_details returns error for unknown action within a valid service."""
    result = await cafe_get_details(service_id="stayright-hotels", action_id="nonexistent-action")
    assert result.get("error") == "action_not_found"
    assert "available_actions" in result


# ---------------------------------------------------------------------------
# cafe.request_card tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_card_success(cafe_http):
    """request_card with a valid passport creates a pending card."""
    passport = await _register_agent(cafe_http)
    result = await cafe_request_card(service_id="stayright-hotels", passport=passport)
    assert result.get("card_id")
    assert result.get("status") == "pending"
    assert result.get("consent_url")
    assert result.get("activation_code")


@pytest.mark.asyncio
async def test_request_card_invalid_passport():
    """request_card with invalid passport returns auth error."""
    result = await cafe_request_card(service_id="stayright-hotels", passport="invalid-token")
    assert "error" in result


@pytest.mark.asyncio
async def test_request_card_nonexistent_service(cafe_http):
    """request_card for unknown service returns error."""
    passport = await _register_agent(cafe_http)
    result = await cafe_request_card(service_id="nonexistent-service", passport=passport)
    assert "error" in result


# ---------------------------------------------------------------------------
# cafe.invoke tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_read_action_success(cafe_http):
    """invoke a read action with a valid Tier-1 passport succeeds."""
    passport = await _register_agent(cafe_http)
    result = await cafe_invoke(
        service_id="stayright-hotels",
        action_id="search-availability",
        passport=passport,
        inputs={"city": "Miami", "check_in": "2026-04-01", "check_out": "2026-04-05", "guests": 2},
    )
    assert "error" not in result or result.get("error") != "HUMAN_AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_invoke_write_action_needs_auth(cafe_http):
    """invoke a write action with Tier-1 passport returns HUMAN_AUTH_REQUIRED."""
    passport = await _register_agent(cafe_http)
    result = await cafe_invoke(
        service_id="stayright-hotels",
        action_id="book-room",
        passport=passport,
        inputs={"room_id": "room-101", "guest_name": "Test User", "nights": 2},
    )
    assert result.get("error") == "HUMAN_AUTH_REQUIRED"
    assert result.get("card_suggestion") is not None
    assert result.get("hint")


@pytest.mark.asyncio
async def test_invoke_invalid_passport():
    """invoke with invalid passport returns error."""
    result = await cafe_invoke(
        service_id="stayright-hotels",
        action_id="search-availability",
        passport="bad-token",
        inputs={},
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_invoke_unknown_action(cafe_http):
    """invoke with unknown action returns error."""
    passport = await _register_agent(cafe_http)
    result = await cafe_invoke(
        service_id="stayright-hotels",
        action_id="nonexistent-action",
        passport=passport,
        inputs={},
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# Tool listing tests
# ---------------------------------------------------------------------------

def test_mcp_server_has_four_tools():
    """The MCP server exposes exactly 4 tools per ADR-029."""
    tools = mcp_server._tool_manager.list_tools()
    tool_names = {t.name for t in tools}
    assert tool_names == {"cafe.search", "cafe.get_details", "cafe.request_card", "cafe.invoke"}


def test_mcp_server_name():
    """MCP server is named AgentCafe."""
    assert mcp_server.name == "AgentCafe"


# ---------------------------------------------------------------------------
# MCP request logging tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_logs_request():
    """cafe.search writes to mcp_request_log."""
    from agentcafe.db.engine import get_db
    await cafe_search(query="hotel logging test")
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM mcp_request_log WHERE tool_name = 'cafe.search' AND query = 'hotel logging test'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["outcome"] == "ok"
    assert row["result_count"] is not None


@pytest.mark.asyncio
async def test_get_details_logs_request():
    """cafe.get_details writes to mcp_request_log."""
    from agentcafe.db.engine import get_db
    await cafe_get_details(service_id="stayright-hotels")
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM mcp_request_log WHERE tool_name = 'cafe.get_details' AND service_id = 'stayright-hotels'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["outcome"] == "ok"


@pytest.mark.asyncio
async def test_get_details_logs_error():
    """cafe.get_details logs errors for unknown services."""
    from agentcafe.db.engine import get_db
    await cafe_get_details(service_id="nonexistent-logged")
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM mcp_request_log WHERE service_id = 'nonexistent-logged'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["outcome"] == "error"
    assert row["error_code"] == "service_not_found"


@pytest.mark.asyncio
async def test_invoke_logs_auth_required(cafe_http):
    """cafe.invoke logs auth_required outcome for unauthorized writes."""
    from agentcafe.db.engine import get_db
    passport = await _register_agent(cafe_http)
    await cafe_invoke(
        service_id="stayright-hotels", action_id="book-room",
        passport=passport, inputs={"room_id": "r1", "guest_name": "Test", "nights": 1},
    )
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM mcp_request_log WHERE tool_name = 'cafe.invoke' AND action_id = 'book-room' AND outcome = 'auth_required'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["passport_hash"] is not None


# ---------------------------------------------------------------------------
# MCP analytics endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_analytics_requires_api_key(cafe_http):
    """GET /cafe/admin/mcp-analytics rejects without API key."""
    resp = await cafe_http.get("/cafe/admin/mcp-analytics")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mcp_analytics_returns_summary(cafe_http):
    """GET /cafe/admin/mcp-analytics returns structured analytics."""
    # Generate some log data first
    await cafe_search(query="analytics test")
    await cafe_get_details(service_id="stayright-hotels")

    resp = await cafe_http.get(
        "/cafe/admin/mcp-analytics",
        headers={"X-Api-Key": TEST_API_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "per_tool" in data
    assert "top_queries" in data
    assert "top_services" in data
    assert "error_breakdown" in data
    assert "recent" in data
    assert data["summary"]["total_requests"] >= 2
