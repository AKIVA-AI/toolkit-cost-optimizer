"""`route --litellm-config`: a LiteLLM proxy config for the chosen policy.

Expected shape (LiteLLM proxy docs, https://docs.litellm.ai/docs/proxy/configs and
/proxy/users): `model_list[].model_name` + `litellm_params.model` ("provider/model") with
`api_key: os.environ/<VAR>`; per-deployment `max_budget` + `budget_duration` in
`litellm_params`; proxy-wide `litellm_settings.max_budget` + `budget_duration` (the proxy
refuses max_budget without budget_duration).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from report_helpers import envelope
from test_route import _setup

from toolkit_cost_latency_opt.cli import EXIT_CLI_ERROR, EXIT_SUCCESS, main
from toolkit_cost_latency_opt.litellm_config import parse_duration, to_yaml

CapSys = pytest.CaptureFixture[str]

PROVIDERS = {"cheap": "openai", "mid": "anthropic", "strong": "gemini"}


def _argv(tmp_path: Path) -> list[str]:
    argv = _setup(tmp_path)
    prices_path = Path(argv[argv.index("--prices") + 1])
    prices = json.loads(prices_path.read_text(encoding="utf-8"))
    for name, provider in PROVIDERS.items():
        prices["models"][name]["provider"] = provider
    prices_path.write_text(json.dumps(prices), encoding="utf-8")
    return argv


def _route(argv: list[str], capsys: CapSys) -> tuple[int, dict[str, Any]]:
    code = main(argv)
    return code, envelope(capsys.readouterr().out)["predicate"]["summary"]


def test_config_for_chosen_policy(tmp_path: Path, capsys: CapSys) -> None:
    out = tmp_path / "litellm.yaml"
    code, summary = _route([*_argv(tmp_path), "--litellm-config", str(out)], capsys)
    assert code == EXIT_SUCCESS
    config = yaml.safe_load(out.read_text(encoding="utf-8"))
    # Chosen policy (see test_route): simple -> mid ($0.024), complex -> strong ($0.100) over a
    # 3-day window. 30d budget = cost x (30 / 3) x headroom 1.2 = cost x 12, rounded up to cents.
    assert config == {
        "model_list": [
            {
                "model_name": "complex",
                "litellm_params": {
                    "model": "gemini/strong",
                    "api_key": "os.environ/GEMINI_API_KEY",
                    "max_budget": 1.2,
                    "budget_duration": "30d",
                },
            },
            {
                "model_name": "simple",
                "litellm_params": {
                    "model": "anthropic/mid",
                    "api_key": "os.environ/ANTHROPIC_API_KEY",
                    "max_budget": 0.29,
                    "budget_duration": "30d",
                },
            },
        ],
        "litellm_settings": {"max_budget": 1.49, "budget_duration": "30d"},
    }
    meta = summary["litellm_config"]
    assert meta["budget_usd"] == 1.49
    assert meta["models_without_provider"] == []
    assert meta["digest"]["sha256"] == envelope_digest(out)


def envelope_digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_budget_options(tmp_path: Path, capsys: CapSys) -> None:
    out = tmp_path / "litellm.yml"
    argv = [*_argv(tmp_path), "--litellm-config", str(out)]
    argv += ["--budget-duration", "24h", "--budget-headroom", "1"]
    assert main(argv) == EXIT_SUCCESS
    capsys.readouterr()
    config = yaml.safe_load(out.read_text(encoding="utf-8"))
    # 24h over a 3-day window = cost / 3: .124 / 3 = .04133 -> .05 (rounded up).
    assert config["litellm_settings"] == {"max_budget": 0.05, "budget_duration": "24h"}


def test_no_budget_without_a_log_window(tmp_path: Path, capsys: CapSys) -> None:
    argv = _argv(tmp_path)
    traffic = Path(argv[2])
    rows = [json.loads(line) for line in traffic.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["created_ts"] = 5.0
    traffic.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / "c.yaml"
    code, summary = _route([*argv, "--litellm-config", str(out)], capsys)
    assert code == EXIT_SUCCESS
    config = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "litellm_settings" not in config
    assert "max_budget" not in config["model_list"][0]["litellm_params"]
    assert summary["litellm_config"]["budget_omitted_reason"] == "empty_log_window"


def test_model_without_provider_is_left_bare(tmp_path: Path, capsys: CapSys) -> None:
    out = tmp_path / "c.yaml"
    code, summary = _route([*_setup(tmp_path), "--litellm-config", str(out)], capsys)
    assert code == EXIT_SUCCESS
    config = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert config["model_list"][0]["litellm_params"]["model"] == "strong"
    assert "api_key" not in config["model_list"][0]["litellm_params"]
    assert summary["litellm_config"]["models_without_provider"] == ["mid", "strong"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--litellm-config", "c.json"],
        ["--litellm-config", "c.yaml", "--budget-duration", "1mo"],
        ["--litellm-config", "c.yaml", "--budget-headroom", "0.5"],
        ["--litellm-config", "c.yaml", "--budget-headroom", "x"],
    ],
)
def test_bad_options(tmp_path: Path, extra: list[str]) -> None:
    extra = [str(tmp_path / e) if e.startswith("c.") else e for e in extra]
    assert main([*_argv(tmp_path), *extra]) == EXIT_CLI_ERROR


def test_parse_duration() -> None:
    assert parse_duration("30d") == 30 * 86400
    assert parse_duration("12h") == 12 * 3600
    assert parse_duration("90m") == 5400
    assert parse_duration("3600s") == 3600
    for bad in ("0d", "1.5d", "d", "30", "30w"):
        with pytest.raises(ValueError):
            parse_duration(bad)


def test_to_yaml_round_trips() -> None:
    obj = {
        "model_list": [
            {"model_name": "a: b # c", "litellm_params": {"model": "x/y", "n": 1, "f": 0.5}},
            {"model_name": "empty", "litellm_params": {}},
        ],
        "flags": [True, None, "yes", "0.5"],
        "nested": {"deep": {"list": []}},
    }
    assert yaml.safe_load(to_yaml(obj)) == obj
