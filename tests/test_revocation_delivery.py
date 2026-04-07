"""Tests for revocation push delivery (PR 2).

Covers:
- queue_revocation: grant state transition + delivery row creation
- deliver_revocation: success, failure, max retries
- attempt_pending_deliveries: backoff schedule, filtering
- queue_jv_revocation: high-level helper, standard-mode no-op, fan-out
- Integration config: MVS hard-coded config
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import aiosqlite

from agentcafe.cafe.integration import (
    _BACKOFF_SCHEDULE,
    _MAX_ATTEMPTS,
    _backoff_seconds,
    attempt_pending_deliveries,
    deliver_revocation,
    get_integration_config,
    queue_jv_revocation,
    queue_revocation,
)

# pylint: disable=redefined-outer-name,protected-access

NOW_ISO = datetime.now(timezone.utc).isoformat()
TEST_USER_ID = str(uuid.uuid4())
TEST_SERVICE = "human-memory"
TEST_CONSENT_REF = f"policy-{uuid.uuid4()}"


async def _init_test_db() -> aiosqlite.Connection:
    """Create an in-memory DB with tables needed for revocation tests."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS cafe_users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS proxy_configs (
            id TEXT PRIMARY KEY,
            service_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            backend_url TEXT NOT NULL,
            backend_path TEXT NOT NULL,
            backend_method TEXT NOT NULL DEFAULT 'POST',
            backend_auth_header TEXT DEFAULT '',
            scope TEXT NOT NULL DEFAULT 'read',
            human_auth_required INTEGER DEFAULT 0,
            rate_limit TEXT DEFAULT '60/minute',
            risk_tier TEXT DEFAULT 'medium',
            human_identifier_field TEXT,
            created_at TEXT NOT NULL,
            quarantine_until TEXT,
            suspended_at TEXT,
            integration_mode TEXT DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS authorization_grants (
            id TEXT PRIMARY KEY,
            ac_human_id TEXT NOT NULL,
            service_id TEXT NOT NULL,
            consent_ref TEXT NOT NULL,
            grant_status TEXT NOT NULL DEFAULT 'active',
            granted_at TEXT NOT NULL,
            revoked_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(consent_ref, service_id)
        );

        CREATE TABLE IF NOT EXISTS revocation_deliveries (
            id TEXT PRIMARY KEY,
            consent_ref TEXT NOT NULL,
            service_id TEXT NOT NULL,
            correlation_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            delivered_at TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS service_integration_configs (
            service_id TEXT PRIMARY KEY,
            integration_base_url TEXT NOT NULL,
            integration_auth_header TEXT NOT NULL DEFAULT '',
            identity_matching TEXT NOT NULL DEFAULT 'opaque_id',
            has_direct_signup INTEGER NOT NULL DEFAULT 0,
            cap_account_check INTEGER NOT NULL DEFAULT 0,
            cap_account_create INTEGER NOT NULL DEFAULT 0,
            cap_link_complete INTEGER NOT NULL DEFAULT 0,
            cap_unlink INTEGER NOT NULL DEFAULT 0,
            cap_revoke INTEGER NOT NULL DEFAULT 1,
            cap_grant_status INTEGER NOT NULL DEFAULT 0,
            path_revoke TEXT,
            configured_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_rd_correlation
            ON revocation_deliveries(correlation_id);
    """)
    await db.commit()
    return db


async def _seed_grant(
    db: aiosqlite.Connection,
    *,
    user_id: str = TEST_USER_ID,
    service_id: str = TEST_SERVICE,
    consent_ref: str = TEST_CONSENT_REF,
    grant_status: str = "active",
) -> None:
    """Insert an authorization_grants row."""
    await db.execute(
        """INSERT OR REPLACE INTO authorization_grants
           (id, ac_human_id, service_id, consent_ref, grant_status,
            granted_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, service_id, consent_ref,
         grant_status, NOW_ISO, NOW_ISO),
    )
    await db.commit()


async def _seed_proxy_config(
    db: aiosqlite.Connection,
    *,
    service_id: str = TEST_SERVICE,
    integration_mode: str | None = "jointly_verified",
) -> None:
    """Insert a proxy_configs row."""
    await db.execute(
        """INSERT INTO proxy_configs
           (id, service_id, action_id, backend_url, backend_path, backend_method,
            scope, created_at, integration_mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), service_id, "store",
         "http://localhost:9000", "/memory/store", "POST", "write", NOW_ISO,
         integration_mode),
    )
    await db.commit()


@pytest_asyncio.fixture
async def test_db():
    """Fresh in-memory DB for each test."""
    db = await _init_test_db()
    yield db
    await db.close()


# ---------------------------------------------------------------------------
# Integration config
# ---------------------------------------------------------------------------

class TestIntegrationConfig:
    """Tests for get_integration_config."""

    @pytest.mark.asyncio
    async def test_returns_hm_config(self):
        """Returns config for human-memory (fallback, no db)."""
        config = await get_integration_config("human-memory")
        assert config is not None
        assert config["service_id"] == "human-memory"
        assert config["capabilities"]["revoke"] is True

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown(self):
        """Returns None for unknown service."""
        assert await get_integration_config("unknown-service") is None


# ---------------------------------------------------------------------------
# queue_revocation
# ---------------------------------------------------------------------------

class TestQueueRevocation:
    """Tests for queue_revocation."""

    @pytest.mark.asyncio
    async def test_creates_delivery_row(self, test_db):
        """Creates a revocation_deliveries row with queued status."""
        await _seed_grant(test_db)
        correlation_id = await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT * FROM revocation_deliveries WHERE correlation_id = ?",
            (correlation_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "queued"
        assert row["attempts"] == 0
        assert row["consent_ref"] == TEST_CONSENT_REF
        assert row["service_id"] == TEST_SERVICE

    @pytest.mark.asyncio
    async def test_generates_rev_prefixed_correlation_id(self, test_db):
        """Correlation ID starts with 'rev_'."""
        await _seed_grant(test_db)
        correlation_id = await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        assert correlation_id.startswith("rev_")

    @pytest.mark.asyncio
    async def test_transitions_grant_to_revoke_queued(self, test_db):
        """Grant status transitions from active to revoke_queued."""
        await _seed_grant(test_db)
        await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT grant_status, revoked_at FROM authorization_grants "
            "WHERE consent_ref = ? AND service_id = ?",
            (TEST_CONSENT_REF, TEST_SERVICE),
        )
        row = await cursor.fetchone()
        assert row["grant_status"] == "revoke_queued"
        assert row["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_does_not_transition_already_revoked(self, test_db):
        """Already-revoked grant is not affected."""
        await _seed_grant(test_db, grant_status="revoke_queued")
        await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT grant_status FROM authorization_grants "
            "WHERE consent_ref = ? AND service_id = ?",
            (TEST_CONSENT_REF, TEST_SERVICE),
        )
        row = await cursor.fetchone()
        assert row["grant_status"] == "revoke_queued"


# ---------------------------------------------------------------------------
# deliver_revocation
# ---------------------------------------------------------------------------

def _mock_response(status_code: int = 200, json_data: dict | None = None):
    """Create a mock httpx.Response."""
    if json_data is None:
        json_data = {"acknowledged": True, "correlation_id": "rev_test"}
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://test/integration/revoke"),
    )


class TestDeliverRevocation:
    """Tests for deliver_revocation."""

    @pytest.mark.asyncio
    async def test_successful_delivery(self, test_db):
        """Successful delivery updates status to delivered and grant to revoke_delivered."""
        await _seed_grant(test_db)
        correlation_id = await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT id FROM revocation_deliveries WHERE correlation_id = ?",
            (correlation_id,),
        )
        delivery_id = (await cursor.fetchone())["id"]

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await deliver_revocation(test_db, delivery_id)

        assert result is True

        # Check delivery status
        cursor = await test_db.execute(
            "SELECT status, delivered_at, attempts FROM revocation_deliveries WHERE id = ?",
            (delivery_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None
        assert row["attempts"] == 1

        # Check grant status
        cursor = await test_db.execute(
            "SELECT grant_status FROM authorization_grants "
            "WHERE consent_ref = ? AND service_id = ?",
            (TEST_CONSENT_REF, TEST_SERVICE),
        )
        grant = await cursor.fetchone()
        assert grant["grant_status"] == "revoke_delivered"

    @pytest.mark.asyncio
    async def test_failed_delivery_increments_attempts(self, test_db):
        """Failed delivery increments attempts, keeps queued status."""
        await _seed_grant(test_db)
        correlation_id = await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT id FROM revocation_deliveries WHERE correlation_id = ?",
            (correlation_id,),
        )
        delivery_id = (await cursor.fetchone())["id"]

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(500, {"error": "internal"})
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await deliver_revocation(test_db, delivery_id)

        assert result is False

        cursor = await test_db.execute(
            "SELECT status, attempts, error_message FROM revocation_deliveries WHERE id = ?",
            (delivery_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "queued"
        assert row["attempts"] == 1
        assert "500" in row["error_message"]

    @pytest.mark.asyncio
    async def test_connection_error(self, test_db):
        """Connection error is handled gracefully."""
        await _seed_grant(test_db)
        correlation_id = await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT id FROM revocation_deliveries WHERE correlation_id = ?",
            (correlation_id,),
        )
        delivery_id = (await cursor.fetchone())["id"]

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await deliver_revocation(test_db, delivery_id)

        assert result is False

        cursor = await test_db.execute(
            "SELECT error_message FROM revocation_deliveries WHERE id = ?",
            (delivery_id,),
        )
        row = await cursor.fetchone()
        assert "Connection" in row["error_message"]

    @pytest.mark.asyncio
    async def test_max_retries_marks_failed(self, test_db):
        """After max retries, delivery status becomes 'failed'."""
        await _seed_grant(test_db)
        correlation_id = await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT id FROM revocation_deliveries WHERE correlation_id = ?",
            (correlation_id,),
        )
        delivery_id = (await cursor.fetchone())["id"]

        # Set attempts to MAX - 1
        await test_db.execute(
            "UPDATE revocation_deliveries SET attempts = ? WHERE id = ?",
            (_MAX_ATTEMPTS - 1, delivery_id),
        )
        await test_db.commit()

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(500, {"error": "internal"})
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await deliver_revocation(test_db, delivery_id)

        assert result is False

        cursor = await test_db.execute(
            "SELECT status, attempts FROM revocation_deliveries WHERE id = ?",
            (delivery_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "failed"
        assert row["attempts"] == _MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_nonexistent_delivery(self, test_db):
        """Returns False for nonexistent delivery ID."""
        result = await deliver_revocation(test_db, "nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_capability_returns_false(self, test_db):
        """Returns False if service has no revoke capability."""
        # Create delivery for an unknown service
        await test_db.execute(
            """INSERT INTO revocation_deliveries
               (id, consent_ref, service_id, correlation_id, status, attempts, created_at)
               VALUES (?, ?, 'unknown-service', 'rev_test', 'queued', 0, ?)""",
            ("del-1", TEST_CONSENT_REF, NOW_ISO),
        )
        await test_db.commit()

        result = await deliver_revocation(test_db, "del-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_acknowledged_false_is_failure(self, test_db):
        """Service returning acknowledged:false is treated as failure."""
        await _seed_grant(test_db)
        correlation_id = await queue_revocation(
            test_db, TEST_CONSENT_REF, TEST_SERVICE, "human_revoked",
        )
        await test_db.commit()

        cursor = await test_db.execute(
            "SELECT id FROM revocation_deliveries WHERE correlation_id = ?",
            (correlation_id,),
        )
        delivery_id = (await cursor.fetchone())["id"]

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(200, {"acknowledged": False})
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await deliver_revocation(test_db, delivery_id)

        assert result is False


# ---------------------------------------------------------------------------
# Backoff schedule
# ---------------------------------------------------------------------------

class TestBackoff:
    """Tests for backoff schedule."""

    def test_first_attempt(self):
        """First retry waits 5 seconds."""
        assert _backoff_seconds(0) == 5

    def test_schedule_progression(self):
        """Backoff follows the defined schedule."""
        assert _backoff_seconds(0) == _BACKOFF_SCHEDULE[0]
        assert _backoff_seconds(1) == _BACKOFF_SCHEDULE[1]
        assert _backoff_seconds(2) == _BACKOFF_SCHEDULE[2]
        assert _backoff_seconds(3) == _BACKOFF_SCHEDULE[3]
        assert _backoff_seconds(4) == _BACKOFF_SCHEDULE[4]

    def test_clamps_at_max(self):
        """Beyond schedule length, clamps to last value."""
        assert _backoff_seconds(100) == _BACKOFF_SCHEDULE[-1]


# ---------------------------------------------------------------------------
# attempt_pending_deliveries
# ---------------------------------------------------------------------------

class TestAttemptPendingDeliveries:
    """Tests for attempt_pending_deliveries."""

    @pytest.mark.asyncio
    async def test_skips_delivered(self, test_db):
        """Delivered rows are not retried."""
        await test_db.execute(
            """INSERT INTO revocation_deliveries
               (id, consent_ref, service_id, correlation_id, status, attempts,
                delivered_at, created_at)
               VALUES (?, ?, ?, ?, 'delivered', 1, ?, ?)""",
            ("del-1", TEST_CONSENT_REF, TEST_SERVICE, "rev_1", NOW_ISO, NOW_ISO),
        )
        await test_db.commit()

        count = await attempt_pending_deliveries(test_db)
        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_failed(self, test_db):
        """Failed rows are not retried."""
        await test_db.execute(
            """INSERT INTO revocation_deliveries
               (id, consent_ref, service_id, correlation_id, status, attempts, created_at)
               VALUES (?, ?, ?, ?, 'failed', 10, ?)""",
            ("del-1", TEST_CONSENT_REF, TEST_SERVICE, "rev_1", NOW_ISO),
        )
        await test_db.commit()

        count = await attempt_pending_deliveries(test_db)
        assert count == 0

    @pytest.mark.asyncio
    async def test_respects_backoff(self, test_db):
        """Queued row within backoff period is skipped."""
        recent = datetime.now(timezone.utc).isoformat()
        await test_db.execute(
            """INSERT INTO revocation_deliveries
               (id, consent_ref, service_id, correlation_id, status, attempts,
                last_attempt_at, created_at)
               VALUES (?, ?, ?, ?, 'queued', 1, ?, ?)""",
            ("del-1", TEST_CONSENT_REF, TEST_SERVICE, "rev_1", recent, NOW_ISO),
        )
        await test_db.commit()

        # Attempt 1 has 15s backoff — should be skipped since last_attempt_at is just now
        count = await attempt_pending_deliveries(test_db)
        assert count == 0

    @pytest.mark.asyncio
    async def test_retries_eligible(self, test_db):
        """Queued row past backoff period is retried."""
        await _seed_grant(test_db)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        await test_db.execute(
            """INSERT INTO revocation_deliveries
               (id, consent_ref, service_id, correlation_id, status, attempts,
                last_attempt_at, created_at)
               VALUES (?, ?, ?, ?, 'queued', 1, ?, ?)""",
            ("del-1", TEST_CONSENT_REF, TEST_SERVICE, "rev_1", old, NOW_ISO),
        )
        await test_db.commit()

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            count = await attempt_pending_deliveries(test_db)

        assert count == 1


# ---------------------------------------------------------------------------
# queue_jv_revocation (high-level helper)
# ---------------------------------------------------------------------------

class TestQueueJvRevocation:
    """Tests for queue_jv_revocation."""

    @pytest.mark.asyncio
    async def test_no_grant_is_noop(self, test_db):
        """No active grant for consent_ref is a no-op."""
        await queue_jv_revocation(test_db, "nonexistent-ref", "human_revoked")

        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM revocation_deliveries",
        )
        assert (await cursor.fetchone())["cnt"] == 0

    @pytest.mark.asyncio
    async def test_standard_mode_is_noop(self, test_db):
        """Standard-mode service grant does not trigger delivery."""
        await _seed_grant(test_db, service_id="standard-service")
        # No integration config for standard-service → skipped
        await queue_jv_revocation(test_db, TEST_CONSENT_REF, "human_revoked")

        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM revocation_deliveries",
        )
        assert (await cursor.fetchone())["cnt"] == 0

    @pytest.mark.asyncio
    async def test_queues_for_jv_service(self, test_db):
        """Jointly-verified service gets a queued delivery."""
        await _seed_grant(test_db)

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await queue_jv_revocation(test_db, TEST_CONSENT_REF, "human_revoked")

        # Grant should be revoke_queued or revoke_delivered
        cursor = await test_db.execute(
            "SELECT grant_status FROM authorization_grants "
            "WHERE consent_ref = ? AND service_id = ?",
            (TEST_CONSENT_REF, TEST_SERVICE),
        )
        grant = await cursor.fetchone()
        assert grant["grant_status"] in ("revoke_queued", "revoke_delivered")

        # Delivery row should exist
        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM revocation_deliveries "
            "WHERE consent_ref = ? AND service_id = ?",
            (TEST_CONSENT_REF, TEST_SERVICE),
        )
        assert (await cursor.fetchone())["cnt"] == 1

    @pytest.mark.asyncio
    async def test_inline_delivery_attempted(self, test_db):
        """Inline delivery is attempted immediately after queuing."""
        await _seed_grant(test_db)

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await queue_jv_revocation(test_db, TEST_CONSENT_REF, "human_revoked")

            # Should have been called (inline delivery attempt)
            assert mock_client.post.called

    @pytest.mark.asyncio
    async def test_inline_failure_leaves_queued(self, test_db):
        """Failed inline delivery leaves status as queued for retry."""
        await _seed_grant(test_db)

        with patch("agentcafe.cafe.integration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await queue_jv_revocation(test_db, TEST_CONSENT_REF, "human_revoked")

        cursor = await test_db.execute(
            "SELECT status FROM revocation_deliveries WHERE consent_ref = ?",
            (TEST_CONSENT_REF,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "queued"
