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

**Where the defensive filter is NOT applied, and why.** ``triage_inbox`` and
``capacity._candidates`` answer "what work should I pick up", so a terminal source
must never appear. ``foundation.get_milestone_status`` answers a different
question — "what does this milestone contain" — and a rollup that hid rows would
misreport the stored state it exists to describe, so it deliberately reports the
table as it is. That is a real seam, though: the filter is applied by hand at two
call sites and nothing structurally stops a third queue read from forgetting it.
A shared "live items" seam (a view, or one query builder every read goes through)
is the deeper fix and is filed as its own card.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from codebugs import entities
from codebugs.types import utc_now

from codebugs.milestones._schema import (
    ENTITY_KIND_TO_ITEM_KIND,
    RECONCILER_ACTOR,
    outcome_for,
)
from codebugs.milestones._spine import _audit


# Inverted once, here, rather than re-scanned per call.
ITEM_KIND_TO_ENTITY_KIND: dict[str, str] = {
    v: k for k, v in ENTITY_KIND_TO_ITEM_KIND.items()
}


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


def _entity_kind_for(item_kind: str) -> entities.EntityKind | None:
    """The entity kind an ``item_kind`` projects, or None (externals)."""
    name = ITEM_KIND_TO_ENTITY_KIND.get(item_kind)
    if name is None:
        return None
    return next((k for k in entities.ENTITY_KINDS if k.name == name), None)


def source_is_terminal(conn: sqlite3.Connection, item_kind: str, item_ref: str) -> bool:
    """Is this item's source entity in a terminal status?

    ``False`` for externals (no source entity), for a source row that no longer
    exists, and when the source table is absent — every unknown fails OPEN here,
    because this predicate HIDES rows and a false positive would silently shrink
    a queue, which is the quieter and worse failure (CB-25's lesson).

    The predicate itself is ``EntityRef.is_resolved``; do not re-implement it. A
    second copy of "is this entity terminal" is exactly the drift this repo keeps
    filing cards about — if the canonical one ever normalizes status, a hand-rolled
    twin silently keeps the old behaviour.
    """
    kind = _entity_kind_for(item_kind)
    if kind is None or not _table_exists(conn, kind.table):
        return False
    return entities.EntityRef(item_ref, kind).is_resolved(conn)


def _live_rows(
    conn: sqlite3.Connection,
    *,
    item_kind: str,
    target: str | None = None,
    item_ref: str | None = None,
) -> list[sqlite3.Row]:
    """Reconcilable stream-milestone rows of one item kind.

    ``deferred`` is EXCLUDED — no queue returns a deferred item (``triage_inbox``
    and ``_bucket_query`` both filter ``status='open'``), so closing one fixes
    nothing while destroying the deferral record.

    ``target`` narrows to rows whose stored status DISAGREES with it. The hook
    passes it; the backfill does not, because it derives a per-row target from the
    source status instead. Note the predicate is ``status != target`` rather than
    "not terminal": both domain updaters permit ``fixed -> wont_fix`` and fire the
    hook, and that must remap ``done -> dismissed``, which a "not terminal" filter
    would skip.
    """
    sql = (
        "SELECT * FROM milestone_items "
        f"WHERE milestone_id IN ({_STREAM_ONLY}) "
        "AND item_kind = ? AND status != 'deferred'"
    )
    params: list[Any] = [item_kind]
    if target is not None:
        sql += " AND status != ?"
        params.append(target)
    if item_ref is not None:
        sql += " AND item_ref = ?"
        params.append(item_ref)
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def _release_slot(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """Release the capacity slot an item's assigned agent holds, if any.

    The ONE copy of the ordering rule both reconcile paths depend on: call this
    BEFORE the UPDATE that clears ``assigned_agent`` — the row is the only
    record of who held the slot, so clearing first and then failing would leak
    the slot unrecoverably (CB-26 trap 3).
    """
    agent = row["assigned_agent"]
    if agent:
        from codebugs.milestones.capacity import _decrement_capacity

        _decrement_capacity(conn, agent, row["size"])


def _run_guarded(
    conn: sqlite3.Connection,
    *,
    savepoint: str,
    fail_action: str,
    entity_id: str,
    old_status: str | None,
    new_status: str,
    body: Callable[[], None],
) -> None:
    """SAVEPOINT + audit-on-failure scaffold shared by both reconcile hooks.

    ``db.run_status_change_hooks`` SWALLOWS exceptions and the caller's
    ``db.txn`` then commits anyway, so a mid-loop failure would otherwise
    commit a partial reconciliation behind a success-shaped return. The body
    therefore runs inside a SAVEPOINT: either every row moves or none does,
    and the failure is recorded as an audit row written AFTER the rollback
    (stderr is invisible to an MCP caller; an audit row is queryable). Shared,
    not copied per hook: the failure-audit contract must move as one piece.
    """
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        body()
    except Exception as exc:  # noqa: BLE001
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        _audit(
            conn,
            milestone_id="stream/triage",
            item_ref=entity_id,
            actor=RECONCILER_ACTOR,
            action=fail_action,
            from_state=old_status,
            to_state=new_status,
            reason=f"{type(exc).__name__}: {exc}",
        )
        raise
    conn.execute(f"RELEASE {savepoint}")


def _apply_row(conn: sqlite3.Connection, row: sqlite3.Row, target: str, actor: str) -> None:
    """Project one item onto ``target``. Non-committing, by contract."""
    _release_slot(conn, row)

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

    Partial-failure discipline lives in ``_run_guarded`` — see its docstring.
    """
    # Pure-Python checks FIRST. This hook fires on every finding and requirement
    # status change, and most of those are not terminal; probing the schema before
    # the free membership test spent two catalog queries per ordinary transition.
    try:
        ref = entities.EntityRef.of(entity_id)
    except ValueError:
        return
    if new_status not in ref.kind.terminal:
        return
    item_kind = ENTITY_KIND_TO_ITEM_KIND.get(ref.kind.name)
    if item_kind is None:
        return
    if not _milestones_ready(conn):
        return

    target = outcome_for(ref.kind.name, new_status)

    def body() -> None:
        for row in _live_rows(
            conn, item_kind=item_kind, target=target, item_ref=entity_id
        ):
            _apply_row(conn, row, target, RECONCILER_ACTOR)

    _run_guarded(
        conn,
        savepoint="ms_reconcile",
        fail_action="reconcile_failed",
        entity_id=entity_id,
        old_status=old_status,
        new_status=new_status,
        body=body,
    )


def _reconcile_on_reopen(
    conn: sqlite3.Connection, entity_id: str, old_status: str | None, new_status: str
) -> None:
    """Reopen a source entity's stream milestone items when the source reopens.

    The inverse of ``_reconcile_on_terminal``, and it exists because that hook's
    nonterminal early-return makes terminal→open a projection NO-OP: the finding
    goes back to ``open`` while its triage item stays ``done``, so the reopened card
    is invisible to ``triage_inbox`` and ``pull_next`` — strictly worse than the
    duplicate row it replaced (CB-43's regression path). Re-running the add-side
    router cannot fix it: ``_auto_route_finding`` is ``INSERT OR IGNORE`` and the
    row already exists as ``done``.

    Fires on terminal→nonterminal only. Reopens ``done``/``dismissed`` items;
    ``deferred`` is untouched (same reasoning as ``_live_rows``: closing or opening
    one destroys the deferral record), and ``open`` items need nothing.

    Ownership is NOT assumed clear: only reconciler-closed items had their slot
    released — an item closed via ``set_item_status(status='done')`` still carries
    ``assigned_agent``/``pulled_at``/``done_commit``, and reopening it without
    releasing would leave the old agent charged for an item a new agent can pull
    (both charged for one item). So: release the slot, then clear ownership and
    ``done_commit``
    (the reopened item is no longer integrated; the audit reason preserves the old
    commit). Slot release goes through the shared ``_release_slot``; the SAVEPOINT
    + audit-on-failure discipline is the shared ``_run_guarded``.
    """
    try:
        ref = entities.EntityRef.of(entity_id)
    except ValueError:
        return
    if old_status is None or old_status not in ref.kind.terminal:
        return
    if new_status in ref.kind.terminal:
        return
    item_kind = ENTITY_KIND_TO_ITEM_KIND.get(ref.kind.name)
    if item_kind is None:
        return
    if not _milestones_ready(conn):
        return

    now = utc_now()

    def body() -> None:
        rows = conn.execute(
            "SELECT * FROM milestone_items "
            f"WHERE milestone_id IN ({_STREAM_ONLY}) "
            "AND item_kind = ? AND item_ref = ? AND status IN ('done', 'dismissed') "
            "ORDER BY id",
            (item_kind, entity_id),
        ).fetchall()
        for row in rows:
            _release_slot(conn, row)
            reason = "source entity reopened"
            if row["done_commit"]:
                reason += f" (was integrated at {row['done_commit']})"
            conn.execute(
                "UPDATE milestone_items SET status = 'open', done_at = NULL, "
                "assigned_agent = NULL, pulled_at = NULL, done_commit = NULL, "
                "updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            _audit(
                conn,
                milestone_id=row["milestone_id"],
                item_ref=row["item_ref"],
                actor=RECONCILER_ACTOR,
                action="reconcile_reopen",
                from_state=row["status"],
                to_state="open",
                reason=reason,
            )

    _run_guarded(
        conn,
        savepoint="ms_reconcile_reopen",
        fail_action="reconcile_reopen_failed",
        entity_id=entity_id,
        old_status=old_status,
        new_status=new_status,
        body=body,
    )


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
            # Scan each kind's rows ONCE and derive the target from the source
            # status actually read. Looping over `kind.terminal` instead re-ran an
            # identical query for every status sharing a target (`not_a_bug` and
            # `wont_fix` both map to `dismissed`) and then re-read the same source
            # status a second time per candidate row.
            for row in _live_rows(conn, item_kind=item_kind):
                src_status = entities.EntityRef(row["item_ref"], kind).status(conn)
                if src_status is None or src_status not in kind.terminal:
                    continue
                target = outcome_for(kind.name, src_status)
                if row["status"] == target:
                    continue
                found.append(
                    {
                        "item_ref": row["item_ref"],
                        "milestone_id": row["milestone_id"],
                        "from_status": row["status"],
                        "to_status": target,
                        "source_status": src_status,
                    }
                )
                if apply:
                    _apply_row(conn, row, target, actor)
    return {"applied": apply, "candidates": len(found), "items": found}
