"""`route`: counterfactual routing under a quality floor, checked against hand computation.

Fixture (prices in USD per 1M tokens): cheap 1/2, mid 4/8, strong 10/30.
Traffic, all served by `strong` today:
  tier simple : 3 requests x (1000 in, 500 out)  -> cheap .006, mid .024, strong .075
  tier complex: 2 requests x (2000 in, 1000 out) -> cheap .008, mid .032, strong .100
Weights: simple 3/5 = .6, complex 2/5 = .4.
Quality observations:
  simple : cheap [.8 .9 1] (mean .90), mid [.9 .95 1] (.95), strong [.9 .95 1] (.95)
  complex: cheap [.2 .4 .6] (.40),     mid [.6 .8 1]   (.80), strong [.9 .9 .9]  (.90)
The nine policies (simple, complex) -> (cost, quality = .6 q_s + .4 q_c):
  cheap,cheap .014 .70 | mid,cheap .032 .73 | cheap,mid .038 .86 | mid,mid .056 .89
  strong,cheap .083 .73 | cheap,strong .106 .90 | strong,mid .107 .89
  mid,strong .124 .93 | strong,strong .175 .93 (the baseline)
Pareto frontier: the first four, cheap,strong and mid,strong (strong,cheap, strong,mid and
strong,strong are each beaten by a cheaper policy of at least equal quality).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any

import pytest
from report_helpers import envelope

from toolkit_cost_latency_opt.cli import (
    EXIT_CLI_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_FAILED,
    main,
)
from toolkit_cost_latency_opt.routing import Estimate

CapSys = pytest.CaptureFixture[str]

PRICES = {
    "as_of": "2026-01-01",
    "models": {
        "cheap": {"input_per_1m": 1, "output_per_1m": 2},
        "mid": {"input_per_1m": 4, "output_per_1m": 8},
        "strong": {"input_per_1m": 10, "output_per_1m": 30},
    },
}
QUALITY = {
    "simple": {"cheap": [0.8, 0.9, 1.0], "mid": [0.9, 0.95, 1.0], "strong": [0.9, 0.95, 1.0]},
    "complex": {"cheap": [0.2, 0.4, 0.6], "mid": [0.6, 0.8, 1.0], "strong": [0.9, 0.9, 0.9]},
}


def _row(tier: str, tin: int, tout: int, model: str = "strong", **extra: Any) -> dict[str, Any]:
    row = {
        "schema_version": 2,
        "created_ts": 1_000.0,
        "model": model,
        "tier": tier,
        "tokens_in": tin,
        "tokens_out": tout,
    }
    row.update(extra)
    return row


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _setup(tmp_path: Path) -> list[str]:
    traffic = [_row("simple", 1000, 500) for _ in range(3)]
    traffic += [_row("complex", 2000, 1000) for _ in range(2)]
    traffic[-1]["created_ts"] = 1_000.0 + 3 * 86_400  # a three-day window
    quality = [
        _row(tier, 1, 1, model, quality=q)
        for tier, models in QUALITY.items()
        for model, scores in models.items()
        for q in scores
    ]
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(PRICES), encoding="utf-8")
    return [
        "route",
        "--input",
        str(_write_jsonl(tmp_path / "traffic.jsonl", traffic)),
        "--quality-rows",
        str(_write_jsonl(tmp_path / "quality.jsonl", quality)),
        "--prices",
        str(prices),
        "--min-quality-samples",
        "3",
    ]


def _route(argv: list[str], capsys: CapSys) -> tuple[int, dict[str, Any], dict[str, Any]]:
    code = main(argv)
    pred = envelope(capsys.readouterr().out)["predicate"]
    return code, pred["summary"], pred["details"]


class TestHandComputedFixture:
    def test_same_quality_as_today_costs_less(self, tmp_path: Path, capsys: CapSys) -> None:
        code, summary, details = _route(_setup(tmp_path), capsys)
        assert code == EXIT_SUCCESS
        assert summary["baseline"]["cost_usd"] == pytest.approx(0.175)
        assert summary["baseline"]["quality"] == pytest.approx(0.93)
        assert summary["quality_floor_source"] == "baseline"
        assert summary["policies_evaluated"] == 9
        chosen = summary["chosen"]
        assert chosen["policy"] == {"complex": "strong", "simple": "mid"}
        assert chosen["cost_usd"] == pytest.approx(0.124)
        assert chosen["quality"] == pytest.approx(0.93)
        assert summary["savings_usd"] == pytest.approx(0.051)
        assert summary["savings_pct"] == pytest.approx(100 * 0.051 / 0.175, abs=1e-4)
        assert summary["window_seconds"] == pytest.approx(3 * 86_400)

    def test_pareto_frontier(self, tmp_path: Path, capsys: CapSys) -> None:
        _, summary, details = _route(_setup(tmp_path), capsys)
        frontier = [(p["policy"]["simple"], p["policy"]["complex"]) for p in details["frontier"]]
        assert frontier == [
            ("cheap", "cheap"),
            ("mid", "cheap"),
            ("cheap", "mid"),
            ("mid", "mid"),
            ("cheap", "strong"),
            ("mid", "strong"),
        ]
        costs = [p["cost_usd"] for p in details["frontier"]]
        quals = [p["quality"] for p in details["frontier"]]
        assert costs == pytest.approx([0.014, 0.032, 0.038, 0.056, 0.106, 0.124])
        assert quals == pytest.approx([0.70, 0.73, 0.86, 0.89, 0.90, 0.93])
        assert summary["frontier_size"] == 6

    def test_explicit_floor(self, tmp_path: Path, capsys: CapSys) -> None:
        code, summary, _ = _route([*_setup(tmp_path), "--min-quality", "0.85"], capsys)
        assert code == EXIT_SUCCESS
        assert summary["chosen"]["policy"] == {"complex": "mid", "simple": "cheap"}
        assert summary["chosen"]["cost_usd"] == pytest.approx(0.038)

    def test_confidence_bound_changes_the_choice(self, tmp_path: Path, capsys: CapSys) -> None:
        argv = [*_setup(tmp_path), "--min-quality", "0.85", "--confidence", "0.95"]
        code, summary, _ = _route(argv, capsys)
        assert code == EXIT_SUCCESS
        chosen = summary["chosen"]
        # mid on simple: s^2 = (.05^2 + 0 + .05^2) / 2 = .0025, n = 3; strong on complex: s^2 = 0.
        # Var(Q) = .6^2 * .0025 / 3 (Cochran 1977, ch. 5, stratified mean without fpc).
        se = math.sqrt(0.36 * 0.0025 / 3)
        z = NormalDist().inv_cdf(0.95)  # 1.6449 (standard normal table)
        assert chosen["policy"] == {"complex": "strong", "simple": "mid"}
        assert chosen["quality_se"] == pytest.approx(se, abs=1e-6)
        assert chosen["quality_lower_bound"] == pytest.approx(0.93 - z * se, abs=1e-6)
        # cheap,strong has mean .90 >= .85 but its bound .90 - z*sqrt(.36*.01/3) = .843 < .85.
        assert 0.90 - z * math.sqrt(0.36 * 0.01 / 3) < 0.85

    def test_unreachable_floor_fails(self, tmp_path: Path, capsys: CapSys) -> None:
        code, summary, details = _route([*_setup(tmp_path), "--min-quality", "0.99"], capsys)
        assert code == EXIT_VALIDATION_FAILED
        assert summary["feasible"] is False
        assert summary["reason"] == "no_policy_meets_floor"
        assert len(details["frontier"]) == 6

    def test_policy_out_reproduces_cost_in_simulate(self, tmp_path: Path, capsys: CapSys) -> None:
        argv = _setup(tmp_path)
        policy = tmp_path / "chosen.json"
        assert main([*argv, "--policy-out", str(policy)]) == EXIT_SUCCESS
        capsys.readouterr()
        assert json.loads(policy.read_text(encoding="utf-8")) == {
            "default_model": "mid",
            "tiers": {"complex": "strong", "simple": "mid"},
        }
        sim = ["simulate", "--input", argv[2], "--policy", str(policy), "--prices", argv[6]]
        assert main(sim) == EXIT_SUCCESS
        assert envelope(capsys.readouterr().out)["predicate"]["summary"][
            "total_cost_usd"
        ] == pytest.approx(0.124)

    def test_candidates_restrict_the_search(self, tmp_path: Path, capsys: CapSys) -> None:
        argv = [*_setup(tmp_path), "--candidates", "cheap,strong", "--min-quality", "0.85"]
        code, summary, _ = _route(argv, capsys)
        assert code == EXIT_SUCCESS
        assert summary["policies_evaluated"] == 4
        assert summary["chosen"]["policy"] == {"complex": "strong", "simple": "cheap"}

    def test_min_samples_excludes_thin_estimates(self, tmp_path: Path, capsys: CapSys) -> None:
        argv = _setup(tmp_path)
        argv[argv.index("--min-quality-samples") + 1] = "4"
        code, summary, details = _route([*argv, "--min-quality", "0.5"], capsys)
        assert code == EXIT_VALIDATION_FAILED
        assert summary["reason"] == "tier_without_candidates"
        assert {e["reason"] for e in details["excluded"]} == {"no_quality_estimate"}


def _eval_report(path: Path, cases: list[dict[str, Any]], verdict: str = "pass") -> Path:
    env = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "suite", "digest": {"sha256": "0" * 64}}],
        "predicateType": "https://github.com/AKIVA-AI/toolkit-eval-harness/report/v1",
        "predicate": {
            "tool": {"name": "toolkit-eval-harness", "version": "1.0.0"},
            "kind": "eval.run",
            "created_at": "2026-09-26T00:00:00Z",
            "verdict": verdict,
            "exit_code": 0 if verdict == "pass" else 1,
            "inputs": [],
            "summary": {},
            "details": {"cases": cases},
        },
    }
    path.write_text(json.dumps(env), encoding="utf-8")
    return path


class TestEvalReports:
    def _traffic(self, tmp_path: Path) -> list[str]:
        rows = [_row("a", 1000, 100), _row("b", 1000, 100)]
        prices = tmp_path / "prices.json"
        prices.write_text(json.dumps(PRICES), encoding="utf-8")
        return [
            "route",
            "--input",
            str(_write_jsonl(tmp_path / "t.jsonl", rows)),
            "--prices",
            str(prices),
            "--min-quality-samples",
            "2",
            "--min-quality",
            "0.5",
        ]

    def test_shared_estimate_is_fully_correlated(self, tmp_path: Path, capsys: CapSys) -> None:
        # Untagged cases apply to both tiers: one estimate with mean .75, s^2 = .25/3, n = 4.
        report = _eval_report(
            tmp_path / "cheap.json",
            [{"id": str(i), "score": s} for i, s in enumerate([0.5, 1, 0.5, 1])],
        )
        argv = [*self._traffic(tmp_path), "--eval", f"cheap={report}", "--confidence", "0.9"]
        code, summary, _ = _route(argv, capsys)
        assert code == EXIT_SUCCESS
        se2 = (4 * 0.25**2 / 3) / 4
        # Tiers a and b (weight .5 each) share the estimate: Var = (.5 + .5)^2 * se2, not
        # (.5^2 + .5^2) * se2.
        assert summary["chosen"]["quality"] == pytest.approx(0.75)
        assert summary["chosen"]["quality_se"] == pytest.approx(math.sqrt(se2), abs=1e-6)
        assert summary["baseline"]["quality"] is None  # strong has no quality data
        assert summary["baseline"]["cost_usd"] == pytest.approx(2 * (10_000 + 3_000) / 1e6)

    def test_tier_tags_and_fallback(self, tmp_path: Path, capsys: CapSys) -> None:
        cases = [
            {"id": "1", "score": 1.0, "tags": ["tier:a"]},
            {"id": "2", "score": 1.0, "tags": ["tier:a"]},
            {"id": "3", "score": 0.2, "tags": []},
            {"id": "4", "score": 0.4},
        ]
        report = _eval_report(tmp_path / "mid.json", cases, verdict="fail")
        code, _, details = _route([*self._traffic(tmp_path), "--eval", f"mid={report}"], capsys)
        assert code == EXIT_SUCCESS  # policy quality .5 * 1.0 + .5 * .3 = .65 >= .5
        opts = {t["tier"]: t["options"][0] for t in details["tiers"]}
        assert opts["a"]["quality"] == pytest.approx(1.0)
        assert opts["a"]["quality_from_tier"] == "a"
        assert opts["b"]["quality"] == pytest.approx(0.3)
        assert opts["b"]["quality_from_tier"] == "*"

    @pytest.mark.parametrize(
        ("cases", "verdict"),
        [
            ([{"id": "1", "score": 1.5}], "pass"),
            ([{"id": "1", "score": True}], "pass"),
            ([{"id": "1"}], "pass"),
            ([], "pass"),
            ([{"id": "1", "score": 1.0}], "error"),
        ],
    )
    def test_bad_reports_are_errors(
        self, tmp_path: Path, capsys: CapSys, cases: list[dict[str, Any]], verdict: str
    ) -> None:
        report = _eval_report(tmp_path / "r.json", cases, verdict)
        assert main([*self._traffic(tmp_path), "--eval", f"cheap={report}"]) == EXIT_CLI_ERROR

    def test_not_an_envelope(self, tmp_path: Path) -> None:
        bad = tmp_path / "r.json"
        bad.write_text(json.dumps({"cases": [{"score": 1}]}), encoding="utf-8")
        assert main([*self._traffic(tmp_path), "--eval", f"cheap={bad}"]) == EXIT_CLI_ERROR
        assert main([*self._traffic(tmp_path), "--eval", "cheap"]) == EXIT_CLI_ERROR


class TestRouteInputs:
    def test_aggregate_rows_without_counts_rejected(self, tmp_path: Path) -> None:
        rows = [_row("a", 10, 1, aggregate=True)]
        argv = ["route", "--input", str(_write_jsonl(tmp_path / "t.jsonl", rows))]
        assert main([*argv, "--min-quality", "0.5"]) == EXIT_CLI_ERROR

    def test_rows_need_tokens(self, tmp_path: Path) -> None:
        rows = [{"schema_version": 2, "created_ts": 1.0, "model": "m", "quality": 0.5}]
        argv = ["route", "--input", str(_write_jsonl(tmp_path / "t.jsonl", rows))]
        assert main(argv) == EXIT_CLI_ERROR

    def test_unpriceable_class_excludes_candidate(self, tmp_path: Path, capsys: CapSys) -> None:
        # `cheap` has no cache-write price, so it cannot serve a tier with cache writes.
        rows = [_row("a", 100, 10, tokens_cache_write=50, quality=0.9) for _ in range(2)]
        rows += [_row("a", 100, 10, model="cheap", quality=0.9) for _ in range(2)]
        prices = dict(PRICES, models=dict(PRICES["models"]))
        prices["models"]["strong"] = {
            "input_per_1m": 10,
            "output_per_1m": 30,
            "cache_write_per_1m": 12,
        }
        pf = tmp_path / "p.json"
        pf.write_text(json.dumps(prices), encoding="utf-8")
        argv = [
            "route",
            "--input",
            str(_write_jsonl(tmp_path / "t.jsonl", rows)),
            "--prices",
            str(pf),
        ]
        code, summary, details = _route([*argv, "--min-quality-samples", "2"], capsys)
        assert code == EXIT_SUCCESS
        assert {"tier": "a", "model": "cheap", "reason": "unpriced_token_class"} in details[
            "excluded"
        ]
        assert summary["chosen"]["policy"] == {"a": "strong"}

    def test_too_many_policies(self, tmp_path: Path) -> None:
        assert main([*_setup(tmp_path), "--max-policies", "8"]) == EXIT_CLI_ERROR

    @pytest.mark.parametrize(
        "flag", [["--min-quality", "2"], ["--confidence", "0.3"], ["--min-quality-samples", "0"]]
    )
    def test_bad_arguments(self, tmp_path: Path, flag: list[str]) -> None:
        assert main([*_setup(tmp_path), *flag]) == EXIT_CLI_ERROR

    def test_no_floor_and_unknown_baseline(self, tmp_path: Path) -> None:
        argv = _setup(tmp_path)
        argv[argv.index("--quality-rows") + 1] = str(
            _write_jsonl(tmp_path / "q.jsonl", [_row("simple", 1, 1, "cheap", quality=1.0)] * 3)
        )
        assert main(argv) == EXIT_CLI_ERROR


def test_estimate_matches_statistics_module() -> None:
    import statistics

    scores = [0.2, 0.4, 0.9, 1.0]
    est = Estimate.from_scores("t", "m", scores)
    assert est.mean == pytest.approx(statistics.fmean(scores))
    assert est.variance == pytest.approx(statistics.variance(scores))
    assert Estimate.from_scores("t", "m", [0.5]).variance is None


def test_single_observation_has_no_confidence_bound(tmp_path: Path, capsys: CapSys) -> None:
    """With one score there is no variance estimate, so a confidence floor cannot be met."""
    prices = tmp_path / "p.json"
    prices.write_text(json.dumps(PRICES), encoding="utf-8")
    rows = [_row("a", 10, 1, quality=1.0)]
    argv = ["route", "--input", str(_write_jsonl(tmp_path / "t.jsonl", rows))]
    argv += ["--prices", str(prices), "--min-quality-samples", "1", "--min-quality", "0.1"]
    assert main(argv) == EXIT_SUCCESS
    capsys.readouterr()
    assert main([*argv, "--confidence", "0.9"]) == EXIT_VALIDATION_FAILED
    summary = envelope(capsys.readouterr().out)["predicate"]["summary"]
    assert summary["reason"] == "no_policy_meets_floor"
