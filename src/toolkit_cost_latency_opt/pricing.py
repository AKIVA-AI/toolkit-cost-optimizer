"""Model price tables: the cost of a request from its token counts.

A price table is a JSON object::

    {
      "as_of": "2026-09-26",
      "models": {
        "<model name>": {
          "input_per_1m": 2.5,              # required: uncached input
          "output_per_1m": 15.0,            # required: output, including reasoning
          "cached_input_per_1m": 0.25,      # optional: prompt-cache reads
          "cache_write_per_1m": 3.125,      # optional: prompt-cache writes (default TTL)
          "cache_write_1h_per_1m": 5.0,     # optional: 1-hour cache writes (Anthropic)
          "reasoning_per_1m": 15.0,         # optional: only if reasoning is priced apart
          "long_context": {                 # optional: a higher tier for long prompts
            "threshold_tokens": 272000,
            "input_per_1m": 5.0, "output_per_1m": 22.5, "cached_input_per_1m": 0.5
          },
          "provider": "openai",             # optional
          "aliases": ["openai/gpt-5.4"]     # optional: other names for this model in logs
        }
      }
    }

Prices are USD per one million tokens and ``as_of`` is required, so every number can be
traced to the date its prices were taken. Pricing fails closed: a request that uses a
token class the model has no price for (for example cache reads on a model without a
``cached_input_per_1m``) is reported as unpriced with a reason, never priced at a guessed
rate. The one documented exception is reasoning tokens: all three major providers bill
them as output tokens, so without ``reasoning_per_1m`` they are priced at the output rate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .io import read_json, validate_file_path
from .schema import is_aggregate

BUILTIN_SOURCE = "built-in"

# Reasons a row cannot be priced (reported in `unpriced_reasons`).
NO_TOKENS = "missing_token_counts"
NO_CACHED_INPUT_PRICE = "no_cached_input_price"
NO_CACHE_WRITE_PRICE = "no_cache_write_price"
NO_CACHE_WRITE_1H_PRICE = "no_cache_write_1h_price"

_OPTIONAL_PRICES = (
    "cached_input_per_1m",
    "cache_write_per_1m",
    "cache_write_1h_per_1m",
    "reasoning_per_1m",
)


def _price_value(model: str, name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"price for {model!r}: {name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"price for {model!r}: {name} must be a finite non-negative number")
    return float(value)


def _optional_price(model: str, entry: dict[str, Any], name: str) -> float | None:
    if entry.get(name) is None:
        return None
    return _price_value(model, name, entry[name])


@dataclass(frozen=True)
class TierPrice:
    """Per-1M-token prices for one context tier."""

    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float | None = None
    cache_write_per_1m: float | None = None
    cache_write_1h_per_1m: float | None = None
    reasoning_per_1m: float | None = None

    @staticmethod
    def parse(model: str, entry: dict[str, Any]) -> TierPrice:
        return TierPrice(
            input_per_1m=_price_value(model, "input_per_1m", entry.get("input_per_1m")),
            output_per_1m=_price_value(model, "output_per_1m", entry.get("output_per_1m")),
            **{k: _optional_price(model, entry, k) for k in _OPTIONAL_PRICES},
        )


@dataclass(frozen=True)
class ModelPrice(TierPrice):
    """A model's standard prices, plus an optional long-context tier."""

    long_context_threshold: int | None = None
    long_context: TierPrice | None = None
    provider: str | None = None
    aliases: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class RowCost:
    """The price of one row, or why it has none."""

    cost_usd: float | None
    reason: str | None = None
    base_tier_assumed: bool = False


def _parse_model(name: str, entry: Any) -> ModelPrice:
    if not isinstance(entry, dict):
        raise ValueError(f"price for {name!r} must be an object")
    base = TierPrice.parse(name, entry)
    threshold: int | None = None
    long_tier: TierPrice | None = None
    lc = entry.get("long_context")
    if lc is not None:
        if not isinstance(lc, dict):
            raise ValueError(f"price for {name!r}: long_context must be an object")
        t = lc.get("threshold_tokens")
        if isinstance(t, bool) or not isinstance(t, int) or t <= 0:
            raise ValueError(f"price for {name!r}: long_context.threshold_tokens must be > 0")
        threshold = t
        long_tier = TierPrice.parse(name, lc)
    provider = entry.get("provider")
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        raise ValueError(f"price for {name!r}: provider must be a non-empty string")
    aliases = entry.get("aliases") or []
    if not isinstance(aliases, list) or not all(isinstance(a, str) and a for a in aliases):
        raise ValueError(f"price for {name!r}: aliases must be a list of strings")
    return ModelPrice(
        input_per_1m=base.input_per_1m,
        output_per_1m=base.output_per_1m,
        cached_input_per_1m=base.cached_input_per_1m,
        cache_write_per_1m=base.cache_write_per_1m,
        cache_write_1h_per_1m=base.cache_write_1h_per_1m,
        reasoning_per_1m=base.reasoning_per_1m,
        long_context_threshold=threshold,
        long_context=long_tier,
        provider=provider.strip() if isinstance(provider, str) else None,
        aliases=tuple(a.strip() for a in aliases),
    )


def _int_field(row: dict[str, Any], name: str) -> int | None:
    v = row.get(name)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return None
    return v


@dataclass(frozen=True)
class PriceTable:
    as_of: str
    source: str
    models: dict[str, ModelPrice]
    _names: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @staticmethod
    def from_json(obj: Any, source: str) -> PriceTable:
        """Parse and validate a price table. Raises ValueError on any malformed entry."""
        if not isinstance(obj, dict):
            raise ValueError("price table must be a JSON object")
        as_of = obj.get("as_of")
        if not isinstance(as_of, str) or not as_of.strip():
            raise ValueError("price table must have a non-empty 'as_of' date string")
        raw_models = obj.get("models")
        if not isinstance(raw_models, dict):
            raise ValueError("price table 'models' must be an object")

        models: dict[str, ModelPrice] = {}
        names: dict[str, str] = {}
        for name, entry in raw_models.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("price table model names must be non-empty strings")
            key = name.strip()
            models[key] = _parse_model(key, entry)
            for alias in (key, *models[key].aliases):
                if alias in names and names[alias] != key:
                    raise ValueError(f"price table name {alias!r} is used by two models")
                names[alias] = key
        return PriceTable(as_of=as_of.strip(), source=source, models=models, _names=names)

    def resolve(self, model: str) -> str | None:
        """Canonical table name for ``model``: exact name, alias, or name without a
        ``provider/`` prefix (``openai/gpt-4o`` -> ``gpt-4o``). None if not priced."""
        if model in self._names:
            return self._names[model]
        if "/" in model:
            return self._names.get(model.split("/", 1)[1])
        return None

    def cost_usd(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Cost of a plain request (no cache or reasoning detail). KeyError if unpriced."""
        name = self.resolve(model)
        if name is None:
            raise KeyError(model)
        result = self.row_cost(name, {"tokens_in": tokens_in, "tokens_out": tokens_out})
        if result.cost_usd is None:  # pragma: no cover - plain token counts always price
            raise KeyError(model)
        return result.cost_usd

    def row_cost(self, model: str, row: dict[str, Any]) -> RowCost:
        """Price ``row``'s token counts at ``model``'s prices (``model`` must resolve)."""
        name = self.resolve(model)
        if name is None:
            raise KeyError(model)
        price = self.models[name]
        tin = _int_field(row, "tokens_in")
        tout = _int_field(row, "tokens_out")
        if tin is None or tout is None:
            return RowCost(None, NO_TOKENS)
        cache_read = _int_field(row, "tokens_cache_read") or 0
        cache_write = _int_field(row, "tokens_cache_write") or 0
        cache_write_1h = _int_field(row, "tokens_cache_write_1h") or 0
        reasoning = _int_field(row, "tokens_reasoning") or 0

        tier: TierPrice = price
        base_assumed = False
        if price.long_context is not None and price.long_context_threshold is not None:
            if is_aggregate(row):
                # An aggregate row hides each request's prompt size: priced at the base tier
                # and reported, never silently.
                base_assumed = True
            elif tin > price.long_context_threshold:
                tier = price.long_context

        if cache_read and tier.cached_input_per_1m is None:
            return RowCost(None, NO_CACHED_INPUT_PRICE)
        cache_write_default = cache_write - cache_write_1h
        if cache_write_default and tier.cache_write_per_1m is None:
            return RowCost(None, NO_CACHE_WRITE_PRICE)
        if cache_write_1h and tier.cache_write_1h_per_1m is None:
            return RowCost(None, NO_CACHE_WRITE_1H_PRICE)

        uncached = tin - cache_read - cache_write
        reasoning_rate = (
            tier.reasoning_per_1m if tier.reasoning_per_1m is not None else tier.output_per_1m
        )
        micro = (
            uncached * tier.input_per_1m
            + cache_read * (tier.cached_input_per_1m or 0.0)
            + cache_write_default * (tier.cache_write_per_1m or 0.0)
            + cache_write_1h * (tier.cache_write_1h_per_1m or 0.0)
            + (tout - reasoning) * tier.output_per_1m
            + reasoning * reasoning_rate
        )
        return RowCost(micro / 1_000_000, None, base_assumed)


def builtin_price_bytes() -> bytes:
    """Raw bytes of the built-in price table (hashed into report inputs)."""
    return (
        resources.files("toolkit_cost_latency_opt").joinpath("data/model_prices.json").read_bytes()
    )


def load_builtin_prices() -> PriceTable:
    text = builtin_price_bytes().decode("utf-8")
    return PriceTable.from_json(json.loads(text), source=BUILTIN_SOURCE)


def load_price_file(path: Path) -> PriceTable:
    resolved = validate_file_path(path, {".json"})
    return PriceTable.from_json(read_json(resolved), source=str(resolved))


def token_counts(row: dict[str, Any]) -> tuple[int, int] | None:
    """Return (tokens_in, tokens_out) if both are valid non-negative ints, else None."""
    tin = _int_field(row, "tokens_in")
    tout = _int_field(row, "tokens_out")
    if tin is None or tout is None:
        return None
    return tin, tout
