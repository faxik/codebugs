# Bugfix loop ledger — codebugs

One row per iteration. Read this in Phase 0 so no card is re-picked and the report can state a
net change. Tracker: this repo's own `.codebugs/findings.db`, served by `mcp__codebugs__*`.

| Date | Focus | Cards | Disposition | Merge | Follow-ups |
|---|---|---|---|---|---|
| 2026-08-13 | `codebugs` | CB-16 | **fixed** — meta clobber in `update_finding` / `update_requirement` | `1d85756` on `feature/entity-claims` (fix commit `82f693f`) | CB-18 now unblocked; CB-1 suspected stale |

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
