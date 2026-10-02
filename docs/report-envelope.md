# Toolkit report envelope v1 (shared convention)

Every tool writes its machine-readable result as an **in-toto Statement v1**, the open attestation format used by SLSA and Sigstore. A report from any tool can then be signed and verified with standard tooling, and gated in CI the same way. Tools stay independent: each one implements this small format itself and does **not** import another toolkit.

## Shape

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {"name": "<what was evaluated: suite, dataset, model dir, run file>", "digest": {"sha256": "<hex>"}}
  ],
  "predicateType": "https://github.com/AKIVA-AI/<repo>/report/v1",
  "predicate": {
    "tool": {"name": "<repo>", "version": "<package version>"},
    "kind": "<command, e.g. eval.run | eval.compare | rag.score | policy.run | data.check | mmqa.scan | cost.simulate | aibom.generate>",
    "created_at": "<RFC 3339 UTC, e.g. 2026-09-26T18:00:00Z>",
    "verdict": "pass | fail | error",
    "exit_code": 0,
    "inputs": [{"name": "<file or ref>", "digest": {"sha256": "<hex>"}}],
    "summary": {},
    "details": {}
  }
}
```

## Rules
- **Canonical form:** the file is canonical JSON (UTF-8, sorted keys, no insignificant whitespace, `\n` at end) so its SHA-256 is stable. Human-readable output is a separate format (`--format table/markdown`).
- **Consistent verdicts:** `verdict` and `exit_code` must agree with the CLI's documented exit codes. `error` means the tool could not judge (bad input, integrity failure). An `error` report is never `pass`.
- **Content:** `summary` holds the headline numbers a gate reads. `details` holds per-case or per-item data.
- **Compatibility:** a tool may keep its legacy JSON output behind a flag for one minor version. The envelope is the default for `--out *.json` or `--report`.
- **Signing:** documented, not built in. Users sign any report with `toolkit-mlsbom sign-file <report.json>` (Ed25519 key, or Sigstore keyless when the `sigstore` extra is installed) and verify it with `toolkit-mlsbom verify-file`. toolkit-ml-provenance implements these two commands. Each other tool's README shows the one-line signing example and names ml-provenance as optional.
- **Schema file:** each repo commits this spec as `docs/report-envelope.md` and a JSON Schema as `schemas/report-envelope.v1.json`. They are identical across repos, and the tool's own `predicate.summary` shape is documented in its README.
