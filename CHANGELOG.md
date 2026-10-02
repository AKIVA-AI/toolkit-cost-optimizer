# Changelog

## Unreleased

## 1.0.0 - 2026-10-02

First release on PyPI.

The tool becomes an LLM spend and routing analyzer: it ingests gateway,
tracing and provider usage exports, prices requests by token class, and finds
the cheapest routing that keeps quality.

### Release and project files

- Release workflow: a `v*` tag runs the tests, builds the sdist and wheel,
  checks them with `twine check --strict` (twine 6.1 or newer, which reads the
  Metadata 2.4 that setuptools 77+ writes), installs the wheel and checks its
  version against the tag, and attaches both files to a GitHub Release. The
  PyPI upload (Trusted Publishing) runs only when the repository variable
  `PUBLISH_TO_PYPI` is `true`. See `RELEASING.md`.
- CI builds and checks the package the same way on every pull request.
- Package metadata: SPDX license expression `Apache-2.0` with `LICENSE` and
  `NOTICE` in the distributions, author AKIVA AI, LLC, and links to the
  documentation, issues and changelog.
- Added `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), issue and pull request
  templates and `RELEASING.md`. `SECURITY.md` lists the supported versions and
  the private reporting channel.
- CI's bandit step no longer falls back to a laxer run when it finds something.
  Its one finding, the `"pass"` verdict constant read as a password, is marked
  as a false positive.

### Changed (breaking)

- `summarize`, `recommend` and `simulate` validate every row and refuse a file
  with invalid rows (exit 2, `error` report listing the issues). Previously
  they analyzed unvalidated rows: `"cost_usd": true` counted as $1.00, a
  numeric string such as `"0.05"` was silently converted, and an unparseable
  value became 0. `NaN`/`Infinity` literals and a boolean `schema_version`
  are now rejected too.
- `summarize` reports `null` (not 0) for success rate and latency when no row
  carries them, plus the sample counts behind each figure.
- Every command now prints a report envelope: an in-toto Statement v1 in
  canonical JSON, with the input log as `subject`, the policy and price table
  as `inputs` (with SHA-256 digests), and `verdict`/`exit_code` that agree
  (`0` pass, `4` fail, `2`/`3` error). The previous output moved into
  `predicate.summary` and `predicate.details`. Spec: `docs/report-envelope.md`;
  schema: `schemas/report-envelope.v1.json`.
- A command that fails after reading its input now prints an `error` envelope.

### Added

- Five-minute example in the README with synthetic example data in
  `examples/` (a week of LiteLLM spend logs and replay quality scores) and
  its generator; a test runs the example end to end.
- `route` command: counterfactual routing under a quality floor. Joins
  per-request quality scores (row `quality`, `--quality-rows`, or eval report
  envelopes via `--eval MODEL=REPORT.json`) to the traffic, prices every
  tier-to-model policy, and reports the cheapest policy meeting the floor
  (default: today's quality; `--confidence` uses the lower confidence bound),
  savings against the current routing, and the cost/quality Pareto frontier.
  `--policy-out` writes the chosen routing for `simulate`.
- `route --litellm-config FILE.yaml`: writes a LiteLLM proxy config for the
  chosen policy (one `model_list` entry per tier with `provider/model` and the
  provider's key variable, per-tier and proxy-wide `max_budget` /
  `budget_duration` projected from the log window with `--budget-headroom`).
- `ingest` command: converts LiteLLM spend logs (`/spend/logs`) and
  `StandardLoggingPayload` callback output, OpenTelemetry GenAI spans
  (OTLP/JSON, Collector file exporter) and OpenAI/Anthropic organization usage
  exports into v2 rows. `--tier-from` fills `tier` from a tag, attribute or
  field. Records that cannot become valid rows are skipped and counted by
  reason (exit 4).
- Row field `aggregate` for usage-export buckets; `summarize` and `simulate`
  report `unknown_request_rows` for aggregates without a request count.
- Built-in price table refreshed to 35 current models (Anthropic, OpenAI,
  Gemini), each checked against the provider's pricing page on 2026-09-26, with
  the page URLs and billing rules recorded in the file.
- Token-class pricing: cached input, cache writes (default and 1-hour),
  reasoning tokens (at the output rate unless `reasoning_per_1m` is set), and
  long-context tiers. Price-table entries may list `aliases` and a `provider`,
  and model names also match without a `provider/` prefix. A row using a token
  class its model has no price for is unpriced with a reason
  (`unpriced_reasons`); aggregate rows on long-context models are counted in
  `base_tier_assumed_rows`.
- Row field `tokens_cache_write_1h` (part of `tokens_cache_write`).
- Row schema version 2: only `schema_version`, `created_ts` and `model` are
  required, for sources that do not log latency, success or cost. New optional
  fields: `tokens_cache_read`, `tokens_cache_write`, `tokens_reasoning`,
  `requests` (aggregate rows), `quality`, `request_id`, `provider`, `source`.
- `--coerce-numeric-strings`: explicit opt-in to convert numeric strings.
- `--out FILE.json` writes the envelope to a file as well as stdout.
- `--legacy-json` prints the pre-1.0 output. Deprecated; removed in the next
  minor release.
- `SOURCE_DATE_EPOCH` sets `created_at`, making reports byte-reproducible.

### Fixed

- `simulate` now re-prices each request under the policy's target model from
  `tokens_in`/`tokens_out` and a price table. Previously it relabelled rows and
  summed their original `cost_usd`, so the total was the same for any policy.
  Rows without valid token counts are reported as unpriced (`complete: false`,
  exit code 4) instead of reusing their logged cost; a policy model missing from
  the price table is an error (exit code 2). Simulate output no longer includes
  latency or success rate, which were the source model's values. The output
  now includes the price source and `as_of` date, priced/unpriced counts, and
  the logged cost of the priced rows.
- `summarize` and `recommend` count a row as successful only when `success` is
  JSON `true`. A missing field or a string such as `"false"` used to count as
  success.

### Added

- Dated built-in price table (`data/model_prices.json`, as of 2026-09-26) for a
  handful of Anthropic and OpenAI models, and `simulate --prices` to supply your
  own table.

### Removed

- The unused `control_plane` package and its tests. Nothing in the CLI used it.
- The `services/cost-optimization-engine` FastAPI service (cloud FinOps for
  AWS/Azure/GCP), with its Dockerfile, docker-compose file, tests, CI jobs,
  Dependabot entry and README section. It was out of scope for an LLM spend
  analyzer and did not work end to end: the AWS cost sync could not return
  data, Azure and GCP were stubs, stored credentials were encrypted but never
  decrypted, routes had no authentication, and two action endpoints returned
  HTTP 500. It is not being replaced. Use a dedicated FinOps tool for cloud
  infrastructure spend.

### Changed

- Relicensed from MIT to Apache-2.0. Releases before this change remain available
  under MIT. Added a `NOTICE` file.
- README rewritten to describe each command's status and which cost input it
  uses (logged `cost_usd` vs token counts). Install is from source; there is no
  PyPI release yet. Package classifier changed from Production/Stable to Beta.

## 0.1.0

- JSONL summarizer, validator, recommender, and tier-policy simulator.
- Shared inference event schema alignment and validator (`toolkit-opt validate`).

