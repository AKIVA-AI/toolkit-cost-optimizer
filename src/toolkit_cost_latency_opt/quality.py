"""Quality observations for routing: from log rows or from eval reports.

An eval report is any report envelope (``docs/report-envelope.md``) whose
``predicate.details.cases`` is a list of cases with a numeric ``score`` in [0, 1], such as
the ``eval.run`` report of an evaluation harness. The file is read as JSON by that shape;
no other tool is imported. A case tagged ``tier:<name>`` counts for that tier; other cases
count for every tier (``*``).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from .envelope import STATEMENT_TYPE
from .io import read_json

PREDICATE_TYPE_PATTERN = re.compile(r"^https://github\.com/AKIVA-AI/[A-Za-z0-9._-]+/report/v1$")
TIER_TAG_PREFIX = "tier:"


def parse_eval_spec(spec: str) -> tuple[str, str]:
    """Split ``MODEL=PATH`` (the model name may not contain ``=``)."""
    model, sep, path = spec.partition("=")
    if not sep or not model.strip() or not path.strip():
        raise ValueError(f"--eval must be MODEL=PATH, got: {spec!r}")
    return model.strip(), path.strip()


def read_eval_cases(path: Path) -> list[tuple[str, float]]:
    """(tier, score) for every case of an eval report envelope. Raises ValueError."""
    env = read_json(path)
    if not isinstance(env, dict) or env.get("_type") != STATEMENT_TYPE:
        raise ValueError(f"{path.name}: not a report envelope (in-toto Statement v1)")
    ptype = env.get("predicateType")
    if not isinstance(ptype, str) or not PREDICATE_TYPE_PATTERN.match(ptype):
        raise ValueError(f"{path.name}: unexpected predicateType {ptype!r}")
    pred = env.get("predicate")
    if not isinstance(pred, dict):
        raise ValueError(f"{path.name}: predicate must be an object")
    if pred.get("verdict") not in ("pass", "fail"):
        raise ValueError(
            f"{path.name}: verdict {pred.get('verdict')!r}; an error report has no usable scores"
        )
    details = pred.get("details")
    cases = details.get("cases") if isinstance(details, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path.name}: predicate.details.cases must be a non-empty list")

    out: list[tuple[str, float]] = []
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"{path.name}: case {i} is not an object")
        score = case.get("score")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0.0 <= score <= 1.0
        ):
            raise ValueError(f"{path.name}: case {i} score must be a number in [0, 1]")
        out.append((_case_tier(case), float(score)))
    return out


def _case_tier(case: dict[str, Any]) -> str:
    tags = case.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.startswith(TIER_TAG_PREFIX):
                name = tag[len(TIER_TAG_PREFIX) :].strip()
                if name:
                    return name
    return "*"
