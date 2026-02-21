from __future__ import annotations

import json
from pathlib import Path

from toolkit_cost_latency_opt.cli import main


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_summarize_and_recommend(tmp_path: Path) -> None:
    logs = tmp_path / "logs.jsonl"
    _write_jsonl(
        logs,
        [
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "cheap",
                "latency_ms": 100,
                "cost_usd": 0.001,
                "success": True,
            },
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "cheap",
                "latency_ms": 200,
                "cost_usd": 0.001,
                "success": True,
            },
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "strong",
                "latency_ms": 500,
                "cost_usd": 0.01,
                "success": True,
            },
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "strong",
                "latency_ms": 600,
                "cost_usd": 0.01,
                "success": True,
            },
        ],
    )

    assert main(["validate", "--input", str(logs)]) == 0
    assert main(["summarize", "--input", str(logs)]) == 0
    # relax samples for test
    assert (
        main(
            [
                "recommend",
                "--input",
                str(logs),
                "--min-samples",
                "1",
                "--max-p95-ms",
                "1000",
                "--min-success",
                "0.5",
            ]
        )
        == 0
    )


def test_simulate(tmp_path: Path) -> None:
    logs = tmp_path / "logs.jsonl"
    _write_jsonl(
        logs,
        [
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "cheap",
                "tier": "default",
                "latency_ms": 100,
                "cost_usd": 0.001,
                "success": True,
            },
            {
                "schema_version": 1,
                "created_ts": 1.0,
                "model": "strong",
                "tier": "deep",
                "latency_ms": 500,
                "cost_usd": 0.01,
                "success": True,
            },
        ],
    )
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps({"default_model": "cheap", "tiers": {"deep": "strong"}}), encoding="utf-8"
    )
    assert main(["simulate", "--input", str(logs), "--policy", str(policy)]) == 0

