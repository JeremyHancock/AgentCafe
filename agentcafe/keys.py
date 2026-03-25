"""RSA key management for Passport and artifact signing (RS256).

Supports:
- Auto-generation for development (ephemeral key pair per startup)
- PEM-encoded private key via env var (PASSPORT_RSA_PRIVATE_KEY / ARTIFACT_RSA_PRIVATE_KEY)
- PEM file path via env var (PASSPORT_RSA_KEY_FILE / ARTIFACT_RSA_KEY_FILE)
- JWKS endpoint serving public keys for external verification
- Key ID (kid) derived from public key thumbprint, with optional prefix
- Dual-key rotation (sign with current, verify with current + previous)
- Separate key pairs for Passports and per-request artifacts (ADR-031)
"""

from __future__ import annotations

import base64
import hashlib
import logging

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

logger = logging.getLogger("agentcafe.keys")


# ---------------------------------------------------------------------------
# Key entry + manager
# ---------------------------------------------------------------------------

class KeyEntry:
    """A single RSA key pair with its key ID."""

    __slots__ = ("kid", "private_key", "public_key")

    def __init__(self, kid: str, private_key: RSAPrivateKey, public_key: RSAPublicKey):
        self.kid = kid
        self.private_key = private_key
        self.public_key = public_key


class PassportKeyManager:
    """Manages RSA key pairs for JWT signing and verification.

    Used for both Passport keys and artifact keys — distinguished by *kid_prefix*.
    Passport keys use no prefix (backward compat); artifact keys use ``"art_"``.
    """

    def __init__(self, kid_prefix: str = ""):
        self._current: KeyEntry | None = None
        self._previous: KeyEntry | None = None
        self._legacy_hs256_secret: str = ""
        self._kid_prefix = kid_prefix

    @property
    def current_key(self) -> KeyEntry:
        """The active signing key."""
        if self._current is None:
            raise RuntimeError("PassportKeyManager not initialized — call configure_keys() first")
        return self._current

    @property
    def verification_keys(self) -> list[KeyEntry]:
        """All keys valid for verification (current + previous for rotation)."""
        keys: list[KeyEntry] = []
        if self._current:
            keys.append(self._current)
        if self._previous:
            keys.append(self._previous)
        return keys

    def load_from_pem(self, pem_data: str) -> None:
        """Load an RSA private key from a PEM-encoded string."""
        raw = pem_data.encode() if isinstance(pem_data, str) else pem_data
        private_key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(private_key, RSAPrivateKey):
            raise ValueError("Key is not an RSA private key")
        public_key = private_key.public_key()
        kid = self._kid_prefix + _compute_kid(public_key)

        # Rotate: current becomes previous
        if self._current:
            self._previous = self._current
        self._current = KeyEntry(kid=kid, private_key=private_key, public_key=public_key)
        logger.info("Loaded RSA signing key: kid=%s", kid)

    def generate(self) -> None:
        """Generate a fresh 2048-bit RSA key pair (suitable for development)."""
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()
        kid = self._kid_prefix + _compute_kid(public_key)

        if self._current:
            self._previous = self._current
        self._current = KeyEntry(kid=kid, private_key=private_key, public_key=public_key)
        logger.info("Generated ephemeral RSA key pair: kid=%s", kid)

    def set_legacy_secret(self, secret: str) -> None:
        """Store the old HS256 secret for backward-compatible verification during migration."""
        self._legacy_hs256_secret = secret

    @property
    def legacy_hs256_secret(self) -> str:
        """The old HS256 secret kept for backward-compatible verification."""
        return self._legacy_hs256_secret

    def jwks(self) -> dict:
        """Return the JWKS (JSON Web Key Set) with all active public keys."""
        return {"keys": [_public_key_to_jwk(e.kid, e.public_key) for e in self.verification_keys]}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_key_manager = PassportKeyManager()
_artifact_key_manager = PassportKeyManager(kid_prefix="art_")


def get_key_manager() -> PassportKeyManager:
    """Return the module-level Passport key manager singleton."""
    return _key_manager


def get_artifact_key_manager() -> PassportKeyManager:
    """Return the module-level artifact key manager singleton (``art_`` prefix)."""
    return _artifact_key_manager


def configure_keys(
    rsa_private_key_pem: str = "",
    rsa_key_file: str = "",
    legacy_hs256_secret: str = "",
) -> PassportKeyManager:
    """Initialize the key manager from configuration.

    Priority:
    1. *rsa_private_key_pem* — PEM string (e.g. ``PASSPORT_RSA_PRIVATE_KEY`` env var)
    2. *rsa_key_file* — path to a PEM file (e.g. ``PASSPORT_RSA_KEY_FILE`` env var)
    3. Auto-generate (development mode, logs a warning)

    The *legacy_hs256_secret* is kept for verifying old HS256 tokens during
    the migration window (tokens expire naturally within hours).
    """
    if rsa_private_key_pem:
        _key_manager.load_from_pem(rsa_private_key_pem)
        logger.info("Passport signing: RS256 (key from environment variable)")
    elif rsa_key_file:
        with open(rsa_key_file, encoding="utf-8") as fh:
            _key_manager.load_from_pem(fh.read())
        logger.info("Passport signing: RS256 (key from file: %s)", rsa_key_file)
    else:
        _key_manager.generate()
        logger.warning(
            "Passport signing: RS256 with auto-generated key — NOT for production. "
            "Set PASSPORT_RSA_PRIVATE_KEY or PASSPORT_RSA_KEY_FILE for production use."
        )

    if legacy_hs256_secret:
        _key_manager.set_legacy_secret(legacy_hs256_secret)

    return _key_manager


def configure_artifact_keys(
    rsa_private_key_pem: str = "",
    rsa_key_file: str = "",
) -> PassportKeyManager:
    """Initialize the artifact key manager (separate key pair from Passports).

    Priority is the same as :func:`configure_keys`:
    1. *rsa_private_key_pem* (``ARTIFACT_RSA_PRIVATE_KEY``)
    2. *rsa_key_file* (``ARTIFACT_RSA_KEY_FILE``)
    3. Auto-generate (development mode)
    """
    if rsa_private_key_pem:
        _artifact_key_manager.load_from_pem(rsa_private_key_pem)
        logger.info("Artifact signing: RS256 (key from environment variable)")
    elif rsa_key_file:
        with open(rsa_key_file, encoding="utf-8") as fh:
            _artifact_key_manager.load_from_pem(fh.read())
        logger.info("Artifact signing: RS256 (key from file: %s)", rsa_key_file)
    else:
        _artifact_key_manager.generate()
        logger.warning(
            "Artifact signing: RS256 with auto-generated key — NOT for production. "
            "Set ARTIFACT_RSA_PRIVATE_KEY or ARTIFACT_RSA_KEY_FILE for production use."
        )
    return _artifact_key_manager


# ---------------------------------------------------------------------------
# Sign / decode helpers (used by passport.py, consent.py, artifact.py)
# ---------------------------------------------------------------------------

def sign_passport_token(payload: dict) -> str:
    """Sign a passport payload with RS256, embedding the *kid* in the header."""
    km = get_key_manager()
    entry = km.current_key
    return jwt.encode(
        payload,
        entry.private_key,
        algorithm="RS256",
        headers={"kid": entry.kid},
    )


def sign_artifact_token(payload: dict) -> str:
    """Sign a per-request artifact payload with RS256 using the artifact key pair."""
    km = get_artifact_key_manager()
    entry = km.current_key
    return jwt.encode(
        payload,
        entry.private_key,
        algorithm="RS256",
        headers={"kid": entry.kid},
    )


def decode_passport_token(
    token: str,
    *,
    audience: str = "agentcafe",
    options: dict | None = None,
) -> dict:
    """Decode and verify a passport JWT.

    Tries RS256 with all active keys first.  Falls back to HS256 with the
    legacy secret if the token header says ``alg: HS256`` (migration window).

    Raises ``jwt.ExpiredSignatureError`` or ``jwt.InvalidTokenError`` on failure.
    """
    km = get_key_manager()
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")
    kid = header.get("kid")

    decode_opts = dict(options) if options else {}
    decode_opts.setdefault("require", ["exp", "iat", "jti", "sub", "iss", "aud"])

    # --- RS256 path ---
    if alg == "RS256":
        last_exc: jwt.InvalidTokenError | None = None
        for entry in km.verification_keys:
            if kid and entry.kid != kid:
                continue
            try:
                return jwt.decode(
                    token,
                    entry.public_key,
                    algorithms=["RS256"],
                    issuer="agentcafe",
                    audience=audience,
                    options=decode_opts,
                )
            except jwt.ExpiredSignatureError:
                raise
            except jwt.InvalidTokenError as exc:
                last_exc = exc
                continue
        raise last_exc or jwt.InvalidTokenError("No matching RS256 key for kid")

    # --- HS256 legacy fallback ---
    if alg == "HS256" and km.legacy_hs256_secret:
        return jwt.decode(
            token,
            km.legacy_hs256_secret,
            algorithms=["HS256"],
            issuer="agentcafe",
            audience=audience,
            options=decode_opts,
        )

    raise jwt.InvalidTokenError(f"Unsupported or unconfigured algorithm: {alg}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_kid(public_key: RSAPublicKey) -> str:
    """Derive a key ID from the SHA-256 thumbprint of the DER-encoded public key."""
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()[:16]


def _int_to_base64url(value: int) -> str:
    """Encode an integer as unpadded base64url (per RFC 7518 §6.3)."""
    length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(length, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _public_key_to_jwk(kid: str, public_key: RSAPublicKey) -> dict:
    """Serialize an RSA public key as a JWK (RFC 7517)."""
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _int_to_base64url(numbers.n),
        "e": _int_to_base64url(numbers.e),
    }
