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
) -> tuple[bool, dict | None]:
    """Check if the request is within the rate limit.

    Counts recent audit_log entries for this passport + action within the
    sliding window. Returns (True, None) if allowed, or (False, error_detail)
    if the limit is exceeded.
    """
    parsed = parse_rate_limit(rate_limit_str)
    if parsed is None:
        logger.warning("Invalid rate_limit format: %s — skipping enforcement", rate_limit_str)
        return True, None

    max_requests, window_seconds = parsed
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()

    cursor = await db.execute(
        """SELECT COUNT(*) FROM audit_log
           WHERE passport_hash = ? AND service_id = ? AND action_id = ?
           AND timestamp > ?""",
        (passport_hash, service_id, action_id, cutoff),
    )
    row = await cursor.fetchone()
    count = row[0] if row else 0

    if count >= max_requests:
        return False, {
            "error": "rate_limit_exceeded",
            "message": f"Rate limit exceeded: {rate_limit_str}. Try again later.",
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


def _types_compatible(expected_example, actual_value) -> bool:
    """Check if actual_value is type-compatible with the expected example.

    Rules:
    - string example → actual must be a string
    - int/float example → actual must be int or float (not bool)
    - bool example → actual must be bool
    - list example → actual must be a list
    - dict example → actual must be a dict
    """
    if isinstance(expected_example, bool):
        return isinstance(actual_value, bool)
    if isinstance(expected_example, (int, float)):
        return isinstance(actual_value, (int, float)) and not isinstance(actual_value, bool)
    if isinstance(expected_example, str):
        return isinstance(actual_value, str)
    if isinstance(expected_example, list):
        return isinstance(actual_value, list)
    if isinstance(expected_example, dict):
        return isinstance(actual_value, dict)
    return True


def validate_input_types(
    inputs: dict,
    required_inputs: list[dict],
) -> tuple[bool, list[str] | None]:
    """Validate that provided input values match expected types from the Menu schema.

    Infers expected types from the `example` field in each required_input.
    Only validates inputs that are present AND have an example in the schema.
    Returns (True, None) if all types match, or (False, error_list) with details.
    """
    errors = []

    for spec in required_inputs:
        name = spec.get("name")
        example = spec.get("example")

        if name is None or example is None:
            continue
        if name not in inputs:
            continue

        actual = inputs[name]
        if not _types_compatible(example, actual):
            expected_type = _python_type_name(example)
            actual_type = _python_type_name(actual)
            errors.append(
                f"'{name}': expected {expected_type}, got {actual_type}"
            )

    if errors:
        return False, errors
    return True, None
