# Releasing (Toolkit Cost & Latency Optimizer)

## Pre-flight

- `pytest`
- `ruff check .`
- `pyright`

## Version bump

- Update `pyproject.toml` version.
- Update `CHANGELOG.md`.

## Build and publish

From `enterprise-tools/cost-latency-optimizer/`:

```bash
python -m pip install -U build twine
python -m build
python -m twine upload dist/*
```

## Tagging (recommended)

- Create a tag: `toolkit-opt/vX.Y.Z`



