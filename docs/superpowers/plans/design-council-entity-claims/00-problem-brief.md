# Design Council: Entity Claim / Ownership Primitive for codebugs

## Problem Statement

Parallel agents work the codebugs queue. To signal "I am working on this", an agent sets a
finding's status to `in_progress`. There is no coordination: two agents can both read
`status='open'` and both write `in_progress`, and both receive an identical success response.
Neither can tell whether it made the transition or merely repeated one.

The user wants agents to be able to distinguish "I claimed it" from "someone already had it,
back off". The user explicitly rejected a first design that bolted `assigned_agent` /
`claimed_at` columns onto the `findings` table, calling it "прибито гвоздями" (nailed down)
and observing that the problem "looks like the seed of a process / workflow". The standing
bar for this work is: **"лучше либо сделать хорошо, либо никак"** — do it properly or not at all.

Design a claim/ownership mechanism that is a genuine capability of the system, not a
findings-specific patch.

## Verified Context (opened by direct Read this run)

Every claim in this section was read this run at the cited line. Anything **not** in this
section is a hypothesis.

1. **`findings.update_finding` is an unconditional write.** `src/codebugs/findings.py:235-302`.
   It `SELECT`s the row, builds an `UPDATE findings SET ... WHERE id = ?` with no status
   guard, commits, re-`SELECT`s, and returns the row. No rowcount check, no expected-state
   parameter. The response is byte-identical whether the caller changed the status or not.

2. **A correct atomic claim already exists — and nothing calls it.**
   `src/codebugs/milestones/capacity.py:167-215` (`pull_next`) saves `conn.isolation_level`,
   runs `BEGIN IMMEDIATE`, selects candidates, issues
   `UPDATE milestone_items SET status='in_progress', assigned_agent=?, pulled_at=?, updated_at=?
   WHERE id=? AND status='open'`, increments capacity, writes `milestone_audit`, commits, and
   restores isolation in a `finally`. `/home/faxik/w/autosorter/TODO.md:492` records the wiring
   as still open: "**Part 2 OPEN** — `/autonomous-sprint` skill calls `pull_next` /
   `mark_branch_only` / `mark_integrated` / `release_item` so multi-track sprints record state
   in `milestone_audit`." `CLAUDE.md` claims autosorter's `worktree-setup.sh` calls these tools
   by name; no such script was found in `/home/faxik/w/autosorter`. **A correct primitive that
   nobody calls is the failure mode this council must avoid repeating.**

3. **No owner is recorded anywhere for findings today.**
   `~/.claude/skills/fix-latest-codebugs/SKILL.md:92` instructs agents to claim a bug with
   `mcp__codebugs__update(id="CB-1234", status="in_progress", assignee="claude")`. The MCP tool
   signature is `update(finding_id, status, notes, tags, meta_update, reported_at_ref)`
   (`src/codebugs/findings.py:574-581`). Neither `id` nor `assignee` exists. The documented
   claim call cannot succeed as written, and ownership is stored in no column and no meta key.

4. **A declarative cross-entity layer already exists.** `src/codebugs/entities.py` — its own
   docstring: "the single deep module that knows how to map an opaque entity ID (CB-N, FR-N,
   NFR-N) to its kind, table, status, and terminal state… Owns the one sanctioned cross-table
   read over `findings` / `requirements`… Adding a new entity kind is a single entry in
   `ENTITY_KINDS`." `EntityKind` is a frozen dataclass with `name / table / id_pattern /
   terminal / sort_col / result_key / readable_cols`. All reads go through `_read()`, which
   enforces a per-kind `readable_cols` allowlist and a `_SAFE_IDENT` regex on interpolated
   identifiers. **The module is read-only today — it has no write path.**

5. **Requirements have no in-progress status.** `src/codebugs/types.py:40` —
   `REQUIREMENT_STATUSES = ("planned", "partial", "implemented", "verified", "superseded",
   "obsolete")`. `partial` means "partially implemented", not "someone is working on it".
   `FINDING_STATUSES` (types.py:21) does include `in_progress`, added by an existing additive
   migration `findings._migrate_statuses` (findings.py:52-70) that rewrites the CHECK
   constraint. So a per-kind "busy" status is **not** universally available.

6. **Ownership already exists once, in a third table.** `milestone_items` (
   `src/codebugs/milestones/_schema.py:52-77`) carries `assigned_agent`, `pulled_at`,
   `done_at`, `done_commit`, and a partial index `idx_mi_assigned`. Any new ownership store is
   therefore the **second** representation of the same concept, and a decision about
   convergence must be stated even if migration is out of scope.

7. **~~Status sync between layers is nearly absent.~~ REFUTED BY RESEARCH — see correction
   below.** Original text: "The only place a milestone action writes a finding's status is
   `src/codebugs/milestones/triage.py:113-115`." `pull_next` does not set `findings.status`
   and `release_item` does not either — that part stands. The "only place" claim does not.

## CORRECTIONS after Round-1 research (`04-research.md`) — these override the text above

- **C1. `findings.status` has a SECOND automated writer.** `src/codebugs/provenance.py:264-269`
  flips finding status from git commit trailers. Sequence that breaks a naive projection: an
  agent claims CB-1234 (status → `in_progress`), commits `Fixes: CB-1234`, and provenance
  moves the status to `fixed` knowing nothing about claims. **Any design that projects a claim
  into `findings.status` must address this writer by name.** It is not enough to say "claim
  owns the status".
- **C2. Mutual exclusion is settled and is NOT a differentiator.** Executed, 200 trials × 4 OS
  processes: bare guarded `UPDATE` with no explicit transaction, `BEGIN IMMEDIATE` + guarded
  `UPDATE`, `INSERT … ON CONFLICT DO NOTHING`, and an append-only log **all** yield exactly one
  winner. Do not argue for your substrate on correctness grounds — that argument is void.
  Argue on API expressiveness, audit value, query cost, and maintenance.
- **C3. `BEGIN IMMEDIATE` is load-bearing for a different reason than assumed.** Under
  `isolation_level=''` a bare `SELECT` opens no transaction, so a guarded `UPDATE` returns a
  clean `rowcount=0`. But an explicit plain `BEGIN` **does** pin a read snapshot, and the
  upgrade then fails with `SQLITE_BUSY_SNAPSHOT`, which `busy_timeout` cannot rescue.
  Rule for the spec: **never write a plain `BEGIN` in this codebase.** `BEGIN IMMEDIATE` buys
  multi-statement atomicity, not exclusion.
- **C4. The clean loss is accidental.** `db.connect()` (`src/codebugs/db.py:492-503`) sets only
  `journal_mode=WAL`; `busy_timeout=5000` arrives from `sqlite3.connect(timeout=5.0)`'s
  default. At `busy_timeout=0` losers raise `OperationalError: database is locked`
  (~1400 occurrences / 200 trials). **The three-outcome contract is under-specified — a fourth
  "undetermined, retry" outcome is required**, and the design should set `busy_timeout`
  explicitly rather than inherit it.
- **C5. `PRAGMA foreign_keys` is OFF.** A `REFERENCES findings(id)` on any new table is
  decorative, not enforced.
- **C6. `db.connect()` is per MCP request** (`src/codebugs/server.py:13-19`), so in-process
  locking would be useless — cross-connection coordination is genuinely required. The premise
  holds.
- **C7. The premise survives but the stated harm shrinks.** Two agents both writing
  `in_progress` produce a *correct* row; nothing corrupts. This is an **API-expressiveness**
  problem, not a data-integrity one. A proposal that adds a subsystem must clear that lower
  bar, and must justify its weight against it.
- **C8. Q8 is answered: no TTL/lease.** The user's "report, never auto-steal" constraint
  already selects tracker semantics. Building lease machinery and then disabling its only
  behavior is waste. `claimed_at` plus a reader-chosen staleness threshold suffices;
  `renewed_at` falls out of the `already_mine` path for free.
- **C9. Measured cost for the append-only substrate.** "Who holds X" is free with an
  `(entity_id, seq DESC)` index (0.006 ms). **"What does agent-7 hold" is a full window fold
  that no index helps: 82 ms at 40k rows, 752 ms at 500k.** That is a direct hit on success
  criterion 3. The append-only case must therefore be argued on audit value, not performance.
- **C10. Precedents to follow.** Existing two-connection race tests:
  `tests/test_milestones.py:801`, `tests/test_sweep.py:754`.
- **C11. Adoption (Q7) has an unusually strong lever.** `fix-latest-codebugs/SKILL.md:92`
  documents a claim call that *already cannot succeed* (fact 3). Replacing a broken instruction
  is a far stronger adoption story than adding an optional tool that dies unwired like
  `pull_next`.
- **C12. Prior art datum.** Jira carries this exact race as JRASERVER-78379, Severity 2,
  unresolved — evidence both that the race is real in this class of system and that a market
  leader has survived it for years. Weigh accordingly.

## Hypotheses — NOT established (trace before relying on)

- **[hypothesis — never observed]** That the race has actually fired in production. The user's
  own account: agents in conflict "рано или поздно видели конфликт и выбирали лучшее решение"
  (eventually noticed and picked the better solution). Cost observed = duplicated work, not
  data corruption. No trace, log, or incident has been produced. **A design whose value
  proposition requires the race to be frequent is unsupported.**
- **[hypothesis — control-flow not traced]** That direct `update(status="in_progress")` is the
  *only* live claim path. It is the documented one (fact 3) and `pull_next` is unwired (fact 2),
  but no agent transcript was read this run to confirm what agents actually emit.
- **[hypothesis — NOT EXECUTED, must be run]** That in this repo's Python/SQLite,
  `INSERT ... ON CONFLICT(pk) DO NOTHING` yields `cursor.rowcount == 1` on insert and `0` on
  conflict, and that two concurrent connections to a WAL file DB therefore produce exactly one
  winner. This is the correctness core of one candidate substrate and **must be executed, not
  reasoned about**.
- **[hypothesis]** That `sqlite3` connections in this codebase are shared across threads or
  processes in a way that makes in-process locking insufficient. `db.connect()` behavior under
  the MCP server's concurrency model was not traced.

## Constraints

- **Hard, from the user:** claim reports its outcome so a caller can distinguish
  claimed / already-mine / held-by-other. Ownership must be recorded. Expired claims are
  **reported, never auto-stolen** — stealing requires an explicit opt-in flag. Claim
  **projects into the entity's status** so existing `query(status="in_progress")` callers and
  reports keep working. First delivery covers **findings and requirements**, to prove the
  generalization rather than assert it.
- **Hard, from the user:** the mechanism must not be nailed to `findings`. It should read as a
  capability of the entity layer.
- **Architectural, from `CLAUDE.md`:** new domain modules self-register (`register_schema`,
  `register_tool_provider`, `register_cli_provider` at module level), add their import to
  `db._ensure_modules_loaded()`, add a mode slug to `SERVER_NAMES` and the `--mode` allowlist.
  Domain modules must not import each other's private functions. `db.py` must not import domain
  modules at top level. All schema changes additive or via explicit migration. Parameterized
  queries only. Python 3.11+, ruff, line length 100, keyword-only args after `conn`, type hints
  on public signatures. MCP tools return `dict[str, Any]`. Tests in `tests/test_<module>.py`,
  no shared `conftest.py`.
- **Storage reality:** single SQLite DB, WAL mode, "no concurrent-write coordination beyond
  SQLite's built-in locking" (CLAUDE.md).
- **Adoption:** the design must state how it gets *called*. Fact 2 is the precedent for a
  correct primitive dying unwired.

## Success Criteria

1. Two concurrent claimants on the same entity: exactly one is told it won. Proven by an
   executed test against a file-backed DB with independent connections — not by argument.
2. A retrying claimant (same agent, repeated call after a timeout) is told it still holds the
   entity, and is never told it lost.
3. Ownership is queryable: "who holds CB-1234", "what does agent-7 hold".
4. Adding a third entity kind requires no new ownership code — only a declaration.
5. Requirements are covered despite having no busy status (fact 5).
6. Existing `query(status="in_progress")` consumers keep working.
7. Stale ownership (claimant died) is visible and recoverable without a background process.
8. The pre-existing `milestone_items` ownership (fact 6) is either subsumed or has a stated
   convergence plan.
9. A named, concrete call site is changed so the primitive is actually used.

## Open Questions

- **Q1.** Where does ownership live: columns on each entity table, one dedicated mutable
  ownership table, an append-only event log, or encoded in existing status/meta?
- **Q2.** `entities.py` is read-only by design and owns the only sanctioned cross-table read
  plus the identifier allowlist. Does the status projection make it read/write, or does the
  write live elsewhere without duplicating identifier interpolation?
- **Q3.** What is the correct primitive for mutual exclusion here — a UNIQUE/PK constraint,
  `BEGIN IMMEDIATE` + guarded UPDATE (the `pull_next` precedent), or something else? Answer
  must be backed by an executed probe, not by reading docs.
- **Q4.** Requirements have no busy status. Options: skip projection for that kind, add
  `in_progress` to `REQUIREMENT_STATUSES` by additive migration (precedent: findings.py:52),
  or drop projection as a concept. Which, and what does it imply for kind #3?
- **Q5.** How does release restore state without destroying work — if the holder already set
  `fixed`, release must not resurrect the entity to `open`. Where is the pre-claim state kept?
- **Q6.** How much of the "process / workflow" the user sensed should be built now? Is a
  declared transition layer (guarded transitions, who-may-transition rules) justified by
  present demand, or is it speculative generality? Argue both sides with evidence from the
  repo.
- **Q7.** What is the adoption mechanism that prevents this from becoming a second unwired
  `pull_next`? Name the exact file and change.
- **Q8.** Is a lease/TTL concept needed at all given no auto-steal, or does `claimed_at` plus
  reporting suffice?

## Team Composition

- **Researcher** (sonnet legwork, opus synthesis) — Q3 must be *executed*; also prior art on
  claim/lease vs assignment models, and SQLite concurrency semantics under this repo's venv.
- **Architect A** — *minimize-change / in-place*. **Forbidden lever: may NOT create any new
  table.** Must express ownership using existing tables, columns, migrations, and the entity
  descriptor.
- **Architect B** — *dedicated-store / capability-first*. **Forbidden lever: may NOT add or
  alter any column on `findings`, `requirements`, or `milestone_items`.** Ownership must live
  in its own new structure.
- **Architect C** — *derived-state / process-first*. **Forbidden lever: may NOT store current
  ownership as directly-mutable state.** Ownership must be a projection over an append-only
  record, which is also the substrate for the "workflow" the user sensed.
- **Adversary (Opus)** and **Adversary (Codex / gpt-5.6-sol)** in parallel — heterogeneous
  attack per the user's standing rule that a pre-implementation spec gets a cross-model pass.
- **Judge** — inherits session model.

Rationale for the three levers: they force three genuinely different substrates — in-place
mutable columns, a separate mutable lock row, and an append-only log — for which no single
solution is simultaneously optimal. Each lever constrains the *whole* problem (ownership
storage), not one sub-task.
