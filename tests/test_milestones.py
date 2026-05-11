"""Tests for codebugs milestones (Phase 1: foundation + auto-routing)."""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import blockers, db, milestones, reqs


@pytest.fixture
def tmp_project(tmp_path):
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _add_finding(conn, fid="CB-1", description="bug", **kw):
    defaults = dict(severity="medium", category="bug", file="src/x.py")
    defaults.update(kw)
    return db.add_finding(conn, finding_id=fid, description=description, **defaults)


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
        db.batch_add_findings(conn, [
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
        db._ensure_findings_schema(c)
        # Hook is already registered (module-level). It must not crash.
        result = db.add_finding(
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
