"""Company Policy engine — rate limiting and input validation.

Enforces company-defined policies from proxy_configs on every order.
This is Gate 2 of the double validation (Gate 1 = Passport).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger("agentcafe.policy")

# ---------------------------------------------------------------------------
# Rate limiting — sliding window using audit_log
# ---------------------------------------------------------------------------

_RATE_LIMIT_PATTERN = re.compile(r"^(\d+)/(minute|hour|day)$")

_WINDOW_SECONDS = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}


def parse_rate_limit(rate_limit_str: str) -> tuple[int, int] | None:
    """Parse a rate limit string like '60/minute' into (max_requests, window_seconds).

    Returns None if the format is invalid.
    """
    match = _RATE_LIMIT_PATTERN.match(rate_limit_str)
    if not match:
        return None
    max_requests = int(match.group(1))
    window_seconds = _WINDOW_SECONDS[match.group(2)]
    return max_requests, window_seconds


async def check_rate_limit(
    db: aiosqlite.Connection,
    passport_hash: str,
    service_id: str,
    action_id: str,
    rate_limit_str: str,
    policy_id: str | None = None,
) -> tuple[bool, dict | None]:
    """Check if the request is within the rate limit.

    Counts recent audit_log entries for this passport + action within the
    sliding window. Returns (True, None) if allowed, or (False, error_detail)
    if the limit is exceeded.

    V2 error body per v2-spec.md §8.3: error, detail, retry_after_seconds, policy_id.
    """
    parsed = parse_rate_limit(rate_limit_str)
    if parsed is None:
        logger.warning("Invalid rate_limit format: %s — skipping enforcement", rate_limit_str)
        return True, None

    max_requests, window_seconds = parsed
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=window_seconds)).isoformat()

    cursor = await db.execute(
        """SELECT COUNT(*), MIN(timestamp) FROM audit_log
           WHERE passport_hash = ? AND service_id = ? AND action_id = ?
           AND timestamp > ?""",
        (passport_hash, service_id, action_id, cutoff),
    )
    row = await cursor.fetchone()
    count = row[0] if row else 0

    if count >= max_requests:
        # Calculate retry_after from oldest entry in the window
        oldest_ts = row[1] if row else None
        retry_after = window_seconds
        if oldest_ts:
            try:
                oldest = datetime.fromisoformat(oldest_ts)
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=timezone.utc)
                retry_after = max(1, int((oldest + timedelta(seconds=window_seconds) - now).total_seconds()))
            except (ValueError, TypeError):
                pass

        detail = f"Rate limit {rate_limit_str} exceeded for {service_id}:{action_id}."
        if policy_id:
            detail += f" Budget is shared across all tokens under policy {policy_id}."

        return False, {
            "error": "rate_limit_exceeded",
            "detail": detail,
            "retry_after_seconds": retry_after,
            "policy_id": policy_id,
            "limit": rate_limit_str,
            "current_count": count,
        }

    return True, None


# ---------------------------------------------------------------------------
# Input type validation — infers expected types from Menu example values
# ---------------------------------------------------------------------------

def _python_type_name(value) -> str:
    """Map a Python value to a simple type name for error messages."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


# Maps the explicit "type" field values to Python type-check functions.
# Uses JSON Schema type names (string, integer, number, boolean, array, object).
_TYPE_CHECKERS: dict[str, callable] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _infer_type_from_example(example) -> str | None:
    """Infer a type name from an example value (fallback when 'type' is absent)."""
    if isinstance(example, bool):
        return "boolean"
    if isinstance(example, int):
        return "integer"
    if isinstance(example, float):
        return "number"
    if isinstance(example, str):
        return "string"
    if isinstance(example, list):
        return "array"
    if isinstance(example, dict):
        return "object"
    return None


def validate_input_types(
    inputs: dict,
    required_inputs: list[dict],
) -> tuple[bool, list[str] | None]:
    """Validate that provided input values match expected types from the Menu schema.

    Uses the explicit `type` field if present (e.g., "string", "integer", "array").
    Falls back to inferring from the `example` field if `type` is absent.
    Only validates inputs that are present AND have a resolvable type.
    Returns (True, None) if all types match, or (False, error_list) with details.
    """
    errors = []

    for spec in required_inputs:
        name = spec.get("name")
        if name is None or name not in inputs:
            continue

        # Resolve expected type: explicit 'type' field preferred, fall back to example
        expected_type = spec.get("type")
        if expected_type is None:
            example = spec.get("example")
            if example is None:
                continue
            expected_type = _infer_type_from_example(example)
            if expected_type is None:
                continue

        checker = _TYPE_CHECKERS.get(expected_type)
        if checker is None:
            continue

        actual = inputs[name]
        if not checker(actual):
            actual_type = _python_type_name(actual)
            errors.append(
                f"'{name}': expected {expected_type}, got {actual_type}"
            )

    if errors:
        return False, errors
    return True, None
