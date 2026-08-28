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
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import types
from contextlib import contextmanager

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult

from codebugs import db, findings, server
from tests._mcp_schema import collect_tool_schemas


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
                conn, severity="medium", category="perf", file="a.py", description="d", new_category=True
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


class _RaisingCallNext:
    """Stands in for the rest of the chain when it raises instead of returning."""

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    async def __call__(self, ctx):
        self.calls += 1
        raise self._exc


class _ToolFailureCallNext:
    """Stands in for `_inner` when the tool body raised and `_handle_call_tool`
    already swallowed it into a `CallToolResult(is_error=True)` — the REAL shape
    `install_usage_tracking`'s `call_next` sees for a domain exception, per its
    own docstring's finding about `mcp.server.mcpserver.server._handle_call_tool`.
    """

    def __init__(self):
        self.calls = 0

    async def __call__(self, ctx):
        self.calls += 1
        return CallToolResult(content=[], is_error=True)


def _rows(tracker):
    with tracker() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM tool_calls ORDER BY id").fetchall()]


class TestUsageTracking:
    """Release-b (DIR-1): the `tool_calls` counter that feeds `codebugs usage`.

    Driven directly against `install_usage_tracking`'s middleware, the same
    seam-testing idiom `TestStrictToolArguments` already uses for the sibling
    middleware — this is where this project touches the SDK's provisional
    `MCPServer.middleware`, so it is worth pinning on its own.
    """

    def _middleware(self, tracker):
        mcp = MCPServer("codebugs")
        server.install_usage_tracking(mcp, tracker)
        return mcp.middleware[-1]

    def test_a_successful_call_is_recorded_with_name_and_nonzero_duration(self, tracker):
        mw = self._middleware(tracker)
        call_next = _Recorder()
        asyncio.run(mw(_ctx("stats", {}), call_next))

        rows = _rows(tracker)
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "stats"
        assert rows[0]["success"] == 1
        assert rows[0]["error_type"] is None
        assert rows[0]["duration_ms"] >= 0

    def test_a_raised_exception_is_recorded_as_a_failure_and_still_reaches_the_caller(
        self, tracker
    ):
        """The dispatch-level exception path (an MCPError escaping past
        `_handle_call_tool`'s own swallow, e.g. from a resolver flow) — recording
        must not swallow it a second time."""
        mw = self._middleware(tracker)
        call_next = _RaisingCallNext(MCPError(code=-32000, message="boom"))

        with pytest.raises(MCPError):
            asyncio.run(mw(_ctx("update", {"finding_id": "CB-1"}), call_next))

        rows = _rows(tracker)
        assert len(rows) == 1
        assert rows[0]["success"] == 0
        assert rows[0]["error_type"] == "MCPError"

    def test_a_tool_body_failure_is_detected_via_the_result_shape_not_an_exception(
        self, tracker
    ):
        """The finding this middleware's docstring documents: a domain exception
        from a tool body never reaches this middleware as a raised exception —
        `_handle_call_tool` already converted it to `CallToolResult(is_error=True)`
        by the time `call_next` returns. `error_type` is therefore the fixed
        `"ToolError"` marker, never a guessed class name."""
        mw = self._middleware(tracker)
        call_next = _ToolFailureCallNext()

        result = asyncio.run(mw(_ctx("update", {"finding_id": "CB-999"}), call_next))
        assert result.is_error is True, "the result must pass through unchanged"

        rows = _rows(tracker)
        assert len(rows) == 1
        assert rows[0]["success"] == 0
        assert rows[0]["error_type"] == "ToolError"

    def test_a_recording_failure_does_not_break_the_call_and_is_announced_on_stderr(
        self, tracker, monkeypatch, capsys
    ):
        """The discriminating test for rule 1: break the WRITE, not the tool call,
        and confirm the call still succeeds while stderr carries a line (rule 2)."""
        from codebugs import usage as usage_module

        def _boom(*a, **k):
            raise RuntimeError("disk is on fire")

        monkeypatch.setattr(usage_module, "record_call", _boom)

        mw = self._middleware(tracker)
        call_next = _Recorder()
        result = asyncio.run(mw(_ctx("stats", {}), call_next))

        assert result == {"ok": True}, "the tool call must succeed despite the write failure"
        assert call_next.calls == 1
        err = capsys.readouterr().err
        assert "stats" in err
        assert "RuntimeError" in err

    def test_error_type_never_carries_the_exception_message(self, tracker):
        """A caller-supplied value can appear in an exception's message (a bad
        category name, a finding id); only the class name is ever stored."""
        mw = self._middleware(tracker)
        call_next = _RaisingCallNext(ValueError("category 'sekret-internal-plan' is unknown"))

        with pytest.raises(ValueError):
            asyncio.run(mw(_ctx("add", {}), call_next))

        rows = _rows(tracker)
        assert rows[0]["error_type"] == "ValueError"
        assert "sekret" not in rows[0]["error_type"]

    def test_non_tools_call_methods_are_left_alone(self, tracker):
        mw = self._middleware(tracker)
        call_next = _Recorder()
        asyncio.run(mw(_ctx("stats", {}, method="tools/list"), call_next))
        assert _rows(tracker) == []


class TestUsageAndStrictArgumentsComposition:
    """The brief's §4 composition requirement: register both middlewares
    together and observe what an argument-name refusal does to the counter,
    rather than trusting each middleware's own isolated test to say so.
    """

    @staticmethod
    def _compose(chain, inner):
        call = inner
        for mw in reversed(chain):

            def _wrap(ctx, mw=mw, nxt=call):
                return mw(ctx, nxt)

            call = _wrap
        return call

    def test_an_argument_name_refusal_is_not_counted(self, tracker):
        """DECISION (see `install_usage_tracking`'s docstring for the reasoning):
        usage tracking is registered INNER of strict-argument checking, so a
        call refused before it ever reaches a tool body is not recorded here —
        `tool_calls` describes what TOOLS do, not client-side spelling mistakes.
        """
        mcp, _ = _server_with_middleware(tracker)
        server.install_usage_tracking(mcp, tracker)
        chain = mcp.middleware[-2:]

        composed = self._compose(chain, _Recorder())

        with pytest.raises(MCPError):
            asyncio.run(composed(_ctx("update", {"finding_id": "CB-1", "note": "TEXT"})))

        assert _rows(tracker) == []

    def test_a_call_with_declared_arguments_is_counted(self, tracker):
        """The composition's other half: a call that clears strict-argument
        checking still reaches usage tracking and is recorded."""
        mcp, _ = _server_with_middleware(tracker)
        server.install_usage_tracking(mcp, tracker)
        chain = mcp.middleware[-2:]

        composed = self._compose(chain, _Recorder())
        asyncio.run(composed(_ctx("update", {"finding_id": "CB-1", "append_note": "T"})))

        rows = _rows(tracker)
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "update"
        assert rows[0]["success"] == 1


class TestRecordDoesNotWaitOutTheSharedBusyTimeout:
    """CB-192, the remainder left after CB-195 fixed `ensure_schema`'s own seed
    write. `_record` opens a connection through the SAME `conn_factory` every
    tool uses (rule 3 of `install_usage_tracking`'s docstring — never a second,
    independently-resolved connection) and that factory's `db.connect()` sets
    `busy_timeout=5000`. `usage.record_call`'s own INSERT is a legitimate write
    with nothing to skip, so it still takes the write lock — and, under
    CB-195's fix, it is now the LAST write left on this path that can contend
    with a foreign writer for the shared 5-second timeout, holding up the
    client's own tool-call response for it.

    The fix shortens `busy_timeout` on the counter's OWN connection only,
    right in `server._record`, after the connection is opened and before the
    write — it never touches `db.connect()` or any other connection's
    setting, since `PRAGMA busy_timeout` is per-connection.

    Same discriminator as `TestConnectDoesNotWaitOnUnconditionalSchemaSeedWrite`
    in `test_db_infra.py`: wall-clock time. Before the fix, `_record` blocks for
    roughly as long as the foreign writer holds the lock (bounded here, well
    under the shared 5000ms, so this is a SLOW pass rather than a failure —
    the outright "database is locked" shape at longer holds is what the unit's
    brief measured separately). After the fix it gives up in ~50ms and drops
    the row, which `_record`'s existing rule 2 (no silent swallow) reports on
    stderr exactly as it already does for any other recording failure.
    """

    HOLD_SECONDS = 0.3
    FAST_THRESHOLD_SECONDS = 0.15

    def _hold_write_lock(self, db_path, *, ready: threading.Event):
        conn = sqlite3.connect(str(db_path))
        conn.execute("BEGIN IMMEDIATE")
        ready.set()
        time.sleep(self.HOLD_SECONDS)
        conn.execute("ROLLBACK")
        conn.close()

    def test_record_returns_fast_while_a_foreign_writer_holds_the_lock(
        self, tracker, capsys
    ):
        with tracker() as conn:
            root = db.connection_root(conn)
        assert root is not None
        db_path = os.path.join(root, ".codebugs", db.DB_FILE)

        ready = threading.Event()
        holder = threading.Thread(
            target=self._hold_write_lock, args=(db_path,), kwargs={"ready": ready}
        )
        holder.start()
        try:
            assert ready.wait(timeout=5.0), "the foreign writer never acquired its lock"

            start = time.perf_counter()
            server._record(
                tracker,
                tool_name="stats",
                success=True,
                error_type=None,
                duration_ms=1.0,
            )
            elapsed = time.perf_counter() - start
        finally:
            holder.join(timeout=5.0)
            assert not holder.is_alive()

        assert elapsed < self.FAST_THRESHOLD_SECONDS, (
            f"_record() took {elapsed:.3f}s while a foreign writer held the lock for "
            f"{self.HOLD_SECONDS:.3f}s — the usage counter must not delay the client's "
            "response by the shared 5000ms busy_timeout (CB-192): give its own "
            "connection a short one instead"
        )
        # Rule 2 (no silent swallow) must still hold when the short timeout drops
        # the row: `_record` already prints to stderr on any recording failure.
        if _rows(tracker) == []:
            assert "stats" in capsys.readouterr().err


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

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_an_unreachable_tracker_is_not_announced_as_a_future_one(
        self, tmp_path, monkeypatch, capsys
    ):
        """CB-203, the second consumer — and the one where it costs more.

        This line is read out of a log hours later, by someone who cannot go and
        look. Under truthiness the tri-state `exists` fell into the `is False`
        branch, so a tracker sitting right there behind a permission wall was
        announced as one the first write would happily create. The wall is the
        `.codebugs/` execute bit here: the directory is still readable and
        writable, so nothing else about the binding looks wrong.

        Both halves are asserted. The promise must be absent — a stale one is
        worse than silence — and the honest line must be present, because
        silence would leave a reader with a binding that looks perfectly fine.
        """
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".codebugs").chmod(0o666)
        try:
            server._preflight()  # warn-only, must not raise
        finally:
            (tmp_path / ".codebugs").chmod(0o755)
        err = capsys.readouterr().err
        assert "the first write will create" not in err
        assert "could not confirm a tracker" in err

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


class TestPreflightWritability:
    """CB-100: this is the moment that matters MOST, per the card's own design.

    `_conn` connects lazily per tool call, so before this an unwritable
    tracker gave a silent, healthy-looking startup and then failed every call
    forever — CB-11's exact failure mode, arriving through a new door. The
    preflight is the one moment that can name it, and it must stay warn-only:
    see the other TestPreflight class for that half of the contract, unchanged
    here.
    """

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_warns_when_the_file_is_unwritable(self, tmp_path, monkeypatch, capsys):
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        findings_path = tmp_path / ".codebugs" / "findings.db"
        findings_path.chmod(0o000)
        try:
            server._preflight()  # must not raise — warn-only
        finally:
            findings_path.chmod(0o644)
        assert "may not be writable" in capsys.readouterr().err

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_warns_when_the_directory_is_unwritable(self, tmp_path, monkeypatch, capsys):
        """The state that decides the mechanism — see
        TestWritabilityProbe in test_db_infra.py for the file-vs-directory
        measurement this pins.
        """
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        codebugs_dir = tmp_path / ".codebugs"
        codebugs_dir.chmod(0o555)
        try:
            server._preflight()  # must not raise — warn-only
        finally:
            codebugs_dir.chmod(0o755)
        assert "may not be writable" in capsys.readouterr().err

    def test_silent_on_a_nonempty_writable_tracker(self, tmp_path, monkeypatch, capsys):
        """Half the oracle, and the one most likely to pass vacuously: a
        healthy, NONEMPTY tracker (CB-100 §7) must stay exactly as silent as
        it is today.
        """
        db.init_project(str(tmp_path))
        conn = db.connect(str(tmp_path))
        findings.add_finding(
            conn, severity="low", category="x", file="f.py", description="d", new_category=True
        )
        conn.close()
        monkeypatch.chdir(tmp_path)
        server._preflight()
        assert capsys.readouterr().err == ""

    def test_the_no_database_yet_line_does_not_grow_a_second_writability_line(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        monkeypatch.chdir(repo)
        server._preflight()
        err = capsys.readouterr().err
        assert err.count("does not exist yet") == 1
        assert "may not be writable" not in err


class TestPreflightSpeaksAboutTheRouteItTook:
    """CB-218/CB-219 at the SECOND consumer, where both cost more than in the CLI.

    Nobody watches a server start. A wrong binding here is read out of a log
    hours later — if anything was written at all — while every tool call in
    between has been quietly reading and WRITING a stranger's tracker. One
    resolver, two consumers, so the same truth has to reach both through each
    one's own channel; asserting it in the CLI alone would leave exactly the
    half CB-11 was filed about.
    """

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_a_binding_reached_past_a_wall_is_announced(self, tmp_path, monkeypatch, capsys):
        stranger = tmp_path / "P"
        project = stranger / "A"
        deep = project / "b" / "c"
        deep.mkdir(parents=True)
        db.init_project(str(project))
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        project.chmod(0o666)
        try:
            server._preflight()  # must not raise — warn-only, like every line here
        finally:
            project.chmod(0o755)
        err = capsys.readouterr().err
        assert "could not be examined" in err
        assert str(project / ".codebugs") in err
        assert "is the wrong one" in err, "the reader must learn what the doubt is ABOUT"

    def test_silent_on_an_ordinary_discovered_binding(self, tmp_path, monkeypatch, capsys):
        """The other half — "one line per project per startup is noise" still holds."""
        db.init_project(str(tmp_path))
        conn = db.connect(str(tmp_path))
        conn.close()
        monkeypatch.chdir(tmp_path)
        server._preflight()
        assert capsys.readouterr().err == ""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_the_first_write_is_not_promised_over_a_directory_that_refuses(
        self, tmp_path, monkeypatch, capsys
    ):
        """CB-219 here: this line promises a healthy FUTURE to a reader who cannot
        go and look, so it must not outlive the check that backs it.
        """
        (tmp_path / ".codebugs").mkdir()
        (tmp_path / ".codebugs").chmod(0o555)
        monkeypatch.chdir(tmp_path)
        try:
            server._preflight()
        finally:
            (tmp_path / ".codebugs").chmod(0o755)
        err = capsys.readouterr().err
        assert err.count("does not exist yet") == 1, "the fact survives, exactly once"
        assert "the first write will create" not in err
        assert "may not be writable" in err


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
        and calls `server.run()`), so the wiring is read.

        T-75 split the construction out of `main()` into `_build_server` (so a
        test can build the real server object without entering the blocking
        `server.run()` loop) — so this is now a TWO-step structural check:
        `main()` actually calls `_build_server`, and `_build_server` is where
        the registrar wiring this test exists to pin now lives.
        """
        main_src = inspect.getsource(server.main)
        assert "_build_server(args.mode)" in main_src, main_src

        build_src = inspect.getsource(server._build_server)
        assert "_NormalizedDescriptions(server_obj)" in build_src, build_src
        assert "provider.register_fn(registrar, conn_factory)" in build_src, build_src

    def test_the_normalizer_has_exactly_one_definition(self):
        """The gate and the server must not be able to disagree about what
        'normalized' means. `tests/_mcp_schema` imports this one rather than
        carrying a copy, which is how CB-70's helper started.

        `normalize_description` is checked as well as `dedent_docstring`, because
        since CB-156 it is the WHOLE composition and the dedent is only its first
        step: importing the same dedent while composing a different amount of
        normalization would put the gate and the server back into disagreement
        with both halves of this assertion still green."""
        from tests import _mcp_schema

        assert _mcp_schema.dedent_docstring is server.dedent_docstring
        assert _mcp_schema.normalize_description is server.normalize_description

    def test_the_adapter_emits_the_whole_composition_not_just_the_dedent(self):
        """The seam-level mutant this class exists to catch (CB-156).

        A mutant that reverts the adapter alone to `dedent_docstring` sends every
        real client back to receiving a run-on paragraph. Since CB-164 the wire
        golden is generated THROUGH this adapter, so that mutant now turns
        `tests/test_boundary.py::TestMcpWireSchema::test_schema_matches_golden`
        red as well — measured by running it, 74 of the 83 snapshotted tools
        drift and every one of them differs on `description`.

        So this is no longer the only thing standing between that mutant and a
        green suite, and this docstring used to claim it was ("would leave the
        golden gate perfectly green" — CB-178). It is kept for what the golden
        gate cannot say: a snapshot comparison fails as "the snapshot moved,
        regenerate if intentional", which routes the reader towards regenerating,
        while this asserts the adapter's own output against `normalize_description`
        and names the seam that broke."""
        srv = MCPServer("composed")
        self._register(server._NormalizedDescriptions(srv), TestMarkdownSections.GOOGLE)
        emitted = asyncio.run(srv.list_tools())[0].description
        assert emitted == server.normalize_description(TestMarkdownSections.GOOGLE)
        assert "\n- severity: " in emitted, emitted


class TestMarkdownSections:
    """CB-156: a Google-style section reaches the client as a Markdown list.

    THE MECHANISM, because two mechanisms share one symptom and only one of them is
    this. `Args:` sits at column 0 and its argument lines are indented 4 with NO
    blank line between. CommonMark reads `Args:` as opening a PARAGRAPH, and an
    indented code block CANNOT interrupt a paragraph — so each argument line is a
    LAZY CONTINUATION: its indentation is stripped, its softbreak renders as a
    space, and the arguments fuse into one line with the boundaries gone. This is
    NOT CB-73's indented code block: that needs a preceding BLANK line, which
    occurs in 0 of the 83 wire descriptions.

    THE SCOPE IS DELIBERATELY MODEST and is stated the same way in the CHANGELOG: a
    client configured with GFM hard line breaks (`breaks: true`) shows the lines
    separately and never sees the defect. So the claim is not "broken for everyone"
    but "correct only under a particular setting of somebody else's renderer" — a
    real Markdown list is correct under BOTH settings.
    """

    GOOGLE = (
        "Add a finding.\n"
        "\n"
        "Args:\n"
        "    severity: critical, high, medium, or low\n"
        "    category: Finding category. Spelling is normalized\n"
        "        (casefold, hyphen -> underscore) before it is stored.\n"
        "    file: File path relative to project root\n"
        "\n"
        "Returns:\n"
        "    finding_id: the id that was written\n"
    )

    def test_arguments_become_list_items_rather_than_one_paragraph(self):
        out = server.markdown_sections(self.GOOGLE)
        assert "Args:\n\n- severity: critical, high, medium, or low\n" in out, out
        assert "\n- file: File path relative to project root\n" in out, out
        assert "\n    severity:" not in out, out

    def test_a_wrapped_argument_folds_into_the_SAME_list_item(self):
        """The unit's main mechanical difficulty: 28 of the 83 wire descriptions
        carry an argument whose text runs onto a further, more deeply indented
        line. It must join its own bullet — not become a second bullet, and not
        be dropped."""
        out = server.markdown_sections(self.GOOGLE)
        item = [ln for ln in out.split("\n") if ln.startswith("- category:")]
        assert len(item) == 1, out
        assert item[0] == (
            "- category: Finding category. Spelling is normalized "
            "(casefold, hyphen -> underscore) before it is stored."
        ), item
        assert "(casefold" not in out.replace(item[0], ""), out

    def test_returns_is_converted_too_not_only_args(self):
        """The card named `Args:` alone; the surface carries `Returns:` as well, and
        fixing only the named one would be this repository's eighth rule-as-an-
        enumeration. The header regex matches any `Word:` line, so `Raises:` and
        `Yields:` are covered by shape rather than by being listed."""
        out = server.markdown_sections(self.GOOGLE)
        assert "Returns:\n\n- finding_id: the id that was written" in out, out

    def test_a_prose_section_body_is_left_byte_identical(self):
        """`codesweep_add`'s `Returns:` is one prose line, not `name: value` items.
        Bulleting it would invent a list; collapsing prose into a paragraph is
        correct, so the section is not touched at all."""
        prose = "Add items.\n\nReturns:\n    {sweep_id, added, duplicates_skipped}\n"
        assert server.markdown_sections(prose) == prose

    def test_a_base_indent_line_that_is_not_an_item_continues_the_item_above(self):
        """Measured on the real surface: `claims_claim` and `claims_release` each
        carry a `Returns:` whose `outcome: …` item is followed by further sentences
        at the SAME indent. The naive rule 'base indent means a new item' split one
        sentence across two bullets."""
        doc = (
            "Claim.\n\nReturns:\n"
            "    outcome: claimed | already_mine | held_by_other.\n"
            "    On held_by_other the holder fields name the INCUMBENT.\n"
        )
        items = [ln for ln in server.markdown_sections(doc).split("\n") if ln.startswith("- ")]
        assert items == [
            "- outcome: claimed | already_mine | held_by_other. "
            "On held_by_other the holder fields name the INCUMBENT."
        ], items

    def test_an_argument_description_containing_a_colon_is_not_re_split(self):
        """Nothing parses the item into name and description — an argument line is
        prefixed with a marker and otherwise left alone — so a colon anywhere in
        the text cannot mislead it."""
        doc = "T.\n\nArgs:\n    ref: a git ref, e.g. refs/heads/main: the default\n"
        out = server.markdown_sections(doc)
        assert "- ref: a git ref, e.g. refs/heads/main: the default" in out, out

    def test_normalization_is_idempotent(self):
        """By construction rather than by luck: what it emits sits at column 0, and
        a section is only recognised when its body is INDENTED, so a second pass
        sees nothing to convert."""
        once = server.markdown_sections(self.GOOGLE)
        assert server.markdown_sections(once) == once
        assert server.normalize_description(once) == once

    def test_prose_that_merely_ends_in_a_colon_is_not_a_section(self):
        """`reqs_import`, `reqs_verify` and `staleness_check` open blocks with a
        sentence ending in a colon. Those are prose lead-ins whose bodies sit at
        column 0, but NOT ALL of them are already followed by column-0 bullets:
        `reqs_verify` and `staleness_check` are, `reqs_import` is not. Where a
        description already does it, that is independent evidence that a list
        is the right emission form, since a list, unlike an indented code
        block, may interrupt a paragraph."""
        doc = "T.\n\nRuns automated checks:\n- tests: do the files exist?\n"
        assert server.markdown_sections(doc) == doc

    def test_no_word_of_the_description_is_changed(self):
        """The unit is markup-only: not one new word about any tool's behaviour."""
        before = self.GOOGLE.split()
        after = server.markdown_sections(self.GOOGLE).split()
        assert [w for w in after if w != "-"] == before


class TestAttentionOverTheWire:
    """BT-5 section H: the ONLY real gate on the response shape.

    The wire golden cannot gate it — no `outputSchema` is snapshotted and the
    live schema carries `additionalProperties: True`, so an extension there
    would be a gate that cannot fire, this repo's named failure shape. The key's
    presence is therefore asserted against real `CallToolResult` objects here.

    Both of `batch_add`'s wire forms are covered because both exist: the
    structured form wraps the list in `{"result": [...]}`, while `content` is one
    `TextContent` per member. That the part count tracks the member count is
    MEASURED here (two members, two parts), not assumed.

    The attribute is `structured_content` (snake_case) in this SDK version;
    `structuredContent` raises `AttributeError`. `async def` would be collected
    and never awaited, so every call goes through `asyncio.run`.
    """

    ESCALATED = {"signal": "severity_escalated", "from": "low", "to": "critical"}
    DESC = "admin route skips the token check"

    def _call(self, mcp, name, arguments):
        async def go():
            return await mcp.call_tool(name, arguments)

        return asyncio.run(go())

    def _member(self, severity, description):
        return {
            "severity": severity,
            "category": "bug",
            "file": "a.py",
            "description": description,
        }

    def test_add_carries_attention_in_both_wire_forms(self, tracker):
        mcp, _ = _server_with_middleware(tracker)
        res = self._call(mcp, "add", {**self._member("low", self.DESC), "new_category": True})

        assert res.structured_content["attention"] == []
        assert len(res.content) == 1
        assert json.loads(res.content[0].text)["attention"] == []

    def test_batch_add_carries_attention_in_both_wire_forms(self, tracker):
        mcp, _ = _server_with_middleware(tracker)
        res = self._call(
            mcp,
            "batch_add",
            {
                "findings": [
                    self._member("low", "first defect text"),
                    self._member("low", "second defect text"),
                ],
                "new_category": True,
            },
        )

        members = res.structured_content["result"]
        assert len(members) == 2
        assert len(res.content) == 2, "one TextContent part per member — measured, not assumed"
        for i in range(2):
            assert members[i]["attention"] == []
            assert json.loads(res.content[i].text)["attention"] == []

    def test_an_escalation_is_visible_over_the_wire(self, tracker):
        """End to end: filed `low` through `add`, re-observed `critical` through
        `batch_add`, and the escalation reaches the client on both forms."""
        mcp, _ = _server_with_middleware(tracker)
        self._call(mcp, "add", {**self._member("low", self.DESC), "new_category": True})

        res = self._call(
            mcp, "batch_add", {"findings": [self._member("critical", self.DESC)]}
        )

        member = res.structured_content["result"][0]
        assert member["dedup_action"] == "bumped", "precondition: this must be a dedup bump"
        assert member["attention"] == [self.ESCALATED]
        assert json.loads(res.content[0].text)["attention"] == [self.ESCALATED]

    def test_a_category_divergence_is_visible_over_the_wire(self, tracker):
        """T-15's signal reaches the client on both forms too.

        A caller-supplied fingerprint is what makes two DIFFERENT categories
        match at all: on the derived path the category is a fingerprint input, so
        a different category is a different hash. No `new_category` is needed on
        the second call — the CB-60 mint gate runs on the insert continuation
        only, and a bump returns before it.
        """
        mcp, _ = _server_with_middleware(tracker)
        self._call(
            mcp,
            "add",
            {
                **self._member("low", "the first observation text"),
                "category": "alpha_svc",
                "fingerprint": "svc:login:timeout",
                "new_category": True,
            },
        )

        res = self._call(
            mcp,
            "add",
            {
                **self._member("low", "an entirely different observation text"),
                "category": "Beta-Svc",
                "fingerprint": "svc:login:timeout",
            },
        )

        diverged = {"signal": "category_divergence", "observed": "beta_svc", "stored": "alpha_svc"}
        assert res.structured_content["dedup_action"] == "bumped"
        assert res.structured_content["attention"] == [diverged]
        assert json.loads(res.content[0].text)["attention"] == [diverged]


class TestStrippedMetaKeysOverTheWire:
    """CB-160: `stripped_meta_keys` follows the `attention` discipline (BT-5) —
    present UNCONDITIONALLY on every `add`/`batch_add` response — but until
    this class nothing exercised it at the WIRE. The golden cannot gate a
    response-shape key for the same reason `TestAttentionOverTheWire` names:
    no `outputSchema` is snapshotted and the live schema carries
    `additionalProperties: True`. Measured: dropping the key in the `add`/
    `batch_add` MCP wrapper (never touching the domain layer, where ~14
    existing tests would catch it) left the full suite green before this
    class existed.
    """

    def _call(self, mcp, name, arguments):
        async def go():
            return await mcp.call_tool(name, arguments)

        return asyncio.run(go())

    def _member(self, description, meta=None):
        member = {
            "severity": "low",
            "category": "bug",
            "file": "a.py",
            "description": description,
        }
        if meta is not None:
            member["meta"] = meta
        return member

    def test_add_carries_stripped_meta_keys_in_both_wire_forms(self, tracker):
        mcp, _ = _server_with_middleware(tracker)
        res = self._call(mcp, "add", {**self._member("ordinary add"), "new_category": True})

        assert res.structured_content["stripped_meta_keys"] == []
        assert json.loads(res.content[0].text)["stripped_meta_keys"] == []

    def test_add_reports_a_stripped_key_in_both_wire_forms(self, tracker):
        mcp, _ = _server_with_middleware(tracker)
        res = self._call(
            mcp,
            "add",
            {
                **self._member("meta carries a reserved key", meta={"occurrences": 1}),
                "new_category": True,
            },
        )

        assert res.structured_content["stripped_meta_keys"] == ["occurrences"]
        assert json.loads(res.content[0].text)["stripped_meta_keys"] == ["occurrences"]
        assert "occurrences" not in res.structured_content["meta"]

    def test_batch_add_carries_stripped_meta_keys_in_both_wire_forms(self, tracker):
        mcp, _ = _server_with_middleware(tracker)
        res = self._call(
            mcp,
            "batch_add",
            {
                "findings": [
                    self._member("plain member, nothing to strip"),
                    self._member("member with a reserved key", meta={"occurrences": 1}),
                ],
                "new_category": True,
            },
        )

        members = res.structured_content["result"]
        assert members[0]["stripped_meta_keys"] == []
        assert members[1]["stripped_meta_keys"] == ["occurrences"]
        assert json.loads(res.content[0].text)["stripped_meta_keys"] == []
        assert json.loads(res.content[1].text)["stripped_meta_keys"] == ["occurrences"]


class TestServerInstructions:
    """The server tells a newly-connected agent the recommended working loop (T-75).

    `MCPServer.__init__` has a public `instructions` parameter (verified via
    `inspect.signature` before this was written — it sits next to `name`,
    `title`, `description`). The MCP wire golden (`tests/golden/mcp_schema.json`)
    is a snapshot of the 83 TOOL descriptions only; `instructions` is a property
    of the SERVER object and is deliberately outside it (see `tests/cli_surface.py`
    and `tests/_mcp_schema.py` for what each golden actually captures) — this test
    exercises the server's own `instructions` attribute directly instead.

    The real construction path is `server._build_server`, the same function
    `server.main()` calls — not a hand-rebuilt `MCPServer(...)` here, which
    would pass even if `main()`'s own call dropped `instructions=` (the exact
    mutant this unit's brief asks for, §4).
    """

    def test_built_server_carries_nonempty_instructions(self, tracker):
        built = server._build_server("findings", tracker)
        assert isinstance(built.instructions, str)
        assert built.instructions.strip()

    def test_instructions_name_the_load_bearing_loop(self):
        text = server.INSTRUCTIONS
        # Presence of the load-bearing names, not a verbatim paragraph (brief
        # §4): a wording edit must not turn this test red.
        for token in ("add", "attention", "dedup_action", "claims_claim", "reqs_add"):
            assert token in text, f"instructions text is missing {token!r}"


class TestInstructionsNamedToolsExist:
    """CB-189: every tool name `INSTRUCTIONS` recommends must exist in the live catalogue.

    `test_instructions_name_the_load_bearing_loop` above checks only that the
    load-bearing TOKENS are present in the text — it says nothing about
    whether the things being named still exist. Rename or remove one of the
    eight tools this text recommends (``add``, ``batch_add``,
    ``anchor_resolve``, ``update``, ``claims_claim``, ``claims_release``,
    ``reqs_add``, ``reqs_query``) and that test stays green while the server
    keeps telling every newly-connected agent to call something that no
    longer exists. This class closes the missing direction: it intersects
    every backtick-quoted token in ``INSTRUCTIONS`` against the LIVE tool
    catalogue (``tests._mcp_schema.collect_tool_schemas``, the same
    registration path ``server._build_server`` uses — never
    ``tests/golden/mcp_schema.json``, which is a snapshot someone updates by
    hand and would drift together with a renamed tool rather than catch it).

    THE NAMED TRAP: not every backtick-quoted token in this text is a tool
    name. Two are response KEYS the server returns from `add`/`batch_add`
    (`attention`, `dedup_action`), and one span is a call with an argument
    (`` `update(status="fixed")` ``). The naive "every backtick token must be
    a tool" reddens on today's healthy tree.

    CHOICE, stated once: a declared-exception list, not a syntactic
    predicate — because `attention` and `dedup_action` are, syntactically,
    ordinary bare snake_case identifiers exactly like `add` or
    `claims_claim`; nothing about their SHAPE distinguishes a response key
    from a tool name, so no syntactic rule could tell them apart without
    silently also excluding real tool names of the same shape. The pattern
    is `DECLARED_EXCEPTIONS` in `tests/test_no_network_capability.py`:
    followed here as `_DECLARED_NON_TOOL_TOKENS`, with the same
    self-deleting property — a row naming a token no longer in the text is
    itself a failing test (`test_declared_non_tool_tokens_are_not_stale`),
    so the table cannot quietly become a place real missing tools get
    parked.
    """

    # A row here says: this backtick-quoted token is deliberately NOT a tool
    # name, and here is why. Self-deleting by
    # `test_declared_non_tool_tokens_are_not_stale`: if the token stops
    # appearing in `INSTRUCTIONS`, the row must be deleted, not kept as a
    # standing exemption.
    _DECLARED_NON_TOOL_TOKENS: dict[str, str] = {
        "attention": (
            "a response KEY `add`/`batch_add` return (BT-5's structural "
            "attention block), not a tool the caller invokes."
        ),
        "dedup_action": (
            "a response KEY `add`/`batch_add` return (CB-43's dedup branch "
            "name), not a tool the caller invokes."
        ),
        "meta": (
            "the head of `meta.line` / `meta.lines` (CB-232), which are INPUT "
            "keys on the observation `add` accepts -- the two spellings the "
            "anchor grammar reads to capture a code span. Not a tool, and not "
            "a response key either: it is an argument the caller supplies."
        ),
    }

    @staticmethod
    def _backtick_head_identifiers(text: str) -> set[str]:
        """Every backtick span in `text`, reduced to its head identifier.

        A bare span (`` `add` ``) is already an identifier. A call span
        (`` `update(status="fixed")` ``) names a tool invoked with an
        argument; everything from the first non-identifier character on is
        the call's syntax, not part of the name, so only the identifier
        prefix is kept.
        """
        heads = set()
        for span in re.findall(r"`([^`]+)`", text):
            match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", span)
            if match:
                heads.add(match.group(0))
        return heads

    def test_every_named_tool_exists_in_the_live_catalogue(self):
        text = server.INSTRUCTIONS
        candidates = self._backtick_head_identifiers(text)
        assert candidates, "premise: INSTRUCTIONS carries at least one backtick-quoted token"
        live_names = {tool["name"] for tool in collect_tool_schemas()}
        assert live_names, "premise: the live catalogue is non-empty"

        missing = sorted(
            token
            for token in candidates
            if token not in self._DECLARED_NON_TOOL_TOKENS and token not in live_names
        )
        assert not missing, (
            f"INSTRUCTIONS names {missing} as if they were tools, but the live "
            "catalogue does not register them under that name -- a "
            "newly-connected agent following the recommended loop would call "
            "something that does not exist. Either the tool was renamed or "
            "removed (fix INSTRUCTIONS to match), or this token was never a "
            "tool name (add it to _DECLARED_NON_TOOL_TOKENS with a reason)."
        )

    def test_declared_non_tool_tokens_are_not_stale(self):
        """Self-deleting: a declared row must still name something in the text."""
        text = server.INSTRUCTIONS
        candidates = self._backtick_head_identifiers(text)
        stale = sorted(
            token for token in self._DECLARED_NON_TOOL_TOKENS if token not in candidates
        )
        assert not stale, (
            f"_DECLARED_NON_TOOL_TOKENS names {stale}, which no longer appears "
            "in INSTRUCTIONS -- delete the row rather than leaving a standing "
            "exemption behind."
        )

    def test_declared_non_tool_tokens_carry_a_reason(self):
        empty = [
            token
            for token, reason in self._DECLARED_NON_TOOL_TOKENS.items()
            if not reason.strip()
        ]
        assert not empty, (
            f"_DECLARED_NON_TOOL_TOKENS row(s) with no reason: {empty} -- a "
            "table that can grow silently is the hole this gate exists to close"
        )

    def test_backtick_extraction_reduces_a_call_span_to_its_head_identifier(self):
        # Pins the boundary from the OTHER side (brief §4's second mutant,
        # syntactic-predicate branch): a real tool name written as a call
        # must still be recognised as that tool, not lost as "not an
        # identifier" or kept whole as `update(status="fixed")`.
        heads = self._backtick_head_identifiers('Close it with `update(status="fixed")`.')
        assert heads == {"update"}
