# Code Review: Codemerge Implementation Plan

**Reviewer:** Senior Code Reviewer
**Date:** 2026-03-25
**Plan:** `docs/superpowers/plans/2026-03-25-codemerge.md`
**Design Spec:** `docs/2026-03-25-codemerge-design.md`

---

## Overall Assessment

The plan is well-structured, follows TDD order correctly, produces committable increments per task, and closely mirrors the existing codebase patterns from `reqs.py`, `server.py`, and `cli.py`. The CAS protocol is the right design decision to close the TOCTOU gap. Below are issues found, organized by severity.

---

## CRITICAL Issues

### C1. CAS check is outside the SQLite transaction — race window still open
**Location:** Task 4, Step 3 — `merge()` function, lines 682-703

The `merge()` function reads the lock row, calls `current_main_head_fn()` (which shells out to `git rev-parse`), then writes the lock — all using autocommit semantics (no `BEGIN IMMEDIATE`). Two concurrent processes can both read the lock as free, both call `git rev-parse`, both get the same HEAD, and both write the lock. The second writer silently overwrites the first.

**Fix required:** Wrap the entire read-check-write in `BEGIN IMMEDIATE`:
```python
conn.execute("BEGIN IMMEDIATE")  # grab write lock on DB
try:
    lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()
    # ... check lock, call current_main_head_fn(), write lock ...
    conn.commit()
except:
    conn.rollback()
    raise
```
`BEGIN IMMEDIATE` acquires the SQLite write lock at the start, serializing concurrent callers at the DB level. Without this, the singleton lock table provides no actual mutual exclusion between processes.

### C2. `callable` type hint is not a valid type annotation
**Location:** Task 4, Step 3 — `merge()` signature; Task 5, Step 3 — `check_overlaps()` signature

```python
current_main_head_fn: callable  # wrong — should be Callable[[], str]
```
`callable` (lowercase) is a builtin function, not a type. This will not raise at runtime but fails static type checking. The existing codebase uses `from typing import Any` — this needs `Callable` from `typing` or `collections.abc`.

**Fix:** `from typing import Callable` and use `Callable[[], str]`.

---

## IMPORTANT Issues

### I1. `merge()` import of `timedelta` is placed mid-file as an instruction, not in the actual code block
**Location:** Task 4, Step 3, line 619

The plan says "Add to merge.py" then shows `from datetime import timedelta` as a separate line before the function. An agent following copy-paste instructions would add it inside the function or miss it entirely. The actual `merge.py` file from Task 1 only imports `datetime, timezone` — `timedelta` is missing from the initial import block.

**Fix:** The Task 1 Step 3 import line should be `from datetime import datetime, timedelta, timezone`.

### I2. Design spec calls for 10 MCP tools; plan implements 4 — deviation justified but not all design-spec tools are accounted for
**Location:** Task 8

The design spec lists: `codemerge_start`, `codemerge_done`, `codemerge_claim`, `codemerge_claims`, `codemerge_check`, `codemerge_acquire`, `codemerge_release`, `codemerge_sessions`, `codemerge_status`, `codemerge_abandon`.

The plan consolidates to 4 MCP tools (start, check, merge, finish) which is a good simplification. However, `codemerge_claim` is completely absent from both MCP tools and CLI. The PostToolUse hook is documented as out-of-scope, but there is no programmatic way for an agent to manually claim a file via MCP. This means the system cannot track files until the hook is separately built.

**Fix:** Either add a `codemerge_claim` MCP tool (simple, follows existing pattern), or explicitly document that file tracking requires the hook and cannot work standalone in v1.

### I3. `check_overlaps` has no `current_main_head_fn` in the MCP tool registration
**Location:** Task 8, Step 1 — `codemerge_check` tool, lines 1117-1139

The MCP tool `codemerge_check` calls `merge.check_overlaps(conn, session_id, main_changed_files=main_changed_files)` but never passes `current_main_head_fn`. This means the response will never include `main_head`, which the agent needs to subsequently pass to `codemerge_merge(expected_main_head=...)`.

The concurrent scenario test (Task 10) relies on `current_main_head_fn` being passed to `check_overlaps`. Without it in the MCP tool, the agent has no ergonomic way to get the current main HEAD atomically with the check.

**Fix:** Add `current_main_head_fn=_get_main_head` to the MCP tool call (reuse the same helper from `codemerge_merge`), or extract `_get_main_head` to module level.

### I4. Lock expiry comparison uses string comparison on ISO timestamps
**Location:** Task 4, Step 3 — `merge()`, line 666

```python
if lock["expires_at"] and lock["expires_at"] > now:
```

This works correctly only because ISO 8601 timestamps sort lexicographically. However, this is fragile — if any code path writes a non-UTC or differently-formatted timestamp, the comparison silently breaks. The existing codebase (`reqs.py`, `db.py`) only stores timestamps, never compares them. This is a new pattern that should be documented or use proper datetime parsing.

**Recommendation:** Add a comment explaining the lexicographic comparison assumption, or parse with `datetime.fromisoformat()`.

### I5. No test for `abandon_session` directly
**Location:** Task 2

`abandon_session` is implemented in Task 2 Step 3 and used as a helper in other tests (`test_start_reactivate_abandoned`, `TestCheckOverlaps::test_ignores_abandoned_sessions`), but there is no dedicated test for:
- Abandoning a session that is currently merging (should release the lock)
- Abandoning an already-abandoned session (idempotency or error?)
- Abandoning a session that doesn't exist (the `KeyError` path is never tested)

**Fix:** Add a `TestAbandonSession` class with at least these 3 cases.

### I6. Task 2 tests depend on `merge()` which is not implemented until Task 4
**Location:** Task 2, Step 1 — `TestEndSession` class

`test_end_done`, `test_end_not_merging_raises`, and `test_end_failure_reverts_to_active` all call `merge.merge(...)` which doesn't exist until Task 4. The plan acknowledges this at Step 4 ("some may still fail because merge() is not yet implemented"), but this violates the "each task produces a working, committable increment" requirement.

**Fix:** Either move `TestEndSession` to Task 4, or write Task 2's finish tests using direct SQL to put the session into "merging" state:
```python
conn.execute("UPDATE codemerge_sessions SET status='merging' WHERE session_id=?", ("s1",))
```

---

## MINOR Issues

### M1. `_get_main_head` is defined inline inside `codemerge_merge` MCP tool
**Location:** Task 8, Step 1, lines 1163-1167

It's also needed by `codemerge_check` (see I3). Extract to module level in `server.py` for reuse.

### M2. CLI commands use `from codebugs import merge` inside function bodies
**Location:** Task 9, Step 1

The existing CLI pattern (`cli.py`) imports `db` and `reqs` at module top level. The merge CLI commands import `merge` inside each function body. While functional, this is inconsistent with the established pattern.

**Fix:** Add `from codebugs import merge` to the top of `cli.py` alongside the existing imports.

### M3. `test_claims_updates_last_activity` may flicker
**Location:** Task 3, Step 1

The test compares `after >= before`, but both `_now()` calls and the SQLite `datetime('now')` default can return the same second. This test can only prove the timestamp is non-decreasing, not that it was actually updated. This is the same pattern used elsewhere in the codebase so it's consistent, but worth noting.

### M4. Missing `import` statement for `merge` in `server.py` top-level
**Location:** Task 8, Step 1

The existing `server.py` imports `from codebugs import db, reqs` at the top. The plan uses `from codebugs import merge` inside each tool function. While this avoids circular imports (matching the lazy-import pattern in `db.py`'s `connect()`), it's inconsistent with how `db` and `reqs` are used in the same file. The plan should note this is intentional.

### M5. Plan summary table counts don't match actual test counts
**Location:** Lines 1471-1483

Task 2 lists 7 tests but actually defines 4 tests (start: 3, end: 4 = 7 total, OK). Task 3 lists 7 tests but defines 6 tests. Minor documentation inaccuracy.

### M6. Design spec's `severity` field in conflicts is dropped
**Location:** Task 5 vs Design Spec line 396

The design spec's conflict detection algorithm includes a `severity` field (`warn` vs `block`) on each conflict. The plan's `check_overlaps` omits this entirely. This is a simplification but loses the ability to distinguish advisory warnings from blocking conflicts.

---

## Positive Observations

1. **TDD order is correct** throughout — test first, verify fail, implement, verify pass, commit.
2. **CAS design** (inject `current_main_head_fn` as callable) keeps the core module git-free and fully unit-testable. This is excellent.
3. **`check_overlaps` takes `main_changed_files` as a parameter** rather than shelling out to git — clean separation of concerns.
4. **State machine** (active -> merging -> done, with abandon as terminal) is simple and correct.
5. **Lock TTL with auto-reclaim** on expiry handles crash scenarios well.
6. **Fixture pattern** exactly matches `test_reqs.py` (in-memory SQLite, `ensure_schema`, yield, close).
7. **`register_merge_tools` / `_register_merge_subcommands` pattern** exactly matches existing code.
8. **File paths are exact** and consistent with the project structure.
9. **Scope is well-bounded** — PostToolUse hook and shell script changes are correctly excluded.
10. **Concurrent scenario test** (Task 10) is thorough and validates the full race-condition flow.

---

## Verdict

**Two critical issues must be fixed before implementation begins (C1, C2).** C1 (no `BEGIN IMMEDIATE`) means the CAS protocol has a real race window under concurrent processes — the core safety guarantee of the design is undermined. C2 is a type annotation bug that will cause linter failures.

The important issues (I1-I6) should also be addressed, particularly I3 (missing `current_main_head_fn` in the MCP check tool) and I6 (Task 2 tests depending on Task 4 code).
