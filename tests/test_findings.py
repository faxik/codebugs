"""Tests for the findings domain — CRUD, query, stats, migrations."""

import csv
import json
import os
import sqlite3
import subprocess
import sys
import threading

import pytest

from codebugs import db, findings
from codebugs.types import (
    FINDING_STATUSES,
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
            description="Query in loop",
        )
        assert result["id"] == "CB-1"
        assert result["severity"] == "high"
        assert result["category"] == "n_plus_one"
        assert result["file"] == "src/api.py"
        assert result["status"] == "open"
        assert result["source"] == "human"
        assert result["tags"] == []
        assert result["meta"] == {}

    def test_add_with_meta_and_tags(self, conn):
        result = findings.add_finding(
            conn,
            severity="medium",
            category="complexity",
            file="src/foo.py",
            description="CC too high",
            source="ruff",
            tags=["tech-debt", "refactor"],
            meta={"lines": "10-50", "rule_code": "C901"},
        )
        assert result["source"] == "ruff"
        assert result["tags"] == ["tech-debt", "refactor"]
        assert result["meta"]["lines"] == "10-50"
        assert result["meta"]["rule_code"] == "C901"

    def test_add_auto_increments_id(self, conn):
        f1 = findings.add_finding(
            conn, severity="low", category="style", file="a.py", description="d1"
        )
        f2 = findings.add_finding(
            conn, severity="low", category="style", file="b.py", description="d2"
        )
        f3 = findings.add_finding(
            conn, severity="low", category="style", file="c.py", description="d3"
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
                description="d",
            )

    def test_add_sets_timestamps(self, conn):
        result = findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="a.py",
            description="d",
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
        results = findings.batch_add_findings(conn, items)
        assert len(results) == 3
        ids = {r["id"] for r in results}
        assert ids == {"CB-1", "CB-2", "CB-3"}

    def test_batch_add_invalid_severity_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid severity"):
            findings.batch_add_findings(
                conn,
                [
                    {"severity": "ultra", "category": "bug", "file": "a.py", "description": "d"},
                ],
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
        results = findings.batch_add_findings(conn, items)
        assert results[0]["source"] == "semgrep"
        assert results[0]["meta"]["cwe"] == "CWE-89"


class TestUpdateFinding:
    def test_update_status(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="fixed")
        assert result["status"] == "fixed"
        assert result["updated_at"] >= result["created_at"]

    def test_update_notes(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", notes="Fixed in PR #42")
        assert result["meta"]["notes"] == "Fixed in PR #42"

    def test_update_tags(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", tags=["urgent", "sprint-5"])
        assert result["tags"] == ["urgent", "sprint-5"]

    def test_update_meta(self, conn):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"lines": "10-20"},
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
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
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
            meta={"lines": "1-2"},
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
            meta={"lines": "1-2"},
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
            meta={"lines": "1-2"},
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
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="in_progress")
        assert result["status"] == "in_progress"

    def test_update_status_alias_done(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="done")
        assert result["status"] == "fixed"

    def test_update_status_alias_resolved(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="resolved")
        assert result["status"] == "fixed"

    def test_update_status_alias_implemented(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="implemented")
        assert result["status"] == "fixed"

    def test_update_status_alias_wontfix(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="wontfix")
        assert result["status"] == "wont_fix"

    def test_update_status_alias_invalid(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="invalid")
        assert result["status"] == "not_a_bug"

    def test_update_status_alias_active(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = findings.update_finding(conn, "CB-1", status="active")
        assert result["status"] == "in_progress"

    def test_update_invalid_status_raises(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        with pytest.raises(ValueError, match="Invalid finding status"):
            findings.update_finding(conn, "CB-1", status="deleted")

    def test_update_noop(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
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
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
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
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")

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
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d")

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
            meta={"lines": "1-2"},
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
            one, severity="low", category="perf", file="b.py", description="bystander"
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
            description="d",
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
        findings.add_finding(conn, severity="medium", category="perf", file="a.py", description="d")
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
            tags=["urgent"],
        )
        findings.add_finding(
            conn,
            severity="high",
            category="n_plus_one",
            file="api.py",
            description="Query in loop",
            source="claude",
        )
        findings.add_finding(
            conn,
            severity="medium",
            category="n_plus_one",
            file="views.py",
            description="Another N+1",
            source="claude",
        )
        findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="utils.py",
            description="Long line",
            source="ruff",
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
            conn, severity=severity, category="c", file=f"{name}.py", description=name
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
            conn, severity="critical", category="other", file="x.py", description="excluded"
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
            meta={"k": "v"},
        )
        result = findings.get_finding(conn, added["id"])
        assert result["id"] == added["id"]
        assert result["description"] == "boom"
        assert result["tags"] == ["a", "b"]
        assert result["meta"] == {"k": "v"}

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
            meta={"rule_code": "C901"},
        )
        findings.add_finding(conn, severity="low", category="style", file="b.py", description="d2")
        result = findings.query_findings(conn, meta_key="rule_code")
        assert result["total"] == 1

    def test_query_by_meta_key_value(self, conn):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
            meta={"rule_code": "C901"},
        )
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="b.py",
            description="d2",
            meta={"rule_code": "E501"},
        )
        result = findings.query_findings(conn, meta_key="rule_code", meta_value="C901")
        assert result["total"] == 1
        assert result["findings"][0]["file"] == "a.py"


class TestStats:
    @pytest.fixture(autouse=True)
    def seed_data(self, conn):
        findings.add_finding(
            conn, severity="critical", category="security", file="a.py", description="d1"
        )
        findings.add_finding(
            conn, severity="high", category="security", file="b.py", description="d2"
        )
        findings.add_finding(conn, severity="high", category="perf", file="c.py", description="d3")
        findings.add_finding(
            conn, severity="medium", category="style", file="d.py", description="d4"
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
            conn, severity="critical", category="sec", file="a.py", description="d1"
        )
        findings.add_finding(conn, severity="high", category="perf", file="b.py", description="d2")
        findings.add_finding(
            conn, severity="medium", category="perf", file="c.py", description="d3"
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
            conn, severity="critical", category="sec", file="danger.py", description="d1"
        )
        findings.add_finding(
            conn, severity="high", category="sec", file="danger.py", description="d2"
        )
        findings.add_finding(
            conn, severity="low", category="style", file="safe.py", description="d3"
        )
        findings.add_finding(
            conn, severity="low", category="style", file="safe.py", description="d4"
        )
        findings.add_finding(
            conn, severity="low", category="style", file="safe.py", description="d5"
        )

        s = findings.get_summary(conn)
        assert s["hottest_files"][0]["file"] == "danger.py"
        assert s["hottest_files"][0]["critical_high"] == 2


class TestCategories:
    def test_categories_empty(self, conn):
        assert findings.get_categories(conn) == []

    def test_categories_with_data(self, conn):
        findings.add_finding(conn, severity="high", category="bug", file="a.py", description="d1")
        findings.add_finding(conn, severity="high", category="bug", file="b.py", description="d2")
        findings.add_finding(
            conn, severity="medium", category="style", file="c.py", description="d3"
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

    def test_provenance_columns_nullable(self, conn):
        result = findings.add_finding(
            conn,
            severity="high",
            category="test",
            file="a.py",
            description="no provenance",
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
        findings.add_finding(conn1, severity="low", category="test", file="a.py", description="d")
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
            reported_at_ref="v2.1.0",
        )
        assert result["reported_at_commit"] == "a" * 40
        assert result["reported_at_ref"] == "v2.1.0"

    def test_add_without_provenance_defaults_none(self, conn):
        result = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="test",
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
            ],
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
            reported_at_commit=sha,
        )
        findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="b.py",
            description="d2",
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
            reported_at_ref="v2.1.0",
        )
        findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="b.py",
            description="d2",
            reported_at_ref="v3.0.0",
        )
        result = findings.query_findings(conn, ref="v2.1.0")
        assert result["total"] == 1
        assert result["findings"][0]["reported_at_ref"] == "v2.1.0"

    def test_update_reported_at_ref(self, conn):
        f = findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="a.py",
            description="d",
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
            reported_at_commit="a" * 40,
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
            conn, severity="High", category="bug", file="a.py", description="d"
        )
        assert conn.execute(
            "SELECT severity FROM findings WHERE id = ?", (f["id"],)
        ).fetchone()["severity"] == "high"

    def test_batch_add_findings(self, conn):
        rows = findings.batch_add_findings(
            conn,
            [{"severity": " CRITICAL ", "category": "bug", "file": "a.py", "description": "d"}],
        )
        assert conn.execute(
            "SELECT severity FROM findings WHERE id = ?", (rows[0]["id"],)
        ).fetchone()["severity"] == "critical"

    def test_batch_add_defaults_still_work(self, conn):
        """The default flows through the resolver too — it must not be special-cased."""
        rows = findings.batch_add_findings(
            conn, [{"category": "bug", "file": "a.py", "description": "d"}]
        )
        assert rows[0]["severity"] == "medium"

    def test_update_finding(self, conn):
        f = findings.add_finding(
            conn, severity="low", category="bug", file="a.py", description="d"
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
            conn, severity="High", category="bug", file="a.py", description="d"
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
        findings.add_finding(conn, severity="high", category="c", file="a.py", description="d")
        findings.add_finding(conn, severity="low", category="c", file="b.py", description="d")

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
        findings.add_finding(conn, severity="high", category="c", file="a.py", description="d")
        with pytest.raises(ValueError, match="meta_value requires meta_key"):
            findings.query_findings(conn, meta_value="anything")

    def test_meta_key_alone_still_means_key_exists(self, conn):
        findings.add_finding(
            conn, severity="high", category="c", file="a.py", description="d", meta={"k": "v"}
        )
        findings.add_finding(conn, severity="low", category="c", file="b.py", description="d")
        assert findings.query_findings(conn, meta_key="k")["total"] == 1

    def test_both_together_still_match_on_value(self, conn):
        findings.add_finding(
            conn, severity="high", category="c", file="a.py", description="d", meta={"k": "v"}
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
                        description="the widget explodes on load")
        findings.update_finding(conn, f["id"], status="fixed")
        path = _write_csv(tmp_path / "x.csv", [self._foreign_row("the widget explodes on load")])
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert findings.get_finding(conn, f["id"])["status"] == "fixed"
        assert report.skipped == 1, report
        assert report.imported == 0, report

    def test_a_live_card_is_still_bumped(self, conn, tmp_path):
        """CONTROL — green on both sides. Another sighting of a card you already
        have IS an occurrence; the fix must not turn that into a skip."""
        f = findings.add_finding(conn, severity="high", category="bug", file="src/a.py",
                        description="the widget explodes on load")
        path = _write_csv(tmp_path / "x.csv", [self._foreign_row("the widget explodes on load")])
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.merged == 1, report
        assert findings.get_finding(conn, f["id"])["occurrence_count"] == 2

    def test_a_wont_fix_card_still_files_a_recurrence(self, conn, tmp_path):
        """CONTROL — green on both sides. `wont_fix` is a decision that stays
        decided, and CB-43's answer is a NEW linked row, not a reopen."""
        f = findings.add_finding(conn, severity="high", category="bug", file="src/a.py",
                        description="the widget explodes on load")
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
                        description=f"local finding number {i}")
        rows = [{"id": f"CB-{i}", "severity": "high", "category": "peerbug",
                 "file": f"src/p{i}.py", "description": f"unrelated peer finding {i}",
                 "source": "peer"} for i in (1, 2, 3)]
        path = _write_csv(tmp_path / "peer.csv", rows)
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.imported == 3, report
        assert report.skipped == 0, report
        landed = conn.execute(
            "SELECT id, json_extract(meta, '$.imported_id') FROM findings WHERE category='peerbug'"
        ).fetchall()
        assert len(landed) == 3
        # the origin survives renumbering
        assert sorted(x[1] for x in landed) == ["CB-1", "CB-2", "CB-3"]

    def test_reimporting_your_own_export_is_still_a_no_op(self, conn, tmp_path):
        """CONTROL for the guard's real purpose. Deleting the id check entirely
        would break this, which is why it was narrowed rather than removed."""
        findings.add_finding(conn, severity="low", category="c", file="a.py", description="mine one")
        findings.add_finding(conn, severity="low", category="c", file="b.py", description="mine two")
        rows = [dict(r) for r in conn.execute(
            "SELECT id, severity, category, file, description, source FROM findings")]
        path = _write_csv(tmp_path / "own.csv", rows)
        with open(path, newline="") as fh:
            report = findings.import_findings(conn, list(csv.DictReader(fh)))
        assert report.skipped == 2, report
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
        assert report.skipped == 1, report
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
        findings.add_finding(conn, severity="low", category="pre", file="p.py", description="pre-existing")
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
                    description="tagged row", tags=["release", "ui"])
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
