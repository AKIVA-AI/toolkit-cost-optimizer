from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Any

from . import __version__
from .envelope import TOOL_NAME, ReportContext, canonical_json, sha256_bytes
from .ingest import FORMATS, ingest, load_documents
from .io import LogFormatError, read_json, validate_file_path
from .litellm_config import build_config, to_yaml
from .observability import configure_structured_logging, get_metrics, track_time
from .policy import TierPolicy
from .pricing import PriceTable, builtin_price_bytes, load_builtin_prices, load_price_file
from .quality import parse_eval_spec, read_eval_cases
from .routing import (
    Option,
    Policy,
    TierData,
    build_estimates,
    choose,
    enumerate_policies,
    estimate_for,
    pareto_frontier,
    tier_cost,
)
from .rows import RowReader, RowValidationError
from .schema import request_count
from .security import sanitize_for_log
from .stats import ModelSummary, summarize_model

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_CLI_ERROR = 2
EXIT_UNEXPECTED_ERROR = 3
EXIT_VALIDATION_FAILED = 4

# `recommend` compares logged per-request cost, latency and success, so every row needs them.
RECOMMEND_REQUIRED_FIELDS = ("latency_ms", "success", "cost_usd")


@dataclass
class CommandResult:
    """What a subcommand produced: its exit code, envelope summary/details, legacy JSON."""

    exit_code: int
    summary: dict[str, Any]
    details: dict[str, Any] = field(default_factory=dict)
    legacy: dict[str, Any] = field(default_factory=dict)


def validate_cli_args(args: argparse.Namespace) -> None:
    """Validate CLI arguments with bounds checking.

    Raises:
        ValueError: If arguments are invalid
    """
    if hasattr(args, "max_p95_ms"):
        try:
            max_p95 = float(args.max_p95_ms)
        except ValueError as e:
            raise ValueError(
                f"Invalid --max-p95-ms: must be a number, got: {args.max_p95_ms}"
            ) from e

        if max_p95 <= 0:
            raise ValueError(f"--max-p95-ms must be positive, got: {max_p95}")

        if max_p95 > 3600000:
            raise ValueError(f"--max-p95-ms too large: {max_p95}ms (max: 1 hour = 3600000ms)")

    if hasattr(args, "min_success"):
        try:
            min_success = float(args.min_success)
        except ValueError as e:
            raise ValueError(
                f"Invalid --min-success: must be a number, got: {args.min_success}"
            ) from e

        if not (0.0 <= min_success <= 1.0):
            raise ValueError(f"--min-success must be between 0.0 and 1.0, got: {min_success}")

    if hasattr(args, "min_samples"):
        try:
            min_samples = int(args.min_samples)
        except ValueError as e:
            raise ValueError(
                f"Invalid --min-samples: must be an integer, got: {args.min_samples}"
            ) from e

        if min_samples < 1:
            raise ValueError(f"--min-samples must be >= 1, got: {min_samples}")


@track_time("validate")
def _cmd_validate(args: argparse.Namespace, ctx: ReportContext) -> CommandResult:
    """Validate logs against schema."""
    metrics = get_metrics()
    metrics.increment("analyses_run")
    input_path = validate_file_path(Path(args.input), {".jsonl"})
    ctx.add_subject(args.input, input_path)
    logger.info("Validating %s", sanitize_for_log(input_path.name))

    reader = RowReader(input_path, coerce=args.coerce_numeric_strings)
    for _ in reader:
        pass
    total, bad = reader.total, reader.invalid
    issue_list = reader.issue_list()
    summary = {"ok": bad == 0, "total": total, "invalid_rows": bad}
    legacy = {**summary, "issues": issue_list}
    if args.coerce_numeric_strings:
        summary["coerced_values"] = reader.coerced_values

    if bad == 0:
        logger.info(f"Validation passed: {total} rows valid")
        code = EXIT_SUCCESS
    else:
        logger.warning(f"Validation failed: {bad}/{total} rows invalid")
        code = EXIT_VALIDATION_FAILED
    return CommandResult(code, summary, {"issues": issue_list}, legacy)


@track_time("summarize")
def _cmd_summarize(args: argparse.Namespace, ctx: ReportContext) -> CommandResult:
    """Summarize logs per model."""
    metrics = get_metrics()
    metrics.increment("analyses_run")
    input_path = validate_file_path(Path(args.input), {".jsonl"})
    ctx.add_subject(args.input, input_path)
    logger.info("Summarizing %s", sanitize_for_log(input_path.name))

    reader = RowReader(input_path, coerce=args.coerce_numeric_strings)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reader:
        buckets[r["model"]].append(r)
    reader.raise_if_invalid()

    logger.info(f"Processed {reader.total} rows for {len(buckets)} models")

    stats = [summarize_model(m, rows) for m, rows in sorted(buckets.items())]
    out = [_model_stats(s) for s in stats]
    legacy = [
        {
            "model": s.model,
            "count": s.count,
            "success_rate": round(s.success_rate, 6),
            "total_cost_usd": round(s.total_cost_usd, 6),
            "p50_ms": _round_or_none(s.p50_ms, 3),
            "p95_ms": _round_or_none(s.p95_ms, 3),
        }
        for s in stats
    ]
    summary: dict[str, Any] = {
        "total_rows": reader.total,
        "total_requests": sum(s.count for s in stats),
        "model_count": len(out),
        "total_cost_usd": round(sum(s.total_cost_usd for s in stats), 6),
    }
    if args.coerce_numeric_strings:
        summary["coerced_values"] = reader.coerced_values
    return CommandResult(EXIT_SUCCESS, summary, {"models": out}, {"models": legacy})


def _round_or_none(value: float, digits: int) -> float | None:
    return None if math.isnan(value) else round(value, digits)


def _model_stats(s: ModelSummary) -> dict[str, Any]:
    """Per-model statistics for reports. Missing measurements are null, never zero."""
    return {
        "model": s.model,
        "count": s.count,
        "rows": s.rows,
        "success_rate": round(s.success_rate, 6) if s.success_samples else None,
        "success_samples": s.success_samples,
        "total_cost_usd": round(s.total_cost_usd, 6),
        "cost_rows": s.cost_rows,
        "p50_ms": _round_or_none(s.p50_ms, 3),
        "p95_ms": _round_or_none(s.p95_ms, 3),
        "latency_samples": s.latency_samples,
        "unknown_request_rows": s.unknown_request_rows,
    }


@track_time("recommend")
def _cmd_recommend(args: argparse.Namespace, ctx: ReportContext) -> CommandResult:
    """Recommend default model under SLO constraints."""
    metrics = get_metrics()
    metrics.increment("analyses_run")
    validate_cli_args(args)
    input_path = validate_file_path(Path(args.input), {".jsonl"})
    ctx.add_subject(args.input, input_path)

    max_p95 = float(args.max_p95_ms)
    min_success = float(args.min_success)
    min_samples = int(args.min_samples)

    logger.info(
        f"Recommending model from {input_path.name} with constraints: "
        f"p95<={max_p95}ms, success>={min_success}, samples>={min_samples}"
    )

    reader = RowReader(
        input_path, coerce=args.coerce_numeric_strings, require=RECOMMEND_REQUIRED_FIELDS
    )
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reader:
        buckets[r["model"]].append(r)
    reader.raise_if_invalid()

    thresholds = {"max_p95_ms": max_p95, "min_success": min_success, "min_samples": min_samples}
    candidates = []
    considered = []
    for m, rows in sorted(buckets.items()):
        s = summarize_model(m, rows)
        considered.append(
            {
                "model": m,
                "count": s.count,
                "avg_cost_usd": s.total_cost_usd / max(1, s.count),
                "p95_ms": s.p95_ms,
                "success_rate": s.success_rate,
                "eligible": s.count >= min_samples
                and s.p95_ms <= max_p95
                and s.success_rate >= min_success,
            }
        )
        if s.count < min_samples:
            logger.debug(f"Skipping {m}: insufficient samples ({s.count} < {min_samples})")
            continue
        if s.p95_ms <= max_p95 and s.success_rate >= min_success:
            candidates.append(s)
            logger.debug(
                f"Candidate {m}: p95={s.p95_ms}ms, success={s.success_rate}, "
                f"cost={s.total_cost_usd / max(1, s.count)}"
            )

    if not candidates:
        logger.warning("No models meet the specified constraints")
        legacy = {"ok": False, "reason": "no_candidate_models"}
        return CommandResult(
            EXIT_VALIDATION_FAILED,
            {**legacy, "thresholds": thresholds},
            {"models": considered},
            legacy,
        )

    MIN_COUNT = 1
    best = min(candidates, key=lambda x: x.total_cost_usd / max(MIN_COUNT, x.count))
    avg_cost = best.total_cost_usd / max(MIN_COUNT, best.count)

    logger.info(f"Recommended model: {best.model} (avg cost: ${avg_cost:.6f})")

    legacy = {
        "ok": True,
        "recommended_model": best.model,
        "avg_cost_usd": avg_cost,
        "p95_ms": best.p95_ms,
        "success_rate": best.success_rate,
        "count": best.count,
    }
    return CommandResult(
        EXIT_SUCCESS, {**legacy, "thresholds": thresholds}, {"models": considered}, legacy
    )


@track_time("simulate")
def _cmd_simulate(args: argparse.Namespace, ctx: ReportContext) -> CommandResult:
    """Re-price historical requests as if a tier policy had routed them.

    Each row is routed to the policy's model for its tier and priced from its token
    counts (input, cache reads/writes, output, reasoning) at that model's prices. The
    row's logged `cost_usd` is never reused as the simulated cost. Rows that cannot be
    priced (no token counts, or a token class the model has no price for) are counted
    as unpriced with a reason and make the result incomplete (exit code 4).
    Latency and quality under the target model cannot be derived from logs, so
    they are not reported.
    """
    metrics = get_metrics()
    metrics.increment("analyses_run")
    input_path = validate_file_path(Path(args.input), {".jsonl"})
    ctx.add_subject(args.input, input_path)
    policy_path = validate_file_path(Path(args.policy), {".json"})
    ctx.add_input(args.policy, policy_path)
    if args.prices:
        prices = load_price_file(Path(args.prices))
        ctx.add_input(args.prices, Path(prices.source))
    else:
        prices = load_builtin_prices()
        ctx.add_input_bytes("built-in:model_prices.json", builtin_price_bytes())

    logger.info(f"Simulating policy from {policy_path.name} on {input_path.name}")

    policy = TierPolicy.from_json(read_json(policy_path))
    logger.info(f"Policy: default={policy.default_model}, tiers={len(policy.tiers)}")

    target_models = {policy.default_model, *policy.tiers.values()}
    missing = sorted(m for m in target_models if prices.resolve(m) is None)
    if missing:
        raise ValueError(
            f"Policy routes to models missing from the price table ({prices.source}, "
            f"as of {prices.as_of}): {', '.join(missing)}. Supply prices with --prices."
        )

    per_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "priced": 0, "unpriced": 0, "cost": 0.0}
    )
    total = 0
    priced = 0
    total_cost = 0.0
    logged_cost = 0.0
    rows = 0
    unpriced_rows = 0
    unknown_requests = 0
    base_tier_assumed = 0
    reasons: dict[str, int] = defaultdict(int)
    reader = RowReader(input_path, coerce=args.coerce_numeric_strings)
    for r in reader:
        rows += 1
        n = request_count(r)
        if n is None:
            unknown_requests += 1
            n = 0
        total += n
        chosen = policy.model_for(str(r.get("tier") or "default"))
        bucket = per_model[chosen]
        bucket["count"] += n
        result = prices.row_cost(chosen, r)
        if result.cost_usd is None:
            bucket["unpriced"] += n
            unpriced_rows += 1
            reasons[str(result.reason)] += 1
            continue
        base_tier_assumed += result.base_tier_assumed
        bucket["priced"] += n
        bucket["cost"] += result.cost_usd
        priced += n
        total_cost += result.cost_usd
        logged = r.get("cost_usd")
        if logged is not None:
            logged_cost += float(logged)

    reader.raise_if_invalid()
    unpriced = total - priced
    models = [
        {
            "model": m,
            "count": int(b["count"]),
            "priced_requests": int(b["priced"]),
            "unpriced_requests": int(b["unpriced"]),
            "total_cost_usd": round(b["cost"], 6),
        }
        for m, b in sorted(per_model.items())
    ]

    logger.info(f"Simulation: {total} requests, {priced} priced, ${total_cost:.6f} total cost")
    if unpriced_rows:
        logger.warning(
            f"{unpriced_rows}/{rows} rows could not be priced ({dict(reasons)}); "
            "total_cost_usd covers priced rows only"
        )

    summary = {
        "prices": {"source": prices.source, "as_of": prices.as_of},
        "complete": unpriced_rows == 0,
        "total_rows": rows,
        "total_requests": total,
        "priced_requests": priced,
        "unpriced_requests": unpriced,
        "unpriced_reasons": dict(sorted(reasons.items())),
        "base_tier_assumed_rows": base_tier_assumed,
        "unknown_request_rows": unknown_requests,
        "total_cost_usd": round(total_cost, 6),
        "logged_cost_usd": round(logged_cost, 6),
    }
    code = EXIT_SUCCESS if unpriced_rows == 0 else EXIT_VALIDATION_FAILED
    legacy_keys = (
        "prices",
        "complete",
        "total_requests",
        "priced_requests",
        "unpriced_requests",
        "total_cost_usd",
        "logged_cost_usd",
    )
    legacy = {k: summary[k] for k in legacy_keys}
    return CommandResult(code, summary, {"models": models}, {**legacy, "models": models})


@track_time("ingest")
def _cmd_ingest(args: argparse.Namespace, ctx: ReportContext) -> CommandResult:
    """Normalize a spend log, trace export or usage export into v2 rows (JSONL)."""
    metrics = get_metrics()
    metrics.increment("analyses_run")
    input_path = validate_file_path(Path(args.input), {".json", ".jsonl"})
    ctx.add_subject(args.input, input_path)
    rows_path = Path(args.rows)
    if rows_path.suffix.lower() != ".jsonl":
        raise ValueError(f"--rows must be a .jsonl file, got: {args.rows}")
    if rows_path.is_symlink():
        raise ValueError(f"Symlinks not allowed: {args.rows}")
    if rows_path.resolve() == input_path:
        raise ValueError("--rows must not overwrite the input file")

    result = ingest(args.format, load_documents(input_path), args.tier_from)
    text = "".join(canonical_json(row) for row in result.rows)
    rows_path.write_text(text, encoding="utf-8", newline="\n")

    skipped = sum(result.skipped.values())
    if skipped:
        logger.warning(f"{skipped}/{result.records} records skipped: {dict(result.skipped)}")
    summary = {
        "format": args.format,
        "records": result.records,
        "rows_written": len(result.rows),
        "skipped": skipped,
        "skipped_reasons": dict(sorted(result.skipped.items())),
        "ignored": sum(result.ignored.values()),
        "ignored_reasons": dict(sorted(result.ignored.items())),
    }
    details = {
        "rows_file": {
            "name": args.rows,
            "digest": {"sha256": sha256_bytes(text.encode("utf-8"))},
        }
    }
    code = EXIT_SUCCESS if skipped == 0 else EXIT_VALIDATION_FAILED
    return CommandResult(code, summary, details, {**summary, **details})


def _unit_interval(name: str, value: str | None) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except ValueError as e:
        raise ValueError(f"{name} must be a number, got: {value}") from e
    if not (math.isfinite(x) and 0.0 <= x <= 1.0):
        raise ValueError(f"{name} must be between 0 and 1, got: {value}")
    return x


def _policy_json(policy: Policy, requests: dict[str, int]) -> dict[str, Any]:
    """The chosen routing in the policy-file format that `simulate` reads."""
    mapping = policy.mapping()
    default_tier = "default" if "default" in mapping else max(requests, key=lambda t: requests[t])
    return {"default_model": mapping[default_tier], "tiers": dict(sorted(mapping.items()))}


def _policy_report(policy: Policy, z: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "policy": dict(sorted(policy.mapping().items())),
        "cost_usd": round(policy.cost_usd, 6),
        "quality": round(policy.quality, 6),
        "quality_se": None if policy.quality_se is None else round(policy.quality_se, 6),
    }
    if z is not None:
        lcb = policy.lower_bound(z)
        out["quality_lower_bound"] = None if lcb is None else round(lcb, 6)
    return out


@track_time("route")
def _cmd_route(args: argparse.Namespace, ctx: ReportContext) -> CommandResult:
    """Cheapest tier-to-model routing that keeps quality at or above a floor."""
    metrics = get_metrics()
    metrics.increment("analyses_run")
    floor_arg = _unit_interval("--min-quality", args.min_quality)
    confidence = _unit_interval("--confidence", args.confidence)
    if confidence is not None and not 0.5 <= confidence < 1.0:
        raise ValueError("--confidence must be in [0.5, 1)")
    min_samples = int(args.min_quality_samples)
    if min_samples < 1:
        raise ValueError("--min-quality-samples must be >= 1")
    if args.max_policies < 1:
        raise ValueError("--max-policies must be >= 1")

    input_path = validate_file_path(Path(args.input), {".jsonl"})
    ctx.add_subject(args.input, input_path)
    prices = _load_prices(args, ctx)

    # Traffic: every row needs token counts and a known request count.
    tiers: dict[str, TierData] = defaultdict(TierData)
    observations: dict[tuple[str, str], list[float]] = defaultdict(list)
    baseline_cost = 0.0
    baseline_unpriced = 0
    logged_cost = 0.0
    served: dict[tuple[str, str], int] = defaultdict(int)
    timestamps: list[float] = []

    def canon(model: str) -> str:
        return prices.resolve(model) or model

    reader = RowReader(
        input_path, coerce=args.coerce_numeric_strings, require=("tokens_in", "tokens_out")
    )
    for r in reader:
        n = request_count(r)
        if n is None:
            raise ValueError(
                "route needs a request count on every row; aggregate rows without "
                "`requests` (for example Anthropic usage exports) cannot be weighted"
            )
        tier = str(r.get("tier") or "default")
        tiers[tier].requests += n
        tiers[tier].rows.append(r)
        served[(tier, canon(r["model"]))] += n
        timestamps.append(float(r["created_ts"]))
        if r.get("quality") is not None:
            observations[(tier, canon(r["model"]))].append(float(r["quality"]))
        if r.get("cost_usd") is not None:
            logged_cost += float(r["cost_usd"])
        own = prices.resolve(r["model"])
        cost = prices.row_cost(own, r).cost_usd if own is not None else None
        if cost is None:
            baseline_unpriced += 1
        else:
            baseline_cost += cost
    reader.raise_if_invalid()
    total_requests = sum(t.requests for t in tiers.values())
    if total_requests == 0:
        raise ValueError("no traffic rows to route")
    weights = {t: d.requests / total_requests for t, d in tiers.items()}

    if args.quality_rows:
        qpath = validate_file_path(Path(args.quality_rows), {".jsonl"})
        ctx.add_input(args.quality_rows, qpath)
        qreader = RowReader(qpath, coerce=args.coerce_numeric_strings, require=("quality",))
        for r in qreader:
            observations[(str(r.get("tier") or "default"), canon(r["model"]))].append(
                float(r["quality"])
            )
        qreader.raise_if_invalid()
    for spec in args.eval or []:
        model, raw_path = parse_eval_spec(spec)
        epath = validate_file_path(Path(raw_path), {".json"})
        ctx.add_input(raw_path, epath)
        for tier, score in read_eval_cases(epath):
            observations[(tier, canon(model))].append(score)

    estimates = build_estimates(observations)
    if args.candidates:
        candidates = sorted({canon(c.strip()) for c in args.candidates.split(",") if c.strip()})
    else:
        candidates = sorted({model for (_, model) in estimates})

    excluded: list[dict[str, str]] = []
    tier_options: dict[str, list[Option]] = {}
    tier_details = []
    for tier in sorted(tiers):
        options = []
        for model in candidates:
            if prices.resolve(model) is None:
                excluded.append({"tier": tier, "model": model, "reason": "not_in_price_table"})
                continue
            est = estimate_for(estimates, tier, model, min_samples)
            if est is None:
                excluded.append({"tier": tier, "model": model, "reason": "no_quality_estimate"})
                continue
            cost = tier_cost(prices, model, tiers[tier].rows)
            if cost is None:
                excluded.append({"tier": tier, "model": model, "reason": "unpriced_token_class"})
                continue
            options.append(Option(tier=tier, model=model, cost_usd=cost, estimate=est))
        tier_options[tier] = options
        tier_details.append(
            {
                "tier": tier,
                "requests": tiers[tier].requests,
                "weight": round(weights[tier], 6),
                "options": [
                    {
                        "model": o.model,
                        "cost_usd": round(o.cost_usd, 6),
                        "quality": round(o.estimate.mean, 6),
                        "quality_samples": o.estimate.n,
                        "quality_from_tier": o.estimate.tier,
                    }
                    for o in options
                ],
            }
        )

    # Baseline: the traffic as it was actually routed, at list price.
    baseline_quality: float | None = 0.0
    for (tier, model), n in served.items():
        est = estimate_for(estimates, tier, model, min_samples)
        if est is None or baseline_quality is None:
            baseline_quality = None
            continue
        baseline_quality += n / total_requests * est.mean
    baseline = {
        "cost_usd": round(baseline_cost, 6) if baseline_unpriced == 0 else None,
        "unpriced_rows": baseline_unpriced,
        "quality": None if baseline_quality is None else round(baseline_quality, 6),
        "logged_cost_usd": round(logged_cost, 6),
    }

    if floor_arg is not None:
        floor, floor_source = floor_arg, "--min-quality"
    elif baseline_quality is not None:
        floor, floor_source = baseline_quality, "baseline"
    else:
        raise ValueError(
            "no --min-quality given and the current routing's quality is unknown "
            "(some served tier/model pairs have no quality estimate)"
        )

    window = (max(timestamps) - min(timestamps)) if timestamps else 0.0
    summary: dict[str, Any] = {
        "prices": {"source": prices.source, "as_of": prices.as_of},
        "total_requests": total_requests,
        "window_seconds": round(window, 3),
        "quality_floor": round(floor, 6),
        "quality_floor_source": floor_source,
        "confidence": confidence,
        "min_quality_samples": min_samples,
        "baseline": baseline,
    }
    details: dict[str, Any] = {"tiers": tier_details, "excluded": excluded}

    empty = sorted(t for t, opts in tier_options.items() if not opts)
    if empty:
        summary.update({"feasible": False, "reason": "tier_without_candidates", "tiers": empty})
        return CommandResult(EXIT_VALIDATION_FAILED, summary, details, summary)

    policies = enumerate_policies(tier_options, weights, args.max_policies)
    frontier = pareto_frontier(policies)
    chosen = choose(policies, floor, confidence)
    z = NormalDist().inv_cdf(confidence) if confidence is not None else None
    summary["policies_evaluated"] = len(policies)
    summary["frontier_size"] = len(frontier)
    details["frontier"] = [_policy_report(p, z) for p in frontier]
    if chosen is None:
        summary.update({"feasible": False, "reason": "no_policy_meets_floor"})
        return CommandResult(EXIT_VALIDATION_FAILED, summary, details, summary)

    summary["feasible"] = True
    summary["chosen"] = _policy_report(chosen, z)
    if baseline["cost_usd"] is not None:
        savings = baseline_cost - chosen.cost_usd
        summary["savings_usd"] = round(savings, 6)
        summary["savings_pct"] = round(100 * savings / baseline_cost, 4) if baseline_cost else None
    policy_file = _policy_json(chosen, {t: d.requests for t, d in tiers.items()})
    details["chosen_policy_file"] = policy_file
    if args.policy_out:
        out_path = Path(args.policy_out)
        if out_path.suffix.lower() != ".json" or out_path.is_symlink():
            raise ValueError("--policy-out must be a .json file (not a symlink)")
        out_path.write_text(json.dumps(policy_file, indent=2) + "\n", encoding="utf-8")
    if args.litellm_config:
        summary["litellm_config"] = _write_litellm_config(args, chosen, prices, window)
    return CommandResult(EXIT_SUCCESS, summary, details, summary)


def _write_litellm_config(
    args: argparse.Namespace, chosen: Policy, prices: PriceTable, window: float
) -> dict[str, Any]:
    path = Path(args.litellm_config)
    if path.suffix.lower() not in {".yaml", ".yml"} or path.is_symlink():
        raise ValueError("--litellm-config must be a .yaml or .yml file (not a symlink)")
    try:
        headroom = float(args.budget_headroom)
    except ValueError as e:
        raise ValueError(f"--budget-headroom must be a number, got: {args.budget_headroom}") from e
    config, notes = build_config(
        policy=chosen.mapping(),
        tier_costs={o.tier: o.cost_usd for o in chosen.assignment},
        prices=prices,
        window_seconds=window,
        budget_duration=args.budget_duration,
        headroom=headroom,
    )
    header = (
        f"# LiteLLM proxy config written by toolkit-opt route ({TOOL_NAME} {__version__}).\n"
        f"# Prices: {prices.source}, as of {prices.as_of}. Budgets: chosen policy cost over the\n"
        f"# log window, scaled to budget_duration, times headroom {headroom}.\n"
    )
    text = header + to_yaml(config) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "name": args.litellm_config,
        "digest": {"sha256": sha256_bytes(text.encode("utf-8"))},
        **notes,
    }


def _load_prices(args: argparse.Namespace, ctx: ReportContext) -> PriceTable:
    if args.prices:
        prices = load_price_file(Path(args.prices))
        ctx.add_input(args.prices, Path(prices.source))
        return prices
    ctx.add_input_bytes("built-in:model_prices.json", builtin_price_bytes())
    return load_builtin_prices()


def _output_options() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--out",
        default=None,
        help="Also write the report envelope (canonical JSON) to this .json file.",
    )
    common.add_argument(
        "--coerce-numeric-strings",
        action="store_true",
        help='Convert numeric strings ("0.5") in numeric fields to numbers before '
        "validation. Without it, such rows are rejected.",
    )
    common.add_argument(
        "--legacy-json",
        action="store_true",
        help="Print the pre-1.0 JSON output instead of the report envelope. Deprecated; "
        "it will be removed in the next minor release.",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="toolkit-opt",
        description="LLM spend and routing analyzer for exported request logs.",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    p.add_argument(
        "--json-log",
        action="store_true",
        help="Output structured JSON logs to stderr",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    common = _output_options()

    v = sub.add_parser(
        "validate", parents=[common], help="Validate logs against the inference-event schema."
    )
    v.add_argument("--input", required=True)
    v.set_defaults(func=_cmd_validate, kind="cost.validate")

    s = sub.add_parser("summarize", parents=[common], help="Summarize logs per model.")
    s.add_argument("--input", required=True)
    s.set_defaults(func=_cmd_summarize, kind="cost.summarize")

    r = sub.add_parser(
        "recommend", parents=[common], help="Recommend a default model under SLO constraints."
    )
    r.add_argument("--input", required=True)
    r.add_argument("--max-p95-ms", default="3000")
    r.add_argument("--min-success", default="0.99")
    r.add_argument("--min-samples", default="50")
    r.set_defaults(func=_cmd_recommend, kind="cost.recommend")

    sim = sub.add_parser(
        "simulate",
        parents=[common],
        help="Re-price logged requests (from token counts) under a tier routing policy.",
    )
    sim.add_argument("--input", required=True)
    sim.add_argument("--policy", required=True)
    sim.add_argument(
        "--prices",
        default=None,
        help="JSON price table (USD per 1M tokens). Defaults to the dated built-in table.",
    )
    sim.set_defaults(func=_cmd_simulate, kind="cost.simulate")

    ing = sub.add_parser(
        "ingest",
        parents=[common],
        help="Normalize LiteLLM spend logs, OpenTelemetry GenAI spans or provider usage "
        "exports into v2 log rows.",
    )
    ing.add_argument("--format", required=True, choices=FORMATS)
    ing.add_argument("--input", required=True, help="Source file (.json or .jsonl).")
    ing.add_argument("--rows", required=True, help="Where to write the v2 rows (.jsonl).")
    ing.add_argument(
        "--tier-from",
        default=None,
        help="Fill `tier` from this key: a LiteLLM request tag 'KEY:value' or metadata "
        "field, an OpenTelemetry span or resource attribute, or a usage-export result "
        "field such as project_id or workspace_id.",
    )
    ing.set_defaults(func=_cmd_ingest, kind="cost.ingest")

    rt = sub.add_parser(
        "route",
        parents=[common],
        help="Find the cheapest tier-to-model routing that keeps quality above a floor, "
        "with the cost/quality Pareto frontier.",
    )
    rt.add_argument("--input", required=True, help="Traffic log rows (.jsonl) with tokens.")
    rt.add_argument(
        "--quality-rows",
        default=None,
        help="Extra rows (.jsonl) carrying `quality` for (tier, model) pairs, such as "
        "shadow-evaluated requests. Used for quality only, not as traffic.",
    )
    rt.add_argument(
        "--eval",
        action="append",
        default=None,
        metavar="MODEL=REPORT.json",
        help="Eval report envelope whose details.cases[].score measure MODEL. Cases tagged "
        "'tier:NAME' count for that tier, others for every tier. Repeatable.",
    )
    rt.add_argument(
        "--candidates",
        default=None,
        help="Comma-separated models to consider (default: every model with quality data).",
    )
    rt.add_argument(
        "--min-quality",
        default=None,
        help="Quality floor in [0, 1] (default: the current routing's estimated quality).",
    )
    rt.add_argument(
        "--confidence",
        default=None,
        help="Require the one-sided lower confidence bound, not the mean, to meet the "
        "floor (for example 0.95).",
    )
    rt.add_argument("--min-quality-samples", default="30")
    rt.add_argument("--max-policies", type=int, default=100_000)
    rt.add_argument("--prices", default=None, help="JSON price table (default: built-in).")
    rt.add_argument(
        "--policy-out",
        default=None,
        help="Write the chosen routing as a policy file (.json) that `simulate` reads.",
    )
    rt.add_argument(
        "--litellm-config",
        default=None,
        help="Write a LiteLLM proxy config (.yaml): one model_list entry per tier with the "
        "chosen model, plus per-tier and proxy-wide budgets.",
    )
    rt.add_argument(
        "--budget-duration",
        default="30d",
        help="LiteLLM budget_duration for the budgets (30d, 24h, 90m, 3600s). Default 30d.",
    )
    rt.add_argument(
        "--budget-headroom",
        default="1.2",
        help="Multiply the projected spend by this factor (>= 1) to set budgets. Default 1.2.",
    )
    rt.set_defaults(func=_cmd_route, kind="cost.route")

    return p


def _check_out_path(out: str | None) -> Path | None:
    if out is None:
        return None
    path = Path(out)
    if path.suffix.lower() != ".json":
        raise ValueError(f"--out must be a .json file, got: {out}")
    if path.is_symlink():
        raise ValueError(f"Symlinks not allowed: {out}")
    return path


def _emit(args: argparse.Namespace, envelope: dict[str, Any], legacy: dict[str, Any]) -> None:
    text = canonical_json(envelope)
    out_path: Path | None = getattr(args, "_out_path", None)
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8", newline="\n")
    if getattr(args, "legacy_json", False):
        print(json.dumps(legacy, indent=2, sort_keys=True))
    else:
        sys.stdout.write(text)


def _error_report(
    args: argparse.Namespace, ctx: ReportContext, code: int, exc: BaseException
) -> None:
    """Emit an `error` envelope when the primary input was read; otherwise stderr only."""
    if not ctx.subject:
        return
    message = sanitize_for_log(str(exc))
    summary: dict[str, Any] = {"error": type(exc).__name__}
    details: dict[str, Any] = {"message": message}
    if isinstance(exc, RowValidationError):
        summary.update({"invalid_rows": exc.invalid_rows, "total_rows": exc.total_rows})
        details["issues"] = exc.issues
    envelope = ctx.build(exit_code=code, summary=summary, details=details)
    try:
        _emit(args, envelope, {"ok": False, "error": type(exc).__name__, "message": message})
    except (OSError, ValueError):  # pragma: no cover - reporting must not mask the error
        logger.error("Could not write the error report")


def main(argv: list[str] | None = None) -> int:
    """Main entry point for CLI.

    Args:
        argv: Command line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 = success, non-zero = error)
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    json_log = getattr(args, "json_log", False)

    if json_log:
        configure_structured_logging(level=log_level, json_format=True)
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stderr,
        )

    metrics = get_metrics()
    ctx = ReportContext(kind=args.kind)

    try:
        args._out_path = _check_out_path(getattr(args, "out", None))
        result: CommandResult = args.func(args, ctx)
        envelope = ctx.build(
            exit_code=result.exit_code, summary=result.summary, details=result.details
        )
        _emit(args, envelope, result.legacy)
        return result.exit_code
    except (ValueError, FileNotFoundError, PermissionError, LogFormatError) as e:
        metrics.increment("errors")
        logger.error("%s: %s", type(e).__name__, sanitize_for_log(str(e)))
        _error_report(args, ctx, EXIT_CLI_ERROR, e)
        return EXIT_CLI_ERROR
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return EXIT_UNEXPECTED_ERROR
    except Exception as e:
        metrics.increment("errors")
        logger.exception("Unexpected error: %s", sanitize_for_log(str(e)))
        print(
            "\nAn unexpected error occurred. Please report this issue.",
            file=sys.stderr,
        )
        _error_report(args, ctx, EXIT_UNEXPECTED_ERROR, e)
        return EXIT_UNEXPECTED_ERROR
