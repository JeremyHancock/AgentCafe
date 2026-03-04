"""Tests for the Jinja2 company wizard pages.

Tests cover: company login/register pages, session cookie auth,
onboard wizard flow (spec → review → policy → preview → publish),
services management, and admin dashboard.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agentcafe.cafe.wizard_pages import configure_wizard_pages
from agentcafe.db.engine import close_db, init_db
from agentcafe.main import create_cafe_app
from agentcafe.wizard.router import configure_wizard

# pylint: disable=redefined-outer-name,unused-argument

_TEST_SIGNING_SECRET = "test-wizard-secret-minimum-32-bytes!"


SAMPLE_OPENAPI_JSON = json.dumps({
    "openapi": "3.1.0",
    "info": {"title": "TestAPI", "version": "1.0.0"},
    "paths": {
        "/items": {
            "get": {
                "operationId": "listItems",
                "summary": "List items",
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "operationId": "createItem",
                "summary": "Create an item",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {
                                    "name": {"type": "string", "example": "Widget"},
                                },
                            },
                        },
                    },
                },
                "responses": {"201": {"description": "Created"}},
            },
        },
    },
})


@pytest_asyncio.fixture
async def wp_db():
    """Isolated in-memory DB for wizard page tests."""
    configure_wizard(_TEST_SIGNING_SECRET)
    configure_wizard_pages(
        _TEST_SIGNING_SECRET,
        quarantine_days=7,
        issuer_api_key="test-admin-key",
    )
    db = await init_db(":memory:")
    yield db
    await close_db()


@pytest_asyncio.fixture
async def wp_client(wp_db):
    """Async HTTP client with wizard page routes available."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _register_company(client, name="TestCo", email=None, password="secure-pass-123"):
    """Register a company via the page flow and return session cookie."""
    if email is None:
        email = f"co-{uuid.uuid4().hex[:6]}@test.com"

    # Get CSRF token
    page = await client.get("/services/register")
    assert page.status_code == 200
    csrf = _extract_csrf(page.text)

    resp = await client.post("/services/register", data={
        "name": name,
        "email": email,
        "password": password,
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 303
    cookie = resp.cookies.get("company_session")
    return cookie, email


def _extract_csrf(html: str) -> str:
    """Extract the CSRF token from HTML form."""
    import re
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'value="([^"]+)"\s*>', html)
    return match.group(1) if match else ""


# ===========================================================================
# Login / Register page tests
# ===========================================================================

@pytest.mark.asyncio
async def test_login_page_renders(wp_client):
    """GET /services/login renders the login form."""
    resp = await wp_client.get("/services/login")
    assert resp.status_code == 200
    assert "Company Dashboard" in resp.text
    assert "csrf_token" in resp.text


@pytest.mark.asyncio
async def test_register_page_renders(wp_client):
    """GET /services/register renders the registration form."""
    resp = await wp_client.get("/services/register")
    assert resp.status_code == 200
    assert "Register your company" in resp.text
    assert "csrf_token" in resp.text


@pytest.mark.asyncio
async def test_register_creates_account(wp_client):
    """POST /services/register creates a company and sets session cookie."""
    cookie, _ = await _register_company(wp_client)
    assert cookie is not None


@pytest.mark.asyncio
async def test_register_duplicate_email_rejected(wp_client):
    """Duplicate email registration returns error."""
    email = f"dup-{uuid.uuid4().hex[:6]}@test.com"
    await _register_company(wp_client, email=email)

    # Clear session so we can access the register page again
    wp_client.cookies.clear()

    # Try again with same email
    page = await wp_client.get("/services/register")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/services/register", data={
        "name": "Dup Co",
        "email": email,
        "password": "another-pass-123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 409
    assert "already exists" in resp.text


@pytest.mark.asyncio
async def test_login_with_valid_credentials(wp_client):
    """POST /services/login with valid credentials sets session cookie."""
    email = f"login-{uuid.uuid4().hex[:6]}@test.com"
    await _register_company(wp_client, email=email, password="my-password-123")

    # Clear session so we can access the login page
    wp_client.cookies.clear()

    # Login
    page = await wp_client.get("/services/login")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/services/login", data={
        "email": email,
        "password": "my-password-123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.cookies.get("company_session") is not None


@pytest.mark.asyncio
async def test_login_with_invalid_password(wp_client):
    """POST /services/login with wrong password returns 401."""
    email = f"bad-{uuid.uuid4().hex[:6]}@test.com"
    await _register_company(wp_client, email=email, password="correct-pass-123")

    # Clear session so we can access the login page
    wp_client.cookies.clear()

    page = await wp_client.get("/services/login")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/services/login", data={
        "email": email,
        "password": "wrong-password",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text


@pytest.mark.asyncio
async def test_logout_clears_cookie(wp_client):
    """GET /services/logout clears the session cookie."""
    cookie, _ = await _register_company(wp_client)
    resp = await wp_client.get("/services/logout", cookies={"company_session": cookie},
                               follow_redirects=False)
    assert resp.status_code == 303
    assert "/services/login" in resp.headers.get("location", "")


# ===========================================================================
# Onboard wizard tests
# ===========================================================================

@pytest.mark.asyncio
async def test_onboard_requires_auth(wp_client):
    """GET /services/onboard without session redirects to login."""
    resp = await wp_client.get("/services/onboard", follow_redirects=False)
    assert resp.status_code == 303
    assert "/services/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_onboard_spec_page_renders(wp_client):
    """GET /services/onboard with session renders spec input page."""
    cookie, _ = await _register_company(wp_client)
    resp = await wp_client.get("/services/onboard",
                               cookies={"company_session": cookie})
    assert resp.status_code == 200
    assert "Upload your OpenAPI spec" in resp.text


@pytest.mark.asyncio
async def test_onboard_spec_parse_redirects_to_review(wp_client):
    """POST /services/onboard with valid spec redirects to review page."""
    cookie, _ = await _register_company(wp_client)

    page = await wp_client.get("/services/onboard",
                               cookies={"company_session": cookie})
    csrf = _extract_csrf(page.text)

    resp = await wp_client.post("/services/onboard", data={
        "raw_spec": SAMPLE_OPENAPI_JSON,
        "csrf_token": csrf,
    }, cookies={"company_session": cookie}, follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    assert "/review" in location


@pytest.mark.asyncio
async def test_onboard_spec_invalid_returns_error(wp_client):
    """POST /services/onboard with invalid spec shows error."""
    cookie, _ = await _register_company(wp_client)

    page = await wp_client.get("/services/onboard",
                               cookies={"company_session": cookie})
    csrf = _extract_csrf(page.text)

    resp = await wp_client.post("/services/onboard", data={
        "raw_spec": "not valid json or yaml",
        "csrf_token": csrf,
    }, cookies={"company_session": cookie}, follow_redirects=False)
    assert resp.status_code == 422
    assert "parse error" in resp.text.lower() or "Spec parse error" in resp.text


@pytest.mark.asyncio
async def test_onboard_review_page_renders(wp_client):
    """Review page renders with candidate data from parsed spec."""
    cookie, _ = await _register_company(wp_client)

    # Parse spec to get draft_id
    page = await wp_client.get("/services/onboard",
                               cookies={"company_session": cookie})
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/services/onboard", data={
        "raw_spec": SAMPLE_OPENAPI_JSON,
        "csrf_token": csrf,
    }, cookies={"company_session": cookie}, follow_redirects=False)
    location = resp.headers.get("location", "")

    # Load review page
    review_resp = await wp_client.get(location,
                                       cookies={"company_session": cookie})
    assert review_resp.status_code == 200
    assert "Review AI-generated Menu entry" in review_resp.text
    assert "listItems" in review_resp.text or "list-items" in review_resp.text


@pytest.mark.asyncio
async def test_services_page_requires_auth(wp_client):
    """GET /services without session redirects to login."""
    resp = await wp_client.get("/services", follow_redirects=False)
    assert resp.status_code == 303
    assert "/services/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_services_page_empty(wp_client):
    """GET /services with session but no published services shows empty state."""
    cookie, _ = await _register_company(wp_client)
    resp = await wp_client.get("/services",
                               cookies={"company_session": cookie})
    assert resp.status_code == 200
    assert "No services published" in resp.text


# ===========================================================================
# Admin dashboard tests
# ===========================================================================

@pytest.mark.asyncio
async def test_admin_page_renders_login_gate(wp_client):
    """GET /admin without key shows login form."""
    resp = await wp_client.get("/admin")
    assert resp.status_code == 200
    assert "Admin Key" in resp.text
    assert "authenticated" not in resp.text or "Platform Admin" not in resp.text


@pytest.mark.asyncio
async def test_admin_page_invalid_key(wp_client):
    """GET /admin with wrong key shows error."""
    resp = await wp_client.get("/admin?key=wrong-key")
    assert resp.status_code == 200
    assert "Invalid admin key" in resp.text


@pytest.mark.asyncio
async def test_admin_page_valid_key(wp_client):
    """GET /admin with correct key shows dashboard."""
    resp = await wp_client.get("/admin?key=test-admin-key")
    assert resp.status_code == 200
    assert "Platform Admin" in resp.text


@pytest.mark.asyncio
async def test_logged_in_user_redirected_from_login(wp_client):
    """GET /services/login when already logged in redirects to onboard."""
    cookie, _ = await _register_company(wp_client)
    resp = await wp_client.get("/services/login",
                               cookies={"company_session": cookie},
                               follow_redirects=False)
    assert resp.status_code == 303
    assert "/services/onboard" in resp.headers.get("location", "")
