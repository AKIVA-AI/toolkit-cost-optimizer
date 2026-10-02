"""Counterfactual routing under a quality floor.

Question answered: *what would this traffic cost at the same quality if each tier were
routed to a different model?*

Inputs:

- **Traffic**: validated log rows with token counts. Each row belongs to a tier (its
  ``tier`` field, or ``"default"``) and stands for ``requests`` requests.
- **Quality observations**: per-request scores in [0, 1] for a (tier, model) pair, from
  rows that carry ``quality`` or from eval reports (report-envelope ``details.cases[].score``).
  Observations for tier ``"*"`` apply to every tier that has none of its own.

For every tier ``t`` and candidate model ``m`` the tool computes the tier's cost if all its
rows were served by ``m`` (token classes at ``m``'s list price) and the mean quality of
``m`` on that tier with its standard error. A *policy* assigns one model to each tier. Its
cost is the sum of its tiers' costs; its quality is the request-weighted mean

    Q = sum_t w_t * q(t, m_t),          w_t = requests in t / all requests

and, treating separate estimates as independent samples (stratified sampling, Cochran,
*Sampling Techniques*, 3rd ed., 1977, ch. 5, without the finite-population correction),

    Var(Q) = sum_e (sum of w_t over tiers using estimate e)^2 * s_e^2 / n_e

where an estimate ``e`` shared by several tiers (a ``"*"`` estimate) enters once with the
summed weight, because those tiers' errors are perfectly correlated. With a confidence
level the floor must hold for the one-sided lower bound ``Q - z * sqrt(Var(Q))``.

The Pareto frontier holds every policy that no other policy beats on both cost (lower)
and point-estimate quality (higher). The chosen policy is the cheapest one meeting the
floor.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

from .pricing import PriceTable

ALL_TIERS = "*"
QUALITY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Estimate:
    """Mean quality of one model on one tier (or on ``*``) from ``n`` observations."""

    tier: str
    model: str
    n: int
    mean: float
    variance: float | None  # sample variance (n - 1 denominator); None when n < 2

    @property
    def se2(self) -> float | None:
        return None if self.variance is None else self.variance / self.n

    @staticmethod
    def from_scores(tier: str, model: str, scores: list[float]) -> Estimate:
        n = len(scores)
        mean = math.fsum(scores) / n
        var = math.fsum((s - mean) ** 2 for s in scores) / (n - 1) if n > 1 else None
        return Estimate(tier=tier, model=model, n=n, mean=mean, variance=var)


@dataclass
class TierData:
    requests: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Option:
    """One candidate model for one tier."""

    tier: str
    model: str
    cost_usd: float
    estimate: Estimate


@dataclass(frozen=True)
class Policy:
    assignment: tuple[Option, ...]
    cost_usd: float
    quality: float
    quality_se: float | None

    def lower_bound(self, z: float) -> float | None:
        return None if self.quality_se is None else self.quality - z * self.quality_se

    def mapping(self) -> dict[str, str]:
        return {o.tier: o.model for o in self.assignment}


def build_estimates(
    observations: dict[tuple[str, str], list[float]],
) -> dict[tuple[str, str], Estimate]:
    return {
        key: Estimate.from_scores(key[0], key[1], scores)
        for key, scores in observations.items()
        if scores
    }


def estimate_for(
    estimates: dict[tuple[str, str], Estimate], tier: str, model: str, min_samples: int
) -> Estimate | None:
    """The tier's own estimate if it has enough samples, else the ``*`` estimate."""
    own = estimates.get((tier, model))
    if own is not None and own.n >= min_samples:
        return own
    shared = estimates.get((ALL_TIERS, model))
    if shared is not None and shared.n >= min_samples:
        return shared
    return None


def tier_cost(prices: PriceTable, model: str, rows: list[dict[str, Any]]) -> float | None:
    """Cost of every row in a tier served by ``model``; None if any row cannot be priced."""
    total = 0.0
    for row in rows:
        result = prices.row_cost(model, row)
        if result.cost_usd is None:
            return None
        total += result.cost_usd
    return total


def evaluate(options: tuple[Option, ...], weights: dict[str, float]) -> Policy:
    cost = math.fsum(o.cost_usd for o in options)
    quality = math.fsum(weights[o.tier] * o.estimate.mean for o in options)
    grouped: dict[tuple[str, str], float] = {}
    for o in options:
        key = (o.estimate.tier, o.estimate.model)
        grouped[key] = grouped.get(key, 0.0) + weights[o.tier]
    se: float | None = 0.0
    variance = 0.0
    for o in {(o.estimate.tier, o.estimate.model): o for o in options}.values():
        se2 = o.estimate.se2
        if se2 is None:
            se = None
            break
        variance += grouped[(o.estimate.tier, o.estimate.model)] ** 2 * se2
    if se is not None:
        se = math.sqrt(variance)
    return Policy(assignment=options, cost_usd=cost, quality=quality, quality_se=se)


def pareto_frontier(policies: list[Policy]) -> list[Policy]:
    """Policies not beaten on both cost (lower) and quality (higher), cheapest first."""
    ordered = sorted(policies, key=lambda p: (p.cost_usd, -p.quality, _key(p)))
    frontier: list[Policy] = []
    best = -math.inf
    for p in ordered:
        if p.quality > best + QUALITY_TOLERANCE:
            frontier.append(p)
            best = p.quality
    return frontier


def choose(policies: list[Policy], floor: float, confidence: float | None) -> Policy | None:
    """Cheapest policy whose quality (or lower confidence bound) meets ``floor``."""
    z = NormalDist().inv_cdf(confidence) if confidence is not None else None
    feasible = []
    for p in policies:
        value = p.quality if z is None else p.lower_bound(z)
        if value is not None and value >= floor - QUALITY_TOLERANCE:
            feasible.append(p)
    if not feasible:
        return None
    return min(feasible, key=lambda p: (p.cost_usd, -p.quality, _key(p)))


def enumerate_policies(
    tier_options: dict[str, list[Option]], weights: dict[str, float], max_policies: int
) -> list[Policy]:
    count = math.prod(len(v) for v in tier_options.values())
    if count > max_policies:
        raise ValueError(
            f"{count} candidate policies exceed --max-policies {max_policies}; narrow --candidates"
        )
    tiers = sorted(tier_options)
    return [
        evaluate(tuple(combo), weights)
        for combo in itertools.product(*(tier_options[t] for t in tiers))
    ]


def _key(p: Policy) -> tuple[str, ...]:
    return tuple(f"{o.tier}={o.model}" for o in p.assignment)
