from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .schema import request_count


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (NumPy's default ``method="linear"``)."""
    if not values:
        return float("nan")
    if p <= 0:
        return min(values)
    if p >= 100:
        return max(values)
    xs = sorted(values)
    k = (len(xs) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[int(f)] * (c - k) + xs[int(c)] * (k - f)


@dataclass(frozen=True)
class ModelSummary:
    """Per-model statistics over schema-valid rows.

    ``count`` is the number of requests (an aggregate row counts ``requests`` times);
    ``unknown_request_rows`` counts aggregate rows whose request count is unknown, which
    ``count`` leaves out.
    Success rate and latency are computed only over rows that carry those fields, so they
    are ``None``/NaN when no row does; ``success_samples`` and ``latency_samples`` say how
    many rows they rest on. ``total_cost_usd`` is the sum of the logged ``cost_usd`` of the
    ``cost_rows`` rows that have one.
    """

    model: str
    count: int
    success_rate: float
    total_cost_usd: float
    p50_ms: float
    p95_ms: float
    rows: int = 0
    success_samples: int = 0
    latency_samples: int = 0
    cost_rows: int = 0
    unknown_request_rows: int = 0


def summarize_model(model: str, rows: Iterable[dict[str, Any]]) -> ModelSummary:
    """Summarize rows that have already passed ``schema.validate_inference_event``."""
    lat: list[float] = []
    cost = 0.0
    ok = 0
    success_samples = 0
    cost_rows = 0
    n_rows = 0
    requests = 0
    unknown = 0
    for r in rows:
        n_rows += 1
        req = request_count(r)
        if req is None:
            unknown += 1
        else:
            requests += req
        success = r.get("success")
        if isinstance(success, bool):
            success_samples += 1
            ok += success is True
        c = r.get("cost_usd")
        if c is not None:
            cost += float(c)
            cost_rows += 1
        latency = r.get("latency_ms")
        if latency is not None:
            lat.append(float(latency))
    return ModelSummary(
        model=model,
        count=requests,
        success_rate=(ok / success_samples) if success_samples else 0.0,
        total_cost_usd=cost,
        p50_ms=percentile(lat, 50),
        p95_ms=percentile(lat, 95),
        rows=n_rows,
        success_samples=success_samples,
        latency_samples=len(lat),
        cost_rows=cost_rows,
        unknown_request_rows=unknown,
    )
