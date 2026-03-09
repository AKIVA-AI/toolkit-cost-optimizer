"""Credential encryption utilities.

Uses Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256)
from the cryptography library when available, otherwise falls back
to base64 obfuscation with a clear warning.

The encryption key is derived from the application's SECRET_KEY
using PBKDF2-HMAC-SHA256.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

_SALT = b"toolkit-cost-optimizer-credential-salt"


def _derive_key(secret_key: str) -> bytes:
    """Derive a 32-byte Fernet key from the application secret."""
    dk = hashlib.pbkdf2_hmac("sha256", secret_key.encode(), _SALT, iterations=100_000)
    return base64.urlsafe_b64encode(dk)


try:
    from cryptography.fernet import Fernet, InvalidToken

    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover
    _HAS_CRYPTO = False
    InvalidToken = Exception  # type: ignore[misc,assignment]


def _get_secret_key() -> str:
    """Get the secret key from environment or settings."""
    key = os.environ.get("SECRET_KEY", "")
    if not key:
        try:
            from .config import get_settings

            get_settings.cache_clear()
            key = get_settings().SECRET_KEY
        except Exception:
            logger.debug("Could not load settings for encryption key")
    return key or "default-dev-key"


def encrypt_credential(plaintext: str | None) -> str | None:
    """Encrypt a credential string. Returns None if input is None."""
    if plaintext is None:
        return None
    if not plaintext:
        return ""

    secret = _get_secret_key()

    if _HAS_CRYPTO:
        key = _derive_key(secret)
        f = Fernet(key)
        return f.encrypt(plaintext.encode()).decode()

    # Fallback: base64 encoding (not truly secure, but avoids plaintext)
    logger.warning(
        "cryptography package not installed; credentials stored with base64 only. "
        "Install 'cryptography' for real encryption.",
    )
    return base64.urlsafe_b64encode(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str | None) -> str | None:
    """Decrypt a credential string. Returns None if input is None."""
    if ciphertext is None:
        return None
    if not ciphertext:
        return ""

    secret = _get_secret_key()

    if _HAS_CRYPTO:
        key = _derive_key(secret)
        f = Fernet(key)
        try:
            return f.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            logger.error("Failed to decrypt credential — key mismatch or corrupted data")
            return None

    # Fallback: base64 decoding
    try:
        return base64.urlsafe_b64decode(ciphertext.encode()).decode()
    except Exception:
        logger.error("Failed to decode credential")
        return None
