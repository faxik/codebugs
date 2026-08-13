"""CB-26: a terminal source entity must not stay live in a derived queue.

Routing was a one-time act at add time, so a resolved finding kept its `open`
milestone item forever: 19 of 23 live `stream/triage` rows pointed at already-fixed
findings and `pull_next` could hand an agent finished work.

Two mechanisms are under test, and they fail differently on purpose — the hook
keeps the STORED row honest, the defensive filter makes the guarantee hold even
when a writer bypasses the hook.
"""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import db, entities, findings, milestones, reqs, types
from codebugs.milestones._schema import TERMINAL_ITEM_OUTCOME


@pytest.fixture
def conn(tmp_path):
    db.init_project(str(tmp_path))
    c = db.connect(str(tmp_path))
    yield c
    c.close()


def _add_finding(conn, fid="CB-1", **kw):
    defaults = dict(severity="medium", category="bug", file="src/x.py")
    defaults.update(kw)
    return findings.add_finding(conn, finding_id=fid, description="bug", **defaults)


def _item(conn, ref):
    row = conn.execute(
        "SELECT * FROM milestone_items WHERE item_ref = ? ORDER BY id DESC LIMIT 1", (ref,)
    ).fetchone()
    return dict(row) if row else None


def _audit_rows(conn, ref, action="reconcile"):
    return conn.execute(
        "SELECT * FROM milestone_audit WHERE item_ref = ? AND action = ?", (ref, action)
    ).fetchall()


# ---------------------------------------------------------------------------
# The eager hook
# ---------------------------------------------------------------------------

class TestTerminalProjection:
    def test_fixed_finding_closes_its_triage_item(self, conn):
        _add_finding(conn, "CB-1")
        assert _item(conn, "CB-1")["status"] == "open"

        findings.update_finding(conn, "CB-1", status="fixed")

        item = _item(conn, "CB-1")
        assert item["status"] == "done"
        assert item["done_at"] is not None
        assert len(_audit_rows(conn, "CB-1")) == 1

    @pytest.mark.parametrize("status", ["not_a_bug", "wont_fix"])
    def test_discarded_finding_dismisses_its_item(self, conn, status):
        _add_finding(conn, "CB-2")
        findings.update_finding(conn, "CB-2", status=status)

        item = _item(conn, "CB-2")
        assert item["status"] == "dismissed"
        # `done_at` records completion, not mere closure.
        assert item["done_at"] is None

    def test_non_terminal_status_leaves_the_item_alone(self, conn):
        _add_finding(conn, "CB-3")
        findings.update_finding(conn, "CB-3", status="in_progress")
        assert _item(conn, "CB-3")["status"] == "open"

    def test_terminal_to_terminal_remaps(self, conn):
        """`fixed -> wont_fix` must move `done -> dismissed`.

        A "skip rows already terminal" filter passes every other test here and
        silently fails this one, which is why the predicate is `status != target`.
        """
        _add_finding(conn, "CB-4")
        findings.update_finding(conn, "CB-4", status="fixed")
        assert _item(conn, "CB-4")["status"] == "done"

        findings.update_finding(conn, "CB-4", status="wont_fix")
        assert _item(conn, "CB-4")["status"] == "dismissed"

    def test_requirements_project_too(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="r", section="core")
        milestones.add_milestone_item(
            conn, milestone_id="stream/triage", item_kind="requirement", item_ref="FR-001",
        )
        reqs.update_requirement(conn, "FR-001", status="implemented")
        assert _item(conn, "FR-001")["status"] == "done"

    def test_obsolete_requirement_is_dismissed(self, conn):
        reqs.add_requirement(conn, req_id="FR-002", description="r", section="core")
        milestones.add_milestone_item(
            conn, milestone_id="stream/triage", item_kind="requirement", item_ref="FR-002",
        )
        reqs.update_requirement(conn, "FR-002", status="obsolete")
        assert _item(conn, "FR-002")["status"] == "dismissed"


class TestScopeAndIsolation:
    def test_external_row_sharing_the_ref_is_untouched(self, conn):
        """`(bug, CB-5)` and `(external, CB-5)` are both legal rows.

        `_validate_item_ref` skips validation for externals and UNIQUE includes
        `item_kind`, so matching on `item_ref` alone would close a row that is not
        a projection of the finding at all.
        """
        _add_finding(conn, "CB-5")
        milestones.add_milestone_item(
            conn, milestone_id="stream/maintenance", item_kind="external", item_ref="CB-5",
        )
        findings.update_finding(conn, "CB-5", status="fixed")

        ext = conn.execute(
            "SELECT status FROM milestone_items WHERE item_kind='external' AND item_ref='CB-5'"
        ).fetchone()
        assert ext["status"] == "open"

    def test_deferred_items_are_not_closed(self, conn):
        """Deferral is a record, and no queue returns a deferred row anyway."""
        _add_finding(conn, "CB-6")
        conn.execute("UPDATE milestone_items SET status='deferred' WHERE item_ref='CB-6'")
        conn.commit()

        findings.update_finding(conn, "CB-6", status="fixed")
        assert _item(conn, "CB-6")["status"] == "deferred"

    def test_release_milestone_items_are_left_to_the_close_gate(self, conn):
        """The hook is stream-scoped so it cannot weaken `milestone_close`.

        `milestone_close`'s unfinished check reads only the item status, and
        `done_commit` is never a gate, so auto-marking a release item `done` would
        let a release close over a missed integration step.
        """
        _add_finding(conn, "CB-7")
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1", item_kind="bug", item_ref="CB-7",
        )
        findings.update_finding(conn, "CB-7", status="fixed")

        rel = conn.execute(
            "SELECT status FROM milestone_items "
            "WHERE milestone_id='release/1.1' AND item_ref='CB-7'"
        ).fetchone()
        assert rel["status"] == "open"
        with pytest.raises(ValueError, match="unfinished"):
            milestones.milestone_close(conn, id="release/1.1")


class TestCapacityIsReleased:
    def test_pulled_item_releases_its_slot(self, conn):
        """Closing a pulled item without decrementing would leak the agent's slot
        permanently — the fix's own edge case."""
        _add_finding(conn, "CB-8")
        pulled = milestones.pull_next(
            conn, agent_id="agent-a", capacity={"triage": 1, "small": 1, "large": 1},
        )
        assert pulled is not None and pulled["item_ref"] == "CB-8"
        held = conn.execute(
            "SELECT triage_held FROM agent_capacity WHERE agent_id='agent-a'"
        ).fetchone()
        assert held["triage_held"] == 1

        findings.update_finding(conn, "CB-8", status="fixed")

        item = _item(conn, "CB-8")
        assert item["status"] == "done"
        assert item["assigned_agent"] is None
        held = conn.execute(
            "SELECT triage_held FROM agent_capacity WHERE agent_id='agent-a'"
        ).fetchone()
        assert held["triage_held"] == 0


class TestIdempotenceAndAtomicity:
    def test_second_terminal_write_adds_no_second_audit_row(self, conn):
        _add_finding(conn, "CB-9")
        findings.update_finding(conn, "CB-9", status="fixed")
        findings.update_finding(conn, "CB-9", status="fixed")
        assert len(_audit_rows(conn, "CB-9")) == 1

    def test_item_change_rolls_back_with_the_caller(self, conn):
        """Atomicity must be induced in the CALLER's frame.

        Raising inside the hook proves nothing: `run_status_change_hooks` swallows,
        so the transaction would commit regardless.
        """
        _add_finding(conn, "CB-10")
        with pytest.raises(RuntimeError):
            with db.txn(conn):
                findings.update_finding(conn, "CB-10", status="fixed")
                raise RuntimeError("caller aborts")

        assert _item(conn, "CB-10")["status"] == "open"
        assert conn.execute(
            "SELECT status FROM findings WHERE id='CB-10'"
        ).fetchone()["status"] == "open"

    def test_triage_dismiss_still_works_and_does_not_double_write(self, conn):
        """Non-regression: `triage_dismiss` closes the item, then propagates to the
        finding, which now fires the hook."""
        _add_finding(conn, "CB-11")
        milestones.triage_dismiss(conn, bug_id="CB-11", reason="not real")

        assert _item(conn, "CB-11")["status"] == "dismissed"
        assert conn.execute(
            "SELECT status FROM findings WHERE id='CB-11'"
        ).fetchone()["status"] == "not_a_bug"
        assert len(_audit_rows(conn, "CB-11")) == 0  # dismissal audited as `dismiss`


# ---------------------------------------------------------------------------
# The defensive filter — what makes the invariant actually hold
# ---------------------------------------------------------------------------

class TestDefensiveFiltering:
    def _stale_row(self, conn, ref="CB-12", milestone="stream/triage"):
        """Reproduce a bypass: resolve the finding, then force the row back open the
        way `set_item_status` / `release_item(abandoned)` / the importers do."""
        _add_finding(conn, ref)
        findings.update_finding(conn, ref, status="fixed")
        conn.execute(
            "UPDATE milestone_items SET status='open', done_at=NULL WHERE item_ref=?", (ref,)
        )
        conn.commit()

    def test_triage_inbox_hides_a_terminal_source(self, conn):
        self._stale_row(conn)
        assert conn.execute(
            "SELECT status FROM milestone_items WHERE item_ref='CB-12'"
        ).fetchone()["status"] == "open"
        assert [i["item_ref"] for i in milestones.triage_inbox(conn)] == []

    def test_pull_next_refuses_a_terminal_source(self, conn):
        self._stale_row(conn, "CB-13")
        assert milestones.pull_next(
            conn, agent_id="a", capacity={"triage": 5, "small": 5, "large": 5},
        ) is None

    def test_limit_counts_live_rows_only(self, conn):
        """The LIMIT is applied after filtering. Pushing it into SQL would return
        fewer than `limit` live rows whenever stale ones sort ahead."""
        self._stale_row(conn, "CB-14")
        _add_finding(conn, "CB-15")
        assert [i["item_ref"] for i in milestones.triage_inbox(conn, limit=1)] == ["CB-15"]

    def test_live_items_are_still_returned(self, conn):
        _add_finding(conn, "CB-16")
        assert [i["item_ref"] for i in milestones.triage_inbox(conn)] == ["CB-16"]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

class TestBackfill:
    def _stale(self, conn, ref):
        _add_finding(conn, ref)
        findings.update_finding(conn, ref, status="fixed")
        conn.execute(
            "UPDATE milestone_items SET status='open', done_at=NULL WHERE item_ref=?", (ref,)
        )
        conn.commit()

    def test_dry_run_reports_without_writing(self, conn):
        self._stale(conn, "CB-17")
        result = milestones.reconcile_all(conn)
        assert result["applied"] is False
        assert result["candidates"] == 1
        assert _item(conn, "CB-17")["status"] == "open"

    def test_apply_closes_exactly_the_stale_rows(self, conn):
        self._stale(conn, "CB-18")
        _add_finding(conn, "CB-19")  # live, must survive

        result = milestones.reconcile_all(conn, apply=True)
        assert result["applied"] is True and result["candidates"] == 1
        assert _item(conn, "CB-18")["status"] == "done"
        assert _item(conn, "CB-19")["status"] == "open"

    def test_second_run_is_a_no_op(self, conn):
        self._stale(conn, "CB-20")
        milestones.reconcile_all(conn, apply=True)
        assert milestones.reconcile_all(conn, apply=True)["candidates"] == 0


# ---------------------------------------------------------------------------
# Ratchets — these are CI drift gates, NOT regression tests. Neither can fail
# by reverting the fix; both fail when someone extends a vocabulary without
# deciding its projection.
# ---------------------------------------------------------------------------

class TestTerminalOutcomeMapIsComplete:
    def test_every_terminal_status_has_a_declared_projection(self):
        assert set(TERMINAL_ITEM_OUTCOME[types.ENTITY_FINDING]) == types.FINDING_TERMINAL
        assert (
            set(TERMINAL_ITEM_OUTCOME[types.ENTITY_REQUIREMENT])
            == types.REQUIREMENT_TERMINAL
        )

    def test_every_entity_kind_is_covered(self):
        assert {k.name for k in entities.ENTITY_KINDS} == set(TERMINAL_ITEM_OUTCOME)

    def test_targets_are_declared_item_statuses(self):
        from codebugs.milestones._schema import MILESTONE_ITEM_TERMINAL
        for per_kind in TERMINAL_ITEM_OUTCOME.values():
            assert set(per_kind.values()) <= MILESTONE_ITEM_TERMINAL

    def test_unknown_pair_fails_closed(self):
        from codebugs.milestones._schema import outcome_for
        with pytest.raises(ValueError, match="No declared milestone-item projection"):
            outcome_for(types.ENTITY_FINDING, "stale")


class TestSchemaProbe:
    def test_update_finding_survives_a_milestone_less_connection(self, tmp_path):
        """Non-regression for raw `sqlite3.connect()` callers. Its real revert is
        removing the probe, not the registration."""
        path = str(tmp_path / "raw.db")
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        findings.ensure_schema(c)
        findings.add_finding(
            c, finding_id="CB-99", description="x", severity="low",
            category="bug", file="a.py",
        )
        findings.update_finding(c, "CB-99", status="fixed")
        assert c.execute(
            "SELECT status FROM findings WHERE id='CB-99'"
        ).fetchone()["status"] == "fixed"
        c.close()
