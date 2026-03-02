"""Server-rendered pages — login, register, consent approval.

Human-facing Jinja2 pages. These wrap the existing API logic and use
session cookies (httponly) for authentication. Separate from the JSON
API endpoints which use Authorization headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agentcafe.cafe.consent import _fire_consent_callback
from agentcafe.cafe.human import (
    _hash_password, _verify_password, _rehash_if_legacy,
    _create_human_session_token, validate_human_session,
)
from agentcafe.db.engine import get_db

logger = logging.getLogger("agentcafe.pages")

pages_router = APIRouter(tags=["pages"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_COOKIE_NAME = "cafe_session"
_COOKIE_MAX_AGE = 24 * 60 * 60  # 24 hours

# Risk-tier ceilings (must match consent.py)
_RISK_TIER_CEILINGS = {"low": 3600, "medium": 900, "high": 300, "critical": 0}


class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""

_state = _State()


def configure_pages(signing_secret: str) -> None:
    """Set the signing secret. Called once at startup."""
    _state.signing_secret = signing_secret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_session(request: Request) -> dict | None:
    """Extract and validate the session cookie. Returns payload or None."""
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    try:
        return validate_human_session(token)
    except HTTPException:
        return None


def _set_session_cookie(response, token: str) -> None:
    """Set the session cookie on a response."""
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


_CSRF_TOKEN_MAX_AGE = 3600  # 1 hour


def _generate_csrf_token(request: Request) -> str:
    """Generate a CSRF token tied to the current session.

    Format: nonce.timestamp.signature
    Signature = HMAC-SHA256(signing_secret, nonce + timestamp + session_cookie)
    """
    session_cookie = request.cookies.get(_COOKIE_NAME, "")
    nonce = secrets.token_hex(16)
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    msg = f"{nonce}.{ts}.{session_cookie}"
    sig = hmac.new(_state.signing_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{ts}.{sig}"


def _validate_csrf_token(request: Request, token: str | None) -> bool:
    """Validate a CSRF token against the current session."""
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    nonce, ts, sig = parts
    # Check timestamp freshness
    try:
        token_time = int(ts)
    except ValueError:
        return False
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - token_time) > _CSRF_TOKEN_MAX_AGE:
        return False
    # Recompute signature
    session_cookie = request.cookies.get(_COOKIE_NAME, "")
    msg = f"{nonce}.{ts}.{session_cookie}"
    expected = hmac.new(_state.signing_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _lifetime_options(risk_tier: str) -> list[dict]:
    """Build duration selector options respecting the risk-tier ceiling."""
    ceiling = _RISK_TIER_CEILINGS.get(risk_tier, 900)
    all_options = [
        {"seconds": 300, "label": "5 minutes"},
        {"seconds": 600, "label": "10 minutes"},
        {"seconds": 900, "label": "15 minutes"},
        {"seconds": 1800, "label": "30 minutes"},
        {"seconds": 3600, "label": "1 hour"},
    ]
    if ceiling == 0:
        return [{"seconds": 0, "label": "Single use", "default": True}]

    options = [o for o in all_options if o["seconds"] <= ceiling]
    if not options:
        options = [{"seconds": ceiling, "label": f"{ceiling}s"}]

    # Default to the largest allowed
    for opt in options:
        opt["default"] = False
    options[-1]["default"] = True
    return options


def _ceiling_label(risk_tier: str) -> str:
    """Human-readable label for the ceiling."""
    ceiling = _RISK_TIER_CEILINGS.get(risk_tier, 900)
    if ceiling == 0:
        return "single use"
    if ceiling >= 3600:
        return f"{ceiling // 3600} hour{'s' if ceiling >= 7200 else ''}"
    return f"{ceiling // 60} minutes"


def _consent_text(service_name: str, actions: list[dict]) -> str:
    """Generate Cafe-authored plain-language consent text."""
    count = len(actions)
    if count == 1:
        return (
            f"An agent is requesting permission to perform an action "
            f"on {service_name} on your behalf."
        )
    return (
        f"An agent is requesting permission to perform {count} actions "
        f"on {service_name} on your behalf."
    )


# ---------------------------------------------------------------------------
# GET / — root redirect
# ---------------------------------------------------------------------------

@pages_router.get("/", response_class=HTMLResponse)
async def root_page(request: Request):
    """Redirect root to dashboard if logged in, else to login."""
    session = _get_session(request)
    if session:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@pages_router.get("/logout", response_class=HTMLResponse)
async def logout_page(request: Request):  # noqa: ARG001  pylint: disable=unused-argument
    """Clear the session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(_COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# GET /dashboard — human policy dashboard
# ---------------------------------------------------------------------------

def _human_date(iso: str | None) -> str:
    """Format an ISO timestamp as a short human-readable string."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return iso


def _lifetime_label(seconds: int) -> str:
    """Human-readable label for a token lifetime in seconds."""
    if seconds == 0:
        return "single-use"
    if seconds >= 3600:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''}"
    return f"{seconds // 60} min"


@pages_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, revoked: str = ""):
    """Render the human policy dashboard."""
    session = _get_session(request)
    if not session:
        return RedirectResponse(url="/login?next=/dashboard", status_code=303)

    user_id = session["user_id"]
    db = await get_db()

    # Fetch all policies for this user
    cursor = await db.execute(
        """SELECT p.id, p.service_id, p.allowed_action_ids, p.scopes,
                  p.risk_tier, p.max_token_lifetime_seconds,
                  p.expires_at, p.revoked_at, p.created_at
           FROM policies p
           WHERE p.cafe_user_id = ?
           ORDER BY p.created_at DESC""",
        (user_id,),
    )
    rows = await cursor.fetchall()

    # Count active tokens per policy
    token_counts: dict[str, int] = {}
    if rows:
        policy_ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(policy_ids))
        tc_cursor = await db.execute(
            f"SELECT policy_id, COUNT(*) AS cnt FROM active_tokens "
            f"WHERE policy_id IN ({placeholders}) AND expires_at > ? "
            f"GROUP BY policy_id",
            (*policy_ids, datetime.now(timezone.utc).isoformat()),
        )
        for tc in await tc_cursor.fetchall():
            token_counts[tc["policy_id"]] = tc["cnt"]

    # Look up service names
    service_names: dict[str, str] = {}
    service_ids = list({r["service_id"] for r in rows})
    for sid in service_ids:
        svc_cursor = await db.execute(
            "SELECT name FROM published_services WHERE service_id = ?", (sid,)
        )
        svc = await svc_cursor.fetchone()
        service_names[sid] = svc["name"] if svc else sid

    active_policies = []
    revoked_policies = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for row in rows:
        entry = {
            "id": row["id"],
            "service_name": service_names.get(row["service_id"], row["service_id"]),
            "action_ids": row["allowed_action_ids"],
            "risk_tier": row["risk_tier"],
            "max_lifetime_label": _lifetime_label(row["max_token_lifetime_seconds"]),
            "active_token_count": token_counts.get(row["id"], 0),
            "created_at_human": _human_date(row["created_at"]),
            "expires_at_human": _human_date(row["expires_at"]),
            "revoked_at_human": _human_date(row["revoked_at"]),
        }
        if row["revoked_at"] or row["expires_at"] < now_iso:
            revoked_policies.append(entry)
        else:
            active_policies.append(entry)

    success_message = ""
    if revoked == "1":
        success_message = "Policy revoked successfully. All active tokens have been invalidated."

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_policies": active_policies,
        "revoked_policies": revoked_policies,
        "csrf_token": _generate_csrf_token(request),
        "success_message": success_message,
    })


# ---------------------------------------------------------------------------
# POST /dashboard/revoke/<policy_id> — one-click policy revocation
# ---------------------------------------------------------------------------

@pages_router.post("/dashboard/revoke/{policy_id}", response_class=HTMLResponse)
async def dashboard_revoke(
    request: Request,
    policy_id: str,
    csrf_token: str = Form(""),
):
    """Revoke a policy from the dashboard."""
    session = _get_session(request)
    if not session:
        return RedirectResponse(url="/login?next=/dashboard", status_code=303)
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url="/dashboard", status_code=303)

    user_id = session["user_id"]
    db = await get_db()

    # Verify ownership
    cursor = await db.execute(
        "SELECT id FROM policies WHERE id = ? AND cafe_user_id = ?",
        (policy_id, user_id),
    )
    if not await cursor.fetchone():
        return RedirectResponse(url="/dashboard", status_code=303)

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE policies SET revoked_at = ?, updated_at = ? WHERE id = ? AND revoked_at IS NULL",
        (now, now, policy_id),
    )
    # Invalidate active tokens for this policy
    await db.execute(
        "DELETE FROM active_tokens WHERE policy_id = ?", (policy_id,)
    )
    await db.commit()

    return RedirectResponse(url="/dashboard?revoked=1", status_code=303)


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------

@pages_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next_url: str = ""):
    """Render the login page."""
    return templates.TemplateResponse("login.html", {
        "request": request,
        "next_url": next_url,
        "error": None,
        "email": None,
        "csrf_token": _generate_csrf_token(request),
    })


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@pages_router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("", alias="next"),
    csrf_token: str = Form(""),
):
    """Handle login form submission."""
    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "next_url": next_url,
            "error": "Invalid or expired form submission. Please try again.",
            "email": email,
            "csrf_token": _generate_csrf_token(request),
        }, status_code=403)
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, password_hash FROM cafe_users WHERE email = ?", (email.lower(),)
    )
    row = await cursor.fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "next_url": next_url,
            "error": "Invalid email or password.",
            "email": email,
            "csrf_token": _generate_csrf_token(request),
        }, status_code=401)

    # Rehash legacy SHA-256 passwords to bcrypt on successful login
    await _rehash_if_legacy(db, row["id"], password, row["password_hash"])

    session_token = _create_human_session_token(row["id"], email.lower())
    redirect_url = next_url if next_url else "/"
    response = RedirectResponse(url=redirect_url, status_code=303)
    _set_session_cookie(response, session_token)
    return response


# ---------------------------------------------------------------------------
# GET /register
# ---------------------------------------------------------------------------

@pages_router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, next_url: str = ""):
    """Render the registration page."""
    return templates.TemplateResponse("register.html", {
        "request": request,
        "next_url": next_url,
        "error": None,
        "email": None,
        "display_name": None,
        "csrf_token": _generate_csrf_token(request),
    })


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------

@pages_router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
    next_url: str = Form("", alias="next"),
    csrf_token: str = Form(""),
):
    """Handle registration form submission."""
    if not _validate_csrf_token(request, csrf_token):
        return templates.TemplateResponse("register.html", {
            "request": request,
            "next_url": next_url,
            "error": "Invalid or expired form submission. Please try again.",
            "email": email,
            "display_name": display_name,
            "csrf_token": _generate_csrf_token(request),
        }, status_code=403)
    if len(password) < 8:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "next_url": next_url,
            "error": "Password must be at least 8 characters.",
            "email": email,
            "display_name": display_name,
            "csrf_token": _generate_csrf_token(request),
        }, status_code=422)

    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM cafe_users WHERE email = ?", (email.lower(),)
    )
    if await cursor.fetchone():
        return templates.TemplateResponse("register.html", {
            "request": request,
            "next_url": next_url,
            "error": "An account with this email already exists.",
            "email": email,
            "display_name": display_name,
            "csrf_token": _generate_csrf_token(request),
        }, status_code=409)

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO cafe_users (id, email, display_name, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, email.lower(), display_name or None, _hash_password(password), now, now),
    )
    await db.commit()

    session_token = _create_human_session_token(user_id, email.lower())
    redirect_url = next_url if next_url else "/"
    response = RedirectResponse(url=redirect_url, status_code=303)
    _set_session_cookie(response, session_token)
    return response


# ---------------------------------------------------------------------------
# GET /consent/<consent_id>
# ---------------------------------------------------------------------------

@pages_router.get("/authorize/{consent_id}", response_class=HTMLResponse)
async def consent_page(request: Request, consent_id: str):
    """Render the authorization approval page."""
    session = _get_session(request)
    if not session:
        return RedirectResponse(url=f"/register?next_url=/authorize/{consent_id}", status_code=303)

    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM consents WHERE id = ?", (consent_id,)
    )
    consent = await cursor.fetchone()
    if not consent:
        return templates.TemplateResponse("consent_done.html", {
            "request": request,
            "status": "error",
            "title": "Not Found",
            "message": "This consent request does not exist.",
            "service_name": "",
            "policy_id": "",
        }, status_code=404)

    # Check expiry
    if consent["expires_at"] < datetime.now(timezone.utc).isoformat():
        return templates.TemplateResponse("consent_done.html", {
            "request": request,
            "status": "expired",
            "title": "Request Expired",
            "message": "",
            "service_name": "",
            "policy_id": "",
        })

    # Already approved
    if consent["status"] == "approved":
        return templates.TemplateResponse("consent_done.html", {
            "request": request,
            "status": "approved",
            "title": "Already Approved",
            "message": "This consent has already been approved.",
            "service_name": consent["service_id"],
            "policy_id": consent["policy_id"] or "",
        })

    # Already declined
    if consent["status"] == "declined":
        return templates.TemplateResponse("consent_done.html", {
            "request": request,
            "status": "declined",
            "title": "Declined",
            "message": "",
            "service_name": "",
            "policy_id": "",
        })

    # Look up service details for display
    svc_cursor = await db.execute(
        "SELECT name, menu_entry_json FROM published_services WHERE service_id = ?",
        (consent["service_id"],),
    )
    svc_row = await svc_cursor.fetchone()
    service_name = svc_row["name"] if svc_row else consent["service_id"]

    # Parse action details from menu
    actions = []
    action_ids = consent["action_ids"].split(",") if consent["action_ids"] else []
    risk_tier = "medium"
    if svc_row and svc_row["menu_entry_json"]:
        menu = json.loads(svc_row["menu_entry_json"])
        for action in menu.get("actions", []):
            if action["action_id"] in action_ids:
                actions.append(action)
                risk_tier = action.get("risk_tier", risk_tier)
    if not actions:
        for aid in action_ids:
            actions.append({"action_id": aid, "description": aid})

    # Look up risk tier from proxy config
    proxy_cursor = await db.execute(
        "SELECT risk_tier FROM proxy_configs WHERE service_id = ? AND action_id = ?",
        (consent["service_id"], action_ids[0] if action_ids else ""),
    )
    proxy_row = await proxy_cursor.fetchone()
    if proxy_row:
        risk_tier = proxy_row["risk_tier"]

    expires_dt = datetime.fromisoformat(consent["expires_at"])
    expires_human = expires_dt.strftime("%b %d, %Y at %I:%M %p UTC")

    return templates.TemplateResponse("consent.html", {
        "request": request,
        "consent_id": consent_id,
        "service_name": service_name,
        "consent_text": _consent_text(service_name, actions),
        "task_summary": consent["task_summary"],
        "actions": actions,
        "risk_tier": risk_tier,
        "lifetime_options": _lifetime_options(risk_tier),
        "max_lifetime_label": _ceiling_label(risk_tier),
        "expires_at_human": expires_human,
        "error": None,
        "csrf_token": _generate_csrf_token(request),
    })


# ---------------------------------------------------------------------------
# POST /consent/<consent_id>/approve
# ---------------------------------------------------------------------------

@pages_router.post("/authorize/{consent_id}/approve", response_class=HTMLResponse)
async def consent_approve_submit(
    request: Request,
    consent_id: str,
    token_lifetime_seconds: int = Form(900),
    csrf_token: str = Form(""),
):
    """Handle consent approval form submission."""
    session = _get_session(request)
    if not session:
        return RedirectResponse(url=f"/login?next=/authorize/{consent_id}", status_code=303)
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url=f"/authorize/{consent_id}", status_code=303)

    try:
        # We call the internal function by simulating the request
        # Instead, let's do it directly via DB to avoid circular complexity
        db = await get_db()

        cursor = await db.execute("SELECT * FROM consents WHERE id = ?", (consent_id,))
        consent = await cursor.fetchone()
        if not consent:
            raise HTTPException(status_code=404, detail="Consent not found")

        if consent["status"] != "pending":
            return RedirectResponse(url=f"/authorize/{consent_id}", status_code=303)

        user_id = session["user_id"]
        now = datetime.now(timezone.utc)

        # Look up risk tier
        action_ids = consent["action_ids"].split(",") if consent["action_ids"] else []
        proxy_cursor = await db.execute(
            "SELECT risk_tier FROM proxy_configs WHERE service_id = ? AND action_id = ?",
            (consent["service_id"], action_ids[0] if action_ids else ""),
        )
        proxy_row = await proxy_cursor.fetchone()
        risk_tier = proxy_row["risk_tier"] if proxy_row else "medium"

        # Apply ceiling
        ceiling = _RISK_TIER_CEILINGS.get(risk_tier, 900)
        if ceiling > 0:
            lifetime = min(token_lifetime_seconds, ceiling)
        else:
            lifetime = 0  # single-use

        # Create policy
        policy_id = str(uuid.uuid4())
        policy_expires = now + timedelta(days=90)

        await db.execute(
            """INSERT INTO policies
               (id, cafe_user_id, service_id, allowed_action_ids, scopes,
                risk_tier, max_token_lifetime_seconds, expires_at,
                revoked_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (
                policy_id, user_id, consent["service_id"],
                consent["action_ids"], consent["requested_scopes"],
                risk_tier, lifetime,
                policy_expires.isoformat(), now.isoformat(), now.isoformat(),
            ),
        )

        # Update consent
        await db.execute(
            """UPDATE consents SET status = 'approved', cafe_user_id = ?,
               policy_id = ?, updated_at = ? WHERE id = ?""",
            (user_id, policy_id, now.isoformat(), consent_id),
        )
        await db.commit()

        # Look up service name
        svc_cursor = await db.execute(
            "SELECT name FROM published_services WHERE service_id = ?",
            (consent["service_id"],),
        )
        svc_row = await svc_cursor.fetchone()
        service_name = svc_row["name"] if svc_row else consent["service_id"]

        # Fire webhook callback (best-effort)
        await _fire_consent_callback(consent["callback_url"], consent_id, "approved", policy_id)

        return templates.TemplateResponse("consent_done.html", {
            "request": request,
            "status": "approved",
            "title": "Authorization Approved",
            "message": "",
            "service_name": service_name,
            "policy_id": policy_id,
        })

    except HTTPException:
        return RedirectResponse(url=f"/authorize/{consent_id}", status_code=303)


# ---------------------------------------------------------------------------
# POST /consent/<consent_id>/decline
# ---------------------------------------------------------------------------

@pages_router.post("/authorize/{consent_id}/decline", response_class=HTMLResponse)
async def consent_decline(
    request: Request,
    consent_id: str,
    csrf_token: str = Form(""),
):
    """Decline an authorization request (POST with CSRF protection)."""
    session = _get_session(request)
    if not session:
        return RedirectResponse(url=f"/login?next=/authorize/{consent_id}", status_code=303)
    if not _validate_csrf_token(request, csrf_token):
        return RedirectResponse(url=f"/authorize/{consent_id}", status_code=303)

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Look up callback_url before updating
    cursor = await db.execute(
        "SELECT callback_url FROM consents WHERE id = ? AND status = 'pending'",
        (consent_id,),
    )
    consent_row = await cursor.fetchone()
    callback_url = consent_row["callback_url"] if consent_row else None

    await db.execute(
        "UPDATE consents SET status = 'declined', updated_at = ? WHERE id = ? AND status = 'pending'",
        (now, consent_id),
    )
    await db.commit()

    # Fire webhook callback (best-effort)
    await _fire_consent_callback(callback_url, consent_id, "declined")

    return templates.TemplateResponse("consent_done.html", {
        "request": request,
        "status": "declined",
        "title": "Authorization Declined",
        "message": "",
        "service_name": "",
        "policy_id": "",
    })
