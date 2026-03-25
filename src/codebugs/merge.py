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
