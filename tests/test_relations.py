"""Tests for the finding_relations ledger.

Written before the implementation (TDD). Each test maps to a numbered item in
`.claude/plans/PLAN-finding-relations-2026-08-18.md` §7, and several exist
because a cross-model adversarial review found the opposite behaviour in an
earlier revision of that plan:

* test 4/5 — `duplicate_of` is DIRECTED (loser -> survivor). Canonicalising it
  inverts the survivor in 3 of 3 real cases in the live tracker.
* test 6 — endpoint validation is EXISTENCE, not liveness: 62.8% of the real
  corpus's edges point at closed cards, and a live card citing a closed one is
  the case this ledger exists to resolve.
* test 11 — `relate()` must run under `db.txn`, never a raw `BEGIN IMMEDIATE`,
  which commits an ambient transaction (CB-40).
"""

import argparse
import ast
import inspect
import os
import pathlib
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from codebugs import db, findings, relations


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _add(conn, fid, description="test finding", **kw):
    defaults = dict(severity="medium", category="bug", file="src/x.py")
    defaults.update(kw)
    return findings.add_finding(conn, finding_id=fid, description=description, **defaults)


@pytest.fixture
def cards(conn):
    for fid in ("CB-1", "CB-2", "CB-3"):
        _add(conn, fid)
    return conn


# --------------------------------------------------------------- schema ----

def test_self_edge_is_rejected(cards):
    """§7.1 — CHECK(src_id != dst_id)."""
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        relations.relate(cards, "CB-1", "related_to", "CB-1", source="test")


def test_unknown_relation_is_rejected(cards):
    """§7.2 — CHECK(rel IN ...). The vocabulary is enforced by the DB, not only
    by application code, matching the blockers idiom."""
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        relations.relate(cards, "CB-1", "vaguely_about", "CB-2", source="test")


def test_retraction_columns_are_paired(cards):
    """§7.8 — a tombstone without an actor is not an audited tombstone."""
    relations.relate(cards, "CB-1", "related_to", "CB-2", source="test")
    with pytest.raises(sqlite3.IntegrityError):
        with db.txn(cards):
            cards.execute(
                "UPDATE finding_relations SET retracted_at = '2026-01-01T00:00:00Z' "
                "WHERE retracted_at IS NULL"
            )


# ---------------------------------------------------------- orientation ----

def test_symmetric_relation_is_canonicalized(cards):
    """§7.3 — one edge per pair, whichever way the caller names it."""
    relations.relate(cards, "CB-2", "related_to", "CB-1", source="test")
    relations.relate(cards, "CB-1", "related_to", "CB-2", source="test")

    rows = relations.query_relations(cards, entity_id="CB-1")["relations"]
    assert len(rows) == 1
    assert (rows[0]["src_id"], rows[0]["dst_id"]) == ("CB-1", "CB-2")


def test_duplicate_of_is_not_canonicalized(cards):
    """§7.4 — `duplicate_of` names the LOSER, so orientation is data, not noise.

    Canonicalising it lexicographically would rewrite (CB-2, duplicate_of, CB-1)
    into (CB-1, duplicate_of, CB-2) -- i.e. swap which card survives. Real
    example from the live tracker: CB-2251 was merged INTO CB-2227, and
    "CB-2227" < "CB-2251".
    """
    relations.relate(cards, "CB-2", "duplicate_of", "CB-1", source="test")

    rows = relations.query_relations(cards, entity_id="CB-2")["relations"]
    assert len(rows) == 1
    assert rows[0]["src_id"] == "CB-2", "the loser must stay in src"
    assert rows[0]["dst_id"] == "CB-1", "the survivor must stay in dst"


def test_reciprocal_duplicate_of_is_rejected(cards):
    """§7.5 — both cards cannot be the loser."""
    relations.relate(cards, "CB-2", "duplicate_of", "CB-1", source="test")
    with pytest.raises(ValueError, match="reciprocal|already"):
        relations.relate(cards, "CB-1", "duplicate_of", "CB-2", source="test")


# ------------------------------------------------------------ endpoints ----

def test_absent_endpoint_is_rejected(cards):
    """§7.6a — the FK that is not enforced. `db._open` never enables
    PRAGMA foreign_keys, so this is validated in the application layer."""
    with pytest.raises(ValueError, match="CB-999"):
        relations.relate(cards, "CB-1", "related_to", "CB-999", source="test")


def test_closed_endpoint_is_accepted(cards):
    """§7.6b — EXISTENCE, not liveness. 62.8% of the real corpus's edges point
    at closed cards; filtering on liveness would discard the very class the
    ledger exists to resolve."""
    findings.update_finding(cards, "CB-3", status="fixed")

    row = relations.relate(cards, "CB-1", "related_to", "CB-3", source="test")

    assert row["dst_id"] == "CB-3"
    assert relations.query_relations(cards, entity_id="CB-1")["count"] == 1


# ----------------------------------------------------------- retraction ----

def test_unrelate_tombstones_and_allows_re_relating(cards):
    """§7.7 — retraction is a tombstone, never a DELETE, and the partial unique
    index (WHERE retracted_at IS NULL) then permits the pair again."""
    relations.relate(cards, "CB-1", "related_to", "CB-2", source="test")
    relations.unrelate(cards, "CB-1", "related_to", "CB-2",
                       retracted_by="tester", reason="wrong call")

    live = relations.query_relations(cards, entity_id="CB-1")
    assert live["count"] == 0

    everything = relations.query_relations(cards, entity_id="CB-1", include_retracted=True)
    assert everything["count"] == 1
    assert everything["relations"][0]["retracted_by"] == "tester"

    relations.relate(cards, "CB-1", "related_to", "CB-2", source="test")
    assert relations.query_relations(cards, entity_id="CB-1")["count"] == 1


# ------------------------------------------------------- contradictions ----

def test_duplicate_of_refused_when_distinct_from_is_live(cards):
    """§7.9 — the two assert opposite things about the same pair."""
    relations.relate(cards, "CB-1", "distinct_from", "CB-2", source="test")
    with pytest.raises(ValueError, match="distinct_from"):
        relations.relate(cards, "CB-2", "duplicate_of", "CB-1", source="test")


def test_distinct_from_refused_when_duplicate_of_is_live(cards):
    """§7.9, the other direction."""
    relations.relate(cards, "CB-2", "duplicate_of", "CB-1", source="test")
    with pytest.raises(ValueError, match="duplicate_of"):
        relations.relate(cards, "CB-1", "distinct_from", "CB-2", source="test")


def test_retracted_contradiction_does_not_block(cards):
    """A tombstoned edge asserts nothing, so it must not veto its opposite."""
    relations.relate(cards, "CB-1", "distinct_from", "CB-2", source="test")
    relations.unrelate(cards, "CB-1", "distinct_from", "CB-2",
                       retracted_by="tester", reason="mistaken")

    row = relations.relate(cards, "CB-2", "duplicate_of", "CB-1", source="test")
    assert row["rel"] == "duplicate_of"


# ---------------------------------------------------------- exclusions ----

def test_recurrence_of_is_refused_as_core_owned(cards):
    """§7.10 — `recurrence_of` is core-owned (_RESERVED_META_KEYS,
    findings.py:219) and guards a spoofing attack."""
    with pytest.raises(ValueError, match="recurrence_of"):
        relations.relate(cards, "CB-1", "recurrence_of", "CB-2", source="test")


def test_blocked_by_is_refused_and_points_at_the_blockers_module(cards):
    """§7.10 — finding->finding blocking already ships with lifecycle semantics."""
    with pytest.raises(ValueError, match="blockers"):
        relations.relate(cards, "CB-1", "blocked_by", "CB-2", source="test")


# -------------------------------------------------------- transactions ----

def test_relate_does_not_commit_an_ambient_transaction(cards):
    """§7.11 — CB-40 regression.

    A raw `BEGIN IMMEDIATE` (via `conn.isolation_level = None`) COMMITS whatever
    the caller had open. `merge.py:257` and `capacity.py:214` both carry notes
    saying the raw form was removed for exactly this reason; `db.txn` is the
    reentrant abstraction that makes the inner frame a no-op.
    """
    saved = cards.isolation_level
    cards.isolation_level = None
    try:
        cards.execute("BEGIN IMMEDIATE")
        relations.relate(cards, "CB-1", "related_to", "CB-2", source="test")
        # The caller owns this transaction and discards it. If relate() had
        # committed on our behalf, the insert would already be durable and this
        # ROLLBACK would have nothing to undo.
        cards.execute("ROLLBACK")
    finally:
        cards.isolation_level = saved

    assert relations.query_relations(cards, entity_id="CB-1")["count"] == 0, (
        "relate() committed the caller's transaction — CB-40 has reopened"
    )


# ------------------------------------------------------------ idempotency --

def test_re_relating_a_live_edge_is_a_no_op_that_keeps_the_original(cards):
    """§7.12 — a second opinion is a note append, not an overwrite."""
    first = relations.relate(cards, "CB-1", "related_to", "CB-2",
                             source="goldset", note="original reasoning")
    second = relations.relate(cards, "CB-1", "related_to", "CB-2",
                              source="llm", note="different reasoning")

    assert second["id"] == first["id"]
    assert second["source"] == "goldset"
    assert second["note"] == "original reasoning"
    assert relations.query_relations(cards, entity_id="CB-1")["count"] == 1


# -------------------------------------------------------------- queries ----

def test_query_finds_edges_in_both_directions(cards):
    """A directed edge must be visible from either endpoint."""
    relations.relate(cards, "CB-2", "duplicate_of", "CB-1", source="test")

    assert relations.query_relations(cards, entity_id="CB-2")["count"] == 1
    assert relations.query_relations(cards, entity_id="CB-1")["count"] == 1


def test_query_filters_by_relation(cards):
    """`rel=` is what answers active_suppressions (live distinct_from edges)."""
    relations.relate(cards, "CB-1", "related_to", "CB-2", source="test")
    relations.relate(cards, "CB-1", "distinct_from", "CB-3", source="test")

    suppressions = relations.query_relations(cards, rel="distinct_from")
    assert suppressions["count"] == 1
    assert suppressions["relations"][0]["dst_id"] == "CB-3"


# ------------------------------------------------------------- registry ----

def test_relations_is_registered_in_all_three_hardcoded_lists():
    """§7.13 — there is no discovery. Omitting any one of these ships a silently
    absent feature, and `CREATE TABLE IF NOT EXISTS` raises no error to catch."""
    import inspect

    from codebugs import cli, server

    assert "relations" in inspect.getsource(db._ensure_modules_loaded)
    assert "relations" in server.SERVER_NAMES
    assert "relations" in inspect.getsource(cli.main)


def test_schema_is_registered_and_table_exists(conn):
    """The table is created by the normal connect path, not by a test helper."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='finding_relations'"
    ).fetchone()
    assert row is not None


# ------------------------------------------- active_suppressions (CB-62) ----


class TestActiveSuppressionsProbe:
    """`active_suppressions` must not answer "nobody declared anything" about a
    ledger it could have read."""

    @staticmethod
    def _bare():
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        return c

    @pytest.mark.parametrize(
        "ddl,label",
        [
            ("CREATE VIEW finding_relations AS SELECT 1 AS src_id, 1 AS dst_id",
             "a VIEW of the name"),
            ("CREATE TEMP TABLE finding_relations (src_id TEXT, dst_id TEXT)",
             "a TEMP table, which lives in sqlite_temp_master"),
            ("CREATE TABLE Finding_Relations (src_id TEXT, dst_id TEXT)",
             "a name created in another case"),
        ],
    )
    def test_premise_sqlite_master_disagrees_with_name_resolution(self, ddl, label):
        """PREMISE, measured rather than argued: reading `sqlite_master` is NOT
        the same question as resolving a name in a SELECT.

        Each of these is queryable while
        `SELECT 1 FROM sqlite_master WHERE type='table' AND name=...` finds
        nothing — so a probe written that way would return the empty set about
        a readable ledger, which is the silent-empty-answer failure the guard
        exists to prevent. `PRAGMA table_info` agrees with the SELECT in every
        case, which is why it is what the guard uses. A SQLite release that
        changed either behaviour turns this red instead of quietly making the
        comment above `active_suppressions`' probe false.
        """
        c = self._bare()
        c.execute(ddl)
        c.execute("SELECT * FROM finding_relations LIMIT 1").fetchall()  # queryable
        assert c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='finding_relations'"
        ).fetchone() is None, f"premise gone: sqlite_master now sees {label}"
        assert c.execute("PRAGMA table_info('finding_relations')").fetchall(), (
            f"the guard's probe must see {label}"
        )
        c.close()

    def test_the_guard_reads_a_ledger_a_catalogue_probe_would_have_missed(self):
        """The premise tests above show that `sqlite_master` and name resolution
        disagree; this one shows the GUARD is on the right side of that
        disagreement.

        Reverting its probe to the catalogue read turns this red and leaves
        those premise tests green — a premise pinned without its consumer is a
        premise, not a gate, which is the distinction this repository keeps
        paying for.
        """
        c = self._bare()
        c.execute(
            "CREATE TEMP TABLE finding_relations "
            "(id INTEGER PRIMARY KEY, src_id TEXT, rel TEXT, dst_id TEXT, "
            " created_at TEXT, source TEXT, note TEXT, retracted_at TEXT, "
            " retracted_by TEXT, retracted_reason TEXT)"
        )
        c.execute(
            "INSERT INTO finding_relations (src_id, rel, dst_id, created_at, source) "
            "VALUES ('CB-9', 'distinct_from', 'CB-10', '2026-01-01T00:00:00Z', 't')"
        )
        assert relations.active_suppressions(c) == {("CB-10", "CB-9")}
        c.close()

    def test_an_absent_ledger_is_an_empty_set_not_an_error(self):
        """The §4(3) decision itself: no ledger means nobody declared anything."""
        c = self._bare()
        assert relations.active_suppressions(c) == set()
        c.close()

    def test_a_present_ledger_is_read_and_canonicalised(self, conn):
        f1 = findings.add_finding(conn, description="one side of a declared pair",
                                  severity="low", category="correctness",
                                  file="a.py", new_category=True)["id"]
        f2 = findings.add_finding(conn, description="the other side of that pair",
                                  severity="low", category="correctness",
                                  file="a.py")["id"]
        relations.relate(conn, f1, "distinct_from", f2, source="test")
        assert relations.active_suppressions(conn) == {tuple(sorted((f1, f2)))}


# ------------------------------------------------- CLI error boundary ----


class TestRelationsCliErrorBoundary:
    """CB-193. All three `relations-*` handlers caught NOTHING, so a domain
    `ValueError` reached the user as a raw traceback instead of the one stderr
    line every other verb in the package prints. `grep -c domain_errors
    src/codebugs/relations.py` was 0 while CLAUDE.md's error-handling rule says
    the rule is encoded exactly once, in `cli.domain_errors()`, and every CLI
    handler that touches a domain call routes through it.

    THERE ARE THREE TESTS BECAUSE THERE ARE THREE HANDLERS. A check that
    validates elements cannot validate their composition: with one test, an
    unwrapped second verb passes unnoticed, which is precisely the shape this
    repository keeps paying for.

    NON-VACUITY, stated because both obvious assertions are satisfied by the
    UNFIXED tree: exit code 1 is what an uncaught exception already produces,
    and the message text already appears inside its traceback. Neither
    discriminates on its own, and both are kept only to pin the contract
    jointly. The real discriminators are `"Traceback" not in stderr` and the
    `codebugs: ` prefix, which only the wrapper can produce.

    THE CARD'S SECOND CLAIM IS FALSE AND IS NOT TESTED AS A FIX. It said the
    handlers also "leak the connection"; measured, they do not — all three were
    already `conn = db.connect()` / `try: ... finally: conn.close()`, and
    `finally` runs on the exception path too. The closure test below therefore
    PINS EXISTING BEHAVIOUR and is green on both sides of this change; it is
    named and documented as such so a later reader cannot mistake it for a
    regression guard.
    """

    # The subprocess must read THIS checkout, not whichever tree happens to be
    # the process cwd — a worktree's suite validating main's source is a green
    # run over code nobody touched. Anchoring on __file__ makes the tree the
    # test lives in the tree the subprocess imports, by construction.
    _SRC = str(pathlib.Path(__file__).resolve().parents[1] / "src")

    @classmethod
    def _cli(cls, project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            capture_output=True,
            text=True,
            cwd=str(project),
            env={**os.environ, "PYTHONPATH": cls._SRC},
        )

    def test_relate_reports_a_missing_endpoint_as_one_line_not_a_traceback(self, tmp_project):
        """The card's own reproduction: relate against a tracker with no CB-1."""
        r = self._cli(tmp_project, "relations-relate", "CB-1", "distinct_from", "CB-2",
                      "--source", "probe")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert r.stderr.strip() == "codebugs: No such finding: CB-1", r.stderr

    def test_unrelate_reports_a_missing_edge_as_one_line_not_a_traceback(self, tmp_project):
        """Retracting an edge that does not exist is `unrelate`'s own ValueError."""
        r = self._cli(tmp_project, "relations-unrelate", "CB-1", "distinct_from", "CB-2",
                      "--retracted-by", "probe")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert r.stderr.strip() == (
            "codebugs: No live distinct_from edge from CB-1 to CB-2 to retract."
        ), r.stderr

    def test_query_reports_a_bad_relation_as_one_line_not_a_traceback(
        self, tmp_project, monkeypatch, capsys
    ):
        """The third handler, and it is exercised DIFFERENTLY on purpose.

        `relations-query` declares `--rel` with `choices=list(RELATIONS)`, so
        argparse refuses an unknown value with its own exit code 2 before the
        handler runs: measured, `query_relations`' only `ValueError` path —
        `_validate_rel` — is UNREACHABLE from a command line today. Driving the
        handler directly is therefore not a shortcut around the subprocess
        convention, it is the only way to reach the arm at all. The wrapper
        still belongs here: it is what makes the third verb's behaviour equal
        to its siblings' the day this verb gains an argument argparse cannot
        pre-validate.

        The discriminator is the exception TYPE: unwrapped, `ValueError`
        escapes; wrapped, `domain_errors` prints one line and raises
        `SystemExit(1)`.
        """
        monkeypatch.chdir(tmp_project)
        args = argparse.Namespace(entity_id=None, rel="no_such_relation",
                                  include_retracted=False)

        with pytest.raises(SystemExit) as excinfo:
            relations._cmd_relations_query(args)

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("codebugs: Unknown relation 'no_such_relation'"), err

    def test_the_connection_is_closed_on_both_outcomes(self, tmp_project, monkeypatch):
        """PINS EXISTING BEHAVIOUR — green before and after CB-193's fix.

        The card claimed the unwrapped handlers leaked their connection. They
        did not: `finally: conn.close()` was already there and runs on the
        exception path. This exists so that the claim is answered by a test
        rather than by prose, and so that a future edit which hoists the domain
        call out of the `try` is caught. It is NOT a regression guard for the
        wrapper, and must not be read as one.

        Both outcomes are covered because only one of them is interesting:
        `SystemExit` raised INSIDE the `with` still has to travel through the
        same `finally`.

        THE REFUSAL PATH ACCEPTS EITHER EXCEPTION, and that is what makes the
        claim above true rather than merely asserted. Wrapped, the handler
        raises `SystemExit`; unwrapped, the domain `ValueError` escapes. Pinning
        `SystemExit` alone would silently make this a second regression guard
        for the wrapper — measured: it went red under the mutant that removes
        `_cmd_relations_relate`'s wrapper, which is exactly the "green on both
        sides" property this test claims to have.
        """
        opened: list[sqlite3.Connection] = []
        real_connect = db.connect

        def spy(*a, **kw):
            c = real_connect(*a, **kw)
            opened.append(c)
            return c

        monkeypatch.chdir(tmp_project)
        monkeypatch.setattr(db, "connect", spy)

        # Refusal path: wrapped this is SystemExit, unwrapped it is ValueError.
        # Either way the `finally` below is what this test is about.
        with pytest.raises((SystemExit, ValueError)):
            relations._cmd_relations_relate(
                argparse.Namespace(src_id="CB-1", rel="distinct_from", dst_id="CB-2",
                                   source="probe", note=None)
            )
        # Success path: an empty query is a perfectly good answer.
        relations._cmd_relations_query(
            argparse.Namespace(entity_id=None, rel=None, include_retracted=False)
        )

        assert len(opened) == 2, "both handlers must have gone through db.connect"
        for c in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                c.execute("SELECT 1")

    @pytest.mark.parametrize("handler_name", [
        "_cmd_relations_relate",
        "_cmd_relations_unrelate",
        "_cmd_relations_query",
    ])
    def test_the_print_stays_outside_the_wrapper(self, handler_name):
        """Placement is pinned STRUCTURALLY, because behaviour cannot see it.

        `print` into a closed stream raises `ValueError` (measured). Inside
        `domain_errors` that would be caught and reported as bad input at exit
        1 — over a write that already committed, which is the CB-15/CB-16 lie
        arriving through the mechanism built to prevent it. It is unreachable
        TODAY only because CB-134's gate refuses a closed stdout at the process
        entry, so no test can discriminate the placement by running the code:
        moving the `print` under the `with` passes all three verb tests, the
        connection pin, and the whole suite. This repository's answer to that
        shape is a structural pin (CB-41's SQL-side deadline, CB-174's
        "Placement is pinned STRUCTURALLY"), not a comment asking the next
        editor to be careful.

        NON-VACUITY: the second assertion is what keeps this honest — deleting
        the `print` outright would satisfy "no print inside the wrapper" while
        removing the handler's entire output.
        """
        handler = getattr(relations, handler_name)
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))

        def _calls(node):
            return {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }

        wrappers = [
            w for w in ast.walk(tree)
            if isinstance(w, ast.With)
            and any(
                isinstance(i.context_expr, ast.Call)
                and isinstance(i.context_expr.func, ast.Name)
                and i.context_expr.func.id == "domain_errors"
                for i in w.items
            )
        ]
        assert len(wrappers) == 1, f"{handler_name} must wrap exactly one region"

        inside = set().union(*(_calls(stmt) for stmt in wrappers[0].body))
        assert "print" not in inside, (
            f"{handler_name} prints INSIDE domain_errors: a post-commit reporting "
            "failure would be reported as bad input"
        )
        assert "print" in _calls(tree), f"{handler_name} must still print its result"
