"""CLI integration tests for edge cases: empty files, malformed JSON, missing fields."""

from __future__ import annotations

import json
from pathlib import Path

from toolkit_cost_latency_opt.cli import EXIT_CLI_ERROR, EXIT_SUCCESS, EXIT_VALIDATION_FAILED, main


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# ============================================================================
# Empty File Tests
# ============================================================================


def test_validate_empty_file(tmp_path: Path) -> None:
    """Validate command on an empty file reports 0 rows, ok=True."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert main(["validate", "--input", str(empty)]) == EXIT_SUCCESS


def test_summarize_empty_file(tmp_path: Path) -> None:
    """Summarize command on an empty file produces empty models list."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert main(["summarize", "--input", str(empty)]) == EXIT_SUCCESS


def test_recommend_empty_file(tmp_path: Path) -> None:
    """Recommend command with empty file returns no candidates."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    result = main([
        "recommend", "--input", str(empty),
        "--min-samples", "1", "--max-p95-ms", "9999", "--min-success", "0",
    ])
    assert result == EXIT_VALIDATION_FAILED


# ============================================================================
# Malformed JSON Tests
# ============================================================================


def test_validate_malformed_json(tmp_path: Path) -> None:
    """Validate command fails gracefully on malformed JSON."""
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{invalid json}\n", encoding="utf-8")
    assert main(["validate", "--input", str(bad)]) == EXIT_CLI_ERROR


def test_summarize_malformed_json(tmp_path: Path) -> None:
    """Summarize command fails gracefully on malformed JSON."""
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not-json\n", encoding="utf-8")
    assert main(["summarize", "--input", str(bad)]) == EXIT_CLI_ERROR


def test_recommend_malformed_json(tmp_path: Path) -> None:
    """Recommend command fails gracefully on malformed JSON."""
    bad = tmp_path / "bad.jsonl"
    bad.write_text("[1,2,3\n", encoding="utf-8")
    assert main(["recommend", "--input", str(bad), "--min-samples", "1"]) == EXIT_CLI_ERROR


def test_simulate_malformed_policy(tmp_path: Path) -> None:
    """Simulate command fails gracefully with malformed policy JSON."""
    logs = tmp_path / "logs.jsonl"
    _write_jsonl(logs, [{
        "schema_version": 1, "created_ts": 1.0,
        "model": "m", "latency_ms": 100, "cost_usd": 0.001, "success": True,
    }])
    policy = tmp_path / "policy.json"
    policy.write_text("{bad json", encoding="utf-8")
    assert main(["simulate", "--input", str(logs), "--policy", str(policy)]) == EXIT_CLI_ERROR


# ============================================================================
# Missing Fields Tests
# ============================================================================


def test_validate_missing_required_fields(tmp_path: Path) -> None:
    """Validate detects rows with missing required fields."""
    logs = tmp_path / "missing.jsonl"
    # Missing model, latency_ms, cost_usd
    _write_jsonl(logs, [{"schema_version": 1, "created_ts": 1.0}])
    result = main(["validate", "--input", str(logs)])
    assert result == EXIT_VALIDATION_FAILED


def test_summarize_missing_model_field(tmp_path: Path) -> None:
    """Summarize treats rows with missing model as 'unknown'."""
    logs = tmp_path / "no_model.jsonl"
    _write_jsonl(logs, [
        {"schema_version": 1, "created_ts": 1.0, "latency_ms": 100,
         "cost_usd": 0.001, "success": True},
    ])
    assert main(["summarize", "--input", str(logs)]) == EXIT_SUCCESS


def test_validate_mixed_valid_invalid(tmp_path: Path) -> None:
    """Validate file with mix of valid and invalid rows."""
    logs = tmp_path / "mixed.jsonl"
    _write_jsonl(logs, [
        {"schema_version": 1, "created_ts": 1.0, "model": "good",
         "latency_ms": 100, "cost_usd": 0.001, "success": True},
        {"incomplete": True},
        {"schema_version": 1, "created_ts": 2.0, "model": "good",
         "latency_ms": 200, "cost_usd": 0.002, "success": True},
    ])
    result = main(["validate", "--input", str(logs)])
    assert result == EXIT_VALIDATION_FAILED


# ============================================================================
# File Not Found / Wrong Extension
# ============================================================================


def test_validate_nonexistent_file() -> None:
    """CLI returns error for nonexistent file."""
    assert main(["validate", "--input", "/no/such/file.jsonl"]) == EXIT_CLI_ERROR


def test_summarize_wrong_extension(tmp_path: Path) -> None:
    """CLI rejects file with wrong extension."""
    bad = tmp_path / "data.csv"
    bad.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    assert main(["summarize", "--input", str(bad)]) == EXIT_CLI_ERROR


# ============================================================================
# Simulate Edge Cases
# ============================================================================


def test_simulate_missing_tier_field(tmp_path: Path) -> None:
    """Simulate handles rows without tier field (defaults to 'default')."""
    logs = tmp_path / "no_tier.jsonl"
    _write_jsonl(logs, [
        {"schema_version": 1, "created_ts": 1.0, "model": "m",
         "latency_ms": 100, "cost_usd": 0.001, "success": True},
    ])
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"default_model": "m"}), encoding="utf-8")
    assert main(["simulate", "--input", str(logs), "--policy", str(policy)]) == EXIT_SUCCESS


def test_simulate_empty_tiers_policy(tmp_path: Path) -> None:
    """Simulate with policy that has no tier mappings."""
    logs = tmp_path / "logs.jsonl"
    _write_jsonl(logs, [
        {"schema_version": 1, "created_ts": 1.0, "model": "m",
         "tier": "custom", "latency_ms": 100, "cost_usd": 0.001, "success": True},
    ])
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"default_model": "fallback", "tiers": {}}), encoding="utf-8")
    assert main(["simulate", "--input", str(logs), "--policy", str(policy)]) == EXIT_SUCCESS


# ============================================================================
# Recommend Edge Cases
# ============================================================================


def test_recommend_no_candidates_meet_slo(tmp_path: Path) -> None:
    """Recommend returns failure when no models meet SLO constraints."""
    logs = tmp_path / "slow.jsonl"
    _write_jsonl(logs, [
        {"schema_version": 1, "created_ts": 1.0, "model": "slow",
         "latency_ms": 50000, "cost_usd": 0.001, "success": True},
    ])
    result = main([
        "recommend", "--input", str(logs),
        "--min-samples", "1", "--max-p95-ms", "100", "--min-success", "0.99",
    ])
    assert result == EXIT_VALIDATION_FAILED


def test_recommend_single_row(tmp_path: Path) -> None:
    """Recommend with exactly one valid row that meets constraints."""
    logs = tmp_path / "single.jsonl"
    _write_jsonl(logs, [
        {"schema_version": 1, "created_ts": 1.0, "model": "fast",
         "latency_ms": 50, "cost_usd": 0.001, "success": True},
    ])
    result = main([
        "recommend", "--input", str(logs),
        "--min-samples", "1", "--max-p95-ms", "1000", "--min-success", "0.5",
    ])
    assert result == EXIT_SUCCESS
