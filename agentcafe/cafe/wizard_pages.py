"""Server-rendered company wizard pages — login, register, onboard, services, admin.

Company-facing Jinja2 pages. These wrap the existing wizard API logic and use
session cookies (httponly) for authentication. Separate from the JSON
API endpoints which use Authorization headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

import bcrypt
import httpx
import jwt
from fastapi import APIRouter, Request, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agentcafe.crypto import decrypt
from agentcafe.db.engine import get_db
from agentcafe.wizard.ai_enricher import enrich_spec
from agentcafe.wizard.publisher import publish_draft
from agentcafe.wizard.models import PolicyAction
from agentcafe.wizard.review_engine import (
    create_draft,
    generate_preview,
    get_draft,
    save_integration,
    save_policy,
    save_review,
)
from agentcafe.wizard.spec_parser import SpecParseError, parse_openapi_spec

logger = logging.getLogger("agentcafe.wizard_pages")

wizard_pages_router = APIRouter(tags=["wizard-pages"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_SAMPLE_SPEC_PATH = Path(__file__).resolve().parent.parent.parent / "examples" / "sample-spec.yaml"


def _load_sample_spec() -> str:
    """Load the sample spec file for the 'Try a sample' button."""
    try:
        return _SAMPLE_SPEC_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

_COOKIE_NAME = "company_session"
_COOKIE_MAX_AGE = 8 * 60 * 60  # 8 hours (matches wizard JWT expiry)
_SECURE_COOKIES = os.getenv("CAFE_SECURE_COOKIES", "").lower() not in ("", "0", "false")


class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""
    quarantine_days: int = 7
    issuer_api_key: str = ""

_state = _State()


def configure_wizard_pages(
    signing_secret: str,
    *,
    quarantine_days: int = 7,
    issuer_api_key: str = "",
) -> None:
    """Set the signing secret and config. Called once at startup."""
    _state.signing_secret = signing_secret
    _state.quarantine_days = quarantine_days
    _state.issuer_api_key = issuer_api_key


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _create_company_session(company_id: str) -> str:
    """Create a JWT session token for a company (matches wizard router)."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "agentcafe-wizard",
        "sub": company_id,
        "exp": now + timedelta(hours=8),
        "iat": now,
    }
    return jwt.encode(payload, _state.signing_secret, algorithm="HS256")


def _get_company_session(request: Request) -> dict | None:
    """Extract and validate the company session cookie. Returns payload or None."""
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            _state.signing_secret,
            algorithms=["HS256"],
            issuer="agentcafe-wizard",
        )
    except jwt.PyJWTError:
        return None


def _get_company_id(request: Request) -> str | None:
    """Get company_id from session cookie, or None."""
    session = _get_company_session(request)
    if session is None:
        return None
    return session.get("sub")


def _set_company_cookie(response, token: str) -> None:
    """Set the company session cookie on a response."""
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_SECURE_COOKIES,
    )


# ---------------------------------------------------------------------------
# CSRF helpers (same pattern as pages.py)
# ---------------------------------------------------------------------------

_CSRF_TOKEN_MAX_AGE = 3600  # 1 hour


def _generate_csrf_token(request: Request) -> str:
    """Generate a CSRF token tied to the current session."""
    session_cookie = request.cookies.get(_COOKIE_NAME, "")
    nonce = secrets.token_hex(16)
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    msg = f"{nonce}.{ts}.{session_cookie}"
    sig = hmac.new(
        _state.signing_secret.encode(), msg.encode(), hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{ts}.{sig}"


def _validate_csrf_token(request: Request, token: str | None) -> bool:
    """Validate a CSRF token against the current session."""
    if not token or token.count(".") != 2:
        return False
    nonce, ts, sig = token.split(".", 2)
    try:
        token_time = int(ts)
    except ValueError:
        return False
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - token_time) > _CSRF_TOKEN_MAX_AGE:
        return False
    session_cookie = request.cookies.get(_COOKIE_NAME, "")
    msg = f"{nonce}.{ts}.{session_cookie}"
    expected = hmac.new(
        _state.signing_secret.encode(), msg.encode(), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _require_auth(request: Request):
    """Return company_id or redirect response if not authenticated."""
    company_id = _get_company_id(request)
    if company_id is None:
        return None, RedirectResponse(url="/login", status_code=303)
    return company_id, None


async def _company_landing_url(company_id: str) -> str:
    """Return /services if the company has published services, else /services/onboard."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM published_services WHERE company_id = ? AND status != 'unpublished'",
        (company_id,),
    )
    row = await cursor.fetchone()
    if row and row["cnt"] > 0:
        return "/services"
    return "/services/onboard"


async def _get_company_name(company_id: str) -> str:
    """Look up company name from DB."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT name FROM companies WHERE id = ?", (company_id,),
    )
    row = await cursor.fetchone()
    return row["name"] if row else "Company"


def _has_human_session(request: Request) -> bool:
    """Check if a valid human session cookie is present."""
    from agentcafe.cafe.human import validate_human_session  # avoid circular
    token = request.cookies.get("cafe_session")
    if not token:
        return False
    try:
        validate_human_session(token)
        return True
    except (ValueError, KeyError):
        return False


def _build_nav_context(request: Request) -> dict:
    """Build navigation context for templates, checking both session types."""
    return {
        "has_human_session": _has_human_session(request),
        "has_company_session": _get_company_id(request) is not None,
        "active_nav": "services",
    }


# ---------------------------------------------------------------------------
# Company login / register
# ---------------------------------------------------------------------------

@wizard_pages_router.get("/services/login")
async def company_login_page(request: Request):  # pylint: disable=unused-argument
    """Redirect to unified login page."""
    return RedirectResponse(url="/login", status_code=303)


@wizard_pages_router.post("/services/login")
async def company_login_submit(request: Request):  # pylint: disable=unused-argument
    """Redirect to unified login page."""
    return RedirectResponse(url="/login", status_code=303)


@wizard_pages_router.get("/services/register", response_class=HTMLResponse)
async def company_register_page(request: Request):
    """Render the company registration page."""
    company_id = _get_company_id(request)
    if company_id:
        return RedirectResponse(url=await _company_landing_url(company_id), status_code=303)
    return templates.TemplateResponse(request, "wizard/register.html", {
        "csrf_token": _generate_csrf_token(request),
        "error": None,
    })


@wizard_pages_router.post("/services/register", response_class=HTMLResponse)
async def company_register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    website: str = Form(""),
    csrf_token: str = Form(""),
):
    """Handle company registration form submission."""
    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse(request, "wizard/register.html", {
            "csrf_token": _generate_csrf_token(request),
            "error": "Invalid or expired form. Please try again.",
        }, status_code=403)

    if len(password) < 8:
        return templates.TemplateResponse(request, "wizard/register.html", {
            "csrf_token": _generate_csrf_token(request),
            "error": "Password must be at least 8 characters.",
        }, status_code=400)

    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM companies WHERE email = ?", (email,),
    )
    if await cursor.fetchone():
        return templates.TemplateResponse(request, "wizard/register.html", {
            "csrf_token": _generate_csrf_token(request),
            "error": "An account with this email already exists.",
        }, status_code=409)

    import uuid  # pylint: disable=import-outside-toplevel
    company_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

    await db.execute(
        """INSERT INTO companies (id, name, email, password_hash, website, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, name, email, password_hash, website or None, now, now),
    )
    await db.commit()

    token = _create_company_session(company_id)
    response = RedirectResponse(url="/services/onboard", status_code=303)
    _set_company_cookie(response, token)
    logger.info("Created company account: %s (%s)", name, email)
    return response


@wizard_pages_router.get("/services/logout")
async def company_logout(request: Request):  # pylint: disable=unused-argument
    """Clear both session cookies and redirect to login."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=_COOKIE_NAME)
    response.delete_cookie(key="cafe_session")
    return response


# ---------------------------------------------------------------------------
# Onboard wizard — Step 1: Spec input
# ---------------------------------------------------------------------------

@wizard_pages_router.get("/services/onboard", response_class=HTMLResponse)
async def onboard_spec_page(request: Request):
    """Render the spec input page (Step 1)."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect
    company_name = await _get_company_name(company_id)
    return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
        "csrf_token": _generate_csrf_token(request),
        "company_name": company_name,
        **_build_nav_context(request),
        "error": None,
        "step": 1,
        "raw_spec": "",
        "sample_spec": _load_sample_spec(),
    })


@wizard_pages_router.get("/services/onboard/{draft_id}", response_class=HTMLResponse)
async def onboard_spec_page_with_draft(request: Request, draft_id: str):
    """Render the spec input page pre-populated with a draft's raw spec (Back from review)."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect
    db = await get_db()
    draft = await get_draft(db, draft_id)
    raw_spec = ""
    if draft and draft["company_id"] == company_id:
        raw_spec = draft["raw_spec_text"] or ""
    company_name = await _get_company_name(company_id)
    return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
        "csrf_token": _generate_csrf_token(request),
        "company_name": company_name,
        **_build_nav_context(request),
        "error": None,
        "step": 1,
        "raw_spec": raw_spec,
        "sample_spec": _load_sample_spec(),
    })


@wizard_pages_router.post("/services/onboard", response_class=HTMLResponse)
async def onboard_spec_submit(
    request: Request,
    csrf_token: str = Form(""),
    raw_spec: str = Form(""),
    spec_url: str = Form(""),
    spec_file: UploadFile | None = None,
):
    """Parse spec (paste, URL, or file upload) and redirect to review."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    company_name = await _get_company_name(company_id)

    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "error": "Invalid or expired form. Please try again.",
            "step": 1,
            "raw_spec": raw_spec,
            "sample_spec": _load_sample_spec(),
        }, status_code=403)

    db = await get_db()

    # Determine spec source
    spec_text = None
    if spec_file and spec_file.filename:
        content = await spec_file.read()
        if len(content) > 2 * 1024 * 1024:
            return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
                "csrf_token": _generate_csrf_token(request),
                "company_name": company_name,
        **_build_nav_context(request),
                "error": "Spec file must be under 2 MB.",
                "step": 1,
                "raw_spec": raw_spec,
                "sample_spec": _load_sample_spec(),
            }, status_code=413)
        try:
            spec_text = content.decode("utf-8")
        except UnicodeDecodeError:
            return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
                "csrf_token": _generate_csrf_token(request),
                "company_name": company_name,
        **_build_nav_context(request),
                "error": "File must be UTF-8 encoded text.",
                "step": 1,
                "raw_spec": raw_spec,
                "sample_spec": _load_sample_spec(),
            }, status_code=422)
    elif spec_url.strip():
        if not spec_url.startswith(("http://", "https://")):
            return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
                "csrf_token": _generate_csrf_token(request),
                "company_name": company_name,
        **_build_nav_context(request),
                "error": "URL must start with http:// or https://.",
                "step": 1,
                "raw_spec": raw_spec,
                "sample_spec": _load_sample_spec(),
            }, status_code=422)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(spec_url, follow_redirects=True)
                resp.raise_for_status()
                spec_text = resp.text
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
                "csrf_token": _generate_csrf_token(request),
                "company_name": company_name,
        **_build_nav_context(request),
                "error": f"Could not fetch URL: {exc}",
                "step": 1,
                "raw_spec": raw_spec,
                "sample_spec": _load_sample_spec(),
            }, status_code=422)
        if len(spec_text) > 2 * 1024 * 1024:
            return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
                "csrf_token": _generate_csrf_token(request),
                "company_name": company_name,
        **_build_nav_context(request),
                "error": "Fetched spec must be under 2 MB.",
                "step": 1,
                "raw_spec": raw_spec,
                "sample_spec": _load_sample_spec(),
            }, status_code=413)
    elif raw_spec.strip():
        spec_text = raw_spec
    else:
        return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "error": "Please paste a spec, upload a file, or provide a URL.",
            "step": 1,
            "raw_spec": raw_spec,
            "sample_spec": _load_sample_spec(),
        }, status_code=400)

    # Parse
    try:
        parsed_spec = parse_openapi_spec(spec_text)
    except SpecParseError as exc:
        return templates.TemplateResponse(request, "wizard/onboard_spec.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "error": f"Spec parse error: {exc.message}",
            "step": 1,
            "raw_spec": raw_spec,
            "sample_spec": _load_sample_spec(),
        }, status_code=422)

    # Enrich and create draft
    candidate_menu = await enrich_spec(parsed_spec)
    draft_id = await create_draft(db, company_id, parsed_spec, candidate_menu, spec_text)

    return RedirectResponse(
        url=f"/services/onboard/{draft_id}/review", status_code=303,
    )


# ---------------------------------------------------------------------------
# Onboard wizard — Step 2: Review
# ---------------------------------------------------------------------------

@wizard_pages_router.get(
    "/services/onboard/{draft_id}/review", response_class=HTMLResponse,
)
async def onboard_review_page(request: Request, draft_id: str):
    """Render the review page (Step 2)."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    candidate = json.loads(draft.get("candidate_menu_json") or "{}")
    company_edits = json.loads(draft.get("company_edits_json") or "{}")
    if company_edits:
        candidate = {**candidate, **company_edits}
    excluded_list = json.loads(draft.get("excluded_actions") or "[]")
    company_name = await _get_company_name(company_id)

    return templates.TemplateResponse(request, "wizard/onboard_review.html", {
        "csrf_token": _generate_csrf_token(request),
        "company_name": company_name,
        **_build_nav_context(request),
        "draft_id": draft_id,
        "candidate": candidate,
        "excluded_list": excluded_list,
        "step": 2,
        "error": None,
    })


@wizard_pages_router.post(
    "/services/onboard/{draft_id}/review", response_class=HTMLResponse,
)
async def onboard_review_submit(
    request: Request,
    draft_id: str,
    csrf_token: str = Form(""),
    service_id: str = Form(""),
    name: str = Form(""),
    category: str = Form(""),
    capability_tags: str = Form(""),
    description: str = Form(""),
    actions_json: str = Form("[]"),
    excluded_actions: str = Form(""),
):
    """Save review edits and redirect to policy step."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    company_name = await _get_company_name(company_id)
    candidate = json.loads(draft.get("candidate_menu_json") or "{}")

    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse(request, "wizard/onboard_review.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "candidate": candidate,
            "step": 2,
            "error": "Invalid or expired form. Please try again.",
        }, status_code=403)

    tags = [t.strip() for t in capability_tags.split(",") if t.strip()]
    excluded = [e.strip() for e in excluded_actions.split(",") if e.strip()]

    try:
        actions = json.loads(actions_json)
    except json.JSONDecodeError:
        actions = candidate.get("actions", [])

    await save_review(
        db,
        draft_id,
        service_id=service_id,
        name=name,
        category=category,
        capability_tags=tags,
        description=description,
        actions=actions,
        excluded_actions=excluded,
    )

    return RedirectResponse(
        url=f"/services/onboard/{draft_id}/policy", status_code=303,
    )


# ---------------------------------------------------------------------------
# Onboard wizard — Step 3: Policy
# ---------------------------------------------------------------------------

@wizard_pages_router.get(
    "/services/onboard/{draft_id}/policy", response_class=HTMLResponse,
)
async def onboard_policy_page(request: Request, draft_id: str):
    """Render the policy configuration page (Step 3)."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    candidate = json.loads(draft.get("candidate_menu_json") or "{}")
    company_edits = json.loads(draft.get("company_edits_json") or "{}")
    excluded = json.loads(draft.get("excluded_actions") or "[]")
    saved_policy = json.loads(draft.get("policy_json") or "{}")
    saved_backend_url = draft.get("backend_url") or ""
    saved_auth_header = draft.get("backend_auth_header") or ""
    # Merge: company edits override candidate fields
    merged = {**candidate, **company_edits}
    # Filter out excluded actions
    if excluded:
        merged["actions"] = [
            a for a in merged.get("actions", [])
            if a.get("action_id") not in excluded
        ]
    company_name = await _get_company_name(company_id)
    saved_integration_mode = draft.get("integration_mode") or "standard"

    return templates.TemplateResponse(request, "wizard/onboard_policy.html", {
        "csrf_token": _generate_csrf_token(request),
        "company_name": company_name,
        **_build_nav_context(request),
        "draft_id": draft_id,
        "candidate": merged,
        "saved_policy": saved_policy,
        "saved_backend_url": saved_backend_url,
        "has_saved_auth": bool(saved_auth_header),
        "saved_integration_mode": saved_integration_mode,
        "step": 3,
        "error": None,
    })


@wizard_pages_router.post(
    "/services/onboard/{draft_id}/policy", response_class=HTMLResponse,
)
async def onboard_policy_submit(
    request: Request,
    draft_id: str,
    csrf_token: str = Form(""),
    backend_url: str = Form(""),
    backend_auth_header: str = Form(""),
    policy_json: str = Form("{}"),
    integration_mode: str = Form("standard"),
):
    """Save policy and redirect to preview step."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    company_name = await _get_company_name(company_id)
    candidate = json.loads(draft.get("candidate_menu_json") or "{}")

    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse(request, "wizard/onboard_policy.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "candidate": candidate,
            "step": 3,
            "error": "Invalid or expired form. Please try again.",
        }, status_code=403)

    if not backend_url.strip():
        return templates.TemplateResponse(request, "wizard/onboard_policy.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "candidate": candidate,
            "step": 3,
            "error": "Backend URL is required.",
        }, status_code=400)

    try:
        raw_policy = json.loads(policy_json)
    except json.JSONDecodeError:
        raw_policy = {}

    actions_policy = {
        action_id: PolicyAction(**p) if isinstance(p, dict) else p
        for action_id, p in raw_policy.items()
    }

    # If auth header is blank and one was already saved, keep the existing one
    effective_auth = backend_auth_header
    if not effective_auth and draft.get("backend_auth_header"):
        effective_auth = decrypt(draft["backend_auth_header"])

    await save_policy(
        db,
        draft_id,
        actions_policy=actions_policy,
        backend_url=backend_url,
        backend_auth_header=effective_auth,
        integration_mode=integration_mode,
    )

    if integration_mode == "jointly_verified":
        return RedirectResponse(
            url=f"/services/onboard/{draft_id}/integration", status_code=303,
        )
    return RedirectResponse(
        url=f"/services/onboard/{draft_id}/preview", status_code=303,
    )


# ---------------------------------------------------------------------------
# Onboard wizard — Step 3b: Integration Setup (JV only)
# ---------------------------------------------------------------------------

@wizard_pages_router.get(
    "/services/onboard/{draft_id}/integration", response_class=HTMLResponse,
)
async def onboard_integration_page(request: Request, draft_id: str):
    """Render the JV integration setup page (Step 3b)."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    if (draft.get("integration_mode") or "standard") != "jointly_verified":
        return RedirectResponse(
            url=f"/services/onboard/{draft_id}/preview", status_code=303,
        )

    saved_config = json.loads(draft.get("integration_config_json") or "{}")
    company_name = await _get_company_name(company_id)

    return templates.TemplateResponse(request, "wizard/onboard_integration.html", {
        "csrf_token": _generate_csrf_token(request),
        "company_name": company_name,
        **_build_nav_context(request),
        "draft_id": draft_id,
        "saved_config": saved_config,
        "integration_mode": "jointly_verified",
        "step": "3b",
        "error": None,
    })


@wizard_pages_router.post(
    "/services/onboard/{draft_id}/integration", response_class=HTMLResponse,
)
async def onboard_integration_submit(
    request: Request,
    draft_id: str,
    csrf_token: str = Form(""),
    integration_base_url: str = Form(""),
    integration_auth_header: str = Form(""),
    identity_matching: str = Form("opaque_id"),
    has_direct_signup: str = Form("false"),
    cap_account_check: str = Form(""),
    cap_account_create: str = Form(""),
    cap_link_complete: str = Form(""),
    cap_unlink: str = Form(""),
    cap_revoke: str = Form("on"),
    cap_grant_status: str = Form(""),
):
    """Save JV integration config and redirect to preview."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    company_name = await _get_company_name(company_id)

    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse(request, "wizard/onboard_integration.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "saved_config": {},
            "integration_mode": "jointly_verified",
            "step": "3b",
            "error": "Invalid or expired form. Please try again.",
        }, status_code=403)

    if not integration_base_url.strip():
        return templates.TemplateResponse(request, "wizard/onboard_integration.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "saved_config": {},
            "integration_mode": "jointly_verified",
            "step": "3b",
            "error": "Integration base URL is required for jointly-verified services.",
        }, status_code=400)

    config = {
        "integration_base_url": integration_base_url.strip(),
        "integration_auth_header": integration_auth_header,
        "identity_matching": identity_matching,
        "has_direct_signup": has_direct_signup == "true",
        "cap_account_check": bool(cap_account_check),
        "cap_account_create": bool(cap_account_create),
        "cap_link_complete": bool(cap_link_complete),
        "cap_unlink": bool(cap_unlink),
        "cap_revoke": bool(cap_revoke),
        "cap_grant_status": bool(cap_grant_status),
        "path_revoke": None,
    }

    if not any([config["cap_account_check"], config["cap_account_create"], config["cap_link_complete"]]):
        return templates.TemplateResponse(request, "wizard/onboard_integration.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "saved_config": config,
            "integration_mode": "jointly_verified",
            "step": "3b",
            "error": "At least one of account-check, account-create, or link-complete must be enabled.",
        }, status_code=422)

    await save_integration(
        db,
        draft_id,
        integration_mode="jointly_verified",
        integration_config=config,
    )

    return RedirectResponse(
        url=f"/services/onboard/{draft_id}/preview", status_code=303,
    )


# ---------------------------------------------------------------------------
# Onboard wizard — Step 4: Preview & Publish
# ---------------------------------------------------------------------------

@wizard_pages_router.get(
    "/services/onboard/{draft_id}/preview", response_class=HTMLResponse,
)
async def onboard_preview_page(request: Request, draft_id: str):
    """Render the preview page (Step 4)."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    company_name = await _get_company_name(company_id)

    try:
        preview_obj = await generate_preview(db, draft_id)
        preview = preview_obj.model_dump() if hasattr(preview_obj, 'model_dump') else preview_obj
    except ValueError as exc:
        return templates.TemplateResponse(request, "wizard/onboard_policy.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "candidate": json.loads(draft.get("candidate_menu_json") or "{}"),
            "step": 3,
            "error": str(exc),
        }, status_code=400)

    integration_mode = draft.get("integration_mode") or "standard"

    return templates.TemplateResponse(request, "wizard/onboard_preview.html", {
        "csrf_token": _generate_csrf_token(request),
        "company_name": company_name,
        **_build_nav_context(request),
        "draft_id": draft_id,
        "preview": preview,
        "quarantine_days": _state.quarantine_days,
        "integration_mode": integration_mode,
        "step": 4,
        "error": None,
    })


@wizard_pages_router.post(
    "/services/onboard/{draft_id}/publish", response_class=HTMLResponse,
)
async def onboard_publish_submit(
    request: Request,
    draft_id: str,
    csrf_token: str = Form(""),
):
    """Publish the draft and redirect to success page."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(
            url=f"/services/onboard/{draft_id}/preview", status_code=303,
        )

    try:
        await publish_draft(db, draft_id, _state.quarantine_days)
    except ValueError as exc:
        company_name = await _get_company_name(company_id)
        preview_obj = await generate_preview(db, draft_id)
        preview = preview_obj.model_dump() if hasattr(preview_obj, 'model_dump') else preview_obj
        return templates.TemplateResponse(request, "wizard/onboard_preview.html", {
            "csrf_token": _generate_csrf_token(request),
            "company_name": company_name,
        **_build_nav_context(request),
            "draft_id": draft_id,
            "preview": preview,
            "quarantine_days": _state.quarantine_days,
            "step": 4,
            "error": str(exc),
        }, status_code=400)

    return RedirectResponse(
        url=f"/services/onboard/{draft_id}/success", status_code=303,
    )


@wizard_pages_router.get(
    "/services/onboard/{draft_id}/success", response_class=HTMLResponse,
)
async def onboard_success_page(request: Request, draft_id: str):
    """Render the post-publish success page."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    draft = await get_draft(db, draft_id)
    if draft is None or draft["company_id"] != company_id:
        return RedirectResponse(url="/services/onboard", status_code=303)

    # Get published service info
    final_menu = json.loads(draft.get("final_menu_json") or "{}")
    company_name = await _get_company_name(company_id)

    return templates.TemplateResponse(request, "wizard/onboard_success.html", {
        "company_name": company_name,
        **_build_nav_context(request),
        "service_name": final_menu.get("name", ""),
        "service_id": final_menu.get("service_id", ""),
        "actions_count": len(final_menu.get("actions", [])),
        "quarantine_days": _state.quarantine_days,
    })


# ---------------------------------------------------------------------------
# Services management
# ---------------------------------------------------------------------------

@wizard_pages_router.get("/services", response_class=HTMLResponse)
async def services_list_page(request: Request):
    """Render the services management page."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    db = await get_db()
    company_name = await _get_company_name(company_id)

    cursor = await db.execute(
        "SELECT * FROM published_services WHERE company_id = ? ORDER BY published_at DESC",
        (company_id,),
    )
    rows = await cursor.fetchall()

    services = []
    for row in rows:
        service_id = row["service_id"]
        menu_data = json.loads(row["menu_entry_json"])
        actions_count = len(menu_data.get("actions", []))

        # Count requests
        c = await db.execute(
            "SELECT COUNT(*) as cnt FROM audit_log WHERE service_id = ?",
            (service_id,),
        )
        total_row = await c.fetchone()
        total_requests = total_row["cnt"] if total_row else 0

        # Recent requests (24h)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        c = await db.execute(
            "SELECT COUNT(*) as cnt FROM audit_log WHERE service_id = ? AND timestamp > ?",
            (service_id, cutoff),
        )
        recent_row = await c.fetchone()
        recent_requests = recent_row["cnt"] if recent_row else 0

        services.append({
            "service_id": service_id,
            "name": menu_data.get("name", service_id),
            "description": menu_data.get("description", ""),
            "status": row["status"],
            "published_at": row["published_at"],
            "actions_count": actions_count,
            "total_requests": total_requests,
            "recent_requests": recent_requests,
        })

    return templates.TemplateResponse(request, "wizard/services.html", {
        "csrf_token": _generate_csrf_token(request),
        "company_name": company_name,
        **_build_nav_context(request),
        "services": services,
        "error": None,
    })


@wizard_pages_router.post(
    "/services/{service_id}/action", response_class=HTMLResponse,
)
async def service_action(
    request: Request,
    service_id: str,
    action: str = Form(...),
    csrf_token: str = Form(""),
):
    """Handle pause/resume/unpublish actions on a service."""
    company_id, redirect = _require_auth(request)
    if redirect:
        return redirect

    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/services", status_code=303)

    db = await get_db()

    # Verify ownership
    cursor = await db.execute(
        "SELECT * FROM published_services WHERE service_id = ? AND company_id = ?",
        (service_id, company_id),
    )
    svc = await cursor.fetchone()
    if not svc:
        return RedirectResponse(url="/services", status_code=303)

    now = datetime.now(timezone.utc).isoformat()

    if action == "pause" and svc["status"] == "live":
        await db.execute(
            "UPDATE published_services SET status = 'paused', updated_at = ? WHERE service_id = ?",
            (now, service_id),
        )
        # Suspend proxy configs
        await db.execute(
            "UPDATE proxy_configs SET suspended_at = ? WHERE service_id = ?",
            (now, service_id),
        )
        await db.commit()
    elif action == "resume" and svc["status"] == "paused":
        await db.execute(
            "UPDATE published_services SET status = 'live', updated_at = ? WHERE service_id = ?",
            (now, service_id),
        )
        await db.execute(
            "UPDATE proxy_configs SET suspended_at = NULL WHERE service_id = ?",
            (service_id,),
        )
        await db.commit()
    elif action == "unpublish" and svc["status"] != "unpublished":
        await db.execute(
            "UPDATE published_services SET status = 'unpublished', updated_at = ? WHERE service_id = ?",
            (now, service_id),
        )
        await db.execute(
            "DELETE FROM proxy_configs WHERE service_id = ?",
            (service_id,),
        )
        try:
            await db.execute(
                "DELETE FROM menu_entries WHERE service_id = ?",
                (service_id,),
            )
        except Exception:  # pylint: disable=broad-except
            pass
        await db.commit()

    return RedirectResponse(url="/services", status_code=303)


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

@wizard_pages_router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Render the platform admin dashboard."""
    admin_key = request.query_params.get("key", "")
    session_key = request.cookies.get("admin_key", "")
    active_key = admin_key or session_key

    if not active_key or active_key != _state.issuer_api_key:
        return templates.TemplateResponse(request, "wizard/admin.html", {
            "authenticated": False,
            "error": "Invalid admin key." if active_key else None,
            "data": None,
        })

    db = await get_db()

    # Load services from both menu_entries (demo/seeded) and published_services (wizard)
    services = []
    try:
        cursor = await db.execute("SELECT * FROM menu_entries")
        for row in await cursor.fetchall():
            menu_data = json.loads(row["menu_json"])
            menu_data["_source"] = "seeded"
            services.append(menu_data)
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        cursor = await db.execute(
            "SELECT * FROM published_services ORDER BY published_at DESC",
        )
        for row in await cursor.fetchall():
            menu_data = json.loads(row["menu_entry_json"])
            menu_data["_source"] = "wizard"
            menu_data["_status"] = row["status"]
            services.append(menu_data)
    except Exception:  # pylint: disable=broad-except
        pass

    # Stats
    c = await db.execute("SELECT COUNT(*) as cnt FROM audit_log")
    total_row = await c.fetchone()
    total_requests = total_row["cnt"] if total_row else 0

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    c = await db.execute(
        "SELECT COUNT(*) as cnt FROM audit_log WHERE timestamp > ?", (cutoff,),
    )
    recent_row = await c.fetchone()
    recent_requests = recent_row["cnt"] if recent_row else 0

    # Active policies & cards
    now_iso = datetime.now(timezone.utc).isoformat()
    active_policies_count = 0
    active_cards_count = 0
    try:
        c = await db.execute(
            "SELECT COUNT(*) as cnt FROM policies WHERE revoked_at IS NULL AND expires_at > ?",
            (now_iso,),
        )
        row = await c.fetchone()
        active_policies_count = row["cnt"] if row else 0
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        c = await db.execute(
            "SELECT COUNT(*) as cnt FROM company_cards WHERE status = 'active' AND expires_at > ?",
            (now_iso,),
        )
        row = await c.fetchone()
        active_cards_count = row["cnt"] if row else 0
    except Exception:  # pylint: disable=broad-except
        pass

    # Consent stats (24h)
    consent_pending = 0
    consent_approved_24h = 0
    consent_declined_24h = 0
    try:
        c = await db.execute("SELECT COUNT(*) as cnt FROM company_cards WHERE status = 'pending'")
        row = await c.fetchone()
        consent_pending = row["cnt"] if row else 0
        c = await db.execute(
            "SELECT COUNT(*) as cnt FROM company_cards WHERE status = 'active' AND updated_at > ?",
            (cutoff,),
        )
        row = await c.fetchone()
        consent_approved_24h = row["cnt"] if row else 0
        c = await db.execute(
            "SELECT COUNT(*) as cnt FROM company_cards WHERE status = 'declined' AND updated_at > ?",
            (cutoff,),
        )
        row = await c.fetchone()
        consent_declined_24h = row["cnt"] if row else 0
    except Exception:  # pylint: disable=broad-except
        pass

    # Latency percentiles (from last 1000 requests)
    p50_latency = None
    p95_latency = None
    try:
        c = await db.execute(
            "SELECT latency_ms FROM audit_log WHERE latency_ms IS NOT NULL ORDER BY timestamp DESC LIMIT 1000",
        )
        latencies = sorted([r["latency_ms"] for r in await c.fetchall()])
        if latencies:
            p50_latency = latencies[len(latencies) // 2]
            p95_latency = latencies[int(len(latencies) * 0.95)]
    except Exception:  # pylint: disable=broad-except
        pass

    # Paginated audit log
    page = 1
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        pass
    page_size = 20
    offset = (page - 1) * page_size

    c = await db.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    )
    audit_rows = await c.fetchall()
    audit_entries = [dict(row) for row in audit_rows]
    has_next_page = len(audit_entries) == page_size

    response = templates.TemplateResponse(request, "wizard/admin.html", {
        "authenticated": True,
        "error": None,
        "services": services,
        "total_requests": total_requests,
        "recent_requests": recent_requests,
        "active_policies_count": active_policies_count,
        "active_cards_count": active_cards_count,
        "consent_pending": consent_pending,
        "consent_approved_24h": consent_approved_24h,
        "consent_declined_24h": consent_declined_24h,
        "p50_latency": p50_latency,
        "p95_latency": p95_latency,
        "audit_entries": audit_entries,
        "audit_page": page,
        "audit_has_next": has_next_page,
    })

    if admin_key:
        response.set_cookie(
            key="admin_key", value=admin_key,
            max_age=3600, httponly=True, samesite="lax", secure=_SECURE_COOKIES,
        )

    return response
