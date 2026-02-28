"""AES-256-GCM encryption for sensitive data at rest.

Used to encrypt backend_auth_header values stored in proxy_configs and
draft_services. The encryption key is derived from the CAFE_ENCRYPTION_KEY
environment variable.

Format: base64(nonce || ciphertext || tag)
- nonce: 12 bytes (GCM standard)
- ciphertext: variable length
- tag: 16 bytes (GCM authentication tag)
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("agentcafe.crypto")

_PREFIX = "enc::"


class _State:
    """Module-level mutable state."""
    key: bytes | None = None

_state = _State()


def configure_crypto(key_hex: str) -> None:
    """Set the AES-256 encryption key. Called once at startup.

    Args:
        key_hex: 64-character hex string (32 bytes = 256 bits).
                 If empty, encryption is disabled (passthrough mode for dev).
    """
    if not key_hex:
        logger.warning("CAFE_ENCRYPTION_KEY not set — credential encryption DISABLED")
        _state.key = None
        return
    raw = bytes.fromhex(key_hex)
    if len(raw) != 32:
        raise ValueError(f"CAFE_ENCRYPTION_KEY must be 32 bytes (64 hex chars), got {len(raw)}")
    _state.key = raw
    logger.info("Credential encryption enabled (AES-256-GCM)")


def generate_key() -> str:
    """Generate a random 256-bit key as a hex string. Utility for setup."""
    return os.urandom(32).hex()


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns prefixed base64 or plaintext if encryption disabled."""
    if not plaintext:
        return plaintext
    if _state.key is None:
        return plaintext
    nonce = os.urandom(12)
    aesgcm = AESGCM(_state.key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # nonce (12) + ciphertext + tag (16, appended by AESGCM)
    return _PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(stored: str) -> str:
    """Decrypt a stored value. Handles both encrypted and legacy plaintext."""
    if not stored:
        return stored
    if not stored.startswith(_PREFIX):
        # Legacy plaintext — return as-is (graceful migration)
        return stored
    if _state.key is None:
        raise RuntimeError(
            "Cannot decrypt: CAFE_ENCRYPTION_KEY not configured but encrypted data found"
        )
    raw = base64.b64decode(stored[len(_PREFIX):])
    nonce = raw[:12]
    ciphertext = raw[12:]
    aesgcm = AESGCM(_state.key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
