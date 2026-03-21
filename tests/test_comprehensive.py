"""Comprehensive tests for full coverage: schema validation edge cases,
stats calculations, IO, policy routing, budget simulation, and CLI integration."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from toolkit_cost_latency_opt.cli import (
    EXIT_CLI_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_FAILED,
    build_parser,
    main,
)
from toolkit_cost_latency_opt.io import read_json, read_jsonl
from toolkit_cost_latency_opt.schema import validate_inference_event
from toolkit_cost_latency_opt.stats import ModelSummary, percentile, summarize_model

CapSys = pytest.CaptureFixture[str]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# ============================================================================
# Schema Validation - Edge Cases
# ============================================================================


class TestSchemaValidation:
    """Test validate_inference_event for uncovered edge cases."""

    def test_schema_version_not_1(self) -> None:
        """Schema version != 1 triggers issue."""
        row = {
            "schema_version": 2,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
        }
        issues = validate_inference_event(row)
        assert any(i.field == "schema_version" and i.message == "must_be_1" for i in issues)

    def test_model_not_string(self) -> None:
        """Non-string model triggers type issue."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": 123,
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
        }
        issues = validate_inference_event(row)
        assert any(i.field == "model" and i.message == "expected_string" for i in issues)

    def test_latency_bool_type(self) -> None:
        """Boolean latency_ms triggers type issue (bool is subclass of int)."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": True,
            "cost_usd": 0.001,
            "success": True,
        }
        issues = validate_inference_event(row)
        assert any(i.field == "latency_ms" and i.message == "expected_number" for i in issues)

    def test_cost_negative(self) -> None:
        """Negative cost triggers constraint issue."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": -0.5,
            "success": True,
        }
        issues = validate_inference_event(row)
        assert any(
            i.field == "cost_usd" and i.message == "must_be_non_negative" for i in issues
        )

    def test_success_not_bool(self) -> None:
        """Non-bool success triggers type issue."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": "yes",
        }
        issues = validate_inference_event(row)
        assert any(i.field == "success" and i.message == "expected_bool" for i in issues)

    def test_tokens_in_not_int(self) -> None:
        """Non-int tokens_in triggers type issue."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
            "tokens_in": 5.5,
        }
        issues = validate_inference_event(row)
        assert any(i.field == "tokens_in" and i.message == "expected_int" for i in issues)

    def test_tokens_in_bool(self) -> None:
        """Boolean tokens_in triggers type issue."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
            "tokens_in": True,
        }
        issues = validate_inference_event(row)
        assert any(i.field == "tokens_in" and i.message == "expected_int" for i in issues)

    def test_tokens_out_negative(self) -> None:
        """Negative tokens_out triggers constraint issue."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
            "tokens_out": -5,
        }
        issues = validate_inference_event(row)
        assert any(
            i.field == "tokens_out" and i.message == "must_be_non_negative" for i in issues
        )

    def test_valid_row_no_issues(self) -> None:
        """Valid row should produce no issues."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
            "tokens_in": 50,
            "tokens_out": 100,
        }
        issues = validate_inference_event(row)
        assert len(issues) == 0

    def test_completely_empty_row(self) -> None:
        """Empty row triggers missing issues for all required fields."""
        issues = validate_inference_event({})
        assert len(issues) == 6  # all 6 required fields missing

    def test_created_ts_string_type(self) -> None:
        """String created_ts triggers type issue."""
        row = {
            "schema_version": 1,
            "created_ts": "not-a-number",
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
        }
        issues = validate_inference_event(row)
        assert any(i.field == "created_ts" and i.message == "expected_number" for i in issues)

    def test_latency_ms_negative(self) -> None:
        """Negative latency_ms triggers constraint issue."""
        row = {
            "schema_version": 1,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": -10,
            "cost_usd": 0.001,
            "success": True,
        }
        issues = validate_inference_event(row)
        assert any(
            i.field == "latency_ms" and i.message == "must_be_non_negative" for i in issues
        )

    def test_schema_version_zero(self) -> None:
        """Schema version 0 triggers issue (must be 1)."""
        row = {
            "schema_version": 0,
            "created_ts": 1.0,
            "model": "test",
            "latency_ms": 100,
            "cost_usd": 0.001,
            "success": True,
        }
        issues = validate_inference_event(row)
        assert any(i.field == "schema_version" for i in issues)


# ============================================================================
# Stats Calculations - Edge Cases
# ============================================================================


class TestStatsCalculations:
    def test_percentile_two_values(self) -> None:
        """Percentile interpolation with two values."""
        values = [10.0, 20.0]
        assert percentile(values, 50) == 15.0

    def test_percentile_negative_p(self) -> None:
        """Negative percentile returns min."""
        values = [1.0, 2.0, 3.0]
        assert percentile(values, -10) == 1.0

    def test_percentile_over_100(self) -> None:
        """Percentile >100 returns max."""
        values = [1.0, 2.0, 3.0]
        assert percentile(values, 150) == 3.0

    def test_percentile_p25(self) -> None:
        """P25 calculation."""
        values = [1.0, 2.0, 3.0, 4.0]
        result = percentile(values, 25)
        assert 1.0 <= result <= 2.0

    def test_summarize_model_empty(self) -> None:
        """Summarize empty rows returns zero counts."""
        summary = summarize_model("empty", [])
        assert summary.count == 0
        assert summary.success_rate == 0.0
        assert summary.total_cost_usd == 0.0
        assert math.isnan(summary.p50_ms)

    def test_summarize_model_with_failures(self) -> None:
        """Success rate accounts for failed rows."""
        rows = [
            {"latency_ms": 100, "cost_usd": 0.01, "success": True},
            {"latency_ms": 200, "cost_usd": 0.02, "success": False},
        ]
        summary = summarize_model("test", rows)
        assert summary.count == 2
        assert summary.success_rate == 0.5

    def test_summarize_model_string_cost(self) -> None:
        """String cost values are converted via _to_float."""
        rows = [
            {"latency_ms": 100, "cost_usd": "0.05", "success": True},
        ]
        summary = summarize_model("test", rows)
        assert summary.total_cost_usd == 0.05

    def test_summarize_model_bool_cost(self) -> None:
        """Boolean cost values are handled (True=1, False=0)."""
        rows = [
            {"latency_ms": 100, "cost_usd": True, "success": True},
        ]
        summary = summarize_model("test", rows)
        assert summary.total_cost_usd == 1.0

    def test_summarize_model_none_values(self) -> None:
        """None latency/cost values default to 0."""
        rows = [
            {"latency_ms": None, "cost_usd": None, "success": True},
        ]
        summary = summarize_model("test", rows)
        assert summary.total_cost_usd == 0.0
        assert summary.p50_ms == 0.0

    def test_summarize_model_invalid_string(self) -> None:
        """Invalid string cost defaults to 0."""
        rows = [
            {"latency_ms": 100, "cost_usd": "not-a-number", "success": True},
        ]
        summary = summarize_model("test", rows)
        assert summary.total_cost_usd == 0.0

    def test_summarize_model_dict_value(self) -> None:
        """Dict cost value defaults to 0 via _to_float."""
        rows = [
            {"latency_ms": 100, "cost_usd": {"amount": 5}, "success": True},
        ]
        summary = summarize_model("test", rows)
        assert summary.total_cost_usd == 0.0

    def test_model_summary_dataclass(self) -> None:
        """ModelSummary is a frozen dataclass."""
        ms = ModelSummary(
            model="test", count=10, success_rate=0.9,
            total_cost_usd=1.0, p50_ms=50.0, p95_ms=95.0,
        )
        assert ms.model == "test"
        assert ms.count == 10
        with pytest.raises(AttributeError):
            ms.count = 20  # type: ignore[misc]


# ============================================================================
# IO Edge Cases
# ============================================================================


class TestIOEdgeCases:
    def test_read_jsonl_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError, match="Log file not found"):
            list(read_jsonl(Path("/nonexistent/path/file.jsonl")))

    def test_read_json_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError, match="Policy file not found"):
            read_json(Path("/nonexistent/path/file.json"))

    def test_read_jsonl_multiple_valid_objects(self, tmp_path: Path) -> None:
        f = tmp_path / "multi.jsonl"
        f.write_text('{"a":1}\n{"b":2}\n{"c":3}\n', encoding="utf-8")
        result = list(read_jsonl(f))
        assert len(result) == 3

    def test_read_json_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "valid.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        result = read_json(f)
        assert result == {"key": "value"}

    def test_read_json_array(self, tmp_path: Path) -> None:
        """read_json accepts any valid JSON, including arrays."""
        f = tmp_path / "arr.json"
        f.write_text("[1, 2, 3]", encoding="utf-8")
        result = read_json(f)
        assert result == [1, 2, 3]


# ============================================================================
# CLI Integration - Output Verification
# ============================================================================


class TestCLIOutputVerification:
    def test_validate_output_structure(self, tmp_path: Path, capsys: CapSys) -> None:
        """Validate command output has correct JSON structure."""
        logs = tmp_path / "valid.jsonl"
        _write_jsonl(logs, [{
            "schema_version": 1, "created_ts": 1.0, "model": "test",
            "latency_ms": 100, "cost_usd": 0.001, "success": True,
        }])
        result = main(["validate", "--input", str(logs)])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        assert output["total"] == 1
        assert output["invalid_rows"] == 0

    def test_summarize_output_structure(self, tmp_path: Path, capsys: CapSys) -> None:
        """Summarize output contains correct model statistics."""
        logs = tmp_path / "logs.jsonl"
        _write_jsonl(logs, [
            {"schema_version": 1, "created_ts": 1.0, "model": "gpt-4",
             "latency_ms": 100, "cost_usd": 0.01, "success": True},
            {"schema_version": 1, "created_ts": 2.0, "model": "gpt-4",
             "latency_ms": 200, "cost_usd": 0.02, "success": True},
        ])
        result = main(["summarize", "--input", str(logs)])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert len(output["models"]) == 1
        model = output["models"][0]
        assert model["model"] == "gpt-4"
        assert model["count"] == 2
        assert model["total_cost_usd"] == 0.03

    def test_recommend_output_structure(self, tmp_path: Path, capsys: CapSys) -> None:
        """Recommend output contains recommended model details."""
        logs = tmp_path / "logs.jsonl"
        rows = [
            {"schema_version": 1, "created_ts": float(i), "model": "cheap",
             "latency_ms": 50, "cost_usd": 0.001, "success": True}
            for i in range(5)
        ]
        _write_jsonl(logs, rows)
        result = main([
            "recommend", "--input", str(logs),
            "--min-samples", "1", "--max-p95-ms", "1000", "--min-success", "0.5",
        ])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        assert output["recommended_model"] == "cheap"
        assert output["count"] == 5

    def test_recommend_cheapest_model(self, tmp_path: Path, capsys: CapSys) -> None:
        """Recommend picks the cheapest model that meets constraints."""
        logs = tmp_path / "logs.jsonl"
        rows = []
        for i in range(5):
            rows.append({"schema_version": 1, "created_ts": float(i), "model": "expensive",
                         "latency_ms": 50, "cost_usd": 0.10, "success": True})
            rows.append({"schema_version": 1, "created_ts": float(i), "model": "cheap",
                         "latency_ms": 50, "cost_usd": 0.001, "success": True})
        _write_jsonl(logs, rows)
        result = main([
            "recommend", "--input", str(logs),
            "--min-samples", "1", "--max-p95-ms", "1000", "--min-success", "0.5",
        ])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert output["recommended_model"] == "cheap"

    def test_simulate_output_structure(self, tmp_path: Path, capsys: CapSys) -> None:
        """Simulate output has total_requests, total_cost, and models."""
        logs = tmp_path / "logs.jsonl"
        _write_jsonl(logs, [
            {"schema_version": 1, "created_ts": 1.0, "model": "a", "tier": "fast",
             "latency_ms": 50, "cost_usd": 0.01, "success": True},
            {"schema_version": 1, "created_ts": 2.0, "model": "b", "tier": "deep",
             "latency_ms": 500, "cost_usd": 0.05, "success": True},
        ])
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({
            "default_model": "cheap",
            "tiers": {"fast": "quick", "deep": "strong"},
        }), encoding="utf-8")
        result = main(["simulate", "--input", str(logs), "--policy", str(policy)])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert output["total_requests"] == 2
        assert len(output["models"]) == 2

    def test_validate_with_invalid_rows_output(
        self, tmp_path: Path, capsys: CapSys
    ) -> None:
        """Validate output reports invalid row details."""
        logs = tmp_path / "mixed.jsonl"
        _write_jsonl(logs, [
            {"schema_version": 1, "created_ts": 1.0, "model": "test",
             "latency_ms": 100, "cost_usd": 0.001, "success": True},
            {"schema_version": 2, "created_ts": 1.0, "model": "test",
             "latency_ms": 100, "cost_usd": 0.001, "success": True},
        ])
        result = main(["validate", "--input", str(logs)])
        assert result == EXIT_VALIDATION_FAILED
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is False
        assert output["invalid_rows"] == 1
        assert len(output["issues"]) > 0


# ============================================================================
# CLI - JSON Log Flag
# ============================================================================


class TestCLIJsonLog:
    def test_json_log_flag(self, tmp_path: Path) -> None:
        """--json-log flag does not crash."""
        logs = tmp_path / "logs.jsonl"
        _write_jsonl(logs, [{
            "schema_version": 1, "created_ts": 1.0, "model": "test",
            "latency_ms": 100, "cost_usd": 0.001, "success": True,
        }])
        result = main(["--json-log", "--verbose", "summarize", "--input", str(logs)])
        assert result == EXIT_SUCCESS

    def test_json_log_with_error(self) -> None:
        """--json-log still works with errors."""
        result = main(["--json-log", "summarize", "--input", "/nonexistent.jsonl"])
        assert result == EXIT_CLI_ERROR


# ============================================================================
# CLI Parser Tests
# ============================================================================


class TestBuildParser:
    def test_parser_has_all_subcommands(self) -> None:
        parser = build_parser()
        # Check that parsing each subcommand works
        args = parser.parse_args(["validate", "--input", "test.jsonl"])
        assert args.cmd == "validate"
        args = parser.parse_args(["summarize", "--input", "test.jsonl"])
        assert args.cmd == "summarize"
        args = parser.parse_args(["recommend", "--input", "test.jsonl"])
        assert args.cmd == "recommend"

    def test_recommend_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["recommend", "--input", "test.jsonl"])
        assert args.max_p95_ms == "3000"
        assert args.min_success == "0.99"
        assert args.min_samples == "50"

    def test_verbose_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--verbose", "summarize", "--input", "test.jsonl"])
        assert args.verbose is True

    def test_no_verbose_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["summarize", "--input", "test.jsonl"])
        assert args.verbose is False


# ============================================================================
# Budget Simulation - Multiple Tiers
# ============================================================================


class TestBudgetSimulation:
    def test_multi_tier_cost_breakdown(self, tmp_path: Path, capsys: CapSys) -> None:
        """Simulate with multiple tiers calculates correct per-model costs."""
        logs = tmp_path / "logs.jsonl"
        rows = [
            {"schema_version": 1, "created_ts": 1.0, "model": "a", "tier": "basic",
             "latency_ms": 50, "cost_usd": 0.001, "success": True},
            {"schema_version": 1, "created_ts": 2.0, "model": "b", "tier": "basic",
             "latency_ms": 60, "cost_usd": 0.002, "success": True},
            {"schema_version": 1, "created_ts": 3.0, "model": "c", "tier": "premium",
             "latency_ms": 200, "cost_usd": 0.05, "success": True},
            {"schema_version": 1, "created_ts": 4.0, "model": "d", "tier": "premium",
             "latency_ms": 300, "cost_usd": 0.08, "success": True},
        ]
        _write_jsonl(logs, rows)
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({
            "default_model": "cheap-model",
            "tiers": {"basic": "cheap-model", "premium": "powerful-model"},
        }), encoding="utf-8")
        result = main(["simulate", "--input", str(logs), "--policy", str(policy)])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert output["total_requests"] == 4
        # cheap-model handles basic tier (2 rows), powerful-model handles premium (2 rows)
        model_names = {m["model"] for m in output["models"]}
        assert "cheap-model" in model_names
        assert "powerful-model" in model_names

    def test_all_requests_default_tier(self, tmp_path: Path, capsys: CapSys) -> None:
        """All requests route to default model when no tier matches."""
        logs = tmp_path / "logs.jsonl"
        rows = [
            {"schema_version": 1, "created_ts": float(i), "model": "x",
             "latency_ms": 100, "cost_usd": 0.01, "success": True}
            for i in range(3)
        ]
        _write_jsonl(logs, rows)
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({
            "default_model": "default-model",
            "tiers": {"special": "special-model"},
        }), encoding="utf-8")
        result = main(["simulate", "--input", str(logs), "--policy", str(policy)])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert output["total_requests"] == 3
        assert len(output["models"]) == 1
        assert output["models"][0]["model"] == "default-model"

    def test_simulate_cost_summation(self, tmp_path: Path, capsys: CapSys) -> None:
        """Total cost is sum of all per-model costs."""
        logs = tmp_path / "logs.jsonl"
        _write_jsonl(logs, [
            {"schema_version": 1, "created_ts": 1.0, "model": "a", "tier": "t1",
             "latency_ms": 50, "cost_usd": 1.0, "success": True},
            {"schema_version": 1, "created_ts": 2.0, "model": "b", "tier": "t2",
             "latency_ms": 50, "cost_usd": 2.0, "success": True},
        ])
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({
            "default_model": "m1",
            "tiers": {"t1": "m1", "t2": "m2"},
        }), encoding="utf-8")
        result = main(["simulate", "--input", str(logs), "--policy", str(policy)])
        assert result == EXIT_SUCCESS
        output = json.loads(capsys.readouterr().out)
        assert output["total_cost_usd"] == 3.0


# ============================================================================
# Metrics Integration via CLI
# ============================================================================


class TestMetricsIntegration:
    def test_analyses_counter_increments(self, tmp_path: Path) -> None:
        """CLI commands increment the analyses_run metric."""
        from toolkit_cost_latency_opt.observability import get_metrics

        metrics = get_metrics()
        metrics.reset()

        logs = tmp_path / "logs.jsonl"
        _write_jsonl(logs, [{
            "schema_version": 1, "created_ts": 1.0, "model": "test",
            "latency_ms": 100, "cost_usd": 0.001, "success": True,
        }])
        main(["summarize", "--input", str(logs)])
        assert metrics.get_counter("analyses_run") >= 1
        metrics.reset()

    def test_error_counter_increments_on_failure(self) -> None:
        """Errors increment the errors metric."""
        from toolkit_cost_latency_opt.observability import get_metrics

        metrics = get_metrics()
        metrics.reset()

        main(["summarize", "--input", "/nonexistent.jsonl"])
        assert metrics.get_counter("errors") >= 1
        metrics.reset()
