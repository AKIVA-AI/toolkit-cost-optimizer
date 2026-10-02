"""Behavioral tests for `simulate`: it must re-price each request under the
policy's target model from token counts, not reuse the logged cost."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from report_helpers import envelope, payload

from toolkit_cost_latency_opt.cli import (
    EXIT_CLI_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_FAILED,
    main,
)
from toolkit_cost_latency_opt.pricing import PriceTable, load_builtin_prices, load_price_file

CapSys = pytest.CaptureFixture[str]

# Custom price table used by most tests (USD per 1M tokens).
PRICES = {
    "as_of": "2026-01-01",
    "models": {
        "cheap": {"input_per_1m": 1.0, "output_per_1m": 2.0},
        "strong": {"input_per_1m": 10.0, "output_per_1m": 20.0},
    },
}


def _row(model: str, tier: str, cost: float, tin: int | None, tout: int | None) -> dict:
    row: dict[str, object] = {
        "schema_version": 1,
        "created_ts": 1.0,
        "model": model,
        "tier": tier,
        "latency_ms": 100,
        "cost_usd": cost,
        "success": True,
    }
    if tin is not None:
        row["tokens_in"] = tin
    if tout is not None:
        row["tokens_out"] = tout
    return row


def _setup(tmp_path: Path, rows: list[dict], policy: dict, prices: dict | None = PRICES):
    logs = tmp_path / "logs.jsonl"
    logs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(policy), encoding="utf-8")
    args = ["simulate", "--input", str(logs), "--policy", str(pol)]
    if prices is not None:
        pf = tmp_path / "prices.json"
        pf.write_text(json.dumps(prices), encoding="utf-8")
        args += ["--prices", str(pf)]
    return args


def _run(args: list[str], capsys: CapSys) -> tuple[int, dict]:
    code = main(args)
    return code, payload(capsys.readouterr().out)


class TestSimulateReprices:
    def test_cost_depends_on_policy(self, tmp_path: Path, capsys: CapSys) -> None:
        """The same traffic costs different amounts under different policies."""
        rows = [_row("strong", "t", 99.0, 1_000_000, 1_000_000)]
        code_a, out_a = _run(_setup(tmp_path, rows, {"default_model": "cheap"}), capsys)
        code_b, out_b = _run(_setup(tmp_path, rows, {"default_model": "strong"}), capsys)
        assert code_a == code_b == EXIT_SUCCESS
        assert out_a["total_cost_usd"] == pytest.approx(3.0)  # 1*1 + 1*2
        assert out_b["total_cost_usd"] == pytest.approx(30.0)  # 1*10 + 1*20

    def test_logged_cost_is_not_reused(self, tmp_path: Path, capsys: CapSys) -> None:
        """The row's logged cost_usd (99.0) never leaks into the simulated total."""
        rows = [_row("strong", "t", 99.0, 500_000, 250_000)]
        code, out = _run(_setup(tmp_path, rows, {"default_model": "cheap"}), capsys)
        assert code == EXIT_SUCCESS
        assert out["total_cost_usd"] == pytest.approx(0.5 + 0.5)
        assert out["logged_cost_usd"] == pytest.approx(99.0)

    def test_tier_routing_prices_each_tier(self, tmp_path: Path, capsys: CapSys) -> None:
        rows = [
            _row("x", "fast", 0.0, 1_000_000, 0),
            _row("x", "deep", 0.0, 0, 1_000_000),
        ]
        policy = {"default_model": "cheap", "tiers": {"deep": "strong"}}
        code, out = _run(_setup(tmp_path, rows, policy), capsys)
        assert code == EXIT_SUCCESS
        by_model = {m["model"]: m for m in out["models"]}
        assert by_model["cheap"]["total_cost_usd"] == pytest.approx(1.0)
        assert by_model["strong"]["total_cost_usd"] == pytest.approx(20.0)
        assert out["total_cost_usd"] == pytest.approx(21.0)
        assert out["total_requests"] == 2
        assert out["priced_requests"] == 2
        assert out["unpriced_requests"] == 0
        assert out["complete"] is True

    def test_output_reports_price_source(self, tmp_path: Path, capsys: CapSys) -> None:
        rows = [_row("x", "t", 0.0, 10, 10)]
        code, out = _run(_setup(tmp_path, rows, {"default_model": "cheap"}), capsys)
        assert code == EXIT_SUCCESS
        assert out["prices"]["as_of"] == "2026-01-01"
        assert out["prices"]["source"].endswith("prices.json")


class TestSimulateMissingTokens:
    def test_rows_without_tokens_are_reported_not_priced(
        self, tmp_path: Path, capsys: CapSys
    ) -> None:
        """A row without token counts is counted as unpriced; its logged cost is not reused."""
        rows = [
            _row("x", "t", 5.0, 1_000_000, 1_000_000),
            _row("x", "t", 7.0, None, None),
            _row("x", "t", 7.0, 100, None),
        ]
        code, out = _run(_setup(tmp_path, rows, {"default_model": "cheap"}), capsys)
        assert code == EXIT_VALIDATION_FAILED
        assert out["complete"] is False
        assert out["total_requests"] == 3
        assert out["priced_requests"] == 1
        assert out["unpriced_requests"] == 2
        assert out["total_cost_usd"] == pytest.approx(3.0)
        assert out["logged_cost_usd"] == pytest.approx(5.0)
        assert out["models"][0]["unpriced_requests"] == 2

    def test_invalid_token_values_are_rejected(self, tmp_path: Path, capsys: CapSys) -> None:
        """A negative or boolean token count is a schema error, not a missing count."""
        rows = [_row("x", "t", 1.0, -5, 10), _row("x", "t", 1.0, 10, 10)]
        rows[1]["tokens_in"] = True  # bool is not a token count
        assert main(_setup(tmp_path, rows, {"default_model": "cheap"})) == EXIT_CLI_ERROR
        pred = envelope(capsys.readouterr().out)["predicate"]
        assert pred["verdict"] == "error"
        assert pred["summary"]["invalid_rows"] == 2
        fields = {(i["field"], i["message"]) for i in pred["details"]["issues"]}
        assert fields == {("tokens_in", "must_be_non_negative"), ("tokens_in", "expected_int")}


class TestSimulatePriceErrors:
    def test_unknown_target_model_fails_closed(self, tmp_path: Path, capsys: CapSys) -> None:
        rows = [_row("x", "t", 1.0, 10, 10)]
        args = _setup(tmp_path, rows, {"default_model": "not-in-table"})
        assert main(args) == EXIT_CLI_ERROR
        pred = envelope(capsys.readouterr().out)["predicate"]
        assert pred["verdict"] == "error"
        assert pred["exit_code"] == EXIT_CLI_ERROR
        assert "not-in-table" in pred["details"]["message"]

    def test_malformed_price_file(self, tmp_path: Path) -> None:
        rows = [_row("x", "t", 1.0, 10, 10)]
        bad = {"as_of": "2026-01-01", "models": {"cheap": {"input_per_1m": -1}}}
        args = _setup(tmp_path, rows, {"default_model": "cheap"}, prices=bad)
        assert main(args) == EXIT_CLI_ERROR

    def test_price_file_requires_as_of(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="as_of"):
            PriceTable.from_json({"models": {}}, source="x")

    def test_price_file_rejects_bool_price(self) -> None:
        obj = {"as_of": "2026-01-01", "models": {"m": {"input_per_1m": True, "output_per_1m": 1}}}
        with pytest.raises(ValueError, match="input_per_1m"):
            PriceTable.from_json(obj, source="x")


class TestBuiltinPrices:
    def test_builtin_table_is_dated_and_nonempty(self) -> None:
        table = load_builtin_prices()
        assert table.as_of  # dated
        assert len(table.models) >= 4
        for price in table.models.values():
            assert price.input_per_1m >= 0 and price.output_per_1m >= 0

    def test_builtin_used_when_no_prices_flag(self, tmp_path: Path, capsys: CapSys) -> None:
        table = load_builtin_prices()
        model = sorted(table.models)[0]
        p = table.models[model]
        rows = [_row("x", "t", 0.0, 1_000_000, 1_000_000)]
        args = _setup(tmp_path, rows, {"default_model": model}, prices=None)
        code, out = _run(args, capsys)
        assert code == EXIT_SUCCESS
        assert out["prices"]["source"] == "built-in"
        assert out["prices"]["as_of"] == table.as_of
        assert out["total_cost_usd"] == pytest.approx(p.input_per_1m + p.output_per_1m)

    def test_load_price_file_roundtrip(self, tmp_path: Path) -> None:
        pf = tmp_path / "p.json"
        pf.write_text(json.dumps(PRICES), encoding="utf-8")
        table = load_price_file(pf)
        assert table.cost_usd("strong", 1_000_000, 500_000) == pytest.approx(20.0)
