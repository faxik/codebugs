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


def _get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    """Fetch a session row or raise KeyError."""
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Session not found: {session_id}")
    return row


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the codemerge tables if they don't exist."""
    for stmt in MERGE_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
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
    _get_session(conn, session_id)

    now = _now()
    conn.execute(
        "UPDATE codemerge_sessions SET status='abandoned', finished_at=?, last_activity=? "
        "WHERE session_id=?",
        (now, now, session_id),
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


def finish(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    success: bool,
) -> dict[str, Any]:
    """Release lock and mark session done (success) or revert to active (failure)."""
    row = _get_session(conn, session_id)
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
    row = _get_session(conn, session_id)
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
    row = _get_session(conn, session_id)

    # Idempotent: already merging with lock held
    if row["status"] == "merging":
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()
        if lock and lock["session_id"] == session_id:
            return {"proceed": True, "session_id": session_id}

    if row["status"] != "active":
        raise ValueError(
            f"Session '{session_id}' is not in 'active' state (is '{row['status']}')"
        )

    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
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

            actual_head = current_main_head_fn()
            if actual_head != expected_main_head:
                conn.execute("ROLLBACK")
                return {
                    "proceed": False,
                    "reason": "main_moved",
                    "expected_head": expected_main_head,
                    "current_head": actual_head,
                }

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
    _get_session(conn, session_id)

    my_claims = conn.execute(
        "SELECT file_path FROM codemerge_claims WHERE session_id = ?", (session_id,)
    ).fetchall()
    my_files = {r["file_path"] for r in my_claims}

    conflicts: list[dict[str, str]] = []

    # Single query for all overlapping claims from other active/merging sessions
    overlapping = conn.execute(
        """SELECT c.file_path, s.session_id, s.branch
           FROM codemerge_claims c
           JOIN codemerge_sessions s ON c.session_id = s.session_id
           WHERE s.session_id != ? AND s.status IN ('active', 'merging')
             AND c.file_path IN (SELECT file_path FROM codemerge_claims WHERE session_id = ?)""",
        (session_id, session_id),
    ).fetchall()

    for row in overlapping:
        conflicts.append({
            "file": row["file_path"],
            "blocking_session": row["session_id"],
            "blocking_branch": row["branch"],
            "type": "parallel_session",
        })
    conflicts.sort(key=lambda c: (c["file"], c["blocking_session"]))

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


from codebugs.db import register_schema, register_tool_provider  # noqa: E402

register_schema("merge", ensure_schema)


def _get_main_head() -> str:
    """Get current main branch HEAD SHA. Used by merge tools that need git."""
    from codebugs.server import _git_rev_parse
    result = _git_rev_parse("main")
    assert result is not None
    return result


def register_tools(mcp, conn_factory) -> None:
    """Register merge-coordination tools on the given MCP server."""

    @mcp.tool()
    def codemerge_start(
        session_id: str,
        branch: str,
        description: str = "",
        base_commit: str = "",
        repo_root: str = "",
        allow_restart: bool = False,
    ) -> dict[str, Any]:
        """Start a new merge session for a branch.

        Args:
            session_id: Unique identifier for this merge session
            branch: Git branch name being merged
            description: Human-readable description of the work
            base_commit: Git commit SHA this branch diverged from
            repo_root: Repo root path (default: cwd)
            allow_restart: If True, restart an existing active session
        """
        with conn_factory() as conn:
            return start_session(
                conn,
                session_id=session_id,
                branch=branch,
                description=description,
                base_commit=base_commit,
                repo_root=repo_root,
                allow_restart=allow_restart,
            )

    @mcp.tool()
    def codemerge_claim(
        session_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Claim a file as being modified by this session.

        Args:
            session_id: The merge session ID
            file_path: File path being modified (relative to repo root)
        """
        with conn_factory() as conn:
            return add_claim(conn, session_id, file_path)

    @mcp.tool()
    def codemerge_check(
        session_id: str,
        main_changed_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check for overlapping file claims with other sessions.

        Returns whether the session is clean to proceed, lists any conflicts,
        and records the current main HEAD for CAS comparison at merge time.

        Args:
            session_id: The merge session ID
            main_changed_files: Files changed on main since base (optional, for overlap check)
        """
        with conn_factory() as conn:
            return check_overlaps(
                conn,
                session_id,
                main_changed_files=main_changed_files,
                current_main_head_fn=_get_main_head,
            )

    @mcp.tool()
    def codemerge_merge(
        session_id: str,
        expected_main_head: str,
    ) -> dict[str, Any]:
        """Acquire the merge lock and proceed with merging.

        Uses compare-and-swap on main HEAD to prevent races. If main has moved
        since check, returns proceed=False with reason='main_moved'. If another
        session holds the lock, returns proceed=False with reason='lock_held'.

        Args:
            session_id: The merge session ID
            expected_main_head: The main HEAD SHA recorded during codemerge_check
        """
        with conn_factory() as conn:
            return merge(
                conn,
                session_id,
                expected_main_head=expected_main_head,
                current_main_head_fn=_get_main_head,
            )

    @mcp.tool()
    def codemerge_finish(
        session_id: str,
        success: bool = True,
    ) -> dict[str, Any]:
        """Finish a merge session and release the lock.

        Args:
            session_id: The merge session ID
            success: True if merge succeeded (status→done), False if it failed (status→abandoned)
        """
        with conn_factory() as conn:
            return finish(conn, session_id, success=success)


register_tool_provider("merge", register_tools)
