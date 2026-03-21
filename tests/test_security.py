"""Tests for security module: credential detection, sanitization, bounds checking."""

from __future__ import annotations

import pytest

from toolkit_cost_latency_opt.security import (
    detect_credentials,
    sanitize_dict_for_log,
    sanitize_for_log,
    validate_numeric_bounds,
    validate_string_input,
)

# ============================================================================
# Credential Detection Tests
# ============================================================================


class TestDetectCredentials:
    def test_clean_text(self) -> None:
        assert detect_credentials("normal text without secrets") == []

    def test_api_key_pattern(self) -> None:
        result = detect_credentials("api_key=sk-1234567890abcdef")
        assert "api_key" in result

    def test_bearer_token_pattern(self) -> None:
        result = detect_credentials("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test")
        assert "bearer_token" in result

    def test_secret_pattern(self) -> None:
        result = detect_credentials("password=mysecretvalue123")
        assert "secret" in result

    def test_aws_key_pattern(self) -> None:
        result = detect_credentials("AKIAIOSFODNN7EXAMPLE")
        assert "aws_key" in result

    def test_non_string_input(self) -> None:
        assert detect_credentials(123) == []  # type: ignore[arg-type]
        assert detect_credentials(None) == []  # type: ignore[arg-type]

    def test_empty_string(self) -> None:
        assert detect_credentials("") == []


# ============================================================================
# Log Sanitization Tests
# ============================================================================


class TestSanitizeForLog:
    def test_clean_string(self) -> None:
        assert sanitize_for_log("hello world") == "hello world"

    def test_truncation(self) -> None:
        long_string = "x" * 2000
        result = sanitize_for_log(long_string, max_length=100)
        assert len(result) < 200  # truncated + suffix
        assert "truncated" in result

    def test_credential_redaction(self) -> None:
        result = sanitize_for_log("api_key=secret123")
        assert "secret123" not in result
        assert "[REDACTED]" in result

    def test_control_character_stripping(self) -> None:
        result = sanitize_for_log("hello\x00\x01\x02world")
        assert "\x00" not in result
        assert "\x01" not in result

    def test_non_string_input(self) -> None:
        result = sanitize_for_log(42)  # type: ignore[arg-type]
        assert result == "42"

    def test_preserves_newlines_tabs(self) -> None:
        result = sanitize_for_log("line1\nline2\ttab")
        assert "\n" in result
        assert "\t" in result


class TestSanitizeDictForLog:
    def test_clean_dict(self) -> None:
        data = {"name": "test", "count": 5}
        result = sanitize_dict_for_log(data)
        assert result == {"name": "test", "count": 5}

    def test_redacts_sensitive_fields(self) -> None:
        data = {"api_key": "secret123", "name": "test"}
        result = sanitize_dict_for_log(data)
        assert result["api_key"] == "[REDACTED]"
        assert result["name"] == "test"

    def test_redacts_password_field(self) -> None:
        redacted = "[REDACTED]"
        data = {"password": "hunter2", "user": "admin"}
        result = sanitize_dict_for_log(data)
        assert result["password"] == redacted

    def test_nested_dict_sanitization(self) -> None:
        redacted = "[REDACTED]"
        data = {"config": {"token": "secret", "model": "gpt-4"}}
        result = sanitize_dict_for_log(data)
        assert result["config"]["token"] == redacted
        assert result["config"]["model"] == "gpt-4"

    def test_non_dict_input(self) -> None:
        result = sanitize_dict_for_log("not a dict")  # type: ignore[arg-type]
        assert result == {}

    def test_credential_in_string_value(self) -> None:
        data = {"log": "api_key=secret123"}
        result = sanitize_dict_for_log(data)
        assert "secret123" not in result["log"]


# ============================================================================
# Numeric Bounds Validation Tests
# ============================================================================


class TestValidateNumericBounds:
    def test_valid_value(self) -> None:
        assert validate_numeric_bounds(5.0, "test", min_value=0, max_value=10) == 5.0

    def test_min_violation(self) -> None:
        with pytest.raises(ValueError, match="must be >="):
            validate_numeric_bounds(-1.0, "test", min_value=0)

    def test_max_violation(self) -> None:
        with pytest.raises(ValueError, match="must be <="):
            validate_numeric_bounds(100.0, "test", max_value=50)

    def test_zero_not_allowed(self) -> None:
        with pytest.raises(ValueError, match="must not be zero"):
            validate_numeric_bounds(0, "test", allow_zero=False)

    def test_zero_allowed_by_default(self) -> None:
        assert validate_numeric_bounds(0, "test") == 0

    def test_bool_rejected(self) -> None:
        with pytest.raises(TypeError, match="got bool"):
            validate_numeric_bounds(True, "test")  # type: ignore[arg-type]

    def test_string_rejected(self) -> None:
        with pytest.raises(TypeError, match="got str"):
            validate_numeric_bounds("5", "test")  # type: ignore[arg-type]

    def test_integer_accepted(self) -> None:
        assert validate_numeric_bounds(5, "test") == 5

    def test_boundary_values_inclusive(self) -> None:
        assert validate_numeric_bounds(0, "test", min_value=0) == 0
        assert validate_numeric_bounds(10, "test", max_value=10) == 10


# ============================================================================
# String Input Validation Tests
# ============================================================================


class TestValidateStringInput:
    def test_valid_string(self) -> None:
        assert validate_string_input("hello", "test") == "hello"

    def test_strips_whitespace(self) -> None:
        assert validate_string_input("  hello  ", "test") == "hello"

    def test_empty_rejected_by_default(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_string_input("", "test")

    def test_empty_allowed(self) -> None:
        assert validate_string_input("", "test", allow_empty=True) == ""

    def test_max_length_exceeded(self) -> None:
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_string_input("x" * 100, "test", max_length=10)

    def test_non_string_rejected(self) -> None:
        with pytest.raises(TypeError, match="got int"):
            validate_string_input(42, "test")  # type: ignore[arg-type]

    def test_credential_in_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="credentials"):
            validate_string_input("api_key=sk-12345678901234567890", "test")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_string_input("   ", "test")
