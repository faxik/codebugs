"""Database layer — sweep batch-iteration for codebugs.

PR1 of the codesweep retro-storage spec adds:
- Stable-key recurrence (atomic upsert bumps `recurrence_count` instead of skipping)
- Configurable lifecycle states per sweep (default ["pending","done"])
- Optional transition DAG validation
- Selective archive with soft-delete semantics — un-archive on re-add

All additions are backward-compatible. Sweeps without `lifecycle` declared and
items without explicit `state` continue to behave as before.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from codebugs import db, surfacegen
from codebugs.fmt import empty_page_line, format_table
from codebugs.types import require_row_limit, utc_now


SCHEMA = """\
CREATE TABLE IF NOT EXISTS codesweep_sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id TEXT UNIQUE NOT NULL,
    name TEXT,
    description TEXT NOT NULL DEFAULT '',
    default_batch_size INTEGER NOT NULL DEFAULT 10,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'archived')),
    lifecycle TEXT NOT NULL DEFAULT '["pending","done"]',
    terminal_states TEXT NOT NULL DEFAULT '["done"]',
    transitions TEXT,
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
    state TEXT NOT NULL DEFAULT 'pending',
    recurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    archived_at TEXT,
    archive_reason TEXT,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE(sweep_id, item)
);

CREATE INDEX IF NOT EXISTS idx_codesweep_items_next
    ON codesweep_items(sweep_id, processed, position);
"""

# Indexes that reference columns added in the PR1 migration. Created AFTER
# `_migrate()` so that legacy DBs gain the columns first.
_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_codesweep_items_archived "
    "ON codesweep_items(sweep_id, archived_at)",
]


_SWEEP_NEW_COLS = {
    "lifecycle": "TEXT NOT NULL DEFAULT '[\"pending\",\"done\"]'",
    "terminal_states": "TEXT NOT NULL DEFAULT '[\"done\"]'",
    "transitions": "TEXT",
}

_ITEM_NEW_COLS = {
    "state": "TEXT NOT NULL DEFAULT 'pending'",
    "recurrence_count": "INTEGER NOT NULL DEFAULT 1",
    "first_seen": "TEXT",
    "last_seen": "TEXT",
    "archived_at": "TEXT",
    "archive_reason": "TEXT",
}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent additive migration for existing DBs."""
    sweep_cols = _existing_columns(conn, "codesweep_sweeps")
    for col, ddl in _SWEEP_NEW_COLS.items():
        if col not in sweep_cols:
            conn.execute(f"ALTER TABLE codesweep_sweeps ADD COLUMN {col} {ddl}")

    item_cols = _existing_columns(conn, "codesweep_items")
    for col, ddl in _ITEM_NEW_COLS.items():
        if col not in item_cols:
            conn.execute(f"ALTER TABLE codesweep_items ADD COLUMN {col} {ddl}")
    # Backfill: state mirrors processed for legacy rows
    if "state" not in item_cols:
        conn.execute(
            "UPDATE codesweep_items SET state = CASE WHEN processed = 1 THEN 'done' ELSE 'pending' END"
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_codesweep_items_archived "
        "ON codesweep_items(sweep_id, archived_at)"
    )
    conn.commit()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the codesweep tables if they don't exist; migrate existing ones."""
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    _migrate(conn)
    for stmt in _POST_MIGRATION_INDEXES:
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


def _load_sweep_lifecycle(
    conn: sqlite3.Connection, sweep_id: str
) -> tuple[list[str], list[str], dict[str, list[str]] | None]:
    row = conn.execute(
        "SELECT lifecycle, terminal_states, transitions FROM codesweep_sweeps WHERE sweep_id = ?",
        (sweep_id,),
    ).fetchone()
    lifecycle = json.loads(row["lifecycle"])
    terminal = json.loads(row["terminal_states"])
    transitions = json.loads(row["transitions"]) if row["transitions"] else None
    return lifecycle, terminal, transitions


def create_sweep(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    description: str = "",
    default_batch_size: int = 10,
    lifecycle: list[str] | None = None,
    terminal_states: list[str] | None = None,
    transitions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Create a new sweep. Returns the created sweep as a dict.

    Args:
        lifecycle: Ordered list of allowed states. Default ["pending","done"].
        terminal_states: States that count as "processed". Default ["done"].
            Must be a subset of lifecycle.
        transitions: Optional dict[state, list[allowed_next_state]] for DAG-constrained
            lifecycles. None (default) = unconstrained.
    """
    if default_batch_size < 1:
        raise ValueError("Batch size must be at least 1")

    if lifecycle is None:
        lifecycle = ["pending", "done"]
    if not lifecycle:
        raise ValueError("Lifecycle must contain at least one state")
    if len(set(lifecycle)) != len(lifecycle):
        raise ValueError(f"Lifecycle states must be unique: {lifecycle}")

    if terminal_states is None:
        # Default: last state of lifecycle, or "done" if it's in there
        terminal_states = ["done"] if "done" in lifecycle else [lifecycle[-1]]
    extra = set(terminal_states) - set(lifecycle)
    if extra:
        raise ValueError(f"Terminal states not in lifecycle: {sorted(extra)}")

    if transitions is not None:
        for src, dsts in transitions.items():
            if src not in lifecycle:
                raise ValueError(f"Transition source not in lifecycle: {src}")
            unknown = set(dsts) - set(lifecycle)
            if unknown:
                raise ValueError(
                    f"Transition targets from {src} not in lifecycle: {sorted(unknown)}"
                )

    if name is not None:
        existing = conn.execute(
            "SELECT 1 FROM codesweep_sweeps WHERE name = ?", (name,),
        ).fetchone()
        if existing:
            raise ValueError(f"Sweep name already exists: {name}")

    now = utc_now()
    cursor = conn.execute(
        """INSERT INTO codesweep_sweeps
           (sweep_id, name, description, default_batch_size, status,
            lifecycle, terminal_states, transitions, created_at, updated_at)
           VALUES ('_placeholder', ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
        (
            name,
            description,
            default_batch_size,
            json.dumps(lifecycle),
            json.dumps(terminal_states),
            json.dumps(transitions) if transitions is not None else None,
            now,
            now,
        ),
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
        "lifecycle": json.loads(row["lifecycle"]),
        "terminal_states": json.loads(row["terminal_states"]),
        "transitions": json.loads(row["transitions"]) if row["transitions"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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
    """Add items to a sweep.

    Atomic upsert (F1): if an item already exists, bumps `recurrence_count`,
    updates `last_seen`, and clears `archived_at` (un-archive on re-detect — R5).
    Tags overwrite on bump if provided.

    Returns counts: `added` = newly inserted, `recurrence_bumped` = existing items
    re-detected. `duplicates_skipped` is kept for backward compat — same value as
    `recurrence_bumped`.

    **One transaction for the batch**, opened before the first read (CB-24). The
    per-item upsert is a single atomic statement, but the values driving it are not:
    the ``archived`` guard, ``initial_state`` (parsed from the sweep's stored
    lifecycle) and the starting ``position`` are all read once, above the loop, and
    then fed into every write in it. Without the write lock taken first, a concurrent
    ``archive_sweep`` or lifecycle rewrite lands between the read and the writes it
    governs, and items are inserted into an archived sweep or stamped with a state
    that is no longer in its lifecycle. Do not restore ``conn.commit()``.
    """
    with db.txn(conn):
        sweep_id = _resolve_sweep(conn, sweep_ref)

        sw_row = conn.execute(
            "SELECT status, lifecycle FROM codesweep_sweeps WHERE sweep_id = ?",
            (sweep_id,),
        ).fetchone()
        if sw_row["status"] == "archived":
            raise ValueError(f"Cannot add items to archived sweep: {sweep_id}")
        initial_state = json.loads(sw_row["lifecycle"])[0]

        now = utc_now()
        tags_json = json.dumps(tags or [])
        pos = _next_position(conn, sweep_id)
        added = 0
        bumped = 0

    # Atomic upsert per item:
    # - On insert: recurrence_count=1, first_seen=now, last_seen=now, state=<initial>.
    # - On update: recurrence_count++, last_seen=now, archived_at=NULL (un-archive), tags overwritten.
    #   State is preserved — re-detection doesn't reset progress; consumer calls mark to transition.
        for item in items:
            row = conn.execute(
                """INSERT INTO codesweep_items
                   (sweep_id, item, tags, processed, state, recurrence_count,
                    first_seen, last_seen, archived_at, archive_reason,
                    position, created_at)
                   VALUES (?, ?, ?, 0, ?, 1, ?, ?, NULL, NULL, ?, ?)
                   ON CONFLICT(sweep_id, item) DO UPDATE SET
                       recurrence_count = codesweep_items.recurrence_count + 1,
                       last_seen = excluded.last_seen,
                       archived_at = NULL,
                       archive_reason = NULL,
                       tags = excluded.tags
                   RETURNING (recurrence_count = 1) AS was_new""",
                (sweep_id, item, tags_json, initial_state, now, now, pos, now),
            ).fetchone()
            if row["was_new"]:
                pos += 1
                added += 1
            else:
                bumped += 1

        conn.execute(
            "UPDATE codesweep_sweeps SET updated_at = ? WHERE sweep_id = ?",
            (now, sweep_id),
        )
    return {
        "sweep_id": sweep_id,
        "added": added,
        "recurrence_bumped": bumped,
        "duplicates_skipped": bumped,  # backward compat alias
    }


def next_batch(
    conn: sqlite3.Connection,
    sweep_ref: str,
    *,
    limit: int | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return the next batch of unprocessed items in insertion order.

    Excludes archived items (F5). "Unprocessed" means `processed = 0`, which
    mirrors `state NOT IN terminal_states`.

    ``limit=None`` keeps its meaning here — fall back to the sweep's own
    ``default_batch_size`` — which is why this site needs a validator that
    accepts ``None`` rather than a bare non-negative check.
    """
    # CB-196, before `_resolve_sweep` so a bad limit refuses without first
    # spending a lookup: validate the argument before anything is resolved.
    limit = require_row_limit("limit", limit)

    sweep_id = _resolve_sweep(conn, sweep_ref)

    row = conn.execute(
        "SELECT default_batch_size FROM codesweep_sweeps WHERE sweep_id = ?",
        (sweep_id,),
    ).fetchone()
    batch_size = limit if limit is not None else row["default_batch_size"]

    conditions = ["sweep_id = ?", "processed = 0", "archived_at IS NULL"]
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
        f"SELECT item, tags, position, state, recurrence_count "
        f"FROM codesweep_items {where} ORDER BY position LIMIT ?",
        params + [batch_size],
    ).fetchall()

    items = [
        {
            "item": r["item"],
            "tags": json.loads(r["tags"]),
            "position": r["position"],
            "state": r["state"],
            "recurrence_count": r["recurrence_count"],
        }
        for r in rows
    ]

    total_unprocessed = conn.execute(
        f"SELECT COUNT(*) as c FROM codesweep_items {where}",
        params,
    ).fetchone()["c"]
    remaining = total_unprocessed - len(items)

    return {"sweep_id": sweep_id, "items": items, "remaining": remaining}


def _validate_transition(
    transitions: dict[str, list[str]] | None, src: str, dst: str
) -> None:
    if transitions is None:
        return
    if src == dst:
        return  # idempotent
    allowed = transitions.get(src, [])
    if dst not in allowed:
        raise ValueError(
            f"Transition not allowed: {src!r} -> {dst!r}. "
            f"Allowed from {src!r}: {allowed}"
        )


def mark_items(
    conn: sqlite3.Connection,
    sweep_ref: str,
    items: list[str],
    *,
    processed: bool | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Mark items by transitioning their state.

    Two modes, and they are MUTUALLY EXCLUSIVE (CB-197):
    - `state="<name>"`: explicit state transition. Validated against the sweep's
      lifecycle, and against `transitions` DAG if declared.
    - `processed=True/False` (legacy): True transitions to the first terminal
      state, False transitions to the first non-terminal state.
    - neither: same as `processed=True`, i.e. the first terminal state.

    **Supplying BOTH raises `ValueError`, and the trigger is the FACT OF SUPPLY,
    never a disagreement between the two values.** `state` names one state while
    `processed` names a CLASS of states, so a contradiction has no resolution that
    is not a guess about which the caller meant — CB-28's rule is "forward when a
    path exists, refuse only when none could", and here none could. A *consistent*
    pair (`state="done", processed=True` where `done` is terminal) is refused too:
    the agreement is an accident of this sweep's lifecycle (nothing says the
    terminal state a caller named is `terminal_states[0]`), and a "do they
    contradict?" test would be a second, hidden rule whose verdict moves with the
    lifecycle. Before this the whole `processed` argument was silently unread when
    `state` was set, so `state="done", processed=False` wrote `processed=1` — the
    opposite of what was asked, reported as success.

    That is why the default is `None` and not `True`: "not supplied" has to be
    REPRESENTABLE for the refusal to key on supply. Every caller that OMITS the
    argument is unaffected. A caller that ASSEMBLES arguments programmatically
    sends `processed=None` (not `True`) alongside a `state` — `_cmd_sweep_mark`
    does exactly that when `--undo` is absent, and deliberately sends `False`
    when it is present, so that `--state … --undo` reaches the refusal above
    rather than being silently reconciled here.

    **ONE BEHAVIOUR CHANGED BEYOND THE REFUSAL, AND IT IS AN INVERSION — named
    here because leaving it unnamed would reproduce this card's own defect.**
    An EXPLICIT `processed=None` used to be read by `if processed:`, where
    `None` is falsey, so it meant *unmark* (the first NON-terminal state). It
    now means *not supplied*, i.e. *mark* (the first terminal state). Measured
    both ways: `todo` before, `done` after. That is the exact opposite outcome,
    behind a success payload — so it is declared rather than absorbed.

    It is unavoidable given the form: `None` is the only value the MCP boundary
    can carry for "absent" (`surfacegen.build_tool` calls `apply_defaults`, so
    the declared default is forwarded on every call), and a sentinel would have
    to be JSON-serialisable to survive into the schema. The cost is bounded and
    was measured before it was accepted: NO caller anywhere passes an explicit
    `None`, and over MCP the value was previously UNREACHABLE — `processed` was
    declared a strict `bool`, which refuses a JSON `null` outright. So the
    change is real, is the opposite of what a hypothetical caller asked for,
    and has an empty population today.

    The refusal is raised BEFORE `db.txn` opens: it is decidable from the arguments
    alone, and a refusal must not take the write lock.

    `processed` and `processed_at` are kept in sync with `state IN terminal_states`.
    Archived items raise — un-archive first via re-add or directly.

    **The whole batch is ONE transaction.** If any item is missing, archived, or
    makes an illegal transition, *no* item in the call is applied. The transaction
    opens before the first read (CB-24): each item's transition is validated in
    Python against the state just read, so without the write lock two callers both
    observe the same state and each writes a transition the DAG permits only one of.

    One transaction for the batch rather than one per item, for two reasons — and
    neither is "a race between items", of which there is none. First, the batch must
    roll back as a unit. Second, ``_resolve_sweep`` and ``_load_sweep_lifecycle`` read
    the lifecycle and ``transitions`` DAG *once, above the loop*; per-item transactions
    would let a concurrent lifecycle rewrite invalidate the DAG that the remaining
    items are still being validated against.

    Do not restore a ``conn.commit()`` here: ``db.txn`` owns the commit, and yields
    ``False`` under an ambient transaction so that committing would land the caller's
    work.
    """
    if state is not None and processed is not None:
        raise ValueError(
            "processed and state are mutually exclusive: state names ONE state, "
            "processed names a CLASS of states, so a call carrying both has no "
            f"unambiguous target (got state={state!r}, processed={processed!r}). "
            "Pass state= for an explicit transition, or processed= for the legacy "
            "first-terminal/first-non-terminal mode, never both."
        )

    with db.txn(conn):
        sweep_id = _resolve_sweep(conn, sweep_ref)
        lifecycle, terminal_states, transitions = _load_sweep_lifecycle(conn, sweep_id)

        if state is not None:
            if state not in lifecycle:
                raise ValueError(
                    f"State {state!r} not in sweep lifecycle: {lifecycle}"
                )
            target_state = state
        else:
            # `None` (not supplied) reads as True — the pre-CB-197 default. Kept
            # as `is None or` rather than a normalisation above, so the one place
            # that decides "supplied?" is the refusal.
            #
            # Truthiness of every OTHER non-bool is unchanged — `0`, `""`, `[]`
            # still unmark, as they did. `None` is the single exception and it
            # INVERTS: it used to be falsey here and therefore meant unmark. See
            # the docstring's "ONE BEHAVIOUR CHANGED" paragraph; an earlier
            # version of this comment claimed non-bool truthiness was unchanged
            # full stop, which was false for exactly the value this line adds.
            if processed is None or processed:
                target_state = terminal_states[0]
            else:
                non_terminal = [s for s in lifecycle if s not in terminal_states]
                if not non_terminal:
                    raise ValueError(
                        "Cannot unmark — every state in this sweep's lifecycle is terminal"
                    )
                target_state = non_terminal[0]

        target_processed = 1 if target_state in terminal_states else 0
        now = utc_now()

        for item in items:
            cur_row = conn.execute(
                "SELECT state, archived_at FROM codesweep_items "
                "WHERE sweep_id = ? AND item = ?",
                (sweep_id, item),
            ).fetchone()
            if cur_row is None:
                raise KeyError(f"Item not found in sweep {sweep_id}: {item}")
            if cur_row["archived_at"] is not None:
                raise ValueError(
                    f"Cannot mark archived item {item!r} in {sweep_id}; un-archive first"
                )
            _validate_transition(transitions, cur_row["state"], target_state)

            if target_processed:
                conn.execute(
                    "UPDATE codesweep_items SET state = ?, processed = 1, processed_at = ? "
                    "WHERE sweep_id = ? AND item = ?",
                    (target_state, now, sweep_id, item),
                )
            else:
                conn.execute(
                    "UPDATE codesweep_items SET state = ?, processed = 0, processed_at = NULL "
                    "WHERE sweep_id = ? AND item = ?",
                    (target_state, sweep_id, item),
                )

        conn.execute(
            "UPDATE codesweep_sweeps SET updated_at = ? WHERE sweep_id = ?",
            (now, sweep_id),
        )
    return {
        "sweep_id": sweep_id,
        "updated": len(items),
        "state": target_state,
    }


def get_status(
    conn: sqlite3.Connection,
    sweep_ref: str,
) -> dict[str, Any]:
    """Return sweep overview with progress, per-tag breakdown, per-state counts.

    Excludes archived items from total/processed/remaining; reports `archived` count
    separately.
    """
    sweep_id = _resolve_sweep(conn, sweep_ref)
    sw = conn.execute(
        "SELECT * FROM codesweep_sweeps WHERE sweep_id = ?", (sweep_id,),
    ).fetchone()

    counts = conn.execute(
        """SELECT
              SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END) as live,
              SUM(CASE WHEN archived_at IS NULL AND processed = 1 THEN 1 ELSE 0 END) as processed,
              SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END) as archived
           FROM codesweep_items WHERE sweep_id = ?""",
        (sweep_id,),
    ).fetchone()
    total = counts["live"] or 0
    processed = counts["processed"] or 0
    archived = counts["archived"] or 0

    tag_rows = conn.execute(
        """SELECT jt.value as tag,
                  COUNT(*) as total,
                  SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) as done
           FROM codesweep_items, json_each(tags) as jt
           WHERE sweep_id = ? AND archived_at IS NULL
           GROUP BY jt.value""",
        (sweep_id,),
    ).fetchall()
    by_tag = {
        r["tag"]: {"total": r["total"], "processed": r["done"]}
        for r in tag_rows
    }

    state_rows = conn.execute(
        """SELECT state, COUNT(*) as c FROM codesweep_items
           WHERE sweep_id = ? AND archived_at IS NULL
           GROUP BY state""",
        (sweep_id,),
    ).fetchall()
    by_state = {r["state"]: r["c"] for r in state_rows}

    return {
        "sweep_id": sweep_id,
        "name": sw["name"],
        "status": sw["status"],
        "default_batch_size": sw["default_batch_size"],
        "lifecycle": json.loads(sw["lifecycle"]),
        "terminal_states": json.loads(sw["terminal_states"]),
        "total": total,
        "processed": processed,
        "remaining": total - processed,
        "archived": archived,
        "by_tag": by_tag,
        "by_state": by_state,
    }


def archive_sweep(
    conn: sqlite3.Connection,
    sweep_ref: str,
) -> dict[str, Any]:
    """Archive an entire sweep (sweep-level archive — distinct from archive_items).

    ONE transaction over ``_resolve_sweep`` and the UPDATE it feeds (CB-24,
    CB-126). The read that decides is HIDDEN BEHIND THE HELPER, which is why a
    grep for ``SELECT`` does not show this function as a read-modify-write; the
    shape is the same one, and ``mark_items`` above wraps the same helper for the
    same reason. A sweep deleted between resolve and UPDATE left the UPDATE
    matching zero rows while the response still reported it archived.

    The response is READ BACK from the UPDATE's ``RETURNING`` rather than
    asserted, so ``status`` is the value the row now carries. Never read
    ``rowcount`` on a ``RETURNING`` statement — it is 0 until the cursor is
    exhausted, which reports nothing-happened over a landed write.

    Do not restore a ``conn.commit()`` here: ``db.txn`` owns the commit, and
    yields False under an ambient transaction so that committing would land the
    caller's work.
    """
    with db.txn(conn):
        sweep_id = _resolve_sweep(conn, sweep_ref)
        row = conn.execute(
            "UPDATE codesweep_sweeps SET status = 'archived', updated_at = ? "
            "WHERE sweep_id = ? RETURNING sweep_id, status",
            (utc_now(), sweep_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Sweep not found: {sweep_ref}")
        archived_id, archived_status = row["sweep_id"], row["status"]

    return {"sweep_id": archived_id, "status": archived_status}


_DURATION_UNITS = {"d": 1, "w": 7, "m": 30, "y": 365}


def _parse_older_than(spec: str) -> timedelta:
    """Parse '30d', '2w', '6m', '1y' into a timedelta."""
    spec = spec.strip().lower()
    if not spec or spec[-1] not in _DURATION_UNITS:
        raise ValueError(f"Invalid duration spec: {spec!r}. Use Nd|Nw|Nm|Ny")
    try:
        n = int(spec[:-1])
    except ValueError as e:
        raise ValueError(f"Invalid duration spec: {spec!r}. Use Nd|Nw|Nm|Ny") from e
    if n < 0:
        raise ValueError(f"Duration must be non-negative: {spec!r}")
    return timedelta(days=n * _DURATION_UNITS[spec[-1]])


def archive_items(
    conn: sqlite3.Connection,
    sweep_ref: str,
    *,
    items: list[str] | None = None,
    where_status: str | None = None,
    older_than: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Selective entry-level archive (F5) with soft-delete semantics.

    Archived entries are excluded from `next_batch`, `get_status` totals, and the
    default `list_items` view. They are STILL matched by `add_items` for recurrence
    bumping — re-adding an archived entry un-archives it (R5 invariant).

    Args:
        items: Specific item identifiers to archive.
        where_status: Archive entries with this state (e.g., "RESOLVED").
        older_than: Duration spec ("30d", "6m") — only archive entries whose
            `processed_at` (or `last_seen` if not processed) is older than this.
        reason: Optional free-form reason stored on each archived entry.

    At least one of `items`, `where_status`, or `older_than` must be supplied —
    otherwise this would archive the whole sweep, which is what `archive_sweep` is for.
    """
    if items is None and where_status is None and older_than is None:
        raise ValueError(
            "archive_items requires at least one of: items, where_status, older_than. "
            "Use archive_sweep to archive an entire sweep."
        )

    # An EXPLICITLY EMPTY `items` short-circuits below with `archived: 0`, and
    # any `where_status`/`older_than` handed in beside it is then never read —
    # a filter silently dropped behind a success-shaped answer (CB-162). Refuse
    # exactly that combination and nothing wider: a bare `items=[]` is honest
    # ("archive this empty set" → nothing archived) and stays legal.
    #
    # `items is not None and not items` is the whole discriminator: `None` is
    # "not supplied" and `[]` is "supplied empty", and they are two different
    # calls. Truthiness cannot tell them apart, and conflating them would either
    # refuse the ordinary filter-only call or miss the defect entirely.
    #
    # Decidable from the arguments alone, so it is refused BEFORE `db.txn`
    # opens: a refusal must not take the write lock.
    if items is not None and not items and (where_status is not None or older_than is not None):
        raise ValueError(
            "items=[] selects no entries, so where_status/older_than would be silently "
            "ignored and nothing would be archived. Omit items to archive by filter, or "
            "pass the item identifiers you mean to archive."
        )

    # One transaction, opened before the lifecycle read (CB-24): `where_status` is
    # validated against the sweep's stored lifecycle and then used to select the rows
    # the bulk UPDATE archives, so a concurrent lifecycle rewrite between the two would
    # let this archive rows on a state the sweep no longer declares. Do not restore
    # `conn.commit()`.
    with db.txn(conn):
        sweep_id = _resolve_sweep(conn, sweep_ref)
        lifecycle, _terminal, _transitions = _load_sweep_lifecycle(conn, sweep_id)

        if where_status is not None and where_status not in lifecycle:
            raise ValueError(
                f"State {where_status!r} not in sweep lifecycle: {lifecycle}"
            )

        conditions = ["sweep_id = ?", "archived_at IS NULL"]
        params: list[Any] = [sweep_id]

        if items is not None:
            if not items:
                return {"sweep_id": sweep_id, "archived": 0}
            placeholders = ",".join("?" * len(items))
            conditions.append(f"item IN ({placeholders})")
            params.extend(items)

        if where_status is not None:
            conditions.append("state = ?")
            params.append(where_status)

        if older_than is not None:
            delta = _parse_older_than(older_than)
            cutoff = (datetime.now(timezone.utc) - delta).isoformat()
            # Use processed_at when available (state-changes), else last_seen, else created_at
            conditions.append(
                "COALESCE(processed_at, last_seen, created_at) < ?"
            )
            params.append(cutoff)

        where = " AND ".join(conditions)
        now = utc_now()

        cursor = conn.execute(
            f"UPDATE codesweep_items SET archived_at = ?, archive_reason = ? "
            f"WHERE {where}",
            [now, reason] + params,
        )
        archived_n = cursor.rowcount

        conn.execute(
            "UPDATE codesweep_sweeps SET updated_at = ? WHERE sweep_id = ?",
            (now, sweep_id),
        )
    return {"sweep_id": sweep_id, "archived": archived_n}


def list_items(
    conn: sqlite3.Connection,
    sweep_ref: str,
    *,
    state: str | None = None,
    tag: str | None = None,
    include_archived: bool = False,
    archived_only: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """List items in a sweep with optional filters.

    By default excludes archived. `archived_only=True` shows only archived
    entries (useful for restore workflows). Always-considered-by-recurrence
    semantics are enforced by `add_items`, not here.

    `limit` is `None` for no limit, or a non-negative integer; `0` means zero
    entries, which is what it ALREADY meant here — this site was the one of
    CB-161's three that behaved correctly on zero, because its guard was already
    `is not None`. What changed is that the value is now BOUND instead of being
    interpolated behind an `int()` cast, and a negative value is refused rather
    than silently read by SQLite as no limit at all.
    """
    limit = require_row_limit("limit", limit)
    sweep_id = _resolve_sweep(conn, sweep_ref)

    conditions = ["sweep_id = ?"]
    params: list[Any] = [sweep_id]

    if archived_only:
        conditions.append("archived_at IS NOT NULL")
    elif not include_archived:
        conditions.append("archived_at IS NULL")

    if state is not None:
        conditions.append("state = ?")
        params.append(state)

    if tag is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)"
        )
        params.append(tag)

    where = "WHERE " + " AND ".join(conditions)
    # Fixed literal + bound value. The `int()` cast is gone with the
    # interpolation: `require_row_limit` above already refused anything that is
    # not an integer, and it does so instead of COERCING one — a numeric string
    # or a `2.7` used to be quietly converted to something the caller did not
    # write. The parameter goes at the fragment's textual position, the end.
    if limit is not None:
        limit_sql = " LIMIT ?"
        params.append(limit)
    else:
        limit_sql = ""

    rows = conn.execute(
        f"SELECT item, tags, state, processed, recurrence_count, "
        f"first_seen, last_seen, position, archived_at, archive_reason "
        f"FROM codesweep_items {where} ORDER BY position{limit_sql}",
        params,
    ).fetchall()

    return {
        "sweep_id": sweep_id,
        "items": [
            {
                "item": r["item"],
                "tags": json.loads(r["tags"]),
                "state": r["state"],
                "processed": bool(r["processed"]),
                "recurrence_count": r["recurrence_count"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "position": r["position"],
                "archived_at": r["archived_at"],
                "archive_reason": r["archive_reason"],
            }
            for r in rows
        ],
    }


def list_sweeps(
    conn: sqlite3.Connection,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """List all sweeps with summary counts (excluding archived items from counts)."""
    condition = "" if include_archived else "WHERE s.status = 'active'"
    rows = conn.execute(
        f"""SELECT s.sweep_id, s.name, s.status, s.default_batch_size,
                   COUNT(CASE WHEN i.archived_at IS NULL THEN 1 END) as total,
                   SUM(CASE WHEN i.archived_at IS NULL AND i.processed = 1 THEN 1 ELSE 0 END) as processed,
                   COUNT(CASE WHEN i.archived_at IS NOT NULL THEN 1 END) as archived
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
            "archived": r["archived"] or 0,
        })
    return {"sweeps": sweeps}


from codebugs.db import register_schema, register_tool_provider, register_cli_provider  # noqa: E402

register_schema("sweep", ensure_schema)


# --- CLI handler bodies ----------------------------------------------------
#
# LOGIC ONLY. The name, the one-line help and the argument list of every verb
# below live in `sweep_surface.py`; these functions decide what a call DOES and
# describe nothing. They were closures inside `register_cli` until this change:
# a declaration file has to NAME the body it wires, and a name that lives only
# in another function's frame cannot be named from outside it. Nothing else
# moved — every local they used to capture (`db`, `sys`, `argparse`,
# `format_table`, the two comma-splitters) is a module-level name now, and the
# bodies themselves are byte-identical apart from the dedent.


def _parse_csv(value: str | None) -> list[str] | None:
    return [t.strip() for t in value.split(",")] if value else None


def _parse_tags(args: argparse.Namespace) -> list[str] | None:
    return _parse_csv(args.tags)


def _cmd_sweep_create(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    kwargs: dict = {}
    if args.name:
        kwargs["name"] = args.name
    if args.description:
        kwargs["description"] = args.description
    if args.batch_size:
        kwargs["default_batch_size"] = args.batch_size
    if args.lifecycle:
        kwargs["lifecycle"] = _parse_csv(args.lifecycle)
    if args.terminal_states:
        kwargs["terminal_states"] = _parse_csv(args.terminal_states)
    try:
        with domain_errors():
            result = create_sweep(conn, **kwargs)
            print(f"Created: {result['sweep_id']}" + (f" ({result['name']})" if result["name"] else ""))
            if result["lifecycle"] != ["pending", "done"]:
                print(f"Lifecycle: {' -> '.join(result['lifecycle'])}")
    finally:
        conn.close()


def _cmd_sweep_add(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    try:
        with domain_errors():
            result = add_items(conn, args.sweep, args.items, tags=_parse_tags(args))
            msg = f"Added {result['added']} new items"
            if result["recurrence_bumped"]:
                msg += f", bumped recurrence on {result['recurrence_bumped']}"
            print(msg + ".")
    finally:
        conn.close()


def _cmd_sweep_next(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    try:
        with domain_errors():
            result = next_batch(conn, args.sweep, limit=args.limit, tags=_parse_tags(args))
            if not result["items"]:
                # CB-210 -- see `fmt.empty_page_line`. The corpus number here is
                # `remaining` rather than `total`: it is what the non-empty
                # branch already prints, and it is what separates "there is
                # nothing left" from "you asked for nothing".
                print(
                    empty_page_line(
                        args.limit,
                        result.get("remaining", 0),
                        empty="(no unprocessed items)",
                        requested="(limit was 0, so no items were requested — {n} remaining)",
                    )
                )
                return
            data = [
                {
                    "item": i["item"],
                    "state": i["state"],
                    "rec": str(i["recurrence_count"]),
                    "tags": ",".join(i["tags"]),
                }
                for i in result["items"]
            ]
            print(format_table(data, ["item", "state", "rec", "tags"], max_widths={"item": 60}))
            print(f"\n{result['remaining']} remaining.")
    finally:
        conn.close()


def _cmd_sweep_mark(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    try:
        with domain_errors():
            # `--undo` is `store_true`, so its ABSENCE is indistinguishable from
            # "not asked for" — which is exactly the domain's `processed=None`.
            # Passing `not args.undo` here (what this did before CB-197) supplies a
            # bool on EVERY invocation, so with the new mutual-exclusion refusal a
            # plain `sweep-mark X --state done` would refuse: the handler would have
            # broken the ordinary verb while fixing the argument it drops. Sending
            # `False` only when `--undo` was typed keeps every legitimate call
            # working and routes `--state … --undo` — the one genuinely ambiguous
            # combination — into the domain refusal, so ONE rule decides for the
            # library, the CLI and MCP alike.
            result = mark_items(
                conn, args.sweep, args.items,
                processed=False if args.undo else None, state=args.state,
            )
            print(f"Marked {result['updated']} items -> state={result['state']}.")
    finally:
        conn.close()


def _cmd_sweep_status(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    try:
        with domain_errors():
            s = get_status(conn, args.sweep)
            print(f"Sweep: {s['sweep_id']}" + (f" ({s['name']})" if s["name"] else ""))
            print(f"Status: {s['status']}")
            print(f"Lifecycle: {' -> '.join(s['lifecycle'])}")
            print(f"Items:  {s['processed']}/{s['total']} processed, {s['remaining']} remaining")
            if s["archived"]:
                print(f"Archived: {s['archived']}")
            if s["by_state"]:
                print("\nBy state:")
                for state, count in s["by_state"].items():
                    print(f"  {state:20s}  {count}")
            if s["by_tag"]:
                print("\nBy tag:")
                for tag, counts in sorted(s["by_tag"].items()):
                    print(f"  {tag:20s}  {counts['processed']}/{counts['total']}")
    finally:
        conn.close()


def _cmd_sweep_archive(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    try:
        with domain_errors():
            result = archive_sweep(conn, args.sweep)
            print(f"Archived: {result['sweep_id']}")
    finally:
        conn.close()


def _cmd_sweep_archive_items(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    try:
        with domain_errors():
            result = archive_items(
                conn, args.sweep,
                items=args.items or None,
                where_status=args.state,
                older_than=args.older_than,
                reason=args.reason,
            )
            print(f"Archived {result['archived']} entries in {result['sweep_id']}.")
    finally:
        conn.close()


def _cmd_sweep_list_items(args: argparse.Namespace) -> None:
    from codebugs.cli import domain_errors

    conn = db.connect()
    try:
        with domain_errors():
            result = list_items(
                conn, args.sweep,
                state=args.state, tag=args.tag,
                include_archived=args.all,
                archived_only=args.archived_only,
                limit=args.limit,
            )
            if not result["items"]:
                print("(no items)")
                return
            data = [
                {
                    "item": i["item"],
                    "state": i["state"],
                    "rec": str(i["recurrence_count"]),
                    "archived": "y" if i["archived_at"] else "",
                    "tags": ",".join(i["tags"]),
                }
                for i in result["items"]
            ]
            print(format_table(
                data,
                ["item", "state", "rec", "archived", "tags"],
                max_widths={"item": 60},
            ))
    finally:
        conn.close()


def _cmd_sweep_list(args: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        result = list_sweeps(conn, include_archived=args.all)
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
                "archived": str(s["archived"]),
            }
            for s in result["sweeps"]
        ]
        print(format_table(data, ["sweep_id", "name", "status", "progress", "remaining", "archived"]))
    finally:
        conn.close()


# --- Registration ----------------------------------------------------------
#
# The declarations are imported at CALL time, not at module load: the
# declaration file names the handlers above, so importing it at the top of this
# module would be a cycle. By the time either function below runs, this module
# is fully initialised.


def register_tools(mcp, conn_factory) -> None:
    """Register sweep batch-iteration tools on the given MCP server."""
    from codebugs.sweep_surface import SURFACE

    surfacegen.emit_tools(mcp, conn_factory, SURFACE)


register_tool_provider("sweep", register_tools)


def register_cli(sub, commands) -> None:
    """Register sweep CLI subcommands."""
    from codebugs.sweep_surface import SURFACE

    surfacegen.emit_cli(sub, commands, SURFACE)


register_cli_provider("sweep", register_cli)
