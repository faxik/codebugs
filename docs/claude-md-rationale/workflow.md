# Rationale — `## Workflow`

Biography for the rules in `CLAUDE.md`'s Workflow section: review rounds, reproduced
incidents, rejected forms and the measurements a decision was made on. **No rule lives here.**
A line in this file that reads as an instruction is a defect, and its place is the rules layer.

### The CI job's own limits — `main-invariants.yml` {#пределы-ci-задачи}

**Justifies the rule** "The CI job's own limits", `CLAUDE.md` → `## Workflow`.
Every item in that list came out of adversarial review, and the first draft of the paragraph
overclaimed.

**Item 1, the measurement behind the pinned baseline.** Main's history predates the rule —
measured, **110 of the 132** first-parent non-merge commits before that point would fail the
assertion.

**Item 2, the correction that matters.** The earlier draft asserted that `amend`/`rebase`/`reset`
"necessarily" leave a non-merge commit on the first-parent line. **They do not**, and the claim
stood inside the very list that exists to name limits — an overclaim committed inside the catalogue
of limits.

**Item 4, the measurement history of the server-side protection.** This paragraph used to record
**`enforce_admins` is `false`** — measured 2026-08-21, and true then — so that every rule above
bound non-admin actors only, while the sole account that pushes here is an admin, and the protection
was "advisory against the owner's own credentials".

Measured 2026-08-22 (UTC 09:24): `gh api repos/faxik/codebugs/branches/main/protection` returns
`"enforce_admins":{"enabled":true}` beside `"allow_force_pushes":{"enabled":false}` and
`"allow_deletions":{"enabled":false}` (`required_linear_history`, `required_signatures`,
`lock_branch` all `false`); `gh api repos/faxik/codebugs --jq .permissions.admin` returns `true`;
and `gh api repos/faxik/codebugs/rulesets` returns `[]`, so the branch rule is the whole of the
server-side protection and it now binds the owner too.

Re-measured 2026-08-22 on the open residual:
`gh api repos/faxik/codebugs/branches/main/protection --jq keys` returns no `required_pull_request_reviews`
and no `required_status_checks` key, so that residual is unchanged.

**The 2026-08-21 measurement is not an error in this document's history**; it was the state on that
date, and it is what CB-59 and the DIR-1 acceptance record (Э-9) describe. The rule deliberately
says that a later measurement, not the paragraph, is the authority.

**Item 5.** The workflow used to subscribe to `pull_request`, guarded by
`if: github.event_name != 'pull_request'`. Both reviewers caught the defect independently. The first version of that test asserted
only the negative half, so deleting `push: branches: [main]` outright left the suite green
(verified, **126 passing**).

**Item 6.** The one test that reads this repository's real history is
`test_ci_workflow_asserts_the_first_parent_invariant`, which `cat-file`s the pinned baseline.

**How review defeated each draft of the pinning test.** Cross-model review defeated the
first draft with a step whose multiline `name:` scalar contained both strings — valid YAML, both
assertions green, checkout still depth 1. The same review defeated "the first checkout carries the
key" with `if: ${{ false }}` on it followed by a second, bare checkout.

---

### CB-121 — the four load-bearing details of the alarm {#cb-121-четыре-несущие-детали}

**Justifies the rule** "Four details are load-bearing", `CLAUDE.md` → `## Workflow`.

**Provenance.** Every one of the four is a cross-model review finding rather than foresight.

**Rejected forms, with their reasons.** Making the merge name the pinned `TESTED_HEAD` closes only
the branch half and pays a false refusal, since `pre-merge-commit` refuses a head with no ref. And a
real compare-and-swap needs `git update-ref` over `commit-tree`, which this repo's own CI alarm
treats as a hook-bypassing shape.

**How `merge.ff=false` was verified.** By replaying the incident in a throwaway repo: default config
gives `Fast-forward` and zero merge commits; `merge.ff=false` gives a merge commit. The two limits
recorded beside the rule exist because the first draft of that sentence overstated it.

---

### The guards that had to be re-read fail-closed {#сторожа-читают-fail-closed}

**Justifies the rules** "`MERGE_HEAD` is read fail-closed", "`core.hooksPath` can make
`_guard_enforcement_armed` lie", "The bootstrap gate's condition must be MONOTONIC" and "Every reader
of the staged set passes `-c core.quotePath=false`" — `CLAUDE.md` → `## Workflow`.

**`MERGE_HEAD`: both states were reproduced, and neither typed `--no-verify`.** The empty file let
arbitrary staged content land on main with no merge at all; the file with no trailing newline landed
a real two-parent merge of an untyped branch. `pre-commit` was the one place left failing open in a
change that had already hardened the CI job and the `pre-merge-commit` hook.

**`core.hooksPath`: what round 3 reproduced.** `git config core.hooksPath <empty-dir>` left the guard
returning `0` while nothing was installed, and a commit of arbitrary content on main then succeeded.
The relative-value half was reproduced too: armed in the primary, main checked out in a linked
worktree with no `.githooks` there, guard `0`, source commit onto main `0`. The `--type=path` detail
came from a third failure, where the same function was resolving the same setting through
`--git-path` two lines earlier — one setting, two answers.

**The bootstrap gate took three attempts.** It first gated on the file existing, so one `rm` was a
permanent silent disarm (round 2). It then read the literal ref `main`, which any clone with no local
main collapsed straight back (round 3). Round 4 reproduced the full disarm once more through the
promisor-remote error path. The claim "no checkout shape can hide the history" was true and still
insufficient — the hole had moved from a checkout shape to an error path, which is the third distinct
door onto the same defect.

**The non-ASCII refusal was the mirror image of a bug this repo already had**, the same default
having once made a guard silently accept what it was there to refuse.
