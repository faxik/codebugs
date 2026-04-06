# ARCH-003: Entity Type Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a shared `types.py` module defining all entity constants, resolvers, and terminal states — eliminating fragmented/colliding constant definitions across db.py, reqs.py, blockers.py, and merge.py.

**Architecture:** New zero-dependency `types.py` with canonical lowercase values + alias resolvers. Domain modules import from `types.py` instead of defining their own constants. Requirements undergo a table-rebuild migration to change SQL CHECK constraints from TitleCase to lowercase.

**Tech Stack:** Python 3.11+, SQLite

**Spec:** `docs/superpowers/specs/2026-04-06-entity-types-design.md`

---

### Task 1: Create `types.py` with constants and resolvers

**Files:**
- Create: `src/codebugs/types.py`
- Create: `tests/test_types.py`

- [ ] **Step 1: Write failing tests for resolvers**

Create `tests/test_types.py`:

```python
"""Tests for shared entity type constants and resolvers."""

from __future__ import annotations

import pytest

from codebugs.types import (
    FINDING_STATUSES, REQUIREMENT_STATUSES, MERGE_STATUSES,
    SEVERITIES, PRIORITIES,
    FINDING_TERMINAL, REQUIREMENT_TERMINAL, TERMINAL_STATUSES,
    ENTITY_FINDING, ENTITY_REQUIREMENT,
    TRIGGER_TYPES,
    resolve_finding_status, resolve_requirement_status, resolve_priority,
)


class TestConstants:
    def test_finding_terminal_subset_of_statuses(self):
        assert FINDING_TERMINAL <= set(FINDING_STATUSES)

    def test_requirement_terminal_subset_of_statuses(self):
        assert REQUIREMENT_TERMINAL <= set(REQUIREMENT_STATUSES)

    def test_terminal_statuses_keys(self):
        assert set(TERMINAL_STATUSES) == {ENTITY_FINDING, ENTITY_REQUIREMENT}

    def test_stale_not_in_finding_terminal(self):
        assert "stale" not in FINDING_TERMINAL


class TestResolveFindingStatus:
    def test_canonical_passthrough(self):
        assert resolve_finding_status("open") == "open"
        assert resolve_finding_status("fixed") == "fixed"

    def test_case_insensitive(self):
        assert resolve_finding_status("OPEN") == "open"
        assert resolve_finding_status("Fixed") == "fixed"

    def test_aliases(self):
        assert resolve_finding_status("done") == "fixed"
        assert resolve_finding_status("resolved") == "fixed"
        assert resolve_finding_status("wontfix") == "wont_fix"
        assert resolve_finding_status("in-progress") == "in_progress"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid finding status"):
            resolve_finding_status("bogus")

    def test_strips_whitespace(self):
        assert resolve_finding_status("  open  ") == "open"


class TestResolveRequirementStatus:
    def test_canonical_passthrough(self):
        assert resolve_requirement_status("planned") == "planned"
        assert resolve_requirement_status("implemented") == "implemented"

    def test_titlecase_accepted(self):
        assert resolve_requirement_status("Planned") == "planned"
        assert resolve_requirement_status("Implemented") == "implemented"
        assert resolve_requirement_status("Obsolete") == "obsolete"

    def test_uppercase_accepted(self):
        assert resolve_requirement_status("PLANNED") == "planned"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid requirement status"):
            resolve_requirement_status("bogus")


class TestResolvePriority:
    def test_canonical_passthrough(self):
        assert resolve_priority("must") == "must"
        assert resolve_priority("should") == "should"
        assert resolve_priority("could") == "could"

    def test_titlecase_accepted(self):
        assert resolve_priority("Must") == "must"
        assert resolve_priority("Should") == "should"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid priority"):
            resolve_priority("bogus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_types.py -v`
Expected: ImportError — `codebugs.types` doesn't exist.

- [ ] **Step 3: Implement `types.py`**

Create `src/codebugs/types.py`:

```python
"""Shared entity type constants, aliases, and resolvers.

This module has zero dependencies on other codebugs modules — safe to import
from anywhere without circular import risk.
"""

from __future__ import annotations

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

REQUIREMENT_TERMINAL = frozenset({"implemented", "verified", "superseded", "obsolete"})

# --- Merge session statuses ---
MERGE_STATUSES = ("active", "merging", "done", "abandoned")

# --- Severities (findings) ---
SEVERITIES = ("critical", "high", "medium", "low")

# --- Priorities (requirements) ---
PRIORITIES = ("must", "should", "could")

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


# --- Resolvers ---

def resolve_finding_status(status: str) -> str:
    """Normalize a finding status input to canonical lowercase form."""
    s = status.lower().strip()
    s = FINDING_STATUS_ALIASES.get(s, s)
    if s not in FINDING_STATUSES:
        raise ValueError(f"Invalid finding status: {status!r}")
    return s


def resolve_requirement_status(status: str) -> str:
    """Normalize a requirement status input to canonical lowercase form."""
    s = status.lower().strip()
    if s not in REQUIREMENT_STATUSES:
        raise ValueError(f"Invalid requirement status: {status!r}")
    return s


def resolve_priority(priority: str) -> str:
    """Normalize a priority input to canonical lowercase form."""
    p = priority.lower().strip()
    if p not in PRIORITIES:
        raise ValueError(f"Invalid priority: {priority!r}")
    return p
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_types.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All 315 tests PASS (types.py is additive, nothing imports it yet).

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/types.py tests/test_types.py
git commit -m "feat: add shared types.py with entity constants and resolvers"
```

---

### Task 2: Migrate `db.py` findings constants to `types.py`

**Files:**
- Modify: `src/codebugs/db.py`

- [ ] **Step 1: Replace constants and resolver in db.py**

In `src/codebugs/db.py`:

1. Add import near the top (after existing imports): `from codebugs.types import FINDING_STATUSES, SEVERITIES, FINDING_STATUS_ALIASES, resolve_finding_status`

2. Remove these lines (~291-306):
   - `VALID_SEVERITIES = ...`
   - `VALID_STATUSES = ...`
   - `STATUS_ALIASES = ...`

3. Remove the `resolve_status()` function (~309-320).

4. Find all references to `VALID_STATUSES` in db.py and replace with `FINDING_STATUSES`. Find all references to `VALID_SEVERITIES` and replace with `SEVERITIES`. Find all calls to `resolve_status(` and replace with `resolve_finding_status(`. Find references to `STATUS_ALIASES` and replace with `FINDING_STATUS_ALIASES`.

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All tests PASS. The findings domain already uses lowercase — no data changes.

- [ ] **Step 3: Commit**

```bash
git add src/codebugs/db.py
git commit -m "refactor(db): use types.py for finding constants and resolver"
```

---

### Task 3: Migrate `blockers.py` constants to `types.py`

**Files:**
- Modify: `src/codebugs/blockers.py`

- [ ] **Step 1: Replace constants in blockers.py**

1. Add import: `from codebugs.types import ENTITY_FINDING, ENTITY_REQUIREMENT, ENTITY_TABLES, TERMINAL_STATUSES, TRIGGER_TYPES`

2. Remove these lines (~35-45):
   - `ENTITY_FINDING = ...`
   - `ENTITY_REQUIREMENT = ...`
   - `ENTITY_TABLES = ...`
   - `TERMINAL_STATUSES = ...`
   - `VALID_TRIGGER_TYPES = ...`

3. Replace all `VALID_TRIGGER_TYPES` references with `TRIGGER_TYPES`.

**IMPORTANT:** `TERMINAL_STATUSES` in blockers.py currently has TitleCase for requirements (`"Implemented"`, `"Verified"`, etc.). The `types.py` version uses lowercase. This is fine ONLY if the requirements data migration (Task 4) runs first. Since blockers uses `db.connect()` which runs all `ensure_schema()` in order, the migration will have run by the time blockers checks terminal statuses.

However, to be safe, migrate blockers AFTER the requirements data migration in Task 4.

- [ ] **Step 2: DO NOT run tests yet** — requirements data is still TitleCase but TERMINAL_STATUSES is now lowercase. Complete Task 4 first, then test both together.

---

### Task 4: Migrate `reqs.py` to lowercase with table-rebuild migration

This is the most complex task. It must:
1. Replace constants with types.py imports
2. Add a table-rebuild migration for SQL CHECK constraints
3. Update all hardcoded TitleCase references in queries and logic
4. Update default parameter values
5. Fix `import_requirements_md()` inverse normalization

**Files:**
- Modify: `src/codebugs/reqs.py`
- Modify: `tests/test_reqs.py` (update ~40+ TitleCase assertions)
- Modify: `tests/test_blockers.py` (update TitleCase requirement seeds)

The implementer MUST:

1. Read the FULL `src/codebugs/reqs.py` file carefully
2. Read the FULL `tests/test_reqs.py` and `tests/test_blockers.py` files
3. Identify EVERY TitleCase status/priority reference

- [ ] **Step 1: Update `REQS_SCHEMA` and add migration**

In `src/codebugs/reqs.py`:

1. Add import: `from codebugs.types import REQUIREMENT_STATUSES, PRIORITIES, resolve_requirement_status, resolve_priority`

2. Remove: `VALID_PRIORITIES = ...` and `VALID_STATUSES = ...` (~lines 38-39)

3. Update `REQS_SCHEMA` to use lowercase CHECK constraints:
   - Change `CHECK(priority IN ('Must', 'Should', 'Could'))` to `CHECK(priority IN ('must', 'should', 'could'))`
   - Change `CHECK(status IN ('Planned', 'Partial', 'Implemented', 'Verified', 'Superseded', 'Obsolete'))` to `CHECK(status IN ('planned', 'partial', 'implemented', 'verified', 'superseded', 'obsolete'))`
   - Change `DEFAULT 'Should'` to `DEFAULT 'should'`
   - Change `DEFAULT 'Planned'` to `DEFAULT 'planned'`

4. Add `_migrate_to_lowercase(conn)` function (after `ensure_schema`), following the table-rebuild pattern from `db.py:_migrate_statuses`:

```python
def _migrate_to_lowercase(conn: sqlite3.Connection) -> None:
    """Migrate requirement statuses and priorities to lowercase."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'"
    ).fetchone()
    if row is None:
        return
    # If CHECK already has lowercase, migration already ran
    if "'planned'" in row[0].lower() and "'must'" in row[0].lower():
        return
    # Table rebuild with lowercase constraints
    conn.executescript("""
        CREATE TABLE requirements_new (
            id TEXT PRIMARY KEY,
            section TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'should'
                CHECK(priority IN ('must', 'should', 'could')),
            status TEXT NOT NULL DEFAULT 'planned'
                CHECK(status IN ('planned', 'partial', 'implemented', 'verified', 'superseded', 'obsolete')),
            source TEXT NOT NULL DEFAULT '',
            test_coverage TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            embedding BLOB,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO requirements_new
            SELECT id, section, description, LOWER(priority), LOWER(status),
                   source, test_coverage, tags, meta, embedding, created_at, updated_at
            FROM requirements;
        DROP TABLE requirements;
        ALTER TABLE requirements_new RENAME TO requirements;
        CREATE INDEX IF NOT EXISTS idx_reqs_status ON requirements(status);
        CREATE INDEX IF NOT EXISTS idx_reqs_section ON requirements(section);
        CREATE INDEX IF NOT EXISTS idx_reqs_priority ON requirements(priority);
    """)
```

5. Call `_migrate_to_lowercase(conn)` at the end of `ensure_schema()`.

- [ ] **Step 2: Update all function signatures and logic**

Search reqs.py for every TitleCase status/priority reference and update:

- Default params: `status="Planned"` → `status="planned"`, `priority="Should"` → `priority="should"`
- Validation: Replace `if status not in VALID_STATUSES` with `status = resolve_requirement_status(status)` (resolver raises on invalid)
- Replace `if priority not in VALID_PRIORITIES` with `priority = resolve_priority(priority)`
- Hardcoded queries: Any SQL or Python code referencing `"Implemented"`, `"Verified"`, `"Planned"`, etc. must use lowercase
- `import_requirements_md()`: Remove the inverse normalization map that converts lowercase back to TitleCase. Imported values should go through the resolver.
- `verify_requirements()`: Update string checks like `"deprecated" in desc_lower and status == "Implemented"` to use lowercase `"implemented"`

- [ ] **Step 3: Update tests**

Update `tests/test_reqs.py`: Change ALL TitleCase status/priority assertions and inputs to lowercase. This includes:
- Test data that passes `status="Planned"`, `priority="Must"` etc.
- Assertions like `assert result["status"] == "Planned"` → `assert result["status"] == "planned"`

Update `tests/test_blockers.py`: Change all requirement seed data from TitleCase to lowercase.

- [ ] **Step 4: Run full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL tests PASS. This validates both the reqs migration AND the blockers constant change from Task 3.

- [ ] **Step 5: Commit**

```bash
git add src/codebugs/reqs.py src/codebugs/blockers.py tests/test_reqs.py tests/test_blockers.py
git commit -m "refactor: migrate requirements to lowercase statuses with table-rebuild"
```

---

### Task 5: Migrate `merge.py` constants and update MCP tool docstrings

**Files:**
- Modify: `src/codebugs/merge.py`
- Modify: domain modules with `register_tools` containing status docstrings

- [ ] **Step 1: Replace merge constants**

In `src/codebugs/merge.py`:
1. Add import: `from codebugs.types import MERGE_STATUSES`
2. Remove: `VALID_STATUSES = ("active", "merging", "done", "abandoned")` (~line 45)
3. Replace any `VALID_STATUSES` references with `MERGE_STATUSES`

- [ ] **Step 2: Update MCP tool docstrings**

In the `register_tools` functions across domain modules, update docstring text that lists valid status/priority values to show lowercase. Key locations:
- `reqs.py:register_tools` — `reqs_add` and `reqs_update` docstrings listing status/priority values
- `db.py:register_tools` — `add` and `update` docstrings if they list statuses

- [ ] **Step 3: Run full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add src/codebugs/merge.py src/codebugs/reqs.py src/codebugs/db.py
git commit -m "refactor: migrate merge constants to types.py, update tool docstrings"
```

---

### Task 6: Cleanup, documentation, and verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

Remove the "Findings naming exception" note about inconsistent constants (now unified). Update architecture section to mention `types.py` as the shared vocabulary module.

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check src/ tests/`
Expected: No new errors from our changes.

- [ ] **Step 4: Update ARCH-003 requirement status**

Use MCP tool: `reqs_update(req_id="ARCH-003", status="implemented", test_coverage="tests/test_types.py")`

- [ ] **Step 5: Commit and push**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for ARCH-003 completion"
git push
```
