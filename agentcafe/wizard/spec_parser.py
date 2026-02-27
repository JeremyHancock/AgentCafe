"""Spec Parser — validates and extracts structured data from OpenAPI specs.

Component 1 of the Onboarding Wizard. Pure Python, no LLM needed.
Accepts OpenAPI 3.0.x and 3.1.x in YAML or JSON format.
"""

from __future__ import annotations

import json
import logging
import re

from agentcafe.wizard.models import ParsedOperation, ParsedSpec

logger = logging.getLogger("agentcafe.wizard.spec_parser")

# HTTP methods that indicate write operations
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Keywords in operationId/summary that override write → read classification
_READ_KEYWORDS = re.compile(
    r"(search|list|browse|get|fetch|find|query|lookup|check|view|status)",
    re.IGNORECASE,
)


class SpecParseError(Exception):
    """Raised when the spec cannot be parsed or is invalid."""

    def __init__(self, message: str, line: int | None = None):
        self.message = message
        self.line = line
        super().__init__(message)


def _try_parse_yaml(raw: str) -> dict:
    """Try to parse as YAML (which also handles JSON)."""
    try:
        import yaml
    except ImportError as exc:
        raise SpecParseError(
            "PyYAML is required for YAML spec parsing. "
            "Install with: pip install 'agentcafe[wizard]'"
        ) from exc
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        line = None
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            line = mark.line + 1
        raise SpecParseError(
            f"YAML/JSON syntax error: {exc}", line=line
        ) from exc


def _detect_and_parse(raw_spec: str) -> dict:
    """Auto-detect format (JSON or YAML) and parse the spec string."""
    stripped = raw_spec.strip()
    if not stripped:
        raise SpecParseError("Empty spec provided.")

    # Try JSON first (faster, no dependency)
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SpecParseError(
                f"JSON syntax error at line {exc.lineno}: {exc.msg}",
                line=exc.lineno,
            ) from exc

    # Fall back to YAML
    return _try_parse_yaml(stripped)


def _validate_openapi_version(spec: dict) -> list[str]:
    """Validate that the spec is OpenAPI 3.0.x or 3.1.x. Returns warnings."""
    warnings: list[str] = []

    openapi_version = spec.get("openapi", "")
    swagger_version = spec.get("swagger", "")

    if swagger_version:
        raise SpecParseError(
            f"We support OpenAPI 3.0.x and 3.1.x. Your spec appears to be "
            f"Swagger {swagger_version}. Convert it using https://converter.swagger.io/"
        )

    if not openapi_version:
        raise SpecParseError(
            "Missing 'openapi' version field. We support OpenAPI 3.0.x and 3.1.x."
        )

    if not (openapi_version.startswith("3.0") or openapi_version.startswith("3.1")):
        raise SpecParseError(
            f"Unsupported OpenAPI version: {openapi_version}. "
            f"We support 3.0.x and 3.1.x."
        )

    return warnings


def _resolve_refs(node, root: dict, depth: int = 0):
    """Recursively resolve $ref pointers against the root spec.

    Only handles internal references (#/components/...). Circular refs
    are broken by a depth limit.
    """
    if depth > 15:
        return node

    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref and isinstance(ref, str) and ref.startswith("#/"):
            parts = ref.lstrip("#/").split("/")
            target = root
            for part in parts:
                if isinstance(target, dict):
                    target = target.get(part)
                else:
                    return node  # unresolvable
            if target is None:
                return node
            return _resolve_refs(target, root, depth + 1)
        return {k: _resolve_refs(v, root, depth + 1) for k, v in node.items()}

    if isinstance(node, list):
        return [_resolve_refs(item, root, depth + 1) for item in node]

    return node


def _classify_write(method: str, operation_id: str, summary: str) -> bool:
    """Classify whether an operation is a write (mutating) action."""
    if method.upper() not in _WRITE_METHODS:
        return False
    # Override: POST/PUT/etc. that are actually reads
    text = f"{operation_id} {summary}"
    if _READ_KEYWORDS.search(text):
        return False
    return True


def _extract_required_inputs(operation: dict, method: str) -> list[dict]:
    """Extract required input parameters from an operation."""
    inputs: list[dict] = []

    # Path and query parameters
    for param in operation.get("parameters", []):
        if param.get("required", False) or param.get("in") == "path":
            schema = param.get("schema", {})
            inputs.append({
                "name": param.get("name", ""),
                "description": param.get("description", ""),
                "example": param.get("example", schema.get("example")),
                "in": param.get("in", "query"),
                "schema": schema,
            })

    # Request body properties (for POST/PUT/PATCH)
    if method.upper() in {"POST", "PUT", "PATCH"}:
        request_body = operation.get("requestBody") or {}
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})

        # Direct properties
        required_props = set(schema.get("required", []))
        properties = schema.get("properties", {})

        for prop_name, prop_schema in properties.items():
            is_required = prop_name in required_props
            inputs.append({
                "name": prop_name,
                "description": prop_schema.get("description", ""),
                "example": prop_schema.get("example"),
                "in": "body",
                "schema": prop_schema,
                "required": is_required,
            })

    # Only return truly required inputs (path params are always required)
    return [inp for inp in inputs if inp.get("in") == "path" or inp.get("required", True)]


def _extract_example_response(operation: dict) -> dict:
    """Extract an example response from the 200/201 response definition."""
    responses = operation.get("responses", {})

    for status_code in ("200", "201", 200, 201):
        resp = responses.get(status_code)
        if resp is None:
            continue

        # Check for direct example
        content = resp.get("content", {})
        json_content = content.get("application/json", {})

        # example field
        if "example" in json_content:
            return json_content["example"]

        # examples field (first one)
        examples = json_content.get("examples", {})
        for ex in examples.values():
            if isinstance(ex, dict) and "value" in ex:
                return ex["value"]

        # Try to build a skeleton from schema
        schema = json_content.get("schema", {})
        if schema:
            return _schema_to_example(schema)

    return {}


def _schema_to_example(schema: dict, depth: int = 0) -> dict | list | str | int | bool | None:
    """Generate a minimal example from a JSON Schema (best effort)."""
    if depth > 3:
        return "..."

    schema_type = schema.get("type", "object")

    if "example" in schema:
        return schema["example"]

    if schema_type == "object":
        props = schema.get("properties", {})
        result = {}
        for name, prop in props.items():
            result[name] = _schema_to_example(prop, depth + 1)
        return result
    elif schema_type == "array":
        items = schema.get("items", {})
        return [_schema_to_example(items, depth + 1)]
    elif schema_type == "string":
        return schema.get("example", "string")
    elif schema_type == "integer":
        return schema.get("example", 0)
    elif schema_type == "number":
        return schema.get("example", 0.0)
    elif schema_type == "boolean":
        return schema.get("example", True)
    else:
        return None


def _extract_agentcafe_extensions(operation: dict) -> dict:
    """Extract x-agentcafe-* extensions from an operation."""
    extensions = {}
    for key, value in operation.items():
        if key.startswith("x-agentcafe-"):
            short_key = key.replace("x-agentcafe-", "")
            extensions[short_key] = value
    return extensions


def parse_openapi_spec(raw_spec: str) -> ParsedSpec:
    """Parse an OpenAPI spec string and extract structured operation data.

    Args:
        raw_spec: Raw YAML or JSON string of an OpenAPI 3.x spec.

    Returns:
        ParsedSpec with all operations extracted.

    Raises:
        SpecParseError: If the spec is invalid or cannot be parsed.
    """
    spec = _detect_and_parse(raw_spec)

    if not isinstance(spec, dict):
        raise SpecParseError("Spec must be a JSON/YAML object (not a list or scalar).")

    # Resolve all $ref pointers in-place before any extraction
    spec = _resolve_refs(spec, spec)

    warnings = _validate_openapi_version(spec)

    # Extract info
    info = spec.get("info", {})
    title = info.get("title", "Untitled API")
    version = info.get("version", "0.0.0")
    description = info.get("description", "")

    # Extract base URL
    servers = spec.get("servers", [])
    base_url = servers[0].get("url", "") if servers else ""

    # Extract operations
    paths = spec.get("paths", {})
    if not paths:
        raise SpecParseError(
            "Your spec has no paths/operations. At least one path with a method is required."
        )

    operations: list[ParsedOperation] = []
    operation_count = 0

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None or not isinstance(operation, dict):
                continue

            operation_count += 1
            op_id = operation.get("operationId", "")
            summary = operation.get("summary", "")
            op_desc = operation.get("description", "")
            is_write = _classify_write(method, op_id, summary)
            raw_params = operation.get("parameters", [])
            raw_body = operation.get("requestBody")
            raw_responses = operation.get("responses", {})

            # Check for x-agentcafe-* extensions
            extensions = _extract_agentcafe_extensions(operation)

            operations.append(ParsedOperation(
                path=path,
                method=method.upper(),
                operation_id=op_id,
                summary=summary,
                description=op_desc,
                is_write=is_write,
                raw_parameters=raw_params,
                raw_request_body=raw_body,
                raw_responses=raw_responses,
                preset_scope=extensions.get("scope"),
                preset_human_auth=extensions.get("human-auth"),
                preset_rate_limit=extensions.get("rate-limit"),
            ))

    if operation_count == 0:
        raise SpecParseError(
            "Your spec has paths but no HTTP methods defined in any of them."
        )

    if operation_count > 50:
        warnings.append(
            f"You have {operation_count} operations. We recommend starting with "
            f"your most important 5-10 and adding more later."
        )

    logger.info(
        "Parsed spec: %s v%s — %d operations (%d write, %d read)",
        title, version, len(operations),
        sum(1 for o in operations if o.is_write),
        sum(1 for o in operations if not o.is_write),
    )

    return ParsedSpec(
        title=title,
        version=version,
        description=description,
        base_url=base_url,
        operations=operations,
        raw_spec=spec,
        warnings=warnings,
    )
