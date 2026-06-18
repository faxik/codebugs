"""Triage: inbox / dismiss / promote + the auto-route post-add hook."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from codebugs.types import utc_now

from codebugs.milestones._schema import AUTO_ROUTER_ACTOR
from codebugs.milestones._spine import (
    _audit,
    _get_item_by_ref,
    _milestone_exists,
    _row_to_item,
)


def _auto_route_finding(conn: sqlite3.Connection, finding: dict[str, Any]) -> None:
    """Route a newly-added finding into stream/triage or stream/security.

    Schema-probes first: raw sqlite3.connect() callers (e.g. tests/test_sweep.py)
    may invoke add_finding on a connection that didn't initialize milestones.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='milestone_items'"
    ).fetchone()
    if not row:
        return

    sev = finding.get("severity", "")
    cat = finding.get("category", "") or ""
    if sev == "critical" and cat.startswith("security:"):
        target = "stream/security"
    else:
        target = "stream/triage"

    now = utc_now()
    cursor = conn.execute(
        """INSERT OR IGNORE INTO milestone_items
           (milestone_id, item_kind, item_ref, size, priority, status,
            acceptance, meta_json, created_at, updated_at)
           VALUES (?, 'bug', ?, 'triage', 100, 'open', '', '{}', ?, ?)""",
        (target, finding["id"], now, now),
    )
    if cursor.rowcount > 0:
        _audit(
            conn,
            milestone_id=target,
            item_ref=finding["id"],
            actor=AUTO_ROUTER_ACTOR,
            action="create",
            from_state=None,
            to_state="open",
            reason="auto-routed",
        )


def triage_inbox(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List items in stream/triage, oldest first."""
    rows = conn.execute(
        """SELECT * FROM milestone_items
           WHERE milestone_id = 'stream/triage' AND status = 'open'
           ORDER BY created_at ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def triage_dismiss(
    conn: sqlite3.Connection,
    *,
    bug_id: str,
    reason: str,
    actor: str = "user",
) -> dict[str, Any]:
    """Mark a triage item as dismissed. Propagates to the underlying entity
    based on item_kind:
      - bug → finding status='not_a_bug'
      - requirement → requirement status='obsolete'
      - external → no propagation, milestone_item dismissal only
    """
    if not reason.strip():
        raise ValueError("reason is required for dismissal")
    item = _get_item_by_ref(conn, bug_id)
    if item["status"] == "dismissed":
        return item

    now = utc_now()
    conn.execute(
        """UPDATE milestone_items SET status='dismissed', done_at=?, updated_at=?
           WHERE id=?""",
        (now, now, item["id"]),
    )
    _audit(
        conn,
        milestone_id=item["milestone_id"],
        item_ref=bug_id,
        actor=actor,
        action="dismiss",
        from_state=item["status"],
        to_state="dismissed",
        reason=reason,
    )

    # Propagate to underlying entity.
    if item["item_kind"] == "bug":
        from codebugs.findings import update_finding
        try:
            update_finding(conn, bug_id, status="not_a_bug")
        except KeyError:
            pass  # finding was deleted; dismissal lives in milestone_items only
    elif item["item_kind"] == "requirement":
        from codebugs.reqs import update_requirement
        try:
            update_requirement(conn, bug_id, status="obsolete")
        except KeyError:
            pass

    conn.commit()
    return _get_item_by_ref(conn, bug_id)


def triage_promote(
    conn: sqlite3.Connection,
    *,
    bug_id: str,
    to_milestone: str,
    size: str = "small",
    acceptance: str = "",
    priority: int = 100,
    linked_frs: list[str] | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Move a triage item to a target milestone, optionally upgrading size
    and acceptance. Acceptance is required for size='large'."""
    if size == "large" and not acceptance.strip():
        raise ValueError("acceptance is required for size='large'")
    if not _milestone_exists(conn, to_milestone):
        raise KeyError(f"Destination milestone not found: {to_milestone}")
    item = _get_item_by_ref(conn, bug_id)
    if item["milestone_id"] != "stream/triage":
        raise ValueError(
            f"{bug_id} is not in stream/triage (currently in {item['milestone_id']})"
        )

    conflict = conn.execute(
        """SELECT id FROM milestone_items
           WHERE milestone_id = ? AND item_kind = ? AND item_ref = ?""",
        (to_milestone, item["item_kind"], bug_id),
    ).fetchone()
    if conflict:
        raise ValueError(f"{bug_id} already attached to {to_milestone}")

    meta = dict(item.get("meta") or {})
    if linked_frs:
        meta["linked_frs"] = linked_frs

    now = utc_now()
    sets = [
        "milestone_id = ?", "size = ?", "priority = ?", "updated_at = ?",
        "meta_json = ?",
    ]
    params: list[Any] = [to_milestone, size, priority, now, json.dumps(meta)]
    if acceptance:
        sets.append("acceptance = ?")
        params.append(acceptance)
    params.append(item["id"])
    conn.execute(
        f"UPDATE milestone_items SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    _audit(
        conn,
        milestone_id=to_milestone,
        item_ref=bug_id,
        actor=actor,
        action="promote",
        from_state="stream/triage",
        to_state=to_milestone,
        reason="",
    )
    conn.commit()
    return _get_item_by_ref(conn, bug_id)
