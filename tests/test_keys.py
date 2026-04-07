"""Tests for RSA key management (Phase 6 — HS256 → RS256 migration).

Covers: key generation, PEM loading, JWKS serialization, key rotation,
RS256 sign/verify, HS256 legacy fallback, and the JWKS endpoint.
"""

from __future__ import annotations

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from httpx import ASGITransport, AsyncClient

from agentcafe.keys import (
    PassportKeyManager,
    configure_keys,
    decode_passport_token,
    get_key_manager,
    sign_passport_token,
)
from agentcafe.main import create_cafe_app

# pylint: disable=redefined-outer-name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_pem() -> str:
    """Generate a fresh RSA private key and return it as a PEM string."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


# ---------------------------------------------------------------------------
# PassportKeyManager unit tests
# ---------------------------------------------------------------------------

def test_generate_creates_key():
    """generate() should populate the current key."""
    km = PassportKeyManager()
    km.generate()
    assert km.current_key is not None
    assert km.current_key.kid
    assert len(km.verification_keys) == 1


def test_load_from_pem():
    """load_from_pem() should load a valid RSA key."""
    km = PassportKeyManager()
    km.load_from_pem(_generate_pem())
    assert km.current_key is not None
    assert len(km.verification_keys) == 1


def test_key_rotation():
    """Loading a second key should keep the previous one for verification."""
    km = PassportKeyManager()
    km.generate()
    kid1 = km.current_key.kid

    km.load_from_pem(_generate_pem())
    kid2 = km.current_key.kid

    assert kid1 != kid2
    assert len(km.verification_keys) == 2
    kids = [e.kid for e in km.verification_keys]
    assert kid1 in kids
    assert kid2 in kids


def test_jwks_format():
    """jwks() should return a valid JWKS with the expected fields."""
    km = PassportKeyManager()
    km.generate()
    jwks = km.jwks()

    assert "keys" in jwks
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"
    assert "kid" in key
    assert "n" in key
    assert "e" in key


def test_jwks_after_rotation():
    """JWKS should contain both current and previous keys after rotation."""
    km = PassportKeyManager()
    km.generate()
    km.load_from_pem(_generate_pem())
    jwks = km.jwks()
    assert len(jwks["keys"]) == 2


def test_uninitialized_raises():
    """Accessing current_key before initialization should raise RuntimeError."""
    km = PassportKeyManager()
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = km.current_key


# ---------------------------------------------------------------------------
# Sign / decode round-trip tests
# ---------------------------------------------------------------------------

def test_sign_and_decode_round_trip():
    """A token signed with RS256 should decode successfully."""
    configure_keys()  # auto-generate

    from datetime import datetime, timezone, timedelta
    import uuid

    now = datetime.now(timezone.utc)
    payload = {
        "iss": "agentcafe",
        "sub": "agent:test",
        "aud": "agentcafe",
        "exp": now + timedelta(hours=1),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    token = sign_passport_token(payload)

    # Should decode without error
    decoded = decode_passport_token(token)
    assert decoded["iss"] == "agentcafe"
    assert decoded["sub"] == "agent:test"

    # Verify the JWT header contains RS256 + kid
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert "kid" in header


def test_hs256_legacy_fallback():
    """A token signed with HS256 should decode when legacy secret is configured."""
    secret = "test-legacy-secret-for-hs256-fallback"
    configure_keys(legacy_hs256_secret=secret)

    from datetime import datetime, timezone, timedelta
    import uuid

    now = datetime.now(timezone.utc)
    payload = {
        "iss": "agentcafe",
        "sub": "agent:legacy",
        "aud": "agentcafe",
        "exp": now + timedelta(hours=1),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    legacy_token = jwt.encode(payload, secret, algorithm="HS256")

    decoded = decode_passport_token(legacy_token)
    assert decoded["sub"] == "agent:legacy"


def test_hs256_without_legacy_secret_fails():
    """An HS256 token should fail when no legacy secret is configured."""
    configure_keys()  # no legacy secret
    get_key_manager().set_legacy_secret("")  # ensure empty

    from datetime import datetime, timezone, timedelta
    import uuid

    now = datetime.now(timezone.utc)
    payload = {
        "iss": "agentcafe",
        "sub": "agent:x",
        "aud": "agentcafe",
        "exp": now + timedelta(hours=1),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, "some-secret", algorithm="HS256")

    with pytest.raises(jwt.InvalidTokenError):
        decode_passport_token(token)


def test_wrong_rsa_key_fails():
    """A token signed with a different RSA key should fail verification."""
    configure_keys()  # generates key A

    # Sign with a completely different key
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from datetime import datetime, timezone, timedelta
    import uuid

    now = datetime.now(timezone.utc)
    payload = {
        "iss": "agentcafe",
        "sub": "agent:intruder",
        "aud": "agentcafe",
        "exp": now + timedelta(hours=1),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    bad_token = jwt.encode(payload, other_key, algorithm="RS256", headers={"kid": "unknown"})

    with pytest.raises(jwt.InvalidTokenError):
        decode_passport_token(bad_token)


def test_expired_token_raises():
    """An expired RS256 token should raise ExpiredSignatureError."""
    configure_keys()

    from datetime import datetime, timezone, timedelta
    import uuid

    now = datetime.now(timezone.utc)
    payload = {
        "iss": "agentcafe",
        "sub": "agent:expired",
        "aud": "agentcafe",
        "exp": now - timedelta(hours=1),  # already expired
        "iat": now - timedelta(hours=2),
        "jti": str(uuid.uuid4()),
    }
    token = sign_passport_token(payload)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_passport_token(token)


# ---------------------------------------------------------------------------
# JWKS endpoint integration test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_client():
    """HTTP client for the Cafe app (no lifespan — keys configured manually)."""
    configure_keys()
    app = create_cafe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_jwks_endpoint(app_client):
    """GET /.well-known/jwks.json should return the public keys."""
    resp = await app_client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert len(data["keys"]) >= 1
    key = data["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert "kid" in key
    assert "n" in key
    assert "e" in key
