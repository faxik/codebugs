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
