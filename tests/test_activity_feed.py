"""Tests for Sprint 5: activity feeds on tab, dashboard, and admin pages.

Verifies that audit log entries surface correctly in the human-facing
activity feeds and that the admin dashboard shows enhanced stats.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agentcafe.cafe.human import _create_human_session_token
from agentcafe.cafe.wizard_pages import configure_wizard_pages
from agentcafe.db.engine import close_db, init_db
from agentcafe.db.seed import seed_demo_data
from agentcafe.config import load_config
from agentcafe.main import create_cafe_app
from agentcafe.wizard.router import configure_wizard

# pylint: disable=redefined-outer-name,unused-argument,protected-access

_TEST_SIGNING_SECRET = "test-activity-secret-minimum-32-bytes!"


@pytest_asyncio.fixture
async def act_db():
    """Isolated in-memory DB with demo data for activity feed tests."""
    configure_wizard(_TEST_SIGNING_SECRET)
    configure_wizard_pages(
        _TEST_SIGNING_SECRET,
        quarantine_days=7,
        issuer_api_key="test-admin-key",
    )
    db = await init_db(":memory:")
    cfg = load_config()
    await seed_demo_data(db, cfg)
    yield db
    await close_db()


@pytest_asyncio.fixture
async def act_client(act_db):
    """Async HTTP client for activity feed tests."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _create_user_with_card(db):
    """Create a test user with an active company card and return session token + service_id."""
    user_id = f"act-user-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO cafe_users (id, email, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, f"{user_id}@test.com", "dummy", now, now),
    )

    # Use a seeded service
    cursor = await db.execute("SELECT service_id FROM proxy_configs LIMIT 1")
    row = await cursor.fetchone()
    service_id = row["service_id"] if row else "test-service"

    # Create an active company card
    card_id = f"card-{uuid.uuid4().hex[:8]}"
    policy_id = f"pol-{uuid.uuid4().hex[:8]}"
    expires = "2099-01-01T00:00:00Z"

    await db.execute(
        """INSERT INTO company_cards (id, service_id, cafe_user_id, status, activation_code,
           excluded_action_ids, budget_limit_cents, budget_spent_cents, budget_period,
           first_use_confirmation, first_use_confirmed_at, policy_id,
           expires_at, revoked_at, created_at, updated_at)
           VALUES (?, ?, ?, 'active', 'TESTCODE', '', 0, 0, '', 0, NULL, ?, ?, NULL, ?, ?)""",
        (card_id, service_id, user_id, policy_id, expires, now, now),
    )
    await db.commit()

    token = _create_human_session_token(user_id, f"{user_id}@test.com")
    return token, service_id


async def _create_user_with_policy(db):
    """Create a test user with an active policy and return session token + service_id."""
    user_id = f"pol-user-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO cafe_users (id, email, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, f"{user_id}@test.com", "dummy", now, now),
    )

    cursor = await db.execute("SELECT service_id FROM proxy_configs LIMIT 1")
    row = await cursor.fetchone()
    service_id = row["service_id"] if row else "test-service"

    policy_id = f"pol-{uuid.uuid4().hex[:8]}"
    expires = "2099-01-01T00:00:00Z"

    await db.execute(
        """INSERT INTO policies (id, cafe_user_id, service_id, allowed_action_ids,
           scopes, risk_tier, max_token_lifetime_seconds, expires_at, revoked_at, created_at, updated_at)
           VALUES (?, ?, ?, 'listItems,createItem', 'test:listItems,test:createItem',
                   'medium', 900, ?, NULL, ?, ?)""",
        (policy_id, user_id, service_id, expires, now, now),
    )
    await db.commit()

    token = _create_human_session_token(user_id, f"{user_id}@test.com")
    return token, service_id, policy_id


async def _insert_audit_entries(db, service_id, count=5):
    """Insert test audit log entries for a service."""
    now = datetime.now(timezone.utc)
    for i in range(count):
        entry_id = f"audit-{uuid.uuid4().hex[:8]}"
        ts = now.isoformat()
        await db.execute(
            """INSERT INTO audit_log (id, timestamp, service_id, action_id,
               passport_hash, inputs_hash, outcome, response_code, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, ts, service_id, "listItems", "hash123", "inp456",
             "success" if i % 3 != 0 else "error", 200 if i % 3 != 0 else 500, 42 + i),
        )
    await db.commit()


# ===========================================================================
# Tab page activity feed
# ===========================================================================


@pytest.mark.asyncio
async def test_tab_shows_recent_activity(act_client, act_db):
    """Tab page should show 'Recent Activity' section when audit entries exist."""
    token, service_id = await _create_user_with_card(act_db)
    await _insert_audit_entries(act_db, service_id, count=3)

    resp = await act_client.get("/tab", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert "Recent Activity" in resp.text


@pytest.mark.asyncio
async def test_tab_activity_shows_outcome(act_client, act_db):
    """Tab activity feed should display outcomes (success/error)."""
    token, service_id = await _create_user_with_card(act_db)
    await _insert_audit_entries(act_db, service_id, count=3)

    resp = await act_client.get("/tab", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert "success" in resp.text or "error" in resp.text


@pytest.mark.asyncio
async def test_tab_activity_shows_latency(act_client, act_db):
    """Tab activity feed should display latency values."""
    token, service_id = await _create_user_with_card(act_db)
    await _insert_audit_entries(act_db, service_id, count=1)

    resp = await act_client.get("/tab", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert "ms" in resp.text


@pytest.mark.asyncio
async def test_tab_no_activity_when_empty(act_client, act_db):
    """Tab page should not show activity section when there are no audit entries."""
    token, _service_id = await _create_user_with_card(act_db)
    # Don't insert any audit entries
    resp = await act_client.get("/tab", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert "Recent Activity" not in resp.text


# ===========================================================================
# Dashboard per-policy activity
# ===========================================================================


@pytest.mark.asyncio
async def test_dashboard_shows_policy_activity(act_client, act_db):
    """Dashboard should show expandable per-policy activity."""
    token, service_id, _policy_id = await _create_user_with_policy(act_db)
    await _insert_audit_entries(act_db, service_id, count=3)

    resp = await act_client.get("/dashboard", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert "Recent activity" in resp.text


@pytest.mark.asyncio
async def test_dashboard_no_activity_when_empty(act_client, act_db):
    """Dashboard should not show activity details when no audit entries exist."""
    token, _service_id, _policy_id = await _create_user_with_policy(act_db)
    resp = await act_client.get("/dashboard", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert "Recent activity" not in resp.text


# ===========================================================================
# Admin dashboard enhanced stats
# ===========================================================================


@pytest.mark.asyncio
async def test_admin_shows_active_policies_count(act_client, act_db):
    """Admin dashboard should show active policies count."""
    resp = await act_client.get("/admin", cookies={"admin_key": "test-admin-key"})
    assert resp.status_code == 200
    assert "Active Policies" in resp.text


@pytest.mark.asyncio
async def test_admin_shows_active_cards_count(act_client, act_db):
    """Admin dashboard should show active cards count."""
    resp = await act_client.get("/admin", cookies={"admin_key": "test-admin-key"})
    assert resp.status_code == 200
    assert "Active Cards" in resp.text


@pytest.mark.asyncio
async def test_admin_shows_latency_stats(act_client, act_db):
    """Admin dashboard should show p50 and p95 latency."""
    resp = await act_client.get("/admin", cookies={"admin_key": "test-admin-key"})
    assert resp.status_code == 200
    assert "p50 Latency" in resp.text
    assert "p95 Latency" in resp.text


@pytest.mark.asyncio
async def test_admin_shows_consent_funnel(act_client, act_db):
    """Admin dashboard should show consent funnel stats."""
    resp = await act_client.get("/admin", cookies={"admin_key": "test-admin-key"})
    assert resp.status_code == 200
    assert "Consent Funnel" in resp.text
    assert "pending" in resp.text
    assert "approved" in resp.text
    assert "declined" in resp.text


@pytest.mark.asyncio
async def test_admin_audit_log_paginated(act_client, act_db):
    """Admin audit log should be paginated with page number shown."""
    resp = await act_client.get("/admin", cookies={"admin_key": "test-admin-key"})
    assert resp.status_code == 200
    assert "page 1" in resp.text


@pytest.mark.asyncio
async def test_admin_audit_log_page_navigation(act_client, act_db):
    """Admin should support page query parameter for audit log."""
    resp = await act_client.get("/admin?page=2", cookies={"admin_key": "test-admin-key"})
    assert resp.status_code == 200
    assert "page 2" in resp.text
    # Page 2 should have a Previous link
    assert "Previous" in resp.text
