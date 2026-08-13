"""Server-level contracts: strict tool arguments.

The SDK builds every tool's argument model with pydantic's default
``extra="ignore"``. An unknown argument NAME is therefore dropped during
validation and the tool runs without it, returning a success payload with the
caller's data discarded — while an unknown VALUE raises. That asymmetry turns a
singular/plural typo into invisible data loss (CB-15), so `server.py` installs a
middleware that refuses the call instead.

These tests drive the middleware callable directly. It is the seam where this
project touches a provisional SDK API, so it is worth pinning on its own.

The project has no pytest-asyncio; async work runs through ``asyncio.run`` inside
sync tests, the same idiom as ``test_boundary.py``. An ``async def`` test here
would be collected and never awaited — i.e. it would pass without running.
"""

from __future__ import annotations

import asyncio
import tempfile
import types
from contextlib import contextmanager

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError

from codebugs import db, findings, server


@pytest.fixture
def tracker():
    project = tempfile.mkdtemp()
    db.init_project(project)

    @contextmanager
    def _conn():
        conn = db.connect(project)
        try:
            yield conn
        finally:
            conn.close()

    return _conn


def _server_with_middleware(tracker, mode="findings"):
    mcp = MCPServer("codebugs")
    for provider in db.get_tool_providers(mode=mode):
        provider.register_fn(mcp, tracker)
    server.install_strict_arguments(mcp)
    return mcp, mcp.middleware[-1]


@pytest.fixture
def middleware(tracker):
    """The installed strict-argument middleware, over the findings tool set."""
    return _server_with_middleware(tracker)[1]


def _ctx(name, arguments, method="tools/call"):
    return types.SimpleNamespace(method=method, params={"name": name, "arguments": arguments})


class _Recorder:
    """Stands in for the rest of the middleware chain."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, ctx):
        self.calls += 1
        return {"ok": True}


class TestStrictToolArguments:
    def test_unknown_argument_name_is_refused(self, middleware):
        """The CB-15 reproducer: `note=` instead of `notes=`.

        Before this middleware the call returned a success payload and the text
        was silently discarded.
        """
        call_next = _Recorder()

        with pytest.raises(MCPError) as excinfo:
            asyncio.run(
                middleware(_ctx("update", {"finding_id": "CB-1", "note": "TEXT"}), call_next)
            )

        assert "note" in excinfo.value.message
        assert "notes" in excinfo.value.message, "the message should list what IS accepted"
        assert call_next.calls == 0, "the tool must not run at all"

    def test_declared_arguments_pass_through(self, middleware):
        call_next = _Recorder()
        asyncio.run(
            middleware(_ctx("update", {"finding_id": "CB-1", "append_note": "T"}), call_next)
        )
        assert call_next.calls == 1

    def test_unknown_tool_name_is_delegated(self, middleware):
        """Not ours to answer — the SDK's own 'Unknown tool' error stays authoritative."""
        call_next = _Recorder()
        asyncio.run(middleware(_ctx("no_such_tool", {"anything": 1}), call_next))
        assert call_next.calls == 1

    def test_other_methods_are_untouched(self, middleware):
        call_next = _Recorder()
        asyncio.run(middleware(_ctx("update", {"bogus": 1}, method="tools/list"), call_next))
        assert call_next.calls == 1

    def test_empty_arguments_are_fine(self, middleware):
        call_next = _Recorder()
        asyncio.run(middleware(_ctx("stats", {}), call_next))
        assert call_next.calls == 1

    def test_severity_is_reachable_over_the_wire(self, middleware):
        """CB-17: a domain parameter is not reachable until the wrapper declares it.

        This assertion is only meaningful *because* of CB-15: since the strict
        middleware landed, an undeclared `severity` is refused outright, so this
        test fails loudly against the pre-CB-17 wrapper rather than passing while
        the value is silently dropped.
        """
        call_next = _Recorder()
        asyncio.run(
            middleware(_ctx("update", {"finding_id": "CB-1", "severity": "high"}), call_next)
        )
        assert call_next.calls == 1

    def test_severity_actually_reaches_the_database(self, tracker):
        """Declaring the argument is not the same as forwarding it.

        The middleware test above passes with a recorder standing in for the tool,
        so it would still pass if ``severity=severity`` were deleted from the
        wrapper's call into ``update_finding`` — the argument would be accepted at
        the wire and then silently dropped, which is the CB-15 failure mode wearing
        a different hat. This drives the real tool and reads the row back.
        """
        mcp, _ = _server_with_middleware(tracker)
        with tracker() as conn:
            findings.add_finding(
                conn, severity="medium", category="perf", file="a.py", description="d"
            )

        async def call():
            return await mcp.call_tool(
                "update",
                {"finding_id": "CB-1", "severity": "high", "append_note": "re-measured"},
            )

        asyncio.run(call())

        with tracker() as conn:
            row = findings.get_finding(conn, "CB-1")
        assert row["severity"] == "high", "the wrapper accepted severity but never forwarded it"
        assert row["meta"]["notes"] == "re-measured", "the CB-16 neighbours must still survive"

    def test_every_registered_tool_is_covered(self, tracker):
        """The guard is server-wide, not a findings special case."""
        mcp, mw = _server_with_middleware(tracker, mode="all")

        async def check():
            tools = await mcp.list_tools()
            assert len(tools) > 50, f"expected the full catalogue, got {len(tools)}"
            for tool in tools:
                with pytest.raises(MCPError):
                    await mw(_ctx(tool.name, {"definitely_not_a_real_argument": 1}), _Recorder())
            return len(tools)

        assert asyncio.run(check()) > 50
