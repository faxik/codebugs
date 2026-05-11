# codebugs

**Persistent code finding, requirements, and release tracker for AI assistants.** SQLite-backed, exposed via MCP server + CLI.

AI assistants lose context between sessions. codebugs gives them durable memory for code review findings, requirements, dependency blockers, parallel-agent coordination, and release milestones — with minimal token overhead.

```
Session 1:  Review code → log 50 findings → forget them
Session 2:  summary → instant orientation → fix 20 → update status
Session 3:  pull_next → claim work → mark integrated → next agent picks up
```

No context lost. No re-reading files. No token-heavy recaps. Parallel agents don't race.

## Why codebugs

Building a real codebase with AI assistants creates four problems that compound over time:

1. **Findings get lost.** You spend 20K tokens reviewing a file, log 12 bugs in chat, and the next session has no idea they exist.
2. **Requirements drift.** REQUIREMENTS.md gets edited by hand, forgotten, contradicted by code, and nobody catches it.
3. **Parallel agents race.** Two agents both pick the same bug, both edit the same file, both think they've shipped it.
4. **Releases lose track of what's in them.** Work sits stranded on feature branches for 9 days. "Where are we on 1.1?" has no single answer.

codebugs is one SQLite database (`.codebugs/findings.db`) that solves all four. Eight self-contained modules, 59 MCP tools, one CLI.

## Install

```bash
# Global install (recommended)
pipx install codebugs

# Or with pip/uv
pip install codebugs
```

## Setup

### Claude Code (MCP)

Add to `~/.claude.json` (global) or `.mcp.json` (per-project):

```json
{
  "mcpServers": {
    "codebugs": {
      "command": "codebugs-mcp"
    }
  }
}
```

The database lives at `.codebugs/findings.db` in the current working directory — each project gets its own. Add `.codebugs/` to your `.gitignore`.

### Running Modules Independently

Use `--mode` to load only the tools you need:

```json
{
  "mcpServers": {
    "codebugs": {
      "command": "codebugs-mcp",
      "args": ["--mode", "findings"]
    }
  }
}
```

| Mode | Tools | Use it when |
|------|-------|-------------|
| `findings` | 8 | Code review / bug tracking only |
| `reqs` | 11 | Specification tracking only |
| `sweep` | 9 | Batch iteration / state-machine tasks |
| `bench` | 4 | Performance benchmarks |
| `merge` | 5 | Multi-agent merge coordination |
| `blockers` | 4 | Cross-entity dependency tracking |
| `milestones` | 18 | Release + stream + capacity-aware pull |
| `all` | **59** | Default — everything |

The CLI takes the same flag: `codebugs --mode findings summary`.

### Other MCP Clients

Any MCP-compatible client can connect to `codebugs-mcp` via stdio transport.

## The eight modules

| Module | Domain | Headline tools |
|--------|--------|----------------|
| **findings** | Bugs, tech-debt, review findings | `summary`, `add`, `query`, `categories` |
| **reqs** | Functional requirements (FR-N) | `reqs_summary`, `reqs_add`, `reqs_verify`, `reqs_search_similar` |
| **blockers** | "X is blocked by Y" dependency graph | `blockers_add`, `blockers_check` |
| **sweep** | Batch iteration with state machines | `codesweep_create`, `codesweep_next`, `codesweep_mark` |
| **bench** | Performance benchmark snapshots | `codebench_import`, `codebench_query` |
| **merge** | Parallel-agent merge serialization | `codemerge_start`, `codemerge_claim` |
| **milestones** | Releases, streams, capacity-aware pull | `pull_next`, `milestone_status`, `milestone_close` |

Modules are self-registering — adding a new one is local to its own file. See [`docs/superpowers/specs/`](docs/superpowers/specs/) for the architecture history.

## Quick tour

### Findings — log it, never re-discover it

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `summary` | Dashboard overview — **start here** for orientation |
| `add` | Log a finding with severity, category, file, description |
| `batch_add` | Log multiple findings at once |
| `update` | Change status, add notes, update tags or metadata |
| `query` | Search/filter with pagination and group-by |
| `stats` | Cross-tabulated counts (severity x category/file/status) |
| `categories` | List existing categories — **call before `add`** for consistency |
| `staleness_check` | Compare against git history; mark obsolete findings stale |

**CLI:**

```bash
codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42"
codebugs summary
codebugs query --status open --severity critical
codebugs update CB-1 --status fixed --notes "Fixed in PR #42"
codebugs categories
```

When a new finding is added, the **milestones auto-router** automatically attaches it to `stream/triage` (or `stream/security` when `severity=critical` and `category` starts with `security:`). The finding and its triage entry land in the same transaction.

### Requirements — verify what shipped, surface contradictions

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `reqs_summary` | Requirements dashboard — **start here** |
| `reqs_add` | Add a requirement (FR-001, priority, status, test coverage) |
| `reqs_update` | Change status, description, priority, test coverage |
| `reqs_query` | Search/filter by status, priority, section, free text |
| `reqs_stats` | Cross-tabulated counts (status x priority) |
| `reqs_verify` | Automated checks: ghost test files, duplicate IDs, status contradictions |
| `reqs_import` | Import from REQUIREMENTS.md (parses markdown tables) |
| `reqs_embed` / `reqs_batch_embed` | Store embedding vectors |
| `reqs_search_similar` | Semantic search across requirements |
| `reqs_embedding_stats` | Report on embedding coverage |

**CLI:**

```bash
codebugs reqs-import REQUIREMENTS.md
codebugs reqs-summary
codebugs reqs-verify
codebugs reqs-query --status Implemented --priority Must
codebugs reqs-update FR-090 --status Superseded --notes "Replaced by vault architecture"
codebugs reqs-export REQUIREMENTS.md
```

### Blockers — "X is blocked by Y", with auto-unblock

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `blockers_add` | Defer an item until another item resolves, a date passes, or a manual signal |
| `blockers_query` | List blockers filtered by item, dependency, trigger type |
| `blockers_check` | Find currently-actionable items (all blockers satisfied) |
| `blockers_resolve` | Cancel or manually resolve a blocker |

Triggers come in three flavors: `entity_resolved` (waits for another finding/requirement to reach a terminal state), `date` (unblocks on a specific datetime), and `manual` (operator signal). When you mark a finding `fixed`, every blocker that was waiting on it auto-unblocks and surfaces in the next `blockers_check`.

### Milestones — release containers + standing streams + capacity-aware pull

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `milestone_status` | Rollup for one milestone (counts by status/size, branch-only, blocked, days to target) |
| `milestone_list` | List milestones, filter by kind / state |
| `milestone_create` | Create a release or stream |
| `milestone_update` | Mutate `description`, `target_date`, `state` |
| `milestone_add_item` | Attach a bug / requirement / external ref to a milestone |
| `milestone_move_item` | Move an item between milestones |
| `milestone_set_status` | Open / in_progress / done / dismissed / deferred |
| `milestone_defer` | Move to `stream/maintenance` with status='deferred' |
| `milestone_close` | Refuses if open / branch-only / blocked items remain (force overrides, except for streams) |
| `milestone_audit_query` | Full state-transition history |
| `triage_inbox` | Items waiting to be triaged |
| `triage_dismiss` | Reject a triage item; propagates to underlying entity |
| `triage_promote` | Move a triage item to a target milestone |
| `pull_next` | **Atomically claim the next eligible item for the calling agent** |
| `release_item` | Free agent capacity (`status='done'` or `'abandoned'`) |
| `wip_status` | Snapshot of `agent_capacity` per agent |
| `mark_branch_only` | Flag an item as living on a feature branch only |
| `mark_integrated` | Mark merged-to-main with commit SHA; clears branch_only |

**Four seed milestones are created automatically:**

- `stream/triage` — inbox for unsorted findings (default destination)
- `stream/maintenance` — deferred / boy-scout work
- `stream/security` — urgent fixes (preempts release work)
- `release/1.1` — first post-1.0 release

**`pull_next` priority order:** `stream/security` > `release/*` (earliest `target_date` first) > `stream/triage` > `stream/maintenance`. Within a milestone: priority ASC, then `created_at` ASC.

**Eligibility:** item is `open`, no active blockers (skipped for `item_kind='external'`), acceptance required for `size='large'`, and a large bug in a release milestone must declare `linked_frs` whose ids resolve to rows in `requirements`. Concurrent calls from multiple agents are atomic — claims are serialized via `BEGIN IMMEDIATE`.

**CLI:**

```bash
codebugs milestone-list
codebugs milestone-status release/1.1
codebugs triage-inbox
codebugs wip-status
codebugs milestone-audit --milestone release/1.1
```

A typical autonomous-agent loop:

```python
# 1. Agent claims the next eligible item.
item = pull_next(agent_id="agent-A", capacity={"large": 1, "small": 2, "triage": 5})

# 2. (Optional) flag a feature branch.
mark_branch_only(item_ref=item["item_ref"], branch_name="feat/CB-1234")

# 3. After integration, mark it done with the commit SHA.
mark_integrated(item_ref=item["item_ref"], commit="abc123…")

# 4. Free the agent's capacity slot.
release_item(item_ref=item["item_ref"], status="done")
```

Closing a release runs the close-gate: unfinished, branch-only, and blocker-gated items refuse to let the milestone ship. `force=True` (with a logged reason) overrides — but `stream/*` milestones **cannot** be closed, even with force.

### Sweeps — batch iteration with recurrence-aware lifecycles

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `codesweep_create` | Create a new sweep (optional `lifecycle=[...]`, `terminal_states=[...]`, `transitions={...}` for state machines) |
| `codesweep_add` | Add items. **Atomic upsert**: existing items bump `recurrence_count`, refresh `last_seen`, un-archive |
| `codesweep_next` | Next batch of unprocessed (non-terminal, non-archived) items |
| `codesweep_mark` | Transition state (legacy `processed=True` still works) |
| `codesweep_status` | Progress overview |
| `codesweep_archive` / `codesweep_archive_items` | Soft-delete |
| `codesweep_list_items` / `codesweep_list` | Inspection |

```bash
codebugs sweep-create --name lint-pass --batch-size 5
codebugs sweep-add lint-pass src/*.py --tags critical
codebugs sweep-next lint-pass
codebugs sweep-mark lint-pass src/api.py
codebugs sweep-status lint-pass
```

With a custom lifecycle (e.g. for retro findings):

```bash
codebugs sweep-create --name retro-findings \
    --lifecycle DETECTED,CONFIRMED,ESCALATED,RESOLVED,DROPPED \
    --terminal-states RESOLVED,DROPPED
codebugs sweep-add retro-findings finding-2026-04-todo-bypassed --tags silent_abandonment
codebugs sweep-mark retro-findings finding-2026-04-todo-bypassed --state CONFIRMED
codebugs sweep-archive-items retro-findings --state RESOLVED --older-than 30d
```

### Bench — performance snapshots over time

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `codebench_import` | Import benchmark results (file or inline) |
| `codebench_query` | Filter and trend metrics across runs |
| `codebench_list` | List recorded runs |
| `codebench_delete` | Remove a run |

### Merge — parallel-agent merge serialization

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `codemerge_start` | Open a merge session |
| `codemerge_claim` | Claim files for the session (advisory file-level claims) |
| `codemerge_check` | Check for overlapping claims against `main` |
| `codemerge_merge` | Mark merge in progress (acquires the global merge lock with TTL) |
| `codemerge_finish` | Release the lock |

## How It Works

### The Problem

AI code review sessions produce findings that get lost. Multiple agents working in parallel double-claim work. Requirements files drift. Releases lose track of what's in them.

### The Solution

codebugs stores everything in one local SQLite database. AI assistants write findings, requirements, and milestone items as they discover them, then query the database in future sessions for instant context recovery. Concurrent agents coordinate via the same database — no race conditions, atomic claims.

**Token savings**: A `summary` call returns a structured JSON overview in ~200 tokens. Without codebugs, re-establishing the same context costs 2K–10K+ tokens of file reading and conversation history.

### Typical Workflows

**Code review loop**:

1. AI reviews code, calls `categories` for naming consistency, then `add` for each finding.
2. Each `add` auto-routes the finding to `stream/triage`.
3. Next session: AI calls `summary` → 50 open findings → `query --severity critical` → fixes the worst → `update CB-N --status fixed`.
4. Over time, `categories` reveals systemic issues — "12 `tz_naive_datetime` fixed across 9 files → time for a lint rule."

**Release loop**:

1. Triage: AI calls `triage_inbox` → `triage_dismiss` non-bugs, `triage_promote` real items to `release/1.1` (with `linked_frs` for the ones that need an FR row).
2. Execution: Each parallel agent calls `pull_next(agent_id=..., capacity=...)` → claims the next eligible item.
3. After landing: `mark_integrated(item, commit)` → `release_item(item, status='done')`.
4. Close: `milestone_close("release/1.1")`. Refuses if anything is stranded on a branch; lists the offenders with the branch name.

## Schema (highlights)

All tables share `.codebugs/findings.db` with flexible JSON columns. Schemas are additive — every module owns its tables, declares dependencies, and migrates additively.

### Findings

| Field | Type | Description |
|-------|------|-------------|
| `id` | text | Auto-generated (`CB-1`, `CB-2`, ...) or user-provided |
| `severity` | text | `critical`, `high`, `medium`, `low` |
| `category` | text | User-defined (e.g. `n_plus_one`, `missing_validation`, `security:xss`) |
| `file` | text | File path relative to project root |
| `status` | text | `open`, `in_progress`, `fixed`, `not_a_bug`, `wont_fix`, `stale` |
| `description` | text | What's wrong |
| `source` | text | `claude`, `ruff`, `human`, `mypy`, ... |
| `tags` | json | Array of strings for ad-hoc grouping |
| `meta` | json | `lines`, `module`, `rule_code`, `cwe_id`, ... |
| `reported_at_commit`, `reported_at_ref` | text | Provenance for staleness checks |

### Requirements

| Field | Type | Description |
|-------|------|-------------|
| `id` | text | User-provided (`FR-001`, `NFR-001`, ...) |
| `section`, `description`, `priority`, `status`, `source`, `test_coverage` | text | per-row metadata |
| `embedding` | blob | Optional float32 vector for semantic search |
| `tags`, `meta` | json | |

### Milestones

| Table | Purpose |
|-------|---------|
| `milestones` | Slug (`release/1.1`, `stream/triage`), kind, state, target_date, description |
| `milestone_items` | `(milestone_id, item_kind, item_ref)` link, size, priority, status, acceptance, branch_only, done_commit |
| `milestone_audit` | Append-only log: actor, action, from_state → to_state, reason, timestamp |
| `agent_capacity` | Per-agent WIP (`large_held`, `small_held`, `triage_held`, last pull/release) |

Item kinds are `bug` (validated against `findings`), `requirement` (validated against `requirements`), or `external` (free-form, blockers skipped). The `(milestone_id, item_kind, item_ref)` unique constraint prevents double-attach.

### Blockers

| Field | Type | Description |
|-------|------|-------------|
| `item_id`, `item_type` | text | Blocked entity (e.g. `CB-5` / `finding`) |
| `blocked_by`, `blocked_by_type` | text | Dependency (or null for date/manual triggers) |
| `trigger_type` | text | `entity_resolved`, `date`, `manual` |
| `trigger_at` | text | UTC datetime for date triggers |
| `reason` | text | Human explanation |

### Sweeps

| Table | Purpose |
|-------|---------|
| `codesweeps` | `sweep_id`, name, description, lifecycle, terminal_states, transitions DAG |
| `codesweep_items` | `(sweep_id, item)` unique key; `state`, `recurrence_count`, `first_seen`, `last_seen`, `archived_at` |

## Killer features

### Pattern detection over time

```
$ codebugs categories
category                        total  open  fixed
tz_naive_datetime                  15     3     12
n_plus_one                          8     2      6
missing_input_validation            6     4      2
```

If you keep fixing the same category → time for a lint rule. codebugs turns reactive bug-fixing into proactive prevention.

### Requirements verification

`reqs_verify` catches documentation rot before it ships:

```
$ codebugs reqs-verify
Verified 683 requirements.

12 issue(s) found:
check   sev       id      message
tests   high      FR-350  Test file not found: test_entity_graph.py
status  high      FR-090  Description mentions 'superseded' but status is 'Planned'
status  medium    FR-006  Must-priority requirement implemented without test coverage
ids     medium    --      Numbering gaps (5+): FR-025..FR-029, FR-316..FR-329
```

### Semantic requirements search

Store embeddings (caller generates vectors via any embedding API) and find related requirements semantically:

```python
reqs_embed(req_id="FR-001", embedding=[0.1, 0.2, ...])
reqs_search_similar(query_embedding=[...], limit=5, min_similarity=0.3)
```

Float32 BLOB storage in SQLite; brute-force cosine similarity — fast for thousands of requirements.

### Close-gate enforcement

`milestone_close("release/1.1")` won't let you ship a release with work stranded on a branch:

```
$ codebugs milestone-status release/1.1
release/1.1  (release, state=open)
  target: 2026-06-15 (35 days)

Items: 12 total (3 open/in_progress, 9 done)
  Branch-only: CB-1234
  Blocked: CB-1240
```

When you try to close it:
```
ValueError: cannot close release/1.1: unfinished items (3): CB-1234, CB-1240, CB-1242;
            branch-only items (1): CB-1234@feat/CB-1234;
            items with active blockers (1): CB-1240
            (use force=True with reason to override)
```

Streams (`stream/*`) refuse to close at all — they're permanent buckets.

## Requirements

- Python 3.11+
- No external runtime dependencies beyond `mcp>=1.0.0` (for the server)
- SQLite (bundled with Python)

## Development

```bash
# Run tests
uv run python -m pytest tests/ -v

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/
```

See [CLAUDE.md](CLAUDE.md) for architectural rules and conventions.

## License

MIT
