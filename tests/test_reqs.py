"""Tests for the requirements tracking module."""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading

import pytest

from codebugs import db, reqs
from codebugs.types import utc_now


class RecordingConnection(sqlite3.Connection):
    """Records SQL *templates*, as issued, before parameters are bound.

    ``set_trace_callback`` expands bound parameters, so a guard reading its output
    cannot distinguish a real ``meta`` assignment from the same text appearing
    inside a value. Duplicated from the findings suite rather than shared: this
    project deliberately has no ``conftest.py``, each test file owns its fixtures.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_sql: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.recorded_sql.append(sql)
        return super().execute(sql, *args, **kwargs)


@pytest.fixture
def conn():
    """In-memory database with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    reqs.ensure_schema(c)
    yield c
    c.close()


@pytest.fixture
def recording():
    """In-memory database on a connection that records SQL templates."""
    c = sqlite3.connect(":memory:", factory=RecordingConnection)
    c.row_factory = sqlite3.Row
    reqs.ensure_schema(c)
    yield c
    c.close()


@pytest.fixture
def with_meta(conn):
    """A requirement that already carries unrelated meta keys."""
    reqs.add_requirement(
        conn,
        req_id="FR-100",
        section="S",
        description="d",
        meta={"origin": "spec"},
    )
    return conn


def _import_md(conn, md_text: str) -> dict:
    """Write markdown to a temp file, import it, and clean up."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(md_text)
        f.flush()
        path = f.name
    try:
        return reqs.import_markdown(conn, path)
    finally:
        os.unlink(path)


@pytest.fixture
def populated(conn):
    """Database with sample requirements."""
    now = utc_now()
    for i, (status, priority, section, tc) in enumerate([
        ("planned", "must", "1.1 Ingestion", ""),
        ("implemented", "must", "1.1 Ingestion", "test_core.py"),
        ("implemented", "should", "1.2 Duplicate Detection", "test_dedup.py"),
        ("superseded", "could", "1.3 Sorting", ""),
        ("partial", "must", "1.2 Duplicate Detection", ""),
        ("implemented", "must", "1.4 Classification", ""),  # no test but must
    ], start=1):
        conn.execute(
            """INSERT INTO requirements (id, section, description, priority, status,
               source, test_coverage, tags, meta, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, '', ?, '[]', '{}', ?, ?)""",
            (f"FR-{i:03d}", section, f"Requirement {i} description", priority, status,
             tc, now, now),
        )
    conn.commit()
    return conn


class TestAddRequirement:
    def test_basic_add(self, conn):
        result = reqs.add_requirement(
            conn, req_id="FR-001", description="System shall ingest documents",
            section="1.1 Ingestion", priority="must", status="planned",
        )
        assert result["id"] == "FR-001"
        assert result["status"] == "planned"
        assert result["priority"] == "must"
        assert result["section"] == "1.1 Ingestion"

    def test_invalid_priority_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid priority"):
            reqs.add_requirement(conn, req_id="FR-001", description="test", priority="high")

    def test_invalid_status_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid.*status"):
            reqs.add_requirement(conn, req_id="FR-001", description="test", status="done")

    def test_duplicate_id_raises(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="first")
        with pytest.raises(sqlite3.IntegrityError):
            reqs.add_requirement(conn, req_id="FR-001", description="second")

    def test_tags_and_meta(self, conn):
        result = reqs.add_requirement(
            conn, req_id="FR-001", description="test",
            tags=["v2", "sweep"], meta={"author": "claude"},
        )
        assert result["tags"] == ["v2", "sweep"]
        assert result["meta"]["author"] == "claude"


class CommitFiringConnection(sqlite3.Connection):
    """One-shot hook fired the instant the write transaction closes.

    Copied in shape from ``tests/test_merge.py::CommitFiringConnection`` (itself
    documented as copied from ``tests/test_milestones.py::CommitPausingConnection``)
    and for the same reason: **two seams, deliberately**. Unfixed ``add_requirement``
    closes with ``conn.commit()``; the fixed one closes with ``db.txn``'s
    ``conn.execute("COMMIT")``. A hook keyed on only one of them gives a vacuous pass
    on the other, which is precisely the failure this class exists to catch. Each
    test file in this repo owns its own fixtures rather than sharing one, per the
    project convention (there is deliberately no ``conftest.py`` for this).

    The hook runs AFTER the underlying commit in both cases — firing before it lands
    would leave the write lock held, so the second connection writing inside the hook
    would block until ``busy_timeout`` expired instead of racing.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_commit = None
        self.bare_commit_calls = 0

    def _fire(self):
        if self.after_commit:
            hook, self.after_commit = self.after_commit, None
            hook()

    def commit(self):
        self.bare_commit_calls += 1
        super().commit()
        self._fire()

    def execute(self, sql, *args, **kwargs):
        cur = super().execute(sql, *args, **kwargs)
        if sql.lstrip().upper().startswith("COMMIT"):
            self._fire()
        return cur


class TestAddRequirementReturnsTheRowItWrote:
    """CB-117: the dict is the row THIS call wrote, never a later re-read.

    ``add_requirement`` used to end with ``conn.commit()`` followed by a fresh
    ``SELECT * FROM requirements WHERE id = ?``, so anything that touched the row
    inside that window was reported as the outcome of THIS add. Unlike CB-111's
    ``merge.abandon_session``, there never was a benign period here: the whole
    dict always went straight to the MCP client (``reqs_add``), so a race in that
    window is not a hypothetical hardening — it is a live client-facing lie.

    Trap worth naming for the next reader (the RETURNING rule, CB-30 consequence
    (5)): once the INSERT carries ``RETURNING``, its ``cursor.rowcount`` is 0 until
    the cursor is exhausted, so re-expressing success as ``cursor.rowcount == 1``
    refuses every successful call. No test here duplicates that mutant; it is
    caught by the ordinary ``TestAddRequirement`` tests above, which all assert on
    the returned dict.
    """

    def _open(self, path, factory=sqlite3.Connection):
        c = sqlite3.connect(path, factory=factory)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def test_a_race_inside_the_commit_window_is_not_reported_as_the_outcome(self, tmp_path):
        """The discriminator: mutate the just-inserted row the instant the commit lands.

        Fixed, the row was captured by ``RETURNING`` before the commit, so the call
        reports the ``description``/``status`` THIS call wrote. Unfixed, the trailing
        ``SELECT`` runs after the hook and reports the competitor's write instead — a
        successful call describing someone else's data as its own.
        """
        path = str(tmp_path / "reqs.db")
        seed = self._open(path)
        try:
            reqs.ensure_schema(seed)
        finally:
            seed.close()

        c = self._open(path, factory=CommitFiringConnection)
        try:
            def race_from_another_connection():
                other = self._open(path)
                try:
                    other.execute(
                        "UPDATE requirements SET description='RACED', status='obsolete' "
                        "WHERE id='FR-001'"
                    )
                    other.commit()
                finally:
                    other.close()

            c.after_commit = race_from_another_connection
            result = reqs.add_requirement(
                c, req_id="FR-001", description="original", status="planned",
            )
        finally:
            c.close()

        assert c.after_commit is None, (
            "vacuous test: the hook never fired, so no commit seam was observed — "
            "check that both seams (conn.commit and execute('COMMIT')) are hooked"
        )
        assert result["description"] == "original", (
            "the call must report the row it wrote, not whatever the row became "
            f"afterwards, got {result['description']!r}"
        )
        assert result["status"] == "planned", (
            "status came from the post-commit re-read, not from the write"
        )

    def test_it_does_not_commit_an_ambient_transaction(self, tmp_path):
        """Composition, not just elements: under a caller's open transaction the add
        must not make the caller's unrelated work permanent.

        This is CB-24 consequence (1): the bare ``conn.commit()`` committed whatever
        DML the caller had pending, silently, because a single connection has one
        transaction. ``db.txn`` yields ``False`` here and issues no COMMIT at all,
        leaving the caller in charge — this is the property the removal of
        ``conn.commit()`` exists to buy, and without a test exercising it from
        inside an already-open transaction it stays an unverified claim rather than
        a checked one.
        """
        path = str(tmp_path / "reqs.db")
        seed = self._open(path)
        try:
            reqs.ensure_schema(seed)
        finally:
            seed.close()

        c = self._open(path)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "INSERT INTO requirements (id, description, priority, status, "
                "created_at, updated_at) VALUES ('FR-999', 'caller work', 'should', "
                "'planned', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
            reqs.add_requirement(c, req_id="FR-001", description="d")
            assert c.in_transaction, "the caller's transaction was closed by the callee"
            c.execute("ROLLBACK")
        finally:
            c.close()

        checker = self._open(path)
        try:
            caller_row = checker.execute(
                "SELECT 1 FROM requirements WHERE id = 'FR-999'"
            ).fetchone()
            own_row = checker.execute(
                "SELECT 1 FROM requirements WHERE id = 'FR-001'"
            ).fetchone()
        finally:
            checker.close()
        assert caller_row is None, "the caller's uncommitted INSERT was committed for it"
        assert own_row is None, (
            "the add's own write must have rolled back with the caller's transaction too"
        )

    def test_no_bare_commit_is_issued(self, tmp_path):
        """``add_requirement`` must never call ``conn.commit()`` itself.

        Distinct from the ambient-transaction test above: this fires even on a plain,
        no-transaction call, where a stray bare ``commit()`` would be harmless in
        isolation but is exactly the kind of leftover ``db.txn`` migrations are
        supposed to remove (see CB-111's identical assertion on
        ``merge.abandon_session``).
        """
        path = str(tmp_path / "reqs.db")
        seed = self._open(path)
        try:
            reqs.ensure_schema(seed)
        finally:
            seed.close()

        c = self._open(path, factory=CommitFiringConnection)
        try:
            reqs.add_requirement(c, req_id="FR-001", description="d")
            assert c.bare_commit_calls == 0, (
                "add_requirement must not call conn.commit() itself; db.txn's "
                "execute('COMMIT') is the only sanctioned close"
            )
        finally:
            c.close()

    def test_no_select_follows_the_insert(self, recording):
        """Template guard: after the INSERT, no separate read of ``requirements``.

        Asserted against the SQL *template* recorded by ``RecordingConnection``,
        per the repo rule that a guard of this kind reads the template rather than
        the executed statement (parameter binding can put arbitrary text, including
        SQL keywords, inside a bound value).
        """
        reqs.add_requirement(recording, req_id="FR-001", description="d")

        insert_index = next(
            i for i, sql in enumerate(recording.recorded_sql)
            if sql.strip().upper().startswith("INSERT INTO REQUIREMENTS")
        )
        after_insert = recording.recorded_sql[insert_index + 1:]
        selects = [
            sql for sql in after_insert
            if "SELECT" in sql.upper() and "REQUIREMENTS" in sql.upper()
        ]
        assert not selects, (
            f"a SELECT followed the INSERT — the row must come from RETURNING: {selects!r}"
        )


class TestBatchAdd:
    def test_batch_insert(self, conn):
        results = reqs.batch_add_requirements(conn, [
            {"id": "FR-001", "description": "First", "priority": "must"},
            {"id": "FR-002", "description": "Second"},
        ])
        assert len(results) == 2
        assert {r["id"] for r in results} == {"FR-001", "FR-002"}

    def test_batch_replace(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="original")
        results = reqs.batch_add_requirements(conn, [
            {"id": "FR-001", "description": "updated"},
        ])
        assert results[0]["description"] == "updated"


class TestUpdateRequirement:
    def test_update_status(self, populated):
        result = reqs.update_requirement(populated, "FR-001", status="implemented")
        assert result["status"] == "implemented"

    def test_update_not_found(self, conn):
        with pytest.raises(KeyError, match="not found"):
            reqs.update_requirement(conn, "FR-999", status="implemented")

    def test_update_notes(self, populated):
        result = reqs.update_requirement(populated, "FR-001", notes="Needs review")
        assert result["meta"]["notes"] == "Needs review"

    def test_update_test_coverage(self, populated):
        result = reqs.update_requirement(populated, "FR-001", test_coverage="test_new.py")
        assert result["test_coverage"] == "test_new.py"

    def test_notes_and_meta_update_both_survive(self, with_meta):
        """CB-16: two branches each rebuilt meta and emitted their own ``meta = ?``,
        so SQLite kept only the last and the notes were silently destroyed.

        Starts from a requirement that already carries unrelated meta, so this also
        covers the key-preservation half — the findings twin had that from the start
        and this one did not.
        """
        result = reqs.update_requirement(
            with_meta, "FR-100", notes="INVESTIGATION", meta_update={"k": "v"}
        )
        assert result["meta"]["notes"] == "INVESTIGATION"
        assert result["meta"]["k"] == "v"
        assert result["meta"]["origin"] == "spec"

    def test_meta_update_alone_preserves_unrelated_keys(self, with_meta):
        reqs.update_requirement(with_meta, "FR-100", notes="KEEP ME")
        result = reqs.update_requirement(with_meta, "FR-100", meta_update={"k": "v"})
        assert result["meta"]["notes"] == "KEEP ME"
        assert result["meta"]["origin"] == "spec"
        assert result["meta"]["k"] == "v"

    def test_empty_string_notes_is_a_real_write(self, with_meta):
        reqs.update_requirement(with_meta, "FR-100", notes="SOMETHING")
        result = reqs.update_requirement(with_meta, "FR-100", notes="", meta_update={"k": "v"})
        assert result["meta"]["notes"] == ""
        assert result["meta"]["k"] == "v"

    def test_meta_update_notes_key_wins_because_it_merges_last(self, populated):
        result = reqs.update_requirement(
            populated, "FR-001", notes="LOSES", meta_update={"notes": "WINS"}
        )
        assert result["meta"]["notes"] == "WINS"

    def test_non_meta_update_still_writes_when_stored_meta_is_malformed(self, populated):
        """A status-only update must not start depending on stored meta parsing.

        Mirrors the findings twin: the meta column has no ``json_valid`` constraint,
        so building the new meta dict eagerly would abort this write before its SQL.
        """
        populated.execute("UPDATE requirements SET meta = ? WHERE id = ?", ("{not json", "FR-001"))
        populated.commit()

        with pytest.raises(json.JSONDecodeError):  # raised by the result conversion
            reqs.update_requirement(populated, "FR-001", status="implemented")

        stored = populated.execute(
            "SELECT status FROM requirements WHERE id = ?", ("FR-001",)
        ).fetchone()
        assert stored["status"] == "implemented", "the status write must still have landed"

    def test_single_update_never_assigns_meta_twice(self, recording):
        """Structural guard: exactly one ``meta = ?`` per emitted UPDATE.

        Asserted against the SQL *template*. The notes payload deliberately carries
        the literal token ``meta =``, which would break a guard that inspected the
        executed statement, since the trace callback expands bound parameters.
        """
        reqs.add_requirement(recording, req_id="FR-100", section="S", description="d")
        recording.recorded_sql.clear()

        reqs.update_requirement(
            recording,
            "FR-100",
            status="implemented",
            notes="a value that itself contains meta = bait",
            meta_update={"k": "v"},
        )

        updates = [s for s in recording.recorded_sql if s.startswith("UPDATE requirements")]
        assert len(updates) == 1, updates
        assert updates[0].count("meta = ?") == 1, updates[0]

    def test_noop_update(self, populated):
        result = reqs.update_requirement(populated, "FR-001")
        assert result["id"] == "FR-001"  # Returns unchanged


class PausingConnection(sqlite3.Connection):
    """Fires a one-shot hook immediately after an update's opening ``SELECT``.

    The findings suite carries a twin of this class. Duplicated rather than
    shared, per the project's deliberate no-``conftest.py`` rule — and the two are
    not interchangeable anyway: each keys on its own entity's SELECT.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_select = None

    def execute(self, sql, *args, **kwargs):
        cur = super().execute(sql, *args, **kwargs)
        if self.after_select and sql.lstrip().startswith("SELECT * FROM requirements WHERE id"):
            hook, self.after_select = self.after_select, None  # one-shot: the
            hook()  # returning SELECT at the end of the update must not re-fire
        return cur


class TestConcurrentMetaUpdatesDoNotLoseEachOther:
    """CB-24, requirements side: the meta read-modify-write must be one transaction.

    ``update_requirement`` merges ``notes`` / ``meta_update`` in Python over the row
    it read at the top. Unless that read and the write are one unit, two writers
    both report success and the later erases the earlier's merge. There is no
    ``append_note`` on this entity (deliberate, and documented on the function), so
    the exposure here is ``meta_update`` — which is worse in one respect: it carries
    structured state such as an assignee or a claim marker, not prose.
    """

    def _open(self, db_path):
        c = sqlite3.connect(db_path, factory=PausingConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def test_an_ambient_transaction_is_not_committed_by_the_nested_update(self, conn):
        """The requirements twin of the findings guard — and not redundant with it.

        Restoring an unconditional ``conn.commit()`` in ``update_requirement`` alone
        would pass every other test in this file while once again committing the
        requirement branch of ``milestones.triage_dismiss`` early. A guard that
        exists on only one of two sibling entities is the CB-17 shape again: the
        asymmetry is invisible from inside either file.
        """
        reqs.add_requirement(conn, req_id="FR-1", section="S", description="d")

        with pytest.raises(RuntimeError, match="caller aborts"):
            with db.txn(conn) as opened:
                assert opened, "the caller owns this transaction"
                conn.execute("UPDATE requirements SET section = ? WHERE id = ?", ("outer", "FR-1"))
                reqs.update_requirement(conn, "FR-1", status="implemented", notes="nested")
                raise RuntimeError("caller aborts after the nested update")

        row = reqs.get_requirement(conn, "FR-1")
        assert row["section"] == "S", "the caller's own write must have rolled back"
        assert row["status"] == "planned", "the nested update must roll back with its caller"
        assert "nested" not in json.dumps(row["meta"]), row["meta"]

    def _interleave(self, db_path, a_call, b_call):
        """Drive two writers through each other's read-modify-write window.

        A pauses after its opening SELECT until B has also read. Before the fix B
        gets there, both merges come off the same stale row, and whichever UPDATE
        lands second erases the other. After the fix A holds the write lock from
        before its own SELECT, so B blocks at BEGIN IMMEDIATE and never reaches its
        read — the wait times out, A commits, and B re-reads a row carrying A's key.

        ``b_started`` is load-bearing, not belt-and-braces: with only the 1.0s
        timeout as a guard, a worker not scheduled inside that second lets A write
        first, B then reads A's committed row, and the test passes against the
        UNFIXED code.
        """
        a = self._open(db_path)
        a_read, b_started, b_read = (threading.Event() for _ in range(3))

        def competing_writer():
            a_read.wait(timeout=10)
            b = self._open(db_path)  # opened here: sqlite3 connections are
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

    def test_a_competing_meta_update_key_is_not_erased(self, tmp_path):
        # File-backed rather than the module's in-memory fixture: two connections
        # to ``:memory:`` are two separate databases, so the race cannot exist there.
        db_path = str(tmp_path / "reqs.db")
        seed = self._open(db_path)
        seed.execute("PRAGMA journal_mode=WAL")
        reqs.ensure_schema(seed)
        reqs.add_requirement(seed, req_id="FR-1", section="S", description="d")

        # B also replaces `notes` wholesale, so this covers both meta-writing
        # arguments at once. A separate notes-vs-notes test would prove nothing
        # extra: only B writes notes, so under the serialization the fix buys, B is
        # always second and always wins cleanly — an ordering assertion, not a race.
        self._interleave(
            db_path,
            lambda a: reqs.update_requirement(a, "FR-1", meta_update={"from_a": True}),
            lambda b: reqs.update_requirement(
                b, "FR-1", notes="FROM-B", meta_update={"from_b": True}
            ),
        )

        meta = reqs.get_requirement(seed, "FR-1")["meta"]
        seed.close()
        assert meta.get("from_a") is True, meta
        assert meta.get("from_b") is True, meta
        assert meta.get("notes") == "FROM-B", meta


class TestQueryRequirements:
    def test_query_all(self, populated):
        result = reqs.query_requirements(populated)
        assert result["total"] == 6

    def test_filter_by_status(self, populated):
        result = reqs.query_requirements(populated, status="implemented")
        assert result["total"] == 3

    def test_filter_by_priority(self, populated):
        result = reqs.query_requirements(populated, priority="must")
        assert result["total"] == 4

    def test_filter_by_section(self, populated):
        result = reqs.query_requirements(populated, section="Duplicate")
        assert result["total"] == 2

    def test_search(self, populated):
        result = reqs.query_requirements(populated, search="FR-003")
        assert result["total"] == 1
        assert result["requirements"][0]["id"] == "FR-003"

    def test_group_by(self, populated):
        result = reqs.query_requirements(populated, group_by="status")
        assert result["grouped"] is True
        groups = {g["group_key"]: g["count"] for g in result["groups"]}
        assert groups["implemented"] == 3

    def test_pagination(self, populated):
        result = reqs.query_requirements(populated, limit=2, offset=0)
        assert len(result["requirements"]) == 2
        assert result["total"] == 6

    def test_query_by_id_single(self, populated):
        result = reqs.query_requirements(populated, id="FR-003")
        assert result["total"] == 1
        assert result["requirements"][0]["id"] == "FR-003"

    def test_query_by_id_missing_returns_empty(self, populated):
        result = reqs.query_requirements(populated, id="FR-NOPE")
        assert result["total"] == 0
        assert result["requirements"] == []

    def test_query_by_ids_batch_skips_missing(self, populated):
        result = reqs.query_requirements(populated, ids=["FR-001", "FR-002", "FR-NOPE"])
        ids = {r["id"] for r in result["requirements"]}
        assert ids == {"FR-001", "FR-002"}
        assert result["total"] == 2


class TestGetRequirement:
    def test_get_returns_full_body(self, populated):
        result = reqs.get_requirement(populated, "FR-001")
        assert result["id"] == "FR-001"
        assert "description" in result
        assert "priority" in result
        assert "status" in result

    def test_get_missing_raises_keyerror(self, populated):
        with pytest.raises(KeyError, match="FR-NOPE"):
            reqs.get_requirement(populated, "FR-NOPE")


class TestStats:
    def test_stats_by_status(self, populated):
        result = reqs.get_reqs_stats(populated, group_by="status")
        groups = result["groups"]
        assert groups["implemented"]["total"] == 3
        assert groups["planned"]["must"] == 1

    def test_stats_by_priority(self, populated):
        result = reqs.get_reqs_stats(populated, group_by="priority")
        assert "must" in result["groups"]

    def test_invalid_group_by(self, populated):
        with pytest.raises(ValueError, match="Invalid group_by"):
            reqs.get_reqs_stats(populated, group_by="file")


class TestSummary:
    def test_summary(self, populated):
        result = reqs.get_reqs_summary(populated)
        assert result["total"] == 6
        assert result["by_status"]["implemented"] == 3
        assert result["implemented_without_tests"] == 1  # FR-006: must, implemented, no test
        assert len(result["sections"]) > 0


class TestVerify:
    def test_verify_duplicate_ids(self, conn):
        # Manually insert duplicate (bypass PK by using different tables — simulate import)
        # Since PK prevents actual duplicates, test the gap detection instead
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-010", description="b")
        result = reqs.verify_requirements(conn, checks=["ids"])
        gap_issues = [i for i in result["issues"] if "gap" in i["message"].lower()]
        assert len(gap_issues) == 1  # FR-002..FR-009 gap (8 items, >=5)

    def test_verify_status_contradiction(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001",
            description="Sorting (superseded by vault architecture)",
            status="planned",
        )
        result = reqs.verify_requirements(conn, checks=["status"])
        status_issues = [i for i in result["issues"] if i["check"] == "status"]
        assert len(status_issues) >= 1
        assert "superseded" in status_issues[0]["message"].lower()

    def test_verify_missing_test_file(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001", description="test",
            status="implemented", test_coverage="test_nonexistent.py",
        )
        result = reqs.verify_requirements(conn, checks=["tests"], project_dir="/tmp")
        test_issues = [i for i in result["issues"] if i["check"] == "tests"]
        assert len(test_issues) == 1
        assert "not found" in test_issues[0]["message"]

    def test_verify_must_without_test(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001", description="Critical feature",
            status="implemented", priority="must",
        )
        result = reqs.verify_requirements(conn, checks=["status"])
        issues = [i for i in result["issues"] if "without test" in i["message"]]
        assert len(issues) == 1

    def test_verify_all_clean(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001", description="Good requirement",
            status="planned", priority="should",
        )
        result = reqs.verify_requirements(conn, checks=["status"])
        assert result["issues_found"] == 0


class TestMarkdownImportExport:
    def test_import_basic(self, conn):
        md = """# Requirements

### 1.1 Ingestion (FR-001 -- FR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | System shall ingest PDFs | Must | Implemented | R&A | test_core.py |
| FR-002 | System shall track duplicates | Should | Planned | R&A | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'FR-001'").fetchone()
        assert row["priority"] == "must"
        assert row["status"] == "implemented"
        assert row["section"] == "1.1 Ingestion"
        assert row["test_coverage"] == "test_core.py"

    def test_export_roundtrip(self, populated):
        md = reqs.export_markdown(populated)
        assert "### 1.1 Ingestion" in md
        assert "FR-001" in md
        assert "| ID |" in md

    def test_import_status_normalization(self, conn):
        md = """### 1.1 Test (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Test | should | implemented | -- | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT * FROM requirements WHERE id = 'FR-001'").fetchone()
        assert row["status"] == "implemented"
        assert row["priority"] == "should"


class TestImportNFRRows:
    """CB-2: NFR-xxx IDs should be imported, not silently dropped."""

    def test_import_nfr_rows(self, conn):
        md = """# Requirements

### 1.1 Non-Functional (NFR-001 -- NFR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | System shall respond within 200ms | Must | Planned | Arch | -- |
| NFR-002 | System shall handle 1000 concurrent users | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row is not None
        assert row["priority"] == "must"
        assert row["description"] == "System shall respond within 200ms"

    def test_import_mixed_fr_and_nfr(self, conn):
        md = """# Requirements

### 1.1 Mixed (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Functional req | Must | Planned | R&A | -- |
| NFR-001 | Non-functional req | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        assert conn.execute("SELECT COUNT(*) as c FROM requirements").fetchone()["c"] == 2


class TestImportUnnumberedSections:
    """CB-3: Unnumbered ### headings should create their own sections."""

    def test_unnumbered_section_heading(self, conn):
        md = """# Requirements

### Plugin Architecture (FR-101 -- FR-102)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-101 | Plugins shall load dynamically | Must | Planned | Arch | -- |
| FR-102 | Plugins shall be sandboxed | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'FR-101'").fetchone()
        assert row is not None
        assert row["section"] == "Plugin Architecture"

    def test_unnumbered_does_not_merge_into_previous(self, conn):
        md = """# Requirements

### 1.81 Archive Extract-and-Ingest (FR-001 -- FR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Extract archives | Must | Planned | R&A | -- |
| FR-002 | Detect format | Should | Planned | R&A | -- |

### Plugin Architecture (FR-003 -- FR-004)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-003 | Load plugins | Must | Planned | Arch | -- |
| FR-004 | Sandbox plugins | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 4
        row_001 = conn.execute("SELECT section FROM requirements WHERE id = 'FR-001'").fetchone()
        row_003 = conn.execute("SELECT section FROM requirements WHERE id = 'FR-003'").fetchone()
        assert row_001["section"] == "1.81 Archive Extract-and-Ingest"
        assert row_003["section"] == "Plugin Architecture"
        assert row_001["section"] != row_003["section"]

    def test_unnumbered_section_with_nfr(self, conn):
        md = """# Requirements

### Performance Targets (NFR-001 -- NFR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Response time < 200ms | Must | Planned | Arch | -- |
| NFR-002 | Uptime 99.9% | Must | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row is not None
        assert row["section"] == "Performance Targets"


class TestImportLevel2SectionHeadings:
    """CB-808: ## level-2 headings should be recognized as section boundaries."""

    def test_l2_heading_resets_section(self, conn):
        md = """# Requirements

### 1.98 Search Quality Benchmark (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Search benchmark | Must | Planned | Arch | -- |

## 2. Non-Functional Requirements

### Performance Targets (NFR-001 -- NFR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Response time < 200ms | Must | Planned | Arch | -- |
| NFR-002 | Uptime 99.9% | Must | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 3
        row_fr = conn.execute("SELECT section FROM requirements WHERE id = 'FR-001'").fetchone()
        row_nfr = conn.execute("SELECT section FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row_fr["section"] == "1.98 Search Quality Benchmark"
        assert row_nfr["section"] == "Performance Targets"

    def test_l2_heading_without_number(self, conn):
        md = """## Non-Functional Requirements

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Latency < 200ms | Must | Planned | Arch | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT section FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row["section"] == "Non-Functional Requirements"

    def test_l2_heading_with_number(self, conn):
        md = """## 2. Non-Functional Requirements

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Latency < 200ms | Must | Planned | Arch | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT section FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row["section"] == "2. Non-Functional Requirements"

    def test_l2_does_not_capture_l3(self, conn):
        """Ensure ### headings still take priority over ## for their own rows."""
        md = """## 1. Functional Requirements

### 1.1 Ingestion (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Ingest PDFs | Must | Planned | R&A | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT section FROM requirements WHERE id = 'FR-001'").fetchone()
        assert row["section"] == "1.1 Ingestion"


class TestUpdateSection:
    """CB-808: reqs_update should support the section field."""

    def test_update_section(self, populated):
        result = reqs.update_requirement(populated, "FR-001", section="2. Non-Functional Requirements")
        assert result["section"] == "2. Non-Functional Requirements"

    def test_update_section_to_empty(self, populated):
        result = reqs.update_requirement(populated, "FR-001", section="")
        assert result["section"] == ""


# Regression: CB-1038 — legacy CHECK constraint migration

_LEGACY_SCHEMA = """
CREATE TABLE requirements (
    id TEXT PRIMARY KEY,
    section TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'Should'
        CHECK(priority IN ('Must', 'Should', 'Could')),
    status TEXT NOT NULL DEFAULT 'Planned'
        CHECK(status IN ('Planned', 'Partial', 'Implemented', 'Verified', 'Superseded', 'Obsolete')),
    source TEXT NOT NULL DEFAULT '',
    test_coverage TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@pytest.fixture
def legacy_conn():
    """In-memory DB preloaded with the pre-migration capitalized schema.

    Mirrors the on-disk shape of real DBs created before the lowercase
    refactor: capitalized CHECK constraints on priority/status, and no
    embedding column (it was added later via an ALTER in ensure_schema).
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_LEGACY_SCHEMA)
    now = utc_now()
    c.execute(
        """INSERT INTO requirements
           (id, section, description, priority, status, source, test_coverage,
            tags, meta, created_at, updated_at)
           VALUES (?, '', ?, 'Must', 'Planned', '', '', '[]', '{}', ?, ?)""",
        ("FR-LEGACY-1", "legacy row", now, now),
    )
    c.commit()
    yield c
    c.close()


class TestLowercaseMigration:
    def test_ensure_schema_rewrites_legacy_check_constraint(self, legacy_conn):
        reqs.ensure_schema(legacy_conn)
        schema_sql = legacy_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'"
        ).fetchone()[0]
        # New CHECK must use lowercase literals, old capitalized ones must be gone.
        assert "'must'" in schema_sql
        assert "'should'" in schema_sql
        assert "'could'" in schema_sql
        assert "'planned'" in schema_sql
        assert "'Must'" not in schema_sql
        assert "'Planned'" not in schema_sql

    def test_ensure_schema_lowercases_existing_rows(self, legacy_conn):
        reqs.ensure_schema(legacy_conn)
        row = legacy_conn.execute(
            "SELECT priority, status FROM requirements WHERE id=?",
            ("FR-LEGACY-1",),
        ).fetchone()
        assert row["priority"] == "must"
        assert row["status"] == "planned"

    def test_add_requirement_accepts_capitalized_input_after_migration(
        self, legacy_conn
    ):
        reqs.ensure_schema(legacy_conn)
        # This is the exact failure mode reported in CB-1038.
        result = reqs.add_requirement(
            legacy_conn,
            req_id="FR-NEW-1",
            description="post-migration insert",
            priority="Must",
            status="planned",
        )
        assert result["priority"] == "must"
        assert result["status"] == "planned"

    def test_ensure_schema_is_idempotent_on_new_db(self, conn):
        # conn fixture already ran ensure_schema once. A second call must be
        # a no-op — no table rebuild, no data loss, no exception.
        before_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'"
        ).fetchone()[0]
        reqs.ensure_schema(conn)
        after_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'"
        ).fetchone()[0]
        assert before_sql == after_sql

    def test_migration_recovers_from_orphan_requirements_new(self, legacy_conn):
        # Simulate a prior aborted migration that left requirements_new behind.
        legacy_conn.execute("CREATE TABLE requirements_new (id TEXT PRIMARY KEY)")
        legacy_conn.commit()
        reqs.ensure_schema(legacy_conn)
        # Migration should have dropped the orphan, rebuilt, and renamed.
        tables = {
            r[0]
            for r in legacy_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "requirements" in tables
        assert "requirements_new" not in tables
        row = legacy_conn.execute(
            "SELECT priority FROM requirements WHERE id=?", ("FR-LEGACY-1",)
        ).fetchone()
        assert row["priority"] == "must"


class TestQueryFiltersResolveLikeTheWritePaths:
    """CB-19 sibling sweep: `query_requirements` compared the caller's spelling
    against a canonical column, while `add_requirement` / `update_requirement` had
    ALWAYS normalized through the same resolvers.

    Verified before the fix: `update_requirement(priority="SHOULD")` stored `should`
    and `query_requirements(priority="SHOULD")` then returned zero rows. A tracker
    reporting "no requirements" for a value it just wrote is indistinguishable from
    an empty queue — this predates CB-19 and is not caused by it.
    """

    def _one(self, conn):
        return reqs.add_requirement(
            conn, req_id="FR-1", section="S", description="d", priority="must"
        )

    @pytest.mark.parametrize("spelling", ["must", "Must", "MUST", "  must  "])
    def test_priority_filter_finds_the_row_whatever_the_spelling(self, conn, spelling):
        self._one(conn)
        assert reqs.query_requirements(conn, priority=spelling)["total"] == 1

    @pytest.mark.parametrize("spelling", ["planned", "Planned", "PLANNED"])
    def test_status_filter_finds_the_row_whatever_the_spelling(self, conn, spelling):
        self._one(conn)
        assert reqs.query_requirements(conn, status=spelling)["total"] == 1

    def test_what_was_written_can_be_found_by_the_same_spelling(self, conn):
        """The round trip that was broken: write `SHOULD`, then look for `SHOULD`."""
        self._one(conn)
        reqs.update_requirement(conn, "FR-1", priority="SHOULD")
        assert reqs.query_requirements(conn, priority="SHOULD")["total"] == 1

    def test_an_unknown_filter_value_raises_instead_of_reporting_an_empty_queue(self, conn):
        self._one(conn)
        with pytest.raises(ValueError, match="Invalid priority"):
            reqs.query_requirements(conn, priority="banana")
        with pytest.raises(ValueError, match="Invalid requirement status"):
            reqs.query_requirements(conn, status="banana")


class TestFalseyVocabularyFiltersDoNotDisableTheFilter:
    """CB-25: `if priority:` conflated "not supplied" with "wrong input".

    A falsey non-string short-circuited past the CB-19 resolver, so the condition was
    never added and the caller got the FULL queue — indistinguishable from a correctly
    filtered one. Only `None` and `""` may mean "no filter"."""

    def _two(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a", priority="must")
        reqs.add_requirement(conn, req_id="FR-2", description="b", priority="could")

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_priority_raises_instead_of_returning_everything(self, conn, falsey):
        self._two(conn)
        with pytest.raises(ValueError, match="Invalid priority"):
            reqs.query_requirements(conn, priority=falsey)

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_status_raises_instead_of_returning_everything(self, conn, falsey):
        self._two(conn)
        with pytest.raises(ValueError, match="Invalid requirement status"):
            reqs.query_requirements(conn, status=falsey)

    @pytest.mark.parametrize("empty", [None, ""])
    def test_none_and_empty_string_still_mean_no_filter(self, conn, empty):
        """The documented convention, unchanged — this is the half a blunt
        `is not None` check would have broken."""
        self._two(conn)
        assert reqs.query_requirements(conn, priority=empty)["total"] == 2
        assert reqs.query_requirements(conn, status=empty)["total"] == 2


class TestReqsImportFileIOErrorContract:
    """CB-71 sibling sweep. `_cmd_reqs_import` had NO try/except at ALL, so an
    unreadable path escaped as a raw traceback from `import_markdown`'s
    `open()`, and every exception also leaked the connection (there was no
    `finally`). CLAUDE.md states both halves of the rule this violated: "CLI
    handlers catch domain exceptions and print to stderr with sys.exit(1)", and
    "a handler that catches nothing violates it just as surely as one that
    catches in the wrong order".

    This is the FIRST subprocess-based CLI test in this file — before it,
    tests/test_reqs.py exercised only in-process domain functions, which cannot
    observe a handler's arms or its stderr at all. Non-vacuity: exit 1 and "the
    path appears on stderr" are both already true of the unfixed tree, so
    `"Traceback" not in stderr` is the only assertion here that discriminates.
    """

    @staticmethod
    def _cli(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            capture_output=True, text=True, cwd=str(project),
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )

    def test_missing_markdown_path_is_a_clean_error_not_a_traceback(self, tmp_path):
        db.init_project(str(tmp_path))
        r = self._cli(tmp_path, "reqs-import", "missing.md")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert "missing.md" in r.stderr, r.stderr

    def test_a_readable_file_still_imports(self, tmp_path):
        """The guard must not swallow the success path — Phase 7 requires the
        normal condition as well as the failure."""
        db.init_project(str(tmp_path))
        (tmp_path / "REQ.md").write_text(
            "### 1.1 Search (FR-001)\n\n"
            "| ID | Requirement | Priority | Status |\n"
            "|---|---|---|---|\n"
            "| FR-001 | Search works | Must | Planned |\n"
        )
        r = self._cli(tmp_path, "reqs-import", "REQ.md")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Imported 1 requirements" in r.stdout, r.stdout


class TestImportMarkdownReadIsEagerPremise:
    """A PREMISE PIN, not a behaviour test — said plainly so a reader cannot
    mistake it for one.

    `_cmd_reqs_import` guards the WHOLE `import_markdown(conn, args.file)` call
    with `except OSError`, which is only safe because that function materializes
    the file eagerly (`f.readlines()`) before it writes any row and before it
    commits. Convert that to lazy iteration — the natural fix if REQUIREMENTS.md
    ever grows too large to hold in memory — and an OSError raised by a late
    read would land AFTER committed rows, where the handler's arm would report
    it as bad input: the CB-15/CB-16 success-shaped lie, arriving silently with
    no other test failing.

    So this pins the premise instead of the consequence, in the style of
    tests/test_worktree_harness.py's `test_premise_*` cases. If it goes red, the
    guard in `_cmd_reqs_import` must be narrowed (read in the handler, pass the
    materialized lines to the domain) before the lazy read lands.
    """

    def test_the_whole_file_is_read_before_any_write(self):
        src = inspect.getsource(reqs.import_markdown)
        assert "readlines()" in src, (
            "import_markdown no longer reads the file eagerly — _cmd_reqs_import's "
            "whole-call `except OSError` guard is no longer safe and must be narrowed"
        )
        # The read must precede the first write, not merely exist.
        assert src.index("readlines()") < src.index("INSERT"), src


class TestVerifyAmbientCwd:
    """CB-79 — `verify_requirements` read an ambient `os.getcwd()` that can raise.

    `test_a_deleted_cwd_*` and `test_a_check_that_does_not_need_a_root_*` MUST
    fail against the pre-fix tree; `TestVerifyAmbientCwdCompatibility` below
    passes on both sides by design.
    """

    def _no_cwd(self, monkeypatch):
        def gone():
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(os, "getcwd", gone)

    def test_a_check_that_does_not_need_a_root_still_runs(self, conn, monkeypatch):
        """The LAZY half, and the reason eager resolution was wrong: `root` is
        consumed by the "tests" check and by nothing else, so resolving it at
        the top of the function made `checks=["ids"]` fail from a deleted cwd
        for a check that never looks at a directory.

        Recorded run at 4ee8c6c: FileNotFoundError from reqs.py:442.
        """
        self._no_cwd(monkeypatch)
        result = reqs.verify_requirements(conn, checks=["ids"])
        assert "issues" in result
        result = reqs.verify_requirements(conn, checks=["status"])
        assert "issues" in result

    def test_the_tests_check_refuses_with_an_actionable_message(self, conn, monkeypatch):
        """The check that DOES need a root raises rather than degrading, because
        this function has no "unknown" vocabulary — reporting no issues because
        we could not look for the tests directory would be a false clean."""
        self._no_cwd(monkeypatch)
        with pytest.raises(ValueError, match="cannot determine the current directory"):
            reqs.verify_requirements(conn, checks=["tests"])

    def test_an_explicit_project_dir_never_touches_the_ambient_cwd(self, conn, monkeypatch):
        self._no_cwd(monkeypatch)
        result = reqs.verify_requirements(conn, checks=["tests"], project_dir="/tmp")
        assert "issues" in result

    def test_the_cli_reports_it_as_one_line_not_a_traceback(self, tmp_path):
        """Recorded run at 4ee8c6c: a raw FileNotFoundError traceback.

        `"Traceback" not in stderr` is the assertion that DISCRIMINATES —
        `returncode == 1` does not, since an uncaught traceback also exits 1.
        """
        project = str(tmp_path / "proj")
        os.makedirs(project)
        db.init_project(project)
        doomed = str(tmp_path / "doomed")
        os.makedirs(doomed)
        script = (
            "import os,sys;"
            f"sys.path.insert(0, {os.path.join(os.getcwd(), 'src')!r});"
            f"os.chdir({doomed!r}); os.rmdir({doomed!r});"
            "from codebugs import cli;"
            f"sys.argv=['codebugs','--tracker-root',{project!r},'reqs-verify'];"
            "cli.main()"
        )
        r = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert "Traceback" not in r.stderr, r.stderr
        assert "codebugs:" in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr


class TestVerifyAmbientCwdCompatibility:
    """Passes on BOTH sides — pins behaviour the change preserves."""

    def test_a_normal_verify_is_unchanged(self, conn):
        result = reqs.verify_requirements(conn)
        assert "issues" in result and "total_requirements" in result

    def test_all_three_checks_still_run_together(self, conn, tmp_path):
        result = reqs.verify_requirements(conn, project_dir=str(tmp_path))
        assert "issues" in result


# --- CB-196 ---------------------------------------------------------------


class TestQueryRequirementsRowLimit:
    """CB-196 — `query_requirements` validates its limit instead of binding it raw.

    The findings twin carries the full reasoning; this is the second of the
    card's three sites, and it gets its own tests rather than sharing them
    because a check of elements is not a check of their composition — one
    unwrapped verb would otherwise pass unnoticed.
    """

    @staticmethod
    def _three(conn):
        for i in range(3):
            reqs.add_requirement(conn, req_id=f"FR-{i}", description=f"requirement {i}")

    def test_a_negative_limit_is_refused(self, conn):
        self._three(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            reqs.query_requirements(conn, limit=-1)

    def test_a_negative_limit_is_refused_even_when_ids_are_given(self, conn):
        """Same composition trap as on findings: the `ids` branch widens the
        limit to `len(ids)`, so a validator below it would be a gate that cannot
        fire for exactly the calls carrying an id list."""
        self._three(conn)
        with pytest.raises(ValueError, match="must not be negative"):
            reqs.query_requirements(conn, ids=["FR-0"], limit=-1)

    def test_zero_still_means_zero_rows(self, conn):
        """PIN of preserved behaviour — green on both sides of this change."""
        self._three(conn)
        assert reqs.query_requirements(conn, limit=0)["requirements"] == []

    def test_a_positive_limit_still_truncates(self, conn):
        """PIN of preserved behaviour."""
        self._three(conn)
        assert len(reqs.query_requirements(conn, limit=2)["requirements"]) == 2

    def test_cli_reqs_query_refuses_a_negative_limit(self, tmp_path, monkeypatch, capsys):
        from codebugs import cli

        project = str(tmp_path)
        db.init_project(project)
        c = db.connect(project)
        try:
            self._three(c)
        finally:
            c.close()

        monkeypatch.setattr(
            sys,
            "argv",
            ["codebugs", "--tracker-root", project, "reqs-query", "--limit", "-1"],
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        out = capsys.readouterr()
        assert out.out == ""
        assert "Traceback" not in out.err
        assert len(out.err.strip().splitlines()) == 1
        assert "must not be negative" in out.err
