"""Human account management — registration and login for Cafe account holders.

MVP implementation uses email + password. Passkey/WebAuthn is post-MVP (v2-spec.md §15).
Human session tokens use aud: "human-dashboard" for strict separation from agent Passports.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agentcafe.db.engine import get_db

logger = logging.getLogger("agentcafe.human")

human_router = APIRouter(prefix="/human", tags=["human"])

_HUMAN_SESSION_HOURS = 24


class _State:
    """Module-level mutable state (avoids global statements)."""
    signing_secret: str = ""

_state = _State()


def configure_human(signing_secret: str) -> None:
    """Set the signing secret for human session tokens. Called once at startup."""
    _state.signing_secret = signing_secret


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Request body for POST /human/register."""
    email: str
    password: str = Field(min_length=8)
    display_name: str | None = None


class RegisterResponse(BaseModel):
    """Response body for POST /human/register."""
    user_id: str
    email: str
    session_token: str


class LoginRequest(BaseModel):
    """Request body for POST /human/login."""
    email: str
    password: str


class LoginResponse(BaseModel):
    """Response body for POST /human/login."""
    user_id: str
    session_token: str


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
# Endpoints
# ---------------------------------------------------------------------------

@human_router.post("/register", response_model=RegisterResponse)
async def register_human(req: RegisterRequest):
    """Register a new human Cafe account.

    MVP: email + password. Passkey enrollment is post-MVP.
    """
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
    """Log in to an existing human Cafe account."""
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

    # Rehash legacy SHA-256 passwords to bcrypt on successful login
    await _rehash_if_legacy(db, row["id"], req.password, row["password_hash"])

    session_token = _create_human_session_token(row["id"], req.email.lower())

    return LoginResponse(
        user_id=row["id"],
        session_token=session_token,
    )
