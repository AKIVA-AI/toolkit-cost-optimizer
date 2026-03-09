from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from .io import LogFormatError, read_json, read_jsonl, validate_file_path
from .policy import TierPolicy
from .schema import SchemaIssue, validate_inference_event
from .stats import summarize_model

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_CLI_ERROR = 2
EXIT_UNEXPECTED_ERROR = 3
EXIT_VALIDATION_FAILED = 4


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


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate logs against schema."""
    input_path = validate_file_path(Path(args.input), {".jsonl"})
    logger.info(f"Validating {input_path.name}")

    issues: dict[tuple[str, str, str], int] = {}
    total = 0
    bad = 0
    for r in read_jsonl(input_path):
        total += 1
        row_issues = validate_inference_event(r)
        if row_issues:
            bad += 1
        for iss in row_issues:
            key = (iss.kind, iss.field, iss.message)
            issues[key] = issues.get(key, 0) + 1

    issue_list = [
        SchemaIssue(kind=k[0], field=k[1], message=k[2], count=v).__dict__
        for k, v in sorted(issues.items())
    ]
    report = {
        "ok": bad == 0,
        "total": total,
        "invalid_rows": bad,
        "issues": issue_list,
    }
    print(json.dumps(report, indent=2, sort_keys=True))

    if bad == 0:
        logger.info(f"Validation passed: {total} rows valid")
        return EXIT_SUCCESS
    logger.warning(f"Validation failed: {bad}/{total} rows invalid")
    return EXIT_VALIDATION_FAILED


def _cmd_summarize(args: argparse.Namespace) -> int:
    """Summarize logs per model."""
    input_path = validate_file_path(Path(args.input), {".jsonl"})
    logger.info(f"Summarizing {input_path.name}")

    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    total = 0
    for r in read_jsonl(input_path):
        total += 1
        buckets[str(r.get("model") or "unknown")].append(r)

    logger.info(f"Processed {total} rows for {len(buckets)} models")

    out = []
    for m, rows in sorted(buckets.items()):
        s = summarize_model(m, rows)
        out.append(
            {
                "model": s.model,
                "count": s.count,
                "success_rate": round(s.success_rate, 6),
                "total_cost_usd": round(s.total_cost_usd, 6),
                "p50_ms": round(s.p50_ms, 3),
                "p95_ms": round(s.p95_ms, 3),
            }
        )
    print(json.dumps({"models": out}, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _cmd_recommend(args: argparse.Namespace) -> int:
    """Recommend default model under SLO constraints."""
    validate_cli_args(args)
    input_path = validate_file_path(Path(args.input), {".jsonl"})

    max_p95 = float(args.max_p95_ms)
    min_success = float(args.min_success)
    min_samples = int(args.min_samples)

    logger.info(
        f"Recommending model from {input_path.name} with constraints: "
        f"p95<={max_p95}ms, success>={min_success}, samples>={min_samples}"
    )

    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in read_jsonl(input_path):
        buckets[str(r.get("model") or "unknown")].append(r)

    candidates = []
    for m, rows in buckets.items():
        s = summarize_model(m, rows)
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
        print(json.dumps({"ok": False, "reason": "no_candidate_models"}, indent=2))
        return EXIT_VALIDATION_FAILED

    MIN_COUNT = 1
    best = min(candidates, key=lambda x: x.total_cost_usd / max(MIN_COUNT, x.count))
    avg_cost = best.total_cost_usd / max(MIN_COUNT, best.count)

    logger.info(f"Recommended model: {best.model} (avg cost: ${avg_cost:.6f})")

    print(
        json.dumps(
            {
                "ok": True,
                "recommended_model": best.model,
                "avg_cost_usd": avg_cost,
                "p95_ms": best.p95_ms,
                "success_rate": best.success_rate,
                "count": best.count,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return EXIT_SUCCESS


def _cmd_simulate(args: argparse.Namespace) -> int:
    """Simulate a tier policy over historical logs."""
    input_path = validate_file_path(Path(args.input), {".jsonl"})
    policy_path = validate_file_path(Path(args.policy), {".json"})

    logger.info(f"Simulating policy from {policy_path.name} on {input_path.name}")

    policy = TierPolicy.from_json(read_json(policy_path))
    logger.info(f"Policy: default={policy.default_model}, tiers={len(policy.tiers)}")

    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in read_jsonl(input_path):
        tier = str(r.get("tier") or "default")
        chosen = policy.model_for(tier)
        buckets[chosen].append(r)

    models = []
    total_cost = 0.0
    total = 0
    for m, rows in sorted(buckets.items()):
        s = summarize_model(m, rows)
        total_cost += s.total_cost_usd
        total += s.count
        models.append(
            {
                "model": m,
                "count": s.count,
                "success_rate": round(s.success_rate, 6),
                "total_cost_usd": round(s.total_cost_usd, 6),
                "p95_ms": round(s.p95_ms, 3),
            }
        )

    logger.info(f"Simulation: {total} requests, ${total_cost:.6f} total cost")

    print(
        json.dumps(
            {"total_requests": total, "total_cost_usd": total_cost, "models": models},
            indent=2,
        )
    )
    return EXIT_SUCCESS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="toolkit-opt",
        description="Toolkit Cost & Latency Optimizer for LLM inference logs",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="Validate logs against Toolkit inference-event schema.")
    v.add_argument("--input", required=True)
    v.set_defaults(func=_cmd_validate)

    s = sub.add_parser("summarize", help="Summarize logs per model.")
    s.add_argument("--input", required=True)
    s.set_defaults(func=_cmd_summarize)

    r = sub.add_parser("recommend", help="Recommend a default model under SLO constraints.")
    r.add_argument("--input", required=True)
    r.add_argument("--max-p95-ms", default="3000")
    r.add_argument("--min-success", default="0.99")
    r.add_argument("--min-samples", default="50")
    r.set_defaults(func=_cmd_recommend)

    sim = sub.add_parser("simulate", help="Simulate a tier policy over historical logs.")
    sim.add_argument("--input", required=True)
    sim.add_argument("--policy", required=True)
    sim.set_defaults(func=_cmd_simulate)

    return p


def main(argv: list[str] | None = None) -> int:
    """Main entry point for CLI.

    Args:
        argv: Command line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 = success, non-zero = error)
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    try:
        return int(args.func(args))
    except (ValueError, FileNotFoundError, PermissionError, LogFormatError) as e:
        logger.error(f"{type(e).__name__}: {e}")
        return EXIT_CLI_ERROR
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return EXIT_UNEXPECTED_ERROR
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(
            "\nAn unexpected error occurred. Please report this issue.",
            file=sys.stderr,
        )
        return EXIT_UNEXPECTED_ERROR
