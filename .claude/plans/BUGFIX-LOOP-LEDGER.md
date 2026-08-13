# Bugfix loop ledger — codebugs

One row per iteration. Read this in Phase 0 so no card is re-picked and the report can state a
net change. Tracker: this repo's own `.codebugs/findings.db`, served by `mcp__codebugs__*`.

| Date | Focus | Cards | Disposition | Merge | Follow-ups |
|---|---|---|---|---|---|
| 2026-08-13 | `codebugs` | CB-16 | **fixed** — meta clobber in `update_finding` / `update_requirement` | `1d85756`, hardening `63d0658` | CB-18 unblocked |
| 2026-08-13 | `codebugs` | CB-4, CB-1, CB-5 | **closed stale** with evidence — all three describe code that has since been refactored | `6e5236c`, `63d0658` (doc corrections) | sweep the remaining arch-debt cards |
| 2026-08-13 | `codebugs` | CB-18, CB-15 | **fixed** — `append_note` unreachable from either surface; unknown argument names silently dropped | `6a1aef2` (`987fc20`, `d6ce8de`) | CB-17 left open by design |
| 2026-08-13 | `codebugs` | CB-17 | **fixed** — severity was write-once, so a re-measured card could not be re-triaged | `dc49160` (`741f428`) | filed CB-19, CB-20; upgraded CB-6 |

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

## 2026-08-13 — CB-17 (severity became mutable)

**The "first question is for the user" call above was wrong, and worth correcting rather than
quietly dropping.** CB-17 was filed by the user, and its body already picks the shape ("Recommend
(a) plus a hook, if an audit trail is wanted"). The only genuinely open sub-question was the
conditional hook, and that answers itself: nothing consumes a severity-change event, so building
one would have been speculative surface. Direction was ratified in the card all along. The lesson
is narrow but real — *check whether the card's author already answered the question before
escalating it back to them.*

**Half the card was already fixed.** Its "unknown kwargs should raise at the MCP boundary" half was
CB-15, closed last iteration. That is now what makes the wire test meaningful: an undeclared
`severity` is REFUSED, so the test fails loudly against the old wrapper instead of passing while
the value is dropped. Two iterations compounding.

**Review placement, deliberately changed.** The standing rule sends a pre-implementation plan to
`adversarial-review-x2`. Here the design question went to Codex *before* the plan (it produced the
"file the case-asymmetry separately" decision), and the full pass went on the **finished diff**
instead — which is where this repo's last real regression was caught. It paid again: Codex found a
regression I had introduced (`except (KeyError, ValueError)` swallows `json.JSONDecodeError`, so a
committed write would report as bad input and exit 1) plus two test gaps that would have let a
broken implementation ship — the MCP test proved the argument was *declared* but never *forwarded*,
and every fixture held one row, so a missing `WHERE` would have passed all 12 tests.

**Mutation testing is now the standard here, not a flourish.** Three separate mutations — delete the
MCP forwarding, delete the CLI forwarding, revert the exception arm — each failed exactly the tests
that should catch them. Verifying the mutation *landed* mattered: an early grep discriminator
returned a misleading count because `query_findings` declares the same signature.

**Live instance of CB-15's failure mode, still running.** An `append_note` call through the session's
MCP server returned a success payload and changed nothing — the server process predates `987fc20`
and `d6ce8de`, so it has neither the parameter nor the strict-argument refusal. **The MCP server must
be restarted before `append_note` or `severity` are usable through it.** Until then, prefer `notes=`
and re-fetch to confirm every write.

**Verify subagent citations, but check your own greps too.** Two of the sweep agent's claims looked
false and were not: `grep -c actor` over `milestones/__init__.py` returns ~27 because the substring
lives inside `conn_factory`, and a `merge.py` window that stopped at 620 missed the parsers at
624–632. The agent was right both times; my discriminators were bad.

**Filed rather than fixed:** CB-19 (severity case-strict, sibling priority case-lenient — split on
Codex's advice because fixing it widens `add_finding`'s accepted inputs) and CB-20 (`ORDER BY
severity` is lexical, so `low` outranks `medium` in the primary read path — this card's own premise
one layer down). CB-6 upgraded from suspected to confirmed: the CLI is *systematically* a subset of
the MCP surface, not just missing blockers, so its real question is a policy one.
