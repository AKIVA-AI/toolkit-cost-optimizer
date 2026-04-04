"""
Config hierarchy contract for toolkit-cost-optimizer.

Three-tier hierarchy (mirrors Akiva platform pattern):
  Level 0 — Platform defaults (global Akiva CLI conventions)
  Level 1 — Toolkit config (pyproject.toml / config file)
  Level 2 — CLI overrides (argv flags)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolkitConfigContract:
    """
    Resolved configuration contract for toolkit-cost-optimizer.

    All fields represent resolved values after applying the three-tier
    hierarchy (platform defaults → toolkit config → CLI overrides).
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    toolkit_id: str = "TK-01"
    toolkit_name: str = "toolkit-cost-optimizer"
    version: str = "1.0.0"

    # ── Runtime behaviour ─────────────────────────────────────────────────────
    log_format: str = "json"          # 'json' | 'text'
    max_log_lines: int = 10_000       # hard cap on JSONL input
    structured_logging: bool = True
    output_format: str = "json"       # 'json' | 'text'

    # ── Policy / tier ─────────────────────────────────────────────────────────
    default_tier: str = "standard"    # 'economy' | 'standard' | 'premium'
    slo_latency_ms: int = 2000        # latency SLO ceiling

    # ── Extension ─────────────────────────────────────────────────────────────
    extra: dict[str, Any] = field(default_factory=dict)


# Config hierarchy levels — mirrors the TypeScript CONFIG_HIERARCHY_LEVELS pattern
# used in HubZone and Website adapters.
CONFIG_LEVELS = {
    "platform_default": 0,
    "toolkit_config": 1,
    "cli_override": 2,
}


def build_config_hierarchy(
    toolkit_config: dict[str, Any] | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ToolkitConfigContract:
    """
    Merge config tiers into a resolved ToolkitConfigContract.

    Priority: CLI overrides > toolkit config > platform defaults.

    Parameters
    ----------
    toolkit_config:
        Values loaded from pyproject.toml [tool.toolkit-cost-optimizer]
        or equivalent config file.
    cli_overrides:
        Values parsed from CLI argv (e.g. ``--output-format text``).

    Returns
    -------
    ToolkitConfigContract
        Fully resolved configuration contract.
    """
    # Start with platform defaults
    resolved: dict[str, Any] = {
        "toolkit_id": "TK-01",
        "toolkit_name": "toolkit-cost-optimizer",
        "version": "1.0.0",
        "log_format": "json",
        "max_log_lines": 10_000,
        "structured_logging": True,
        "output_format": "json",
        "default_tier": "standard",
        "slo_latency_ms": 2000,
        "extra": {},
    }

    # Layer 1: toolkit config
    if toolkit_config:
        for k, v in toolkit_config.items():
            if k in resolved:
                resolved[k] = v
            else:
                resolved["extra"][k] = v

    # Layer 2: CLI overrides (highest priority)
    if cli_overrides:
        for k, v in cli_overrides.items():
            if k in resolved:
                resolved[k] = v
            else:
                resolved["extra"][k] = v

    return ToolkitConfigContract(**{k: v for k, v in resolved.items()})


__all__ = ["ToolkitConfigContract", "CONFIG_LEVELS", "build_config_hierarchy"]
