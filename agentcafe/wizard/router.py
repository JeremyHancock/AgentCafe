"""Wizard Router — FastAPI endpoints for the Company Onboarding Wizard.

All /wizard/* endpoints are company-facing, not agent-facing.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import httpx
import jwt
import re
from fastapi import APIRouter, Header, HTTPException, UploadFile

from agentcafe.db.engine import get_db
from agentcafe.wizard.ai_enricher import enrich_spec
from agentcafe.wizard.models import (
    AuditLogEntry,
    CompanyCreateRequest,
    CompanyCreateResponse,
    CompanyLoginRequest,
    CompanyLoginResponse,
    DryRunResponse,
    DryRunResult,
    EditServiceResponse,
    IntegrationSaveRequest,
    PolicySaveRequest,
    PreviewResponse,
    PublishResponse,
    ReviewSaveRequest,
    ServiceDashboardResponse,
    ServiceListResponse,
    ServiceLogsResponse,
    ServiceStatusResponse,
    SpecFetchRequest,
    SpecParseRequest,
    SpecParseResponse,
)
from agentcafe.wizard.publisher import publish_draft
from agentcafe.wizard.review_engine import (
    create_draft,
    generate_preview,
    get_draft,
    save_integration,
    save_policy,
    save_review,
)
from agentcafe.wizard.spec_parser import SpecParseError, parse_openapi_spec

logger = logging.getLogger("agentcafe.wizard.router")

wizard_router = APIRouter(prefix="/wizard", tags=["wizard"])

class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""
    quarantine_days: int = 7

_state = _State()


def configure_wizard(signing_secret: str, quarantine_days: int = 7) -> None:
    """Set the signing secret for wizard session tokens. Called once at startup."""
    _state.signing_secret = signing_secret
    _state.quarantine_days = quarantine_days


def _create_session_token(company_id: str) -> str:
    """Create a short-lived JWT session token for a company."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": company_id,
        "iat": now,
        "exp": now + timedelta(hours=8),
        "iss": "agentcafe-wizard",
    }
    return jwt.encode(payload, _state.signing_secret, algorithm="HS256")


def _get_company_id_from_token(authorization: str | None) -> str:
    """Decode the session token from the Authorization header and return company_id."""
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"error": "missing_token", "message": "Authorization header is required."},
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token, _state.signing_secret, algorithms=["HS256"],
            issuer="agentcafe-wizard",
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "token_expired", "message": "Session token has expired."},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_token", "message": "Invalid session token."},
        ) from exc
    return payload["sub"]


# ---------------------------------------------------------------------------
# Step 1: Company account
# ---------------------------------------------------------------------------

@wizard_router.post("/companies", response_model=CompanyCreateResponse)
async def create_company(req: CompanyCreateRequest):
    """Create a new company account (Step 1)."""
    db = await get_db()

    # Check for existing email
    email = req.email.strip().lower()

    cursor = await db.execute(
        "SELECT id FROM companies WHERE email = ?", (email,)
    )
    if await cursor.fetchone():
        raise HTTPException(
            status_code=409,
            detail={"error": "email_exists", "message": "An account with this email already exists."},
        )

    company_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt())

    await db.execute(
        """INSERT INTO companies (id, name, email, password_hash, website, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, req.name, email, password_hash, req.website, now, now),
    )
    await db.commit()

    session_token = _create_session_token(company_id)
    logger.info("Created company account: %s (%s)", req.name, email)
    return CompanyCreateResponse(company_id=company_id, name=req.name, email=email, session_token=session_token)


@wizard_router.post("/companies/login", response_model=CompanyLoginResponse)
async def login_company(req: CompanyLoginRequest):
    """Sign in to a company account."""
    db = await get_db()

    email = req.email.strip().lower()

    cursor = await db.execute(
        "SELECT id, name, password_hash FROM companies WHERE email = ?",
        (email,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials", "message": "Invalid email or password."},
        )

    stored_hash = row["password_hash"]
    if isinstance(stored_hash, str):
        stored_hash = stored_hash.encode()
    if not bcrypt.checkpw(req.password.encode(), stored_hash):
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials", "message": "Invalid email or password."},
        )

    session_token = _create_session_token(row["id"])
    return CompanyLoginResponse(company_id=row["id"], name=row["name"], session_token=session_token)


# ---------------------------------------------------------------------------
# Step 2: Parse spec
# ---------------------------------------------------------------------------

@wizard_router.post("/specs/parse", response_model=SpecParseResponse)
async def parse_spec(
    req: SpecParseRequest,
    authorization: str | None = Header(default=None),
):
    """Upload and parse an OpenAPI spec (Step 2).

    Validates the spec, extracts operations, and generates a candidate Menu entry.
    Requires a valid session token in the Authorization header.
    """
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    # Verify company exists
    cursor = await db.execute(
        "SELECT id FROM companies WHERE id = ?", (company_id,)
    )
    if not await cursor.fetchone():
        raise HTTPException(
            status_code=404,
            detail={"error": "company_not_found", "message": "Company not found."},
        )

    # Parse the spec
    try:
        parsed_spec = parse_openapi_spec(req.raw_spec)
    except SpecParseError as exc:
        detail = {"error": "spec_parse_error", "message": exc.message}
        if exc.line is not None:
            detail["line"] = exc.line
        raise HTTPException(status_code=422, detail=detail) from exc

    # Generate candidate Menu entry (AI enrichment or rule-based fallback)
    candidate_menu = await enrich_spec(parsed_spec)

    # Create a draft in the database
    draft_id = await create_draft(
        db, company_id, parsed_spec, candidate_menu, req.raw_spec
    )

    return SpecParseResponse(
        draft_id=draft_id,
        parsed_spec=parsed_spec,
        candidate_menu=candidate_menu,
    )


# ---------------------------------------------------------------------------
# Step 2b: Upload spec file (multipart)
# ---------------------------------------------------------------------------

@wizard_router.post("/specs/upload", response_model=SpecParseResponse)
async def upload_spec(
    file: UploadFile,
    authorization: str | None = Header(default=None),
):
    """Upload an OpenAPI spec file (JSON or YAML) and parse it.

    Accepts multipart/form-data with a single file field.
    """
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    cursor = await db.execute(
        "SELECT id FROM companies WHERE id = ?", (company_id,)
    )
    if not await cursor.fetchone():
        raise HTTPException(
            status_code=404,
            detail={"error": "company_not_found", "message": "Company not found."},
        )

    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"error": "file_too_large", "message": "Spec file must be under 2 MB."},
        )

    try:
        raw_spec = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_encoding", "message": "File must be UTF-8 encoded text."},
        ) from exc

    try:
        parsed_spec = parse_openapi_spec(raw_spec)
    except SpecParseError as exc:
        detail = {"error": "spec_parse_error", "message": exc.message}
        if exc.line is not None:
            detail["line"] = exc.line
        raise HTTPException(status_code=422, detail=detail) from exc

    candidate_menu = await enrich_spec(parsed_spec)
    draft_id = await create_draft(db, company_id, parsed_spec, candidate_menu, raw_spec)

    return SpecParseResponse(
        draft_id=draft_id,
        parsed_spec=parsed_spec,
        candidate_menu=candidate_menu,
    )


# ---------------------------------------------------------------------------
# Step 2c: Fetch spec from URL
# ---------------------------------------------------------------------------

@wizard_router.post("/specs/fetch", response_model=SpecParseResponse)
async def fetch_spec(
    req: SpecFetchRequest,
    authorization: str | None = Header(default=None),
):
    """Fetch an OpenAPI spec from a URL and parse it."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    cursor = await db.execute(
        "SELECT id FROM companies WHERE id = ?", (company_id,)
    )
    if not await cursor.fetchone():
        raise HTTPException(
            status_code=404,
            detail={"error": "company_not_found", "message": "Company not found."},
        )

    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_url", "message": "URL must start with http:// or https://."},
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(req.url, follow_redirects=True)
            resp.raise_for_status()
            raw_spec = resp.text
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "fetch_failed", "message": f"URL returned HTTP {exc.response.status_code}."},
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "fetch_failed", "message": f"Could not fetch URL: {exc}"},
        ) from exc

    if len(raw_spec) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"error": "file_too_large", "message": "Fetched spec must be under 2 MB."},
        )

    try:
        parsed_spec = parse_openapi_spec(raw_spec)
    except SpecParseError as exc:
        detail = {"error": "spec_parse_error", "message": exc.message}
        if exc.line is not None:
            detail["line"] = exc.line
        raise HTTPException(status_code=422, detail=detail) from exc

    candidate_menu = await enrich_spec(parsed_spec)
    draft_id = await create_draft(db, company_id, parsed_spec, candidate_menu, raw_spec)

    return SpecParseResponse(
        draft_id=draft_id,
        parsed_spec=parsed_spec,
        candidate_menu=candidate_menu,
    )


# ---------------------------------------------------------------------------
# Step 3: Review
# ---------------------------------------------------------------------------

@wizard_router.put("/drafts/{draft_id}/review")
async def review_draft(
    draft_id: str,
    req: ReviewSaveRequest,
    authorization: str | None = Header(default=None),
):
    """Save the company's review edits (Step 3)."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    draft = await get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})
    if draft["company_id"] != company_id:
        raise HTTPException(status_code=403, detail={"error": "not_owner", "message": "You do not own this draft."})

    await save_review(
        db,
        draft_id,
        service_id=req.service_id,
        name=req.name,
        category=req.category,
        capability_tags=req.capability_tags,
        description=req.description,
        actions=req.actions,
        excluded_actions=req.excluded_actions,
    )

    return {"status": "saved", "draft_id": draft_id, "wizard_step": 3}


# ---------------------------------------------------------------------------
# Step 4: Policy & Safety
# ---------------------------------------------------------------------------

@wizard_router.put("/drafts/{draft_id}/policy")
async def policy_draft(
    draft_id: str,
    req: PolicySaveRequest,
    authorization: str | None = Header(default=None),
):
    """Save the company's policy settings (Step 4)."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    draft = await get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})
    if draft["company_id"] != company_id:
        raise HTTPException(status_code=403, detail={"error": "not_owner", "message": "You do not own this draft."})

    await save_policy(
        db,
        draft_id,
        actions_policy=req.actions,
        backend_url=req.backend_url,
        backend_auth_header=req.backend_auth_header,
        integration_mode=req.integration_mode,
    )

    return {"status": "saved", "draft_id": draft_id, "wizard_step": 4}


# ---------------------------------------------------------------------------
# Step 4b: Integration Setup (JV services only)
# ---------------------------------------------------------------------------

@wizard_router.put("/drafts/{draft_id}/integration")
async def integration_draft(
    draft_id: str,
    req: IntegrationSaveRequest,
    authorization: str | None = Header(default=None),
):
    """Save JV integration settings (Step 4b). Only for jointly_verified services."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    draft = await get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})
    if draft["company_id"] != company_id:
        raise HTTPException(status_code=403, detail={"error": "not_owner", "message": "You do not own this draft."})

    if req.integration_mode != "jointly_verified":
        raise HTTPException(
            status_code=400,
            detail={"error": "not_jv", "message": "Integration setup is only for jointly_verified services."},
        )

    if not any([req.cap_account_check, req.cap_account_create, req.cap_link_complete]):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "no_account_capability",
                "message": "A jointly-verified service must implement at least one of: account-check, account-create, or link-complete.",
            },
        )

    config = req.model_dump(exclude={"integration_mode"})

    await save_integration(
        db,
        draft_id,
        integration_mode=req.integration_mode,
        integration_config=config,
    )

    return {"status": "saved", "draft_id": draft_id, "wizard_step": "4b"}


# ---------------------------------------------------------------------------
# Step 5: Preview
# ---------------------------------------------------------------------------

@wizard_router.get("/drafts/{draft_id}/preview", response_model=PreviewResponse)
async def preview_draft(
    draft_id: str,
    authorization: str | None = Header(default=None),
):
    """Generate the final Menu entry preview (Step 5)."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    draft = await get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})
    if draft["company_id"] != company_id:
        raise HTTPException(status_code=403, detail={"error": "not_owner", "message": "You do not own this draft."})

    try:
        preview = await generate_preview(db, draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return preview


@wizard_router.post("/drafts/{draft_id}/dry-run", response_model=DryRunResponse)
async def dry_run_draft(
    draft_id: str,
    authorization: str | None = Header(default=None),
):
    """Test proxy mapping for each action (Step 5).

    Verifies that the backend URL is reachable and each action's
    path resolves correctly. Does not execute real backend calls.
    """
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    draft = await get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})
    if draft["company_id"] != company_id:
        raise HTTPException(status_code=403, detail={"error": "not_owner", "message": "You do not own this draft."})

    backend_url = draft.get("backend_url", "")
    final_menu_str = draft.get("final_menu_json")
    if not final_menu_str:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_preview", "message": "Generate a preview first (GET /preview)."},
        )

    final_menu = json.loads(final_menu_str)
    candidate_menu = json.loads(draft.get("candidate_menu_json") or "{}")
    candidate_actions = {
        a["action_id"]: a for a in candidate_menu.get("actions", [])
    }

    results: list[DryRunResult] = []
    all_ok = True

    async with httpx.AsyncClient(timeout=5.0) as client:
        for action in final_menu.get("actions", []):
            action_id = action["action_id"]
            candidate = candidate_actions.get(action_id, {})
            source_path = candidate.get("source_path", f"/{action_id}")

            # Check that we have a backend URL
            if not backend_url:
                results.append(DryRunResult(
                    action_id=action_id,
                    status="error",
                    message="No backend URL configured.",
                ))
                all_ok = False
                continue

            # Replace path template params with example values
            resolved_path = source_path
            for inp in candidate.get("required_inputs", []):
                placeholder = "{" + inp["name"] + "}"
                if placeholder in resolved_path:
                    example = str(inp.get("example", "test"))
                    resolved_path = resolved_path.replace(placeholder, example)
            # Replace any remaining unresolved {param} with "test"
            resolved_path = re.sub(r"\{[^}]+\}", "test", resolved_path)

            full_url = f"{backend_url.rstrip('/')}{resolved_path}"

            # Try a lightweight connectivity check against the action path
            try:
                resp = await client.request(
                    "HEAD", full_url,
                    follow_redirects=True,
                )
                if resp.status_code < 500:
                    results.append(DryRunResult(
                        action_id=action_id,
                        status="ok",
                        message=f"proxy mapping OK → {full_url}",
                    ))
                else:
                    results.append(DryRunResult(
                        action_id=action_id,
                        status="error",
                        message=f"Backend returned {resp.status_code}",
                    ))
                    all_ok = False
            except httpx.RequestError as exc:
                results.append(DryRunResult(
                    action_id=action_id,
                    status="error",
                    message=f"Backend unreachable: {exc}",
                ))
                all_ok = False

    # Save dry run results
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE draft_services
           SET dry_run_results_json = ?, backend_reachable = ?, updated_at = ?
           WHERE id = ?""",
        (
            json.dumps([r.model_dump() for r in results]),
            1 if all_ok else 0,
            now,
            draft_id,
        ),
    )
    await db.commit()

    return DryRunResponse(results=results, all_ok=all_ok)


# ---------------------------------------------------------------------------
# Step 6: Publish
# ---------------------------------------------------------------------------

@wizard_router.post("/drafts/{draft_id}/publish", response_model=PublishResponse)
async def publish_draft_endpoint(
    draft_id: str,
    authorization: str | None = Header(default=None),
):
    """Publish the draft to the live Menu (Step 6)."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    draft = await get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})
    if draft["company_id"] != company_id:
        raise HTTPException(status_code=403, detail={"error": "not_owner", "message": "You do not own this draft."})

    try:
        result = await publish_draft(db, draft_id, quarantine_days=_state.quarantine_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return result


# ---------------------------------------------------------------------------
# Post-publish management
# ---------------------------------------------------------------------------

async def _get_published_service(db, service_id: str, company_id: str) -> dict:
    """Fetch a published service and verify ownership. Raises HTTPException on failure."""
    cursor = await db.execute(
        "SELECT * FROM published_services WHERE service_id = ?",
        (service_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "service_not_found"})
    svc = dict(row)
    if svc["company_id"] != company_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "not_owner", "message": "You do not own this service."},
        )
    return svc


@wizard_router.post("/services/{service_id}/edit", response_model=EditServiceResponse)
async def edit_published_service(
    service_id: str,
    authorization: str | None = Header(default=None),
):
    """Create a new draft from an existing published service for re-editing.

    Pre-populates the draft with the current Menu entry and policy config,
    starting at the review step (wizard_step=3). Re-publishing overwrites
    the existing published service.
    """
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    svc = await _get_published_service(db, service_id, company_id)
    if svc["status"] == "unpublished":
        raise HTTPException(
            status_code=400,
            detail={"error": "service_unpublished", "message": "Cannot edit an unpublished service."},
        )

    menu_entry = json.loads(svc["menu_entry_json"])

    # Reconstruct policy_json from current proxy_configs
    cursor = await db.execute(
        "SELECT * FROM proxy_configs WHERE service_id = ?", (service_id,),
    )
    proxy_rows = await cursor.fetchall()

    policy_data = {}
    backend_url = ""
    backend_auth_header = ""
    edit_integration_mode = None
    for row in proxy_rows:
        row = dict(row)
        action_id = row["action_id"]
        if not backend_url:
            backend_url = row.get("backend_url", "")
            backend_auth_header = row.get("backend_auth_header", "")
        if not edit_integration_mode and row.get("integration_mode"):
            edit_integration_mode = row["integration_mode"]
        policy_data[action_id] = {
            "scope": row["scope"],
            "human_auth": bool(row["human_auth_required"]),
            "rate_limit": row["rate_limit"],
        }

    # Load service_integration_configs if JV
    edit_integration_config = None
    if edit_integration_mode == "jointly_verified":
        sic_cursor = await db.execute(
            "SELECT * FROM service_integration_configs WHERE service_id = ?",
            (service_id,),
        )
        sic_row = await sic_cursor.fetchone()
        if sic_row:
            sic = dict(sic_row)
            edit_integration_config = json.dumps({
                "integration_base_url": sic["integration_base_url"],
                "integration_auth_header": "",
                "identity_matching": sic["identity_matching"],
                "has_direct_signup": bool(sic["has_direct_signup"]),
                "cap_account_check": bool(sic["cap_account_check"]),
                "cap_account_create": bool(sic["cap_account_create"]),
                "cap_link_complete": bool(sic["cap_link_complete"]),
                "cap_unlink": bool(sic["cap_unlink"]),
                "cap_revoke": bool(sic["cap_revoke"]),
                "cap_grant_status": bool(sic["cap_grant_status"]),
                "path_revoke": sic.get("path_revoke"),
            })

    # Create the draft at wizard_step=3 (review), pre-populated
    draft_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO draft_services
           (id, company_id, wizard_step, raw_spec_text, parsed_spec_json,
            candidate_menu_json, policy_json, backend_url, backend_auth_header,
            integration_mode, integration_config_json,
            created_at, updated_at)
           VALUES (?, ?, 3, '', '{}', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            draft_id,
            company_id,
            json.dumps(menu_entry),
            json.dumps(policy_data),
            backend_url,
            backend_auth_header,
            edit_integration_mode,
            edit_integration_config,
            now,
            now,
        ),
    )
    await db.commit()

    logger.info("Created edit draft %s from published service %s", draft_id, service_id)

    return EditServiceResponse(
        draft_id=draft_id,
        service_id=service_id,
        message=f"Draft created from '{svc['name']}'. Resume the wizard to review and re-publish.",
    )


@wizard_router.get("/services", response_model=ServiceListResponse)
async def list_services(
    authorization: str | None = Header(default=None),
):
    """List all published services for the authenticated company."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()

    cursor = await db.execute(
        "SELECT service_id, name, description, status, published_at, menu_entry_json "
        "FROM published_services WHERE company_id = ? ORDER BY published_at DESC",
        (company_id,),
    )
    rows = await cursor.fetchall()

    services = []
    for row in rows:
        menu_entry = json.loads(row["menu_entry_json"])
        actions_count = len(menu_entry.get("actions", []))

        # Total requests
        req_cursor = await db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE service_id = ?",
            (row["service_id"],),
        )
        total_requests = (await req_cursor.fetchone())[0]

        # Recent requests (last 24h)
        req_cursor = await db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE service_id = ? AND timestamp > datetime('now', '-1 day')",
            (row["service_id"],),
        )
        recent_requests = (await req_cursor.fetchone())[0]

        services.append(ServiceDashboardResponse(
            service_id=row["service_id"],
            name=row["name"],
            description=row["description"],
            status=row["status"],
            published_at=row["published_at"],
            actions_count=actions_count,
            total_requests=total_requests,
            recent_requests=recent_requests,
        ))

    return ServiceListResponse(services=services)


@wizard_router.get("/services/{service_id}/dashboard", response_model=ServiceDashboardResponse)
async def service_dashboard(
    service_id: str,
    authorization: str | None = Header(default=None),
):
    """View basic dashboard info for a published service."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()
    svc = await _get_published_service(db, service_id, company_id)

    menu_entry = json.loads(svc["menu_entry_json"])
    actions_count = len(menu_entry.get("actions", []))

    # Total requests from audit log
    cursor = await db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE service_id = ?",
        (service_id,),
    )
    total_requests = (await cursor.fetchone())[0]

    # Recent requests (last 24 hours)
    cursor = await db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE service_id = ? AND timestamp > datetime('now', '-1 day')",
        (service_id,),
    )
    recent_requests = (await cursor.fetchone())[0]

    return ServiceDashboardResponse(
        service_id=service_id,
        name=svc["name"],
        description=svc["description"],
        status=svc["status"],
        published_at=svc["published_at"],
        actions_count=actions_count,
        total_requests=total_requests,
        recent_requests=recent_requests,
    )


@wizard_router.put("/services/{service_id}/pause", response_model=ServiceStatusResponse)
async def pause_service(
    service_id: str,
    authorization: str | None = Header(default=None),
):
    """Pause a live service — removes it from the Menu temporarily."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()
    svc = await _get_published_service(db, service_id, company_id)

    if svc["status"] == "paused":
        raise HTTPException(
            status_code=409,
            detail={"error": "already_paused", "message": "Service is already paused."},
        )
    if svc["status"] == "unpublished":
        raise HTTPException(
            status_code=409,
            detail={"error": "unpublished", "message": "Service is unpublished. Republish it first."},
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE published_services SET status = 'paused', updated_at = ? WHERE service_id = ?",
        (now, service_id),
    )
    await db.commit()
    logger.info("Paused service '%s'", service_id)

    return ServiceStatusResponse(
        service_id=service_id,
        status="paused",
        message=f"'{svc['name']}' is now paused and hidden from the Menu.",
    )


@wizard_router.put("/services/{service_id}/unpublish", response_model=ServiceStatusResponse)
async def unpublish_service(
    service_id: str,
    authorization: str | None = Header(default=None),
):
    """Unpublish a service — removes it from the Menu permanently."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()
    svc = await _get_published_service(db, service_id, company_id)

    if svc["status"] == "unpublished":
        raise HTTPException(
            status_code=409,
            detail={"error": "already_unpublished", "message": "Service is already unpublished."},
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE published_services SET status = 'unpublished', updated_at = ? WHERE service_id = ?",
        (now, service_id),
    )
    await db.commit()
    logger.info("Unpublished service '%s'", service_id)

    return ServiceStatusResponse(
        service_id=service_id,
        status="unpublished",
        message=f"'{svc['name']}' has been removed from the Menu.",
    )


@wizard_router.put("/services/{service_id}/resume", response_model=ServiceStatusResponse)
async def resume_service(
    service_id: str,
    authorization: str | None = Header(default=None),
):
    """Resume a paused service — makes it visible on the Menu again."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()
    svc = await _get_published_service(db, service_id, company_id)

    if svc["status"] == "live":
        raise HTTPException(
            status_code=409,
            detail={"error": "already_live", "message": "Service is already live."},
        )
    if svc["status"] == "unpublished":
        raise HTTPException(
            status_code=409,
            detail={"error": "unpublished", "message": "Service is unpublished. Republish it first."},
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE published_services SET status = 'live', updated_at = ? WHERE service_id = ?",
        (now, service_id),
    )
    await db.commit()
    logger.info("Resumed service '%s'", service_id)

    return ServiceStatusResponse(
        service_id=service_id,
        status="live",
        message=f"'{svc['name']}' is live on the Menu again.",
    )


@wizard_router.get("/services/{service_id}/logs", response_model=ServiceLogsResponse)
async def service_logs(
    service_id: str,
    authorization: str | None = Header(default=None),
    limit: int = 50,
):
    """View anonymized request logs for a published service."""
    company_id = _get_company_id_from_token(authorization)
    db = await get_db()
    await _get_published_service(db, service_id, company_id)

    # Cap limit at 200
    limit = min(limit, 200)

    cursor = await db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE service_id = ?",
        (service_id,),
    )
    total_entries = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT timestamp, action_id, outcome, response_code, latency_ms "
        "FROM audit_log WHERE service_id = ? ORDER BY timestamp DESC LIMIT ?",
        (service_id, limit),
    )
    rows = await cursor.fetchall()

    entries = [
        AuditLogEntry(
            timestamp=row["timestamp"],
            action_id=row["action_id"],
            outcome=row["outcome"],
            response_code=row["response_code"],
            latency_ms=row["latency_ms"],
        )
        for row in rows
    ]

    return ServiceLogsResponse(
        service_id=service_id,
        total_entries=total_entries,
        entries=entries,
    )
