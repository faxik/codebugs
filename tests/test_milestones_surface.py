"""Frozen-surface contract for the milestones module.

Autosorter's worktree-setup.sh / worktree-finish.sh call milestones MCP tools
and CLI subcommands BY NAME. This module's public surface must therefore stay
byte-identical across the package split (and any future refactor).

Two tool-name mechanisms are both load-bearing:
  - 10 tools use explicit @mcp.tool(name="..."); identity is the kwarg.
  - 8 foundation tools use bare @mcp.tool(); identity is the inner def name.
A bare-decorator rename would silently change the wire name — this test is the
guard prose rules cannot provide. (The repo-wide golden in test_boundary.py
also covers MCP names; this test localizes failures to milestones and adds the
CLI-subcommand + re-export guards the golden does not.)
"""

from __future__ import annotations

import pytest

from codebugs import milestones

# --- Expected frozen surface ------------------------------------------------

EXPECTED_TOOL_NAMES = {
    # bare @mcp.tool() — wire name derived from the inner def name
    "milestone_create",
    "milestone_update",
    "milestone_list",
    "milestone_status",
    "milestone_add_item",
    "milestone_move_item",
    "milestone_set_status",
    "milestone_audit_query",
    # CB-107: reconcile_all's own MCP surface, dry-run default preserved
    # (apply=false unless the caller passes a literal JSON true).
    "milestone_reconcile",
    # explicit @mcp.tool(name=...) — spec-canonical identifiers
    "triage_inbox",
    "triage_dismiss",
    "triage_promote",
    "pull_next",
    "release_item",
    "wip_status",
    "mark_branch_only",
    "mark_integrated",
    "milestone_close",
    "milestone_defer",
}

EXPECTED_CLI_SUBCOMMANDS = {
    "milestone-list",
    "milestone-status",
    "milestone-audit",
    "triage-inbox",
    "wip-status",
    "milestone-mark-branch",
    "milestone-mark-integrated",
    # CB-26 repair tool. Also reachable from MCP as `milestone_reconcile`
    # since CB-107 -- this CLI verb's own contract (dry run by default,
    # --apply to write) is unchanged; the MCP wrapper preserves the same
    # apply=False default rather than replacing this surface.
    "milestone-reconcile",
}

# Every symbol callers (incl. the test suite) reach via codebugs.milestones.*
# These must remain importable from the package facade after the split.
REEXPORTED_NAMES = [
    # public API
    "create_milestone",
    "update_milestone",
    "list_milestones",
    "get_milestone_status",
    "add_milestone_item",
    "move_milestone_item",
    "set_item_status",
    "query_audit",
    "triage_inbox",
    "triage_dismiss",
    "triage_promote",
    "pull_next",
    "release_item",
    "get_wip_status",
    "mark_branch_only",
    "mark_integrated",
    "milestone_defer",
    "milestone_close",
    # private / constants the test suite reaches directly
    "_get_item_by_ref",
    "_eligibility_failure",
    "AUTO_ROUTER_ACTOR",
    "ensure_schema",
    "register_tools",
    "register_cli",
]


# --- Fakes ------------------------------------------------------------------


class _FakeMcp:
    """Records the wire name each @mcp.tool(...) would expose."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def tool(self, name: str | None = None, **_kw):
        def deco(fn):
            self.names.append(name or fn.__name__)
            return fn

        return deco


class _FakeParser:
    def add_argument(self, *_a, **_k) -> None:
        pass

    def set_defaults(self, *_a, **_k) -> None:
        pass


class _FakeSub:
    """Records every add_parser(name, ...) call."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def add_parser(self, name: str, **_kw) -> _FakeParser:
        self.names.append(name)
        return _FakeParser()


# --- Tests ------------------------------------------------------------------


class TestFrozenSurface:
    def test_mcp_tool_names(self):
        fake = _FakeMcp()
        milestones.register_tools(fake, lambda: None)
        got = set(fake.names)
        assert len(fake.names) == len(got), f"duplicate tool name emitted: {fake.names}"
        assert got == EXPECTED_TOOL_NAMES, (
            f"MCP tool surface drift.\n"
            f"  added:   {sorted(got - EXPECTED_TOOL_NAMES)}\n"
            f"  removed: {sorted(EXPECTED_TOOL_NAMES - got)}"
        )

    def test_cli_subcommand_names(self):
        fake = _FakeSub()
        commands: dict = {}
        milestones.register_cli(fake, commands)
        got = set(fake.names)
        assert got == EXPECTED_CLI_SUBCOMMANDS, (
            f"CLI subcommand surface drift.\n"
            f"  added:   {sorted(got - EXPECTED_CLI_SUBCOMMANDS)}\n"
            f"  removed: {sorted(EXPECTED_CLI_SUBCOMMANDS - got)}"
        )
        # dispatch dict keys must match the argparse subcommand strings
        assert set(commands) == EXPECTED_CLI_SUBCOMMANDS, (
            f"commands dict keys drift: {sorted(set(commands) ^ EXPECTED_CLI_SUBCOMMANDS)}"
        )

    @pytest.mark.parametrize("name", REEXPORTED_NAMES)
    def test_reexport_resolves(self, name):
        assert hasattr(milestones, name), (
            f"codebugs.milestones.{name} is not importable — add it to the "
            f"__init__ re-export manifest."
        )
