"""Triage: inbox / dismiss / promote + the auto-route post-add hook."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from codebugs import db
from codebugs.types import utc_now

from codebugs.milestones._schema import AUTO_ROUTER_ACTOR
from codebugs.milestones._spine import (
    _audit,
    _get_item_by_ref,
    _milestone_exists,
    _row_to_item,
)
from codebugs.milestones.reconcile import _table_exists, source_is_terminal


def _auto_route_finding(conn: sqlite3.Connection, finding: dict[str, Any]) -> None:
    """Route a newly-added finding into stream/triage or stream/security.

    Schema-probes first: raw sqlite3.connect() callers (e.g. tests/test_sweep.py)
    may invoke add_finding on a connection that didn't initialize milestones.
    """
    if not _table_exists(conn, "milestone_items"):
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
    """List items in stream/triage, oldest first.

    Items whose SOURCE entity is already terminal are filtered out even when the
    stored row still says ``open`` (CB-26). The reconciliation hook normally keeps
    the stored status honest, but several writers bypass it — ``add_milestone_item``
    inserts ``open`` regardless of the source, ``set_item_status`` and
    ``release_item(status='abandoned')`` can reopen, and the requirement importers
    write statuses with no hook at all — so the filter here is what actually makes
    "a resolved finding never appears in the inbox" true.

    The LIMIT is applied AFTER filtering; pushing it into the SQL would silently
    return fewer than ``limit`` live rows whenever stale ones sort ahead of them.
    """
    rows = conn.execute(
        """SELECT * FROM milestone_items
           WHERE milestone_id = 'stream/triage' AND status = 'open'
           ORDER BY created_at ASC""",
    ).fetchall()
    live = [r for r in rows if not source_is_terminal(conn, r["item_kind"], r["item_ref"])]
    return [_row_to_item(r) for r in live[:limit]]


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
    and acceptance. Acceptance is required for size='large'.

    Everything from the eligibility reads through the write is one transaction
    (CB-24 sibling), for two reasons: ``meta_json`` is merged in Python from the
    row read here, so a concurrent meta writer would otherwise be erased; and the
    duplicate-attachment check below is a check-then-act that two concurrent
    promotions could both pass. The argument-only validation stays outside, so
    bad input still raises ``ValueError`` without first waiting for the write
    lock. ``db.txn`` owns the commit — do not restore ``conn.commit()``.
    """
    if size == "large" and not acceptance.strip():
        raise ValueError("acceptance is required for size='large'")

    with db.txn(conn):
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
    return _get_item_by_ref(conn, bug_id)
