"""Security utilities: input sanitization, credential detection, log scrubbing.

Provides:
- Credential pattern detection (API keys, tokens, secrets)
- Log output sanitization (redact sensitive values)
- Input bounds checking for numeric parameters
- Safe string sanitization for log messages
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that indicate potential credentials or secrets
_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*\S+", re.IGNORECASE)),
    ("bearer_token", re.compile(r"(?i)bearer\s+[a-zA-Z0-9._\-]+", re.IGNORECASE)),
    ("secret", re.compile(r"(?i)(secret|password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE)),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("generic_token", re.compile(r"(?i)(token|auth)\s*[:=]\s*[a-zA-Z0-9._\-]{20,}")),
]

# Fields that should never appear in log output
_SENSITIVE_FIELD_NAMES: set[str] = {
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "passwd",
    "token",
    "auth_token",
    "access_token",
    "refresh_token",
    "private_key",
    "secret_key",
    "credentials",
}

_REDACTED = "[REDACTED]"


def detect_credentials(text: str) -> list[str]:
    """Check if text contains potential credential patterns.

    Args:
        text: Text to scan

    Returns:
        List of matched pattern names (empty if clean)
    """
    if not isinstance(text, str):
        return []
    found: list[str] = []
    for name, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            found.append(name)
    return found


def sanitize_for_log(value: str, max_length: int = 1000) -> str:
    """Sanitize a string value for safe logging.

    - Truncates to max_length
    - Redacts detected credential patterns
    - Strips control characters

    Args:
        value: String to sanitize
        max_length: Maximum output length

    Returns:
        Sanitized string
    """
    if not isinstance(value, str):
        return str(value)[:max_length]

    # Strip control characters (except newline, tab)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)

    # Redact credential patterns
    for _name, pattern in _CREDENTIAL_PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)

    # Truncate
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "...[truncated]"

    return cleaned


def sanitize_dict_for_log(data: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a dictionary for safe logging by redacting sensitive fields.

    Args:
        data: Dictionary to sanitize

    Returns:
        New dictionary with sensitive values redacted
    """
    if not isinstance(data, dict):
        return {}

    result: dict[str, Any] = {}
    for key, value in data.items():
        key_lower = str(key).lower().replace("-", "_")
        if key_lower in _SENSITIVE_FIELD_NAMES:
            result[key] = _REDACTED
        elif isinstance(value, str):
            result[key] = sanitize_for_log(value)
        elif isinstance(value, dict):
            result[key] = sanitize_dict_for_log(value)
        else:
            result[key] = value
    return result


def validate_numeric_bounds(
    value: float,
    name: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    allow_zero: bool = True,
) -> float:
    """Validate that a numeric value is within acceptable bounds.

    Args:
        value: The value to validate
        name: Parameter name (for error messages)
        min_value: Minimum allowed value (inclusive)
        max_value: Maximum allowed value (inclusive)
        allow_zero: Whether zero is allowed

    Returns:
        The validated value

    Raises:
        ValueError: If value is out of bounds
        TypeError: If value is not numeric
    """
    if isinstance(value, bool):
        raise TypeError(f"{name}: expected numeric, got bool")
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name}: expected numeric, got {type(value).__name__}")

    if not allow_zero and value == 0:
        raise ValueError(f"{name} must not be zero")

    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}, got: {value}")

    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be <= {max_value}, got: {value}")

    return value


def validate_string_input(
    value: str,
    name: str,
    *,
    max_length: int = 1000,
    allow_empty: bool = False,
) -> str:
    """Validate and sanitize a string input.

    Args:
        value: The string to validate
        name: Parameter name (for error messages)
        max_length: Maximum allowed length
        allow_empty: Whether empty strings are allowed

    Returns:
        The validated, stripped string

    Raises:
        TypeError: If value is not a string
        ValueError: If value fails validation
    """
    if not isinstance(value, str):
        raise TypeError(f"{name}: expected string, got {type(value).__name__}")

    stripped = value.strip()

    if not allow_empty and not stripped:
        raise ValueError(f"{name} must not be empty")

    if len(stripped) > max_length:
        raise ValueError(f"{name} exceeds maximum length of {max_length}")

    # Check for credential leaks
    creds = detect_credentials(stripped)
    if creds:
        raise ValueError(f"{name} appears to contain credentials: {', '.join(creds)}")

    return stripped
