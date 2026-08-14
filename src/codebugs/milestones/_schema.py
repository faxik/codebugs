"""Milestones schema + constants. Single schema owner for the package."""

from __future__ import annotations

import sqlite3

from codebugs.types import ENTITY_FINDING, ENTITY_REQUIREMENT, utc_now


MILESTONE_KINDS = ("release", "stream")


MILESTONE_STATES = ("open", "closing", "shipped", "archived")


ITEM_KINDS = ("bug", "requirement", "external")


ITEM_SIZES = ("large", "small", "triage")


ITEM_STATUSES = ("open", "in_progress", "done", "deferred", "dismissed")


MILESTONE_ITEM_TERMINAL = frozenset({"done", "dismissed"})


AUTO_ROUTER_ACTOR = "auto-router"


RECONCILER_ACTOR = "auto-reconciler"


# --- Terminal-source projection (CB-26) -------------------------------------
#
# A milestone item is a PROJECTION of a source entity. When the source reaches a
# terminal status the item must follow, or the derived queues keep offering work
# that is already finished.
#
# Keyed by (entity kind, source status) rather than by status alone. The two
# vocabularies happen to be disjoint today, but a flat map would silently pick a
# winner the day they are not, and this table is exactly the kind of enumeration
# this repo has repeatedly found drifts from its source of truth.
#
# `_outcome_for` FAILS CLOSED on an unknown pair, and
# `TestTerminalOutcomeMapIsComplete` asserts these keys equal
# `types.FINDING_TERMINAL` / `types.REQUIREMENT_TERMINAL`, so adding a terminal
# status without deciding its projection fails CI rather than defaulting.
TERMINAL_ITEM_OUTCOME: dict[str, dict[str, str]] = {
    ENTITY_FINDING: {
        "fixed": "done",
        "not_a_bug": "dismissed",
        "wont_fix": "dismissed",
    },
    ENTITY_REQUIREMENT: {
        "implemented": "done",
        "verified": "done",
        "superseded": "dismissed",
        "obsolete": "dismissed",
    },
}


# An entity kind names its own rows in `milestone_items.item_kind`. Selecting on
# `item_ref` alone is WRONG: `_validate_item_ref` skips validation for externals
# and UNIQUE includes `item_kind`, so `(bug, CB-1)` and `(external, CB-1)` are
# both legal rows and only the first projects the finding CB-1.
ENTITY_KIND_TO_ITEM_KIND: dict[str, str] = {
    ENTITY_FINDING: "bug",
    ENTITY_REQUIREMENT: "requirement",
}


def outcome_for(entity_kind: str, source_status: str) -> str:
    """Terminal item status for a terminal source status. Raises on an unknown pair."""
    try:
        return TERMINAL_ITEM_OUTCOME[entity_kind][source_status]
    except KeyError:
        raise ValueError(
            f"No declared milestone-item projection for {entity_kind} "
            f"status {source_status!r}. Add it to TERMINAL_ITEM_OUTCOME."
        ) from None


SEED_MILESTONES = [
    ("stream/triage", "stream", "Inbox for unsorted findings. Default destination for new bugs."),
    ("stream/maintenance", "stream", "Deferred / boy-scout work. Pulled when release stream is blocked."),
    ("stream/security", "stream", "Urgent fixes. Preempts release work."),
    ("release/1.1", "release", "First post-1.0 feature release. Target date set later."),
]


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
