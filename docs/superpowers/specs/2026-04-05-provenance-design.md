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

CREATE INDEX IF NOT EXISTS idx_findings_reported_at_commit ON findings(reported_at_commit);
CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref);
```

- `reported_at_commit` — full 40-char git SHA at the time the finding was created.
- `reported_at_ref` — optional version/tag label (e.g., `"v2.1.0"`), always caller-supplied.

Both are nullable. Existing findings get NULL (no backfill — we can't know what commit was current). No table rebuild needed; SQLite `ALTER TABLE ADD COLUMN` handles this.

### Auto-Population

When `add()` or `batch_add()` is called without an explicit `reported_at_commit`, the system runs `git rev-parse HEAD` from the current working directory (same convention as codemerge's `base_commit` detection). If git is unavailable or the directory isn't a repo, the field stays NULL.

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
| `commit` | str \| None | Prefix match via `LIKE '{commit}%'` — works with both full and short SHAs |
| `ref` | str \| None | Exact match on `reported_at_ref` |

#### `update()`

- `reported_at_ref` is updatable (you might tag a release after findings were filed).
- `reported_at_commit` is **not updatable** — it's an immutable fact about when the finding was created.

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

Returns a list of findings, each enriched with a `staleness` object:

```json
{
  "finding_id": "CB-12",
  "file": "src/auth.py",
  "staleness": "modified",
  "reason": "src/auth.py modified in 14 commits since reported_at_commit",
  "reported_at_commit": "abc123def...",
  "current_head": "def456abc..."
}
```

#### Staleness Statuses

| Status | Meaning | Detection |
|--------|---------|-----------|
| `current` | File unchanged since report | `git diff --quiet reported..HEAD -- file` |
| `modified` | File changed but still exists | `git diff --stat` shows changes |
| `deleted` | File no longer exists | File path not found at HEAD |
| `unknown` | Can't determine | No `reported_at_commit`, unreachable SHA, or not a git repo |

#### Git Operations

- `git log --oneline <reported_at_commit>..HEAD -- <file>` for commit count
- `git diff --stat <reported_at_commit>..HEAD -- <file>` for modification check
- All operations scoped to the finding's file path, keeping it fast
- Unreachable commits (force-push, shallow clone) return `unknown` — no error

#### Findings Without Provenance

Findings with NULL `reported_at_commit` return staleness `"unknown"` with reason `"no_provenance"`.

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
| 2 new columns + 2 indexes on `findings` | db.py schema |
| Auto-populate `reported_at_commit` on add | db.py `add_finding()` |
| 2 new params on `add`/`batch_add` tools | server.py |
| 2 new filters on `query` tool | server.py, db.py |
| `reported_at_ref` updatable via `update` | server.py, db.py |
| New `staleness_check` MCP tool | server.py, db.py (new function) |
