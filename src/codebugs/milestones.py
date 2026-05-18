"""Milestones & streams — release containers + standing buckets for codebugs.

Spec: ~/w/autosorter/.claude/plans/codebugs-milestones-streams-v1.md
Plan: docs/superpowers/plans/2026-05-11-milestones-streams.md
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from codebugs.types import utc_now


# --- Constants -------------------------------------------------------------

MILESTONE_KINDS = ("release", "stream")
MILESTONE_STATES = ("open", "closing", "shipped", "archived")
ITEM_KINDS = ("bug", "requirement", "external")
ITEM_SIZES = ("large", "small", "triage")
ITEM_STATUSES = ("open", "in_progress", "done", "deferred", "dismissed")

MILESTONE_ITEM_TERMINAL = frozenset({"done", "dismissed"})
AUTO_ROUTER_ACTOR = "auto-router"

SEED_MILESTONES = [
    ("stream/triage", "stream", "Inbox for unsorted findings. Default destination for new bugs."),
    ("stream/maintenance", "stream", "Deferred / boy-scout work. Pulled when release stream is blocked."),
    ("stream/security", "stream", "Urgent fixes. Preempts release work."),
    ("release/1.1", "release", "First post-1.0 feature release. Target date set later."),
]


# --- Schema ----------------------------------------------------------------

MILESTONES_SCHEMA = """\
CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('release', 'stream')),
    state TEXT NOT NULL DEFAULT 'open'
        CHECK(state IN ('open', 'closing', 'shipped', 'archived')),
    target_date TEXT,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    closed_at TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS milestone_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id TEXT NOT NULL REFERENCES milestones(id),
    item_kind TEXT NOT NULL
        CHECK(item_kind IN ('bug', 'requirement', 'external')),
    item_ref TEXT NOT NULL,
    size TEXT NOT NULL DEFAULT 'small'
        CHECK(size IN ('large', 'small', 'triage')),
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'in_progress', 'done', 'deferred', 'dismissed')),
    acceptance TEXT NOT NULL DEFAULT '',
    assigned_agent TEXT,
    pulled_at TEXT,
    done_at TEXT,
    done_commit TEXT,
    branch_only INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(milestone_id, item_kind, item_ref)
);

CREATE INDEX IF NOT EXISTS idx_mi_milestone_status ON milestone_items(milestone_id, status);
CREATE INDEX IF NOT EXISTS idx_mi_ref ON milestone_items(item_ref);
CREATE INDEX IF NOT EXISTS idx_mi_assigned ON milestone_items(assigned_agent) WHERE assigned_agent IS NOT NULL;

CREATE TABLE IF NOT EXISTS milestone_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    milestone_id TEXT NOT NULL,
    item_ref TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    reason TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_milestone_at ON milestone_audit(milestone_id, at);
CREATE INDEX IF NOT EXISTS idx_audit_item_ref ON milestone_audit(item_ref);

CREATE TABLE IF NOT EXISTS agent_capacity (
    agent_id TEXT PRIMARY KEY,
    large_held INTEGER NOT NULL DEFAULT 0,
    small_held INTEGER NOT NULL DEFAULT 0,
    triage_held INTEGER NOT NULL DEFAULT 0,
    last_pull_at TEXT,
    last_release_at TEXT
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create milestones tables + seed the 4 default rows. Idempotent."""
    for stmt in MILESTONES_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    now = utc_now()
    for mid, kind, description in SEED_MILESTONES:
        conn.execute(
            """INSERT OR IGNORE INTO milestones
               (id, kind, state, description, created_at) VALUES (?, ?, 'open', ?, ?)""",
            (mid, kind, description, now),
        )
    conn.commit()


# --- Helpers ---------------------------------------------------------------

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


# --- Auto-router (post-add hook) -------------------------------------------

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


# --- Milestone CRUD --------------------------------------------------------

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


# --- Item management -------------------------------------------------------

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


def _get_item_by_ref(conn: sqlite3.Connection, item_ref: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM milestone_items WHERE item_ref = ? ORDER BY id DESC LIMIT 1",
        (item_ref,),
    ).fetchone()
    if not row:
        raise KeyError(f"Item not found: {item_ref}")
    return _row_to_item(row)


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


# --- Triage (Phase 2) ------------------------------------------------------

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


# --- Capacity-aware pull (Phase 2) -----------------------------------------

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
    col = f"{size}_held"
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
    col = f"{size}_held"
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


def _eligibility_failure(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    milestone: dict[str, Any],
    capacity: dict[str, int],
    held: dict[str, int],
) -> str | None:
    """Return None if eligible, else a short reason string. Public-ish helper
    used by pull_next."""
    if item["status"] != "open":
        return f"not open (status={item['status']})"
    if item["item_kind"] != "external" and _has_active_blocker(conn, item["item_ref"]):
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
            row = conn.execute(
                "SELECT 1 FROM requirements WHERE id = ?", (fr,)
            ).fetchone()
            if not row:
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
    so the item is re-pullable)."""
    item = _get_item_by_ref(conn, item_ref)
    agent = item.get("assigned_agent")

    now = utc_now()
    if status == "abandoned":
        conn.execute(
            """UPDATE milestone_items SET status='open', assigned_agent=NULL,
               pulled_at=NULL, updated_at=? WHERE id=?""",
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
        conn.execute(
            f"UPDATE milestone_items SET {', '.join(sets)} WHERE id=?",
            params,
        )
        to_state = "done"
        action = "done"
    else:
        raise ValueError(f"Invalid release status: {status!r}. Use 'done' or 'abandoned'.")

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
    conn.commit()
    return _get_item_by_ref(conn, item_ref)


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


# --- Phase 3: branch tracking + close gate + defer -------------------------

def mark_branch_only(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    branch_name: str,
    actor: str = "user",
) -> dict[str, Any]:
    """Flag an item as living on a feature branch (not yet integrated to main).
    Called by worktree-setup.sh when a branch is created for this item."""
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
    conn.commit()
    return _get_item_by_ref(conn, item_ref)


def mark_integrated(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    commit: str,
    actor: str = "user",
) -> dict[str, Any]:
    """Mark an item as integrated on main: clears branch_only, sets done_commit,
    sets status=done. Called by worktree-finish.sh on successful main integration."""
    if not commit.strip():
        raise ValueError("commit is required for integration")
    item = _get_item_by_ref(conn, item_ref)
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
    conn.commit()
    return _get_item_by_ref(conn, item_ref)


def milestone_defer(
    conn: sqlite3.Connection,
    *,
    item_ref: str,
    to_milestone: str = "stream/maintenance",
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """Move an item to stream/maintenance (or another milestone) with status='deferred'."""
    item = _get_item_by_ref(conn, item_ref)
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
    conn.commit()
    return _get_item_by_ref(conn, item_ref)


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


# --- Audit -----------------------------------------------------------------

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


# --- MCP tools (Phase 1) ---------------------------------------------------

def register_tools(mcp, conn_factory) -> None:
    """Register milestones MCP tools."""

    @mcp.tool()
    def milestone_create(
        id: str,
        kind: str,
        description: str,
        target_date: str | None = None,
    ) -> dict[str, Any]:
        """Create a new milestone.

        Args:
            id: Slug identifier, e.g. 'release/1.2' or 'stream/security'.
            kind: 'release' or 'stream'. Streams never close.
            description: Short charter for the milestone.
            target_date: ISO date (e.g. '2026-06-30'). Optional, releases only.
        """
        with conn_factory() as conn:
            return create_milestone(
                conn, id=id, kind=kind, description=description,
                target_date=target_date,
            )

    @mcp.tool()
    def milestone_update(
        id: str,
        description: str | None = None,
        target_date: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Update mutable fields of a milestone. id and kind are immutable.

        Args:
            id: Milestone slug.
            description: New description (or None to skip).
            target_date: New ISO target date (or None to skip).
            state: New state (open / closing / shipped / archived).
        """
        with conn_factory() as conn:
            return update_milestone(
                conn, id=id, description=description,
                target_date=target_date, state=state,
            )

    @mcp.tool()
    def milestone_list(
        kind: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """List milestones with optional filters.

        Args:
            kind: 'release' or 'stream'.
            state: 'open' / 'closing' / 'shipped' / 'archived'.
        """
        with conn_factory() as conn:
            return list_milestones(conn, kind=kind, state=state)

    @mcp.tool()
    def milestone_status(id: str) -> dict[str, Any]:
        """Detailed rollup for one milestone: item counts by status / size,
        blockers, branch-only items, days to target.

        Args:
            id: Milestone slug.
        """
        with conn_factory() as conn:
            return get_milestone_status(conn, id=id)

    @mcp.tool()
    def milestone_add_item(
        milestone_id: str,
        item_kind: str,
        item_ref: str,
        size: str = "small",
        priority: int = 100,
        acceptance: str = "",
        linked_frs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Attach an item (bug / requirement / external) to a milestone.

        Args:
            milestone_id: Target milestone slug.
            item_kind: 'bug' (CB-N), 'requirement' (FR-N), or 'external'.
            item_ref: The id of the underlying entity (must exist for bug/req).
            size: 'large' (worktree+sprint), 'small' (1-2h), 'triage' (minutes).
            priority: Lower = higher priority. Default 100.
            acceptance: Markdown acceptance criteria. Required for size='large'.
            linked_frs: Optional list of FR ids to link (used by pull_next eligibility).
        """
        meta: dict[str, Any] = {}
        if linked_frs:
            meta["linked_frs"] = linked_frs
        with conn_factory() as conn:
            return add_milestone_item(
                conn,
                milestone_id=milestone_id,
                item_kind=item_kind,
                item_ref=item_ref,
                size=size,
                priority=priority,
                acceptance=acceptance,
                meta=meta or None,
            )

    @mcp.tool()
    def milestone_move_item(
        item_ref: str,
        to_milestone: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Move an item to a different milestone.

        Args:
            item_ref: The item to move (e.g. CB-5).
            to_milestone: Destination milestone slug.
            reason: One-line audit reason.
        """
        with conn_factory() as conn:
            return move_milestone_item(
                conn, item_ref=item_ref, to_milestone=to_milestone, reason=reason,
            )

    @mcp.tool()
    def milestone_set_status(
        item_ref: str,
        status: str,
        commit: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Set an item's status. Records done_commit if status is terminal.

        Args:
            item_ref: The item id (e.g. CB-5).
            status: open / in_progress / done / deferred / dismissed.
            commit: SHA where the work landed on main (recorded for terminal status).
            reason: Optional audit reason.
        """
        with conn_factory() as conn:
            return set_item_status(
                conn, item_ref=item_ref, status=status,
                commit=commit, reason=reason,
            )

    @mcp.tool()
    def milestone_audit_query(
        milestone_id: str | None = None,
        item_ref: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Audit log query with filters. Returns most-recent rows first.

        Args:
            milestone_id: Filter by milestone slug.
            item_ref: Filter by item id.
            actor: Filter by actor.
            since: ISO datetime — only rows at or after this time.
            limit: Max rows (default 200).
        """
        with conn_factory() as conn:
            return query_audit(
                conn, milestone_id=milestone_id, item_ref=item_ref,
                actor=actor, since=since, limit=limit,
            )

    # --- Phase 2: triage + pull_next + WIP ---
    # Tools are bound with explicit names to expose spec-mandated identifiers
    # (`triage_inbox`, `pull_next`, etc.) without shadowing module-level
    # functions of the same name.

    @mcp.tool(name="triage_inbox")
    def _triage_inbox(limit: int = 50) -> list[dict[str, Any]]:
        """List open items in stream/triage, oldest first.

        Args:
            limit: Max rows (default 50).
        """
        with conn_factory() as conn:
            return triage_inbox(conn, limit=limit)

    @mcp.tool(name="triage_dismiss")
    def _triage_dismiss(bug_id: str, reason: str) -> dict[str, Any]:
        """Mark a triage item as dismissed. Propagates to the underlying entity:
        bug → finding 'not_a_bug'; requirement → requirement 'obsolete';
        external → no propagation.

        Args:
            bug_id: The item id (e.g. CB-5).
            reason: Required one-line audit reason.
        """
        with conn_factory() as conn:
            return triage_dismiss(conn, bug_id=bug_id, reason=reason)

    @mcp.tool(name="triage_promote")
    def _triage_promote(
        bug_id: str,
        to_milestone: str,
        size: str = "small",
        acceptance: str = "",
        priority: int = 100,
        linked_frs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Move a triage item to a target milestone.

        Args:
            bug_id: The item id (e.g. CB-5).
            to_milestone: Destination milestone slug.
            size: 'large' / 'small' / 'triage'. Default 'small'.
            acceptance: Required for size='large'.
            priority: Lower = higher priority. Default 100.
            linked_frs: FR ids linked to this item (required for size='large' bugs
                in release milestones to be pull-eligible).
        """
        with conn_factory() as conn:
            return triage_promote(
                conn, bug_id=bug_id, to_milestone=to_milestone,
                size=size, acceptance=acceptance, priority=priority,
                linked_frs=linked_frs,
            )

    @mcp.tool(name="pull_next")
    def _pull_next(
        agent_id: str,
        capacity: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        """Claim the next eligible item for the calling agent. Returns the
        item dict or None if nothing eligible.

        Priority: stream/security > release/* (earliest target_date) >
                  stream/triage > stream/maintenance.

        Args:
            agent_id: Stable id for the calling agent. Used as actor in audit.
            capacity: Dict like {'large':1,'small':2,'triage':5}. Defaults
                to those values if not provided.
        """
        cap = capacity or {"large": 1, "small": 2, "triage": 5}
        with conn_factory() as conn:
            return pull_next(conn, agent_id=agent_id, capacity=cap)

    @mcp.tool(name="release_item")
    def _release_item(
        item_ref: str,
        status: str = "done",
        commit: str | None = None,
    ) -> dict[str, Any]:
        """Free agent capacity for an item.

        Args:
            item_ref: The item id (e.g. CB-5).
            status: 'done' (terminal) or 'abandoned' (returns item to 'open').
            commit: SHA where the work landed (recorded if status='done').
        """
        with conn_factory() as conn:
            return release_item(
                conn, item_ref=item_ref, status=status, commit=commit,
            )

    @mcp.tool(name="wip_status")
    def _wip_status(agent_id: str | None = None) -> list[dict[str, Any]]:
        """Snapshot of agent_capacity. agent_id=None returns all agents.

        Args:
            agent_id: Filter to one agent (None = all).
        """
        with conn_factory() as conn:
            return get_wip_status(conn, agent_id=agent_id)

    # --- Phase 3: branch tracking + close gate + defer ---

    @mcp.tool(name="mark_branch_only")
    def _mark_branch_only(item_ref: str, branch_name: str) -> dict[str, Any]:
        """Flag an item as living on a feature branch (not yet integrated).
        Called by worktree-setup.sh when a branch is created.

        Args:
            item_ref: The item id (e.g. CB-5).
            branch_name: Git branch holding the work.
        """
        with conn_factory() as conn:
            return mark_branch_only(
                conn, item_ref=item_ref, branch_name=branch_name,
            )

    @mcp.tool(name="mark_integrated")
    def _mark_integrated(item_ref: str, commit: str) -> dict[str, Any]:
        """Mark an item as merged to main. Sets done_commit, status='done',
        clears branch_only. Called by worktree-finish.sh.

        Args:
            item_ref: The item id (e.g. CB-5).
            commit: Commit SHA where the work landed on main.
        """
        with conn_factory() as conn:
            return mark_integrated(conn, item_ref=item_ref, commit=commit)

    @mcp.tool(name="milestone_close")
    def _milestone_close(
        id: str,
        force: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        """Close a release milestone. Refuses if items are unfinished, on a
        branch, or have unresolved blockers. Streams cannot be closed.

        Args:
            id: Milestone slug (must be kind='release').
            force: Override the close-gate (still won't close streams). Audit-logged.
            reason: Audit reason for the close.
        """
        with conn_factory() as conn:
            return milestone_close(conn, id=id, force=force, reason=reason)

    @mcp.tool(name="milestone_defer")
    def _milestone_defer(
        item_ref: str,
        to_milestone: str = "stream/maintenance",
        reason: str = "",
    ) -> dict[str, Any]:
        """Move an item to stream/maintenance (or another milestone) and
        mark it deferred.

        Args:
            item_ref: The item to defer.
            to_milestone: Destination (default 'stream/maintenance').
            reason: Optional audit reason.
        """
        with conn_factory() as conn:
            return milestone_defer(
                conn, item_ref=item_ref, to_milestone=to_milestone,
                reason=reason,
            )


# --- CLI (Phase 1, subset) -------------------------------------------------

def register_cli(sub, commands) -> None:
    """Register milestones CLI subcommands (flat domain-action pattern)."""
    import argparse
    import sys

    from codebugs.fmt import format_table

    def _cmd_milestone_list(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = list_milestones(conn, kind=args.kind, state=args.state)
        finally:
            conn.close()
        if not rows:
            print("(no milestones)")
            return
        data = [
            {
                "id": r["id"],
                "kind": r["kind"],
                "state": r["state"],
                "target": r.get("target_date") or "-",
                "description": r["description"][:60],
            }
            for r in rows
        ]
        print(format_table(data, ["id", "kind", "state", "target", "description"]))

    def _cmd_milestone_status(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            status = get_milestone_status(conn, id=args.id)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        m = status["milestone"]
        print(f"{m['id']}  ({m['kind']}, state={m['state']})")
        if m.get("target_date"):
            countdown = status.get("days_to_target")
            cs = f" ({countdown} days)" if countdown is not None else ""
            print(f"  target: {m['target_date']}{cs}")
        if m["description"]:
            print(f"  {m['description']}")
        print()
        print(f"Items: {status['total_items']} total "
              f"({status['open_items']} open/in_progress, "
              f"{status['done_items']} done)")
        print()
        print("  By status:")
        for k, v in status["by_status"].items():
            if v:
                print(f"    {k:14s} {v:>4d}")
        print("  By size:")
        for k, v in status["by_size"].items():
            if v:
                print(f"    {k:14s} {v:>4d}")
        if status["branch_only_items"]:
            print()
            print(f"  Branch-only: {', '.join(status['branch_only_items'])}")
        if status["blocked_items"]:
            print(f"  Blocked: {', '.join(status['blocked_items'])}")

    def _cmd_milestone_audit(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = query_audit(
                conn,
                milestone_id=args.milestone or None,
                item_ref=args.item or None,
                actor=args.actor or None,
                limit=args.limit or 200,
            )
        finally:
            conn.close()
        if not rows:
            print("(no audit entries)")
            return
        data = [
            {
                "at": r["at"],
                "actor": r["actor"],
                "action": r["action"],
                "milestone": r["milestone_id"],
                "item": r.get("item_ref") or "-",
                "from": r.get("from_state") or "-",
                "to": r.get("to_state") or "-",
                "reason": (r.get("reason") or "")[:30],
            }
            for r in rows
        ]
        print(format_table(
            data,
            ["at", "actor", "action", "milestone", "item", "from", "to", "reason"],
        ))

    p = sub.add_parser("milestone-list", help="List milestones")
    p.add_argument("--kind", help="Filter by kind (release|stream)")
    p.add_argument("--state", help="Filter by state")

    p = sub.add_parser("milestone-status", help="Show milestone rollup")
    p.add_argument("id", help="Milestone slug (e.g. release/1.1)")

    p = sub.add_parser("milestone-audit", help="Show audit log")
    p.add_argument("--milestone", help="Filter by milestone slug")
    p.add_argument("--item", help="Filter by item ref")
    p.add_argument("--actor", help="Filter by actor")
    p.add_argument("--limit", type=int, help="Row limit (default 200)")

    def _cmd_triage_inbox(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = triage_inbox(conn, limit=args.limit or 50)
        finally:
            conn.close()
        if not rows:
            print("(triage inbox empty)")
            return
        data = [
            {
                "ref": r["item_ref"],
                "kind": r["item_kind"],
                "size": r["size"],
                "priority": str(r["priority"]),
                "created": r["created_at"],
            }
            for r in rows
        ]
        print(format_table(data, ["ref", "kind", "size", "priority", "created"]))

    def _cmd_wip_status(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = get_wip_status(conn, agent_id=args.agent)
        finally:
            conn.close()
        if not rows:
            print("(no agent capacity records)")
            return
        data = [
            {
                "agent": r["agent_id"],
                "large": str(r["large_held"]),
                "small": str(r["small_held"]),
                "triage": str(r["triage_held"]),
                "last_pull": r.get("last_pull_at") or "-",
            }
            for r in rows
        ]
        print(format_table(data, ["agent", "large", "small", "triage", "last_pull"]))

    p = sub.add_parser("triage-inbox", help="List items in stream/triage")
    p.add_argument("--limit", type=int, help="Row limit (default 50)")

    p = sub.add_parser("wip-status", help="Show agent capacity snapshot")
    p.add_argument("--agent", help="Filter by agent id (default: all)")

    def _cmd_milestone_mark_branch(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            item = mark_branch_only(
                conn, item_ref=args.item_ref, branch_name=args.branch,
            )
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        print(f"branch-only: {item['item_ref']} @ {args.branch}")

    def _cmd_milestone_mark_integrated(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            item = mark_integrated(
                conn, item_ref=args.item_ref, commit=args.commit,
            )
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        print(f"integrated: {item['item_ref']} @ {item['done_commit']}")

    p = sub.add_parser(
        "milestone-mark-branch",
        help="Flag an item as living on a feature branch (not yet integrated)",
    )
    p.add_argument("item_ref", help="Item id (e.g. CB-1234)")
    p.add_argument("branch", help="Git branch name holding the work")

    p = sub.add_parser(
        "milestone-mark-integrated",
        help="Mark an item as merged to main; clears branch-only, records done_commit",
    )
    p.add_argument("item_ref", help="Item id (e.g. CB-1234)")
    p.add_argument("commit", help="Commit SHA where the work landed on main")

    commands.update({
        "milestone-list": _cmd_milestone_list,
        "milestone-status": _cmd_milestone_status,
        "milestone-audit": _cmd_milestone_audit,
        "triage-inbox": _cmd_triage_inbox,
        "wip-status": _cmd_wip_status,
        "milestone-mark-branch": _cmd_milestone_mark_branch,
        "milestone-mark-integrated": _cmd_milestone_mark_integrated,
    })


# --- Module-level registrations --------------------------------------------

from codebugs.db import register_schema, register_tool_provider, register_cli_provider, register_post_add_hook  # noqa: E402

register_schema("milestones", ensure_schema, depends_on=("findings", "reqs", "blockers"))
register_tool_provider("milestones", register_tools)
register_cli_provider("milestones", register_cli)
register_post_add_hook("milestones.auto_route", _auto_route_finding)
