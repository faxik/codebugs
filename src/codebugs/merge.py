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
CREATE INDEX IF NOT EXISTS idx_codemerge_sessions_status ON codemerge_sessions(status)
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
    conn.execute(
        "UPDATE codemerge_locks SET session_id=NULL, acquired_at=NULL, expires_at=NULL "
        "WHERE id=1 AND session_id=?",
        (session_id,),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone())


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
    #
    # We must disable Python's implicit transaction management to issue
    # BEGIN IMMEDIATE ourselves. Save and restore isolation_level around
    # the critical section.
    saved_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
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
    finally:
        conn.isolation_level = saved_isolation

    return {"proceed": True, "session_id": session_id}


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
