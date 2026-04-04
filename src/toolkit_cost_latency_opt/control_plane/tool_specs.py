"""
CLI command → ToolSpec mapping for toolkit-cost-optimizer.

Maps the 4 CLI subcommands (validate, summarize, recommend, simulate)
to ToolSpec contracts with appropriate permission scope and approval policy.

All commands are READ_ONLY + AUTO — this is a read/analysis-only toolkit;
it reads log files and produces reports but never modifies external state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ApprovalPolicy, AuthorityBoundary, PermissionScope, ToolSpec


@dataclass
class ToolkitCommandSpec:
    """Maps a CLI subcommand name to its ToolSpec and authority boundary."""

    command: str
    spec: ToolSpec
    boundary: AuthorityBoundary


def _make_spec(
    name: str,
    description: str,
    input_schema: dict[str, Any] | None = None,
) -> ToolSpec:
    """Create a ToolSpec for a read-only CLI command.

    Note: approval_policy is NOT a field on ToolSpec (it lives on AuthorityBoundary).
    The ToolSpec carries permission_scope; the paired AuthorityBoundary carries approval.
    """
    return ToolSpec(
        name=name,
        description=description,
        category="tool",
        version="1.0.0",
        owner="toolkit-cost-optimizer",
        permission_scope=PermissionScope.READ_ONLY,
        input_schema=input_schema,
        output_schema=None,
        sandbox_requirement=None,
        aliases=None,
    )


_READ_ONLY_AUTO = AuthorityBoundary(
    scope=PermissionScope.READ_ONLY,
    approval=ApprovalPolicy.AUTO,
)

# ── Per-command specs ─────────────────────────────────────────────────────────

TOOLKIT_TOOL_SPECS: dict[str, ToolkitCommandSpec] = {
    "validate": ToolkitCommandSpec(
        command="validate",
        spec=_make_spec(
            name="validate",
            description=(
                "Validate JSONL inference-event logs against the Toolkit schema. "
                "Read-only; reports validation errors to stdout."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "input_file": {"type": "string", "description": "Path to JSONL log file"},
                    "format": {"type": "string", "enum": ["json", "text"]},
                    "max_errors": {"type": "integer"},
                },
                "required": ["input_file"],
            },
        ),
        boundary=_READ_ONLY_AUTO,
    ),
    "summarize": ToolkitCommandSpec(
        command="summarize",
        spec=_make_spec(
            name="summarize",
            description=(
                "Summarize inference-event logs per model: token counts, cost, "
                "latency percentiles. Read-only report."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "input_file": {"type": "string"},
                    "format": {"type": "string", "enum": ["json", "text"]},
                    "top_n": {"type": "integer"},
                },
                "required": ["input_file"],
            },
        ),
        boundary=_READ_ONLY_AUTO,
    ),
    "recommend": ToolkitCommandSpec(
        command="recommend",
        spec=_make_spec(
            name="recommend",
            description=(
                "Recommend a default model under SLO constraints from historical logs. "
                "Read-only analysis."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "input_file": {"type": "string"},
                    "latency_slo_ms": {"type": "integer"},
                    "cost_weight": {"type": "number"},
                },
                "required": ["input_file"],
            },
        ),
        boundary=_READ_ONLY_AUTO,
    ),
    "simulate": ToolkitCommandSpec(
        command="simulate",
        spec=_make_spec(
            name="simulate",
            description=(
                "Simulate a tier policy over historical logs to project "
                "cost and latency outcomes. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "input_file": {"type": "string"},
                    "tier": {"type": "string", "enum": ["economy", "standard", "premium"]},
                    "format": {"type": "string", "enum": ["json", "text"]},
                },
                "required": ["input_file"],
            },
        ),
        boundary=_READ_ONLY_AUTO,
    ),
}


def get_tool_spec(command: str) -> ToolkitCommandSpec | None:
    """Return the ToolkitCommandSpec for a CLI subcommand, or None if unknown."""
    return TOOLKIT_TOOL_SPECS.get(command)


__all__ = ["TOOLKIT_TOOL_SPECS", "ToolkitCommandSpec", "get_tool_spec"]
