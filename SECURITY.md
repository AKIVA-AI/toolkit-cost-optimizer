# Security policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | Yes       |
| < 1.0   | No        |

Security fixes are released as patch versions of the latest minor release.

## Reporting a vulnerability

Please do not report security problems in a public issue, pull request or
discussion. Report them privately through GitHub: open this repository's
**Security** tab and choose **Report a vulnerability**
(<https://github.com/AKIVA-AI/toolkit-cost-optimizer/security/advisories/new>). Include:

- what the problem is and its impact;
- steps or input files to reproduce it;
- the affected version or commit.

We aim to acknowledge a report within 7 days and ask for up to 90 days to
release a fix before public disclosure. We credit reporters who want to be
credited.

## Guidance

- `toolkit-opt` is an offline analyzer: it reads the files you pass and writes
  reports. It calls no LLM provider, gateway or collector.
- Exported spend logs and traces can contain prompts, user identifiers and
  keys in metadata. Redact them before sharing a log or a report.
- Inputs must be regular files (no symlinks) of at most 1 GB; error messages
  written to logs are redacted for common secret patterns.
