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
* **Defensive** — ``live_source_clause`` filters the queue reads themselves, in
  SQL. This is not belt-and-braces, it is the only reason the invariant can be
  claimed at all: several writers bypass the hook entirely (``add_milestone_item``
  inserts ``open`` even for an already-terminal source, ``set_item_status`` can
  reopen, ``release_item(status='abandoned')`` reopens unconditionally, the
  requirements bulk/markdown importers replace rows with no hook, and
  ``EntityRef.set_status`` never fires hooks by design).

Scoped to ``kind='stream'`` milestones on purpose — see ``_STREAM_ONLY`` below.

**Where the defensive filter IS applied.** Three queue reads, each answering "what
work should I pick up", all now going through ``live_source_clause`` (CB-31):
``triage.triage_inbox``, ``capacity._candidates`` and
``foundation.list_milestone_items(live_only=True)``. That third one was added
after CB-31 was filed and remembered the rule on its own — which is precisely the
argument the card made, so the seam exists to stop the fourth from having to.

**Where it is NOT applied, and why — both sites, deliberately.**

* ``foundation.get_milestone_status`` answers "what does this milestone contain",
  and a rollup that hid rows would misreport the stored state it exists to
  describe.
* ``closegate``'s unfinished gate reads stored status, so a stored-``open`` item
  over a terminal source produces a FALSE REFUSAL of ``milestone_close`` — and
  that is correct: ``done_commit`` is never a gate, so hiding those rows would let
  a release close over a missed integration (CB-32, and see ``_STREAM_ONLY``).

An exclusion list that omits a site is the same defect as a call-site list that
omits one, which is why both are named here rather than only the obvious one.

**Why a query builder and not a VIEW.** Measured: ``CREATE VIEW`` over a missing
source table SUCCEEDS, and the first ``SELECT`` from it raises ``no such table``.
A view therefore fails CLOSED, with a crash, for exactly the raw-connection
callers this design must keep working — the opposite of the contract. (The
weaker objection, that a view's DDL would hardcode the terminal sets, does not
hold: it could be regenerated from ``kind.terminal`` on every schema init.)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from codebugs import entities
from codebugs.types import is_sql_identifier, utc_now

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

    Raw ``sqlite3.connect()`` callers invoke ``add_finding`` / ``update_finding``
    on connections that never initialised milestones. Probing only
    ``milestone_items`` would still explode on the audit insert, so probe what is
    actually written.

    This docstring used to cite ``tests/test_sweep.py``; that file initialises only
    the sweep schema and never reaches this path. The real precedents are the
    milestone hooks running on findings-only databases —
    ``tests/test_milestones_reconcile.py:333-348`` and
    ``tests/test_milestones.py:360-375``. The distinction matters because CB-31's
    plan inherited the wrong citation from here and applied it to QUEUE READS,
    which have a different set of raw-connection callers again.
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


def live_source_clause(
    conn: sqlite3.Connection, *, alias: str
) -> tuple[str, list[Any]]:
    """SQL keeping only ``milestone_items`` rows whose SOURCE entity is still live.

    The COMPOSED twin of ``source_is_terminal``, for the reads that must not hand
    out finished work. Returns ``(fragment, params)`` where the fragment is ALWAYS
    a valid boolean expression — the literal ``1`` when no source table is present
    — so no call site needs an ``if fragment:`` branch. Point-of-use discipline is
    the wrong enforcement layer (CB-41), including for this seam's own adoption.

    Both halves derive from ``kind.terminal``, so they cannot drift; a differential
    test asserts they agree row for row.

    **Why this is ``NOT EXISTS`` and never ``status NOT IN (...)`` over a LEFT JOIN
    or a scalar subquery.** A missing source row yields NULL, ``NULL NOT IN (...)``
    is NULL, and ``WHERE NULL`` EXCLUDES the row — silently inverting fail-open into
    a queue that hides work. ``NOT EXISTS`` is never NULL, so a row is hidden only on
    AFFIRMATIVE proof: recognised kind, existing source table, matching row, terminal
    status. Every unknown keeps the row live, exactly as ``source_is_terminal`` does.

    **``alias`` is REQUIRED, and that is not stylistic.** The correlated columns must
    be qualified, because inside the subquery an unqualified ``item_kind`` /
    ``item_ref`` resolves against the SOURCE table first and only reaches
    ``milestone_items`` because ``findings`` and ``requirements`` happen to lack those
    column names today. Measured with an ``item_kind`` column added to ``findings``:
    the unqualified form stopped referencing the outer ``item_kind`` altogether and
    hid an ``external`` row that must stay live — failing CLOSED, hiding live work,
    which is the one failure this predicate exists to prevent. It is a BARE
    identifier (this function appends the ``.``) so a caller cannot smuggle in a
    fragment, and it is validated: ``EntityKind`` validates ``table`` at construction
    (CB-22), nothing validated ``alias``.

    Callers must compute this ONCE per traversal and reuse it. It probes
    ``sqlite_master`` per kind, so rebuilding it per bucket inside ``pull_next``'s
    ``BEGIN IMMEDIATE`` would add reads to an exclusive-lock hold — the opposite of
    why this exists.
    """
    if not is_sql_identifier(alias):
        raise ValueError(
            f"alias must be a plain SQL identifier, got {alias!r}"
        )

    fragments: list[str] = []
    params: list[Any] = []
    for kind in entities.ENTITY_KINDS:
        # `.get`, never `[...]`: ENTITY_KIND_TO_ITEM_KIND is not total over
        # ENTITY_KINDS by contract, and an unmapped kind must fail OPEN (skip the
        # fragment) exactly as `_entity_kind_for` does, not raise KeyError.
        item_kind = ENTITY_KIND_TO_ITEM_KIND.get(kind.name)
        if item_kind is None or not kind.terminal:
            continue
        # An absent source table drops out of BOTH the SQL and the params, which
        # keeps raw-sqlite3 callers (milestone hooks on a findings-only database)
        # working and is fail-open by construction.
        if not _table_exists(conn, kind.table):
            continue
        # sorted(): frozenset iteration order is unstable, and unstable SQL text
        # makes a template-asserting test flaky.
        terminal = sorted(kind.terminal)
        placeholders = ", ".join("?" * len(terminal))
        fragments.append(  # noqa: S608 - kind.table validated in EntityKind.__post_init__
            f"NOT EXISTS (SELECT 1 FROM {kind.table} _src "
            f"WHERE {alias}.item_kind = ? AND _src.id = {alias}.item_ref "
            f"AND _src.status IN ({placeholders}))"
        )
        params.append(item_kind)
        params.extend(terminal)

    if not fragments:
        return "1", []
    return " AND ".join(fragments), params


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
