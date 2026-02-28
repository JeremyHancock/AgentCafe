"""Tests for backend credential encryption (AES-256-GCM)."""

from __future__ import annotations

import pytest

from agentcafe.crypto import configure_crypto, decrypt, encrypt, generate_key, _state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_crypto():
    """Reset crypto state between tests."""
    old_key = _state.key
    yield
    _state.key = old_key


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_generate_key_length():
    """Generated key should be 64 hex chars (32 bytes)."""
    key = generate_key()
    assert len(key) == 64
    bytes.fromhex(key)  # should not raise


def test_encrypt_decrypt_roundtrip():
    """Encrypting then decrypting should return the original plaintext."""
    configure_crypto(generate_key())
    original = "Bearer sk-live-abc123xyz"
    encrypted = encrypt(original)
    assert encrypted != original
    assert encrypted.startswith("enc::")
    assert decrypt(encrypted) == original


def test_encrypt_empty_string_passthrough():
    """Empty strings should pass through without encryption."""
    configure_crypto(generate_key())
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_decrypt_legacy_plaintext():
    """Plaintext values (no enc:: prefix) should be returned as-is."""
    configure_crypto(generate_key())
    assert decrypt("Bearer old-token-123") == "Bearer old-token-123"


def test_encryption_disabled_passthrough():
    """With no key configured, encrypt returns plaintext."""
    configure_crypto("")
    original = "Bearer sk-live-abc123xyz"
    assert encrypt(original) == original


def test_decrypt_encrypted_without_key_raises():
    """Decrypting encrypted data without a key should raise RuntimeError."""
    configure_crypto(generate_key())
    encrypted = encrypt("secret-value")
    configure_crypto("")  # disable encryption
    with pytest.raises(RuntimeError, match="CAFE_ENCRYPTION_KEY not configured"):
        decrypt(encrypted)


def test_different_encryptions_differ():
    """Two encryptions of the same plaintext should produce different ciphertexts (random nonce)."""
    configure_crypto(generate_key())
    a = encrypt("same-value")
    b = encrypt("same-value")
    assert a != b
    assert decrypt(a) == decrypt(b) == "same-value"


def test_wrong_key_fails_to_decrypt():
    """Decrypting with a different key should fail."""
    configure_crypto(generate_key())
    encrypted = encrypt("secret")
    configure_crypto(generate_key())  # different key
    with pytest.raises(Exception):
        decrypt(encrypted)


def test_invalid_key_length_raises():
    """A key that isn't 32 bytes should raise ValueError."""
    with pytest.raises(ValueError, match="32 bytes"):
        configure_crypto("abcd1234")  # only 4 bytes
