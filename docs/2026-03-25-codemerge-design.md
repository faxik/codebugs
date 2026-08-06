# Codemerge: Coordinated Parallel Session Merging

**Date**: 2026-03-25
**Status**: Draft
**Author**: Brainstorming session

## Problem

Multiple independent Claude Code sessions work on different features in parallel worktrees. When they merge to main, several failure modes occur:

1. **Textual conflicts** — two sessions modify the same file; second merge conflicts
2. **Semantic breakage** — sessions modify different files but interacting code; main breaks after both merge
3. **Race conditions** — two sessions merge simultaneously; flock serializes but doesn't prevent stale-base issues
4. **Simplify-on-main** — `/simplify` runs on main after merge, creating dirty working tree conflicts when another session merges concurrently
5. **Post-integration fix cascade** — 30% of commits are `fix:` prefixes correcting breakage from blind merges

**Current mitigations** (from CLAUDE.md):
- `flock` in `worktree-finish.sh` serializes the merge moment
- Cherry-pick with merge --no-ff fallback
- Manual `comm -12` overlap check (rarely done in practice)
- CHANGELOG.md excluded from agent work

**What's missing**: a priori conflict detection and coordinated merge paths.

## Solution

Extend the **codebugs** MCP server with a `codemerge` namespace that provides centralized coordination for parallel sessions merging to the same main branch.

### Core Principle

The system is **not sequential**. N sessions work freely in parallel. Only the merge moment is coordinated. A PostToolUse hook automatically tracks which files each session modifies. At merge time, the system detects overlaps and routes to the appropriate merge strategy:

- **Clean path** (no overlap): acquire merge lock, merge worktree to main directly
- **Dirty path** (overlap detected): merge main into worktree first, re-test, then merge to main

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Codebugs MCP Server                       │
│                                                              │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────────────┐  │
│  │ codebugs │  │   reqs    │  │       codemerge          │  │
│  │ (bugs)   │  │ (require- │  │  (session coordination)  │  │
│  │          │  │  ments)   │  │                          │  │
│  └──────────┘  └───────────┘  └──────────────────────────┘  │
│                       │                                      │
│                  SQLite DB                                    │
└─────────────────────────────────────────────────────────────┘
         ▲                              ▲
         │                              │
    MCP calls                   PostToolUse hook
    (explicit)                  (automatic claims)
         │                              │
┌────────┴──────────────────────────────┴──────────┐
│              Claude Code Sessions                 │
│                                                   │
│  Session A          Session B          Session C  │
│  (feature/foo)      (fix/bar)         (refactor/) │
│  worktree-A         worktree-B        worktree-C  │
└───────────────────────────────────────────────────┘
```

## MCP Tools

### Session Lifecycle

```
codemerge_start(session_id, branch, description)
```
Register a new working session. Called at worktree creation (step 4 in lifecycle).
- `session_id`: worktree slug (unique, maps 1:1 to branch)
- `branch`: git branch name
- `description`: what this session is doing
- Returns: `{session_id, started_at}`

```
codemerge_done(session_id)
```
Release all claims, mark session complete. Called after successful merge (step 8).
- Releases merge lock if held
- Sets status to `done`
- Claims remain in DB for audit trail but are no longer blocking

### File Claims

```
codemerge_claim(session_id, file_path)
```
Register that a session has modified a file. Called automatically by PostToolUse hook on every Edit/Write.
- Idempotent — claiming the same file twice is a no-op
- Only tracks files under `src/`, `tests/`, `dashboard-ui/src/`, and config files
- Ignores: `.claude/`, `.worktrees/`, `docs/`, `CHANGELOG.md`, `TODO.md`

```
codemerge_claims(session_id)
```
List all claimed files for a session.
- Returns: `[{file_path, claimed_at}]`

### Pre-Merge Coordination

```
codemerge_check(session_id)
```
Check for conflicts before merging. This is the critical decision point.
- Compares this session's claimed files against:
  1. Other **active** sessions' claims (parallel work)
  2. Files changed on main since this session branched (main has moved)
- Returns:
  ```json
  {
    "clean": false,
    "conflicts": [
      {"file": "src/autosorter/dashboard/routers/facts.py",
       "blocking_session": "fix-dashboard-nav",
       "blocking_branch": "fix/dashboard-nav",
       "type": "parallel_session"},
      {"file": "src/autosorter/core/container.py",
       "blocking_session": "main",
       "blocking_branch": "main",
       "type": "main_diverged"}
    ],
    "recommendation": "dirty"  // or "clean"
  }
  ```

```
codemerge_acquire(session_id)
```
Acquire exclusive merge lock. Only one session can merge at a time.
- Blocks (with timeout) if another session holds the lock
- Lock has TTL of 5 minutes (auto-expires for crash safety)
- Returns: `{acquired: true, token: "..."}` or `{acquired: false, holder: "...", held_since: "..."}`

```
codemerge_release(session_id)
```
Release the merge lock after merge completes (success or failure).

### Visibility

```
codemerge_sessions(status?)
```
List all active sessions with their claimed files.
- Optional filter by status: `active`, `merging`, `done`, `abandoned`
- Returns: `[{session_id, branch, description, status, started_at, claim_count, files: [...]}]`

```
codemerge_status()
```
Dashboard summary.
- Returns: `{active_sessions: N, total_claims: M, conflicts: K, lock_holder: "..." | null}`

```
codemerge_abandon(session_id)
```
Manually mark a stale session as abandoned. Releases all claims and lock.
- Safety: sessions with no activity for >2 hours are auto-marked as stale (shown in `codemerge_sessions()`)

## SQLite Schema

```sql
-- Same database as codebugs, new tables with codemerge_ prefix

CREATE TABLE IF NOT EXISTS codemerge_sessions (
    session_id   TEXT PRIMARY KEY,
    branch       TEXT NOT NULL,
    description  TEXT,
    repo_root    TEXT NOT NULL,
    base_commit  TEXT NOT NULL,          -- commit SHA when session branched
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_activity TEXT NOT NULL DEFAULT (datetime('now')),
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'merging', 'done', 'abandoned')),
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS codemerge_claims (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES codemerge_sessions(session_id),
    file_path    TEXT NOT NULL,           -- relative to repo root
    claimed_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, file_path)
);

CREATE TABLE IF NOT EXISTS codemerge_locks (
    id           INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
    session_id   TEXT REFERENCES codemerge_sessions(session_id),
    acquired_at  TEXT,
    expires_at   TEXT                     -- TTL-based auto-expiry
);

CREATE INDEX IF NOT EXISTS idx_claims_file ON codemerge_claims(file_path);
CREATE INDEX IF NOT EXISTS idx_claims_session ON codemerge_claims(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON codemerge_sessions(status);
```

## PostToolUse Hook

Automatically claims files when agents edit them. The agent never needs to know about codemerge.

### Hook Configuration (settings.json)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": {"toolName": "^(Edit|Write)$"},
        "command": "python /home/faxik/w/autosorter/tools/codemerge-hook.py claim"
      }
    ]
  }
}
```

### Hook Script (tools/codemerge-hook.py)

Receives tool use JSON on stdin. Extracts `file_path`, determines session ID from current git branch/worktree slug, calls codemerge_claim via the MCP server.

```python
#!/usr/bin/env python3
"""PostToolUse hook: auto-claim files in codemerge."""
import json, sys, os, subprocess

def main():
    event = json.load(sys.stdin)
    tool_name = event.get("tool_name", "")

    if tool_name not in ("Edit", "Write"):
        return

    file_path = event.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    # Determine repo-relative path
    repo_root = os.environ.get("CLAUDE_REPO_ROOT", "/home/faxik/w/autosorter")
    if file_path.startswith(repo_root):
        rel_path = os.path.relpath(file_path, repo_root)
    else:
        rel_path = file_path

    # Skip non-source files
    SKIP_PREFIXES = (".claude/", ".worktrees/", "docs/", "CHANGELOG.md", "TODO.md")
    if any(rel_path.startswith(p) for p in SKIP_PREFIXES):
        return

    # Determine session ID from git branch
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=os.path.dirname(file_path) or ".",
            text=True, timeout=5
        ).strip()
    except Exception:
        return

    if branch == "main":
        return  # Don't track main workspace edits

    session_id = branch.replace("/", "-")

    # Call codemerge_claim via MCP
    # Implementation depends on MCP client availability in hooks
    # Option A: HTTP call to codebugs server
    # Option B: Direct SQLite write (same DB)
    # Option C: Write to a claim file that the server picks up

    # Simplest: direct SQLite (codebugs DB path from env)
    db_path = os.environ.get("CODEBUGS_DB", os.path.expanduser("~/.codebugs/autosorter.db"))

    import sqlite3
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("""
        INSERT OR IGNORE INTO codemerge_claims (session_id, file_path)
        VALUES (?, ?)
    """, (session_id, rel_path))
    conn.execute("""
        UPDATE codemerge_sessions SET last_activity = datetime('now')
        WHERE session_id = ?
    """, (session_id,))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
```

## Merge Flow

### Current Flow (worktree-finish.sh)

```
commit → flock → cherry-pick/merge → remove worktree
```

### New Flow (worktree-finish.sh updated)

```
simplify (in worktree) → re-test (in worktree) → commit →
                                │
                    codemerge_check(session_id)
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                  CLEAN                   DIRTY
                    │                       │
                    │               merge main → worktree
                    │               re-test in worktree
                    │               re-commit if needed
                    │                       │
                    └───────────┬───────────┘
                                │
                    codemerge_acquire(session_id)
                                │
                    flock + cherry-pick/merge to main
                                │
                    codemerge_release(session_id)
                    codemerge_done(session_id)
                                │
                        remove worktree
```

### Integration with worktree-finish.sh

The script gains a new phase between commit and flock:

```bash
# After commit, before flock:

# 1. Check for conflicts via codemerge
MERGE_CHECK=$(codemerge_check "$SESSION_ID")
IS_CLEAN=$(echo "$MERGE_CHECK" | jq -r '.clean')

# 2. If dirty, update worktree from main first
if [ "$IS_CLEAN" = "false" ]; then
    echo "⚠ Conflicts detected, merging main into worktree first..."
    git -C "$WORKTREE_PATH" merge main --no-edit

    if [ $? -ne 0 ]; then
        echo "❌ Merge conflict in worktree. Resolve manually."
        exit 1
    fi

    # Re-run tests after merging main
    echo "Re-running tests after main merge..."
    cd "$WORKTREE_PATH" && python -m pytest tests/ --no-testmon --timeout=60 -q

    if [ $? -ne 0 ]; then
        echo "❌ Tests fail after merging main. Fix before integrating."
        exit 1
    fi

    # Re-commit the merge
    git -C "$WORKTREE_PATH" add -A
    git -C "$WORKTREE_PATH" commit --no-verify -m "merge main before integration"
fi

# 3. Acquire merge lock (replaces raw flock)
codemerge_acquire "$SESSION_ID"

# 4. Proceed with existing cherry-pick/merge logic
# ...

# 5. Release and mark done
codemerge_release "$SESSION_ID"
codemerge_done "$SESSION_ID"
```

## Conflict Detection Algorithm

```python
def check_merge(session_id: str) -> dict:
    session = get_session(session_id)
    my_claims = get_claims(session_id)
    my_files = {c.file_path for c in my_claims}

    conflicts = []

    # 1. Check against other active sessions
    for other in get_sessions(status='active', exclude=session_id):
        other_files = {c.file_path for c in get_claims(other.session_id)}
        overlap = my_files & other_files
        for f in overlap:
            # Only conflict if the other session has ALREADY merged
            # (if both are still working, we only warn)
            conflicts.append({
                'file': f,
                'blocking_session': other.session_id,
                'blocking_branch': other.branch,
                'type': 'parallel_session',
                'severity': 'warn' if other.status == 'active' else 'block'
            })

    # 2. Check if main has moved since we branched
    main_head = git_rev_parse('main')
    if main_head != session.base_commit:
        # Files changed on main since we branched
        main_changed = git_diff_files(session.base_commit, main_head)
        main_overlap = my_files & set(main_changed)
        for f in main_overlap:
            conflicts.append({
                'file': f,
                'blocking_session': 'main',
                'blocking_branch': 'main',
                'type': 'main_diverged',
                'severity': 'block'
            })

    has_blocks = any(c['severity'] == 'block' for c in conflicts)

    return {
        'clean': len(conflicts) == 0,
        'conflicts': conflicts,
        'recommendation': 'dirty' if has_blocks else 'clean',
        'main_behind': main_head != session.base_commit
    }
```

## Stale Session Handling

Sessions can be abandoned (Claude Code crashes, user kills terminal).

1. **Lock TTL**: Merge lock expires after 5 minutes. If a session crashes while holding the lock, the next `acquire` call reclaims it.

2. **Session staleness**: Sessions with `last_activity` older than 2 hours are flagged as stale in `codemerge_sessions()` output. They still block conflict checks.

3. **Manual cleanup**: `codemerge_abandon(session_id)` marks a session as abandoned, releasing all claims and lock. Use when a session is known to be dead.

4. **Auto-abandon**: A cron/loop job could periodically mark sessions stale after N hours. Not required for v1 — manual cleanup is sufficient.

## Updated /simplify Flow

`/simplify` MUST always run inside a worktree. The updated rule:

### Normal Case (simplify before merge)
1. Agent finishes implementation in worktree
2. Runs `/simplify` on changed files — still in the same worktree
3. Re-runs tests
4. Commits
5. Proceeds to merge via `worktree-finish.sh`

### Post-Merge Simplify (worktree already gone)
If simplify is needed after merge (e.g., code reviewer requests it):
1. Create a new worktree: `simplify/<original-slug>` branched from current main
2. `codemerge_start()` for the new session
3. Run simplifications (hook auto-claims files)
4. Re-test
5. Merge back through the standard codemerge path

## Session Identity

Each session is identified by its **worktree slug** (derived from branch name):
- `feature/sidebar-nav` → `feature-sidebar-nav`
- `fix/CB-500-quality-issues` → `fix-CB-500-quality-issues`

This is already unique (git enforces unique branch names) and maps 1:1 to a worktree directory.

The slug is determined by:
1. `git rev-parse --abbrev-ref HEAD` in the worktree
2. Replace `/` with `-`

## CLAUDE.md Lifecycle Integration

### Step 3 (LOCK) — add:
```
Call `codemerge_start(session_id, branch, description)` to register the session.
```

### Step 5 (IMPLEMENT) — add:
```
PostToolUse hook automatically calls `codemerge_claim()` on every Edit/Write.
No manual action needed.
```

### Step 7 (SIMPLIFY) — updated:
```
Run `/simplify` inside the worktree. NEVER on main.
```

### Step 8 (MERGE) — updated:
```
worktree-finish.sh calls codemerge_check() → clean/dirty path → codemerge_acquire/release/done.
```

## Implementation Plan (high-level)

### Phase 1: SQLite schema + MCP tools
- Add codemerge tables to codebugs DB
- Implement 8 MCP tools (start, done, claim, claims, check, acquire, release, sessions, status, abandon)
- Unit tests for conflict detection logic

### Phase 2: PostToolUse hook
- `tools/codemerge-hook.py` — auto-claim on Edit/Write
- Hook configuration in settings.json
- Integration test: edit file → verify claim appears

### Phase 3: worktree-finish.sh integration
- Add codemerge_check + dirty path (merge main → worktree → re-test)
- Replace raw flock with codemerge_acquire/release
- Keep flock as inner lock (codemerge_acquire wraps it)

### Phase 4: /simplify update
- Update simplify skill to detect "am I on main?" and auto-create worktree
- Register simplify worktrees with codemerge

## Open Questions

1. **Should `codemerge_check` also detect semantic conflicts?** (e.g., Session A modifies a function that Session B's test imports). This requires import graph analysis (jcodemunch `get_blast_radius`). Defer to v2.

2. **Should claims include line ranges?** Two sessions editing different functions in the same file might not conflict. But line-level tracking adds complexity and git merge already handles this. Defer to v2.

3. **Should worktree-finish.sh run tests before merge?** Currently it doesn't. Adding a test gate would catch breakage before it hits main but adds time. Configurable flag: `--test-before-merge`.

4. **Notification when a parallel session claims a file you also claimed?** Useful for long-running sessions. Could be a `/loop` check or a hook. Defer to v2.
