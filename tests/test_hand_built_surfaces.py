"""The suite's own guard against judging a hand-built MCP surface (CB-311).

`tests/conftest.py` refuses a `call_tool` into a tool that was registered on a
bare `MCPServer` instead of through `server.build_registrar`. This file is what
keeps that guard from being indistinguishable from no guard at all: it fires the
refusal on purpose, confirms the production route is NOT refused, and holds the
allowance table to the CB-179 discipline.

WHY THE ALLOWANCE TABLE IS EMPTY, AND WHY THAT IS A MEASUREMENT. Three places in
this suite build a bare server deliberately — `test_boundary.py` inspects
un-normalized descriptions, `test_findings.py` enumerates tool names,
`test_add_lines_surface.py` uses a stand-in that is not an `MCPServer` at all —
and every one of them stops short of calling a tool. The guard keys on the CALL,
which is the act that claims "this is the boundary a client meets", so all three
stay legal without a row. Rows would have had to be maintained; a predicate that
does not reach them does not.
"""

from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from codebugs import findings, server
from tests import conftest


def _live_sites() -> set[str]:
    """Every `<file>::<function>` the test tree currently contains.

    The staleness half compares a ROW against this, rather than taking the row
    apart and checking the pieces: a check built on the pieces cannot be seen to
    hold the KEY against the live tree — by `tests/test_exception_table_discipline.py`,
    which measured exactly that and refused this file's first draft — and, more to
    the point, a row whose file and function both exist but never sat together
    would have passed it.
    """
    sites: set[str] = set()
    for path in sorted(Path(conftest._TESTS_DIR).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                sites.add(f"{path.name}::{node.name}")
    return sites


def _factory():
    @contextmanager
    def factory():
        raise AssertionError(
            "the guard must refuse before the tool body runs, so no connection is ever needed"
        )
        yield  # pragma: no cover - unreachable, keeps this a context manager

    return factory


class TestTheGuardFires:
    def test_a_hand_registered_tool_is_refused_at_the_call(self):
        """The mutant this file exists to be: a surface assembled by hand."""
        mcp = MCPServer("cb311-hand-built")
        findings.register_tools(mcp, _factory())
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("get", {"finding_id": "CB-1"}))
        assert "CB-311" in str(refusal.value)
        assert "registered by hand" in str(refusal.value)
        assert "build_registrar" in str(refusal.value)

    def test_the_refusal_names_the_place_and_the_tool(self):
        mcp = MCPServer("cb311-hand-built-named")
        findings.register_tools(mcp, _factory())
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("query", {}))
        message = str(refusal.value)
        assert "'query'" in message
        assert "test_hand_built_surfaces.py::test_the_refusal_names_the_place_and_the_tool" in (
            message
        ), message

    def test_a_tool_registered_under_an_EXPLICIT_name_is_caught_too(self):
        """The name is reconstructed the way the SDK reconstructs it, and that
        reconstruction is a SECOND COPY of the SDK's rule — the guard's one
        silent failure direction. Both registration shapes must therefore be
        exercised: every other test here registers through `register_tools`,
        which uses `@mcp.tool()` with no name at all, so a reconstruction that
        handled only that shape would look fully tested."""
        mcp = MCPServer("cb311-explicit-name")

        def probe() -> dict:
            """A hand-registered tool named explicitly."""

        mcp.tool(name="cb311_named_probe")(probe)
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("cb311_named_probe", {}))
        assert "CB-311" in str(refusal.value)
        assert "'cb311_named_probe'" in str(refusal.value)

    def test_a_tool_added_through_the_public_add_tool_is_caught(self):
        """GAP ONE, found by cross-model review and reproduced before fixing.

        `add_tool` is a PUBLIC method that registers without going through the
        decorator at all. The first version of this guard wrapped the decorator,
        so a test taking this route left no record and its call sailed through —
        the exact scenario the guard exists to forbid, reached by a documented
        API rather than by anything exotic.
        """
        mcp = MCPServer("cb311-add-tool")

        def probe() -> dict:
            """Registered through the public add_tool."""

        mcp.add_tool(probe, name="cb311_added_probe")
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("cb311_added_probe", {}))
        assert "CB-311" in str(refusal.value)

    def test_half_the_production_build_is_caught(self):
        """GAP TWO: `build_registrar` composes TWO adapters.

        Wrapping only the outer one gives bodies that carry the production
        decorator — so the per-tool half of the check is satisfied — while
        description normalization, the other half of what ships, is absent. The
        server half is what catches it, which is why the guard needs both.
        """
        mcp = MCPServer("cb311-half-build")
        half = server._RefusalsReachTheClient(mcp)
        findings.register_tools(half, _factory())
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("query", {}))
        assert "CB-311" in str(refusal.value)

    def test_a_correct_re_registration_clears_a_stale_record(self):
        """A record that outlives the violation refuses correct code.

        Register by hand, then register the same name properly: the call must go
        through. Otherwise a helper fixed mid-file keeps failing on a tool that
        is now built right, and a guard that refuses correct code gets deleted
        by the first person it inconveniences.
        """
        mcp = MCPServer("cb311-restale")
        findings.register_tools(mcp, _factory())
        findings.register_tools(server.build_registrar(mcp), _factory())
        with pytest.raises(BaseException) as raised:
            asyncio.run(mcp.call_tool("query", {}))
        assert "CB-311" not in str(raised.value)
        assert "no connection is ever needed" in str(raised.value)

    def test_a_production_built_surface_is_not_refused(self):
        """The other half: a guard that refused everything would also 'fire'.

        The tool body here is reached — the connection factory raises, and that
        raise arriving is the proof the guard let the call through rather than
        stopping it.
        """
        mcp = MCPServer("cb311-production")
        findings.register_tools(server.build_registrar(mcp), _factory())
        with pytest.raises(BaseException) as raised:
            asyncio.run(mcp.call_tool("query", {}))
        assert "CB-311" not in str(raised.value)
        assert "no connection is ever needed" in str(raised.value)


class TestTheAllowanceTableIsDisciplined:
    def test_every_row_carries_a_reason(self):
        empty = [
            site
            for site, reason in conftest._HAND_BUILT_SURFACES_ALLOWED.items()
            if not reason.strip()
        ]
        assert not empty, (
            f"allowance row(s) with no reason: {empty} -- a table whose rows can lose their "
            "justification becomes the place hand-built surfaces are parked silently."
        )

    def test_no_row_names_a_place_that_no_longer_exists(self):
        live = _live_sites()
        stale = [site for site in conftest._HAND_BUILT_SURFACES_ALLOWED if site not in live]
        assert not stale, (
            f"stale allowance row(s): {stale} -- the place they license is gone, so the row "
            "now licenses nothing and hides the next one that appears under the same name."
        )

    def test_a_row_actually_licenses_the_place_it_names(self):
        """The table is READ by the guard, not merely declared beside it.

        With the table empty this cannot be shown from the live rows, so it is
        shown on the mechanism: the refusal is keyed by the same site string the
        table is keyed by, so adding that string licenses exactly that place.
        """
        site = "test_hand_built_surfaces.py::test_a_row_actually_licenses_the_place_it_names"
        mcp = MCPServer("cb311-licensed")
        findings.register_tools(mcp, _factory())
        conftest._HAND_BUILT_SURFACES_ALLOWED[site] = "temporary row, this test only"
        try:
            with pytest.raises(BaseException) as raised:
                asyncio.run(mcp.call_tool("query", {}))
            assert "CB-311" not in str(raised.value)
            assert "no connection is ever needed" in str(raised.value)
        finally:
            del conftest._HAND_BUILT_SURFACES_ALLOWED[site]
