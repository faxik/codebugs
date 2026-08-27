# codebugs

**A code-finding, requirements, and release tracker for AI assistants — one where a finding has an identity.** SQLite-backed, exposed via an MCP server and a CLI.

Most trackers treat every report as a new row, so the second agent to notice the same bug files it again, and the queue fills with copies of one defect. codebugs treats a finding as **a defect**, and each report of it as **one observation of that defect**:

```
$ codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42" --new-category
Added: CB-1
$ codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42"
Bumped: CB-1 (occurrence 2)
$ codebugs update CB-1 --status fixed
Updated: CB-1 (status=fixed, severity=high)
$ codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42"
Reopened as regression: CB-1 (occurrence 3)
```

One card, three observations, and a regression recorded on the row it belongs to. Filing an observation again is normal and useful, not noise.

## What makes this different

Deduplication is the point, not a side effect — and the rest of the design falls out of that one decision.

- **Filing the same finding twice does not create two cards.** The second report bumps the first: its occurrence count goes up, and its severity rises if this sighting was worse than the last. Severity only ever escalates under observation, so a card filed `low` and re-seen `critical` stops hiding from a `--severity critical` query. Lowering it back is a deliberate `update`, never an accident of the last report.
- **A card that was fixed and comes back is a regression, not a duplicate.** Re-filing reopens the same card and records the regression on it, so one defect's whole history stays on one row.
- **A decision stays decided.** Re-filing something already dismissed as `wont_fix` or `not_a_bug` does not quietly reopen the argument. It files a new card pointing back at the dismissal, so the recurrence is visible and the original ruling survives.
- **The place in the code outlives the edit.** When a report names a line range, the location is anchored at filing time from git. After the file is edited around it, `anchor_resolve` reports where that code went — `moved`, with the new line numbers — instead of pointing at whatever now occupies the old line.
- **Parallel agents don't collide.** `claims_claim` gives one agent a card and refuses the second, naming who holds it and from which repo, so two agents cannot silently fix the same thing. Closing a card releases the claim in the same transaction.
- **Findings can be related and grouped.** Cards link to each other, and a similarity report proposes families of near-duplicate findings as a dry run you inspect before merging anything.
- **It tells you when it could not look.** A tracker it cannot read, a file it cannot stat, an anchor whose card never named a code span — each of these comes back as *undetermined*, with the reason, rather than as a confident wrong answer. `codebugs where` will tell you which tracker a command is actually bound to and which channel decided that, because a binding you cannot see is a binding you cannot debug.

Underneath that, it is durable memory across sessions: findings survive the conversation that produced them, requirements are checked against the code that claims to implement them, blocked work resurfaces when its dependency resolves, and a release knows what is still stranded on a branch.

codebugs is one SQLite database (`.codebugs/findings.db`). Modules are self-registering, and the running server reports its own tool catalogue — the module table below is the set of them.

## Install

```bash
# Global install (recommended)
pipx install codebugs

# Or with pip/uv
pip install codebugs
```

## Setup

### Create the tracker

Run this once per project, in the project root:

```bash
codebugs init
```

This creates `.codebugs/findings.db`. **`init` is the only command that creates the `.codebugs/` directory** — every other command discovers an existing one by walking up from the current directory (unless you point it somewhere explicitly, see below), and refuses with an actionable error if there is none. That refusal is deliberate: silently creating an empty database is how findings go missing.

There is one deliberate exception, and it is worth stating precisely because it looks like the rule being broken. **The upward walk treats an existing `.codebugs/` directory as the opt-in**, so if that directory is there but holds no `findings.db`, the next command creates the database inside it rather than refusing. The common way to end up in that state is an interrupted `init` — the directory is created before the database — and self-healing on the next command is more useful there than demanding a second `init`.

**A tracker you name explicitly is held to the stricter rule.** `--repo`, `--tracker-root` and `$CODEBUGS_ROOT` must resolve to a directory that actually contains `findings.db`; a `.codebugs/` without one is refused, and the message names which channel pointed there. The difference is about evidence: standing inside a directory says something about where you are, while a named path is an assertion that can be mistyped, or exported into a shell days ago and inherited by an unrelated process. That is exactly where a silent empty tracker does the most damage.

Two consequences worth knowing:

- **Run `init` at the project root, not in a subdirectory.** Discovery binds to the *nearest* `.codebugs/`, so a nested tracker hides the project's real one from everything beneath it. `init` refuses to do this unless you pass `--force`.
- **Git worktrees share the main repo's tracker.** A worktree's `.git` is a file pointing at the main repo, which discovery follows — so findings filed from a worktree land in the project's database, not in a throwaway that dies with the worktree. `init` refuses to run inside a worktree for the same reason; run it in the main checkout.

  Two layouts are the exception: if the main repo is **bare** or was created with **`--separate-git-dir`**, git records no path back to a main checkout — its own `git worktree list` reports the git directory instead. Discovery usually refuses those with an explicit error rather than guessing. There is one case it cannot detect: a `--separate-git-dir` repo whose git directory is itself named `.git` looks exactly like a normal checkout, so discovery binds to the directory holding the git dir instead of the real checkout, silently. Nothing local can distinguish the two — git reports that directory as a valid work tree as well — so the remedy is to state the root explicitly (below). Run `init` in the main checkout before creating worktrees.

### Pointing codebugs at a specific tracker

Discovery is a heuristic, so it has an override:

```bash
codebugs --tracker-root /path/to/project query   # this invocation only
export CODEBUGS_ROOT=/path/to/project            # this shell and anything it spawns
```

Resolution order, most specific first: a command's own explicit path argument (`--repo`, where a command has one) → `--tracker-root` → `CODEBUGS_ROOT` → walking up from the current directory. A per-command argument outranks a declaration because it names one operation's target, while a declaration is process-wide.

```bash
codebugs where     # show the current binding and which channel decided it
```

`where` is a diagnostic, not a precedence level: it prints the resolved root, the database path, and the channel — the fastest way to check that a command is about to read the tracker you think it is.

Two things worth knowing. A declared root that contains no `.codebugs/` is a **hard error**, never a new tracker: the value may be a stale export inherited from another shell, and silently creating an empty database there is how findings go missing. And `init` ignores the declaration — it always creates where you are standing — but warns if the declaration points somewhere else, since otherwise it would report success for a tracker no other command will read.

`CODEBUGS_ROOT` is inherited by every subprocess, so export it only when you mean "this shell works on that project". For one-off use across projects, prefer `--tracker-root`.

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

The database lives at `.codebugs/findings.db`, discovered by walking up from the server's working directory — each project gets its own. Run `codebugs init` in the project first (see above), or every tool call will fail with "no `.codebugs/` found".

The server connects lazily, per tool call, so it starts successfully even when no tracker is reachable. At startup it writes one line to **stderr** — which MCP clients log — if discovery failed, or if a root was declared rather than discovered; on the ordinary path it says nothing. It never refuses to start: a project directory that appears later must still work.

To pin one server to one tracker instead of deriving it from the working directory:

```json
{
  "mcpServers": {
    "codebugs": {
      "command": "codebugs-mcp",
      "args": ["--tracker-root", "/path/to/project"]
    }
  }
}
```

Only do this when you want that server bound to a single project — the default cwd-derived behavior is what lets one registration serve many. Add `.codebugs/` to your `.gitignore`.

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

Any module name from [the module table below](#the-modules) is a valid mode, and `all` — the default — loads everything. The CLI takes the same flag: `codebugs --mode findings summary`.

One asymmetry is worth knowing before you rely on it: `usage` is a **CLI-only** mode. It registers a command but no MCP tools, so `codebugs --mode usage usage` works while an MCP server started with `--mode usage` would have nothing to offer.

### Other MCP Clients

Any MCP-compatible client can connect to `codebugs-mcp` via stdio transport.

## The modules

Every name in this table is a valid `--mode` value.

| Module | Domain | Headline tools |
|--------|--------|----------------|
| **findings** | Bugs, tech-debt, review findings | `summary`, `add`, `query`, `categories` |
| **reqs** | Functional requirements (FR-N) | `reqs_summary`, `reqs_add`, `reqs_verify`, `reqs_search_similar` |
| **blockers** | "X is blocked by Y" dependency graph | `blockers_add`, `blockers_check` |
| **sweep** | Batch iteration with state machines | `codesweep_create`, `codesweep_next`, `codesweep_mark` |
| **bench** | Performance benchmark snapshots | `codebench_import`, `codebench_query` |
| **merge** | Parallel-agent merge serialization | `codemerge_start`, `codemerge_claim` |
| **milestones** | Releases, streams, capacity-aware pull | `pull_next`, `milestone_status`, `milestone_close` |
| **provenance** | Staleness vs git history, commit trailers | `staleness_check` |
| **claims** | Which agent holds a finding or requirement | `claims_claim`, `claims_release`, `claims_who_holds` |
| **loc** | Where in the code a finding is, across edits | `anchor_resolve`, `anchor_recapture` |
| **similarity** | Near-duplicate findings, as a dry run | `similarity_check`, `similarity_report` |
| **relations** | Typed, retractable links between findings | `relations_relate`, `relations_query` |
| **grouping** | Reads the axes the tracker stores but never exposed | `grouping_citations`, `grouping_tags`, `grouping_filing` |
| **usage** | Tool-call counters (**CLI only** — no MCP tools) | — |

Modules are self-registering — adding a new one is local to its own file. See [`docs/superpowers/specs/`](docs/superpowers/specs/) for the architecture history.

**Not every MCP tool has a CLI verb.** The milestones module is where the gap is widest: `milestone_create`, `milestone_update`, `milestone_add_item`, `milestone_move_item`, `milestone_set_status`, `milestone_defer`, `milestone_close`, `triage_dismiss`, `triage_promote`, `pull_next` and `release_item` are reachable through MCP only. From a terminal you can inspect a release, but you cannot create one or pull work from it.

## Quick tour

### Findings — log it, never re-discover it

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `summary` | Dashboard overview — **start here** for orientation |
| `add` | File an observation. Creates, bumps, reopens or refiles — read `dedup_action` to see which |
| `batch_add` | File several observations at once |
| `update` | Change status, severity, notes, tags or metadata (`append_note` adds, `notes` replaces) |
| `query` | Search/filter with pagination and group-by |
| `get` | Fetch one finding by id, with its full body and occurrence history |
| `recent` | Findings touched at or after a date — the one call for "what closed since" |
| `stats` | Cross-tabulated counts (severity x category/file/status) |
| `categories` | List existing categories — **call before `add`** for consistency |
| `categories_normalize` | Fold twin category spellings together. **Dry run by default** |

`staleness_check` lives in the **provenance** module rather than here, and `anchor_resolve` in **loc**; both are listed in [the module table](#the-modules). The tools behind the opening section — anchors, similarity, relations and grouping — have their own entry under [Identity, location and grouping](#identity-location-and-grouping) below.

**CLI:**

```bash
codebugs add -s high -c n_plus_one -f src/api.py -d "Query in loop at line 42" --new-category
codebugs summary
codebugs query --status open --severity critical
codebugs update CB-1 --status fixed --append-note "Fixed in PR #42"
codebugs categories
```

**`--new-category` is needed the first time a category is used, and only then.** A category the tracker has never seen is refused, naming the closest existing spellings, because the common way a category set fragments is a typo — `n-plus-one` beside `n_plus_one` — and a fragmented category set is what makes `categories` stop revealing patterns. Once `n_plus_one` exists, later findings use it without the flag. Spelling is normalized on the way in, so hyphens, spaces and case do not mint twins.

`--append-note` adds to a finding's notes; `--notes` **replaces** them wholesale. Prefer `--append-note` when recording investigation history — `--notes` will discard whatever was there, which is usually not what you want on a finding others have been working.

When a new finding is added, the **milestones auto-router** automatically attaches it to `stream/triage` (or `stream/security` when `severity=critical` and `category` starts with `security:`). The finding and its triage entry land in the same transaction.

### Requirements — verify what shipped, surface contradictions

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `reqs_summary` | Requirements dashboard — **start here** |
| `reqs_add` | Add a requirement (FR-001, priority, status, test coverage) |
| `reqs_update` | Change status, description, priority, test coverage |
| `reqs_query` | Search/filter by status, priority, section, free text |
| `reqs_get` | Fetch a single requirement by ID with full body |
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
| `codemerge_claims` | List all files a session has claimed, in claim order |
| `codemerge_sessions` | List merge sessions with claim counts |
| `codemerge_status` | Dashboard: session counts by status, total active claims |
| `codemerge_abandon` | Close a session for good, so its files stop blocking everyone else |

### Identity, location and grouping

These four modules are what the opening section is about, and none of them had an entry here before.

| Tool | Purpose |
|------|---------|
| `anchor_resolve` | Where a finding's code is **now** — `current`, `moved`, `moved_file`, `lost`, `ambiguous`, or `unknown` with a reason |
| `anchor_recapture` | Rebuild stored anchors from the git object store. **Dry run by default** |
| `similarity_check` | Preview what the file-time annotator would stamp for an observation |
| `similarity_report` | Similarity families as auditable evidence — a scrub you read, never a merge it performs |
| `relations_relate` | Assert a typed relation (`duplicate_of`, `follow_up_of`, `split_from`, ...) between two findings |
| `relations_query` | List relations touching a finding, in both directions |
| `relations_unrelate` | Retract a relation. Tombstones it — the row and its history remain |
| `grouping_citations` | Connected components of the hand-written CB-id reference graph |
| `grouping_tags` | Tag pivots: counts, co-occurrence, near-duplicate taxonomy strings |
| `grouping_filing` | Split lineages and shared filing events |

**An anchor needs the filing to name a code span.** The location is captured from `meta.line` / `meta.lines` (and a few equivalent spellings) at the moment the finding is filed. A report that names only a file has nothing to anchor, and `anchor_resolve` says so — `unknown`, reason `no_grammar` — instead of guessing a line. Most findings in practice name no span, so this is the ordinary case, not an error:

```
$ codebugs anchor-resolve --finding-id CB-2 --repo . --json
      "anchor": {
        "status": "moved",
        "path": "src_api.py",
        "line": 25,
        "end": 27,
        "channel": "git",
        "survived": "3/3",
```

That card was filed against lines 5–7. Twenty lines were then inserted above it. The stored line numbers are stale; the anchor is not.

## How It Works

### The Problem

AI code review sessions produce findings that get lost. Multiple agents working in parallel double-claim work. Requirements files drift. Releases lose track of what's in them.

### The Solution

codebugs stores everything in one local SQLite database. AI assistants write findings, requirements, and milestone items as they discover them, then query the database in future sessions for instant context recovery. Concurrent agents coordinate via the same database — no race conditions, atomic claims.

**Token savings**: a `summary` call returns one small structured overview — counts by severity, the top categories, the hottest files — instead of the file reading and conversation replay it would otherwise take to re-establish the same picture.

### Typical Workflows

**Code review loop.** This is the same loop the MCP server tells every client that connects, so the two cannot drift apart:

1. File the observation with `add` (or `batch_add` for several at once). Call `categories` first when you are unsure of the naming.
2. **Read what came back before doing anything else.** `dedup_action` says whether this created a new card, bumped or reopened an existing one, or refiled one already dismissed. `attention` is the server's own flag when your observation raised the card's severity or diverged from its stored category — a card you thought you were filing fresh but which the tracker already knew about, differently.
3. The code location is anchored at file time when the observation names one; `anchor_resolve` reports whether that anchor still points at live code.
4. Close the card with `update CB-N --status fixed` once it is actually fixed.

Working alongside other agents on the same tracker? Claim the card with `claims_claim` before starting and release it with `claims_release` when done, or two agents can end up fixing the same thing. Closing the card releases the claim on its own.

Each `add` also auto-routes the finding to `stream/triage`, and over time `categories` reveals systemic issues — "12 `tz_naive_datetime` fixed across 9 files → time for a lint rule."

Requirements (`reqs_add`, `reqs_query`, ...) are a separate, authored entity next to findings, and they have **no** deduplication. Do not file a requirement through `add`, or a defect through `reqs_add`.

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
category                  total  open  fixed
------------------------  -----  ----  -----
tz_naive_datetime         15     3     12
n_plus_one                8      2     6
missing_input_validation  6      4     2
```

If you keep fixing the same category → time for a lint rule. codebugs turns reactive bug-fixing into proactive prevention.

This is the view the category gate above protects. Had half those findings been filed as `tz_naive_dt`, the table would show two rows of 8 and 7 instead of one row of 15, and there would be no pattern to see. Normalization handles the punctuation-and-case twins on its own; the gate is what catches a genuinely different name for the same thing.

### Requirements verification

`reqs_verify` catches documentation rot before it ships:

```
$ codebugs reqs-verify
Verified 3 requirements.

4 issue(s) found:

check   sev     id      message
------  ------  ------  -----------------------------------------------------------
ids     medium  --      Numbering gaps (5+): FR-007..FR-089, FR-091..FR-349
status  medium  FR-006  Must-priority requirement implemented without test coverage
status  high    FR-090  Description mentions 'superseded' but status is 'planned'
status  medium  FR-350  Must-priority requirement implemented without test coverage
```

### Semantic requirements search

Store embeddings (caller generates vectors via any embedding API) and find related requirements semantically:

```python
reqs_embed(req_id="FR-001", embedding=[0.1, 0.2, ...])
reqs_search_similar(query_embedding=[...], limit=5, min_similarity=0.3)
```

Float32 BLOB storage in SQLite; brute-force cosine similarity — fast for thousands of requirements.

### Close-gate enforcement

`milestone_close` won't let you ship a release with work stranded on a branch. First, what the release looks like:

```
$ codebugs milestone-status release/1.1
release/1.1  (release, state=open)
  target: 2026-09-15 (19 days)
  First post-1.0 feature release. Target date set later.

Items: 3 total (3 open/in_progress, 0 done)

  By status:
    open              3
  By size:
    small             3

  Branch-only: CB-1
  Blocked: CB-2
```

Then closing it. **`milestone_close` is one of the milestone tools with no CLI verb** — this is the error the MCP tool returns, raised as a `ValueError` from the domain function, on a single line:

```
cannot close release/1.1: unfinished items (3): CB-1, CB-2, CB-3; branch-only items (1): CB-1@feat/CB-1; items with active blockers (1): CB-2  (use force=True with reason to override)
```

`force=True` with a logged reason overrides that. Streams do not have an override — `milestone_close(id="stream/triage", force=True, reason="x")` still refuses with `streams cannot be closed (milestone=stream/triage)`, because they are permanent buckets rather than things that ship.

## Requirements

- Python 3.11+
- One runtime dependency: `mcp>=2.0.0,<3`, for the server. The 2.0 floor is not cosmetic — `server.py` uses `MCPServer`, the class that replaced `FastMCP` in the 2.0 SDK, so an older `mcp` will not start.
- SQLite (bundled with Python)

## Development

```bash
# Run tests
uv run --extra dev python -m pytest tests/ -q

# Lint — this is the gate
uv run --extra dev ruff check src/ tests/
```

`pytest` and `ruff` live in the `dev` extra, which `uv run` does not install by default, so `--extra dev` is not optional in a fresh clone.

**`ruff format` is deliberately not run over this tree.** Much of the existing code does not conform to it, so `ruff format src/ tests/` would rewrite most of the repository in one commit. `ruff check` is the gate; formatting is left alone on purpose.

See [CLAUDE.md](CLAUDE.md) for architectural rules and conventions.

## License

MIT
