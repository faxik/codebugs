"""Foundation: milestone + item + audit CRUD."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from codebugs.types import utc_now

from codebugs.milestones._schema import (
    ITEM_SIZES,
    ITEM_STATUSES,
    MILESTONE_ITEM_TERMINAL,
    MILESTONE_KINDS,
    MILESTONE_STATES,
)
from codebugs.milestones._spine import (
    _audit,
    _get_item_by_ref,
    _get_milestone,
    _items_with_active_blockers,
    _milestone_exists,
    _row_to_audit,
    _row_to_item,
    _row_to_milestone,
    _validate_item_ref,
)


def create_milestone(
    conn: sqlite3.Connection,
    *,
    id: str,
    kind: str,
    description: str,
    target_date: str | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Create a new milestone (release or stream)."""
    if kind not in MILESTONE_KINDS:
        raise ValueError(f"Invalid kind: {kind!r}. Must be one of {MILESTONE_KINDS}")
    if _milestone_exists(conn, id):
        raise ValueError(f"Milestone already exists: {id}")
    now = utc_now()
    conn.execute(
        """INSERT INTO milestones (id, kind, state, target_date, description, created_at)
           VALUES (?, ?, 'open', ?, ?, ?)""",
        (id, kind, target_date, description, now),
    )
    _audit(conn, milestone_id=id, item_ref=None, actor=actor, action="create",
           from_state=None, to_state="open", reason="")
    conn.commit()
    return _get_milestone(conn, id)


def update_milestone(
    conn: sqlite3.Connection,
    *,
    id: str,
    description: str | None = None,
    target_date: str | None = None,
    state: str | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Update mutable fields of a milestone. id/kind/created_at are immutable."""
    current = _get_milestone(conn, id)
    updates: list[str] = []
    params: list[Any] = []
    from_state = current["state"]
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if target_date is not None:
        updates.append("target_date = ?")
        params.append(target_date)
    if state is not None:
        if state not in MILESTONE_STATES:
            raise ValueError(f"Invalid state: {state!r}. Must be one of {MILESTONE_STATES}")
        updates.append("state = ?")
        params.append(state)
    if not updates:
        return current
    params.append(id)
    conn.execute(f"UPDATE milestones SET {', '.join(updates)} WHERE id = ?", params)
    _audit(conn, milestone_id=id, item_ref=None, actor=actor, action="update",
           from_state=from_state, to_state=state, reason="")
    conn.commit()
    return _get_milestone(conn, id)


def list_milestones(
    conn: sqlite3.Connection,
    *,
    kind: str | None = None,
    state: str | None = None,
) -> list[dict[str, Any]]:
    """List milestones with optional filters."""
    conditions: list[str] = []
    params: list[Any] = []
    if kind:
        conditions.append("kind = ?")
        params.append(kind)
    if state:
        conditions.append("state = ?")
        params.append(state)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM milestones {where} ORDER BY kind, id", params
    ).fetchall()
    return [_row_to_milestone(r) for r in rows]


def get_milestone_status(conn: sqlite3.Connection, *, id: str) -> dict[str, Any]:
    """Detailed status rollup for one milestone: counts by status / size,
    blockers, branch_only items, target-date countdown."""
    milestone = _get_milestone(conn, id)

    rows = conn.execute(
        "SELECT * FROM milestone_items WHERE milestone_id = ?", (id,)
    ).fetchall()
    items = [_row_to_item(r) for r in rows]

    by_status: dict[str, int] = {s: 0 for s in ITEM_STATUSES}
    by_size: dict[str, int] = {s: 0 for s in ITEM_SIZES}
    branch_only_items: list[str] = []
    for it in items:
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
        by_size[it["size"]] = by_size.get(it["size"], 0) + 1
        if it["branch_only"]:
            branch_only_items.append(it["item_ref"])

    blocked_items = _items_with_active_blockers(conn, items)

    target = milestone.get("target_date")
    days_to_target: int | None = None
    if target:
        try:
            target_d = date.fromisoformat(target)
            today = datetime.now(timezone.utc).date()
            days_to_target = (target_d - today).days
        except ValueError:
            days_to_target = None

    return {
        "milestone": milestone,
        "total_items": len(items),
        "by_status": by_status,
        "by_size": by_size,
        "branch_only_items": branch_only_items,
        "blocked_items": blocked_items,
        "open_items": by_status.get("open", 0) + by_status.get("in_progress", 0),
        "done_items": by_status.get("done", 0),
        "days_to_target": days_to_target,
    }


def add_milestone_item(
    conn: sqlite3.Connection,
    *,
    milestone_id: str,
    item_kind: str,
    item_ref: str,
    size: str = "small",
    priority: int = 100,
    acceptance: str = "",
    meta: dict[str, Any] | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Attach a (bug | requirement | external) reference to a milestone."""
    if not _milestone_exists(conn, milestone_id):
        raise KeyError(f"Milestone not found: {milestone_id}")
    if size not in ITEM_SIZES:
        raise ValueError(f"Invalid size: {size!r}. Must be one of {ITEM_SIZES}")
    if size == "large" and not acceptance.strip():
        raise ValueError("acceptance is required for size='large'")
    _validate_item_ref(conn, item_kind, item_ref)

    existing = conn.execute(
        """SELECT id FROM milestone_items
           WHERE milestone_id = ? AND item_kind = ? AND item_ref = ?""",
        (milestone_id, item_kind, item_ref),
    ).fetchone()
    if existing:
        raise ValueError(
            f"{item_ref} is already attached to {milestone_id} (item_kind={item_kind})"
        )

    now = utc_now()
    meta_json = json.dumps(meta or {})
    conn.execute(
        """INSERT INTO milestone_items
           (milestone_id, item_kind, item_ref, size, priority, status,
            acceptance, meta_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
        (milestone_id, item_kind, item_ref, size, priority,
         acceptance, meta_json, now, now),
    )
    _audit(
        conn,
        milestone_id=milestone_id,
        item_ref=item_ref,
        actor=actor,
        action="create",
        from_state=None,
        to_state="open",
        reason="",
    )
    conn.commit()
    return _get_item_by_ref(conn, item_ref)


def move_milestone_item(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    to_milestone: str,
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """Move an item to a different milestone. Errors if the destination already
    has an item with the same (item_kind, item_ref)."""
    current = _get_item_by_ref(conn, item_ref)
    if current["milestone_id"] == to_milestone:
        return current
    if not _milestone_exists(conn, to_milestone):
        raise KeyError(f"Destination milestone not found: {to_milestone}")
    conflict = conn.execute(
        """SELECT id FROM milestone_items
           WHERE milestone_id = ? AND item_kind = ? AND item_ref = ?""",
        (to_milestone, current["item_kind"], item_ref),
    ).fetchone()
    if conflict:
        raise ValueError(
            f"{item_ref} already attached to {to_milestone}; cannot move"
        )
    from_milestone = current["milestone_id"]
    conn.execute(
        "UPDATE milestone_items SET milestone_id = ?, updated_at = ? WHERE id = ?",
        (to_milestone, utc_now(), current["id"]),
    )
    _audit(
        conn,
        milestone_id=to_milestone,
        item_ref=item_ref,
        actor=actor,
        action="move",
        from_state=from_milestone,
        to_state=to_milestone,
        reason=reason,
    )
    conn.commit()
    return _get_item_by_ref(conn, item_ref)


def set_item_status(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    status: str,
    commit: str | None = None,
    actor: str = "user",
    reason: str = "",
) -> dict[str, Any]:
    """Set an item's status. Records done_commit + done_at when terminal."""
    if status not in ITEM_STATUSES:
        raise ValueError(f"Invalid status: {status!r}. Must be one of {ITEM_STATUSES}")
    current = _get_item_by_ref(conn, item_ref)
    if current["status"] == status:
        return current
    now = utc_now()
    sets = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, now]
    if status in MILESTONE_ITEM_TERMINAL:
        sets.append("done_at = ?")
        params.append(now)
        if commit:
            sets.append("done_commit = ?")
            params.append(commit)
    params.append(current["id"])
    conn.execute(f"UPDATE milestone_items SET {', '.join(sets)} WHERE id = ?", params)
    _audit(
        conn,
        milestone_id=current["milestone_id"],
        item_ref=item_ref,
        actor=actor,
        action="status",
        from_state=current["status"],
        to_state=status,
        reason=reason,
    )
    conn.commit()
    return _get_item_by_ref(conn, item_ref)


def query_audit(
    conn: sqlite3.Connection,
    *,
    milestone_id: str | None = None,
    item_ref: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Filtered audit log query."""
    conditions: list[str] = []
    params: list[Any] = []
    if milestone_id:
        conditions.append("milestone_id = ?")
        params.append(milestone_id)
    if item_ref:
        conditions.append("item_ref = ?")
        params.append(item_ref)
    if actor:
        conditions.append("actor = ?")
        params.append(actor)
    if since:
        conditions.append("at >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM milestone_audit {where} ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_audit(r) for r in rows]
