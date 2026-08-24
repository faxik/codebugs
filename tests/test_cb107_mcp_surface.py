"""CB-107: four MCP-unreachable domain functions get a thin tool surface.

Covers exactly the four wrappers this unit adds: three read-only codemerge
getters (`codemerge_sessions` / `codemerge_status` / `codemerge_claims`) and
the milestones repair tool (`milestone_reconcile`). Every call goes through
the real `mcp.server.mcpserver.MCPServer` + `call_tool` pipeline (same
harness `test_merge.py::TestMcpAbandon` already uses), not a hand-rolled
stub, because CB-107's own `apply` requirement turned out to hinge on how
that real pipeline actually coerces argument types -- a fake stub would not
have shown it.

Two traps this file is built to catch, named in the brief for this unit:

  * A wrapper that calls the WRONG sibling domain function still returns
    "something", so every assertion here pins the top-level SHAPE (type +
    key set) of the response, not just that it is non-empty.
  * `get_status`/`get_sessions`/`get_claims` on an empty database can return
    an empty-but-valid structure, so every test seeds real state first and
    then asserts the tool actually reflects it.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from codebugs import db, findings, merge, milestones


def _call(mcp, name, **arguments):
    return asyncio.run(mcp.call_tool(name, arguments))


# ---------------------------------------------------------------------------
# codemerge_sessions / codemerge_status / codemerge_claims
# ---------------------------------------------------------------------------


class TestCodemergeIntrospectionTools:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:", check_same_thread=False)
        c.row_factory = sqlite3.Row
        merge.ensure_schema(c)
        yield c
        c.close()

    @staticmethod
    def _mcp(conn):
        @contextmanager
        def factory():
            # Deliberately does not close -- the in-memory DB IS this
            # connection, matching test_merge.py's own harness.
            yield conn

        mcp = MCPServer("cb107-merge-test")
        merge.register_tools(mcp, factory)
        return mcp

    def test_response_shapes_discriminate_a_wrong_function_call(self, conn):
        """Pin type + top-level key set for each of the three getters.

        A copy-paste mistake that made e.g. `codemerge_status` call
        `get_sessions` instead of `get_status` would still return a
        "working" response -- just the wrong shape. `get_sessions`/
        `get_claims` return LISTS of rows with disjoint key sets;
        `get_status` returns a single dashboard DICT. That is the
        discriminator the brief names, not "non-empty".
        """
        merge.start_session(conn, session_id="s1", branch="fix/a", base_commit="deadbeef")
        merge.add_claim(conn, "s1", "src/x.py")
        mcp = self._mcp(conn)

        sessions = _call(mcp, "codemerge_sessions").structured_content["result"]
        status = _call(mcp, "codemerge_status").structured_content
        claims = _call(mcp, "codemerge_claims", session_id="s1").structured_content["result"]

        assert isinstance(sessions, list) and sessions
        assert isinstance(claims, list) and claims
        assert isinstance(status, dict)

        assert set(sessions[0].keys()) >= {
            "session_id", "branch", "status", "claim_count",
        }
        assert set(status.keys()) == {
            "active_sessions", "merging_sessions", "done_sessions",
            "abandoned_sessions", "total_claims", "lock_holder",
        }
        assert set(claims[0].keys()) == {"session_id", "file_path", "claimed_at"}

        # And the CONTENT is this session's, not some other function's idea
        # of the world -- catches the same mutant from the value side.
        assert sessions[0]["session_id"] == "s1"
        assert status["active_sessions"] == 1
        assert claims[0]["file_path"] == "src/x.py"

    def test_codemerge_sessions_status_filter_is_forwarded(self, conn):
        merge.start_session(conn, session_id="s1", branch="fix/a", base_commit="x")
        merge.start_session(conn, session_id="s2", branch="fix/b", base_commit="y")
        merge.abandon_session(conn, "s2")
        mcp = self._mcp(conn)

        active = _call(mcp, "codemerge_sessions", status="active").structured_content["result"]
        assert [s["session_id"] for s in active] == ["s1"]

        abandoned = _call(mcp, "codemerge_sessions", status="abandoned").structured_content["result"]
        assert [s["session_id"] for s in abandoned] == ["s2"]

    def test_codemerge_status_reflects_a_held_lock(self, conn):
        """Empty-DB `codemerge_status` would return a fully-populated-looking
        dict with every count at zero -- indistinguishable from broken. This
        creates and merges a session so `lock_holder` has a real value to see."""
        merge.start_session(conn, session_id="s1", branch="fix/a", base_commit="H0")
        merge.merge(conn, "s1", expected_main_head="H0", current_main_head_fn=lambda: "H0")
        mcp = self._mcp(conn)

        status = _call(mcp, "codemerge_status").structured_content
        assert status["merging_sessions"] == 1
        assert status["lock_holder"] == "s1"

    def test_codemerge_claims_lists_only_the_named_session(self, conn):
        merge.start_session(conn, session_id="s1", branch="fix/a", base_commit="x")
        merge.start_session(conn, session_id="s2", branch="fix/b", base_commit="y")
        merge.add_claim(conn, "s1", "a.py")
        merge.add_claim(conn, "s2", "b.py")
        mcp = self._mcp(conn)

        claims = _call(mcp, "codemerge_claims", session_id="s1").structured_content["result"]
        assert [c["file_path"] for c in claims] == ["a.py"]

    def test_get_sessions_status_vocabulary_is_validated_on_query_too(self, conn):
        """Not a new test of behavior this unit adds -- a check requested by the
        brief (CLAUDE.md's own note that `merge.get_sessions` once validated
        `MERGE_STATUSES` write-side only). Confirms that gap is already closed:
        an unknown status is refused through the MCP path exactly as it is
        from the CLI, rather than silently returning the whole table.

        The domain `ValueError` propagates as a `ToolError` raised out of
        `call_tool` (CLAUDE.md's Error handling: MCP tools let exceptions
        propagate) -- it is not a `CallToolResult(isError=True)` a caller
        gets back to inspect, which is the same pipeline behavior
        `test_merge.py::TestMcpAbandon` already pins for `codemerge_abandon`."""
        mcp = self._mcp(conn)
        with pytest.raises(ToolError, match="Invalid status"):
            _call(mcp, "codemerge_sessions", status="not-a-real-status")


# ---------------------------------------------------------------------------
# milestone_reconcile
# ---------------------------------------------------------------------------


class _Closing:
    """`server._conn` in miniature: a connection usable as a context manager,
    matching `test_bench_surface.py`'s own harness for the same reason -- the
    tool body runs inside an anyio worker thread (`to_thread.run_sync`), so a
    fixture that opens ONE sqlite3 connection and hands it across that thread
    boundary hits sqlite3's `check_same_thread` guard. A fresh connection per
    call, over a real file-backed tracker, is what `server.py` itself does."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        return False


class TestMilestoneReconcileTool:
    @pytest.fixture
    def root(self, tmp_path):
        """A real tracker on disk, so each MCP call factory can open its OWN
        connection (the in-memory single-connection trick the merge tests use
        above does not work here: an in-memory DB is scoped to the connection
        that created it, so a fresh-connection-per-call factory would each see
        an empty database)."""
        db.init_project(str(tmp_path))
        return str(tmp_path)

    @staticmethod
    def _mcp(root):
        def factory():
            return _Closing(db.connect(project_dir=root))

        mcp = MCPServer("cb107-milestones-test")
        milestones.register_tools(mcp, factory)
        return mcp

    @staticmethod
    def _stale_row(root, ref="CB-1"):
        """Reproduce the CB-26 drift: a finding resolves, but the projected
        milestone item is forced back open the way a hook-bypassing writer
        would leave it -- exactly `test_milestones_reconcile.py`'s own helper.
        Uses its own connection to the same file-backed tracker, closed before
        returning, so it never overlaps with a connection the MCP factory
        opens next."""
        conn = db.connect(project_dir=root)
        try:
            findings.add_finding(
                conn, finding_id=ref, description="bug", severity="medium",
                category="bug", file="src/x.py",
            )
            findings.update_finding(conn, ref, status="fixed")
            conn.execute(
                "UPDATE milestone_items SET status='open', done_at=NULL WHERE item_ref=?",
                (ref,),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _item(root, ref):
        conn = db.connect(project_dir=root)
        try:
            row = conn.execute(
                "SELECT * FROM milestone_items WHERE item_ref = ? ORDER BY id DESC LIMIT 1",
                (ref,),
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def test_response_shape_is_the_reconcile_report(self, root):
        """Discriminates a wrong-function call the same way as the merge
        getters above: `reconcile_all`'s key set is distinct from anything
        else registered in this module."""
        self._stale_row(root, "CB-1")
        mcp = self._mcp(root)
        result = _call(mcp, "milestone_reconcile").structured_content
        assert set(result.keys()) == {"applied", "candidates", "items"}

    def test_default_call_does_not_write_on_a_nonempty_candidate_set(self, root):
        """The brief's §2.1 requirement, and its own named trap: this MUST run
        against a real candidate, or the assertion passes on a tool that is
        wired to do nothing at all."""
        self._stale_row(root, "CB-1")
        before = self._item(root, "CB-1")
        assert before["status"] == "open"  # the drift this fixture manufactures

        mcp = self._mcp(root)
        result = _call(mcp, "milestone_reconcile").structured_content

        assert result["candidates"] == 1  # proves the candidate set was non-empty
        assert result["applied"] is False
        after = self._item(root, "CB-1")
        assert after == before  # literally nothing changed

    def test_explicit_apply_true_writes(self, root):
        self._stale_row(root, "CB-1")
        mcp = self._mcp(root)
        result = _call(mcp, "milestone_reconcile", apply=True).structured_content
        assert result["applied"] is True
        assert result["candidates"] == 1
        assert self._item(root, "CB-1")["status"] == "done"

    @pytest.mark.parametrize("bad_apply", ["false", "0", 0, 1, "", 1.0, 0.0])
    def test_non_bool_apply_is_refused_and_writes_nothing(self, root, bad_apply):
        """CB-82's class of bug: Python's bool("false") and bool("0") are both
        True, and an MCP client sends JSON, not Python literals. Every one of
        these values must be refused rather than coerced -- in
        particular `"false"` must NOT be silently accepted as a truthy
        dry-run confirmation, and `1`/`"1"`-shaped values must NOT be
        silently accepted as `apply=True`.

        CB-151 added `1.0` and `0.0` to this list. They are the exact hole the
        old `bool | int | str | None` union + isinstance check could not see:
        pydantic's lax mode coerces a JSON float into a real `bool` BEFORE
        isinstance ever runs, so `apply=1.0` used to pass this gate and
        perform the write -- reproduced against the pre-fix tree before this
        unit touched anything (see the L3 brief for CB-151). The refusal is
        now raised by pydantic itself (`Annotated[bool, Field(strict=True)]`)
        at the wire boundary, before the tool body runs at all, so the
        message is pydantic's own rather than the removed hand-rolled
        `ValueError` -- CB-151's own instruction is to fix the TEST here, not
        weaken the mechanism, since the old hand-rolled check is now
        unreachable dead code and was deleted along with the union
        annotation that made it necessary."""
        self._stale_row(root, "CB-1")
        before = self._item(root, "CB-1")
        mcp = self._mcp(root)

        with pytest.raises(ToolError, match="Input should be a valid boolean"):
            _call(mcp, "milestone_reconcile", apply=bad_apply)

        after = self._item(root, "CB-1")
        assert after == before, f"apply={bad_apply!r} must not have written anything"

    def test_apply_none_is_also_refused(self, root):
        """An explicit JSON null is a distinct signal from 'omitted' (which
        keeps the True default False); it must not silently mean either."""
        self._stale_row(root, "CB-1")
        mcp = self._mcp(root)
        with pytest.raises(ToolError, match="Input should be a valid boolean"):
            _call(mcp, "milestone_reconcile", apply=None)
