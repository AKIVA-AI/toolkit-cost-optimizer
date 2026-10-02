"""Regenerate the synthetic example data (deterministic).

    python examples/generate.py

Writes:
- spend_logs.json: a week of LiteLLM spend-log rows (the shape `GET /spend/logs` returns)
  for a support assistant. Every request is served by claude-opus-5 today; requests are
  tagged `tier:faq` (short answers) or `tier:escalation` (long, multi-document answers).
- quality.jsonl: quality scores in [0, 1] from replaying a sample of each tier's prompts on
  four candidate models and grading the answers (30 per tier and model).

The numbers are invented but shaped like real traffic: prompt caching on a shared system
prompt, a few failures, and quality that drops more on hard requests for small models.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
START = datetime(2026, 9, 14, tzinfo=timezone.utc)

# Opus 5 list prices (USD per 1M tokens): input 5, cache hit 0.50, output 25.
OPUS = {"input": 5.0, "cached": 0.5, "output": 25.0}

# Mean grade per (tier, model) used to draw the replay scores.
QUALITY = {
    "faq": {
        "claude-opus-5": 0.95,
        "claude-sonnet-5": 0.94,
        "gpt-5.4-mini": 0.92,
        "gemini-2.5-flash": 0.9,
    },
    "escalation": {
        "claude-opus-5": 0.92,
        "claude-sonnet-5": 0.89,
        "gpt-5.4-mini": 0.74,
        "gemini-2.5-flash": 0.7,
    },
}


def main() -> None:
    rng = random.Random(20260926)  # noqa: S311 - synthetic example data, not security
    rows = []
    for i in range(160):
        tier = "faq" if rng.random() < 0.75 else "escalation"
        start = START + timedelta(seconds=rng.uniform(0, 7 * 86400))
        system = 2400  # shared system prompt, usually a cache hit
        if tier == "faq":
            user, out = rng.randint(80, 400), rng.randint(60, 250)
        else:
            user, out = rng.randint(3000, 9000), rng.randint(500, 1400)
        cached = system if rng.random() < 0.85 else 0
        prompt = system + user
        failed = rng.random() < 0.02
        latency_ms = int(400 + out * rng.uniform(18, 30))
        spend = 0.0
        if not failed:
            spend = (
                (prompt - cached) * OPUS["input"] + cached * OPUS["cached"] + out * OPUS["output"]
            ) / 1e6
        rows.append(
            {
                "request_id": f"chatcmpl-{i:05d}",
                "call_type": "acompletion",
                "spend": round(spend, 8),
                "total_tokens": prompt + (0 if failed else out),
                "prompt_tokens": prompt,
                "completion_tokens": 0 if failed else out,
                "startTime": start.isoformat().replace("+00:00", "Z"),
                "endTime": (start + timedelta(milliseconds=latency_ms))
                .isoformat()
                .replace("+00:00", "Z"),
                "request_duration_ms": latency_ms,
                "model": "claude-opus-5",
                "model_group": "support-assistant",
                "custom_llm_provider": "anthropic",
                "metadata": {
                    "user_api_key_alias": "support-bot",
                    "additional_usage_values": {"cache_read_input_tokens": cached},
                },
                "cache_hit": "False",
                "request_tags": [f"tier:{tier}", "app:support"],
                "status": "failure" if failed else "success",
            }
        )
    rows.sort(key=lambda r: r["startTime"])
    _write(HERE / "spend_logs.json", json.dumps(rows, indent=1) + "\n")

    lines = []
    for tier, models in QUALITY.items():
        for model, mean in models.items():
            for _ in range(30):
                score = min(1.0, max(0.0, rng.gauss(mean, 0.08)))
                row = {
                    "schema_version": 2,
                    "created_ts": START.timestamp(),
                    "model": model,
                    "tier": tier,
                    "quality": round(score, 3),
                    "source": "replay-eval",
                }
                lines.append(json.dumps(row))
    _write(HERE / "quality.jsonl", "\n".join(lines) + "\n")


def _write(path: Path, text: str) -> None:
    # LF on every platform, so the files are byte-identical wherever they are generated.
    path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
