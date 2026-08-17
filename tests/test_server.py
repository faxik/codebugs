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
import inspect
import os
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


class TestPreflight:
    """CB-11: a server pointed at nothing must SAY so, once, and keep running.

    Before this, `_conn` connected lazily per call, so a misconfigured server
    looked healthy at startup and failed every tool call forever with no single
    moment that named the problem. The card's constraint is equally load-bearing
    in the other direction: the preflight must not be fatal, or a server whose
    project appears later stops self-healing.
    """

    def test_warns_when_no_tracker_is_reachable(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        server._preflight()
        err = capsys.readouterr().err
        assert str(tmp_path) in err
        assert "codebugs init" in err

    def test_does_not_exit_when_no_tracker_is_reachable(self, tmp_path, monkeypatch):
        """Warn-only. A SystemExit here would break lazy-connect self-healing."""
        monkeypatch.chdir(tmp_path)
        server._preflight()  # must simply return

    def test_silent_on_the_ordinary_discovered_path(self, tmp_path, monkeypatch, capsys):
        """No noise per project per startup — the default binding is unremarkable."""
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        server._preflight()
        assert capsys.readouterr().err == ""

    def test_announces_a_declared_root(self, tmp_path, monkeypatch, capsys):
        """A non-default binding is worth exactly one line, on the record."""
        db.init_project(str(tmp_path))
        monkeypatch.setenv(db.ENV_ROOT, str(tmp_path))
        server._preflight()
        err = capsys.readouterr().err
        assert str(tmp_path) in err
        assert db.ENV_ROOT in err

    def test_a_declared_root_that_fails_names_the_channel(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(db.ENV_ROOT, str(tmp_path / "nope"))
        server._preflight()
        err = capsys.readouterr().err
        assert db.ENV_ROOT in err

    def test_survives_a_deleted_working_directory(self, tmp_path, capsys):
        """The server outliving its worktree must warn, not die before `run()`."""
        doomed = tmp_path / "doomed"
        doomed.mkdir()
        original = os.getcwd()
        os.chdir(doomed)
        try:
            doomed.rmdir()
            server._preflight()  # must not raise
            assert capsys.readouterr().err != ""
        finally:
            os.chdir(original)


class TestInterpreterIndependentDescriptions:
    """CB-73 — what a client sees must not depend on which Python built the server.

    The SDK reads `Tool.description` from `__doc__`. CPython 3.13 dedents
    docstrings at compile time; 3.11 and 3.12 do not. MCP clients render
    descriptions as Markdown, and CommonMark treats a 4-space-indented line
    following a blank line as an INDENTED CODE BLOCK — so on a 3.12 host the
    whole prose body of ~61 tools rendered monospaced as code.

    MEASURED end to end on both interpreters, which is what the fix actually
    rests on: 64 of 68 descriptions differed between a 3.12- and a 3.13-hosted
    server, and 61 of 68 contained the code-block pattern; both are 0 after the
    fix, and 3.13 output is byte-identical before and after. That comparison
    needs two interpreters, so it is NOT run here — reproduce with
    `uv run --python 3.12 --extra dev` against `_NormalizedDescriptions`.

    These tests are interpreter-independent instead: they feed the adapter a
    docstring that ALREADY carries indentation — exactly what 3.12 hands the SDK
    — so they discriminate on any host.
    """

    INDENTED = "Summary line.\n\n    Body that CommonMark would render as code.\n    "

    def _register(self, target, doc, **kw):
        def sample(a: str) -> dict:
            return {}

        sample.__doc__ = doc
        target.tool(**kw)(sample)

    def test_an_indented_docstring_is_normalized_before_the_client_sees_it(self):
        """Fails against the unfixed tree, where the raw docstring reaches the
        SDK untouched."""
        raw = MCPServer("raw")
        self._register(raw, self.INDENTED)
        before = asyncio.run(raw.list_tools())[0].description

        fixed = MCPServer("fixed")
        self._register(server._NormalizedDescriptions(fixed), self.INDENTED)
        after = asyncio.run(fixed.list_tools())[0].description

        assert "\n    " in before, (
            "fixture is wrong: the unwrapped server was expected to keep the indentation"
        )
        assert "\n    " not in after, after
        assert after.startswith("Summary line.")
        assert "Body that CommonMark" in after

    def test_an_explicit_description_still_wins(self):
        """The adapter normalizes; it does not decide. A caller that passed a
        description has already said what the client should see."""
        srv = MCPServer("explicit")
        self._register(
            server._NormalizedDescriptions(srv), self.INDENTED, description="chosen text"
        )
        assert asyncio.run(srv.list_tools())[0].description == "chosen text"

    def test_a_tool_with_no_docstring_still_registers(self):
        srv = MCPServer("nodoc")
        self._register(server._NormalizedDescriptions(srv), None)
        tools = asyncio.run(srv.list_tools())
        assert len(tools) == 1

    def test_normalization_is_idempotent_so_a_3_13_host_is_unaffected(self):
        """Why the golden does not move: on 3.13 the docstring is already
        dedented, so the adapter is a no-op there."""
        already = "Summary line.\n\nBody.\n"
        assert server.dedent_docstring(already) == already

    def test_the_registrar_is_actually_wired_into_main(self):
        """Structural, and this repo's own lesson for exactly this shape: a
        helper that is unit-tested but never invoked leaves the suite green
        while the defect ships. `main()` cannot be executed here (it parses argv
        and calls `server.run()`), so the wiring is read."""
        src = inspect.getsource(server.main)
        assert "_NormalizedDescriptions(server)" in src, src
        assert "provider.register_fn(registrar, _conn)" in src, src

    def test_the_normalizer_has_exactly_one_definition(self):
        """The gate and the server must not be able to disagree about what
        'normalized' means. `tests/_mcp_schema` imports this one rather than
        carrying a copy, which is how CB-70's helper started."""
        from tests import _mcp_schema

        assert _mcp_schema.dedent_docstring is server.dedent_docstring
