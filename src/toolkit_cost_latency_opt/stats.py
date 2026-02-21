from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


def percentile(values: list[float], p: float) -> float:
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
    model: str
    count: int
    success_rate: float
    total_cost_usd: float
    p50_ms: float
    p95_ms: float


def summarize_model(model: str, rows: Iterable[dict[str, object]]) -> ModelSummary:
    def _to_float(v: object | None) -> float:
        if v is None:
            return 0.0
        if isinstance(v, bool):
            return float(int(v))
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except Exception:
                return 0.0
        return 0.0

    lat: list[float] = []
    cost = 0.0
    ok = 0
    n = 0
    for r in rows:
        n += 1
        if bool(r.get("success", True)):
            ok += 1
        cost += _to_float(r.get("cost_usd"))
        lat.append(_to_float(r.get("latency_ms")))
    return ModelSummary(
        model=model,
        count=n,
        success_rate=(ok / n) if n else 0.0,
        total_cost_usd=cost,
        p50_ms=percentile(lat, 50),
        p95_ms=percentile(lat, 95),
    )
