# Checkpoint — Round 2 → Round 3

**Round:** 2
**Date:** 2026-08-06

## User decisions (verbatim)

1. Asked: "Должен ли захват требования (FR-N) менять его статус? Это единственная претензия,
   которую оба адверсария выставили всем девяти вариантам — и закрыл её я, а не вы."
   Answered: **"Нет, только запись захвата (моё решение, рекомендую)"**

2. Asked: "Что делаем дальше с самим дизайном?"
   Answered: **"Точечный фикс-пасс, потом спек (рекомендую)"**

## Governance item RESOLVED

The requirements-projection question — the only finding both adversaries scored against all nine
Round-1 proposals — was declared `SETTLED` in `CHECKPOINT-r1.md:33` by the ORCHESTRATOR on the
Judge's Round-1 reasoning, with no user in the loop. The Round-2 Judge ruled its own reasoning
correct but the *settlement* improper ("three steps from 'the Judge thinks' to 'not a defect'"),
and required explicit user ratification before code starts.

**The user has now ratified it directly.** Requirements get claim records; claiming a requirement
does NOT change its status; `reqs.py:22`'s CHECK constraint is NOT rebuilt. This is no longer an
orchestrator inference — it is a user decision on the record.

## Judge's Round-2 rulings carried into Round 3

- **Substrate: READY.** Both adversaries independently executed probes against the partial-index
  ledger (`CREATE UNIQUE INDEX … ON entity_claims(entity_id) WHERE released_at IS NULL`) and could
  not break it: no release/reclaim race, no double-release, no path to two live rows for one
  entity. Success criteria 1, 2, 3 are proven on real SQL by two model families.
- **All eight Round-1 mandatory fixes: genuinely FIXED**, each re-verified by independent
  execution rather than trusted.
- **The trustworthiness argument does NOT survive.** The git-liveness predicate is dead on two
  independent counts, both re-verified by the orchestrator:
  (a) `worktree-finish.sh` contains **zero** `git branch -d/-D` (grep count 0) — branches are never
      deleted, so a branch-existence verifier returns `live` forever and discriminates nothing in
      the only deployment that would use it;
  (b) the predicate is wrong as written, not merely as implemented — `db.git_rev_parse` runs
      `git rev-parse`, and `git rev-parse --verify refs/heads/main~1` **also** returns a SHA
      (orchestrator-verified: `f229e78e…`). The correct existence primitive is
      `git show-ref --verify --quiet`, which rejects it.
  **Replacement:** the ancestry test. Integration is `merge --no-ff` (`worktree-finish.sh:1198`),
  so `git branch --merged` / `git merge-base --is-ancestor` separates merged branches from work in
  flight with ZERO new state.
- **The guard contradiction, adjudicated.** The shipped guard IS check-then-act (scan at
  `worktree-setup.sh:86`, branch creation at `:143`, no `flock`), so two concurrent setups with
  different slugs both pass — git cannot help because the branch names differ by construction.
  The ledger therefore adds real collision prevention. **But both recorded incidents are ~40 min
  apart — sequential — and the shipped guard already refuses those.** Nobody has established
  either observed incident was a race. Motivation is *partially* restored, not restored.
- **The two adversaries attack different mechanisms and compose:** Opus's "empty at launch" kills
  the false-positive downgrade (inert on day one); Codex's TOCTOU vindicates the atomic gate
  (needs zero history).
- **Shell integration rule (structural, replaces a fix list).** All four shell FATALs are the same
  error — unguarded `codebugs` calls in a script where every shipped one is `if`-guarded or
  `|| true`. Rule: **exactly ONE new call may be fatal** — the claim gate placed BEFORE
  `git worktree add`. Everything else is guarded. This makes the worst defect (killing
  `worktree-finish.sh` *after* `merge --no-ff` has landed on main) impossible by construction.

## Scope ruling — IN vs DEFERRED (~550 lines; approval was ~520)

**IN:** soft-delete claim ledger; atomic claim / release / query; touch-count outcome
discrimination; terminal hooks guarded by CAS `changed`; corrected pre-worktree claim gate;
unconditional finish release using `BRANCH` (NOT `BRANCH_NAME` — it does not exist in that script);
an ID-output mode.

**DEFERRED to their own commits:** `merge.py` refactor; the `release_item` atomicity fix (the design
itself calls it "independent of claims"); `expected_status` + `changed`; `steal`; history/summary
extras; the audit/verifier tooling; `pull_next` integration (deferring it removes four findings at
once, including a call that cannot run — it passes `holder=agent_id` while `release_item`'s
variable is `agent`, `capacity.py:234-235`).

Note for the record: `expected_status`/`changed` was the literal form of the user's original
question. It is deferred, not dropped — the ledger's claim outcome answers the same need
(claimed / already_mine / held_by_other / undetermined) for the claim case; `expected_status` is
the generic CAS for arbitrary transitions and is separable.

## Worth doing regardless of the ledger's fate

- Add `git branch --merged` filtering to the `worktree-setup.sh` guard.
- Add `git branch -d` to `worktree-finish.sh` — branches accumulate forever today, and each one
  blocks every future branch for its card.

## Orchestrator verifications this round

- `worktree-finish.sh`: `git branch -d/-D` → 0 hits; `BRANCH_NAME` → 0 hits; `BRANCH` defined at
  `:647`; `set -euo pipefail` at `:11`; `merge --no-ff` at `:1198`; `worktree remove` at `:1338`.
- `git rev-parse --verify refs/heads/main~1` → returns a SHA; `git show-ref --verify --quiet` on the
  same ref → rejects. Judge correct, both adversaries incomplete.
- Citation dispute settled in the orchestrator's favour by BOTH adversaries independently:
  `worktree-setup.sh` is 274 lines; `worktree add` `:143`; claim loop `:208-209`; "~41 cards" `:120`.
  The Round-2 architect's "off by two throughout" is wrong and internally impossible.

## Scan attestation

7-point pre-finalization scan: NOT YET RUN — Round 3 pending.
