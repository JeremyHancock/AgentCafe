"""Tests for Sprint 3: landing page, sign-up funnel, default redirects."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient  # noqa: F401

from agentcafe.main import create_cafe_app  # noqa: F401

# pylint: disable=redefined-outer-name,unused-argument


@pytest_asyncio.fixture(scope="module")
async def landing_client(seeded_db):
    """HTTP client for landing/auth tests."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_landing_has_signin_link(landing_client: AsyncClient):
    """Landing page should have a Sign in link."""
    resp = await landing_client.get("/")
    assert resp.status_code == 200
    assert 'href="/login"' in resp.text
    assert "Sign in" in resp.text


@pytest.mark.asyncio
async def test_landing_has_list_service_link(landing_client: AsyncClient):
    """Landing page should have a List your service link."""
    resp = await landing_client.get("/")
    assert resp.status_code == 200
    assert 'href="/services/register"' in resp.text


@pytest.mark.asyncio
async def test_landing_has_for_humans_section(landing_client: AsyncClient):
    """Landing page should have a For humans section."""
    resp = await landing_client.get("/")
    assert resp.status_code == 200
    assert "For humans" in resp.text


@pytest.mark.asyncio
async def test_landing_has_for_companies_section(landing_client: AsyncClient):
    """Landing page should have a For companies section."""
    resp = await landing_client.get("/")
    assert resp.status_code == 200
    assert "For companies" in resp.text


@pytest.mark.asyncio
async def test_landing_mentions_mcp(landing_client: AsyncClient):
    """Landing page how-it-works should mention MCP."""
    resp = await landing_client.get("/")
    assert resp.status_code == 200
    assert "MCP" in resp.text or "Model Context Protocol" in resp.text


# ---------------------------------------------------------------------------
# Login/register default redirect to /tab
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_page_has_register_link(landing_client: AsyncClient):
    """Login page should link to register."""
    resp = await landing_client.get("/login")
    assert resp.status_code == 200
    assert 'href="/register' in resp.text
    assert "Create one" in resp.text


@pytest.mark.asyncio
async def test_register_page_has_login_link(landing_client: AsyncClient):
    """Register page should link to login."""
    resp = await landing_client.get("/register")
    assert resp.status_code == 200
    assert 'href="/login' in resp.text
    assert "Sign in" in resp.text


@pytest.mark.asyncio
async def test_login_js_defaults_to_tab(landing_client: AsyncClient):
    """Login page JS passkey flow should default redirect to /tab."""
    resp = await landing_client.get("/login")
    assert resp.status_code == 200
    assert "'/tab'" in resp.text


@pytest.mark.asyncio
async def test_register_js_defaults_to_tab(landing_client: AsyncClient):
    """Register page JS passkey flow should default redirect to /tab."""
    resp = await landing_client.get("/register")
    assert resp.status_code == 200
    assert "'/tab'" in resp.text
