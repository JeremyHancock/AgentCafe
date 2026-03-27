"""Tests for Sprint 2: navigation bar, consent flow polish, dashboard/tab improvements."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient  # noqa: F401

from agentcafe.cafe.human import _create_human_session_token
from agentcafe.db.engine import get_db
from agentcafe.main import create_cafe_app  # noqa: F401

# pylint: disable=redefined-outer-name,unused-argument,protected-access


@pytest_asyncio.fixture(scope="module")
async def nav_client(seeded_db):
    """HTTP client for navigation tests."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _create_test_user_session():
    """Create a test human user and return session token."""
    db = await get_db()
    user_id = "nav-test-user"
    now = "2026-01-01T00:00:00Z"
    await db.execute(
        """INSERT OR IGNORE INTO cafe_users (id, email, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, "nav@test.com", "dummy", now, now),
    )
    await db.commit()
    return _create_human_session_token(user_id, "nav@test.com")


# ---------------------------------------------------------------------------
# Navigation bar presence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nav_bar_on_dashboard(nav_client: AsyncClient):
    """Dashboard page should show the navigation bar with Cards and Policies links."""
    token = await _create_test_user_session()
    resp = await nav_client.get("/dashboard", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert 'class="nav-links"' in resp.text
    assert 'href="/tab"' in resp.text
    assert 'href="/dashboard"' in resp.text
    assert "Sign out" in resp.text


@pytest.mark.asyncio
async def test_nav_bar_on_tab(nav_client: AsyncClient):
    """Tab page should show the navigation bar."""
    token = await _create_test_user_session()
    resp = await nav_client.get("/tab", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert 'class="nav-links"' in resp.text
    assert 'href="/tab"' in resp.text


@pytest.mark.asyncio
async def test_nav_bar_absent_on_login(nav_client: AsyncClient):
    """Login page should NOT show the navigation bar."""
    resp = await nav_client.get("/login")
    assert resp.status_code == 200
    assert 'class="nav-links"' not in resp.text


@pytest.mark.asyncio
async def test_nav_bar_absent_on_landing(nav_client: AsyncClient):
    """Landing page should NOT show the navigation bar."""
    resp = await nav_client.get("/")
    assert resp.status_code == 200
    assert 'class="nav-links"' not in resp.text


# ---------------------------------------------------------------------------
# Dashboard shows action descriptions (not raw IDs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_no_standalone_logout(nav_client: AsyncClient):
    """Dashboard should not have a standalone logout link (moved to nav bar)."""
    token = await _create_test_user_session()
    resp = await nav_client.get("/dashboard", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert 'class="logout-link"' not in resp.text


# ---------------------------------------------------------------------------
# Consent done page has CTAs
# ---------------------------------------------------------------------------


def test_consent_done_template_has_cta_links():
    """consent_done.html template should contain dashboard/tab CTA links."""
    from pathlib import Path
    template_path = Path(__file__).resolve().parent.parent / "agentcafe" / "templates" / "consent_done.html"
    content = template_path.read_text()
    # Approved state should link to dashboard
    assert "Go to your Dashboard" in content
    # Declined/expired states should link to tab
    assert "Go to your Tab" in content


@pytest.mark.asyncio
async def test_tab_no_standalone_logout(nav_client: AsyncClient):
    """Tab should not have a standalone logout link (moved to nav bar)."""
    token = await _create_test_user_session()
    resp = await nav_client.get("/tab", cookies={"cafe_session": token})
    assert resp.status_code == 200
    assert 'class="logout-link"' not in resp.text
    # Also verify the old tab-nav (Cards/Policies sub-nav) is removed
    assert 'class="tab-nav"' not in resp.text
