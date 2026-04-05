# Finding Provenance: Commit & Version Tracking

**Date:** 2026-04-05
**Status:** Draft
**Module:** codebugs core (db.py, server.py)

## Problem

Findings have no record of *when in the codebase's history* they were reported. This makes it impossible to:

- Detect staleness — has the file changed significantly since the bug was filed?
- Auto-obsolete findings about deleted code
- Filter by version/release for release preparation workflows

## Design

### Schema Changes

Two new nullable columns on `findings`:

```sql
ALTER TABLE findings ADD COLUMN reported_at_commit TEXT;
ALTER TABLE findings ADD COLUMN reported_at_ref TEXT;

CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref);
```

- `reported_at_commit` — full 40-char git SHA at the time the finding was created.
- `reported_at_ref` — optional version/tag label (e.g., `"v2.1.0"`), always caller-supplied.

Both are nullable. Existing findings get NULL (no backfill — we can't know what commit was current).

Only `reported_at_ref` gets an index (exact-match filtering for release workflows). `reported_at_commit` is queried rarely enough that an index isn't worth the write overhead.

### Migration Strategy

Three sites must be updated atomically:

1. **`SCHEMA` constant** — add both columns to the CREATE TABLE DDL so fresh databases include them.
2. **`_migrate_statuses()` hardcoded DDL** — update the CREATE TABLE inside the rebuild to include both columns, so the table rebuild doesn't silently drop them.
3. **New `_migrate_provenance()` function** — runs after `_migrate_statuses()` in `connect()`. Checks `PRAGMA table_info(findings)` for the columns and runs `ALTER TABLE ADD COLUMN` only if missing. This handles existing databases that already passed the status migration.

### Auto-Population

When `add()` or `batch_add()` is called without an explicit `reported_at_commit`, the server layer runs `git rev-parse HEAD` via subprocess (following the architectural precedent of `_get_main_head()` in `server.py`). This is a new pattern — codemerge's `base_commit` is purely caller-supplied. If git is unavailable or the directory isn't a repo, the field stays NULL.

`reported_at_ref` is never auto-populated — the system can't know what version label to assign.

### Tool Interface Changes

#### `add()` and `batch_add()`

Two new optional parameters:

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `reported_at_commit` | str \| None | auto from `git rev-parse HEAD` | Full SHA preferred |
| `reported_at_ref` | str \| None | None | Caller-supplied version label |

#### `query()`

Two new optional filters:

| Filter | Type | Behavior |
|--------|------|----------|
| `commit` | str \| None | Validated as hex (`[0-9a-f]+`), then parameterized prefix match via `LIKE ? \|\| '%'` |
| `ref` | str \| None | Exact match on `reported_at_ref` |

#### `update()`

- `reported_at_ref` is updatable (you might tag a release after findings were filed).
- `reported_at_commit` is **not updatable** — it's an immutable fact about when the finding was created. Enforced structurally: `update_finding()` uses a whitelist of parameters and does not accept `reported_at_commit`. A code comment documents this invariant.

#### `summary()` and `stats()`

No changes. These aggregate by severity/status/category, which is orthogonal to provenance.

### Staleness Detection

New MCP tool: **`staleness_check`**

#### Parameters

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `finding_id` | str \| None | None | Check a single finding |
| `status` | str \| None | `"open"` | Filter which findings to check |
| `category` | str \| None | None | Further filtering |
| `file` | str \| None | None | Further filtering |

#### Response

Returns a list of findings, each enriched with a `file_status` field:

```json
{
  "finding_id": "CB-12",
  "file": "src/auth.py",
  "file_status": "modified",
  "reason": "src/auth.py modified in 14 commits since reported_at_commit",
  "reported_at_commit": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
  "current_head": "f0e1d2c3b4a5f6e7d8c9b0a1f2e3d4c5b6a7f8e9"
}
```

The field is named `file_status` (not `staleness`) to avoid confusion with the existing `stale` finding status.

#### File Statuses

| Status | Meaning | Detection |
|--------|---------|-----------|
| `current` | File unchanged since report | `git diff --quiet reported..HEAD -- file` |
| `modified` | File changed but still exists | `git diff --stat` shows changes |
| `renamed` | File renamed/moved but content exists | `git log --diff-filter=R --find-renames -- file` detects rename; new path included in reason |
| `deleted` | File no longer exists | File path not found at HEAD and no rename detected |
| `unknown` | Can't determine | No `reported_at_commit`, unreachable SHA, or not a git repo |

#### Architecture & Git Operations

**Layer separation:** All git subprocess calls live in `server.py` (following the `_get_main_head()` precedent). `db.py` provides only a query helper to fetch findings needing staleness checks. The `staleness_check` MCP tool in `server.py` orchestrates: query DB → run git → assemble response.

**Batching:** Findings are grouped by unique file path before git operations. One `git log` + one `git diff` per file, not per finding. For N findings across M unique files, this is 2M subprocess calls (plus one rename check per missing file), not 2N.

**Git commands per file:**

- `git log --oneline <reported_at_commit>..HEAD -- <file>` for commit count
- `git diff --stat <reported_at_commit>..HEAD -- <file>` for modification check
- `git log --diff-filter=R --find-renames -- <file>` for rename detection (only when file appears deleted)
- Unreachable commits (force-push, shallow clone) return `unknown` — no error

#### Findings Without Provenance

Findings with NULL `reported_at_commit` return `file_status: "unknown"` with reason `"no_provenance"`.

### Obsoletion Workflow

No dedicated obsoletion tool. Obsoletion is a workflow over existing primitives:

1. `staleness_check(status="open")` — get staleness for all open findings
2. `deleted` findings — strong signal for auto-closing via `update(finding_id, status="stale", meta_update={"stale_reason": "file_deleted", ...})`
3. `modified` findings — surfaced for triage (modification doesn't mean the bug is fixed)
4. `unknown` / `current` — no action

The `stale` status already exists in the schema. The decision of what to auto-close vs. triage belongs to the caller (human or agent), not the database layer.

### What This Design Does NOT Include

- **Automatic staleness on every query** — too expensive (git ops per finding). Use `staleness_check` explicitly.
- **File content hashing** — git SHAs already capture this; no need for a parallel mechanism.
- **Finding version history / audit log** — out of scope. The `updated_at` timestamp and `meta` field cover basic change tracking.
- **Auto-detection of version tags** — ambiguous (a commit can have multiple tags). Always caller-supplied.

## Summary

| Change | Scope |
|--------|-------|
| 2 new columns + 1 index on `findings` | db.py SCHEMA constant |
| Migration: update `_migrate_statuses()` DDL + new `_migrate_provenance()` | db.py |
| Auto-populate `reported_at_commit` via `git rev-parse HEAD` | server.py (subprocess) |
| 2 new params on `add`/`batch_add` tools | server.py |
| 2 new filters on `query` tool (hex-validated, parameterized) | server.py, db.py |
| `reported_at_ref` updatable via `update` | server.py, db.py |
| New `staleness_check` MCP tool (git ops in server.py, queries in db.py) | server.py, db.py |

## Adversarial Review Corrections

Applied 2026-04-05 after three-agent adversarial review (Adversary → Defender → Judge).

**Mandatory fixes applied:**

| ID | Issue | Fix |
|----|-------|-----|
| FATAL-1 | `_migrate_statuses()` rebuild would drop new columns | Added Migration Strategy section: update SCHEMA, rebuild DDL, and new `_migrate_provenance()` |
| FATAL-2 | Hallucinated "codemerge base_commit detection" pattern | Rewritten as new pattern, citing `_get_main_head()` precedent |
| FATAL-3 | Git subprocess calls in db.py violate architecture | All git ops moved to server.py; db.py provides query helpers only |
| SERIOUS-2 | SQL injection via LIKE string interpolation | Hex validation + parameterized `LIKE ? \|\| '%'` |
| SERIOUS-3 | N+1 subprocess spawning per finding | Batch by unique file path (2M calls, not 2N) |

**Recommended fixes applied:**

| ID | Issue | Fix |
|----|-------|-----|
| WEAKNESS-4 | File renames produce false "deleted" | Added `--find-renames` detection, new `renamed` file status |
| NITPICK-2 | staleness/stale naming collision | Response field renamed to `file_status` |
| NITPICK-3 | Example showed short SHA | Updated to full 40-char SHA |
| WEAKNESS-1 | Low-value index on reported_at_commit | Dropped; only ref index kept |
| SERIOUS-1 | Immutability not documented | Added structural enforcement note + code comment requirement |

**Dismissed:** WEAKNESS-3 (`reported_at_ref` belongs in meta) — defender showed it follows existing column-per-filter pattern.

**Design health:** 5/10 pre-review → estimated 8/10 post-fixes.
