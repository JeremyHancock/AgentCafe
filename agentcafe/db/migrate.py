"""Lightweight schema migration runner for AgentCafe.

Applies numbered SQL migration files from the migrations/ directory.
Tracks applied versions in a `schema_version` table.
No external dependencies — pure aiosqlite.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger("agentcafe.migrate")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def get_current_version(db: aiosqlite.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    await db.executescript(_VERSION_TABLE_SQL)
    cursor = await db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
    row = await cursor.fetchone()
    return row[0]


def discover_migrations() -> list[tuple[int, Path]]:
    """Find all .sql migration files and return them sorted by version number.

    Filenames must start with a zero-padded number: 0001_description.sql
    """
    migrations: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            version = int(path.stem.split("_", 1)[0])
        except ValueError:
            logger.warning("Skipping non-numbered migration file: %s", path.name)
            continue
        migrations.append((version, path))
    return migrations


async def run_migrations(db: aiosqlite.Connection) -> int:
    """Apply all pending migrations. Returns the number of migrations applied."""
    current = await get_current_version(db)
    all_migrations = discover_migrations()
    pending = [(v, p) for v, p in all_migrations if v > current]

    if not pending:
        logger.debug("Schema up to date (version %d)", current)
        return 0

    applied = 0
    final_version = current
    for ver, path in pending:
        sql = path.read_text(encoding="utf-8")
        logger.info("Applying migration %04d: %s", ver, path.name)
        await db.executescript(sql)
        await db.execute(
            "INSERT INTO schema_version (version, filename) VALUES (?, ?)",
            (ver, path.name),
        )
        await db.commit()
        applied += 1
        final_version = ver

    logger.info("Applied %d migration(s). Schema now at version %d", applied, final_version)
    return applied
