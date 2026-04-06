"""Tests for codebugs blockers (dependency tracking) layer."""

import pytest

from codebugs import db, reqs, blockers


@pytest.fixture
def tmp_project(tmp_path):
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _add_finding(conn, fid="CB-1", description="test finding", **kw):
    defaults = dict(severity="medium", category="bug", file="src/x.py")
    defaults.update(kw)
    return db.add_finding(conn, finding_id=fid, description=description, **defaults)


def _add_req(conn, rid="FR-001", description="test requirement", **kw):
    defaults = dict(section="core", priority="should", status="planned")
    defaults.update(kw)
    return reqs.add_requirement(conn, req_id=rid, description=description, **defaults)


# ---------------------------------------------------------------------------
# _detect_entity_type
# ---------------------------------------------------------------------------

class TestDetectEntityType:
    def test_finding(self):
        assert blockers._detect_entity_type("CB-1") == "finding"
        assert blockers._detect_entity_type("CB-999") == "finding"

    def test_requirement(self):
        assert blockers._detect_entity_type("FR-001") == "requirement"
        assert blockers._detect_entity_type("NFR-001") == "requirement"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown entity ID"):
            blockers._detect_entity_type("XY-1")


# ---------------------------------------------------------------------------
# _normalize_trigger_at
# ---------------------------------------------------------------------------

class TestNormalizeTriggerAt:
    def test_date_only(self):
        result = blockers._normalize_trigger_at("2026-04-10")
        assert result == "2026-04-10T00:00:00Z"

    def test_datetime_utc(self):
        result = blockers._normalize_trigger_at("2026-04-10T14:30:00Z")
        assert result == "2026-04-10T14:30:00Z"

    def test_datetime_with_offset(self):
        result = blockers._normalize_trigger_at("2026-04-10T14:30:00+02:00")
        assert result == "2026-04-10T12:30:00Z"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            blockers._normalize_trigger_at("not-a-date")


# ---------------------------------------------------------------------------
# add_blocker
# ---------------------------------------------------------------------------

class TestAddBlocker:
    def test_entity_resolved(self, conn):
        _add_finding(conn, "CB-1", "blocker bug")
        _add_finding(conn, "CB-2", "blocked bug")
        result = blockers.add_blocker(
            conn, item_id="CB-2", reason="needs CB-1 first", blocked_by="CB-1",
        )
        assert result["item_id"] == "CB-2"
        assert result["blocked_by"] == "CB-1"
        assert result["trigger_type"] == "entity_resolved"
        assert result["is_active"] is True
        assert result["item_description"] == "blocked bug"

    def test_date_trigger(self, conn):
        _add_finding(conn, "CB-1")
        result = blockers.add_blocker(
            conn, item_id="CB-1", reason="wait a week",
            trigger_type="date", trigger_at="2026-04-10",
        )
        assert result["trigger_type"] == "date"
        assert result["trigger_at"] == "2026-04-10T00:00:00Z"

    def test_manual_trigger(self, conn):
        _add_finding(conn, "CB-1")
        result = blockers.add_blocker(
            conn, item_id="CB-1", reason="manual hold",
        )
        assert result["trigger_type"] == "manual"
        assert result["blocked_by"] is None

    def test_cross_entity(self, conn):
        _add_finding(conn, "CB-1", "auth fix")
        _add_req(conn, "FR-001", "auth feature")
        result = blockers.add_blocker(
            conn, item_id="FR-001", reason="needs auth fix", blocked_by="CB-1",
        )
        assert result["item_type"] == "requirement"
        assert result["blocked_by_type"] == "finding"

    def test_validates_item_exists(self, conn):
        with pytest.raises(KeyError, match="Entity not found: CB-99"):
            blockers.add_blocker(conn, item_id="CB-99", reason="nope")

    def test_validates_blocked_by_exists(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(KeyError, match="Blocking entity not found: CB-99"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="nope", blocked_by="CB-99",
            )

    def test_rejects_self_block(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="cannot block itself"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="loop", blocked_by="CB-1",
            )

    def test_rejects_duplicate_entity(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r1", blocked_by="CB-1")
        with pytest.raises(ValueError, match="Duplicate blocker"):
            blockers.add_blocker(conn, item_id="CB-2", reason="r2", blocked_by="CB-1")

    def test_rejects_duplicate_date(self, conn):
        _add_finding(conn, "CB-1")
        blockers.add_blocker(
            conn, item_id="CB-1", reason="r1", trigger_type="date", trigger_at="2026-04-10",
        )
        with pytest.raises(ValueError, match="Duplicate blocker"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="r2", trigger_type="date", trigger_at="2026-04-10",
            )

    def test_entity_resolved_requires_blocked_by(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="blocked_by is required"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="r", trigger_type="entity_resolved",
            )

    def test_date_requires_trigger_at(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="trigger_at is required"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="r", trigger_type="date",
            )


# ---------------------------------------------------------------------------
# Dynamic evaluation — is_blocker_satisfied
# ---------------------------------------------------------------------------

class TestDynamicEvaluation:
    def test_entity_resolved_satisfied_when_fixed(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        b = blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        assert b["is_satisfied"] is False

        db.update_finding(conn, "CB-1", status="fixed")
        result = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True
        assert result["blockers"][0]["is_active"] is False

    def test_entity_resolved_reverts_on_reopen(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        db.update_finding(conn, "CB-1", status="fixed")
        result = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True

        db.update_finding(conn, "CB-1", status="open")
        result = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is False
        assert result["blockers"][0]["is_active"] is True

    def test_date_satisfied_when_past(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(
            conn, item_id="CB-1", reason="r",
            trigger_type="date", trigger_at="2020-01-01",
        )
        assert b["is_satisfied"] is True
        assert b["is_active"] is False

    def test_date_not_satisfied_when_future(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(
            conn, item_id="CB-1", reason="r",
            trigger_type="date", trigger_at="2099-12-31",
        )
        assert b["is_satisfied"] is False
        assert b["is_active"] is True

    def test_manual_satisfied_when_resolved(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(conn, item_id="CB-1", reason="manual hold")
        assert b["is_satisfied"] is False

        blockers.resolve_blocker(conn, blocker_id=b["id"], action="resolve")
        result = blockers.query_blockers(conn, item_id="CB-1", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True

    def test_requirement_terminal_statuses(self, conn):
        _add_req(conn, "FR-001", "req1")
        _add_req(conn, "FR-002", "req2")
        blockers.add_blocker(conn, item_id="FR-002", reason="r", blocked_by="FR-001")

        reqs.update_requirement(conn, "FR-001", status="implemented")
        result = blockers.query_blockers(conn, item_id="FR-002", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True


# ---------------------------------------------------------------------------
# query_blockers
# ---------------------------------------------------------------------------

class TestQueryBlockers:
    def test_active_only_default(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        db.update_finding(conn, "CB-1", status="fixed")

        active = blockers.query_blockers(conn, item_id="CB-2")
        assert active["total"] == 0

        all_ = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert all_["total"] == 1

    def test_reverse_lookup(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-2", reason="r1", blocked_by="CB-1")
        blockers.add_blocker(conn, item_id="CB-3", reason="r2", blocked_by="CB-1")

        result = blockers.query_blockers(conn, blocked_by="CB-1")
        assert result["total"] == 2
        ids = {b["item_id"] for b in result["blockers"]}
        assert ids == {"CB-2", "CB-3"}

    def test_filter_by_trigger_type(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-1", reason="wait", trigger_type="date", trigger_at="2099-12-31")
        blockers.add_blocker(conn, item_id="CB-2", reason="hold")

        result = blockers.query_blockers(conn, trigger_type="manual")
        assert result["total"] == 1
        assert result["blockers"][0]["item_id"] == "CB-2"


# ---------------------------------------------------------------------------
# check_blockers
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def test_actionable(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        db.update_finding(conn, "CB-1", status="fixed")

        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 1
        assert result["actionable"][0]["item_id"] == "CB-2"

    def test_partially_unblocked(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-3", reason="r1", blocked_by="CB-1")
        blockers.add_blocker(conn, item_id="CB-3", reason="r2", blocked_by="CB-2")
        db.update_finding(conn, "CB-1", status="fixed")

        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 0
        assert len(result["partially_unblocked"]) == 1
        assert result["partially_unblocked"][0]["item_id"] == "CB-3"
        assert result["partially_unblocked"][0]["remaining"] == 1

    def test_overdue_date_triggers(self, conn):
        _add_finding(conn, "CB-1")
        blockers.add_blocker(
            conn, item_id="CB-1", reason="old",
            trigger_type="date", trigger_at="2020-01-01",
        )
        result = blockers.check_blockers(conn)
        assert len(result["overdue_date_triggers"]) == 1
        assert result["overdue_date_triggers"][0]["item_id"] == "CB-1"


# ---------------------------------------------------------------------------
# resolve_blocker
# ---------------------------------------------------------------------------

class TestResolveBlocker:
    def test_cancel(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        b = blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        result = blockers.resolve_blocker(conn, blocker_id=b["id"], action="cancel")
        assert result["blocker"]["is_cancelled"] is True
        assert result["remaining_count"] == 0

    def test_resolve_manual(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(conn, item_id="CB-1", reason="hold")

        result = blockers.resolve_blocker(conn, blocker_id=b["id"], action="resolve")
        assert result["blocker"]["is_satisfied"] is True
        assert result["blocker"]["resolved_at"] is not None

    def test_resolve_non_manual_raises(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        b = blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        with pytest.raises(ValueError, match="only valid for manual"):
            blockers.resolve_blocker(conn, blocker_id=b["id"], action="resolve")

    def test_cancel_already_cancelled_raises(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(conn, item_id="CB-1", reason="hold")
        blockers.resolve_blocker(conn, blocker_id=b["id"], action="cancel")

        with pytest.raises(ValueError, match="already cancelled"):
            blockers.resolve_blocker(conn, blocker_id=b["id"], action="cancel")

    def test_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="Blocker not found"):
            blockers.resolve_blocker(conn, blocker_id=9999, action="cancel")


# ---------------------------------------------------------------------------
# Integration: get_unblocked_by
# ---------------------------------------------------------------------------

class TestGetUnblockedBy:
    def test_single_blocker_unblocked(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        db.update_finding(conn, "CB-1", status="fixed")

        result = blockers.get_unblocked_by(conn, "CB-1", "finding")
        assert len(result) == 1
        assert result[0]["item_id"] == "CB-2"
        assert result[0]["all_blockers_satisfied"] is True

    def test_partial_unblock(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-3", reason="r1", blocked_by="CB-1")
        blockers.add_blocker(conn, item_id="CB-3", reason="r2", blocked_by="CB-2")
        db.update_finding(conn, "CB-1", status="fixed")

        result = blockers.get_unblocked_by(conn, "CB-1", "finding")
        assert len(result) == 1
        assert result[0]["all_blockers_satisfied"] is False
        assert result[0]["remaining_blockers"] == 1


# ---------------------------------------------------------------------------
# Integration: get_deferred_item_ids / get_deferred_counts
# ---------------------------------------------------------------------------

class TestDeferredHelpers:
    def test_deferred_item_ids(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        ids = blockers.get_deferred_item_ids(conn, "finding")
        assert ids == {"CB-2"}

    def test_deferred_counts(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        counts = blockers.get_deferred_counts(conn, "finding")
        assert counts["deferred_count"] == 1
        assert counts["overdue_count"] == 0
        assert counts["currently_unblocked_count"] == 0

    def test_deferred_counts_with_resolved(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        db.update_finding(conn, "CB-1", status="fixed")

        counts = blockers.get_deferred_counts(conn, "finding")
        assert counts["deferred_count"] == 0
        assert counts["currently_unblocked_count"] == 1


# ---------------------------------------------------------------------------
# Full workflow
# ---------------------------------------------------------------------------

class TestFullWorkflow:
    def test_add_defer_fix_check_reopen(self, conn):
        _add_finding(conn, "CB-1", "auth bug")
        _add_finding(conn, "CB-2", "depends on auth fix")

        # Defer CB-2 on CB-1
        b = blockers.add_blocker(
            conn, item_id="CB-2", reason="depends on auth fix", blocked_by="CB-1",
        )
        assert b["is_active"] is True

        # CB-2 is deferred
        deferred = blockers.get_deferred_item_ids(conn, "finding")
        assert "CB-2" in deferred

        # Fix CB-1
        db.update_finding(conn, "CB-1", status="fixed")

        # CB-2 is now actionable
        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 1
        assert result["actionable"][0]["item_id"] == "CB-2"

        # CB-2 no longer deferred
        deferred = blockers.get_deferred_item_ids(conn, "finding")
        assert "CB-2" not in deferred

        # Reopen CB-1 — CB-2 is deferred again
        db.update_finding(conn, "CB-1", status="open")
        deferred = blockers.get_deferred_item_ids(conn, "finding")
        assert "CB-2" in deferred

        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 0
