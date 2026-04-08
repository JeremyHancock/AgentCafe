"""Tests for the Service Integration Standard (jointly-verified mode).

Covers:
- Artifact module: request hashing, artifact signing, human ID hashing
- Artifact key management: kid prefix, separate from Passport keys, JWKS merge
- Binding resolution: all grant/binding state combinations
- Router integration: jointly-verified proxy path, header injection, error translation
- Consent/cards integration: grant creation on approval
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
import jwt
import pytest
import pytest_asyncio
import aiosqlite

from agentcafe.cafe.artifact import (
    compute_request_hash,
    hash_human_id,
    sign_artifact,
)
from agentcafe.cafe.binding import BindingResult, resolve_binding, resolve_human_id
from agentcafe.keys import (
    configure_artifact_keys,
    configure_keys,
    get_artifact_key_manager,
    get_key_manager,
    sign_artifact_token,
)

# pylint: disable=redefined-outer-name,protected-access


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _init_test_db() -> aiosqlite.Connection:
    """Create an in-memory DB with the tables needed for SIS tests."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")

    # Minimal schema: cafe_users + SIS tables
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS cafe_users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS human_service_accounts (
            id TEXT PRIMARY KEY,
            ac_human_id TEXT NOT NULL REFERENCES cafe_users(id),
            service_id TEXT NOT NULL,
            service_account_id TEXT,
            binding_method TEXT NOT NULL,
            binding_status TEXT NOT NULL DEFAULT 'active',
            identity_binding TEXT NOT NULL,
            linked_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(ac_human_id, service_id)
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
    """)
    await db.commit()
    return db


NOW_ISO = datetime.now(timezone.utc).isoformat()
TEST_USER_ID = str(uuid.uuid4())
TEST_EMAIL = "alice@example.com"
TEST_SERVICE = "human-memory"
TEST_ACTION = "store-memory"
TEST_CONSENT_REF = f"policy-{uuid.uuid4()}"


async def _seed_user(db: aiosqlite.Connection) -> str:
    """Insert a test user and return user ID."""
    await db.execute(
        "INSERT OR IGNORE INTO cafe_users (id, email, display_name, created_at) VALUES (?, ?, ?, ?)",
        (TEST_USER_ID, TEST_EMAIL, "Alice", NOW_ISO),
    )
    await db.commit()
    return TEST_USER_ID


async def _seed_binding(
    db: aiosqlite.Connection,
    *,
    user_id: str = TEST_USER_ID,
    service_id: str = TEST_SERVICE,
    binding_status: str = "active",
    identity_binding: str = "broker_delegated",
) -> str:
    """Insert a human_service_accounts row and return the service_account_id."""
    service_account_id = user_id  # MVS pattern
    await db.execute(
        """INSERT OR REPLACE INTO human_service_accounts
           (id, ac_human_id, service_id, service_account_id,
            binding_method, binding_status, identity_binding,
            linked_at, updated_at)
           VALUES (?, ?, ?, ?, 'broker_delegated', ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, service_id, service_account_id,
         binding_status, identity_binding, NOW_ISO, NOW_ISO),
    )
    await db.commit()
    return service_account_id


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


@pytest_asyncio.fixture
async def test_db():
    """Fresh in-memory DB for each test."""
    db = await _init_test_db()
    yield db
    await db.close()


# ---------------------------------------------------------------------------
# Artifact module: compute_request_hash
# ---------------------------------------------------------------------------

class TestComputeRequestHash:
    """Tests for compute_request_hash."""

    def test_deterministic(self):
        """Same inputs produce the same hash."""
        h1, b1 = compute_request_hash("POST", "/api/store", {"key": "val"})
        h2, b2 = compute_request_hash("POST", "/api/store", {"key": "val"})
        assert h1 == h2
        assert b1 == b2

    def test_method_case_insensitive(self):
        """Method is uppercased internally."""
        h1, _ = compute_request_hash("post", "/api/store", {"key": "val"})
        h2, _ = compute_request_hash("POST", "/api/store", {"key": "val"})
        assert h1 == h2

    def test_different_method_different_hash(self):
        """Different HTTP methods produce different hashes."""
        h1, _ = compute_request_hash("GET", "/api/store", {"key": "val"})
        h2, _ = compute_request_hash("POST", "/api/store", {"key": "val"})
        assert h1 != h2

    def test_different_path_different_hash(self):
        """Different paths produce different hashes."""
        h1, _ = compute_request_hash("POST", "/api/store", {"key": "val"})
        h2, _ = compute_request_hash("POST", "/api/other", {"key": "val"})
        assert h1 != h2

    def test_trailing_slash_normalized(self):
        """Trailing slash on path is stripped."""
        h1, _ = compute_request_hash("POST", "/api/store/", {"key": "val"})
        h2, _ = compute_request_hash("POST", "/api/store", {"key": "val"})
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        """Different inputs produce different hashes."""
        h1, _ = compute_request_hash("POST", "/api/store", {"key": "a"})
        h2, _ = compute_request_hash("POST", "/api/store", {"key": "b"})
        assert h1 != h2

    def test_key_order_irrelevant(self):
        """Input key order does not affect hash (sort_keys=True)."""
        h1, _ = compute_request_hash("POST", "/api/store", {"b": 2, "a": 1})
        h2, _ = compute_request_hash("POST", "/api/store", {"a": 1, "b": 2})
        assert h1 == h2

    def test_empty_inputs(self):
        """Empty inputs dict produces a valid hash."""
        h, body = compute_request_hash("GET", "/api/list", {})
        assert len(h) == 64  # SHA-256 hex
        assert body == b"{}"

    def test_body_bytes_are_canonical_json(self):
        """Body bytes use compact JSON (no extra whitespace)."""
        _, body = compute_request_hash("POST", "/api/store", {"x": 1, "y": [2, 3]})
        parsed = json.loads(body)
        assert parsed == {"x": 1, "y": [2, 3]}
        # No spaces after separators
        assert b" " not in body

    def test_hash_is_sha256_hex(self):
        """Hash is 64-char hex string (SHA-256)."""
        h, _ = compute_request_hash("POST", "/api/store", {"k": "v"})
        assert len(h) == 64
        int(h, 16)  # should not raise


# ---------------------------------------------------------------------------
# Artifact module: hash_human_id
# ---------------------------------------------------------------------------

class TestHashHumanId:
    """Tests for hash_human_id."""

    def test_produces_sha256_hex(self):
        """Produces a 64-character hex digest."""
        result = hash_human_id("user-123")
        assert len(result) == 64
        assert result == hashlib.sha256(b"user-123").hexdigest()

    def test_deterministic(self):
        """Same input produces same output."""
        assert hash_human_id("abc") == hash_human_id("abc")

    def test_different_input_different_hash(self):
        """Different inputs produce different hashes."""
        assert hash_human_id("a") != hash_human_id("b")


# ---------------------------------------------------------------------------
# Artifact module: sign_artifact
# ---------------------------------------------------------------------------

class TestSignArtifact:
    """Tests for sign_artifact."""

    @pytest.fixture(autouse=True)
    def _setup_keys(self):
        """Ensure artifact keys are configured."""
        configure_artifact_keys()

    def test_produces_valid_jwt(self):
        """sign_artifact returns a JWT decodable with the artifact public key."""
        token = sign_artifact(
            service_id="svc-1",
            service_account_id="acct-1",
            action_id="store",
            consent_ref="policy-1",
            ac_human_id="user-1",
            identity_binding="broker_delegated",
            request_hash="a" * 64,
        )
        km = get_artifact_key_manager()
        claims = jwt.decode(
            token,
            km.current_key.public_key,
            algorithms=["RS256"],
            audience="svc-1",
        )
        assert claims["iss"] == "agentcafe"
        assert claims["aud"] == "svc-1"
        assert claims["sub"] == "acct-1"
        assert claims["action"] == "store"
        assert claims["consent_ref"] == "policy-1"
        assert claims["identity_binding"] == "broker_delegated"
        assert claims["request_hash"] == "a" * 64
        assert claims["standard_version"] == "1.0"
        assert claims["scopes"] == ["svc-1:store"]

    def test_30_second_ttl(self):
        """Artifact TTL is 30 seconds."""
        token = sign_artifact(
            service_id="s",
            service_account_id="a",
            action_id="x",
            consent_ref="c",
            ac_human_id="u",
            identity_binding="broker_delegated",
            request_hash="b" * 64,
        )
        claims = jwt.decode(
            token,
            get_artifact_key_manager().current_key.public_key,
            algorithms=["RS256"],
            audience="s",
            options={"verify_exp": False},
        )
        assert claims["exp"] - claims["iat"] == 30

    def test_custom_jti(self):
        """Custom jti is included in the token."""
        custom_jti = "my-custom-jti"
        token = sign_artifact(
            service_id="s",
            service_account_id="a",
            action_id="x",
            consent_ref="c",
            ac_human_id="u",
            identity_binding="broker_delegated",
            request_hash="c" * 64,
            jti=custom_jti,
        )
        claims = jwt.decode(
            token,
            get_artifact_key_manager().current_key.public_key,
            algorithms=["RS256"],
            audience="s",
        )
        assert claims["jti"] == custom_jti

    def test_auto_generated_jti(self):
        """Without explicit jti, a UUID is generated."""
        token = sign_artifact(
            service_id="s",
            service_account_id="a",
            action_id="x",
            consent_ref="c",
            ac_human_id="u",
            identity_binding="broker_delegated",
            request_hash="d" * 64,
        )
        claims = jwt.decode(
            token,
            get_artifact_key_manager().current_key.public_key,
            algorithms=["RS256"],
            audience="s",
        )
        uuid.UUID(claims["jti"])  # should not raise

    def test_human_id_is_hashed(self):
        """ac_human_id_hash claim is SHA-256 of the plain human ID."""
        human_id = "user-xyz"
        token = sign_artifact(
            service_id="s",
            service_account_id="a",
            action_id="x",
            consent_ref="c",
            ac_human_id=human_id,
            identity_binding="broker_delegated",
            request_hash="e" * 64,
        )
        claims = jwt.decode(
            token,
            get_artifact_key_manager().current_key.public_key,
            algorithms=["RS256"],
            audience="s",
        )
        assert claims["ac_human_id_hash"] == hash_human_id(human_id)


# ---------------------------------------------------------------------------
# Key management: artifact keys separate from Passport keys
# ---------------------------------------------------------------------------

class TestArtifactKeyManagement:
    """Tests for the artifact key manager."""

    def test_artifact_kid_has_prefix(self):
        """Artifact key IDs are prefixed with 'art_'."""
        configure_artifact_keys()
        km = get_artifact_key_manager()
        assert km.current_key.kid.startswith("art_")

    def test_passport_kid_no_prefix(self):
        """Passport key IDs have no prefix (backward compat)."""
        configure_keys()
        km = get_key_manager()
        assert not km.current_key.kid.startswith("art_")

    def test_separate_key_pairs(self):
        """Artifact and Passport key pairs are distinct."""
        configure_keys()
        configure_artifact_keys()
        passport_km = get_key_manager()
        artifact_km = get_artifact_key_manager()
        assert passport_km.current_key.kid != artifact_km.current_key.kid

    def test_sign_artifact_token_uses_artifact_key(self):
        """sign_artifact_token uses the artifact key, not Passport key."""
        configure_artifact_keys()
        payload = {"test": True, "iat": int(time.time()), "exp": int(time.time()) + 60}
        token = sign_artifact_token(payload)
        headers = jwt.get_unverified_header(token)
        assert headers["kid"].startswith("art_")

    def test_jwks_includes_both(self):
        """Both key sets can be merged for JWKS output."""
        configure_keys()
        configure_artifact_keys()
        passport_keys = get_key_manager().jwks()["keys"]
        artifact_keys = get_artifact_key_manager().jwks()["keys"]
        merged = passport_keys + artifact_keys
        kids = [k["kid"] for k in merged]
        assert any(k.startswith("art_") for k in kids), "Missing artifact key in JWKS"
        assert any(not k.startswith("art_") for k in kids), "Missing Passport key in JWKS"


# ---------------------------------------------------------------------------
# Binding resolution: resolve_human_id
# ---------------------------------------------------------------------------

class TestResolveHumanId:
    """Tests for resolve_human_id."""

    @pytest.mark.asyncio
    async def test_resolves_known_user(self, test_db):
        """Returns user ID for a known email."""
        await _seed_user(test_db)
        result = await resolve_human_id(test_db, f"user:{TEST_EMAIL}")
        assert result == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_rejects_non_user_subject(self, test_db):
        """Non-'user:' prefix raises 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await resolve_human_id(test_db, "agent:bot@example.com")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "invalid_passport_subject"

    @pytest.mark.asyncio
    async def test_rejects_unknown_email(self, test_db):
        """Unknown email raises 403."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await resolve_human_id(test_db, "user:unknown@example.com")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "human_not_found"


# ---------------------------------------------------------------------------
# Binding resolution: resolve_binding
# ---------------------------------------------------------------------------

class TestResolveBinding:
    """Tests for resolve_binding — all grant/binding state combinations."""

    @pytest.mark.asyncio
    async def test_active_binding_active_grant(self, test_db):
        """Active binding + active grant returns BindingResult."""
        await _seed_user(test_db)
        svc_acct = await _seed_binding(test_db)
        await _seed_grant(test_db)

        result = await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert isinstance(result, BindingResult)
        assert result.service_account_id == svc_acct
        assert result.identity_binding == "broker_delegated"

    @pytest.mark.asyncio
    async def test_revoked_grant(self, test_db):
        """Revoked grant raises 403 GRANT_REVOKED."""
        from fastapi import HTTPException
        await _seed_user(test_db)
        await _seed_binding(test_db)
        await _seed_grant(test_db, grant_status="revoke_queued")

        with pytest.raises(HTTPException) as exc_info:
            await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "grant_revoked"

    @pytest.mark.asyncio
    async def test_revoke_delivered_grant(self, test_db):
        """revoke_delivered grant also raises 403 GRANT_REVOKED."""
        from fastapi import HTTPException
        await _seed_user(test_db)
        await _seed_binding(test_db)
        await _seed_grant(test_db, grant_status="revoke_delivered")

        with pytest.raises(HTTPException) as exc_info:
            await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "grant_revoked"

    @pytest.mark.asyncio
    async def test_no_binding(self, test_db):
        """No binding row raises 403 ACCOUNT_LINK_REQUIRED."""
        from fastapi import HTTPException
        await _seed_user(test_db)
        await _seed_grant(test_db)

        with pytest.raises(HTTPException) as exc_info:
            await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "account_link_required"

    @pytest.mark.asyncio
    async def test_deferred_binding(self, test_db):
        """Deferred binding raises 503 SERVICE_SETUP_PENDING."""
        from fastapi import HTTPException
        await _seed_user(test_db)
        await _seed_binding(test_db, binding_status="deferred")
        await _seed_grant(test_db)

        with pytest.raises(HTTPException) as exc_info:
            await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "service_setup_pending"
        assert exc_info.value.detail["retry_after_seconds"] == 30

    @pytest.mark.asyncio
    async def test_unlinked_binding(self, test_db):
        """Unlinked binding raises 403 BINDING_INACTIVE."""
        from fastapi import HTTPException
        await _seed_user(test_db)
        await _seed_binding(test_db, binding_status="unlinked")
        await _seed_grant(test_db)

        with pytest.raises(HTTPException) as exc_info:
            await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "binding_inactive"

    @pytest.mark.asyncio
    async def test_active_binding_missing_grant(self, test_db):
        """Active binding but no grant raises 403 GRANT_NOT_FOUND."""
        from fastapi import HTTPException
        await _seed_user(test_db)
        await _seed_binding(test_db)
        # No grant seeded

        with pytest.raises(HTTPException) as exc_info:
            await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "grant_not_found"

    @pytest.mark.asyncio
    async def test_active_binding_pending_grant(self, test_db):
        """Active binding + non-active grant raises 403 GRANT_NOT_ACTIVE."""
        from fastapi import HTTPException
        await _seed_user(test_db)
        await _seed_binding(test_db)
        await _seed_grant(test_db, grant_status="pending")

        with pytest.raises(HTTPException) as exc_info:
            await resolve_binding(test_db, TEST_USER_ID, TEST_SERVICE, TEST_CONSENT_REF)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "grant_not_active"


# ---------------------------------------------------------------------------
# Consent integration: _handle_jointly_verified_consent
# ---------------------------------------------------------------------------

class TestConsentJointlyVerified:
    """Tests for _handle_jointly_verified_consent in consent.py."""

    @pytest.mark.asyncio
    async def test_standard_mode_is_noop(self, test_db):
        """Standard-mode service creates no binding or grant rows."""
        from agentcafe.cafe.consent import _handle_jointly_verified_consent
        await _seed_user(test_db)
        # Insert a standard-mode proxy config
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        await test_db.commit()

        await _handle_jointly_verified_consent(
            test_db, TEST_USER_ID, TEST_EMAIL, TEST_SERVICE, TEST_CONSENT_REF, [TEST_ACTION],
        )

        # No binding or grant rows should exist
        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM human_service_accounts WHERE ac_human_id = ?",
            (TEST_USER_ID,),
        )
        assert (await cursor.fetchone())["cnt"] == 0

        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM authorization_grants WHERE ac_human_id = ?",
            (TEST_USER_ID,),
        )
        assert (await cursor.fetchone())["cnt"] == 0

    @pytest.mark.asyncio
    async def test_jointly_verified_creates_binding_and_grant(self, test_db):
        """Jointly-verified service creates both binding and grant rows."""
        from agentcafe.cafe.consent import _handle_jointly_verified_consent
        await _seed_user(test_db)
        # Insert a jointly-verified proxy config
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'jointly_verified')""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        await test_db.commit()

        await _handle_jointly_verified_consent(
            test_db, TEST_USER_ID, TEST_EMAIL, TEST_SERVICE, TEST_CONSENT_REF, [TEST_ACTION],
        )

        # Binding should exist
        cursor = await test_db.execute(
            "SELECT * FROM human_service_accounts WHERE ac_human_id = ? AND service_id = ?",
            (TEST_USER_ID, TEST_SERVICE),
        )
        binding = await cursor.fetchone()
        assert binding is not None
        assert binding["binding_status"] == "active"
        assert binding["identity_binding"] == "broker_delegated"
        assert binding["service_account_id"] == TEST_USER_ID

        # Grant should exist
        cursor = await test_db.execute(
            "SELECT * FROM authorization_grants WHERE consent_ref = ? AND service_id = ?",
            (TEST_CONSENT_REF, TEST_SERVICE),
        )
        grant = await cursor.fetchone()
        assert grant is not None
        assert grant["grant_status"] == "active"

    @pytest.mark.asyncio
    async def test_idempotent_binding_creation(self, test_db):
        """Re-calling with existing active binding does not fail or duplicate."""
        from agentcafe.cafe.consent import _handle_jointly_verified_consent
        await _seed_user(test_db)
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'jointly_verified')""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        await test_db.commit()

        # Call twice with different consent_refs
        await _handle_jointly_verified_consent(
            test_db, TEST_USER_ID, TEST_EMAIL, TEST_SERVICE, TEST_CONSENT_REF, [TEST_ACTION],
        )
        consent_ref_2 = f"policy-{uuid.uuid4()}"
        await _handle_jointly_verified_consent(
            test_db, TEST_USER_ID, TEST_EMAIL, TEST_SERVICE, consent_ref_2, [TEST_ACTION],
        )

        # Only one binding row
        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM human_service_accounts WHERE ac_human_id = ?",
            (TEST_USER_ID,),
        )
        assert (await cursor.fetchone())["cnt"] == 1

        # Two grant rows (different consent_refs)
        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM authorization_grants WHERE ac_human_id = ?",
            (TEST_USER_ID,),
        )
        assert (await cursor.fetchone())["cnt"] == 2


# ---------------------------------------------------------------------------
# Cards integration: _create_jv_grant_if_needed
# ---------------------------------------------------------------------------

class TestCardsJointlyVerified:
    """Tests for _create_jv_grant_if_needed in cards.py."""

    @pytest.mark.asyncio
    async def test_standard_service_no_grant(self, test_db):
        """Standard-mode service creates no grant row."""
        from agentcafe.cafe.cards import _create_jv_grant_if_needed
        await _seed_user(test_db)
        # Standard proxy config
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        await test_db.commit()

        card_id = f"card-{uuid.uuid4()}"
        await _create_jv_grant_if_needed(test_db, TEST_USER_ID, TEST_SERVICE, card_id)

        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM authorization_grants WHERE consent_ref = ?",
            (card_id,),
        )
        assert (await cursor.fetchone())["cnt"] == 0

    @pytest.mark.asyncio
    async def test_jointly_verified_creates_grant(self, test_db):
        """Jointly-verified service creates a grant row with card_id as consent_ref."""
        from agentcafe.cafe.cards import _create_jv_grant_if_needed
        await _seed_user(test_db)
        # Jointly-verified proxy config
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'jointly_verified')""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        await test_db.commit()

        card_id = f"card-{uuid.uuid4()}"
        await _create_jv_grant_if_needed(test_db, TEST_USER_ID, TEST_SERVICE, card_id)

        cursor = await test_db.execute(
            "SELECT * FROM authorization_grants WHERE consent_ref = ? AND service_id = ?",
            (card_id, TEST_SERVICE),
        )
        grant = await cursor.fetchone()
        assert grant is not None
        assert grant["grant_status"] == "active"
        assert grant["ac_human_id"] == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_jointly_verified_creates_binding(self, test_db):
        """Jointly-verified card approval creates a human_service_accounts binding."""
        from agentcafe.cafe.cards import _create_jv_grant_if_needed
        await _seed_user(test_db)
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'jointly_verified')""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        await test_db.commit()

        card_id = f"card-{uuid.uuid4()}"
        await _create_jv_grant_if_needed(test_db, TEST_USER_ID, TEST_SERVICE, card_id)

        cursor = await test_db.execute(
            "SELECT * FROM human_service_accounts "
            "WHERE ac_human_id = ? AND service_id = ?",
            (TEST_USER_ID, TEST_SERVICE),
        )
        binding = await cursor.fetchone()
        assert binding is not None
        assert binding["binding_status"] == "active"
        assert binding["binding_method"] == "broker_delegated"
        assert binding["service_account_id"] == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_binding_reactivated_on_repeat(self, test_db):
        """Card approval reactivates an existing inactive binding."""
        from agentcafe.cafe.cards import _create_jv_grant_if_needed
        await _seed_user(test_db)
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'jointly_verified')""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        # Create an inactive binding
        await test_db.execute(
            """INSERT INTO human_service_accounts
               (id, ac_human_id, service_id, service_account_id,
                binding_method, binding_status, identity_binding,
                linked_at, updated_at)
               VALUES (?, ?, ?, ?, 'broker_delegated', 'unlinked', 'broker_delegated', ?, ?)""",
            (str(uuid.uuid4()), TEST_USER_ID, TEST_SERVICE, TEST_USER_ID, NOW_ISO, NOW_ISO),
        )
        await test_db.commit()

        card_id = f"card-{uuid.uuid4()}"
        await _create_jv_grant_if_needed(test_db, TEST_USER_ID, TEST_SERVICE, card_id)

        cursor = await test_db.execute(
            "SELECT binding_status FROM human_service_accounts "
            "WHERE ac_human_id = ? AND service_id = ?",
            (TEST_USER_ID, TEST_SERVICE),
        )
        assert (await cursor.fetchone())["binding_status"] == "active"

    @pytest.mark.asyncio
    async def test_idempotent_grant(self, test_db):
        """Calling twice with same card_id does not duplicate (INSERT OR IGNORE)."""
        from agentcafe.cafe.cards import _create_jv_grant_if_needed
        await _seed_user(test_db)
        await test_db.execute(
            """INSERT INTO proxy_configs
               (id, service_id, action_id, backend_url, backend_path, backend_method,
                scope, created_at, integration_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'jointly_verified')""",
            (str(uuid.uuid4()), TEST_SERVICE, TEST_ACTION,
             "http://localhost:9000", "/api/store", "POST", "write", NOW_ISO),
        )
        await test_db.commit()

        card_id = f"card-{uuid.uuid4()}"
        await _create_jv_grant_if_needed(test_db, TEST_USER_ID, TEST_SERVICE, card_id)
        await _create_jv_grant_if_needed(test_db, TEST_USER_ID, TEST_SERVICE, card_id)

        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM authorization_grants WHERE consent_ref = ?",
            (card_id,),
        )
        assert (await cursor.fetchone())["cnt"] == 1

        # Binding should also only have one row
        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM human_service_accounts "
            "WHERE ac_human_id = ? AND service_id = ?",
            (TEST_USER_ID, TEST_SERVICE),
        )
        assert (await cursor.fetchone())["cnt"] == 1


# ---------------------------------------------------------------------------
# Router: error translation
# ---------------------------------------------------------------------------

class TestServiceArtifactErrorTranslation:
    """Tests for _translate_service_artifact_error."""

    def test_artifact_expired_translates(self):
        """artifact_expired maps to 502 integration_error."""
        from agentcafe.cafe.router import _translate_service_artifact_error
        result = _translate_service_artifact_error(401, "artifact_expired")
        assert result is not None
        assert result["status"] == 502
        assert result["error"] == "integration_error"

    def test_request_hash_mismatch_translates(self):
        """request_hash_mismatch maps to 502."""
        from agentcafe.cafe.router import _translate_service_artifact_error
        result = _translate_service_artifact_error(401, "request_hash_mismatch")
        assert result is not None
        assert result["status"] == 502

    def test_artifact_replay_translates(self):
        """artifact_replay_detected maps to 409 duplicate_request."""
        from agentcafe.cafe.router import _translate_service_artifact_error
        result = _translate_service_artifact_error(409, "artifact_replay_detected")
        assert result is not None
        assert result["status"] == 409
        assert result["error"] == "duplicate_request"

    def test_unknown_error_returns_none(self):
        """Non-artifact error codes return None (pass-through)."""
        from agentcafe.cafe.router import _translate_service_artifact_error
        result = _translate_service_artifact_error(500, "internal_server_error")
        assert result is None

    def test_artifact_subject_unknown_translates(self):
        """artifact_subject_unknown maps to 403."""
        from agentcafe.cafe.router import _translate_service_artifact_error
        result = _translate_service_artifact_error(403, "artifact_subject_unknown")
        assert result is not None
        assert result["status"] == 403
        assert result["error"] == "account_not_found_on_service"


# ===========================================================================
# Database-driven get_integration_config tests
# ===========================================================================

class TestGetIntegrationConfigDB:
    """Tests for get_integration_config querying service_integration_configs."""

    @pytest.mark.asyncio
    async def test_returns_config_from_db(self):
        """Config stored in DB is returned with correct structure."""
        from agentcafe.cafe.integration import get_integration_config
        from agentcafe.crypto import encrypt
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        now = datetime.now(timezone.utc).isoformat()
        await db.executescript("""
            CREATE TABLE service_integration_configs (
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
        """)
        await db.execute(
            """INSERT INTO service_integration_configs
               (service_id, integration_base_url, integration_auth_header,
                identity_matching, has_direct_signup,
                cap_account_check, cap_account_create, cap_revoke,
                configured_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test-svc", "https://test.example.com", encrypt("Bearer secret"),
             "opaque_id", 0, 0, 1, 1, now, now),
        )
        await db.commit()

        config = await get_integration_config("test-svc", db)
        assert config is not None
        assert config["service_id"] == "test-svc"
        assert config["integration_base_url"] == "https://test.example.com"
        assert config["capabilities"]["account_create"] is True
        assert config["capabilities"]["revoke"] is True
        assert config["identity_matching"] == "opaque_id"
        await db.close()

    @pytest.mark.asyncio
    async def test_fallback_to_hm_without_db(self):
        """Without db, returns HM fallback."""
        from agentcafe.cafe.integration import get_integration_config
        config = await get_integration_config("human-memory")
        assert config is not None
        assert config["service_id"] == "human-memory"

    @pytest.mark.asyncio
    async def test_unknown_service_returns_none(self):
        """Unknown service returns None."""
        from agentcafe.cafe.integration import get_integration_config
        config = await get_integration_config("nonexistent-service")
        assert config is None
