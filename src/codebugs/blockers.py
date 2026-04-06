"""Database layer — dependency/blocker tracking for codebugs."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from codebugs.types import ENTITY_FINDING, ENTITY_REQUIREMENT, ENTITY_TABLES, TERMINAL_STATUSES, TRIGGER_TYPES


BLOCKERS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS blockers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    item_type TEXT NOT NULL
        CHECK(item_type IN ('finding', 'requirement')),
    blocked_by TEXT,
    blocked_by_type TEXT
        CHECK(blocked_by_type IN ('finding', 'requirement') OR blocked_by_type IS NULL),
    reason TEXT NOT NULL,
    trigger_type TEXT NOT NULL
        CHECK(trigger_type IN ('entity_resolved', 'date', 'manual')),
    trigger_at TEXT,
    resolved_at TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blockers_item ON blockers(item_id, item_type);
CREATE INDEX IF NOT EXISTS idx_blockers_blocked_by ON blockers(blocked_by);
CREATE INDEX IF NOT EXISTS idx_blockers_trigger ON blockers(trigger_type, trigger_at);
"""



def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the blockers table if it doesn't exist."""
    for stmt in BLOCKERS_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def _detect_entity_type(entity_id: str) -> str:
    """Infer entity type from ID prefix."""
    if re.match(r"^CB-\d+", entity_id):
        return ENTITY_FINDING
    if re.match(r"^N?FR-\d+", entity_id):
        return ENTITY_REQUIREMENT
    raise ValueError(
        f"Unknown entity ID format: {entity_id}. Expected CB-N, FR-N, or NFR-N."
    )


def _get_entity_field(
    conn: sqlite3.Connection, entity_id: str, entity_type: str, field: str
) -> Any | None:
    """Look up a single field from a finding or requirement by ID."""
    table = ENTITY_TABLES[entity_type]
    row = conn.execute(
        f"SELECT {field} FROM {table} WHERE id = ?", (entity_id,)
    ).fetchone()
    return row[field] if row else None


def _entity_exists(conn: sqlite3.Connection, entity_id: str, entity_type: str) -> bool:
    return _get_entity_field(conn, entity_id, entity_type, "id") is not None


def _get_entity_status(conn: sqlite3.Connection, entity_id: str, entity_type: str) -> str | None:
    return _get_entity_field(conn, entity_id, entity_type, "status")


def _get_entity_description(conn: sqlite3.Connection, entity_id: str, entity_type: str) -> str | None:
    return _get_entity_field(conn, entity_id, entity_type, "description")


def _normalize_trigger_at(value: str) -> str:
    """Normalize a date/datetime string to YYYY-MM-DDTHH:MM:SSZ (UTC)."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"Invalid date format: {value}. Expected ISO 8601 (e.g., 2026-04-10 or 2026-04-10T14:30:00Z)."
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def is_blocker_satisfied(conn: sqlite3.Connection, blocker: dict[str, Any]) -> bool:
    """Evaluate whether a blocker's condition is currently met."""
    if blocker["cancelled_at"]:
        return True
    if blocker["trigger_type"] == "entity_resolved":
        status = _get_entity_status(conn, blocker["blocked_by"], blocker["blocked_by_type"])
        if status is None:
            return False
        return status in TERMINAL_STATUSES[blocker["blocked_by_type"]]
    if blocker["trigger_type"] == "date":
        return blocker["trigger_at"] <= _now()
    if blocker["trigger_type"] == "manual":
        return blocker["resolved_at"] is not None
    return False


def _evaluate_blocker(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Convert a blocker row to a dict with computed satisfaction state."""
    d = _row_to_dict(row)
    d["is_cancelled"] = d["cancelled_at"] is not None
    d["is_satisfied"] = is_blocker_satisfied(conn, d)
    d["is_active"] = not d["is_cancelled"] and not d["is_satisfied"]
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_blocker(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    reason: str,
    blocked_by: str | None = None,
    trigger_type: str | None = None,
    trigger_at: str | None = None,
) -> dict[str, Any]:
    """Add a blocker to defer an item."""
    item_type = _detect_entity_type(item_id)
    if not _entity_exists(conn, item_id, item_type):
        raise KeyError(f"Entity not found: {item_id}")

    # Defaults
    if trigger_type is None:
        trigger_type = "entity_resolved" if blocked_by else "manual"

    if trigger_type not in TRIGGER_TYPES:
        raise ValueError(
            f"Invalid trigger_type: {trigger_type}. Must be one of {TRIGGER_TYPES}"
        )

    # Validate blocked_by
    blocked_by_type = None
    if blocked_by:
        blocked_by_type = _detect_entity_type(blocked_by)
        if not _entity_exists(conn, blocked_by, blocked_by_type):
            raise KeyError(f"Blocking entity not found: {blocked_by}")
        if item_id == blocked_by:
            raise ValueError("An item cannot block itself.")

    if trigger_type == "entity_resolved" and not blocked_by:
        raise ValueError("blocked_by is required when trigger_type is 'entity_resolved'.")

    if trigger_type == "date":
        if not trigger_at:
            raise ValueError("trigger_at is required when trigger_type is 'date'.")
        trigger_at = _normalize_trigger_at(trigger_at)

    # Duplicate check
    if blocked_by and trigger_type == "entity_resolved":
        dup = conn.execute(
            """SELECT 1 FROM blockers
               WHERE item_id = ? AND blocked_by = ? AND trigger_type = 'entity_resolved'
               AND cancelled_at IS NULL""",
            (item_id, blocked_by),
        ).fetchone()
        if dup:
            raise ValueError(
                f"Duplicate blocker: {item_id} is already blocked by {blocked_by}."
            )
    elif trigger_type == "date" and trigger_at:
        dup = conn.execute(
            """SELECT 1 FROM blockers
               WHERE item_id = ? AND trigger_type = 'date' AND trigger_at = ?
               AND cancelled_at IS NULL""",
            (item_id, trigger_at),
        ).fetchone()
        if dup:
            raise ValueError(
                f"Duplicate blocker: {item_id} already has a date trigger for {trigger_at}."
            )

    now = _now()
    conn.execute(
        """INSERT INTO blockers
           (item_id, item_type, blocked_by, blocked_by_type, reason,
            trigger_type, trigger_at, resolved_at, cancelled_at,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)""",
        (item_id, item_type, blocked_by, blocked_by_type, reason,
         trigger_type, trigger_at, now, now),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM blockers WHERE id = last_insert_rowid()"
    ).fetchone()
    result = _evaluate_blocker(conn, row)
    result["item_description"] = _get_entity_description(conn, item_id, item_type)
    return result


def query_blockers(
    conn: sqlite3.Connection,
    *,
    item_id: str | None = None,
    blocked_by: str | None = None,
    trigger_type: str | None = None,
    active_only: bool = True,
) -> dict[str, Any]:
    """List blockers with filters."""
    conditions: list[str] = []
    params: list[Any] = []

    if item_id:
        conditions.append("item_id = ?")
        params.append(item_id)
    if blocked_by:
        conditions.append("blocked_by = ?")
        params.append(blocked_by)
    if trigger_type:
        if trigger_type not in TRIGGER_TYPES:
            raise ValueError(
                f"Invalid trigger_type: {trigger_type}. Must be one of {TRIGGER_TYPES}"
            )
        conditions.append("trigger_type = ?")
        params.append(trigger_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM blockers {where} ORDER BY created_at DESC", params
    ).fetchall()

    blockers = []
    for row in rows:
        b = _evaluate_blocker(conn, row)
        if active_only and not b["is_active"]:
            continue
        b["item_description"] = _get_entity_description(conn, b["item_id"], b["item_type"])
        if b["blocked_by"]:
            b["blocked_by_description"] = _get_entity_description(
                conn, b["blocked_by"], b["blocked_by_type"]
            )
            b["blocked_by_status"] = _get_entity_status(
                conn, b["blocked_by"], b["blocked_by_type"]
            )
        blockers.append(b)

    return {"blockers": blockers, "total": len(blockers)}


def check_blockers(conn: sqlite3.Connection) -> dict[str, Any]:
    """Scan for currently actionable items."""
    rows = conn.execute(
        "SELECT * FROM blockers WHERE cancelled_at IS NULL ORDER BY item_id"
    ).fetchall()

    now = _now()
    items: dict[str, dict[str, Any]] = {}
    overdue = []

    for row in rows:
        b = _evaluate_blocker(conn, row)
        key = b["item_id"]
        if key not in items:
            items[key] = {
                "item_id": key,
                "item_type": b["item_type"],
                "description": _get_entity_description(conn, key, b["item_type"]),
                "satisfied": [],
                "remaining": [],
            }
        if b["is_satisfied"]:
            items[key]["satisfied"].append(b)
        else:
            items[key]["remaining"].append(b)

        if b["trigger_type"] == "date" and b["trigger_at"] and b["trigger_at"] <= now:
            overdue.append({
                "id": b["id"],
                "item_id": b["item_id"],
                "trigger_at": b["trigger_at"],
                "reason": b["reason"],
            })

    actionable = []
    partially_unblocked = []
    for item in items.values():
        if not item["remaining"]:
            actionable.append({
                "item_id": item["item_id"],
                "item_type": item["item_type"],
                "description": item["description"],
                "satisfied_blockers": item["satisfied"],
            })
        elif item["satisfied"]:
            partially_unblocked.append({
                "item_id": item["item_id"],
                "item_type": item["item_type"],
                "description": item["description"],
                "remaining": len(item["remaining"]),
                "satisfied": len(item["satisfied"]),
                "remaining_blockers": item["remaining"],
            })

    return {
        "actionable": actionable,
        "partially_unblocked": partially_unblocked,
        "overdue_date_triggers": overdue,
    }


def resolve_blocker(
    conn: sqlite3.Connection,
    *,
    blocker_id: int,
    action: str,
) -> dict[str, Any]:
    """Cancel or manually resolve a blocker."""
    row = conn.execute(
        "SELECT * FROM blockers WHERE id = ?", (blocker_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Blocker not found: {blocker_id}")

    b = _row_to_dict(row)
    if b["cancelled_at"]:
        raise ValueError(f"Blocker {blocker_id} is already cancelled.")

    now = _now()

    if action == "cancel":
        conn.execute(
            "UPDATE blockers SET cancelled_at = ?, updated_at = ? WHERE id = ?",
            (now, now, blocker_id),
        )
    elif action == "resolve":
        if b["trigger_type"] != "manual":
            raise ValueError(
                f"'resolve' is only valid for manual triggers (this is '{b['trigger_type']}')."
            )
        if b["resolved_at"]:
            raise ValueError(f"Blocker {blocker_id} is already resolved.")
        conn.execute(
            "UPDATE blockers SET resolved_at = ?, updated_at = ? WHERE id = ?",
            (now, now, blocker_id),
        )
    else:
        raise ValueError(f"Invalid action: {action}. Must be 'cancel' or 'resolve'.")

    conn.commit()

    updated = _evaluate_blocker(
        conn, conn.execute("SELECT * FROM blockers WHERE id = ?", (blocker_id,)).fetchone()
    )

    # Remaining active blockers for the same item
    remaining_rows = conn.execute(
        "SELECT * FROM blockers WHERE item_id = ? AND id != ? AND cancelled_at IS NULL",
        (b["item_id"], blocker_id),
    ).fetchall()
    remaining = [r for r in (_evaluate_blocker(conn, rr) for rr in remaining_rows) if r["is_active"]]

    return {
        "blocker": updated,
        "remaining_active_blockers": remaining,
        "remaining_count": len(remaining),
    }


# ---------------------------------------------------------------------------
# Helpers for server.py integration
# ---------------------------------------------------------------------------


def get_unblocked_by(
    conn: sqlite3.Connection, entity_id: str, entity_type: str
) -> list[dict[str, Any]]:
    """Find items that are unblocked by resolving the given entity."""
    rows = conn.execute(
        """SELECT * FROM blockers
           WHERE blocked_by = ? AND blocked_by_type = ? AND cancelled_at IS NULL""",
        (entity_id, entity_type),
    ).fetchall()

    if not rows:
        return []

    # Group by item to check if ALL blockers for an item are satisfied
    items: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        b = _evaluate_blocker(conn, row)
        items.setdefault(b["item_id"], []).append(b)

    results = []
    for item_id, blockers_for_entity in items.items():
        # Check all blockers for this item (not just those for this entity)
        all_rows = conn.execute(
            "SELECT * FROM blockers WHERE item_id = ? AND cancelled_at IS NULL",
            (item_id,),
        ).fetchall()
        all_active = [
            b for b in (_evaluate_blocker(conn, r) for r in all_rows) if b["is_active"]
        ]

        item_type = blockers_for_entity[0]["item_type"]
        results.append({
            "item_id": item_id,
            "item_type": item_type,
            "description": _get_entity_description(conn, item_id, item_type),
            "reason": blockers_for_entity[0]["reason"],
            "all_blockers_satisfied": len(all_active) == 0,
            "remaining_blockers": len(all_active),
        })

    return results


def _get_active_blockers_by_type(
    conn: sqlite3.Connection, entity_type: str
) -> list[dict[str, Any]]:
    """Fetch and evaluate all non-cancelled blockers for an entity type."""
    rows = conn.execute(
        "SELECT * FROM blockers WHERE item_type = ? AND cancelled_at IS NULL",
        (entity_type,),
    ).fetchall()
    return [_evaluate_blocker(conn, row) for row in rows]


def query_deferred_entities(
    conn: sqlite3.Connection,
    entity_type: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Query entities that have active blockers, with blocker counts.

    Encapsulates the SQL + serialization so server.py doesn't need to reach
    into db._row_to_dict or reqs._row_to_dict.
    """
    evaluated = _get_active_blockers_by_type(conn, entity_type)

    # Build blocker counts per item from already-evaluated data
    active_counts: dict[str, int] = {}
    for b in evaluated:
        if b["is_active"]:
            active_counts[b["item_id"]] = active_counts.get(b["item_id"], 0) + 1

    if not active_counts:
        key = "findings" if entity_type == ENTITY_FINDING else "requirements"
        return {"grouped": False, "total": 0, "limit": limit, "offset": offset, key: []}

    table = ENTITY_TABLES[entity_type]
    sort_col = "severity" if entity_type == ENTITY_FINDING else "priority"
    key = "findings" if entity_type == ENTITY_FINDING else "requirements"

    ids_list = sorted(active_counts)
    placeholders = ",".join("?" for _ in ids_list)
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY {sort_col}, created_at DESC LIMIT ? OFFSET ?",
        ids_list + [limit, offset],
    ).fetchall()

    # Serialize rows using the appropriate module's pattern
    if entity_type == ENTITY_FINDING:
        from codebugs import db
        entities = [db._row_to_dict(r) for r in rows]
    else:
        from codebugs import reqs
        entities = [reqs._row_to_dict(r) for r in rows]

    for e in entities:
        e["blocker_count"] = active_counts.get(e["id"], 0)

    return {"grouped": False, "total": len(ids_list), "limit": limit, "offset": offset, key: entities}


def get_deferred_item_ids(
    conn: sqlite3.Connection, entity_type: str
) -> set[str]:
    """Return set of item IDs that have active blockers of given entity type."""
    return {b["item_id"] for b in _get_active_blockers_by_type(conn, entity_type) if b["is_active"]}


def get_deferred_counts(
    conn: sqlite3.Connection, entity_type: str
) -> dict[str, int]:
    """Return deferred/overdue/unblocked counts for an entity type."""
    evaluated = _get_active_blockers_by_type(conn, entity_type)

    now = _now()
    items: dict[str, list[dict[str, Any]]] = {}
    for b in evaluated:
        items.setdefault(b["item_id"], []).append(b)

    deferred_count = 0
    currently_unblocked_count = 0
    overdue_items: set[str] = set()

    for item_id, item_blockers in items.items():
        active = [b for b in item_blockers if b["is_active"]]
        if active:
            deferred_count += 1
            for b in active:
                if b["trigger_type"] == "date" and b["trigger_at"] and b["trigger_at"] <= now:
                    overdue_items.add(item_id)
        else:
            currently_unblocked_count += 1

    return {
        "deferred_count": deferred_count,
        "overdue_count": len(overdue_items),
        "currently_unblocked_count": currently_unblocked_count,
    }


from codebugs.db import register_schema, register_tool_provider  # noqa: E402

register_schema("blockers", ensure_schema, depends_on=("db", "reqs"))


def register_tools(mcp, conn_factory) -> None:
    """Register blocker/dependency tools on the given MCP server."""

    @mcp.tool()
    def blockers_add(
        item_id: str,
        reason: str,
        blocked_by: str | None = None,
        trigger_type: str | None = None,
        trigger_at: str | None = None,
    ) -> dict[str, Any]:
        """Defer an item by adding a blocker.

        Args:
            item_id: The blocked entity (e.g. "CB-5", "FR-012")
            reason: Why it's blocked
            blocked_by: Dependency entity (e.g. "CB-3"). Required for entity_resolved triggers.
            trigger_type: entity_resolved, date, or manual.
                          Defaults to entity_resolved if blocked_by provided, manual otherwise.
            trigger_at: Date/datetime for date triggers (e.g. "2026-04-10"). Normalized to UTC.
        """
        with conn_factory() as conn:
            return add_blocker(
                conn, item_id=item_id, reason=reason, blocked_by=blocked_by,
                trigger_type=trigger_type, trigger_at=trigger_at,
            )

    @mcp.tool()
    def blockers_query(
        item_id: str | None = None,
        blocked_by: str | None = None,
        trigger_type: str | None = None,
        active_only: bool = True,
    ) -> dict[str, Any]:
        """List blockers with filters. Each result includes computed satisfaction state.

        Args:
            item_id: Filter by blocked item (e.g. "CB-5")
            blocked_by: Filter by dependency ("what does CB-3 unblock?")
            trigger_type: Filter by trigger type (entity_resolved, date, manual)
            active_only: Only unsatisfied, uncancelled blockers (default: true)
        """
        with conn_factory() as conn:
            return query_blockers(
                conn, item_id=item_id, blocked_by=blocked_by,
                trigger_type=trigger_type, active_only=active_only,
            )

    @mcp.tool()
    def blockers_check() -> dict[str, Any]:
        """Scan for currently actionable items — items whose blockers are all satisfied.

        Returns actionable items (all blockers met), partially unblocked items
        (some blockers met), and overdue date triggers.
        """
        with conn_factory() as conn:
            return check_blockers(conn)

    @mcp.tool()
    def blockers_resolve(
        blocker_id: int,
        action: str,
    ) -> dict[str, Any]:
        """Cancel or manually resolve a blocker.

        Args:
            blocker_id: The blocker row ID
            action: 'cancel' (any trigger type) or 'resolve' (manual triggers only)
        """
        with conn_factory() as conn:
            return resolve_blocker(conn, blocker_id=blocker_id, action=action)


register_tool_provider("blockers", register_tools)
