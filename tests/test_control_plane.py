"""
Tests for toolkit-cost-optimizer control_plane adapter.

Coverage:
  - contracts: PermissionScope ordinal, AuthorityBoundary helpers
  - config: build_config_hierarchy (platform defaults, overrides, CLI)
  - tool_specs: TOOLKIT_TOOL_SPECS covers all 4 commands, get_tool_spec lookup
  - Optional framework import: _HAS_EXECUTION_CONTRACTS flag is a bool (no crash)
"""

from __future__ import annotations

import pytest

from toolkit_cost_latency_opt.control_plane.config import (
    CONFIG_LEVELS,
    ToolkitConfigContract,
    build_config_hierarchy,
)
from toolkit_cost_latency_opt.control_plane.contracts import (
    _HAS_EXECUTION_CONTRACTS,
    ApprovalPolicy,
    AuthorityBoundary,
    PermissionScope,
    ToolSpec,
)
from toolkit_cost_latency_opt.control_plane.tool_specs import (
    TOOLKIT_TOOL_SPECS,
    get_tool_spec,
)

# ── contracts ─────────────────────────────────────────────────────────────────


class TestPermissionScope:
    def test_values_are_strings(self) -> None:
        assert PermissionScope.READ_ONLY.value == "read_only"
        assert PermissionScope.WORKSPACE_WRITE.value == "workspace_write"
        assert PermissionScope.FULL_ACCESS.value == "full_access"

    def test_ordinal_ascending(self) -> None:
        order = [
            PermissionScope.READ_ONLY,
            PermissionScope.WORKSPACE_WRITE,
            PermissionScope.FULL_ACCESS,
        ]
        boundary = AuthorityBoundary(
            scope=PermissionScope.FULL_ACCESS, approval=ApprovalPolicy.AUTO
        )
        # full_access scope_allows read_only
        assert boundary.scope_allows(PermissionScope.READ_ONLY)

    def test_lower_does_not_satisfy_higher(self) -> None:
        boundary = AuthorityBoundary(scope=PermissionScope.READ_ONLY, approval=ApprovalPolicy.AUTO)
        assert not boundary.scope_allows(PermissionScope.WORKSPACE_WRITE)
        assert not boundary.scope_allows(PermissionScope.FULL_ACCESS)


class TestApprovalPolicy:
    def test_values_are_strings(self) -> None:
        assert ApprovalPolicy.AUTO.value == "auto"
        assert ApprovalPolicy.REQUIRE_APPROVAL.value == "require_approval"
        assert ApprovalPolicy.DENY.value == "deny"


class TestAuthorityBoundary:
    def test_is_denied(self) -> None:
        b = AuthorityBoundary(scope=PermissionScope.READ_ONLY, approval=ApprovalPolicy.DENY)
        assert b.is_denied()
        assert not b.needs_approval()

    def test_needs_approval(self) -> None:
        b = AuthorityBoundary(
            scope=PermissionScope.FULL_ACCESS, approval=ApprovalPolicy.REQUIRE_APPROVAL
        )
        assert b.needs_approval()
        assert not b.is_denied()

    def test_auto_neither(self) -> None:
        b = AuthorityBoundary(scope=PermissionScope.WORKSPACE_WRITE, approval=ApprovalPolicy.AUTO)
        assert not b.is_denied()
        assert not b.needs_approval()

    def test_sandbox_defaults_none(self) -> None:
        b = AuthorityBoundary(scope=PermissionScope.READ_ONLY, approval=ApprovalPolicy.AUTO)
        assert b.sandbox is None


class TestToolSpec:
    def test_construction(self) -> None:
        spec = ToolSpec(
            name="validate",
            description="test",
            category="tool",
            version="1.0.0",
            owner="toolkit-cost-optimizer",
            permission_scope=PermissionScope.READ_ONLY,
        )
        assert spec.name == "validate"
        assert spec.permission_scope == PermissionScope.READ_ONLY
        # approval_policy is NOT a ToolSpec field — it lives on AuthorityBoundary
        assert spec.input_schema is None
        assert spec.aliases is None

    def test_repr_contains_name(self) -> None:
        spec = ToolSpec(
            name="validate",
            description="test",
            category="tool",
            version="1.0.0",
            owner="o",
            permission_scope=PermissionScope.READ_ONLY,
        )
        assert "validate" in repr(spec)


class TestFrameworkFlag:
    def test_flag_is_bool(self) -> None:
        """_HAS_EXECUTION_CONTRACTS must be a bool regardless of install state."""
        assert isinstance(_HAS_EXECUTION_CONTRACTS, bool)


# ── config ────────────────────────────────────────────────────────────────────


class TestConfigLevels:
    def test_ordering(self) -> None:
        assert CONFIG_LEVELS["platform_default"] < CONFIG_LEVELS["toolkit_config"]
        assert CONFIG_LEVELS["toolkit_config"] < CONFIG_LEVELS["cli_override"]


class TestBuildConfigHierarchy:
    def test_defaults(self) -> None:
        cfg = build_config_hierarchy()
        assert cfg.toolkit_id == "TK-01"
        assert cfg.toolkit_name == "toolkit-cost-optimizer"
        assert cfg.log_format == "json"
        assert cfg.structured_logging is True
        assert cfg.default_tier == "standard"
        assert cfg.slo_latency_ms == 2000

    def test_toolkit_config_overrides_defaults(self) -> None:
        cfg = build_config_hierarchy(toolkit_config={"log_format": "text", "slo_latency_ms": 3000})
        assert cfg.log_format == "text"
        assert cfg.slo_latency_ms == 3000
        # defaults preserved for unset fields
        assert cfg.default_tier == "standard"

    def test_cli_overrides_toolkit_config(self) -> None:
        cfg = build_config_hierarchy(
            toolkit_config={"log_format": "text"},
            cli_overrides={"log_format": "json", "output_format": "text"},
        )
        assert cfg.log_format == "json"  # CLI wins
        assert cfg.output_format == "text"

    def test_unknown_keys_go_to_extra(self) -> None:
        cfg = build_config_hierarchy(toolkit_config={"custom_flag": True})
        assert cfg.extra.get("custom_flag") is True

    def test_cli_unknown_keys_go_to_extra(self) -> None:
        cfg = build_config_hierarchy(cli_overrides={"verbose": True})
        assert cfg.extra.get("verbose") is True

    def test_returns_toolkit_config_contract(self) -> None:
        cfg = build_config_hierarchy()
        assert isinstance(cfg, ToolkitConfigContract)


# ── tool_specs ────────────────────────────────────────────────────────────────


class TestToolkitToolSpecs:
    def test_all_four_commands_present(self) -> None:
        expected = {"validate", "summarize", "recommend", "simulate"}
        assert set(TOOLKIT_TOOL_SPECS.keys()) == expected

    def test_all_commands_are_read_only(self) -> None:
        for name, cmd_spec in TOOLKIT_TOOL_SPECS.items():
            assert cmd_spec.spec.permission_scope == PermissionScope.READ_ONLY, (
                f"command '{name}' should be READ_ONLY"
            )

    def test_all_commands_are_auto_approved(self) -> None:
        # approval lives on AuthorityBoundary, not ToolSpec
        for name, cmd_spec in TOOLKIT_TOOL_SPECS.items():
            assert cmd_spec.boundary.approval == ApprovalPolicy.AUTO, (
                f"command '{name}' should have AUTO approval on its AuthorityBoundary"
            )

    def test_boundary_scope_matches_spec_scope(self) -> None:
        for name, cmd_spec in TOOLKIT_TOOL_SPECS.items():
            assert cmd_spec.boundary.scope == cmd_spec.spec.permission_scope, name

    def test_no_sandbox_required(self) -> None:
        for name, cmd_spec in TOOLKIT_TOOL_SPECS.items():
            assert cmd_spec.spec.sandbox_requirement is None, (
                f"command '{name}' is read-only and should not require a sandbox"
            )

    def test_all_have_input_schema(self) -> None:
        for name, cmd_spec in TOOLKIT_TOOL_SPECS.items():
            assert cmd_spec.spec.input_schema is not None, (
                f"command '{name}' should have an input schema"
            )

    def test_validate_requires_input_file(self) -> None:
        schema = TOOLKIT_TOOL_SPECS["validate"].spec.input_schema
        assert schema is not None
        assert "input_file" in schema.get("required", [])

    def test_recommend_requires_input_file(self) -> None:
        schema = TOOLKIT_TOOL_SPECS["recommend"].spec.input_schema
        assert schema is not None
        assert "input_file" in schema.get("required", [])

    def test_simulate_has_tier_enum(self) -> None:
        schema = TOOLKIT_TOOL_SPECS["simulate"].spec.input_schema
        assert schema is not None
        tier_prop = schema.get("properties", {}).get("tier", {})
        assert "economy" in tier_prop.get("enum", [])

    def test_command_name_matches_key(self) -> None:
        for key, cmd_spec in TOOLKIT_TOOL_SPECS.items():
            assert cmd_spec.command == key

    def test_owner_is_toolkit(self) -> None:
        for cmd_spec in TOOLKIT_TOOL_SPECS.values():
            assert cmd_spec.spec.owner == "toolkit-cost-optimizer"


class TestGetToolSpec:
    def test_returns_spec_for_known_command(self) -> None:
        spec = get_tool_spec("validate")
        assert spec is not None
        assert spec.command == "validate"

    def test_returns_none_for_unknown_command(self) -> None:
        assert get_tool_spec("nonexistent") is None

    def test_returns_none_for_empty_string(self) -> None:
        assert get_tool_spec("") is None
