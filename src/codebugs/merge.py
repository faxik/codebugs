"""Database layer — coordinated parallel session merging for codebugs."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

from codebugs import db
from codebugs.types import MERGE_STATUSES, is_vocabulary_filter_active, utc_now

# `MERGE_STATUSES` is imported, not redeclared. It has lived in `types.py` since the
# module was written and was **dead** — nothing referenced it — so the CHECK constraint
# below was the only thing enforcing the vocabulary, and `get_sessions`' filter
# validated nothing. That is the write/query asymmetry CLAUDE.md's vocabulary rule
# prohibits (CB-25 sibling sweep), and the fix is to USE the constant that already
# existed rather than to declare a second one: an earlier draft of this change did
# declare a separate `MERGE_SESSION_STATUSES` here, which would have made three copies
# of one four-value vocabulary while the surrounding comment argued against duplication.
#
# The CHECK keeps its own literal rather than interpolating the tuple: values are never
# interpolated into SQL in this package, and DDL cannot bind them. The two are pinned to
# each other behaviourally instead, by `TestSessionStatusVocabulary` — which probes the
# constraint with every member and a non-member rather than matching the schema text.


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

LOCK_TTL_SECONDS = 300  # 5 minutes


def _get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    """Fetch a session row or raise KeyError."""
    row = conn.execute(
        "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Session not found: {session_id}")
    return row


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the codemerge tables if they don't exist.

    CB-195: read before seeding the lock singleton. `db.connect()` runs this on
    EVERY open (it is part of `_open`'s `_resolved_order()` loop), and the
    id=1 row exists from the very first open onward — every later
    `INSERT OR IGNORE` is a guaranteed no-op. SQLite still takes the write
    lock to attempt an INSERT even when it will end up ignored (it cannot
    know the outcome without starting a write), so the unconditional form
    made a purely reading `db.connect()` contend with any concurrent writer
    for up to the full `busy_timeout` — and, past it, fail outright with
    "database is locked". Checking first turns the steady-state path into a
    single WAL read, which never blocks on a writer at all.

    THE OTHER SIDE OF "STEADY STATE", MEASURED RATHER THAN IMPLIED (CB-202).
    The gain is real and complete once the row exists — but while it is MISSING,
    which means the first open of any tracker and any tracker whose seed rows
    were removed, this insert runs and a reading `db.connect()` waits out a
    concurrent writer exactly as before. Measured on this tree against a 700ms
    foreign hold: 734ms with the seed row absent, 0.8ms with it present. That is
    not a residual defect — one open per tracker cannot be avoided by any
    read-first rule — but it is a boundary a reader is entitled to know about
    instead of inferring it from the words "steady state".

    The race on an EMPTY database is harmless and does not need `db.txn`:
    two connections opening concurrently against a fresh, seedless database
    both see the row missing, both attempt the insert, and SQLite's own
    `OR IGNORE` conflict resolution silently drops the loser — this is NOT
    the read-modify-write shape CLAUDE.md requires `db.txn` for (CB-24),
    because nothing here is COMPUTED from what was read; the row's values
    are constants, and the read only decides whether to skip a redundant
    write.
    """
    for stmt in MERGE_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    if conn.execute("SELECT 1 FROM codemerge_locks WHERE id = 1").fetchone() is None:
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
    """Register a new working session.

    The restart branch is chosen from ``existing["status"]``, so the read and the
    write it selects must be one transaction (CB-24): otherwise two callers both see
    a ``done`` session, both take the restart path, and the second silently deletes
    the claims the first has already begun recording against the restarted session.
    ``db.txn`` takes the write lock before the read; do not restore ``conn.commit()``.
    """
    now = utc_now()
    with db.txn(conn):
        restarted = False
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
                restarted = True

        if not restarted:
            conn.execute(
                """INSERT INTO codemerge_sessions
                   (session_id, branch, description, base_commit, repo_root, started_at, last_activity)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, branch, description, base_commit, repo_root, now, now),
            )

        # Read the result INSIDE the block. Re-reading after the commit returns
        # whatever another writer has done since, not what this call wrote.
        row = conn.execute(
            "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row)


def abandon_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """Mark a session abandoned, so its claims stop conflicting and it drops the lock.

    Precisely, because this text is also what an MCP client reads (CB-106) and an
    overclaiming description is the defect that card is about: the claim ROWS are
    not deleted, they stop being reported, because `check_overlaps` and
    `get_status` select on the session's status. The lock is released only if
    THIS session holds it (``WHERE id=1 AND session_id=?``).

    A ``done`` session is REFUSED. Abandoning one only destroys the record that
    the merge succeeded — `get_status` would then tally finished work under
    ``abandoned_sessions``, which is the audit corruption CB-106 names as its
    third consequence. The guard is a condition on the UPDATE rather than a
    Python check on the row read above, so a session that reaches ``done``
    concurrently is refused too instead of losing the race.

    **The dict returned is the row this call WROTE, captured by ``RETURNING``
    (CB-111).** It used to be a fresh ``SELECT`` issued after ``conn.commit()``,
    so a session restarted inside that window came back as ``status: 'active'``
    for a call that had just written ``abandoned`` — harmless while the only
    caller printed ``session_id``, and a live lie once CB-106 exposed the whole
    dict over MCP. Because THAT statement carries ``RETURNING`` — the
    ``codemerge_locks`` update deliberately does not — its ``rowcount`` must
    never be read (the RETURNING rule): measured, it is 0 right after
    ``execute()`` even on a matching row and only becomes 1 after ``fetchone()``,
    so a ``rowcount == 0`` refusal placed where the old one was fires on every
    successful call. The outcome is read by FETCHING instead.

    Do not restore a ``conn.commit()`` on any path: ``db.txn`` owns the commit,
    and under an ambient transaction it yields False, so a commit here would
    make the caller's unrelated work permanent — CB-24 consequence (1), the
    hazard ``merge()`` and ``pull_next`` were cured of by CB-40.

    **Under an ambient transaction the dict describes an UNCOMMITTED row**, and
    the caller's own ``ROLLBACK`` erases it. That is the ambient-transaction
    invariant `claims.py` documents, not a defect here: this is a release rather
    than an acquisition, so it does not refuse the way ``merge()`` does. It is
    unreachable today — both callers (the MCP tool and the CLI handler) open a
    fresh connection.
    """
    _get_session(conn, session_id)

    now = utc_now()
    with db.txn(conn):
        cur = conn.execute(
            "UPDATE codemerge_sessions SET status='abandoned', finished_at=?, last_activity=? "
            "WHERE session_id=? AND status != 'done' RETURNING *",
            (now, now, session_id),
        )
        # Exhaust the cursor inside the block: db.txn issues COMMIT on exit, and
        # an open RETURNING cursor at that point is a statement still in progress
        # — moving this fetch below the block makes every abandon die with
        # "cannot commit transaction - SQL statements in progress" (measured).
        # One fetchone() suffices only because `session_id` is TEXT PRIMARY KEY,
        # so the WHERE matches at most one row; widen that predicate and this
        # must become a fetchall().
        updated = cur.fetchone()
        if updated is None:
            # NOTHING WAS WRITTEN, AND ON THIS FRAME'S OWN TRANSACTION THE WRITE
            # LOCK IS HELD — `db.txn` took it at BEGIN IMMEDIATE, before the
            # UPDATE ever matched zero rows. Raising is safe there because the
            # context manager rolls back on the way out; a bare `raise` under the
            # old hand-rolled discipline handed the next caller on this connection
            # a held lock, under which `merge()` refuses outright and another
            # connection gets "database is locked". Under an AMBIENT transaction
            # `db.txn` does nothing at all — no rollback — and that is correct:
            # the lock and the decision to undo belong to the caller.
            observed = conn.execute(
                "SELECT status FROM codemerge_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if observed is None:
                raise KeyError(f"Session '{session_id}' not found")
            raise ValueError(
                f"Session '{session_id}' is '{observed['status']}' and cannot be abandoned: "
                "that would erase the record of a merge that succeeded. Nothing is "
                "holding it open — no 'done' session holds the merge lock, and its "
                "claims are already excluded from conflict checks."
            )
        conn.execute(
            "UPDATE codemerge_locks SET session_id=NULL, acquired_at=NULL, expires_at=NULL "
            "WHERE id=1 AND session_id=?",
            (session_id,),
        )
    return dict(updated)


def finish(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    success: bool,
) -> dict[str, Any]:
    """Release lock and mark session done (success) or revert to active (failure).

    The ``merging`` guard is decided from the row read at the top, and the two writes
    below depend on it, so read and writes are one transaction (CB-24). Without it two
    callers both observe ``merging``, both pass the guard and both report success for a
    transition only one of them can legitimately make. ``db.txn`` takes the write lock
    before the read; do not restore ``conn.commit()``.

    What the double-success does NOT do, stated because an earlier draft of this
    docstring claimed it did: the second finisher cannot free a lock already handed to
    another session, because the lock update is guarded by ``AND session_id=?``. The
    defect is the unserialized guard, not lock theft.
    """
    with db.txn(conn):
        row = _get_session(conn, session_id)
        if row["status"] != "merging":
            raise ValueError(
                f"Session '{session_id}' is not in 'merging' state (is '{row['status']}')"
            )

        now = utc_now()
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
        updated = conn.execute(
            "SELECT * FROM codemerge_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(updated)


def add_claim(
    conn: sqlite3.Connection,
    session_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Record that a session has modified a file. Idempotent.

    The active/merging guard is decided from the row read at the top and the claim
    insert depends on it, so both are one transaction (CB-24). Without it a claim can
    be recorded against a session another writer abandoned between the check and the
    insert — the claim then belongs to a dead session and is invisible to overlap
    detection, which is the one thing this table exists for. ``db.txn`` takes the
    write lock before the read; do not restore ``conn.commit()``.
    """
    with db.txn(conn):
        row = _get_session(conn, session_id)
        if row["status"] not in ("active", "merging"):
            raise ValueError(f"Session '{session_id}' is not active (is '{row['status']}')")

        now = utc_now()
        conn.execute(
            "INSERT OR IGNORE INTO codemerge_claims (session_id, file_path, claimed_at) "
            "VALUES (?, ?, ?)",
            (session_id, file_path, now),
        )
        conn.execute(
            "UPDATE codemerge_sessions SET last_activity=? WHERE session_id=?",
            (now, session_id),
        )
    return {"session_id": session_id, "file_path": file_path, "claimed_at": now}


def merge(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    expected_main_head: str,
    current_main_head_fn: Callable[[], str],
) -> dict[str, Any]:
    """Acquire merge lock with CAS verification.

    THE MUTUAL-EXCLUSION POINT for parallel agents pushing to main. One
    ``db.txn`` (``BEGIN IMMEDIATE``) spans the whole decision: the session read,
    every guard derived from it, the head check, and the lock write.

    Three defects are fixed here together because they share this one boundary:

    * **CB-36** — the ``status != 'active'`` guard used to be decided BEFORE the
      lock was taken, so a concurrent ``abandon_session`` could commit in between
      and this call would revive an abandoned session and hand it the lock.
    * **CB-40** — the old ``conn.isolation_level = None`` line COMMITS any open
      transaction, so calling this under an ambient transaction silently committed
      the caller's unrelated work. There is no raw ``BEGIN IMMEDIATE`` here now.
    * **CB-41** — the idempotent branch compared only ``session_id`` and never read
      ``expires_at``, while the acquisition branch treated an expired lease as
      reclaimable. An expired holder retrying got ``proceed: True`` from the first
      branch while a competitor reclaimed the lease and got ``proceed: True`` from
      the second — two agents merging at once.

    **Expired self-retry RENEWS (CB-41).** If the caller already holds the lock it
    gets a fresh lease, whether or not the old one had lapsed, so expiry no longer
    decides anything on the self-owned path and the two branches cannot disagree.
    The TTL still reclaims from a holder that has *died* — a dead holder does not
    retry — but it no longer bounds one that is alive, wedged and retrying. That is
    the accepted cost of preserving the documented idempotent contract; bounding a
    live-but-stuck holder needs liveness detection, not a timestamp.

    **NO REFUSAL PATH WRITES ANYTHING.** The head check runs BEFORE the expired
    holder is marked abandoned, so ``main_moved`` has nothing to undo and can simply
    return. That ordering is why this function needs no rollback-and-return
    machinery — and it is also plainly more correct than abandoning another session
    and only then deciding not to proceed.

    **Refuses an ambient transaction, unconditionally.** Under one, ``db.txn``
    yields ``False`` and the caller owns the commit, so this would report
    ``proceed: True`` for a lock row no other connection can see yet. A gate that
    says "you hold the lock" before the lock is committed is worse than any defect
    fixed here. This is a plain ``raise``, not an ``assert`` — ``assert`` is stripped
    under ``-O``.

    Contract change, accepted: guards are now evaluated under the write lock, so a
    nonexistent / ``done`` / ``abandoned`` session may wait up to ``busy_timeout``
    and can surface ``sqlite3.OperationalError`` instead of ``KeyError`` /
    ``ValueError`` when another writer holds the database. A guard evaluated outside
    the lock is precisely the defect.

    Args:
        session_id: The session requesting merge.
        expected_main_head: The main HEAD SHA the caller last checked against.
        current_main_head_fn: Callable returning current main HEAD SHA.
            Injected so core logic stays git-free and testable.

    Returns:
        {proceed: True} or {proceed: False, reason: "...", ...}
    """
    if conn.in_transaction:
        raise RuntimeError(
            "merge() must be called on a connection with no open transaction: it is a "
            "lock acquisition, and under an ambient transaction it would report success "
            "for a lock row that is not yet committed."
        )

    # THE DEADLINE IS COMPUTED BY SQLITE, AT THE MOMENT OF THE WRITE (CB-41, round 3).
    #
    # Three review rounds died on the same shape: a Python timestamp sampled at one
    # point and written as a lease deadline at another, with something slow in
    # between — first the lock wait and the git callback, then the stale-holder
    # `abandoned` UPDATE. Each time the lease landed ALREADY EXPIRED, this call
    # returned `proceed: True`, and the next contender saw the lock reclaimable and
    # also got `proceed: True`.
    #
    # Point-of-use discipline is the wrong layer for that: it has to be re-established
    # every time a statement is inserted. Letting SQLite evaluate `strftime('now')` as
    # part of the UPDATE makes a stale deadline UNREPRESENTABLE — there is no window
    # between sampling and writing, because they are the same operation.
    ttl = f"+{LOCK_TTL_SECONDS} seconds"
    _EXPIRES_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)"
    _NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

    with db.txn(conn):
        row = _get_session(conn, session_id)
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()

        # Self-owned: renew and report success. Deliberately does NOT consult
        # `expires_at` — see CB-41 in the docstring. This branch WRITES, so it must
        # commit; returning from inside the block does exactly that.
        if row["status"] == "merging" and lock and lock["session_id"] == session_id:
            conn.execute(
                f"UPDATE codemerge_locks SET expires_at={_EXPIRES_SQL} "  # noqa: S608
                "WHERE id=1 AND session_id=?",
                (ttl, session_id),
            )
            return {"proceed": True, "session_id": session_id}

        if row["status"] != "active":
            raise ValueError(
                f"Session '{session_id}' is not in 'active' state (is '{row['status']}')"
            )

        # Held by SOMEONE ELSE on a live lease. No writes; refuse.
        # ISO 8601 string comparison is safe — the format is fixed-width. `now` is
        # sampled HERE, immediately before its only use. Sampling it early would be
        # conservative rather than unsafe (an early `now` makes a lease look live, so
        # we refuse rather than over-grant) but there is no reason to accept even that.
        now = conn.execute(f"SELECT {_NOW_SQL} AS t").fetchone()["t"]  # noqa: S608
        if (lock["session_id"] is not None
                and lock["expires_at"] and lock["expires_at"] > now):
            return {
                "proceed": False,
                "reason": "lock_held",
                "holder": lock["session_id"],
                "held_since": lock["acquired_at"],
                "expires_at": lock["expires_at"],
            }

        # Head check BEFORE any write, so this refusal has nothing to undo.
        actual_head = current_main_head_fn()
        if actual_head != expected_main_head:
            return {
                "proceed": False,
                "reason": "main_moved",
                "expected_head": expected_main_head,
                "current_head": actual_head,
            }

        # A stale holder (expired lease) loses its session here. Its timestamp is
        # computed by SQLite in this statement, so nothing above can make it stale.
        if lock["session_id"] is not None:
            conn.execute(
                f"UPDATE codemerge_sessions SET status='abandoned', "  # noqa: S608
                f"last_activity={_NOW_SQL} WHERE session_id=? AND status='merging'",
                (lock["session_id"],),
            )

        # Session state BEFORE the lease, so the lease write is the LAST mutation in
        # this transaction. Every statement between writing a deadline and committing
        # it is time the deadline is already burning; keeping that list empty is the
        # only part of that window this code controls.
        conn.execute(
            f"UPDATE codemerge_sessions SET status='merging', "  # noqa: S608
            f"last_activity={_NOW_SQL} WHERE session_id=?",
            (session_id,),
        )

        # The granted lease, evaluated by SQLite as this statement runs — so however
        # long anything above took, the deadline is TTL seconds from NOW.
        #
        # RESIDUAL, and it is irreducible here rather than an oversight (CB-42): the
        # gap between this statement and `COMMIT` is still unaccounted for. A stall
        # longer than the TTL in that gap commits a lease that is already expired.
        # Post-commit re-validation does not close it — it only moves the window to
        # "between the re-validation and the caller acting" — because `proceed: True`
        # is not a durable capability in a TTL scheme without a fencing token checked
        # at the protected operation itself. Filed rather than papered over.
        conn.execute(
            f"UPDATE codemerge_locks SET session_id=?, acquired_at={_NOW_SQL}, "  # noqa: S608
            f"expires_at={_EXPIRES_SQL} WHERE id=1",
            (session_id, ttl),
        )
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
    if is_vocabulary_filter_active(status):
        if status not in MERGE_STATUSES:
            raise ValueError(
                f"Invalid status: {status!r}. Must be one of {MERGE_STATUSES}"
            )
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


from codebugs.db import register_schema, register_tool_provider, register_cli_provider  # noqa: E402

register_schema("merge", ensure_schema)


def _get_main_head() -> str:
    """Get current main branch HEAD SHA. Used by merge tools that need git."""
    from codebugs.db import git_rev_parse
    result = git_rev_parse("main")
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
        allow_restart: Annotated[bool, Field(strict=True)] = False,
    ) -> dict[str, Any]:
        """Start a new merge session for a branch.

        Args:
            session_id: Unique identifier for this merge session
            branch: Git branch name being merged
            description: Human-readable description of the work
            base_commit: Git commit SHA this branch diverged from
            repo_root: Repo root path (default: cwd)
            allow_restart: If True, reuse this session_id when its previous
                session is finished — 'abandoned' or 'done'. Restarting DELETES
                that session's file claims, so re-claim anything you still need.
                It does NOT restart a live one: starting over an 'active' or
                'merging' session is an error whether or not this is set.
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
        success: Annotated[bool, Field(strict=True)] = True,
    ) -> dict[str, Any]:
        """Finish a merge session and release the lock.

        Call this after codemerge_merge() returned proceed=true; the session must
        be in 'merging' state or this refuses in BOTH directions of `success`.

        Args:
            session_id: The merge session ID
            success: True if the merge succeeded (status→done). False if the git
                merge/cherry-pick failed (status→active): the lock is released and
                the session stays alive so it can try again. False does NOT close
                the session — use codemerge_abandon for that.
        """
        with conn_factory() as conn:
            return finish(conn, session_id, success=success)

    @mcp.tool()
    def codemerge_abandon(
        session_id: str,
    ) -> dict[str, Any]:
        """Close a session for good, so its files stop blocking everyone else.

        This is the way OUT of a session that will not be merged under its own
        lock — including the case an agent hits routinely: the branch was
        integrated by some other route (a merge harness holding its own lock), so
        codemerge_merge refuses with reason='main_moved' and the session is
        stranded in 'active', which codemerge_finish will not accept. Until it is
        abandoned, its claimed files are reported as conflicts to every later
        session, with no expiry — so closing it is what keeps codemerge_check
        worth consulting.

        What it does, stated exactly: the session's claim rows are NOT deleted,
        they stop being reported, because the conflict query selects on session
        status. The merge lock is released only if this session holds it.

        Re-issuing it is safe: a second call on an already-abandoned session
        changes nothing but the timestamps. A 'done' session is REFUSED — that
        would erase the record of a merge that succeeded — and an unknown
        session_id is an error.

        Args:
            session_id: The merge session ID. Whatever session is NAMED is the
                one closed; nothing ties it to the caller, so passing a stale or
                mistyped id closes someone else's work.
        """
        with conn_factory() as conn:
            return abandon_session(conn, session_id)

    # --- Read-only introspection (CB-107) ---
    # Until these three, an MCP client could start, claim, merge and finish a
    # session but could not see what was actually happening: whose session held
    # the lock, or what its own claims were. Thin wrappers over the same
    # functions the CLI has always called (merge-sessions/-status/-claims).

    @mcp.tool()
    def codemerge_sessions(status: str | None = None) -> list[dict[str, Any]]:
        """List merge sessions with claim counts.

        Args:
            status: Filter by status ('active', 'merging', 'done', 'abandoned').
                Omit for all sessions.
        """
        with conn_factory() as conn:
            return get_sessions(conn, status=status)

    @mcp.tool()
    def codemerge_status() -> dict[str, Any]:
        """Dashboard summary: session counts by status, total active claims,
        and who (if anyone) holds the merge lock."""
        with conn_factory() as conn:
            return get_status(conn)

    @mcp.tool()
    def codemerge_claims(session_id: str) -> list[dict[str, Any]]:
        """List all files a session has claimed, in claim order.

        Args:
            session_id: The merge session ID.
        """
        with conn_factory() as conn:
            return get_claims(conn, session_id)


register_tool_provider("merge", register_tools)


# --- CLI ---

def register_cli(sub, commands) -> None:
    """Register merge CLI subcommands."""
    import argparse
    from codebugs.fmt import format_table

    def _cmd_merge_sessions(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            sessions = get_sessions(conn, status=args.status)
        finally:
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
        print(format_table(
            data, ["session_id", "branch", "status", "claims", "description"],
            max_widths={"description": 40, "branch": 30},
        ))

    def _cmd_merge_status(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            s = get_status(conn)
        finally:
            conn.close()
        print("Codemerge Status")
        print("=" * 40)
        print(f"Active sessions:    {s['active_sessions']}")
        print(f"Merging sessions:   {s['merging_sessions']}")
        print(f"Done sessions:      {s['done_sessions']}")
        print(f"Abandoned sessions: {s['abandoned_sessions']}")
        print(f"Total claims:       {s['total_claims']}")
        print(f"Lock holder:        {s['lock_holder'] or '(none)'}")

    def _cmd_merge_abandon(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        try:
            with domain_errors():
                result = abandon_session(conn, args.session_id)
                print(f"Abandoned: {result['session_id']}")
        finally:
            conn.close()

    def _cmd_merge_claims(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            claims = get_claims(conn, args.session_id)
        finally:
            conn.close()
        if not claims:
            print("(no claims)")
            return
        data = [{"file": c["file_path"], "claimed_at": c["claimed_at"]} for c in claims]
        print(format_table(data, ["file", "claimed_at"]))

    # Argparse setup
    p = sub.add_parser("merge-sessions", help="List merge sessions")
    p.add_argument("--status", help="Filter: active|merging|done|abandoned")

    sub.add_parser("merge-status", help="Merge coordination dashboard")

    p = sub.add_parser("merge-abandon", help="Abandon a stale session")
    p.add_argument("session_id", help="Session ID to abandon")

    p = sub.add_parser("merge-claims", help="List claimed files for a session")
    p.add_argument("session_id", help="Session ID")

    commands.update({
        "merge-sessions": _cmd_merge_sessions,
        "merge-status": _cmd_merge_status,
        "merge-abandon": _cmd_merge_abandon,
        "merge-claims": _cmd_merge_claims,
    })


register_cli_provider("merge", register_cli)
