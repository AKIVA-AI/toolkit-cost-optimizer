"""Report envelope v1: every command prints an in-toto Statement v1 in canonical JSON."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from report_helpers import envelope

from toolkit_cost_latency_opt import __version__
from toolkit_cost_latency_opt.cli import (
    EXIT_CLI_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_FAILED,
    main,
)
from toolkit_cost_latency_opt.envelope import (
    PREDICATE_TYPE,
    canonical_json,
    sha256_bytes,
    verdict_for_exit,
)
from toolkit_cost_latency_opt.pricing import builtin_price_bytes

CapSys = pytest.CaptureFixture[str]

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "report-envelope.v1.json").read_text(
        encoding="utf-8"
    )
)
PRICES = {"as_of": "2026-01-01", "models": {"cheap": {"input_per_1m": 1.0, "output_per_1m": 2.0}}}


def _row(model: str = "cheap", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": 1,
        "created_ts": 1.0,
        "model": model,
        "latency_ms": 100,
        "cost_usd": 0.001,
        "success": True,
        "tokens_in": 1000,
        "tokens_out": 100,
    }
    row.update(extra)
    return row


def _files(tmp_path: Path, rows: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    logs = tmp_path / "logs.jsonl"
    logs.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"default_model": "cheap"}), encoding="utf-8")
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(PRICES), encoding="utf-8")
    return logs, policy, prices


def _run(argv: list[str], capsys: CapSys) -> tuple[int, dict[str, Any], str]:
    code = main(argv)
    text = capsys.readouterr().out
    return code, envelope(text), text


class TestPrimitives:
    def test_sha256_known_vector(self) -> None:
        # FIPS 180-2, Appendix B.1: SHA-256("abc").
        assert (
            sha256_bytes(b"abc")
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_canonical_json_is_sorted_compact_with_newline(self) -> None:
        a = canonical_json({"b": 1, "a": {"d": [1, 2], "c": "x"}})
        b = canonical_json({"a": {"c": "x", "d": [1, 2]}, "b": 1})
        assert a == b == '{"a":{"c":"x","d":[1,2]},"b":1}\n'

    def test_canonical_json_keeps_unicode_as_utf8(self) -> None:
        assert canonical_json({"m": "café"}) == '{"m":"café"}\n'

    def test_canonical_json_rejects_nan(self) -> None:
        with pytest.raises(ValueError):
            canonical_json({"x": float("nan")})

    @pytest.mark.parametrize(
        ("code", "verdict"), [(0, "pass"), (4, "fail"), (2, "error"), (3, "error")]
    )
    def test_verdict_for_exit(self, code: int, verdict: str) -> None:
        assert verdict_for_exit(code) == verdict


class TestCommandEnvelopes:
    def test_every_command_validates_against_schema(self, tmp_path: Path, capsys: CapSys) -> None:
        logs, policy, prices = _files(tmp_path, [_row(), _row()])
        runs = [
            (["validate", "--input", str(logs)], "cost.validate", EXIT_SUCCESS),
            (["summarize", "--input", str(logs)], "cost.summarize", EXIT_SUCCESS),
            (
                ["recommend", "--input", str(logs), "--min-samples", "1"],
                "cost.recommend",
                EXIT_SUCCESS,
            ),
            (
                ["recommend", "--input", str(logs), "--min-samples", "5"],
                "cost.recommend",
                EXIT_VALIDATION_FAILED,
            ),
            (
                [
                    "simulate",
                    "--input",
                    str(logs),
                    "--policy",
                    str(policy),
                    "--prices",
                    str(prices),
                ],
                "cost.simulate",
                EXIT_SUCCESS,
            ),
        ]
        for argv, kind, expected in runs:
            code, env, _ = _run(argv, capsys)
            assert code == expected, argv
            jsonschema.validate(env, SCHEMA)
            pred = env["predicate"]
            assert env["predicateType"] == PREDICATE_TYPE
            assert pred["kind"] == kind
            assert pred["tool"] == {"name": "toolkit-cost-optimizer", "version": __version__}
            assert pred["exit_code"] == code
            assert pred["verdict"] == ("pass" if code == 0 else "fail")

    def test_subject_is_input_digest(self, tmp_path: Path, capsys: CapSys) -> None:
        logs, _, _ = _files(tmp_path, [_row()])
        _, env, _ = _run(["summarize", "--input", str(logs)], capsys)
        expected = hashlib.sha256(logs.read_bytes()).hexdigest()
        assert env["subject"] == [{"name": str(logs), "digest": {"sha256": expected}}]

    def test_simulate_inputs_are_policy_and_prices(self, tmp_path: Path, capsys: CapSys) -> None:
        logs, policy, prices = _files(tmp_path, [_row()])
        argv = ["simulate", "--input", str(logs), "--policy", str(policy), "--prices", str(prices)]
        _, env, _ = _run(argv, capsys)
        digests = {i["name"]: i["digest"]["sha256"] for i in env["predicate"]["inputs"]}
        assert digests == {
            str(policy): hashlib.sha256(policy.read_bytes()).hexdigest(),
            str(prices): hashlib.sha256(prices.read_bytes()).hexdigest(),
        }

    def test_builtin_prices_digest_recorded(self, tmp_path: Path, capsys: CapSys) -> None:
        logs, _, _ = _files(tmp_path, [_row(tokens_in=1, tokens_out=1)])
        policy = tmp_path / "p2.json"
        policy.write_text(json.dumps({"default_model": "gpt-4o"}), encoding="utf-8")
        _, env, _ = _run(["simulate", "--input", str(logs), "--policy", str(policy)], capsys)
        inputs = {i["name"]: i["digest"]["sha256"] for i in env["predicate"]["inputs"]}
        assert inputs["built-in:model_prices.json"] == sha256_bytes(builtin_price_bytes())

    def test_incomplete_simulation_is_fail(self, tmp_path: Path, capsys: CapSys) -> None:
        row = _row()
        del row["tokens_in"]
        logs, policy, prices = _files(tmp_path, [row])
        argv = ["simulate", "--input", str(logs), "--policy", str(policy), "--prices", str(prices)]
        code, env, _ = _run(argv, capsys)
        assert code == EXIT_VALIDATION_FAILED
        assert env["predicate"]["verdict"] == "fail"
        assert env["predicate"]["summary"]["complete"] is False
        jsonschema.validate(env, SCHEMA)

    def test_error_envelope_when_input_was_read(self, tmp_path: Path, capsys: CapSys) -> None:
        logs, _, _ = _files(tmp_path, [_row()])
        bad_policy = tmp_path / "bad.json"
        bad_policy.write_text("[]", encoding="utf-8")
        code, env, _ = _run(["simulate", "--input", str(logs), "--policy", str(bad_policy)], capsys)
        assert code == EXIT_CLI_ERROR
        assert env["predicate"]["verdict"] == "error"
        assert env["predicate"]["exit_code"] == EXIT_CLI_ERROR
        jsonschema.validate(env, SCHEMA)

    def test_no_envelope_when_input_missing(self, tmp_path: Path, capsys: CapSys) -> None:
        code = main(["summarize", "--input", str(tmp_path / "missing.jsonl")])
        assert code == EXIT_CLI_ERROR
        assert capsys.readouterr().out == ""


class TestOutputOptions:
    def test_out_file_matches_stdout_and_is_canonical(
        self, tmp_path: Path, capsys: CapSys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
        logs, _, _ = _files(tmp_path, [_row()])
        out = tmp_path / "report.json"
        _, env, text = _run(["summarize", "--input", str(logs), "--out", str(out)], capsys)
        raw = out.read_bytes()
        assert raw.decode("utf-8") == text
        assert raw.endswith(b"\n") and b"\r" not in raw
        assert raw.decode("utf-8") == canonical_json(json.loads(raw))
        assert env["predicate"]["created_at"] == "2026-09-21T14:13:20Z"

    def test_reports_are_byte_reproducible(
        self, tmp_path: Path, capsys: CapSys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
        logs, _, _ = _files(tmp_path, [_row(), _row(model="other")])
        _, _, first = _run(["summarize", "--input", str(logs)], capsys)
        _, _, second = _run(["summarize", "--input", str(logs)], capsys)
        assert first == second

    def test_out_must_be_json(self, tmp_path: Path) -> None:
        logs, _, _ = _files(tmp_path, [_row()])
        assert main(["summarize", "--input", str(logs), "--out", str(tmp_path / "r.txt")]) == 2

    def test_legacy_json_prints_pre_envelope_shape(self, tmp_path: Path, capsys: CapSys) -> None:
        logs, _, _ = _files(tmp_path, [_row()])
        out = tmp_path / "report.json"
        assert main(["summarize", "--input", str(logs), "--legacy-json", "--out", str(out)]) == 0
        legacy = json.loads(capsys.readouterr().out)
        assert set(legacy) == {"models"}
        assert legacy["models"][0]["model"] == "cheap"
        # The --out file is still the envelope.
        assert json.loads(out.read_text(encoding="utf-8"))["_type"].startswith("https://in-toto")
