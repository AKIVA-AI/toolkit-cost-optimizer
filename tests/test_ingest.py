"""`toolkit-opt ingest`: normalize real export formats into v2 rows.

Fixtures in tests/fixtures/ingest/ are built from each format's documented field names:

- litellm_spendlogs.json: LiteLLM_SpendLogs columns (litellm/proxy/schema.prisma) as
  returned by GET /spend/logs?summarize=false; cache counts in
  metadata.additional_usage_values / metadata.usage_object.prompt_tokens_details.
- litellm_payload.jsonl: StandardLoggingPayload keys (litellm/types/utils.py), NDJSON as
  written by the s3_v2 / gcs_bucket / generic_api loggers; startTime/endTime are epoch
  seconds.
- otel_traces.jsonl: OTLP/JSON TracesData (opentelemetry.io/docs/specs/otlp, int64 as
  decimal strings, status code 2 = ERROR) with gen_ai.* attributes (semantic-conventions
  v1.41.0 registry plus the renamed gen_ai.usage.cache_write.input_tokens).
- openai_usage.json: GET /v1/organization/usage/completions page; the first result uses the
  OpenAPI spec's example identity input 5000 = cached 1800 + cache-write 200 + uncached 3000.
- anthropic_usage.json: GET /v1/organizations/usage_report/messages, values from the API
  reference example.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from report_helpers import envelope

from toolkit_cost_latency_opt.cli import (
    EXIT_CLI_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_FAILED,
    main,
)
from toolkit_cost_latency_opt.envelope import sha256_bytes
from toolkit_cost_latency_opt.ingest import ingest, load_documents, parse_timestamp

CapSys = pytest.CaptureFixture[str]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ingest"


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def _run(
    tmp_path: Path, fmt: str, fixture: str, capsys: CapSys, *extra: str
) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    rows_path = tmp_path / "rows.jsonl"
    argv = ["ingest", "--format", fmt, "--input", str(FIXTURES / fixture)]
    code = main([*argv, "--rows", str(rows_path), *extra])
    pred = envelope(capsys.readouterr().out)["predicate"]
    text = rows_path.read_text(encoding="utf-8") if rows_path.exists() else ""
    rows = [json.loads(line) for line in text.splitlines()]
    if rows_path.exists():
        assert pred["details"]["rows_file"]["digest"]["sha256"] == sha256_bytes(
            rows_path.read_bytes()
        )
    return code, pred, rows


class TestLiteLLMSpendLogs:
    def test_rows(self, tmp_path: Path, capsys: CapSys) -> None:
        code, pred, rows = _run(
            tmp_path, "litellm-spendlogs", "litellm_spendlogs.json", capsys, "--tier-from", "tier"
        )
        assert code == EXIT_VALIDATION_FAILED  # the row with an empty model is skipped
        assert pred["kind"] == "cost.ingest"
        assert pred["summary"]["records"] == 4
        assert pred["summary"]["rows_written"] == 3
        assert pred["summary"]["skipped_reasons"] == {"missing_model": 1}
        openai_row, anthropic_row, failed = rows
        assert openai_row == {
            "schema_version": 2,
            "source": "litellm-spendlogs",
            "created_ts": _epoch("2026-09-25T10:00:00"),
            "model": "gpt-5.4-mini",
            "provider": "openai",
            "request_id": "chatcmpl-9f1c2a",
            "latency_ms": 1250,
            "success": True,
            "cost_usd": 0.001575,
            "tokens_in": 1200,
            "tokens_out": 300,
            "tokens_cache_read": 1000,
            "tokens_reasoning": 120,
            "tier": "premium",
        }
        # Anthropic via LiteLLM: prompt_tokens already includes cache reads and writes;
        # metadata and request_tags stored as JSON strings; naive timestamps are UTC.
        assert anthropic_row["created_ts"] == _epoch("2026-09-25T10:05:00")
        assert anthropic_row["latency_ms"] == pytest.approx(2500.0)
        assert anthropic_row["tokens_in"] == 5000
        assert anthropic_row["tokens_cache_read"] == 500
        assert anthropic_row["tokens_cache_write"] == 1500
        assert anthropic_row["tokens_cache_write_1h"] == 500
        assert anthropic_row["tier"] == "basic"
        assert failed["success"] is False
        assert failed["latency_ms"] == pytest.approx(30000.0)
        assert "tier" not in failed

    def test_tier_from_metadata_field(self) -> None:
        docs = load_documents(FIXTURES / "litellm_spendlogs.json")
        result = ingest("litellm-spendlogs", docs, "user_api_key_alias")
        assert result.rows[0]["tier"] == "support-bot"
        assert "tier" not in result.rows[1]

    def test_v2_data_envelope(self) -> None:
        docs = [{"data": load_documents(FIXTURES / "litellm_spendlogs.json")[0][:1]}]
        assert len(ingest("litellm-spendlogs", docs).rows) == 1


class TestLiteLLMPayload:
    def test_rows(self, tmp_path: Path, capsys: CapSys) -> None:
        code, pred, rows = _run(
            tmp_path, "litellm-payload", "litellm_payload.jsonl", capsys, "--tier-from", "tier"
        )
        assert code == EXIT_SUCCESS
        assert pred["verdict"] == "pass"
        gemini, failed = rows
        assert gemini["created_ts"] == 1790330400.0
        assert gemini["latency_ms"] == pytest.approx(1500.0)
        assert gemini["tokens_reasoning"] == 200
        assert gemini["cost_usd"] == 0.00105
        assert gemini["tier"] == "basic"
        assert failed["success"] is False
        assert failed["tokens_in"] == 50


class TestOtel:
    def test_rows(self, tmp_path: Path, capsys: CapSys) -> None:
        code, pred, rows = _run(
            tmp_path, "otel", "otel_traces.jsonl", capsys, "--tier-from", "app.tier"
        )
        assert code == EXIT_SUCCESS
        assert pred["summary"]["records"] == 4
        assert pred["summary"]["ignored_reasons"] == {
            "not_a_genai_span": 1,
            "operation:execute_tool": 1,
        }
        chat, failed = rows
        assert chat == {
            "schema_version": 2,
            "source": "otel",
            "created_ts": 1790380800.0,
            "model": "claude-opus-5",
            "provider": "anthropic",
            "request_id": "msg_123",
            "latency_ms": 1250.0,
            "success": True,
            "tokens_in": 2000,
            "tokens_out": 450,
            "tokens_cache_read": 1200,
            "tokens_cache_write": 300,
            "tokens_reasoning": 200,
            "tier": "premium",  # from the resource attribute
        }
        # Pre-1.37 attribute names (gen_ai.system, usage.prompt_tokens) and an ERROR status.
        assert failed["provider"] == "openai"
        assert failed["tokens_in"] == 80
        assert failed["success"] is False
        assert failed["request_id"] == "eee19b7ec3c1b176"
        assert failed["latency_ms"] == pytest.approx(400.0)

    def test_old_cache_creation_attribute_name(self) -> None:
        attrs = [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
            {"key": "gen_ai.request.model", "value": {"stringValue": "m"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": 100}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "5"}},
            {"key": "gen_ai.usage.cache_creation.input_tokens", "value": {"intValue": "40"}},
        ]
        doc = {
            "resourceSpans": [
                {"scopeSpans": [{"spans": [{"startTimeUnixNano": "1", "attributes": attrs}]}]}
            ]
        }
        (row,) = ingest("otel", [doc]).rows
        assert row["tokens_cache_write"] == 40
        assert row["tokens_in"] == 100

    def test_non_otlp_document_is_skipped(self) -> None:
        result = ingest("otel", [{"spans": []}])
        assert result.skipped == {"not_otlp_trace_data": 1}


class TestProviderUsageExports:
    def test_openai_usage(self, tmp_path: Path, capsys: CapSys) -> None:
        code, pred, rows = _run(
            tmp_path, "openai-usage", "openai_usage.json", capsys, "--tier-from", "project_id"
        )
        assert code == EXIT_VALIDATION_FAILED
        assert pred["summary"]["skipped_reasons"] == {"batch_pricing_not_modeled": 1}
        assert pred["summary"]["ignored_reasons"] == {"empty_result": 1}
        assert rows == [
            {
                "schema_version": 2,
                "source": "openai-usage",
                "created_ts": 1790380800,
                "model": "gpt-5",
                "provider": "openai",
                "aggregate": True,
                "requests": 5,
                "tokens_in": 5000,
                "tokens_cache_read": 1800,
                "tokens_cache_write": 200,
                "tokens_out": 1000,
                "tier": "proj_abc",
            }
        ]

    def test_anthropic_usage(self, tmp_path: Path, capsys: CapSys) -> None:
        code, pred, rows = _run(
            tmp_path,
            "anthropic-usage",
            "anthropic_usage.json",
            capsys,
            "--tier-from",
            "workspace_id",
        )
        assert code == EXIT_VALIDATION_FAILED
        assert pred["summary"]["skipped_reasons"] == {
            "missing_model": 1,
            "service_tier_not_modeled:batch": 1,
        }
        # Total input = uncached 1500 + cache read 200 + 5m writes 300 + 1h writes 0.
        assert rows == [
            {
                "schema_version": 2,
                "source": "anthropic-usage",
                "created_ts": _epoch("2026-09-25T00:00:00"),
                "model": "claude-opus-5",
                "provider": "anthropic",
                "aggregate": True,
                "tokens_in": 2000,
                "tokens_cache_read": 200,
                "tokens_cache_write": 300,
                "tokens_cache_write_1h": 0,
                "tokens_out": 500,
                "tier": "wrkspc_01",
            }
        ]


class TestEndToEnd:
    def test_ingested_rows_feed_simulate(self, tmp_path: Path, capsys: CapSys) -> None:
        """Anthropic usage -> v2 rows -> simulate at list price (hand-computed)."""
        rows_path = tmp_path / "rows.jsonl"
        main(
            [
                "ingest",
                "--format",
                "anthropic-usage",
                "--input",
                str(FIXTURES / "anthropic_usage.json"),
                "--rows",
                str(rows_path),
            ]
        )
        capsys.readouterr()
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({"default_model": "claude-opus-5"}), encoding="utf-8")
        code = main(["simulate", "--input", str(rows_path), "--policy", str(policy)])
        out = envelope(capsys.readouterr().out)["predicate"]["summary"]
        assert code == EXIT_SUCCESS
        # claude-opus-5: $5 in, $0.50 cache hit, $6.25 5m write, $25 out.
        expected = (1500 * 5 + 200 * 0.5 + 300 * 6.25 + 500 * 25) / 1_000_000
        assert out["total_cost_usd"] == pytest.approx(round(expected, 6))
        assert out["unknown_request_rows"] == 1

        assert main(["summarize", "--input", str(rows_path)]) == EXIT_SUCCESS
        summary = envelope(capsys.readouterr().out)["predicate"]
        assert summary["details"]["models"][0]["unknown_request_rows"] == 1
        assert summary["summary"]["total_requests"] == 0


class TestInputHandling:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2026-09-25T10:00:00Z", "2026-09-25T10:00:00"),
            ("2026-09-25T10:00:00.1234567Z", "2026-09-25T10:00:00.123456"),
            ("2026-09-25 10:00:00", "2026-09-25T10:00:00"),
            ("2026-09-25T12:00:00+02:00", "2026-09-25T10:00:00"),
        ],
    )
    def test_parse_timestamp(self, text: str, expected: str) -> None:
        assert parse_timestamp(text) == pytest.approx(_epoch(expected))

    def test_parse_timestamp_rejects_garbage(self) -> None:
        assert parse_timestamp("yesterday") is None
        assert parse_timestamp(-5) is None
        assert parse_timestamp(None) is None

    def test_rows_must_be_jsonl_and_not_the_input(self, tmp_path: Path) -> None:
        src = tmp_path / "in.jsonl"
        src.write_text((FIXTURES / "litellm_payload.jsonl").read_text(), encoding="utf-8")
        base = ["ingest", "--format", "litellm-payload", "--input", str(src)]
        assert main([*base, "--rows", str(tmp_path / "rows.json")]) == EXIT_CLI_ERROR
        assert main([*base, "--rows", str(src)]) == EXIT_CLI_ERROR

    def test_invalid_normalized_row_is_skipped(self) -> None:
        # Cache reads larger than the input total cannot be a valid row.
        rec = {"model": "m", "startTime": 1.0, "prompt_tokens": 10, "completion_tokens": 1}
        rec["hidden_params"] = {"usage_object": {"prompt_tokens_details": {"cached_tokens": 50}}}
        result = ingest("litellm-payload", [rec])
        assert result.skipped == {"invalid_row:tokens_in:must_include_cache_tokens": 1}

    def test_non_object_records_are_skipped(self) -> None:
        assert ingest("litellm-spendlogs", [[1, "x"]]).skipped == {"not_an_object": 2}
        assert ingest("litellm-payload", [["x"]]).skipped == {"not_an_object": 1}

    def test_unknown_format(self) -> None:
        with pytest.raises(ValueError, match="unknown format"):
            ingest("csv", [])
