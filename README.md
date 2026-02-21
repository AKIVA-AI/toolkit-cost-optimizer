# Toolkit Cost & Latency Optimizer

Toolkit Cost & Latency Optimizer is a lightweight, production-hardened CLI for analyzing LLM inference logs, validating schema compliance, and simulating routing policies.

This tool now also hosts the Cost Optimization Engine service (formerly a standalone tool) under `services/cost-optimization-engine`.

## What It Does

- Validate JSONL logs against the Toolkit inference event schema.
- Summarize cost, latency, and success rates per model.
- Recommend a default model based on SLO constraints.
- Simulate tier routing policies over historical logs.

## Install

```bash
pip install -e .
```

## Quick Start

```bash
# Validate logs
toolkit-opt validate --input logs.jsonl

# Summarize per model
toolkit-opt summarize --input logs.jsonl

# Recommend a default model (based on p95, success rate, and sample count)
toolkit-opt recommend --input logs.jsonl --max-p95-ms 3000 --min-success 0.99 --min-samples 50

# Simulate a tier policy
toolkit-opt simulate --input logs.jsonl --policy policy.json
```

Use `--verbose` to enable DEBUG logging:

```bash
toolkit-opt --verbose summarize --input logs.jsonl
```

## Input Log Schema (JSONL)

Each line in the log file must be a JSON object with the following required fields:

- `schema_version` (int) must be `1`
- `created_ts` (number) timestamp (seconds)
- `model` (string)
- `latency_ms` (number, >= 0)
- `cost_usd` (number, >= 0)
- `success` (bool)

Optional fields supported by the tool:

- `tier` (string) used by policy simulation
- `tokens_in` (int, >= 0)
- `tokens_out` (int, >= 0)

Example:

```json
{"schema_version": 1, "created_ts": 1700000000.0, "model": "gpt-4", "latency_ms": 1200, "cost_usd": 0.045, "success": true, "tier": "premium"}
```

## Policy File (JSON)

Policy files are JSON objects with a default model and optional tier overrides:

```json
{
  "default_model": "gpt-3.5-turbo",
  "tiers": {
    "premium": "gpt-4",
    "fast": "gpt-3.5-turbo"
  }
}
```

## Output

All commands emit JSON to stdout. This makes the tool easy to pipeline into other systems.

## Exit Codes

- `0` success
- `2` CLI or input validation error
- `3` unexpected error
- `4` schema validation failed

## Safety Notes

- Input files must be regular files (no symlinks).
- Log inputs are restricted to `.jsonl`; policy inputs to `.json`.
- Maximum file size is 1 GB.

## License

MIT. See `LICENSE`.

## Cost Optimization Engine (Service)

The FastAPI cost optimization service has been consolidated here.

- Location: `services/cost-optimization-engine`
- Start the API:

```bash
cd services/cost-optimization-engine
python -m venv .venv
. .venv/Scripts/activate
pip install -e ".[dev]"

set DATABASE_URL=postgresql://user:pass@localhost:5432/cost_optimization_engine
toolkit-cost-optimizer serve --host 0.0.0.0 --port 8005
```

### Migration Note

If you previously deployed `production-ready/cost-optimization-engine`, update paths to:

- `production-ready/cost-latency-optimizer/services/cost-optimization-engine`


