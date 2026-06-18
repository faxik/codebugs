"""Shared spine: row converters, existence/fetch helpers, audit. Leaf module
used by every milestones context. Imports only stdlib + types + _schema."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from codebugs.types import utc_now

from codebugs.milestones._schema import ITEM_KINDS


def _row_to_milestone(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["meta"] = json.loads(d.pop("meta_json") or "{}")
    return d


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["meta"] = json.loads(d.pop("meta_json") or "{}")
    d["branch_only"] = bool(d["branch_only"])
    return d


def _row_to_audit(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _milestone_exists(conn: sqlite3.Connection, milestone_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM milestones WHERE id = ?", (milestone_id,)
    ).fetchone() is not None


def _get_milestone(conn: sqlite3.Connection, milestone_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM milestones WHERE id = ?", (milestone_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Milestone not found: {milestone_id}")
    return _row_to_milestone(row)


def _validate_item_ref(conn: sqlite3.Connection, item_kind: str, item_ref: str) -> None:
    """Phantom-ID guard: bug must exist in findings, requirement in requirements.
    external is free-form and skipped."""
    if item_kind == "bug":
        row = conn.execute("SELECT 1 FROM findings WHERE id = ?", (item_ref,)).fetchone()
        if not row:
            raise ValueError(f"Unknown bug: {item_ref} (not present in findings)")
    elif item_kind == "requirement":
        row = conn.execute("SELECT 1 FROM requirements WHERE id = ?", (item_ref,)).fetchone()
        if not row:
            raise ValueError(f"Unknown requirement: {item_ref} (not present in requirements)")
    elif item_kind == "external":
        return
    else:
        raise ValueError(f"Invalid item_kind: {item_kind!r}. Must be one of {ITEM_KINDS}")


def _audit(
    conn: sqlite3.Connection,
    *,
    milestone_id: str,
    item_ref: str | None,
    actor: str,
    action: str,
    from_state: str | None = None,
    to_state: str | None = None,
    reason: str = "",
) -> None:
    conn.execute(
        """INSERT INTO milestone_audit
           (milestone_id, item_ref, actor, action, from_state, to_state, reason, at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (milestone_id, item_ref, actor, action, from_state, to_state, reason, utc_now()),
    )


def _get_item_by_ref(conn: sqlite3.Connection, item_ref: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM milestone_items WHERE item_ref = ? ORDER BY id DESC LIMIT 1",
        (item_ref,),
    ).fetchone()
    if not row:
        raise KeyError(f"Item not found: {item_ref}")
    return _row_to_item(row)


def _items_with_active_blockers(
    conn: sqlite3.Connection, items: list[dict[str, Any]]
) -> list[str]:
    """Return item_refs that have at least one active blocker. Skips externals."""
    from codebugs import blockers as blockers_module
    refs: list[str] = []
    for it in items:
        if it["item_kind"] == "external":
            continue
        try:
            r = blockers_module.query_blockers(conn, item_id=it["item_ref"], active_only=True)
        except Exception:
            continue
        if r.get("blockers"):
            refs.append(it["item_ref"])
    return refs
