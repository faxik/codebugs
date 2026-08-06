# ARCH-003 Adversarial Review

**Date:** 2026-04-06
**Reviewer:** Hostile adversarial review
**Verdict:** BLOCKED — one FATAL, three SERIOUS issues must be resolved before implementation

---

## Summary Scorecard

| Category | Count |
|----------|-------|
| FATAL    | 1     |
| SERIOUS  | 3     |
| WEAKNESS | 3     |
| NITPICK  | 2     |

---

## FATAL

### F1. SQL CHECK constraints will reject migrated lowercase data

The spec says "Keep the CHECK as-is (it's a safety net), but validation happens in Python before the INSERT." This is WRONG for requirements.

`reqs.py:19-22` contains hardcoded TitleCase CHECK constraints in the schema:

```sql
priority TEXT NOT NULL DEFAULT 'Should'
    CHECK(priority IN ('Must', 'Should', 'Could')),
status TEXT NOT NULL DEFAULT 'Planned'
    CHECK(status IN ('Planned', 'Partial', 'Implemented', 'Verified', 'Superseded', 'Obsolete')),
```

After the data migration runs `UPDATE requirements SET status = LOWER(status), priority = LOWER(priority)`, **every subsequent INSERT or UPDATE of lowercase values will be rejected by SQLite's CHECK constraint**. The Python resolver converts `"Planned"` to `"planned"`, passes Python validation, then the SQL INSERT throws `sqlite3.IntegrityError`.

The spec's migration plan for `reqs.py` says nothing about recreating the table with updated CHECK constraints. The `db.py` migration (`_migrate_statuses`, lines 604-649) shows the correct approach: CREATE TABLE new, INSERT INTO new SELECT FROM old, DROP old, RENAME new. The spec needs this same table-rebuild approach for requirements, with lowercase CHECK values. This is not mentioned anywhere.

**Impact:** Every requirement write operation breaks after migration. Complete data loss scenario if migration runs but table isn't rebuilt.

---

## SERIOUS

### S1. Hardcoded TitleCase SQL queries in `reqs.py` not mentioned in migration plan

The spec only mentions updating `add_requirement()`, `update_requirement()`, and importing from `types.py`. But `reqs.py` has **hardcoded TitleCase string literals in raw SQL queries** that will silently return wrong results:

- Line 307: `WHERE status = 'Implemented' AND (test_coverage = '' ...` — counts implemented-without-tests
- Line 312: `CASE WHEN status IN ('Implemented', 'Verified') THEN 1 ELSE 0 END` — section progress
- Line 396: `status not in ("Superseded", "Obsolete")` — verify checks
- Line 401: `status == "Implemented"` — verify checks
- Line 407: `status == "Implemented" and r["priority"] == "Must"` — verify checks

After migration to lowercase, these queries will silently match zero rows. The `get_reqs_summary()` function will report 0 implemented requirements, 0 section progress, and `verify_requirements()` will stop catching real issues. **Silent data corruption with no error raised.**

### S2. The `import_requirements_md()` function has an inverse normalization map

`reqs.py:481-486` contains a `status_map` that normalizes lowercase-TO-TitleCase:

```python
status_map = {
    "planned": "Planned", "partial": "Partial",
    "implemented": "Implemented", ...
}
```

And `priority_map` at line 490 does the same for priorities. After migration, this function will **actively convert lowercase values back to TitleCase**, fighting the new convention. The spec doesn't mention this function at all.

### S3. `resolve_requirement_status()` alias lookup is inconsistent with `resolve_finding_status()`

The spec's resolver for findings does:
```python
s = status.lower().strip()
s = FINDING_STATUS_ALIASES.get(s, s)
```

But the resolver for requirements does:
```python
s = REQUIREMENT_STATUS_ALIASES.get(status, status.lower().strip())
```

The finding resolver lowercases FIRST then looks up aliases (so aliases must be lowercase keys). The requirement resolver looks up the ORIGINAL input first, then falls back to lowercase. This means `REQUIREMENT_STATUS_ALIASES` has TitleCase keys (`"Planned"`, `"Partial"`, etc.), but if someone sends `"PLANNED"` (all-caps), the alias lookup fails (no key `"PLANNED"`), falls through to `"planned"`, and that IS in `REQUIREMENT_STATUSES` so it passes. This works by accident but the inconsistent pattern will confuse maintainers and break if someone adds an alias with a non-TitleCase, non-lowercase variant.

---

## WEAKNESS

### W1. "All 315 tests" claim is unverifiable

The spec says "All 315 tests. The data migration means tests that assert TitleCase requirement statuses need updating to expect lowercase." I counted **40+ TitleCase assertions** in `tests/test_reqs.py` alone (lines 41-46, 63, 66-67, 95, 111-112, 137, 141, 157, 169-170, 174, 185, 204, 214, 224, 233, 254-255, 275-276, 297, 499-500, 504) plus `tests/test_blockers.py:27,227`. The spec undersells this as a minor update. It's a significant test rewrite touching at least two test files with 40+ assertion changes.

Additionally, I could not verify the "315 tests" count as `pytest --collect-only` produced no output in the current environment.

### W2. `FINDING_TERMINAL` includes "stale" — spec is correct but `blockers.py` does NOT

The spec defines `FINDING_TERMINAL = frozenset({"fixed", "not_a_bug", "wont_fix", "stale"})`. But the current `blockers.py:41` has:

```python
TERMINAL_STATUSES = {
    ENTITY_FINDING: {"fixed", "not_a_bug", "wont_fix"},  # NO "stale"
```

This means the spec is **changing behavior**, not just refactoring. A finding with status `"stale"` currently does NOT unblock blockers. After ARCH-003, it would. This is a semantic change disguised as a refactoring, and it's not called out anywhere in the spec.

### W3. Default parameter values in `add_requirement()` and `batch_add_requirements()` are TitleCase

`add_requirement()` at line 73 has `status: str = "Planned"` and `priority: str = "Should"`. `batch_add_requirements()` at lines 109-110 has `r.get("priority", "Should")` and `r.get("status", "Planned")`. After migration, these defaults must change to lowercase, but the resolver would catch them... except the resolver isn't called on defaults — the defaults are passed directly to the SQL INSERT. If the CHECK constraint is also not updated (see F1), TitleCase defaults would work with old CHECK but fail with new CHECK. If CHECK is updated, lowercase defaults are needed. The spec doesn't address default values at all.

---

## NITPICK

### N1. `types.py` naming collision with Python's `types` stdlib module

`from codebugs.types import ...` is fine, but `import types` in the same file will shadow the stdlib. This is a minor annoyance but worth noting since the stdlib `types` module is commonly used.

### N2. `MERGE_STATUSES` included "for completeness" but merge.py doesn't need this

The spec says merge statuses are "domain-internal, included for completeness." If they're domain-internal, they shouldn't be in the shared module. This contradicts the stated goal of only sharing cross-domain constants. Either merge statuses ARE shared (justify why) or leave them in `merge.py`.

---

## Recommendation

**Do not implement until F1 is resolved.** The requirements table needs a full table-rebuild migration (like `_migrate_statuses` in `db.py`) that:
1. Creates a new table with lowercase CHECK constraints and lowercase DEFAULT values
2. Copies data with `LOWER()` transformation
3. Drops the old table and renames

Additionally, S1 requires a comprehensive audit of every raw SQL string and Python comparison in `reqs.py` that references TitleCase status/priority values. The spec should include an explicit list of every line that needs updating.
