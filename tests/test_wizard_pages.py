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

from agentcafe.cafe.pages import configure_pages
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
    configure_pages(_TEST_SIGNING_SECRET)
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
async def test_login_page_redirects_to_unified(wp_client):
    """GET /services/login redirects to the unified /login page."""
    resp = await wp_client.get("/services/login", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers.get("location", "")


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
    """POST /login with valid company credentials sets company session cookie."""
    email = f"login-{uuid.uuid4().hex[:6]}@test.com"
    await _register_company(wp_client, email=email, password="my-password-123")

    # Clear session so we can access the login page
    wp_client.cookies.clear()

    # Login via unified endpoint
    page = await wp_client.get("/login")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/login", data={
        "email": email,
        "password": "my-password-123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.cookies.get("company_session") is not None


@pytest.mark.asyncio
async def test_login_with_invalid_password(wp_client):
    """POST /login with wrong company password returns 401."""
    email = f"bad-{uuid.uuid4().hex[:6]}@test.com"
    await _register_company(wp_client, email=email, password="correct-pass-123")

    # Clear session so we can access the login page
    wp_client.cookies.clear()

    page = await wp_client.get("/login")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/login", data={
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
    assert "/login" in resp.headers.get("location", "")


# ===========================================================================
# Onboard wizard tests
# ===========================================================================

@pytest.mark.asyncio
async def test_onboard_requires_auth(wp_client):
    """GET /services/onboard without session redirects to login."""
    resp = await wp_client.get("/services/onboard", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers.get("location", "")


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
    assert "/login" in resp.headers.get("location", "")


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
    """GET /services/login always redirects to unified /login."""
    cookie, _ = await _register_company(wp_client)
    resp = await wp_client.get("/services/login",
                               cookies={"company_session": cookie},
                               follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers.get("location", "")


# ===========================================================================
# Unified login tests
# ===========================================================================


@pytest.mark.asyncio
async def test_unified_login_company_only(wp_client):
    """POST /login with company-only credentials sets company_session only."""
    email = f"co-only-{uuid.uuid4().hex[:6]}@test.com"
    await _register_company(wp_client, email=email, password="test-pass-123")
    wp_client.cookies.clear()

    page = await wp_client.get("/login")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/login", data={
        "email": email,
        "password": "test-pass-123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.cookies.get("company_session") is not None
    assert resp.cookies.get("cafe_session") is None


@pytest.mark.asyncio
async def test_unified_login_both_accounts(wp_client):
    """POST /login with same email in both tables sets both session cookies."""
    from agentcafe.db.engine import get_db
    from agentcafe.cafe.human import _hash_password

    email = f"both-{uuid.uuid4().hex[:6]}@test.com"
    password = "shared-pass-123"

    # Register company
    await _register_company(wp_client, email=email, password=password)
    wp_client.cookies.clear()

    # Also create a human account with same email and password
    db = await get_db()
    user_id = str(uuid.uuid4())
    now = "2026-01-01T00:00:00Z"
    await db.execute(
        """INSERT INTO cafe_users (id, email, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, email.lower(), _hash_password(password), now, now),
    )
    await db.commit()

    page = await wp_client.get("/login")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/login", data={
        "email": email,
        "password": password,
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.cookies.get("company_session") is not None
    assert resp.cookies.get("cafe_session") is not None


@pytest.mark.asyncio
async def test_unified_logout_clears_both_cookies(wp_client):
    """GET /logout clears both cafe_session and company_session."""
    from agentcafe.db.engine import get_db
    from agentcafe.cafe.human import _hash_password, _create_human_session_token

    email = f"logout-{uuid.uuid4().hex[:6]}@test.com"
    await _register_company(wp_client, email=email, password="test-pass-123")
    wp_client.cookies.clear()

    # Create human account too
    db = await get_db()
    user_id = str(uuid.uuid4())
    now = "2026-01-01T00:00:00Z"
    await db.execute(
        """INSERT INTO cafe_users (id, email, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, email.lower(), _hash_password("test-pass-123"), now, now),
    )
    await db.commit()

    # Login to get both cookies
    page = await wp_client.get("/login")
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/login", data={
        "email": email,
        "password": "test-pass-123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.cookies.get("cafe_session") is not None
    assert resp.cookies.get("company_session") is not None

    # Logout should clear both
    resp = await wp_client.get("/logout", follow_redirects=False)
    assert resp.status_code == 303
    # Check that delete-cookie headers are set for both cookies
    set_cookies = resp.headers.get_list("set-cookie")
    cookie_names = [c.split("=")[0] for c in set_cookies]
    assert "cafe_session" in cookie_names
    assert "company_session" in cookie_names


@pytest.mark.asyncio
async def test_services_login_redirect(wp_client):
    """GET /services/login redirects to /login."""
    resp = await wp_client.get("/services/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/login"


@pytest.mark.asyncio
async def test_nav_shows_services_for_company(wp_client):
    """Authenticated company pages show 'My Services' in nav."""
    cookie, _ = await _register_company(wp_client)
    resp = await wp_client.get("/services",
                               cookies={"company_session": cookie})
    assert resp.status_code == 200
    assert "My Services" in resp.text
    assert "Sign out" in resp.text


@pytest.mark.asyncio
async def test_landing_shows_signout_when_logged_in(wp_client):
    """Landing page shows Sign out when a company session is active."""
    cookie, _ = await _register_company(wp_client)
    resp = await wp_client.get("/", cookies={"company_session": cookie})
    assert resp.status_code == 200
    assert "Sign out" in resp.text
    assert "My Services" in resp.text


@pytest.mark.asyncio
async def test_login_page_has_company_register_link(wp_client):
    """Login page has a link to register as a company."""
    resp = await wp_client.get("/login")
    assert resp.status_code == 200
    assert 'href="/services/register"' in resp.text
    assert "Register as a company" in resp.text


@pytest.mark.asyncio
async def test_company_register_has_unified_login_link(wp_client):
    """Company register page links to unified /login, not /services/login."""
    resp = await wp_client.get("/services/register")
    assert resp.status_code == 200
    assert 'href="/login"' in resp.text


# ===========================================================================
# JV integration page flow tests
# ===========================================================================


async def _get_draft_at_policy(wp_client):
    """Helper: register, parse spec, submit review. Returns (cookie, draft_id, csrf)."""
    cookie, _ = await _register_company(wp_client)
    cookies = {"company_session": cookie}

    # Parse spec
    page = await wp_client.get("/services/onboard", cookies=cookies)
    csrf = _extract_csrf(page.text)
    resp = await wp_client.post("/services/onboard", data={
        "raw_spec": SAMPLE_OPENAPI_JSON,
        "csrf_token": csrf,
    }, cookies=cookies, follow_redirects=False)
    location = resp.headers.get("location", "")
    draft_id = location.split("/")[-2] if "/review" in location else ""

    # Submit review
    review_page = await wp_client.get(location, cookies=cookies)
    csrf = _extract_csrf(review_page.text)
    await wp_client.post(location, data={
        "csrf_token": csrf,
        "service_id": "jvpage-test",
        "name": "JV Page Test",
        "description": "Test service for JV pages",
    }, cookies=cookies, follow_redirects=False)

    return cookie, draft_id, cookies


@pytest.mark.asyncio
async def test_policy_page_shows_integration_selector(wp_client):
    """Policy page renders integration mode radio buttons."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    resp = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    assert resp.status_code == 200
    assert "integration_mode_radio" in resp.text
    assert "jointly_verified" in resp.text
    assert "Standard" in resp.text


@pytest.mark.asyncio
async def test_policy_submit_jv_redirects_to_integration(wp_client):
    """POST policy with JV mode redirects to integration page."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    policy_page = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    csrf = _extract_csrf(policy_page.text)
    resp = await wp_client.post(f"/services/onboard/{draft_id}/policy", data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "policy_json": "{}",
        "integration_mode": "jointly_verified",
    }, cookies=cookies, follow_redirects=False)
    assert resp.status_code == 303
    assert "/integration" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_policy_submit_standard_redirects_to_preview(wp_client):
    """POST policy with standard mode redirects to preview."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    policy_page = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    csrf = _extract_csrf(policy_page.text)
    resp = await wp_client.post(f"/services/onboard/{draft_id}/policy", data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "policy_json": "{}",
        "integration_mode": "standard",
    }, cookies=cookies, follow_redirects=False)
    assert resp.status_code == 303
    assert "/preview" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_integration_page_renders(wp_client):
    """GET integration page for JV draft renders the form."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    # Set JV mode via policy submit
    policy_page = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    csrf = _extract_csrf(policy_page.text)
    await wp_client.post(f"/services/onboard/{draft_id}/policy", data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "policy_json": "{}",
        "integration_mode": "jointly_verified",
    }, cookies=cookies, follow_redirects=False)

    resp = await wp_client.get(
        f"/services/onboard/{draft_id}/integration", cookies=cookies,
    )
    assert resp.status_code == 200
    assert "integration_base_url" in resp.text
    assert "cap_revoke" in resp.text
    assert "Jointly-verified" in resp.text


@pytest.mark.asyncio
async def test_integration_page_redirects_standard(wp_client):
    """GET integration page for standard draft redirects to preview."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    # Set standard mode via policy
    policy_page = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    csrf = _extract_csrf(policy_page.text)
    await wp_client.post(f"/services/onboard/{draft_id}/policy", data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "policy_json": "{}",
        "integration_mode": "standard",
    }, cookies=cookies, follow_redirects=False)

    resp = await wp_client.get(
        f"/services/onboard/{draft_id}/integration", cookies=cookies,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/preview" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_integration_submit_saves_and_redirects(wp_client):
    """POST integration with valid data redirects to preview."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    policy_page = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    csrf = _extract_csrf(policy_page.text)
    await wp_client.post(f"/services/onboard/{draft_id}/policy", data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "policy_json": "{}",
        "integration_mode": "jointly_verified",
    }, cookies=cookies, follow_redirects=False)

    integ_page = await wp_client.get(
        f"/services/onboard/{draft_id}/integration", cookies=cookies,
    )
    csrf = _extract_csrf(integ_page.text)
    resp = await wp_client.post(f"/services/onboard/{draft_id}/integration", data={
        "csrf_token": csrf,
        "integration_base_url": "https://hm.example.com",
        "identity_matching": "opaque_id",
        "cap_account_create": "on",
        "cap_revoke": "on",
    }, cookies=cookies, follow_redirects=False)
    assert resp.status_code == 303
    assert "/preview" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_integration_submit_requires_base_url(wp_client):
    """POST integration without base URL returns 400."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    policy_page = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    csrf = _extract_csrf(policy_page.text)
    await wp_client.post(f"/services/onboard/{draft_id}/policy", data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "policy_json": "{}",
        "integration_mode": "jointly_verified",
    }, cookies=cookies, follow_redirects=False)

    integ_page = await wp_client.get(
        f"/services/onboard/{draft_id}/integration", cookies=cookies,
    )
    csrf = _extract_csrf(integ_page.text)
    resp = await wp_client.post(f"/services/onboard/{draft_id}/integration", data={
        "csrf_token": csrf,
        "integration_base_url": "",
        "cap_account_create": "on",
    }, cookies=cookies, follow_redirects=False)
    assert resp.status_code == 400
    assert "required" in resp.text.lower()


@pytest.mark.asyncio
async def test_integration_submit_requires_account_cap(wp_client):
    """POST integration with no account capabilities returns 422."""
    _cookie, draft_id, cookies = await _get_draft_at_policy(wp_client)
    policy_page = await wp_client.get(
        f"/services/onboard/{draft_id}/policy", cookies=cookies,
    )
    csrf = _extract_csrf(policy_page.text)
    await wp_client.post(f"/services/onboard/{draft_id}/policy", data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "policy_json": "{}",
        "integration_mode": "jointly_verified",
    }, cookies=cookies, follow_redirects=False)

    integ_page = await wp_client.get(
        f"/services/onboard/{draft_id}/integration", cookies=cookies,
    )
    csrf = _extract_csrf(integ_page.text)
    resp = await wp_client.post(f"/services/onboard/{draft_id}/integration", data={
        "csrf_token": csrf,
        "integration_base_url": "https://hm.example.com",
        "cap_revoke": "on",
    }, cookies=cookies, follow_redirects=False)
    assert resp.status_code == 422
    assert "account-check" in resp.text or "account-create" in resp.text or "link-complete" in resp.text
