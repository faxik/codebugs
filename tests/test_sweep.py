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
        assert result["duplicates_skipped"] == 1

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
