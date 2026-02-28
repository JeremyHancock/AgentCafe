"""Review Engine — draft management and editing for the Onboarding Wizard.

Component 3 of the Onboarding Wizard. Manages draft_services in SQLite,
handles company edits, and produces the final validated Menu entry.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite

from agentcafe.crypto import encrypt
from agentcafe.wizard.models import (
    CandidateAction,
    CandidateMenuEntry,
    ParsedSpec,
    PolicyAction,
    PreviewResponse,
)

logger = logging.getLogger("agentcafe.wizard.review_engine")


# ---------------------------------------------------------------------------
# Draft CRUD operations
# ---------------------------------------------------------------------------

async def create_draft(
    db: aiosqlite.Connection,
    company_id: str,
    parsed_spec: ParsedSpec,
    candidate_menu: CandidateMenuEntry,
    raw_spec_text: str,
) -> str:
    """Create a new draft_service and return its ID."""
    draft_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO draft_services
           (id, company_id, wizard_step, raw_spec_text, parsed_spec_json,
            candidate_menu_json, created_at, updated_at)
           VALUES (?, ?, 2, ?, ?, ?, ?, ?)""",
        (
            draft_id,
            company_id,
            raw_spec_text,
            parsed_spec.model_dump_json(),
            candidate_menu.model_dump_json(),
            now,
            now,
        ),
    )
    await db.commit()
    logger.info("Created draft %s for company %s", draft_id, company_id)
    return draft_id


async def get_draft(db: aiosqlite.Connection, draft_id: str) -> dict | None:
    """Fetch a draft by ID. Returns all columns as a dict, or None."""
    cursor = await db.execute(
        "SELECT * FROM draft_services WHERE id = ?", (draft_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def save_review(
    db: aiosqlite.Connection,
    draft_id: str,
    service_id: str,
    name: str,
    category: str,
    capability_tags: list[str],
    description: str,
    actions: list[CandidateAction],
    excluded_actions: list[str],
) -> None:
    """Save the company's Step 3 review edits to the draft."""
    now = datetime.now(timezone.utc).isoformat()

    # Build the edited candidate menu
    edited_menu = CandidateMenuEntry(
        service_id=service_id,
        name=name,
        category=category,
        capability_tags=capability_tags,
        description=description,
        actions=actions,
    )

    await db.execute(
        """UPDATE draft_services
           SET wizard_step = 3,
               company_edits_json = ?,
               excluded_actions = ?,
               updated_at = ?
           WHERE id = ?""",
        (
            edited_menu.model_dump_json(),
            json.dumps(excluded_actions),
            now,
            draft_id,
        ),
    )
    await db.commit()
    logger.info("Saved review edits for draft %s", draft_id)


async def save_policy(
    db: aiosqlite.Connection,
    draft_id: str,
    actions_policy: dict[str, PolicyAction],
    backend_url: str,
    backend_auth_header: str,
) -> None:
    """Save the company's Step 4 policy settings to the draft."""
    now = datetime.now(timezone.utc).isoformat()

    policy_json = json.dumps({
        action_id: policy.model_dump()
        for action_id, policy in actions_policy.items()
    })

    await db.execute(
        """UPDATE draft_services
           SET wizard_step = 4,
               policy_json = ?,
               backend_url = ?,
               backend_auth_header = ?,
               updated_at = ?
           WHERE id = ?""",
        (
            policy_json,
            backend_url,
            encrypt(backend_auth_header),
            now,
            draft_id,
        ),
    )
    await db.commit()
    logger.info("Saved policy for draft %s", draft_id)


async def generate_preview(
    db: aiosqlite.Connection,
    draft_id: str,
) -> PreviewResponse:
    """Generate the final Menu entry preview from a draft (Step 5).

    Combines the candidate menu with policy settings into the locked Menu format.
    """
    draft = await get_draft(db, draft_id)
    if draft is None:
        raise ValueError(f"Draft {draft_id} not found")

    # Prefer company edits over original AI-generated candidate
    edits_json = draft.get("company_edits_json")
    candidate_menu = json.loads(edits_json if edits_json else draft["candidate_menu_json"])
    excluded = json.loads(draft["excluded_actions"] or "[]")
    policy_data = json.loads(draft["policy_json"] or "{}")
    backend_url = draft["backend_url"] or ""

    # Filter out excluded actions
    active_actions = [
        a for a in candidate_menu.get("actions", [])
        if a["action_id"] not in excluded
    ]

    # Build the locked Menu entry format
    menu_actions = []
    proxy_configs = []

    for action in active_actions:
        action_id = action["action_id"]
        policy = policy_data.get(action_id, {})

        # Build cost object
        scope = policy.get("scope", f"{candidate_menu['service_id']}:{action_id}")
        human_auth = policy.get("human_auth", action.get("is_write", False))
        rate_limit = policy.get("rate_limit", action.get("suggested_rate_limit", "60/minute"))

        cost = {
            "required_scopes": [scope],
            "human_authorization_required": human_auth,
            "limits": {"rate_limit": rate_limit},
        }

        # Build required_inputs in locked format
        required_inputs = []
        for inp in action.get("required_inputs", []):
            entry = {
                "name": inp["name"],
                "description": inp.get("description", ""),
                "example": inp.get("example"),
            }
            if inp.get("type"):
                entry["type"] = inp["type"]
            required_inputs.append(entry)

        menu_actions.append({
            "action_id": action_id,
            "description": action.get("description", ""),
            "required_inputs": required_inputs,
            "example_response": action.get("example_response", {}),
            "cost": cost,
        })

        # Build proxy config
        source_path = action.get("source_path", f"/{action_id}")
        source_method = action.get("source_method", "POST")

        proxy_configs.append({
            "service_id": candidate_menu["service_id"],
            "action_id": action_id,
            "backend_url": backend_url,
            "backend_path": source_path,
            "backend_method": source_method,
            "scope": scope,
            "human_auth_required": human_auth,
            "rate_limit": rate_limit,
        })

    final_menu_entry = {
        "service_id": candidate_menu["service_id"],
        "name": candidate_menu["name"],
        "category": candidate_menu.get("category", ""),
        "capability_tags": candidate_menu.get("capability_tags", []),
        "description": candidate_menu.get("description", ""),
        "actions": menu_actions,
    }

    # Save the final preview to draft
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE draft_services
           SET wizard_step = 5,
               final_menu_json = ?,
               updated_at = ?
           WHERE id = ?""",
        (json.dumps(final_menu_entry), now, draft_id),
    )
    await db.commit()

    return PreviewResponse(
        final_menu_entry=final_menu_entry,
        proxy_configs=proxy_configs,
    )
