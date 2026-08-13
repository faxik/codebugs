# Bugfix loop ledger — codebugs

One row per iteration. Read this in Phase 0 so no card is re-picked and the report can state a
net change. Tracker: this repo's own `.codebugs/findings.db`, served by `mcp__codebugs__*`.

| Date | Focus | Cards | Disposition | Merge | Follow-ups |
|---|---|---|---|---|---|
| 2026-08-13 | `codebugs` | CB-16 | **fixed** — meta clobber in `update_finding` / `update_requirement` | `1d85756`, hardening `63d0658` | CB-18 unblocked |
| 2026-08-13 | `codebugs` | CB-4, CB-1, CB-5 | **closed stale** with evidence — all three describe code that has since been refactored | `6e5236c`, `63d0658` (doc corrections) | sweep the remaining arch-debt cards |
| 2026-08-13 | `codebugs` | CB-18, CB-15 | **fixed** — `append_note` unreachable from either surface; unknown argument names silently dropped | `6a1aef2` (`987fc20`, `d6ce8de`) | CB-17 left open by design |

## 2026-08-13 — CB-16

**Picked** over CB-15 (high, older) on blast radius: CB-16 silently destroys the investigation
record, which is the tracker's entire value. Codex/Sol agreed on the shortlist ranking.

**Not clustered.** CB-18 and CB-15 (expose `append_note` over MCP/CLI) were rejected as cluster
members — "wrapper missing a parameter" is a different transformation in a different layer from
"build meta once in the domain layer", so it passes none of the four clustering predicates.
CB-18's own text asks for the clobber to be fixed first, which is an ordering argument, not
atomicity.

**Reproduced before planning**, including the reqs.py twin the card had only read.

**Cross-model review of the diff (Codex/gpt-5.6-sol) found a regression I had introduced** —
hoisting the `json.loads` made every update depend on stored meta parsing, so a status-only
update on a malformed legacy row aborted before its own SQL. Fixed by parsing lazily; pinned by
a test that fails against the eager version. Codex also caught a vacuous `updated_at >=`
assertion. Both fixed before landing.

**Sibling sweep:** grep of every built `SET` clause in `src/` — only instance. Rule added to
`CLAUDE.md`.

**Left open, deliberately:** CB-15, CB-18 (MCP/CLI `append_note` surface), CB-17 (severity
immutable). CB-1 (March, "separate MCP servers") is a **stale-card candidate** — `server.py` and
`cli.py` already filter by `--mode`, so it may already be delivered; needs verification, not a fix.

**Note for the next iteration:** the long-running MCP server process holds pre-fix code in
memory until it restarts, so `update` calls made through it can still clobber. Use one
meta-writing argument per call until the server is restarted.

### Post-landing adversarial review (Codex / gpt-5.6-sol, three lenses)

Run after CB-16 landed, at the user's request. Lenses: code correctness, test quality, and
verification of the author's own report claims. Hardening merged as `63d0658`.

- **Code: clean.** No correctness regression in the shipped fix. Ordering, hook firing, commit
  ordering and the no-op path all unchanged; concurrency exposure identical to before.
- **Tests: the worst finding.** The structural guard asserted on `set_trace_callback` output,
  which expands bound parameters — so it produced false failures (a notes value containing the
  token `meta =`) and false passes (quoted identifiers). Both suites now assert on the SQL
  *template* via a `RecordingConnection`. The requirements twin was also materially thinner than
  the findings one and has been brought level.
- **Claims: two were overstated.** The sibling sweep did not enumerate `ON CONFLICT DO UPDATE SET`
  upserts (checked since — `claims.py` and `sweep.py`, both safe, conclusion unchanged). And the
  CB-4 closure claimed provenance "owns `head_sha`" when that function is a dead delegation.
- **The review found a third stale card, CB-5**, by checking `CLAUDE.md` against the code rather
  than the code against itself.

**Lesson worth keeping:** three of this tracker's April-2026 architecture-debt cards (CB-4, CB-1,
CB-5) described a codebase that no longer exists, and `CLAUDE.md`'s debt section had drifted with
them — each doc entry was repeating its card's false premise. Verify that section against the code
as a batch rather than discovering them one card at a time.

## 2026-08-13 — CB-18 + CB-15 (the update surface)

One tree, two commits, because CB-18 is exactly and only CB-15's first defect — dedup rather than
clustering. Plan: `.claude/plans/CB-18-CB-15-update-surface.md`.

- **CB-18 / CB-15(1)** — `append_note` existed in the domain layer but neither surface declared it,
  so every agent note edit took the destructive replace. Now plumbed to MCP and CLI; `notes` says
  REPLACES.
- **CB-15(2)** — an unknown argument *name* was dropped and the call reported success, while an
  unknown *value* raised. Cause: the SDK's argument model uses pydantic's default `extra="ignore"`.
  Fixed server-wide by a middleware refusing undeclared arguments.

**I nearly got this wrong.** My plan was to ship (1) and defer (2) as "needs SDK internals". The
bounded Codex pass rejected the split and pointed at the public `MCPServer.middleware` API I had
missed — deferring would have been the cheap-substitute failure the workflow exists to prevent. The
lesson generalises: *"the correct fix needs private API"* is a claim to verify against the library,
not a reason to shrink scope.

**Negative result worth not re-deriving:** `additionalProperties: false` does nothing here. The
server never validates arguments against the JSON Schema — confirmed by injecting it into a live
tool and watching the call still succeed.

**CB-17 deliberately excluded** despite Codex judging it cluster-eligible. It needs a core parameter
plus validation and carries real product questions (should retriage record actor/reason/history?).
The hostage test decided it: if CB-17 stalled on that decision, two finished edits would wait behind
it. It is now the only substantive card left — and its first question is for the user, not an agent.
