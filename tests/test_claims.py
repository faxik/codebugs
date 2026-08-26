"""Tests for the entity_claims ledger.

File-based tmp_path DBs throughout: every test here either races across
connections or exercises db.connect() discovery, so the in-memory fixture used
elsewhere is not applicable in this file.

Tests 1-9 prove the design's success criteria. Tests 10-29 each exist because a
specific defect was found during review — the defect is named in the docstring.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from codebugs import claims, db, entities, findings, reqs
from codebugs import types as t

SRC = Path(__file__).resolve().parents[1] / "src" / "codebugs"


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


_finding_seq = 0


def _finding(conn, **kw):
    # Each call means "a distinct entity". Since CB-43, identical
    # (category, file, description) tuples are the SAME identity and collapse
    # into one row — so the default description must differ per call.
    global _finding_seq
    _finding_seq += 1
    return findings.add_finding(
        conn,
        severity=kw.get("severity", "high"),
        category=kw.get("category", "test"),
        file=kw.get("file", "a.py"),
        description=kw.get("description", f"d{_finding_seq}"), new_category=True,
    )


def _claim(conn, entity_id, holder="br-a", **kw):
    kw.setdefault("holder_kind", "branch")
    kw.setdefault("holder_repo", "/repo/x")
    return claims.claim(conn, entity_id=entity_id, holder=holder, **kw)


def _release(conn, entity_id, holder="br-a", **kw):
    kw.setdefault("holder_kind", "branch")
    kw.setdefault("holder_repo", "/repo/x")
    return claims.release(conn, entity_id=entity_id, holder=holder, **kw)


def _live_count(conn, entity_id):
    return conn.execute(
        "SELECT COUNT(*) c FROM entity_claims WHERE entity_id=? AND released_at IS NULL",
        (entity_id,),
    ).fetchone()["c"]


# --- 1-9: the success criteria ------------------------------------------------


class TestSuccessCriteria:
    def test_1_two_connections_exactly_one_winner(self, tmp_project, conn):
        """Deploy gate G1. Two threads, two connections, one barrier. Exactly one
        wins, AND the loser is told who won — asserting the loser's report is the
        entire point of the feature, so uniqueness alone is not enough."""
        cb = _finding(conn)["id"]
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def worker(holder):
            c = db.connect(tmp_project)
            try:
                barrier.wait()
                r = claims.claim(
                    c,
                    entity_id=cb,
                    holder=holder,
                    holder_kind="branch",
                    holder_repo="/repo/x",
                )
                with lock:
                    results.append(r)
            finally:
                c.close()

        threads = [threading.Thread(target=worker, args=(h,)) for h in ("br-a", "br-b")]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        outcomes = sorted(r["outcome"] for r in results)
        assert outcomes == ["claimed", "held_by_other"], results
        winner = next(r for r in results if r["outcome"] == "claimed")
        loser = next(r for r in results if r["outcome"] == "held_by_other")
        # The loser's response names the winner's FULL triple.
        assert loser["holder"] == winner["holder"]
        assert loser["holder_kind"] == "branch"
        assert loser["holder_repo"] == "/repo/x"
        assert _live_count(conn, cb) == 1

    def test_2_idempotent_within_one_clock_second(self, conn, monkeypatch):
        """Idempotence must not depend on the clock. utc_now() is whole-second
        (types.py), so a timestamp discriminator misreports a fast retry as a
        fresh claim. touch_count is monotone and clock-independent."""
        monkeypatch.setattr(t, "utc_now", lambda: "2026-08-06T10:00:00Z")
        monkeypatch.setattr(claims.t, "utc_now", lambda: "2026-08-06T10:00:00Z")
        cb = _finding(conn)["id"]
        first = _claim(conn, cb)
        second = _claim(conn, cb)
        assert (first["outcome"], first["touch_count"]) == ("claimed", 1)
        assert (second["outcome"], second["touch_count"]) == ("already_mine", 2)

    def test_3_idempotent_under_load(self, tmp_project, conn):
        """10 threads claiming as the SAME holder: one claim, nine renewals,
        touch_count lands on exactly 10, and one live row survives."""
        cb = _finding(conn)["id"]
        n = 10
        barrier = threading.Barrier(n)
        results = []
        lock = threading.Lock()

        def worker():
            c = db.connect(tmp_project)
            try:
                barrier.wait()
                r = claims.claim(
                    c, entity_id=cb, holder="br-a", holder_kind="branch", holder_repo="/repo/x"
                )
                with lock:
                    results.append(r)
            finally:
                c.close()

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert [r["outcome"] for r in results].count("claimed") == 1
        assert [r["outcome"] for r in results].count("already_mine") == n - 1
        assert _live_count(conn, cb) == 1
        row = conn.execute(
            "SELECT touch_count FROM entity_claims WHERE entity_id=? AND released_at IS NULL",
            (cb,),
        ).fetchone()
        assert row["touch_count"] == n

    def test_4_reads_are_indexed_point_queries(self, conn):
        """Both ownership questions must stay point queries, not folds."""
        cb = _finding(conn)["id"]
        _claim(conn, cb)
        assert claims.who_holds(conn, entity_id=cb)["holder"] == "br-a"
        held = claims.held_by(conn, holder="br-a")
        assert held["count"] == 1 and held["claims"][0]["entity_id"] == cb

        p1 = " ".join(
            r["detail"]
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM entity_claims "
                "WHERE entity_id=? AND released_at IS NULL",
                (cb,),
            )
        )
        p2 = " ".join(
            r["detail"]
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM entity_claims "
                "WHERE holder=? AND released_at IS NULL",
                ("br-a",),
            )
        )
        assert "idx_claims_live" in p1, p1
        assert "idx_claims_holder_live" in p2, p2

    def test_5a_third_kind_by_declaration_only(self, conn, monkeypatch):
        """A third entity kind must need ZERO changes to claims.py."""
        conn.execute(
            "CREATE TABLE widgets (id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "description TEXT, updated_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO widgets VALUES ('WG-1','idle','w','2026-01-01T00:00:00Z')")
        widget = entities.EntityKind(
            name="widget",
            table="widgets",
            id_pattern=re.compile(r"^WG-\d+"),
            terminal=frozenset({"done"}),
            sort_col="id",
            sort_vocabulary=None,  # "id" is not a vocabulary column (CB-20)
            result_key="widgets",
            readable_cols=frozenset({"id", "status", "description"}),
            busy_status="working",
        )
        monkeypatch.setattr(entities, "ENTITY_KINDS", (*entities.ENTITY_KINDS, widget))

        r = _claim(conn, "WG-1")
        assert r["outcome"] == "claimed" and r["projected_to"] == "working"
        assert conn.execute("SELECT status FROM widgets").fetchone()["status"] == "working"
        rel = _release(conn, "WG-1")
        assert rel["outcome"] == "released" and rel["status_restored"] is True
        assert conn.execute("SELECT status FROM widgets").fetchone()["status"] == "idle"

    def test_5b_projection_preconditions_hold(self, conn):
        """P1-P3 of the projection contract, enforced rather than assumed."""
        for kind in entities.ENTITY_KINDS:
            if kind.busy_status is None:
                continue
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({kind.table})")}
            assert {"id", "status", "updated_at"} <= cols, kind.name  # P1
            assert kind.busy_status not in kind.terminal, kind.name  # P3

        # P2: the declared value is admissible and canonical (round-trips verbatim).
        cb = _finding(conn)["id"]
        ref = entities.EntityRef.of(cb)
        assert ref.set_status(conn, new_status="in_progress", expected="open") is True
        assert ref.status(conn) == "in_progress"
        conn.rollback()

    def test_6_requirement_claimed_without_projection(self, conn):
        """The user's ratified decision: requirements get claim records, their
        status is untouched, and no CHECK constraint is rebuilt."""
        reqs.add_requirement(conn, req_id="FR-1", description="r", section="s")
        r = _claim(conn, "FR-1")
        assert r["outcome"] == "claimed"
        assert r["projected"] is False
        assert r["projected_to"] is None and r["prev_status"] is None
        assert reqs.get_requirement(conn, "FR-1")["status"] == "planned"
        rel = _release(conn, "FR-1")
        assert rel["outcome"] == "released"
        assert reqs.get_requirement(conn, "FR-1")["status"] == "planned"

    def test_7_existing_status_query_still_works(self, conn):
        """No regression on the read the whole workflow already depends on."""
        cb = _finding(conn)["id"]
        _claim(conn, cb)
        got = findings.query_findings(conn, status="in_progress")
        assert [f["id"] for f in got["findings"]] == [cb]

    def test_8_staleness_reporting_only(self, conn, monkeypatch):
        """idle_seconds is the honest staleness signal. The deferred audit
        decorations (stale / orphaned / divergent) must not have reappeared."""
        cb = _finding(conn)["id"]
        monkeypatch.setattr(claims.t, "utc_now", lambda: "2026-08-06T10:00:00Z")
        _claim(conn, cb)
        monkeypatch.setattr(claims.t, "utc_now", lambda: "2026-08-06T10:01:40Z")
        row = claims.who_holds(conn, entity_id=cb)
        assert row["idle_seconds"] == 100 and row["held_seconds"] == 100
        for banned in ("stale", "orphaned", "divergent"):
            assert banned not in row

        _release(conn, cb)
        assert claims.list_claims(conn)["count"] == 0
        assert claims.who_holds(conn, entity_id=cb) is None

    def test_9_pull_next_untouched(self, conn):
        """Deploy gate G9: the pull_next deferral is real. milestone_items still
        carries its own assigned_agent and no claim row is created by a pull."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(milestone_items)")}
        assert "assigned_agent" in cols and "pulled_at" in cols
        src = (SRC / "milestones" / "capacity.py").read_text()
        assert "claims" not in src, "capacity.py must not reference the claims module in v1"


# --- 10-29: one test per defect found in review -------------------------------


class TestTransactionDiscipline:
    def test_10_core_never_commits(self, conn):
        """A core function that commits would break the terminal hook's
        atomicity with the status write that triggered it."""
        cb = _finding(conn)["id"]
        conn.execute("BEGIN IMMEDIATE")
        claims._claim_core(
            conn, entity_id=cb, holder="br-a", holder_kind="branch", holder_repo="/repo/x"
        )
        assert conn.in_transaction is True
        claims._release_core(
            conn, entity_id=cb, holder="br-a", holder_kind="branch", holder_repo="/repo/x"
        )
        assert conn.in_transaction is True
        conn.execute("ROLLBACK")

    def test_11_txn_is_reentrant(self, conn):
        """Nested BEGIN IMMEDIATE raises SQLITE_ERROR. db.txn detects the ambient
        transaction and yields False instead of attempting it."""
        with db.txn(conn) as outer:
            assert outer is True
            with db.txn(conn) as inner:
                assert inner is False
                conn.execute(
                    "INSERT INTO entity_claims (claim_id, entity_id, kind, holder, "
                    "claimed_at, renewed_at) VALUES ('CLM-99','CB-99','finding','h','t','t')"
                )
            assert conn.in_transaction is True
        assert conn.in_transaction is False
        assert (
            conn.execute("SELECT COUNT(*) c FROM entity_claims WHERE claim_id='CLM-99'").fetchone()[
                "c"
            ]
            == 1
        )

    def test_12_original_exception_survives_cleanup(self, conn):
        """A ROLLBACK with no active transaction raises SQLITE_ERROR whose message
        mentions neither 'locked' nor 'busy'. Unguarded, that cleanup exception
        replaces the real one and escapes past the contention classifier."""
        with pytest.raises(RuntimeError, match="the real failure"):
            with db.txn(conn):
                conn.execute("ROLLBACK")  # SQLite now has no active transaction
                raise RuntimeError("the real failure")

    def test_27_ambient_transaction_invariant(self, tmp_project, conn):
        """NORMATIVE: the public layer is only correct on a clean connection. On a
        connection with an implicitly-opened transaction, db.txn yields False, the
        write happens, nothing commits, and claim still reports success. v1 is safe
        only because every public caller opens a fresh connection — this test is
        what keeps that true."""
        cb = _finding(conn)["id"]
        conn.execute("UPDATE findings SET category='x' WHERE id=?", (cb,))
        assert conn.in_transaction is True  # implicit transaction now open

        r = claims.claim(
            conn, entity_id=cb, holder="br-a", holder_kind="branch", holder_repo="/repo/x"
        )
        assert r["outcome"] == "claimed"
        conn.rollback()  # nothing was committed: the claim evaporates
        assert claims.who_holds(conn, entity_id=cb) is None

        # The reason it is unreachable: every public caller holds a fresh connection.
        fresh = db.connect(tmp_project)
        try:
            assert fresh.in_transaction is False
        finally:
            fresh.close()

    def test_24_no_plain_begin_ratchet(self):
        """Deploy gate G6. A plain BEGIN pins a read snapshot and the later write
        upgrade dies with SQLITE_BUSY_SNAPSHOT, which busy_timeout cannot rescue.
        This allowlist may shrink, never grow.

        **It shrank to one (CB-40).** `merge.merge` and `capacity.pull_next` both
        opened their raw transaction with `conn.isolation_level = None`, and assigning
        `isolation_level` COMMITS any open transaction — so either function, called
        under an ambient transaction, silently committed the caller's unrelated work.
        Both now go through `db.txn`, leaving `db.txn` itself as the only executable
        site in the package.

        **Now counted, not deduplicated.** The old version collected
        `(filename, statement)` into a SET and asserted `found <= allowed`, so any
        number of raw sites inside an already-allowed file passed — and so did zero.
        That is a filename allowlist, not a one-site ratchet. Since the claim this
        gate now makes is about the COUNT of raw sites, it counts them.

        **Counted from the AST, not from a line scan.** The line-based version missed
        a multiline `conn.execute(\\n "BEGIN ..." )`, lowercase spelling, and two calls
        on one line.

        **Three further gaps, each found by review rather than by me**, and each closed
        below: an `executescript` body holds MANY statements, so only checking that the
        literal *starts* with BEGIN missed `"SELECT 1; BEGIN IMMEDIATE; ..."`; a leading
        `--` comment hid the statement after it; and a bare `startswith("BEGIN")`
        over-counted `BEGIN TUTORIAL` and `BEGINNING`.

        **What it still cannot see — enumerated, because two rounds of review caught
        this docstring claiming more than the code enforces:** SQL built at runtime is
        invisible; the receiver is not resolved, so `anything.execute("BEGIN")` counts;
        the statement split is a plain `;` split, so it mishandles semicolons inside
        quoted strings and inside `--` comments, and does not strip `/* block */`
        comments; and a non-script `execute("BEGIN;")` with a trailing semicolon is
        missed. It also does NOT verify that the one allowed site is `db.txn` — it
        records a filename and a classification, nothing more.

        So: this is a tripwire against a raw BEGIN being *typed into the source* in a
        recognisable form. It is not a proof that exactly one can execute, and the
        allowlist is the real contract. Tightening it further is tracked rather than
        claimed.
        """
        import ast as _ast

        def _statements(sql: str, is_script: bool) -> list[str]:
            """Statements in a literal, comments stripped, upper-cased."""
            parts = sql.split(";") if is_script else [sql]
            out = []
            for part in parts:
                lines = [
                    ln for ln in part.splitlines()
                    if not ln.strip().startswith("--")
                ]
                cleaned = " ".join(lines).strip().upper()
                if cleaned:
                    out.append(cleaned)
            return out

        def _classify(stmt: str) -> str | None:
            """`BEGIN IMMEDIATE` / `BEGIN` / None — token-aware, so BEGINNING is not a hit."""
            tokens = stmt.replace("(", " ").split()
            if not tokens or tokens[0] != "BEGIN":
                return None
            if len(tokens) > 1 and tokens[1] == "IMMEDIATE":
                return "BEGIN IMMEDIATE"
            return "BEGIN"

        allowed = {("db.py", "BEGIN IMMEDIATE"): 1}

        found: dict[tuple[str, str], int] = {}
        for path in SRC.rglob("*.py"):
            tree = _ast.parse(path.read_text())
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Call):
                    continue
                fn = node.func
                if not (isinstance(fn, _ast.Attribute)
                        and fn.attr in ("execute", "executescript")):
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                if not (isinstance(arg, _ast.Constant) and isinstance(arg.value, str)):
                    continue  # runtime-built SQL — invisible to any static check
                for statement in _statements(arg.value, fn.attr == "executescript"):
                    stmt = _classify(statement)
                    if stmt is None:
                        continue
                    key = (path.name, stmt)
                    found[key] = found.get(key, 0) + 1

        unexpected = {k: v for k, v in found.items() if k not in allowed}
        assert not unexpected, f"new or plain BEGIN: {unexpected}"
        assert found == allowed, (
            "the executable BEGIN IMMEDIATE census changed. Fewer is fine — update "
            f"`allowed` downward. More is the regression this gate exists for. {found}"
        )


class TestContentionClassifier:
    def test_13_contention_becomes_undetermined(self, tmp_project, conn):
        """Without this the fourth outcome is documentation. A losing writer with
        busy_timeout=0 must be reported, not raised."""
        cb = _finding(conn)["id"]
        blocker = db.connect(tmp_project)
        victim = db.connect(tmp_project)
        try:
            victim.execute("PRAGMA busy_timeout=0")
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("UPDATE findings SET category='held' WHERE id=?", (cb,))
            r = claims.claim(
                victim, entity_id=cb, holder="br-a", holder_kind="branch", holder_repo="/r"
            )
            assert r["outcome"] == "undetermined"
            assert r["reason"] == "database_busy" and r["retry_after_ms"] == 250
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
            victim.close()

    def test_14_real_errors_are_never_masked(self, conn):
        """The classifier keys on numeric codes, not message text. SQLITE_ERROR
        (1) is a programming error and must stay loud."""
        exc = sqlite3.OperationalError("cannot rollback - no transaction is active")
        exc.sqlite_errorcode = 1
        with pytest.raises(sqlite3.OperationalError):
            claims._undetermined(exc, entity_id="CB-1")
        assert claims._is_contention(exc) is False

        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorcode = 517  # SQLITE_BUSY_SNAPSHOT masks to 5
        assert claims._is_contention(busy) is True


class TestTerminalGuardAndHook:
    def test_15_terminal_guard_is_unconditional(self, conn):
        """Round 2 wrapped the guard in `if project and busy is not None`, which
        made it unreachable for EVERY requirement and every --no-project claim."""
        reqs.add_requirement(conn, req_id="FR-1", description="r", section="s")
        reqs.update_requirement(conn, "FR-1", status="implemented")
        r = _claim(conn, "FR-1")
        assert r["outcome"] == "entity_terminal" and r["current_status"] == "implemented"

        cb = _finding(conn)["id"]
        findings.update_finding(conn, cb, status="fixed")
        r2 = _claim(conn, cb, project=False)
        assert r2["outcome"] == "entity_terminal"

        r3 = _claim(conn, cb, allow_terminal=True)
        assert r3["outcome"] == "claimed"

    def test_16_terminal_hook_releases_and_keeps_history(self, conn):
        cb = _finding(conn)["id"]
        _claim(conn, cb)
        findings.update_finding(conn, cb, status="fixed")
        assert claims.who_holds(conn, entity_id=cb) is None
        row = conn.execute(
            "SELECT released_at, released_by, release_reason FROM entity_claims WHERE entity_id=?",
            (cb,),
        ).fetchone()
        assert row["released_at"] is not None
        assert row["release_reason"] == "terminal:fixed"
        assert row["released_by"] == "hook:status_change"

    def test_17_both_writers_fire_the_hook(self, conn):
        """Not just findings: a seam over the entity layer must fire from reqs too."""
        reqs.add_requirement(conn, req_id="FR-1", description="r", section="s")
        _claim(conn, "FR-1")
        reqs.update_requirement(conn, "FR-1", status="implemented")
        assert claims.who_holds(conn, entity_id="FR-1") is None
        row = conn.execute(
            "SELECT release_reason FROM entity_claims WHERE entity_id='FR-1'"
        ).fetchone()
        assert row["release_reason"] == "terminal:implemented"

    def test_18_changed_guard_all_three_arms(self, conn, monkeypatch):
        """Round 2 fired when the intended status differed from the pre-read row.
        That is not 'the write happened' — a refused write could release ownership
        on an entity whose status never became terminal."""
        calls = []
        monkeypatch.setattr(db, "run_status_change_hooks", lambda *a, **k: calls.append(a[1:]))
        cb = _finding(conn)["id"]

        findings.update_finding(conn, cb, notes="no status here")
        assert calls == []  # no status requested

        findings.update_finding(conn, cb, status="fixed")
        assert len(calls) == 1  # a real open -> fixed

        findings.update_finding(conn, cb, status="fixed")
        assert len(calls) == 1  # already fixed: not a change

    def test_19_no_hook_recursion(self, conn, monkeypatch):
        """set_status is only ever called with a non-terminal value, so a claim's
        projection can never trigger the terminal hook."""
        calls = []
        monkeypatch.setattr(db, "run_status_change_hooks", lambda *a, **k: calls.append(a[1:]))
        cb = _finding(conn)["id"]
        _claim(conn, cb)
        _release(conn, cb)
        assert calls == []


class TestHolderIdentity:
    def test_20_claim_compares_the_full_triple(self, conn):
        """Identical holder text in another repo or of another kind is a DIFFERENT
        claimant. NULL repo must match NULL repo (IS, not =)."""
        cb = _finding(conn)["id"]
        _claim(conn, cb, holder_repo="/repo/x")

        other_repo = _claim(conn, cb, holder_repo="/repo/y")
        assert other_repo["outcome"] == "held_by_other"
        assert other_repo["holder_repo"] == "/repo/x"

        other_kind = claims.claim(
            conn, entity_id=cb, holder="br-a", holder_kind="agent", holder_repo="/repo/x"
        )
        assert other_kind["outcome"] == "held_by_other"

        cb2 = _finding(conn)["id"]
        first = claims.claim(
            conn, entity_id=cb2, holder="br-a", holder_kind="branch", holder_repo=None
        )
        again = claims.claim(
            conn, entity_id=cb2, holder="br-a", holder_kind="branch", holder_repo=None
        )
        assert (first["outcome"], again["outcome"]) == ("claimed", "already_mine")

    def test_29_release_authorizes_on_the_full_triple(self, conn):
        """The hole the final cross-model verifier found: with holder-only
        matching, a same-text holder of another kind or repo could release someone
        else's claim."""
        cb = _finding(conn)["id"]
        _claim(conn, cb, holder_repo="/repo/x")

        for kind, repo in (("branch", "/repo/y"), ("agent", "/repo/x"), ("branch", None)):
            r = claims.release(
                conn, entity_id=cb, holder="br-a", holder_kind=kind, holder_repo=repo
            )
            assert r["outcome"] == "not_yours", (kind, repo)
            assert _live_count(conn, cb) == 1

        assert _release(conn, cb)["outcome"] == "released"

        cb2 = _finding(conn)["id"]
        claims.claim(conn, entity_id=cb2, holder="br-a", holder_kind="branch", holder_repo=None)
        r = claims.release(
            conn, entity_id=cb2, holder="br-a", holder_kind="branch", holder_repo=None
        )
        assert r["outcome"] == "released"


class TestReleaseSemantics:
    def test_21_release_never_resurrects_finished_work(self, conn):
        cb = _finding(conn)["id"]
        _claim(conn, cb)
        conn.execute("UPDATE findings SET status='fixed' WHERE id=?", (cb,))
        conn.commit()
        r = _release(conn, cb)
        assert r["outcome"] == "released"
        assert r["status_restored"] is False
        assert findings.get_finding(conn, cb)["status"] == "fixed"

    def test_22_soft_delete_allows_clean_reclaim(self, conn):
        cb = _finding(conn)["id"]
        first = _claim(conn, cb)
        assert _live_count(conn, cb) == 1
        _release(conn, cb)
        assert _live_count(conn, cb) == 0
        second = _claim(conn, cb, holder="br-b")
        assert second["outcome"] == "claimed"
        assert second["claim_id"] != first["claim_id"]
        assert _live_count(conn, cb) == 1
        total = conn.execute(
            "SELECT COUNT(*) c FROM entity_claims WHERE entity_id=?", (cb,)
        ).fetchone()["c"]
        assert total == 2  # the released row survives

    def test_23_release_misses_write_nothing(self, conn):
        cb = _finding(conn)["id"]
        _claim(conn, cb)
        before = conn.execute("SELECT * FROM entity_claims").fetchall()

        wrong = claims.release(
            conn, entity_id=cb, holder="someone-else", holder_kind="branch", holder_repo="/repo/x"
        )
        assert wrong["outcome"] == "not_yours" and wrong["holder"] == "br-a"

        cb2 = _finding(conn)["id"]
        assert _release(conn, cb2)["outcome"] == "not_claimed"

        after = conn.execute("SELECT * FROM entity_claims").fetchall()
        assert [dict(r) for r in before] == [dict(r) for r in after]


class TestResponseContract:
    def test_28_every_outcome_carries_every_common_key(self, tmp_project, conn):
        """Round 3 promised this and returned partial dicts from two paths."""
        responses = []
        cb = _finding(conn)["id"]
        responses.append(_claim(conn, cb))  # claimed
        responses.append(_claim(conn, cb))  # already_mine
        responses.append(_claim(conn, cb, holder="br-b"))  # held_by_other
        responses.append(_release(conn, cb))  # released
        responses.append(_release(conn, cb))  # not_claimed

        cb2 = _finding(conn)["id"]
        _claim(conn, cb2)
        responses.append(
            claims.release(
                conn, entity_id=cb2, holder="nope", holder_kind="branch", holder_repo="/repo/x"
            )
        )  # not_yours

        cb3 = _finding(conn)["id"]
        findings.update_finding(conn, cb3, status="fixed")
        responses.append(_claim(conn, cb3))  # entity_terminal

        blocker = db.connect(tmp_project)
        victim = db.connect(tmp_project)
        try:
            victim.execute("PRAGMA busy_timeout=0")
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("UPDATE findings SET category='held' WHERE id=?", (cb3,))
            responses.append(
                claims.claim(
                    victim, entity_id=cb3, holder="br-z", holder_kind="branch", holder_repo="/r"
                )
            )  # undetermined (claim)
            responses.append(
                claims.release(
                    victim, entity_id=cb3, holder="br-z", holder_kind="branch", holder_repo="/r"
                )
            )  # undetermined (release)
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
            victim.close()

        seen = {r["outcome"] for r in responses}
        assert seen == {
            "claimed",
            "already_mine",
            "held_by_other",
            "entity_terminal",
            "released",
            "not_yours",
            "not_claimed",
            "undetermined",
        }, seen
        for r in responses:
            missing = [k for k in claims._COMMON_KEYS if k not in r]
            assert not missing, f"{r['outcome']} missing {missing}"


class TestCliContract:
    """The CLI's exit codes ARE the API for shell callers. An unasserted exit code
    is an unasserted gate."""

    @staticmethod
    def _run(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            cwd=project,
            capture_output=True,
            text=True,
        )

    def test_25_exit_codes(self, tmp_project, conn):
        """Deploy gates G2 and G7."""
        cb = _finding(conn)["id"]
        conn.close()

        ok = self._run(tmp_project, "claim", cb, "--holder", "br-a", "--holder-kind", "branch")
        assert ok.returncode == 0, ok.stderr

        taken = self._run(tmp_project, "claim", cb, "--holder", "br-b", "--holder-kind", "branch")
        assert taken.returncode == 3, taken.stdout + taken.stderr

        held = self._run(tmp_project, "who-holds", cb)
        assert held.returncode == 0
        free = self._run(tmp_project, "who-holds", "CB-999")
        assert free.returncode == 3

        c = db.connect(tmp_project)
        findings.update_finding(c, cb, status="fixed")
        c.close()
        terminal = self._run(tmp_project, "claim", cb, "--holder", "br-c")
        assert terminal.returncode == 4, terminal.stdout

        empty = self._run(tmp_project, "claims", "--format", "ids")
        assert empty.returncode == 0 and empty.stdout.strip() == ""

    def test_25b_exit_code_5_is_actually_emitted(self, tmp_project, conn):
        """Deploy gate G2. If the CLI never emits 5, worktree-setup.sh's retry is
        dead code and a contended database silently proceeds unclaimed.

        Note WHERE the contention surfaces, and note that this changed under
        CB-195 while this docstring did not (CB-202). It used to say
        `db.connect()` itself writes during schema init, so the refusal arrived
        before any claim code ran. Those seed inserts are conditional now, and
        the fixture below opens the tracker first, so on THIS path connect() is a
        pure read and does not contend at all — what raises is `claim`'s and
        `release`'s OWN write against the held lock. The assertion is unchanged
        and still correct; only the stated mechanism was stale. Exit 5 must hold
        for the connect path too, which is why the CLI classifies contention
        around connect as well — that arm is exercised on a tracker whose seed
        row is still missing, i.e. its very first open.
        """
        cb = _finding(conn)["id"]
        blocker = db.connect(tmp_project)
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("UPDATE findings SET category='held' WHERE id=?", (cb,))
        try:
            r = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codebugs.cli",
                    "claim",
                    cb,
                    "--holder",
                    "br-a",
                    "--holder-kind",
                    "branch",
                ],
                cwd=tmp_project,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert r.returncode == 5, (r.returncode, r.stdout, r.stderr)
            assert "UNDETERMINED" in r.stdout

            rel = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codebugs.cli",
                    "release",
                    cb,
                    "--holder",
                    "br-a",
                    "--holder-kind",
                    "branch",
                ],
                cwd=tmp_project,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert rel.returncode == 5, (rel.returncode, rel.stdout, rel.stderr)
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

    def test_30_contention_never_crashes_any_verb(self, tmp_project, conn):
        """CB-14, UPDATED for CB-195 — the old assertion tested the bug's own
        symptom as if it were the guarantee.

        `db.connect()` used to WRITE during schema init on every open
        (`merge.ensure_schema`'s unconditional `INSERT OR IGNORE`), so a held
        write lock made EVERY verb contend — including these three, which
        never write anything themselves — and this test's original assertion
        (`returncode == 5`, "database busy") was pinning exactly that: a read
        call failing because of someone else's write.

        CB-195 removed that accidental write for the steady state (the seed
        row already exists after the first open, so the insert is skipped),
        and the DIRECT, INTENDED consequence is that a purely reading verb no
        longer contends with a foreign writer at all — the whole reason the
        server exists is to let several agents work in parallel, and refusing
        a read merely because another agent is mid-write defeats that. So
        `query`, `stats` and `get` must now SUCCEED (exit 0) under the exact
        same held lock that used to make them fail.

        CB-14's original point — a verb that genuinely needs to write still
        classifies real contention cleanly, exit 5, no traceback — is
        unchanged and is still covered immediately above this test by `claim`
        and `release`, which hold under the identical lock (they write the
        claims ledger, so contention there is real, not accidental).
        """
        _finding(conn)
        blocker = db.connect(tmp_project)
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute("UPDATE findings SET category='held' WHERE id='CB-1'")
        try:
            for verb in (["query"], ["stats"], ["get", "CB-1"]):
                r = subprocess.run(
                    [sys.executable, "-m", "codebugs.cli", *verb],
                    cwd=tmp_project,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                assert r.returncode == 0, (verb, r.returncode, r.stdout, r.stderr)
                assert "Traceback" not in r.stderr, (verb, r.stderr)
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

    def test_26_format_ids_round_trips_into_release(self, tmp_project, conn):
        """worktree-finish.sh's release loop is only correct if this holds."""
        cb1 = _finding(conn)["id"]
        cb2 = _finding(conn)["id"]
        conn.close()
        for cb in (cb1, cb2):
            self._run(tmp_project, "claim", cb, "--holder", "br-a", "--holder-kind", "branch")

        listed = self._run(tmp_project, "claims", "--holder", "br-a", "--format", "ids")
        assert listed.returncode == 0
        ids = listed.stdout.split()
        assert sorted(ids) == sorted([cb1, cb2]), listed.stdout

        for cb in ids:
            rel = self._run(
                tmp_project, "release", cb, "--holder", "br-a", "--holder-kind", "branch"
            )
            assert rel.returncode == 0, rel.stdout + rel.stderr

        after = self._run(tmp_project, "claims", "--holder", "br-a", "--format", "ids")
        assert after.stdout.strip() == ""

    def test_31_format_table_renders_live_claims(self, tmp_project, conn):
        """CB-64: the DEFAULT format. claims.py passed (columns, rows) swapped into
        fmt.format_table(rows, columns) and built list rows, so any live claim
        crashed the table path with AttributeError ('str' has no .get).

        Assert headers AND representative values, not just the exit code —
        format_table fills misspelled keys silently via dict.get, so a test that
        only checks rc would pass on blank columns."""
        cb = _finding(conn)["id"]
        conn.close()
        ok = self._run(tmp_project, "claim", cb, "--holder", "br-tbl", "--holder-kind", "branch")
        assert ok.returncode == 0, ok.stderr

        out = self._run(tmp_project, "claims")  # no --format: table is the default
        assert out.returncode == 0, out.stdout + out.stderr
        for header in ("ENTITY", "HOLDER", "KIND", "REPO", "IDLE_S"):
            assert header in out.stdout, out.stdout
        assert cb in out.stdout, out.stdout
        assert "br-tbl" in out.stdout, out.stdout
        assert "branch" in out.stdout, out.stdout

    def test_32_format_table_empty_says_no_results(self, tmp_project):
        """CB-64's other half: with zero claims the swapped call printed blank
        lines (the header list is non-empty, so the '(no results)' early-return
        never fired). The formatter's contract for an empty row set is the
        literal '(no results)'."""
        out = self._run(tmp_project, "claims")
        assert out.returncode == 0, out.stdout + out.stderr
        assert out.stdout.strip() == "(no results)", repr(out.stdout)
