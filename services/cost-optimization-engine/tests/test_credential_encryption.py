"""Tests for credential encryption utilities."""

from __future__ import annotations

import os
from unittest.mock import patch

from toolkit_cost_optimization_engine.core.credential_encryption import (
    decrypt_credential,
    encrypt_credential,
)


def test_encrypt_none_returns_none():
    """Encrypting None returns None."""
    assert encrypt_credential(None) is None


def test_decrypt_none_returns_none():
    """Decrypting None returns None."""
    assert decrypt_credential(None) is None


def test_encrypt_empty_returns_empty():
    """Encrypting empty string returns empty string."""
    assert encrypt_credential("") == ""


def test_decrypt_empty_returns_empty():
    """Decrypting empty string returns empty string."""
    assert decrypt_credential("") == ""


def test_round_trip():
    """Encrypting then decrypting returns original value."""
    with patch.dict(os.environ, {"SECRET_KEY": "test-secret-for-encryption"}):
        original = "my-super-secret-api-key-12345"
        encrypted = encrypt_credential(original)
        assert encrypted is not None
        assert encrypted != original  # Must not be plaintext
        decrypted = decrypt_credential(encrypted)
        assert decrypted == original


def test_different_keys_fail():
    """Decrypting with wrong key returns None or fails gracefully."""
    with patch.dict(os.environ, {"SECRET_KEY": "key-one"}):
        encrypted = encrypt_credential("secret-data")

    with patch.dict(os.environ, {"SECRET_KEY": "key-two"}):
        result = decrypt_credential(encrypted)
        # With cryptography lib: returns None (InvalidToken)
        # With fallback base64: may return garbage, but not the original
        # Either way, it should not return the original plaintext
        assert result != "secret-data" or result is None


def test_encrypt_does_not_store_plaintext():
    """Encrypted value must not contain the original plaintext."""
    with patch.dict(os.environ, {"SECRET_KEY": "enc-test-key"}):
        secret = "AKIAIOSFODNN7EXAMPLE"
        encrypted = encrypt_credential(secret)
        assert encrypted is not None
        assert secret not in encrypted
