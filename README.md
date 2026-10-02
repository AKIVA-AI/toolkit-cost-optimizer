# Toolkit Cost Optimizer: LLM spend and routing analyzer

[![PyPI](https://img.shields.io/pypi/v/toolkit-cost-optimizer.svg)](https://pypi.org/project/toolkit-cost-optimizer/)
[![Python versions](https://img.shields.io/pypi/pyversions/toolkit-cost-optimizer.svg)](https://pypi.org/project/toolkit-cost-optimizer/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

`toolkit-opt` answers one question from the logs you already have: **what would this LLM traffic cost at the same quality if it were routed differently?**

It reads your gateway spend logs, OpenTelemetry GenAI traces or provider usage exports, prices every request from its token classes (input, cache reads and writes, output, reasoning) against a dated price table, joins per-request quality scores, and finds the cheapest tier-to-model routing that keeps quality above a floor. The result is a report you can sign and gate in CI, a cost/quality Pareto frontier, and a LiteLLM proxy config you can deploy.

It is an offline analyzer with no runtime dependencies. It does not call any LLM provider, proxy traffic, or collect logs for you.

## Capabilities

| Command | Status | What it does |
|---|---|---|
| `ingest` | Working | Converts LiteLLM spend logs and logging payloads, OpenTelemetry GenAI spans (OTLP/JSON) and OpenAI/Anthropic usage exports into log rows. Reads files you export; it does not connect to a gateway, collector or provider API. |
| `route` | Working | Counterfactual routing under a quality floor: joins per-request quality scores (from log rows or eval reports) to your traffic, prices every tier-to-model policy, and reports the cheapest policy that keeps quality (optionally at a confidence level), the savings against today's routing, and the cost/quality Pareto frontier. Writes the chosen policy and a LiteLLM proxy config with budgets. |
| `simulate` | Working (cost only) | Re-prices each request under a given tier-to-model policy from its token classes and a dated price table covering 35 current Anthropic, OpenAI and Gemini models. It cannot predict latency, success or output quality on the target model, so it does not report them; `route` handles quality. |
| `summarize` | Working | Per model: request count, success rate, total logged cost, p50/p95 latency, and how many rows each figure rests on. Measurements a row does not carry are reported as `null`, never as zero. |
| `validate` | Working | Checks each row against the row schema and reports issues by field. Every other command also validates every row and refuses invalid input. |
| `recommend` | Working (simple) | Picks the model with the lowest average **logged** `cost_usd` among models that meet `--max-p95-ms`, `--min-success` and `--min-samples`. No quality signal beyond `success`; prefer `route`. |
| Report envelope output | Working | Every command prints an in-toto Statement v1 report (canonical JSON with input digests and a pass/fail/error verdict). See [Output](#output-report-envelope). |
| Human-readable output (`--format table/markdown`) | Planned | Reports are JSON only; pipe them through `python -m json.tool` or `jq`. |
| Live ingestion (streaming from a gateway or collector) | Planned | Export to a file and run `ingest`. |
| Latency prediction for a target model | Planned | Not modeled: `route` optimizes cost under a quality floor only. |

## Install

Python 3.10 or newer; no runtime dependencies.

```bash
pip install toolkit-cost-optimizer
toolkit-opt --help
```

The package has no optional extras. To work on the code, see [Development](#development).

## Five-minute example

Run these commands from a clone of this repository, because they read the sample data in its `examples/` folder:

```bash
git clone https://github.com/AKIVA-AI/toolkit-cost-optimizer.git
cd toolkit-cost-optimizer
```

The `examples/` folder holds a synthetic but realistic week of traffic for a support assistant: 160 LiteLLM spend-log rows (`examples/spend_logs.json`), all served by `claude-opus-5`, tagged `tier:faq` or `tier:escalation`, with prompt caching on a shared system prompt and a few failures. `examples/quality.jsonl` holds 30 graded replays per tier for four candidate models. `examples/generate.py` regenerates both.

**1. Ingest the spend logs** into normalized rows, taking the tier from the `tier:` request tag:

```bash
toolkit-opt ingest --format litellm-spendlogs --input examples/spend_logs.json \
  --rows rows.jsonl --tier-from tier
```

The report says `"records":160,"rows_written":160,"skipped":0`.

**2. See what you spend today:**

```bash
toolkit-opt summarize --input rows.jsonl
```

`claude-opus-5`: 160 requests, success rate 0.975, logged spend $3.575365, p50 5.2 s.

**3. Find the cheapest routing at the same quality**, and write the policy and a LiteLLM config:

```bash
toolkit-opt route --input rows.jsonl --quality-rows examples/quality.jsonl \
  --policy-out chosen.json --litellm-config litellm.yaml --out route.json
```

From `route.json` (`predicate.summary`):

```json
{"baseline": {"cost_usd": 3.62312, "quality": 0.924198, "logged_cost_usd": 3.575365},
 "quality_floor": 0.924198, "quality_floor_source": "baseline",
 "chosen": {"policy": {"escalation": "claude-sonnet-5", "faq": "claude-sonnet-5"},
            "cost_usd": 1.449248, "quality": 0.925427, "quality_se": 0.009051},
 "savings_usd": 2.173872, "savings_pct": 60.0,
 "policies_evaluated": 16, "frontier_size": 9}
```

The baseline reprices today's routing at list price, including the prompt tokens of failed requests, which is why it is slightly above the logged spend. Moving both tiers to `claude-sonnet-5` keeps the estimated quality (0.925 against 0.924) for 60% less. `predicate.details.frontier` lists the nine cost/quality trade-offs, from all-`gemini-2.5-flash` ($0.28, quality 0.83) to `claude-opus-5` on escalations ($3.05, 0.93). Add `--confidence 0.95` to require the lower confidence bound, not the mean, to meet the floor.

**4. Check the chosen policy request by request, and deploy it:**

```bash
toolkit-opt simulate --input rows.jsonl --policy chosen.json   # total_cost_usd 1.449248
cat litellm.yaml                                                # LiteLLM proxy config with 30-day budgets
```

## Command reference

```bash
toolkit-opt ingest    --format FORMAT --input EXPORT --rows rows.jsonl [--tier-from KEY]
toolkit-opt validate  --input rows.jsonl
toolkit-opt summarize --input rows.jsonl
toolkit-opt simulate  --input rows.jsonl --policy policy.json [--prices prices.json]
toolkit-opt route     --input rows.jsonl [--quality-rows q.jsonl] [--eval MODEL=report.json]
                      [--min-quality Q] [--confidence 0.95] [--candidates a,b]
                      [--policy-out policy.json] [--litellm-config litellm.yaml]
toolkit-opt recommend --input rows.jsonl --max-p95-ms 3000 --min-success 0.99 --min-samples 50
```

Every command accepts `--out report.json` (write the report envelope to a file as well), `--coerce-numeric-strings` and `--legacy-json` (deprecated pre-1.0 output). Use `--verbose` for debug logging and `--json-log` for structured JSON logs on stderr.

## Where the cost numbers come from

This matters, because the commands use different inputs:

- **`summarize` and `recommend` use `cost_usd` from your log rows.** Whatever you logged is what gets summed and compared. If your logger computed cost from an out-of-date price, these numbers inherit that error.
- **`simulate` and `route` ignore the logged `cost_usd` when pricing.** They price each request's token classes (see [Token classes](#token-classes)) at list price for the model a policy routes it to. A row that cannot be priced is counted as unpriced with a reason, never priced from its logged cost. The logged cost is reported separately as `logged_cost_usd`, so you can compare the two.

## Route: what would this cost at the same quality?

`route` answers the question the rest of the tool builds up to: given your traffic and how well each model does on each kind of request, which routing is cheapest without losing quality, and how much would it save?

```bash
toolkit-opt route --input rows.jsonl --quality-rows shadow_scores.jsonl \
  --eval gpt-5.4-mini=evals/mini.json --min-quality-samples 30 --policy-out chosen.json
```

**Inputs**

- **Traffic** (`--input`): log rows with `tokens_in`/`tokens_out` and a known request count. Each row's `tier` (or `default`) is a traffic segment the router can send to a different model.
- **Quality**: per-request scores in [0, 1] for (tier, model) pairs, from any of:
  - a `quality` field on traffic rows (the model that served them);
  - `--quality-rows FILE.jsonl`: rows used only for quality, for example the same prompts replayed on candidate models (they are not counted as traffic);
  - `--eval MODEL=REPORT.json`: an eval report envelope whose `predicate.details.cases[].score` measure `MODEL`, such as an `eval.run` report from an evaluation harness. It is read as JSON by that shape; no other package is needed. Cases tagged `tier:NAME` count for that tier; untagged cases count for every tier that has no data of its own. An `error` report is refused.
- `--candidates a,b,c` limits the models considered (default: every model with quality data). A model is a candidate for a tier only if it is in the price table, has at least `--min-quality-samples` scores (default 30) for that tier (or for all tiers), and can price every row of the tier.

**What it computes**

- For each tier and candidate: the tier's cost if every request went to that model (token classes at list price), and the model's mean quality there with its standard error.
- For every policy (one model per tier): cost = sum of tier costs; quality = request-weighted mean of tier qualities; standard error from the stratified-sampling variance `sum (w_t^2 s^2 / n)` (Cochran, *Sampling Techniques*, ch. 5). An estimate shared by several tiers enters once with their summed weight, because their errors are correlated.
- **Baseline**: the traffic as actually routed, repriced at list price, and its quality from the same estimates.
- **Floor**: `--min-quality Q`, or by default the baseline's quality ("same quality"). With `--confidence 0.95`, the one-sided 95% lower bound of a policy's quality must meet the floor, not just its mean.
- **Result**: the cheapest policy meeting the floor, the savings against the baseline, and the cost/quality **Pareto frontier** (every policy that no other policy beats on both cost and quality). `--policy-out` writes the chosen routing as a policy file that `simulate` reads.

`predicate.summary`: `baseline` (`cost_usd`, `quality`, `logged_cost_usd`), `quality_floor` and its source, `chosen` (`policy`, `cost_usd`, `quality`, `quality_se`, `quality_lower_bound` with `--confidence`), `savings_usd`, `savings_pct`, `policies_evaluated`, `frontier_size`, `feasible`. `predicate.details`: per-tier options, excluded (tier, model) pairs with reasons, the frontier, and the chosen policy file. Exit 4 when no policy meets the floor or a tier has no candidate.

**LiteLLM config**: `--litellm-config litellm.yaml` writes a [LiteLLM proxy](https://docs.litellm.ai/docs/proxy/configs) config for the chosen policy, so the result can be deployed as is:

```yaml
model_list:
  - model_name: "complex"              # clients request the tier name
    litellm_params:
      model: "anthropic/claude-sonnet-5"
      api_key: "os.environ/ANTHROPIC_API_KEY"
      max_budget: 1.56                 # per-tier budget (USD)
      budget_duration: "30d"
  - model_name: "simple"
    litellm_params:
      model: "gemini/gemini-2.5-flash"
      api_key: "os.environ/GEMINI_API_KEY"
      max_budget: 0.9
      budget_duration: "30d"
litellm_settings:
  max_budget: 2.45                     # proxy-wide budget
  budget_duration: "30d"
```

- The provider prefix and key variable come from the price table's `provider` (`openai`, `anthropic`, `gemini` map to `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`). A model without a provider is written bare and listed in `models_without_provider`.
- Budgets are the chosen policy's cost over the log window, scaled to `--budget-duration` (default `30d`; `s`, `m`, `h`, `d` units) and multiplied by `--budget-headroom` (default 1.2), rounded up to the cent. With a single-timestamp log there is no window, so budgets are left out.
- The shape follows the LiteLLM docs. A config written by `route` was loaded into `litellm.Router` (litellm 1.85.0) with its deployment budgets to confirm LiteLLM accepts it; the test suite checks the structure with a YAML parser and does not depend on LiteLLM.

**Limits**: quality estimates are only as good as the scores you supply, and they assume the scored requests represent the tier's traffic. Token counts are reused across models; different tokenizers produce different counts for the same text (Anthropic notes about 30% more tokens on its newer tokenizer), so cross-provider savings are estimates. Latency on the target model is not predicted. Policies are enumerated exhaustively up to `--max-policies` (default 100,000).

## Ingest: from your gateway, traces or provider bill to log rows

`ingest` converts an export you already have into version 2 log rows that every other command reads:

```bash
toolkit-opt ingest --format litellm-spendlogs --input spend_logs.json --rows rows.jsonl --tier-from tier
toolkit-opt summarize --input rows.jsonl
```

| `--format` | Input | Where to get it | What a row is |
|---|---|---|---|
| `litellm-spendlogs` | JSON list of `LiteLLM_SpendLogs` rows, or `{"data": [...]}` | LiteLLM proxy `GET /spend/logs?summarize=false` or `GET /spend/logs/v2` | one request |
| `litellm-payload` | `StandardLoggingPayload` objects, JSON array or NDJSON | LiteLLM `s3_v2`, `gcs_bucket` or `generic_api` logging callbacks | one request |
| `otel` | OTLP/JSON trace data, one `{"resourceSpans": ...}` per line | OpenTelemetry Collector `file` exporter (JSON), spans with GenAI semantic-convention `gen_ai.*` attributes | one inference span (`chat`, `text_completion`, `generate_content`) |
| `openai-usage` | Usage API completions page(s) | `GET /v1/organization/usage/completions`, grouped by `model` | one time bucket per model, with `requests` |
| `anthropic-usage` | Usage report page(s) | `GET /v1/organizations/usage_report/messages`, grouped by `model` | one time bucket per model, request count unknown |

How fields are mapped:

- **Tokens** follow the row schema: `tokens_in` includes cache reads and writes. LiteLLM's `prompt_tokens` already does (for Anthropic it adds cache reads and cache creation to `input_tokens`); OpenAI's usage `input_tokens` does per its API spec; for OpenTelemetry the GenAI conventions say `gen_ai.usage.input_tokens` SHOULD include cached tokens; for Anthropic's usage report the tool adds `uncached_input_tokens`, `cache_read_input_tokens` and both `cache_creation` counts. Reasoning comes from `completion_tokens_details.reasoning_tokens` (LiteLLM) or `gen_ai.usage.reasoning.output_tokens` (OpenTelemetry).
- **OpenTelemetry**: model from `gen_ai.response.model`, else `gen_ai.request.model`; provider from `gen_ai.provider.name`, else the older `gen_ai.system`; `gen_ai.usage.prompt_tokens`/`completion_tokens` and `gen_ai.usage.cache_creation.input_tokens` (older names) are accepted; latency is end minus start time; a span with status code 2 (ERROR) or an `error.type` attribute is `success: false`. Spans without `gen_ai.operation.name` and non-inference operations (tools, agents, embeddings) are ignored and counted.
- **LiteLLM**: `spend` / `response_cost` becomes `cost_usd` (LiteLLM's own calculation), `status` becomes `success`, naive timestamps are read as UTC.
- **Usage exports** produce aggregate rows (`"aggregate": true`) without latency or success. Batch and non-standard service tiers (flex, priority) are skipped, because the price table models standard pricing only.
- **`--tier-from KEY`** fills `tier`: a LiteLLM request tag `KEY:value` (or a metadata field `KEY`, such as `user_api_key_team_alias`), an OpenTelemetry span or resource attribute `KEY`, or a usage-export result field such as `project_id` or `workspace_id`.

A record that cannot become a valid row (no model, no timestamp, cache counts larger than the input total, a batch bucket) is **skipped and counted by reason**, and the command exits 4 so a pipeline notices. Nothing is filled in. The report's `details.rows_file` records the SHA-256 of the rows it wrote.

## Input log schema (JSONL)

Each line is a JSON object. Two schema versions are accepted:

- **Version 1**: one row per request, with logged cost, latency and success. Required: `schema_version` (`1`), `created_ts`, `model`, `latency_ms`, `cost_usd`, `success`.
- **Version 2**: the normalized row that different sources can fill in part. Required: `schema_version` (`2`), `created_ts`, `model`. Everything else is optional.

Fields:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | `1` or `2` (a JSON `true` or `"1"` is rejected) |
| `created_ts` | number >= 0 | request time, Unix seconds |
| `model` | non-empty string | the model that served the request |
| `latency_ms` | number >= 0 | end-to-end latency |
| `cost_usd` | number >= 0 | the cost you recorded for the request |
| `success` | bool | only JSON `true`/`false` |
| `tier` | non-empty string | traffic segment used by routing policies; rows without one use the policy's `default_model` |
| `tokens_in` | int >= 0 | **all** input tokens, including cached reads and cache writes |
| `tokens_cache_read` | int >= 0 | the part of `tokens_in` read from a prompt cache |
| `tokens_cache_write` | int >= 0 | the part of `tokens_in` written to a prompt cache |
| `tokens_cache_write_1h` | int >= 0 | the part of `tokens_cache_write` written with a 1-hour lifetime (Anthropic prices these apart) |
| `tokens_out` | int >= 0 | **all** output tokens, including reasoning/thinking tokens |
| `tokens_reasoning` | int >= 0 | the part of `tokens_out` spent on reasoning |
| `requests` | int >= 1 | number of requests the row stands for (default 1) |
| `aggregate` | bool | `true` for a row that sums several requests, such as a usage-export bucket. A row with `aggregate: true` or `requests` > 1 cannot carry `latency_ms`, `success` or `quality`; without `requests` its request count is unknown and is reported as `unknown_request_rows` |
| `quality` | number in [0, 1] | a per-request quality score |
| `request_id`, `provider`, `source` | non-empty string | identifiers kept for traceability |

Numbers must be finite JSON numbers: `NaN`, `Infinity`, booleans and numeric strings are rejected. An optional field set to `null` is treated as absent. Unknown extra fields are allowed. Cache and reasoning counts must not exceed the totals they are part of.

Example (version 1):

```json
{"schema_version": 1, "created_ts": 1700000000.0, "model": "gpt-4o", "latency_ms": 1200, "cost_usd": 0.0045, "success": true, "tier": "premium", "tokens_in": 1200, "tokens_out": 150}
```

**Every command validates every row.** `validate` reports the issues (exit 4 when any row is invalid). `summarize`, `recommend` and `simulate` refuse to analyze a file with invalid rows: they exit 2 with an `error` report whose `details.issues` lists each problem and its count. `recommend` also needs `latency_ms`, `success` and `cost_usd` on every row, including version 2 rows.

If your logger writes numbers as strings (`"cost_usd": "0.0045"`), pass `--coerce-numeric-strings` to convert strings that parse completely as numbers (integers for token counts). Nothing else is converted; the report's `summary.coerced_values` says how many values were changed.

## Policy file (JSON)

A default model plus optional tier overrides:

```json
{
  "default_model": "gpt-4o-mini",
  "tiers": {
    "premium": "gpt-4o"
  }
}
```

## Price table (JSON)

`simulate` needs a price for every model the policy can route to. If any is missing it stops with exit code 2 rather than guessing.

### Built-in table

The package ships a dated table at `src/toolkit_cost_latency_opt/data/model_prices.json` (`as_of: 2026-09-26`) with 35 current models from Anthropic (Claude Fable, Opus, Sonnet and Haiku), OpenAI (GPT-6, GPT-5.x, GPT-4.1/4o, o3/o4-mini) and Google (Gemini 3.x and 2.5). Every price was checked against the provider's official pricing page on that date; the page URLs and the billing rules used are recorded in the file's `sources`. The prices are the standard tier in the default region: batch, flex, priority/fast mode and data-residency surcharges are not included. Provider prices change, so pass your own table for anything that matters.

### Token classes

A request is priced from its token classes (see the row schema):

```
cost = (tokens_in - tokens_cache_read - tokens_cache_write) x input
     + tokens_cache_read                          x cached_input
     + (tokens_cache_write - tokens_cache_write_1h) x cache_write
     + tokens_cache_write_1h                      x cache_write_1h
     + (tokens_out - tokens_reasoning)            x output
     + tokens_reasoning                           x reasoning (defaults to output)
```

- **Reasoning tokens** are billed as output tokens by Anthropic, OpenAI and Google (each page is cited in the table), so they use the output rate unless a model sets `reasoning_per_1m`.
- **Cache writes**: Anthropic prices 5-minute and 1-hour writes separately. OpenAI charges 1.25x input for GPT-5.6 and later and "no additional cache-write charge" for earlier models, so those models' `cache_write_per_1m` equals their input price. Gemini has no per-token cache-write price.
- **Long context**: some OpenAI and Gemini models charge more for the whole request when the prompt is over 272K (OpenAI) or 200K (Gemini) tokens. A per-request row above the threshold is priced at the long-context rates. An aggregate row (`requests` > 1) cannot show each prompt's size, so it is priced at the standard rate and counted in `base_tier_assumed_rows`.
- **Fail closed**: a row that uses a token class its model has no price for (for example cache writes on a Gemini model) is reported as unpriced with a reason in `unpriced_reasons` (`missing_token_counts`, `no_cached_input_price`, `no_cache_write_price`, `no_cache_write_1h_price`), never priced at a guessed rate.
- **Model names** match exactly, by an alias listed in the table (`claude-haiku-4-5-20251001`), or without a `provider/` prefix (`openai/gpt-4o`). There is no fuzzy matching.

### Your own table

To use negotiated rates, other providers or self-hosted models, pass `--prices` with the same format. Prices are USD per one million tokens and `as_of` is required; everything except `input_per_1m` and `output_per_1m` is optional:

```json
{
  "as_of": "2026-09-26",
  "models": {
    "gpt-5.4": {
      "input_per_1m": 2.5, "cached_input_per_1m": 0.25, "cache_write_per_1m": 2.5,
      "output_per_1m": 15.0,
      "long_context": {"threshold_tokens": 272000, "input_per_1m": 5.0,
                       "cached_input_per_1m": 0.5, "cache_write_per_1m": 5.0, "output_per_1m": 22.5},
      "provider": "openai", "aliases": ["prod-gpt-5.4"]
    },
    "claude-sonnet-5": {
      "input_per_1m": 2.0, "cached_input_per_1m": 0.2, "cache_write_per_1m": 2.5,
      "cache_write_1h_per_1m": 4.0, "output_per_1m": 10.0, "provider": "anthropic"
    },
    "my-local-model": {"input_per_1m": 0.0, "output_per_1m": 0.0}
  }
}
```

## Output: report envelope

Every command prints one **report envelope** to stdout: an [in-toto Statement v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md) written as canonical JSON (UTF-8, sorted keys, no insignificant whitespace, trailing newline), so the same result always has the same SHA-256. `--out report.json` also writes it to a file. The format is specified in [`docs/report-envelope.md`](docs/report-envelope.md) and [`schemas/report-envelope.v1.json`](schemas/report-envelope.v1.json).

```json
{"_type":"https://in-toto.io/Statement/v1",
 "subject":[{"name":"logs.jsonl","digest":{"sha256":"..."}}],
 "predicateType":"https://github.com/AKIVA-AI/toolkit-cost-optimizer/report/v1",
 "predicate":{"tool":{"name":"toolkit-cost-optimizer","version":"..."},
              "kind":"cost.simulate","created_at":"2026-09-26T18:00:00Z",
              "verdict":"pass","exit_code":0,
              "inputs":[{"name":"policy.json","digest":{"sha256":"..."}}],
              "summary":{...},"details":{...}}}
```

- `subject` is the log file analyzed; `inputs` are the policy, price table and any other file the result depends on (the built-in price table is recorded as `built-in:model_prices.json` with its digest).
- `verdict` follows the exit code: `0` is `pass`, `4` is `fail`, `2`/`3` are `error`. When a command fails after it has read its input (for example an invalid policy), it still prints an `error` envelope with the message in `details.message`. When the input itself cannot be read, nothing is printed to stdout.
- `created_at` is the current UTC time, or `SOURCE_DATE_EPOCH` when that is set, which makes reports byte-for-byte reproducible.

`predicate.summary` per command (the numbers a CI gate reads):

| `kind` | `summary` | `details` |
|---|---|---|
| `cost.ingest` | `format`, `records`, `rows_written`, `skipped`, `skipped_reasons`, `ignored`, `ignored_reasons` | `rows_file` (name and SHA-256 of the rows written) |
| `cost.validate` | `ok`, `total`, `invalid_rows` (`coerced_values` with `--coerce-numeric-strings`) | `issues` (kind, field, message, count) |
| `cost.summarize` | `total_rows`, `total_requests`, `model_count`, `total_cost_usd` | `models` (per model: count, rows, success_rate, success_samples, total_cost_usd, cost_rows, p50_ms, p95_ms, latency_samples, unknown_request_rows) |
| `cost.recommend` | `ok`, `recommended_model`, `avg_cost_usd`, `p95_ms`, `success_rate`, `count`, `thresholds` (or `ok: false`, `reason`) | `models` (every model considered, with `eligible`) |
| `cost.simulate` | `prices` (`source`, `as_of`), `complete`, `total_rows`, `total_requests`, `priced_requests`, `unpriced_requests`, `unpriced_reasons`, `base_tier_assumed_rows`, `unknown_request_rows`, `total_cost_usd`, `logged_cost_usd` | `models` (per target model) |
| `cost.route` | see [Route](#route-what-would-this-cost-at-the-same-quality) | per-tier options, excluded pairs, frontier, chosen policy file |

A command that refuses invalid rows reports `summary.invalid_rows`, `summary.total_rows` and `details.issues` in its `error` envelope.

`--legacy-json` prints the pre-1.0 output (described in `schemas/cli-output.schema.json`) instead. It is deprecated and will be removed in the next minor release; `--out` still writes the envelope.

### Signing a report

Signing is not built in. Any report can be signed and verified with the optional [toolkit-ml-provenance](https://github.com/AKIVA-AI/toolkit-ml-provenance) CLI:

```bash
toolkit-opt route --input rows.jsonl --quality-rows q.jsonl --out report.json
toolkit-mlsbom sign-file report.json --key signing.pem          # or --sigstore (keyless)
toolkit-mlsbom verify-file report.json --public-key signing.pub
```

## Exit codes

- `0` success
- `2` CLI or input error: bad arguments, unreadable file, invalid rows, invalid policy, price table or eval report, policy model missing from the price table
- `3` unexpected error
- `4` result not usable as-is: `validate` found invalid rows, `ingest` skipped records, `recommend` found no model meeting the thresholds, `simulate` could not price some rows (`complete: false`), or `route` found no policy meeting the floor

## Safety notes

- Input files must be regular files (no symlinks).
- Log inputs must be `.jsonl`; policy, price and eval-report inputs must be `.json`; `ingest` reads `.json` or `.jsonl`.
- Outputs: `--out` and `--policy-out` must be `.json`, `--rows` `.jsonl`, `--litellm-config` `.yaml`/`.yml`; none may be a symlink.
- Maximum file size is 1 GB.
- Error messages written to logs are redacted for common secret patterns.

## Development

Install from source in editable mode, with the test, lint and type-check tools:

```bash
git clone https://github.com/AKIVA-AI/toolkit-cost-optimizer.git
cd toolkit-cost-optimizer
pip install -e ".[dev]"
pytest
ruff check .
pyright
```

## Contributing and security

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Please report security problems
privately, as described in [SECURITY.md](SECURITY.md).

## Releasing

Releases are cut by pushing a `vX.Y.Z` tag. CI runs the tests, builds the
sdist and wheel, checks them, attaches them to a GitHub Release and publishes
them to PyPI with Trusted Publishing. [RELEASING.md](RELEASING.md) describes
the process and how to verify a release.

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Releases before the relicensing remain available under the MIT License.
