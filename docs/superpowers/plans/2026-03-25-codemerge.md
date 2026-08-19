# Codemerge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `codemerge` module to the codebugs MCP server that coordinates parallel worktree sessions merging to main, using a CAS (Compare-and-Swap) protocol to guarantee no git race conditions.

**Architecture:** New `merge.py` DB layer (same pattern as `reqs.py`) with session/claim/lock tables in the existing SQLite DB. State machine enforces valid transitions (`active → merging → done`). CAS on `expected_main_head` closes the TOCTOU gap between conflict-check and merge. `BEGIN IMMEDIATE` transaction in `merge()` provides SQLite-level write lock to prevent concurrent lock acquisition. 5 MCP tools for agent-facing operations (start, check, claim, merge, finish); remaining operations via Python API + CLI subcommands.

> **Amended 2026-08-19 (CB-106): the surface is SIX tools — `codemerge_abandon` was added.**
> The sentence above is kept verbatim because it is what was built, and the amendment is
> the point. Its premise — that abandon is not "agent-facing" — was falsified by using the
> tools: an MCP client reaches `codemerge_merge`, gets a legitimate `main_moved` refusal
> once the branch has landed by another route, and is then stranded in `active`, which
> `codemerge_finish` refuses in both directions of `success`. With abandon routed to the
> CLI, such a client had no exit at all, and `check_overlaps` matches
> `status IN ('active','merging')` with no expiry and no reaper — so its file claims were
> reported as conflicts to every later session forever. Note this RESTORES the original
> design: `docs/2026-03-25-codemerge-design.md:157` specified `codemerge_abandon` as an MCP
> tool and its line 493 counted abandon among the tools; this plan is where it was dropped.
>
> What was deliberately NOT changed, against CB-106's own suggestion: `finish(success=False)`
> still reverts to `active`. `codemerge_finish`'s docstring below — "success: True = merged
> successfully (done), False = failed (revert to active)" — is the ratified semantics and
> the code follows it. It is cited by its text rather than by a line number because this
> very amendment shifts the lines beneath it. What had drifted was the docstring in
> `merge.py`, which promised
> `status→abandoned` and now matches. "Release the lock, I will retry" and "this session is
> over" are different intents, and keeping them different is what makes `codemerge_abandon`
> the answer instead of overloading `success`.

**Tech Stack:** Python 3.11+, SQLite (existing DB), FastMCP, pytest

**Spec:** `docs/2026-03-25-codemerge-design.md` (original design) + race-condition analysis from brainstorming session.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/codebugs/merge.py` | **New.** DB layer — schema, session lifecycle, claims, conflict detection, lock with CAS. Pure SQLite, no git. |
| `tests/test_merge.py` | **New.** Unit tests for merge module. |
| `src/codebugs/db.py` | **Modify.** Call `merge.ensure_schema(conn)` in `connect()`, same pattern as `reqs.ensure_schema`. |
| `src/codebugs/server.py` | **Modify.** Add `register_merge_tools(mcp)`, extend `--mode` flag. |
| `src/codebugs/cli.py` | **Modify.** Add `_register_merge_subcommands()`, extend `--mode` flag. |

---

## Task 1: Schema and ensure_schema

**Files:**
- Create: `src/codebugs/merge.py`
- Create: `tests/test_merge.py`

- [ ] **Step 1: Write test for schema creation**

```python
# tests/test_merge.py
"""Tests for the codemerge coordination module."""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import merge


@pytest.fixture
def conn():
    """In-memory database with merge schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    merge.ensure_schema(c)
    yield c
    c.close()


class TestSchema:
    def test_tables_created(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "codemerge_sessions" in tables
        assert "codemerge_claims" in tables
        assert "codemerge_locks" in tables

    def test_lock_singleton_initialized(self, conn):
        row = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert row is not None
        assert row["session_id"] is None

    def test_ensure_schema_idempotent(self, conn):
        merge.ensure_schema(conn)  # second call should not raise
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "codemerge_sessions" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codebugs.merge'`

- [ ] **Step 3: Write merge.py with schema and ensure_schema**

```python
# src/codebugs/merge.py
"""Database layer — coordinated parallel session merging for codebugs."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any


MERGE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS codemerge_sessions (
    session_id   TEXT PRIMARY KEY,
    branch       TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    repo_root    TEXT NOT NULL DEFAULT '',
    base_commit  TEXT NOT NULL DEFAULT '',
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_activity TEXT NOT NULL DEFAULT (datetime('now')),
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'merging', 'done', 'abandoned')),
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS codemerge_claims (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES codemerge_sessions(session_id),
    file_path    TEXT NOT NULL,
    claimed_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, file_path)
);

CREATE TABLE IF NOT EXISTS codemerge_locks (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    session_id   TEXT REFERENCES codemerge_sessions(session_id),
    acquired_at  TEXT,
    expires_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_codemerge_claims_file ON codemerge_claims(file_path);
CREATE INDEX IF NOT EXISTS idx_codemerge_claims_session ON codemerge_claims(session_id);
CREATE INDEX IF NOT EXISTS idx_codemerge_sessions_status ON codemerge_sessions(status);
"""

VALID_STATUSES = ("active", "merging", "done", "abandoned")
LOCK_TTL_SECONDS = 300  # 5 minutes


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the codemerge tables if they don't exist."""
    for stmt in MERGE_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    # Initialize singleton lock row
    conn.execute(
        "INSERT OR IGNORE INTO codemerge_locks (id, session_id, acquired_at, expires_at) "
        "VALUES (1, NULL, NULL, NULL)"
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/merge.py tests/test_merge.py
git commit -m "feat(codemerge): add schema and ensure_schema"
```

---

## Task 2: start_session and end_session

**Files:**
- Modify: `src/codebugs/merge.py`
- Modify: `tests/test_merge.py`

- [ ] **Step 1: Write tests for session lifecycle**

```python
class TestStartSession:
    def test_start_basic(self, conn):
        result = merge.start_session(
            conn, session_id="feat-sidebar",
            branch="feature/sidebar", description="Add sidebar nav",
        )
        assert result["session_id"] == "feat-sidebar"
        assert result["branch"] == "feature/sidebar"
        assert result["status"] == "active"
        assert "started_at" in result

    def test_start_with_base_commit(self, conn):
        result = merge.start_session(
            conn, session_id="feat-x", branch="feature/x",
            description="desc", base_commit="abc123", repo_root="/repo",
        )
        assert result["base_commit"] == "abc123"
        assert result["repo_root"] == "/repo"

    def test_start_duplicate_raises(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        with pytest.raises(sqlite3.IntegrityError):
            merge.start_session(conn, session_id="s1", branch="b2", description="d2")

    def test_start_reactivate_abandoned(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.abandon_session(conn, "s1")
        result = merge.start_session(
            conn, session_id="s1", branch="b1", description="d1 retry",
            allow_restart=True,
        )
        assert result["status"] == "active"
        assert result["description"] == "d1 retry"


def _force_merging(conn, session_id):
    """Test helper: set session to 'merging' state and hold lock via direct SQL.
    This avoids depending on merge() which is implemented in Task 4."""
    now = merge._now()
    conn.execute(
        "UPDATE codemerge_sessions SET status='merging', last_activity=? WHERE session_id=?",
        (now, session_id),
    )
    conn.execute(
        "UPDATE codemerge_locks SET session_id=?, acquired_at=?, expires_at=? WHERE id=1",
        (session_id, now, "2099-01-01T00:00:00Z"),
    )
    conn.commit()


class TestFinishSession:
    def test_finish_success(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        result = merge.finish(conn, "s1", success=True)
        assert result["status"] == "done"
        assert result["finished_at"] is not None

    def test_finish_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.finish(conn, "nonexistent", success=True)

    def test_finish_not_merging_raises(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        with pytest.raises(ValueError, match="not in 'merging' state"):
            merge.finish(conn, "s1", success=True)

    def test_finish_failure_reverts_to_active(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        result = merge.finish(conn, "s1", success=False)
        assert result["status"] == "active"
        # Lock should be released
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert lock["session_id"] is None

    def test_finish_releases_lock(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        merge.finish(conn, "s1", success=True)
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert lock["session_id"] is None
        assert lock["acquired_at"] is None


class TestAbandonSession:
    def test_abandon_active(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.abandon_session(conn, "s1")
        assert result["status"] == "abandoned"
        assert result["finished_at"] is not None

    def test_abandon_merging_releases_lock(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        result = merge.abandon_session(conn, "s1")
        assert result["status"] == "abandoned"
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert lock["session_id"] is None

    def test_abandon_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.abandon_session(conn, "nonexistent")

    def test_abandon_already_abandoned_is_idempotent(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.abandon_session(conn, "s1")
        result = merge.abandon_session(conn, "s1")
        assert result["status"] == "abandoned"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py::TestStartSession -v`
Expected: FAIL — `AttributeError: module 'codebugs.merge' has no attribute 'start_session'`

- [ ] **Step 3: Implement start_session, finish, abandon_session**

Add to `merge.py`:

```python
def start_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    branch: str,
    description: str = "",
    base_commit: str = "",
    repo_root: str = "",
    allow_restart: bool = False,
) -> dict[str, Any]:
    """Register a new working session."""
    now = _now()
    if allow_restart:
        existing = conn.execute(
            "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing and existing["status"] in ("abandoned", "done"):
            conn.execute(
                """UPDATE codemerge_sessions
                   SET branch=?, description=?, base_commit=?, repo_root=?,
                       started_at=?, last_activity=?, status='active', finished_at=NULL
                   WHERE session_id=?""",
                (branch, description, base_commit, repo_root, now, now, session_id),
            )
            # Clear old claims
            conn.execute("DELETE FROM codemerge_claims WHERE session_id = ?", (session_id,))
            conn.commit()
            row = conn.execute(
                "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row)

    conn.execute(
        """INSERT INTO codemerge_sessions
           (session_id, branch, description, base_commit, repo_root, started_at, last_activity)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, branch, description, base_commit, repo_root, now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return dict(row)


def abandon_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """Mark a session as abandoned, releasing claims and lock."""
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Session not found: {session_id}")

    now = _now()
    conn.execute(
        "UPDATE codemerge_sessions SET status='abandoned', finished_at=?, last_activity=? "
        "WHERE session_id=?",
        (now, now, session_id),
    )
    # Release lock if held
    conn.execute(
        "UPDATE codemerge_locks SET session_id=NULL, acquired_at=NULL, expires_at=NULL "
        "WHERE session_id=?",
        (session_id,),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone())


def finish(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    success: bool,
) -> dict[str, Any]:
    """Release lock and mark session done (success) or revert to active (failure)."""
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Session not found: {session_id}")
    if row["status"] != "merging":
        raise ValueError(f"Session '{session_id}' is not in 'merging' state (is '{row['status']}')")

    now = _now()
    new_status = "done" if success else "active"
    finished_at = now if success else None

    conn.execute(
        "UPDATE codemerge_sessions SET status=?, finished_at=?, last_activity=? "
        "WHERE session_id=?",
        (new_status, finished_at, now, session_id),
    )
    # Release lock
    conn.execute(
        "UPDATE codemerge_locks SET session_id=NULL, acquired_at=NULL, expires_at=NULL "
        "WHERE id=1 AND session_id=?",
        (session_id,),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py -v`
Expected: All tests PASS (tests use `_force_merging()` helper — no dependency on `merge()` from Task 4)

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/merge.py tests/test_merge.py
git commit -m "feat(codemerge): add session lifecycle — start, finish, abandon"
```

---

## Task 3: Claims (add_claim, get_claims)

**Files:**
- Modify: `src/codebugs/merge.py`
- Modify: `tests/test_merge.py`

- [ ] **Step 1: Write tests for claims**

```python
class TestClaims:
    def test_add_claim(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.add_claim(conn, "s1", "src/foo.py")
        assert result["file_path"] == "src/foo.py"
        assert result["session_id"] == "s1"

    def test_add_claim_idempotent(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.add_claim(conn, "s1", "src/foo.py")  # no error
        claims = merge.get_claims(conn, "s1")
        assert len(claims) == 1

    def test_add_claim_unknown_session_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.add_claim(conn, "nonexistent", "src/foo.py")

    def test_add_claim_done_session_raises(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        merge.finish(conn, "s1", success=True)
        with pytest.raises(ValueError, match="not active"):
            merge.add_claim(conn, "s1", "src/foo.py")

    def test_get_claims_empty(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        assert merge.get_claims(conn, "s1") == []

    def test_get_claims_multiple(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.add_claim(conn, "s1", "src/bar.py")
        claims = merge.get_claims(conn, "s1")
        paths = {c["file_path"] for c in claims}
        assert paths == {"src/foo.py", "src/bar.py"}

    def test_claims_updates_last_activity(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        before = conn.execute(
            "SELECT last_activity FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()[0]
        merge.add_claim(conn, "s1", "src/foo.py")
        after = conn.execute(
            "SELECT last_activity FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()[0]
        assert after >= before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py::TestClaims -v`
Expected: FAIL — `AttributeError: module 'codebugs.merge' has no attribute 'add_claim'`

- [ ] **Step 3: Implement add_claim and get_claims**

Add to `merge.py`:

```python
def add_claim(
    conn: sqlite3.Connection,
    session_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Record that a session has modified a file. Idempotent."""
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Session not found: {session_id}")
    if row["status"] not in ("active", "merging"):
        raise ValueError(f"Session '{session_id}' is not active (is '{row['status']}')")

    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO codemerge_claims (session_id, file_path, claimed_at) "
        "VALUES (?, ?, ?)",
        (session_id, file_path, now),
    )
    conn.execute(
        "UPDATE codemerge_sessions SET last_activity=? WHERE session_id=?",
        (now, session_id),
    )
    conn.commit()
    return {"session_id": session_id, "file_path": file_path, "claimed_at": now}


def get_claims(
    conn: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, Any]]:
    """List all claimed files for a session."""
    rows = conn.execute(
        "SELECT session_id, file_path, claimed_at FROM codemerge_claims "
        "WHERE session_id = ? ORDER BY claimed_at",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py -v`
Expected: All claim tests PASS. Some lifecycle tests that depend on `merge()` may still fail — that's next.

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/merge.py tests/test_merge.py
git commit -m "feat(codemerge): add file claim tracking"
```

---

## Task 4: merge() with CAS and lock — the critical path

This is the core of the race-condition prevention. `merge()` atomically: validates session state, acquires the singleton lock, and verifies the CAS on `expected_main_head`.

**Files:**
- Modify: `src/codebugs/merge.py`
- Modify: `tests/test_merge.py`

- [ ] **Step 1: Write tests for merge + CAS**

```python
class TestMerge:
    def _head_fn(self, sha="abc123"):
        """Return a callable that returns a fixed main HEAD."""
        return lambda: sha

    def test_merge_clean(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is True
        # Session should be in merging state
        row = conn.execute(
            "SELECT status FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()
        assert row["status"] == "merging"
        # Lock should be held
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()
        assert lock["session_id"] == "s1"

    def test_merge_cas_rejects_stale_head(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("def456"),
        )
        assert result["proceed"] is False
        assert result["reason"] == "main_moved"
        assert result["current_head"] == "def456"
        # Session should still be active
        row = conn.execute(
            "SELECT status FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()
        assert row["status"] == "active"

    def test_merge_lock_held_rejects(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        result = merge.merge(
            conn, "s2", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is False
        assert result["reason"] == "lock_held"
        assert result["holder"] == "s1"

    def test_merge_expired_lock_reclaimed(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        # Simulate s1 acquiring lock then crashing (set expires_at in the past)
        merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        conn.execute(
            "UPDATE codemerge_locks SET expires_at='2000-01-01T00:00:00Z' WHERE id=1"
        )
        conn.commit()
        # s2 should be able to reclaim
        result = merge.merge(
            conn, "s2", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is True

    def test_merge_idempotent_if_already_merging(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        # Calling again for same session should be idempotent
        result = merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is True

    def test_merge_unknown_session_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.merge(
                conn, "nope", expected_main_head="abc",
                current_main_head_fn=self._head_fn("abc"),
            )

    def test_merge_done_session_rejects(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.merge(
            conn, "s1", expected_main_head="abc",
            current_main_head_fn=self._head_fn("abc"),
        )
        merge.finish(conn, "s1", success=True)
        with pytest.raises(ValueError, match="not in 'active' state"):
            merge.merge(
                conn, "s1", expected_main_head="abc",
                current_main_head_fn=self._head_fn("abc"),
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py::TestMerge -v`
Expected: FAIL — `AttributeError: module 'codebugs.merge' has no attribute 'merge'`

- [ ] **Step 3: Implement merge()**

Add to `merge.py` (note: `Callable`, `timedelta` already imported in Task 1):

```python
def merge(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    expected_main_head: str,
    current_main_head_fn: Callable[[], str],
) -> dict[str, Any]:
    """Acquire merge lock with CAS verification.

    Uses BEGIN IMMEDIATE to acquire a SQLite write lock at transaction
    start, preventing two concurrent processes from both reading the
    singleton lock as free. This is the critical mutual exclusion point.

    Args:
        session_id: The session requesting merge.
        expected_main_head: The main HEAD SHA the caller last checked against.
        current_main_head_fn: Callable returning current main HEAD SHA.
            Injected so core logic stays git-free and testable.

    Returns:
        {proceed: True} or {proceed: False, reason: "...", ...}
    """
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Session not found: {session_id}")

    # Idempotent: already merging with lock held
    if row["status"] == "merging":
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()
        if lock and lock["session_id"] == session_id:
            return {"proceed": True, "session_id": session_id}

    if row["status"] != "active":
        raise ValueError(
            f"Session '{session_id}' is not in 'active' state (is '{row['status']}')"
        )

    now = _now()
    now_dt = datetime.now(timezone.utc)
    expires = (now_dt + timedelta(seconds=LOCK_TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # BEGIN IMMEDIATE acquires a RESERVED lock on the database file,
    # blocking other IMMEDIATE/EXCLUSIVE transactions from starting.
    # This prevents the race where two processes both read the lock as free.
    conn.execute("BEGIN IMMEDIATE")
    try:
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()

        if lock["session_id"] is not None:
            # Check if lock is expired (ISO 8601 string comparison is safe
            # because format is fixed-width: YYYY-MM-DDTHH:MM:SSZ)
            if lock["expires_at"] and lock["expires_at"] > now:
                # Lock is held and not expired — rollback and report
                conn.execute("ROLLBACK")
                return {
                    "proceed": False,
                    "reason": "lock_held",
                    "holder": lock["session_id"],
                    "held_since": lock["acquired_at"],
                    "expires_at": lock["expires_at"],
                }
            # Lock expired — mark the holder's session as abandoned
            conn.execute(
                "UPDATE codemerge_sessions SET status='abandoned', last_activity=? "
                "WHERE session_id=? AND status='merging'",
                (now, lock["session_id"]),
            )

        # CAS check: verify main hasn't moved
        actual_head = current_main_head_fn()
        if actual_head != expected_main_head:
            conn.execute("ROLLBACK")
            return {
                "proceed": False,
                "reason": "main_moved",
                "expected_head": expected_main_head,
                "current_head": actual_head,
            }

        # Acquire lock + transition to merging
        conn.execute(
            "UPDATE codemerge_locks SET session_id=?, acquired_at=?, expires_at=? WHERE id=1",
            (session_id, now, expires),
        )
        conn.execute(
            "UPDATE codemerge_sessions SET status='merging', last_activity=? WHERE session_id=?",
            (now, session_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"proceed": True, "session_id": session_id}
```

> **Implementation note:** `BEGIN IMMEDIATE` + manual `COMMIT`/`ROLLBACK` means
> SQLite's default autocommit is bypassed for this function. All other functions
> in `merge.py` use the normal autocommit pattern (`conn.commit()`). This is
> intentional — only `merge()` needs the write lock at transaction start.

- [ ] **Step 4: Run ALL tests to verify they pass**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/merge.py tests/test_merge.py
git commit -m "feat(codemerge): add merge() with CAS lock — race-condition-free"
```

---

## Task 5: check_overlaps (conflict detection)

**Files:**
- Modify: `src/codebugs/merge.py`
- Modify: `tests/test_merge.py`

- [ ] **Step 1: Write tests for conflict detection**

```python
class TestCheckOverlaps:
    def test_no_overlaps(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.add_claim(conn, "s2", "src/bar.py")
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is True
        assert result["conflicts"] == []

    def test_parallel_session_overlap(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/shared.py")
        merge.add_claim(conn, "s2", "src/shared.py")
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is False
        assert len(result["conflicts"]) == 1
        conflict = result["conflicts"][0]
        assert conflict["file"] == "src/shared.py"
        assert conflict["blocking_session"] == "s2"
        assert conflict["type"] == "parallel_session"

    def test_main_diverged_overlap(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        result = merge.check_overlaps(
            conn, "s1", main_changed_files=["src/foo.py", "src/other.py"],
        )
        assert result["clean"] is False
        conflict = result["conflicts"][0]
        assert conflict["file"] == "src/foo.py"
        assert conflict["type"] == "main_diverged"

    def test_ignores_done_sessions(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/shared.py")
        merge.add_claim(conn, "s2", "src/shared.py")
        # Mark s2 as done
        merge.merge(conn, "s2", expected_main_head="abc", current_main_head_fn=lambda: "abc")
        merge.finish(conn, "s2", success=True)
        # s1 should not see s2 as a conflict
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is True

    def test_ignores_abandoned_sessions(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/shared.py")
        merge.add_claim(conn, "s2", "src/shared.py")
        merge.abandon_session(conn, "s2")
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is True

    def test_returns_main_head(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.check_overlaps(
            conn, "s1", current_main_head_fn=lambda: "abc123",
        )
        assert result["main_head"] == "abc123"

    def test_unknown_session_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.check_overlaps(conn, "nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py::TestCheckOverlaps -v`
Expected: FAIL — `AttributeError: module 'codebugs.merge' has no attribute 'check_overlaps'`

- [ ] **Step 3: Implement check_overlaps**

Add to `merge.py`:

```python
def check_overlaps(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    main_changed_files: list[str] | None = None,
    current_main_head_fn: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Advisory conflict check. Does not acquire any lock.

    Args:
        session_id: Session to check.
        main_changed_files: Files changed on main since this session branched.
            Caller computes via git diff. If None, skips main-divergence check.
        current_main_head_fn: Callable returning current main HEAD SHA.
            If None, main_head is omitted from result.

    Returns:
        {clean: bool, conflicts: [...], main_head: "...", recommendation: "clean"|"dirty"}
    """
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Session not found: {session_id}")

    my_claims = conn.execute(
        "SELECT file_path FROM codemerge_claims WHERE session_id = ?", (session_id,)
    ).fetchall()
    my_files = {r["file_path"] for r in my_claims}

    conflicts: list[dict[str, str]] = []

    # Check against other active/merging sessions
    others = conn.execute(
        "SELECT session_id, branch FROM codemerge_sessions "
        "WHERE session_id != ? AND status IN ('active', 'merging')",
        (session_id,),
    ).fetchall()

    for other in others:
        other_claims = conn.execute(
            "SELECT file_path FROM codemerge_claims WHERE session_id = ?",
            (other["session_id"],),
        ).fetchall()
        other_files = {r["file_path"] for r in other_claims}
        overlap = my_files & other_files
        for f in sorted(overlap):
            conflicts.append({
                "file": f,
                "blocking_session": other["session_id"],
                "blocking_branch": other["branch"],
                "type": "parallel_session",
            })

    # Check main divergence
    if main_changed_files is not None:
        main_overlap = my_files & set(main_changed_files)
        for f in sorted(main_overlap):
            conflicts.append({
                "file": f,
                "blocking_session": "main",
                "blocking_branch": "main",
                "type": "main_diverged",
            })

    result: dict[str, Any] = {
        "clean": len(conflicts) == 0,
        "conflicts": conflicts,
        "recommendation": "dirty" if conflicts else "clean",
    }

    if current_main_head_fn is not None:
        result["main_head"] = current_main_head_fn()

    return result
```

- [ ] **Step 4: Run ALL tests**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/merge.py tests/test_merge.py
git commit -m "feat(codemerge): add check_overlaps conflict detection"
```

---

## Task 6: Visibility — get_sessions, get_status

**Files:**
- Modify: `src/codebugs/merge.py`
- Modify: `tests/test_merge.py`

- [ ] **Step 1: Write tests for visibility functions**

```python
class TestVisibility:
    def test_get_sessions_all(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        result = merge.get_sessions(conn)
        assert len(result) == 2

    def test_get_sessions_filter_status(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.abandon_session(conn, "s2")
        result = merge.get_sessions(conn, status="active")
        assert len(result) == 1
        assert result[0]["session_id"] == "s1"

    def test_get_sessions_includes_claim_count(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.add_claim(conn, "s1", "src/bar.py")
        result = merge.get_sessions(conn)
        assert result[0]["claim_count"] == 2

    def test_get_status_empty(self, conn):
        result = merge.get_status(conn)
        assert result["active_sessions"] == 0
        assert result["total_claims"] == 0
        assert result["lock_holder"] is None

    def test_get_status_with_data(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.merge(conn, "s1", expected_main_head="abc", current_main_head_fn=lambda: "abc")
        result = merge.get_status(conn)
        assert result["active_sessions"] == 0  # s1 is now merging
        assert result["merging_sessions"] == 1
        assert result["total_claims"] == 1
        assert result["lock_holder"] == "s1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py::TestVisibility -v`
Expected: FAIL

- [ ] **Step 3: Implement get_sessions and get_status**

Add to `merge.py`:

```python
def get_sessions(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List sessions with claim counts."""
    conditions = []
    params: list[Any] = []
    if status:
        conditions.append("s.status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = conn.execute(
        f"""SELECT s.*, COUNT(c.id) as claim_count
            FROM codemerge_sessions s
            LEFT JOIN codemerge_claims c ON s.session_id = c.session_id
            {where}
            GROUP BY s.session_id
            ORDER BY s.started_at DESC""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Dashboard summary."""
    counts = {}
    for r in conn.execute(
        "SELECT status, COUNT(*) as c FROM codemerge_sessions GROUP BY status"
    ):
        counts[r["status"]] = r["c"]

    total_claims = conn.execute(
        "SELECT COUNT(*) as c FROM codemerge_claims cc "
        "JOIN codemerge_sessions cs ON cc.session_id = cs.session_id "
        "WHERE cs.status IN ('active', 'merging')"
    ).fetchone()["c"]

    lock = conn.execute("SELECT session_id FROM codemerge_locks WHERE id=1").fetchone()

    return {
        "active_sessions": counts.get("active", 0),
        "merging_sessions": counts.get("merging", 0),
        "done_sessions": counts.get("done", 0),
        "abandoned_sessions": counts.get("abandoned", 0),
        "total_claims": total_claims,
        "lock_holder": lock["session_id"] if lock else None,
    }
```

- [ ] **Step 4: Run ALL tests**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/merge.py tests/test_merge.py
git commit -m "feat(codemerge): add get_sessions and get_status visibility"
```

---

## Task 7: Wire into db.connect()

**Files:**
- Modify: `src/codebugs/db.py`

- [ ] **Step 1: Write test to verify merge schema is created via db.connect()**

Add to `tests/test_merge.py`:

```python
class TestIntegration:
    def test_db_connect_creates_merge_schema(self, tmp_path):
        """db.connect() should initialize merge tables too."""
        from codebugs import db
        c = db.connect(str(tmp_path))
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "codemerge_sessions" in tables
        assert "codemerge_claims" in tables
        assert "codemerge_locks" in tables
        c.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py::TestIntegration -v`
Expected: FAIL — tables don't exist because `db.connect()` doesn't call `merge.ensure_schema()` yet.

- [ ] **Step 3: Add merge.ensure_schema to db.connect()**

In `src/codebugs/db.py`, in the `connect()` function, after the line `reqs.ensure_schema(conn)`, add:

```python
    from codebugs import merge
    merge.ensure_schema(conn)
```

- [ ] **Step 4: Run ALL tests (db + merge + reqs)**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/db.py tests/test_merge.py
git commit -m "feat(codemerge): wire merge schema into db.connect()"
```

---

## Task 8: MCP server tools (5 agent-facing tools)

**Files:**
- Modify: `src/codebugs/server.py`

- [ ] **Step 1: Write register_merge_tools function**

Add to `server.py`, after `register_reqs_tools`. Helper function first, then tools:

```python
def _get_main_head() -> str:
    """Get current main branch HEAD SHA. Used by merge tools that need git."""
    import subprocess
    return subprocess.check_output(
        ["git", "rev-parse", "main"],
        text=True, timeout=10,
    ).strip()


def register_merge_tools(mcp: FastMCP) -> None:
    """Register codemerge coordination tools on the given MCP server."""

    @mcp.tool()
    def codemerge_start(
        session_id: str,
        branch: str,
        description: str = "",
        base_commit: str = "",
        repo_root: str = "",
        allow_restart: bool = False,
    ) -> dict[str, Any]:
        """Register a new working session for merge coordination.

        Call when creating a worktree or starting parallel work.

        Args:
            session_id: Unique slug (e.g. branch name with / replaced by -)
            branch: Git branch name
            description: What this session is doing
            base_commit: Commit SHA when session branched from main
            repo_root: Absolute path to repo root
            allow_restart: If True, reactivate an abandoned/done session
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.start_session(
                conn, session_id=session_id, branch=branch,
                description=description, base_commit=base_commit,
                repo_root=repo_root, allow_restart=allow_restart,
            )

    @mcp.tool()
    def codemerge_claim(
        session_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Register that a session has modified a file.

        Idempotent — claiming the same file twice is a no-op.
        Can be called by agents directly or by a PostToolUse hook.

        Args:
            session_id: Session that modified the file
            file_path: File path relative to repo root
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.add_claim(conn, session_id, file_path)

    @mcp.tool()
    def codemerge_check(
        session_id: str,
        main_changed_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check for file conflicts before merging. Advisory — does not acquire lock.

        Compares this session's claimed files against:
        1. Other active sessions' claims (parallel work)
        2. Files listed in main_changed_files (main has moved)

        Returns main_head in the response — pass it to codemerge_merge().

        Call this before codemerge_merge() to decide clean vs dirty path.

        Args:
            session_id: Session to check
            main_changed_files: Files changed on main since branching
                (compute via: git diff --name-only <base_commit> main)
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.check_overlaps(
                conn, session_id,
                main_changed_files=main_changed_files,
                current_main_head_fn=_get_main_head,
            )

    @mcp.tool()
    def codemerge_merge(
        session_id: str,
        expected_main_head: str,
    ) -> dict[str, Any]:
        """Acquire exclusive merge lock with CAS verification.

        This is the critical gate. Only one session can merge at a time.
        The expected_main_head must match the current main HEAD (CAS guard).
        Get it from codemerge_check()'s response or `git rev-parse main`.

        Returns {proceed: true} or {proceed: false, reason: "..."}.
        After receiving proceed=true, perform git merge/cherry-pick,
        then call codemerge_finish().

        Args:
            session_id: Session requesting merge
            expected_main_head: The main HEAD SHA you last verified against
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.merge(
                conn, session_id,
                expected_main_head=expected_main_head,
                current_main_head_fn=_get_main_head,
            )

    @mcp.tool()
    def codemerge_finish(
        session_id: str,
        success: bool = True,
    ) -> dict[str, Any]:
        """Release merge lock and mark session done or revert to active.

        Must be called after codemerge_merge() returned proceed=true.
        Call with success=false if git merge/cherry-pick failed.

        Args:
            session_id: Session that is finishing
            success: True = merged successfully (done), False = failed (revert to active)
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.finish(conn, session_id, success=success)
```

- [ ] **Step 2: Register merge tools in main() and extend mode flag**

Update the `main()` function in `server.py`:

```python
def main():
    """Run the MCP server with optional mode selection."""
    parser = argparse.ArgumentParser(description="Codebugs MCP server")
    parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "all"],
        default="all",
        help="Which tools to expose: findings, reqs, merge, or all (default: all)",
    )
    args = parser.parse_args()

    name = {
        "findings": "codebugs",
        "reqs": "codereqs",
        "merge": "codemerge",
        "all": "codebugs",
    }[args.mode]
    server = FastMCP(name, json_response=True)

    if args.mode in ("findings", "all"):
        register_findings_tools(server)
    if args.mode in ("reqs", "all"):
        register_reqs_tools(server)
    if args.mode in ("merge", "all"):
        register_merge_tools(server)

    server.run()
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/codebugs/server.py
git commit -m "feat(codemerge): add 5 MCP tools — start, claim, check, merge, finish"
```

---

## Task 9: CLI subcommands

**Files:**
- Modify: `src/codebugs/cli.py`

- [ ] **Step 1: Add merge subcommands**

Add to `cli.py`:

```python
# --- Merge CLI commands ---

def cmd_merge_sessions(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    sessions = merge.get_sessions(conn, status=args.status)
    conn.close()
    if not sessions:
        print("(no sessions)")
        return
    data = [
        {
            "session_id": s["session_id"],
            "branch": s["branch"],
            "status": s["status"],
            "claims": str(s["claim_count"]),
            "description": s["description"],
        }
        for s in sessions
    ]
    print(_format_table(
        data, ["session_id", "branch", "status", "claims", "description"],
        max_widths={"description": 40, "branch": 30},
    ))


def cmd_merge_status(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    s = merge.get_status(conn)
    conn.close()
    print("Codemerge Status")
    print("=" * 40)
    print(f"Active sessions:    {s['active_sessions']}")
    print(f"Merging sessions:   {s['merging_sessions']}")
    print(f"Done sessions:      {s['done_sessions']}")
    print(f"Abandoned sessions: {s['abandoned_sessions']}")
    print(f"Total claims:       {s['total_claims']}")
    print(f"Lock holder:        {s['lock_holder'] or '(none)'}")


def cmd_merge_abandon(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    try:
        result = merge.abandon_session(conn, args.session_id)
        print(f"Abandoned: {result['session_id']}")
    except KeyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_merge_claims(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    claims = merge.get_claims(conn, args.session_id)
    conn.close()
    if not claims:
        print("(no claims)")
        return
    data = [{"file": c["file_path"], "claimed_at": c["claimed_at"]} for c in claims]
    print(_format_table(data, ["file", "claimed_at"]))
```

- [ ] **Step 2: Add _register_merge_subcommands**

```python
def _register_merge_subcommands(sub, commands):
    """Register merge CLI subcommands."""
    p = sub.add_parser("merge-sessions", help="List merge sessions")
    p.add_argument("--status", help="Filter: active|merging|done|abandoned")

    sub.add_parser("merge-status", help="Merge coordination dashboard")

    p = sub.add_parser("merge-abandon", help="Abandon a stale session")
    p.add_argument("session_id", help="Session ID to abandon")

    p = sub.add_parser("merge-claims", help="List claimed files for a session")
    p.add_argument("session_id", help="Session ID")

    commands.update({
        "merge-sessions": cmd_merge_sessions,
        "merge-status": cmd_merge_status,
        "merge-abandon": cmd_merge_abandon,
        "merge-claims": cmd_merge_claims,
    })
```

- [ ] **Step 3: Update main() to register merge subcommands and extend --mode**

In `cli.py`, update the `main()` function:
- Add `"merge"` to mode choices
- Add `if pre_args.mode in ("merge", "all"): _register_merge_subcommands(sub, commands)`

- [ ] **Step 4: Smoke test the CLI**

Run: `cd /home/faxik/w/codebugs && python -m codebugs.cli merge-status`
Expected: Prints the dashboard with 0 sessions

Run: `cd /home/faxik/w/codebugs && python -m codebugs.cli merge-sessions`
Expected: Prints "(no sessions)"

- [ ] **Step 5: Run ALL tests**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/cli.py
git commit -m "feat(codemerge): add CLI subcommands — sessions, status, abandon, claims"
```

---

## Task 10: Full integration test — concurrent merge scenario

**Files:**
- Modify: `tests/test_merge.py`

- [ ] **Step 1: Write end-to-end scenario test**

```python
class TestConcurrentMergeScenario:
    """End-to-end test: two sessions racing to merge."""

    def test_second_session_blocked_by_cas(self, conn):
        """Simulates the race from the design doc:
        A checks, B merges first, A's CAS fails, A re-checks and retries."""
        merge.start_session(conn, session_id="A", branch="feature/a", description="A")
        merge.start_session(conn, session_id="B", branch="feature/b", description="B")
        merge.add_claim(conn, "A", "src/foo.py")
        merge.add_claim(conn, "B", "src/bar.py")

        # Both check — both see clean
        check_a = merge.check_overlaps(conn, "A", current_main_head_fn=lambda: "v1")
        check_b = merge.check_overlaps(conn, "B", current_main_head_fn=lambda: "v1")
        assert check_a["clean"] is True
        assert check_b["clean"] is True

        # B merges first
        result_b = merge.merge(
            conn, "B", expected_main_head="v1", current_main_head_fn=lambda: "v1",
        )
        assert result_b["proceed"] is True

        # A tries to merge — lock held by B
        result_a = merge.merge(
            conn, "A", expected_main_head="v1", current_main_head_fn=lambda: "v1",
        )
        assert result_a["proceed"] is False
        assert result_a["reason"] == "lock_held"

        # B finishes, main moves to v2
        merge.finish(conn, "B", success=True)

        # A retries with stale head — CAS rejects
        result_a2 = merge.merge(
            conn, "A", expected_main_head="v1", current_main_head_fn=lambda: "v2",
        )
        assert result_a2["proceed"] is False
        assert result_a2["reason"] == "main_moved"

        # A re-checks with updated main
        check_a2 = merge.check_overlaps(
            conn, "A", current_main_head_fn=lambda: "v2",
        )
        assert check_a2["main_head"] == "v2"

        # A merges with correct head
        result_a3 = merge.merge(
            conn, "A", expected_main_head="v2", current_main_head_fn=lambda: "v2",
        )
        assert result_a3["proceed"] is True
        merge.finish(conn, "A", success=True)

        # Both done
        sessions = merge.get_sessions(conn, status="done")
        assert len(sessions) == 2

    def test_overlapping_files_detected(self, conn):
        """Two sessions editing the same file — dirty path required."""
        merge.start_session(conn, session_id="A", branch="feature/a", description="A")
        merge.start_session(conn, session_id="B", branch="feature/b", description="B")
        merge.add_claim(conn, "A", "src/shared.py")
        merge.add_claim(conn, "B", "src/shared.py")

        check = merge.check_overlaps(conn, "A")
        assert check["clean"] is False
        assert check["recommendation"] == "dirty"
        assert check["conflicts"][0]["file"] == "src/shared.py"
        assert check["conflicts"][0]["blocking_session"] == "B"
```

- [ ] **Step 2: Run the scenario test**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/test_merge.py::TestConcurrentMergeScenario -v`
Expected: All tests PASS

- [ ] **Step 3: Run the full test suite one final time**

Run: `cd /home/faxik/w/codebugs && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_merge.py
git commit -m "test(codemerge): add concurrent merge scenario integration tests"
```

---

## Summary

| Task | What | Tests |
|------|------|-------|
| 1 | Schema + ensure_schema | 3 |
| 2 | start_session, finish, abandon | 12 |
| 3 | add_claim, get_claims | 7 |
| 4 | merge() with CAS + BEGIN IMMEDIATE | 7 |
| 5 | check_overlaps | 7 |
| 6 | get_sessions, get_status | 5 |
| 7 | Wire into db.connect() | 1 |
| 8 | MCP server tools (5 tools) | regression |
| 9 | CLI subcommands (4 commands) | smoke |
| 10 | Concurrent scenario e2e | 2 |
| **Total** | | **~44 tests** |

**Key design decisions:**
- `BEGIN IMMEDIATE` in `merge()` prevents concurrent SQLite writers from both reading the lock as free
- `Callable[[], str]` for git HEAD injection keeps core module git-free and testable
- ISO 8601 fixed-width format (`YYYY-MM-DDTHH:MM:SSZ`) makes string comparison safe for lock expiry
- `_force_merging()` test helper allows Task 2/3 tests to work without depending on Task 4's `merge()`

**Not in scope (by design):**
- PostToolUse hook script — depends on target repo's integration, not codebugs itself
- `worktree-finish.sh` changes — private to consumer
- Semantic conflict detection — v2
- Line-range claims — v2
