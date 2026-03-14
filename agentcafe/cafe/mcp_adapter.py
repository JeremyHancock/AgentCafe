"""MCP Server Adapter — 4-tool LLM-native discovery pattern via Streamable HTTP.

Exposes AgentCafe's Menu, Company Cards, and order proxy as MCP tools.
MCP adapts to the Cafe — not the other way around (ADR-029).

Tools:
    cafe.search      — Semantic search across the Menu (summaries only)
    cafe.get_details — Full Menu entry for a specific service
    cafe.request_card — Initiate a Company Card request
    cafe.invoke      — Execute a service action via the Cafe proxy

See docs/strategy/strategic-review-briefing.md §8.2 and ADR-029.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from agentcafe.cafe.cards import (
    CardRequestBody,
    _validate_agent_passport,
    request_card as _cards_request_card,
)
from agentcafe.cafe.menu import get_full_menu
from agentcafe.cafe.router import OrderRequest, place_order
from agentcafe.db.engine import get_db

logger = logging.getLogger("agentcafe.mcp")

# ---------------------------------------------------------------------------
# MCP Server — stateless, Streamable HTTP
# ---------------------------------------------------------------------------

mcp_server = FastMCP(
    "AgentCafe",
    stateless_http=True,
    json_response=True,
)


# ---------------------------------------------------------------------------
# Tool 1: cafe.search — Semantic search across the Menu
# ---------------------------------------------------------------------------

@mcp_server.tool(name="cafe.search")
async def cafe_search(
    query: str = "",
    category: str = "",
    max_results: int = 10,
) -> dict[str, Any]:
    """Search AgentCafe's service catalog. Returns lightweight summaries only — use cafe.get_details for full schemas.

    Args:
        query: Natural language search query (e.g. "book a hotel in Miami").
        category: Optional category filter.
        max_results: Max results to return (1-20, default 10).
    """
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

    return {
        "results": results,
        "total_matched": len(results),
        "hint": "Use cafe.get_details with a service_id to see full input schemas before invoking.",
    }


# ---------------------------------------------------------------------------
# Tool 2: cafe.get_details — Full Menu entry for a specific service
# ---------------------------------------------------------------------------

@mcp_server.tool(name="cafe.get_details")
async def cafe_get_details(
    service_id: str,
    action_id: str = "",
) -> dict[str, Any]:
    """Get the full Menu entry for a service, including required inputs, constraints, and risk tiers.

    Args:
        service_id: The service to look up (from cafe.search results).
        action_id: Optional — filter to a single action within the service.
    """
    db = await get_db()
    menu = await get_full_menu(db)

    service = next(
        (s for s in menu.get("services", []) if s.get("service_id") == service_id),
        None,
    )
    if service is None:
        return {"error": "service_not_found", "message": f"No service '{service_id}' found on the Menu."}

    if action_id:
        action = next(
            (a for a in service.get("actions", []) if a.get("action_id") == action_id),
            None,
        )
        if action is None:
            return {
                "error": "action_not_found",
                "message": f"No action '{action_id}' found in service '{service_id}'.",
                "available_actions": [a.get("action_id") for a in service.get("actions", [])],
            }
        return {
            "service_id": service_id,
            "service_name": service.get("name", ""),
            "action": action,
        }

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
    """Request a Company Card for standing authorization with a service. The human approves asynchronously.

    Args:
        service_id: The service to request a card for.
        passport: Your valid AgentCafe Passport token (Tier-1 or Tier-2).
        suggested_scope: Optional list of action_ids to suggest for the card scope.
        suggested_budget_cents: Optional suggested budget in cents.
        suggested_duration_days: Optional suggested duration in days (1-365).
    """
    try:
        _validate_agent_passport(f"Bearer {passport}")
    except HTTPException as exc:
        return {"error": exc.detail.get("error", "auth_failed"), "message": "Invalid or expired Passport."}

    req = CardRequestBody(
        service_id=service_id,
        suggested_scope=suggested_scope,
        suggested_budget_cents=suggested_budget_cents,
        suggested_duration_days=suggested_duration_days,
    )

    try:
        response = await _cards_request_card(req, authorization=f"Bearer {passport}")
        return response.model_dump()
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return {"error": detail.get("error", "request_failed"), **detail}


# ---------------------------------------------------------------------------
# Tool 4: cafe.invoke — Execute a service action via the Cafe proxy
# ---------------------------------------------------------------------------

@mcp_server.tool(name="cafe.invoke")
async def cafe_invoke(
    service_id: str,
    action_id: str,
    passport: str,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke a service action through AgentCafe. All consent, card, and policy logic is enforced by the Cafe.

    Args:
        service_id: The service to call.
        action_id: The specific action to perform.
        passport: Your valid AgentCafe Passport token.
        inputs: The action inputs (required fields depend on the action — use cafe.get_details to check).
    """
    req = OrderRequest(
        service_id=service_id,
        action_id=action_id,
        passport=passport,
        inputs=inputs or {},
    )

    try:
        result = await place_order(req)
        return result if isinstance(result, dict) else {"result": result}
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        error_code = detail.get("error", "invocation_failed")

        # Structured HUMAN_AUTH_REQUIRED error per ADR-029
        if error_code in ("human_auth_required", "tier_insufficient", "scope_missing"):
            return {
                "error": "HUMAN_AUTH_REQUIRED",
                "detail": detail.get("message", ""),
                "card_suggestion": detail.get("card_suggestion"),
                "hint": "Use cafe.request_card to get standing authorization for this service.",
            }

        return {"error": error_code, **detail}
