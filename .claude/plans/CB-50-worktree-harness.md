# CB-50 — the workflow was prose, and prose was violated the day it was written

## What actually happened on 2026-08-16 (verified from git, not recalled)

| time | event |
|---|---|
| 13:37 | `2957070` adds the "Workflow — `main` is never edited directly" section to `CLAUDE.md`. It mandates a typed branch in a worktree and `git merge --no-ff`. It also declines autosorter's harness: *"minus its `tools/worktree-*.sh` harness, which this repo does not have and does not need — plain git is enough."* |
| 14:44 | `566f547` merges `worktree-cb-45-similarity-seam` — a branch with no type prefix. |
| 15:30 | `git reflog` records `merge worktree-cb-45-similarity-seam: **Fast-forward**` → `fb03d8e`. No merge commit. The branch then sat at main's exact SHA. |

Two hours between writing the rule and breaking it, in both of its clauses.

**The section contained its own diagnosis and ignored it.** Two sentences before declining the
harness it said: *"A convention that exists only as a pattern in the log is not a rule — nothing
bound it, so it held until it didn't."* It then declined to bind the rule it was introducing.

## Other findings from the same sweep

- `.worktrees/` (root, autosorter's convention, created 10:36) and `.claude/worktrees/` (documented,
  in use) both existed; **neither was gitignored**.
- Enforcement surface was exactly zero: no `.claude/settings.json`, no git hooks (`.git/hooks` held
  only samples), no CI (`.github` absent), no `tools/`.
- `.claude/plans/CB-49-where-abspath.md` sat untracked in main, against the session-end clean-tree
  rule.
- `CONTRIBUTING.md` (50 lines) documented `tests/test_db.py` — which has not existed for a long time
  — and an "adding features" flow that contradicts the self-registration rules in `CLAUDE.md`.

## Decision

User chose **port the autosorter harness** over git-config-only enforcement and over
harness-plus-blocking-edits-on-main. The third was rejected as diverging from the repo we were
asked to align with — autosorter's own "never edit main" rule is prose there too, enforced by
nothing.

## What landed

`tools/_guards.sh` (12 guards, distinct exit codes 2–13), `worktree-setup.sh`, `worktree-finish.sh`,
`pre-commit-hook.sh`, `install-hooks.sh`, `tests/test_worktree_harness.py` (95 tests).

**Deliberate divergences from autosorter, each load-bearing:**

1. **Guards are a SOURCED LIBRARY, not inlined.** autosorter's guard test extracts a function's text
   by reading to the first column-0 `}`, which forced an `INVARIANT` comment onto two of its guards
   warning that a stray brace silently truncates the loaded copy. A test that can load half a
   function and pass is the failure mode this harness exists to prevent.
2. **No `.venv` symlink.** autosorter symlinks the root venv into each worktree. Here that would
   destroy the isolation the workflow depends on — `uv run` building a per-worktree editable install
   is *what makes a worktree's test run actually test the worktree*. Setup runs `uv sync --extra dev`
   instead, closing the documented "No module named pytest" trap.
3. **Stale-base threshold re-derived: 40, not 200.** Measured across all 20 branches on 2026-08-16 —
   active 0–16 behind, dormant 77–155, band `[17,76]` empty. autosorter's 200 would never fire here.
4. **A branch-type guard, which autosorter has no equivalent of.** It is the rule that was broken.
5. **`_guard_main_clean` and `_guard_enforcement_armed`**, also new.

## The empirical checks (run, not reasoned)

- Replaying the incident in a throwaway repo: default config → `Fast-forward`, 0 merge commits;
  `merge.ff=false` → merge commit. **The fix works.**
- Branch already an ancestor of main → `Already up to date`, main does not move. `merge.ff` is
  irrelevant there; the doc's first draft overstated this and was corrected.
- The old `git show | grep -q` conflict guard **misses a marker on line 1 of a 4 MB file** (SIGPIPE →
  141 → `pipefail` → pipeline non-zero → marker accepted).
- All enforcement paths exercised against the real repo: source edit on main refused, plan note
  allowed, sanctioned branch allowed.

## Review

**Codex/Sol adversarial pass + a live peer session** found five real defects, all fixed in `d0d138e`:
the hook blocked *conflicted* merges on main (the flow `CLAUDE.md` documents — clean merges use
`pre-merge-commit`, which is not installed, so only the conflicted half broke); the SIGPIPE false
negative above; hook/guard predicate drift (`fix/a/b` accepted at commit, refused at integration);
the lock serialized the merge but not the testing, so a second finisher could land a combination
never tested; and a seventh mutation the author's own six missed.

**An independent Opus adversary then found eight more**, fixed in `ac9bb65`. Its headline is the one
worth keeping: round 1's fix for the lock race **reintroduced CB-41's shape** — `TESTED_MAIN` was
sampled *after* the 70-second test run, so a concurrent land moved main, the sample recorded the new
main, and the in-lock check compared new-main to new-main and passed, certifying the untested
combination it was written to refuse. It also proved the suite never executed the scripts: deleting
guard *invocations* left 70 tests green, the branch-type guard included.

Mutation testing, honestly stated: the author's own six were caught by exactly the intended test,
but two reviews found six survivors the author's set did not reach. Two of those turned out to be
**equivalent mutants** (`-e` vs `-f` in the dangling-symlink check; `--diff-filter=d`, which was
equivalent only because a `|| continue` was swallowing genuine read failures — fixing that made the
filter load-bearing and the mutation catchable). The rest were real gaps and are now covered. **The
claim "every guard was mutation-checked" was retracted rather than defended.**

**A defect found in this branch's own installer, by running it:** the first `install-hooks.sh`
symlinked the hook to the **authoring worktree**, so it would have dangled the moment that worktree
was removed — leaving a repo that looked armed (the symlink sits right there in `.git/hooks`) and
silently was not. Both halves fixed: the installer targets `REPO_ROOT/tools`, and
`_guard_enforcement_armed` refuses to integrate from an unarmed clone.

## Known limitations, recorded rather than half-fixed

- **All of it is client-side and per-clone.** No CI, no server-side protection on `origin`. `git
  push`, `rebase`, `cherry-pick`, `revert`, `am`, `reset --hard` and `core.hooksPath` all move or
  publish `main` without passing the hook. A protected remote branch plus required CI is the real
  answer for a shared repo; this is the local half, and the docs now say so instead of claiming
  "mechanical enforcement" flatly.
- A typed branch committed in the **primary checkout** satisfies the hook while ignoring the worktree
  rule entirely.
- `fd 9` (the flock) is inherited by subprocesses; a daemonizing descendant could hold the lock past
  script exit. Needs a close-on-exec lock wrapper.

## Coordination note

A parallel session was live in `.claude/worktrees/cb-45-similarity-seam` on the untyped branch
throughout, with uncommitted work. It was messaged before arming; its next commit hit the hook and it
renamed to `fix/cb-45-post-merge-review`. That is the enforcement working on a real session on its
first day, and it is also why the merge-safety defect was found by a question rather than an outage.

## Outcome

Landed as `238d125`. Enforcement armed from main; `.git/hooks/pre-commit` →
`/home/faxik/w/codebugs/tools/pre-commit-hook.sh`, `merge.ff=false`,
`_guard_enforcement_armed` returns 0. Verified against the real repo: a source edit on main is
refused, a `.claude/plans/*.md` note is allowed, 1196 tests pass, ruff clean.

**The invariant now holds and is checkable in one command.** `git log --first-parent --no-merges
fb03d8e..main` returns exactly two commits, both `.claude/plans/*.md` notes. Every line of code that
landed today arrived through a merge commit.

Follow-ups filed: **CB-57** (a plain `git merge <untyped-branch>` onto main is still caught by
nothing — the surviving half of this card's own incident), **CB-58** (setup's card "claim" bypasses
`claims.py` and has no release path), **CB-59** (no branch protection and no CI on a repo that has a
GitHub remote — both reviewers independently argued this is where the enforcement belongs).

## What this iteration should be remembered for

Not the harness. **Both review rounds found that the fix for a race had reintroduced the defect
class it was written for**, and the second one was CB-41's shape exactly — a value sampled at the
wrong end of a window, so the guard compared a state to itself and passed. This repo has now
recorded that shape at least four times (CB-41 thrice, here once). The rule that keeps coming out of
it is the same every time: *point-of-use discipline is the wrong enforcement layer; make the bad
state unrepresentable.* Round 1 wrote the skew guard and re-sampled; round 2 replaced the sample
with the value itself, so no statement can be inserted between them.

The second thing: **a suite that tests every guard and never runs the script tests nothing about the
composition.** Deleting the guard *invocations* left 70 tests green — including the branch-type
guard that exists for the incident this card is about. That is the repo's own "a check that
validates elements cannot validate their composition", found in the harness built to enforce the
repo's rules.
