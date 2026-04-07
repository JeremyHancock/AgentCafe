"""AI Enricher — transforms parsed operations into agent-friendly Menu entries.

Component 2 of the Onboarding Wizard. Uses LiteLLM when available,
falls back to rule-based generation when LiteLLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re

from agentcafe.wizard.models import (
    CandidateAction,
    CandidateInput,
    CandidateMenuEntry,
    ParsedSpec,
)
from agentcafe.wizard.spec_parser import _extract_required_inputs, _extract_example_response

logger = logging.getLogger("agentcafe.wizard.ai_enricher")

# ---------------------------------------------------------------------------
# LiteLLM configuration
# ---------------------------------------------------------------------------

ENRICHMENT_MODEL = os.getenv("ENRICHMENT_MODEL", "gpt-4o-mini")
ENRICHMENT_TEMPERATURE = 0.2
ENRICHMENT_MAX_TOKENS = 4000

ENRICHMENT_PROMPT = """
You are helping a company list their API on AgentCafe — a marketplace where AI agents
discover and use services.

Given the following parsed API spec, generate a Menu entry in the AgentCafe format.

Rules:
- service_id: lowercase slug in {{brand}}-{{category}} format (e.g., "stayright-hotels")
- name: clean display name (e.g., "StayRight Hotels")
- category: short lowercase category (e.g., "hotels", "food-delivery", "home-services")
- capability_tags: array of 3-5 lowercase discovery tags (e.g., ["travel", "booking", "accommodation"])
- description: 1-2 sentences describing what the service does, written for an AI agent
- For each operation, generate:
  - action_id: lowercase hyphenated slug (e.g., "search-availability")
  - description: clear sentence describing what this action accomplishes
  - required_inputs: array of {{name, description, example, type}} for each required parameter.
    type must be one of: string, integer, number, boolean, array, object
  - example_response: realistic JSON example based on the response schema
- Keep descriptions concise, factual, and agent-friendly
- Never include HTTP methods, paths, or technical implementation details

API Title: {title}
API Description: {description}
Operations:
{operations_json}

Return a JSON object with these exact keys:
{{
  "service_id": "...",
  "name": "...",
  "category": "...",
  "capability_tags": [...],
  "description": "...",
  "actions": [
    {{
      "action_id": "...",
      "description": "...",
      "required_inputs": [{{"name": "...", "description": "...", "example": ..., "type": "..."}}],
      "example_response": {{...}},
      "is_write": true/false
    }}
  ]
}}
"""


def _slugify(text: str) -> str:
    """Convert a string to a lowercase hyphenated slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def _operation_id_to_slug(op_id: str) -> str:
    """Convert an operationId like 'searchAvailability' to 'search-availability'."""
    if not op_id:
        return ""
    # Insert hyphens before uppercase letters (camelCase → kebab-case)
    slug = re.sub(r"([a-z])([A-Z])", r"\1-\2", op_id)
    return _slugify(slug)


def _infer_input_type(schema: dict, example) -> str:
    """Infer a JSON Schema type name from a schema dict or example value."""
    # Explicit schema type takes priority
    schema_type = schema.get("type", "")
    if schema_type in ("string", "integer", "number", "boolean", "array", "object"):
        return schema_type

    # Infer from example
    if isinstance(example, bool):
        return "boolean"
    if isinstance(example, int):
        return "integer"
    if isinstance(example, float):
        return "number"
    if isinstance(example, list):
        return "array"
    if isinstance(example, dict):
        return "object"
    return "string"


# ---------------------------------------------------------------------------
# Rule-based fallback enrichment (no LLM needed)
# ---------------------------------------------------------------------------

def _enrich_rule_based(parsed_spec: ParsedSpec) -> CandidateMenuEntry:
    """Generate a candidate Menu entry using rule-based heuristics.

    Used as a fallback when LiteLLM is unavailable or returns invalid output.
    """
    title = parsed_spec.title
    service_id = _slugify(title)
    name = title

    # Guess category from title/description
    text_lower = f"{title} {parsed_spec.description}".lower()
    category = "general"
    for cat_keyword, cat_name in [
        ("hotel", "hotels"), ("travel", "travel"), ("booking", "booking"),
        ("food", "food-delivery"), ("delivery", "food-delivery"),
        ("restaurant", "food-delivery"), ("lunch", "food-delivery"),
        ("home", "home-services"), ("plumb", "home-services"),
        ("clean", "home-services"), ("repair", "home-services"),
        ("payment", "payments"), ("finance", "finance"),
        ("health", "healthcare"), ("medical", "healthcare"),
    ]:
        if cat_keyword in text_lower:
            category = cat_name
            break

    actions: list[CandidateAction] = []

    for op in parsed_spec.operations:
        # Generate action_id from operationId or path+method
        action_id = _operation_id_to_slug(op.operation_id)
        if not action_id:
            action_id = _slugify(f"{op.method.lower()}-{op.path.strip('/').replace('/', '-')}")

        # Description from summary or operationId
        desc = op.summary or op.description or f"{op.method} {op.path}"

        # Extract inputs
        raw_inputs = _extract_required_inputs(
            {"parameters": op.raw_parameters, "requestBody": op.raw_request_body, "responses": op.raw_responses},
            op.method,
        )
        candidate_inputs: list[CandidateInput] = []
        for inp in raw_inputs:
            input_type = _infer_input_type(inp.get("schema", {}), inp.get("example"))
            candidate_inputs.append(CandidateInput(
                name=inp["name"],
                description=inp.get("description", ""),
                example=inp.get("example"),
                type=input_type,
            ))

        # Extract example response
        example_resp = _extract_example_response(
            {"responses": op.raw_responses}
        )

        # Scope and policy defaults
        scope = f"{service_id}:{action_id}"
        human_auth = op.is_write
        rate_limit = "10/minute" if op.is_write else "60/minute"

        # Use preset values if available (x-agentcafe-* extensions)
        if op.preset_scope is not None:
            scope = op.preset_scope
        if op.preset_human_auth is not None:
            human_auth = op.preset_human_auth
        if op.preset_rate_limit is not None:
            rate_limit = op.preset_rate_limit

        risk_tier = ""
        if op.preset_risk_tier is not None:
            risk_tier = op.preset_risk_tier
        elif op.is_write:
            risk_tier = "medium"
        else:
            risk_tier = "low"

        human_identifier_field = op.preset_human_identifier_field or ""

        actions.append(CandidateAction(
            action_id=action_id,
            description=desc,
            required_inputs=candidate_inputs,
            example_response=example_resp,
            suggested_scope=scope,
            suggested_human_auth=human_auth,
            suggested_rate_limit=rate_limit,
            suggested_risk_tier=risk_tier,
            suggested_human_identifier_field=human_identifier_field,
            is_write=op.is_write,
            confidence={"description": 0.6, "inputs": 0.8, "example_response": 0.4},
            source_path=op.path,
            source_method=op.method,
        ))

    return CandidateMenuEntry(
        service_id=service_id,
        name=name,
        category=category,
        capability_tags=[category],
        description=parsed_spec.description or f"API service: {title}",
        actions=actions,
        confidence={"service_id": 0.5, "name": 0.8, "description": 0.5},
    )


# ---------------------------------------------------------------------------
# LLM-based enrichment
# ---------------------------------------------------------------------------

async def _enrich_with_llm(parsed_spec: ParsedSpec) -> CandidateMenuEntry | None:
    """Try to enrich using LiteLLM. Returns None on failure."""
    try:
        import litellm
    except ImportError:
        logger.info("LiteLLM not installed — using rule-based enrichment")
        return None

    # Build operations summary for the prompt
    ops_summary = []
    for op in parsed_spec.operations:
        if len(op.raw_parameters) > 10:
            parsed_spec.warnings.append(
                f"Operation '{op.operation_id or op.path}' has {len(op.raw_parameters)} parameters. "
                "Consider splitting it into smaller operations."
            )
        ops_summary.append({
            "path": op.path,
            "method": op.method,
            "operationId": op.operation_id,
            "summary": op.summary,
            "description": op.description,
            "is_write": op.is_write,
            "parameters": op.raw_parameters,
        })

    prompt = ENRICHMENT_PROMPT.format(
        title=parsed_spec.title,
        description=parsed_spec.description,
        operations_json=json.dumps(ops_summary, indent=2),
    )

    try:
        response = await litellm.acompletion(
            model=ENRICHMENT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=ENRICHMENT_TEMPERATURE,
            max_tokens=ENRICHMENT_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        raw_json = json.loads(response.choices[0].message.content)
        return _validate_llm_output(raw_json, parsed_spec)

    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("LiteLLM enrichment failed: %s — falling back to rule-based", exc)
        return None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # litellm can raise many vendor-specific exception types not available at import time
        logger.warning("LiteLLM enrichment failed (unexpected): %s — falling back to rule-based", exc)
        return None


def _validate_llm_output(raw: dict, parsed_spec: ParsedSpec) -> CandidateMenuEntry | None:
    """Validate and convert LLM JSON output into a CandidateMenuEntry."""
    try:
        actions = []
        for action_data in raw.get("actions", []):
            inputs = []
            for inp in action_data.get("required_inputs", []):
                inputs.append(CandidateInput(
                    name=inp.get("name", ""),
                    description=inp.get("description", ""),
                    example=inp.get("example"),
                    type=inp.get("type", "string"),
                ))

            # Match back to source operation for metadata
            action_id = action_data.get("action_id", "")
            source_op = None
            for op in parsed_spec.operations:
                if _operation_id_to_slug(op.operation_id) == action_id:
                    source_op = op
                    break

            # Start with LLM suggestions, override with x-agentcafe-* presets
            scope = f"{raw.get('service_id', '')}:{action_id}"
            human_auth = action_data.get("is_write", False)
            rate_limit = "10/minute" if human_auth else "60/minute"
            risk_tier = "medium" if human_auth else "low"
            human_id_field = ""

            if source_op:
                if source_op.preset_scope is not None:
                    scope = source_op.preset_scope
                if source_op.preset_human_auth is not None:
                    human_auth = source_op.preset_human_auth
                if source_op.preset_rate_limit is not None:
                    rate_limit = source_op.preset_rate_limit
                if source_op.preset_risk_tier is not None:
                    risk_tier = source_op.preset_risk_tier
                if source_op.preset_human_identifier_field:
                    human_id_field = source_op.preset_human_identifier_field

            actions.append(CandidateAction(
                action_id=action_id,
                description=action_data.get("description", ""),
                required_inputs=inputs,
                example_response=action_data.get("example_response", {}),
                suggested_scope=scope,
                suggested_human_auth=human_auth,
                suggested_rate_limit=rate_limit,
                suggested_risk_tier=risk_tier,
                suggested_human_identifier_field=human_id_field,
                is_write=action_data.get("is_write", False),
                confidence={"description": 0.9, "inputs": 0.85, "example_response": 0.7},
                source_path=source_op.path if source_op else "",
                source_method=source_op.method if source_op else "",
            ))

        return CandidateMenuEntry(
            service_id=raw.get("service_id", ""),
            name=raw.get("name", ""),
            category=raw.get("category", ""),
            capability_tags=raw.get("capability_tags", []),
            description=raw.get("description", ""),
            actions=actions,
            confidence={"service_id": 0.9, "name": 0.9, "description": 0.9},
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.warning("Failed to validate LLM output: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def enrich_spec(parsed_spec: ParsedSpec) -> CandidateMenuEntry:
    """Transform a parsed spec into a candidate Menu entry.

    Tries LiteLLM first, falls back to rule-based generation.
    The result always needs human review (Step 3 of the wizard).
    """
    # Try LLM enrichment first
    result = await _enrich_with_llm(parsed_spec)
    if result is not None:
        logger.info("LLM enrichment succeeded for: %s", parsed_spec.title)
        return result

    # Fall back to rule-based
    logger.info("Using rule-based enrichment for: %s", parsed_spec.title)
    return _enrich_rule_based(parsed_spec)
