"""MCP Server Adapter — 5-tool LLM-native discovery pattern via Streamable HTTP.

Exposes AgentCafe's Menu, Company Cards, and order proxy as MCP tools.
MCP adapts to the Cafe — not the other way around (ADR-029).
cafe.invoke auto-resolves active Company Cards: if a Tier-1 passport fails
authorization, the adapter transparently upgrades to a Tier-2 token via the
card's token endpoint and retries.

Tools:
    cafe.search      — Semantic search across the Menu (summaries only)
    cafe.get_details — Full Menu entry for a specific service
    cafe.request_card — Initiate a Company Card request
    cafe.invoke      — Execute a service action via the Cafe proxy

Auth: OAuth 2.0 disabled — MCP clients (Claude Code, etc.) do not yet
reliably support the OAuth flow for HTTP transport. The server is open.
Write operations still require a valid Passport (checked inside each tool).
OAuth can be re-enabled via mcp_oauth.py once clients catch up.

See docs/strategy/strategic-review-briefing.md §8.2 and ADR-029.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from agentcafe.cafe.cards import (
    CardRequestBody,
    CardTokenRequest,
    _validate_agent_passport,
    get_card_token as _get_card_token,
    request_card as _cards_request_card,
)
from agentcafe.cafe.menu import get_full_menu
from agentcafe.cafe.router import OrderRequest, place_order
from agentcafe.db.engine import get_db
from agentcafe.keys import sign_passport_token

logger = logging.getLogger("agentcafe.mcp")

# ---------------------------------------------------------------------------
# MCP Server — stateless, Streamable HTTP (open access)
#
# OAuth removed: MCP clients (Claude Code, etc.) do not yet reliably
# support the OAuth 2.0 authorization flow for HTTP transport. The
# server is open so any agent can connect and browse the Menu. Write
# operations still require a valid Passport (checked inside each tool).
# OAuth support can be re-enabled once MCP clients catch up.
# ---------------------------------------------------------------------------

mcp_server = FastMCP(
    "AgentCafe",
    instructions=(
        "You have access to AgentCafe, a marketplace of real-world services. "
        "When a task requires capabilities you don't have natively, "
        "use cafe.search to find available services. "
        "Browsing is free and requires no credentials. "
        "To perform actions, first call cafe.get_passport to obtain a "
        "read-only Passport. Write operations that modify real-world state "
        "require human authorization — the Cafe will guide you through the "
        "process when needed."
    ),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["agentcafe.io", "agentcafe.fly.dev", "localhost", "127.0.0.1"],
    ),
)


# ---------------------------------------------------------------------------
# Request logging — writes to mcp_request_log (migration 0011)
# ---------------------------------------------------------------------------

async def _log_mcp_request(
    tool_name: str,
    *,
    query: str | None = None,
    service_id: str | None = None,
    action_id: str | None = None,
    category: str | None = None,
    result_count: int | None = None,
    outcome: str = "ok",
    error_code: str | None = None,
    passport: str | None = None,
    latency_ms: int = 0,
) -> None:
    """Write an entry to the mcp_request_log table. Fire-and-forget — never raises."""
    try:
        db = await get_db()
        passport_hash = None
        if passport:
            passport_hash = hashlib.sha256(passport.encode()).hexdigest()[:16]
        await db.execute(
            """INSERT INTO mcp_request_log
               (id, timestamp, tool_name, query, service_id, action_id, category,
                result_count, outcome, error_code, passport_hash, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                tool_name,
                query,
                service_id,
                action_id,
                category,
                result_count,
                outcome,
                error_code,
                passport_hash,
                latency_ms,
            ),
        )
        await db.commit()
    except Exception:  # pylint: disable=broad-except
        logger.debug("Failed to log MCP request for %s", tool_name, exc_info=True)


# ---------------------------------------------------------------------------
# Tool 0: cafe.get_passport — Self-issue a read-only Passport
# ---------------------------------------------------------------------------

_TIER1_LIFETIME_HOURS = 3


@mcp_server.tool(name="cafe.get_passport")
async def cafe_get_passport(
    agent_tag: str = "mcp-agent",
) -> dict[str, Any]:
    """Get a Passport so you can use Cafe services.

    Call this before cafe.invoke or cafe.request_card. Returns a read-only
    Passport valid for 3 hours. No human approval needed.

    Args:
        agent_tag: A short label identifying you (used for audit trail).
    """
    t0 = time.monotonic()
    agent_handle = hashlib.sha256(
        f"agent:{agent_tag}:{uuid.uuid4()}".encode()
    ).hexdigest()[:16]

    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=_TIER1_LIFETIME_HOURS)

    payload = {
        "iss": "agentcafe",
        "sub": f"agent:{agent_handle}",
        "aud": "agentcafe",
        "exp": exp,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "tier": "read",
        "granted_by": "self",
        "agent_tag": agent_tag,
    }

    token = sign_passport_token(payload)
    await _log_mcp_request(
        "cafe.get_passport",
        outcome="ok",
        latency_ms=int((time.monotonic() - t0) * 1000),
    )
    return {
        "passport": token,
        "tier": "read",
        "expires_at": exp.isoformat(),
        "agent_handle": agent_handle,
        "hint": (
            "This is a read-only Passport. You can browse and get details freely. "
            "If an action requires human authorization, the Cafe will return "
            "instructions on how to proceed."
        ),
    }


# ---------------------------------------------------------------------------
# Tool 1: cafe.search — Semantic search across the Menu
# ---------------------------------------------------------------------------

@mcp_server.tool(name="cafe.search")
async def cafe_search(
    query: str = "",
    category: str = "",
    max_results: int = 10,
) -> dict[str, Any]:
    """Find external services and capabilities you don't have natively.

    Use this whenever a task requires a real-world action you can't perform
    directly. Returns lightweight summaries — use cafe.get_details for full
    input schemas.

    Args:
        query: Natural language description of what you need.
        category: Optional category filter.
        max_results: Max results to return (1-20, default 10).
    """
    t0 = time.monotonic()
    max_results = max(1, min(20, max_results))
    db = await get_db()
    menu = await get_full_menu(db)
    services = menu.get("services", [])

    query_lower = query.lower()
    category_lower = category.lower()

    results: list[dict] = []
    for service in services:
        service_id = service.get("service_id", "")
        service_name = service.get("name", "")
        service_desc = service.get("description", "")

        # Category filter
        if category_lower and category_lower not in service.get("category", "").lower():
            continue

        for action in service.get("actions", []):
            action_id = action.get("action_id", "")
            action_name = action.get("name", "")
            action_desc = action.get("short_description", action.get("description", ""))
            risk_tier = action.get("risk_tier", "medium")

            # Keyword relevance scoring
            searchable = f"{service_name} {service_desc} {action_name} {action_desc}".lower()
            if query_lower:
                terms = query_lower.split()
                matched = sum(1 for t in terms if t in searchable)
                if matched == 0:
                    continue
                relevance = round(matched / len(terms), 2)
            else:
                relevance = 1.0

            results.append({
                "service_id": service_id,
                "action_id": action_id,
                "name": action_name,
                "short_description": action_desc[:200] if action_desc else "",
                "risk_tier": risk_tier,
                "relevance": relevance,
            })

    # Sort by relevance descending, then alphabetically
    results.sort(key=lambda r: (-r["relevance"], r["service_id"], r["action_id"]))
    results = results[:max_results]

    hint = "Use cafe.get_details with a service_id to see full input schemas before invoking."
    if not results and query:
        hint = "No services matched your query. Try different keywords or use an empty query to browse all available services."
    response = {
        "results": results,
        "total_matched": len(results),
        "hint": hint,
    }
    await _log_mcp_request(
        "cafe.search",
        query=query or None,
        category=category or None,
        result_count=len(results),
        latency_ms=int((time.monotonic() - t0) * 1000),
    )
    return response


# ---------------------------------------------------------------------------
# Tool 2: cafe.get_details — Full Menu entry for a specific service
# ---------------------------------------------------------------------------

@mcp_server.tool(name="cafe.get_details")
async def cafe_get_details(
    service_id: str,
    action_id: str = "",
) -> dict[str, Any]:
    """Get full details for a service: required inputs, constraints, and risk tiers.

    Call this before cafe.invoke to understand what inputs are required
    and what authorization level is needed.

    Args:
        service_id: The service to look up (from cafe.search results).
        action_id: Optional — filter to a single action within the service.
    """
    t0 = time.monotonic()
    db = await get_db()
    menu = await get_full_menu(db)

    service = next(
        (s for s in menu.get("services", []) if s.get("service_id") == service_id),
        None,
    )
    if service is None:
        await _log_mcp_request(
            "cafe.get_details", service_id=service_id, outcome="error",
            error_code="service_not_found", latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return {"error": "service_not_found", "message": f"No service '{service_id}' found on the Menu."}

    if action_id:
        action = next(
            (a for a in service.get("actions", []) if a.get("action_id") == action_id),
            None,
        )
        if action is None:
            await _log_mcp_request(
                "cafe.get_details", service_id=service_id, action_id=action_id,
                outcome="error", error_code="action_not_found",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return {
                "error": "action_not_found",
                "message": f"No action '{action_id}' found in service '{service_id}'.",
                "available_actions": [a.get("action_id") for a in service.get("actions", [])],
            }
        await _log_mcp_request(
            "cafe.get_details", service_id=service_id, action_id=action_id,
            result_count=1, latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return {
            "service_id": service_id,
            "service_name": service.get("name", ""),
            "action": action,
        }

    action_count = len(service.get("actions", []))
    await _log_mcp_request(
        "cafe.get_details", service_id=service_id, result_count=action_count,
        latency_ms=int((time.monotonic() - t0) * 1000),
    )
    return {
        "service_id": service_id,
        "service": service,
    }


# ---------------------------------------------------------------------------
# Tool 3: cafe.request_card — Initiate a Company Card request
# ---------------------------------------------------------------------------

@mcp_server.tool(name="cafe.request_card")
async def cafe_request_card(
    service_id: str,
    passport: str,
    suggested_scope: list[str] | None = None,
    suggested_budget_cents: int | None = None,
    suggested_duration_days: int | None = None,
) -> dict[str, Any]:
    """Request standing authorization (Company Card) to use a service repeatedly.

    The human approves asynchronously. Once approved, you can invoke
    actions on this service without per-request consent.

    Args:
        service_id: The service to request a card for.
        passport: Your valid AgentCafe Passport token (Tier-1 or Tier-2).
        suggested_scope: Optional list of action_ids to suggest for the card scope.
        suggested_budget_cents: Optional suggested budget in cents.
        suggested_duration_days: Optional suggested duration in days (1-365).
    """
    t0 = time.monotonic()
    try:
        _validate_agent_passport(f"Bearer {passport}")
    except HTTPException as exc:
        await _log_mcp_request(
            "cafe.request_card", service_id=service_id, passport=passport,
            outcome="error", error_code=exc.detail.get("error", "auth_failed"),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return {"error": exc.detail.get("error", "auth_failed"), "message": "Invalid or expired Passport."}

    req = CardRequestBody(
        service_id=service_id,
        suggested_scope=suggested_scope,
        suggested_budget_cents=suggested_budget_cents,
        suggested_duration_days=suggested_duration_days,
    )

    try:
        response = await _cards_request_card(req, authorization=f"Bearer {passport}")
        await _log_mcp_request(
            "cafe.request_card", service_id=service_id, passport=passport,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return response.model_dump()
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        await _log_mcp_request(
            "cafe.request_card", service_id=service_id, passport=passport,
            outcome="error", error_code=detail.get("error", "request_failed"),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return {"error": detail.get("error", "request_failed"), **detail}


# ---------------------------------------------------------------------------
# Tool 4: cafe.invoke — Execute a service action via the Cafe proxy
# ---------------------------------------------------------------------------

async def _find_active_card(service_id: str, action_id: str):
    """Find an active Company Card that covers the given service+action.

    Returns the card row or None.
    """
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """SELECT * FROM company_cards
           WHERE service_id = ? AND status = 'active' AND expires_at > ?
           ORDER BY created_at DESC LIMIT 1""",
        (service_id, now),
    )
    card = await cursor.fetchone()
    if not card:
        return None

    # Check action is not excluded
    if card["excluded_action_ids"]:
        excluded = set(card["excluded_action_ids"].split(","))
        if action_id in excluded:
            return None

    # Check action is in allowed list (if set)
    if card["allowed_action_ids"]:
        allowed = set(card["allowed_action_ids"].split(","))
        if action_id not in allowed:
            return None

    # Check first-use confirmation
    if card["first_use_confirmation"] and not card["first_use_confirmed_at"]:
        return None

    return card


async def _auto_resolve_card_token(
    service_id: str, action_id: str, passport: str,
) -> str | None:
    """Try to get a Tier-2 token from an active card. Returns token or None."""
    card = await _find_active_card(service_id, action_id)
    if not card:
        return None

    try:
        token_resp = await _get_card_token(
            card_id=card["id"],
            req=CardTokenRequest(action_id=action_id),
            authorization=f"Bearer {passport}",
        )
        return token_resp.token
    except HTTPException:
        return None


@mcp_server.tool(name="cafe.invoke")
async def cafe_invoke(
    service_id: str,
    action_id: str,
    passport: str,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a real-world action through an external service.

    All authorization, consent, and policy enforcement is handled automatically.
    If you have an approved Company Card, the token upgrade happens transparently.
    Use cafe.get_details first to check required inputs.

    Args:
        service_id: The service to call.
        action_id: The specific action to perform.
        passport: Your valid AgentCafe Passport token.
        inputs: The action inputs (required fields depend on the action — use cafe.get_details to check).
    """
    t0 = time.monotonic()
    req = OrderRequest(
        service_id=service_id,
        action_id=action_id,
        passport=passport,
        inputs=inputs or {},
    )

    try:
        result = await place_order(req)
        await _log_mcp_request(
            "cafe.invoke", service_id=service_id, action_id=action_id,
            passport=passport, latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return result if isinstance(result, dict) else {"result": result}
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        error_code = detail.get("error", "invocation_failed")

        # --- Auto-resolve: try to upgrade via an active Company Card ---
        if error_code in ("human_auth_required", "tier_insufficient", "scope_missing"):
            tier2_token = await _auto_resolve_card_token(
                service_id, action_id, passport,
            )
            if tier2_token:
                # Retry with the Tier-2 token
                retry_req = OrderRequest(
                    service_id=service_id,
                    action_id=action_id,
                    passport=tier2_token,
                    inputs=inputs or {},
                )
                try:
                    result = await place_order(retry_req)
                    await _log_mcp_request(
                        "cafe.invoke", service_id=service_id,
                        action_id=action_id, passport=passport,
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    return result if isinstance(result, dict) else {"result": result}
                except HTTPException as retry_exc:
                    # Retry failed — fall through to error handling
                    detail = retry_exc.detail if isinstance(retry_exc.detail, dict) else {"message": str(retry_exc.detail)}
                    error_code = detail.get("error", "invocation_failed")

            # No card or card token failed — return actionable error
            if not tier2_token:
                ms = int((time.monotonic() - t0) * 1000)
                await _log_mcp_request(
                    "cafe.invoke", service_id=service_id,
                    action_id=action_id, passport=passport,
                    outcome="auth_required", error_code=error_code,
                    latency_ms=ms,
                )
                # Check if there's a pending card already
                db = await get_db()
                pending_cursor = await db.execute(
                    "SELECT id, activation_code FROM company_cards "
                    "WHERE service_id = ? AND status = 'pending' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (service_id,),
                )
                pending = await pending_cursor.fetchone()
                if pending:
                    return {
                        "error": "HUMAN_AUTH_REQUIRED",
                        "detail": detail.get("message", ""),
                        "card_id": pending["id"],
                        "activation_code": pending["activation_code"],
                        "hint": (
                            "A card request is already pending for this service. "
                            "Ask the human to approve it using the activation code above."
                        ),
                    }
                return {
                    "error": "HUMAN_AUTH_REQUIRED",
                    "detail": detail.get("message", ""),
                    "card_suggestion": detail.get("card_suggestion"),
                    "hint": "Use cafe.request_card to get standing authorization for this service.",
                }

        ms = int((time.monotonic() - t0) * 1000)
        await _log_mcp_request(
            "cafe.invoke", service_id=service_id, action_id=action_id,
            passport=passport, outcome="error", error_code=error_code,
            latency_ms=ms,
        )
        return {"error": error_code, **detail}
