"""Logging configuration for AgentCafe.

Supports two output formats (controlled by ``CAFE_LOG_FORMAT``):

- ``text`` (default in dev): human-readable ``asctime [name] LEVEL: message``
- ``json`` (default in production): structured JSON with ``timestamp``,
  ``level``, ``logger``, ``message``, ``request_id``, plus any extra fields.

Both formats automatically include the current ``request_id`` from
:data:`agentcafe.middleware.request_id_var` when one is set.
"""

from __future__ import annotations

import logging

from pythonjsonlogger.json import JsonFormatter

from agentcafe.middleware import request_id_var


class _RequestIDFilter(logging.Filter):
    """Inject the current request ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("")  # type: ignore[attr-defined]
        return True


_TEXT_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s [rid=%(request_id)s]"
_JSON_FORMAT = "%(timestamp)s %(level)s %(name)s %(message)s"


def configure_logging(log_level: str = "INFO", log_format: str = "text") -> None:
    """Set up root logger with the chosen format and a request-ID filter."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (avoids duplicates on re-configure)
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setLevel(level)

    if log_format == "json":
        formatter = JsonFormatter(
            fmt=_JSON_FORMAT,
            rename_fields={"asctime": "timestamp", "levelname": "level"},
            timestamp=True,
        )
    else:
        formatter = logging.Formatter(_TEXT_FORMAT)

    handler.setFormatter(formatter)
    handler.addFilter(_RequestIDFilter())
    root.addHandler(handler)
