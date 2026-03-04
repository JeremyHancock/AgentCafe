"""Tests for WebAuthn passkey registration and login.

Since WebAuthn requires a real browser authenticator for the full ceremony,
these tests verify:
1. The begin endpoints return valid WebAuthn options
2. Challenge management (storage, expiry, consumption, type mismatch)
3. Password auth gating (ALLOW_PASSWORD_AUTH flag)
4. The full endpoint wiring and error handling
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

import agentcafe.cafe.human as human_module
from agentcafe.db.engine import get_db

# pylint: disable=redefined-outer-name,protected-access

TEST_SECRET = "test-secret-key-for-webauthn-testing!!"


@pytest_asyncio.fixture(autouse=True)
async def _configure_modules(monkeypatch):
    """Configure human module with test secrets and WebAuthn settings."""
    monkeypatch.setattr(human_module._state, "signing_secret", TEST_SECRET)
    monkeypatch.setattr(human_module._state, "webauthn_rp_id", "localhost")
    monkeypatch.setattr(human_module._state, "webauthn_rp_name", "AgentCafe Test")
    monkeypatch.setattr(human_module._state, "webauthn_origin", "http://localhost:8000")
    monkeypatch.setattr(human_module._state, "allow_password_auth", True)
    yield


# ---------------------------------------------------------------------------
# Passkey register/begin tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_register_begin_returns_options(cafe_client):
    """POST /human/passkey/register/begin returns WebAuthn creation options."""
    resp = await cafe_client.post("/human/passkey/register/begin", json={
        "email": f"passkey-{uuid.uuid4().hex[:8]}@test.com",
    })
    assert resp.status_code == 200
    data = resp.json()
    # Must contain standard WebAuthn fields
    assert "challenge" in data
    assert "rp" in data
    assert data["rp"]["id"] == "localhost"
    assert data["rp"]["name"] == "AgentCafe Test"
    assert "user" in data
    assert "pubKeyCredParams" in data
    # Must contain our challenge_id for the complete step
    assert "challenge_id" in data


@pytest.mark.asyncio
async def test_passkey_register_begin_duplicate_email(cafe_client):
    """POST /human/passkey/register/begin rejects duplicate email."""
    email = f"dup-{uuid.uuid4().hex[:8]}@test.com"
    # Register with password first
    resp = await cafe_client.post("/human/register", json={
        "email": email, "password": "testpassword123",
    })
    assert resp.status_code == 200

    # Now try passkey registration with same email
    resp2 = await cafe_client.post("/human/passkey/register/begin", json={
        "email": email,
    })
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["error"] == "email_exists"


# ---------------------------------------------------------------------------
# Passkey register/complete tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_register_complete_invalid_challenge(cafe_client):
    """POST /human/passkey/register/complete rejects unknown challenge_id."""
    resp = await cafe_client.post("/human/passkey/register/complete", json={
        "challenge_id": "nonexistent-challenge-id",
        "credential": {},
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "challenge_not_found"


@pytest.mark.asyncio
async def test_passkey_register_complete_expired_challenge(cafe_client):
    """POST /human/passkey/register/complete rejects expired challenge."""
    db = await get_db()
    challenge_id = str(uuid.uuid4())
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, user_id, email, display_name, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (challenge_id, "dGVzdA", None, "expired@test.com", None, "register", past, past),
    )
    await db.commit()

    resp = await cafe_client.post("/human/passkey/register/complete", json={
        "challenge_id": challenge_id,
        "credential": {},
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "challenge_expired"


@pytest.mark.asyncio
async def test_passkey_register_complete_type_mismatch(cafe_client):
    """POST /human/passkey/register/complete rejects login-type challenge."""
    db = await get_db()
    challenge_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=5)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, user_id, email, display_name, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (challenge_id, "dGVzdA", None, "mismatch@test.com", None, "login",
         now.isoformat(), future),
    )
    await db.commit()

    resp = await cafe_client.post("/human/passkey/register/complete", json={
        "challenge_id": challenge_id,
        "credential": {},
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "challenge_type_mismatch"


# ---------------------------------------------------------------------------
# Passkey login/begin tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_login_begin_unknown_email(cafe_client):
    """POST /human/passkey/login/begin returns 404 for unknown email."""
    resp = await cafe_client.post("/human/passkey/login/begin", json={
        "email": "nobody@nowhere.com",
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "user_not_found"


@pytest.mark.asyncio
async def test_passkey_login_begin_no_passkey(cafe_client):
    """POST /human/passkey/login/begin returns 400 for account without passkey."""
    email = f"nopasskey-{uuid.uuid4().hex[:8]}@test.com"
    # Register with password only
    await cafe_client.post("/human/register", json={
        "email": email, "password": "testpassword123",
    })

    resp = await cafe_client.post("/human/passkey/login/begin", json={
        "email": email,
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "no_passkey"


@pytest.mark.asyncio
async def test_passkey_login_begin_discoverable(cafe_client):
    """POST /human/passkey/login/begin without email returns options for discoverable credentials."""
    resp = await cafe_client.post("/human/passkey/login/begin", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "challenge" in data
    assert "challenge_id" in data
    assert "rpId" in data


# ---------------------------------------------------------------------------
# Passkey login/complete tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_login_complete_invalid_challenge(cafe_client):
    """POST /human/passkey/login/complete rejects unknown challenge_id."""
    resp = await cafe_client.post("/human/passkey/login/complete", json={
        "challenge_id": "nonexistent",
        "credential": {"id": "fake", "rawId": "fake", "response": {}, "type": "public-key"},
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "challenge_not_found"


@pytest.mark.asyncio
async def test_passkey_login_complete_unknown_credential(cafe_client):
    """POST /human/passkey/login/complete rejects unknown credential_id."""
    # Store a valid login challenge
    db = await get_db()
    challenge_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=5)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, user_id, email, display_name, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (challenge_id, "dGVzdA", None, None, None, "login", now.isoformat(), future),
    )
    await db.commit()

    resp = await cafe_client.post("/human/passkey/login/complete", json={
        "challenge_id": challenge_id,
        "credential": {"id": "unknown-cred", "rawId": "unknown-cred",
                        "response": {}, "type": "public-key"},
    })
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "credential_not_found"


# ---------------------------------------------------------------------------
# Password auth gating tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_auth_disabled_register(cafe_client, monkeypatch):
    """POST /human/register returns 403 when password auth is disabled."""
    monkeypatch.setattr(human_module._state, "allow_password_auth", False)
    resp = await cafe_client.post("/human/register", json={
        "email": "blocked@test.com", "password": "testpassword123",
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "password_auth_disabled"


@pytest.mark.asyncio
async def test_password_auth_disabled_login(cafe_client, monkeypatch):
    """POST /human/login returns 403 when password auth is disabled."""
    monkeypatch.setattr(human_module._state, "allow_password_auth", False)
    resp = await cafe_client.post("/human/login", json={
        "email": "blocked@test.com", "password": "testpassword123",
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "password_auth_disabled"


@pytest.mark.asyncio
async def test_password_auth_enabled_register_still_works(cafe_client):
    """POST /human/register works when password auth is enabled (default)."""
    email = f"pwok-{uuid.uuid4().hex[:8]}@test.com"
    resp = await cafe_client.post("/human/register", json={
        "email": email, "password": "testpassword123",
    })
    assert resp.status_code == 200
    assert resp.json()["email"] == email


# ---------------------------------------------------------------------------
# Challenge cleanup test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_expired_challenges(cafe_client):  # pylint: disable=unused-argument
    """cleanup_expired_challenges removes expired challenges."""
    db = await get_db()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    now_str = datetime.now(timezone.utc).isoformat()

    # Insert one expired and one valid challenge
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("expired-1", "dGVzdA", "register", now_str, past),
    )
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("valid-1", "dGVzdA", "register", now_str, future),
    )
    await db.commit()

    deleted = await human_module.cleanup_expired_challenges()
    assert deleted >= 1

    # Valid challenge should still exist
    cursor = await db.execute(
        "SELECT id FROM webauthn_challenges WHERE id = ?", ("valid-1",)
    )
    assert await cursor.fetchone() is not None

    # Expired challenge should be gone
    cursor2 = await db.execute(
        "SELECT id FROM webauthn_challenges WHERE id = ?", ("expired-1",)
    )
    assert await cursor2.fetchone() is None


# ---------------------------------------------------------------------------
# Challenge single-use test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_consumed_on_use(cafe_client):
    """A challenge can only be consumed once (single-use)."""
    db = await get_db()
    challenge_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=5)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, user_id, email, display_name, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (challenge_id, "dGVzdA", None, "single@test.com", None, "register",
         now.isoformat(), future),
    )
    await db.commit()

    # First attempt — consumes the challenge (will fail at verification, but challenge is gone)
    resp1 = await cafe_client.post("/human/passkey/register/complete", json={
        "challenge_id": challenge_id,
        "credential": {},
    })
    # Will fail at verification (no real credential), but challenge was consumed
    # The error will be verification_failed, not challenge_not_found
    assert resp1.status_code == 400

    # Second attempt — challenge is consumed, should get challenge_not_found
    resp2 = await cafe_client.post("/human/passkey/register/complete", json={
        "challenge_id": challenge_id,
        "credential": {},
    })
    assert resp2.status_code == 400
    assert resp2.json()["detail"]["error"] == "challenge_not_found"


# ---------------------------------------------------------------------------
# Sprint 4 — Grace period + enrollment tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_returns_passkey_enrolled_false(cafe_client):
    """Password login for a user with no passkey returns passkey_enrolled=False."""
    email = f"nopk-{uuid.uuid4().hex[:6]}@test.com"
    await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    resp = await cafe_client.post("/human/login", json={
        "email": email, "password": "secure-password-123",
    })
    assert resp.status_code == 200
    assert resp.json()["passkey_enrolled"] is False


@pytest.mark.asyncio
async def test_login_returns_passkey_enrolled_true_within_grace(cafe_client):
    """Password login for a user with a recent passkey returns passkey_enrolled=True."""
    email = f"haspk-{uuid.uuid4().hex[:6]}@test.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    user_id = reg.json()["user_id"]

    # Manually insert a passkey credential (recent, within grace period)
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO webauthn_credentials
           (id, user_id, credential_id, public_key, sign_count, device_name, created_at, last_used_at)
           VALUES (?, ?, ?, ?, 0, 'Test', ?, ?)""",
        (str(uuid.uuid4()), user_id, "cred-test-1", "pk-test-1", now, now),
    )
    await db.commit()

    resp = await cafe_client.post("/human/login", json={
        "email": email, "password": "secure-password-123",
    })
    assert resp.status_code == 200
    assert resp.json()["passkey_enrolled"] is True


@pytest.mark.asyncio
async def test_grace_period_blocks_password_login(cafe_client):
    """After grace period, password login is rejected for users with a passkey."""
    email = f"grace-{uuid.uuid4().hex[:6]}@test.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    user_id = reg.json()["user_id"]

    # Insert a passkey credential dated 8 days ago
    db = await get_db()
    old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_credentials
           (id, user_id, credential_id, public_key, sign_count, device_name, created_at, last_used_at)
           VALUES (?, ?, ?, ?, 0, 'Test', ?, ?)""",
        (str(uuid.uuid4()), user_id, "cred-old-1", "pk-old-1", old_date, old_date),
    )
    await db.commit()

    # Grace period is 7 days (default), passkey is 8 days old → should block
    resp = await cafe_client.post("/human/login", json={
        "email": email, "password": "secure-password-123",
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "password_login_disabled"


@pytest.mark.asyncio
async def test_grace_period_allows_login_within_window(cafe_client):
    """Within grace period, password login still works even with a passkey enrolled."""
    email = f"grace-ok-{uuid.uuid4().hex[:6]}@test.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    user_id = reg.json()["user_id"]

    # Insert a passkey credential dated 3 days ago (within 7-day grace)
    db = await get_db()
    recent_date = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_credentials
           (id, user_id, credential_id, public_key, sign_count, device_name, created_at, last_used_at)
           VALUES (?, ?, ?, ?, 0, 'Test', ?, ?)""",
        (str(uuid.uuid4()), user_id, "cred-recent-1", "pk-recent-1", recent_date, recent_date),
    )
    await db.commit()

    resp = await cafe_client.post("/human/login", json={
        "email": email, "password": "secure-password-123",
    })
    assert resp.status_code == 200
    assert resp.json()["passkey_enrolled"] is True


@pytest.mark.asyncio
async def test_enroll_begin_requires_session(cafe_client):
    """POST /human/passkey/enroll/begin rejects invalid session tokens."""
    resp = await cafe_client.post("/human/passkey/enroll/begin", json={
        "session_token": "invalid-token",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_enroll_begin_returns_options(cafe_client):
    """POST /human/passkey/enroll/begin returns WebAuthn options for existing user."""
    email = f"enroll-{uuid.uuid4().hex[:6]}@test.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    session_token = reg.json()["session_token"]

    resp = await cafe_client.post("/human/passkey/enroll/begin", json={
        "session_token": session_token,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "challenge" in data
    assert "challenge_id" in data
    assert "rp" in data
    assert data["rp"]["id"] == "localhost"


@pytest.mark.asyncio
async def test_enroll_complete_requires_matching_user(cafe_client):
    """POST /human/passkey/enroll/complete rejects if challenge user doesn't match session."""
    email = f"enroll-mismatch-{uuid.uuid4().hex[:6]}@test.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    session_token = reg.json()["session_token"]

    # Create a challenge with a different user_id
    db = await get_db()
    challenge_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=5)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, user_id, email, display_name, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (challenge_id, "dGVzdA", "wrong-user-id", email, None, "register",
         now.isoformat(), future),
    )
    await db.commit()

    resp = await cafe_client.post("/human/passkey/enroll/complete", json={
        "session_token": session_token,
        "challenge_id": challenge_id,
        "credential": {},
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "user_mismatch"


@pytest.mark.asyncio
async def test_enroll_passkey_page_requires_login(cafe_client):
    """GET /enroll-passkey redirects to login if no session."""
    cafe_client.cookies.clear()
    resp = await cafe_client.get("/enroll-passkey", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_enroll_passkey_page_renders_for_logged_in_user(cafe_client):
    """GET /enroll-passkey renders enrollment prompt for logged-in user."""
    email = f"enroll-page-{uuid.uuid4().hex[:6]}@test.com"
    await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    login_resp = await cafe_client.post("/human/login", json={
        "email": email, "password": "secure-password-123",
    })
    session_token = login_resp.json()["session_token"]

    resp = await cafe_client.get(
        "/enroll-passkey",
        cookies={"cafe_session": session_token},
    )
    assert resp.status_code == 200
    assert "passkey" in resp.text.lower()
    assert "Set Up Passkey" in resp.text


@pytest.mark.asyncio
async def test_page_login_redirects_to_enroll_when_no_passkey(cafe_client):
    """Page login for user without passkey redirects to /enroll-passkey."""
    email = f"page-enroll-{uuid.uuid4().hex[:6]}@test.com"
    await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })

    # Get CSRF from login page
    login_page = await cafe_client.get("/login")
    csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', login_page.text)
    csrf = csrf_match.group(1) if csrf_match else ""

    resp = await cafe_client.post("/login", data={
        "email": email,
        "password": "secure-password-123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "/enroll-passkey" in resp.headers["location"]


@pytest.mark.asyncio
async def test_page_login_grace_period_blocks(cafe_client):
    """Page login rejects password after grace period for users with passkey."""
    email = f"page-grace-{uuid.uuid4().hex[:6]}@test.com"
    reg = await cafe_client.post("/human/register", json={
        "email": email, "password": "secure-password-123",
    })
    user_id = reg.json()["user_id"]

    # Insert old passkey (8 days ago)
    db = await get_db()
    old_date = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    await db.execute(
        """INSERT INTO webauthn_credentials
           (id, user_id, credential_id, public_key, sign_count, device_name, created_at, last_used_at)
           VALUES (?, ?, ?, ?, 0, 'Test', ?, ?)""",
        (str(uuid.uuid4()), user_id, "cred-page-old", "pk-page-old", old_date, old_date),
    )
    await db.commit()

    login_page = await cafe_client.get("/login")
    csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', login_page.text)
    csrf = csrf_match.group(1) if csrf_match else ""

    resp = await cafe_client.post("/login", data={
        "email": email,
        "password": "secure-password-123",
        "csrf_token": csrf,
    })
    assert resp.status_code == 403
    assert "passkey" in resp.text.lower()
    assert "no longer available" in resp.text.lower()
