"""Tests for the polymorphic entity resolver (entities.py)."""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import entities, findings, reqs
from codebugs.entities import EntityRef, entity_kind


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
    for k in entities.ENTITY_KINDS:
        assert entities._SAFE_IDENT.match(k.table)
        assert entities._SAFE_IDENT.match(k.sort_col)
        for col in k.readable_cols:
            assert entities._SAFE_IDENT.match(col)


def test_id_patterns_are_mutually_exclusive():
    sample_ids = ["CB-1", "FR-1", "NFR-1", "CB-9999", "FR-0", "NFR-42"]
    for sid in sample_ids:
        matches = [k.name for k in entities.ENTITY_KINDS if k.id_pattern.match(sid)]
        assert len(matches) == 1, f"{sid} matched {matches}"
