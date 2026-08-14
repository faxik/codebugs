"""Tests for codebugs milestones (Phase 1: foundation + auto-routing)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading

import pytest

from codebugs import blockers, db, findings, milestones, reqs


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _add_finding(conn, fid="CB-1", description="bug", **kw):
    defaults = dict(severity="medium", category="bug", file="src/x.py")
    defaults.update(kw)
    return findings.add_finding(conn, finding_id=fid, description=description, **defaults)


def _add_req(conn, rid="FR-001", description="req", **kw):
    defaults = dict(section="core", priority="should", status="planned")
    defaults.update(kw)
    return reqs.add_requirement(conn, req_id=rid, description=description, **defaults)


# ---------------------------------------------------------------------------
# Schema + seeds
# ---------------------------------------------------------------------------

class TestSchema:
    def test_seed_rows_present(self, conn):
        rows = milestones.list_milestones(conn)
        ids = {r["id"] for r in rows}
        assert "stream/triage" in ids
        assert "stream/maintenance" in ids
        assert "stream/security" in ids
        assert "release/1.1" in ids

    def test_seed_kinds(self, conn):
        rows = {r["id"]: r for r in milestones.list_milestones(conn)}
        assert rows["stream/triage"]["kind"] == "stream"
        assert rows["release/1.1"]["kind"] == "release"

    def test_seeds_idempotent(self, conn, tmp_project):
        # Open a second connection; seeds should not duplicate.
        c2 = db.connect(tmp_project)
        try:
            rows = milestones.list_milestones(c2)
            ids = [r["id"] for r in rows]
            assert ids.count("stream/triage") == 1
        finally:
            c2.close()

    def test_check_constraints_reject_bad_kind(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO milestones (id, kind, state, description, created_at)
                   VALUES ('bad/x', 'invalid', 'open', '', '2026-01-01T00:00:00Z')"""
            )

    def test_check_constraints_reject_bad_item_kind(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO milestone_items
                   (milestone_id, item_kind, item_ref, size, priority, status,
                    acceptance, meta_json, created_at, updated_at)
                   VALUES ('stream/triage', 'banana', 'X', 'small', 100, 'open',
                           '', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
            )


# ---------------------------------------------------------------------------
# Milestone CRUD
# ---------------------------------------------------------------------------

class TestMilestoneCRUD:
    def test_create_release(self, conn):
        m = milestones.create_milestone(
            conn, id="release/1.2", kind="release",
            description="Second release", target_date="2026-09-30",
        )
        assert m["id"] == "release/1.2"
        assert m["kind"] == "release"
        assert m["state"] == "open"
        assert m["target_date"] == "2026-09-30"

    def test_create_duplicate_rejected(self, conn):
        with pytest.raises(ValueError, match="already exists"):
            milestones.create_milestone(
                conn, id="release/1.1", kind="release", description="x",
            )

    def test_create_invalid_kind(self, conn):
        with pytest.raises(ValueError, match="Invalid kind"):
            milestones.create_milestone(conn, id="x/1", kind="other", description="x")

    def test_update_description(self, conn):
        m = milestones.update_milestone(
            conn, id="release/1.1", description="Updated desc",
        )
        assert m["description"] == "Updated desc"

    def test_update_target_date(self, conn):
        m = milestones.update_milestone(
            conn, id="release/1.1", target_date="2026-07-01",
        )
        assert m["target_date"] == "2026-07-01"

    def test_update_invalid_state(self, conn):
        with pytest.raises(ValueError, match="Invalid state"):
            milestones.update_milestone(conn, id="release/1.1", state="exploded")

    def test_list_filter_by_kind(self, conn):
        rows = milestones.list_milestones(conn, kind="stream")
        assert all(r["kind"] == "stream" for r in rows)
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# milestone_status rollup
# ---------------------------------------------------------------------------

class TestMilestoneStatus:
    def test_empty_release(self, conn):
        s = milestones.get_milestone_status(conn, id="release/1.1")
        assert s["total_items"] == 0
        assert s["by_status"]["open"] == 0
        assert s["branch_only_items"] == []

    def test_counts_by_status(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="bug", item_ref="CB-1", size="small",
        )
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="bug", item_ref="CB-2", size="small",
        )
        milestones.set_item_status(
            conn, item_ref="CB-2", status="done", commit="abc123",
        )
        s = milestones.get_milestone_status(conn, id="release/1.1")
        assert s["total_items"] == 2
        assert s["by_status"]["open"] == 1
        assert s["by_status"]["done"] == 1
        assert s["done_items"] == 1

    def test_days_to_target(self, conn):
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
        milestones.update_milestone(conn, id="release/1.1", target_date=future)
        s = milestones.get_milestone_status(conn, id="release/1.1")
        assert s["days_to_target"] == 10


# ---------------------------------------------------------------------------
# Item CRUD + phantom-ID validation
# ---------------------------------------------------------------------------

class TestItemCRUD:
    def test_add_bug_requires_existing_finding(self, conn):
        with pytest.raises(ValueError, match="Unknown bug"):
            milestones.add_milestone_item(
                conn, milestone_id="release/1.1",
                item_kind="bug", item_ref="CB-99999",
            )

    def test_add_requirement_requires_existing_req(self, conn):
        with pytest.raises(ValueError, match="Unknown requirement"):
            milestones.add_milestone_item(
                conn, milestone_id="release/1.1",
                item_kind="requirement", item_ref="FR-99999",
            )

    def test_add_external_accepts_freeform(self, conn):
        item = milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="external", item_ref="external://jira/ABC-1",
        )
        assert item["item_kind"] == "external"
        assert item["item_ref"] == "external://jira/ABC-1"

    def test_add_large_without_acceptance_rejected(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="acceptance is required"):
            milestones.add_milestone_item(
                conn, milestone_id="release/1.1",
                item_kind="bug", item_ref="CB-1", size="large",
            )

    def test_add_large_with_acceptance(self, conn):
        _add_finding(conn, "CB-1")
        item = milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="bug", item_ref="CB-1", size="large",
            acceptance="All tests pass",
        )
        assert item["acceptance"] == "All tests pass"

    def test_add_duplicate_in_same_milestone(self, conn):
        _add_finding(conn, "CB-1")
        # auto-router already put CB-1 in stream/triage.
        with pytest.raises(ValueError, match="already attached"):
            milestones.add_milestone_item(
                conn, milestone_id="stream/triage",
                item_kind="bug", item_ref="CB-1",
            )

    def test_add_invalid_size(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="Invalid size"):
            milestones.add_milestone_item(
                conn, milestone_id="release/1.1",
                item_kind="bug", item_ref="CB-1", size="medium",
            )

    def test_move_item(self, conn):
        _add_finding(conn, "CB-1")
        # CB-1 was auto-routed to stream/triage.
        moved = milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
            reason="ready for 1.1",
        )
        assert moved["milestone_id"] == "release/1.1"

    def test_move_to_nonexistent(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(KeyError, match="Destination milestone not found"):
            milestones.move_milestone_item(
                conn, item_ref="CB-1", to_milestone="release/9.9",
            )

    def test_move_collision(self, conn):
        _add_finding(conn, "CB-1")
        # CB-1 in stream/triage. Manually add another row in release/1.1 first.
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="bug", item_ref="CB-1",
        )
        # Now try moving the stream/triage one — release/1.1 already has it.
        # _get_item_by_ref returns DESC by id, so the most recent row (the
        # release/1.1 one we just inserted) is picked. The move is a no-op
        # because milestone_id already matches. Move from the triage row
        # is exercised by deleting the release row and re-adding — easier
        # to test by direct conn manipulation:
        # Actually just verify that explicit double-add raises (covered above).
        # Here we ensure a same-milestone move is a no-op.
        item = milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        assert item["milestone_id"] == "release/1.1"

    def test_set_status_done_records_commit(self, conn):
        _add_finding(conn, "CB-1")
        result = milestones.set_item_status(
            conn, item_ref="CB-1", status="done", commit="deadbeef",
        )
        assert result["status"] == "done"
        assert result["done_commit"] == "deadbeef"
        assert result["done_at"] is not None

    def test_set_status_invalid(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="Invalid status"):
            milestones.set_item_status(
                conn, item_ref="CB-1", status="exploded",
            )

    def test_set_status_no_commit_for_open(self, conn):
        _add_finding(conn, "CB-1")
        result = milestones.set_item_status(
            conn, item_ref="CB-1", status="in_progress",
        )
        assert result["status"] == "in_progress"
        assert result["done_at"] is None
        assert result["done_commit"] is None


# ---------------------------------------------------------------------------
# Auto-routing post-add hook
# ---------------------------------------------------------------------------

class TestAutoRouting:
    def test_default_routes_to_triage(self, conn):
        result = _add_finding(conn, "CB-1", description="some bug")
        # After add, CB-1 should be in stream/triage.
        item = milestones._get_item_by_ref(conn, "CB-1")
        assert item["milestone_id"] == "stream/triage"
        assert item["size"] == "triage"
        assert item["status"] == "open"
        assert result["id"] == "CB-1"  # finding insert succeeded

    def test_critical_security_routes_to_security(self, conn):
        _add_finding(
            conn, "CB-1", description="sqli",
            severity="critical", category="security:sqli",
        )
        item = milestones._get_item_by_ref(conn, "CB-1")
        assert item["milestone_id"] == "stream/security"

    def test_non_critical_security_still_triage(self, conn):
        _add_finding(
            conn, "CB-1", description="weak validation",
            severity="medium", category="security:weak",
        )
        item = milestones._get_item_by_ref(conn, "CB-1")
        assert item["milestone_id"] == "stream/triage"

    def test_critical_non_security_still_triage(self, conn):
        _add_finding(
            conn, "CB-1", description="data loss",
            severity="critical", category="bug",
        )
        item = milestones._get_item_by_ref(conn, "CB-1")
        assert item["milestone_id"] == "stream/triage"

    def test_batch_add_routes_each(self, conn):
        findings.batch_add_findings(conn, [
            {"severity": "high", "category": "bug", "file": "a.py",
             "description": "x"},
            {"severity": "critical", "category": "security:xss",
             "file": "b.py", "description": "y"},
        ])
        triage = conn.execute(
            "SELECT COUNT(*) c FROM milestone_items WHERE milestone_id='stream/triage'"
        ).fetchone()["c"]
        security = conn.execute(
            "SELECT COUNT(*) c FROM milestone_items WHERE milestone_id='stream/security'"
        ).fetchone()["c"]
        assert triage == 1
        assert security == 1

    def test_hook_atomic_with_finding(self, conn):
        # Finding row and milestone_items row should be visible together
        # (committed in same transaction).
        _add_finding(conn, "CB-1")
        finding = conn.execute("SELECT id FROM findings WHERE id='CB-1'").fetchone()
        item = conn.execute(
            "SELECT id FROM milestone_items WHERE item_ref='CB-1'"
        ).fetchone()
        assert finding is not None
        assert item is not None

    def test_hook_schema_probe_no_milestones_table(self, tmp_project):
        # Raw sqlite3 connect to a fresh file, only findings schema applied.
        # The hook should detect the missing milestone_items table and skip.
        import os
        path = os.path.join(tmp_project, "raw.db")
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        findings.ensure_schema(c)
        # Hook is already registered (module-level). It must not crash.
        result = findings.add_finding(
            c, severity="high", category="bug",
            file="x.py", description="d",
        )
        assert result["id"] == "CB-1"
        c.close()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAudit:
    def test_create_milestone_writes_audit(self, conn):
        milestones.create_milestone(
            conn, id="release/2.0", kind="release", description="future",
        )
        rows = milestones.query_audit(conn, milestone_id="release/2.0")
        assert len(rows) == 1
        assert rows[0]["action"] == "create"
        assert rows[0]["actor"] == "user"

    def test_add_item_writes_audit(self, conn):
        _add_finding(conn, "CB-1")  # auto-routed (one audit row already)
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="bug", item_ref="CB-1",
        )
        rows = milestones.query_audit(conn, item_ref="CB-1")
        # 1 from auto-router (stream/triage) + 1 from explicit add (release/1.1)
        assert len(rows) == 2
        assert {r["milestone_id"] for r in rows} == {"stream/triage", "release/1.1"}

    def test_set_status_writes_audit(self, conn):
        _add_finding(conn, "CB-1")
        milestones.set_item_status(
            conn, item_ref="CB-1", status="done", commit="abc",
        )
        rows = milestones.query_audit(conn, item_ref="CB-1", actor="user")
        assert any(r["action"] == "status" and r["to_state"] == "done" for r in rows)

    def test_filter_by_actor(self, conn):
        _add_finding(conn, "CB-1")
        rows = milestones.query_audit(conn, actor=milestones.AUTO_ROUTER_ACTOR)
        assert len(rows) >= 1
        assert all(r["actor"] == milestones.AUTO_ROUTER_ACTOR for r in rows)

    def test_filter_by_since(self, conn):
        from codebugs.types import utc_now
        marker = utc_now()
        _add_finding(conn, "CB-1")
        rows = milestones.query_audit(conn, since=marker)
        # The auto-route audit was written at or after marker.
        assert len(rows) >= 1

    def test_audit_move(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
            reason="moving",
        )
        rows = milestones.query_audit(conn, item_ref="CB-1")
        move_rows = [r for r in rows if r["action"] == "move"]
        assert len(move_rows) == 1
        assert move_rows[0]["from_state"] == "stream/triage"
        assert move_rows[0]["to_state"] == "release/1.1"
        assert move_rows[0]["reason"] == "moving"


# ---------------------------------------------------------------------------
# Status rollup includes blockers correctly
# ---------------------------------------------------------------------------

class TestStatusBlockers:
    def test_external_item_skips_blocker_check(self, conn):
        # External item refs would crash blockers._detect_entity_type.
        # The status rollup must skip them safely.
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="external", item_ref="external://x",
        )
        s = milestones.get_milestone_status(conn, id="release/1.1")
        assert s["blocked_items"] == []

    def test_blocked_bug_appears_in_rollup(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        # CB-2 blocks CB-1.
        blockers.add_blocker(
            conn, item_id="CB-1", reason="needs CB-2 first",
            blocked_by="CB-2",
        )
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        s = milestones.get_milestone_status(conn, id="release/1.1")
        assert "CB-1" in s["blocked_items"]


# ---------------------------------------------------------------------------
# Phase 2: Triage tools
# ---------------------------------------------------------------------------

class TestTriageInbox:
    def test_empty(self, conn):
        rows = milestones.triage_inbox(conn)
        assert rows == []

    def test_oldest_first(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        rows = milestones.triage_inbox(conn)
        assert [r["item_ref"] for r in rows] == ["CB-1", "CB-2", "CB-3"]

    def test_limit(self, conn):
        for i in range(5):
            _add_finding(conn, f"CB-{i + 1}")
        rows = milestones.triage_inbox(conn, limit=2)
        assert len(rows) == 2

    def test_excludes_promoted(self, conn):
        _add_finding(conn, "CB-1")
        milestones.triage_promote(
            conn, bug_id="CB-1", to_milestone="release/1.1",
        )
        rows = milestones.triage_inbox(conn)
        assert rows == []


class TestTriageDismiss:
    def test_dismiss_bug_propagates_not_a_bug(self, conn):
        _add_finding(conn, "CB-1")
        milestones.triage_dismiss(
            conn, bug_id="CB-1", reason="user error, not a real bug",
        )
        item = milestones._get_item_by_ref(conn, "CB-1")
        assert item["status"] == "dismissed"
        # Finding status updated.
        f = conn.execute("SELECT status FROM findings WHERE id='CB-1'").fetchone()
        assert f["status"] == "not_a_bug"

    def test_dismiss_requirement_propagates_obsolete(self, conn):
        _add_req(conn, "FR-001", description="some requirement")
        # Attach the requirement to stream/triage manually (auto-router is finding-only).
        milestones.add_milestone_item(
            conn, milestone_id="stream/triage",
            item_kind="requirement", item_ref="FR-001",
        )
        milestones.triage_dismiss(
            conn, bug_id="FR-001", reason="superseded by FR-002",
        )
        r = conn.execute("SELECT status FROM requirements WHERE id='FR-001'").fetchone()
        assert r["status"] == "obsolete"

    def test_dismiss_external_no_propagation(self, conn):
        milestones.add_milestone_item(
            conn, milestone_id="stream/triage",
            item_kind="external", item_ref="ext://x/1",
        )
        # Should not crash.
        item = milestones.triage_dismiss(
            conn, bug_id="ext://x/1", reason="duplicate",
        )
        assert item["status"] == "dismissed"

    def test_dismiss_empty_reason_rejected(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="reason is required"):
            milestones.triage_dismiss(conn, bug_id="CB-1", reason="   ")


class TestTriagePromote:
    def test_promote_to_release(self, conn):
        _add_finding(conn, "CB-1")
        item = milestones.triage_promote(
            conn, bug_id="CB-1", to_milestone="release/1.1",
            size="small",
        )
        assert item["milestone_id"] == "release/1.1"
        assert item["size"] == "small"

    def test_promote_large_needs_acceptance(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="acceptance is required"):
            milestones.triage_promote(
                conn, bug_id="CB-1", to_milestone="release/1.1",
                size="large",
            )

    def test_promote_with_linked_frs(self, conn):
        _add_finding(conn, "CB-1")
        _add_req(conn, "FR-001")
        item = milestones.triage_promote(
            conn, bug_id="CB-1", to_milestone="release/1.1",
            size="large", acceptance="ship it",
            linked_frs=["FR-001"],
        )
        assert item["meta"].get("linked_frs") == ["FR-001"]

    def test_promote_non_triage_rejected(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        with pytest.raises(ValueError, match="not in stream/triage"):
            milestones.triage_promote(
                conn, bug_id="CB-1", to_milestone="release/1.1",
            )


# ---------------------------------------------------------------------------
# Phase 2: pull_next + release_item + wip_status
# ---------------------------------------------------------------------------

class TestPullNext:
    def test_empty_returns_none(self, conn):
        result = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        assert result is None

    def test_pulls_security_before_release(self, conn):
        _add_finding(conn, "CB-1")  # → stream/triage
        _add_finding(
            conn, "CB-2", severity="critical", category="security:xss",
        )  # → stream/security
        # Release item too
        _add_finding(conn, "CB-3")
        milestones.move_milestone_item(
            conn, item_ref="CB-3", to_milestone="release/1.1",
        )

        result = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 5, "triage": 5},
        )
        assert result is not None
        assert result["item_ref"] == "CB-2"
        assert result["milestone_id"] == "stream/security"
        assert result["status"] == "in_progress"
        assert result["assigned_agent"] == "A"

    def test_pulls_release_before_triage(self, conn):
        _add_finding(conn, "CB-1")  # triage
        _add_finding(conn, "CB-2")
        milestones.move_milestone_item(
            conn, item_ref="CB-2", to_milestone="release/1.1",
        )
        result = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 5, "triage": 5},
        )
        assert result["item_ref"] == "CB-2"

    def test_release_sorted_by_target_date(self, conn):
        milestones.create_milestone(
            conn, id="release/1.2", kind="release",
            description="later", target_date="2027-01-01",
        )
        milestones.create_milestone(
            conn, id="release/1.5", kind="release",
            description="earlier", target_date="2026-06-01",
        )
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.2",
        )
        milestones.move_milestone_item(
            conn, item_ref="CB-2", to_milestone="release/1.5",
        )
        result = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 5, "triage": 5},
        )
        assert result["item_ref"] == "CB-2"  # 1.5 has earlier target_date

    def test_priority_within_milestone(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        milestones.move_milestone_item(
            conn, item_ref="CB-2", to_milestone="release/1.1",
        )
        # Set CB-2 to higher priority (lower number).
        conn.execute(
            "UPDATE milestone_items SET priority=10 WHERE item_ref='CB-2'"
        )
        conn.commit()
        result = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 5, "triage": 5},
        )
        assert result["item_ref"] == "CB-2"

    def test_capacity_full_skips(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        # Both in stream/triage with size=triage.
        # Capacity for triage = 1.
        first = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 1, "triage": 1},
        )
        assert first is not None
        # Next pull for the same agent with same capacity returns nothing
        # (slot full) — because triage_held=1, capacity=1.
        second = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 1, "triage": 1},
        )
        assert second is None

    def test_two_agents_get_different_items(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        a = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        b = milestones.pull_next(
            conn, agent_id="B", capacity={"large": 1, "small": 2, "triage": 5},
        )
        assert a is not None and b is not None
        assert a["item_ref"] != b["item_ref"]

    def test_large_bug_in_release_needs_linked_frs(self, conn):
        _add_finding(conn, "CB-1")
        # promote to release as large with acceptance but NO linked FRs.
        milestones.triage_promote(
            conn, bug_id="CB-1", to_milestone="release/1.1",
            size="large", acceptance="acceptance",
        )
        result = milestones.pull_next(
            conn, agent_id="A",
            capacity={"large": 1, "small": 2, "triage": 5},
        )
        assert result is None  # ineligible: missing linked_frs

    def test_large_bug_in_release_with_linked_fr_eligible(self, conn):
        _add_finding(conn, "CB-1")
        _add_req(conn, "FR-001")
        milestones.triage_promote(
            conn, bug_id="CB-1", to_milestone="release/1.1",
            size="large", acceptance="acceptance",
            linked_frs=["FR-001"],
        )
        result = milestones.pull_next(
            conn, agent_id="A",
            capacity={"large": 1, "small": 2, "triage": 5},
        )
        assert result is not None
        assert result["item_ref"] == "CB-1"

    def test_blocker_makes_ineligible(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-99")
        blockers.add_blocker(
            conn, item_id="CB-1", reason="needs CB-99",
            blocked_by="CB-99",
        )
        # Only CB-1 in triage (CB-99 also gets routed). Pull should skip CB-1.
        result = milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        assert result is not None
        assert result["item_ref"] == "CB-99"  # the unblocker is pulled instead


class TestReleaseItem:
    def test_release_done(self, conn):
        _add_finding(conn, "CB-1")
        milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        result = milestones.release_item(
            conn, item_ref="CB-1", status="done", commit="abc123",
        )
        assert result["status"] == "done"
        assert result["done_commit"] == "abc123"
        # capacity decremented.
        wip = milestones.get_wip_status(conn, agent_id="A")
        assert wip[0]["triage_held"] == 0

    def test_release_abandoned(self, conn):
        _add_finding(conn, "CB-1")
        milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        result = milestones.release_item(
            conn, item_ref="CB-1", status="abandoned",
        )
        assert result["status"] == "open"
        assert result["assigned_agent"] is None
        wip = milestones.get_wip_status(conn, agent_id="A")
        assert wip[0]["triage_held"] == 0

    def test_release_invalid_status(self, conn):
        _add_finding(conn, "CB-1")
        milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        with pytest.raises(ValueError, match="Invalid release status"):
            milestones.release_item(conn, item_ref="CB-1", status="exploded")


class TestWipStatus:
    def test_empty(self, conn):
        rows = milestones.get_wip_status(conn)
        assert rows == []

    def test_after_pull(self, conn):
        _add_finding(conn, "CB-1")
        milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        rows = milestones.get_wip_status(conn)
        assert len(rows) == 1
        assert rows[0]["agent_id"] == "A"
        assert rows[0]["triage_held"] == 1

    def test_filter_by_agent(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        milestones.pull_next(
            conn, agent_id="A", capacity={"large": 1, "small": 2, "triage": 5},
        )
        milestones.pull_next(
            conn, agent_id="B", capacity={"large": 1, "small": 2, "triage": 5},
        )
        a_rows = milestones.get_wip_status(conn, agent_id="A")
        assert len(a_rows) == 1 and a_rows[0]["agent_id"] == "A"


class TestCapacityHeldColumn:
    """CB-22 sibling: `<size>_held` is interpolated into SQL on the strength of a
    CHECK constraint enforced two layers away, in another table.

    Before the guard the SAME bad input failed two different ways depending on
    caller state — and one of them was silent."""

    def test_unknown_size_is_refused_when_no_capacity_row_exists(self, conn):
        """The silent branch. It used to take the dict path, write a row of all
        zeros, and return success having lost the increment entirely."""
        from codebugs.milestones.capacity import _upsert_capacity_increment

        with pytest.raises(ValueError, match="Invalid size"):
            _upsert_capacity_increment(conn, "agent-1", "bogus")
        assert conn.execute("SELECT COUNT(*) c FROM agent_capacity").fetchone()["c"] == 0

    def test_unknown_size_is_refused_when_a_capacity_row_exists(self, conn):
        """The loud branch: it raised OperationalError, not the contract's ValueError."""
        from codebugs.milestones.capacity import _upsert_capacity_increment

        _upsert_capacity_increment(conn, "agent-1", "large")
        with pytest.raises(ValueError, match="Invalid size"):
            _upsert_capacity_increment(conn, "agent-1", "bogus")
        row = conn.execute(
            "SELECT * FROM agent_capacity WHERE agent_id = 'agent-1'"
        ).fetchone()
        assert row["large_held"] == 1

    def test_unknown_size_is_refused_on_decrement(self, conn):
        from codebugs.milestones.capacity import _decrement_capacity

        with pytest.raises(ValueError, match="Invalid size"):
            _decrement_capacity(conn, "agent-1", "bogus")

    def test_every_declared_size_still_increments(self, conn):
        """The vacuous-pass direction: the guard must not refuse legitimate sizes."""
        from codebugs.milestones._schema import ITEM_SIZES
        from codebugs.milestones.capacity import _upsert_capacity_increment

        for size in ITEM_SIZES:
            _upsert_capacity_increment(conn, "agent-1", size)
        row = conn.execute(
            "SELECT * FROM agent_capacity WHERE agent_id = 'agent-1'"
        ).fetchone()
        assert all(row[f"{s}_held"] == 1 for s in ITEM_SIZES)


# ---------------------------------------------------------------------------
# Phase 2: concurrent pull_next (BEGIN IMMEDIATE atomicity)
# ---------------------------------------------------------------------------

class TestPullNextConcurrent:
    def test_two_threads_two_connections_no_double_claim(self, tmp_project):
        """Two threads, two connections, race to pull. Each item must be
        claimed by exactly one thread."""
        import threading

        # Seed 4 findings → 4 triage items.
        seed_conn = db.connect(tmp_project)
        try:
            for i in range(4):
                _add_finding(seed_conn, f"CB-{i + 1}")
        finally:
            seed_conn.close()

        results: list[dict | None] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker(agent_id: str) -> None:
            c = db.connect(tmp_project)
            try:
                barrier.wait()
                # Each agent does 2 pulls back-to-back.
                for _ in range(2):
                    r = milestones.pull_next(
                        c, agent_id=agent_id,
                        capacity={"large": 1, "small": 2, "triage": 5},
                    )
                    with results_lock:
                        results.append(r)
            finally:
                c.close()

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        claimed = [r for r in results if r is not None]
        refs = [r["item_ref"] for r in claimed]
        # Every claim must be unique — no item double-claimed.
        assert len(refs) == len(set(refs))
        # All 4 items got claimed (since capacity is generous and 2x2 pulls).
        assert set(refs) == {"CB-1", "CB-2", "CB-3", "CB-4"}


class PausingConnection(sqlite3.Connection):
    """Fires a one-shot hook right after ``_get_item_by_ref``'s SELECT.

    The findings and requirements suites carry twins of this; each keys on its
    own entity's read, and the project deliberately has no ``conftest.py``.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_select = None

    def execute(self, sql, *args, **kwargs):
        cur = super().execute(sql, *args, **kwargs)
        if self.after_select and sql.lstrip().startswith("SELECT * FROM milestone_items"):
            hook, self.after_select = self.after_select, None
            hook()
        return cur


class TestConcurrentItemMetaWritesDoNotLoseEachOther:
    """CB-24 siblings: ``meta_json`` is merged in Python by two milestone writers.

    ``mark_branch_only`` merges ``meta["branch"]`` and ``triage_promote`` merges
    ``meta["linked_frs"]``, each over the row it read a statement earlier. Unless
    the read and the write are one transaction, an agent branching an item while
    another promotes it loses one of the two keys, and both calls report success.

    This is not hypothetical plumbing: ``mark_branch_only`` is called by
    autosorter's ``worktree-setup.sh`` at the moment a branch is created, which is
    exactly when another agent is likely to be triaging the same queue.
    """

    def _open(self, tmp_project):
        path = os.path.join(tmp_project, ".codebugs", "findings.db")
        c = sqlite3.connect(path, factory=PausingConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def test_an_ambient_transaction_is_not_committed_by_mark_branch_only(self, conn):
        """The milestones twin of the findings/reqs ambient guard.

        Both wrapped functions here carry the same "do not restore
        ``conn.commit()``" instruction in their docstrings, and prose is not a
        gate. Without this test the claim holds on two of the four CB-24 sites and
        is merely asserted on the other two — the CB-17 asymmetry again, invisible
        from inside any one file. Dormant today (both are only reached from
        fresh-connection entry points) which is exactly when it is cheap to pin.
        """
        _add_finding(conn, "CB-1")
        before = conn.execute(
            "SELECT priority FROM milestone_items WHERE item_ref = ?", ("CB-1",)
        ).fetchone()["priority"]

        with pytest.raises(RuntimeError, match="caller aborts"):
            with db.txn(conn) as opened:
                assert opened, "the caller owns this transaction"
                conn.execute(
                    "UPDATE milestone_items SET priority = ? WHERE item_ref = ?", (7, "CB-1")
                )
                milestones.mark_branch_only(conn, item_ref="CB-1", branch_name="fix/cb-1")
                raise RuntimeError("caller aborts after the nested call")

        row = conn.execute(
            "SELECT priority, branch_only, meta_json FROM milestone_items WHERE item_ref = ?",
            ("CB-1",),
        ).fetchone()
        assert row["priority"] == before, "the caller's own write must have rolled back"
        assert not row["branch_only"], "the nested call must roll back with its caller"
        assert "branch" not in json.loads(row["meta_json"]), row["meta_json"]

    def test_an_ambient_transaction_is_not_committed_by_triage_dismiss(self, conn):
        """CB-36 batch 4: `triage_dismiss` now owns no commit of its own.

        It writes three things — the item row, its audit row, and the propagated
        finding status — so all three are asserted. A partial rollback would pass a
        test that checked only the item.

        The sibling test below covers the inverse direction (a failure *inside* the
        unit rolls the whole unit back). This one covers the caller's frame: under an
        ambient transaction `db.txn` yields False and the caller keeps ownership, so
        the caller's abort must discard all three writes.
        """
        _add_finding(conn, "CB-1")

        with pytest.raises(RuntimeError, match="caller aborts"):
            with db.txn(conn) as opened:
                assert opened, "the caller owns this transaction"
                milestones.triage_dismiss(conn, bug_id="CB-1", reason="duplicate")
                raise RuntimeError("caller aborts after the nested call")

        item = conn.execute(
            "SELECT status FROM milestone_items WHERE item_ref = 'CB-1'"
        ).fetchone()
        assert item["status"] != "dismissed", "the dismissal must roll back"
        audits = conn.execute(
            "SELECT COUNT(*) AS c FROM milestone_audit "
            "WHERE item_ref = 'CB-1' AND action = 'dismiss'"
        ).fetchone()["c"]
        assert audits == 0, "the audit row must roll back with the dismissal"
        status = conn.execute(
            "SELECT status FROM findings WHERE id = 'CB-1'"
        ).fetchone()["status"]
        assert status != "not_a_bug", "the propagated entity write must roll back too"

    def test_triage_dismiss_is_atomic_when_the_propagated_write_fails(self, conn):
        """The atomicity this change claims for the compound caller, through it.

        ``triage_dismiss`` writes ``milestone_items``, then its audit row, then
        propagates to the finding. Before CB-24 the nested ``update_finding``
        committed all three at its own ``conn.commit()``, so a failure raised after
        that point left the dismissal and audit row committed while the caller's
        own commit never ran. Now the nested call commits nothing and the unit
        stands or falls together.

        The failure is induced the only way the code allows: stored meta that is
        not valid JSON, which ``row_to_dict`` refuses while building the return
        value — after the write, which is the whole point.
        """
        _add_finding(conn, "CB-1")
        conn.execute("UPDATE findings SET meta = ? WHERE id = ?", ("{not json", "CB-1"))
        conn.commit()

        with pytest.raises(json.JSONDecodeError):
            milestones.triage_dismiss(conn, bug_id="CB-1", reason="duplicate")
        conn.rollback()  # the aborted unit is still open on this connection

        item = conn.execute(
            "SELECT status FROM milestone_items WHERE item_ref = ?", ("CB-1",)
        ).fetchone()
        assert item["status"] != "dismissed", "the dismissal must not survive on its own"
        audits = conn.execute(
            "SELECT COUNT(*) AS c FROM milestone_audit WHERE item_ref = ? AND action = 'dismiss'",
            ("CB-1",),
        ).fetchone()["c"]
        assert audits == 0, "the audit row must roll back with the dismissal it records"

    def test_branching_an_item_does_not_erase_a_concurrent_promotion(self, tmp_project):
        seed = db.connect(tmp_project)
        try:
            _add_finding(seed, "CB-1")  # auto-routes into stream/triage
            _add_req(seed, "FR-1")
        finally:
            seed.close()

        a = self._open(tmp_project)
        a_read, b_started, b_read = (threading.Event() for _ in range(3))

        def competing_promoter():
            a_read.wait(timeout=10)
            b = self._open(tmp_project)
            b.after_select = b_read.set
            b_started.set()
            try:
                milestones.triage_promote(
                    b, bug_id="CB-1", to_milestone="release/1.1",
                    size="small", linked_frs=["FR-1"],
                )
            finally:
                b.close()

        a.after_select = lambda: (
            a_read.set(),
            b_started.wait(timeout=10),
            b_read.wait(timeout=1.0),
        )

        t = threading.Thread(target=competing_promoter)
        t.start()
        try:
            milestones.mark_branch_only(a, item_ref="CB-1", branch_name="fix/cb-1")
        finally:
            t.join(timeout=30)
            a.close()
        assert not t.is_alive()

        # Read the stored column directly: no public reader returns a single item's
        # meta, and what this test is about is what actually landed in the row.
        check = db.connect(tmp_project)
        try:
            stored = check.execute(
                "SELECT meta_json FROM milestone_items WHERE item_ref = ?", ("CB-1",)
            ).fetchone()["meta_json"]
        finally:
            check.close()
        meta = json.loads(stored)
        assert meta.get("branch") == "fix/cb-1", meta
        assert meta.get("linked_frs") == ["FR-1"], meta


# ---------------------------------------------------------------------------
# Phase 3: branch tracking
# ---------------------------------------------------------------------------

class TestBranchTracking:
    def test_mark_branch_only(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        item = milestones.mark_branch_only(
            conn, item_ref="CB-1", branch_name="feat/fix-CB-1",
        )
        assert item["branch_only"] is True
        assert item["meta"]["branch"] == "feat/fix-CB-1"

    def test_mark_integrated(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        milestones.mark_branch_only(
            conn, item_ref="CB-1", branch_name="feat/fix-CB-1",
        )
        item = milestones.mark_integrated(
            conn, item_ref="CB-1", commit="cafebabe",
        )
        assert item["branch_only"] is False
        assert item["status"] == "done"
        assert item["done_commit"] == "cafebabe"

    def test_mark_integrated_requires_commit(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="commit is required"):
            milestones.mark_integrated(conn, item_ref="CB-1", commit="  ")


# ---------------------------------------------------------------------------
# Phase 3: close gate
# ---------------------------------------------------------------------------

class TestCloseGate:
    def test_an_ambient_transaction_is_not_committed_by_milestone_close(self, conn):
        """CB-36 batch 5: the cross-table gate now owns no commit of its own.

        `milestone_close` is the only site in this card whose guard and write live in
        DIFFERENT tables — it reads `milestone_items` to build its refusal list and
        writes `milestones`. Both are now in one transaction, so a concurrent item
        writer is serialized either wholly before the check or wholly after the close,
        and the gate cannot be stepped over.

        Both writes are asserted — the milestone state AND the audit row. Checking only
        the state would let a partial rollback pass.

        No race test, for the reason recorded on the sibling sites: driving the
        interleave leaves the same final state a legal ordering produces (A checks, B
        adds an item, A closes → shipped with an open item; and "A closes, then B adds"
        → the same). "No oracle found", not "no oracle exists" — a reviewer found one
        for `add_claim` after I made the stronger claim.
        """
        milestones.create_milestone(
            conn, id="release/8.8", kind="release", description="gate fixture"
        )

        with pytest.raises(RuntimeError, match="caller aborts"):
            with db.txn(conn) as opened:
                assert opened, "the caller owns this transaction"
                milestones.milestone_close(conn, id="release/8.8")
                raise RuntimeError("caller aborts after the nested call")

        state = conn.execute(
            "SELECT state FROM milestones WHERE id = 'release/8.8'"
        ).fetchone()["state"]
        assert state != "shipped", "the close must roll back with its caller"
        audits = conn.execute(
            "SELECT COUNT(*) AS c FROM milestone_audit "
            "WHERE milestone_id = 'release/8.8' AND action = 'close'"
        ).fetchone()["c"]
        assert audits == 0, "the audit row must roll back with the close it records"

    def test_close_stream_always_refused(self, conn):
        with pytest.raises(ValueError, match="streams cannot be closed"):
            milestones.milestone_close(conn, id="stream/triage")

    def test_close_stream_refused_even_with_force(self, conn):
        with pytest.raises(ValueError, match="streams cannot be closed"):
            milestones.milestone_close(
                conn, id="stream/triage", force=True, reason="ignored",
            )

    def test_close_empty_release_succeeds(self, conn):
        result = milestones.milestone_close(conn, id="release/1.1")
        assert result["state"] == "shipped"
        assert result["closed_at"] is not None

    def test_close_refuses_unfinished(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        with pytest.raises(ValueError, match="unfinished items"):
            milestones.milestone_close(conn, id="release/1.1")

    def test_close_refuses_branch_only(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        milestones.set_item_status(
            conn, item_ref="CB-1", status="done", commit="x",
        )
        milestones.mark_branch_only(
            conn, item_ref="CB-1", branch_name="feat/CB-1",
        )
        with pytest.raises(ValueError, match="branch-only items"):
            milestones.milestone_close(conn, id="release/1.1")

    def test_close_refuses_blocked(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-99")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        # CB-1 done but with an unresolved blocker.
        milestones.set_item_status(
            conn, item_ref="CB-1", status="done", commit="x",
        )
        blockers.add_blocker(
            conn, item_id="CB-1", reason="needs CB-99",
            blocked_by="CB-99",
        )
        with pytest.raises(ValueError, match="active blockers"):
            milestones.milestone_close(conn, id="release/1.1")

    def test_close_force_overrides(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        result = milestones.milestone_close(
            conn, id="release/1.1", force=True,
            reason="emergency cut for compliance",
        )
        assert result["state"] == "shipped"
        audit = milestones.query_audit(conn, milestone_id="release/1.1")
        close_audits = [r for r in audit if r["action"] == "close"]
        assert len(close_audits) == 1
        assert "force" in close_audits[0]["reason"]

    def test_close_external_item_no_crash(self, conn):
        # External items have free-form ids that would crash blockers'
        # _detect_entity_type — close-gate must skip them safely.
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1",
            item_kind="external", item_ref="external://x/1",
        )
        milestones.set_item_status(
            conn, item_ref="external://x/1", status="done",
        )
        result = milestones.milestone_close(conn, id="release/1.1")
        assert result["state"] == "shipped"

    def test_close_error_message_names_items(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        milestones.move_milestone_item(
            conn, item_ref="CB-2", to_milestone="release/1.1",
        )
        milestones.set_item_status(
            conn, item_ref="CB-2", status="done", commit="x",
        )
        milestones.mark_branch_only(
            conn, item_ref="CB-2", branch_name="feat/CB-2",
        )
        with pytest.raises(ValueError) as exc:
            milestones.milestone_close(conn, id="release/1.1")
        msg = str(exc.value)
        assert "CB-1" in msg
        assert "CB-2" in msg
        assert "feat/CB-2" in msg


# ---------------------------------------------------------------------------
# Phase 3: defer
# ---------------------------------------------------------------------------

class TestDefer:
    def test_defer_to_maintenance(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        result = milestones.milestone_defer(
            conn, item_ref="CB-1", reason="not for 1.1",
        )
        assert result["milestone_id"] == "stream/maintenance"
        assert result["status"] == "deferred"

    def test_defer_writes_audit(self, conn):
        _add_finding(conn, "CB-1")
        milestones.move_milestone_item(
            conn, item_ref="CB-1", to_milestone="release/1.1",
        )
        milestones.milestone_defer(
            conn, item_ref="CB-1", reason="not now",
        )
        audit = milestones.query_audit(conn, item_ref="CB-1")
        assert any(r["action"] == "defer" for r in audit)


# ---------------------------------------------------------------------------
# Spec-level acceptance smoke test
# ---------------------------------------------------------------------------

class TestSpecAcceptance:
    def test_full_acceptance_workflow(self, conn):
        # §13 acceptance — end-to-end smoke.
        # (1) milestone_status returns a snapshot for the seeded release.
        s = milestones.get_milestone_status(conn, id="release/1.1")
        assert s["milestone"]["id"] == "release/1.1"

        # (2) add a finding → lands in stream/triage automatically.
        _add_finding(conn, "CB-1", severity="high")
        triage = milestones.triage_inbox(conn)
        assert "CB-1" in [r["item_ref"] for r in triage]

        # (3) triage_dismiss completes (no follow-up prompts) — propagates.
        _add_finding(conn, "CB-2", severity="low")
        milestones.triage_dismiss(conn, bug_id="CB-2", reason="test")
        item = milestones._get_item_by_ref(conn, "CB-2")
        assert item["status"] == "dismissed"

        # (4) Two agents → non-overlapping work.
        _add_finding(conn, "CB-3")
        a = milestones.pull_next(
            conn, agent_id="A",
            capacity={"large": 1, "small": 2, "triage": 5},
        )
        b = milestones.pull_next(
            conn, agent_id="B",
            capacity={"large": 1, "small": 2, "triage": 5},
        )
        assert a["item_ref"] != b["item_ref"]

        # (5) milestone_close with branch-only refuses, names item + branch.
        _add_finding(conn, "CB-4")
        milestones.move_milestone_item(
            conn, item_ref="CB-4", to_milestone="release/1.1",
        )
        milestones.set_item_status(
            conn, item_ref="CB-4", status="done", commit="x",
        )
        milestones.mark_branch_only(
            conn, item_ref="CB-4", branch_name="feat/x",
        )
        with pytest.raises(ValueError, match="CB-4.*feat/x"):
            milestones.milestone_close(conn, id="release/1.1")

        # (6) audit shows every transition.
        audit = milestones.query_audit(conn, milestone_id="release/1.1")
        actions = {r["action"] for r in audit}
        # release/1.1 is seeded silently (no 'create' audit), but moves and
        # branch operations write audit rows.
        assert {"move", "branch"} <= actions

        # (7) phantom-ID rejected.
        with pytest.raises(ValueError, match="Unknown bug"):
            milestones.add_milestone_item(
                conn, milestone_id="release/1.1",
                item_kind="bug", item_ref="CB-99999",
            )


# ---------------------------------------------------------------------------
# Eligibility seam — rules tested in isolation via injected accessors.
# No findings / reqs / blockers schema required (the two cross-domain reads
# are injected), so the full matrix — including the has-active-blocker case
# the real fail-soft swallow would otherwise mask — is reachable directly.
# ---------------------------------------------------------------------------


class TestEligibilitySeam:
    REL = {"id": "release/1.1", "kind": "release"}
    STREAM = {"id": "stream/triage", "kind": "stream"}

    @staticmethod
    def _no_block(_ref):
        return False

    @staticmethod
    def _has_block(_ref):
        return True

    @staticmethod
    def _fr_ok(_fr):
        return True

    @staticmethod
    def _fr_missing(_fr):
        return False

    def _item(self, **kw):
        base = dict(
            status="open",
            item_kind="bug",
            item_ref="CB-1",
            size="small",
            acceptance="",
            meta={},
        )
        base.update(kw)
        return base

    def _check(
        self,
        item,
        milestone=None,
        capacity=None,
        held=None,
        has_active_blocker=None,
        requirement_exists=None,
    ):
        # conn is never touched when both accessors are injected.
        return milestones._eligibility_failure(
            sqlite3.connect(":memory:"),
            item,
            milestone or self.STREAM,
            capacity or {"small": 1, "large": 1, "triage": 1},
            held or {},
            has_active_blocker=has_active_blocker or self._no_block,
            requirement_exists=requirement_exists or self._fr_ok,
        )

    def test_open_small_is_eligible(self):
        assert self._check(self._item()) is None

    def test_non_open_rejected(self):
        assert "not open" in self._check(self._item(status="in_progress"))

    def test_active_blocker_rejected(self):
        # Reachable with NO blockers schema — the point of the seam.
        assert self._check(self._item(), has_active_blocker=self._has_block) == "has active blocker"

    def test_external_ignores_blocker(self):
        item = self._item(item_kind="external")
        assert self._check(item, has_active_blocker=self._has_block) is None

    def test_large_requires_acceptance(self):
        item = self._item(size="large", acceptance="")
        assert "requires acceptance" in self._check(item, capacity={"large": 1})

    def test_large_in_stream_needs_no_linked_frs(self):
        item = self._item(size="large", acceptance="ok")
        assert self._check(item, milestone=self.STREAM, capacity={"large": 1}) is None

    def test_large_bug_in_release_needs_linked_frs(self):
        item = self._item(size="large", acceptance="ok", meta={})
        assert "needs linked_frs" in self._check(item, milestone=self.REL, capacity={"large": 1})

    def test_large_bug_in_release_with_resolvable_frs_ok(self):
        item = self._item(size="large", acceptance="ok", meta={"linked_frs": ["FR-1"]})
        assert (
            self._check(
                item,
                milestone=self.REL,
                capacity={"large": 1},
                requirement_exists=self._fr_ok,
            )
            is None
        )

    def test_large_bug_in_release_with_unresolved_fr_rejected(self):
        item = self._item(size="large", acceptance="ok", meta={"linked_frs": ["FR-9"]})
        msg = self._check(
            item,
            milestone=self.REL,
            capacity={"large": 1},
            requirement_exists=self._fr_missing,
        )
        assert "FR-9 not in requirements" in msg

    def test_capacity_full_rejected(self):
        msg = self._check(self._item(), capacity={"small": 0}, held={"small": 0})
        assert "capacity for small full" in msg

    def test_defaults_bind_to_real_reads(self, conn):
        # No injected accessors: defaults bind to the real conn-backed reads.
        # conn has full schema; FR-404 absent -> linked_frs check fires.
        item = self._item(size="large", acceptance="ok", meta={"linked_frs": ["FR-404"]})
        msg = milestones._eligibility_failure(conn, item, self.REL, {"large": 1}, {})
        assert "FR-404 not in requirements" in msg


class TestListMilestonesValidatesItsVocabularies:
    """CB-25 sibling sweep. `list_milestones` guarded `kind`/`state` with plain
    truthiness and validated neither, while `create_milestone` (:42) and
    `set_milestone_state` (:79) have always validated the same two vocabularies.
    So `kind=0` returned every milestone and `kind="stremm"` returned none — both
    halves of CLAUDE.md's rule that a vocabulary must resolve on BOTH sides."""

    def _two(self, conn):
        milestones.create_milestone(conn, id="release/9.9", kind="release", description="r")
        milestones.create_milestone(conn, id="stream/x", kind="stream", description="s")

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_kind_raises_instead_of_returning_everything(self, conn, falsey):
        self._two(conn)
        with pytest.raises(ValueError, match="Invalid kind"):
            milestones.list_milestones(conn, kind=falsey)

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_state_raises_instead_of_returning_everything(self, conn, falsey):
        self._two(conn)
        with pytest.raises(ValueError, match="Invalid state"):
            milestones.list_milestones(conn, state=falsey)

    def test_unknown_values_raise_instead_of_returning_nothing(self, conn):
        self._two(conn)
        with pytest.raises(ValueError, match="Invalid kind"):
            milestones.list_milestones(conn, kind="stremm")
        with pytest.raises(ValueError, match="Invalid state"):
            milestones.list_milestones(conn, state="opne")

    @pytest.mark.parametrize("empty", [None, ""])
    def test_none_and_empty_string_still_mean_no_filter(self, conn, empty):
        self._two(conn)
        assert len(milestones.list_milestones(conn, kind=empty)) >= 2
        assert len(milestones.list_milestones(conn, state=empty)) >= 2

    def test_valid_values_still_filter(self, conn):
        self._two(conn)
        kinds = {m["kind"] for m in milestones.list_milestones(conn, kind="release")}
        assert kinds == {"release"}


class TestUnhonourableCommitIsRefused:
    """CB-28, the refuse half: two paths accepted a `commit` they could never store
    and returned success. Unlike the deferred query sites there is nothing to forward
    to — an abandoned item has no `done_commit` column to fill, and the no-op status
    path performs no write at all."""

    def _pulled_item(self, conn):
        f = _add_finding(conn, fid="CB-70", description="work")
        milestones.create_milestone(conn, id="release/7.7", kind="release", description="r")
        milestones.move_milestone_item(conn, item_ref=f["id"], to_milestone="release/7.7")
        milestones.pull_next(conn, agent_id="ag1", capacity={"small": 1})
        return f

    def test_abandoned_with_commit_raises(self, conn):
        f = self._pulled_item(conn)
        with pytest.raises(ValueError, match="commit cannot be recorded"):
            milestones.release_item(conn, item_ref=f["id"], status="abandoned", commit="deadbeef")

    def test_abandoned_without_commit_still_works(self, conn):
        f = self._pulled_item(conn)
        milestones.release_item(conn, item_ref=f["id"], status="abandoned")
        row = conn.execute(
            "SELECT status FROM milestone_items WHERE item_ref = ?", (f["id"],)
        ).fetchone()
        assert row["status"] == "open"

    def test_done_with_commit_still_records_it(self, conn):
        f = self._pulled_item(conn)
        milestones.release_item(conn, item_ref=f["id"], status="done", commit="cafe1234")
        row = conn.execute(
            "SELECT status, done_commit FROM milestone_items WHERE item_ref = ?", (f["id"],)
        ).fetchone()
        assert row["status"] == "done" and row["done_commit"] == "cafe1234"

    def test_setting_the_status_it_already_has_with_a_commit_raises(self, conn):
        f = self._pulled_item(conn)
        milestones.set_item_status(conn, item_ref=f["id"], status="done", commit="aaa111")
        with pytest.raises(ValueError, match="already 'done'; commit not recorded"):
            milestones.set_item_status(conn, item_ref=f["id"], status="done", commit="bbb222")

    def test_repeating_a_status_without_a_commit_is_still_a_quiet_no_op(self, conn):
        f = self._pulled_item(conn)
        milestones.set_item_status(conn, item_ref=f["id"], status="done", commit="aaa111")
        again = milestones.set_item_status(conn, item_ref=f["id"], status="done")
        assert again["status"] == "done" and again["done_commit"] == "aaa111"


class TestPullNextTransactionBoundary:
    """CB-40 + CB-39 — `pull_next` no longer runs a raw `BEGIN IMMEDIATE`."""

    def _open_item(self, conn, ref="CB-1"):
        _add_finding(conn, ref)
        conn.execute(
            "UPDATE milestone_items SET size='small' WHERE item_ref = ?", (ref,)
        )
        conn.commit()

    def test_pull_next_refuses_an_ambient_transaction(self, conn):
        """CB-40. The old save/restore opened with `conn.isolation_level = None`, and
        assigning `isolation_level` COMMITS any open transaction — so this silently
        committed the caller's unrelated work. Now it refuses outright, because under
        an ambient transaction it would report a claim no other connection can see.

        A raise, not an `assert`: `assert` is stripped under `-O`.
        """
        self._open_item(conn)
        with db.txn(conn) as opened:
            assert opened
            with pytest.raises(RuntimeError, match="no open transaction"):
                milestones.pull_next(conn, agent_id="agent-A", capacity={"small": 1})

    def test_pull_next_returns_the_row_it_claimed(self, conn):
        """CB-39. It used to return `_get_item_by_ref(...)` AFTER the commit, which
        resolves `ORDER BY id DESC LIMIT 1` and could hand back a newer attachment
        inserted in that window — an item this call never claimed. The row now comes
        from the claim UPDATE's `RETURNING`.

        **This PASSES against the old code, and the docstring above would be a lie
        without saying so.** With a single attachment the post-commit re-resolve finds
        the same row, so nothing here discriminates. It is a structural regression pin:
        it fixes the returned row's identity as "the row carrying this agent's claim",
        which the `RETURNING` implementation guarantees by construction and the
        re-resolve only happened to satisfy.

        The discriminating version needs a SECOND attachment inserted between the
        commit and the re-read, via the `CommitPausingConnection` seam below — the
        same recipe `TestReleaseItemAtomicity` uses for CB-30. Not built here because
        `pull_next` reaches its commit through `db.txn`'s `COMMIT` while the old code
        used `conn.commit()`, so the seam needs both hooks and a candidate item that
        survives `_candidates`' eligibility filter. Recorded on CB-39 rather than
        approximated.
        """
        self._open_item(conn)
        got = milestones.pull_next(conn, agent_id="agent-A", capacity={"small": 1})

        assert got is not None
        assert got["assigned_agent"] == "agent-A"
        assert got["status"] == "in_progress"
        stored_id = conn.execute(
            "SELECT id FROM milestone_items WHERE item_ref='CB-1' AND assigned_agent='agent-A'"
        ).fetchone()["id"]
        assert got["id"] == stored_id, "the returned row must be the claimed row"

    def test_no_candidate_returns_none_and_writes_nothing(self, conn):
        """The refusal path writes nothing, so it just returns and commits an empty
        transaction — no rollback-and-return machinery needed. Pinned because that
        fact is the reason the `TxnAbort` sentinel was rejected.
        """
        _add_finding(conn, "CB-1")
        conn.execute("UPDATE milestone_items SET status='done' WHERE item_ref='CB-1'")
        conn.commit()

        assert milestones.pull_next(conn, agent_id="agent-A", capacity={"small": 1}) is None
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM agent_capacity WHERE agent_id='agent-A'"
        ).fetchone()["c"] == 0, "a refusal must not have created a capacity row"


class CommitPausingConnection(sqlite3.Connection):
    """One-shot hook fired the instant the write transaction closes.

    Distinct from ``PausingConnection`` above, which fires after a SELECT. **Two
    seams, deliberately**, because the code under test changes which one it uses:
    unfixed ``release_item`` closes with ``conn.commit()`` and the fixed version with
    ``db.txn``'s ``conn.execute("COMMIT")``. Keying on only one gives a vacuous pass
    on the other, which is the whole failure mode this test exists to catch.

    The hook runs AFTER the underlying commit in both cases — firing before it lands
    would leave the write lock held, so a second connection writing inside the hook
    would block until ``busy_timeout`` expired.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_commit = None

    def _fire(self):
        if self.after_commit:
            hook, self.after_commit = self.after_commit, None
            hook()

    def commit(self):
        super().commit()
        self._fire()

    def execute(self, sql, *args, **kwargs):
        cur = super().execute(sql, *args, **kwargs)
        if sql.lstrip().upper().startswith("COMMIT"):
            self._fire()
        return cur


class TestReleaseItemAtomicity:
    """CB-30 fault (1): ``release_item`` must not decrement from a pre-lock read.

    It reads ``assigned_agent`` off the row and only later uses it to decrement a
    counter. Without the write lock taken before that read, CB-26's reconciliation
    hook can close the item and decrement in between, and this call then decrements a
    second time — the agent's *other* item stays assigned while capacity reports zero,
    so the agent is handed more work than its declared capacity.

    Two preconditions make the race reachable, and without either the test is vacuous
    on both sides. The reconciliation hook is **stream-scoped**
    (``reconcile._STREAM_ONLY``), so a release-milestone item never fires it; and
    ``_auto_route_finding`` hardcodes ``size='triage'``, so an auto-routed finding
    moves ``triage_held``, never ``small_held``. Both are set explicitly below.
    """

    def _open(self, tmp_project, factory=PausingConnection):
        path = os.path.join(tmp_project, ".codebugs", "findings.db")
        c = sqlite3.connect(path, factory=factory)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def _seed(self, tmp_project, held=2):
        """One agent holding two same-size items in a STREAM milestone."""
        seed = db.connect(tmp_project)
        try:
            _add_finding(seed, "CB-1")  # auto-routes into stream/triage
            _add_finding(seed, "CB-2")
            seed.execute(
                "UPDATE milestone_items SET size='small', status='in_progress', "
                "assigned_agent='agent-A' WHERE item_ref IN ('CB-1', 'CB-2')"
            )
            seed.execute(
                "INSERT INTO agent_capacity (agent_id, small_held) VALUES ('agent-A', ?)",
                (held,),
            )
            seed.commit()
        finally:
            seed.close()

    @staticmethod
    def _small_held(tmp_project):
        c = db.connect(tmp_project)
        try:
            return c.execute(
                "SELECT small_held FROM agent_capacity WHERE agent_id='agent-A'"
            ).fetchone()["small_held"]
        finally:
            c.close()

    def test_a_concurrent_reconciliation_does_not_cause_a_double_decrement(self, tmp_project):
        """The CB-30 race, driven through the bounded three-event interleave.

        Pre-fix: B's hook decrements 2->1 while A holds a stale ``assigned_agent``,
        then A decrements 1->0 — while CB-2 is still assigned. Post-fix: A holds the
        write lock from before its own read, so B blocks at ``BEGIN IMMEDIATE``, A's
        bounded wait expires, A commits (2->1), and B's hook then finds CB-1's item
        already ``done`` — ``_live_rows`` filters ``status != target`` — so it is a
        no-op. Final count 1, which is the truth: one of two items was released.
        """
        self._seed(tmp_project)

        a = self._open(tmp_project)
        a_read, b_started, b_read = (threading.Event() for _ in range(3))

        def competing_reconciler():
            a_read.wait(timeout=10)
            b = self._open(tmp_project)
            b.after_select = b_read.set
            b_started.set()
            try:
                # Resolving the finding fires reconcile._reconcile_on_terminal.
                findings.update_finding(b, "CB-1", status="fixed")
            except Exception:  # noqa: BLE001 — contention is the fixed path's business
                b_read.set()
            finally:
                b.close()

        a.after_select = lambda: (
            a_read.set(),
            b_started.wait(timeout=10),
            b_read.wait(timeout=1.0),
        )

        t = threading.Thread(target=competing_reconciler)
        t.start()
        try:
            milestones.release_item(a, item_ref="CB-1", status="done")
        finally:
            t.join(timeout=30)
            a.close()
        assert not t.is_alive()

        assert self._small_held(tmp_project) == 1, (
            "one of the agent's two items was released, so exactly one slot is free; "
            "0 means the release and the reconciliation hook each decremented for the "
            "same item"
        )

    def test_the_returned_row_is_the_row_that_was_written(self, tmp_project):
        """CB-30's return-value half — and it fails on unfixed code.

        Unfixed, ``release_item`` commits and *then* re-reads by ``item_ref`` through
        ``ORDER BY id DESC LIMIT 1``. A newer attachment inserted in that window wins
        the re-read, so the call reports ``status='open'`` for an item it just marked
        ``done``. Fixed, the row comes back from the UPDATE's ``RETURNING``, so no
        post-commit read exists for anything to race.

        Single-threaded on purpose: the discriminator is injected at the commit seam
        rather than by scheduling, so there is no timing luck and no ``busy_timeout``
        exposure.
        """
        self._seed(tmp_project)
        before = db.connect(tmp_project)
        try:
            mutated_id = before.execute(
                "SELECT id FROM milestone_items WHERE item_ref='CB-1'"
            ).fetchone()["id"]
        finally:
            before.close()

        path = os.path.join(tmp_project, ".codebugs", "findings.db")

        def insert_newer_attachment():
            other = sqlite3.connect(path)
            other.row_factory = sqlite3.Row
            try:
                now = "2026-08-14T00:00:00Z"
                other.execute(
                    "INSERT INTO milestone_items (milestone_id, item_kind, item_ref, size, "
                    "priority, status, acceptance, meta_json, created_at, updated_at) "
                    "VALUES ('release/1.1', 'bug', 'CB-1', 'small', 100, 'open', '', '{}', ?, ?)",
                    (now, now),
                )
                other.commit()
            finally:
                other.close()

        a = self._open(tmp_project, factory=CommitPausingConnection)
        a.after_commit = insert_newer_attachment
        try:
            result = milestones.release_item(a, item_ref="CB-1", status="done")
        finally:
            a.close()

        assert result["id"] == mutated_id, (
            "release_item must return the attachment it mutated, not whichever one is "
            "newest by the time it looks again"
        )
        assert result["status"] == "done"

    def test_a_malformed_meta_surfaces_after_the_write_has_landed(self, tmp_project):
        """CB-24 consequence (2): conversion happens outside the transaction.

        The row is read RAW inside the block, so a malformed ``meta_json`` cannot roll
        back a write the contract promises has landed. The write commits; the error is
        raised while building the return value. Unfixed, ``_get_item_by_ref`` parsed at
        the top and the call died with *no* write attempted — which is why this
        assertion is about what is in the table afterwards, not about the exception.
        """
        self._seed(tmp_project)
        seed = db.connect(tmp_project)
        try:
            seed.execute(
                "UPDATE milestone_items SET meta_json='{not json' WHERE item_ref='CB-1'"
            )
            seed.commit()
        finally:
            seed.close()

        a = self._open(tmp_project)
        try:
            with pytest.raises(json.JSONDecodeError):
                milestones.release_item(a, item_ref="CB-1", status="done")
        finally:
            a.close()

        check = db.connect(tmp_project)
        try:
            row = check.execute(
                "SELECT status FROM milestone_items WHERE item_ref='CB-1'"
            ).fetchone()
        finally:
            check.close()
        assert row["status"] == "done", (
            "the write must have landed — the failure is in serializing the response, "
            "and reporting it as if nothing happened is the CB-16 lie"
        )

    def test_an_invalid_status_beats_malformed_meta_to_the_exception(self, tmp_project):
        """Precedence, stated because the raw-row read deliberately changed it.

        Unfixed, ``_row_to_item`` parsed first and a malformed row raised
        ``JSONDecodeError`` regardless of the status argument. Now the raw read skips
        parsing, so the argument check wins. ``JSONDecodeError`` subclasses
        ``ValueError``, so the two are only distinguishable by exact type — which is
        why this asserts the message rather than the class alone.
        """
        self._seed(tmp_project)
        seed = db.connect(tmp_project)
        try:
            seed.execute(
                "UPDATE milestone_items SET meta_json='{not json' WHERE item_ref='CB-1'"
            )
            seed.commit()
        finally:
            seed.close()

        a = self._open(tmp_project)
        try:
            with pytest.raises(ValueError, match="Invalid release status"):
                milestones.release_item(a, item_ref="CB-1", status="bogus")
        finally:
            a.close()

    def test_a_missing_item_still_raises_keyerror_before_any_argument_check(self, tmp_project):
        """The uncontended ordering the change promises to preserve."""
        self._seed(tmp_project)
        a = self._open(tmp_project)
        try:
            with pytest.raises(KeyError, match="Item not found"):
                milestones.release_item(a, item_ref="CB-404", status="bogus")
        finally:
            a.close()

    def test_an_ambient_transaction_is_not_committed_by_the_batch3_item_writers(self, conn):
        """CB-36 batch 3: four more item writers now own no commit of their own.

        `move_milestone_item`, `set_item_status`, `milestone_defer` and
        `mark_integrated` each read an item row, decide from it, and write. All four
        are now wrapped, so under an ambient transaction `db.txn` yields False and the
        caller keeps ownership. Parameterized in one test because the assertion is
        identical for all four and the interesting content is the LIST — a fifth writer
        added later should be added here, which a per-function test makes easy to skip.

        No race tests for these four, and the reason is the same one recorded for
        `add_items` in tests/test_sweep.py: their illegal interleaving leaves the state
        a legal ordering also produces. Treat that as "no oracle found", not "no oracle
        exists" — a reviewer found one for `add_claim` after I claimed the same thing.
        """
        _add_finding(conn, "CB-1")
        milestones.create_milestone(
            conn, id="release/9.9", kind="release", description="batch-3 fixture"
        )

        calls = [
            ("move_milestone_item",
             lambda: milestones.move_milestone_item(
                 conn, item_ref="CB-1", to_milestone="release/9.9")),
            ("set_item_status",
             lambda: milestones.set_item_status(conn, item_ref="CB-1", status="done")),
            ("milestone_defer",
             lambda: milestones.milestone_defer(conn, item_ref="CB-1")),
            ("mark_integrated",
             lambda: milestones.mark_integrated(conn, item_ref="CB-1", commit="abc123")),
        ]

        for name, call in calls:
            before = dict(conn.execute(
                "SELECT milestone_id, status, done_commit FROM milestone_items "
                "WHERE item_ref = 'CB-1'"
            ).fetchone())

            with pytest.raises(RuntimeError, match="caller aborts"):
                with db.txn(conn) as opened:
                    assert opened, f"the caller owns this transaction ({name})"
                    call()
                    raise RuntimeError("caller aborts after the nested call")

            after = dict(conn.execute(
                "SELECT milestone_id, status, done_commit FROM milestone_items "
                "WHERE item_ref = 'CB-1'"
            ).fetchone())
            assert after == before, f"{name} must roll back with its caller"

    def test_an_ambient_transaction_is_not_committed_by_release_item(self, conn):
        """``db.txn`` yields False under an ambient transaction, so the caller owns it.

        Dormant today — ``release_item``'s only caller is a fresh-connection MCP
        wrapper — which is exactly when pinning it is cheap. Without this, "do not
        restore ``conn.commit()``" is prose.
        """
        _add_finding(conn, "CB-1")
        conn.execute("UPDATE milestone_items SET size='small' WHERE item_ref='CB-1'")
        conn.commit()

        with pytest.raises(RuntimeError, match="caller aborts"):
            with db.txn(conn) as opened:
                assert opened, "the caller owns this transaction"
                milestones.release_item(conn, item_ref="CB-1", status="done")
                raise RuntimeError("caller aborts after the nested call")

        row = conn.execute(
            "SELECT status FROM milestone_items WHERE item_ref='CB-1'"
        ).fetchone()
        assert row["status"] != "done", "the nested call must roll back with its caller"
