"""Tests for Sprint 4: wizard documentation and inline helper content.

Verifies that all five onboarding wizard steps contain the expected
helper text, tooltips, and guidance added in Sprint 4.
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
async def wd_db():
    """Isolated in-memory DB for wizard doc tests."""
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
async def wd_client(wd_db):
    """Async HTTP client for wizard doc tests."""
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


import re  # pylint: disable=wrong-import-position


def _extract_csrf(html: str) -> str:
    """Extract the CSRF token from HTML form."""
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'value="([^"]+)"\s*>', html)
    return match.group(1) if match else ""


async def _register_company(client, name="DocTestCo", email=None, password="secure-pass-123"):
    """Register a company and return session cookie."""
    if email is None:
        email = f"doc-{uuid.uuid4().hex[:6]}@test.com"
    page = await client.get("/services/register")
    csrf = _extract_csrf(page.text)
    resp = await client.post("/services/register", data={
        "name": name, "email": email, "password": password, "csrf_token": csrf,
    }, follow_redirects=False)
    return resp.cookies.get("company_session")


async def _go_through_wizard(client, cookie):
    """Walk through wizard steps 1-4 and return (draft_id, preview_response)."""
    # Step 1: submit spec
    page = await client.get("/services/onboard", cookies={"company_session": cookie})
    csrf = _extract_csrf(page.text)
    resp = await client.post("/services/onboard", data={
        "raw_spec": SAMPLE_OPENAPI_JSON, "csrf_token": csrf,
    }, cookies={"company_session": cookie}, follow_redirects=False)
    assert resp.status_code == 303
    review_url = resp.headers["location"]
    draft_id = review_url.split("/")[-2]

    # Step 2: submit review
    review = await client.get(review_url, cookies={"company_session": cookie})
    csrf = _extract_csrf(review.text)
    resp = await client.post(review_url, data={
        "csrf_token": csrf,
        "service_id": "test-doc-svc",
        "name": "Doc Test Service",
        "category": "testing",
        "capability_tags": "docs,test",
        "description": "A test service for doc tests",
        "actions_json": "[]",
        "excluded_actions": "",
    }, cookies={"company_session": cookie}, follow_redirects=False)
    assert resp.status_code == 303

    # Step 3: submit policy
    policy_url = f"/services/onboard/{draft_id}/policy"
    policy_page = await client.get(policy_url, cookies={"company_session": cookie})
    csrf = _extract_csrf(policy_page.text)
    resp = await client.post(policy_url, data={
        "csrf_token": csrf,
        "backend_url": "https://api.example.com",
        "backend_auth_header": "",
        "policy_json": "{}",
    }, cookies={"company_session": cookie}, follow_redirects=False)
    assert resp.status_code == 303

    # Step 4: preview
    preview_url = f"/services/onboard/{draft_id}/preview"
    preview = await client.get(preview_url, cookies={"company_session": cookie})
    return draft_id, preview


# ===========================================================================
# Step 1: Spec input helper content
# ===========================================================================


@pytest.mark.asyncio
async def test_spec_page_has_good_spec_guide(wd_client):
    """Spec input page should show 'What makes a good spec?' guidance."""
    cookie = await _register_company(wd_client)
    resp = await wd_client.get("/services/onboard", cookies={"company_session": cookie})
    assert resp.status_code == 200
    assert "What makes a good spec?" in resp.text


@pytest.mark.asyncio
async def test_spec_page_has_sample_button(wd_client):
    """Spec input page should have a 'Try a sample' button."""
    cookie = await _register_company(wd_client)
    resp = await wd_client.get("/services/onboard", cookies={"company_session": cookie})
    assert resp.status_code == 200
    assert "Try a sample" in resp.text


@pytest.mark.asyncio
async def test_spec_page_has_sample_spec_data(wd_client):
    """Spec input page should embed the sample spec for the JS loader."""
    cookie = await _register_company(wd_client)
    resp = await wd_client.get("/services/onboard", cookies={"company_session": cookie})
    assert resp.status_code == 200
    # The sample spec (BookShelf API) should be embedded as a JSON string
    assert "BookShelf" in resp.text or "sampleSpec" in resp.text


@pytest.mark.asyncio
async def test_spec_page_mentions_openapi_versions(wd_client):
    """Spec input page should mention supported OpenAPI versions."""
    cookie = await _register_company(wd_client)
    resp = await wd_client.get("/services/onboard", cookies={"company_session": cookie})
    assert resp.status_code == 200
    assert "OpenAPI 3.0" in resp.text or "3.1" in resp.text


# ===========================================================================
# Step 2: Review helper content
# ===========================================================================


@pytest.mark.asyncio
async def test_review_page_has_ai_generated_notice(wd_client):
    """Review page should show AI-generated verification notice."""
    cookie = await _register_company(wd_client)
    # Submit a spec to get to the review page
    page = await wd_client.get("/services/onboard", cookies={"company_session": cookie})
    csrf = _extract_csrf(page.text)
    resp = await wd_client.post("/services/onboard", data={
        "raw_spec": SAMPLE_OPENAPI_JSON, "csrf_token": csrf,
    }, cookies={"company_session": cookie}, follow_redirects=False)
    review_url = resp.headers["location"]
    review = await wd_client.get(review_url, cookies={"company_session": cookie})
    assert review.status_code == 200
    assert "AI-generated" in review.text


@pytest.mark.asyncio
async def test_review_page_has_field_descriptions(wd_client):
    """Review page should show field-level help text."""
    cookie = await _register_company(wd_client)
    page = await wd_client.get("/services/onboard", cookies={"company_session": cookie})
    csrf = _extract_csrf(page.text)
    resp = await wd_client.post("/services/onboard", data={
        "raw_spec": SAMPLE_OPENAPI_JSON, "csrf_token": csrf,
    }, cookies={"company_session": cookie}, follow_redirects=False)
    review_url = resp.headers["location"]
    review = await wd_client.get(review_url, cookies={"company_session": cookie})
    assert review.status_code == 200
    assert "Unique identifier agents use" in review.text
    assert "Human-readable name shown on the Menu" in review.text


# ===========================================================================
# Step 3: Policy helper content
# ===========================================================================


@pytest.mark.asyncio
async def test_policy_page_has_quick_reference(wd_client):
    """Policy page should show the policy quick reference box."""
    cookie = await _register_company(wd_client)
    # Walk through steps 1-2
    page = await wd_client.get("/services/onboard", cookies={"company_session": cookie})
    csrf = _extract_csrf(page.text)
    resp = await wd_client.post("/services/onboard", data={
        "raw_spec": SAMPLE_OPENAPI_JSON, "csrf_token": csrf,
    }, cookies={"company_session": cookie}, follow_redirects=False)
    review_url = resp.headers["location"]
    draft_id = review_url.split("/")[-2]
    review = await wd_client.get(review_url, cookies={"company_session": cookie})
    csrf = _extract_csrf(review.text)
    await wd_client.post(review_url, data={
        "csrf_token": csrf, "service_id": "pol-test", "name": "Policy Test",
        "category": "test", "capability_tags": "", "description": "test",
        "actions_json": "[]", "excluded_actions": "",
    }, cookies={"company_session": cookie}, follow_redirects=False)

    # Step 3
    policy = await wd_client.get(
        f"/services/onboard/{draft_id}/policy",
        cookies={"company_session": cookie},
    )
    assert policy.status_code == 200
    assert "Policy quick reference" in policy.text
    assert "Rate limit" in policy.text
    assert "Human consent" in policy.text


# ===========================================================================
# Step 4: Preview helper content
# ===========================================================================


@pytest.mark.asyncio
async def test_preview_page_has_what_happens_next(wd_client):
    """Preview page should show the 'What happens after you publish?' checklist."""
    cookie = await _register_company(wd_client)
    _draft_id, preview = await _go_through_wizard(wd_client, cookie)
    assert preview.status_code == 200
    assert "What happens after you publish?" in preview.text


@pytest.mark.asyncio
async def test_preview_page_has_quarantine_notice(wd_client):
    """Preview page should show the quarantine notice."""
    cookie = await _register_company(wd_client)
    _draft_id, preview = await _go_through_wizard(wd_client, cookie)
    assert preview.status_code == 200
    assert "quarantine" in preview.text.lower()


# ===========================================================================
# Step 5: Success page helper content (template-level check)
# ===========================================================================


def test_success_template_has_what_to_do_now():
    """Success page template should contain the 'What to do now' checklist."""
    from pathlib import Path
    template = Path(__file__).resolve().parent.parent / "agentcafe" / "templates" / "wizard" / "onboard_success.html"
    content = template.read_text()
    assert "What to do now" in content
    assert "Test with an agent" in content
    assert "Monitor your dashboard" in content
    assert "/mcp" in content


def test_success_template_has_menu_link():
    """Success page template should link to the public menu."""
    from pathlib import Path
    template = Path(__file__).resolve().parent.parent / "agentcafe" / "templates" / "wizard" / "onboard_success.html"
    content = template.read_text()
    assert "View on Menu" in content
