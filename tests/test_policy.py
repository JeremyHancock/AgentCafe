"""Tests for the Company Policy engine (rate limiting + input type validation)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import agentcafe.cafe.router as router_module
from agentcafe.cafe.policy import check_rate_limit, parse_rate_limit, validate_input_types
from agentcafe.db.engine import get_db
from agentcafe.demo_backends.hotel import app as hotel_app
from agentcafe.demo_backends.lunch import app as lunch_app
from agentcafe.demo_backends.home_service import app as home_service_app


# ---------------------------------------------------------------------------
# Helpers — reuse the multi-backend transport from test_order.py
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
# Unit tests — parse_rate_limit
# ---------------------------------------------------------------------------

def test_parse_rate_limit_minute():
    assert parse_rate_limit("60/minute") == (60, 60)


def test_parse_rate_limit_hour():
    assert parse_rate_limit("100/hour") == (100, 3600)


def test_parse_rate_limit_day():
    assert parse_rate_limit("1000/day") == (1000, 86400)


def test_parse_rate_limit_invalid():
    assert parse_rate_limit("bad") is None
    assert parse_rate_limit("60/second") is None
    assert parse_rate_limit("") is None


# ---------------------------------------------------------------------------
# Unit tests — validate_input_types
# ---------------------------------------------------------------------------

def test_validate_types_all_correct():
    """Correct types should pass."""
    schema = [
        {"name": "city", "type": "string", "example": "Austin"},
        {"name": "guests", "type": "integer", "example": 2},
    ]
    ok, errors = validate_input_types({"city": "Dallas", "guests": 4}, schema)
    assert ok is True
    assert errors is None


def test_validate_types_string_as_int():
    """Passing a string where an int is expected should fail."""
    schema = [{"name": "guests", "type": "integer", "example": 2}]
    ok, errors = validate_input_types({"guests": "two"}, schema)
    assert ok is False
    assert len(errors) == 1
    assert "'guests'" in errors[0]


def test_validate_types_int_as_string():
    """Passing an int where a string is expected should fail."""
    schema = [{"name": "city", "type": "string", "example": "Austin"}]
    ok, errors = validate_input_types({"city": 123}, schema)
    assert ok is False
    assert "'city'" in errors[0]


def test_validate_types_float_for_int():
    """Float should be accepted where int is expected (both are numbers)."""
    schema = [{"name": "guests", "type": "integer", "example": 2}]
    ok, errors = validate_input_types({"guests": 2.5}, schema)
    assert ok is True


def test_validate_types_bool_not_number():
    """Bool should not be accepted where a number is expected."""
    schema = [{"name": "guests", "type": "integer", "example": 2}]
    ok, errors = validate_input_types({"guests": True}, schema)
    assert ok is False


def test_validate_types_missing_input_skipped():
    """Inputs not present should be silently skipped (missing_inputs catches those)."""
    schema = [{"name": "city", "type": "string", "example": "Austin"}]
    ok, errors = validate_input_types({}, schema)
    assert ok is True


def test_validate_types_no_type_or_example_skipped():
    """Inputs without a type or example in the schema should be skipped."""
    schema = [{"name": "city"}]
    ok, errors = validate_input_types({"city": 123}, schema)
    assert ok is True


def test_validate_types_explicit_type_preferred_over_example():
    """Explicit type field should take precedence over example inference."""
    schema = [{"name": "count", "type": "string", "example": 42}]
    ok, errors = validate_input_types({"count": "hello"}, schema)
    assert ok is True
    ok2, errors2 = validate_input_types({"count": 42}, schema)
    assert ok2 is False


def test_validate_types_fallback_to_example():
    """When type field is absent, should fall back to inferring from example."""
    schema = [{"name": "guests", "example": 2}]
    ok, errors = validate_input_types({"guests": "two"}, schema)
    assert ok is False


# ---------------------------------------------------------------------------
# Unit tests — check_rate_limit (direct DB interaction)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_under_limit(seeded_db):
    """Request should be allowed when under the limit."""
    ok, error = await check_rate_limit(
        seeded_db, "test-hash-unused", "stayright-hotels", "search-availability", "60/minute"
    )
    assert ok is True
    assert error is None


@pytest.mark.asyncio
async def test_rate_limit_at_limit(seeded_db):
    """Request should be rejected when at the limit."""
    db = seeded_db
    passport_hash = "rate-test-hash"
    now = datetime.now(timezone.utc).isoformat()

    # Insert 3 audit log entries within the last minute
    for _ in range(3):
        await db.execute(
            """INSERT INTO audit_log (id, timestamp, service_id, action_id, passport_hash,
                                       inputs_hash, outcome, response_code, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), now, "stayright-hotels", "search-availability",
             passport_hash, "inputs-hash", "success", 200, 10),
        )
    await db.commit()

    # Should be rejected with limit of 3/minute
    ok, error = await check_rate_limit(
        db, passport_hash, "stayright-hotels", "search-availability", "3/minute"
    )
    assert ok is False
    assert error["error"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_rate_limit_different_action_not_counted(seeded_db):
    """Requests to a different action should not count against the limit."""
    db = seeded_db
    passport_hash = "rate-test-diff-action"
    now = datetime.now(timezone.utc).isoformat()

    # Insert 5 entries for a DIFFERENT action
    for _ in range(5):
        await db.execute(
            """INSERT INTO audit_log (id, timestamp, service_id, action_id, passport_hash,
                                       inputs_hash, outcome, response_code, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), now, "stayright-hotels", "get-room-details",
             passport_hash, "inputs-hash", "success", 200, 10),
        )
    await db.commit()

    # search-availability should still be allowed (different action)
    ok, error = await check_rate_limit(
        db, passport_hash, "stayright-hotels", "search-availability", "3/minute"
    )
    assert ok is True


@pytest.mark.asyncio
async def test_rate_limit_expired_entries_not_counted(seeded_db):
    """Entries outside the window should not count."""
    db = seeded_db
    passport_hash = "rate-test-expired"
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    # Insert entries that are 5 minutes old (outside 1-minute window)
    for _ in range(10):
        await db.execute(
            """INSERT INTO audit_log (id, timestamp, service_id, action_id, passport_hash,
                                       inputs_hash, outcome, response_code, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), old_time, "stayright-hotels", "search-availability",
             passport_hash, "inputs-hash", "success", 200, 10),
        )
    await db.commit()

    # Should pass — old entries are outside the window
    ok, error = await check_rate_limit(
        db, passport_hash, "stayright-hotels", "search-availability", "3/minute"
    )
    assert ok is True


# ---------------------------------------------------------------------------
# Integration tests — full order flow with policy enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_wrong_input_type_rejected(cafe_client):
    """Passing wrong input type (string instead of int) should return 422."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {
            "city": "Austin",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guests": "two",  # should be int
        },
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "invalid_input_types"
    assert any("guests" in e for e in detail["type_errors"])


@pytest.mark.asyncio
async def test_order_bool_for_int_rejected(cafe_client):
    """Passing bool where int is expected should be rejected."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {
            "city": "Austin",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guests": True,
        },
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "invalid_input_types"


@pytest.mark.asyncio
async def test_order_correct_types_still_pass(cafe_client):
    """Correct types should still reach the backend and return 200."""
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {
            "city": "Austin",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guests": 2,
        },
    })
    assert resp.status_code == 200
    assert "results" in resp.json()


@pytest.mark.asyncio
async def test_order_rate_limit_enforced(cafe_client, seeded_db):
    """Exceeding rate limit via rapid requests should return 429."""
    db = seeded_db
    passport_hash = hashlib.sha256(b"demo-passport").hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()

    # Stuff the audit log to simulate hitting the 60/minute limit
    for _ in range(60):
        await db.execute(
            """INSERT INTO audit_log (id, timestamp, service_id, action_id, passport_hash,
                                       inputs_hash, outcome, response_code, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), now, "stayright-hotels", "search-availability",
             passport_hash, "inputs-hash", "success", 200, 10),
        )
    await db.commit()

    # This request should be rate-limited
    resp = await cafe_client.post("/cafe/order", json={
        "service_id": "stayright-hotels",
        "action_id": "search-availability",
        "passport": "demo-passport",
        "inputs": {
            "city": "Austin",
            "check_in": "2026-03-15",
            "check_out": "2026-03-18",
            "guests": 2,
        },
    })
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "rate_limit_exceeded"
