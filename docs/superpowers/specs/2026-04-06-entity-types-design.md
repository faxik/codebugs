# ARCH-003: Entity Type Unification

**Date:** 2026-04-06
**Status:** Design approved
**Requirement:** ARCH-003
**Depends on:** ARCH-001, ARCH-002

## Problem

Status, priority, severity, and entity-type constants are fragmented across 3 modules with naming collisions and inconsistent conventions:

- `db.py:292` — `VALID_STATUSES = ("open", "in_progress", "fixed", "not_a_bug", "wont_fix", "stale")` (snake_case)
- `reqs.py:39` — `VALID_STATUSES = ("Planned", "Partial", "Implemented", "Verified", "Superseded", "Obsolete")` (TitleCase)
- `merge.py:45` — `VALID_STATUSES = ("active", "merging", "done", "abandoned")` (lowercase)
- `blockers.py:40-43` — `TERMINAL_STATUSES` hardcodes knowledge of both findings and reqs status sets

Three modules define `VALID_STATUSES` — a name collision when any two are imported together. Blockers reaches across domain boundaries to know terminal states. Case conventions differ.

## Design

### New file: `src/codebugs/types.py`

A zero-dependency module defining the shared vocabulary for all entity types. It imports nothing from codebugs — no circular import risk.

#### Canonical values

All canonical values are **lowercase snake_case** (industry standard for programmatic identifiers). Each domain preserves its semantic values but normalizes to lowercase.

```python
# --- Finding statuses ---
FINDING_STATUSES = ("open", "in_progress", "fixed", "not_a_bug", "wont_fix", "stale")

FINDING_STATUS_ALIASES: dict[str, str] = {
    "done": "fixed",
    "resolved": "fixed",
    "implemented": "fixed",
    "closed": "fixed",
    "wontfix": "wont_fix",
    "won't_fix": "wont_fix",
    "invalid": "not_a_bug",
    "in-progress": "in_progress",
    "active": "in_progress",
    "working": "in_progress",
}

FINDING_TERMINAL = frozenset({"fixed", "not_a_bug", "wont_fix", "stale"})

# --- Requirement statuses ---
REQUIREMENT_STATUSES = ("planned", "partial", "implemented", "verified", "superseded", "obsolete")

REQUIREMENT_STATUS_ALIASES: dict[str, str] = {
    "Planned": "planned",
    "Partial": "partial",
    "Implemented": "implemented",
    "Verified": "verified",
    "Superseded": "superseded",
    "Obsolete": "obsolete",
}

REQUIREMENT_TERMINAL = frozenset({"implemented", "verified", "superseded", "obsolete"})

# --- Merge session statuses (domain-internal, included for completeness) ---
MERGE_STATUSES = ("active", "merging", "done", "abandoned")

# --- Severities (findings only) ---
SEVERITIES = ("critical", "high", "medium", "low")

# --- Priorities (requirements only) ---
PRIORITIES = ("must", "should", "could")

PRIORITY_ALIASES: dict[str, str] = {
    "Must": "must",
    "Should": "should",
    "Could": "could",
}

# --- Entity types (used by blockers) ---
ENTITY_FINDING = "finding"
ENTITY_REQUIREMENT = "requirement"

ENTITY_TABLES: dict[str, str] = {
    ENTITY_FINDING: "findings",
    ENTITY_REQUIREMENT: "requirements",
}

TERMINAL_STATUSES: dict[str, frozenset[str]] = {
    ENTITY_FINDING: FINDING_TERMINAL,
    ENTITY_REQUIREMENT: REQUIREMENT_TERMINAL,
}

# --- Blocker trigger types ---
TRIGGER_TYPES = ("entity_resolved", "date", "manual")
```

#### Resolver functions

```python
def resolve_finding_status(status: str) -> str:
    """Normalize a finding status input to canonical form."""
    s = status.lower().strip()
    s = FINDING_STATUS_ALIASES.get(s, s)
    if s not in FINDING_STATUSES:
        raise ValueError(f"Invalid finding status: {status!r}")
    return s

def resolve_requirement_status(status: str) -> str:
    """Normalize a requirement status input to canonical form."""
    s = REQUIREMENT_STATUS_ALIASES.get(status, status.lower().strip())
    if s not in REQUIREMENT_STATUSES:
        raise ValueError(f"Invalid requirement status: {status!r}")
    return s

def resolve_priority(priority: str) -> str:
    """Normalize a priority input to canonical form."""
    p = PRIORITY_ALIASES.get(priority, priority.lower().strip())
    if p not in PRIORITIES:
        raise ValueError(f"Invalid priority: {priority!r}")
    return p
```

### Migration plan per module

#### `db.py` (findings)
- Remove: `VALID_SEVERITIES`, `VALID_STATUSES`, `STATUS_ALIASES`, `resolve_status()`
- Import from `types.py`: `FINDING_STATUSES`, `SEVERITIES`, `resolve_finding_status`
- Update `add_finding()` and `update_finding()` to call `resolve_finding_status()`
- Update SQL CHECK constraint: The CHECK in the schema string uses hardcoded values. Keep the CHECK as-is (it's a safety net), but validation happens in Python before the INSERT.

#### `reqs.py` (requirements)
- Remove: `VALID_STATUSES`, `VALID_PRIORITIES`
- Import from `types.py`: `REQUIREMENT_STATUSES`, `PRIORITIES`, `resolve_requirement_status`, `resolve_priority`
- Update `add_requirement()` and `update_requirement()` to use resolvers
- TitleCase inputs like `"Planned"` are accepted via aliases and stored as `"planned"`

#### `blockers.py`
- Remove: `ENTITY_FINDING`, `ENTITY_REQUIREMENT`, `ENTITY_TABLES`, `TERMINAL_STATUSES`, `VALID_TRIGGER_TYPES`
- Import all from `types.py`
- Logic stays the same — just the constant source changes

#### `merge.py`
- Remove: `VALID_STATUSES`
- Import `MERGE_STATUSES` from `types.py`
- Minimal change — merge statuses are domain-internal

### Breaking change: requirement status case

Requirements currently store TitleCase (`"Planned"`, `"Implemented"`). After ARCH-003, they store lowercase (`"planned"`, `"implemented"`). This requires:

1. A **data migration** function that updates existing rows: `UPDATE requirements SET status = LOWER(status), priority = LOWER(priority)`
2. The `reqs.ensure_schema()` function runs this migration once (same pattern as `_migrate_statuses` in db.py)
3. MCP clients that send TitleCase values continue to work via aliases — no breaking change to the API

### What does NOT change

- SQL table structures (no column additions/removals)
- MCP tool signatures and parameter names
- Tool docstrings
- Number of tools or CLI commands
- Database file path or format

## Testing strategy

### New tests: `tests/test_types.py`

1. Each resolver function: valid inputs, aliases, invalid inputs raise ValueError
2. Constants: all terminal statuses are subsets of their domain's status set
3. Alias coverage: every TitleCase value in REQUIREMENT_STATUS_ALIASES maps correctly

### Existing tests (must all pass)

All 315 tests. The data migration means tests that assert TitleCase requirement statuses need updating to expect lowercase.

### Migration safety

- `types.py` is pure constants + functions with no imports from codebugs — zero circular import risk
- Each domain module can be migrated independently
- The requirement data migration is idempotent (LOWER on already-lowercase is a no-op)
