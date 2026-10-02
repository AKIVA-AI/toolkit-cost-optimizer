"""Write a LiteLLM proxy config (routing + budgets) for a chosen routing policy.

Shape, from the LiteLLM proxy docs (https://docs.litellm.ai/docs/proxy/configs,
https://docs.litellm.ai/docs/proxy/users, https://docs.litellm.ai/docs/proxy/provider_budget_routing):

.. code-block:: yaml

    model_list:
      - model_name: <tier>                  # the name clients request
        litellm_params:
          model: <provider>/<model>         # e.g. openai/gpt-5.4-mini
          api_key: os.environ/OPENAI_API_KEY
          max_budget: 12.5                  # per-deployment budget (USD)
          budget_duration: 30d
    litellm_settings:
      max_budget: 40.0                      # proxy-wide budget; needs budget_duration
      budget_duration: 30d

Budgets are the chosen policy's cost over the observed log window, scaled to
``budget_duration`` and multiplied by a headroom factor. No YAML library is needed: the
writer emits a small, fixed structure with JSON-quoted strings (valid YAML scalars).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .pricing import PriceTable

# Environment variables LiteLLM reads for each provider's key (litellm.utils
# validate_environment): OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY.
PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Duration formats documented for LiteLLM budgets: "30s", "30m", "30h", "30d".
_DURATION = re.compile(r"^([1-9][0-9]*)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    match = _DURATION.match(text.strip())
    if not match:
        raise ValueError(f"budget duration must look like 30d, 12h, 90m or 3600s, got: {text!r}")
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def litellm_model(prices: PriceTable, model: str) -> tuple[str, str | None]:
    """(``provider/model`` for litellm_params.model, provider or None)."""
    name = prices.resolve(model) or model
    price = prices.models.get(name)
    provider = price.provider if price is not None else None
    if provider and not name.startswith(f"{provider}/"):
        return f"{provider}/{name}", provider
    return name, provider


def build_config(
    *,
    policy: dict[str, str],
    tier_costs: dict[str, float],
    prices: PriceTable,
    window_seconds: float,
    budget_duration: str,
    headroom: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (config, notes). Budgets are omitted when the log window is empty."""
    duration_s = parse_duration(budget_duration)
    if not (math.isfinite(headroom) and headroom >= 1.0):
        raise ValueError("--budget-headroom must be a number >= 1")
    scale = duration_s / window_seconds * headroom if window_seconds > 0 else None

    model_list = []
    missing_provider = []
    for tier in sorted(policy):
        target, provider = litellm_model(prices, policy[tier])
        params: dict[str, Any] = {"model": target}
        if provider in PROVIDER_KEY_ENV:
            params["api_key"] = f"os.environ/{PROVIDER_KEY_ENV[provider]}"
        elif provider is None:
            missing_provider.append(policy[tier])
        if scale is not None:
            params["max_budget"] = _money(tier_costs[tier] * scale)
            params["budget_duration"] = budget_duration
        model_list.append({"model_name": tier, "litellm_params": params})

    config: dict[str, Any] = {"model_list": model_list}
    total_budget = None
    if scale is not None:
        total_budget = _money(sum(tier_costs[t] for t in policy) * scale)
        config["litellm_settings"] = {
            "max_budget": total_budget,
            "budget_duration": budget_duration,
        }
    notes = {
        "budget_usd": total_budget,
        "budget_duration": budget_duration if scale is not None else None,
        "budget_headroom": headroom,
        "budget_omitted_reason": None if scale is not None else "empty_log_window",
        "models_without_provider": sorted(set(missing_provider)),
    }
    return config, notes


def _money(x: float) -> float:
    """Round a budget up to the cent so the cap never undercuts the projection."""
    return math.ceil(x * 100 - 1e-9) / 100


def to_yaml(obj: Any, indent: int = 0) -> str:
    """Emit dicts, lists and scalars as block YAML (strings JSON-quoted)."""
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key}:")
                lines.append(to_yaml(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_scalar(value)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item:
                first, *rest = to_yaml(item, indent + 1).split("\n")
                lines.append(f"{pad}- {first.lstrip()}")
                lines.extend(rest)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(f"{pad}{_scalar(obj)}")
    return "\n".join(lines)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, (dict, list)):
        return "{}" if isinstance(value, dict) else "[]"
    return json.dumps(str(value))
