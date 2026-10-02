"""The README's five-minute example, end to end: the numbers quoted there must hold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from report_helpers import envelope

from toolkit_cost_latency_opt.cli import EXIT_SUCCESS, main

CapSys = pytest.CaptureFixture[str]
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_five_minute_example(tmp_path: Path, capsys: CapSys) -> None:
    rows = tmp_path / "rows.jsonl"
    argv = ["ingest", "--format", "litellm-spendlogs", "--input", str(EXAMPLES / "spend_logs.json")]
    assert main([*argv, "--rows", str(rows), "--tier-from", "tier"]) == EXIT_SUCCESS
    ingest = envelope(capsys.readouterr().out)["predicate"]["summary"]
    assert (ingest["records"], ingest["rows_written"], ingest["skipped"]) == (160, 160, 0)

    assert main(["summarize", "--input", str(rows)]) == EXIT_SUCCESS
    (model,) = envelope(capsys.readouterr().out)["predicate"]["details"]["models"]
    assert model["model"] == "claude-opus-5"
    assert model["count"] == 160
    assert model["success_rate"] == 0.975
    assert model["total_cost_usd"] == pytest.approx(3.575365)

    policy, config, report = (
        tmp_path / "chosen.json",
        tmp_path / "litellm.yaml",
        tmp_path / "r.json",
    )
    argv = ["route", "--input", str(rows), "--quality-rows", str(EXAMPLES / "quality.jsonl")]
    argv += ["--policy-out", str(policy), "--litellm-config", str(config), "--out", str(report)]
    assert main(argv) == EXIT_SUCCESS
    capsys.readouterr()
    pred = json.loads(report.read_text(encoding="utf-8"))["predicate"]
    summary = pred["summary"]
    assert summary["baseline"]["cost_usd"] == pytest.approx(3.62312)
    assert summary["baseline"]["quality"] == pytest.approx(0.924198)
    assert summary["quality_floor_source"] == "baseline"
    assert summary["chosen"]["policy"] == {
        "escalation": "claude-sonnet-5",
        "faq": "claude-sonnet-5",
    }
    assert summary["chosen"]["cost_usd"] == pytest.approx(1.449248)
    assert summary["chosen"]["quality"] == pytest.approx(0.925427)
    assert summary["savings_pct"] == pytest.approx(60.0)
    assert (summary["policies_evaluated"], summary["frontier_size"]) == (16, 9)
    frontier = pred["details"]["frontier"]
    assert frontier[0]["policy"] == {"escalation": "gemini-2.5-flash", "faq": "gemini-2.5-flash"}
    assert frontier[-1]["cost_usd"] == pytest.approx(3.048239)
    assert (
        yaml.safe_load(config.read_text(encoding="utf-8"))["model_list"][0]["litellm_params"][
            "model"
        ]
        == "anthropic/claude-sonnet-5"
    )

    assert main(["simulate", "--input", str(rows), "--policy", str(policy)]) == EXIT_SUCCESS
    sim = envelope(capsys.readouterr().out)["predicate"]["summary"]
    assert sim["total_cost_usd"] == pytest.approx(1.449248)


def test_example_data_is_reproducible(tmp_path: Path) -> None:
    """examples/generate.py regenerates the committed files byte for byte."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("example_generate", EXAMPLES / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = tmp_path
    module.main()
    for name in ("spend_logs.json", "quality.jsonl"):
        expected = (EXAMPLES / name).read_bytes().replace(b"\r\n", b"\n")
        assert (tmp_path / name).read_bytes() == expected, name
