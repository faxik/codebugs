# Bugfix loop ledger — codebugs

One row per iteration. Read this in Phase 0 so no card is re-picked and the report can state a
net change. Tracker: this repo's own `.codebugs/findings.db`, served by `mcp__codebugs__*`.

| Date | Focus | Cards | Disposition | Merge | Follow-ups |
|---|---|---|---|---|---|
| 2026-08-13 | `codebugs` | CB-16 | **fixed** — meta clobber in `update_finding` / `update_requirement` | `1d85756`, hardening `63d0658` | CB-18 unblocked |
| 2026-08-13 | `codebugs` | CB-4, CB-1, CB-5 | **closed stale** with evidence — all three describe code that has since been refactored | `6e5236c`, `63d0658` (doc corrections) | sweep the remaining arch-debt cards |
| 2026-08-13 | `codebugs` | CB-18, CB-15 | **fixed** — `append_note` unreachable from either surface; unknown argument names silently dropped | `6a1aef2` (`987fc20`, `d6ce8de`) | CB-17 left open by design |
| 2026-08-13 | `codebugs` | CB-17 | **fixed** — severity was write-once, so a re-measured card could not be re-triaged | `dc49160` (`741f428`) | filed CB-19, CB-20; upgraded CB-6 |
| 2026-08-13 | `codebugs` | CB-17 (post-hoc) | **simplify pass** that should have run before landing — 3 redundant tests, a duplicated fixture, and two false doc claims | `1892b80` | filed CB-21 |
| 2026-08-13 | `codebugs` | CB-20 | **fixed** — vocabulary columns ordered alphabetically, so `low` outranked `medium` and `could` outranked `must` | `f9a682e` (`2ac27c4`) | filed CB-22 |
| 2026-08-13 | `codebugs` | CB-22 | **fixed** — an allowlist that never validated its own members; sibling in `capacity.py` silently lost an increment | `e1900d4` (`4db5a07`, `6996b8e`, `d1fea09`) | CB-21 still needs a user decision |
| 2026-08-13 | `codebugs` | CB-19 | **fixed** — severity had no resolver; the sweep found query filters comparing raw text against canonical columns in both entities | `071a630` (`f1f4bd0`, `d1a31f5`, `3f91704`) | queue now blocked: every remaining card needs a product decision |

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

## 2026-08-13 — the CB-17 simplify pass, run after landing

**I skipped `/simplify` before landing CB-17.** Phase 8 mandates it; I went from verification
straight to commit. The user asked. Running it found five things worth fixing, so the skip cost
something real — record it as a process failure, not a footnote.

The tests were the cheap half (a fixture duplicated verbatim across two classes, two tests
subsumed by a loop, one testing the same guard twice). **The docs were the expensive half:** the
CLAUDE.md rule I had just written, "a column settable at INSERT should be settable at UPDATE",
**shipped false on the tree it was written for** — `description`/`category`/`file` are immutable
on findings while requirements can rewrite `description`. A Codex doc audit then found my
*correction* was also incomplete: `source` is INSERT-settable on **both** entities and in neither
update contract.

Three passes over one function, three different missing columns. That is the argument for CB-21:
enumeration by inspection does not converge, so the answer is a parity test, not more careful
reading.

**Codex reliability, worth knowing:** the broad five-part review prompt timed out at 600s, and two
`codex exec` CLI runs exited 0 after a single planning line (it reads stdin as extra prompt input,
which `nohup` leaves dangling; `reasoning effort: ultra` compounds it). A short single-question MCP
prompt worked first time, as every focused prompt has. **Keep cross-model prompts narrow.**

## 2026-08-13 — CB-20 (queues ordered alphabetically)

**The sibling sweep is what made this iteration worth more than its card.** The card named two
sites; sweeping every `ORDER BY` in the package found a third — `blockers.query_deferred_entities`
— and that is where the worse defect lived: `PRIORITIES` sorts lexically to `could, must, should`,
so the deferred queue put the **highest** priority **last**. The card's own "RELATED: reqs is also
lexical" lead was, meanwhile, **wrong** — both `get_stats` functions pre-seed their output dict
with the vocabulary, so row order never reaches the caller. Verify a card's leads before carrying
them; two of this one's three were false.

**Fixed at the registry, not the call site.** `EntityKind` already declared `sort_col`; its
precedence now sits beside it as `sort_vocabulary`, behind `order_by()`. The field is **required
but nullable** — a kind may legitimately sort by an id, but a *default* would let a future kind
with a TEXT vocabulary column inherit "no precedence" silently, which is the defect itself. That
choice is why adding it broke a claims test, and breaking that test was the design working.

**The mutation that mattered.** Codex, asked only for the fix shape, warned that the CASE
placeholders sit between the WHERE fragment and LIMIT/OFFSET, so their params must be spliced
there rather than prepended. Prepending fails **exactly one** test — the filtered-query one —
while all five unfiltered ordering tests pass. Without that single test the bug ships. A test
whose absence is invisible is the kind worth writing down.

**Also worth not re-deriving:** `ruff format` on a file you only appended to reformats the whole
file. `test_blockers.py` came back with 142 changed lines for a 50-line addition. Restore the file
and re-append rather than shipping the noise — `ruff check` is the gate, not `ruff format --check`,
and the repo has pre-existing drift in 25 files.

## 2026-08-13 — CB-22 (the allowlist that never checked itself)

**Picked against my own ranking.** CB-21 (medium) led on severity and blast radius; CB-22 (low) was
second. The bounded Codex pass over the shortlist put CB-22 first and it was right: CB-21's correct
fix includes making `description`/`file` mutable on findings, which widens the tracker's API — a
user decision, and Phase 3's explicit always-ask condition. CB-22 has none, and it lays the
fail-closed-at-construction pattern CB-21's parity gate wants. Sequencing, not substitution.

**The card was wrong twice, and only running it showed that.** It claimed `sort_col` was unguarded
on the vocabulary branch (both branches were covered) and that `_SAFE_IDENT` had no callers (its
grep was scoped to `src/`; a test used it). Meanwhile the defect it *understated* was the real one:
`readable_cols` was not merely a prospective syntax hazard — a member of `(SELECT meta FROM
findings)` passed the membership check and `field()` returned the `meta` column. **An allowlist
membership check guards the caller's argument, never the allowlist's own contents.** Two
obligations; only the first is visible at the query site. That is now a CLAUDE.md rule.

**The fix shipped with the defect it was fixing, until review caught it.** I lifted the pattern
`^[A-Za-z_][A-Za-z0-9_]*$` unchanged out of `_SAFE_IDENT` and into the new gate. `$` also matches
just before a trailing newline, so `is_sql_identifier("id\n")` was True and
`EntityKind(table="findings\n")` constructed cleanly. Codex/Sol found it on the finished diff.
**Anchor with `fullmatch`, never `^…$`.** Related: `entities._SAFE_IDENT is types._IDENT` returned
`True`, which reads as "already deduped" and is only `re` caching on the pattern string — a check
that would have talked me out of the dedup for a reason that isn't one.

**Two of five mutations were added because a mutation SURVIVED.** Truncating the member loop to its
first element passed all 28 tests: my payload `(SELECT …)` starts with `0x28` and sorts before every
real column name, so a check-one implementation still caught it. The test proved *some* member was
validated, not *every* member — fixed by parametrizing over payloads at both ends of sort order.
Codex found the other survivor: validating `sort_col` only when `sort_vocabulary is None`, which
every negative test happened to set, while both production kinds declare a vocabulary.

**Process failure worth recording:** I mutation-tested `capacity.py` before committing it, so the
`git checkout --` restore destroyed my own fix. The skill says commit first, mutate second, and it
says so for exactly this reason. Cost was one re-edit; the tests caught it in the same run.

**The sibling sweep paid again.** `group_by` in `findings.py`/`reqs.py` is caller-supplied straight
from MCP and correctly allowlisted — the scariest-looking site was fine. `milestones/capacity.py`
was not: `f"{size}_held"` guarded only by a CHECK two layers away, and the two branches disagreed
for the same input — `OperationalError` with a capacity row, and **a row of zeros plus a success
return** without one. A success-shaped return for a write that did not happen is the CB-15/CB-16
class of lie, found in a module this card never mentioned.

## 2026-08-13 — CB-19 (the vocabulary that only resolved on one side)

**The card scoped it to three sites. It was five, and the missing one would have made the fix
harmful.** `query_findings` resolved `status` and left `severity` raw, *two lines apart*. Routing
only the write paths — exactly what the card specified — would have stored `high` for
`add(severity="High")` while a read-back by the same spelling found nothing. The card would have
manufactured a silent wrong answer. Codex/Sol found it when asked, specifically, "is there a fifth
site I have not listed"; that phrasing is worth reusing, because "review my plan" would not have.

**The sibling sweep found the bigger, older defect.** Sweeping every vocabulary filter turned up
`query_requirements`'s `status`/`priority` and `embeddings.search_similar`'s `status`, all raw —
while the requirements *write* path has normalized through `resolve_priority` since it was written.
So `update_requirement(priority="SHOULD")` stored `should` and `query_requirements(priority="SHOULD")`
returned zero rows, **live, in the sibling entity, predating this card entirely**. Worse than the
findings case the card was filed for, where a bad severity was at least rejected loudly on write.
The general rule is now in CLAUDE.md: *a vocabulary must resolve on both sides of the entity.*

**A tripwire fired, exactly as its author intended.** `test_invalid_severity_raises[HIGH]` carried
the docstring "pins the case-strictness that CB-19 proposes to relax, so relaxing it cannot happen
silently." That is the pattern worth copying: when you deliberately ship a strictness you expect a
future card to relax, pin it with a test that *names the card*. It cost nothing and it converted a
silent contract change into a deliberate one.

**Review of the finished diff caught two regressions, one of them mine and one pre-existing.**
`_resolve` called `.lower()` before checking anything, so `severity=None` escaped as `AttributeError`
instead of the contractual `ValueError` — fixed at the shared helper, not per resolver. And
`_cmd_query`/`_cmd_reqs_query` never caught `ValueError` at all, so an unknown `--status` printed a
raw traceback and leaked the connection. **I checked whether that was mine before claiming it**: it
was not — `acf520b` already resolved `status` with no `try` in the handler. Worth the two minutes;
the honest framing is "pre-existing hole I widened", not "regression I introduced".

**Verification trap, hit and worth not repeating twice.** Testing the old behaviour by pointing
`PYTHONPATH` at a worktree of `acf520b` produced a traceback citing `/home/faxik/w/codebugs/src` —
the editable install resolved `codebugs` to the *main* checkout, so the test proved nothing. This is
the same trap CLAUDE.md documents for `dump_schema.py`. `git show <ref>:<path>` is the reliable way
to read old behaviour.

**Also: the mutation loop timed out mid-run and left a mutation applied**, and a second time a
`git checkout --` restore during mutation testing destroyed an uncommitted fix. Both are the same
lesson the skill already states — commit before mutating — and the second occurrence means it should
be a habit, not a rule to re-read. Run mutations against targeted test files, not the full suite:
five full-suite runs is 5 minutes and blows a 2-minute timeout, five targeted runs is 50 seconds.
