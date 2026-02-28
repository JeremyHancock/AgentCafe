"""Cafe router — GET /cafe/menu and POST /cafe/order endpoints.

These are the agent-facing endpoints. Agents interact only with these.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

import httpx
import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentcafe.cafe.menu import get_full_menu
from agentcafe.cafe.passport import validate_passport_jwt
from agentcafe.cafe.policy import check_rate_limit, validate_input_types
from agentcafe.crypto import decrypt
from agentcafe.db.engine import get_db

router = APIRouter(prefix="/cafe", tags=["cafe"])

# Safe characters for path parameter values: alphanumeric, underscore, dot, @, ~, hyphen.
# Blocks: /, \, ?, #, newlines, null bytes, spaces, and other injection vectors.
_SAFE_PATH_VALUE = re.compile(r'^[\w.@~-]+$')

class _State:
    """Module-level mutable state (avoids global statements)."""
    use_real_passport: bool = False
    http_client: httpx.AsyncClient | None = None

_state = _State()


def configure_router(use_real_passport: bool) -> None:
    """Set runtime config for the router. Called once at startup."""
    _state.use_real_passport = use_real_passport


async def get_http_client() -> httpx.AsyncClient:
    """Return the shared httpx client, creating it on first use."""
    if _state.http_client is None:
        _state.http_client = httpx.AsyncClient(timeout=30.0)
    return _state.http_client


async def close_http_client() -> None:
    """Close the shared httpx client (call on shutdown)."""
    if _state.http_client is not None:
        await _state.http_client.aclose()
        _state.http_client = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class OrderRequest(BaseModel):
    """The locked order format: service_id + action_id + passport + inputs."""
    service_id: str
    action_id: str
    passport: str
    inputs: dict


# ---------------------------------------------------------------------------
# GET /cafe/menu — Browse the Menu
# ---------------------------------------------------------------------------

@router.get("/menu")
async def browse_menu():
    """Return the full AgentCafe Menu.

    Agents browse freely — no Passport required.
    The Menu is semantic, lightweight, and agent-friendly.
    No HTTP methods, no paths, no backend details.
    """
    db = await get_db()
    menu = await get_full_menu(db)
    return menu


# ---------------------------------------------------------------------------
# POST /cafe/order — Place an order
# ---------------------------------------------------------------------------

@router.post("/order")
async def place_order(req: OrderRequest):
    """Place an order through the Cafe proxy.

    Double validation:
    1. Human Passport validation (scope + authorization)
    2. Company Policy validation (rate limit, action enabled, inputs)

    Then proxy the request to the backend.
    """
    db = await get_db()

    # --- Look up the proxy config for this service_id + action_id ---
    cursor = await db.execute(
        """SELECT backend_url, backend_path, backend_method, backend_auth_header,
                  scope, human_auth_required, rate_limit, risk_tier,
                  human_identifier_field
           FROM proxy_configs
           WHERE service_id = ? AND action_id = ?""",
        (req.service_id, req.action_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "action_not_found",
                "message": f"No action '{req.action_id}' found for service '{req.service_id}'.",
            },
        )

    backend_url = row["backend_url"]
    backend_path = row["backend_path"]
    backend_method = row["backend_method"]
    backend_auth_header = decrypt(row["backend_auth_header"])
    scope = row["scope"]
    human_auth_required = bool(row["human_auth_required"])
    _rate_limit = row["rate_limit"]
    risk_tier = row["risk_tier"] if "risk_tier" in row.keys() else "medium"
    human_identifier_field = row["human_identifier_field"] if "human_identifier_field" in row.keys() else None

    # --- GATE 1: Passport Validation ---
    if _state.use_real_passport:
        # Real JWT validation (Phase 2)
        valid, error_code = await validate_passport_jwt(
            req.passport, req.service_id, req.action_id, human_auth_required
        )
        if not valid:
            status_map = {
                "passport_invalid": 401,
                "passport_expired": 401,
                "passport_revoked": 401,
                "policy_revoked": 401,
                "tier_insufficient": 403,
                "scope_missing": 403,
                "human_auth_required": 403,
            }
            status_code = status_map.get(error_code, 401)
            message_map = {
                "passport_invalid": "Invalid or expired Passport.",
                "passport_expired": "Passport has expired.",
                "passport_revoked": "Passport has been revoked.",
                "policy_revoked": "The policy backing this Passport has been revoked by the human.",
                "tier_insufficient": "This action requires a Tier-2 (write) Passport. Your Tier-1 (read) Passport cannot perform write actions.",
                "scope_missing": f"Your Passport is missing the required scope: '{scope}'.",
                "human_auth_required": "This action requires explicit human authorization in your Passport.",
            }
            await _audit_log(db, req, error_code, status_code)
            raise HTTPException(
                status_code=status_code,
                detail={"error": error_code, "message": message_map.get(error_code, "Passport validation failed.")},
            )
    else:
        # MVP: Accept "demo-passport" as a valid passport with all scopes.
        passport_valid, passport_scopes = _validate_passport_mvp(req.passport)
        if not passport_valid:
            await _audit_log(db, req, "passport_invalid", 401)
            raise HTTPException(
                status_code=401,
                detail={"error": "passport_invalid", "message": "Invalid or expired Passport."},
            )

        # Check required scope
        if scope not in passport_scopes:
            await _audit_log(db, req, "scope_missing", 403)
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "scope_missing",
                    "message": f"Your Passport is missing the required scope: '{scope}'.",
                },
            )

        # Check human authorization
        if human_auth_required:
            has_human_auth = _check_human_authorization_mvp(req.passport, req.service_id, req.action_id)
            if not has_human_auth:
                await _audit_log(db, req, "human_auth_required", 403)
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "human_auth_required",
                        "message": "This action requires explicit human authorization in your Passport.",
                    },
                )

    # --- GATE 1b: Identity Verification (v2-spec.md §7) ---
    # For medium+ risk actions with a human_identifier_field, enforce read-before-write.
    # The agent must have performed a prior read action on this service before writing.
    if (
        _state.use_real_passport
        and human_identifier_field
        and risk_tier in ("medium", "high", "critical")
    ):
        # Verify the identifier field is present in inputs
        identifier_value = req.inputs.get(human_identifier_field)
        if not identifier_value:
            await _audit_log(db, req, "identity_field_missing", 422)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "identity_field_missing",
                    "message": f"Write action requires '{human_identifier_field}' for identity verification.",
                },
            )

        # Read-before-write: check audit log for a prior successful read on this service
        passport_hash_check = hashlib.sha256(req.passport.encode()).hexdigest()[:16]
        read_cursor = await db.execute(
            """SELECT 1 FROM audit_log
               WHERE passport_hash = ? AND service_id = ? AND response_code = 200
               LIMIT 1""",
            (passport_hash_check, req.service_id),
        )
        if not await read_cursor.fetchone():
            await _audit_log(db, req, "read_before_write_required", 403)
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "read_before_write_required",
                    "message": f"Risk tier '{risk_tier}' requires a prior read action on this service before writing.",
                },
            )

    # --- GATE 2: Company Policy Validation ---
    # Check the service is live and get menu entry for input validation
    svc_cursor = await db.execute(
        "SELECT status, menu_entry_json FROM published_services WHERE service_id = ?",
        (req.service_id,),
    )
    svc_row = await svc_cursor.fetchone()
    if svc_row is None or svc_row["status"] != "live":
        await _audit_log(db, req, "service_unavailable", 503)
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "This service is currently unavailable."},
        )

    # Validate required inputs are present
    menu_entry = json.loads(svc_row["menu_entry_json"])
    action_def = next((a for a in menu_entry.get("actions", []) if a["action_id"] == req.action_id), None)
    if action_def:
        required_inputs = action_def.get("required_inputs", [])
        required_names = {inp["name"] for inp in required_inputs}
        provided_names = set(req.inputs.keys())
        missing = required_names - provided_names
        if missing:
            await _audit_log(db, req, "missing_inputs", 422)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "missing_inputs",
                    "message": f"Missing required inputs: {', '.join(sorted(missing))}",
                    "missing": sorted(missing),
                },
            )

        # Validate input types against Menu example values
        types_ok, type_errors = validate_input_types(req.inputs, required_inputs)
        if not types_ok:
            await _audit_log(db, req, "invalid_input_types", 422)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_input_types",
                    "message": f"Input type errors: {'; '.join(type_errors)}",
                    "type_errors": type_errors,
                },
            )

    # Rate limiting — sliding window per passport + action
    passport_hash = hashlib.sha256(req.passport.encode()).hexdigest()[:16]

    # Extract policy_id for V2 rate-limit communication (per-policy shared budget)
    _policy_id_for_rate = None
    try:
        _decoded = jwt.decode(req.passport, options={"verify_signature": False})
        _policy_id_for_rate = _decoded.get("policy_id")
    except Exception:  # pylint: disable=broad-except  # best-effort extraction
        pass

    rate_ok, rate_error = await check_rate_limit(
        db, passport_hash, req.service_id, req.action_id, _rate_limit,
        policy_id=_policy_id_for_rate,
    )
    if not rate_ok:
        await _audit_log(db, req, "rate_limit_exceeded", 429)
        retry_after = rate_error.get("retry_after_seconds", 60) if rate_error else 60
        raise HTTPException(
            status_code=429,
            detail=rate_error,
            headers={"Retry-After": str(retry_after)},
        )

    # --- PROXY: Forward to backend ---
    # Resolve path parameters from inputs (e.g., {room_id} → inputs["room_id"])
    resolved_path = backend_path
    for key, value in req.inputs.items():
        placeholder = f"{{{key}}}"
        if placeholder in resolved_path:
            str_value = str(value)
            if not _SAFE_PATH_VALUE.match(str_value):
                await _audit_log(db, req, "input_injection_blocked", 422)
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid_path_parameter",
                        "message": f"Input '{key}' contains unsafe characters for use in a URL path.",
                        "field": key,
                    },
                )
            resolved_path = resolved_path.replace(placeholder, str_value)

    # Reject if any placeholders remain unresolved
    if re.search(r'\{[\w]+\}', resolved_path):
        unresolved = re.findall(r'\{([\w]+)\}', resolved_path)
        await _audit_log(db, req, "missing_path_params", 422)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_path_parameters",
                "message": f"Required path parameters not provided: {', '.join(unresolved)}",
                "fields": unresolved,
            },
        )

    target_url = f"{backend_url}{resolved_path}"

    headers = {}
    if backend_auth_header:
        headers["Authorization"] = backend_auth_header

    start_time = datetime.now(timezone.utc)

    try:
        client = await get_http_client()
        if backend_method.upper() == "GET":
            resp = await client.get(target_url, headers=headers)
        elif backend_method.upper() == "POST":
            resp = await client.post(target_url, json=req.inputs, headers=headers)
        elif backend_method.upper() == "PUT":
            resp = await client.put(target_url, json=req.inputs, headers=headers)
        elif backend_method.upper() == "DELETE":
            resp = await client.delete(target_url, headers=headers)
        else:
            resp = await client.post(target_url, json=req.inputs, headers=headers)
    except httpx.RequestError as exc:
        await _audit_log(db, req, "backend_error", 502)
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": "The service backend is temporarily unreachable."},
        ) from exc

    latency_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    # Audit log
    outcome = "success" if 200 <= resp.status_code < 400 else "backend_error"
    await _audit_log(db, req, outcome, resp.status_code, latency_ms)

    # Return the backend response to the agent
    try:
        body = resp.json()
    except (ValueError, KeyError):
        body = {"raw": resp.text}

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=body)

    return body


# ---------------------------------------------------------------------------
# MVP Passport helpers (kept for backward compatibility, USE_REAL_PASSPORT=false)
# ---------------------------------------------------------------------------

def _validate_passport_mvp(passport: str) -> tuple[bool, list[str]]:
    """MVP passport validation.

    Accepts "demo-passport" as a valid passport with all scopes.
    Scopes use the locked {service_id}:{action_id} format.
    """
    if passport == "demo-passport":
        # Demo passport has all scopes
        return True, [
            "stayright-hotels:search-availability", "stayright-hotels:get-room-details",
            "stayright-hotels:book-room", "stayright-hotels:cancel-booking",
            "quickbite-delivery:browse-menu", "quickbite-delivery:place-order",
            "quickbite-delivery:track-order", "quickbite-delivery:cancel-order",
            "fixright-home:search-providers", "fixright-home:book-appointment",
            "fixright-home:reschedule-appointment", "fixright-home:cancel-appointment",
        ]
    return False, []


def _check_human_authorization_mvp(passport: str, service_id: str, action_id: str) -> bool:
    """MVP human authorization check.

    In the demo, "demo-passport" is pre-authorized for everything.
    When USE_REAL_PASSPORT is true, this function is not called.
    """
    _ = (service_id, action_id)
    return passport == "demo-passport"


async def _audit_log(
    db, req: OrderRequest, outcome: str, response_code: int, latency_ms: int = 0
) -> None:
    """Write an entry to the audit log."""
    await db.execute(
        """INSERT INTO audit_log (id, timestamp, service_id, action_id, passport_hash,
                                   inputs_hash, outcome, response_code, latency_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            datetime.now(timezone.utc).isoformat(),
            req.service_id,
            req.action_id,
            hashlib.sha256(req.passport.encode()).hexdigest()[:16],
            hashlib.sha256(json.dumps(req.inputs, sort_keys=True).encode()).hexdigest()[:16],
            outcome,
            response_code,
            latency_ms,
        ),
    )
    await db.commit()
