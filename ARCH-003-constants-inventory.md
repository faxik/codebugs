# ARCH-003 Entity Type Unification: Complete Constants Inventory

**Date:** 2026-04-06  
**Task:** Map ALL status, priority, severity, and entity-type constants for ARCH-003 entity type unification

---

## 1. CONSTANT DEFINITIONS (Location & Values)

### 1.1 Findings Domain (`db.py`)

| Constant | Line | Type | Values |
|----------|------|------|--------|
| `VALID_SEVERITIES` | 291 | tuple | `critical`, `high`, `medium`, `low` |
| `VALID_STATUSES` | 292 | tuple | `open`, `in_progress`, `fixed`, `not_a_bug`, `wont_fix`, `stale` |
| `STATUS_ALIASES` | ~300+ | dict | Maps: `done`→`fixed`, `resolved`→`fixed`, `implemented`→`fixed`, `closed`→`fixed`, `wontfix`→`wont_fix`, `won't_fix`→`wont_fix`, `invalid`→`not_a_bug`, `in-progress`→`in_progress`, `active`→`in_progress`, `working`→`in_progress` |

**SQL Constraints** (`SCHEMA` string):
```sql
severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low'))
status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale'))
```

---

### 1.2 Requirements Domain (`reqs.py`)

| Constant | Line | Type | Values |
|----------|------|------|--------|
| `VALID_PRIORITIES` | 38 | tuple | `Must`, `Should`, `Could` |
| `VALID_STATUSES` | 39 | tuple | `Planned`, `Partial`, `Implemented`, `Verified`, `Superseded`, `Obsolete` |

**SQL Constraints** (`REQS_SCHEMA` string):
```sql
priority TEXT NOT NULL CHECK(priority IN ('Must', 'Should', 'Could'))
status TEXT NOT NULL DEFAULT 'Planned' CHECK(status IN ('Planned', 'Partial', 'Implemented', 'Verified', 'Superseded', 'Obsolete'))
```

---

### 1.3 Blockers Domain (`blockers.py`)

| Constant | Line | Type | Values |
|----------|------|------|--------|
| `ENTITY_FINDING` | 35 | str | `"finding"` |
| `ENTITY_REQUIREMENT` | 36 | str | `"requirement"` |
| `ENTITY_TABLES` | 38 | dict | `{ENTITY_FINDING: "findings", ENTITY_REQUIREMENT: "requirements"}` |
| `TERMINAL_STATUSES` | 40 | dict[str, set[str]] | `ENTITY_FINDING: {"fixed", "not_a_bug", "wont_fix"}` <br> `ENTITY_REQUIREMENT: {"Implemented", "Verified", "Superseded", "Obsolete"}` |
| `VALID_TRIGGER_TYPES` | 45 | tuple | `entity_resolved`, `date`, `manual` |

**SQL Constraints** (`BLOCKERS_SCHEMA` string):
```sql
item_type TEXT CHECK(item_type IN ('finding', 'requirement'))
blocked_by_type TEXT CHECK(blocked_by_type IN ('finding', 'requirement') OR blocked_by_type IS NULL)
trigger_type TEXT NOT NULL CHECK(trigger_type IN ('entity_resolved', 'date', 'manual'))
```

---

### 1.4 Merge Domain (`merge.py`)

| Constant | Line | Type | Values |
|----------|------|------|--------|
| `VALID_STATUSES` | 45 | tuple | `active`, `merging`, `done`, `abandoned` |

**SQL Constraints**:
```sql
status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'merging', 'done', 'abandoned'))
```

**Note:** Domain-specific; does NOT conflict with findings or requirements statuses.

---

## 2. USAGE & CROSS-REFERENCES

### 2.1 Where Each Constant Is Used

#### `db.py` VALID_STATUSES
- **Validation:** Lines 315, 322 (resolve_status function)
- **Constraint checks:** Line 691, 692 (severity validation)
- **Cross-module:** cli.py line 357 (status iteration)

#### `reqs.py` VALID_STATUSES
- **Validation:** Lines 82–83, 113, 162–163, 487, 492 (in add_req, update_req)
- **Cross-module:** cli.py line 357 (status iteration)
- **Blocker integration:** reqs.py line 746 (uses `blockers.TERMINAL_STATUSES`)

#### `reqs.py` VALID_PRIORITIES
- **Validation:** Lines 80–81, 111, 170–171, 492 (in add_req, update_req)
- **Cross-module:** cli.py line 363 (priority iteration)

#### `blockers.py` ENTITY_* Constants
- **ENTITY_FINDING:** blockers.py lines 38, 41, 64; db.py lines 457, 533
- **ENTITY_REQUIREMENT:** blockers.py lines 38, 42, 66; reqs.py lines 746, 807
- **ENTITY_TABLES:** blockers.py (in blocker_add, query operations)
- **TERMINAL_STATUSES:** db.py line 457, reqs.py line 746 (blocker resolution checks)

#### `blockers.py` VALID_TRIGGER_TYPES
- **Validation:** Lines 162, 164, 247, 249 (in blocker_add, blockers_add_blocker)

---

### 2.2 Cross-Module Dependencies

```
blockers.py (entities & terminal states)
  ├─→ db.py (VALID_STATUSES, VALID_SEVERITIES)
  └─→ reqs.py (VALID_STATUSES, VALID_PRIORITIES)

db.py & reqs.py
  └─→ blockers.py (ENTITY_FINDING, ENTITY_REQUIREMENT, TERMINAL_STATUSES)

cli.py
  ├─→ reqs.py (VALID_STATUSES, VALID_PRIORITIES)
  └─→ db.py (queries using resolved statuses)
```

---

## 3. IDENTIFIED INCONSISTENCIES & CONFLICTS

### 3.1 CRITICAL: Name Collision on `VALID_STATUSES`

**Problem:** Three separate modules define constants with identical names but different values:

```python
# db.py:292
VALID_STATUSES = ("open", "in_progress", "fixed", "not_a_bug", "wont_fix", "stale")

# reqs.py:39
VALID_STATUSES = ("Planned", "Partial", "Implemented", "Verified", "Superseded", "Obsolete")

# merge.py:45
VALID_STATUSES = ("active", "merging", "done", "abandoned")
```

**Impact:**
- Easy to accidentally import wrong constant in new code
- IDE autocomplete may not disambiguate
- Risk of validation accepting wrong values

**Root Cause:** Status is a domain-specific concept; each domain needs its own valid set.

---

### 3.2 TERMINAL_STATUSES Value Mismatch

**Problem:** Terminal states reference different status values with inconsistent naming:

```python
# blockers.py:41-42
TERMINAL_STATUSES = {
    ENTITY_FINDING: {"fixed", "not_a_bug", "wont_fix"},           # lowercase, snake_case
    ENTITY_REQUIREMENT: {"Implemented", "Verified", "Superseded", "Obsolete"},  # Title Case
}
```

**Observations:**
- Findings terminal states: 3 values (subset of 6 VALID_STATUSES in db.py)
- Requirements terminal states: 4 values (subset of 6 VALID_STATUSES in reqs.py)
- Value names do NOT match—findings use snake_case, requirements use Title Case
- Both sets must match their respective entity's VALID_STATUSES

---

### 3.3 No Severity for Requirements, No Priority for Findings

**Current State:**
- **Findings:** Have severity (critical/high/medium/low), no priority concept
- **Requirements:** Have priority (Must/Should/Could), no severity concept
- **No shared vocabulary** for importance/impact across domains

**Impact:** Cannot correlate severity of findings with priority of requirements that address them.

---

### 3.4 STATUS_ALIASES Only for Findings

**Current State:**
- `db.py` defines `STATUS_ALIASES` for user input normalization (10 aliases)
- No equivalent for requirements domain
- Merge domain has no aliases

**Impact:** Inconsistent user experience: findings accept flexible input, requirements are strict.

---

## 4. VALIDATION PATHS (Where Constants Are Enforced)

### 4.1 Findings Validation (`db.py`)

1. **resolve_status()** (lines ~300+): Maps aliases to canonical status
2. **add_finding()** (line 691): Checks `severity in VALID_SEVERITIES`
3. **update()** (line 719): Checks `severity in VALID_SEVERITIES`
4. **SQL CHECK constraint:** Validates status on insert/update

### 4.2 Requirements Validation (`reqs.py`)

1. **add_req()** (lines 80–83): Checks both priority and status
2. **update_req()** (lines 162–171): Checks both priority and status
3. **query()** (lines 487, 492): Validates before querying
4. **SQL CHECK constraint:** Validates both fields on insert/update

### 4.3 Blockers Validation (`blockers.py`)

1. **blocker_add()** (lines 162–164): Checks `trigger_type in VALID_TRIGGER_TYPES`
2. **blockers_add_blocker()** (lines 247–249): Same check
3. **SQL CHECK constraints:** Validate entity types and trigger type

---

## 5. SUMMARY TABLE: All Constants at a Glance

| Domain | Module | Constant | Line | Type | Count | Notes |
|--------|--------|----------|------|------|-------|-------|
| Findings | db.py | VALID_SEVERITIES | 291 | tuple | 4 | critical, high, medium, low |
| Findings | db.py | VALID_STATUSES | 292 | tuple | 6 | open, in_progress, fixed, not_a_bug, wont_fix, stale |
| Findings | db.py | STATUS_ALIASES | ~300+ | dict | 10 pairs | User input normalization |
| Requirements | reqs.py | VALID_PRIORITIES | 38 | tuple | 3 | Must, Should, Could |
| Requirements | reqs.py | VALID_STATUSES | 39 | tuple | 6 | Planned, Partial, Implemented, Verified, Superseded, Obsolete |
| Blockers | blockers.py | ENTITY_FINDING | 35 | str | 1 | "finding" |
| Blockers | blockers.py | ENTITY_REQUIREMENT | 36 | str | 1 | "requirement" |
| Blockers | blockers.py | ENTITY_TABLES | 38 | dict | 2 | Maps to table names |
| Blockers | blockers.py | TERMINAL_STATUSES | 40 | dict | 2 entries, 7 values | Terminal states per entity |
| Blockers | blockers.py | VALID_TRIGGER_TYPES | 45 | tuple | 3 | entity_resolved, date, manual |
| Merge | merge.py | VALID_STATUSES | 45 | tuple | 4 | active, merging, done, abandoned |

---

## 6. Recommendations for ARCH-003

1. **Rename conflicting constants:** 
   - `db.py`: `VALID_STATUSES` → `FINDING_VALID_STATUSES`
   - `reqs.py`: `VALID_STATUSES` → `REQUIREMENT_VALID_STATUSES`
   - `merge.py`: `VALID_STATUSES` → `MERGE_SESSION_VALID_STATUSES`

2. **Create centralized enum module:** Consider `src/codebugs/enums.py` with:
   - `FindingSeverity`, `FindingStatus`
   - `RequirementPriority`, `RequirementStatus`
   - `EntityType`, `TriggerType`

3. **Add cross-domain mapping:** If requirements need severity or findings need priority, define explicit mappings.

4. **Add aliases for requirements:** If user-friendly input is required.

5. **Align TERMINAL_STATUSES naming:** Use consistent casing.

---

**End of Inventory Report**
