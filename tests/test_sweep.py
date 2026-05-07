"""Tests for the sweep batch-iteration module."""

from __future__ import annotations

import json
import sqlite3

import pytest

from codebugs import sweep


@pytest.fixture
def conn():
    """In-memory database with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    sweep.ensure_schema(c)
    yield c
    c.close()


class TestCreateSweep:
    def test_basic_create(self, conn):
        result = sweep.create_sweep(conn)
        assert result["sweep_id"] == "SW-1"
        assert result["status"] == "active"
        assert result["default_batch_size"] == 10

    def test_create_with_name(self, conn):
        result = sweep.create_sweep(conn, name="lint-pass")
        assert result["name"] == "lint-pass"
        assert result["sweep_id"] == "SW-1"

    def test_create_with_description(self, conn):
        result = sweep.create_sweep(conn, description="Review all controllers")
        assert result["description"] == "Review all controllers"

    def test_create_with_batch_size(self, conn):
        result = sweep.create_sweep(conn, default_batch_size=5)
        assert result["default_batch_size"] == 5

    def test_auto_increment_id(self, conn):
        r1 = sweep.create_sweep(conn)
        r2 = sweep.create_sweep(conn)
        assert r1["sweep_id"] == "SW-1"
        assert r2["sweep_id"] == "SW-2"

    def test_duplicate_name_raises(self, conn):
        sweep.create_sweep(conn, name="lint")
        with pytest.raises(ValueError, match="already exists"):
            sweep.create_sweep(conn, name="lint")

    def test_batch_size_zero_raises(self, conn):
        with pytest.raises(ValueError, match="at least 1"):
            sweep.create_sweep(conn, default_batch_size=0)


class TestAddItems:
    def test_add_basic(self, conn):
        sw = sweep.create_sweep(conn)
        result = sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        assert result["added"] == 3
        assert result["duplicates_skipped"] == 0

    def test_add_with_tags(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"], tags=["critical"])
        row = conn.execute(
            "SELECT tags FROM codesweep_items WHERE item = 'a.py'"
        ).fetchone()
        assert json.loads(row["tags"]) == ["critical"]

    def test_add_duplicates_skipped(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"])
        result = sweep.add_items(conn, sw["sweep_id"], ["b.py", "c.py"])
        assert result["added"] == 1
        # Backward compat alias for recurrence_bumped
        assert result["duplicates_skipped"] == 1
        assert result["recurrence_bumped"] == 1

    def test_add_preserves_position_order(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"])
        sweep.add_items(conn, sw["sweep_id"], ["c.py"])
        rows = conn.execute(
            "SELECT item, position FROM codesweep_items ORDER BY position"
        ).fetchall()
        assert [r["item"] for r in rows] == ["a.py", "b.py", "c.py"]
        assert [r["position"] for r in rows] == [0, 1, 2]

    def test_add_to_archived_raises(self, conn):
        sw = sweep.create_sweep(conn)
        conn.execute(
            "UPDATE codesweep_sweeps SET status = 'archived' WHERE sweep_id = ?",
            (sw["sweep_id"],),
        )
        conn.commit()
        with pytest.raises(ValueError, match="archived"):
            sweep.add_items(conn, sw["sweep_id"], ["a.py"])

    def test_add_by_name(self, conn):
        sweep.create_sweep(conn, name="my-sweep")
        result = sweep.add_items(conn, "my-sweep", ["a.py"])
        assert result["added"] == 1

    def test_add_to_nonexistent_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            sweep.add_items(conn, "SW-999", ["a.py"])


class TestNextBatch:
    @pytest.fixture(autouse=True)
    def setup(self, conn):
        self.conn = conn
        sw = sweep.create_sweep(conn, default_batch_size=2)
        self.sweep_id = sw["sweep_id"]
        sweep.add_items(conn, self.sweep_id, ["a.py", "b.py", "c.py", "d.py", "e.py"])

    def test_returns_default_batch_size(self):
        result = sweep.next_batch(self.conn, self.sweep_id)
        assert len(result["items"]) == 2
        assert result["items"][0]["item"] == "a.py"
        assert result["items"][1]["item"] == "b.py"

    def test_override_limit(self):
        result = sweep.next_batch(self.conn, self.sweep_id, limit=3)
        assert len(result["items"]) == 3

    def test_remaining_count(self):
        result = sweep.next_batch(self.conn, self.sweep_id)
        # remaining = total unprocessed - items in this batch
        assert result["remaining"] == 3  # 5 unprocessed - 2 returned

    def test_skips_processed_items(self):
        sweep.mark_items(self.conn, self.sweep_id, ["a.py", "b.py"])
        result = sweep.next_batch(self.conn, self.sweep_id)
        assert result["items"][0]["item"] == "c.py"

    def test_empty_when_all_processed(self):
        sweep.mark_items(self.conn, self.sweep_id, ["a.py", "b.py", "c.py", "d.py", "e.py"])
        result = sweep.next_batch(self.conn, self.sweep_id)
        assert result["items"] == []
        assert result["remaining"] == 0

    def test_tag_filtering(self):
        sw = sweep.create_sweep(self.conn, default_batch_size=10)
        sweep.add_items(self.conn, sw["sweep_id"], ["x.py", "y.py"], tags=["critical"])
        sweep.add_items(self.conn, sw["sweep_id"], ["z.py"], tags=["low"])
        result = sweep.next_batch(self.conn, sw["sweep_id"], tags=["critical"])
        assert len(result["items"]) == 2
        assert {i["item"] for i in result["items"]} == {"x.py", "y.py"}

    def test_tag_filtering_any_match(self):
        sw = sweep.create_sweep(self.conn, default_batch_size=10)
        sweep.add_items(self.conn, sw["sweep_id"], ["x.py"], tags=["critical"])
        sweep.add_items(self.conn, sw["sweep_id"], ["y.py"], tags=["low"])
        sweep.add_items(self.conn, sw["sweep_id"], ["z.py"], tags=["medium"])
        result = sweep.next_batch(self.conn, sw["sweep_id"], tags=["critical", "low"])
        assert len(result["items"]) == 2

    def test_items_include_position_and_tags(self):
        result = sweep.next_batch(self.conn, self.sweep_id)
        item = result["items"][0]
        assert "item" in item
        assert "tags" in item
        assert "position" in item
        assert isinstance(item["tags"], list)

    def test_by_name(self):
        sw = sweep.create_sweep(self.conn, name="named", default_batch_size=10)
        sweep.add_items(self.conn, "named", ["f.py"])
        result = sweep.next_batch(self.conn, "named")
        assert len(result["items"]) == 1


class TestMarkItems:
    @pytest.fixture(autouse=True)
    def setup(self, conn):
        self.conn = conn
        sw = sweep.create_sweep(conn)
        self.sweep_id = sw["sweep_id"]
        sweep.add_items(conn, self.sweep_id, ["a.py", "b.py", "c.py"])

    def test_mark_processed(self):
        result = sweep.mark_items(self.conn, self.sweep_id, ["a.py", "b.py"])
        assert result["updated"] == 2
        row = self.conn.execute(
            "SELECT processed, processed_at FROM codesweep_items WHERE item = 'a.py'"
        ).fetchone()
        assert row["processed"] == 1
        assert row["processed_at"] is not None

    def test_unmark(self):
        sweep.mark_items(self.conn, self.sweep_id, ["a.py"])
        result = sweep.mark_items(self.conn, self.sweep_id, ["a.py"], processed=False)
        assert result["updated"] == 1
        row = self.conn.execute(
            "SELECT processed, processed_at FROM codesweep_items WHERE item = 'a.py'"
        ).fetchone()
        assert row["processed"] == 0
        assert row["processed_at"] is None

    def test_mark_nonexistent_raises(self):
        with pytest.raises(KeyError, match="not found"):
            sweep.mark_items(self.conn, self.sweep_id, ["nonexistent.py"])

    def test_mark_by_name(self):
        sw = sweep.create_sweep(self.conn, name="named")
        sweep.add_items(self.conn, "named", ["x.py"])
        result = sweep.mark_items(self.conn, "named", ["x.py"])
        assert result["updated"] == 1


class TestGetStatus:
    def test_status_counts(self, conn):
        sw = sweep.create_sweep(conn, name="test")
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        sweep.mark_items(conn, sw["sweep_id"], ["a.py"])
        result = sweep.get_status(conn, sw["sweep_id"])
        assert result["sweep_id"] == sw["sweep_id"]
        assert result["name"] == "test"
        assert result["total"] == 3
        assert result["processed"] == 1
        assert result["remaining"] == 2

    def test_status_by_tag(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"], tags=["critical"])
        sweep.add_items(conn, sw["sweep_id"], ["c.py"], tags=["low"])
        sweep.mark_items(conn, sw["sweep_id"], ["a.py"])
        result = sweep.get_status(conn, sw["sweep_id"])
        assert result["by_tag"]["critical"]["total"] == 2
        assert result["by_tag"]["critical"]["processed"] == 1
        assert result["by_tag"]["low"]["total"] == 1
        assert result["by_tag"]["low"]["processed"] == 0

    def test_status_empty_sweep(self, conn):
        sw = sweep.create_sweep(conn)
        result = sweep.get_status(conn, sw["sweep_id"])
        assert result["total"] == 0
        assert result["processed"] == 0
        assert result["remaining"] == 0
        assert result["by_tag"] == {}

    def test_status_by_name(self, conn):
        sweep.create_sweep(conn, name="named")
        result = sweep.get_status(conn, "named")
        assert result["name"] == "named"


class TestArchiveSweep:
    def test_archive(self, conn):
        sw = sweep.create_sweep(conn)
        result = sweep.archive_sweep(conn, sw["sweep_id"])
        assert result["status"] == "archived"

    def test_archive_by_name(self, conn):
        sweep.create_sweep(conn, name="old")
        result = sweep.archive_sweep(conn, "old")
        assert result["status"] == "archived"

    def test_archive_not_found(self, conn):
        with pytest.raises(ValueError, match="not found"):
            sweep.archive_sweep(conn, "SW-999")


class TestListSweeps:
    def test_list_active_only(self, conn):
        sweep.create_sweep(conn, name="active1")
        sw2 = sweep.create_sweep(conn, name="archived1")
        sweep.archive_sweep(conn, sw2["sweep_id"])
        result = sweep.list_sweeps(conn)
        assert len(result["sweeps"]) == 1
        assert result["sweeps"][0]["name"] == "active1"

    def test_list_include_archived(self, conn):
        sweep.create_sweep(conn, name="a")
        sw2 = sweep.create_sweep(conn, name="b")
        sweep.archive_sweep(conn, sw2["sweep_id"])
        result = sweep.list_sweeps(conn, include_archived=True)
        assert len(result["sweeps"]) == 2

    def test_list_with_counts(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        sweep.mark_items(conn, sw["sweep_id"], ["a.py"])
        result = sweep.list_sweeps(conn)
        s = result["sweeps"][0]
        assert s["total"] == 3
        assert s["processed"] == 1
        assert s["remaining"] == 2

    def test_list_empty(self, conn):
        result = sweep.list_sweeps(conn)
        assert result["sweeps"] == []


class TestFullWorkflow:
    """End-to-end test simulating a real sweep pass."""

    def test_complete_sweep_lifecycle(self, conn):
        # Create
        sw = sweep.create_sweep(conn, name="lint-pass", default_batch_size=2)
        assert sw["sweep_id"] == "SW-1"

        # Add items in two batches
        sweep.add_items(conn, "lint-pass", ["a.py", "b.py", "c.py"], tags=["src"])
        sweep.add_items(conn, "lint-pass", ["test_a.py", "test_b.py"], tags=["test"])

        # Check status
        status = sweep.get_status(conn, "lint-pass")
        assert status["total"] == 5
        assert status["processed"] == 0
        assert status["by_tag"]["src"]["total"] == 3
        assert status["by_tag"]["test"]["total"] == 2

        # Iterate: batch 1
        batch1 = sweep.next_batch(conn, "lint-pass")
        assert len(batch1["items"]) == 2
        assert batch1["items"][0]["item"] == "a.py"
        assert batch1["remaining"] == 3
        sweep.mark_items(conn, "lint-pass", [i["item"] for i in batch1["items"]])

        # Iterate: batch 2
        batch2 = sweep.next_batch(conn, "lint-pass")
        assert len(batch2["items"]) == 2
        assert batch2["items"][0]["item"] == "c.py"
        sweep.mark_items(conn, "lint-pass", [i["item"] for i in batch2["items"]])

        # Iterate: batch 3 (last item)
        batch3 = sweep.next_batch(conn, "lint-pass")
        assert len(batch3["items"]) == 1
        assert batch3["items"][0]["item"] == "test_b.py"
        assert batch3["remaining"] == 0
        sweep.mark_items(conn, "lint-pass", [i["item"] for i in batch3["items"]])

        # All done
        batch4 = sweep.next_batch(conn, "lint-pass")
        assert batch4["items"] == []

        # Status shows complete
        final = sweep.get_status(conn, "lint-pass")
        assert final["processed"] == 5
        assert final["remaining"] == 0

        # Archive
        sweep.archive_sweep(conn, "lint-pass")
        sweeps = sweep.list_sweeps(conn)
        assert len(sweeps["sweeps"]) == 0
        sweeps_all = sweep.list_sweeps(conn, include_archived=True)
        assert len(sweeps_all["sweeps"]) == 1

    def test_tag_filtered_sweep(self, conn):
        sw = sweep.create_sweep(conn, default_batch_size=10)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"], tags=["critical"])
        sweep.add_items(conn, sw["sweep_id"], ["c.py", "d.py", "e.py"], tags=["low"])

        # Only process critical items
        batch = sweep.next_batch(conn, sw["sweep_id"], tags=["critical"])
        assert len(batch["items"]) == 2
        sweep.mark_items(conn, sw["sweep_id"], [i["item"] for i in batch["items"]])

        # Status shows 2 processed total, critical fully done
        status = sweep.get_status(conn, sw["sweep_id"])
        assert status["processed"] == 2
        assert status["remaining"] == 3
        assert status["by_tag"]["critical"]["processed"] == 2

    def test_unmark_and_reprocess(self, conn):
        sw = sweep.create_sweep(conn, default_batch_size=10)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        sweep.mark_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])

        # Oops, b.py needs reprocessing
        sweep.mark_items(conn, sw["sweep_id"], ["b.py"], processed=False)

        batch = sweep.next_batch(conn, sw["sweep_id"])
        assert len(batch["items"]) == 1
        assert batch["items"][0]["item"] == "b.py"


# ---------------------------------------------------------------------------
# PR1: F1 (recurrence) + F2 (lifecycle) + F5 (selective archive)
# ---------------------------------------------------------------------------


class TestRecurrence:
    """F1 — atomic upsert bumps recurrence_count on duplicate adds."""

    def test_recurrence_count_starts_at_one(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        items = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert items[0]["recurrence_count"] == 1

    def test_recurrence_count_bumps(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        items = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert items[0]["recurrence_count"] == 3
        assert len(items) == 1  # No duplicate row

    def test_first_seen_does_not_change(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        first = sweep.list_items(conn, sw["sweep_id"])["items"][0]["first_seen"]
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        second = sweep.list_items(conn, sw["sweep_id"])["items"][0]["first_seen"]
        assert first == second

    def test_last_seen_set_on_insert_and_update(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        first = sweep.list_items(conn, sw["sweep_id"])["items"][0]["last_seen"]
        assert first is not None
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        item = sweep.list_items(conn, sw["sweep_id"])["items"][0]
        # last_seen monotonic across re-adds (utc_now has 1s resolution; same-second
        # is OK as long as the field was rewritten — recurrence_count proves it ran).
        assert item["last_seen"] >= first
        assert item["recurrence_count"] == 2

    def test_tags_overwrite_on_bump(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"], tags=["t1"])
        sweep.add_items(conn, sw["sweep_id"], ["a.py"], tags=["t2"])
        items = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert items[0]["tags"] == ["t2"]

    def test_position_preserved_on_bump(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"])
        sweep.add_items(conn, sw["sweep_id"], ["b.py"])
        items = sweep.list_items(conn, sw["sweep_id"])["items"]
        positions = {i["item"]: i["position"] for i in items}
        assert positions["a.py"] == 0
        assert positions["b.py"] == 1


class TestLifecycle:
    """F2 — configurable lifecycle states + optional transition DAG."""

    def test_default_lifecycle(self, conn):
        sw = sweep.create_sweep(conn)
        assert sw["lifecycle"] == ["pending", "done"]
        assert sw["terminal_states"] == ["done"]
        assert sw["transitions"] is None

    def test_custom_lifecycle(self, conn):
        sw = sweep.create_sweep(
            conn,
            lifecycle=["DETECTED", "CONFIRMED", "RESOLVED"],
            terminal_states=["RESOLVED"],
        )
        assert sw["lifecycle"] == ["DETECTED", "CONFIRMED", "RESOLVED"]
        assert sw["terminal_states"] == ["RESOLVED"]

    def test_default_terminal_for_custom_lifecycle(self, conn):
        sw = sweep.create_sweep(conn, lifecycle=["a", "b", "c"])
        # No 'done' in lifecycle, last state used as default terminal
        assert sw["terminal_states"] == ["c"]

    def test_lifecycle_must_be_unique(self, conn):
        with pytest.raises(ValueError, match="unique"):
            sweep.create_sweep(conn, lifecycle=["a", "b", "a"])

    def test_lifecycle_must_be_nonempty(self, conn):
        with pytest.raises(ValueError, match="at least one"):
            sweep.create_sweep(conn, lifecycle=[])

    def test_terminal_states_must_be_subset(self, conn):
        with pytest.raises(ValueError, match="not in lifecycle"):
            sweep.create_sweep(
                conn,
                lifecycle=["a", "b"],
                terminal_states=["c"],
            )

    def test_explicit_state_marking(self, conn):
        sw = sweep.create_sweep(
            conn,
            lifecycle=["DETECTED", "CONFIRMED", "RESOLVED"],
            terminal_states=["RESOLVED"],
        )
        sweep.add_items(conn, sw["sweep_id"], ["finding-1"])
        sweep.mark_items(conn, sw["sweep_id"], ["finding-1"], state="CONFIRMED")
        items = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert items[0]["state"] == "CONFIRMED"
        assert items[0]["processed"] is False  # not yet terminal

        sweep.mark_items(conn, sw["sweep_id"], ["finding-1"], state="RESOLVED")
        items = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert items[0]["state"] == "RESOLVED"
        assert items[0]["processed"] is True  # terminal

    def test_invalid_state_rejected(self, conn):
        sw = sweep.create_sweep(conn, lifecycle=["a", "b"])
        sweep.add_items(conn, sw["sweep_id"], ["x"])
        with pytest.raises(ValueError, match="not in sweep lifecycle"):
            sweep.mark_items(conn, sw["sweep_id"], ["x"], state="zzz")

    def test_legacy_processed_true_maps_to_first_terminal(self, conn):
        sw = sweep.create_sweep(
            conn,
            lifecycle=["a", "b", "c", "d"],
            terminal_states=["c", "d"],
        )
        sweep.add_items(conn, sw["sweep_id"], ["x"])
        result = sweep.mark_items(conn, sw["sweep_id"], ["x"], processed=True)
        assert result["state"] == "c"  # first terminal

    def test_legacy_processed_false_maps_to_first_non_terminal(self, conn):
        sw = sweep.create_sweep(
            conn,
            lifecycle=["a", "b", "c"],
            terminal_states=["c"],
        )
        sweep.add_items(conn, sw["sweep_id"], ["x"])
        sweep.mark_items(conn, sw["sweep_id"], ["x"], state="c")
        result = sweep.mark_items(conn, sw["sweep_id"], ["x"], processed=False)
        assert result["state"] == "a"

    def test_transition_dag_enforced(self, conn):
        sw = sweep.create_sweep(
            conn,
            lifecycle=["DETECTED", "CONFIRMED", "RESOLVED", "DROPPED"],
            terminal_states=["RESOLVED", "DROPPED"],
            transitions={
                "DETECTED": ["CONFIRMED", "DROPPED"],
                "CONFIRMED": ["RESOLVED"],
                "RESOLVED": [],
                "DROPPED": [],
            },
        )
        sweep.add_items(conn, sw["sweep_id"], ["x"])
        # Allowed: DETECTED -> CONFIRMED
        sweep.mark_items(conn, sw["sweep_id"], ["x"], state="CONFIRMED")
        # Not allowed: CONFIRMED -> DETECTED
        with pytest.raises(ValueError, match="Transition not allowed"):
            sweep.mark_items(conn, sw["sweep_id"], ["x"], state="DETECTED")

    def test_transition_dag_idempotent(self, conn):
        sw = sweep.create_sweep(
            conn,
            lifecycle=["a", "b"],
            transitions={"a": ["b"], "b": []},
        )
        sweep.add_items(conn, sw["sweep_id"], ["x"])
        # Same state -> same state should always be allowed
        sweep.mark_items(conn, sw["sweep_id"], ["x"], state="a")

    def test_transitions_validated_at_create(self, conn):
        with pytest.raises(ValueError, match="not in lifecycle"):
            sweep.create_sweep(
                conn,
                lifecycle=["a", "b"],
                transitions={"zzz": ["a"]},
            )
        with pytest.raises(ValueError, match="not in lifecycle"):
            sweep.create_sweep(
                conn,
                lifecycle=["a", "b"],
                transitions={"a": ["zzz"]},
            )


class TestSelectiveArchive:
    """F5 — entry-level archive with soft-delete + un-archive on re-add."""

    def test_archive_specific_items(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        result = sweep.archive_items(conn, sw["sweep_id"], items=["a.py", "c.py"])
        assert result["archived"] == 2

        live = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert {i["item"] for i in live} == {"b.py"}

        archived = sweep.list_items(conn, sw["sweep_id"], archived_only=True)["items"]
        assert {i["item"] for i in archived} == {"a.py", "c.py"}

    def test_archive_by_state(self, conn):
        sw = sweep.create_sweep(
            conn,
            lifecycle=["DETECTED", "RESOLVED", "DROPPED"],
            terminal_states=["RESOLVED", "DROPPED"],
        )
        sweep.add_items(conn, sw["sweep_id"], ["a", "b", "c"])
        sweep.mark_items(conn, sw["sweep_id"], ["a", "b"], state="RESOLVED")
        sweep.mark_items(conn, sw["sweep_id"], ["c"], state="DROPPED")

        result = sweep.archive_items(conn, sw["sweep_id"], where_status="RESOLVED")
        assert result["archived"] == 2

        live = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert {i["item"] for i in live} == {"c"}

    def test_archive_with_reason(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        sweep.archive_items(
            conn, sw["sweep_id"], items=["a.py"], reason="cleanup-2026-Q2",
        )
        archived = sweep.list_items(conn, sw["sweep_id"], archived_only=True)["items"]
        assert archived[0]["archive_reason"] == "cleanup-2026-Q2"

    def test_archive_excludes_from_next_batch(self, conn):
        sw = sweep.create_sweep(conn, default_batch_size=10)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        sweep.archive_items(conn, sw["sweep_id"], items=["a.py"])

        batch = sweep.next_batch(conn, sw["sweep_id"])
        assert {i["item"] for i in batch["items"]} == {"b.py", "c.py"}

    def test_archive_excludes_from_status_totals(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        sweep.archive_items(conn, sw["sweep_id"], items=["a.py"])

        s = sweep.get_status(conn, sw["sweep_id"])
        assert s["total"] == 2
        assert s["archived"] == 1

    def test_re_add_un_archives(self, conn):
        """Critical R5 invariant — recurrence count carries forward across archive cycles."""
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["finding-1"])  # rc=1
        sweep.add_items(conn, sw["sweep_id"], ["finding-1"])  # rc=2
        sweep.archive_items(conn, sw["sweep_id"], items=["finding-1"])

        # Confirm archived and excluded
        live = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert live == []

        # Re-add un-archives and bumps to 3
        result = sweep.add_items(conn, sw["sweep_id"], ["finding-1"])
        assert result["added"] == 0
        assert result["recurrence_bumped"] == 1

        items = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert len(items) == 1
        assert items[0]["recurrence_count"] == 3
        assert items[0]["archived_at"] is None
        assert items[0]["archive_reason"] is None

    def test_archive_older_than(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["recent.py", "old.py"])
        # Backdate `last_seen` for old.py
        conn.execute(
            "UPDATE codesweep_items SET last_seen = '2020-01-01T00:00:00+00:00', "
            "created_at = '2020-01-01T00:00:00+00:00' "
            "WHERE item = 'old.py'"
        )
        conn.commit()
        result = sweep.archive_items(conn, sw["sweep_id"], older_than="30d")
        assert result["archived"] == 1
        live = sweep.list_items(conn, sw["sweep_id"])["items"]
        assert {i["item"] for i in live} == {"recent.py"}

    def test_archive_requires_filter(self, conn):
        sw = sweep.create_sweep(conn)
        with pytest.raises(ValueError, match="at least one of"):
            sweep.archive_items(conn, sw["sweep_id"])

    def test_archive_invalid_state_rejected(self, conn):
        sw = sweep.create_sweep(conn)
        with pytest.raises(ValueError, match="not in sweep lifecycle"):
            sweep.archive_items(conn, sw["sweep_id"], where_status="BOGUS")

    def test_archive_combined_filters(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py", "c.py"])
        sweep.mark_items(conn, sw["sweep_id"], ["a.py"], state="done")
        sweep.mark_items(conn, sw["sweep_id"], ["b.py"], state="done")
        # Only archive a.py — both filters must apply
        result = sweep.archive_items(
            conn, sw["sweep_id"], items=["a.py"], where_status="done",
        )
        assert result["archived"] == 1

    def test_cannot_mark_archived_item(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"])
        sweep.archive_items(conn, sw["sweep_id"], items=["a.py"])
        with pytest.raises(ValueError, match="archived"):
            sweep.mark_items(conn, sw["sweep_id"], ["a.py"])


class TestMigration:
    """Schema migration on legacy DBs: missing columns are added idempotently."""

    def test_legacy_db_gets_new_columns(self, tmp_path):
        # Build a legacy schema by hand (no new columns)
        db_path = tmp_path / "legacy.db"
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        c.executescript(
            """
            CREATE TABLE codesweep_sweeps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_id TEXT UNIQUE NOT NULL,
                name TEXT,
                description TEXT NOT NULL DEFAULT '',
                default_batch_size INTEGER NOT NULL DEFAULT 10,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE codesweep_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_id TEXT NOT NULL,
                item TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                processed INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                UNIQUE(sweep_id, item)
            );
            INSERT INTO codesweep_sweeps
                (sweep_id, name, description, default_batch_size, status, created_at, updated_at)
                VALUES ('SW-1', 'legacy', '', 10, 'active', '2024-01-01', '2024-01-01');
            INSERT INTO codesweep_items
                (sweep_id, item, tags, processed, position, created_at, processed_at)
                VALUES
                ('SW-1', 'a.py', '[]', 1, 0, '2024-01-01', '2024-01-02'),
                ('SW-1', 'b.py', '[]', 0, 1, '2024-01-01', NULL);
            """
        )
        c.commit()

        # Migrate
        sweep.ensure_schema(c)

        # New columns present and backfilled
        items = sweep.list_items(c, "SW-1", include_archived=True)["items"]
        by_name = {i["item"]: i for i in items}
        assert by_name["a.py"]["state"] == "done"
        assert by_name["b.py"]["state"] == "pending"
        assert by_name["a.py"]["recurrence_count"] == 1
        assert by_name["a.py"]["archived_at"] is None

        # Sweep gets default lifecycle
        s = sweep.get_status(c, "SW-1")
        assert s["lifecycle"] == ["pending", "done"]
        c.close()


class TestConcurrentAdd:
    """PR1 acceptance criterion #7: 10 parallel adds for same key -> exactly one
    entry with recurrence_count = 10."""

    def test_concurrent_upsert_atomic(self, tmp_path):
        import threading

        db_path = tmp_path / "concurrent.db"
        # Initialize schema with a primary connection
        primary = sqlite3.connect(db_path)
        primary.row_factory = sqlite3.Row
        sweep.ensure_schema(primary)
        primary.execute("PRAGMA journal_mode = WAL")
        sw = sweep.create_sweep(primary, name="stress")
        sweep_id = sw["sweep_id"]
        primary.commit()

        N = 10
        errors: list[Exception] = []
        barrier = threading.Barrier(N)

        def worker():
            try:
                c = sqlite3.connect(db_path, timeout=10.0)
                c.row_factory = sqlite3.Row
                # Wait for all threads to be ready, then race
                barrier.wait()
                sweep.add_items(c, sweep_id, ["X"])
                c.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"Concurrent errors: {errors}"

        items = sweep.list_items(primary, sweep_id)["items"]
        assert len(items) == 1, "Expected exactly one row for the racing key"
        assert items[0]["recurrence_count"] == N, (
            f"Expected recurrence_count={N}, got {items[0]['recurrence_count']}"
        )
        primary.close()


class TestListItems:
    """Smoke coverage for the new list_items helper."""

    def test_list_items_basic(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"], tags=["t1"])
        result = sweep.list_items(conn, sw["sweep_id"])
        assert len(result["items"]) == 2
        assert result["items"][0]["item"] == "a.py"
        assert result["items"][0]["state"] == "pending"

    def test_list_items_filter_by_state(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"])
        sweep.mark_items(conn, sw["sweep_id"], ["a.py"])
        done = sweep.list_items(conn, sw["sweep_id"], state="done")["items"]
        assert {i["item"] for i in done} == {"a.py"}

    def test_list_items_filter_by_tag(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py"], tags=["t1"])
        sweep.add_items(conn, sw["sweep_id"], ["b.py"], tags=["t2"])
        result = sweep.list_items(conn, sw["sweep_id"], tag="t1")
        assert {i["item"] for i in result["items"]} == {"a.py"}

    def test_list_items_archived_only(self, conn):
        sw = sweep.create_sweep(conn)
        sweep.add_items(conn, sw["sweep_id"], ["a.py", "b.py"])
        sweep.archive_items(conn, sw["sweep_id"], items=["a.py"])
        result = sweep.list_items(conn, sw["sweep_id"], archived_only=True)
        assert {i["item"] for i in result["items"]} == {"a.py"}
