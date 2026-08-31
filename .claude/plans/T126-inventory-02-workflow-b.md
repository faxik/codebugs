# Inventory — CLAUDE.md lines 401–826 (codebugs, docs-t126-claude-md-compression worktree)

L401 | HISTORY-LOADBEARING | The immediately preceding CI-limits item (item 3, before this range) established that both the CI audit job and the pre-commit hook shared a rename-detection defect (`--name-only` without `--no-renames` hiding the deleted source of a `git mv`), and this line records that both were fixed and both are pinned by test — the "both" claim only makes sense against that earlier story.
L403 | RULE | A GitHub Actions workflow cannot refuse a push by itself — it can only report on the push after the fact.
L403-404 | RULE | `main-invariants.yml` is therefore only an alarm; the actual gate enforcing main's invariants is branch protection configured on `origin/main`.
L404 | MEASURED | Branch protection on `origin/main` has been ON since 2026-08-21.
L404-406 | RULE | The enabled branch-protection scope is deliberately narrower than what this CI-limits list originally demanded.
L405-406 | RULE | Enabled: force pushes to `origin/main` are refused; deletion of `origin/main` is refused.
L406-408 | RULE | NOT enabled: require-pull-request, and consequently the two settings that only mean anything behind a PR — marking `ci.yml`'s `tests` job as a required status check, and disabling squash/rebase merging.
L408-409 | RULE | That narrower branch-protection scope was ratified by the repository owner as sufficient to close CB-59.
L409-411 | WHY-NARROW | Force-push and branch deletion are exactly the class of action nothing local (no client-side hook) can catch, because they rewrite or destroy history that every local hook has already approved.
L410-412 | WHY-NARROW | Require-PR and a required status check instead constrain HOW work arrives at main, and that is already governed locally by `merge.ff=false`, the three git hooks, and `_guard_enforcement_armed` — for any clone that has run `tools/install-hooks.sh`.
L413-414 | RULE | CB-59 is considered closed at this narrower branch-protection scope, not at the CI-limits paragraph's original four-item list.
L413-414 | BOUNDARY | Branch protection is repository configuration, not committed state, so nothing in this repository's tree can verify or restore it — it must be checked live via the GitHub API.
L416-418 | BOUNDARY | Residual, still open: an UNARMED clone (one that has not run install-hooks.sh) can still push a non-merge commit straight to main, because require-PR is off; `main-invariants.yml` remains only the alarm for that case, never a gate.
L419-421 | MEASURED | Re-measured 2026-08-22: `gh api repos/faxik/codebugs/branches/main/protection --jq keys` returns no `required_pull_request_reviews` key and no `required_status_checks` key.
L421 | RULE | That residual (unarmed clones can push directly) is confirmed unchanged as of the 2026-08-22 measurement.
L422-425 | HISTORY-LOADBEARING | This paragraph used to record that `enforce_admins` was `false` (measured 2026-08-21, true at that time), meaning every branch-protection rule above bound only non-admin actors while the sole account that ever pushes here is an admin — making the protection merely "advisory against the owner's own credentials"; the rule that follows (enforce_admins now true) is stated only as a correction of this earlier state.
L425-430 | MEASURED | Measured 2026-08-22 (UTC 09:24): `gh api repos/faxik/codebugs/branches/main/protection` returns `enforce_admins.enabled: true`, `allow_force_pushes.enabled: false`, `allow_deletions.enabled: false`, with `required_linear_history`, `required_signatures`, `lock_branch` all `false`; `gh api repos/faxik/codebugs --jq .permissions.admin` returns `true`; `gh api repos/faxik/codebugs/rulesets` returns `[]`.
L430-431 | RULE | The single branch-protection rule is the whole of the server-side protection, and (per the 2026-08-22 measurement) it now binds the repository owner as well as other actors.
L431-434 | BOUNDARY | Cost of enforce_admins=true, named explicitly: an emergency rewrite of `origin/main`'s history now requires first turning `enforce_admins` off (or the protection itself) — an explicit, deliberate repository-settings act rather than a hurried `git push --force` — which is precisely the friction this setting is meant to buy.
L435-438 | BOUNDARY | The 2026-08-21 measurement (enforce_admins=false) is not an error in this document's history — it correctly described the state on that date, and that state is what CB-59 and the DIR-1 acceptance record (Э-9) refer to; a later live measurement, not this paragraph, is the authority on the current setting.
L439 | RULE | `main-invariants.yml` deliberately does NOT subscribe to the `pull_request` GitHub Actions event.
L439-444 | HISTORY-LOADBEARING | It used to be guarded instead by `if: github.event_name != 'pull_request'`; a job skipped by an `if:` condition is reported as PASSING for required-status-check purposes, so marking such a job "required" would create a check that can never fail on the only path branch protection evaluates it on — the rule to keep the workflow off `pull_request` entirely (rather than skip-guard it) exists specifically because this failure mode ("gate that cannot fire", reintroduced inside its own attempted fix) was found and reproduced by two independent reviewers.
L444 | RULE | Lint and tests instead live in a separate workflow file, `ci.yml`, which does run on pull requests.
L445-447 | RULE | The push/pull_request trigger split for `main-invariants.yml` is pinned by a test that checks BOTH directions (that it fires on push to main, and that it does not fire on pull_request).
L445-447 | HISTORY-LOADBEARING | The first version of that pinning test asserted only the negative half; deleting `push: branches: [main]` outright therefore left the whole suite green (verified, 126 tests passing) — proving that testing only one direction would have let "gate that cannot fire" reappear as "workflow that never fires at all".
L448-450 | RULE | `main-invariants.yml` needs `fetch-depth: 0` on its checkout: with the default shallow checkout, the pinned baseline commit is absent and `origin/main` itself may not exist, which would silently narrow the audited history down to just `HEAD`.
L450 | RULE | This dependency is now stated explicitly at the checkout step, because the coupling used to be implicit and undocumented.
L451-453 | WHY-NARROW | `ci.yml` needs the identical `fetch-depth: 0` key, but for a DIFFERENT reason than `main-invariants.yml`: there, history is read only by the audit step; in `ci.yml`, history is read by the test SUITE itself.
L452-454 | IDENT | Exactly one test in the entire suite reads this repository's own real git history: `test_ci_workflow_asserts_the_first_parent_invariant`, which runs `git cat-file` on the pinned baseline commit.
L454-457 | HISTORY-LOADBEARING | (CB-139) Under the default depth-1 checkout, `ci.yml`'s `tests` job was red in CI ALWAYS and green in every local run, because that one history-reading test failed only there — a gate that can never pass hides exactly the regressions it exists to catch; the fetch-depth coupling had been understood for one workflow (main-invariants.yml) and missed for the other (ci.yml) until this was found.
L458 | IDENT | `test_ci_suite_job_checks_out_the_history_its_own_suite_reads` pins the `ci.yml` fetch-depth fix.
L458 | RULE | That pinning test asserts four distinct properties, each of which was earned by defeating a specific evasion, not chosen arbitrarily.
L459-461 | RULE | Property 1, "comments do not count": the pin must not be satisfiable merely by the literal text `fetch-depth: 0` appearing anywhere in the file, including inside a comment; the stripping used to check this is whole-line only, so a trailing inline `#` comment is tolerated (not parsed away) by the matcher.
L459-461 | HISTORY-LOADBEARING | This was found because the fix's OWN explanatory comment happens to contain the literal string `fetch-depth: 0`, so a raw text grep for that string stays green even after the real `with:` key is deleted — the whole-line-comment-stripping rule exists only to defeat that specific false pass.
L462-464 | RULE | Property 2, "a file is not a composition": the check must verify the key is present on the CORRECT job/checkout, since `ci.yml` has two jobs (`tests` and `contracts`) each with its own checkout, and merely finding the key "somewhere in ci.yml" (e.g. on the wrong job) would satisfy a naive check while leaving the real gate (the `tests` job) still broken.
L464-467 | RULE | Property 3: the key must specifically be a YAML `with:` input, and its explanation must live in a separate comment LINE — not be folded together into one YAML scalar.
L465-467 | HISTORY-LOADBEARING | Cross-model review defeated the first draft of this test with a step whose multiline `name:` scalar contained both the key-looking text and the explanatory text — syntactically valid YAML, both naive assertions green, while the actual checkout remained depth 1 — which is why the rule now requires the key to be a genuine `with:` input.
L466-469 | RULE | Property 4: exactly one checkout step must carry the fetch-depth key, and that step must carry no `if:` condition at all.
L467-469 | HISTORY-LOADBEARING | The same review round defeated "the first checkout carries the key" by adding `if: ${{ false }}` to that (correct) checkout step and following it with a second, bare checkout that actually executes — showing the naive "first matching checkout" check was gameable, hence the "no if: condition" requirement.
L468-470 | BOUNDARY | Deliberately NOT closed, and named so it is not silently rediscovered later: a job-level `if:` that disables the entire `tests` job is the same class of gap, and this particular test does not check for it.
L470-471 | RULE | The `contracts` job in `ci.yml` stays on a shallow checkout deliberately, because it only runs `tests/test_cli_signals.py` and `tests/test_fsio.py`, neither of which reads repository history.
L473-475 | RULE | `.python-version` is the single source for the interpreter used by main, by every worktree, and by CI; `_guard_interpreter_matches_main` refuses to land any work where the worktree and main did not agree on that interpreter (CB-135).
L475 | RULE | "Single source" here is a claim about what actually DECIDED which interpreter ran, not merely about what value is written in the file.
L476-478 | RULE | `UV_PYTHON` and `--python` both outrank the `.python-version` file (measured), so the guard cannot merely check that the pin file exists — it must confirm the interpreter uv actually selected is the one the pin file names, and must refuse whenever something else outranked the pin.
L478-481 | HISTORY-LOADBEARING | (CB-135) Without that outranking check, an exported `UV_PYTHON` made BOTH main and the worktree answer with the same override, so they "agreed", the gate passed, and the branch landed a different actual interpreter pin that main would then silently adopt on its next `uv run` — CB-135 was rebuilt directly out of this mechanism, and the outranking clause exists only to close this exact hole.
L481 | HISTORY | Cross-model review found this hole; the first draft of the guard contained only a plain existence check on the pin file.
L482-485 | RULE | The interpreter-match gate runs the suite in the WORKTREE while the work being validated lands in MAIN — those are two different statements that came apart in practice.
L483-485 | MEASURED | On 2026-08-22, a manager reported "1943 passed" running the suite from a worktree on Python 3.13.3, while the identical suite run on the already-landed main, under the documented command, gave "1 failed, 1942 passed" on Python 3.14.4 — the red state was on main BEFORE the merge occurred, so no finish-time check could ever have caught it after the fact.
L486-488 | WHY-NARROW | The pre-existing rule "never validate a worktree's changes by running the suite from main" (because `pythonpath=["src"]` resolves against whichever checkout you actually run in) is correct on its own terms, but is exactly what let a second, previously-unnamed variable — WHICH python interpreter is in use — go completely untracked.
L488-491 | MEASURED | Before the pin existed, three different, untracked interpreter states coexisted: main used the system interpreter its `.venv` happened to be built with; a fresh worktree used uv's own default (the newest uv-MANAGED install, a different thing entirely); and `.github/workflows/ci.yml` named no interpreter version at all.
L491-493 | RULE | `uv.lock` had already pinned every dependency VERSION, which is exactly what made the interpreter the one conspicuously unpinned variable remaining; it is explicitly false that "everything else was already nailed down" — an earlier draft of this document wrongly implied that.
L493-495 | BOUNDARY | uv's own version, the operating platform, and the specific BUILD of a given CPython version all still vary and remain unpinned; the guard compares only a version STRING, so two different builds of 3.14.4 read as identical to it.
L497 | RULE | The chosen interpreter pin is `3.14.4`, at full patch precision; both the specific version chosen and the decision to pin at full-patch granularity were deliberate, not defaults.
L498-500 | WHY-NARROW | Which version: 3.14.4 was chosen because it is what main was already running, so landing the pin file moved no environment and opened no window where main would be stale relative to the pin.
L499-500 | MEASURED | The suite is green on 3.14.4 (1949 passed, measured on main after CB-134 landed) and equally green on 3.11.12, 3.12.10, and 3.13.3 — so no red interpreter version forced the choice; the newest stable CPython was picked as where the tested surface should sit.
L501-503 | WHY-NARROW | Full patch (X.Y.Z) rather than just X.Y: this unit's whole subject is making a divergent state unrepresentable, and a bare MAJOR.MINOR pin still leaves one divergent state representable, because uv resolves it to whatever patch of that minor version a given machine happens to already have — two machines could then legitimately differ under an X.Y-only pin.
L504-508 | BOUNDARY | Real, accepted cost of the full-patch pin: it must be bumped by a deliberate, reviewable edit, and a machine lacking that exact build must download one.
L505-508 | MEASURED | Measured: `cpython-3.14.4-linux-x86_64-gnu` shows as "<download available>", which is also what makes the pin reachable in CI; `uv python list` shows only the newest patch per minor version by default, so checking whether an exact older patch is downloadable requires the `--all-versions` flag.
L510 | HISTORY | uv rebuilds a mismatched environment on its own — correcting an assumption the original design brief had made in the opposite direction.
L511-513 | MEASURED | Measured: with `.venv` at 3.13.3 and the pin file at 3.14.4, a plain `uv run` printed "Removed virtual environment", recreated the venv, and ran successfully — no explicit `uv sync` was necessary.
L512-513 | RULE | Because of that self-repair, the pin file does most of the interpreter-matching work on its own; the interpreter guard exists specifically for the cases that self-repair cannot reach.
L514-516 | RULE | Two consequences worth knowing: `UV_PYTHON=` (set empty) OUTRANKS the pin file, and a subsequent plain `uv run` snaps the tree straight back to the pin (both measured) — which is why the pin-bump repair command is written with a leading `UV_PYTHON=` and why its effect only needs to survive until the finish run completes.
L518-520 | RULE | The worktree side of the interpreter comparison is probed with `uv run --extra dev python -c <probe>` — byte-for-byte the same launcher command the pytest phase `[6/7]` itself uses — so the returned answer IS the interpreter the suite will actually run under.
L520-521 | RULE | That same probe call also syncs the worktree to the pin as a side effect, which is desirable since the worktree is about to be tested anyway.
L521-523 | RULE | The main side of the comparison instead executes `<repo_root>/.venv/bin/python` DIRECTLY, deliberately bypassing `uv run`.
L522-523 | WHY-NARROW | Running `uv run` in main would rebuild a checkout that other sessions may currently be working in, and a guard must never mutate the very tree it is judging.
L523-525 | RULE | The direct-execution approach on main also answers the precise question the incident asked — what main ACTUALLY has right now — rather than what main would acquire the NEXT time something happened to run there.
L525-526 | RULE | `pyvenv.cfg` is deliberately not read as part of this comparison, because it merely describes an environment rather than being a live probe of one, and could be stale.
L528-530 | RULE | `_guard_interpreter_matches_main` is fail-closed: no `.venv` present in main, no `uv` on PATH, a non-zero return code from either probe, an empty answer, or an unparseable answer — every one of these refuses with exit code 14.
L530-532 | RULE | The version-shape sanity check (`_interpreter_version_is_sane`) exists to handle exactly one otherwise-dangerous state: both probes independently failing and returning `""`, which a naive `"" == ""` comparison would otherwise treat as legitimate agreement.
L532 | IDENT | `test_two_undeterminable_sides_refuse_rather_than_agree` was written to pin the both-sides-empty state.
L533-539 | HISTORY-LOADBEARING | (CB-140) That test stopped discriminating a mutant that disables the shape check, once the separate UV_PYTHON-outranks-the-pin logic existed alongside it — NOT because of any reordering (the shape check still runs first, in the same position it always has); rather, the pin-check ALSO independently refuses an empty `wt_ver` on its own (since `""` is unequal to the pin string and does not extend it with a dot), so with the shape check neutered by a mutant, execution simply falls through to the still-present pin check, which produces the identical exit-14 refusal via a different code path — this is the specific mechanism the correction (a new, separate test) exists to close.
L539-541 | MEASURED | Measured: a mutant turning `_interpreter_version_is_sane` into `return 0` left that specific test green, and left the entire 248-test harness suite green as well.
L541-547 | RULE | The one state the pin-check alone still cannot catch — where the shape check is the sole remaining backstop before CB-135 could recur — is a NON-version that PREFIX-MATCHES the pin string: e.g. a degenerate bare pin like `"3"` would accept anything spelled `"3."` plus more as if it were a legitimate patch release, so a stub value `"3.0"` on both sides clears the pin-equality check (since it looks like an extension of the pin) while still failing the strict `X.Y.Z` shape the sanity check demands — there, neutering the shape check is the only way to reach a false `wt_ver == main_ver` agreement and get exit code 0.
L548-550 | IDENT | `test_two_prefix_matching_non_versions_refuse_rather_than_agree` is what actually holds the version-shape check today; the older `test_two_undeterminable_sides_refuse_rather_than_agree` is kept only as a premise fixture (both probes genuinely absent) and is no longer sufficient by itself.
L552-553 | RULE | The interpreter guard also demands that the worktree carry its own `pyproject.toml`, and this requirement is deliberate design, not incidental tidiness.
L553-555 | WHY-NARROW | `uv run` resolves which project it belongs to by walking UP the directory tree, and every worktree lives INSIDE the main repository at `.worktrees/<slug>` — so a worktree missing its own `pyproject.toml` would silently resolve against MAIN's project instead of its own.
L554-556 | MEASURED | Measured: `uv run` executed from such a directory answered with main's interpreter AND imported the `codebugs` package from main's own `src/` directory.
L556-557 | RULE | Without the pyproject.toml requirement, the guard would end up comparing main against main — an agreement that can only ever hold, i.e. a gate that cannot fire, in the very change whose entire subject is preventing exactly that shape of defect.
L559-561 | RULE | The interpreter-match call is placed at phase `[5/7]`, strictly AFTER the forward-merge of main into the worktree (so a `.python-version` update arriving from main is already present before being judged) and strictly BEFORE phase `[6/7]` (the test run), so that a refusal costs only seconds instead of invalidating the whole ~70-second suite run after the fact.
L561-563 | RULE | The call sits OUTSIDE the `--skip-checks` branch, because that flag skips ruff and pytest (which are CHECKS), and this particular call is what decides whether running those checks would mean anything at all.
L563-564 | RULE | Both phase-ordering bounds are pinned structurally, and the `--skip-checks` placement is additionally pinned behaviourally by a test class that runs the finish script end to end.
L564-567 | RULE | The structural test asserting call order must anchor on the literal `git merge` invocation itself, not on the echo/announcement text that precedes it.
L565-567 | HISTORY-LOADBEARING | Review once moved the interpreter-match call to sit between the announcing echo and the actual `git merge`, and the first version of the ordering test stayed green — proving that "after the text announcing a merge is coming" is not the same assertion as "after the merge itself", which is why the anchor had to be the merge command specifically.
L569-570 | RULE | The interpreter match is asserted a SECOND time, inside the merge lock, because a pre-check performed before the lock is not itself an invariant guaranteed to hold at the actual moment of landing.
L570-571 | RULE | Main's `.venv` directory is gitignored, so `_guard_main_clean` cannot detect it moving, and the in-lock SHA re-checks only cover git commits, not the venv contents.
L571-573 | RULE | A `UV_PYTHON=… uv sync` run against main during the ~90-second suite run could land work that was tested on one interpreter onto a main that has since switched to a different one — this is exactly the kind of skew `TESTED_MAIN` exists to guard against, reached through the one piece of state neither guard otherwise observes.
L573-574 | RULE | The re-assertion happens at the point where nothing can intervene before the merge actually executes, and it must produce the same answer as the earlier check.
L574-576 | RULE | The in-lock re-check is implemented as a fresh, second CALL (~100ms cost) rather than reusing a stored sample from earlier, specifically so it cannot drift from the state it is meant to verify.
L576 | HISTORY | This second-assertion requirement was also found by cross-model review.
L578-580 | RULE | A shared `.venv` (main's `.venv` pointed at a worktree's) is explicitly refused by the guard, and the comparison performed is between the two RESOLVED DIRECTORIES.
L578-580 | WHY-NARROW | If main's `.venv` pointed at a worktree's, the two sides would become literally one environment that can only ever agree, and the worktree's removal at the end of the finish would then leave main's link dangling.
L580-582 | RULE | The comparison must specifically be between the resolved venv DIRECTORIES, never between the interpreter BINARIES they each happen to resolve to.
L581-582 | WHY-NARROW | Two independent, honest venvs built from the same underlying system python both resolve to a single identical `/usr/bin/pythonX.Y` binary, so an interpreter-level (rather than directory-level) comparison would falsely refuse every ordinary, non-shared case.
L584-585 | RULE | Bumping the interpreter pin is a mandatory two-step procedure, and the guard's own ordering makes that order enforced rather than merely advisory: a branch that changes `.python-version` is refused until main is brought onto the NEW interpreter first.
L586 | RULE | The required first step is `(cd <repo_root> && UV_PYTHON=<new> uv sync --extra dev)` run in main, followed by re-running the finish.
L586-587 | RULE | A bare `uv sync` with no `UV_PYTHON=<new>` override is not sufficient at that step, because it re-reads main's OLD pin file and restores the old interpreter unchanged.
L588-589 | RULE | The refusal message prints that exact repair command with both the old and new versions already filled in, on the principle that a gate with no way out is a wall rather than a diagnostic.
L591-593 | RULE | `worktree-finish.sh` runs from the repository root, so the copy of the script that lands any given change is MAIN's copy — which by construction does not yet contain a call that was only just introduced.
L593 | RULE | The commit that introduces the interpreter guard is therefore itself not gated by it; the first finish run AFTER that commit lands is the first one actually subject to the gate.
L594-595 | BOUNDARY | Unlike CB-57's hook-based bootstrap wall, no re-run of `tools/install-hooks.sh` is needed here, because this particular guard lives inside the finish SCRIPT itself, not in an installed git hook.
L597-599 | BOUNDARY | What the interpreter guard does NOT do: it is per-clone and client-side like the rest of the harness — it asserts nothing about which interpreter any OTHER machine or CI system actually used, only that these two local trees (main and the worktree) currently agree with each other.
L599-600 | BOUNDARY | It compares only a version STRING, so two different builds of the identical version string, built with different compile-time options, read as identical to it.
L600-602 | BOUNDARY | A `.python-version` naming a build this particular machine cannot obtain fails at the `uv run` step, which the guard then reports merely as "undeterminable" — a correct outcome, but the resulting error text is uv's own message rather than a diagnosis specific to the pin.
L602-604 | RULE | The pin value itself is required to be a plain `X`, `X.Y`, or `X.Y.Z`; uv separately accepts implementation- and platform-qualified requests (e.g. `pypy@3.11`, or a full `cpython-…-linux-…` triple), and rather than guess what such a value would resolve to, the guard refuses outright and states why.
L606-608 | RULE | `tools/worktree-setup.sh <type>/<slug> [base]` validates the branch name, refuses a card already carried by another branch, claims every card the branch names through the claims ledger, creates the `.worktrees/<type>-<slug>` directory, and primes the worktree's own dev environment.
L609-611 | HISTORY-LOADBEARING | (CB-58) This bullet used to describe the setup script as merely flipping an `open` card to `in_progress` — "a best-effort status write, not a claim" — which was an accurate description of the earlier, defective behaviour; the claim mechanism described in the surrounding text exists specifically as the fix to that defect, and is only meaningful stated against this earlier, now-superseded description.
L611-613 | RULE | The status flip described in the old text is not gone, it is SUBSUMED: it now arrives as the claim's own projection via `EntityKind.busy_status`, so the card still visibly reads `in_progress` while the branch holds the claim.
L613-616 | RULE | What is new: the write carries a holder TRIPLE (`--holder <branch> --holder-kind branch --repo <root>`); mutual exclusion is enforced by a partial unique index rather than by convention; and there is now a release path, including `_auto_release_on_terminal`, which releases the claim in the same transaction that closes the card.
L617-619 | RULE | Order is load-bearing: the claim must happen BEFORE `git worktree add`.
L617-619 | WHY-NARROW | This ordering matches `_guard_branch_type`'s own reasoning: otherwise the losing side of a race would already own a branch and a directory by the time it is told no.
L619-621 | RULE | Exit code `3` (held by someone else) is FATAL and prints the incumbent's holder triple; this call is the "setup gate" — the one tracker call in the entire harness that is permitted to abort the operation.
L621-622 | RULE | Exit code `4` (already resolved) only warns and proceeds, because starting a legitimate follow-up branch on an already-closed card is a normal, allowed action.
L622-624 | RULE | Exit code `5` (undetermined) triggers exactly one retry with the identical call, which converges rather than double-claiming because the underlying claim primitive is an idempotent upsert.
L624-626 | RULE | An EXIT trap releases whatever claim(s) the run took if setup aborts; it is armed only after the first successful claim, and it is DISARMED on overall success.
L625-626 | WHY-NARROW | Leaving that trap armed on success would make every setup run that actually WORKED release its own claim again on the way out, which would be wrong.
L626-627 | RULE | `CODEBUGS_SETUP_NO_CLAIM=1` still skips the tracker claim step entirely and is the documented, sanctioned escape hatch past an exit-`3` refusal.
L627-630 | RULE | `--allow-duplicate` deliberately does NOT bypass the same thing `CODEBUGS_SETUP_NO_CLAIM=1` bypasses.
L627-630 | WHY-NARROW | `--allow-duplicate` answers a different question — whether another BRANCH (not a claim) already carries the same card id — and since this repository never deletes merged branches, that flag is needed for ordinary follow-up work; folding claim-bypass into the same flag would make the claim gate routinely bypassed as a side effect of ordinary usage.
L631-632 | RULE | The branch-name collision check remains independent of the tracker and is still the half of the setup logic that works even with no tracker reachable at all, because it is pure git.
L632-634 | BOUNDARY | What this does NOT do, stated as the honest scope: a branch abandoned AFTER a successful setup still leaves a live, un-released claim behind.
L633-634 | BOUNDARY | Steal and expiry mechanisms for claims are deliberately deferred by design (see the Claims module section later in the document).
L634-636 | BOUNDARY | This is nonetheless strictly better than the anonymous `in_progress` status it replaced, since `codebugs who-holds` names the current holder and repo and any close of the card releases the claim — but it is not the same as the claim simply disappearing on abandonment.
L636-637 | RULE | Convention: one concern per branch; a card-driven branch carries that card's id in its own name (worked example: `fix/cb-48-tracker-root-init`).
L637-639 | RULE | Work already begun directly on main is moved into a proper worktree via `git stash push <files>` → run setup → `git stash pop` inside the new worktree; the stash itself is shared across all worktrees because it lives in the common git directory.
L640 | RULE | Worktrees live under `.worktrees/`, with the slug formed by taking the branch name and replacing `/` with `-`, matching the convention used by the sibling `autosorter` project.
L641-642 | RULE | Both `.worktrees/` and the legacy `.claude/worktrees/` path are gitignored; the legacy path still works and `worktree-finish.sh` resolves either location, but new worktrees are always created under `.worktrees/`.
L643 | RULE | All work on a branch happens in its worktree, entirely; the operator must check which checkout is current before any `Edit`/`Write` to a source file.
L644-645 | RULE | A surgical `git checkout <branch> -- <files>` performed while standing on main counts as directly editing main — "wearing a hat" (i.e. disguising the edit).
L645-646 | RULE | Merge conflicts must be resolved INSIDE the worktree, never by committing a conflict resolution directly on main.
L647-648 | RULE | Tests and lint must run inside the worktree, using its own environment: `uv run --extra dev python -m pytest tests/ -q`.
L648-649 | RULE | The `--extra dev` flag is NOT optional when running tests in a worktree.
L648-651 | WHY-NARROW | `pytest` and `ruff` live in `project.optional-dependencies`, which `uv run` does not install by default, so a fresh worktree fails with "No module named pytest" without the flag, while main (synced long ago) happens to work without it — meaning the plain commands documented later under the Testing section are correct for main but INCOMPLETE for a fresh worktree.
L651-653 | RULE | `uv run` does build the worktree's own editable install pointing at the worktree itself, so once `--extra dev` is included, the isolation between worktree and main is real.
L653-655 | RULE | Never validate a worktree's changes by running the test suite from main, because `pythonpath = ["src"]` resolves relative to whichever checkout you actually run the command in — so running from main tests main's OWN source and would pass even against a tree you never actually touched.
L655-657 | RULE | The mirror-image trap sits in the MCP-registration rules: from a worktree, invoking a bare `python` reaches the `codebugs` package through MAIN's editable install rather than the worktree's own, which is specifically why `tests/dump_schema.py` must be run with `PYTHONPATH=src` set explicitly.
L658-661 | RULE | `tools/worktree-finish.sh <slug> ['commit msg'] [--merge-msg '…']` commits any dirty state in the worktree, runs the guards, forward-merges main INTO the worktree (so conflicts surface in a safe, disposable space), runs `ruff check` and the full test suite there against the combined tree, then merges the result onto main with `--no-ff` under a lock, and finally removes the worktree.
L661-662 | WHY-NARROW | The resulting merge commit is what makes a card's whole iteration recoverable as one unit; a fast-forward merge would scatter that history instead.
L663-664 | RULE | The branch itself must NEVER be deleted — no merged branch has ever been deleted in this repository, and that is treated as the permanent record; the finish script removes only the WORKTREE, never the branch.
L665-667 | RULE | The integration commit message follows the fixed format `Merge <branch>: <what changed> (CB-NN)`.
L666-667 | RULE | When no explicit message is supplied, the subject is derived from `main..<branch> --first-parent --no-merges --reverse` — specifically the FIRST commit on the branch's OWN first-parent line among the commits main does not already have (CB-116).
L667-668 | HISTORY-LOADBEARING | This bullet used to say the subject was derived from "the branch and last subject", which was itself the defect being fixed; the correction stated here is meaningful only in contrast to that earlier, wrong description.
L668-671 | HISTORY-LOADBEARING | (CB-116) The old derivation ran `git log -1 --no-merges` on the worktree tip, which the forward-merge two steps earlier had just populated with main's own commits: landing CB-111 produced an integration merge whose subject actually named an unrelated plan note mentioning CB-113/114/115 instead of CB-111 itself — reproduced and fixed end to end in a throwaway repo, which is the concrete incident the whole derivation rewrite exists to prevent.
L672-674 | RULE | The defect was never a topological ordering issue — `git log` orders by commit date, so it only manifests when main's own commit is NEWER than the branch's last commit (the ordinary case), which is exactly why a test fixture whose commits all share one timestamp second stayed green against the underlying bug.
L675-680 | RULE | `--first-parent` in the derivation command is load-bearing, not decorative; merely restricting the commit RANGE without it is NOT sufficient.
L676-680 | HISTORY-LOADBEARING | The first draft of the fix did restrict only the range, and both adversarial reviewers independently reproduced a new regression from it: a branch that itself merges a SIBLING branch absorbs the sibling's commits into that range, and if the sibling happens to be older (the ordinary case), date ordering puts its commit first, so the derived subject would name the SIBLING's card instead — on that shape, the range-only fix was actually WORSE than the original `log -1` code it was meant to replace; only following first-parents correctly skips every absorbed lineage, including main's own forward-merge commits.
L681-685 | RULE | The FIRST commit of the branch's own first-parent line is used as the subject source, deliberately not the LAST.
L681-683 | MEASURED | Measured over main's own first-parent history: of the 47 integration merges whose branch carried two or more commits, the FIRST commit's subject was judged closer to what a human would actually have written in 38 cases, versus the LAST commit's subject in 7 cases.
L683-685 | BOUNDARY | That 38/7 split is a subjective JUDGEMENT and does not exhaustively partition all 47 cases (two are unclassified either way), and the split itself cannot be re-derived mechanically — only the total population of 47, and a subset of five commits following a `wip(cb-NN): checkpoint before …` naming pattern, reproduce mechanically.
L685-687 | RULE | Branches in this repository characteristically end on review-fixup commits (e.g. "close the altitude findings"), which describe an iteration's TAIL rather than its actual subject — this observed pattern supports using the first, not the last, commit.
L687-688 | RULE | Do NOT implement the derivation as `git log --reverse -1`, because git applies the `-1` count BEFORE reversing the order, so it silently returns the NEWEST commit and re-introduces the exact behaviour the fix was meant to remove.
L688 | MEASURED | The `--reverse -1` pitfall was verified by direct measurement, not merely reasoned about.
L689-692 | RULE | A branch with no commit of its own carrying a subject line is REFUSED outright rather than having a subject guessed for it.
L690-691 | WHY-NARROW | This state is reachable when all the content arrived purely through a merge commit, a case where `_guard_nonempty_diff` has already proven the content is genuinely real (i.e. this refusal is not conflated with the empty-diff refusal).
L691-692 | RULE | The subject derivation runs at the point where `TESTED_MAIN`/`TESTED_HEAD` are first sampled, rather than under the final merge lock, so this particular refusal costs nothing rather than wasting the whole ~70-second gate run.
L693-695 | RULE | The empty-population refusal check examines the entire POPULATION of candidate commits, not merely whether the FIRST line in the derived range is non-empty.
L693-695 | HISTORY-LOADBEARING | `git commit --allow-empty-message` places a blank line at the head of a commit's message; treating that blank first line as proof of an "empty population" produced a false refusal that also asserted something factually untrue about the repository's state — this is why the check now examines the whole population rather than just the first line.
L695-696 | RULE | The derivation cannot drift from what actually lands: both of its inputs are the pinned `TESTED_*` values, and the in-lock re-checks refuse with exit code 13 if either one has moved since sampling.
L697-699 | BOUNDARY | Rejected alternative: refusing to derive a subject at all whenever main has moved since the branch was cut.
L697-699 | WHY-NARROW | Rejected because level-(2) sessions commit plan notes to main continuously, making "main moved" the ordinary common case; that alternative would have converted an optional derivation into a mandatory manual argument on nearly every single finish, even though the correct subject was already sitting right there in the derivable range.
L700-705 | BOUNDARY | One limit stays open and is documented rather than silently guessed around: `worktree-setup.sh <branch> [base]` can cut a branch from a NON-MAIN base, whose own commits then sit on this branch's first-parent line, causing the derivation to name the BASE branch's first commit instead of the feature branch's own content.
L702-704 | MEASURED | Measured: neither `--first-parent` nor `--topo-order` avoids this, because the issue is not one of traversal ordering — those commits genuinely are this branch's own ancestry, and the merge genuinely does land them.
L705 | RULE | The documented workaround for a non-main-based branch is to pass `--merge-msg` explicitly rather than rely on derivation.
L706-707 | RULE | Every re-run hint printed by a refusal echoes back the `--merge-msg` value the original (aborted) run was given.
L706-707 | BOUNDARY | This echoing behaviour is orthogonal to the subject-derivation logic — it would be necessary even if the derivation logic were flawless.
L707-710 | HISTORY-LOADBEARING | The exit-13 "main moved" refusal used to print only the bare short-form retry command, which routed the operator straight back into the subject-derivation logic that main's own move had just invalidated — this is literally how the observed CB-111 wrong-subject incident described earlier was produced, and it is what motivates always echoing --merge-msg on retry.
L710-711 | IDENT | A single shared `_retry_hint` helper builds the retry line for all four distinct refusal paths: forward-merge conflict, main moved, branch moved, and merge failed.
L711-713 | RULE | A test refuses the exact literal string `echo "      tools/worktree-finish.sh ${SLUG}"` — pinning the specific spelling that had regressed — while a separate count of calls to the shared helper is what verifies correctness at the other three refusal sites.
L713-715 | RULE | The retry hint echoes back `--merge-msg` and deliberately nothing else: `--skip-checks` and `--allow-stale-base` are relaxations, so silently dropping them on a suggested retry makes the retry stricter (a safe direction), and the positional commit-message argument applies only to a still-dirty worktree, so it is not relevant to echo.
L716-717 | RULE | `ruff check` is the enforced lint gate; `ruff format` is deliberately NOT enforced as a gate.
L717 | WHY-NARROW | A large part of the existing tree is non-conformant to `ruff format`, so gating on it would refuse every single finish run.
L717-718 | RULE | ruff is pinned to version 0.15.7, because version 0.16.x flags the entire repository.
L719-720 | RULE | Session-end procedure: `git status` must be clean in main AND in every open worktree, then `git worktree remove <path>` is run for each.
L720 | RULE | `--force` must never be used on `git worktree remove`: a removal that refuses to proceed is a signal that work in that worktree is uncommitted.
L721-723 | RULE | The only things permitted to land directly on main are: a `.claude/plans/*.md` note (exactly one directory level deep, not a subtree), or, since CB-266, a `.claude/plans/briefs/*.html` daily brief (exactly one level under `briefs/`, likewise not a subtree of it, and no other file extension is permitted).
L723 | RULE | The pre-commit hook is what enforces this "only these two kinds of files may land on main" rule.
L724-725 | RULE | The plan note must be NAMED explicitly in the commit message, and staged by exact name: `git add -- .claude/plans/<note>.md`, never the directory form `git add .claude/plans/`.
L726-727 | RULE | The commit-msg hook refuses a plan-note commit whose message does not name the note — this is the mechanised enforcement of the naming convention (see the earlier Workflow paragraphs in this document for why naming, rather than some other signal, is the discriminator used).
L727-729 | RULE | `git commit --no-verify` remains the escape hatch for both the pre-commit and commit-msg hooks: they exist to stop accidental violations, and an operator who explicitly types that flag has stated a deliberate intent to bypass them.
L731-733 | RULE | `tests/test_worktree_harness.py` covers every individual guard on both sides — both the state it must refuse and the state it must allow — AND, separately, asserts that `worktree-finish.sh` actually CALLS each guard.
L734-736 | HISTORY-LOADBEARING | That second class of "wiring" test exists because it had to: two separate adversarial review rounds managed to delete guard INVOCATIONS from the finish script (including the branch-type guard that exists specifically because of the 2026-08-16 incident) while the entire test suite stayed green, because nothing in the suite actually executed the script itself — this is the concrete failure the wiring tests exist to prevent.
L736-738 | RULE | Every individual guard was unit-tested, but their COMPOSITION (correct wiring into the script) was not — an instance of this repository's own general principle that a check validating individual elements cannot thereby validate their composition, applied here to the test harness itself.
L738 | RULE | The per-guard unit tests must not be mistaken for coverage of the wiring; they are two separate concerns.
L740-745 | HISTORY-LOADBEARING | `TestKnownLimits` (the test meant to pin the one documented residual limit of this whole design) passed the literal argv token `"--git-path hooks"` (as one single argument, with the space embedded) to `git rev-parse`; `git rev-parse` echoes back an unrecognised option-looking argument and exits 0, so the resolved "hooks directory" became a relative path literally named `--git-path hooks`, the hook file was copied into a directory of that literal name inside the repo root, the test's own fixture repo ended up with NO real hook installed at all, and the test's `rc == 0` assertion could therefore never fail — it stayed green even with the entire merge hook fully reverted.
L745-747 | MEASURED | Worse, the test suite's run COMMITTED that resulting 11 KB directory to the branch, and `git status` stayed reported clean because every subsequent test run regenerated the exact same bytes.
L747 | HISTORY | This defect was found by round-3 human review, not caught by the automated test suite itself.
L747-749 | RULE | Two general lessons drawn from this incident: a test that sets up its own fixture must explicitly ASSERT that the fixture was actually created as intended, and `git rev-parse` is not a safe place to be careless with argv formatting, since it silently echoes back unrecognised arguments rather than erroring on them.
L751-753 | RULE | Executing the entire `worktree-finish.sh` script inside a test is impractical, because doing so merges onto main and runs the full test suite; the wiring tests are therefore STRUCTURAL — they read the script's source text and assert each guard is invoked with `|| exit $?`, in the correct phase of the script.
L751-753 | RULE | It is stated plainly that these structural tests are structural rather than behavioural, rather than being left to appear behavioural.
L753-754 | RULE | The claim that end-to-end execution is "impractical" is narrower than it may read — it does not mean no behavioural testing of the script is ever possible.
L754-757 | IDENT | `TestMergeSubjectDerivation` (CB-116) is the counter-example: it runs `worktree-finish.sh` genuinely end to end in a disposable throwaway repository under `--skip-checks` (which disables ruff and pytest but explicitly NOT the safety guards), and the resulting merge really does land onto that throwaway repo's own main branch.
L757-759 | RULE | A property of the SCRIPT'S OUTPUT (specifically, the commit subject it writes) can therefore be tested behaviourally, and had to be, because the CB-116 defect was invisible to every purely structural test — the defective code did call `git log`, which is exactly the kind of call a structural read-the-source test would be looking for as evidence of correctness, so it could not distinguish correct usage from defective usage.
L759-761 | RULE | The behavioural fixture also caught something structural reading alone could not: the sibling-branch-absorption regression discussed earlier was found by human review, but it is the behavioural fixture that now permanently holds that particular line.
L761 | BOUNDARY | What remains genuinely impractical to test is the full GATE RUN itself (the real lock, merge, and suite pipeline against the actual main), not the script's overall testability.
L761-765 | RULE | Three additional structural tests landed together with CB-57, all of the same general kind: (1) the integration merge command must NOT carry `--no-verify`; (2) the installer script must arm the merge hook and point it specifically at main's own checkout; (3) the CI workflow must carry a baseline SHA that is verified to be a real, existing commit in this repository.
L764-765 | WHY-NARROW | Each of these three pins a property whose failure mode would otherwise be silent: a gate that is textually present in the tree but has no actual effect.
L767-770 | RULE | The branch-type predicate is constructed FOUR separate times, spread across THREE different files: once in `_guards.sh`, twice within `pre-commit-hook.sh` (its own direct branch check, plus a duplicate copy embedded in the shared merge-gate block), and once in `pre-merge-commit-hook.sh`.
L769-770 | WHY-NARROW | This duplication is necessary because neither hook script may `source` the shared guard library — each git hook runs from `.git/hooks/` as a symlink and must still work correctly even in a checkout where the `tools/` directory is entirely absent.
L770-774 | HISTORY-LOADBEARING | This sentence used to say "three copies" (counting FILES rather than construction SITES), and round-3 review demonstrated the practical consequence: degrading `pre-commit-hook.sh`'s own standalone branch-type regex to a mere prefix test still left the old counting test GREEN, because the shared block's separate embedded copy still satisfied the grep — which is why the count was corrected to be per construction site rather than per file.
L774-776 | RULE | "Accepting the same set of branch-name TYPES" is not the same guarantee as "implementing the same PREDICATE": a mere prefix test would wrongly accept a name like `fix/a/b`, which the real `_guard_branch_type` correctly refuses — so a divergence among the four constructions could let a branch clear the finish-time guard and only be refused later by the merge hook, after the entire test suite had already run to completion (wasted effort).
L778-780 | RULE | Code being byte-identical across two hooks is NOT automatically the same claim as "the two hooks agree" on outcomes — this distinction cost an extra review round to fully learn.
L778-780 | HISTORY-LOADBEARING | The shared predicate used to accept a second argument — what the caller had literally typed, sourced from the git `GITHEAD_` environment variable — and would judge that literal ref alone whenever it happened to resolve; this was identical CODE implementing two DIFFERENT rules in practice, because only the `pre-merge-commit` caller actually has that second argument available to pass.
L781-783 | MEASURED | Review reproduced: `git branch fix/tmp <untyped-sha>; git merge fix/tmp --no-ff` landed cleanly (bypassing the check) on the clean-merge path, while the identical underlying repository state was correctly refused on the conflicted-merge path — and the byte-identity test could not structurally detect this divergence, because the actual difference lived in the ARGUMENTS supplied to the shared function, not in the shared function's own source code.
L784 | RULE | The fix: the shared predicate now takes ONLY the merge head as its input, so both callers are guaranteed to supply identical information to it.
L784-786 | RULE | General lesson stated explicitly: sharing one implementation does not guarantee sharing one decision, if the different callers of that implementation supply different inputs to it.
L788-790 | RULE | Cherry-pick and revert operations lost their prior exemption from the pre-commit hook's checks, but the honestly-stated scope of that change is narrower than simply "they are now refused".
L789-793 | HISTORY-LOADBEARING | The merge-in-progress exemption used to trigger on the MERE EXISTENCE of any of `MERGE_HEAD`, `CHERRY_PICK_HEAD`, or `REVERT_HEAD`; only the `MERGE_HEAD` case had actually been hardened against being an empty marker, and review reproduced the identical bypass through the other two: creating an empty `.git/CHERRY_PICK_HEAD` file followed by `git commit` landed arbitrary staged content on main AND simultaneously skipped the branch-type check — one empty marker file disabled both of the hook's rules at once, reachable the same way the earlier empty-`MERGE_HEAD` bug was, since a conflicted cherry-pick leaves that marker file present until `--continue` or `--abort`.
L794 | RULE | The actual fix applied was simply to stop exempting `CHERRY_PICK_HEAD` and `REVERT_HEAD` from the checks at all.
L796-798 | RULE | A CLEAN (non-conflicted) `git cherry-pick` or `git revert` performed directly on main never reaches the `pre-commit` hook at all, because git's own sequencer commits directly in that case — so it still lands completely unguarded, a fact verified by actually running both commands.
L797-800 | HISTORY-LOADBEARING | An earlier draft of the documentation for this row claimed cherry-pick and revert now get NO exemption on main and exit 1 — which is itself the identical "gate that cannot fire" category error this same section separately corrects for `main-invariants.yml`, and that wrong claim was committed into the very same table just two rows apart from the correction it contradicts.
L800-802 | RULE | What the actual fix buys is narrower than the earlier draft claimed: a MARKER FILE (`CHERRY_PICK_HEAD`/`REVERT_HEAD`) can no longer be used to launder an unrelated commit past the hook's checks.
L801-802 | BOUNDARY | A clean cherry-pick or revert directly onto main is caught only by the CI alarm (`main-invariants.yml`), since it leaves behind a single-parent (non-merge) commit on main's first-parent line — and that CI check, as established earlier, is only an alarm, never a gate that can refuse the push.
L804-806 | RULE | The fail-closed validation of `MERGE_HEAD`'s contents is NOT scoped to main only, because the exemption logic it guards against is not scoped to main only either.
L805-806 | MEASURED | While the validation logic effectively behaved as main-scoped in an earlier version, an empty `.git/MERGE_HEAD` on an UNTYPED, non-main branch still skipped the branch-type check there too — demonstrating the underlying bypass was not confined to main.
L806 | RULE | Only the head-ACCEPTABILITY rules specifically (a properly typed local branch, or upstream `main`) are actually about main; the fail-closed validation of MERGE_HEAD's contents itself applies universally to any branch.
L808-810 | RULE | The bootstrap constraint is a genuine, unavoidable limitation, not an oversight: `worktree-finish.sh` cannot itself land the very commit that first creates the `tools/` directory, because `_guard_enforcement_armed` refuses — main has no `tools/pre-commit-hook.sh` yet for the newly-installed git hook to symlink to and point at.
L810-812 | HISTORY-LOADBEARING | (CB-50) CB-50 was therefore merged onto main by hand exactly once, using `git merge --no-ff` directly, after the automated harness had already run its full pipeline on the branch and could only refuse at the final lock step — this incident is the origin of the "expect a one-time manual merge if tools/ is ever rewritten" guidance stated at the end of this section.
L812-815 | HISTORY-LOADBEARING | (CB-57) CB-57 hit the identical wall in a smaller form and, this time, was deliberately DESIGNED AROUND it rather than merged by hand: `_guard_enforcement_armed` runs BEFORE the merge that first places `tools/pre-merge-commit-hook.sh` onto main, so an unconditional file-existence check would have made the very commit introducing that hook unlandable by the harness it was meant to extend.
L815-816 | RULE | The bootstrap-detection condition used by the guard must be MONOTONIC (i.e. it must never flip back to "unarmed" once armed).
L816-819 | HISTORY-LOADBEARING | The obvious naive condition — "does the hook source file currently exist" — was itself a live defect: a single `rm tools/pre-merge-commit-hook.sh` both silently dangled the already-installed hook symlink (git skips a dangling hook with no warning) AND made the guard's own check pass (return 0) as if properly armed — a permanent, flagless disarm reachable on a perfectly correctly-typed branch, reproduced end to end independently by both reviewers.
L819-821 | RULE | The gate condition is now instead whether the hook source path HAS HISTORY on main — a property that simply deleting the file cannot undo — so after CB-57 the enforcement check is genuinely unconditional, and a currently-missing source file now reports as "cannot verify the hook's identity" rather than silently vanishing from detection.
L821-823 | RULE | The operational consequence of this design: `tools/install-hooks.sh` must be re-run immediately after such a bootstrap merge lands, or the very next finish run will refuse — correctly so, since a clone armed before CB-57 landed really is missing part of its intended enforcement.
L824-826 | RULE | Every subsequent landing after that bootstrap point goes through the full automated harness as normal; if the `tools/` directory is ever rewritten in the same disruptive way again, the same one-time manual merge exception should be expected and applied again.

## VERBATIM-CRITICAL

- `origin/main` — L404
- `enforce_admins` — L423, L426
- `allow_force_pushes` — L427
- `allow_deletions` — L427
- `required_linear_history` / `required_signatures` / `lock_branch` — L428
- `gh api repos/faxik/codebugs/branches/main/protection --jq keys` — L420
- `required_pull_request_reviews` — L421
- `required_status_checks` — L421
- `gh api repos/faxik/codebugs --jq .permissions.admin` — L429
- `gh api repos/faxik/codebugs/rulesets` — L430
- CB-59 — L408, L413, L436
- `if: github.event_name != 'pull_request'` — L440
- `push: branches: [main]` — L446
- `fetch-depth: 0` — L448, L451, L459-460
- `test_ci_workflow_asserts_the_first_parent_invariant` — L454
- `test_ci_suite_job_checks_out_the_history_its_own_suite_reads` — L458
- CB-139 — L452
- `with:` (YAML input) — L464
- `if: ${{ false }}` — L467
- `contracts` (job name) — L463, L470
- `tests/test_cli_signals.py` — L470
- `tests/test_fsio.py` — L471
- `.python-version` — L473, L560, L585, L600
- `_guard_interpreter_matches_main` — L474, L528
- CB-135 — L475, L480, L533, L547
- `UV_PYTHON` — L476, L479, L514-515, L533-534, L586
- `--python` — L476
- "1943 passed" — L483
- "1 failed, 1942 passed" — L485
- 3.13.3 — L484, L489, L511
- 3.14.4 — L485, L497, L501, L505-506, L511
- `pythonpath=["src"]` — L487
- `cpython-3.14.4-linux-x86_64-gnu` — L506
- `uv python list` — L507
- `--all-versions` — L508
- "1949 passed" — L499
- 3.11.12 / 3.12.10 — L500
- CB-134 — L499
- "Removed virtual environment" — L511
- `uv run --extra dev python -c <probe>` — L519
- `<repo_root>/.venv/bin/python` — L522
- `pyvenv.cfg` — L525
- exit 14 — L529-530, L539
- `_interpreter_version_is_sane` — L530, L541
- `test_two_undeterminable_sides_refuse_rather_than_agree` — L532
- CB-140 — L534
- 248-test harness suite — L541
- `test_two_prefix_matching_non_versions_refuse_rather_than_agree` — L548
- `pyproject.toml` — L552
- `.worktrees/<slug>` — L554, L640
- `[5/7]` — L559
- `[6/7]` — L561
- `--skip-checks` — L562, L714, L755
- `TESTED_MAIN` — L573, L691
- `UV_PYTHON=<new> uv sync --extra dev` — L586
- CB-57 — L594, L761, L812, L819, L821-822
- `tools/worktree-setup.sh <type>/<slug> [base]` — L606
- CB-58 — L609
- `EntityKind.busy_status` — L612
- `--holder <branch> --holder-kind branch --repo <root>` — L614
- `_auto_release_on_terminal` — L616
- `_guard_branch_type` — L618, L775
- exit code 3 — L619
- exit code 4 — L621
- exit code 5 — L622
- `CODEBUGS_SETUP_NO_CLAIM=1` — L626
- `--allow-duplicate` — L627
- `codebugs who-holds` — L635
- `fix/cb-48-tracker-root-init` — L637
- `git stash push <files>` — L637
- `.worktrees/` — L640
- `.claude/worktrees/` — L641
- `uv run --extra dev python -m pytest tests/ -q` — L647-648
- `--extra dev` — L648
- `PYTHONPATH=src` — L657
- `tests/dump_schema.py` — L656-657
- `tools/worktree-finish.sh <slug> ['commit msg'] [--merge-msg '…']` — L658
- `--no-ff` — L661
- `Merge <branch>: <what changed> (CB-NN)` — L665
- `main..<branch> --first-parent --no-merges --reverse` — L666
- CB-116 — L667, L754, L758
- CB-111 — L670, L709
- CB-113/114/115 — L671
- `--first-parent` — L675, L703
- `--reverse -1` — L687
- `_guard_nonempty_diff` — L690
- `TESTED_MAIN`/`TESTED_HEAD` — L691
- exit code 13 — L696, L708
- `--merge-msg` — L700, L705, L706, L713
- `wip(cb-NN): checkpoint before …` — L685
- `_retry_hint` — L710
- `echo "      tools/worktree-finish.sh ${SLUG}"` — L712
- `--allow-stale-base` — L714
- `ruff check` — L716
- `ruff format` — L716
- ruff 0.15.7 — L717
- ruff 0.16.x — L718
- `git worktree remove <path>` — L719
- `--force` — L720
- `.claude/plans/*.md` — L722
- `.claude/plans/briefs/*.html` — L722
- CB-266 — L722
- `git add -- .claude/plans/<note>.md` — L725
- `git add .claude/plans/` — L725
- `git commit --no-verify` — L728
- `tests/test_worktree_harness.py` — L732
- `TestKnownLimits` — L740
- `"--git-path hooks"` — L741
- `rc == 0` — L744
- 11 KB directory — L746
- `TestMergeSubjectDerivation` — L754
- `_guards.sh` — L767
- `pre-commit-hook.sh` — L768
- `pre-merge-commit-hook.sh` — L769
- `GITHEAD_` — L779
- `git branch fix/tmp <untyped-sha>; git merge fix/tmp --no-ff` — L781
- `MERGE_HEAD` — L789, L805
- `CHERRY_PICK_HEAD` — L789, L791
- `REVERT_HEAD` — L789
- `: > .git/CHERRY_PICK_HEAD` — L791
- `: > .git/MERGE_HEAD` — L805
- CB-50 — L810
- `git merge --no-ff` — L811, L661(pattern)
- `tools/install-hooks.sh` — L821-822
