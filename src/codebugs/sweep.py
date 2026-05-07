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

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from codebugs.types import utc_now


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
    """
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
    conn.commit()
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
    """
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
    processed: bool = True,
    state: str | None = None,
) -> dict[str, Any]:
    """Mark items by transitioning their state.

    Two modes:
    - `state="<name>"`: explicit state transition. Validated against the sweep's
      lifecycle, and against `transitions` DAG if declared.
    - `processed=True/False` (legacy): True transitions to the first terminal
      state, False transitions to the first non-terminal state.

    `processed` and `processed_at` are kept in sync with `state IN terminal_states`.
    Archived items raise — un-archive first via re-add or directly.
    """
    sweep_id = _resolve_sweep(conn, sweep_ref)
    lifecycle, terminal_states, transitions = _load_sweep_lifecycle(conn, sweep_id)

    if state is not None:
        if state not in lifecycle:
            raise ValueError(
                f"State {state!r} not in sweep lifecycle: {lifecycle}"
            )
        target_state = state
    else:
        if processed:
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
    conn.commit()
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
    """Archive an entire sweep (sweep-level archive — distinct from archive_items)."""
    sweep_id = _resolve_sweep(conn, sweep_ref)
    conn.execute(
        "UPDATE codesweep_sweeps SET status = 'archived', updated_at = ? WHERE sweep_id = ?",
        (utc_now(), sweep_id),
    )
    conn.commit()
    return {"sweep_id": sweep_id, "status": "archived"}


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
    conn.commit()
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
    """
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
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""

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


def register_tools(mcp, conn_factory) -> None:
    """Register sweep batch-iteration tools on the given MCP server."""

    @mcp.tool()
    def codesweep_create(
        name: str | None = None,
        description: str = "",
        default_batch_size: int = 10,
        lifecycle: list[str] | None = None,
        terminal_states: list[str] | None = None,
        transitions: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        """Create a new sweep for batch iteration over items.

        Args:
            name: Optional human-readable name (must be unique)
            description: What this sweep is for
            default_batch_size: Default items per batch (default: 10)
            lifecycle: Ordered list of allowed states (default ["pending","done"]).
                For retro-style workflows: ["DETECTED","CONFIRMED","ESCALATED",
                "POSTPONED","RESOLVED","DROPPED"].
            terminal_states: States that count as "processed" (default ["done"]).
            transitions: Optional dict[state, list[allowed_next_state]] for
                DAG-constrained lifecycles. None = unconstrained transitions.
        """
        with conn_factory() as conn:
            return create_sweep(
                conn, name=name, description=description,
                default_batch_size=default_batch_size,
                lifecycle=lifecycle,
                terminal_states=terminal_states,
                transitions=transitions,
            )

    @mcp.tool()
    def codesweep_add(
        sweep_ref: str,
        items: list[str],
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add items to a sweep. Atomic upsert: existing items have their
        `recurrence_count` bumped instead of being silently skipped, their
        `last_seen` updated, and their archive flag cleared (R5: re-detected
        archived items un-archive automatically).

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            items: Item identifiers to add
            tags: Optional tags applied to this batch (overwrite on bump)

        Returns:
            {sweep_id, added, recurrence_bumped, duplicates_skipped (alias)}
        """
        with conn_factory() as conn:
            return add_items(conn, sweep_ref, items, tags=tags)

    @mcp.tool()
    def codesweep_next(
        sweep_ref: str,
        limit: int | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get next batch of unprocessed (non-terminal, non-archived) items in
        insertion order.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            limit: Batch size (overrides sweep default)
            tags: Filter to items matching any of these tags
        """
        with conn_factory() as conn:
            return next_batch(conn, sweep_ref, limit=limit, tags=tags)

    @mcp.tool()
    def codesweep_mark(
        sweep_ref: str,
        items: list[str],
        processed: bool = True,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Mark items by state transition.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            items: Item identifiers to mark
            processed: Legacy mode — True maps to first terminal state, False
                to first non-terminal state. Ignored if `state` is set.
            state: Explicit target state. Validated against the sweep's
                `lifecycle` and `transitions` DAG (if declared).
        """
        with conn_factory() as conn:
            return mark_items(
                conn, sweep_ref, items, processed=processed, state=state
            )

    @mcp.tool()
    def codesweep_status(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Sweep overview — total/processed/remaining/archived counts, per-tag and
        per-state breakdowns. Archived entries are excluded from total/processed/
        remaining and reported separately as `archived`.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        with conn_factory() as conn:
            return get_status(conn, sweep_ref)

    @mcp.tool()
    def codesweep_archive(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Archive a sweep. Archived sweeps are excluded from codesweep_list by default.

        For entry-level archive, use `codesweep_archive_items`.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        with conn_factory() as conn:
            return archive_sweep(conn, sweep_ref)

    @mcp.tool()
    def codesweep_archive_items(
        sweep_ref: str,
        items: list[str] | None = None,
        where_status: str | None = None,
        older_than: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Selectively archive entries within a sweep (soft-delete).

        Archived entries are excluded from `codesweep_next`, `codesweep_status`
        totals, and default `codesweep_list_items`. They remain matchable by
        `codesweep_add` for recurrence detection — re-adding un-archives them
        with `recurrence_count` carried forward (R5 invariant).

        At least one filter is required.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            items: Specific item identifiers to archive
            where_status: Archive entries currently in this state
            older_than: Duration spec — '30d', '2w', '6m', '1y'. Compares against
                the entry's last activity timestamp.
            reason: Free-form reason recorded on each archived entry
        """
        with conn_factory() as conn:
            return archive_items(
                conn, sweep_ref,
                items=items, where_status=where_status,
                older_than=older_than, reason=reason,
            )

    @mcp.tool()
    def codesweep_list_items(
        sweep_ref: str,
        state: str | None = None,
        tag: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List items in a sweep with optional filters.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            state: Filter to a specific state
            tag: Filter to items having this tag
            include_archived: Include archived entries alongside live ones
            archived_only: Show only archived entries (overrides include_archived)
            limit: Max number of entries to return
        """
        with conn_factory() as conn:
            return list_items(
                conn, sweep_ref,
                state=state, tag=tag,
                include_archived=include_archived,
                archived_only=archived_only,
                limit=limit,
            )

    @mcp.tool()
    def codesweep_list(
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List all sweeps with summary counts.

        Args:
            include_archived: Include archived sweeps (default: false)
        """
        with conn_factory() as conn:
            return list_sweeps(conn, include_archived=include_archived)


register_tool_provider("sweep", register_tools)


# --- CLI ---

def register_cli(sub, commands) -> None:
    """Register sweep CLI subcommands."""
    import argparse
    import sys
    from codebugs import db
    from codebugs.fmt import format_table

    def _parse_csv(value: str | None) -> list[str] | None:
        return [t.strip() for t in value.split(",")] if value else None

    def _parse_tags(args: argparse.Namespace) -> list[str] | None:
        return _parse_csv(args.tags)

    def _cmd_sweep_create(args: argparse.Namespace) -> None:
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
            result = create_sweep(conn, **kwargs)
            print(f"Created: {result['sweep_id']}" + (f" ({result['name']})" if result["name"] else ""))
            if result["lifecycle"] != ["pending", "done"]:
                print(f"Lifecycle: {' -> '.join(result['lifecycle'])}")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_add(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = add_items(conn, args.sweep, args.items, tags=_parse_tags(args))
            msg = f"Added {result['added']} new items"
            if result["recurrence_bumped"]:
                msg += f", bumped recurrence on {result['recurrence_bumped']}"
            print(msg + ".")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_next(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = next_batch(conn, args.sweep, limit=args.limit, tags=_parse_tags(args))
            if not result["items"]:
                print("(no unprocessed items)")
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
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_mark(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = mark_items(
                conn, args.sweep, args.items,
                processed=not args.undo, state=args.state,
            )
            print(f"Marked {result['updated']} items -> state={result['state']}.")
        except (ValueError, KeyError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_status(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
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
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_archive(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = archive_sweep(conn, args.sweep)
            print(f"Archived: {result['sweep_id']}")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_archive_items(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = archive_items(
                conn, args.sweep,
                items=args.items or None,
                where_status=args.state,
                older_than=args.older_than,
                reason=args.reason,
            )
            print(f"Archived {result['archived']} entries in {result['sweep_id']}.")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_list_items(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
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
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
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

    p = sub.add_parser("sweep-create", help="Create a new sweep")
    p.add_argument("--name", help="Optional sweep name")
    p.add_argument("--description", help="Sweep description")
    p.add_argument("--batch-size", type=int, help="Default batch size (default: 10)")
    p.add_argument("--lifecycle", help="Comma-separated lifecycle states (default: pending,done)")
    p.add_argument("--terminal-states", help="Comma-separated terminal states (default: done)")

    p = sub.add_parser("sweep-add", help="Add items to a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to add")
    p.add_argument("--tags", help="Comma-separated tags")

    p = sub.add_parser("sweep-next", help="Get next batch of unprocessed items")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("--limit", type=int, help="Batch size override")
    p.add_argument("--tags", help="Filter by tags (comma-separated)")

    p = sub.add_parser("sweep-mark", help="Mark items as processed or transition state")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to mark")
    p.add_argument("--undo", action="store_true", help="Map to first non-terminal state")
    p.add_argument("--state", help="Explicit target state (validated against lifecycle)")

    p = sub.add_parser("sweep-status", help="Sweep progress overview")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-archive", help="Archive an entire sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-archive-items", help="Selectively archive entries (soft-delete)")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="*", help="Specific items to archive (optional)")
    p.add_argument("--state", help="Archive entries in this state")
    p.add_argument("--older-than", help="Archive entries older than (e.g. 30d, 6m)")
    p.add_argument("--reason", help="Free-form reason recorded on archived entries")

    p = sub.add_parser("sweep-list-items", help="List entries in a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("--state", help="Filter by state")
    p.add_argument("--tag", help="Filter by tag")
    p.add_argument("--all", action="store_true", help="Include archived entries")
    p.add_argument("--archived-only", action="store_true", help="Show only archived entries")
    p.add_argument("--limit", type=int, help="Max entries to return")

    p = sub.add_parser("sweep-list", help="List sweeps")
    p.add_argument("--all", action="store_true", help="Include archived sweeps")

    commands.update({
        "sweep-create": _cmd_sweep_create,
        "sweep-add": _cmd_sweep_add,
        "sweep-next": _cmd_sweep_next,
        "sweep-mark": _cmd_sweep_mark,
        "sweep-status": _cmd_sweep_status,
        "sweep-archive": _cmd_sweep_archive,
        "sweep-archive-items": _cmd_sweep_archive_items,
        "sweep-list-items": _cmd_sweep_list_items,
        "sweep-list": _cmd_sweep_list,
    })


register_cli_provider("sweep", register_cli)
