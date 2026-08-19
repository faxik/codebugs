# BRIEF: CB-58 — wire worktree-setup/finish to the claims ledger

Dispatched: 2026-08-19, by the planner-cascade pilot (SPEC-planner-cascade-2026-08-19, §12 full
brief format). Lane: **mechanical** — no unresolved design decision; the target design is already
ratified in this repo's CLAUDE.md (Claims module → Adoption) and running in autosorter's scripts.
Branch: `fix/cb-58-claims-wiring`. The reader arrives cold; everything needed is below.

## Unit

CB-58 (medium, `process`, `tools/worktree-setup.sh`) — single card, single concern.

## Intent (замысел)

Creating a worktree for a card must acquire a **real claim**: holder identity, database-guaranteed
mutual exclusion, a release path, and abort safety. Today it is a best-effort anonymous status
write, so an abandoned branch leaves the card `in_progress` forever and two setups can silently
build the same card past the tracker (the pure-git branch guard is the only thing with teeth).
This is also the dispatch substrate the planner cascade itself stands on (SPEC §7.1: claim every
card of a unit at dispatch) — the pilot's own dispatch integrity is the pain being fixed, not just
a tidiness item.

## Premises (each re-verified by reading the file on 2026-08-19)

1. `tools/worktree-setup.sh:201-207` — the "claim" is `codebugs update "${cb}" --status
   in_progress`, looped over `_claim_ids`; failure degrades to "mark it in_progress by hand".
   No holder, no exclusion, no release.
2. `tools/worktree-setup.sh:100-121` — registry check gates which ids enter `_claim_ids`: only
   `open` cards; `in_progress` prints a warning (deliberately not a refusal — the comment explains
   why); skipped entirely when the `codebugs` CLI is off `PATH` or `CODEBUGS_SETUP_NO_CLAIM` is
   set (:103). Tests rely on that variable; its semantics must survive.
3. `tools/worktree-setup.sh` contains **no `trap` statement** (grep verified — the two "trap"
   hits at :138/:165 are prose in comments). An abort between claim and worktree creation leaks
   the claim.
4. `tools/worktree-finish.sh:355` — the ONLY tracker interaction in finish is printing
   `Remaining by hand: close the card (codebugs update CB-NN --status fixed)`. Nothing releases.
5. The target mechanism exists and is complete: `codebugs claim ID --holder H --holder-kind
   branch --repo R [--note …] [--no-project]` and `codebugs release ID --holder H … [--no-restore]
   [--reason …]` (both verified via `--help` today). Exit codes are the shell API: 0 proceed,
   1 error, 3 held-by-other, 4 already-resolved, 5 contended-retry (CLAUDE.md, Claims module).
   Mutual exclusion is a partial unique index — a database guarantee, not discipline.
6. Claiming a finding **projects its status to `in_progress` for free** (`EntityKind.busy_status`;
   `--no-project` opts out), and closing a card auto-releases the claim in the same transaction
   (`claims._auto_release_on_terminal`). So the current status flip is subsumed by `claim`, and
   the happy path (card closed as `fixed`) needs no explicit release at all.
7. The adoption example to port (CLAUDE.md, Claims → Adoption): autosorter's `worktree-setup.sh`
   claims every card named in the branch (and in `--items`) **before** `git worktree add`, with an
   EXIT trap releasing claims if setup aborts; `worktree-finish.sh` releases whatever the branch
   still holds. **Exactly one of those calls may be fatal — the setup gate.** Everything else is
   guarded, so a missing/contended tracker can never abort a finish after the merge has landed.

## Prescription (hypothesis — stop and report if reality diverges)

In `tools/worktree-setup.sh`:
- Replace the `codebugs update --status in_progress` loop with `codebugs claim "${cb}"
  --holder "${BRANCH_NAME}" --holder-kind branch --repo "<absolute main-checkout root>"
  --note "worktree-setup"`.
- Handle exit codes explicitly: `0` → claimed; `3` → **fatal refusal** naming the holder
  (`codebugs who-holds` output helps) — this is the one fatal call, the setup gate, and it needs an
  escape hatch consistent with the existing `--allow-duplicate` philosophy; `4` → warn (mirrors
  today's non-open warning); `5` → retry once, then warn; CLI off PATH / `CODEBUGS_SETUP_NO_CLAIM`
  → current degradation messages unchanged.
- Claim **before** `git worktree add` (today the loop runs after, at step [4/4] — reorder or
  justify not reordering in the PR), and install an EXIT trap after the first successful claim
  that releases every claim taken so far if setup aborts; disarm it on success.
- The existing `open`/`in_progress` pre-check (:100-121) largely duplicates what `claim`'s own
  outcomes report — collapse it into the exit-code handling rather than keeping two gates that can
  disagree (this repo's shared-predicate lesson).

In `tools/worktree-finish.sh`:
- After the merge lands, release what the branch still holds: `codebugs release "${cb}" --holder
  "${BRANCH_NAME}" --holder-kind branch --repo … --reason "worktree-finish"` — **guarded, never
  fatal** (premise 7). Decide `--no-restore` vs default restore by reading the release semantics
  in `claims.py` and state the choice in the PR: the card here is usually still open-and-claimed
  (closing happens by hand, :355), and the wrong choice silently rewrites a status.

Docs (worked-example rule — the doc that overclaimed this before must not keep the stale claim):
- CLAUDE.md Workflow section: rewrite the "**That last part is a best-effort status write, not a
  claim**" passage to state the new contract, including the residual below.
- CLAUDE.md Claims → Adoption: add codebugs' own scripts alongside autosorter's.

## Acceptance (must include a mutation probe)

Extend `tests/test_worktree_harness.py`, following its existing two-class pattern (per-guard
behavioural + structural wiring — the file's docstrings explain why both exist):
1. **Structural**: setup invokes `codebugs claim` with `--holder-kind branch` and carries an EXIT
   trap that releases; setup no longer contains `update … --status in_progress`; finish invokes
   `codebugs release` on a guarded (non-fatal) path.
2. **Behavioural** (cheap, no real tracker): a stub `codebugs` executable on `PATH` recording
   argv and returning scripted exit codes — assert exit 3 refuses setup, exit 4/5 do not, abort
   mid-setup runs the release trap, and `CODEBUGS_SETUP_NO_CLAIM` still skips everything.
3. **Mutation probe (gate)**: revert the setup loop to `codebugs update --status in_progress` —
   the new tests MUST fail; show that in the PR (run before/after). A test green on both sides of
   the fix is the failure mode this repo documents.

Full suite + `ruff check` green in the worktree (`uv run --extra dev …`), landed via
`tools/worktree-finish.sh` as usual.

## Preflight verdicts (SPEC §4, five checks — 2026-08-19)

1. Cited file:lines exist and say what the card claims — **pass** (premises 1–4).
2. Premises hold against schema/config, not just code — **pass**: partial unique index
   `entity_claims(entity_id) WHERE released_at IS NULL` is the exclusion; exit-code API documented.
3. Named file is the fix site — **pass**, plus `tools/worktree-finish.sh`, tests, CLAUDE.md.
4. No overlap with in-flight units — **pass**: no live worktrees, main clean; no other open card
   touches these scripts (CB-59 is `.github`/branch protection — adjacent, not overlapping).
5. Re-run staleness — n/a (fresh brief; re-run checks 1–4 if this sits >7 days).

## Residual, stated honestly

A branch abandoned **after** a successful setup leaves a live claim (steal/expiry are deliberately
deferred — CLAUDE.md Claims, design doc §10). That is strictly better than today's anonymous
`in_progress`: the claim names holder+repo (`codebugs who-holds`), has a release verb, and
auto-releases the moment anyone closes the card. Do not attempt expiry/steal in this unit.

## Escalations slot

Empty — explicitly. (The claim-expiry gap above is already tracked as deferred-by-design with a
revisit path; no new decision above this level was uncovered.)
