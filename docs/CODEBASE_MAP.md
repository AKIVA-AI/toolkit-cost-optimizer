# toolkit-cost-optimizer: codebase map

Offline CLI for analyzing exported LLM request logs. Package `toolkit-cost-optimizer`, import name `toolkit_cost_latency_opt`, console script `toolkit-opt`.

```text
toolkit-cost-optimizer/
|-- src/toolkit_cost_latency_opt/
|   |-- cli.py            # argparse CLI: validate, summarize, recommend, simulate; exit codes
|   |-- envelope.py       # report envelope (in-toto Statement v1), canonical JSON, digests
|   |-- stats.py          # percentile(), summarize_model(), ModelSummary
|   |-- schema.py         # row schema v1/v2: validate_inference_event(), coerce_numeric_strings()
|   |-- rows.py           # RowReader: validated row iteration; RowValidationError fails the command
|   |-- ingest.py         # LiteLLM / OTel GenAI / OpenAI & Anthropic usage exports -> v2 rows
|   |-- quality.py        # quality observations from eval report envelopes (details.cases[].score)
|   |-- routing.py        # estimates, policy evaluation, Pareto frontier, floor choice (route)
|   |-- litellm_config.py # LiteLLM proxy config + budgets for the chosen policy; tiny YAML writer
|   |-- policy.py         # TierPolicy: tier -> model routing
|   |-- pricing.py        # PriceTable: load/validate price tables, cost from token counts
|   |-- data/model_prices.json  # dated built-in price table (USD per 1M tokens)
|   |-- io.py             # file validation (no symlinks, extension allowlist, 1 GB cap), JSON/JSONL readers
|   |-- security.py       # credential redaction for log messages
|   `-- observability.py  # in-process counters, timing decorator, JSON log formatter
|-- tests/                # behavioral CLI and unit tests; tests/fixtures/ingest/ holds sample exports
|-- examples/             # synthetic week of LiteLLM spend logs + replay quality scores (README example); generate.py rebuilds them
|-- schemas/report-envelope.v1.json  # report envelope schema (shared across toolkits)
|-- schemas/cli-output.schema.json   # deprecated --legacy-json output
|-- docs/report-envelope.md          # report envelope spec (shared across toolkits)
`-- .github/              # CI (tests on Linux/macOS/Windows x Python 3.10-3.12, ruff, pyright, pip-audit, bandit), release.yml (v* tag: GitHub Release with the sdist and wheel; PyPI upload when PUBLISH_TO_PYPI is true) and Dependabot
```

## Data flow

1. `io.validate_file_path` checks every input path; `rows.RowReader` streams rows from `io.read_jsonl`, validates each one, and the command calls `raise_if_invalid()` before reporting.
2. `summarize`/`recommend` bucket rows by `model` and call `stats.summarize_model` (logged `cost_usd`, `latency_ms`, `success`).
3. `simulate` routes each row with `TierPolicy.model_for(tier)` and prices it with `PriceTable.cost_usd(model, tokens_in, tokens_out)`. Rows without valid token counts are counted as unpriced.
4. Each command returns a `CommandResult`; `cli.main` wraps it with `envelope.ReportContext.build` (subject/input digests, verdict from the exit code) and prints canonical JSON, optionally also to `--out`.
