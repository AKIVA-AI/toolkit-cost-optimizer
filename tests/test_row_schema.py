"""Row schema v1/v2 and strict row validation in every analysis command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from report_helpers import envelope, payload

from toolkit_cost_latency_opt.cli import EXIT_CLI_ERROR, EXIT_SUCCESS, main
from toolkit_cost_latency_opt.schema import validate_inference_event

CapSys = pytest.CaptureFixture[str]


def _v2(**extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"schema_version": 2, "created_ts": 1.0, "model": "m"}
    row.update(extra)
    return row


def _issues(row: dict[str, Any], require: tuple[str, ...] = ()) -> set[tuple[str, str]]:
    return {(i.field, i.message) for i in validate_inference_event(row, require)}


class TestSchemaV2:
    def test_minimal_v2_row_is_valid(self) -> None:
        assert _issues(_v2()) == set()

    def test_v2_requires_model_and_created_ts(self) -> None:
        assert _issues({"schema_version": 2}) == {
            ("created_ts", "required"),
            ("model", "required"),
        }

    def test_schema_version_must_be_int_one_or_two(self) -> None:
        # JSON true == 1 in Python; it must not pass as version 1.
        for bad in (True, "1", 1.5, 3, 0):
            assert ("schema_version", "must_be_1_or_2") in _issues(_v2(schema_version=bad))

    def test_full_v2_row_is_valid(self) -> None:
        row = _v2(
            latency_ms=12.5,
            cost_usd=0.01,
            success=True,
            tier="premium",
            tokens_in=1000,
            tokens_cache_read=600,
            tokens_cache_write=100,
            tokens_out=300,
            tokens_reasoning=200,
            quality=0.75,
            request_id="r1",
            provider="openai",
            source="litellm",
        )
        assert _issues(row) == set()

    def test_cache_tokens_are_part_of_tokens_in(self) -> None:
        row = _v2(tokens_in=100, tokens_cache_read=80, tokens_cache_write=30)
        assert _issues(row) == {("tokens_in", "must_include_cache_tokens")}
        assert _issues(_v2(tokens_cache_read=5)) == {("tokens_in", "required_with_cache_tokens")}

    def test_one_hour_writes_are_part_of_cache_writes(self) -> None:
        row = _v2(tokens_in=100, tokens_cache_write=10, tokens_cache_write_1h=11)
        assert _issues(row) == {("tokens_cache_write", "must_include_1h_writes")}
        assert _issues(_v2(tokens_in=100, tokens_cache_write_1h=5)) == {
            ("tokens_cache_write", "required_with_1h_writes")
        }

    def test_reasoning_tokens_are_part_of_tokens_out(self) -> None:
        assert _issues(_v2(tokens_out=10, tokens_reasoning=11)) == {
            ("tokens_out", "must_include_reasoning_tokens")
        }
        assert _issues(_v2(tokens_reasoning=5)) == {("tokens_out", "required_with_reasoning")}

    def test_aggregate_rows_cannot_carry_per_request_fields(self) -> None:
        row = _v2(requests=10, tokens_in=100, tokens_out=10, latency_ms=5.0, success=True)
        assert _issues(row) == {
            ("latency_ms", "not_allowed_on_aggregate_row"),
            ("success", "not_allowed_on_aggregate_row"),
        }
        assert _issues(_v2(requests=0)) == {("requests", "must_be_positive")}

    def test_aggregate_flag(self) -> None:
        assert _issues(_v2(aggregate=True, tokens_in=5, tokens_out=1)) == set()
        assert _issues(_v2(aggregate="yes")) == {("aggregate", "expected_bool")}
        assert _issues(_v2(aggregate=True, quality=0.5)) == {
            ("quality", "not_allowed_on_aggregate_row")
        }

    def test_quality_must_be_in_unit_interval(self) -> None:
        assert _issues(_v2(quality=1.0)) == set()
        assert _issues(_v2(quality=1.2)) == {("quality", "must_be_between_0_and_1")}
        assert _issues(_v2(quality="0.9")) == {("quality", "expected_number")}

    def test_non_finite_numbers_rejected(self) -> None:
        assert _issues(_v2(latency_ms=float("nan"))) == {("latency_ms", "must_be_finite")}
        assert _issues(_v2(cost_usd=float("inf"))) == {("cost_usd", "must_be_finite")}

    def test_null_optional_field_is_absent_but_null_required_field_is_not(self) -> None:
        assert _issues(_v2(tier=None, tokens_in=None, cost_usd=None)) == set()
        assert _issues(_v2(model=None)) == {("model", "expected_string")}

    def test_empty_strings_rejected(self) -> None:
        assert _issues(_v2(model=" ")) == {("model", "must_be_non_empty")}
        assert _issues(_v2(tier="")) == {("tier", "must_be_non_empty")}

    def test_command_specific_required_fields(self) -> None:
        need = ("latency_ms", "success", "cost_usd")
        assert _issues(_v2(), need) == {
            ("latency_ms", "required"),
            ("success", "required"),
            ("cost_usd", "required"),
        }


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestCommandsValidateRows:
    def test_nan_literal_in_log_is_rejected(self, tmp_path: Path, capsys: CapSys) -> None:
        # Python's json module accepts the non-standard NaN literal; the schema must not.
        logs = _write(
            tmp_path / "l.jsonl",
            [
                '{"schema_version":1,"created_ts":1,"model":"m","latency_ms":NaN,'
                '"cost_usd":0.1,"success":true}'
            ],
        )
        assert main(["summarize", "--input", str(logs)]) == EXIT_CLI_ERROR
        pred = envelope(capsys.readouterr().out)["predicate"]
        assert pred["details"]["issues"] == [
            {"count": 1, "field": "latency_ms", "kind": "constraint", "message": "must_be_finite"}
        ]

    def test_invalid_rows_fail_every_analysis_command(self, tmp_path: Path, capsys: CapSys) -> None:
        good = _v2(latency_ms=1.0, cost_usd=0.1, success=True, tokens_in=1, tokens_out=1)
        bad = dict(good, cost_usd=True)
        logs = _write(tmp_path / "l.jsonl", [json.dumps(good), json.dumps(bad)])
        policy = tmp_path / "p.json"
        policy.write_text(json.dumps({"default_model": "gpt-4o"}), encoding="utf-8")
        for argv in (
            ["summarize", "--input", str(logs)],
            ["recommend", "--input", str(logs), "--min-samples", "1"],
            ["simulate", "--input", str(logs), "--policy", str(policy)],
        ):
            assert main(argv) == EXIT_CLI_ERROR, argv
            pred = envelope(capsys.readouterr().out)["predicate"]
            assert pred["verdict"] == "error"
            assert pred["summary"]["invalid_rows"] == 1
            assert pred["summary"]["total_rows"] == 2

    def test_summarize_v2_rows_without_latency_or_success(
        self, tmp_path: Path, capsys: CapSys
    ) -> None:
        rows = [
            _v2(requests=40, tokens_in=1000, tokens_out=10, cost_usd=0.5),
            _v2(tokens_in=10, tokens_out=1),
        ]
        logs = _write(tmp_path / "l.jsonl", [json.dumps(r) for r in rows])
        assert main(["summarize", "--input", str(logs)]) == EXIT_SUCCESS
        out = payload(capsys.readouterr().out)
        assert out["total_rows"] == 2
        assert out["total_requests"] == 41
        (m,) = out["models"]
        assert m["count"] == 41
        assert m["rows"] == 2
        assert m["success_rate"] is None
        assert m["p50_ms"] is None and m["p95_ms"] is None
        assert m["total_cost_usd"] == pytest.approx(0.5)
        assert m["cost_rows"] == 1

    def test_recommend_requires_per_request_fields_on_v2_rows(self, tmp_path: Path) -> None:
        logs = _write(tmp_path / "l.jsonl", [json.dumps(_v2(latency_ms=5.0, success=True))])
        assert main(["recommend", "--input", str(logs), "--min-samples", "1"]) == EXIT_CLI_ERROR

    def test_validate_reports_coerced_count(self, tmp_path: Path, capsys: CapSys) -> None:
        logs = _write(tmp_path / "l.jsonl", [json.dumps(_v2(created_ts="5", tokens_in="7"))])
        argv = ["validate", "--input", str(logs), "--coerce-numeric-strings"]
        assert main(argv) == EXIT_SUCCESS
        out = payload(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["coerced_values"] == 2
