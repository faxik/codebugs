# Adversarial Review: Finding Provenance Design

**Reviewer:** Adversarial Review Agent
**Date:** 2026-04-05
**Spec under review:** `docs/superpowers/specs/2026-04-05-provenance-design.md`

---

## FATAL

**FATAL-1: `ALTER TABLE ADD COLUMN` will fail on existing databases due to migration pattern** (db.py, lines 108-144)

The spec claims "No table rebuild needed; SQLite `ALTER TABLE ADD COLUMN` handles this." This is technically true for SQLite itself, but the codebase already has a migration function `_migrate_statuses()` that **rebuilds the entire table** via `CREATE TABLE findings_new ... INSERT INTO findings_new SELECT * FROM findings ... DROP TABLE findings ... ALTER TABLE findings_new RENAME TO findings`. This migration uses `SELECT * FROM findings` which returns columns in the **current** DDL order. If the new columns are added via `ALTER TABLE ADD COLUMN` *before* this migration runs on a legacy DB that still needs the status migration, the `SELECT *` insert will fail because `findings_new` won't have the provenance columns but `findings` will. The migration order is: schema init runs first (which would add the columns), then `_migrate_statuses()` runs second (which rebuilds without those columns). **This will silently drop the two new columns on any database that still needs the status migration.**

**FATAL-2: `batch_add()` has no parameter passthrough for provenance fields** (server.py, line 70-79; db.py, lines 193-231)

The spec says `batch_add()` gets two new optional parameters. But the current `batch_add` server tool takes `findings: list[dict[str, Any]]` and passes it directly to `db.batch_add_findings(conn, findings)`. There is no top-level `reported_at_commit` parameter -- each finding dict would need its own. The spec's interface table shows `reported_at_commit` and `reported_at_ref` as parameters on `batch_add()` itself, implying a single value applied to the whole batch. The spec is ambiguous about whether these are per-finding (in the dict) or per-batch (top-level). Either way, the auto-population logic ("run `git rev-parse HEAD`") has no existing precedent in the db layer -- subprocess is only called in `server.py`'s `_get_main_head()`, never in `db.py`. The spec claims this follows "the same convention as codemerge's `base_commit` detection" but **codemerge does NOT auto-detect base_commit** -- it's a plain string parameter passed by the caller (merge.py `start_session()` takes `base_commit: str = ""`). The spec hallucinated an auto-detection pattern that does not exist.

**FATAL-3: `staleness_check` requires `subprocess` calls from the database layer -- breaks architecture** (db.py, server.py)

The spec's summary table says `staleness_check` goes in "server.py, db.py (new function)". The staleness check requires running `git log`, `git diff --stat`, and `git diff --quiet` commands. But the existing codebase has a strict architectural boundary: **`db.py` is pure SQLite operations with zero subprocess calls**. The only subprocess usage is `_get_main_head()` in `server.py`, and even codemerge deliberately passes git info as parameters rather than calling git from the db layer. Putting git operations in `db.py` violates the established pattern. Putting them only in `server.py` means the "new function in db.py" claim is wrong.

---

## SERIOUS

**SERIOUS-1: `reported_at_commit` immutability is unenforceable** (spec line 63-64)

The spec states "`reported_at_commit` is not updatable -- it's an immutable fact." But the `update_finding()` function in db.py (line 236) uses `meta_update` to merge arbitrary keys into the meta JSON. There is no mechanism in the current `update_finding()` to block updates to specific columns. If someone adds `reported_at_commit` as a column, nothing in the existing update pattern prevents it from being set via a raw `UPDATE` or a future code change. The spec provides no enforcement mechanism (no trigger, no application-level guard) -- it's just a stated intention.

**SERIOUS-2: `LIKE '{commit}%'` prefix match on commit SHA is an SQL injection vector** (spec line 57)

The spec proposes filtering by commit via `LIKE '{commit}%'`. If the commit string format is `f"LIKE '{commit}%'"` as shown (string interpolation), this is a SQL injection vulnerability. The existing codebase uses parameterized queries everywhere (`?` placeholders). The spec's notation is ambiguous -- if implemented with f-strings as written, it's injectable. Even with parameterization, LIKE with user-supplied prefix and no escaping of `%` and `_` in the input means a caller can pass `%` to match everything.

**SERIOUS-3: N+1 git subprocess calls in `staleness_check`** (spec lines 108-111)

`staleness_check(status="open")` will run `git log` and `git diff` **per finding**. With 500 open findings across 100 files, that's 500-1000 subprocess spawns. Each `git log --oneline <sha>..HEAD -- <file>` forks a process and reads git history. The spec says "All operations scoped to the finding's file path, keeping it fast" -- this is false for any non-trivial number of findings. There is no batching, no deduplication by file path (multiple findings on the same file would run identical git commands), and no caching.

**SERIOUS-4: Schema migration strategy is incomplete** (spec lines 21-27)

The spec shows raw `ALTER TABLE` SQL but the codebase initializes schema via the `SCHEMA` constant string in `db.py` (line 16). New columns must be added to this constant AND to the `_migrate_statuses()` rebuild DDL. The spec doesn't mention either. If you only add the ALTER TABLE statements, existing databases get the columns but newly created databases from the SCHEMA constant won't have them. If you only modify the SCHEMA constant, existing databases won't get them until the next table rebuild.

---

## WEAKNESS

**WEAKNESS-1: Index on `reported_at_commit` is low-value** (spec line 26)

An index on a 40-character SHA text column that will be queried via `LIKE 'abc%'` prefix match cannot use a standard B-tree index efficiently for the LIKE pattern (SQLite can use the index for prefix LIKE only if the column is not `TEXT` with a non-binary collation -- but the default `BINARY` collation does work). More importantly, how often will anyone query by commit SHA? This is a write-heavy, rarely-queried field. The index adds write overhead for marginal read benefit.

**WEAKNESS-2: No deduplication strategy for staleness per file** (spec lines 82-111)

If 20 findings reference `src/auth.py`, `staleness_check` will run `git log <sha>..HEAD -- src/auth.py` twenty times (once per finding, each potentially with a different `reported_at_commit`). The spec should propose grouping by (file, reported_at_commit) to deduplicate git calls.

**WEAKNESS-3: `reported_at_ref` adds a column for data that fits in `meta`** (spec line 30)

The existing pattern for optional metadata is the `meta` JSON field. Tags, notes, and arbitrary key-value pairs all go there. Adding a dedicated column for `reported_at_ref` (an optional, caller-supplied label) is inconsistent -- `meta.version` or `meta.ref` would work identically and require zero schema changes. The only argument for a column is queryability, but `json_extract(meta, '$.ref')` already works and is used for `meta_key`/`meta_value` filtering.

**WEAKNESS-4: Staleness for renamed files is completely unaddressed** (spec lines 98-104)

Git tracks renames. A file renamed from `auth.py` to `authentication.py` will show as "deleted" in `git diff --stat <old_sha>..HEAD -- auth.py`. The finding will be marked `deleted` (strong signal for auto-closing) even though the code is alive under a new name. This is a false positive for obsoletion that the spec ignores entirely. `git log --follow` exists for this purpose.

---

## NITPICK

**NITPICK-1: Spec says "same convention as codemerge's `base_commit` detection"** (spec line 36)

This is misleading at best. Codemerge's `base_commit` is a caller-supplied string with a default of `""`. There is no "detection." The spec is borrowing credibility from a pattern that doesn't exist.

**NITPICK-2: Staleness status name collision** (spec line 99)

The staleness check returns a `staleness` field with values like `"current"`, `"modified"`, `"deleted"`, `"unknown"`. The finding itself has a `status` field with values like `"open"`, `"stale"`. The obsoletion workflow then sets `status="stale"`. Having both a `staleness` response field and a `status="stale"` value in the same domain is confusing naming.

**NITPICK-3: Response example uses short SHA** (spec line 93)

The example shows `"reported_at_commit": "abc123def..."` which is only 9 characters. The spec says "full 40-char git SHA." The example contradicts the constraint, which will confuse implementers about what to validate.

---

## Summary Scorecard

| Category | Count |
|----------|-------|
| FATAL    | 3     |
| SERIOUS  | 4     |
| WEAKNESS | 4     |
| NITPICK  | 3     |

**Verdict:** This spec cannot be implemented as written. The three FATAL issues -- the migration race condition, the hallucinated codemerge auto-detection pattern, and the architecture violation of putting subprocess calls in the db layer -- each independently block implementation. The spec needs a revision that:

1. Decides where git subprocess calls live (server.py only, matching existing pattern)
2. Acknowledges that codemerge does NOT auto-detect base_commit and proposes a real auto-population strategy
3. Addresses the migration ordering problem with `_migrate_statuses()`
4. Adds batching/deduplication to `staleness_check` to avoid the N+1 subprocess problem
5. Clarifies whether provenance fields on `batch_add` are per-finding or per-batch
