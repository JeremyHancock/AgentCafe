"""Database connection and initialization for AgentCafe."""

from __future__ import annotations

import aiosqlite

from agentcafe.db.models import SCHEMA_SQL


_db: aiosqlite.Connection | None = None


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Initialize the database connection and create tables."""
    global _db
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA_SQL)
    await _db.commit()
    return _db


async def get_db() -> aiosqlite.Connection:
    """Get the active database connection."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
