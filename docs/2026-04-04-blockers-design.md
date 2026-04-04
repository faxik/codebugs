# Blockers: Dynamic Dependency Tracking for Codebugs

**Date:** 2026-04-04
**Status:** Draft

## Problem

When working across sessions, valuable ideas and incomplete plan phases get lost.
Dependencies waiting for a feature to land, phases postponed until a bug is fixed,
or items parked for a time-based evaluation period — all of these are easy to forget
once a conversation ends. Codebugs already tracks findings and requirements, but has
no way to express "CB-5 can't proceed until CB-3 is done" or "revisit FR-012 after
a week of testing."

## Goals

1. Track **internal blockers** between codebugs entities (findings ↔ findings,
   findings ↔ requirements, requirements ↔ requirements).
2. Support **time-based triggers** ("revisit after April 10") and **manual holds**.
3. Provide **dynamic evaluation** — blocker state is computed from live entity status,
   so reopening a bug automatically re-blocks dependent items.
4. Surface deferred/unblocked items to both the AI assistant (for session continuity)
   and the human (for backlog review).

## Non-Goals

- External dependency tracking (upstream library releases, third-party APIs).
- Workflow automation (auto-assigning work when something unblocks).
- Dependency cycle detection (kept simple; cycles are queryable but not prevented).

## Design

### Data Model

A single `blockers` table in the shared `findings.db`. Each row represents one
dependency edge: "item X is blocked because of Y."

```sql
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
```

**Column semantics:**

| Column | Purpose |
|---|---|
| `item_id` / `item_type` | The blocked entity (CB-5, FR-012) |
| `blocked_by` / `blocked_by_type` | The dependency (CB-3, FR-007). NULL for manual/standalone date triggers. |
| `reason` | Human-readable explanation of why it's blocked |
| `trigger_type` | How unblocking is evaluated: `entity_resolved`, `date`, or `manual` |
| `trigger_at` | ISO 8601 date for `date` triggers. NULL otherwise. |
| `resolved_at` | Timestamp when a `manual` trigger was explicitly resolved. NULL until then. |
| `cancelled_at` | Timestamp when the blocker was cancelled (relationship no longer relevant). Any trigger type. |

**Key properties:**
- An item can have **multiple** blockers.
- An item is **deferred** when it has at least one active (unsatisfied, uncancelled) blocker.
- `blocked_by` is optional — `manual` and standalone `date` triggers don't require an entity ref.

### Dynamic Evaluation (No Stored Status)

Blocker state is **computed at query time**, not stored. This is the core design
decision — it makes the system bullet-proof against status oscillation (e.g., a bug
fixed then reopened).

**Satisfaction rules:**

| Trigger type | Satisfied when | Reverts when |
|---|---|---|
| `entity_resolved` | `blocked_by` entity is in a terminal status | Entity is reopened |
| `date` | `trigger_at <= now()` | Never (time is monotonic) |
| `manual` | `resolved_at IS NOT NULL` | `resolved_at` is cleared |

A blocker is **cancelled** when `cancelled_at IS NOT NULL` (permanent, does not revert).

A blocker is **active** = not cancelled AND not satisfied.

**Terminal statuses:**
- Findings: `fixed`, `not_a_bug`, `wont_fix`
- Requirements: `Implemented`, `Verified`, `Superseded`, `Obsolete`

Note: `stale` (findings) is deliberately not terminal — stale items may return.

**Evaluation helper:**

```python
TERMINAL_STATUSES = {
    "finding": {"fixed", "not_a_bug", "wont_fix"},
    "requirement": {"Implemented", "Verified", "Superseded", "Obsolete"},
}

def is_blocker_satisfied(conn, blocker) -> bool:
    if blocker["cancelled_at"]:
        return True
    if blocker["trigger_type"] == "entity_resolved":
        status = _get_entity_status(conn, blocker["blocked_by"], blocker["blocked_by_type"])
        return status in TERMINAL_STATUSES[blocker["blocked_by_type"]]
    if blocker["trigger_type"] == "date":
        return blocker["trigger_at"] <= _now()
    if blocker["trigger_type"] == "manual":
        return blocker["resolved_at"] is not None
    return False
```

### ID Detection

Entity type is inferred from ID prefix, following existing codebugs conventions:

| Prefix | Type |
|---|---|
| `CB-` | finding |
| `FR-`, `NFR-` | requirement |

This avoids requiring the caller to specify `item_type` — it's derived automatically.

### MCP Tools

Four new tools in `blockers.py`, registered under mode `blockers` (and `all`).

#### `blockers_add`

Add a blocker to defer an item.

**Args:**
- `item_id` (str, required) — the blocked entity (e.g., "CB-5")
- `reason` (str, required) — why it's blocked
- `blocked_by` (str, optional) — dependency entity (e.g., "CB-3")
- `trigger_type` (str, optional) — `entity_resolved` | `date` | `manual`.
  Defaults to `entity_resolved` if `blocked_by` is provided, `manual` otherwise.
- `trigger_at` (str, optional) — ISO 8601 date for `date` triggers

**Validation:**
- Both `item_id` and `blocked_by` (if provided) must exist in their respective tables.
- Self-blocking is rejected (`item_id == blocked_by`).
- Duplicate active blockers are rejected: same `item_id` + same `blocked_by` + same
  `trigger_type` where no existing matching blocker has `cancelled_at` set. For `date`
  triggers, same `item_id` + same `trigger_at` is considered a duplicate.
- `trigger_at` is required when `trigger_type = 'date'`.
- `blocked_by` is required when `trigger_type = 'entity_resolved'`.

**Returns:** Created blocker record with item summary.

#### `blockers_query`

List blockers with filters. Each result includes computed `is_satisfied` and `is_cancelled`.

**Args:**
- `item_id` (str, optional) — filter by blocked item
- `blocked_by` (str, optional) — filter by dependency ("what does CB-3 unblock?")
- `trigger_type` (str, optional) — filter by trigger type
- `active_only` (bool, optional, default True) — only unsatisfied, uncancelled blockers

**Returns:** List of blocker records with computed state and joined entity descriptions.

#### `blockers_check`

Scan for newly actionable items. No args required.

**Logic:**
1. Fetch all active blockers, evaluate each.
2. Group by `item_id`.
3. For each item: if ALL blockers are satisfied, it's **newly actionable**.
4. Items with some satisfied and some still active get reported with remaining blockers.

**Returns:**
```json
{
  "actionable": [
    {"item_id": "CB-5", "item_type": "finding", "description": "...",
     "satisfied_blockers": [...]}
  ],
  "partially_unblocked": [
    {"item_id": "FR-015", "remaining": 1, "satisfied": 1,
     "remaining_blockers": [...]}
  ],
  "overdue_date_triggers": [
    {"id": 8, "item_id": "CB-7", "trigger_at": "2026-04-01", "reason": "..."}
  ]
}
```

#### `blockers_resolve`

Cancel or manually resolve a blocker.

**Args:**
- `blocker_id` (int, required) — the blocker row ID
- `action` (str, required) — `cancel` | `resolve`

**Validation:**
- `resolve` is only valid for `manual` triggers.
- `cancel` works for any trigger type.
- Already cancelled/resolved blockers are rejected.

**Returns:** Updated blocker + remaining active blockers for the item.

### Integration with Existing Tools

#### Response augmentation on status change

When `update_finding` or `reqs_update` changes an entity to a terminal status:

1. Query `blockers WHERE blocked_by = <entity_id> AND cancelled_at IS NULL`.
2. Evaluate each blocker — report any that just became satisfied.
3. For each blocked item where ALL blockers are now satisfied, include in `unblocked_items`.
4. **Informational only** — no state changes to blocker rows.

Response shape (appended to existing return dict):

```json
{
  "id": "CB-3",
  "status": "fixed",
  "unblocked_items": [
    {"item_id": "CB-5", "reason": "needs error handling refactor",
     "all_blockers_satisfied": true},
    {"item_id": "FR-015", "reason": "depends on CB-3 fix",
     "all_blockers_satisfied": false, "remaining_blockers": 1}
  ]
}
```

#### Query augmentation

`query_findings` and `reqs_query` gain:
- Pseudo-status `"deferred"`: returns only items with at least one active blocker.
- Each result annotated with `blocker_count` (number of active blockers, 0 if none).

#### Summary augmentation

`summary` and `reqs_summary` gain:
- `deferred_count`: items with active blockers.
- `overdue_count`: items with date triggers past due.
- `newly_unblocked_count`: items where all blockers are satisfied but item is still open/planned.

### Module Structure

New file: `src/codebugs/blockers.py`

Follows existing patterns:
- `ensure_schema(conn)` — called from `db.connect()`
- Pure functions taking `conn` as first arg, returning dicts
- `_now()` for ISO 8601 timestamps
- `_detect_entity_type(entity_id)` for prefix-based type inference

Registration in `server.py`:
- New mode `blockers` with 4 tools
- Included in `all` mode
- `ensure_schema` called in `db.connect()` alongside other modules

### Testing Strategy

- Unit tests in `tests/test_blockers.py`:
  - CRUD: add, query, resolve, cancel
  - Dynamic evaluation: entity_resolved triggers satisfy/revert correctly
  - Date triggers: satisfied when past due
  - Manual triggers: resolve/unresolve
  - Duplicate/self-block rejection
  - Cross-entity blocking (finding blocks requirement)
- Integration tests:
  - `update_finding` response augmentation with unblocked items
  - `query_findings(status="deferred")` filter
  - `summary` deferred counts
  - Full workflow: add blocker → fix dependency → check → see unblocked

### Verification

1. Run `pytest tests/test_blockers.py` — all unit tests pass.
2. Run `pytest tests/` — no regressions in existing modules.
3. Manual MCP workflow:
   - Add a finding CB-1, add a finding CB-2
   - `blockers_add(item_id="CB-2", blocked_by="CB-1", reason="depends on auth fix")`
   - `query_findings(status="deferred")` → CB-2 appears
   - `update_finding(id="CB-1", status="fixed")` → response includes `unblocked_items: [CB-2]`
   - `blockers_check()` → CB-2 is actionable
   - Reopen CB-1: `update_finding(id="CB-1", status="open")` → CB-2 is deferred again
