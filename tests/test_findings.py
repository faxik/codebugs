"""Tests for the findings domain — CRUD, query, stats, migrations."""

import ast
import contextlib
import csv
import json
import os
import re
import pathlib
import sqlite3
import subprocess
import sys
import threading

import pytest

from codebugs import db, findings
from codebugs.types import (
    FINDING_STATUSES,
    FINDING_TERMINAL,
    FINDING_STATUS_ALIASES,
    SEVERITIES,
    resolve_finding_status,
)


class RecordingConnection(sqlite3.Connection):
    """Records SQL *templates*, as issued, before parameters are bound.

    ``set_trace_callback`` reports statements with parameters already expanded, so a
    guard built on it cannot tell a real ``meta`` assignment from the literal text
    ``meta =`` sitting inside a notes value — it yields both false passes (quoted
    identifiers) and false failures (a notes payload containing the token). Recording
    the template removes that whole class of error: the template contains `?`
    placeholders, so only genuine assignments are ever counted.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_sql: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.recorded_sql.append(sql)
        return super().execute(sql, *args, **kwargs)


@pytest.fixture
def tmp_project(tmp_path):
    """Provide a temporary project directory with an initialized tracker."""
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    """Provide a connected database."""
    c = db.connect(tmp_project)
    yield c
    c.close()


@pytest.fixture
def recording(tmp_project):
    """A connection that records SQL templates, on an already-initialized tracker.

    Module-level rather than per-class: the SET-clause structural guards for both
    `meta` (CB-16) and `severity` (CB-17) need it. The project's "no shared
    conftest.py" rule is about cross-*file* sharing, not cross-class sharing
    inside one file.
    """
    db.connect(tmp_project).close()  # apply every module's schema to the file
    path = os.path.join(tmp_project, ".codebugs", "findings.db")
    conn = sqlite3.connect(path, factory=RecordingConnection)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


class TestAddFinding:
    def test_add_basic(self, conn):
        result = findings.add_finding(
            conn,
            severity="high",
            category="n_plus_one",
            file="src/api.py",
            description="Query in loop", new_category=True,
        )
        assert result["id"] == "CB-1"
        assert result["severity"] == "high"
        assert result["category"] == "n_plus_one"
        assert result["file"] == "src/api.py"
        assert result["status"] == "open"
        assert result["source"] == "human"
        assert result["tags"] == []
        # The first add of a category in an empty tracker MINTS it (CB-60).
        # `loc` is the capture resolver's record and is ALWAYS present (BT-7 Р7:
        # a refusal is persisted as an object, never as an absent key) — it is
        # excluded rather than relaxing the equality, so this still pins that
        # nothing ELSE appears in meta.
        assert {k: v for k, v in result["meta"].items() if k != "loc"} == {
            "category_minted": True
        }

    def test_add_with_meta_and_tags(self, conn):
        result = findings.add_finding(
            conn,
            severity="medium",
            category="complexity",
            file="src/foo.py",
            description="CC too high",
            source="ruff",
            tags=["tech-debt", "refactor"],
            meta={"lines": "10-50", "rule_code": "C901"}, new_category=True,
        )
        assert result["source"] == "ruff"
        assert result["tags"] == ["tech-debt", "refactor"]
        assert result["meta"]["lines"] == "10-50"
        assert result["meta"]["rule_code"] == "C901"

    def test_add_auto_increments_id(self, conn):
        f1 = findings.add_finding(
            conn, severity="low", category="style", file="a.py", description="d1", new_category=True
        )
        f2 = findings.add_finding(
            conn, severity="low", category="style", file="b.py", description="d2", new_category=True
        )
        f3 = findings.add_finding(
            conn, severity="low", category="style", file="c.py", description="d3", new_category=True
        )
        assert f1["id"] == "CB-1"
        assert f2["id"] == "CB-2"
        assert f3["id"] == "CB-3"

    def test_add_custom_id(self, conn):
        result = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="x.py",
            description="desc",
            finding_id="CUSTOM-42",
        )
        assert result["id"] == "CUSTOM-42"

    def test_add_invalid_severity_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid severity"):
            findings.add_finding(
                conn,
                severity="extreme",
                category="bug",
                file="x.py",
                description="d", new_category=True,
            )

    def test_add_sets_timestamps(self, conn):
        result = findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="a.py",
            description="d", new_category=True,
        )
        assert result["created_at"].endswith("Z")
        assert result["updated_at"] == result["created_at"]


class TestBatchAdd:
    def test_batch_add_multiple(self, conn):
        items = [
            {"severity": "high", "category": "bug", "file": "a.py", "description": "d1"},
            {"severity": "medium", "category": "style", "file": "b.py", "description": "d2"},
            {"severity": "low", "category": "perf", "file": "c.py", "description": "d3"},
        ]
        results = findings.batch_add_findings(conn, items, new_category=True)
        assert len(results) == 3
        ids = {r["id"] for r in results}
        assert ids == {"CB-1", "CB-2", "CB-3"}

    def test_batch_add_invalid_severity_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid severity"):
            findings.batch_add_findings(
                conn,
                [
                    {"severity": "ultra", "category": "bug", "file": "a.py", "description": "d"},
                ], new_category=True,
            )

    def test_batch_add_with_source_and_meta(self, conn):
        items = [
            {
                "severity": "high",
                "category": "sec",
                "file": "auth.py",
                "description": "SQL injection",
                "source": "semgrep",
                "meta": {"cwe": "CWE-89"},
            },
        ]
        results = findings.batch_add_findings(conn, items, new_category=True)
        assert results[0]["source"] == "semgrep"
        assert results[0]["meta"]["cwe"] == "CWE-89"


class TestUpdateFinding:
    def test_update_status(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="fixed")
        assert result["status"] == "fixed"
        assert result["updated_at"] >= result["created_at"]

    def test_update_notes(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", notes="Fixed in PR #42")
        assert result["meta"]["notes"] == "Fixed in PR #42"

    def test_update_tags(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", tags=["urgent", "sprint-5"])
        assert result["tags"] == ["urgent", "sprint-5"]

    def test_update_meta(self, conn):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"lines": "10-20"}, new_category=True,
        )
        result = findings.update_finding(conn, "CB-1", meta_update={"fix_commit": "abc123"})
        assert result["meta"]["lines"] == "10-20"
        assert result["meta"]["fix_commit"] == "abc123"

    def test_update_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            findings.update_finding(conn, "CB-999", status="fixed")


class TestAppendNoteIsReachable:
    """CB-18 / CB-15(1): `append_note` existed in the domain layer but was not
    plumbed to either surface, so every agent-driven note edit was forced through
    the destructive whole-value replace."""

    def test_mcp_update_tool_declares_append_note(self):
        import asyncio
        from contextlib import contextmanager

        from mcp.server.mcpserver import MCPServer

        @contextmanager
        def _never_called():
            raise AssertionError("listing tools must not open a connection")
            yield  # pragma: no cover

        async def update_tool():
            mcp = MCPServer("codebugs")
            for provider in db.get_tool_providers(mode="findings"):
                provider.register_fn(mcp, _never_called)
            for tool in await mcp.list_tools():
                if tool.name == "update":
                    return tool
            raise AssertionError("no `update` tool registered")

        tool = asyncio.run(update_tool())
        assert "append_note" in tool.input_schema.get("properties", {})
        # The SDK carries per-argument prose in the tool's top-level description
        # (built from the docstring Args block), not in the property schemas.
        assert "REPLACES" in tool.description, (
            "the destructive option must not read like the safe one"
        )
        assert "append_note" in tool.description

    def test_cli_update_parser_accepts_append_note(self):
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        findings.register_cli(sub, {})

        args = parser.parse_args(["update", "CB-1", "--append-note", "another line"])
        assert args.append_note == "another line"

    def test_append_note_extends_rather_than_replaces(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        findings.update_finding(conn, "CB-1", notes="FIRST")
        result = findings.update_finding(conn, "CB-1", append_note="SECOND")
        assert result["meta"]["notes"] == "FIRST\nSECOND"


class TestUpdateMetaComposition:
    """CB-16: the meta-writing arguments must compose over one dict.

    Each branch used to rebuild meta from the pre-update row and append its own
    ``meta = ?``, so one UPDATE assigned meta several times and SQLite kept only
    the last — silently destroying the others.
    """

    @pytest.fixture
    def one(self, conn):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"lines": "1-2"}, new_category=True,
        )
        return conn

    def test_notes_and_meta_update_both_survive(self, one):
        result = findings.update_finding(
            one, "CB-1", notes="INVESTIGATION", meta_update={"fix_commit": "abc123"}
        )
        assert result["meta"]["notes"] == "INVESTIGATION"
        assert result["meta"]["fix_commit"] == "abc123"
        assert result["meta"]["lines"] == "1-2"

    def test_append_note_and_meta_update_both_survive(self, one):
        findings.update_finding(one, "CB-1", notes="FIRST")
        result = findings.update_finding(one, "CB-1", append_note="SECOND", meta_update={"k": "v"})
        assert result["meta"]["notes"] == "FIRST\nSECOND"
        assert result["meta"]["k"] == "v"

    def test_append_note_extends_the_replacement_not_the_prior(self, one):
        findings.update_finding(one, "CB-1", notes="OLD")
        result = findings.update_finding(one, "CB-1", notes="NEW", append_note="EXTRA")
        assert result["meta"]["notes"] == "NEW\nEXTRA"

    def test_all_three_compose_in_order(self, one):
        findings.update_finding(one, "CB-1", notes="PRIOR")
        result = findings.update_finding(
            one, "CB-1", notes="NEW", append_note="EXTRA", meta_update={"k": "v"}
        )
        assert result["meta"]["notes"] == "NEW\nEXTRA"
        assert result["meta"]["k"] == "v"

    def test_meta_update_notes_key_wins_because_it_merges_last(self, one):
        result = findings.update_finding(one, "CB-1", notes="LOSES", meta_update={"notes": "WINS"})
        assert result["meta"]["notes"] == "WINS"

    def test_empty_string_notes_is_a_real_write(self, one):
        findings.update_finding(one, "CB-1", notes="SOMETHING")
        result = findings.update_finding(one, "CB-1", notes="", meta_update={"k": "v"})
        assert result["meta"]["notes"] == ""
        assert result["meta"]["k"] == "v"

    def test_empty_meta_update_still_counts_as_a_write(self, recording):
        """``meta_update={}`` emits a real meta assignment, not a no-op.

        Asserted on the SQL template rather than on ``updated_at``: timestamps
        have one-second resolution, so a ``>=`` comparison passes even when the
        call degrades to a no-op, and proves nothing.
        """
        findings.add_finding(
            recording,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"lines": "1-2"}, new_category=True,
        )
        recording.recorded_sql.clear()

        result = findings.update_finding(recording, "CB-1", meta_update={})

        updates = [s for s in recording.recorded_sql if s.startswith("UPDATE findings")]
        assert len(updates) == 1, updates
        assert updates[0].count("meta = ?") == 1, updates[0]
        assert result["meta"]["lines"] == "1-2"

    def test_non_meta_update_still_writes_when_stored_meta_is_malformed(self, one):
        """A status-only update must not start depending on stored meta parsing.

        The column carries no ``json_valid`` constraint, so legacy rows may hold
        anything. Pre-existing behaviour on such a row: the UPDATE lands and only
        then does the return-value conversion raise. That is odd but out of scope
        here — this test exists to pin it, so building the new meta dict eagerly
        (which would abort before the SQL) is caught as the behaviour change it is.
        """
        one.execute("UPDATE findings SET meta = ? WHERE id = ?", ("{not json", "CB-1"))
        one.commit()

        with pytest.raises(json.JSONDecodeError):  # raised by the result conversion
            findings.update_finding(one, "CB-1", status="fixed")

        stored = one.execute("SELECT status FROM findings WHERE id = ?", ("CB-1",)).fetchone()
        assert stored["status"] == "fixed", "the status write must still have landed"

    def test_single_update_never_assigns_meta_twice(self, recording):
        """Structural guard: exactly one ``meta = ?`` per emitted UPDATE.

        Asserted against the SQL *template* rather than the executed statement.
        The notes payload deliberately contains the literal token ``meta =``: a
        guard reading trace-callback output would see that text inside the
        expanded JSON and fail on correct code.
        """
        findings.add_finding(
            recording,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"lines": "1-2"}, new_category=True,
        )
        recording.recorded_sql.clear()

        findings.update_finding(
            recording,
            "CB-1",
            status="fixed",
            notes="a value that itself contains meta = bait",
            append_note="A",
            meta_update={"k": "v"},
        )

        updates = [s for s in recording.recorded_sql if s.startswith("UPDATE findings")]
        assert len(updates) == 1, updates
        assert updates[0].count("meta = ?") == 1, updates[0]

    def test_update_status_in_progress(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="in_progress")
        assert result["status"] == "in_progress"

    def test_update_status_alias_done(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="done")
        assert result["status"] == "fixed"

    def test_update_status_alias_resolved(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="resolved")
        assert result["status"] == "fixed"

    def test_update_status_alias_implemented(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="implemented")
        assert result["status"] == "fixed"

    def test_update_status_alias_wontfix(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="wontfix")
        assert result["status"] == "wont_fix"

    def test_update_status_alias_invalid(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="invalid")
        assert result["status"] == "not_a_bug"

    def test_update_status_alias_active(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1", status="active")
        assert result["status"] == "in_progress"

    def test_update_invalid_status_raises(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        with pytest.raises(ValueError, match="Invalid finding status"):
            findings.update_finding(conn, "CB-1", status="deleted")

    def test_update_noop(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        result = findings.update_finding(conn, "CB-1")
        assert result["status"] == "open"


class PausingConnection(sqlite3.Connection):
    """Fires a one-shot hook immediately after an update's opening ``SELECT``.

    That instant is exactly the window CB-24 is about: the row has been read and
    the merged ``meta`` has not been written yet. Interleaving a competing writer
    there is what makes the lost update *deterministic* rather than a race the
    test would only sometimes lose — and a concurrency test that only sometimes
    fails against the broken code is a test that will be believed when it passes.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_select = None

    def execute(self, sql, *args, **kwargs):
        cur = super().execute(sql, *args, **kwargs)
        if self.after_select and sql.lstrip().startswith("SELECT * FROM findings WHERE id"):
            hook, self.after_select = self.after_select, None  # one-shot: the
            hook()  # returning SELECT at the end of the update must not re-fire
        return cur


class TestConcurrentMetaUpdatesDoNotLoseEachOther:
    """CB-24: the meta read-modify-write must be one transaction, not two steps.

    ``update_finding`` SELECTs the row, merges ``notes`` / ``append_note`` /
    ``meta_update`` in Python, then UPDATEs. Without a transaction spanning that
    pair, two writers that both read before either writes both report success and
    the later one erases the earlier one's merge. ``busy_timeout`` serializes the
    *writes*; it does nothing about the read that preceded them.

    This matters here specifically because the project exists to coordinate
    parallel agents, and ``append_note`` — the operation whose entire purpose is
    to be additive rather than destructive — is the one most likely to be issued
    concurrently. Losing an append is the harm CB-18 was filed to prevent,
    reached by a different route.
    """

    def _open(self, tmp_project):
        """A second connection to the same file, carrying ``db.connect``'s pragmas.

        ``busy_timeout`` is the load-bearing one: after the fix the losing writer
        must *wait* for the lock rather than fail, which is what converts the race
        into serialization.
        """
        path = os.path.join(tmp_project, ".codebugs", "findings.db")
        c = sqlite3.connect(path, factory=PausingConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def _interleave(self, tmp_project, a_call, b_call):
        """Drive two writers through each other's read-modify-write window.

        A pauses after its opening SELECT until B has also read. Before the fix B
        gets there, both merges are built from the same stale row, and whichever
        UPDATE lands second erases the other. After the fix A holds the write lock
        from before its own SELECT, so B blocks at BEGIN IMMEDIATE and never
        reaches its read — the wait times out, A commits, and B then re-reads a row
        that already carries A's write.

        Waiting on ``b_started`` before ``b_read`` is what keeps this honest. With
        only the 1.0s timeout as a guard, a worker thread not scheduled inside that
        second lets A write first — after which B reads A's committed row, merges
        cleanly, and the test PASSES against the unfixed code. ``b_started`` is set
        once B's connection is open, so the unguarded gap is B's entry into the
        call rather than thread startup plus a connection open.
        """
        a = self._open(tmp_project)
        a_read, b_started, b_read = (threading.Event() for _ in range(3))

        def competing_writer():
            a_read.wait(timeout=10)
            b = self._open(tmp_project)  # opened here: sqlite3 connections are
            b.after_select = b_read.set  # bound to their creating thread
            b_started.set()
            try:
                b_call(b)
            finally:
                b.close()

        a.after_select = lambda: (
            a_read.set(),
            b_started.wait(timeout=10),
            b_read.wait(timeout=1.0),
        )

        t = threading.Thread(target=competing_writer)
        t.start()
        try:
            a_call(a)
        finally:
            t.join(timeout=30)
            a.close()
        assert not t.is_alive()

    def test_a_competing_append_note_is_not_erased(self, conn, tmp_project):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)
        findings.update_finding(conn, "CB-1", notes="BASE")

        self._interleave(
            tmp_project,
            lambda a: findings.update_finding(a, "CB-1", append_note="FROM-A"),
            lambda b: findings.update_finding(b, "CB-1", append_note="FROM-B"),
        )

        notes = findings.get_finding(conn, "CB-1")["meta"]["notes"]
        assert "FROM-A" in notes, notes
        assert "FROM-B" in notes, notes

    def test_an_ambient_transaction_is_not_committed_by_the_nested_update(self, conn):
        """The other half of the fix: under an ambient transaction this frame must not commit.

        ``db.txn`` yields ``False`` when a transaction is already open, and the
        owning frame keeps full control. A restored ``conn.commit()`` inside
        ``update_finding`` would therefore commit *the caller's* work from inside a
        nested call — ``milestones.triage_dismiss`` writes ``milestone_items`` and
        its audit row before calling here, and would lose the ability to roll them
        back. Rolling the outer transaction back is the only way to observe that,
        so this test asserts on what survives the rollback.
        """
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)

        with pytest.raises(RuntimeError, match="caller aborts"):
            with db.txn(conn) as opened:
                assert opened, "the caller owns this transaction"
                conn.execute("UPDATE findings SET category = ? WHERE id = ?", ("outer", "CB-1"))
                findings.update_finding(conn, "CB-1", status="fixed", append_note="nested")
                raise RuntimeError("caller aborts after the nested update")

        row = findings.get_finding(conn, "CB-1")
        assert row["category"] == "bug", "the caller's own write must have rolled back"
        assert row["status"] == "open", "the nested update must roll back with its caller"
        assert "nested" not in json.dumps(row["meta"]), row["meta"]

    def test_a_competing_meta_update_key_is_not_erased(self, conn, tmp_project):
        """The same defect reached through ``meta_update`` rather than ``append_note``.

        Worth pinning separately: ``meta_update`` merges arbitrary keys, so a lost
        write here silently drops another agent's structured state (an assignee, a
        claim marker) rather than a line of prose.
        """
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d", new_category=True)

        self._interleave(
            tmp_project,
            lambda a: findings.update_finding(a, "CB-1", meta_update={"from_a": True}),
            lambda b: findings.update_finding(b, "CB-1", meta_update={"from_b": True}),
        )

        meta = findings.get_finding(conn, "CB-1")["meta"]
        assert meta.get("from_a") is True, meta
        assert meta.get("from_b") is True, meta


class TestRetriageSeverity:
    """CB-17: severity must be mutable, as ``priority`` already is on requirements.

    Severity is the tracker's ranking input but is assigned when the least is known
    about a finding. While it was write-once, a re-triage had to be carried as prose
    inside a note body — i.e. the structured field stayed wrong and the truth moved
    into free text, which is the state a tracker exists to prevent.
    """

    @pytest.fixture
    def one(self, conn):
        findings.add_finding(
            conn,
            severity="medium",
            category="perf",
            file="a.py",
            description="filed on a hunch",
            meta={"lines": "1-2"}, new_category=True,
        )
        return conn

    def test_the_escalation_is_durable(self, one, tmp_project):
        """Read back through a SECOND connection, so the commit is what is proved.

        Re-reading through the same connection would pass even with the commit
        removed, which makes the assertion about the statement rather than about
        durability.
        """
        findings.update_finding(one, "CB-1", severity="critical")
        one.close()

        reopened = db.connect(tmp_project)
        try:
            assert findings.get_finding(reopened, "CB-1")["severity"] == "critical"
        finally:
            reopened.close()

    def test_retriage_touches_only_the_named_row(self, one):
        """A missing or wrong WHERE clause would pass every single-row test above."""
        findings.add_finding(
            one, severity="low", category="perf", file="b.py", description="bystander", new_category=True
        )
        findings.update_finding(one, "CB-1", severity="critical")

        assert findings.get_finding(one, "CB-1")["severity"] == "critical"
        assert findings.get_finding(one, "CB-2")["severity"] == "low", (
            "the bystander must not have been re-triaged too"
        )

    def test_every_canonical_severity_is_reachable(self, one):
        """Covers escalation and downgrade both — the fixture is seeded `medium`."""
        for sev in SEVERITIES:
            assert findings.update_finding(one, "CB-1", severity=sev)["severity"] == sev

    @pytest.mark.parametrize("bad", ["urgent", "crit", "P0", ""])
    def test_invalid_severity_raises(self, one, bad):
        """`ValueError`, not `IntegrityError` — the Python check is the validator and
        the column CHECK is only a backstop.

        ``HIGH`` used to be in this list, pinning the case-strictness so that CB-19
        could not relax it silently. CB-19 has now relaxed it deliberately, so the
        payloads here are the ones that must STILL raise: an alias-shaped input
        (`crit`, `P0`) and a non-value. Severity normalizes case, never meaning."""
        with pytest.raises(ValueError, match="Invalid severity"):
            findings.update_finding(one, "CB-1", severity=bad)

    def test_invalid_severity_leaves_the_row_untouched(self, one):
        with pytest.raises(ValueError):
            findings.update_finding(one, "CB-1", severity="urgent", status="fixed")
        row = findings.get_finding(one, "CB-1")
        assert row["severity"] == "medium"
        assert row["status"] == "open", "validation must precede the write, not follow it"

    def test_mixed_case_is_normalized_not_stored_verbatim(self, one):
        """CB-19. Asserts the STORED value, not the return value — a fix that
        normalized only on the way out would leave a non-canonical row."""
        findings.update_finding(one, "CB-1", severity="HiGh")
        assert findings.get_finding(one, "CB-1")["severity"] == "high"

    def test_severity_and_status_compose_in_one_call(self, one):
        result = findings.update_finding(one, "CB-1", severity="high", status="in_progress")
        assert result["severity"] == "high"
        assert result["status"] == "in_progress"

    def test_severity_does_not_disturb_the_meta_arguments(self, one):
        """The CB-16 neighbours must survive a call that also re-triages."""
        findings.update_finding(one, "CB-1", notes="PRIOR")
        result = findings.update_finding(
            one, "CB-1", severity="critical", append_note="EXTRA", meta_update={"k": "v"}
        )
        assert result["severity"] == "critical"
        assert result["meta"]["notes"] == "PRIOR\nEXTRA"
        assert result["meta"]["k"] == "v"
        assert result["meta"]["lines"] == "1-2"

    def test_severity_only_update_fires_no_status_hook(self, one, monkeypatch):
        """Adding a column must not widen the status-hook condition."""
        fired = []
        monkeypatch.setattr(db, "run_status_change_hooks", lambda *a, **k: fired.append(a[1:]))

        findings.update_finding(one, "CB-1", severity="high")
        assert fired == [], "a severity-only update is not a status change"

        findings.update_finding(one, "CB-1", severity="low", status="fixed")
        assert fired == [("CB-1", "open", "fixed")]

    def test_single_update_assigns_severity_exactly_once(self, recording):
        """Structural guard, in the CB-16 shape: one ``severity = ?`` per UPDATE.

        Asserted against the SQL *template*. The notes payload deliberately carries
        the literal token ``severity =`` — a guard reading expanded statements would
        count that bait and fail on correct code.
        """
        findings.add_finding(
            recording,
            severity="medium",
            category="bug",
            file="a.py",
            description="d", new_category=True,
        )
        recording.recorded_sql.clear()

        findings.update_finding(
            recording,
            "CB-1",
            severity="high",
            status="fixed",
            notes="a value that itself contains severity = bait",
            meta_update={"k": "v"},
        )

        updates = [s for s in recording.recorded_sql if s.startswith("UPDATE findings")]
        assert len(updates) == 1, updates
        assert updates[0].count("severity = ?") == 1, updates[0]
        assert updates[0].count("meta = ?") == 1, updates[0]

    def test_retriage_moves_the_card_between_query_buckets(self, one):
        """The point of the fix: the queue stops lying to whoever reads it."""
        assert [f["id"] for f in findings.query_findings(one, severity="high")["findings"]] == []
        findings.update_finding(one, "CB-1", severity="high")
        assert [f["id"] for f in findings.query_findings(one, severity="high")["findings"]] == [
            "CB-1"
        ]
        assert findings.query_findings(one, severity="medium")["findings"] == []


class TestRetriageCliContract:
    """CB-17 over the real CLI. The domain tests cannot see a parser that never
    declares the flag, nor a handler that never forwards it."""

    @staticmethod
    def _run(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            cwd=project,
            capture_output=True,
            text=True,
        )

    @pytest.fixture
    def project(self, tmp_project, conn):
        findings.add_finding(conn, severity="medium", category="perf", file="a.py", description="d", new_category=True)
        conn.close()
        return tmp_project

    def test_long_flag_retriages(self, project):
        r = self._run(project, "update", "CB-1", "--severity", "high")
        assert r.returncode == 0, r.stderr
        assert "severity=high" in r.stdout, r.stdout

        shown = self._run(project, "query", "--severity", "high")
        assert "CB-1" in shown.stdout

    def test_short_flag_retriages(self, project):
        r = self._run(project, "update", "CB-1", "-s", "critical")
        assert r.returncode == 0, r.stderr
        assert "severity=critical" in r.stdout

    def test_invalid_value_exits_1_without_a_traceback(self, project):
        r = self._run(project, "update", "CB-1", "--severity", "urgent")
        assert r.returncode == 1
        assert "Invalid severity" in r.stderr
        assert "Traceback" not in r.stderr, "bad input is a clean error, not a crash"

    def test_invalid_value_leaves_the_row_alone(self, project):
        self._run(project, "update", "CB-1", "--severity", "urgent")
        r = self._run(project, "query", "--severity", "medium")
        assert "CB-1" in r.stdout, "the rejected retriage must not have partially landed"

    def test_severity_and_status_together(self, project):
        r = self._run(project, "update", "CB-1", "--severity", "low", "--status", "fixed")
        assert r.returncode == 0, r.stderr
        assert "status=fixed" in r.stdout
        assert "severity=low" in r.stdout

    def test_a_committed_write_is_never_reported_as_bad_input(self, project):
        """``json.JSONDecodeError`` subclasses ``ValueError`` — it must not be caught.

        On a row with malformed stored ``meta``, ``update_finding`` commits the
        severity change and only then raises while serializing its return value.
        Folding that into the handler's ``ValueError`` arm would print a tidy
        one-line error and exit 1 — a failure-shaped signal for a write that
        already landed, which is the exact class of lie CB-15/CB-16 were about.
        The corruption must surface as a crash instead.
        """
        conn = db.connect(project)
        conn.execute("UPDATE findings SET meta = ? WHERE id = ?", ("{not json", "CB-1"))
        conn.commit()
        conn.close()

        r = self._run(project, "update", "CB-1", "--severity", "high")
        assert "Traceback" in r.stderr, (
            "stored-data corruption must not be disguised as an input error"
        )
        assert "JSONDecodeError" in r.stderr, r.stderr

        conn = db.connect(project)
        try:
            stored = conn.execute(
                "SELECT severity FROM findings WHERE id = ?", ("CB-1",)
            ).fetchone()
        finally:
            conn.close()
        assert stored["severity"] == "high", (
            "the write did land — which is precisely why a clean 'invalid input' "
            "exit would have been a false report"
        )


class TestDomainErrorsOrderingPin:
    """Direct pin for ``cli.domain_errors()``'s arm ORDER — CB-159.

    Every CLI handler that touches a domain call routes it through
    ``with domain_errors():`` (``cli.py``), and its two ``except`` arms must
    stay in this order: ``json.JSONDecodeError`` re-raises unchanged FIRST,
    and only then does a plain ``(ValueError, KeyError)`` arm print one line
    and ``sys.exit(1)``. Reversing or collapsing the two loses the
    distinction, because ``json.JSONDecodeError`` **is** a ``ValueError``
    subclass — see ``domain_errors``'s own docstring and CLAUDE.md's Error
    handling section.

    ``TestRetriageCliContract::test_a_committed_write_is_never_reported_as_bad_input``
    (above) already exercises this end to end, through the real ``update``
    CLI verb and a corrupted database — it is a genuine pin, not vacuous
    (confirmed by running the CB-159 mutant against it directly: removing the
    ``except json.JSONDecodeError: raise`` arm turns exactly that test red,
    5 of the class's 6 tests still pass). This class adds a second, MINIMAL
    pin that exercises ``domain_errors()`` in isolation — no subprocess, no
    database, no corrupted row — so a reader diagnosing an ordering
    regression has one test that names the mechanism directly rather than
    inferring it from a CLI round-trip.
    """

    def test_json_decode_error_propagates_unmodified(self):
        from codebugs.cli import domain_errors

        with pytest.raises(json.JSONDecodeError):
            with domain_errors():
                json.loads("{not json")

    def test_plain_value_error_is_caught_and_exits_1(self, capsys):
        from codebugs.cli import domain_errors

        with pytest.raises(SystemExit) as exc:
            with domain_errors(prefix="codebugs: "):
                raise ValueError("bad input")
        assert exc.value.code == 1
        assert "codebugs: bad input" in capsys.readouterr().err


class TestResolveStatus:
    def test_canonical_passthrough(self):
        for s in FINDING_STATUSES:
            assert resolve_finding_status(s) == s

    def test_all_aliases_resolve(self):
        for alias, canonical in FINDING_STATUS_ALIASES.items():
            assert resolve_finding_status(alias) == canonical

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Invalid finding status"):
            resolve_finding_status("banana")


class TestQueryFindings:
    @pytest.fixture(autouse=True)
    def seed_data(self, conn):
        findings.add_finding(
            conn,
            severity="critical",
            category="security",
            file="auth.py",
            description="SQL injection",
            source="semgrep",
            tags=["urgent"], new_category=True,
        )
        findings.add_finding(
            conn,
            severity="high",
            category="n_plus_one",
            file="api.py",
            description="Query in loop",
            source="claude", new_category=True,
        )
        findings.add_finding(
            conn,
            severity="medium",
            category="n_plus_one",
            file="views.py",
            description="Another N+1",
            source="claude", new_category=True,
        )
        findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="utils.py",
            description="Long line",
            source="ruff", new_category=True,
        )
        findings.update_finding(conn, "CB-4", status="fixed")

    def test_query_all(self, conn):
        result = findings.query_findings(conn)
        assert result["total"] == 4
        assert len(result["findings"]) == 4

    def test_query_by_status(self, conn):
        result = findings.query_findings(conn, status="open")
        assert result["total"] == 3
        assert all(f["status"] == "open" for f in result["findings"])

    def test_query_by_severity(self, conn):
        result = findings.query_findings(conn, severity="critical")
        assert result["total"] == 1
        assert result["findings"][0]["category"] == "security"

    def test_query_by_category(self, conn):
        result = findings.query_findings(conn, category="n_plus_one")
        assert result["total"] == 2

    def test_query_by_file_substring(self, conn):
        result = findings.query_findings(conn, file="api")
        assert result["total"] == 1

    def test_query_by_source(self, conn):
        result = findings.query_findings(conn, source="claude")
        assert result["total"] == 2

    def test_query_by_tag(self, conn):
        result = findings.query_findings(conn, tag="urgent")
        assert result["total"] == 1
        assert result["findings"][0]["id"] == "CB-1"

    def test_query_group_by_category(self, conn):
        result = findings.query_findings(conn, group_by="category")
        assert result["grouped"] is True
        groups = {g["group_key"]: g["count"] for g in result["groups"]}
        assert groups["n_plus_one"] == 2

    def test_query_group_by_file(self, conn):
        result = findings.query_findings(conn, group_by="file")
        assert result["grouped"] is True
        assert len(result["groups"]) == 4

    def test_query_with_limit(self, conn):
        result = findings.query_findings(conn, limit=2)
        assert len(result["findings"]) == 2
        assert result["total"] == 4

    def test_query_with_offset(self, conn):
        r1 = findings.query_findings(conn, limit=2, offset=0)
        r2 = findings.query_findings(conn, limit=2, offset=2)
        ids1 = {f["id"] for f in r1["findings"]}
        ids2 = {f["id"] for f in r2["findings"]}
        assert ids1.isdisjoint(ids2)

    def test_query_by_status_alias(self, conn):
        result = findings.query_findings(conn, status="done")
        assert result["total"] == 1
        assert result["findings"][0]["status"] == "fixed"

    def test_query_combined_filters(self, conn):
        result = findings.query_findings(conn, status="open", source="claude")
        assert result["total"] == 2
        assert all(f["source"] == "claude" for f in result["findings"])

    def test_query_invalid_group_by_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid group_by"):
            findings.query_findings(conn, group_by="invalid")

    def test_query_by_id_single(self, conn):
        result = findings.query_findings(conn, id="CB-1")
        assert result["total"] == 1
        assert result["findings"][0]["id"] == "CB-1"
        assert result["findings"][0]["description"] == "SQL injection"

    def test_query_by_id_missing_returns_empty(self, conn):
        result = findings.query_findings(conn, id="CB-MISSING")
        assert result["total"] == 0
        assert result["findings"] == []

    def test_query_by_ids_batch(self, conn):
        result = findings.query_findings(conn, ids=["CB-1", "CB-2", "CB-MISSING"])
        ids = {f["id"] for f in result["findings"]}
        assert ids == {"CB-1", "CB-2"}
        assert result["total"] == 2

    def test_query_id_and_filters_are_and_combined(self, conn):
        # CB-4 has status=fixed; AND with status=open must yield nothing.
        result = findings.query_findings(conn, id="CB-4", status="open")
        assert result["total"] == 0

    def test_query_empty_id_string_is_ignored(self, conn):
        # Empty-string id must not collapse to `WHERE id = ''` (which returns 0).
        result = findings.query_findings(conn, id="")
        assert result["total"] == 4

    def test_query_ids_batch_exceeding_default_limit_returns_all(self, conn):
        # Default limit=100; with 4 IDs the bump is a no-op, but verify the contract.
        all_ids = [f"CB-{n}" for n in range(1, 5)]
        result = findings.query_findings(conn, ids=all_ids, limit=2)
        assert result["total"] == 4
        assert len(result["findings"]) == 4


class TestSeverityOrdering:
    """CB-20: `severity` is TEXT, so a bare ORDER BY ranked `low` above `medium`.

    Under a LIMIT that is not a display quirk — it truncates the more important
    rows and nothing signals it.
    """

    @staticmethod
    def _add(conn, severity, name):
        findings.add_finding(
            conn, severity=severity, category="c", file=f"{name}.py", description=name, new_category=True
        )

    def test_full_order_follows_declared_precedence(self, conn):
        for sev in ("low", "critical", "medium", "high"):
            self._add(conn, sev, sev)
        got = [f["severity"] for f in findings.query_findings(conn, limit=50)["findings"]]
        assert got == list(SEVERITIES)

    def test_limit_does_not_truncate_medium_in_favour_of_low(self, conn):
        """The regression that makes this a defect rather than a cosmetic issue."""
        for i in range(3):
            self._add(conn, "medium", f"m{i}")
        for i in range(3):
            self._add(conn, "low", f"l{i}")

        top3 = [f["severity"] for f in findings.query_findings(conn, limit=3)["findings"]]
        assert top3 == ["medium"] * 3, "low cards outranked medium and truncated them"

    def test_ordering_survives_an_active_where_filter(self, conn):
        """Guards the parameter-splice trap.

        The CASE placeholders sit between the WHERE fragment and LIMIT/OFFSET.
        Binding them in the wrong position corrupts *filtered* queries only —
        every unfiltered test above would still pass.
        """
        for sev in ("low", "critical", "medium", "high"):
            self._add(conn, sev, sev)
        findings.add_finding(
            conn, severity="critical", category="other", file="x.py", description="excluded", new_category=True
        )

        result = findings.query_findings(conn, category="c", status="open", limit=50)
        assert [f["severity"] for f in result["findings"]] == list(SEVERITIES)
        assert result["total"] == 4, "the WHERE filter itself must still be applied"
        assert all(f["category"] == "c" for f in result["findings"])

    def test_offset_paginates_in_rank_order(self, conn):
        for sev in ("low", "critical", "medium", "high"):
            self._add(conn, sev, sev)
        page2 = findings.query_findings(conn, limit=2, offset=2)["findings"]
        assert [f["severity"] for f in page2] == ["medium", "low"]

    def test_summary_severity_keys_are_in_precedence_order(self, conn):
        for sev in ("low", "critical", "medium", "high"):
            self._add(conn, sev, sev)
        assert list(findings.get_summary(conn)["open_by_severity"].keys()) == list(SEVERITIES)

    def test_rows_of_one_severity_stay_contiguous(self, conn):
        """The rank sorts by severity, so equal severities must not interleave.

        Deliberately not asserting the order WITHIN a severity: `utc_now()` is
        whole-second, so rows created in the same second share a `created_at` and
        the secondary sort has nothing to separate them. Asserting an order there
        would be a flaky test dressed up as a contract.
        """
        for i in range(2):
            self._add(conn, "low", f"l{i}")
            self._add(conn, "medium", f"m{i}")

        got = [f["severity"] for f in findings.query_findings(conn, limit=50)["findings"]]
        assert got == ["medium", "medium", "low", "low"]


class TestGetFinding:
    def test_get_returns_full_body(self, conn):
        added = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="x.py",
            description="boom",
            tags=["a", "b"],
            meta={"k": "v"}, new_category=True,
        )
        result = findings.get_finding(conn, added["id"])
        assert result["id"] == added["id"]
        assert result["description"] == "boom"
        assert result["tags"] == ["a", "b"]
        # `loc` excluded, not relaxed away — see TestAddFinding::test_add_basic.
        assert {k: v for k, v in result["meta"].items() if k != "loc"} == {
            "k": "v",
            "category_minted": True,
        }

    def test_get_missing_raises_keyerror(self, conn):
        with pytest.raises(KeyError, match="CB-MISSING"):
            findings.get_finding(conn, "CB-MISSING")


class TestQueryMeta:
    def test_query_by_meta_key(self, conn):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"rule_code": "C901"}, new_category=True,
        )
        findings.add_finding(conn, severity="low", category="style", file="b.py", description="d2", new_category=True)
        result = findings.query_findings(conn, meta_key="rule_code")
        assert result["total"] == 1

    def test_query_by_meta_key_value(self, conn):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"rule_code": "C901"}, new_category=True,
        )
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="b.py",
            description="d2",
            meta={"rule_code": "E501"}, new_category=True,
        )
        result = findings.query_findings(conn, meta_key="rule_code", meta_value="C901")
        assert result["total"] == 1
        assert result["findings"][0]["file"] == "a.py"


class TestStats:
    @pytest.fixture(autouse=True)
    def seed_data(self, conn):
        findings.add_finding(
            conn, severity="critical", category="security", file="a.py", description="d1", new_category=True
        )
        findings.add_finding(
            conn, severity="high", category="security", file="b.py", description="d2", new_category=True
        )
        findings.add_finding(conn, severity="high", category="perf", file="c.py", description="d3", new_category=True)
        findings.add_finding(
            conn, severity="medium", category="style", file="d.py", description="d4", new_category=True
        )

    def test_stats_by_severity(self, conn):
        result = findings.get_stats(conn, group_by="severity")
        groups = result["groups"]
        # When group_by=severity, each group key is a severity level
        assert groups["critical"]["total"] == 1
        assert groups["high"]["total"] == 2
        assert groups["medium"]["total"] == 1

    def test_stats_by_category(self, conn):
        result = findings.get_stats(conn, group_by="category")
        groups = result["groups"]
        assert groups["security"]["total"] == 2
        assert groups["security"]["critical"] == 1
        assert groups["security"]["high"] == 1

    def test_stats_invalid_group_by(self, conn):
        with pytest.raises(ValueError, match="Invalid group_by"):
            findings.get_stats(conn, group_by="invalid")


class TestSummary:
    def test_summary_empty(self, conn):
        s = findings.get_summary(conn)
        assert s["total"] == 0
        assert s["open"] == 0

    def test_summary_with_data(self, conn):
        findings.add_finding(
            conn, severity="critical", category="sec", file="a.py", description="d1", new_category=True
        )
        findings.add_finding(conn, severity="high", category="perf", file="b.py", description="d2", new_category=True)
        findings.add_finding(
            conn, severity="medium", category="perf", file="c.py", description="d3", new_category=True
        )
        findings.update_finding(conn, "CB-3", status="fixed")

        s = findings.get_summary(conn)
        assert s["total"] == 3
        assert s["open"] == 2
        assert s["resolved"] == 1
        assert s["open_by_severity"]["critical"] == 1
        assert s["open_by_severity"]["high"] == 1
        assert len(s["top_categories"]) == 2
        assert len(s["hottest_files"]) == 2

    def test_summary_hottest_files_ranked_by_crit_high(self, conn):
        findings.add_finding(
            conn, severity="critical", category="sec", file="danger.py", description="d1", new_category=True
        )
        findings.add_finding(
            conn, severity="high", category="sec", file="danger.py", description="d2", new_category=True
        )
        findings.add_finding(
            conn, severity="low", category="style", file="safe.py", description="d3", new_category=True
        )
        findings.add_finding(
            conn, severity="low", category="style", file="safe.py", description="d4", new_category=True
        )
        findings.add_finding(
            conn, severity="low", category="style", file="safe.py", description="d5", new_category=True
        )

        s = findings.get_summary(conn)
        assert s["hottest_files"][0]["file"] == "danger.py"
        assert s["hottest_files"][0]["critical_high"] == 2


class TestCategories:
    def test_categories_empty(self, conn):
        assert findings.get_categories(conn) == []

    def test_categories_with_data(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d1", new_category=True)
        findings.add_finding(conn, severity="high", category="bug", file="b.py", description="d2", new_category=True)
        findings.add_finding(
            conn, severity="medium", category="style", file="c.py", description="d3", new_category=True
        )
        findings.update_finding(conn, "CB-1", status="fixed")

        cats = findings.get_categories(conn)
        assert len(cats) == 2
        bug = next(c for c in cats if c["category"] == "bug")
        assert bug["total"] == 2
        assert bug["open_count"] == 1
        assert bug["fixed_count"] == 1


class TestProvenance:
    def test_fresh_db_has_provenance_columns(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
        assert "reported_at_commit" in cols
        assert "reported_at_ref" in cols

    def test_fresh_db_carries_reported_at_ref_index(self):
        """CB-115: a FRESH database must carry idx_findings_reported_at_ref.

        Both historical creators of this index live on migration paths a fresh
        database never takes — `_migrate_statuses`' conditional rebuild (SCHEMA
        already spells `in_progress`, so it early-returns) and the provenance
        ALTER (whose `"reported_at_ref" not in cols` guard is false because
        SCHEMA carries the column). Freshness is therefore the discriminating
        fixture: an in-memory DB sees only SCHEMA + _POST_MIGRATION_INDEXES,
        which is exactly where the index must be declared.
        """
        c = sqlite3.connect(":memory:")
        try:
            findings.ensure_schema(c)
            row = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_findings_reported_at_ref'"
            ).fetchone()
            assert row is not None, "fresh database is missing idx_findings_reported_at_ref"
        finally:
            c.close()

    def test_provenance_columns_nullable(self, conn):
        result = findings.add_finding(
            conn,
            severity="high",
            category="test",
            file="a.py",
            description="no provenance", new_category=True,
        )
        assert result.get("reported_at_commit") is None
        assert result.get("reported_at_ref") is None

    def test_migrate_adds_provenance_to_existing_db(self, tmp_path):
        """Simulate a DB created before provenance columns existed.

        Uses a bare tmp_path (not the initialized `tmp_project`) so the legacy
        schema is the FIRST thing in this DB file — `.codebugs/` is created
        directly, which is the opt-in `connect()` requires.
        """
        tmp_project = str(tmp_path)
        path = os.path.join(tmp_project, db.DB_DIR, db.DB_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old_conn = sqlite3.connect(path)
        old_conn.execute("""CREATE TABLE findings (
            id TEXT PRIMARY KEY,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
            description TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'human',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        old_conn.execute(
            "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "CB-1",
                "high",
                "bug",
                "x.py",
                "open",
                "old bug",
                "human",
                "[]",
                "{}",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        old_conn.commit()
        old_conn.close()

        # Re-open via connect() which triggers migration
        conn = db.connect(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
        assert "reported_at_commit" in cols
        assert "reported_at_ref" in cols

        # Old data survives
        row = conn.execute("SELECT * FROM findings WHERE id = 'CB-1'").fetchone()
        assert row is not None
        assert row["reported_at_commit"] is None
        assert row["reported_at_ref"] is None
        conn.close()

    def test_migrate_provenance_idempotent(self, tmp_project):
        """Calling connect() twice on the same DB should not error."""
        conn1 = db.connect(tmp_project)
        findings.add_finding(conn1, severity="low", category="test", file="a.py", description="d", new_category=True)
        conn1.close()

        conn2 = db.connect(tmp_project)
        row = conn2.execute("SELECT * FROM findings WHERE id = 'CB-1'").fetchone()
        assert row is not None
        conn2.close()

    def test_add_with_explicit_provenance(self, conn):
        result = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="test",
            reported_at_commit="a" * 40,
            reported_at_ref="v2.1.0", new_category=True,
        )
        assert result["reported_at_commit"] == "a" * 40
        assert result["reported_at_ref"] == "v2.1.0"

    def test_add_without_provenance_defaults_none(self, conn):
        result = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="test", new_category=True,
        )
        assert result["reported_at_commit"] is None
        assert result["reported_at_ref"] is None

    def test_batch_add_with_provenance(self, conn):
        results = findings.batch_add_findings(
            conn,
            [
                {
                    "severity": "high",
                    "category": "bug",
                    "file": "a.py",
                    "description": "d1",
                    "reported_at_commit": "b" * 40,
                    "reported_at_ref": "v1.0",
                },
                {
                    "severity": "low",
                    "category": "style",
                    "file": "b.py",
                    "description": "d2",
                },
            ], new_category=True,
        )
        assert results[0]["reported_at_commit"] == "b" * 40
        assert results[0]["reported_at_ref"] == "v1.0"
        assert results[1]["reported_at_commit"] is None
        assert results[1]["reported_at_ref"] is None

    def test_query_by_commit_prefix(self, conn):
        sha = "a1b2c3d4e5" + "0" * 30
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            reported_at_commit=sha, new_category=True,
        )
        findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="b.py",
            description="d2", new_category=True,
        )
        result = findings.query_findings(conn, commit="a1b2c3d4e5")
        assert result["total"] == 1
        assert result["findings"][0]["reported_at_commit"] == sha

    def test_query_by_commit_rejects_non_hex(self, conn):
        with pytest.raises(ValueError, match="hex"):
            findings.query_findings(conn, commit="not-hex!")

    def test_query_by_ref(self, conn):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            reported_at_ref="v2.1.0", new_category=True,
        )
        findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="b.py",
            description="d2",
            reported_at_ref="v3.0.0", new_category=True,
        )
        result = findings.query_findings(conn, ref="v2.1.0")
        assert result["total"] == 1
        assert result["findings"][0]["reported_at_ref"] == "v2.1.0"

    def test_query_by_ref_is_exact_not_prefix(self, conn):
        """Pins DELIBERATELY PRESERVED behaviour (BT-4 T-11 — green on both
        sides of the docs-only change): `ref` matches the stored value — the
        first-observed or manually assigned release ref — EXACTLY. "v1" must
        not prefix-match a "v1.0" row, unlike the `commit` filter, which is
        documented prefix. The sibling test above cannot see a prefix mutant
        because it queries the full value only."""
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            reported_at_ref="v1.0", new_category=True,
        )
        assert findings.query_findings(conn, ref="v1")["total"] == 0
        assert findings.query_findings(conn, ref="v1.0")["total"] == 1

    def test_update_reported_at_ref(self, conn):
        f = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d", new_category=True,
        )
        updated = findings.update_finding(conn, f["id"], reported_at_ref="v2.0")
        assert updated["reported_at_ref"] == "v2.0"

    def test_update_does_not_accept_reported_at_commit(self, conn):
        """reported_at_commit is immutable — not a parameter of update_finding."""
        f = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            reported_at_commit="a" * 40, new_category=True,
        )
        with pytest.raises(TypeError):
            findings.update_finding(conn, f["id"], reported_at_commit="b" * 40)


class TestSeverityIsNormalizedAtEveryRoute:
    """CB-19: severity gained a resolver, so every route must use it.

    One test per route, deliberately. A single CSV-import test would pass whether
    or not the fix exists, because the CSV path already lowercased inline — the
    cross-model review named that as the likely vacuous test for this card. Each
    test here asserts the value as STORED, so normalizing only on the way out
    would not satisfy them.
    """

    def test_add_finding(self, conn):
        f = findings.add_finding(
            conn, severity="High", category="bug", file="a.py", description="d", new_category=True
        )
        assert conn.execute(
            "SELECT severity FROM findings WHERE id = ?", (f["id"],)
        ).fetchone()["severity"] == "high"

    def test_batch_add_findings(self, conn):
        rows = findings.batch_add_findings(
            conn,
            [{"severity": " CRITICAL ", "category": "bug", "file": "a.py", "description": "d"}], new_category=True,
        )
        assert conn.execute(
            "SELECT severity FROM findings WHERE id = ?", (rows[0]["id"],)
        ).fetchone()["severity"] == "critical"

    def test_batch_add_defaults_still_work(self, conn):
        """The default flows through the resolver too — it must not be special-cased."""
        rows = findings.batch_add_findings(
            conn, [{"category": "bug", "file": "a.py", "description": "d"}], new_category=True
        )
        assert rows[0]["severity"] == "medium"

    def test_update_finding(self, conn):
        f = findings.add_finding(
            conn, severity="low", category="bug", file="a.py", description="d", new_category=True
        )
        findings.update_finding(conn, f["id"], severity="MeDiUm")
        assert conn.execute(
            "SELECT severity FROM findings WHERE id = ?", (f["id"],)
        ).fetchone()["severity"] == "medium"

    def test_query_filter_finds_the_row_it_wrote(self, conn):
        """The fifth site, found by cross-model review and missed by the card.

        `query_findings` resolved `status` and left `severity` RAW, two lines apart.
        Had the write paths been normalized alone, this exact call would have written
        `high` and then matched nothing — a silent empty result, not an error."""
        findings.add_finding(
            conn, severity="High", category="bug", file="a.py", description="d", new_category=True
        )
        assert findings.query_findings(conn, severity="HIGH")["total"] == 1
        assert findings.query_findings(conn, severity="high")["total"] == 1

    def test_query_filter_refuses_a_non_value(self, conn):
        """Raising beats returning zero rows: an unknown filter is a caller bug, and
        silently reporting "no findings" is the worst possible answer for a tracker."""
        with pytest.raises(ValueError, match="Invalid severity"):
            findings.query_findings(conn, severity="banana")

    def test_csv_import_still_normalizes_without_its_inline_lower(self, tmp_project, tmp_path):
        """The inline `.strip().lower()` was removed as redundant — this proves the
        redundancy claim rather than assuming it."""
        csv_file = tmp_path / "in.csv"
        csv_file.write_text(
            "severity,category,file,description\nHIGH,bug,a.py,something broke\n"
        )
        r = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "import-csv", str(csv_file)],
            capture_output=True, text=True, cwd=tmp_project,
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )
        assert r.returncode == 0, r.stderr
        c = db.connect(tmp_project)
        try:
            assert c.execute("SELECT severity FROM findings").fetchone()["severity"] == "high"
        finally:
            c.close()


class TestQueryCliReportsBadVocabularyCleanly:
    """CB-19 review finding: `_cmd_query` never caught `ValueError`, so an unknown
    `--status` printed a raw traceback. That predates CB-19 — `status` already
    resolved — but routing `--severity` through a resolver widened the exposure,
    so the handler is fixed here rather than left for the next flag to trip."""

    def _run(self, tmp_project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            capture_output=True, text=True, cwd=tmp_project,
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )

    @pytest.mark.parametrize("flag", ["--severity", "--status"])
    def test_unknown_vocabulary_value_exits_1_without_a_traceback(self, tmp_project, flag):
        r = self._run(tmp_project, "query", flag, "banana")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert "banana" in r.stderr

    def test_a_valid_query_still_works(self, tmp_project):
        """The guard must not swallow the happy path."""
        r = self._run(tmp_project, "query", "--severity", "HIGH")
        assert r.returncode == 0, r.stdout + r.stderr


class TestFalseyVocabularyFiltersDoNotDisableTheFilter:
    """CB-25: `if severity:` conflated "not supplied" with "wrong input".

    CB-19 put the non-string refusal inside `_resolve`, but a *falsey* value never
    reached it — the truthy guard short-circuited first, the condition was never added,
    and the caller got the whole table back. An unfiltered queue is indistinguishable
    from a correctly filtered one, which is the silent shape CB-19 was filed against."""

    def _two(self, conn):
        findings.add_finding(conn, severity="high", category="c", file="a.py", description="d", new_category=True)
        findings.add_finding(conn, severity="low", category="c", file="b.py", description="d", new_category=True)

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_severity_raises_instead_of_returning_everything(self, conn, falsey):
        self._two(conn)
        with pytest.raises(ValueError, match="Invalid severity"):
            findings.query_findings(conn, severity=falsey)

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_status_raises_instead_of_returning_everything(self, conn, falsey):
        self._two(conn)
        with pytest.raises(ValueError, match="Invalid finding status"):
            findings.query_findings(conn, status=falsey)

    @pytest.mark.parametrize("empty", [None, ""])
    def test_none_and_empty_string_still_mean_no_filter(self, conn, empty):
        """The documented convention, unchanged."""
        self._two(conn)
        assert findings.query_findings(conn, severity=empty)["total"] == 2
        assert findings.query_findings(conn, status=empty)["total"] == 2

    def test_list_valued_filters_keep_their_empty_means_no_filter_semantics(self, conn):
        """`ids=[]` is NOT a vocabulary filter and must stay untouched: made "active"
        it would emit `id IN ()`, which SQLite accepts and which returns NOTHING —
        turning a silent full queue into a silent empty one, quieter and worse."""
        self._two(conn)
        assert findings.query_findings(conn, ids=[])["total"] == 2


class TestMetaValueRequiresMetaKey:
    """CB-28: a lone `meta_value` matched neither arm of the meta filter, so it
    added no condition and the caller got the unfiltered queue — while the MCP
    description already declared `meta_key` required."""

    def test_meta_value_without_meta_key_raises(self, conn):
        findings.add_finding(conn, severity="high", category="c", file="a.py", description="d", new_category=True)
        with pytest.raises(ValueError, match="meta_value requires meta_key"):
            findings.query_findings(conn, meta_value="anything")

    def test_meta_key_alone_still_means_key_exists(self, conn):
        findings.add_finding(
            conn, severity="high", category="c", file="a.py", description="d", meta={"k": "v"}, new_category=True
        )
        findings.add_finding(conn, severity="low", category="c", file="b.py", description="d", new_category=True)
        assert findings.query_findings(conn, meta_key="k")["total"] == 1

    def test_both_together_still_match_on_value(self, conn):
        findings.add_finding(
            conn, severity="high", category="c", file="a.py", description="d", meta={"k": "v"}, new_category=True
        )
        assert findings.query_findings(conn, meta_key="k", meta_value="v")["total"] == 1
        assert findings.query_findings(conn, meta_key="k", meta_value="other")["total"] == 0


class TestImportCsvFileIOErrorContract:
    """CB-71 sibling sweep. `_cmd_import_csv` performed its `open()` outside any
    arm, so an unreadable path escaped as a raw traceback.

    UPDATED BY CB-77. The original fix hoisted the `open` out of its `with` and
    guarded that alone, leaving a mid-iteration read failure to crash on purpose,
    because what to report with rows already committed was an undecided
    semantics question. That question was ratified 2026-08-18 as ALL-OR-NOTHING,
    so the handler now reads the WHOLE file before the transaction opens and the
    import runs inside one `db.txn`. A read failure therefore happens with
    nothing written at all, and the interleaving this docstring used to describe
    no longer exists. The CB-15/CB-16 rule it was protecting is unchanged and is
    now pinned by `TestImportIsAllOrNothing`: a failure must not print a count.

    Non-vacuity: the unfixed tree already exits 1 with the path inside its
    traceback, so `"Traceback" not in stderr` is the only discriminating
    assertion below.
    """

    def _run(self, tmp_project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            capture_output=True, text=True, cwd=tmp_project,
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )

    def test_missing_csv_path_is_a_clean_error_not_a_traceback(self, tmp_project):
        r = self._run(tmp_project, "import-csv", "missing.csv")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert "missing.csv" in r.stderr, r.stderr

    def test_a_directory_path_is_a_clean_error_not_a_traceback(self, tmp_project, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        r = self._run(tmp_project, "import-csv", str(d))
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr

    def test_a_readable_csv_still_imports(self, tmp_project, tmp_path):
        """The normal condition: hoisting the open out of the `with` must not
        change what the loop does, and the handle must still be closed by it."""
        csv_file = tmp_path / "in.csv"
        csv_file.write_text(
            "severity,category,file,description\nhigh,bug,a.py,something broke\n"
        )
        r = self._run(tmp_project, "import-csv", str(csv_file))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Imported 1" in r.stdout, r.stdout


def _write_csv(path, rows, header=None):
    """Write an import CSV. Header defaults to the export's own column set."""
    header = header or [
        "id", "severity", "category", "file", "status", "description",
        "source", "tags", "meta", "created_at", "updated_at",
        "fingerprint", "occurrence_count", "last_seen_at",
    ]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


class TestImportNeverReopensADecidedCard:
    """CB-51 defect 1. An import is a statement about someone ELSE's tracker, so
    it must not resurrect a card this tracker has decided. Reproduced before the
    fix: a foreign row whose id did not even exist locally (`CB-9001`) matched a
    local `fixed` card by fingerprint and flipped it to `open`.

    The change is deliberately narrow — only the REOPEN branch. A live hit still
    bumps and a `wont_fix` hit still files a recurrence, both pinned below as
    green-on-both-sides controls, because neither is a filed defect and changing
    them would be an unrequested behaviour change riding along.
    """

    def _foreign_row(self, description, row_id="CB-9001"):
        return {"id": row_id, "severity": "high", "category": "bug",
                "file": "src/a.py", "description": description, "source": "peer"}

    def test_a_fixed_card_is_not_reopened(self, conn, tmp_path):
        f = findings.add_finding(conn, severity="high", category="bug", file="src/a.py",
                        description="the widget explodes on load", new_category=True)
        findings.update_finding(conn, f["id"], status="fixed")
        path = _write_csv(tmp_path / "x.csv", [self._foreign_row("the widget explodes on load")])
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert findings.get_finding(conn, f["id"])["status"] == "fixed"
        # counted as DECIDED, not as "already present" — the local tracker does
        # not hold this row, it holds a decision about the same defect, and the
        # operator-facing message says so.
        assert report.skipped_decided == 1, report
        assert report.skipped_present == 0, report
        assert report.imported == 0, report

    def test_a_live_card_is_still_bumped(self, conn, tmp_path):
        """CONTROL — green on both sides. Another sighting of a card you already
        have IS an occurrence; the fix must not turn that into a skip."""
        f = findings.add_finding(conn, severity="high", category="bug", file="src/a.py",
                        description="the widget explodes on load", new_category=True)
        path = _write_csv(tmp_path / "x.csv", [self._foreign_row("the widget explodes on load")])
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.merged == 1, report
        assert findings.get_finding(conn, f["id"])["occurrence_count"] == 2

    def test_docstring_reopen_claim_matches_the_executable_set(self):
        """CB-114: the import_findings docstring must not overstate the REOPEN set.

        The prose claimed a hit on a `fixed`/`stale` row "would normally REOPEN
        it", while the executable set is `_REOPEN_STATUSES = ("fixed",)` —
        `stale` is in LIVE_STATUSES and bumps. This pin extracts the sentences
        that use REOPEN (the branch's own capitalization) and asserts the
        backticked statuses they name are exactly the executable reopen set, so
        the prose cannot silently widen or narrow again.
        """
        doc = findings.import_findings.__doc__
        assert doc, "import_findings lost its docstring"
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", doc) if "REOPEN" in s]
        assert sentences, "the docstring no longer explains the reopen rule"
        named = set(re.findall(r"`([a-z_]+)`", " ".join(sentences)))
        assert named & set(FINDING_STATUSES) == set(findings._REOPEN_STATUSES), (
            f"docstring names {sorted(named & set(FINDING_STATUSES))} in its REOPEN "
            f"sentences; the executable set is {sorted(findings._REOPEN_STATUSES)}"
        )

    def test_a_wont_fix_card_still_files_a_recurrence(self, conn, tmp_path):
        """CONTROL — green on both sides. `wont_fix` is a decision that stays
        decided, and CB-43's answer is a NEW linked row, not a reopen."""
        f = findings.add_finding(conn, severity="high", category="bug", file="src/a.py",
                        description="the widget explodes on load", new_category=True)
        findings.update_finding(conn, f["id"], status="wont_fix")
        path = _write_csv(tmp_path / "x.csv", [self._foreign_row("the widget explodes on load")])
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.imported == 1, report
        assert findings.get_finding(conn, f["id"])["status"] == "wont_fix"


class TestAnIdIdentifiesARowOnlyWithItsContent:
    """CB-51 defect 2 and the unfiled defect 4. The old guard asked
    `SELECT 1 FROM findings WHERE id = ?` — bare-id existence, not identity."""

    def test_a_foreign_export_with_colliding_ids_lands(self, conn, tmp_path):
        """Every tracker numbers CB-1, CB-2, ... Reproduced before the fix: all
        three peer rows were dropped into a 3-row tracker and reported as
        '3 already present, skipped'."""
        for i in range(3):
            findings.add_finding(conn, severity="low", category="local", file=f"src/l{i}.py",
                        description=f"local finding number {i}", new_category=True)
        rows = [{"id": f"CB-{i}", "severity": "high", "category": "peerbug",
                 "file": f"src/p{i}.py", "description": f"unrelated peer finding {i}",
                 "source": "peer"} for i in (1, 2, 3)]
        path = _write_csv(tmp_path / "peer.csv", rows)
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.imported == 3, report
        assert report.skipped_present == 0, report
        landed = conn.execute(
            "SELECT id, json_extract(meta, '$.imported_id') FROM findings WHERE category='peerbug'"
        ).fetchall()
        assert len(landed) == 3
        # the origin survives renumbering
        assert sorted(x[1] for x in landed) == ["CB-1", "CB-2", "CB-3"]

    def test_reimporting_your_own_export_is_still_a_no_op(self, conn, tmp_path):
        """CONTROL for the guard's real purpose. Deleting the id check entirely
        would break this, which is why it was narrowed rather than removed."""
        findings.add_finding(conn, severity="low", category="c", file="a.py", description="mine one", new_category=True)
        findings.add_finding(conn, severity="low", category="c", file="b.py", description="mine two", new_category=True)
        rows = [dict(r) for r in conn.execute(
            "SELECT id, severity, category, file, description, source FROM findings")]
        path = _write_csv(tmp_path / "own.csv", rows)
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.skipped_present == 2, report
        assert report.imported == 0, report
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 2

    def test_a_null_fingerprint_row_does_not_duplicate(self, conn, tmp_path):
        """The population a fingerprint-only skip CANNOT see. A row written with
        an explicit id stores `fingerprint = NULL`, and NULL matches nothing — so
        without the id half of the guard, every pre-CB-43 row and every
        explicit-id row would duplicate on each re-import. Found by adversarial
        review of the plan that proposed deleting the id check."""
        findings.add_finding(conn, severity="low", category="c", file="a.py",
                    description="explicit id row", finding_id="CB-500")
        assert conn.execute(
            "SELECT fingerprint FROM findings WHERE id='CB-500'").fetchone()[0] is None
        rows = [{"id": "CB-500", "severity": "low", "category": "c",
                 "file": "a.py", "description": "explicit id row", "source": "import"}]
        path = _write_csv(tmp_path / "null.csv", rows)
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.skipped_present == 1, report
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1

    def test_an_id_minted_by_this_import_does_not_drop_a_later_row(self, conn, tmp_path):
        """DEFECT 4, which was on no card. `export-csv` orders by SEVERITY, so a
        normal export is not in ascending id order; the allocator then mints ids
        that later rows of the same file still name. Measured before the fix:
        3 rows exported, 2 restored, exit 0, one row silently lost."""
        rows = [
            {"id": "CB-1", "severity": "high", "category": "b", "file": "x.py",
             "description": "alpha defect", "source": "s"},
            {"id": "CB-3", "severity": "medium", "category": "b", "file": "z.py",
             "description": "gamma defect", "source": "s"},
            {"id": "CB-2", "severity": "low", "category": "b", "file": "y.py",
             "description": "beta defect", "source": "s"},
        ]
        path = _write_csv(tmp_path / "sev.csv", rows)
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.imported == 3, report
        descriptions = {r[0] for r in conn.execute("SELECT description FROM findings")}
        assert descriptions == {"alpha defect", "beta defect", "gamma defect"}


class TestImportIsAllOrNothing:
    """CB-77, ratified 2026-08-18. The loop used to call `add_finding` per row,
    each committing, so a failure part-way left N rows behind it."""

    def test_a_failure_mid_import_lands_nothing(self, conn, tmp_path, monkeypatch):
        findings.add_finding(conn, severity="low", category="pre", file="p.py", description="pre-existing", new_category=True)
        rows = [{"id": "", "severity": "low", "category": "c", "file": f"f{i}.py",
                 "description": f"row {i}", "source": "s"} for i in range(5)]
        path = _write_csv(tmp_path / "boom.csv", rows)

        real = findings._add_one
        calls = {"n": 0}

        def exploding(*a, **k):
            calls["n"] += 1
            if calls["n"] == 3:
                raise sqlite3.OperationalError("simulated failure mid-import")
            return real(*a, **k)

        monkeypatch.setattr(findings, "_add_one", exploding)
        with open(path, newline="") as fh:
            with pytest.raises(sqlite3.OperationalError):
                findings.import_findings(conn, list(csv.DictReader(fh)))
        # the two rows that had already been written must be gone
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1

    def test_the_cli_prints_no_count_when_nothing_landed(self, tmp_project, tmp_path):
        """The CB-15/CB-16 rule: a rollback must not be reported with a success
        count. Driven through the real CLI because that is where the lie would
        be printed."""
        csv_file = tmp_path / "bad.csv"
        csv_file.write_text("id,severity,category,file,description\n")
        r = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "import-csv", str(csv_file)],
            capture_output=True, text=True, cwd=tmp_project,
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Imported 0 findings." in r.stdout, r.stdout


class TestImportCarriesTags:
    """Half of CB-51 defect 3 that the import path can close on its own. The
    exported `tags` column was parsed by nobody. Id, status and occurrence_count
    need the raw-insert seam and are CB-97."""

    def test_tags_round_trip_through_import(self, tmp_project, tmp_path):
        conn = db.connect(tmp_project)
        findings.add_finding(conn, severity="low", category="c", file="a.py",
                    description="tagged row", tags=["release", "ui"], new_category=True)
        conn.close()
        out = tmp_path / "e.csv"
        env = {**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")}
        subprocess.run([sys.executable, "-m", "codebugs.cli", "export-csv", str(out)],
                       cwd=tmp_project, capture_output=True, text=True, env=env, check=True)
        other = tmp_path.parent / (tmp_path.name + "-dst")
        other.mkdir()
        db.init_project(str(other))
        subprocess.run([sys.executable, "-m", "codebugs.cli", "import-csv", str(out)],
                       cwd=str(other), capture_output=True, text=True, env=env, check=True)
        conn2 = db.connect(str(other))
        tags = conn2.execute("SELECT tags FROM findings").fetchone()[0]
        conn2.close()
        assert json.loads(tags) == ["release", "ui"], tags


class TestImportRowContract:
    """`import_findings` takes rows in the shape `csv.DictReader` yields, so the
    CLI and a library caller hand over the same thing.

    An earlier draft accepted TWO shapes — the handler pre-decoded `meta` into a
    dict while every test passed the raw JSON string — and `dict("...")` would
    have raised. It only worked because the fixtures exported an empty meta
    column, i.e. the tests could not reach the contradiction they contained.
    Found by review of the diff, not by the suite.
    """

    def test_meta_arrives_as_json_text_and_reserved_keys_are_dropped(self, conn):
        rows = [{
            "id": "CB-900", "severity": "low", "category": "c", "file": "a.py",
            "description": "meta as text", "source": "peer",
            "meta": json.dumps({"lines": "10-20", "similar_to": [{"id": "CB-1"}],
                                "recurrence_of": "CB-2"}),
        }]
        report = findings.import_findings(conn, rows)
        assert report.imported == 1, report
        stored = json.loads(conn.execute("SELECT meta FROM findings").fetchone()[0])
        # the machinery's OUTPUT is not its input
        assert "similar_to" not in stored and "recurrence_of" not in stored, stored
        assert stored["lines"] == "10-20", stored
        assert stored["imported_id"] == "CB-900", stored

    def test_meta_already_decoded_is_also_accepted(self, conn):
        rows = [{"id": "", "severity": "low", "category": "c", "file": "a.py",
                 "description": "meta as dict", "meta": {"lines": "1-2"}}]
        report = findings.import_findings(conn, rows)
        assert report.imported == 1, report

    def test_malformed_tags_are_an_ERROR_not_a_silent_drop(self, conn):
        """A row whose tags cannot be read must say so. Dropping them quietly is
        the same quiet loss this card exists to remove, and the row already has
        an error channel."""
        rows = [{"id": "", "severity": "low", "category": "c", "file": "a.py",
                 "description": "bad tags", "tags": "not json at all"}]
        report = findings.import_findings(conn, rows)
        assert report.imported == 0, report
        assert len(report.errors) == 1, report
        assert "tags" in report.errors[0].message, report.errors

    def test_a_row_missing_required_fields_is_passed_over_silently(self, conn):
        """CONTROL — green on both sides. An incomplete row is not a finding and
        never was an error; pinned so the new error channel does not start
        claiming it is."""
        rows = [{"id": "", "severity": "low", "category": "", "file": "",
                 "description": ""}]
        report = findings.import_findings(conn, rows)
        assert report == findings.ImportReport(0, 0, 0, 0, []), report


class TestRestoreIsVerbatim:
    """CB-97. A restore states that these rows ARE the tracker, so it writes the stored
    columns directly — bypassing the identity function, the pre-add resolvers and the
    post-add hooks. Every constraint below was measured during the review that split this
    card out of CB-51."""

    def _seed(self, conn):
        a = findings.add_finding(conn, severity="high", category="bug", file="x.py",
                                 description="alpha", tags=["rel"], new_category=True)
        b = findings.add_finding(conn, severity="low", category="bug", file="y.py",
                                 description="beta", new_category=True)
        findings.update_finding(conn, a["id"], status="fixed")
        findings.update_finding(conn, b["id"], status="wont_fix")
        for _ in range(4):
            findings.add_finding(conn, severity="medium", category="bug", file="z.py",
                                 description="gamma repeated", new_category=True)
        # the recurrence twin — SAME fingerprint as the wont_fix row, and live
        findings.add_finding(conn, severity="low", category="bug", file="y.py",
                             description="beta", new_category=True)

    def _snapshot(self, conn):
        return [tuple(r) for r in conn.execute(
            "SELECT id, severity, category, file, status, description, source, tags, meta,"
            " reported_at_commit, reported_at_ref, created_at, updated_at, fingerprint,"
            " occurrence_count, last_seen_at FROM findings ORDER BY id")]

    def _export_rows(self, conn):
        rows = []
        for r in conn.execute(f"SELECT {', '.join(findings._RESTORE_COLUMNS)} FROM findings"):
            rows.append({c: r[c] for c in findings._RESTORE_COLUMNS})
        return rows

    def test_every_column_survives_the_round_trip(self, tmp_path):
        src_dir, dst_dir = tmp_path / "s", tmp_path / "d"
        for d in (src_dir, dst_dir):
            d.mkdir()
            db.init_project(str(d))
        src = db.connect(str(src_dir))
        self._seed(src)
        before = self._snapshot(src)
        rows = self._export_rows(src)
        src.close()

        dst = db.connect(str(dst_dir))
        report = findings.restore_findings(dst, rows)
        after = self._snapshot(dst)
        dst.close()
        assert report.restored == len(before), report
        assert after == before, "a restore must be verbatim"

    def test_a_recurrence_PAIR_sharing_a_fingerprint_restores(self, tmp_path):
        """The input that killed the previous design. A `wont_fix` card and its
        `recurrence_of` twin share a fingerprint BY DESIGN, so inserting both as `open`
        first collides on `ux_findings_fingerprint_live` — whichever lands second fails.
        Writing the FINAL statuses satisfies the partial index by construction."""
        src_dir, dst_dir = tmp_path / "s2", tmp_path / "d2"
        for d in (src_dir, dst_dir):
            d.mkdir()
            db.init_project(str(d))
        src = db.connect(str(src_dir))
        self._seed(src)
        fps = [r[0] for r in src.execute(
            "SELECT fingerprint FROM findings WHERE description='beta'")]
        rows = self._export_rows(src)
        src.close()
        assert len(fps) == 2 and fps[0] == fps[1], fps  # the premise this test rests on

        dst = db.connect(str(dst_dir))
        findings.restore_findings(dst, rows)
        pair = dst.execute(
            "SELECT status FROM findings WHERE description='beta' ORDER BY id").fetchall()
        dst.close()
        assert sorted(p[0] for p in pair) == ["open", "wont_fix"], pair

    def test_it_refuses_a_colliding_id_and_writes_nothing(self, conn):
        findings.add_finding(conn, severity="low", category="c", file="a.py",
                             description="already here", finding_id="CB-1")
        rows = [{"id": "CB-1", "severity": "low", "category": "c", "file": "b.py",
                 "description": "incoming", "status": "open"},
                {"id": "CB-2", "severity": "low", "category": "c", "file": "c.py",
                 "description": "also incoming", "status": "open"}]
        with pytest.raises(ValueError, match="already exist"):
            findings.restore_findings(conn, rows)
        # the non-colliding row must NOT have landed
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1

    def test_it_refuses_a_duplicate_id_within_the_file(self, conn):
        rows = [{"id": "CB-9", "severity": "low", "category": "c", "file": "a.py",
                 "description": "one"},
                {"id": "CB-9", "severity": "low", "category": "c", "file": "b.py",
                 "description": "two"}]
        with pytest.raises(ValueError, match="appears twice"):
            findings.restore_findings(conn, rows)
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0

    def test_it_refuses_a_row_with_no_id(self, conn):
        rows = [{"id": "", "severity": "low", "category": "c", "file": "a.py",
                 "description": "no identity"}]
        with pytest.raises(ValueError, match="requires an id"):
            findings.restore_findings(conn, rows)

    def test_a_bad_row_late_in_the_file_lands_nothing(self, conn):
        rows = [{"id": f"CB-{i}", "severity": "low", "category": "c", "file": "a.py",
                 "description": f"row {i}"} for i in range(1, 5)]
        rows[3]["occurrence_count"] = "not a number"
        with pytest.raises(ValueError, match="occurrence_count"):
            findings.restore_findings(conn, rows)
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0

    def test_no_milestone_items_are_fabricated(self, tmp_path):
        """Post-add hooks would invent one triage item and two audit rows per restored
        card, asserting a history that never happened. Absent is honest; fabricated is
        not. The CLI says so out loud."""
        d = tmp_path / "m"
        d.mkdir()
        db.init_project(str(d))
        conn = db.connect(str(d))
        rows = [{"id": "CB-1", "severity": "low", "category": "c", "file": "a.py",
                 "description": "restored", "status": "fixed"}]
        findings.restore_findings(conn, rows)
        items = conn.execute("SELECT COUNT(*) FROM milestone_items").fetchone()[0]
        audit = conn.execute("SELECT COUNT(*) FROM milestone_audit").fetchone()[0]
        conn.close()
        assert (items, audit) == (0, 0), (items, audit)

    def test_id_allocation_continues_after_restored_ids(self, conn):
        """A restore writes ids the allocator never minted, so the next `add` must not
        collide with them. `_next_id` is max-numeric + 1 — read, not assumed — and this
        pins it, because a restore depends on it silently."""
        rows = [{"id": "CB-500", "severity": "low", "category": "c", "file": "a.py",
                 "description": "restored high id"}]
        findings.restore_findings(conn, rows)
        nxt = findings.add_finding(conn, severity="low", category="c", file="b.py",
                                   description="after restore", new_category=True)
        assert nxt["id"] == "CB-501", nxt["id"]


class TestRestoreColumnsMatchTheSchema:
    """RATCHET. `_RESTORE_COLUMNS` is the single declaration the exporter AND the restore
    both build from. A column added to `findings` and forgotten here would be silently
    dropped by every future export and restore — quiet backup loss, which is the whole
    defect class this card closes."""

    def test_the_declaration_covers_every_findings_column(self, conn):
        live = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
        assert set(findings._RESTORE_COLUMNS) == live, (
            f"missing from _RESTORE_COLUMNS: {sorted(live - set(findings._RESTORE_COLUMNS))}; "
            f"stale in _RESTORE_COLUMNS: {sorted(set(findings._RESTORE_COLUMNS) - live)}"
        )


class TestExportIsNotCapped:
    """CB-97. `export-csv` used `query_findings(limit=100000)` — a silent ceiling on the one
    path where losing rows costs most, since an export is the input to a restore.

    Asserting "7 rows in, 7 rows out" would NOT catch a reintroduced cap of any size, so
    this asserts the property directly: the export must ask for at least as many rows as
    the tracker holds, and must not be paged. Paging was the first fix and review killed
    it — `query_findings` orders by severity rank and whole-second `created_at` with no
    unique tiebreaker, so OFFSET paging over that is not a stable partition and a tie group
    on a page boundary can be duplicated or skipped.
    """

    def test_the_export_requests_no_fewer_rows_than_exist(self, tmp_project, tmp_path, monkeypatch):
        conn = db.connect(tmp_project)
        for i in range(7):
            findings.add_finding(conn, severity="low", category="c", file=f"f{i}.py",
                                 description=f"row number {i}", new_category=True)
        conn.close()

        real = findings.query_findings
        limits = []

        def spy(conn_, **kw):
            limits.append(kw.get("limit"))
            return real(conn_, **kw)

        monkeypatch.setattr(findings, "query_findings", spy)
        out = tmp_path / "all.csv"
        # IN-PROCESS: an earlier draft shelled out, so a monkeypatch never reached the
        # exporter and the test proved nothing about the exporter at all.
        from codebugs import cli

        monkeypatch.setattr(
            sys, "argv", ["codebugs", "--tracker-root", tmp_project, "export-csv", str(out)]
        )
        cli.main()

        with open(out, newline="") as fh:
            assert len(list(csv.DictReader(fh))) == 7
        # the fetch that actually pulled the rows asked for >= the row count
        assert max(x for x in limits if x is not None) >= 7, limits
        # ...and there is no OFFSET walk: at most a count probe plus the real fetch
        assert len(limits) <= 2, limits


class TestRestoreScalesPastTheSqlVariableLimit:
    """A backup you cannot restore is not a backup. The collision pre-check used one SQL
    placeholder per row, and SQLite caps `SQLITE_LIMIT_VARIABLE_NUMBER` at 32766 on this
    build (999 on older ones) — measured: 40000 placeholders raise `too many SQL
    variables`. A tracker large enough would therefore export a file it could not read
    back, which is this card's own defect arriving at scale."""

    def test_a_restore_larger_than_the_variable_limit_succeeds(self, conn):
        n = 40000  # comfortably past 32766
        rows = [{"id": f"CB-{i}", "severity": "low", "category": "c", "file": "a.py",
                 "description": f"row {i}"} for i in range(1, n + 1)]
        report = findings.restore_findings(conn, rows)
        assert report.restored == n, report
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == n

    def test_a_collision_is_still_detected_in_a_large_file(self, conn):
        """The single-parameter `json_each` form must not lose a collision buried in a
        large batch — the obvious way to get the no-ceiling rewrite wrong."""
        findings.add_finding(conn, severity="low", category="c", file="a.py",
                             description="already here", finding_id="CB-33333")
        rows = [{"id": f"CB-{i}", "severity": "low", "category": "c", "file": "a.py",
                 "description": f"row {i}"} for i in range(1, 40001)]
        with pytest.raises(ValueError, match="CB-33333"):
            findings.restore_findings(conn, rows)
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# CB-123 — the `recent` verb: "what closed since <date>" in ONE call, while
# saying out loud that what it actually measures is the LAST TOUCH.
# ---------------------------------------------------------------------------


def _touch(conn, finding_id: str, stamp: str) -> None:
    """Force a row's `updated_at` to an exact whole-second value.

    Every write path stamps `utc_now()`, which no test can pin, and the ordering
    property under test is specifically about TIES in that whole-second column.
    Setting the column directly is the only way to construct a tie deliberately
    rather than by timing luck.
    """
    conn.execute("UPDATE findings SET updated_at = ? WHERE id = ?", (stamp, finding_id))
    conn.commit()


def _file_a_finding(conn, finding_id: str, *, status: str = "open", description: str = "") -> None:
    """An explicit id bypasses identity derivation and matching (CB-43 rule 3), so
    fixtures built from near-identical tuples stay distinct rows."""
    findings.add_finding(
        conn,
        severity="low",
        category="c",
        file="a.py",
        description=description or f"finding {finding_id}",
        finding_id=finding_id,
    )
    if status != "open":
        findings.update_finding(conn, finding_id, status=status)


def _findings_mcp_tools(conn):
    """The findings MCP wrappers, bound to a live connection, keyed by tool name."""
    import contextlib

    captured: dict[str, object] = {}

    class FakeMCP:
        def tool(self):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn

            return deco

    @contextlib.contextmanager
    def conn_factory():
        yield conn

    findings.register_tools(FakeMCP(), conn_factory)
    return captured


def _findings_cli_parsers():
    """The findings subparsers, by verb, without building the whole CLI."""
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    findings.register_cli(sub, {})
    return sub.choices


class TestRecentIsOneCallOnBothSurfaces:
    """Acceptance 1. The whole point of the card is that the answer arrives in ONE
    call on each surface — today it is assembled by hand from a ledger file and git
    history. Provider presence proves nothing (CB-28): these CALL the tools."""

    def test_the_mcp_tool_answers_in_one_call(self, conn):
        _file_a_finding(conn, "CB-1", status="fixed")
        _touch(conn, "CB-1", "2026-08-20T12:00:00Z")
        _file_a_finding(conn, "CB-2", status="fixed")
        _touch(conn, "CB-2", "2026-01-05T12:00:00Z")

        tools = _findings_mcp_tools(conn)
        out = tools["recent"](since="2026-08-01", status="fixed")

        assert [f["id"] for f in out["findings"]] == ["CB-1"]
        assert out["total"] == 1
        assert out["since"] == "2026-08-01"
        assert out["status"] == "fixed"

    def test_the_cli_verb_answers_in_one_call(self, tmp_project, monkeypatch, capsys):
        conn = db.connect(tmp_project)
        _file_a_finding(conn, "CB-1", status="fixed")
        _touch(conn, "CB-1", "2026-08-20T12:00:00Z")
        _file_a_finding(conn, "CB-2", status="fixed")
        _touch(conn, "CB-2", "2026-01-05T12:00:00Z")
        conn.close()

        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            ["codebugs", "--tracker-root", tmp_project,
             "recent", "--since", "2026-08-01", "--status", "fixed"],
        )
        cli.main()
        out = capsys.readouterr().out
        assert "CB-1" in out
        assert "CB-2" not in out


class TestRecentOrderIsDeterministic:
    """Acceptance 2. `utc_now()` is whole-second, so ties in `updated_at` are the
    ORDINARY case, not an edge one — and an arbitrary order under a LIMIT silently
    drops rows from the page. `rowid DESC` is the tiebreaker, the same form
    `_match_fingerprint` already uses for the same reason.

    This test is the mutant gate: delete `, rowid DESC` and SQLite falls back to
    scan order (rowid ASCENDING), which is the exact reverse of what is asserted.
    """

    def test_a_whole_second_tie_resolves_newest_row_first(self, conn):
        for i in range(1, 6):
            _file_a_finding(conn, f"CB-{i}")
            _touch(conn, f"CB-{i}", "2026-08-20T12:00:00Z")

        first = findings.recent_findings(conn, since="2026-08-20")
        second = findings.recent_findings(conn, since="2026-08-20")

        assert [f["id"] for f in first["findings"]] == ["CB-5", "CB-4", "CB-3", "CB-2", "CB-1"]
        assert [f["id"] for f in second["findings"]] == [f["id"] for f in first["findings"]]

    def test_the_tie_break_survives_paging(self, conn):
        """A LIMIT is where an arbitrary tie order stops being cosmetic: without a
        tiebreaker the two pages can overlap or skip a row entirely."""
        for i in range(1, 6):
            _file_a_finding(conn, f"CB-{i}")
            _touch(conn, f"CB-{i}", "2026-08-20T12:00:00Z")

        page1 = findings.recent_findings(conn, since="2026-08-20", limit=2, offset=0)
        page2 = findings.recent_findings(conn, since="2026-08-20", limit=2, offset=2)
        assert [f["id"] for f in page1["findings"]] == ["CB-5", "CB-4"]
        assert [f["id"] for f in page2["findings"]] == ["CB-3", "CB-2"]

    def test_the_order_by_is_a_literal_in_the_sql_template(self, recording):
        """Asserted against the TEMPLATE, not the executed statement — the repo rule.
        The literal also records the OTHER half of the chosen form: no caller-supplied
        column reaches `ORDER BY`, so CB-20/CB-22 cannot be reopened by an argument."""
        findings.recent_findings(recording, since="2026-08-20", status="fixed")
        selects = [s for s in recording.recorded_sql if s.startswith("SELECT * FROM findings")]
        assert len(selects) == 1, recording.recorded_sql
        assert "ORDER BY updated_at DESC, rowid DESC" in selects[0]


class TestQueryFindingsIsUntouchedByRecent:
    """Acceptance 3. The chosen form (a separate verb, no `order_by` parameter) makes
    this a property of the DIFF: `query_findings` is not changed at all, so the
    severity-rank-then-`created_at` order under LIMIT (CB-20) cannot have moved.
    These pin it so a later 'unification' cannot quietly undo the decision.
    `TestSeverityOrdering` above is the behavioural half and stays untouched.
    """

    def test_query_still_orders_by_severity_rank_then_created_at(self, recording):
        findings.query_findings(recording, status="open", severity="high")
        selects = [s for s in recording.recorded_sql if s.startswith("SELECT * FROM findings")]
        assert len(selects) == 1, recording.recorded_sql
        sql = selects[0]
        assert "created_at DESC LIMIT ? OFFSET ?" in sql
        assert "updated_at" not in sql
        assert "rowid" not in sql

    def test_query_gained_no_new_parameter(self):
        """The form was chosen so that the load-bearing parameter-order hazard inside
        `query_findings` is not entered at all. A `since`/`order_by` parameter
        appearing here would mean it was."""
        import inspect

        params = inspect.signature(findings.query_findings).parameters
        assert "since" not in params
        assert "order_by" not in params
        assert "updated_at" not in params


class TestRecentWithAStatusFilter:
    """Acceptance 4. The FILTERED case is mandatory: a swapped parameter order in a
    built query corrupts only filtered calls, while unfiltered ones keep passing —
    which is precisely the hazard `query_findings`' comment warns about, and the
    reason this new query must be exercised WITH its second bind."""

    def test_only_rows_matching_both_the_window_and_the_status_come_back(self, conn):
        _file_a_finding(conn, "CB-1", status="fixed")
        _touch(conn, "CB-1", "2026-08-20T12:00:00Z")
        _file_a_finding(conn, "CB-2")  # open, inside the window
        _touch(conn, "CB-2", "2026-08-20T12:00:00Z")
        _file_a_finding(conn, "CB-3", status="fixed")  # fixed, outside the window
        _touch(conn, "CB-3", "2026-08-01T12:00:00Z")

        out = findings.recent_findings(conn, since="2026-08-15", status="fixed")
        assert [f["id"] for f in out["findings"]] == ["CB-1"]
        assert out["total"] == 1

    def test_the_window_bound_is_inclusive(self, conn):
        """A row touched at exactly `since` is INSIDE the window. `>=`, never `>` —
        with a date-granular `since` the exclusive form silently drops the whole
        first day, and a net-delta indicator built on it would be quietly wrong."""
        _file_a_finding(conn, "CB-1", status="fixed")
        _touch(conn, "CB-1", "2026-08-20T12:00:00Z")

        assert [
            f["id"] for f in findings.recent_findings(
                conn, since="2026-08-20T12:00:00Z", status="fixed"
            )["findings"]
        ] == ["CB-1"]
        assert findings.recent_findings(conn, since="2026-08-20")["total"] == 1

    def test_the_status_filter_resolves_aliases_on_the_query_side(self, conn):
        """Both sides of the vocabulary, CB-19: `done` is stored as `fixed`, so a
        filter spelled `done` must find it rather than return an empty page."""
        _file_a_finding(conn, "CB-1", status="fixed")
        _touch(conn, "CB-1", "2026-08-20T12:00:00Z")
        out = findings.recent_findings(conn, since="2026-08-01", status="done")
        assert [f["id"] for f in out["findings"]] == ["CB-1"]
        assert out["status"] == "fixed"

    def test_an_empty_status_is_no_filter_and_a_wrong_typed_one_raises(self, conn):
        """CB-25, on the OPTIONAL half of this surface: `None`/`''` mean 'no filter',
        and everything else must reach the resolver rather than be skipped by
        truthiness — `status=0` returning the whole table is the defect."""
        _file_a_finding(conn, "CB-1", status="fixed")
        _touch(conn, "CB-1", "2026-08-20T12:00:00Z")
        _file_a_finding(conn, "CB-2")
        _touch(conn, "CB-2", "2026-08-20T12:00:00Z")

        assert findings.recent_findings(conn, since="2026-08-01", status="")["total"] == 2
        assert findings.recent_findings(conn, since="2026-08-01", status=None)["total"] == 2
        with pytest.raises(ValueError):
            findings.recent_findings(conn, since="2026-08-01", status=0)


class TestRecentMeasuresTheLastTouchNotTheClosure:
    """Acceptance 5. The caveat, pinned as BEHAVIOUR rather than as prose.

    `updated_at` is the time of the last WRITE to the row. There is no close
    timestamp anywhere in the schema, so `recent(status="fixed")` answers "cards
    that are fixed NOW and were touched since that date" — the error is one-sided:
    false positives are possible, misses are not.
    """

    def test_a_deduplicated_re_observation_does_not_make_a_live_card_look_closed(self, conn):
        """The bump moves `updated_at` without moving `status`. The card must stay
        out of a `status="fixed"` window — and the window is non-empty, so the
        assertion discriminates instead of passing vacuously on an empty page."""
        findings.add_finding(
            conn, severity="low", category="c", file="a.py",
            description="a repeating gate failure", fingerprint="fp-live",
            new_category=True,  # the CB-60 mint gate; explicit-id fixtures bypass it
        )
        _file_a_finding(conn, "CB-2", status="fixed")
        conn.execute("UPDATE findings SET updated_at = '2026-01-01T00:00:00Z'")
        conn.commit()

        again = findings.add_finding(
            conn, severity="low", category="c", file="a.py",
            description="a repeating gate failure", fingerprint="fp-live",
        )
        assert again["dedup_action"] == "bumped"
        assert again["status"] == "open"
        _touch(conn, "CB-2", "2026-08-20T12:00:00Z")

        out = findings.recent_findings(conn, since="2026-08-01", status="fixed")
        assert [f["id"] for f in out["findings"]] == ["CB-2"]

    def test_a_note_appended_today_resurfaces_a_card_closed_long_ago(self, conn):
        """THE executable form of "last touch is not the moment of closure": CB-1 was
        closed in January and only annotated today, yet a `since=today` window
        reports it. That is the documented false positive, and this test exists so a
        reader cannot mistake the tool for a close-time query."""
        from codebugs.types import utc_now

        _file_a_finding(conn, "CB-1", status="fixed")
        _touch(conn, "CB-1", "2026-01-05T12:00:00Z")

        findings.update_finding(conn, "CB-1", append_note="new evidence arrived")

        today = utc_now()[:10]
        out = findings.recent_findings(conn, since=today, status="fixed")
        assert [f["id"] for f in out["findings"]] == ["CB-1"]


class TestRecentDeclaresWhatItMeasures:
    """Acceptance 5, the declaration half: the caveat has to reach the CALLER, so it
    lives in the tool description on both surfaces — not only in the card. A tool
    that hands `updated_at` over as a close time is a success-shaped lie."""

    def test_the_mcp_description_names_the_column_and_denies_the_close_reading(self, conn):
        doc = _findings_mcp_tools(conn)["recent"].__doc__ or ""
        assert "updated_at" in doc
        assert "not the moment the finding was closed" in doc
        assert "append_note" in doc
        assert "one-sided" in doc.lower()

    def test_the_cli_help_carries_the_same_caveat(self):
        text = _findings_cli_parsers()["recent"].format_help()
        assert "updated_at" in text
        assert "not the moment the finding was closed" in text
        assert "append_note" in text


class TestRecentRefusesABadSince:
    """Acceptance 6. `since` is a MANDATORY filter, so the query-side convention
    ('' and None mean 'no filter') is exactly wrong here — there is no honest
    'everything' to fall back to. Wrong input must raise, and it must be refused by
    TYPE before anything is measured or compared: guarding with truthiness lets `0`
    reach SQLite, where `updated_at >= 0` matches every row and the caller reads an
    unfiltered table as a filtered one (CB-25/CB-82)."""

    @pytest.mark.parametrize(
        "bad",
        ["", "yesterday", 0, [], None, "2026-13-01", "2026-02-31", "26-08-20", "2026-08-20 12:00:00"],
        ids=["empty", "prose", "zero", "list", "none", "month13", "feb31", "shortyear", "spacesep"],
    )
    def test_a_since_that_is_not_a_date_raises(self, conn, bad):
        with pytest.raises(ValueError):
            findings.recent_findings(conn, since=bad)

    def test_a_refusal_writes_nothing_and_reads_nothing(self, recording):
        """Validated BEFORE anything is queried (CB-82), so a refusal costs no work."""
        with pytest.raises(ValueError):
            findings.recent_findings(recording, since="yesterday")
        assert not [s for s in recording.recorded_sql if "findings" in s]

    def test_the_cli_prints_one_line_and_exits_1(self, tmp_project, monkeypatch, capsys):
        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            ["codebugs", "--tracker-root", tmp_project, "recent", "--since", "yesterday"],
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert len(err.strip().splitlines()) == 1
        assert "since" in err


class TestAddLinesMetaConflict:
    """CB-129. `codebugs add -l "10-20" --meta '{"lines": [10, 20]}'` used to store
    ONLY the `--meta` value and report success: the handler seeded `meta["lines"]`
    from `-l` and then let `meta.update(json.loads(args.meta))` overwrite it. Two
    spellings of one fact, one silently chosen — the CB-15 class of success-shaped
    discard, reached through composition rather than through validation.

    The measured consequence was not hypothetical: the arch-health filing script
    passes BOTH `-l "a-b"` (a string) and `--meta '{"lines": [a, b], ...}'` (a list)
    on every one of its fifteen invocations, so its `-l` never landed once.

    The fix REFUSES the conflict rather than picking a winner, because both
    spellings target the same field and no "honour both" path exists (CB-28's rule:
    forward when a path exists, refuse only when none could). Applying `-l` last
    was rejected: it would silently INVERT the stored type (list -> string) for
    every existing caller, replacing one quiet data shift with another.

    These tests call `cli.main()` IN PROCESS on purpose. The defect lives in the
    seam between the parser and the handler; a unit test of a helper cannot see it.
    """

    def _run(self, tmp_project, monkeypatch, extra):
        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "codebugs", "--tracker-root", tmp_project, "add",
                "-s", "low", "-c", "c", "-f", "f.py", "-d", "something is wrong",
                "--new-category",
            ] + extra,
        )
        cli.main()

    def _stored_meta(self, tmp_project):
        conn = db.connect(tmp_project)
        try:
            rows = findings.query_findings(conn)["findings"]
            assert len(rows) == 1, rows
            return rows[0]["meta"] or {}
        finally:
            conn.close()

    def test_conflicting_spellings_are_refused_and_nothing_is_stored(
        self, tmp_project, monkeypatch, capsys
    ):
        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "codebugs", "--tracker-root", tmp_project, "add",
                "-s", "low", "-c", "c", "-f", "f.py", "-d", "something is wrong",
                "--new-category",
                "-l", "10-20", "--meta", '{"lines": [10, 20]}',
            ],
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1

        err = capsys.readouterr().err
        # No traceback: this is a refusal, not a crash.
        assert "Traceback" not in err
        # It must name BOTH spellings and the value each one carries, or the caller
        # cannot tell which of its two lines to delete.
        assert "--lines" in err
        assert "--meta" in err
        assert "10-20" in err
        assert "[10, 20]" in err

        # A refusal costs no partial work (CB-82): the row must not exist.
        conn = db.connect(tmp_project)
        try:
            assert findings.query_findings(conn)["findings"] == []
        finally:
            conn.close()

    def test_equal_values_are_not_a_conflict(self, tmp_project, monkeypatch, capsys):
        self._run(
            tmp_project, monkeypatch,
            ["-l", "10-20", "--meta", '{"lines": "10-20", "module": "m"}'],
        )
        assert "Added" in capsys.readouterr().out
        meta = self._stored_meta(tmp_project)
        assert meta["lines"] == "10-20"
        assert meta["module"] == "m"

    def test_lines_alone_lands(self, tmp_project, monkeypatch, capsys):
        """The direction that was DEAD in the live corpus. Without this the suite
        would pass on a fix that simply dropped `-l` on the floor."""
        self._run(tmp_project, monkeypatch, ["-l", "10-20"])
        assert "Added" in capsys.readouterr().out
        assert self._stored_meta(tmp_project)["lines"] == "10-20"

    def test_meta_lines_alone_lands(self, tmp_project, monkeypatch, capsys):
        """The other direction. Together with the previous test this is what makes
        the pair non-vacuous: a refusal that fired on either spelling ALONE would
        be caught here rather than mistaken for the fix."""
        self._run(tmp_project, monkeypatch, ["--meta", '{"lines": [10, 20]}'])
        assert "Added" in capsys.readouterr().out
        assert self._stored_meta(tmp_project)["lines"] == [10, 20]

    def test_meta_keys_that_no_flag_writes_are_untouched(
        self, tmp_project, monkeypatch, capsys
    ):
        """The check is scoped to keys a flag actually writes. A `--meta` payload
        that shares no key with any flag is not a conflict and must still merge."""
        self._run(
            tmp_project, monkeypatch,
            ["-l", "10-20", "--meta", '{"module": "m", "confidence": "high"}'],
        )
        assert "Added" in capsys.readouterr().out
        meta = self._stored_meta(tmp_project)
        assert meta["lines"] == "10-20"
        assert meta["module"] == "m"
        assert meta["confidence"] == "high"

    def test_every_declared_flag_is_a_real_argparse_dest_of_add(self):
        """The declaration and the parser must not drift. `_ADD_META_FLAGS` is what
        both the seeding and the conflict check read, so an entry naming a dest the
        `add` parser does not have would make the check silently unreachable — the
        'gate that cannot fire' shape this repo keeps rediscovering."""
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        findings.register_cli(sub, {})
        add_parser = sub.choices["add"]

        dests = {a.dest for a in add_parser._actions}
        assert findings._ADD_META_FLAGS, "the declaration must not be empty"
        for dest, _meta_key, spelling in findings._ADD_META_FLAGS:
            assert dest in dests, f"{dest!r} ({spelling}) is not an argument of `add`"
        assert "meta" in dests, "`--meta` itself must exist for the check to matter"


class TestAddMetaRefusalsAndEmptyFlags:
    """CB-132 + CB-133 — two residuals of CB-129, one handler, DIFFERENT mechanisms.

    CB-132: `--meta` had no arm over its `json.loads`, so a malformed payload left
    `codebugs add` as a RAW TRACEBACK (`json.JSONDecodeError`), and a well-formed
    but non-object one (`--meta '[1,2]'`) as `TypeError: cannot convert dictionary
    update sequence element #0` out of the `meta.update` below. CLAUDE.md names
    this class outright under Error handling: a handler that catches NOTHING breaks
    the rule exactly as surely as one that catches in the wrong order (CB-19/CB-79,
    where an unknown `--status` printed a traceback and leaked the connection).

    CB-133: the flag-seeded meta was guarded by TRUTHINESS (`if value:`), so an
    explicitly typed `-l ""` landed nowhere, counted as no conflict, and reported
    success — CB-129's own success-shaped-discard class surviving on the empty
    string. A CLI flag is a WRITE path, so CB-82 applies (`None` is the only "not
    supplied"), not CB-25 (where `""` legitimately means "no filter"). The write
    side of this repo already answers what `""` should then do: `bench._require_text`
    REFUSES it rather than defaulting or storing it.

    These call `cli.main()` IN PROCESS on purpose, like `TestAddLinesMetaConflict`
    above: both defects live in the seam between the parser and the handler, which
    a unit test of a helper cannot see.
    """

    def _argv(self, tmp_project, extra):
        return [
            "codebugs", "--tracker-root", tmp_project, "add",
            "-s", "low", "-c", "c", "-f", "f.py", "-d", "something is wrong",
            "--new-category",
        ] + extra

    def _run(self, tmp_project, monkeypatch, extra):
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, extra))
        cli.main()

    def _rows(self, tmp_project):
        conn = db.connect(tmp_project)
        try:
            return findings.query_findings(conn)["findings"]
        finally:
            conn.close()

    # ---- CB-132: the SHAPE of the refusal for a bad --meta -------------------

    def test_non_json_meta_refuses_instead_of_printing_a_traceback(
        self, tmp_project, monkeypatch, capsys
    ):
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, ["--meta", "не json"]))
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "--meta" in err
        # The offending text must be named, or the caller cannot see WHICH of its
        # arguments the parser choked on.
        assert "не json" in err

    def test_a_json_array_meta_refuses(self, tmp_project, monkeypatch, capsys):
        """Well-formed JSON, wrong SHAPE. This is the arm the `json.JSONDecodeError`
        one cannot cover: the payload parses and then kills `dict.update`."""
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, ["--meta", "[1,2]"]))
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "--meta" in err
        assert "list" in err
        assert "[1,2]" in err

    def test_a_json_scalar_meta_refuses(self, tmp_project, monkeypatch, capsys):
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, ["--meta", '"text"']))
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "str" in err

    def test_a_json_null_meta_refuses(self, tmp_project, monkeypatch, capsys):
        """`json.loads("null")` is `None`, which `dict.update` also refuses — and
        which a truthiness guard would have quietly read as "no --meta at all"."""
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, ["--meta", "null"]))
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "NoneType" in err

    def test_an_empty_meta_refuses(self, tmp_project, monkeypatch, capsys):
        """`--meta ""` is a SUPPLIED empty document, not an absent argument — the
        same split `bench.py` draws between `csv_data=None` and `csv_data=""`. It
        used to be discarded by `if args.meta` and reported as success."""
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, ["--meta", ""]))
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "--meta" in err
        assert "empty" in err

    def test_a_refused_meta_costs_no_partial_work(self, tmp_project, monkeypatch, capsys):
        """CB-82: a refusal must land nothing. The parse already runs before
        `db.connect()`; this pins that it stays there."""
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, ["--meta", "[1,2]"]))
        with pytest.raises(SystemExit):
            cli.main()
        capsys.readouterr()
        assert self._rows(tmp_project) == []

    def test_a_valid_object_meta_still_lands(self, tmp_project, monkeypatch, capsys):
        """Control. Without it the set is one-sided and a fix that refused EVERY
        `--meta` would pass."""
        self._run(tmp_project, monkeypatch, ["--meta", '{"module": "m"}'])
        assert "Added" in capsys.readouterr().out
        assert self._rows(tmp_project)[0]["meta"]["module"] == "m"

    # ---- CB-133: an explicitly typed empty flag must not vanish --------------

    def test_an_empty_lines_flag_refuses(self, tmp_project, monkeypatch, capsys):
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, ["-l", ""]))
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "--lines" in err
        assert "empty" in err
        assert self._rows(tmp_project) == []

    def test_an_empty_lines_flag_refuses_even_when_meta_carries_lines(
        self, tmp_project, monkeypatch, capsys
    ):
        """The nastiest spelling of CB-133: under the old truthiness guard the empty
        `-l` was dropped, so this was not even a CB-129 conflict — `--meta` won and
        the call reported success over a discarded argument."""
        from codebugs import cli

        monkeypatch.setattr(
            sys, "argv",
            self._argv(tmp_project, ["-l", "", "--meta", '{"lines": "10-20"}']),
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "--lines" in err
        assert self._rows(tmp_project) == []

    def test_a_lines_flag_with_a_value_still_lands(self, tmp_project, monkeypatch, capsys):
        """Control, the other side of the empty-string predicate."""
        self._run(tmp_project, monkeypatch, ["-l", "10-20"])
        assert "Added" in capsys.readouterr().out
        assert self._rows(tmp_project)[0]["meta"]["lines"] == "10-20"

    def test_an_add_naming_neither_flag_still_lands(self, tmp_project, monkeypatch, capsys):
        """Control: `None` really is "not supplied" and must stay silent."""
        self._run(tmp_project, monkeypatch, [])
        assert "Added" in capsys.readouterr().out
        # `--new-category` stamps `category_minted` (CB-60), so the row's meta is
        # not empty — what must be absent is any key the two flags write.
        assert "lines" not in (self._rows(tmp_project)[0]["meta"] or {})

    # ---- the arm must stay NARROW -------------------------------------------

    def test_a_bump_over_corrupt_stored_meta_still_crashes(
        self, tmp_project, monkeypatch, capsys
    ):
        """Pins behaviour the fix deliberately PRESERVES, so it passes on both sides
        of the change — it exists to refuse a BROAD `try` around the whole handler.

        `add_finding` raises `json.JSONDecodeError` from `_bump_row`'s pre-write
        parse of a MATCHED row's stored meta: that is corruption, not bad input,
        and folding it into the new `--meta` arm would print a tidy usage error for
        a state no caller typed — the CB-15/CB-16 lie, and the twin of
        `TestRetriageCliContract::test_a_committed_write_is_never_reported_as_bad_input`.
        """
        from codebugs import cli

        self._run(tmp_project, monkeypatch, [])
        capsys.readouterr()

        conn = db.connect(tmp_project)
        conn.execute("UPDATE findings SET meta = ?", ("{not json",))
        conn.commit()
        conn.close()

        monkeypatch.setattr(sys, "argv", self._argv(tmp_project, []))
        with pytest.raises(json.JSONDecodeError):
            cli.main()


# ---------------------------------------------------------------------------
# CB-144 + BT-8 (unit T-41)
# ---------------------------------------------------------------------------


def _git_repo_with_one_commit(path: str) -> str:
    """Make `path` a real repository with one commit; return that commit's SHA.

    A real repo rather than a stubbed `git_rev_parse`: the defect lives in the
    seam between the CLI parser, the handler and the ambient tree, and a stub
    for the very call that was missing cannot see a missing call.
    """
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.path.join(path, ".gitconfig-absent"),
        "GIT_CONFIG_SYSTEM": os.path.join(path, ".gitconfig-absent"),
    }
    subprocess.run(["git", "init", "-q", path], check=True, env=env)
    with open(os.path.join(path, "seed.txt"), "w") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "-C", path, "add", "seed.txt"], check=True, env=env)
    subprocess.run(["git", "-C", path, "commit", "-qm", "seed"], check=True, env=env)
    return subprocess.check_output(
        ["git", "-C", path, "rev-parse", "HEAD"], encoding="utf-8", env=env
    ).strip()


class TestCliAddCapturesTheRevisionItWasFiledAt:
    """CB-144. A card filed through `codebugs add` carried `reported_at_commit =
    NULL` forever: HEAD auto-capture lived ONLY in the two MCP wrappers, and
    `_cmd_add` never mentioned the column. That is why the card is `high` rather
    than `medium` — BT-7's ratified location anchor is keyed on the frozen
    commit (reverse blame starts there, and the content channel reads the anchor
    text out of the object store AT that revision), so a card with no commit is
    unanchorable by EITHER channel. The anchor's coverage ceiling was therefore
    set by the FILING SURFACE rather than by the mechanism.

    The card's own premise about the fix is WRONG and was corrected before this
    was written: it says "the domain default is `git_rev_parse("HEAD")` only
    when the argument is omitted at the DOMAIN layer". There is no such domain
    default — `add_finding` never calls `git_rev_parse` and stores what it is
    handed, `None` included. So the fix could not be "reach the domain path";
    the domain path does not exist.

    These call `cli.main()` IN PROCESS, like the CB-129 tests above and for the
    same reason: the defect is in the seam between the parser and the handler.
    """

    def _add_via_cli(self, tmp_project, monkeypatch, description="a cli-filed defect"):
        from codebugs import cli

        monkeypatch.setattr(
            sys, "argv",
            [
                "codebugs", "--tracker-root", tmp_project, "add",
                "-s", "low", "-c", "cli_capture", "-f", "f.py",
                "-d", description, "--new-category",
            ],
        )
        cli.main()

    def _only_row(self, tmp_project):
        conn = db.connect(tmp_project)
        try:
            rows = findings.query_findings(conn)["findings"]
            assert len(rows) == 1, rows
            return rows[0]
        finally:
            conn.close()

    def test_a_cli_filed_card_carries_the_head_it_was_filed_at(
        self, tmp_project, monkeypatch
    ):
        head = _git_repo_with_one_commit(tmp_project)
        monkeypatch.chdir(tmp_project)
        self._add_via_cli(tmp_project, monkeypatch)
        assert self._only_row(tmp_project)["reported_at_commit"] == head

    def test_outside_a_repository_the_column_stays_null_rather_than_lying(
        self, tmp_project, monkeypatch
    ):
        """The capture is `silent=True`: git being unable to answer must leave
        the column NULL, exactly as the MCP wrappers already behave. A capture
        that invented a value would be worse than the absence it replaces."""
        monkeypatch.chdir(tmp_project)  # tmp_project is NOT a git repository here
        self._add_via_cli(tmp_project, monkeypatch)
        assert self._only_row(tmp_project)["reported_at_commit"] is None

    def test_the_mcp_add_wrapper_captures_exactly_what_it_captured_before(
        self, tmp_project, monkeypatch
    ):
        """Control case, and the §2.5 invariance proof: routing the MCP wrapper
        through the shared helper must capture the SAME value at the SAME moment.
        This test passes before AND after the fix, deliberately — it pins
        behaviour the change preserves, which is why the name says so."""
        head = _git_repo_with_one_commit(tmp_project)
        monkeypatch.chdir(tmp_project)
        conn = db.connect(tmp_project)
        try:
            tools = _findings_mcp_tools(conn)
            res = tools["add"](
                severity="low", category="mcp_capture", file="f.py",
                description="an mcp-filed defect", new_category=True,
            )
            assert res["reported_at_commit"] == head
        finally:
            conn.close()

    def test_the_mcp_batch_add_wrapper_is_unchanged_too(self, tmp_project, monkeypatch):
        """Second control. `batch_add` carried its own copy of the same two
        lines; collapsing both copies into one helper must not move either."""
        head = _git_repo_with_one_commit(tmp_project)
        monkeypatch.chdir(tmp_project)
        conn = db.connect(tmp_project)
        try:
            tools = _findings_mcp_tools(conn)
            res = tools["batch_add"](
                findings=[{
                    "severity": "low", "category": "mcp_capture", "file": "f.py",
                    "description": "a batch-filed defect",
                }],
                new_category=True,
            )
            assert len(res) == 1, res
            row = findings.query_findings(conn)["findings"][0]
            assert row["reported_at_commit"] == head
        finally:
            conn.close()

    def test_an_import_never_acquires_the_local_head(self, tmp_project, monkeypatch):
        """The trap that DEFINES the shape of this fix (§2.3). An import is not
        an observation (CB-51) — that is why it already passes `annotate=False`,
        `escalate=False` and `promote_tags=False`. Stamping the LOCAL HEAD onto a
        row that came from someone ELSE's tracker would be a confidently wrong
        answer, so the obvious "make the state unrepresentable" move — capture
        inside `add_finding` — is a regression here, not a hardening."""
        _git_repo_with_one_commit(tmp_project)
        monkeypatch.chdir(tmp_project)
        conn = db.connect(tmp_project)
        try:
            report = findings.import_findings(conn, [{
                "id": "CB-9001", "severity": "low", "category": "foreign",
                "file": "peer.py", "description": "a peer tracker's row",
            }])
            assert report.imported == 1, report
            row = findings.query_findings(conn)["findings"][0]
            assert row["reported_at_commit"] is None
        finally:
            conn.close()

    def test_a_restore_never_acquires_the_local_head(self, tmp_project, monkeypatch):
        """Second half of the same trap. `restore_findings` (CB-97) exists to put
        a row back BYTE FOR BYTE, its own `reported_at_commit` included — and
        `None` is one of the values it must be able to restore."""
        _git_repo_with_one_commit(tmp_project)
        monkeypatch.chdir(tmp_project)
        conn = db.connect(tmp_project)
        try:
            row = {c: None for c in findings._RESTORE_COLUMNS}
            row.update({
                "id": "CB-4242", "severity": "low", "category": "restored",
                "file": "old.py", "status": "fixed", "description": "restored row",
                "source": "human", "tags": "[]", "meta": "{}",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "occurrence_count": 1,
            })
            report = findings.restore_findings(conn, [row])
            assert report.restored == 1, report
            stored = conn.execute(
                "SELECT reported_at_commit FROM findings WHERE id = 'CB-4242'"
            ).fetchone()
            assert stored["reported_at_commit"] is None
        finally:
            conn.close()


def _refusal_pair(conn):
    """A `wont_fix` card and the LIVE recurrence twin that holds its fingerprint.

    Built by the identity machinery itself rather than by hand: a hit on a
    `wont_fix` row files a NEW row (decision stays decided, CB-43), and that new
    row derives the SAME `auto:v1` fingerprint from the same inputs. Re-triaging
    the decided card back to a live status is therefore exactly the state the
    pre-check in `update_finding` exists to refuse.
    """
    text = "the very same defect, observed twice"
    a = findings.add_finding(conn, severity="low", category="fp_fork", file="f.py",
                             description=text, new_category=True)
    findings.update_finding(conn, a["id"], status="wont_fix")
    b = findings.add_finding(conn, severity="low", category="fp_fork", file="f.py",
                             description=text)
    assert a["id"] != b["id"], "expected a recurrence row, not a bump"
    return a["id"], b["id"]


class TestFingerprintRefusalIsCountable:
    """BT-8, the single mechanical unit of the ratified decision: the dedup fork
    is LEFT AS IT IS and its refusals are COUNTED. Policy by measured demand —
    the same move the `moved_file` counter makes. No merge policy is built here,
    no `merged` status, no fingerprint backfill; CB-46 stays open, waiting on
    data rather than on code.

    The refusal being counted is `update_finding`'s pre-check: re-triaging a
    decided card back to a live status while a live recurrence already holds its
    fingerprint. Without that pre-check the write would hit
    `ux_findings_fingerprint_live` and surface as a raw `IntegrityError` —
    outside the module's `ValueError`/`KeyError` contract.
    """

    def test_the_refusal_still_names_the_blocking_card(self, conn):
        """Constraint 1: a refusal stays a refusal. Same exception type, same
        message, still naming the row that blocks. Passes before AND after —
        it pins what the counter must not be allowed to change."""
        a_id, b_id = _refusal_pair(conn)
        with pytest.raises(ValueError) as exc:
            findings.update_finding(conn, a_id, status="open")
        assert "its fingerprint is held by" in str(exc.value)
        assert b_id in str(exc.value)

    def test_two_refusals_in_a_row_count_two(self, conn):
        """CONSTRAINT 2, and the central trap of this unit. A counter written
        inside `with db.txn(conn):` is invisible BY CONSTRUCTION — the `raise`
        rolls the transaction back and takes the count with it. So the assertion
        is not "a row was written" but "after two refusals the count is 2",
        read AFTER the exception has left the frame."""
        a_id, _ = _refusal_pair(conn)
        for _ in range(2):
            with pytest.raises(ValueError):
                findings.update_finding(conn, a_id, status="open")
        stored = findings.get_finding(conn, a_id)
        assert stored["meta"].get(findings.REFUSAL_COUNT_META_KEY) == 2

    def test_the_count_is_readable_through_the_existing_query_surface(self, conn):
        """Constraint 4: `query(meta_key=…)`. No new MCP tool and no new CLI
        verb — the wire golden must not move by a single byte, because DIR-1's
        surface-generator pilot is measuring byte-equality of that file right
        now and a stray tool would corrupt someone else's measurement."""
        a_id, b_id = _refusal_pair(conn)
        with pytest.raises(ValueError):
            findings.update_finding(conn, a_id, status="open")
        hits = findings.query_findings(
            conn, meta_key=findings.REFUSAL_COUNT_META_KEY
        )["findings"]
        assert [r["id"] for r in hits] == [a_id], hits
        # The stamp lands on the REFUSED card, not on the blocking one: the demand
        # this number answers is "somebody wanted THIS card back in play".
        assert findings.get_finding(conn, b_id)["meta"].get(
            findings.REFUSAL_COUNT_META_KEY
        ) is None

    def test_a_broken_counter_never_masks_or_softens_the_refusal(
        self, conn, monkeypatch, capsys
    ):
        """Constraint 3. The counter is fail-OPEN (losing one unit of statistics
        is acceptable); the refusal is fail-CLOSED (never). Swallow and log, in
        that direction and never the other — the `run_status_change_hooks`
        precedent."""
        a_id, b_id = _refusal_pair(conn)

        def boom(*_a, **_k):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(findings, "_bump_refusal_count", boom)
        with pytest.raises(ValueError) as exc:
            findings.update_finding(conn, a_id, status="open")
        assert "its fingerprint is held by" in str(exc.value)
        assert b_id in str(exc.value)
        assert "disk I/O error" in capsys.readouterr().err

    def test_an_ambient_transaction_that_commits_still_carries_no_count(
        self, tmp_project
    ):
        """CONSTRAINT 5, and the fixture here had to be chosen by measurement.

        `update_finding` may run inside a caller's open transaction, where
        `db.txn` yields False and commits nothing. On that path the counter is
        SKIPPED: statistics must never ride along inside a stranger's unit of
        work, whose fate they would then share and whose commit they would be
        part of. Fail-open for the counter, always.

        THE DISCRIMINATING CALLER IS ONE THAT COMMITS, and the first version of
        this test used one that rolls back — where it could not fail. Measured
        against a mutant with the `conn.in_transaction` guard deleted: with a
        rolling-back caller the count is lost either way (the caller's own frame
        undoes it), so the test passed on the mutant; with a caller that swallows
        the `ValueError` and commits, the unguarded write LANDS, count = 1. Only
        the second shape sees the guard at all.

        Honest scope, enumerated rather than assumed: this path has NO PRODUCER
        today. All four callers of `update_finding` were read. The two that run
        under an ambient transaction pass a TERMINAL status
        (`milestones.triage_dismiss` → the literal `"not_a_bug"`;
        `provenance.resolve_trailers` → `"resolved"`/`None` out of
        `_VERB_ACTIONS`), and the refusal predicate fires only on nonlive→live;
        the two that CAN pass a live status — the MCP `update` wrapper and the
        CLI `update` handler — each open their own connection with no
        transaction open. So this builds the caller by hand: it is the guard for
        the day one appears, not a reproduction of a live route.
        """
        conn = db.connect(tmp_project)
        observer = db.connect(tmp_project)
        try:
            a_id, b_id = _refusal_pair(conn)
            with db.txn(conn):
                conn.execute(
                    "UPDATE findings SET severity = 'high' WHERE id = ?", (b_id,)
                )
                with pytest.raises(ValueError):
                    findings.update_finding(conn, a_id, status="open")
                # ...and the caller carries on and COMMITS its own work.
            seen = observer.execute(
                "SELECT severity FROM findings WHERE id = ?", (b_id,)
            ).fetchone()
            assert seen["severity"] == "high", "the caller's own work must still land"
            assert findings.query_findings(
                observer, meta_key=findings.REFUSAL_COUNT_META_KEY
            )["findings"] == [], "the count must not have ridden along on that commit"
        finally:
            conn.close()
            observer.close()

    def test_an_ambient_transaction_that_aborts_commits_nothing_foreign(
        self, tmp_project
    ):
        """The other half, and it PASSES ON BOTH SIDES of the guard by design —
        said so in the name's neighbourhood rather than left to look broken. It
        pins that letting the refusal escape a caller's transaction destroys the
        caller's work and nothing else: the counter never forces a commit, so
        `db.txn`'s reentrancy is what makes CB-40's actual defect
        (`isolation_level` committing an ambient transaction) unreachable here.
        """
        conn = db.connect(tmp_project)
        observer = db.connect(tmp_project)
        try:
            a_id, b_id = _refusal_pair(conn)
            with pytest.raises(ValueError):
                with db.txn(conn):
                    conn.execute(
                        "UPDATE findings SET severity = 'high' WHERE id = ?", (b_id,)
                    )
                    findings.update_finding(conn, a_id, status="open")
            seen = observer.execute(
                "SELECT severity FROM findings WHERE id = ?", (b_id,)
            ).fetchone()
            assert seen["severity"] == "low"
            assert findings.query_findings(
                observer, meta_key=findings.REFUSAL_COUNT_META_KEY
            )["findings"] == []
        finally:
            conn.close()
            observer.close()

    def test_the_refusal_does_not_move_updated_at(self, conn):
        """Constraint 6. CB-123 ratified `recent` as a reader over `updated_at`
        with the stated caveat that a last TOUCH is not a closure. A touch that
        changed nothing a reader of the card can see would make that reader
        LIER, not more accurate, so the counter deliberately leaves the column
        alone."""
        a_id, _ = _refusal_pair(conn)
        before = findings.get_finding(conn, a_id)["updated_at"]
        with pytest.raises(ValueError):
            findings.update_finding(conn, a_id, status="open")
        assert findings.get_finding(conn, a_id)["updated_at"] == before

    def test_an_ordinary_update_stamps_nothing(self, conn):
        """Control. The counter must be invisible on every path that does not
        refuse — including a terminal→live re-triage that legitimately succeeds
        because nothing holds the fingerprint."""
        solo = findings.add_finding(conn, severity="low", category="fp_fork",
                                    file="g.py", description="a lonely defect",
                                    new_category=True)
        findings.update_finding(conn, solo["id"], status="wont_fix")
        reopened = findings.update_finding(conn, solo["id"], status="open")
        assert reopened["status"] == "open"
        assert findings.REFUSAL_COUNT_META_KEY not in reopened["meta"]
        assert findings.query_findings(
            conn, meta_key=findings.REFUSAL_COUNT_META_KEY
        )["findings"] == []

    def test_the_key_is_reserved_on_add_and_repairable_on_update(self, conn):
        """The `category_minted` precedent, chosen for the same two reasons: the
        key is the machinery's OUTPUT, so a caller supplying it at filing time
        would spoof a number that goes to the owner; and a permanently
        unrepairable stamp is the CB-26 shape, so `update(meta_update=)` must be
        able to rewrite a wrong one.

        CB-56: the ADD path no longer REFUSES this — it strips it with
        visibility instead, same as every other machinery-output key except
        `resolver_errors`. "Reserved on add" now means "cannot be spoofed",
        proven by the stripped value never landing, not by a raised error."""
        spoofed = findings.add_finding(
            conn, severity="low", category="fp_fork", file="h.py",
            description="spoofing the count", new_category=True,
            meta={findings.REFUSAL_COUNT_META_KEY: 99},
        )
        assert findings.REFUSAL_COUNT_META_KEY not in spoofed["meta"]
        assert findings.REFUSAL_COUNT_META_KEY in spoofed["stripped_meta_keys"]

        a_id, _ = _refusal_pair(conn)
        with pytest.raises(ValueError):
            findings.update_finding(conn, a_id, status="open")
        repaired = findings.update_finding(
            conn, a_id, meta_update={findings.REFUSAL_COUNT_META_KEY: 0}
        )
        assert repaired["meta"][findings.REFUSAL_COUNT_META_KEY] == 0

    def test_an_imported_row_does_not_carry_a_peers_count(self, conn):
        """The add-only reservation is also what strips the key on import: a
        peer tracker's refusal count is not this tracker's demand signal."""
        report = findings.import_findings(conn, [{
            "id": "CB-9002", "severity": "low", "category": "foreign",
            "file": "peer.py", "description": "a peer row carrying a count",
            "meta": json.dumps({findings.REFUSAL_COUNT_META_KEY: 17}),
        }])
        assert report.imported == 1, report
        assert findings.query_findings(
            conn, meta_key=findings.REFUSAL_COUNT_META_KEY
        )["findings"] == []


def _update_finding_call_sites() -> list[tuple[str, str, int, frozenset[str]]]:
    """Every `update_finding(...)` CALL in `src/codebugs/`, by AST.

    Returns `(module, enclosing function, lineno, keyword names)` per site.
    AST rather than text search is the point: this package's docstrings mention
    ``update_finding(meta_update=)`` several times, and `tests/test_fsio.py::
    TestWriteCallSitesRatchet` records that the first, grep-shaped version of
    that ratchet matched its own prose. A parser cannot make that mistake.
    """
    pkg = pathlib.Path(findings.__file__).parent
    sites: list[tuple[str, str, int, frozenset[str]]] = []

    def called_name(node: ast.Call) -> str | None:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def descend(node: ast.AST, module: str, scope: str) -> None:
        # The NEAREST enclosing function, so the two surface handlers report as
        # `update` / `_cmd_update` and not as the `register_*` frames they nest in.
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else scope
            )
            if isinstance(child, ast.Call) and called_name(child) == "update_finding":
                sites.append((
                    module,
                    scope,
                    child.lineno,
                    frozenset(kw.arg for kw in child.keywords if kw.arg),
                ))
            descend(child, module, inner)

    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        descend(tree, str(path.relative_to(pkg)), "<module>")
    return sites


class TestUpdateFindingCallSitesRatchet:
    """The caller enumeration behind `_count_fingerprint_refusal` is PINNED (CB-131).

    That docstring argues "this skip path has no producer today" from a reading of
    every caller. An enumeration is the letter of a rule, and this repository's
    recurring lesson is that the letter goes stale: the previous version said "all
    four callers of `update_finding` were read" while there were FIVE. The fifth,
    `loc._apply_recapture`, arrived by FORWARD MERGE from a sibling branch AFTER the
    enumeration was made — the conclusion survived, its stated reason did not, and a
    green suite certified the false reason all the way to review.

    So the enumeration is mechanised: a new, moved, or renamed call site turns this
    RED, which forces the reasoning to be replayed instead of silently inherited.
    That is the mechanism, not the reminder — prose is the wrong enforcement layer
    for a premise whose defining property is that a merge can invalidate it without
    touching the file that states it.
    """

    def test_the_count_and_the_locations_are_exactly_the_five_that_were_read(self):
        located = sorted((module, func) for module, func, _, _ in _update_finding_call_sites())
        expected = sorted([
            ("findings.py", "update"),                    # MCP wrapper — own connection
            ("findings.py", "_cmd_update"),               # CLI handler — own connection
            ("loc.py", "_apply_recapture"),               # AMBIENT — passes no status
            ("milestones/triage.py", "triage_dismiss"),   # AMBIENT — terminal status
            ("provenance.py", "resolve_trailers"),        # NOT ambient (measured)
        ])
        assert located == expected, (
            "the `update_finding` caller set moved. `_count_fingerprint_refusal`'s "
            "docstring reasons over exactly these five sites — replay that reasoning "
            f"against the new set, correct the docstring, then this test. Found: {located}"
        )

    def test_neither_ambient_caller_can_request_a_live_status(self):
        """The two REASONS, mechanised — they differ, and that is the trap.

        `triage_dismiss` passes a status and it is terminal; `_apply_recapture`
        passes no status at all. Collapsing them into one clause ("both pass a
        terminal status") is precisely how the previous docstring became wrong,
        so each is pinned on its own property.
        """
        by_func = {func: kwargs for _, func, _, kwargs in _update_finding_call_sites()}

        assert "status" not in by_func["_apply_recapture"], (
            "`loc._apply_recapture` runs under the caller's `db.txn` and now names a "
            "`status`. The refusal predicate needs one, so this is a candidate "
            "producer for the skip path — re-read _count_fingerprint_refusal."
        )
        assert "status" in by_func["triage_dismiss"]

        source = pathlib.Path(findings.__file__).parent / "milestones" / "triage.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        statuses = [
            kw.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
            ) == "update_finding"
            for kw in node.keywords
            if kw.arg == "status"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ]
        assert statuses == ["not_a_bug"], statuses
        assert set(statuses) <= FINDING_TERMINAL, (
            "`milestones.triage_dismiss` runs under an ambient transaction and no "
            "longer passes a literal TERMINAL status; a live one would make it a "
            "producer for the skip path documented on _count_fingerprint_refusal."
        )


class TestGroupingAxes:
    """CB-62 item (1): `query`/`stats` group by TAG and by a top-level `meta` key.

    Both axes are MULTI-SOURCE in a way the five column axes are not, and the
    tests below exist to pin the two places where a right-looking answer is a
    wrong one: a tagged row belongs to several groups at once (so the counts stop
    being a partition of the population), and a row carrying no value on the axis
    belongs to NONE (so it vanishes from the result entirely unless something
    counts it).
    """

    @staticmethod
    def _file(conn, fid, *, tags=None, meta=None, severity="medium", status=None):
        findings.add_finding(
            conn,
            severity=severity,
            category="perf",
            file=f"{fid}.py",
            description=f"desc {fid}",
            tags=tags,
            meta=meta,
            finding_id=fid,
        )
        if status:
            findings.update_finding(conn, finding_id=fid, status=status)

    # --- axis: tag ---------------------------------------------------------

    def test_a_two_tag_row_lands_in_two_groups(self, conn):
        """Order inside the stored array must not create groups.

        MUTANT this kills: grouping by the raw `tags` COLUMN. `["a","b"]` and
        `["b","a"]` are two distinct strings, so the mutant reports two groups of
        one where the truth is two groups of two.
        """
        self._file(conn, "CB-1", tags=["a", "b"])
        self._file(conn, "CB-2", tags=["b", "a"])
        result = findings.query_findings(conn, group_by="tag")
        groups = {g["group_key"]: g["count"] for g in result["groups"]}
        assert groups == {"a": 2, "b": 2}
        assert result["multi_group_rows"] == 2
        assert result["population"] == 2

    def test_an_untagged_row_is_counted_rather_than_dropped(self, conn):
        """`json_each` over an empty array yields nothing, so the row leaves no
        trace in the groups. MUTANT this kills: dropping `ungrouped_rows`."""
        self._file(conn, "CB-1", tags=["a"])
        self._file(conn, "CB-2", tags=[])
        self._file(conn, "CB-3", tags=None)
        result = findings.query_findings(conn, group_by="tag")
        assert [g["group_key"] for g in result["groups"]] == ["a"]
        assert result["population"] == 3
        assert result["ungrouped_rows"] == 2
        assert result["multi_group_rows"] == 0

    def test_a_tag_repeated_inside_one_row_counts_once(self, conn):
        """MUTANT this kills: `COUNT(*)` instead of `COUNT(DISTINCT id)`. The
        stored array is the caller's; nothing forbids `["a","a"]`, and two
        shipped tools reporting different totals for it is the divergence this
        axis exists to avoid."""
        self._file(conn, "CB-1", tags=["a", "a"])
        result = findings.query_findings(conn, group_by="tag")
        assert [(g["group_key"], g["count"]) for g in result["groups"]] == [("a", 1)]

    def test_the_numbers_agree_with_grouping_tags(self, conn):
        """The parity pin. `grouping.tag_report` deduplicates within a row in
        Python; this axis must reach the same totals from SQL, or one corpus has
        two shipped answers."""
        from codebugs import grouping

        self._file(conn, "CB-1", tags=["a", "b"])
        self._file(conn, "CB-2", tags=["a", "a"])
        self._file(conn, "CB-3", tags=[])
        self._file(conn, "CB-4", tags=["b", "c"])
        report = grouping.tag_report(conn, status="open")
        theirs = {t["tag"]: t["count"] for t in report["tags"]}
        mine = {
            g["group_key"]: g["count"]
            for g in findings.query_findings(conn, status="open", group_by="tag")["groups"]
        }
        assert mine == theirs
        assert findings.query_findings(conn, status="open", group_by="tag")[
            "ungrouped_rows"
        ] == report["rows_untagged"]

    def test_the_tag_axis_composes_with_the_other_filters(self, conn):
        """The whole reason this axis is not a duplicate of `grouping-tags`:
        that tool takes only `status` and `category`."""
        self._file(conn, "CB-1", tags=["a"], severity="critical")
        self._file(conn, "CB-2", tags=["a", "b"], severity="low")
        result = findings.query_findings(conn, severity="critical", group_by="tag")
        assert {g["group_key"]: g["count"] for g in result["groups"]} == {"a": 1}
        assert result["population"] == 1

    def test_both_axes_survive_every_filter_at_once(self, conn):
        """PARAMETER ORDER, which only a FILTERED query can test.

        The meta branch splices its bound path around the caller's WHERE values
        — two placeholders before them, one after — and this file already
        records what happens when such a splice is done as a block instead: the
        values bind to the wrong placeholders and *filtered* queries are
        corrupted while every unfiltered test keeps passing (CB-20, the
        severity-rank CASE). The control below is the same filter set with no
        axis: the grouped population must equal it, or the grouping path is
        selecting a different set of rows than the caller asked for."""
        findings.add_finding(
            conn,
            severity="critical",
            category="perf",
            file="target.py",
            description="d1",
            finding_id="CB-1",
            tags=["x", "y"],
            meta={"k": "v", "other": 1},
            source="ruff",
            reported_at_commit="abc123",
            reported_at_ref="v1",
        )
        self._file(conn, "CB-2", tags=["x"], meta={"k": "w"})
        self._file(conn, "CB-3", tags=["x"], meta={"k": "v"}, severity="critical")
        filters = dict(
            ids=["CB-1", "CB-2", "CB-3"],
            status="open",
            severity="critical",
            category="perf",
            file="target",
            source="ruff",
            tag="x",
            meta_key="other",
            commit="abc",
            ref="v1",
        )
        control = findings.query_findings(conn, **filters)
        assert [f["id"] for f in control["findings"]] == ["CB-1"]
        by_meta = findings.query_findings(conn, group_by="meta:k", **filters)
        assert by_meta["population"] == control["total"]
        assert [dict(g) for g in by_meta["groups"]] == [{"group_key": "v", "count": 1}]
        by_tag = findings.query_findings(conn, group_by="tag", **filters)
        assert by_tag["population"] == control["total"]
        assert {g["group_key"] for g in by_tag["groups"]} == {"x", "y"}
        assert by_tag["multi_group_rows"] == 1

    def test_a_row_whose_tags_do_not_parse_is_ungrouped_not_fatal(self, conn):
        """PREMISE PIN, not a feature. `json_each` RAISES on malformed JSON, so
        one hand-edited row could abort the whole report; the guard has to be
        evaluated BEFORE the table-valued function sees the value, and SQLite is
        free to flatten the subquery that carries it. If a future SQLite reorders
        that, this test goes red instead of the report dying in a user's hands.
        `parse_tags` degrades the same three states to no tags at all, which is
        why they are `ungrouped_rows` rather than a fourth counter."""
        self._file(conn, "CB-1", tags=["a"])
        self._file(conn, "CB-2", tags=["b"])
        conn.execute("UPDATE findings SET tags = 'notjson' WHERE id = 'CB-2'")
        conn.commit()
        result = findings.query_findings(conn, group_by="tag")
        assert [g["group_key"] for g in result["groups"]] == ["a"]
        assert result["ungrouped_rows"] == 1

    def test_known_limit_the_existing_tag_FILTER_is_still_fatal_on_such_a_row(self, conn):
        """The honest scope of the test above, pinned so the claim cannot rot.

        The GROUPING path guards itself. The pre-existing `tag=` FILTER does not:
        it is a bare `EXISTS (SELECT 1 FROM json_each(tags) ...)`, with no
        `json_valid` guard — unlike the `commit` filter three conditions below it,
        which has carried one all along. So one hand-edited row aborts any query
        that uses `tag=`, with or without an axis, and the axis neither caused
        that nor repairs it.

        Deliberately NOT fixed here: this unit is forbidden from touching the
        existing filters, and the reason is the same one that makes the dotted
        meta key a refusal — the population depending on current filter behaviour
        is not measured. Pinned as a KNOWN LIMIT, the shape `TestKnownLimits`
        uses for the harness: the day this stops raising, someone re-reads this
        instead of trusting a stale sentence."""
        self._file(conn, "CB-1", tags=["a"])
        self._file(conn, "CB-2", tags=["b"])
        conn.execute("UPDATE findings SET tags = 'notjson' WHERE id = 'CB-2'")
        conn.commit()
        with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
            findings.query_findings(conn, tag="a")
        with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
            findings.query_findings(conn, tag="a", group_by="tag")

    # --- axis: meta:<key> --------------------------------------------------

    def test_a_non_string_tag_element_is_dropped_exactly_as_parse_tags_drops_it(self, conn):
        """MUTANT this kills: removing `je.type = 'text'` from the join.

        Found by mutation probe, not by design — the first version of this class
        left that clause unpinned, so the axis could have started counting a
        numeric element as a tag while `grouping-tags` silently dropped it, and
        the parity claim in `_membership_sql`'s docstring would have been prose
        with nothing behind it. The state needs a hand-edited row because the
        write path type-checks tag members (CB-82), which is exactly why it is
        worth a test: it is reachable only from a foreign or repaired row, i.e.
        the case nobody exercises by accident."""
        from codebugs import grouping

        self._file(conn, "CB-1", tags=["c"])
        self._file(conn, "CB-2", tags=["c"])
        conn.execute("""UPDATE findings SET tags = '[1, 2, "c"]' WHERE id = 'CB-2'""")
        conn.commit()
        result = findings.query_findings(conn, status="open", group_by="tag")
        assert [(g["group_key"], g["count"]) for g in result["groups"]] == [("c", 2)]
        assert result["ungrouped_rows"] == 0
        report = grouping.tag_report(conn, status="open")
        assert {t["tag"]: t["count"] for t in report["tags"]} == {"c": 2}

    def test_a_NaN_written_by_the_ordinary_write_path_does_not_hide_a_row(self, conn):
        """THE BLOCKING FINDING of this unit's adversarial review, and it broke
        the one claim the whole axis was built to make.

        `json_valid(X)` with no flags means canonical RFC-8259, which rejects
        `NaN`/`Infinity`. Python's `json.loads` accepts them and `json.dumps`
        WRITES them by default — so this package's own `add_finding` stores
        `{"x": NaN}`, and CLAUDE.md's CB-82 entry ratifies exactly that value as
        supported. The guard was therefore STRICTER THAN THE ENGINE IT GUARDS:
        the row vanished from every meta axis — including for a sibling key
        holding a perfectly good string — while `grouping.tag_report` counted it,
        because that goes through `json.loads`. Two shipped tools, one corpus,
        different answers: the exact divergence this axis exists to prevent,
        reintroduced by its own safety check.

        MUTANT this kills: dropping the `_JSON5` flag from either guard."""
        from codebugs import grouping

        self._file(conn, "CB-1", tags=["realtag"], meta={"found_by": "ruff"})
        self._file(conn, "CB-2", tags=["realtag"], meta={"found_by": "ruff", "x": float("nan")})
        stored = conn.execute("SELECT meta FROM findings WHERE id = 'CB-2'").fetchone()["meta"]
        assert "NaN" in stored, f"premise: the write path stores NaN, got {stored!r}"

        by_meta = findings.query_findings(conn, group_by="meta:found_by")
        assert {g["group_key"]: g["count"] for g in by_meta["groups"]} == {"ruff": 2}
        assert by_meta["ungrouped_rows"] == 0

        by_tag = findings.query_findings(conn, status="open", group_by="tag")
        report = grouping.tag_report(conn, status="open")
        assert {g["group_key"]: g["count"] for g in by_tag["groups"]} == {
            t["tag"]: t["count"] for t in report["tags"]
        }
        assert by_tag["ungrouped_rows"] == report["rows_untagged"]

    def test_a_control_character_in_a_meta_key_is_refused_as_input(self, conn):
        """Adversarial review. SQLite's path is a C string, so it TRUNCATES at a
        NUL: measured, `json_extract('{"a":"WRONG_A"}', '$.a\\0b')` answers
        `'WRONG_A'` — the key `a\\0b` silently reads its neighbour `a`, which is
        the dotted-key failure exactly. A LEADING NUL is worse still: the path
        collapses to `'$.'` and SQLite raises `OperationalError`, the
        environmental class the EMPTY key is already refused to avoid, escaping a
        domain function that promises `ValueError` and reaching the CLI as a raw
        traceback because `domain_errors()` classifies neither."""
        for key in ("\x00a", "a\x00b", "a\tb", "a\nb"):
            with pytest.raises(ValueError, match="CB-167"):
                findings.query_findings(conn, group_by=f"meta:{key}")
            with pytest.raises(ValueError, match="CB-167"):
                findings.get_stats(conn, group_by=f"meta:{key}")

    def test_the_deferred_short_circuit_keeps_the_grouped_shape(self, conn):
        """The tool description promises the four disclosure keys on EVERY
        grouped response, and a promise with "always" in it has to survive its
        own short circuits (adversarial review).

        `status="deferred"` is a pseudo-status resolved in the MCP wrapper to an
        id restriction; with nothing deferred it short-circuits, and that arm
        used to return an UNGROUPED empty page even when the caller asked for
        groups — so the promised keys were simply absent. The wrapper is called
        as a plain function here, which is what the branch under test is."""
        captured = {}

        class _Recorder:
            def tool(self, *_a, **_k):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn

                return deco

        @contextlib.contextmanager
        def factory():
            yield conn

        findings.register_tools(_Recorder(), factory)
        self._file(conn, "CB-1", tags=["a"])
        result = captured["query"](status="deferred", group_by="tag")
        assert result["grouped"] is True
        assert result["groups"] == []
        for key in ("population", "ungrouped_rows", "multi_group_rows", "nonscalar_value_rows"):
            assert result[key] == 0, (key, result)
        # A bad axis must still be refused on this path rather than answered.
        with pytest.raises(ValueError, match="CB-167"):
            captured["query"](status="deferred", group_by="meta:a.b")

    def test_the_meta_axis_groups_by_a_top_level_key(self, conn):
        self._file(conn, "CB-1", meta={"found_by": "ruff"})
        self._file(conn, "CB-2", meta={"found_by": "ruff"})
        self._file(conn, "CB-3", meta={"found_by": "human"})
        result = findings.query_findings(conn, group_by="meta:found_by")
        assert {g["group_key"]: g["count"] for g in result["groups"]} == {"ruff": 2, "human": 1}
        assert result["ungrouped_rows"] == 0
        assert result["multi_group_rows"] == 0

    def test_a_key_holding_an_APOSTROPHE_proves_the_path_is_BOUND(self, conn):
        """MUTANT this kills: interpolating the path into the SQL text.

        THE FIRST VERSION OF THIS TEST USED A SPACE AND KILLED NOTHING, and the
        correction is the point. Its docstring argued that
        `json_extract(meta, $.found by)` is a syntax error — but nobody writes an
        interpolation that way, because the string would not assemble at all. The
        realistic one is QUOTED, `json_extract(meta, '$.found by')`, and that is
        perfectly valid SQL: measured, bound and interpolated return the same
        answer for a spaced key, so the test could not tell them apart. Found by
        adversarial review.

        An apostrophe is the discriminator, and it is also the real reason the
        path is bound rather than a stylistic preference: interpolated, the key
        `found'by` closes the literal and yields
        `sqlite3.OperationalError: near "by": syntax error` — a quoting break,
        which is the doorway an injection walks through. Note the apostrophe is
        deliberately NOT in `_META_PATH_METACHARS`: it is not path GRAMMAR, it is
        SQL grammar, and binding is what makes it harmless, so the key is
        accepted and answered rather than refused.

        The space is kept as a second case, now honestly labelled: it proves the
        key survives a character that needs no quoting, not that the path is bound.
        """
        self._file(conn, "CB-1", meta={"found'by": "ruff"})
        self._file(conn, "CB-2", meta={"found by": "human"})
        quoted = findings.query_findings(conn, group_by="meta:found'by")
        assert {g["group_key"]: g["count"] for g in quoted["groups"]} == {"ruff": 1}
        spaced = findings.query_findings(conn, group_by="meta:found by")
        assert {g["group_key"]: g["count"] for g in spaced["groups"]} == {"human": 1}

    def test_an_absent_key_and_a_null_value_are_both_ungrouped(self, conn):
        """One answer to one question: neither row carries a value on this axis.
        Both are counted, because a corpus with forty valueless rows and one with
        none are different facts and the groups alone cannot tell them apart."""
        self._file(conn, "CB-1", meta={"assignee": "faxik"})
        self._file(conn, "CB-2", meta={"assignee": None})
        self._file(conn, "CB-3", meta={"other": "x"})
        result = findings.query_findings(conn, group_by="meta:assignee")
        assert [g["group_key"] for g in result["groups"]] == ["faxik"]
        assert result["population"] == 3
        assert result["ungrouped_rows"] == 2
        assert result["nonscalar_value_rows"] == 0

    def test_a_container_value_is_ungrouped_AND_named_separately(self, conn):
        """Measured on this tracker on 2026-08-25: `loc` is a container on 169 of its
        172 rows, `forms_not_chosen` on 5, `sites` on 3. Folding those into
        `ungrouped_rows` would report the rows as carrying no value when they
        carry one this axis cannot rank. (The fixture uses `sites` rather than
        `loc` because the anchor machinery consumes a `loc` key on the add
        path — which is beside the point being pinned here.)"""
        self._file(conn, "CB-1", meta={"sites": "a.py:1"})
        self._file(conn, "CB-2", meta={"sites": {"file": "b.py"}})
        self._file(conn, "CB-3", meta={"sites": ["c.py"]})
        result = findings.query_findings(conn, group_by="meta:sites")
        assert [g["group_key"] for g in result["groups"]] == ["a.py:1"]
        assert result["ungrouped_rows"] == 2
        assert result["nonscalar_value_rows"] == 2

    def test_a_number_stays_a_number_and_a_boolean_renders_as_a_word(self, conn):
        """`json_extract` hands back 1/0 for a JSON boolean, which would put
        `true` and the integer 1 in one group. Rendering the boolean as a word is
        the cheaper collision: a string spelled "true" is rarer than the number 1."""
        self._file(conn, "CB-1", meta={"k": 42})
        self._file(conn, "CB-2", meta={"k": True})
        self._file(conn, "CB-3", meta={"k": False})
        groups = {
            g["group_key"] for g in findings.query_findings(conn, group_by="meta:k")["groups"]
        }
        assert groups == {"42", "true", "false"}

    def test_a_meta_key_with_a_dot_is_refused_and_names_the_card(self, conn):
        """SQLite reads `$.a.b` as a PATH: on `{"a.b": 1, "a": {"b": 2}}` it
        answers 2, the nested value, not the top-level key that was asked for.
        The two existing `meta_key` FILTERS build the path exactly that way and
        are deliberately untouched — that asymmetry is CB-167, and the refusal
        names it so a reader meets the debt instead of a silent wrong answer.

        MUTANT this kills: dropping the refusal. Note it must NOT be replaced by
        a test asserting path semantics — that would ratify the wrong answer."""
        self._file(conn, "CB-1", meta={"misassigned_to_1.81": "x"})
        with pytest.raises(ValueError, match="CB-167"):
            findings.query_findings(conn, group_by="meta:misassigned_to_1.81")
        with pytest.raises(ValueError, match="CB-167"):
            findings.get_stats(conn, group_by="meta:misassigned_to_1.81")

    @pytest.mark.parametrize("key", ["a.b", "a[0]", 'q"k', "a]b"])
    def test_every_path_metacharacter_is_refused(self, conn, key):
        with pytest.raises(ValueError, match="CB-167"):
            findings.query_findings(conn, group_by=f"meta:{key}")

    def test_an_empty_meta_key_is_refused_as_input_not_as_sqlite(self, conn):
        """`json_extract(doc, '$.')` raises OperationalError — an environmental
        exception class out of a domain function, which no CLI arm classifies."""
        with pytest.raises(ValueError):
            findings.query_findings(conn, group_by="meta:")

    # --- one definition, both sites ---------------------------------------

    def test_an_unknown_axis_refuses_identically_on_both_sites(self, conn):
        with pytest.raises(ValueError, match="Invalid group_by") as q:
            findings.query_findings(conn, group_by="nonsense")
        with pytest.raises(ValueError, match="Invalid group_by") as s:
            findings.get_stats(conn, group_by="nonsense")
        assert str(q.value) == str(s.value)
        assert "tag" in str(q.value) and "meta:" in str(q.value)

    def test_both_sites_resolve_the_axis_through_one_definition(self):
        """The two axis lists were hand-written twins in different orders before
        this unit. Nothing mechanical held them together, so the anti-drift
        measure has to be that exactly one definition exists and both call it."""
        tree = ast.parse(pathlib.Path(findings.__file__).read_text())
        callers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id == "_resolve_group_axis"
                for c in ast.walk(node)
            )
        }
        assert {"query_findings", "get_stats"} <= callers, callers
        assert not re.search(r"valid_groups\s*=", pathlib.Path(findings.__file__).read_text())

    def test_every_surface_enumerates_every_axis(self):
        """Prose against code, for the four texts that cannot be generated.

        A docstring cannot be an f-string, so the two MCP descriptions and the
        two CLI `help=` strings are hand-written while `GROUP_AXES_HELP` — which
        the refusal message uses — is derived. That asymmetry is exactly how a
        sixth axis would get added to the resolver and enumerated on none of the
        surfaces, leaving four texts describing a tool that no longer exists.
        `test_golden` cannot see it either: a golden pins what the text IS, never
        what it OUGHT to name.

        MATCHING IS BY TOKEN, and the first draft of this test was VACUOUS
        because it was not. Asking `"tag" in text` passes on the word "tags",
        which the very next sentence of every one of these texts contains — so
        deleting the `tag` axis from an enumeration left this test green
        (measured). Same defect this repository records for the plan-note naming
        hook, where `plan.md` is a substring of `my-plan.md`. The enumeration is
        therefore PARSED — the run of name characters after `by: `, split on its
        own separators — and compared as a SET, so a missing axis and an invented
        one both fail.
        """
        source = pathlib.Path(findings.__file__).read_text()
        expected = {*findings.GROUP_COLUMNS, findings.GROUP_TAG, f"{findings.GROUP_META_PREFIX}<key>"}
        # Stops at the first character an axis name cannot contain — the `.` that
        # ends the CLI sentence, or the ` (` that opens the MCP aside. A NEWLINE
        # is a name character here on purpose: both MCP docstrings wrap the
        # enumeration onto a second line, and without it this test read only the
        # first five names and failed on texts that were in fact correct.
        found = [
            {tok.strip() for tok in re.split(r"[,|]", m.group(1)) if tok.strip()}
            for m in re.finditer(r"[Gg]roup (?:results )?by: ([A-Za-z_:<>|,\s]+)", source)
        ]
        assert len(found) == 4, f"expected 4 axis enumerations, found {len(found)}"
        for i, names in enumerate(found):
            assert names == expected, f"surface text #{i} enumerates {names}, expected {expected}"

    def test_group_by_still_refuses_resolve_anchors(self, conn):
        self._file(conn, "CB-1", tags=["a"])
        with pytest.raises(ValueError, match="resolve_anchors"):
            findings.query_findings(conn, group_by="tag", resolve_anchors=True)

    # --- stats carries both axes and the same disclosure -------------------

    def test_stats_groups_by_tag_and_discloses(self, conn):
        self._file(conn, "CB-1", tags=["a", "b"], severity="critical")
        self._file(conn, "CB-2", tags=[], severity="low")
        result = findings.get_stats(conn, group_by="tag")
        assert result["groups"]["a"]["critical"] == 1
        assert result["groups"]["b"]["total"] == 1
        assert result["population"] == 2
        assert result["ungrouped_rows"] == 1
        assert result["multi_group_rows"] == 1

    def test_stats_groups_by_a_meta_key(self, conn):
        self._file(conn, "CB-1", meta={"found_by": "ruff"}, severity="high")
        self._file(conn, "CB-2", meta={"found_by": "ruff"}, severity="low")
        result = findings.get_stats(conn, group_by="meta:found_by")
        assert result["groups"]["ruff"]["total"] == 2
        assert result["groups"]["ruff"]["high"] == 1
        assert result["nonscalar_value_rows"] == 0

    def test_a_column_axis_still_reports_a_partition(self, conn):
        """The five original axes ARE partitions, and the new keys must say so
        rather than being absent — a reader must never have to test for presence
        to learn what a number means."""
        self._file(conn, "CB-1", tags=["a"])
        self._file(conn, "CB-2", tags=["b"])
        for result in (
            findings.query_findings(conn, group_by="category"),
            findings.get_stats(conn, group_by="category"),
        ):
            assert result["population"] == 2
            assert result["ungrouped_rows"] == 0
            assert result["multi_group_rows"] == 0
            assert result["nonscalar_value_rows"] == 0


class TestGroupingAxesCliContract:
    """The domain tests cannot see a parser that never accepts the value nor a
    handler that crashes formatting it. CB-170 lives here too: `stats` reached
    `get_stats` with no `domain_errors()` wrapper and closed its connection
    outside a `finally`, so an unknown axis printed a traceback — and this unit
    is the one that makes unknown axes more likely, not less."""

    @staticmethod
    def _run(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            cwd=project,
            capture_output=True,
            text=True,
        )

    @pytest.fixture
    def project(self, tmp_project, conn):
        for i, tags in enumerate((["a", "b"], ["a"], []), start=1):
            findings.add_finding(
                conn,
                severity="medium",
                category="perf",
                file=f"f{i}.py",
                description=f"d{i}",
                tags=tags,
                meta={"found_by": "ruff", "n": i},
                finding_id=f"CB-{i}",
            )
        conn.close()
        return tmp_project

    def test_query_groups_by_tag(self, project):
        r = self._run(project, "query", "--group-by", "tag")
        assert r.returncode == 0, r.stderr
        assert "a" in r.stdout and "b" in r.stdout

    def test_query_discloses_the_two_peculiarities(self, project):
        r = self._run(project, "query", "--group-by", "tag")
        assert "1 in no group" in r.stdout, r.stdout
        assert "1 in more than one group" in r.stdout, r.stdout

    def test_query_groups_by_a_meta_key(self, project):
        r = self._run(project, "query", "--group-by", "meta:found_by")
        assert r.returncode == 0, r.stderr
        assert "ruff" in r.stdout

    def test_stats_groups_by_tag(self, project):
        r = self._run(project, "stats", "--by", "tag")
        assert r.returncode == 0, r.stderr
        assert "a" in r.stdout

    def test_stats_renders_a_numeric_group_key(self, project):
        """A COMPOSITION pin, and it is worth saying which mutants it does and
        does not catch. `f"{grp:30s}"` raises TypeError on an integer and
        `sorted()` refuses to order one against a string, so this verb crashes
        outright if a numeric meta value ever reaches it as a number. TWO
        independent mechanisms stop that — the `CAST` in the membership SQL and
        `_group_cell` at the print — so removing EITHER one leaves this test
        green, and only removing both turns it red. It is here because a crash in
        a shipped verb is the failure, and neither mechanism alone is the
        contract."""
        r = self._run(project, "stats", "--by", "meta:n")
        assert r.returncode == 0, r.stderr
        assert "Traceback" not in r.stderr

    def test_stats_with_no_groups_still_reports_the_population(self, project):
        """Adversarial review: `stats` said "(no findings)" over a NON-EMPTY
        tracker whenever an axis put every row outside every group.

        Measured on the live tracker before the fix: `stats --by meta:loc`
        printed "(no findings)" at exit 0 over 172 cards, 169 of which carry that
        key — as a container, so none could be grouped by it. The early `return`
        jumped past the disclosure line, i.e. past the one thing that could have
        said so. A success-shaped false statement about the corpus, the CB-15 /
        CB-16 family — and this unit's own subject besides, since the sibling
        `query` verb answered the same question truthfully all along."""
        r = self._run(project, "stats", "--by", "meta:absent_everywhere")
        assert r.returncode == 0, r.stderr
        assert "no findings" not in r.stdout, r.stdout
        assert "population 3 row(s)" in r.stdout, r.stdout
        assert "3 in no group" in r.stdout, r.stdout

    def test_an_unknown_axis_on_stats_is_one_line_not_a_traceback(self, project):
        """CB-170. MUTANT this kills: removing the `domain_errors()` wrapper."""
        r = self._run(project, "stats", "--by", "nonsense")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "Traceback" not in r.stderr, r.stderr
        assert len(r.stderr.strip().splitlines()) == 1, r.stderr

    def test_an_unknown_axis_on_query_is_still_one_line(self, project):
        r = self._run(project, "query", "--group-by", "nonsense")
        assert r.returncode == 1
        assert "Traceback" not in r.stderr

    def test_a_dotted_meta_key_refuses_on_both_verbs(self, project):
        for args in (("query", "--group-by", "meta:a.b"), ("stats", "--by", "meta:a.b")):
            r = self._run(project, *args)
            assert r.returncode == 1, (args, r.stdout, r.stderr)
            assert "CB-167" in r.stderr, (args, r.stderr)
            assert "Traceback" not in r.stderr


# --- CB-196 ---------------------------------------------------------------


class TestQueryFindingsRowLimit:
    """CB-196 — `query_findings` validates its limit instead of binding it raw.

    SQLite reads a negative LIMIT as NO limit, so `--limit -1` used to return the
    whole table at exit 0: the caller asked to be bounded and silently received
    the opposite. CB-161 had already built `types.require_row_limit` and had
    already NAMED this site as still outstanding; this routes it through.

    Two of the tests below are PINS OF PRESERVED BEHAVIOUR, not guards against a
    mutant — said here and in their own names because a reader who mistakes a pin
    for a live guard draws the wrong conclusion from its passing.
    """

    @staticmethod
    def _three(conn):
        for i, name in enumerate(("a.py", "b.py", "c.py")):
            findings.add_finding(
                conn,
                severity="low",
                category="bug",
                file=name,
                description=f"finding number {i} about {name}",
                new_category=(i == 0),
            )

    def test_a_negative_limit_is_refused(self, conn):
        """The card itself: this returned every row at exit 0 before."""
        self._three(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            findings.query_findings(conn, limit=-1)

    def test_a_negative_limit_is_refused_even_when_ids_are_given(self, conn):
        """THE COMPOSITION, and the reason the call sits at the top of the body.

        `query_findings` widens the limit to `len(ids)` inside `if ids:`, so a
        validator placed after that widening would never see a negative value on
        any call carrying an id list — one argument with two verdicts, decided by
        an unrelated parameter. Measured before the fix: the MCP call
        `query(ids=["CB-196"], limit=-1)` came back reporting `limit: 1`, because
        the widening had already rewritten it. Moving the call below the widening
        turns this test red while the bare test above stays green.
        """
        self._three(conn)
        got = findings.query_findings(conn, limit=100)["findings"]
        with pytest.raises(ValueError, match="must not be negative"):
            findings.query_findings(conn, ids=[got[0]["id"]], limit=-1)

    def test_zero_still_means_zero_rows(self, conn):
        """PIN of preserved behaviour — green before this change and after it.

        Zero was already honest here (CB-124 removed an `or 100` that had turned
        it into 100), and `require_row_limit` keeps zero legal precisely so that
        the sites which behaved correctly are not broken by the fix.
        """
        self._three(conn)
        assert findings.query_findings(conn, limit=0)["findings"] == []

    def test_a_positive_limit_still_truncates(self, conn):
        """PIN of preserved behaviour — the ordinary path must be untouched."""
        self._three(conn)
        assert len(findings.query_findings(conn, limit=2)["findings"]) == 2

    def test_zero_does_NOT_mean_zero_when_ids_are_given(self, conn):
        """PIN of a SURPRISE, and of the sentence the surface now carries.

        The `ids` widening raises the limit to `len(ids)`, so `limit=0` returns
        the whole id list rather than nothing. Adversarial review caught this
        against CB-196's FIRST draft of the help text, which promised a flat
        "0 means NO results" — true on the bare path and false here, a promise
        the change itself introduced. The text now carries the exception, and
        this test is what keeps the two in agreement.
        """
        self._three(conn)
        got = findings.query_findings(conn, limit=100)["findings"]
        ids = [f["id"] for f in got]
        result = findings.query_findings(conn, ids=ids, limit=0)
        assert len(result["findings"]) == len(ids)
        assert result["limit"] == len(ids)

    def test_the_deferred_shortcircuit_refuses_a_negative_limit_too(self, conn):
        """The `deferred` pseudo-status RETURNS before `query_findings` is
        called, so the domain guard cannot see that path: with no deferred rows
        the call used to succeed at exit 0 echoing `"limit": -1`, while the
        identical call on a tracker holding one refused. One argument, two
        verdicts, decided by an unrelated fact about the data. Found by
        adversarial review, not by the first draft of this class.
        """
        import asyncio
        from contextlib import contextmanager

        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError

        self._three(conn)

        @contextmanager
        def factory():
            yield conn

        mcp = MCPServer("cb196-deferred")
        findings.register_tools(mcp, factory)
        with pytest.raises(ToolError, match="must not be negative"):
            asyncio.run(mcp.call_tool("query", {"status": "deferred", "limit": -1}))

    def test_cli_query_refuses_a_negative_limit(self, tmp_project, monkeypatch, capsys):
        """Reached through the real verb, because the domain test above cannot
        see the parser-to-handler seam. One line on stderr, nothing on stdout,
        exit 1 — deliberately 1 and not 3/4/5/74/141, each of which already means
        something else in this package."""
        from codebugs import cli

        conn = db.connect(tmp_project)
        try:
            self._three(conn)
        finally:
            conn.close()

        monkeypatch.setattr(
            sys, "argv", ["codebugs", "--tracker-root", tmp_project, "query", "--limit", "-1"]
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        out = capsys.readouterr()
        assert out.out == ""
        assert "Traceback" not in out.err
        assert len(out.err.strip().splitlines()) == 1
        assert "must not be negative" in out.err
