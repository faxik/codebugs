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
