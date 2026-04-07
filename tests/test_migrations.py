"""Tests for the schema migration system."""

from __future__ import annotations

import pytest
import pytest_asyncio
import aiosqlite

from agentcafe.db.models import SCHEMA_SQL
from agentcafe.db.migrate import get_current_version, discover_migrations, run_migrations

# pylint: disable=redefined-outer-name


@pytest_asyncio.fixture
async def fresh_db():
    """Create an in-memory database with baseline schema (no migrations)."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_current_version_fresh_db(fresh_db):
    """A fresh DB with no migrations should report version 0."""
    version = await get_current_version(fresh_db)
    assert version == 0


@pytest.mark.asyncio
async def test_discover_migrations_finds_files():
    """discover_migrations should find at least one .sql file."""
    migrations = discover_migrations()
    assert len(migrations) >= 1
    # All versions should be positive integers
    for ver, path in migrations:
        assert ver > 0
        assert path.suffix == ".sql"


@pytest.mark.asyncio
async def test_discover_migrations_sorted():
    """Migrations should be returned in ascending version order."""
    migrations = discover_migrations()
    versions = [v for v, _ in migrations]
    assert versions == sorted(versions)


@pytest.mark.asyncio
async def test_run_migrations_applies_all(fresh_db):
    """run_migrations should apply all pending migrations."""
    applied = await run_migrations(fresh_db)
    assert applied >= 1
    version = await get_current_version(fresh_db)
    assert version >= 1


@pytest.mark.asyncio
async def test_run_migrations_idempotent(fresh_db):
    """Running migrations twice should apply zero the second time."""
    first = await run_migrations(fresh_db)
    assert first >= 1
    second = await run_migrations(fresh_db)
    assert second == 0


@pytest.mark.asyncio
async def test_migration_0001_creates_policies_table(fresh_db):
    """Migration 0001 should create the policies table."""
    await run_migrations(fresh_db)
    cursor = await fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='policies'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "policies"


@pytest.mark.asyncio
async def test_policies_table_has_revoked_at_column(fresh_db):
    """The policies table should have a revoked_at column (for instant revocation)."""
    await run_migrations(fresh_db)
    cursor = await fresh_db.execute("PRAGMA table_info(policies)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert "revoked_at" in columns
    assert "cafe_user_id" in columns
    assert "risk_tier" in columns
