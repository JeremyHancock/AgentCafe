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
from fastapi import APIRouter, Header, HTTPException

from agentcafe.db.engine import get_db
from agentcafe.wizard.ai_enricher import enrich_spec
from agentcafe.wizard.models import (
    CompanyCreateRequest,
    CompanyCreateResponse,
    CompanyLoginRequest,
    CompanyLoginResponse,
    DryRunResponse,
    DryRunResult,
    PolicySaveRequest,
    PreviewResponse,
    PublishResponse,
    ReviewSaveRequest,
    SpecParseRequest,
    SpecParseResponse,
)
from agentcafe.wizard.publisher import publish_draft
from agentcafe.wizard.review_engine import (
    create_draft,
    generate_preview,
    get_draft,
    save_policy,
    save_review,
)
from agentcafe.wizard.spec_parser import SpecParseError, parse_openapi_spec

logger = logging.getLogger("agentcafe.wizard.router")

wizard_router = APIRouter(prefix="/wizard", tags=["wizard"])

class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""

_state = _State()


def configure_wizard(signing_secret: str) -> None:
    """Set the signing secret for wizard session tokens. Called once at startup."""
    _state.signing_secret = signing_secret


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
    cursor = await db.execute(
        "SELECT id FROM companies WHERE email = ?", (req.email,)
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
        (company_id, req.name, req.email, password_hash, req.website, now, now),
    )
    await db.commit()

    session_token = _create_session_token(company_id)
    logger.info("Created company account: %s (%s)", req.name, req.email)
    return CompanyCreateResponse(company_id=company_id, name=req.name, email=req.email, session_token=session_token)


@wizard_router.post("/companies/login", response_model=CompanyLoginResponse)
async def login_company(req: CompanyLoginRequest):
    """Sign in to a company account."""
    db = await get_db()

    cursor = await db.execute(
        "SELECT id, name, password_hash FROM companies WHERE email = ?",
        (req.email,),
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
    )

    return {"status": "saved", "draft_id": draft_id, "wizard_step": 4}


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
        result = await publish_draft(db, draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return result
