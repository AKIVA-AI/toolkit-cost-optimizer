"""Token-class pricing: cached input, cache writes, reasoning and long-context tiers.

Expected values are hand-computed from the providers' published pricing pages and
worked examples, cited on each test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from report_helpers import payload

from toolkit_cost_latency_opt.cli import EXIT_SUCCESS, EXIT_VALIDATION_FAILED, main
from toolkit_cost_latency_opt.pricing import (
    NO_CACHE_WRITE_1H_PRICE,
    NO_CACHE_WRITE_PRICE,
    NO_CACHED_INPUT_PRICE,
    NO_TOKENS,
    PriceTable,
    load_builtin_prices,
)

CapSys = pytest.CaptureFixture[str]
BUILTIN = load_builtin_prices()
M = 1_000_000


def _cost(model: str, **tokens: int) -> float:
    result = BUILTIN.row_cost(model, tokens)
    assert result.reason is None, result.reason
    assert result.cost_usd is not None
    return result.cost_usd


class TestPublishedWorkedExamples:
    def test_openai_cache_write_then_one_read_is_1_35x(self) -> None:
        # developers.openai.com/api/docs/guides/prompt-caching (retrieved 2026-09-26):
        # "For GPT-5.6 and later, cache writes cost 1.25x the standard, uncached input-token
        # rate ... subsequent reads cost only 0.1x that rate. Writing a prefix once and fully
        # reusing it once costs 1.35x its ordinary input cost".
        n = 10_000
        ordinary = _cost("gpt-5.6-terra", tokens_in=n, tokens_out=0)
        write = _cost("gpt-5.6-terra", tokens_in=n, tokens_cache_write=n, tokens_out=0)
        read = _cost("gpt-5.6-terra", tokens_in=n, tokens_cache_read=n, tokens_out=0)
        assert (write + read) / ordinary == pytest.approx(1.35)

    def test_openai_one_write_nine_reads_is_2_15x(self) -> None:
        # Same page: "across ten requests, one write and nine full reads cost 2.15x".
        n = 5_000
        ordinary = _cost("gpt-6-sol", tokens_in=n, tokens_out=0)
        write = _cost("gpt-6-sol", tokens_in=n, tokens_cache_write=n, tokens_out=0)
        read = _cost("gpt-6-sol", tokens_in=n, tokens_cache_read=n, tokens_out=0)
        assert (write + 9 * read) / ordinary == pytest.approx(2.15)

    def test_anthropic_cache_break_even(self) -> None:
        # platform.claude.com/docs/en/about-claude/pricing (retrieved 2026-09-26): "A cache
        # hit costs 10% of the standard input price, which means caching pays off after one
        # cache read for the 5-minute duration (1.25x write), or after two cache reads for
        # the 1-hour duration (2x write)."
        n = 100_000
        base = _cost("claude-sonnet-4-6", tokens_in=n, tokens_out=0)
        w5 = _cost("claude-sonnet-4-6", tokens_in=n, tokens_cache_write=n, tokens_out=0)
        w1h = _cost(
            "claude-sonnet-4-6",
            tokens_in=n,
            tokens_cache_write=n,
            tokens_cache_write_1h=n,
            tokens_out=0,
        )
        hit = _cost("claude-sonnet-4-6", tokens_in=n, tokens_cache_read=n, tokens_out=0)
        assert w5 / base == pytest.approx(1.25)
        assert w1h / base == pytest.approx(2.0)
        assert hit / base == pytest.approx(0.1)
        assert w5 + hit < 2 * base  # 5m: pays off after one read
        assert w1h + hit > 2 * base  # 1h: not after one read...
        assert w1h + 2 * hit < 3 * base  # ...but after two

    def test_gemini_thinking_tokens_billed_as_output(self) -> None:
        # ai.google.dev/gemini-api/docs/thinking (retrieved 2026-09-26): usage example with
        # 62 input, 171 output and 297 thought tokens; "response pricing is the sum of output
        # tokens and thinking tokens". gemini-2.5-flash: $0.30 in, $2.50 out (incl. thinking).
        cost = _cost("gemini-2.5-flash", tokens_in=62, tokens_out=171 + 297, tokens_reasoning=297)
        assert cost == pytest.approx((62 * 0.30 + 468 * 2.50) / M)

    def test_openai_reasoning_tokens_billed_as_output(self) -> None:
        # developers.openai.com/api/docs/guides/reasoning: reasoning tokens "are billed as
        # output tokens". o4-mini: $1.10 in, $4.40 out.
        with_reasoning = _cost("o4-mini", tokens_in=1000, tokens_out=900, tokens_reasoning=800)
        without = _cost("o4-mini", tokens_in=1000, tokens_out=900)
        assert with_reasoning == without == pytest.approx((1000 * 1.10 + 900 * 4.40) / M)


class TestTierAndClassRules:
    def test_long_context_tier_applies_above_threshold(self) -> None:
        # gpt-5.4: short $2.50/$0.25/$15.00; >272K input: $5.00/$0.50/$22.50.
        at = _cost("gpt-5.4", tokens_in=272_000, tokens_out=1_000)
        above = _cost("gpt-5.4", tokens_in=272_001, tokens_cache_read=1, tokens_out=1_000)
        assert at == pytest.approx((272_000 * 2.50 + 1_000 * 15.0) / M)
        assert above == pytest.approx((272_000 * 5.00 + 1 * 0.50 + 1_000 * 22.50) / M)

    def test_aggregate_row_is_priced_at_base_tier_and_flagged(self) -> None:
        result = BUILTIN.row_cost(
            "gemini-2.5-pro", {"tokens_in": 900_000, "tokens_out": 1_000, "requests": 3}
        )
        assert result.cost_usd == pytest.approx((900_000 * 1.25 + 1_000 * 10.0) / M)
        assert result.base_tier_assumed is True

    def test_missing_class_prices_fail_closed(self) -> None:
        # Gemini publishes no per-token cache-write price.
        assert (
            BUILTIN.row_cost(
                "gemini-2.5-flash", {"tokens_in": 10, "tokens_cache_write": 5, "tokens_out": 1}
            ).reason
            == NO_CACHE_WRITE_PRICE
        )
        table = PriceTable.from_json(
            {"as_of": "2026-01-01", "models": {"m": {"input_per_1m": 1, "output_per_1m": 2}}},
            source="t",
        )
        assert (
            table.row_cost("m", {"tokens_in": 10, "tokens_cache_read": 1, "tokens_out": 1}).reason
            == NO_CACHED_INPUT_PRICE
        )
        assert (
            BUILTIN.row_cost(
                "gpt-4o",
                {
                    "tokens_in": 10,
                    "tokens_cache_write": 5,
                    "tokens_cache_write_1h": 5,
                    "tokens_out": 1,
                },
            ).reason
            == NO_CACHE_WRITE_1H_PRICE
        )
        assert BUILTIN.row_cost("gpt-4o", {"tokens_in": 10}).reason == NO_TOKENS

    def test_separately_priced_reasoning(self) -> None:
        table = PriceTable.from_json(
            {
                "as_of": "2026-01-01",
                "models": {"m": {"input_per_1m": 1, "output_per_1m": 2, "reasoning_per_1m": 5}},
            },
            source="t",
        )
        result = table.row_cost("m", {"tokens_in": 100, "tokens_out": 50, "tokens_reasoning": 30})
        assert result.cost_usd == pytest.approx((100 * 1 + 20 * 2 + 30 * 5) / M)

    def test_names_resolve_by_alias_and_provider_prefix(self) -> None:
        assert BUILTIN.resolve("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
        assert BUILTIN.resolve("openai/gpt-4o-mini") == "gpt-4o-mini"
        assert BUILTIN.resolve("anthropic/claude-sonnet-4-5-20250929") == "claude-sonnet-4-5"
        assert BUILTIN.resolve("gpt-4o-2099") is None  # no fuzzy matching

    @pytest.mark.parametrize(
        "entry",
        [
            {"input_per_1m": -1, "output_per_1m": 1},
            {"input_per_1m": float("nan"), "output_per_1m": 1},
            {"input_per_1m": 1, "output_per_1m": 1, "cached_input_per_1m": True},
            {"input_per_1m": 1, "output_per_1m": 1, "long_context": {"threshold_tokens": 0}},
            {"input_per_1m": 1, "output_per_1m": 1, "long_context": []},
            {"input_per_1m": 1, "output_per_1m": 1, "aliases": "x"},
            {"input_per_1m": 1, "output_per_1m": 1, "provider": ""},
        ],
    )
    def test_malformed_entries_rejected(self, entry: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            PriceTable.from_json({"as_of": "2026-01-01", "models": {"m": entry}}, source="t")

    def test_duplicate_alias_rejected(self) -> None:
        models = {
            "a": {"input_per_1m": 1, "output_per_1m": 1, "aliases": ["x"]},
            "b": {"input_per_1m": 1, "output_per_1m": 1, "aliases": ["x"]},
        }
        with pytest.raises(ValueError, match="two models"):
            PriceTable.from_json({"as_of": "2026-01-01", "models": models}, source="t")


class TestBuiltinTableMatchesSources:
    """Spot checks against the pages cited in model_prices.json (retrieved 2026-09-26)."""

    @pytest.mark.parametrize(
        ("model", "inp", "cached", "write", "write_1h", "out"),
        [
            ("claude-opus-5-5", 4.0, 0.20, 5.0, 8.0, 20.0),
            ("claude-sonnet-5", 2.0, 0.20, 2.5, 4.0, 10.0),
            ("claude-haiku-4-5", 1.0, 0.10, 1.25, 2.0, 5.0),
            ("gpt-5.6-terra", 2.0, 0.20, 2.5, None, 12.0),
            ("gpt-5.4-mini", 0.75, 0.075, 0.75, None, 4.5),
            ("gpt-4o-mini", 0.15, 0.075, 0.15, None, 0.6),
            ("gemini-2.5-flash", 0.30, 0.03, None, None, 2.5),
            ("gemini-3.5-flash", 1.50, 0.15, None, None, 9.0),
        ],
    )
    def test_prices(
        self,
        model: str,
        inp: float,
        cached: float,
        write: float | None,
        write_1h: float | None,
        out: float,
    ) -> None:
        p = BUILTIN.models[model]
        assert (p.input_per_1m, p.cached_input_per_1m, p.output_per_1m) == (inp, cached, out)
        assert p.cache_write_per_1m == write
        assert p.cache_write_1h_per_1m == write_1h

    def test_every_builtin_model_has_provider_and_cached_price(self) -> None:
        raw = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "src/toolkit_cost_latency_opt/data/model_prices.json"
            ).read_text(encoding="utf-8")
        )
        assert raw["as_of"] == "2026-09-26"
        assert set(raw["sources"]) == {"anthropic", "openai", "gemini"}
        for name, price in BUILTIN.models.items():
            assert price.provider in raw["sources"], name
            assert price.cached_input_per_1m is not None, name


class TestSimulateWithTokenClasses:
    def _run(self, tmp_path: Path, rows: list[dict[str, Any]], model: str, capsys: CapSys):
        logs = tmp_path / "logs.jsonl"
        logs.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({"default_model": model}), encoding="utf-8")
        code = main(["simulate", "--input", str(logs), "--policy", str(policy)])
        return code, payload(capsys.readouterr().out)

    def test_cached_tokens_reduce_simulated_cost(self, tmp_path: Path, capsys: CapSys) -> None:
        row = {
            "schema_version": 2,
            "created_ts": 1.0,
            "model": "claude-opus-5",
            "tokens_in": 10_000,
            "tokens_cache_read": 8_000,
            "tokens_out": 500,
        }
        code, out = self._run(tmp_path, [row], "gpt-5.4-mini", capsys)
        assert code == EXIT_SUCCESS
        assert out["total_cost_usd"] == pytest.approx(
            round((2_000 * 0.75 + 8_000 * 0.075 + 500 * 4.5) / M, 6)
        )

    def test_unpriceable_class_is_reported(self, tmp_path: Path, capsys: CapSys) -> None:
        rows = [
            {
                "schema_version": 2,
                "created_ts": 1.0,
                "model": "x",
                "tokens_in": 100,
                "tokens_cache_write": 50,
                "tokens_out": 5,
            },
            {"schema_version": 2, "created_ts": 1.0, "model": "x", "requests": 4},
        ]
        code, out = self._run(tmp_path, rows, "gemini-2.5-flash", capsys)
        assert code == EXIT_VALIDATION_FAILED
        assert out["complete"] is False
        assert out["total_rows"] == 2
        assert out["total_requests"] == 5
        assert out["unpriced_requests"] == 5
        assert out["unpriced_reasons"] == {NO_CACHE_WRITE_PRICE: 1, NO_TOKENS: 1}
