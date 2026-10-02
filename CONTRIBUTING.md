# Contributing to toolkit-cost-optimizer

Thanks for helping. For a large change, please open an issue first so we can
agree on the approach.

## Development setup

```bash
git clone https://github.com/AKIVA-AI/toolkit-cost-optimizer.git
cd toolkit-cost-optimizer
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Checks

CI runs these on every pull request; run them before you push:

```bash
pytest --cov=toolkit_cost_latency_opt --cov-fail-under=90
ruff check .
pyright
```

CI also runs `bandit -r src/`, `pip-audit` with every optional extra installed,
and builds the sdist and wheel (`twine check --strict`).

## Pull requests

1. Branch from `main`.
2. Write a failing test first, then the change. Tests should exercise real
   behavior (real input files, real CLI calls), not only construction.
3. Update the README for user-visible behavior and add a `CHANGELOG.md` entry
   under `[Unreleased]`.
4. Keep the core free of runtime dependencies; optional features go
   in an extra in `pyproject.toml`.
5. Open the pull request against `main` and fill in the template.

## Project conventions

- Keep metrics deterministic: the same input gives the same report.
- Add tests for new log fields and policy semantics.
- `simulate` and `route` price requests from token classes; never reuse the
  logged `cost_usd` there.
- When editing `src/toolkit_cost_latency_opt/data/model_prices.json`, verify each
  changed price on the provider's page and update `as_of` and `sources`.

## Conduct, security and license

- Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Report security problems privately as described in [SECURITY.md](SECURITY.md),
  not in a public issue.
- Contributions are accepted under the Apache License 2.0 ([LICENSE](LICENSE)):
  by opening a pull request you agree that your contribution is licensed under
  it, as section 5 of the license describes.
- Maintainers: releases are described in [RELEASING.md](RELEASING.md).
