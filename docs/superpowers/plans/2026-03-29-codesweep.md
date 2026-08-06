# Codesweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a batch-iteration utility (codesweep) to the codebugs MCP server that creates ordered lists of arbitrary items and iterates through them in batches without double-passes or misses.

**Architecture:** New `sweep.py` module following the same pattern as `reqs.py` — SCHEMA constant, `ensure_schema()`, pure functions taking `conn` as first arg returning dicts. Integrated into the existing server via `register_sweep_tools()`, the CLI via `_register_sweep_subcommands()`, and the DB via `ensure_schema()` in `db.connect()`. Two SQLite tables: `codesweep_sweeps` (metadata) and `codesweep_items` (items with processed flag and tags as JSON).

**Tech Stack:** Python 3.11+, SQLite (json_each for tag filtering), FastMCP, pytest

**Spec:** `docs/superpowers/specs/2026-03-29-codesweep-design.md`

---

## File Structure

- **Create:** `src/codebugs/sweep.py` — all sweep business logic (schema, create, add, next, mark, status, archive, list)
- **Create:** `tests/test_sweep.py` — unit tests with in-memory SQLite
- **Modify:** `src/codebugs/db.py:96-99` — add `sweep.ensure_schema(conn)` call in `connect()`
- **Modify:** `src/codebugs/server.py:10,503-524` — import, register_sweep_tools, mode selection
- **Modify:** `src/codebugs/cli.py:8,603-628` — import, sweep subcommands, mode selection

---

### Task 1: Schema and create_sweep

**Files:**
- Create: `src/codebugs/sweep.py`
- Create: `tests/test_sweep.py`

- [ ] **Step 1: Write test file with fixtures and create_sweep tests**

```python
"""Tests for the sweep batch-iteration module."""

from __future__ import annotations

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codebugs.sweep'`

- [ ] **Step 3: Write sweep.py with schema and create_sweep**

```python
"""Database layer — sweep batch-iteration for codebugs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


SCHEMA = """\
CREATE TABLE IF NOT EXISTS codesweep_sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id TEXT UNIQUE NOT NULL,
    name TEXT,
    description TEXT NOT NULL DEFAULT '',
    default_batch_size INTEGER NOT NULL DEFAULT 10,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_codesweep_sweeps_name
    ON codesweep_sweeps(name) WHERE name IS NOT NULL;

CREATE TABLE IF NOT EXISTS codesweep_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id TEXT NOT NULL REFERENCES codesweep_sweeps(sweep_id),
    item TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    processed INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE(sweep_id, item)
);

CREATE INDEX IF NOT EXISTS idx_codesweep_items_next
    ON codesweep_items(sweep_id, processed, position);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the codesweep tables if they don't exist."""
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def _resolve_sweep(conn: sqlite3.Connection, ref: str) -> str:
    """Resolve a sweep reference (SW-N or name) to a sweep_id.

    Raises ValueError if not found.
    """
    row = conn.execute(
        "SELECT sweep_id FROM codesweep_sweeps WHERE sweep_id = ? OR name = ?",
        (ref, ref),
    ).fetchone()
    if not row:
        raise ValueError(f"Sweep not found: {ref}")
    return row["sweep_id"]


def create_sweep(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    description: str = "",
    default_batch_size: int = 10,
) -> dict[str, Any]:
    """Create a new sweep. Returns the created sweep as a dict."""
    if default_batch_size < 1:
        raise ValueError("Batch size must be at least 1")

    if name is not None:
        existing = conn.execute(
            "SELECT 1 FROM codesweep_sweeps WHERE name = ?", (name,),
        ).fetchone()
        if existing:
            raise ValueError(f"Sweep name already exists: {name}")

    now = _now()
    cursor = conn.execute(
        """INSERT INTO codesweep_sweeps
           (sweep_id, name, description, default_batch_size, status, created_at, updated_at)
           VALUES ('_placeholder', ?, ?, ?, 'active', ?, ?)""",
        (name, description, default_batch_size, now, now),
    )
    sweep_id = f"SW-{cursor.lastrowid}"
    conn.execute(
        "UPDATE codesweep_sweeps SET sweep_id = ? WHERE id = ?",
        (sweep_id, cursor.lastrowid),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM codesweep_sweeps WHERE id = ?", (cursor.lastrowid,),
    ).fetchone()
    return _sweep_to_dict(row)


def _sweep_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sweep row to a dict."""
    return {
        "sweep_id": row["sweep_id"],
        "name": row["name"],
        "description": row["description"],
        "default_batch_size": row["default_batch_size"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/sweep.py tests/test_sweep.py
git commit -m "feat(codesweep): add schema and create_sweep"
```

---

### Task 2: add_items

**Files:**
- Modify: `src/codebugs/sweep.py`
- Modify: `tests/test_sweep.py`

- [ ] **Step 1: Write add_items tests**

Append to `tests/test_sweep.py`:

```python
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
```

Add `import json` to the test file imports if not already present.

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py::TestAddItems -v`
Expected: FAIL — `AttributeError: module 'codebugs.sweep' has no attribute 'add_items'`

- [ ] **Step 3: Implement add_items in sweep.py**

Append to `src/codebugs/sweep.py`:

```python
def _next_position(conn: sqlite3.Connection, sweep_id: str) -> int:
    """Return the next insertion position for a sweep."""
    row = conn.execute(
        "SELECT MAX(position) as max_pos FROM codesweep_items WHERE sweep_id = ?",
        (sweep_id,),
    ).fetchone()
    return (row["max_pos"] + 1) if row["max_pos"] is not None else 0


def add_items(
    conn: sqlite3.Connection,
    sweep_ref: str,
    items: list[str],
    *,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Add items to a sweep. Duplicates are silently skipped."""
    sweep_id = _resolve_sweep(conn, sweep_ref)

    status = conn.execute(
        "SELECT status FROM codesweep_sweeps WHERE sweep_id = ?", (sweep_id,),
    ).fetchone()["status"]
    if status == "archived":
        raise ValueError(f"Cannot add items to archived sweep: {sweep_id}")

    now = _now()
    tags_json = json.dumps(tags or [])
    pos = _next_position(conn, sweep_id)
    added = 0
    duplicates = 0

    for item in items:
        try:
            conn.execute(
                """INSERT INTO codesweep_items
                   (sweep_id, item, tags, processed, position, created_at)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (sweep_id, item, tags_json, pos, now),
            )
            pos += 1
            added += 1
        except sqlite3.IntegrityError:
            duplicates += 1

    conn.execute(
        "UPDATE codesweep_sweeps SET updated_at = ? WHERE sweep_id = ?",
        (_now(), sweep_id),
    )
    conn.commit()
    return {"sweep_id": sweep_id, "added": added, "duplicates_skipped": duplicates}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/sweep.py tests/test_sweep.py
git commit -m "feat(codesweep): add add_items with dedup and tag support"
```

---

### Task 3: next_batch

**Files:**
- Modify: `src/codebugs/sweep.py`
- Modify: `tests/test_sweep.py`

- [ ] **Step 1: Write next_batch tests**

Append to `tests/test_sweep.py`:

```python
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
        # Add tagged items to a fresh sweep
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
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py::TestNextBatch -v`
Expected: FAIL — `AttributeError: module 'codebugs.sweep' has no attribute 'next_batch'` (also `mark_items` not yet defined, but `test_returns_default_batch_size` will fail first)

- [ ] **Step 3: Implement next_batch in sweep.py**

Append to `src/codebugs/sweep.py`:

```python
def next_batch(
    conn: sqlite3.Connection,
    sweep_ref: str,
    *,
    limit: int | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return the next batch of unprocessed items in insertion order."""
    sweep_id = _resolve_sweep(conn, sweep_ref)

    row = conn.execute(
        "SELECT default_batch_size FROM codesweep_sweeps WHERE sweep_id = ?",
        (sweep_id,),
    ).fetchone()
    batch_size = limit if limit is not None else row["default_batch_size"]

    conditions = ["sweep_id = ?", "processed = 0"]
    params: list[Any] = [sweep_id]

    if tags:
        tag_conditions = []
        for tag in tags:
            tag_conditions.append(
                "EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)"
            )
            params.append(tag)
        conditions.append(f"({' OR '.join(tag_conditions)})")

    where = f"WHERE {' AND '.join(conditions)}"

    rows = conn.execute(
        f"SELECT item, tags, position FROM codesweep_items {where} ORDER BY position LIMIT ?",
        params + [batch_size],
    ).fetchall()

    items = [
        {"item": r["item"], "tags": json.loads(r["tags"]), "position": r["position"]}
        for r in rows
    ]

    # Count total remaining unprocessed (with same tag filter) minus what we just returned
    total_unprocessed = conn.execute(
        f"SELECT COUNT(*) as c FROM codesweep_items {where}",
        params,
    ).fetchone()["c"]
    remaining = total_unprocessed - len(items)

    return {"sweep_id": sweep_id, "items": items, "remaining": remaining}
```

- [ ] **Step 4: Run tests to verify they pass** (some tests depend on mark_items which is Task 4 — only run tests that don't need it)

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py::TestNextBatch::test_returns_default_batch_size tests/test_sweep.py::TestNextBatch::test_override_limit tests/test_sweep.py::TestNextBatch::test_remaining_count_correct tests/test_sweep.py::TestNextBatch::test_tag_filtering tests/test_sweep.py::TestNextBatch::test_tag_filtering_any_match tests/test_sweep.py::TestNextBatch::test_items_include_position_and_tags tests/test_sweep.py::TestNextBatch::test_by_name -v`
Expected: PASS (7 tests). Tests using `mark_items` will be verified after Task 4.

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/sweep.py tests/test_sweep.py
git commit -m "feat(codesweep): add next_batch with tag filtering"
```

---

### Task 4: mark_items

**Files:**
- Modify: `src/codebugs/sweep.py`
- Modify: `tests/test_sweep.py`

- [ ] **Step 1: Write mark_items tests**

Append to `tests/test_sweep.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py::TestMarkItems -v`
Expected: FAIL — `AttributeError: module 'codebugs.sweep' has no attribute 'mark_items'`

- [ ] **Step 3: Implement mark_items in sweep.py**

Append to `src/codebugs/sweep.py`:

```python
def mark_items(
    conn: sqlite3.Connection,
    sweep_ref: str,
    items: list[str],
    *,
    processed: bool = True,
) -> dict[str, Any]:
    """Mark items as processed or unprocessed."""
    sweep_id = _resolve_sweep(conn, sweep_ref)
    now = _now()
    updated = 0

    for item in items:
        row = conn.execute(
            "SELECT id FROM codesweep_items WHERE sweep_id = ? AND item = ?",
            (sweep_id, item),
        ).fetchone()
        if not row:
            raise KeyError(f"Item not found in sweep {sweep_id}: {item}")

        if processed:
            conn.execute(
                "UPDATE codesweep_items SET processed = 1, processed_at = ? WHERE id = ?",
                (now, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE codesweep_items SET processed = 0, processed_at = NULL WHERE id = ?",
                (row["id"],),
            )
        updated += 1

    conn.execute(
        "UPDATE codesweep_sweeps SET updated_at = ? WHERE sweep_id = ?",
        (_now(), sweep_id),
    )
    conn.commit()
    return {"sweep_id": sweep_id, "updated": updated}
```

- [ ] **Step 4: Run all tests including the ones from Task 3 that depend on mark_items**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py -v`
Expected: All tests PASS (including `TestNextBatch::test_skips_processed_items` and `test_empty_when_all_processed`)

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/sweep.py tests/test_sweep.py
git commit -m "feat(codesweep): add mark_items with processed/unprocessed toggle"
```

---

### Task 5: get_status

**Files:**
- Modify: `src/codebugs/sweep.py`
- Modify: `tests/test_sweep.py`

- [ ] **Step 1: Write get_status tests**

Append to `tests/test_sweep.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py::TestGetStatus -v`
Expected: FAIL — `AttributeError: module 'codebugs.sweep' has no attribute 'get_status'`

- [ ] **Step 3: Implement get_status in sweep.py**

Append to `src/codebugs/sweep.py`:

```python
def get_status(
    conn: sqlite3.Connection,
    sweep_ref: str,
) -> dict[str, Any]:
    """Return sweep overview with progress and per-tag breakdown."""
    sweep_id = _resolve_sweep(conn, sweep_ref)
    sw = conn.execute(
        "SELECT * FROM codesweep_sweeps WHERE sweep_id = ?", (sweep_id,),
    ).fetchone()

    total = conn.execute(
        "SELECT COUNT(*) as c FROM codesweep_items WHERE sweep_id = ?",
        (sweep_id,),
    ).fetchone()["c"]
    processed = conn.execute(
        "SELECT COUNT(*) as c FROM codesweep_items WHERE sweep_id = ? AND processed = 1",
        (sweep_id,),
    ).fetchone()["c"]

    # Per-tag breakdown
    tag_rows = conn.execute(
        """SELECT jt.value as tag,
                  COUNT(*) as total,
                  SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) as done
           FROM codesweep_items, json_each(tags) as jt
           WHERE sweep_id = ?
           GROUP BY jt.value""",
        (sweep_id,),
    ).fetchall()
    by_tag = {
        r["tag"]: {"total": r["total"], "processed": r["done"]}
        for r in tag_rows
    }

    return {
        "sweep_id": sweep_id,
        "name": sw["name"],
        "status": sw["status"],
        "default_batch_size": sw["default_batch_size"],
        "total": total,
        "processed": processed,
        "remaining": total - processed,
        "by_tag": by_tag,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/sweep.py tests/test_sweep.py
git commit -m "feat(codesweep): add get_status with per-tag breakdown"
```

---

### Task 6: archive_sweep and list_sweeps

**Files:**
- Modify: `src/codebugs/sweep.py`
- Modify: `tests/test_sweep.py`

- [ ] **Step 1: Write archive and list tests**

Append to `tests/test_sweep.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py::TestArchiveSweep tests/test_sweep.py::TestListSweeps -v`
Expected: FAIL — `AttributeError: module 'codebugs.sweep' has no attribute 'archive_sweep'`

- [ ] **Step 3: Implement archive_sweep and list_sweeps in sweep.py**

Append to `src/codebugs/sweep.py`:

```python
def archive_sweep(
    conn: sqlite3.Connection,
    sweep_ref: str,
) -> dict[str, Any]:
    """Archive a sweep."""
    sweep_id = _resolve_sweep(conn, sweep_ref)
    conn.execute(
        "UPDATE codesweep_sweeps SET status = 'archived', updated_at = ? WHERE sweep_id = ?",
        (_now(), sweep_id),
    )
    conn.commit()
    return {"sweep_id": sweep_id, "status": "archived"}


def list_sweeps(
    conn: sqlite3.Connection,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """List all sweeps with summary counts."""
    condition = "" if include_archived else "WHERE s.status = 'active'"
    rows = conn.execute(
        f"""SELECT s.sweep_id, s.name, s.status, s.default_batch_size,
                   COUNT(i.id) as total,
                   SUM(CASE WHEN i.processed = 1 THEN 1 ELSE 0 END) as processed
            FROM codesweep_sweeps s
            LEFT JOIN codesweep_items i ON s.sweep_id = i.sweep_id
            {condition}
            GROUP BY s.sweep_id
            ORDER BY s.id""",
    ).fetchall()
    sweeps = []
    for r in rows:
        total = r["total"] or 0
        processed = r["processed"] or 0
        sweeps.append({
            "sweep_id": r["sweep_id"],
            "name": r["name"],
            "status": r["status"],
            "total": total,
            "processed": processed,
            "remaining": total - processed,
        })
    return {"sweeps": sweeps}
```

- [ ] **Step 4: Run all tests**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/sweep.py tests/test_sweep.py
git commit -m "feat(codesweep): add archive_sweep and list_sweeps"
```

---

### Task 7: Integrate into db.connect()

**Files:**
- Modify: `src/codebugs/db.py:96-99`

- [ ] **Step 1: Add sweep.ensure_schema to db.connect()**

In `src/codebugs/db.py`, after line 99 (`merge.ensure_schema(conn)`), add:

```python
    from codebugs import sweep
    sweep.ensure_schema(conn)
```

- [ ] **Step 2: Run existing tests to verify nothing breaks**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/codebugs/db.py
git commit -m "feat(codesweep): register schema in db.connect()"
```

---

### Task 8: MCP server tools

**Files:**
- Modify: `src/codebugs/server.py`

- [ ] **Step 1: Add register_sweep_tools and wire into mode selection**

In `src/codebugs/server.py`, add the following function before `main()`:

```python
def register_sweep_tools(mcp: FastMCP) -> None:
    """Register sweep batch-iteration tools on the given MCP server."""

    @mcp.tool()
    def codesweep_create(
        name: str | None = None,
        description: str = "",
        default_batch_size: int = 10,
    ) -> dict[str, Any]:
        """Create a new sweep for batch iteration over items.

        Args:
            name: Optional human-readable name (must be unique)
            description: What this sweep is for
            default_batch_size: Default items per batch (default: 10)
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.create_sweep(
                conn, name=name, description=description,
                default_batch_size=default_batch_size,
            )

    @mcp.tool()
    def codesweep_add(
        sweep_ref: str,
        items: list[str],
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add items to a sweep. Duplicates are silently skipped.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            items: Item identifiers to add
            tags: Optional tags applied to all items in this batch
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.add_items(conn, sweep_ref, items, tags=tags)

    @mcp.tool()
    def codesweep_next(
        sweep_ref: str,
        limit: int | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get next batch of unprocessed items in insertion order.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            limit: Batch size (overrides sweep default)
            tags: Filter to items matching any of these tags
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.next_batch(conn, sweep_ref, limit=limit, tags=tags)

    @mcp.tool()
    def codesweep_mark(
        sweep_ref: str,
        items: list[str],
        processed: bool = True,
    ) -> dict[str, Any]:
        """Mark items as processed or unprocessed.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            items: Item identifiers to mark
            processed: True to mark processed (default), False to unmark
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.mark_items(conn, sweep_ref, items, processed=processed)

    @mcp.tool()
    def codesweep_status(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Sweep overview — total, processed, remaining counts, per-tag breakdown.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.get_status(conn, sweep_ref)

    @mcp.tool()
    def codesweep_archive(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Archive a sweep. Archived sweeps are excluded from codesweep_list by default.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.archive_sweep(conn, sweep_ref)

    @mcp.tool()
    def codesweep_list(
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List all sweeps with summary counts.

        Args:
            include_archived: Include archived sweeps (default: false)
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.list_sweeps(conn, include_archived=include_archived)
```

Then update `main()`:

- Change `--mode` choices to: `["findings", "reqs", "merge", "sweep", "all"]`
- Add to name mapping: `"sweep": "codesweep"`
- Add mode condition: `if args.mode in ("sweep", "all"): register_sweep_tools(server)`

- [ ] **Step 2: Verify the server module imports cleanly**

Run: `cd /home/faxik/w/codebugs && python -c "from codebugs.server import register_sweep_tools; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/codebugs/server.py
git commit -m "feat(codesweep): add 7 MCP tools — create, add, next, mark, status, archive, list"
```

---

### Task 9: CLI subcommands

**Files:**
- Modify: `src/codebugs/cli.py`

- [ ] **Step 1: Add sweep CLI commands and registration**

Add sweep command functions and `_register_sweep_subcommands` to `src/codebugs/cli.py`:

```python
# --- Sweep CLI commands ---


def cmd_sweep_create(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    result = sweep.create_sweep(
        conn, name=args.name, description=args.description or "",
        default_batch_size=args.batch_size or 10,
    )
    conn.close()
    print(f"Created: {result['sweep_id']}" + (f" ({result['name']})" if result["name"] else ""))


def cmd_sweep_add(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    result = sweep.add_items(conn, args.sweep, args.items, tags=tags)
    conn.close()
    print(f"Added {result['added']} items, {result['duplicates_skipped']} duplicates skipped.")


def cmd_sweep_next(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    result = sweep.next_batch(conn, args.sweep, limit=args.limit, tags=tags)
    conn.close()
    if not result["items"]:
        print("(no unprocessed items)")
        return
    data = [{"item": i["item"], "tags": ",".join(i["tags"])} for i in result["items"]]
    print(_format_table(data, ["item", "tags"], max_widths={"item": 60}))
    print(f"\n{result['remaining']} remaining.")


def cmd_sweep_mark(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    result = sweep.mark_items(conn, args.sweep, args.items, processed=not args.undo)
    conn.close()
    action = "Unmarked" if args.undo else "Marked"
    print(f"{action} {result['updated']} items.")


def cmd_sweep_status(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    s = sweep.get_status(conn, args.sweep)
    conn.close()
    print(f"Sweep: {s['sweep_id']}" + (f" ({s['name']})" if s["name"] else ""))
    print(f"Status: {s['status']}")
    print(f"Items:  {s['processed']}/{s['total']} processed, {s['remaining']} remaining")
    if s["by_tag"]:
        print("\nBy tag:")
        for tag, counts in sorted(s["by_tag"].items()):
            print(f"  {tag:20s}  {counts['processed']}/{counts['total']}")


def cmd_sweep_archive(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    result = sweep.archive_sweep(conn, args.sweep)
    conn.close()
    print(f"Archived: {result['sweep_id']}")


def cmd_sweep_list(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    result = sweep.list_sweeps(conn, include_archived=args.all)
    conn.close()
    if not result["sweeps"]:
        print("(no sweeps)")
        return
    data = [
        {
            "sweep_id": s["sweep_id"],
            "name": s["name"] or "",
            "status": s["status"],
            "progress": f"{s['processed']}/{s['total']}",
            "remaining": str(s["remaining"]),
        }
        for s in result["sweeps"]
    ]
    print(_format_table(data, ["sweep_id", "name", "status", "progress", "remaining"]))


def _register_sweep_subcommands(sub, commands):
    """Register sweep CLI subcommands."""
    p = sub.add_parser("sweep-create", help="Create a new sweep")
    p.add_argument("--name", help="Optional sweep name")
    p.add_argument("--description", help="Sweep description")
    p.add_argument("--batch-size", type=int, help="Default batch size (default: 10)")

    p = sub.add_parser("sweep-add", help="Add items to a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to add")
    p.add_argument("--tags", help="Comma-separated tags")

    p = sub.add_parser("sweep-next", help="Get next batch of unprocessed items")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("--limit", type=int, help="Batch size override")
    p.add_argument("--tags", help="Filter by tags (comma-separated)")

    p = sub.add_parser("sweep-mark", help="Mark items as processed")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to mark")
    p.add_argument("--undo", action="store_true", help="Unmark items instead")

    p = sub.add_parser("sweep-status", help="Sweep progress overview")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-archive", help="Archive a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-list", help="List sweeps")
    p.add_argument("--all", action="store_true", help="Include archived sweeps")

    commands.update({
        "sweep-create": cmd_sweep_create,
        "sweep-add": cmd_sweep_add,
        "sweep-next": cmd_sweep_next,
        "sweep-mark": cmd_sweep_mark,
        "sweep-status": cmd_sweep_status,
        "sweep-archive": cmd_sweep_archive,
        "sweep-list": cmd_sweep_list,
    })
```

Then update `main()`:

- Add `"sweep"` to `--mode` choices in both `pre_parser` and `parser`
- Add `if pre_args.mode in ("sweep", "all"): _register_sweep_subcommands(sub, commands)` after the existing merge registration

- [ ] **Step 2: Verify CLI loads cleanly**

Run: `cd /home/faxik/w/codebugs && python -m codebugs.cli sweep-list --help`
Expected: Shows help for `sweep-list` subcommand

- [ ] **Step 3: Commit**

```bash
git add src/codebugs/cli.py
git commit -m "feat(codesweep): add CLI subcommands — sweep-create, sweep-add, sweep-next, sweep-mark, sweep-status, sweep-archive, sweep-list"
```

---

### Task 10: Full integration test

**Files:**
- Modify: `tests/test_sweep.py`

- [ ] **Step 1: Write end-to-end workflow test**

Append to `tests/test_sweep.py`:

```python
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
```

- [ ] **Step 2: Run all tests**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_sweep.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run the full test suite to verify no regressions**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/ -v`
Expected: All tests across all modules PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_sweep.py
git commit -m "test(codesweep): add full lifecycle integration tests"
```
