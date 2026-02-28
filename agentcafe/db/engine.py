"""Database connection and initialization for AgentCafe."""

from __future__ import annotations

import aiosqlite

from agentcafe.db.migrate import run_migrations
from agentcafe.db.models import SCHEMA_SQL


class _State:
    """Module-level mutable state (avoids global statements)."""
    db: aiosqlite.Connection | None = None

_state = _State()


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Initialize the database connection, create tables, and run migrations."""
    _state.db = await aiosqlite.connect(db_path)
    _state.db.row_factory = aiosqlite.Row
    await _state.db.executescript(SCHEMA_SQL)
    await _state.db.commit()
    await run_migrations(_state.db)
    return _state.db


async def get_db() -> aiosqlite.Connection:
    """Get the active database connection."""
    if _state.db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _state.db


async def close_db() -> None:
    """Close the database connection."""
    if _state.db is not None:
        await _state.db.close()
        _state.db = None
