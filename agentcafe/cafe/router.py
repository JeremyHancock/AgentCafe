"""Cafe router — GET /cafe/menu and POST /cafe/order endpoints.

These are the agent-facing endpoints. Agents interact only with these.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
import jwt
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agentcafe.cafe.menu import get_full_menu
from agentcafe.cafe.passport import validate_passport_jwt
from agentcafe.cafe.policy import check_rate_limit, validate_input_types
from agentcafe.crypto import decrypt
from agentcafe.db.engine import get_db

logger = logging.getLogger("agentcafe.cafe.router")

router = APIRouter(prefix="/cafe", tags=["cafe"])


def _card_suggestion(service_id: str) -> dict:
    """Build a card_suggestion object for auth-related 403 errors.

    Tells the agent they can request a Company Card to get standing
    authorization for this service.
    """
    return {
        "action": "request_card",
        "endpoint": "POST /cards/request",
        "body": {"service_id": service_id},
        "message": (
            f"You can request a Company Card for '{service_id}' to get "
            f"standing authorization. The human will review and approve "
            f"the card, then you can obtain tokens without per-action consent."
        ),
    }

# Safe characters for path parameter values: alphanumeric, underscore, dot, @, ~, hyphen.
# Blocks: /, \, ?, #, newlines, null bytes, spaces, and other injection vectors.
_SAFE_PATH_VALUE = re.compile(r'^[\w.@~-]+$')

class _State:
    """Module-level mutable state (avoids global statements)."""
    use_real_passport: bool = False
    http_client: httpx.AsyncClient | None = None
    issuer_api_key: str = ""

_state = _State()


def configure_router(use_real_passport: bool, issuer_api_key: str = "") -> None:
    """Set runtime config for the router. Called once at startup."""
    _state.use_real_passport = use_real_passport
    _state.issuer_api_key = issuer_api_key


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
                  human_identifier_field, quarantine_until, suspended_at
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

    # --- GATE 0: Service-level blocks (ADR-025) ---
    suspended_at = row["suspended_at"] if "suspended_at" in row.keys() else None
    if suspended_at:
        await _audit_log(db, req, "service_suspended", 503)
        raise HTTPException(
            status_code=503,
            detail={"error": "service_suspended", "message": "This service has been suspended by AgentCafe."},
        )

    quarantine_until = row["quarantine_until"] if "quarantine_until" in row.keys() else None
    if quarantine_until:
        quarantine_dt = datetime.fromisoformat(quarantine_until)
        if quarantine_dt.tzinfo is None:
            quarantine_dt = quarantine_dt.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < quarantine_dt:
            # Force Tier-2 consent for ALL actions during quarantine
            human_auth_required = True

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
            detail = {"error": error_code, "message": message_map.get(error_code, "Passport validation failed.")}
            if error_code in ("tier_insufficient", "scope_missing", "human_auth_required"):
                detail["card_suggestion"] = _card_suggestion(req.service_id)
            raise HTTPException(status_code=status_code, detail=detail)
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
                    "card_suggestion": _card_suggestion(req.service_id),
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
                        "card_suggestion": _card_suggestion(req.service_id),
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
# GET /cafe/admin/overview — Platform admin overview (requires ISSUER_API_KEY)
# ---------------------------------------------------------------------------

@router.get("/admin/overview")
async def admin_overview(x_api_key: str | None = Header(default=None, alias="X-Api-Key")):
    """Platform admin overview: full Menu + audit stats. Requires ISSUER_API_KEY via X-Api-Key header."""
    if not x_api_key or x_api_key != _state.issuer_api_key:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Invalid admin key."})

    db = await get_db()
    menu = await get_full_menu(db)

    # Global audit stats
    cursor = await db.execute("SELECT COUNT(*) FROM audit_log")
    total_requests = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE timestamp > datetime('now', '-1 day')"
    )
    recent_requests = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE outcome != 'success'"
    )
    failed_requests = (await cursor.fetchone())[0]

    # Per-service audit stats
    cursor = await db.execute(
        "SELECT service_id, COUNT(*) as cnt, "
        "SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as ok, "
        "SUM(CASE WHEN outcome != 'success' THEN 1 ELSE 0 END) as err "
        "FROM audit_log GROUP BY service_id ORDER BY cnt DESC"
    )
    rows = await cursor.fetchall()
    per_service_stats = {
        row["service_id"]: {"total": row["cnt"], "success": row["ok"], "errors": row["err"]}
        for row in rows
    }

    # Recent audit entries (last 50)
    cursor = await db.execute(
        "SELECT timestamp, service_id, action_id, outcome, response_code, latency_ms "
        "FROM audit_log ORDER BY timestamp DESC LIMIT 50"
    )
    recent_entries = [dict(row) for row in await cursor.fetchall()]

    return {
        "services": menu["services"],
        "stats": {
            "total_requests": total_requests,
            "recent_requests_24h": recent_requests,
            "failed_requests": failed_requests,
            "per_service": per_service_stats,
        },
        "recent_audit": recent_entries,
    }


# ---------------------------------------------------------------------------
# GET /cafe/admin/mcp-analytics — MCP traffic analytics (requires ISSUER_API_KEY)
# ---------------------------------------------------------------------------

@router.get("/admin/mcp-analytics")
async def mcp_analytics(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    hours: int = 24,
):
    """MCP adapter analytics: tool usage, popular queries, error rates, top services.

    Args:
        hours: Look-back window in hours (default 24, max 720 = 30 days).
    """
    if not x_api_key or x_api_key != _state.issuer_api_key:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Invalid admin key."})

    hours = max(1, min(720, hours))
    db = await get_db()
    since = f"datetime('now', '-{hours} hours')"

    # Total MCP requests in window
    cursor = await db.execute(
        f"SELECT COUNT(*) FROM mcp_request_log WHERE timestamp > {since}"
    )
    total = (await cursor.fetchone())[0]

    # Per-tool breakdown
    cursor = await db.execute(
        f"""SELECT tool_name, COUNT(*) as cnt,
                   SUM(CASE WHEN outcome = 'ok' THEN 1 ELSE 0 END) as ok,
                   SUM(CASE WHEN outcome = 'error' THEN 1 ELSE 0 END) as errors,
                   SUM(CASE WHEN outcome = 'auth_required' THEN 1 ELSE 0 END) as auth_required,
                   ROUND(AVG(latency_ms), 1) as avg_latency_ms
            FROM mcp_request_log WHERE timestamp > {since}
            GROUP BY tool_name ORDER BY cnt DESC"""
    )
    per_tool = {row["tool_name"]: {
        "total": row["cnt"], "ok": row["ok"], "errors": row["errors"],
        "auth_required": row["auth_required"], "avg_latency_ms": row["avg_latency_ms"],
    } for row in await cursor.fetchall()}

    # Top search queries (cafe.search only)
    cursor = await db.execute(
        f"""SELECT query, COUNT(*) as cnt
            FROM mcp_request_log
            WHERE tool_name = 'cafe.search' AND query IS NOT NULL AND timestamp > {since}
            GROUP BY query ORDER BY cnt DESC LIMIT 20"""
    )
    top_queries = [{"query": row["query"], "count": row["cnt"]} for row in await cursor.fetchall()]

    # Top explored services (cafe.get_details + cafe.invoke + cafe.request_card)
    cursor = await db.execute(
        f"""SELECT service_id, COUNT(*) as cnt, tool_name
            FROM mcp_request_log
            WHERE service_id IS NOT NULL AND timestamp > {since}
            GROUP BY service_id, tool_name ORDER BY cnt DESC LIMIT 30"""
    )
    service_rows = await cursor.fetchall()
    top_services: dict[str, dict] = {}
    for row in service_rows:
        sid = row["service_id"]
        if sid not in top_services:
            top_services[sid] = {"total": 0, "by_tool": {}}
        top_services[sid]["total"] += row["cnt"]
        top_services[sid]["by_tool"][row["tool_name"]] = row["cnt"]

    # Error breakdown
    cursor = await db.execute(
        f"""SELECT error_code, COUNT(*) as cnt
            FROM mcp_request_log
            WHERE error_code IS NOT NULL AND timestamp > {since}
            GROUP BY error_code ORDER BY cnt DESC LIMIT 20"""
    )
    error_breakdown = [{"error_code": row["error_code"], "count": row["cnt"]} for row in await cursor.fetchall()]

    # Unique passport hashes (proxy for unique agents)
    cursor = await db.execute(
        f"""SELECT COUNT(DISTINCT passport_hash) FROM mcp_request_log
            WHERE passport_hash IS NOT NULL AND timestamp > {since}"""
    )
    unique_agents = (await cursor.fetchone())[0]

    # Recent entries (last 50)
    cursor = await db.execute(
        f"""SELECT timestamp, tool_name, query, service_id, action_id,
                   result_count, outcome, error_code, latency_ms
            FROM mcp_request_log WHERE timestamp > {since}
            ORDER BY timestamp DESC LIMIT 50"""
    )
    recent = [dict(row) for row in await cursor.fetchall()]

    return {
        "window_hours": hours,
        "summary": {
            "total_requests": total,
            "unique_agents": unique_agents,
        },
        "per_tool": per_tool,
        "top_queries": top_queries,
        "top_services": dict(sorted(top_services.items(), key=lambda x: -x[1]["total"])),
        "error_breakdown": error_breakdown,
        "recent": recent,
    }


# ---------------------------------------------------------------------------
# POST /cafe/services/{service_id}/suspend — Admin suspends a service (ADR-025)
# ---------------------------------------------------------------------------

class SuspendRequest(BaseModel):
    """Request body for service suspension."""
    reason: str = ""


@router.post("/services/{service_id}/suspend")
async def suspend_service(
    service_id: str,
    req: SuspendRequest,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    """Suspend a service immediately. All future orders return 503.

    Requires the ISSUER_API_KEY via X-Api-Key header (admin-only).
    """
    if not x_api_key or x_api_key != _state.issuer_api_key:
        raise HTTPException(status_code=403, detail={"error": "forbidden"})

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        "SELECT DISTINCT service_id FROM proxy_configs WHERE service_id = ?",
        (service_id,),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail={"error": "service_not_found"})

    await db.execute(
        "UPDATE proxy_configs SET suspended_at = ? WHERE service_id = ?",
        (now, service_id),
    )
    await db.commit()

    logger.warning("Service %s suspended: %s", service_id, req.reason or "(no reason)")
    return {"service_id": service_id, "suspended_at": now, "reason": req.reason}


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


_GENESIS_HASH = "0" * 64
_audit_lock = asyncio.Lock()


async def verify_audit_chain(db) -> dict:
    """Walk the audit log and verify every hash link.

    Returns {"valid": True, "entries": N} or
            {"valid": False, "entries": N, "broken_at": entry_id, "position": idx}.
    """
    cursor = await db.execute(
        """SELECT id, timestamp, service_id, action_id, passport_hash,
                  inputs_hash, outcome, response_code, latency_ms,
                  prev_hash, entry_hash, seq
           FROM audit_log ORDER BY seq ASC, timestamp ASC"""
    )
    rows = await cursor.fetchall()

    expected_prev = _GENESIS_HASH
    for idx, row in enumerate(rows):
        # Skip legacy entries without hash chain columns
        if not row["entry_hash"]:
            continue

        # Verify prev_hash links to the previous entry
        if row["prev_hash"] != expected_prev:
            return {"valid": False, "entries": len(rows), "broken_at": row["id"], "position": idx}

        # Recompute entry_hash
        chain_input = "|".join([
            row["prev_hash"], row["id"], row["timestamp"], row["service_id"],
            row["action_id"], row["passport_hash"], row["inputs_hash"],
            row["outcome"], str(row["response_code"]), str(row["latency_ms"]),
        ])
        recomputed = hashlib.sha256(chain_input.encode()).hexdigest()
        if recomputed != row["entry_hash"]:
            return {"valid": False, "entries": len(rows), "broken_at": row["id"], "position": idx}

        expected_prev = row["entry_hash"]

    return {"valid": True, "entries": len(rows)}


async def _audit_log(
    db, req: OrderRequest, outcome: str, response_code: int, latency_ms: int = 0
) -> None:
    """Write a hash-chained entry to the audit log.

    Uses an asyncio Lock to serialize SELECT prev_hash + INSERT, preventing
    concurrent tasks from forking the hash chain.
    """
    async with _audit_lock:
        # Fetch the most recent entry's hash for chaining (ordered by seq)
        cursor = await db.execute(
            "SELECT entry_hash, seq FROM audit_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
        )
        prev_row = await cursor.fetchone()
        prev_hash = prev_row["entry_hash"] if prev_row and prev_row["entry_hash"] else _GENESIS_HASH
        next_seq = (prev_row["seq"] + 1) if prev_row and prev_row["seq"] is not None else 1

        entry_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        passport_hash = hashlib.sha256(req.passport.encode()).hexdigest()[:16]
        inputs_hash = hashlib.sha256(json.dumps(req.inputs, sort_keys=True).encode()).hexdigest()[:16]

        # Compute entry hash: SHA-256(prev_hash || all fields)
        chain_input = "|".join([
            prev_hash, entry_id, timestamp, req.service_id, req.action_id,
            passport_hash, inputs_hash, outcome, str(response_code), str(latency_ms),
        ])
        entry_hash = hashlib.sha256(chain_input.encode()).hexdigest()

        await db.execute(
            """INSERT INTO audit_log (id, timestamp, service_id, action_id, passport_hash,
                                       inputs_hash, outcome, response_code, latency_ms,
                                       prev_hash, entry_hash, seq)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id, timestamp, req.service_id, req.action_id,
                passport_hash, inputs_hash, outcome, response_code, latency_ms,
                prev_hash, entry_hash, next_seq,
            ),
        )
        await db.commit()
