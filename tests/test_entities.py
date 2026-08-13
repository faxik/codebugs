"""Tests for the polymorphic entity resolver (entities.py)."""

from __future__ import annotations

import sqlite3

import pytest

from dataclasses import replace

from codebugs import entities, findings, reqs
from codebugs.entities import EntityRef, entity_kind
from codebugs.types import PRIORITIES, SEVERITIES, is_sql_identifier


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    reqs.ensure_schema(c)
    yield c
    c.close()


def _add_finding(conn, fid, *, status="open", description="a bug", severity="high"):
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO findings (id, severity, category, file, status, description, created_at, updated_at) "
        "VALUES (?, ?, 'bug', 'x.py', ?, ?, ?, ?)",
        (fid, severity, status, description, now, now),
    )
    conn.commit()


def _add_requirement(conn, rid, *, status="planned", description="a req", priority="should"):
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO requirements (id, section, description, priority, status, created_at, updated_at) "
        "VALUES (?, 'S', ?, ?, ?, ?, ?)",
        (rid, description, priority, status, now, now),
    )
    conn.commit()


# --- kind detection ---


def test_of_detects_finding():
    assert EntityRef.of("CB-5").kind.name == "finding"


def test_of_detects_requirement_fr():
    assert EntityRef.of("FR-12").kind.name == "requirement"


def test_of_detects_requirement_nfr():
    assert EntityRef.of("NFR-3").kind.name == "requirement"


def test_of_rejects_unknown_prefix():
    with pytest.raises(ValueError):
        EntityRef.of("XYZ-1")


def test_entity_kind_by_name():
    assert entity_kind("finding").table == "findings"
    assert entity_kind("requirement").table == "requirements"


def test_entity_kind_unknown_name_raises():
    # milestones' "bug"/"external" vocabulary is NOT the entity vocabulary
    with pytest.raises(KeyError):
        entity_kind("bug")


# --- reads against a real DB ---


def test_exists(conn):
    _add_finding(conn, "CB-1")
    assert EntityRef.of("CB-1").exists(conn) is True
    assert EntityRef.of("CB-2").exists(conn) is False


def test_status(conn):
    _add_finding(conn, "CB-1", status="in_progress")
    assert EntityRef.of("CB-1").status(conn) == "in_progress"
    assert EntityRef.of("CB-2").status(conn) is None


def test_description(conn):
    _add_requirement(conn, "FR-1", description="must do X")
    assert EntityRef.of("FR-1").description(conn) == "must do X"


def test_is_resolved_finding(conn):
    _add_finding(conn, "CB-1", status="open")
    _add_finding(conn, "CB-2", status="fixed")
    assert EntityRef.of("CB-1").is_resolved(conn) is False
    assert EntityRef.of("CB-2").is_resolved(conn) is True


def test_is_resolved_requirement(conn):
    _add_requirement(conn, "FR-1", status="planned")
    _add_requirement(conn, "FR-2", status="implemented")
    assert EntityRef.of("FR-1").is_resolved(conn) is False
    assert EntityRef.of("FR-2").is_resolved(conn) is True


def test_is_resolved_missing_is_false(conn):
    # missing entity ⇒ not resolved (never raises)
    assert EntityRef.of("CB-999").is_resolved(conn) is False


def test_require_present(conn):
    _add_finding(conn, "CB-1")
    assert EntityRef.of("CB-1").require(conn) is None  # no raise


def test_require_missing_raises(conn):
    with pytest.raises(KeyError):
        EntityRef.of("CB-1").require(conn)


# --- field() per-kind allowlist (schema-validity == injection guard) ---


def test_field_allowed_column(conn):
    _add_finding(conn, "CB-1", severity="critical")
    assert EntityRef.of("CB-1").field(conn, name="severity") == "critical"


def test_field_rejects_column_not_on_kind(conn):
    # 'priority' exists on requirements, NOT findings — must be rejected before SQL
    _add_finding(conn, "CB-1")
    with pytest.raises(ValueError):
        EntityRef.of("CB-1").field(conn, name="priority")


def test_field_rejects_arbitrary_injection(conn):
    _add_finding(conn, "CB-1")
    with pytest.raises(ValueError):
        EntityRef.of("CB-1").field(conn, name="id FROM findings; DROP TABLE findings; --")


# --- registry integrity ---


def test_all_registry_identifiers_are_safe():
    """The registry conforms — now via the public predicate, not a private pattern.

    Importing the module at all proves this (``__post_init__`` runs on every entry),
    so this test is a readable restatement rather than the enforcement (CB-22).
    """
    for k in entities.ENTITY_KINDS:
        assert is_sql_identifier(k.table)
        assert is_sql_identifier(k.sort_col)
        for col in k.readable_cols:
            assert is_sql_identifier(col)


class TestEntityKindIdentifierGuard:
    """CB-22: every field reaching SQL as an identifier is refused at construction.

    Before this, `_SAFE_IDENT` carried a comment claiming to guard table / sort_col /
    readable column and guarded only `sort_col`, inside `order_by()`. A kind built by
    `dataclasses.replace` — which the suite itself does — walked straight into an
    f-string.
    """

    def test_a_non_identifier_table_is_refused(self):
        with pytest.raises(ValueError, match=r"\.table is not a bare column identifier"):
            replace(entity_kind("finding"), table="findings WHERE 1=1 OR ''='")

    def test_a_non_identifier_sort_col_is_refused_at_construction(self):
        with pytest.raises(ValueError, match=r"\.sort_col is not a bare column identifier"):
            replace(entity_kind("finding"), sort_col="id; DROP TABLE t", sort_vocabulary=None)

    # Both ends of sort order, deliberately. The guard iterates a set, and a
    # mutation validating only its FIRST member survived a single-payload test:
    # "(SELECT ...)" starts with 0x28, so it sorts BEFORE every real column name
    # and a check-one implementation still caught it. The second payload sorts
    # after them, so together they pin "every member", not "some member".
    @pytest.mark.parametrize(
        "payload",
        ["(SELECT meta FROM findings)", "zz_evil; DROP TABLE findings"],
        ids=["sorts-first", "sorts-last"],
    )
    def test_a_non_identifier_readable_col_is_refused(self, payload):
        """The member-by-member case. `_read`'s membership check passes such a value —
        it guards the caller's argument against the allowlist, never the allowlist's
        own contents — so before CB-22 `field()` returned the `meta` column through an
        allowlist that does not contain it."""
        base = entity_kind("finding")
        with pytest.raises(ValueError, match=r"readable_cols member is not a bare"):
            replace(base, readable_cols=base.readable_cols | {payload})

    def test_a_readable_col_leak_cannot_reach_sql(self, conn):
        """End-to-end: the exfiltration path from the CB-22 reproducer is closed."""
        _add_finding(conn, "CB-1")
        leak = "(SELECT meta FROM findings)"
        with pytest.raises(ValueError):
            kind = replace(entity_kind("finding"), readable_cols=frozenset({"id", leak}))
            EntityRef("CB-1", kind).field(conn, name=leak)

    def test_sort_col_is_validated_even_when_a_vocabulary_is_declared(self):
        """Found by cross-model review as a SURVIVING mutation, not by a failure.

        Every other negative `sort_col` case sets `sort_vocabulary=None`, so an
        implementation validating `sort_col` only on the None branch passed the whole
        suite — while violating the stated invariant on both production kinds, which
        both declare a vocabulary."""
        with pytest.raises(ValueError, match=r"\.sort_col is not a bare column identifier"):
            replace(entity_kind("finding"), sort_col="severity; DROP TABLE t")

    def test_a_trailing_newline_is_not_an_identifier(self):
        """`re` `$` matches before a trailing newline, so the anchored `^...$` pattern
        this guard inherited accepted "findings\\n". `fullmatch` is what makes the
        claim true."""
        assert not is_sql_identifier("id\n")
        with pytest.raises(ValueError, match=r"\.table is not a bare column identifier"):
            replace(entity_kind("finding"), table="findings\n")

    def test_a_well_formed_kind_still_constructs(self):
        """The guard must not refuse legitimate kinds — the vacuous-pass direction."""
        k = replace(entity_kind("finding"), table="widgets", sort_col="id", sort_vocabulary=None)
        assert (k.table, k.sort_col) == ("widgets", "id")


def test_id_patterns_are_mutually_exclusive():
    sample_ids = ["CB-1", "FR-1", "NFR-1", "CB-9999", "FR-0", "NFR-42"]
    for sid in sample_ids:
        matches = [k.name for k in entities.ENTITY_KINDS if k.id_pattern.match(sid)]
        assert len(matches) == 1, f"{sid} matched {matches}"


class TestEntityKindOrderBy:
    """CB-20: sort_col is TEXT, so ordering by it directly sorts alphabetically."""

    def test_findings_rank_by_declared_severity(self):
        sql, params = entity_kind("finding").order_by()
        assert params == list(SEVERITIES)
        assert sql.startswith("CASE severity ")

    def test_requirements_rank_by_declared_priority(self):
        sql, params = entity_kind("requirement").order_by()
        assert params == list(PRIORITIES)
        assert sql.startswith("CASE priority ")

    def test_every_registered_kind_declares_its_precedence(self):
        """The field is required, so this cannot silently regress — but a kind
        whose sort_col IS a vocabulary column must not declare None."""
        for kind in entities.ENTITY_KINDS:
            assert kind.sort_vocabulary is not None, (
                f"{kind.name} sorts by {kind.sort_col!r} with no declared precedence"
            )

    def test_a_non_vocabulary_sort_col_orders_directly(self):
        kind = replace(entity_kind("finding"), sort_col="id", sort_vocabulary=None)
        assert kind.order_by() == ("id", [])

    # The old "a non-identifier sort_col is refused by order_by()" test moved to
    # TestEntityKindIdentifierGuard: CB-22 pushed that refusal to construction, so
    # such a kind can no longer exist to have order_by() called on it.
