# Finalization — 7-point consistency scan attestation

**Date:** 2026-08-06
**Scanned:** `FINAL-DESIGN.md` (1762 lines), `FINAL-PLAN.md` (233 lines)
**Scanned by:** orchestrator, directly (not delegated)

| # | Item | Outcome |
|---|---|---|
| 1 | **Test framework** — prose vs code blocks | PASS. `FINAL-PLAN.md:168` runs `uv run pytest`, matching CLAUDE.md's stated command. No competing framework named anywhere. |
| 2 | **Test count** | PASS. "29 tests" at `FINAL-PLAN.md:17`, `:127`, `:168`, and the section heading "§3.2 The 29 tests". The earlier 26-vs-27 discrepancy (Codex erratum E6) is resolved by folding 5b into test 5. |
| 3 | **File paths** — prose paths appear in code blocks | PASS. `tests/test_claims.py`, `src/codebugs/claims.py`, and both autosorter shell scripts appear as path indicators in the plan's step headers and diffs. |
| 4 | **Dependency claims** | PASS. No new third-party dependency. The design's only imports are stdlib + existing `codebugs` modules; the git-verifier subprocess path (the one component that would have shelled out) is deferred, so v1 launches no subprocess at all. |
| 5 | **Cross-doc consistency** | PASS. `FINAL-PLAN.md`'s scope matches `FINAL-DESIGN.md`'s v1 delivery; the seven deferred items appear in both as deferrals, never as work. |
| 6 | **Ruling survival — no dropped or INVERTED ruling** | PASS, verified by context rather than counts. Grep hits on deferred names are *negations or deferral rows*, not reappearances: `FINAL-DESIGN.md:249` ("**None of them is present under an alias.** In particular there is no `include_released`, no `--all`, no `divergent_only`, no `--divergent`, no `stale_after_seconds`, no `--stale-after`"), `:579` (`list_claims` takes none of them), `:1726` (deferred row D5). The user's ratified requirements decision survives verbatim in substance at `:964`. The one-fatal-call rule, S0's independence, and the sequential-vs-concurrent honesty all survive, the last one even inside the shell refusal message (`:1409`). Deploy gates G1/G2 carry falsifiable failure conditions. |
| 7 | **Brief-question coverage, incl. negative answers** | PASS. Q1 (where ownership lives) → dedicated table. Q2 (`entities.py` read-only) → answered as a stated rule change, with the note that `_SAFE_IDENT` is a test-only invariant and `blockers.py:436-439` already interpolates. Q3 (exclusion primitive) → partial unique index; settled empirically, not by argument. Q4 (requirements have no busy status) → **negative answer preserved**: requirements declare nothing and never project (`:138`, `:957`, `:964`). Q5 (release must not resurrect) → `prior_status` restore. Q6 (transition/workflow layer) → **negative answer preserved**: not built; the 14 hand-rolled gates and the orphaned `sweep._validate_transition` are recorded as the reason it was considered and the reason it was declined. Q7 (adoption) → in-repo CLI + the autosorter shell diffs; the out-of-repo `SKILL.md` explicitly rejected as adoption. Q8 (TTL/lease) → **negative answer preserved**: no TTL, no lease, no heartbeat; `lease` survives only in artifact descriptions of prior art. |

## Deviation accepted during the fold

Erratum E5 asked that "each recorded pair was ~40 minutes apart" be stated. The source
(`worktree-setup.sh:58-71`) attributes "~40 minutes" to **CB-2431 only**. The architect declined to
extend it to CB-2534, stated the timing each incident actually carries, and explained why in the
document. **The orchestrator accepts this deviation**: complying literally would have asserted
something the record does not support, and the erratum's substance (no recorded incident is
established as concurrent) is carried.

## Corrections folded during finalization

Three citations were corrected against the real files this run: the incident comment is
`worktree-setup.sh:58-71` (not `:57-66` as the brief had it); the `--items` `if` begins at `:220`
(the comment header is at `:216`); and `worktree-finish.sh:1338` begins `if git … worktree remove`,
not a bare `git` command — Codex was right on that one.

## Status

Design council COMPLETE. Deliverables: `FINAL-DESIGN.md`, `FINAL-PLAN.md`.
Two independent final verifiers (Opus, Codex/Sol) returned SPEC-READY WITH NAMED FIXES; all eleven
named fixes were folded as normative text before finalization.
