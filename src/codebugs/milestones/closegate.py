"""Close-gate + branch tracking: mark_branch_only / mark_integrated / defer / close."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from codebugs import db
from codebugs.types import utc_now

from codebugs.milestones._spine import (
    _audit,
    _get_item_by_ref,
    _get_item_row_by_ref,
    _get_milestone,
    _items_with_active_blockers,
    _milestone_exists,
    _row_to_item,
)


def mark_branch_only(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    branch_name: str,
    actor: str = "user",
) -> dict[str, Any]:
    """Flag an item as living on a feature branch (not yet integrated to main).
    Called by worktree-setup.sh when a branch is created for this item.

    ``meta_json`` is merged in Python from the row read here, so the read and the
    write are one transaction (CB-24 sibling). Two agents branching different items
    do not collide, but a concurrent writer to the *same* item's meta would
    otherwise be erased by whichever UPDATE landed second, with both reporting
    success. ``db.txn`` owns the commit — do not restore ``conn.commit()``.
    """
    with db.txn(conn):
        item = _get_item_by_ref(conn, item_ref)
        meta = dict(item.get("meta") or {})
        meta["branch"] = branch_name
        now = utc_now()
        conn.execute(
            "UPDATE milestone_items SET branch_only=1, meta_json=?, updated_at=? WHERE id=?",
            (json.dumps(meta), now, item["id"]),
        )
        _audit(
            conn,
            milestone_id=item["milestone_id"],
            item_ref=item_ref,
            actor=actor,
            action="branch",
            from_state="branch_only=0",
            to_state="branch_only=1",
            reason=f"branch={branch_name}",
        )
    return _get_item_by_ref(conn, item_ref)


def mark_integrated(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    commit: str,
    actor: str = "user",
) -> dict[str, Any]:
    """Mark an item as integrated on main: clears branch_only, sets done_commit,
    sets status=done. Called by worktree-finish.sh on successful main integration.

    The weakest of the CB-36 instances and wrapped anyway: its only guard is on the
    ``commit`` ARGUMENT, and the row it reads supplies ``id`` / ``milestone_id`` for
    targeting and ``status`` for the audit trail rather than a branch condition. But
    the read and the write must still not straddle an unlocked window — otherwise the
    audit row records a ``from_state`` the item had already left, which is a quiet lie
    in the one table that exists to say what happened. The result row is captured by
    numeric ``id`` rather than re-resolved by ``item_ref`` after the commit (CB-39).
    Do not restore ``conn.commit()``.
    """
    if not commit.strip():
        raise ValueError("commit is required for integration")
    with db.txn(conn):
        item = _get_item_row_by_ref(conn, item_ref)
        now = utc_now()
        conn.execute(
            """UPDATE milestone_items
               SET branch_only=0, done_commit=?, status='done', done_at=?, updated_at=?
               WHERE id=?""",
            (commit, now, now, item["id"]),
        )
        _audit(
            conn,
            milestone_id=item["milestone_id"],
            item_ref=item_ref,
            actor=actor,
            action="integrate",
            from_state=item["status"],
            to_state="done",
            reason=f"commit={commit}",
        )
        result = conn.execute(
            "SELECT * FROM milestone_items WHERE id = ?", (item["id"],)
        ).fetchone()
    return _row_to_item(result)


def milestone_defer(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    to_milestone: str = "stream/maintenance",
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """Move an item to stream/maintenance (or another milestone) with status='deferred'.

    One transaction, opened before the read (CB-24): the conflict query is keyed on
    ``item_kind`` and ``id`` from the row read at the top, so without the write lock a
    concurrent attach to the destination lands between the check and the move and
    violates the UNIQUE constraint the check exists to protect. The audit row's
    ``from_state`` is likewise composed from that read. The result row is captured by
    numeric ``id``, not re-resolved by ``item_ref`` after the commit (CB-39). Do not
    restore ``conn.commit()``.
    """
    with db.txn(conn):
        item = _get_item_row_by_ref(conn, item_ref)
        if not _milestone_exists(conn, to_milestone):
            raise KeyError(f"Destination milestone not found: {to_milestone}")
        conflict = conn.execute(
            """SELECT id FROM milestone_items
               WHERE milestone_id = ? AND item_kind = ? AND item_ref = ? AND id != ?""",
            (to_milestone, item["item_kind"], item_ref, item["id"]),
        ).fetchone()
        if conflict:
            raise ValueError(f"{item_ref} already attached to {to_milestone}")

        from_milestone = item["milestone_id"]
        from_status = item["status"]
        now = utc_now()
        conn.execute(
            """UPDATE milestone_items
               SET milestone_id=?, status='deferred', updated_at=?
               WHERE id=?""",
            (to_milestone, now, item["id"]),
        )
        _audit(
            conn,
            milestone_id=to_milestone,
            item_ref=item_ref,
            actor=actor,
            action="defer",
            from_state=f"{from_milestone}/{from_status}",
            to_state=f"{to_milestone}/deferred",
            reason=reason,
        )
        result = conn.execute(
            "SELECT * FROM milestone_items WHERE id = ?", (item["id"],)
        ).fetchone()
    return _row_to_item(result)


def milestone_close(
    conn: sqlite3.Connection,
    *,
    id: str,
    force: bool = False,
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """Close a release milestone. Refuses if:
      - the milestone is a stream (always — `force` does not bypass)
      - any item is open / in_progress (unless force=True)
      - any item has branch_only=1 (unless force=True)
      - any item has unresolved blockers (unless force=True)

    Error messages list the specific blocking items so the caller can act.
    """
    milestone = _get_milestone(conn, id)
    if milestone["kind"] == "stream":
        raise ValueError(f"streams cannot be closed (milestone={id})")

    if not force:
        problems: list[str] = []
        rows = conn.execute(
            "SELECT * FROM milestone_items WHERE milestone_id = ?", (id,)
        ).fetchall()
        items = [_row_to_item(r) for r in rows]

        unfinished = [
            i["item_ref"] for i in items
            if i["status"] in ("open", "in_progress")
        ]
        if unfinished:
            problems.append(
                f"unfinished items ({len(unfinished)}): {', '.join(unfinished)}"
            )

        branch_only = [
            f"{i['item_ref']}@{(i.get('meta') or {}).get('branch', '?')}"
            for i in items if i["branch_only"]
        ]
        if branch_only:
            problems.append(
                f"branch-only items ({len(branch_only)}): {', '.join(branch_only)}"
            )

        blocked = _items_with_active_blockers(conn, items)
        if blocked:
            problems.append(
                f"items with active blockers ({len(blocked)}): {', '.join(blocked)}"
            )

        if problems:
            raise ValueError(
                f"cannot close {id}: " + "; ".join(problems)
                + "  (use force=True with reason to override)"
            )

    now = utc_now()
    from_state = milestone["state"]
    conn.execute(
        "UPDATE milestones SET state='shipped', closed_at=? WHERE id=?",
        (now, id),
    )
    audit_reason = (f"force:{reason}" if force and reason else
                    "force" if force else reason)
    _audit(
        conn,
        milestone_id=id,
        item_ref=None,
        actor=actor,
        action="close",
        from_state=from_state,
        to_state="shipped",
        reason=audit_reason,
    )
    conn.commit()
    return _get_milestone(conn, id)
