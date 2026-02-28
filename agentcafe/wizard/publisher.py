"""Publisher — one-click publish from draft to live Menu + proxy config.

Component 4 of the Onboarding Wizard. Takes the finalized Menu entry
from a draft and writes it to published_services + proxy_configs in
a single atomic transaction.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite

from agentcafe.crypto import encrypt

from agentcafe.wizard.models import PublishResponse
from agentcafe.wizard.review_engine import get_draft

logger = logging.getLogger("agentcafe.wizard.publisher")


async def publish_draft(
    db: aiosqlite.Connection,
    draft_id: str,
) -> PublishResponse:
    """Publish a draft to the live Menu.

    Steps 3-4 of the publish sequence run in a single transaction:
    1. Validate final_menu_json exists
    2. Check service_id uniqueness
    3. INSERT into published_services
    4. INSERT proxy_configs for each action
    """
    draft = await get_draft(db, draft_id)
    if draft is None:
        raise ValueError(f"Draft {draft_id} not found")

    final_menu_json_str = draft.get("final_menu_json")
    if not final_menu_json_str:
        raise ValueError(
            f"Draft {draft_id} has no final menu entry. "
            "Complete the preview step (Step 5) first."
        )

    final_menu = json.loads(final_menu_json_str)
    service_id = final_menu["service_id"]
    name = final_menu["name"]
    description = final_menu.get("description", "")
    company_id = draft["company_id"]
    backend_url = draft.get("backend_url", "")
    backend_auth_header = encrypt(draft.get("backend_auth_header", ""))
    policy_data = json.loads(draft.get("policy_json") or "{}")

    # Check service_id uniqueness
    cursor = await db.execute(
        "SELECT 1 FROM published_services WHERE service_id = ?",
        (service_id,),
    )
    if await cursor.fetchone():
        raise ValueError(
            f"A service with ID '{service_id}' already exists on the Menu. "
            "Please choose a different service_id."
        )

    now = datetime.now(timezone.utc).isoformat()

    # --- Atomic transaction: publish service + proxy configs ---
    try:
        # Insert published service
        await db.execute(
            """INSERT INTO published_services
               (id, company_id, service_id, name, description, menu_entry_json,
                status, published_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'live', ?, ?)""",
            (
                str(uuid.uuid4()),
                company_id,
                service_id,
                name,
                description,
                json.dumps(final_menu),
                now,
                now,
            ),
        )

        # Build and insert proxy configs from the final menu + policy
        actions = final_menu.get("actions", [])
        candidate_menu = json.loads(draft.get("candidate_menu_json") or "{}")
        candidate_actions = {
            a["action_id"]: a
            for a in candidate_menu.get("actions", [])
        }

        actions_published = 0
        for action in actions:
            action_id = action["action_id"]

            # Get policy for this action
            policy = policy_data.get(action_id, {})
            scope = policy.get("scope", f"{service_id}:{action_id}")
            human_auth = policy.get("human_auth", False)
            rate_limit = policy.get("rate_limit", "60/minute")

            # Get backend path and method from candidate action source
            candidate = candidate_actions.get(action_id, {})
            backend_path = candidate.get("source_path", f"/{action_id}")
            backend_method = candidate.get("source_method", "POST")

            await db.execute(
                """INSERT INTO proxy_configs
                   (id, service_id, action_id, backend_url, backend_path,
                    backend_method, backend_auth_header, scope,
                    human_auth_required, rate_limit, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    service_id,
                    action_id,
                    backend_url,
                    backend_path,
                    backend_method,
                    backend_auth_header,
                    scope,
                    1 if human_auth else 0,
                    rate_limit,
                    now,
                ),
            )
            actions_published += 1

        # Update draft to step 6 (published)
        await db.execute(
            """UPDATE draft_services
               SET wizard_step = 6, updated_at = ?
               WHERE id = ?""",
            (now, draft_id),
        )

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    logger.info(
        "Published service '%s' (%s) with %d actions",
        name, service_id, actions_published,
    )

    return PublishResponse(
        service_id=service_id,
        name=name,
        actions_published=actions_published,
        message=f"'{name}' is now live on the AgentCafe Menu with {actions_published} actions.",
    )
