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

FINDING_TERMINAL = frozenset({"fixed", "not_a_bug", "wont_fix"})

# --- Requirement statuses ---
REQUIREMENT_STATUSES = ("planned", "partial", "implemented", "verified", "superseded", "obsolete")

REQUIREMENT_STATUS_ALIASES: dict[str, str] = {
    # TitleCase aliases (lowercased by resolver before lookup)
    # No entries needed — lowercase of TitleCase matches canonical values directly
}

REQUIREMENT_TERMINAL = frozenset({"implemented", "verified", "superseded", "obsolete"})

# --- Merge session statuses (domain-internal, included for completeness) ---
MERGE_STATUSES = ("active", "merging", "done", "abandoned")

# --- Severities (findings only) ---
SEVERITIES = ("critical", "high", "medium", "low")

# --- Priorities (requirements only) ---
PRIORITIES = ("must", "should", "could")

PRIORITY_ALIASES: dict[str, str] = {
    # TitleCase aliases (lowercased by resolver before lookup)
    # No entries needed — lowercase of TitleCase matches canonical values directly
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
    s = status.lower().strip()
    s = REQUIREMENT_STATUS_ALIASES.get(s, s)
    if s not in REQUIREMENT_STATUSES:
        raise ValueError(f"Invalid requirement status: {status!r}")
    return s

def resolve_priority(priority: str) -> str:
    """Normalize a priority input to canonical form."""
    p = priority.lower().strip()
    p = PRIORITY_ALIASES.get(p, p)
    if p not in PRIORITIES:
        raise ValueError(f"Invalid priority: {priority!r}")
    return p
```

### Migration plan per module

#### `db.py` (findings)
- Remove: `VALID_SEVERITIES`, `VALID_STATUSES`, `STATUS_ALIASES`, `resolve_status()`
- Import from `types.py`: `FINDING_STATUSES`, `SEVERITIES`, `resolve_finding_status`
- Update `add_finding()` and `update_finding()` to call `resolve_finding_status()`
- SQL CHECK constraint: findings schema already uses lowercase values — no change needed

#### `reqs.py` (requirements)
- Remove: `VALID_STATUSES`, `VALID_PRIORITIES`
- Import from `types.py`: `REQUIREMENT_STATUSES`, `PRIORITIES`, `resolve_requirement_status`, `resolve_priority`
- Update `add_requirement()` and `update_requirement()` to use resolvers
- **SQL CHECK constraint migration (CRITICAL):** `REQS_SCHEMA` has `CHECK(status IN ('Planned', 'Partial', ...))` and `CHECK(priority IN ('Must', 'Should', 'Could'))`. These must be rebuilt with lowercase values using the same table-rebuild pattern as `_migrate_statuses` in db.py. Add `_migrate_to_lowercase(conn)` in `reqs.py`.
- **Hardcoded TitleCase queries:** Several functions in reqs.py contain hardcoded TitleCase status references in SQL queries and in-code logic (e.g., `verify_requirements` checks for `"Implemented"`, `"Superseded"`). All must be updated to lowercase.
- **`import_requirements_md()`:** Contains an inverse normalization map that converts statuses BACK to TitleCase. This must be removed — imported values go through the resolver and are stored as lowercase.
- **Default parameter values:** Function signatures with defaults like `status="Planned"`, `priority="Should"` must be changed to `status="planned"`, `priority="should"`.
- TitleCase inputs from MCP clients are accepted via `resolve_requirement_status()` which lowercases then validates.

#### `blockers.py`
- Remove: `ENTITY_FINDING`, `ENTITY_REQUIREMENT`, `ENTITY_TABLES`, `TERMINAL_STATUSES`, `VALID_TRIGGER_TYPES`
- Import all from `types.py`
- Logic stays the same — just the constant source changes
- Note: `FINDING_TERMINAL` intentionally excludes `"stale"` to match current behavior (stale findings do NOT unblock blockers)

#### `merge.py`
- Remove: `VALID_STATUSES`
- Import `MERGE_STATUSES` from `types.py`
- Minimal change — merge statuses are domain-internal, already lowercase

### Breaking change: requirement status case

Requirements currently store TitleCase (`"Planned"`, `"Implemented"`). After ARCH-003, they store lowercase (`"planned"`, `"implemented"`). This requires:

1. A **table-rebuild migration** (`_migrate_to_lowercase`) that:
   - Creates a new table with lowercase CHECK constraints
   - Copies data with `LOWER(status)` and `LOWER(priority)`
   - Drops old table, renames new
   - Same proven pattern as `_migrate_statuses` in db.py (lines 604-649)
2. The `reqs.ensure_schema()` function runs this migration once
3. MCP clients that send TitleCase values continue to work via resolvers — no breaking change to the API

### What does NOT change

- MCP tool signatures and parameter names
- Tool docstrings (update valid values in docstrings to show lowercase)
- Number of tools or CLI commands
- Database file path or format

## Testing strategy

### New tests: `tests/test_types.py`

1. Each resolver function: valid inputs, aliases, invalid inputs raise ValueError
2. Constants: all terminal statuses are subsets of their domain's status set
3. Alias coverage: every TitleCase value in REQUIREMENT_STATUS_ALIASES maps correctly

### Existing test updates

All 315 tests must pass. Significant test updates required:
- **`test_reqs.py`**: ~40+ assertions check TitleCase statuses/priorities — all must be updated to lowercase
- **`test_blockers.py`**: Tests that seed requirement data with TitleCase must be updated
- **CLI tests** (if any): Output that displays statuses will show lowercase

### Migration safety

- `types.py` is pure constants + functions with no imports from codebugs — zero circular import risk
- The table-rebuild migration follows the proven `_migrate_statuses` pattern already in db.py
- The migration is idempotent (LOWER on already-lowercase is a no-op)
- Each domain module can be migrated independently, but reqs.py is the most complex due to the data migration

## Adversarial Review Corrections

Reviewed 2026-04-06. Findings addressed:
- FATAL: Added table-rebuild migration for SQL CHECK constraints (was "keep CHECK as-is" — would reject all lowercase data)
- SERIOUS: Documented hardcoded TitleCase queries in reqs.py that need updating
- SERIOUS: Documented import_requirements_md() inverse normalization map that must be removed
- SERIOUS: Made all resolver functions use consistent lowercase-first lookup order
- WEAKNESS: Removed "stale" from FINDING_TERMINAL to match current blockers behavior (not a refactor concern)
- WEAKNESS: Documented default parameter value changes needed in function signatures
- WEAKNESS: Updated test impact estimate to ~40+ assertions
