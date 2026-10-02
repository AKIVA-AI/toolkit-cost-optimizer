# toolkit-cost-optimizer: guidance for coding agents

Offline CLI (`toolkit-opt`) that analyzes exported LLM request logs (JSONL). Python 3.10+, no runtime dependencies.

## Commands

- Install: `pip install -e ".[dev]"`
- Test: `pytest` (CI enforces 90% coverage: `pytest --cov=toolkit_cost_latency_opt --cov-fail-under=90`)
- Lint: `ruff check .`
- Type-check: `pyright`

## Layout

- `src/toolkit_cost_latency_opt/cli.py`: argparse entry point and the subcommands.
- `stats.py` (per-model summary, percentiles), `schema.py` (row schema v1/v2), `rows.py` (validated row reader used by every command), `ingest.py` (export formats to v2 rows; each format's source schema is cited in its docstring), `routing.py` + `quality.py` (`route`: estimates, stratified variance, Pareto frontier; eval reports are read by JSON shape, never by importing another toolkit), `policy.py` (tier policy), `pricing.py` (price tables), `io.py` (hardened file reading), `security.py` (log redaction), `observability.py` (metrics, JSON logs).
- `src/toolkit_cost_latency_opt/data/model_prices.json`: dated built-in price table.
- `envelope.py`: report envelope (in-toto Statement v1, canonical JSON). Spec in `docs/report-envelope.md`, schema in `schemas/report-envelope.v1.json`; both are shared verbatim with the other toolkit repos, so do not edit them here alone.
- Commands return a `CommandResult` (exit code, `summary`, `details`, legacy dict); `main()` wraps it in the envelope. Document any `summary` change in the README table.
- `schemas/cli-output.schema.json`: the deprecated `--legacy-json` output.

## Conventions

- `summarize`/`recommend` use the caller's logged `cost_usd`. `simulate` and `route` must never reuse it: they price from token classes and report rows they cannot price.
- Fail closed: unknown models, malformed tables, invalid rows and missing token counts are reported (exit 2 or 4), never filled with a default. Commands read rows only through `rows.RowReader` and call `raise_if_invalid()`.
- When editing `model_prices.json`, verify every changed price on the provider page, update `as_of` and the `sources` entry (URL, retrieved date, billing notes), and update the spot checks in `tests/test_pricing_classes.py`.
- Tests are behavioral: write JSONL fixtures, call `main([...])`, assert on the JSON output.
- `tests/test_example.py` pins the numbers quoted in the README's five-minute example; update both together (`python examples/generate.py` rebuilds the data).
