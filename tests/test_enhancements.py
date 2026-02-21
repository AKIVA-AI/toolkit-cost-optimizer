"""Tests for enhanced features: validation, error handling, logging."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toolkit_cost_latency_opt.cli import main, validate_cli_args
from toolkit_cost_latency_opt.io import LogFormatError, validate_file_path
from toolkit_cost_latency_opt.policy import TierPolicy
from toolkit_cost_latency_opt.stats import percentile


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# ============================================================================
# Path Validation Tests
# ============================================================================


def test_validate_file_path_file_not_found(tmp_path: Path) -> None:
    """Test path validation with non-existent file."""
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError, match="File not found"):
        validate_file_path(missing)


def test_validate_file_path_invalid_extension(tmp_path: Path) -> None:
    """Test path validation rejects invalid file extensions."""
    bad_file = tmp_path / "bad.exe"
    bad_file.write_text("test", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid file extension"):
        validate_file_path(bad_file)


def test_validate_file_path_is_directory(tmp_path: Path) -> None:
    """Test path validation rejects directories."""
    with pytest.raises(ValueError, match="not a file"):
        validate_file_path(tmp_path)


def test_validate_file_path_symlink(tmp_path: Path) -> None:
    """Test path validation rejects symlinks."""
    real_file = tmp_path / "real.jsonl"
    real_file.write_text("{}\n", encoding="utf-8")

    symlink = tmp_path / "link.jsonl"
    try:
        symlink.symlink_to(real_file)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")

    with pytest.raises(ValueError, match="Symlinks not allowed"):
        validate_file_path(symlink)


def test_validate_file_path_success(tmp_path: Path) -> None:
    """Test path validation succeeds with valid file."""
    valid_file = tmp_path / "valid.jsonl"
    valid_file.write_text("{}\n", encoding="utf-8")

    result = validate_file_path(valid_file)
    assert result.is_absolute()
    assert result.exists()


# ============================================================================
# CLI Argument Validation Tests
# ============================================================================


def test_validate_cli_args_invalid_max_p95_ms() -> None:
    """Test CLI validation rejects invalid max_p95_ms."""
    import argparse

    args = argparse.Namespace(max_p95_ms="not_a_number")
    with pytest.raises(ValueError, match="Invalid --max-p95-ms"):
        validate_cli_args(args)


def test_validate_cli_args_negative_max_p95_ms() -> None:
    """Test CLI validation rejects negative max_p95_ms."""
    import argparse

    args = argparse.Namespace(max_p95_ms="-100")
    with pytest.raises(ValueError, match="must be positive"):
        validate_cli_args(args)


def test_validate_cli_args_too_large_max_p95_ms() -> None:
    """Test CLI validation rejects excessively large max_p95_ms."""
    import argparse

    args = argparse.Namespace(max_p95_ms="9999999999")
    with pytest.raises(ValueError, match="too large"):
        validate_cli_args(args)


def test_validate_cli_args_invalid_min_success() -> None:
    """Test CLI validation rejects invalid min_success."""
    import argparse

    args = argparse.Namespace(min_success="abc")
    with pytest.raises(ValueError, match="Invalid --min-success"):
        validate_cli_args(args)


def test_validate_cli_args_min_success_out_of_range() -> None:
    """Test CLI validation rejects min_success outside [0, 1]."""
    import argparse

    args = argparse.Namespace(min_success="1.5")
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        validate_cli_args(args)

    args = argparse.Namespace(min_success="-0.1")
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        validate_cli_args(args)


def test_validate_cli_args_invalid_min_samples() -> None:
    """Test CLI validation rejects invalid min_samples."""
    import argparse

    args = argparse.Namespace(min_samples="not_int")
    with pytest.raises(ValueError, match="Invalid --min-samples"):
        validate_cli_args(args)


def test_validate_cli_args_negative_min_samples() -> None:
    """Test CLI validation rejects negative or zero min_samples."""
    import argparse

    args = argparse.Namespace(min_samples="0")
    with pytest.raises(ValueError, match="must be >= 1"):
        validate_cli_args(args)

    args = argparse.Namespace(min_samples="-5")
    with pytest.raises(ValueError, match="must be >= 1"):
        validate_cli_args(args)


# ============================================================================
# JSON/JSONL Parsing Error Tests
# ============================================================================


def test_read_jsonl_invalid_json(tmp_path: Path) -> None:
    """Test JSONL reader handles invalid JSON."""
    from toolkit_cost_latency_opt.io import read_jsonl

    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text("not valid json\n", encoding="utf-8")

    with pytest.raises(LogFormatError, match="Invalid JSON at line 1"):
        list(read_jsonl(bad_file))


def test_read_jsonl_not_dict(tmp_path: Path) -> None:
    """Test JSONL reader handles non-dict JSON."""
    from toolkit_cost_latency_opt.io import read_jsonl

    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text('"string"\n[1, 2, 3]\n', encoding="utf-8")

    with pytest.raises(LogFormatError, match="Expected dict at line 1"):
        list(read_jsonl(bad_file))


def test_read_jsonl_empty_lines(tmp_path: Path) -> None:
    """Test JSONL reader skips empty lines."""
    from toolkit_cost_latency_opt.io import read_jsonl

    file = tmp_path / "test.jsonl"
    file.write_text('{"a": 1}\n\n{"b": 2}\n  \n{"c": 3}\n', encoding="utf-8")

    result = list(read_jsonl(file))
    assert len(result) == 3
    assert result[0] == {"a": 1}
    assert result[1] == {"b": 2}
    assert result[2] == {"c": 3}


def test_read_json_invalid_json(tmp_path: Path) -> None:
    """Test JSON reader handles invalid JSON."""
    from toolkit_cost_latency_opt.io import read_json

    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        read_json(bad_file)


# ============================================================================
# Policy Validation Tests
# ============================================================================


def test_policy_not_object() -> None:
    """Test policy validation rejects non-dict."""
    with pytest.raises(ValueError, match="must be a JSON object"):
        TierPolicy.from_json("not a dict")

    with pytest.raises(ValueError, match="must be a JSON object"):
        TierPolicy.from_json([1, 2, 3])


def test_policy_missing_default_model() -> None:
    """Test policy validation requires default_model."""
    with pytest.raises(ValueError, match="cannot be empty"):
        TierPolicy.from_json({"default_model": ""})

    with pytest.raises(ValueError, match="cannot be empty"):
        TierPolicy.from_json({"default_model": "  "})


def test_policy_invalid_default_model_type() -> None:
    """Test policy validation rejects non-string default_model."""
    with pytest.raises(ValueError, match="must be a string"):
        TierPolicy.from_json({"default_model": 123})

    with pytest.raises(ValueError, match="must be a string"):
        TierPolicy.from_json({"default_model": None})


def test_policy_invalid_tiers_type() -> None:
    """Test policy validation rejects non-dict tiers."""
    with pytest.raises(ValueError, match="must be an object"):
        TierPolicy.from_json({"default_model": "cheap", "tiers": "not a dict"})


def test_policy_invalid_tier_values() -> None:
    """Test policy validation rejects non-string tier keys/values."""
    with pytest.raises(ValueError, match="must be strings"):
        TierPolicy.from_json({"default_model": "cheap", "tiers": {123: "model"}})

    with pytest.raises(ValueError, match="must be strings"):
        TierPolicy.from_json({"default_model": "cheap", "tiers": {"tier": 456}})


def test_policy_empty_tier_values() -> None:
    """Test policy validation rejects empty tier keys/values."""
    with pytest.raises(ValueError, match="cannot be empty"):
        TierPolicy.from_json({"default_model": "cheap", "tiers": {"": "model"}})

    with pytest.raises(ValueError, match="cannot be empty"):
        TierPolicy.from_json({"default_model": "cheap", "tiers": {"tier": ""}})


def test_policy_valid() -> None:
    """Test policy validation succeeds with valid policy."""
    policy = TierPolicy.from_json(
        {"default_model": "cheap", "tiers": {"deep": "strong", "fast": "quick"}}
    )
    assert policy.default_model == "cheap"
    assert policy.tiers == {"deep": "strong", "fast": "quick"}
    assert policy.model_for("deep") == "strong"
    assert policy.model_for("unknown") == "cheap"


def test_policy_no_tiers() -> None:
    """Test policy validation allows missing tiers field."""
    policy = TierPolicy.from_json({"default_model": "cheap"})
    assert policy.default_model == "cheap"
    assert policy.tiers == {}


# ============================================================================
# Percentile Edge Case Tests
# ============================================================================


def test_percentile_empty_list() -> None:
    """Test percentile handles empty list."""
    import math

    result = percentile([], 50)
    assert math.isnan(result)


def test_percentile_single_value() -> None:
    """Test percentile with single value."""
    assert percentile([5.0], 0) == 5.0
    assert percentile([5.0], 50) == 5.0
    assert percentile([5.0], 100) == 5.0


def test_percentile_boundary_values() -> None:
    """Test percentile at boundaries."""
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 100) == 5.0


# ============================================================================
# Integration Tests - Error Handling
# ============================================================================


def test_cli_file_not_found() -> None:
    """Test CLI handles missing file gracefully."""
    result = main(["summarize", "--input", "/nonexistent/file.jsonl"])
    assert result != 0


def test_cli_invalid_json(tmp_path: Path) -> None:
    """Test CLI handles invalid JSON gracefully."""
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text("not json\n", encoding="utf-8")

    result = main(["summarize", "--input", str(bad_file)])
    assert result != 0


def test_cli_invalid_extension(tmp_path: Path) -> None:
    """Test CLI rejects invalid file extensions."""
    bad_file = tmp_path / "bad.exe"
    bad_file.write_text("{}\n", encoding="utf-8")

    result = main(["summarize", "--input", str(bad_file)])
    assert result != 0


def test_cli_recommend_invalid_args(tmp_path: Path) -> None:
    """Test CLI validates recommend arguments."""
    logs = tmp_path / "logs.jsonl"
    _write_jsonl(
        logs,
        [
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "test",
                "latency_ms": 100,
                "cost_usd": 0.001,
                "success": True,
            }
        ],
    )

    result = main(
        [
            "recommend",
            "--input",
            str(logs),
            "--min-success",
            "1.5",
        ]
    )
    assert result != 0

    result = main(
        [
            "recommend",
            "--input",
            str(logs),
            "--max-p95-ms",
            "-100",
        ]
    )
    assert result != 0

    result = main(
        [
            "recommend",
            "--input",
            str(logs),
            "--min-samples",
            "0",
        ]
    )
    assert result != 0


def test_cli_simulate_invalid_policy(tmp_path: Path) -> None:
    """Test CLI handles invalid policy gracefully."""
    logs = tmp_path / "logs.jsonl"
    _write_jsonl(
        logs,
        [
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "test",
                "latency_ms": 100,
                "cost_usd": 0.001,
                "success": True,
            }
        ],
    )

    policy = tmp_path / "policy.json"
    policy.write_text('{"default_model": ""}', encoding="utf-8")

    result = main(["simulate", "--input", str(logs), "--policy", str(policy)])
    assert result != 0


def test_cli_empty_file(tmp_path: Path) -> None:
    """Test CLI handles empty file gracefully."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    result = main(["summarize", "--input", str(empty)])
    assert result == 0


# ============================================================================
# Verbose Logging Test
# ============================================================================


def test_cli_verbose_flag(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test --verbose flag enables debug logging."""
    logs = tmp_path / "logs.jsonl"
    _write_jsonl(
        logs,
        [
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "test",
                "latency_ms": 100,
                "cost_usd": 0.001,
                "success": True,
            }
        ],
    )

    result = main(["--verbose", "summarize", "--input", str(logs)])
    assert result == 0

