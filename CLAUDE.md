# Cost Optimizer — LLM inference cost analysis and routing policy simulation

**Archetype:** 9 — Developer Tool / CLI Utility
**Standards:** Akiva Build Standard v2.13
**Ontology ID:** TK-01

## Stack
- Language: Python 3.10+
- Test: `pytest -xvs`
- Lint: `ruff check src/ tests/`
- Build: `pip install -e .`

## Verification Commands
| Command | Purpose |
|---------|---------|
| `pytest -xvs` | Run tests |
| `ruff check src/ tests/` | Lint |

## Current State
- Audit Score: 52/100
- Tests: 6

## Key Rules
- Archetype 9: single-purpose CLI tool, zero or minimal dependencies in core
- Tests first, security fixes before features
- One task at a time, verified before moving to next
