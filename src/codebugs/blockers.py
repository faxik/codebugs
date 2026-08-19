"""Database layer — dependency/blocker tracking for codebugs."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from codebugs import db
from codebugs.entities import EntityRef, entity_kind
from codebugs.types import TRIGGER_TYPES, is_vocabulary_filter_active, utc_now


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



def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the blockers table if it doesn't exist."""
    for stmt in BLOCKERS_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def _entity_description(conn: sqlite3.Connection, entity_id: str, entity_type: str) -> str | None:
    """Description of an entity whose kind is already known (from a stored *_type column)."""
    return EntityRef(entity_id, entity_kind(entity_type)).description(conn)


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


def is_blocker_satisfied(conn: sqlite3.Connection, blocker: dict[str, Any]) -> bool:
    """Evaluate whether a blocker's condition is currently met."""
    if blocker["cancelled_at"]:
        return True
    if blocker["trigger_type"] == "entity_resolved":
        # blocked_by_type is only populated for entity_resolved triggers (NULL for date/manual),
        # so the resolver is only ever constructed inside this guard.
        ref = EntityRef(blocker["blocked_by"], entity_kind(blocker["blocked_by_type"]))
        return ref.is_resolved(conn)
    if blocker["trigger_type"] == "date":
        return blocker["trigger_at"] <= utc_now()
    if blocker["trigger_type"] == "manual":
        return blocker["resolved_at"] is not None
    return False


def _evaluate_blocker(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Convert a blocker row to a dict with computed satisfaction state."""
    d = db.row_to_dict(row)
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
    item_ref = EntityRef.of(item_id)
    item_type = item_ref.kind.name
    item_ref.require(conn)

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
        dep_ref = EntityRef.of(blocked_by)
        blocked_by_type = dep_ref.kind.name
        if not dep_ref.exists(conn):
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

    now = utc_now()
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
    result["item_description"] = _entity_description(conn, item_id, item_type)
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
    # The validation is right here in the body and was still bypassable: a falsey
    # `trigger_type` skipped the whole block, so `query_blockers(trigger_type=0)`
    # returned every blocker (CB-25). Found only by sweeping for the SHAPE of the
    # guard — the first sweep grepped for the filter *names* it already knew about
    # and therefore could not find this one.
    if is_vocabulary_filter_active(trigger_type):
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
        b["item_description"] = _entity_description(conn, b["item_id"], b["item_type"])
        if b["blocked_by"]:
            b["blocked_by_description"] = _entity_description(
                conn, b["blocked_by"], b["blocked_by_type"]
            )
            b["blocked_by_status"] = EntityRef(
                b["blocked_by"], entity_kind(b["blocked_by_type"])
            ).status(conn)
        blockers.append(b)

    return {"blockers": blockers, "total": len(blockers)}


def check_blockers(conn: sqlite3.Connection) -> dict[str, Any]:
    """Scan for currently actionable items."""
    rows = conn.execute(
        "SELECT * FROM blockers WHERE cancelled_at IS NULL ORDER BY item_id"
    ).fetchall()

    now = utc_now()
    items: dict[str, dict[str, Any]] = {}
    overdue = []

    for row in rows:
        b = _evaluate_blocker(conn, row)
        key = b["item_id"]
        if key not in items:
            items[key] = {
                "item_id": key,
                "item_type": b["item_type"],
                "description": _entity_description(conn, key, b["item_type"]),
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
    """Cancel or manually resolve a blocker.

    Every guard here is decided in Python from the row read at the top —
    ``cancelled_at``, then ``trigger_type`` and ``resolved_at`` — and each selects a
    different write, so the read and the write are one transaction (CB-24). Without it
    a concurrent ``cancel`` and ``resolve`` both observe ``cancelled_at IS NULL``, both
    pass their guard, and the row ends up simultaneously cancelled AND resolved — a
    state no serial ordering permits, reached with both callers reporting success.
    ``busy_timeout`` serializes the writes; it never touches the read before them.

    The response is built inside the block too, so the returned view of this blocker
    and of the item's remaining active blockers is the state this call actually
    produced rather than whatever a later writer has since done. ``_evaluate_blocker``
    is pure computation over a row, so it is safe here. Do not restore
    ``conn.commit()`` — ``db.txn`` owns the commit.
    """
    with db.txn(conn):
        row = conn.execute(
            "SELECT * FROM blockers WHERE id = ?", (blocker_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Blocker not found: {blocker_id}")

        b = db.row_to_dict(row)
        if b["cancelled_at"]:
            raise ValueError(f"Blocker {blocker_id} is already cancelled.")

        now = utc_now()

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

        updated = _evaluate_blocker(
            conn, conn.execute("SELECT * FROM blockers WHERE id = ?", (blocker_id,)).fetchone()
        )

        # Remaining active blockers for the same item
        remaining_rows = conn.execute(
            "SELECT * FROM blockers WHERE item_id = ? AND id != ? AND cancelled_at IS NULL",
            (b["item_id"], blocker_id),
        ).fetchall()
        remaining = [
            r for r in (_evaluate_blocker(conn, rr) for rr in remaining_rows) if r["is_active"]
        ]

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
            "description": _entity_description(conn, item_id, item_type),
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


def _active_counts(evaluated: list[dict[str, Any]]) -> dict[str, int]:
    """Active-blocker count per ``item_id``, from ALREADY-EVALUATED rows.

    PURE — it issues no SQL. That is the whole point: the expensive part is
    ``_get_active_blockers_by_type``, so every consumer that needs a different
    projection of the same evaluation derives it here instead of scanning again
    (CB-69). ``query_deferred_entities`` carried its own copy of this loop; a
    second definition of one aggregation contract in one file is the drift this
    repo keeps filing cards about.
    """
    counts: dict[str, int] = {}
    for b in evaluated:
        if b["is_active"]:
            counts[b["item_id"]] = counts.get(b["item_id"], 0) + 1
    return counts


def _restrict_ids(
    deferred: set[str], *, id: str | None = None, ids: list[str] | None = None
) -> list[str]:
    """Pure id-intersection half of ``deferred_id_restriction``."""
    requested = {i for i in ([id] if id else []) + list(ids or []) if i}
    return sorted(deferred & requested) if requested else sorted(deferred)


def deferred_ids_and_counts(
    conn: sqlite3.Connection,
    entity_type: str,
    *,
    id: str | None = None,
    ids: list[str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Both halves of the deferred projection from ONE evaluation (CB-69).

    The wrappers need the restricted id set AND the per-id active count, and were
    calling ``deferred_id_restriction`` then ``blocker_counts_for`` back to back —
    two independent scans of the blocker table, each re-resolving every
    ``entity_resolved`` dependency's status. Measured at 30 such blockers: 62
    statements where 31 would do.

    **Why this takes ``entity_type`` and returns BOTH halves, rather than the two
    helpers gaining a ``counts=`` parameter.** A precomputed summary is a plain
    dict that records neither its entity-type scope nor when it was evaluated, so
    a ``finding``-scoped summary handed to a ``requirement`` query returns the
    empty set — with no error — and the callers short-circuit an empty deferred
    set into a ZERO-ROW PAGE. That is CB-25/CB-28's failure shape, an empty queue
    indistinguishable from a correct one, installed by the fix for a performance
    bug. Taking the scope once makes the mismatch unrepresentable; returning both
    halves together makes pairing mismatched ones unrepresentable too.

    **"One pass" means one pass over the blockers TABLE, not one SQL statement.**
    Entity-trigger evaluation is still ``1 + B`` statements, because each
    ``entity_resolved`` blocker resolves its dependency individually. Batching that
    is a different mechanism and is deliberately out of scope.

    **Snapshot semantics — this IS a behaviour change, stated rather than asserted
    away.** The two calls previously took two independent snapshots and could
    legitimately disagree: a ``date`` trigger crossing its deadline between them
    yielded an id in the restricted set with a count of 0, and another connection
    can resolve or cancel a blocker in that window. The two halves are now always
    mutually consistent. A single-threaded differential test cannot observe this,
    which is why it is documented.
    """
    counts = _active_counts(_get_active_blockers_by_type(conn, entity_type))
    restricted = _restrict_ids(set(counts), id=id, ids=ids)
    return restricted, {i: counts[i] for i in restricted}


def query_deferred_entities(
    conn: sqlite3.Connection,
    entity_type: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Query entities that have active blockers, with blocker counts.

    Encapsulates the SQL + serialization so server.py doesn't need to reach
    into per-module row codecs.

    SUPERSEDED for the MCP `status="deferred"` path, and now called only by its own
    tests. It ignores every domain filter but limit/offset, which is what CB-28 was
    filed for; the wrappers instead use `deferred_id_restriction` and let the owning
    domain apply its filters. Kept because it is public and its tests pin the ranked
    ordering the forwarded path must also produce — but **do not route new callers
    here**, and if it is ever deleted, move those ordering assertions rather than
    dropping them.
    """
    evaluated = _get_active_blockers_by_type(conn, entity_type)
    active_counts = _active_counts(evaluated)

    kind = entity_kind(entity_type)
    if not active_counts:
        return {"grouped": False, "total": 0, "limit": limit, "offset": offset, kind.result_key: []}

    ids_list = sorted(active_counts)
    placeholders = ",".join("?" for _ in ids_list)
    # Declared precedence, not alphabetical (CB-20). `sort_col` is TEXT, so ordering
    # by it directly ranked `low` above `medium` for findings and — worse — `could`
    # above `must` for requirements, inverting the priority order outright. Params
    # are spliced in textual placeholder order: ids, then the rank, then limit/offset.
    rank_sql, rank_params = kind.order_by()
    rows = conn.execute(
        # noqa justified structurally: `table` is validated as a bare identifier in
        # EntityKind.__post_init__, and `rank_sql` is not caller text — it is a fixed
        # CASE template built by order_by() around that same validated column, whose
        # vocabulary values are BOUND, not interpolated (CB-22, CB-20).
        f"SELECT * FROM {kind.table} WHERE id IN ({placeholders}) "  # noqa: S608
        f"ORDER BY {rank_sql}, created_at DESC LIMIT ? OFFSET ?",
        ids_list + rank_params + [limit, offset],
    ).fetchall()

    rows_out = [db.row_to_dict(r) for r in rows]
    for e in rows_out:
        e["blocker_count"] = active_counts.get(e["id"], 0)

    return {"grouped": False, "total": len(ids_list), "limit": limit, "offset": offset, kind.result_key: rows_out}


def get_deferred_item_ids(
    conn: sqlite3.Connection, entity_type: str
) -> set[str]:
    """Return set of item IDs that have active blockers of given entity type.

    A caller that also needs the per-id counts must use ``deferred_ids_and_counts``
    instead of pairing this with ``blocker_counts_for`` — that pairing is the
    double evaluation CB-69 was filed for.
    """
    return set(_active_counts(_get_active_blockers_by_type(conn, entity_type)))


def deferred_id_restriction(
    conn: sqlite3.Connection,
    entity_type: str,
    *,
    id: str | None = None,
    ids: list[str] | None = None,
) -> list[str]:
    """The id set a ``status="deferred"`` query must be restricted to.

    This is the seam the 2026-04-04 blockers design specified and that the MCP
    wrappers never used: get the deferred ids here, then let the *owning domain*
    apply its own filters to them. Blockers stays generic over entity kinds and
    never learns what `severity` or `priority` mean (CB-28).

    Intersected with whatever `id` / `ids` the caller also supplied, so those keep
    working alongside the pseudo-status instead of being overwritten by it.

    **An empty result means "match nothing" and callers MUST short-circuit on it —
    never pass it on as `ids=[]`**, which every domain query reads as "no filter"
    and which would therefore return the entire table. That is CB-28's own defect
    reappearing inside CB-28's fix, the same way the naive predicate reintroduced
    CB-25 inside its fix. `TestDeferredEmptyIntersection` pins it.
    """
    return _restrict_ids(get_deferred_item_ids(conn, entity_type), id=id, ids=ids)


def blocker_counts_for(
    conn: sqlite3.Connection, entity_type: str, entity_ids: list[str]
) -> dict[str, int]:
    """Active-blocker count per id, for annotating a forwarded deferred query.

    **No production caller remains** — both wrappers moved to
    ``deferred_ids_and_counts`` in CB-69. This and ``deferred_id_restriction`` are
    kept because the differential tests use them as the before/after baseline that
    pins the halving. ("They are public" is NOT a second reason — nothing consumes
    this package as a Python library; the public surface is MCP and the CLI.) A
    caller that needs BOTH halves must use ``deferred_ids_and_counts``; calling
    these two together is exactly the double evaluation CB-69 was filed for.

    The signature keeps its positional arguments deliberately: the missing
    keyword-only ``*`` is CB-68's question over a nine-site population, and fixing
    one site by hand is what that card explicitly refuses.
    """
    wanted = set(entity_ids)
    counts = _active_counts(_get_active_blockers_by_type(conn, entity_type))
    return {i: c for i, c in counts.items() if i in wanted}


def get_deferred_counts(
    conn: sqlite3.Connection, entity_type: str
) -> dict[str, int]:
    """Return deferred/overdue/unblocked counts for an entity type."""
    evaluated = _get_active_blockers_by_type(conn, entity_type)
    now = utc_now()

    # `deferred_count` IS the size of the shared aggregation — "this item has at
    # least one active blocker" is precisely what `_active_counts` decides, so
    # re-deriving it here would be a third definition of one contract in one file.
    # The CB-69 plan justified leaving this function alone on the grounds that it
    # needs `trigger_type`/`trigger_at`; that is only half true, and only of
    # `overdue_count`.
    active_counts = _active_counts(evaluated)
    all_items = {b["item_id"] for b in evaluated}

    # The one projection a count map cannot carry: it needs each ACTIVE blocker's
    # trigger fields, not how many there are.
    overdue_items = {
        b["item_id"]
        for b in evaluated
        if b["is_active"]
        and b["trigger_type"] == "date"
        and b["trigger_at"]
        and b["trigger_at"] <= now
    }

    return {
        "deferred_count": len(active_counts),
        "overdue_count": len(overdue_items),
        "currently_unblocked_count": len(all_items) - len(active_counts),
    }


import json  # noqa: E402

from codebugs.db import register_schema, register_tool_provider, register_cli_provider  # noqa: E402

register_schema("blockers", ensure_schema, depends_on=("findings", "reqs"))


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


def register_cli(sub, commands):
    """Register blocker CLI subcommands."""
    p = sub.add_parser("blockers-add", help="Defer an item by adding a blocker")
    p.add_argument("item_id", help="The blocked entity (e.g. CB-5, FR-012)")
    p.add_argument("reason", help="Why it's blocked")
    p.add_argument("--blocked-by", help="Dependency entity")
    p.add_argument("--trigger-type", choices=["entity_resolved", "date", "manual"])
    p.add_argument("--trigger-at", help="Date for date triggers")
    commands["blockers-add"] = _cmd_blockers_add

    p = sub.add_parser("blockers-query", help="List blockers with filters")
    p.add_argument("--item-id", help="Filter by blocked item")
    p.add_argument("--blocked-by", help="Filter by dependency")
    p.add_argument("--trigger-type", choices=["entity_resolved", "date", "manual"])
    p.add_argument("--no-active-only", action="store_true", help="Include satisfied/cancelled")
    commands["blockers-query"] = _cmd_blockers_query

    p = sub.add_parser("blockers-check", help="Scan for actionable items")
    commands["blockers-check"] = _cmd_blockers_check

    p = sub.add_parser("blockers-resolve", help="Cancel or resolve a blocker")
    p.add_argument("blocker_id", type=int, help="Blocker row ID")
    p.add_argument("action", choices=["cancel", "resolve"])
    commands["blockers-resolve"] = _cmd_blockers_resolve


def _cmd_blockers_add(args):
    from codebugs import db
    conn = db.connect()
    try:
        result = add_blocker(
            conn, item_id=args.item_id, reason=args.reason,
            blocked_by=args.blocked_by, trigger_type=args.trigger_type,
            trigger_at=args.trigger_at,
        )
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


def _cmd_blockers_query(args):
    from codebugs import db
    conn = db.connect()
    try:
        result = query_blockers(
            conn, item_id=args.item_id, blocked_by=args.blocked_by,
            trigger_type=args.trigger_type, active_only=not args.no_active_only,
        )
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


def _cmd_blockers_check(args):
    from codebugs import db
    conn = db.connect()
    try:
        result = check_blockers(conn)
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


def _cmd_blockers_resolve(args):
    from codebugs import db
    conn = db.connect()
    try:
        result = resolve_blocker(conn, blocker_id=args.blocker_id, action=args.action)
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


register_cli_provider("blockers", register_cli)
