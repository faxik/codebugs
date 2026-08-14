"""Capacity-aware pull: agent capacity ledger, eligibility, atomic claim."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from codebugs import db
from codebugs.types import utc_now

from codebugs.milestones._schema import ITEM_SIZES
from codebugs.milestones._spine import (
    _audit,
    _get_item_by_ref,
    _get_item_row_by_ref,
    _row_to_item,
)
from codebugs.milestones.reconcile import source_is_terminal


def _held_col(size: str) -> str:
    """``<size>_held``, refusing any size that is not a declared one (CB-22 sibling).

    This column name is INTERPOLATED into SQL, on the strength of an invariant —
    the ``size`` CHECK constraint — enforced two layers away in another table. That
    is the same shape as the ``EntityKind`` identifiers.

    Production callers pass ``item["size"]`` read back from that CHECK-constrained
    column, so a bad size needs a direct call to one of these private helpers or a
    corrupted row; the exposure is prospective, as in CB-22 itself. What made it
    worth closing is that the two paths disagreed for the SAME input: with an
    existing capacity row an unknown size raised ``OperationalError: no such
    column``, while with no row it took the dict branch below, wrote a row of
    zeros, and returned SUCCESS having silently lost the increment. Fail closed,
    identically, in both.
    """
    if size not in ITEM_SIZES:
        raise ValueError(f"Invalid size: {size!r}. Must be one of {ITEM_SIZES}")
    return f"{size}_held"


def _capacity_for(conn: sqlite3.Connection, agent_id: str) -> dict[str, int]:
    """Read current held counts for an agent. Returns zeros if no row."""
    row = conn.execute(
        "SELECT large_held, small_held, triage_held FROM agent_capacity WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    if not row:
        return {"large": 0, "small": 0, "triage": 0}
    return {
        "large": row["large_held"],
        "small": row["small_held"],
        "triage": row["triage_held"],
    }


def _upsert_capacity_increment(
    conn: sqlite3.Connection, agent_id: str, size: str
) -> None:
    """Increment the held counter for size; insert row if missing."""
    col = _held_col(size)
    row = conn.execute(
        "SELECT agent_id FROM agent_capacity WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    now = utc_now()
    if row:
        conn.execute(
            f"UPDATE agent_capacity SET {col} = {col} + 1, last_pull_at = ? "
            f"WHERE agent_id = ?",
            (now, agent_id),
        )
    else:
        cols = {"large_held": 0, "small_held": 0, "triage_held": 0}
        cols[col] = 1
        conn.execute(
            """INSERT INTO agent_capacity
               (agent_id, large_held, small_held, triage_held, last_pull_at)
               VALUES (?, ?, ?, ?, ?)""",
            (agent_id, cols["large_held"], cols["small_held"],
             cols["triage_held"], now),
        )


def _decrement_capacity(
    conn: sqlite3.Connection, agent_id: str, size: str
) -> None:
    col = _held_col(size)
    now = utc_now()
    conn.execute(
        f"UPDATE agent_capacity SET {col} = MAX({col} - 1, 0), last_release_at = ? "
        f"WHERE agent_id = ?",
        (now, agent_id),
    )


def _has_active_blocker(conn: sqlite3.Connection, item_ref: str) -> bool:
    """True if item_ref has at least one unsatisfied, uncancelled blocker."""
    from codebugs import blockers as blockers_module
    try:
        r = blockers_module.query_blockers(
            conn, item_id=item_ref, active_only=True,
        )
    except Exception:
        return False
    return bool(r.get("blockers"))


def _real_requirement_exists(conn: sqlite3.Connection, fr_id: str) -> bool:
    """True if a requirement with this id exists. The real production read."""
    row = conn.execute("SELECT 1 FROM requirements WHERE id = ?", (fr_id,)).fetchone()
    return row is not None


def _eligibility_failure(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    milestone: dict[str, Any],
    capacity: dict[str, int],
    held: dict[str, int],
    *,
    has_active_blocker=None,
    requirement_exists=None,
) -> str | None:
    """Return None if eligible, else a short reason string. Used by pull_next.

    The two cross-domain reads are injectable for testing: ``has_active_blocker``
    (item_ref -> bool) and ``requirement_exists`` (fr_id -> bool). Both default
    to the real conn-backed implementations, so production callers pass only the
    five positionals. Tests can drive the full matrix — including the
    has-active-blocker case the real fail-soft swallow would otherwise mask —
    with no findings/reqs/blockers schema present."""
    if has_active_blocker is None:
        def has_active_blocker(ref):
            return _has_active_blocker(conn, ref)
    if requirement_exists is None:
        def requirement_exists(fr):
            return _real_requirement_exists(conn, fr)
    if item["status"] != "open":
        return f"not open (status={item['status']})"
    if item["item_kind"] != "external" and has_active_blocker(item["item_ref"]):
        return "has active blocker"
    if item["size"] == "large" and not (item.get("acceptance") or "").strip():
        return "size=large requires acceptance"
    if (item["size"] == "large"
            and item["item_kind"] == "bug"
            and milestone["kind"] == "release"):
        meta = item.get("meta") or {}
        linked = meta.get("linked_frs") or []
        if not linked:
            return "size=large bug in release needs linked_frs"
        for fr in linked:
            if not requirement_exists(fr):
                return f"linked FR {fr} not in requirements"
    size = item["size"]
    cap = capacity.get(size, 0)
    used = held.get(size, 0)
    if used >= cap:
        return f"agent capacity for {size} full ({used}/{cap})"
    return None


def _bucket_query(milestone_pattern: str) -> str:
    return (
        "SELECT mi.*, m.kind AS milestone_kind, m.target_date AS milestone_target_date "
        "FROM milestone_items mi JOIN milestones m ON m.id = mi.milestone_id "
        f"WHERE mi.milestone_id {milestone_pattern} AND mi.status = 'open' "
        "ORDER BY m.target_date ASC NULLS LAST, mi.priority ASC, mi.created_at ASC"
    )


def _candidates(conn: sqlite3.Connection):
    """Yield (item, milestone) tuples in priority order across buckets."""
    buckets = [
        ("= 'stream/security'", None),
        ("IN (SELECT id FROM milestones WHERE kind='release' AND state='open')", None),
        ("= 'stream/triage'", None),
        ("= 'stream/maintenance'", None),
    ]
    for pattern, _ in buckets:
        rows = conn.execute(_bucket_query(pattern)).fetchall()
        for row in rows:
            # A terminal source is never eligible, whatever the stored status says
            # (CB-26). This covers RELEASE milestones too, which the reconciliation
            # hook deliberately does not touch, so `pull_next` never hands out
            # finished work even where the stored row was left behind.
            if source_is_terminal(conn, row["item_kind"], row["item_ref"]):
                continue
            d = dict(row)
            kind = d.pop("milestone_kind")
            d.pop("milestone_target_date")
            milestone = {"id": d["milestone_id"], "kind": kind}
            item = {
                k: v for k, v in d.items()
                if k not in ("milestone_kind", "milestone_target_date")
            }
            item["meta"] = json.loads(item.pop("meta_json") or "{}")
            item["branch_only"] = bool(item["branch_only"])
            yield item, milestone


def pull_next(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    capacity: dict[str, int],
    actor: str | None = None,
) -> dict[str, Any] | None:
    """Claim the highest-priority eligible item for the calling agent.
    Returns the item dict (with `_eligibility` annotation) or None if no
    candidate matches. Atomic under BEGIN IMMEDIATE."""
    actor = actor or agent_id

    saved_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            held = _capacity_for(conn, agent_id)
            chosen: dict[str, Any] | None = None
            for item, milestone in _candidates(conn):
                fail = _eligibility_failure(conn, item, milestone, capacity, held)
                if fail is None:
                    chosen = item
                    break
            if chosen is None:
                conn.execute("ROLLBACK")
                return None

            now = utc_now()
            conn.execute(
                """UPDATE milestone_items
                   SET status='in_progress', assigned_agent=?, pulled_at=?, updated_at=?
                   WHERE id=? AND status='open'""",
                (agent_id, now, now, chosen["id"]),
            )
            _upsert_capacity_increment(conn, agent_id, chosen["size"])
            _audit(
                conn,
                milestone_id=chosen["milestone_id"],
                item_ref=chosen["item_ref"],
                actor=actor,
                action="pull",
                from_state="open",
                to_state="in_progress",
                reason=f"agent={agent_id} capacity={capacity}",
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = saved_isolation

    return _get_item_by_ref(conn, chosen["item_ref"])


def release_item(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    status: str = "done",
    commit: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Free an agent's capacity slot for the item. status is the terminal
    state to record ('done' or 'abandoned'; 'abandoned' maps to 'open' again
    so the item is re-pullable).

    Returns **the attachment it actually mutated** — it does NOT promise that
    attachment is the one the caller meant. The opening lookup still resolves
    ``ORDER BY id DESC LIMIT 1`` and this signature accepts neither an attachment id
    nor a ``milestone_id``, so with several attachments per ref the selection is
    arbitrary and the decremented slot may belong to a different one. That is CB-33
    and it is open; what is closed here is only the *post-commit re-read window*.

    Three things load-bearing enough to state (CB-24, CB-30):

    * The transaction opens BEFORE the read. ``assigned_agent`` is read from the row
      and then used to decrement a counter, so without the write lock the CB-26
      reconciliation hook can close the item and decrement between the two, and this
      call decrements a second time — the agent's other item stays assigned while
      capacity reports zero.
    * The row read here is RAW (``_get_item_row_by_ref``), not ``_row_to_item``.
      Parsing ``meta_json`` inside the block would turn a malformed stored value into
      a rollback of a write that already succeeded.
    * The returned row is captured by ``RETURNING`` and converted after the block.
      Re-querying by ref after the commit returns whatever attachment is newest by
      then, which is not necessarily the one written. Because these statements carry
      ``RETURNING``, their ``rowcount`` must never be read (the RETURNING rule) — note
      that forecloses rowcount-based hardening of ``_decrement_capacity`` (CB-38).

    Do not restore a ``conn.commit()``: ``db.txn`` owns the commit.
    """
    with db.txn(conn):
        # sqlite3.Row is not a dict — no .get(). The column always exists; NULL is None.
        item = _get_item_row_by_ref(conn, item_ref)
        agent = item["assigned_agent"]

        if status == "abandoned" and commit:
            # Incompatible combination, not a forwarding gap: an abandoned item is
            # reopened and re-pullable, so there is no landed commit to record and only
            # `done` has a `done_commit` column to record it in. Silently dropping it
            # returned success for a commit that was never stored (CB-28).
            raise ValueError(
                "commit cannot be recorded with status='abandoned' "
                "(only 'done' records a commit)"
            )

        now = utc_now()
        if status == "abandoned":
            cur = conn.execute(
                """UPDATE milestone_items SET status='open', assigned_agent=NULL,
                   pulled_at=NULL, updated_at=? WHERE id=? RETURNING *""",
                (now, item["id"]),
            )
            to_state = "open"
            action = "release"
        elif status == "done":
            sets = ["status='done'", "done_at=?", "updated_at=?", "assigned_agent=NULL"]
            params: list[Any] = [now, now]
            if commit:
                sets.append("done_commit=?")
                params.append(commit)
            params.append(item["id"])
            cur = conn.execute(
                f"UPDATE milestone_items SET {', '.join(sets)} WHERE id=? RETURNING *",
                params,
            )
            to_state = "done"
            action = "done"
        else:
            raise ValueError(f"Invalid release status: {status!r}. Use 'done' or 'abandoned'.")

        # Exhaust the cursor inside the block: db.txn issues COMMIT on exit, and an
        # open RETURNING cursor at that point is a statement still in progress.
        updated_row = cur.fetchone()
        if updated_row is None:
            # Unreachable while the write lock is held — the row was read two
            # statements ago. Named anyway, because the alternative is an opaque
            # TypeError from _row_to_item(None) after the block has committed.
            raise KeyError(f"Item vanished during release: {item_ref}")

        if agent:
            _decrement_capacity(conn, agent, item["size"])
        _audit(
            conn,
            milestone_id=item["milestone_id"],
            item_ref=item_ref,
            actor=actor or agent or "user",
            action=action,
            from_state=item["status"],
            to_state=to_state,
            reason="",
        )
    return _row_to_item(updated_row)


def get_wip_status(
    conn: sqlite3.Connection,
    *,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Snapshot of agent capacity rows. If agent_id is given, returns one row."""
    if agent_id:
        rows = conn.execute(
            "SELECT * FROM agent_capacity WHERE agent_id = ?", (agent_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_capacity ORDER BY agent_id"
        ).fetchall()
    return [dict(r) for r in rows]
