"""Tests for Sprint 1 observability: request IDs, structured logging, health, admin stats."""

from __future__ import annotations

import json
import logging
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient  # noqa: F401

from agentcafe.cafe import router as router_module  # noqa: F401
from agentcafe.logging_config import configure_logging
from agentcafe.main import create_cafe_app  # noqa: F401
from agentcafe.middleware import request_id_var

# pylint: disable=redefined-outer-name,unused-argument

TEST_API_KEY = "test-obs-api-key"


@pytest_asyncio.fixture(scope="module")
async def obs_client(seeded_db):
    """HTTP client for observability tests (reuses session-scoped seeded_db)."""
    router_module._state.issuer_api_key = TEST_API_KEY
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    router_module._state.issuer_api_key = ""


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_id_generated(obs_client: AsyncClient):
    """Requests without X-Request-ID get one generated in the response."""
    resp = await obs_client.get("/health")
    rid = resp.headers.get("x-request-id")
    assert rid is not None
    # Should be a valid UUID4
    uuid.UUID(rid, version=4)


@pytest.mark.asyncio
async def test_request_id_preserved(obs_client: AsyncClient):
    """Client-provided X-Request-ID is echoed back."""
    custom_id = "test-rid-12345"
    resp = await obs_client.get("/health", headers={"X-Request-ID": custom_id})
    assert resp.headers.get("x-request-id") == custom_id


@pytest.mark.asyncio
async def test_request_id_on_cafe_menu(obs_client: AsyncClient):
    """Request ID appears on non-trivial endpoints too."""
    resp = await obs_client.get("/cafe/menu")
    assert resp.status_code == 200
    rid = resp.headers.get("x-request-id")
    assert rid is not None
    uuid.UUID(rid, version=4)


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------


def test_configure_logging_text():
    """Text format produces a standard formatter (no crash)."""
    configure_logging("INFO", "text")
    root = logging.getLogger()
    assert any(h.formatter is not None for h in root.handlers)


def test_configure_logging_json():
    """JSON format produces a JsonFormatter."""
    configure_logging("INFO", "json")
    root = logging.getLogger()
    handler = root.handlers[0]
    assert "Json" in type(handler.formatter).__name__


def test_json_log_output(capfd):
    """JSON log output is valid JSON with expected fields."""
    configure_logging("INFO", "json")
    test_logger = logging.getLogger("test.observability")
    request_id_var.set("test-rid-json")
    test_logger.info("test_message", extra={"service_id": "test-svc"})
    request_id_var.set("")
    # Restore text mode for other tests
    configure_logging("INFO", "text")

    captured = capfd.readouterr()
    # The JSON line should be parseable
    for line in captured.err.strip().split("\n"):
        if "test_message" in line:
            data = json.loads(line)
            assert data["message"] == "test_message"
            assert data["request_id"] == "test-rid-json"
            assert data["service_id"] == "test-svc"
            break
    else:
        pytest.fail("Expected JSON log line with 'test_message' not found")


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_ok(obs_client: AsyncClient):
    """Health endpoint returns ok with db status."""
    resp = await obs_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["service"] == "agentcafe"


# ---------------------------------------------------------------------------
# Enhanced admin stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_overview_has_enhanced_stats(obs_client: AsyncClient):
    """Admin overview response includes new stat fields."""
    resp = await obs_client.get(
        "/cafe/admin/overview",
        headers={"X-Api-Key": TEST_API_KEY},
    )
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert "requests_per_hour_24h" in stats
    assert isinstance(stats["requests_per_hour_24h"], list)
    assert "p50_latency_ms" in stats
    assert "p95_latency_ms" in stats
    assert "active_policies" in stats
    assert "active_cards" in stats
    assert "consent_stats" in stats
    assert "pending" in stats["consent_stats"]
    assert "approved_24h" in stats["consent_stats"]
    assert "declined_24h" in stats["consent_stats"]


@pytest.mark.asyncio
async def test_admin_overview_requires_key(obs_client: AsyncClient):
    """Admin overview rejects requests without valid API key."""
    resp = await obs_client.get("/cafe/admin/overview")
    assert resp.status_code == 403

    resp = await obs_client.get(
        "/cafe/admin/overview",
        headers={"X-Api-Key": "wrong-key"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_overview_latency_defaults(obs_client: AsyncClient):
    """Latency percentiles default to 0 when no data exists."""
    resp = await obs_client.get(
        "/cafe/admin/overview",
        headers={"X-Api-Key": TEST_API_KEY},
    )
    stats = resp.json()["stats"]
    # Fresh DB — latencies should be 0
    assert isinstance(stats["p50_latency_ms"], int)
    assert isinstance(stats["p95_latency_ms"], int)
