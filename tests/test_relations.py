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

import sqlite3

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

