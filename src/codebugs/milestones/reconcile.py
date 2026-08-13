"""Terminal-source reconciliation (CB-26).

A ``milestone_items`` row is a PROJECTION of a source finding or requirement.
Routing happened once, at add time (``triage._auto_route_finding``), and nothing
ever moved the item when the source resolved — so 19 of 23 open ``stream/triage``
rows pointed at already-terminal findings and ``pull_next`` could hand an agent
work that was already done.

Two mechanisms, deliberately both:

* **Eager** — ``_reconcile_on_terminal`` is the update-side twin of the add-side
  router, registered through ``db.register_status_change_hook``. It keeps the
  STORED rows correct, so ``milestone_status`` rollups and audit history mean
  something.
* **Defensive** — ``source_is_terminal`` filters the queue reads themselves.
  This is not belt-and-braces, it is the only reason the invariant can be
  claimed at all: several writers bypass the hook entirely (``add_milestone_item``
  inserts ``open`` even for an already-terminal source, ``set_item_status`` can
  reopen, ``release_item(status='abandoned')`` reopens unconditionally, the
  requirements bulk/markdown importers replace rows with no hook, and
  ``EntityRef.set_status`` never fires hooks by design).

Scoped to ``kind='stream'`` milestones on purpose — see ``_STREAM_ONLY`` below.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from codebugs import entities
from codebugs.types import utc_now

from codebugs.milestones._schema import (
    ENTITY_KIND_TO_ITEM_KIND,
    RECONCILER_ACTOR,
    outcome_for,
)
from codebugs.milestones._spine import _audit


# The hook closes items only in STREAM milestones.
#
# `milestone_close`'s unfinished gate derives purely from item status
# (`closegate.py:162-165`) and `done_commit` is never read as a gate anywhere. So
# projecting a `fixed` finding onto `done` in a RELEASE milestone would let a
# release close over an item whose integration step never ran — and
# `worktree-finish.sh` flips the finding to `fixed` BEFORE its non-fatal
# `mark_integrated` step, which is exactly the sequence that gate exists to
# catch. Streams have no close gate (a stream cannot be closed at all), and
# streams hold 100% of the observed defect.
#
# Release-milestone reconciliation is a separate card, together with the
# question it depends on: is a `fixed` source sufficient integration evidence
# without a `done_commit`? The defensive filter below still protects `pull_next`
# for release milestones, so nothing hands out stale work in the meantime.
_STREAM_ONLY = "SELECT id FROM milestones WHERE kind = 'stream'"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _milestones_ready(conn: sqlite3.Connection) -> bool:
    """Both tables the reconciler writes must exist.

    Raw ``sqlite3.connect()`` callers (e.g. ``tests/test_sweep.py``) invoke
    ``add_finding`` / ``update_finding`` on connections that never initialised
    milestones. Probing only ``milestone_items`` would still explode on the audit
    insert, so probe what is actually written.
    """
    return _table_exists(conn, "milestone_items") and _table_exists(conn, "milestone_audit")


def source_is_terminal(conn: sqlite3.Connection, item_kind: str, item_ref: str) -> bool:
    """Is this item's source entity in a terminal status?

    ``False`` for externals (no source entity), for a source row that no longer
    exists, and when the source table is absent — every unknown fails OPEN here,
    because this predicate HIDES rows and a false positive would silently shrink
    a queue, which is the quieter and worse failure (CB-25's lesson).
    """
    for kind in entities.ENTITY_KINDS:
        if ENTITY_KIND_TO_ITEM_KIND.get(kind.name) != item_kind:
            continue
        if not _table_exists(conn, kind.table):
            return False
        row = conn.execute(
            # noqa justified structurally: `table` is an EntityKind field validated
            # as a bare identifier in EntityKind.__post_init__ (CB-22).
            f"SELECT status FROM {kind.table} WHERE id = ?",  # noqa: S608
            (item_ref,),
        ).fetchone()
        return row is not None and row["status"] in kind.terminal
    return False


def _pending_rows(
    conn: sqlite3.Connection,
    *,
    item_kind: str,
    target: str,
    item_ref: str | None = None,
) -> list[sqlite3.Row]:
    """Stream-milestone rows whose stored status disagrees with the mapped target.

    ``status != target`` rather than "not terminal", for two reasons the first
    draft got wrong in opposite directions:

    * ``deferred`` is EXCLUDED — no queue returns a deferred item
      (``triage_inbox`` and ``_bucket_query`` both filter ``status='open'``), so
      closing one fixes nothing while destroying the deferral record.
    * terminal-to-terminal IS included — both domain updaters permit
      ``fixed -> wont_fix`` and fire the hook, and that must remap ``done ->
      dismissed``. A "not terminal" filter would skip it.
    """
    sql = (
        "SELECT * FROM milestone_items "
        f"WHERE milestone_id IN ({_STREAM_ONLY}) "
        "AND item_kind = ? AND status != ? AND status != 'deferred'"
    )
    params: list[Any] = [item_kind, target]
    if item_ref is not None:
        sql += " AND item_ref = ?"
        params.append(item_ref)
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def _apply_row(conn: sqlite3.Connection, row: sqlite3.Row, target: str, actor: str) -> None:
    """Project one item onto ``target``. Non-committing, by contract.

    Capacity is released BEFORE ``assigned_agent`` is cleared: the row is the only
    record of who held the slot, so clearing first and then failing would leak the
    slot unrecoverably.
    """
    agent = row["assigned_agent"]
    if agent:
        from codebugs.milestones.capacity import _decrement_capacity

        _decrement_capacity(conn, agent, row["size"])

    now = utc_now()
    done_at = now if target == "done" else None
    conn.execute(
        "UPDATE milestone_items SET status = ?, done_at = ?, updated_at = ?, "
        "assigned_agent = NULL WHERE id = ?",
        (target, done_at, now, row["id"]),
    )
    _audit(
        conn,
        milestone_id=row["milestone_id"],
        item_ref=row["item_ref"],
        actor=actor,
        action="reconcile",
        from_state=row["status"],
        to_state=target,
        reason="source entity reached a terminal status",
    )


def _reconcile_on_terminal(
    conn: sqlite3.Connection, entity_id: str, old_status: str | None, new_status: str
) -> None:
    """Project a terminal source status onto its stream milestone items.

    Runs inside the domain update's OPEN transaction, so every statement here is
    non-committing — calling ``release_item`` or ``move_milestone_item`` instead
    would commit the CALLER's work (the CB-24 trap).

    ``db.run_status_change_hooks`` SWALLOWS exceptions and the caller's ``db.txn``
    then commits anyway, so a mid-loop failure would otherwise commit a partial
    reconciliation behind a success-shaped return. The body therefore runs inside a
    SAVEPOINT: either every row moves or none does, and the failure is recorded as
    an audit row written AFTER the rollback (stderr is invisible to an MCP caller;
    an audit row is queryable).
    """
    if not _milestones_ready(conn):
        return
    try:
        ref = entities.EntityRef.of(entity_id)
    except ValueError:
        return
    if new_status not in ref.kind.terminal:
        return
    item_kind = ENTITY_KIND_TO_ITEM_KIND.get(ref.kind.name)
    if item_kind is None:
        return

    target = outcome_for(ref.kind.name, new_status)

    conn.execute("SAVEPOINT ms_reconcile")
    try:
        for row in _pending_rows(
            conn, item_kind=item_kind, target=target, item_ref=entity_id
        ):
            _apply_row(conn, row, target, RECONCILER_ACTOR)
    except Exception as exc:  # noqa: BLE001
        conn.execute("ROLLBACK TO ms_reconcile")
        conn.execute("RELEASE ms_reconcile")
        _audit(
            conn,
            milestone_id="stream/triage",
            item_ref=entity_id,
            actor=RECONCILER_ACTOR,
            action="reconcile_failed",
            from_state=old_status,
            to_state=new_status,
            reason=f"{type(exc).__name__}: {exc}",
        )
        raise
    conn.execute("RELEASE ms_reconcile")


def reconcile_all(
    conn: sqlite3.Connection,
    *,
    actor: str = RECONCILER_ACTOR,
    apply: bool = False,
) -> dict[str, Any]:
    """One-time repair for items whose source resolved before the hook existed.

    Defaults to a DRY RUN. A bulk mutation that runs by default is how a repair
    tool becomes an accident; the CLI requires an explicit ``--apply``.
    """
    if not _milestones_ready(conn):
        return {"applied": False, "candidates": 0, "items": []}

    from codebugs import db

    found: list[dict[str, Any]] = []
    with db.txn(conn):
        for kind in entities.ENTITY_KINDS:
            item_kind = ENTITY_KIND_TO_ITEM_KIND.get(kind.name)
            if item_kind is None or not _table_exists(conn, kind.table):
                continue
            for status in sorted(kind.terminal):
                target = outcome_for(kind.name, status)
                for row in _pending_rows(conn, item_kind=item_kind, target=target):
                    if not source_is_terminal(conn, item_kind, row["item_ref"]):
                        continue
                    src = conn.execute(
                        # noqa justified as in source_is_terminal.
                        f"SELECT status FROM {kind.table} WHERE id = ?",  # noqa: S608
                        (row["item_ref"],),
                    ).fetchone()
                    if src is None or src["status"] != status:
                        continue
                    found.append(
                        {
                            "item_ref": row["item_ref"],
                            "milestone_id": row["milestone_id"],
                            "from_status": row["status"],
                            "to_status": target,
                            "source_status": status,
                        }
                    )
                    if apply:
                        _apply_row(conn, row, target, actor)
    return {"applied": apply, "candidates": len(found), "items": found}
