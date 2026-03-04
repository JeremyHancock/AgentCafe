"""Human account management — registration and login for Cafe account holders.

Supports two auth modes:
- WebAuthn passkeys (default, required for production) — physical device proof that agents cannot fake.
- Email + password (legacy, gated behind ALLOW_PASSWORD_AUTH=true) — for beta migration only.

Human session tokens use aud: "human-dashboard" for strict separation from agent Passports.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from agentcafe.db.engine import get_db

logger = logging.getLogger("agentcafe.human")

human_router = APIRouter(prefix="/human", tags=["human"])

_HUMAN_SESSION_HOURS = 24
_CHALLENGE_TTL_SECONDS = 300  # 5 minutes
_DEFAULT_GRACE_PERIOD_DAYS = 7


class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""
    passkey_grace_period_days: int = _DEFAULT_GRACE_PERIOD_DAYS
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "AgentCafe"
    webauthn_origin: str = "http://localhost:8000"
    allow_password_auth: bool = True

_state = _State()


def configure_human(
    signing_secret: str,
    *,
    webauthn_rp_id: str = "localhost",
    webauthn_rp_name: str = "AgentCafe",
    webauthn_origin: str = "http://localhost:8000",
    allow_password_auth: bool = True,
    passkey_grace_period_days: int = _DEFAULT_GRACE_PERIOD_DAYS,
) -> None:
    """Set secrets and WebAuthn config for human accounts. Called once at startup."""
    _state.signing_secret = signing_secret
    _state.webauthn_rp_id = webauthn_rp_id
    _state.webauthn_rp_name = webauthn_rp_name
    _state.webauthn_origin = webauthn_origin
    _state.allow_password_auth = allow_password_auth
    _state.passkey_grace_period_days = passkey_grace_period_days


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Request body for POST /human/register (password auth)."""
    email: str
    password: str = Field(min_length=8)
    display_name: str | None = None


class RegisterResponse(BaseModel):
    """Response body for POST /human/register (password auth)."""
    user_id: str
    email: str
    session_token: str


class LoginRequest(BaseModel):
    """Request body for POST /human/login (password auth)."""
    email: str
    password: str


class LoginResponse(BaseModel):
    """Response body for POST /human/login (password auth)."""
    user_id: str
    session_token: str
    passkey_enrolled: bool = True


class PasskeyRegisterBeginRequest(BaseModel):
    """Request body for POST /human/passkey/register/begin."""
    email: str
    display_name: str | None = None


class PasskeyRegisterCompleteRequest(BaseModel):
    """Request body for POST /human/passkey/register/complete."""
    challenge_id: str
    credential: dict


class PasskeyLoginBeginRequest(BaseModel):
    """Request body for POST /human/passkey/login/begin."""
    email: str | None = None


class PasskeyLoginCompleteRequest(BaseModel):
    """Request body for POST /human/passkey/login/complete."""
    challenge_id: str
    credential: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash.

    Supports both bcrypt hashes (current) and legacy SHA-256 hex hashes.
    Returns True if the password matches.
    """
    if _SHA256_HEX_RE.match(stored_hash):
        # Legacy SHA-256 hash — verify against it
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    # bcrypt hash
    try:
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except (ValueError, TypeError):
        return False


async def _rehash_if_legacy(db, user_id: str, password: str, stored_hash: str) -> None:
    """If the stored hash is legacy SHA-256, re-hash with bcrypt and update DB."""
    if _SHA256_HEX_RE.match(stored_hash):
        new_hash = _hash_password(password)
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE cafe_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (new_hash, now, user_id),
        )
        await db.commit()
        logger.info("Rehashed legacy SHA-256 password to bcrypt for user %s", user_id)


def _create_human_session_token(user_id: str, email: str) -> str:
    """Create a human session JWT (aud: human-dashboard)."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "agentcafe",
        "sub": f"user:{email}",
        "aud": "human-dashboard",
        "exp": now + timedelta(hours=_HUMAN_SESSION_HOURS),
        "iat": now,
        "jti": str(uuid.uuid4()),
        "user_id": user_id,
    }
    return jwt.encode(payload, _state.signing_secret, algorithm="HS256")


def validate_human_session(token: str) -> dict:
    """Validate a human session token. Returns the payload or raises HTTPException."""
    try:
        payload = jwt.decode(
            token,
            _state.signing_secret,
            algorithms=["HS256"],
            issuer="agentcafe",
            audience="human-dashboard",
            options={"require": ["exp", "iat", "jti", "sub", "iss", "aud"]},
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail={"error": "session_expired"}) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail={"error": "session_invalid"}) from exc


# ---------------------------------------------------------------------------
# Challenge management helpers
# ---------------------------------------------------------------------------

async def _store_challenge(
    challenge: bytes,
    challenge_type: str,
    user_id: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
) -> str:
    """Store a WebAuthn challenge in the DB. Returns the challenge_id."""
    db = await get_db()
    challenge_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=_CHALLENGE_TTL_SECONDS)
    await db.execute(
        """INSERT INTO webauthn_challenges
           (id, challenge, user_id, email, display_name, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            challenge_id, bytes_to_base64url(challenge), user_id, email,
            display_name, challenge_type, now.isoformat(), expires.isoformat(),
        ),
    )
    await db.commit()
    return challenge_id


async def _load_and_consume_challenge(challenge_id: str, expected_type: str) -> dict:
    """Load a challenge by ID, validate type/expiry, then delete it. Returns row as dict."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM webauthn_challenges WHERE id = ?", (challenge_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail={"error": "challenge_not_found"})

    if row["type"] != expected_type:
        raise HTTPException(status_code=400, detail={"error": "challenge_type_mismatch"})

    expires = datetime.fromisoformat(row["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        await db.execute("DELETE FROM webauthn_challenges WHERE id = ?", (challenge_id,))
        await db.commit()
        raise HTTPException(status_code=400, detail={"error": "challenge_expired"})

    # Consume (single-use)
    await db.execute("DELETE FROM webauthn_challenges WHERE id = ?", (challenge_id,))
    await db.commit()
    return dict(row)


async def cleanup_expired_challenges() -> int:
    """Delete expired challenges. Returns count deleted."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "DELETE FROM webauthn_challenges WHERE expires_at < ?", (now,)
    )
    await db.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Endpoints — Password auth (legacy, gated behind ALLOW_PASSWORD_AUTH)
# ---------------------------------------------------------------------------

def _require_password_auth() -> None:
    """Raise 403 if password auth is disabled."""
    if not _state.allow_password_auth:
        raise HTTPException(
            status_code=403,
            detail={"error": "password_auth_disabled",
                    "message": "Password authentication is disabled. Use passkey authentication."},
        )


async def _check_passkey_enrollment(db, user_id: str) -> dict:
    """Check if a user has enrolled a passkey and whether the grace period has elapsed.

    Returns {"enrolled": bool, "grace_expired": bool, "enrolled_at": str|None}.
    """
    cursor = await db.execute(
        "SELECT created_at FROM webauthn_credentials WHERE user_id = ? ORDER BY created_at ASC LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {"enrolled": False, "grace_expired": False, "enrolled_at": None}

    enrolled_at = datetime.fromisoformat(row["created_at"])
    if enrolled_at.tzinfo is None:
        enrolled_at = enrolled_at.replace(tzinfo=timezone.utc)
    grace_deadline = enrolled_at + timedelta(days=_state.passkey_grace_period_days)
    grace_expired = datetime.now(timezone.utc) > grace_deadline

    return {"enrolled": True, "grace_expired": grace_expired, "enrolled_at": row["created_at"]}


@human_router.post("/register", response_model=RegisterResponse)
async def register_human(req: RegisterRequest):
    """Register a new human Cafe account with email + password.

    Legacy auth mode — gated behind ALLOW_PASSWORD_AUTH. Use passkey endpoints for production.
    """
    _require_password_auth()
    db = await get_db()

    # Check for duplicate email
    cursor = await db.execute(
        "SELECT id FROM cafe_users WHERE email = ?", (req.email.lower(),)
    )
    if await cursor.fetchone():
        raise HTTPException(
            status_code=409,
            detail={"error": "email_exists", "message": "An account with this email already exists."},
        )

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO cafe_users (id, email, display_name, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, req.email.lower(), req.display_name, _hash_password(req.password), now, now),
    )
    await db.commit()

    session_token = _create_human_session_token(user_id, req.email.lower())

    return RegisterResponse(
        user_id=user_id,
        email=req.email.lower(),
        session_token=session_token,
    )


@human_router.post("/login", response_model=LoginResponse)
async def login_human(req: LoginRequest):
    """Log in to an existing human Cafe account with email + password.

    Legacy auth mode — gated behind ALLOW_PASSWORD_AUTH. Use passkey endpoints for production.
    After grace period, users with enrolled passkeys must use passkey login.
    """
    _require_password_auth()
    db = await get_db()

    cursor = await db.execute(
        "SELECT id, password_hash FROM cafe_users WHERE email = ?", (req.email.lower(),)
    )
    row = await cursor.fetchone()
    if not row or not _verify_password(req.password, row["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials", "message": "Invalid email or password."},
        )

    # Grace period: reject password login if user has passkey enrolled > N days ago
    pk_status = await _check_passkey_enrollment(db, row["id"])
    if pk_status["grace_expired"]:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "password_login_disabled",
                "message": "Your account has a passkey enrolled. "
                           "Password login is no longer available. Please sign in with your passkey.",
            },
        )

    # Rehash legacy SHA-256 passwords to bcrypt on successful login
    await _rehash_if_legacy(db, row["id"], req.password, row["password_hash"])

    session_token = _create_human_session_token(row["id"], req.email.lower())

    return LoginResponse(
        user_id=row["id"],
        session_token=session_token,
        passkey_enrolled=pk_status["enrolled"],
    )


# ---------------------------------------------------------------------------
# Endpoints — WebAuthn passkey registration
# ---------------------------------------------------------------------------

@human_router.post("/passkey/register/begin")
async def passkey_register_begin(req: PasskeyRegisterBeginRequest):
    """Begin passkey registration. Returns WebAuthn creation options.

    The browser calls navigator.credentials.create() with these options.
    """
    email = req.email.lower()
    db = await get_db()

    # Check if email is already registered
    cursor = await db.execute(
        "SELECT id FROM cafe_users WHERE email = ?", (email,)
    )
    if await cursor.fetchone():
        raise HTTPException(
            status_code=409,
            detail={"error": "email_exists", "message": "An account with this email already exists."},
        )

    # Generate a temporary user_id for the registration ceremony
    user_id_bytes = uuid.uuid4().bytes

    options = generate_registration_options(
        rp_id=_state.webauthn_rp_id,
        rp_name=_state.webauthn_rp_name,
        user_id=user_id_bytes,
        user_name=email,
        user_display_name=req.display_name or email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )

    # Store challenge for verification in the complete step
    challenge_id = await _store_challenge(
        challenge=options.challenge,
        challenge_type="register",
        user_id=bytes_to_base64url(user_id_bytes),
        email=email,
        display_name=req.display_name,
    )

    # Return options as JSON (browser-ready) plus our challenge_id
    options_json = json.loads(options_to_json(options))
    options_json["challenge_id"] = challenge_id

    return JSONResponse(content=options_json)


async def complete_passkey_registration(challenge_id: str, credential: dict) -> dict:
    """Verify passkey attestation, create user account, store credential.

    Returns {"user_id", "email", "session_token", "credential_id"}.
    Raises HTTPException on any failure. Used by both the /passkey/register/complete
    endpoint and the /activate/complete page flow.
    """
    challenge_row = await _load_and_consume_challenge(challenge_id, "register")

    expected_challenge = base64url_to_bytes(challenge_row["challenge"])

    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=_state.webauthn_rp_id,
            expected_origin=_state.webauthn_origin,
        )
    except Exception as exc:
        logger.warning("Passkey registration verification failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail={"error": "verification_failed", "message": str(exc)},
        ) from exc

    db = await get_db()
    email = challenge_row["email"]
    display_name = challenge_row["display_name"]

    # Double-check email not taken (race condition guard)
    cursor = await db.execute(
        "SELECT id FROM cafe_users WHERE email = ?", (email,)
    )
    if await cursor.fetchone():
        raise HTTPException(
            status_code=409,
            detail={"error": "email_exists", "message": "An account with this email already exists."},
        )

    # Create user account (no password — passkey only)
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """INSERT INTO cafe_users (id, email, display_name, password_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, email, display_name, "", now, now),
    )

    # Store the credential
    credential_id_b64 = bytes_to_base64url(verification.credential_id)
    public_key_b64 = bytes_to_base64url(verification.credential_public_key)

    await db.execute(
        """INSERT INTO webauthn_credentials
           (id, user_id, credential_id, public_key, sign_count, device_name, created_at, last_used_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), user_id, credential_id_b64,
            public_key_b64, verification.sign_count,
            "Primary passkey", now, now,
        ),
    )
    await db.commit()

    logger.info("Passkey registered for user %s (%s)", user_id, email)

    session_token = _create_human_session_token(user_id, email)

    return {
        "user_id": user_id,
        "email": email,
        "session_token": session_token,
        "credential_id": credential_id_b64,
    }


@human_router.post("/passkey/register/complete")
async def passkey_register_complete(req: PasskeyRegisterCompleteRequest):
    """Complete passkey registration. Verifies attestation, creates account, returns session."""
    result = await complete_passkey_registration(req.challenge_id, req.credential)
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Endpoints — WebAuthn passkey enrollment (existing password users)
# ---------------------------------------------------------------------------

class PasskeyEnrollBeginRequest(BaseModel):
    """Request body for POST /human/passkey/enroll/begin."""
    session_token: str


@human_router.post("/passkey/enroll/begin")
async def passkey_enroll_begin(req: PasskeyEnrollBeginRequest):
    """Begin passkey enrollment for an existing password user.

    Requires a valid session token. Returns WebAuthn creation options
    so the user can add a passkey to their existing account.
    """
    session = validate_human_session(req.session_token)
    user_id = session["user_id"]

    db = await get_db()

    # Verify user exists and get email
    cursor = await db.execute("SELECT id, email FROM cafe_users WHERE id = ?", (user_id,))
    user_row = await cursor.fetchone()
    if not user_row:
        raise HTTPException(status_code=404, detail={"error": "user_not_found"})
    email = user_row["email"]

    user_id_bytes = uuid.UUID(user_id).bytes

    options = generate_registration_options(
        rp_id=_state.webauthn_rp_id,
        rp_name=_state.webauthn_rp_name,
        user_id=user_id_bytes,
        user_name=email,
        user_display_name=email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )

    challenge_id = await _store_challenge(
        challenge=options.challenge,
        challenge_type="register",
        user_id=user_id,
        email=email,
    )

    options_json = json.loads(options_to_json(options))
    options_json["challenge_id"] = challenge_id

    return JSONResponse(content=options_json)


class PasskeyEnrollCompleteRequest(BaseModel):
    """Request body for POST /human/passkey/enroll/complete."""
    session_token: str
    challenge_id: str
    credential: dict


@human_router.post("/passkey/enroll/complete")
async def passkey_enroll_complete(req: PasskeyEnrollCompleteRequest):
    """Complete passkey enrollment for an existing user. Stores credential, no new account."""
    session = validate_human_session(req.session_token)
    user_id = session["user_id"]

    challenge_row = await _load_and_consume_challenge(req.challenge_id, "register")

    # Ensure the challenge belongs to this user
    if challenge_row.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail={"error": "user_mismatch"})

    expected_challenge = base64url_to_bytes(challenge_row["challenge"])

    try:
        verification = verify_registration_response(
            credential=req.credential,
            expected_challenge=expected_challenge,
            expected_rp_id=_state.webauthn_rp_id,
            expected_origin=_state.webauthn_origin,
        )
    except Exception as exc:
        logger.warning("Passkey enrollment verification failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail={"error": "verification_failed", "message": str(exc)},
        ) from exc

    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    credential_id_b64 = bytes_to_base64url(verification.credential_id)
    public_key_b64 = bytes_to_base64url(verification.credential_public_key)

    await db.execute(
        """INSERT INTO webauthn_credentials
           (id, user_id, credential_id, public_key, sign_count, device_name, created_at, last_used_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), user_id, credential_id_b64,
            public_key_b64, verification.sign_count,
            "Enrolled passkey", now, now,
        ),
    )
    await db.commit()

    logger.info("Passkey enrolled for existing user %s", user_id)

    return JSONResponse(content={
        "user_id": user_id,
        "credential_id": credential_id_b64,
        "enrolled": True,
    })


# ---------------------------------------------------------------------------
# Endpoints — WebAuthn passkey login
# ---------------------------------------------------------------------------

@human_router.post("/passkey/login/begin")
async def passkey_login_begin(req: PasskeyLoginBeginRequest):
    """Begin passkey login. Returns WebAuthn authentication options.

    If email is provided, returns allowed credentials for that user.
    If email is omitted, allows discoverable credential (resident key) login.
    """
    db = await get_db()
    allow_credentials = []
    user_id = None

    if req.email:
        email = req.email.lower()
        cursor = await db.execute(
            "SELECT id FROM cafe_users WHERE email = ?", (email,)
        )
        user_row = await cursor.fetchone()
        if not user_row:
            raise HTTPException(
                status_code=404,
                detail={"error": "user_not_found", "message": "No account with this email."},
            )
        user_id = user_row["id"]

        # Fetch registered credentials for this user
        cred_cursor = await db.execute(
            "SELECT credential_id FROM webauthn_credentials WHERE user_id = ?",
            (user_id,),
        )
        cred_rows = await cred_cursor.fetchall()
        if not cred_rows:
            raise HTTPException(
                status_code=400,
                detail={"error": "no_passkey", "message": "This account has no registered passkey."},
            )

        allow_credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(row["credential_id"]))
            for row in cred_rows
        ]

    options = generate_authentication_options(
        rp_id=_state.webauthn_rp_id,
        allow_credentials=allow_credentials if allow_credentials else None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    challenge_id = await _store_challenge(
        challenge=options.challenge,
        challenge_type="login",
        user_id=user_id,
    )

    options_json = json.loads(options_to_json(options))
    options_json["challenge_id"] = challenge_id

    return JSONResponse(content=options_json)


async def verify_passkey_assertion(challenge_id: str, credential: dict) -> dict:
    """Verify a passkey assertion and return the authenticated user info.

    Consumes the challenge, verifies the assertion against stored credentials,
    and updates the sign count. Returns {"user_id": ..., "email": ...}.

    Raises HTTPException on any failure. Used by both passkey login and
    consent approval flows — single code path, single security gate.
    """
    challenge_row = await _load_and_consume_challenge(challenge_id, "login")

    expected_challenge = base64url_to_bytes(challenge_row["challenge"])

    # Look up the credential to get the stored public key
    raw_id = credential.get("rawId", credential.get("id", ""))

    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM webauthn_credentials WHERE credential_id = ?",
        (raw_id,),
    )
    cred_row = await cursor.fetchone()
    if not cred_row:
        raise HTTPException(
            status_code=400,
            detail={"error": "credential_not_found", "message": "Unknown credential."},
        )

    credential_public_key = base64url_to_bytes(cred_row["public_key"])
    credential_current_sign_count = cred_row["sign_count"]

    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=_state.webauthn_rp_id,
            expected_origin=_state.webauthn_origin,
            credential_public_key=credential_public_key,
            credential_current_sign_count=credential_current_sign_count,
        )
    except Exception as exc:
        logger.warning("Passkey assertion verification failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail={"error": "verification_failed", "message": str(exc)},
        ) from exc

    # Update sign count (replay protection)
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE webauthn_credentials SET sign_count = ?, last_used_at = ? WHERE credential_id = ?",
        (verification.new_sign_count, now, raw_id),
    )
    await db.commit()

    # Look up user
    user_cursor = await db.execute(
        "SELECT id, email FROM cafe_users WHERE id = ?",
        (cred_row["user_id"],),
    )
    user_row = await user_cursor.fetchone()
    if not user_row:
        raise HTTPException(status_code=500, detail={"error": "user_not_found"})

    return {"user_id": user_row["id"], "email": user_row["email"]}


@human_router.post("/passkey/login/complete")
async def passkey_login_complete(req: PasskeyLoginCompleteRequest):
    """Complete passkey login. Verifies assertion, returns session token."""
    user_info = await verify_passkey_assertion(req.challenge_id, req.credential)

    logger.info("Passkey login for user %s (%s)", user_info["user_id"], user_info["email"])

    session_token = _create_human_session_token(user_info["user_id"], user_info["email"])

    return JSONResponse(content={
        "user_id": user_info["user_id"],
        "session_token": session_token,
    })
