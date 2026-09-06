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
        stale = []
        for site in conftest._HAND_BUILT_SURFACES_ALLOWED:
            filename, _, function = site.partition("::")
            path = Path(conftest._TESTS_DIR) / filename
            if not path.exists():
                stale.append(site)
                continue
            names = {
                node.name
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            if function not in names:
                stale.append(site)
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
