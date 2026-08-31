# Rationale — `## Workflow` Biography for the rules in `CLAUDE.md`'s Workflow section: review rounds, reproduced
incidents, rejected forms and the measurements a decision was made on. **No rule lives here.**
A line in this file that reads as an instruction is a defect, and its place is the rules layer.

### The CI job's own limits — `main-invariants.yml` {#пределы-ci-задачи} **Justifies the rule** "The CI job's own limits", `CLAUDE.md` → `## Workflow`.
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

Measured 2026-08-22 (UTC 09:24): `gh api repos/faxik/codebugs/branches/main/protection` returns `"enforce_admins":{"enabled":true}` beside `"allow_force_pushes":{"enabled":false}` and `"allow_deletions":{"enabled":false}` (`required_linear_history`, `required_signatures`, `lock_branch` all `false`); `gh api repos/faxik/codebugs --jq .permissions.admin` returns `true`; and `gh api repos/faxik/codebugs/rulesets` returns `[]`, so the branch rule is the whole of the
server-side protection and it now binds the owner too.

Re-measured 2026-08-22 on the open residual:
`gh api repos/faxik/codebugs/branches/main/protection --jq keys` returns no `required_pull_request_reviews` and no `required_status_checks` key, so that residual is unchanged.

**The 2026-08-21 measurement is not an error in this document's history**; it was the state on that
date, and it is what CB-59 and the DIR-1 acceptance record (Э-9) describe. The rule deliberately
says that a later measurement, not the paragraph, is the authority.

**Item 5.** The workflow used to subscribe to `pull_request`, guarded by `if: github.event_name != 'pull_request'`. Both reviewers caught the defect independently. The first version of that test asserted only the negative half, so deleting `push: branches: [main]` outright left the suite green
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

**Rejected forms, with their reasons.** Making the merge name the pinned `TESTED_HEAD` closes only the branch half and pays a false refusal, since `pre-merge-commit` refuses a head with no ref. And a real compare-and-swap needs `git update-ref` over `commit-tree`, which this repo's own CI alarm
treats as a hook-bypassing shape.

**How `merge.ff=false` was verified.** By replaying the incident in a throwaway repo: default config gives `Fast-forward` and zero merge commits; `merge.ff=false` gives a merge commit. The two limits
recorded beside the rule exist because the first draft of that sentence overstated it.

---

### The guards that had to be re-read fail-closed {#сторожа-читают-fail-closed}

**Justifies the rules** "`MERGE_HEAD` is read fail-closed", "`core.hooksPath` can make `_guard_enforcement_armed` lie", "The bootstrap gate's condition must be MONOTONIC" and "Every reader of the staged set passes `-c core.quotePath=false`" — `CLAUDE.md` → `## Workflow`. **`MERGE_HEAD`: both states were reproduced, and neither typed `--no-verify`.** The empty file let
arbitrary staged content land on main with no merge at all; the file with no trailing newline landed
a real two-parent merge of an untyped branch. `pre-commit` was the one place left failing open in a change that had already hardened the CI job and the `pre-merge-commit` hook. **`core.hooksPath`: what round 3 reproduced.** `git config core.hooksPath <empty-dir>` left the guard returning `0` while nothing was installed, and a commit of arbitrary content on main then succeeded.
The relative-value half was reproduced too: armed in the primary, main checked out in a linked
worktree with no `.githooks` there, guard `0`, source commit onto main `0`. The `--type=path` detail
came from a third failure, where the same function was resolving the same setting through
`--git-path` two lines earlier — one setting, two answers. **The bootstrap gate took three attempts.** It first gated on the file existing, so one `rm tools/pre-merge-commit-hook.sh` was a permanent silent disarm (round 2). It then read the literal ref `main`, which any clone with no local
main collapsed straight back (round 3). Round 4 reproduced the full disarm once more through the
promisor-remote error path. The claim "no checkout shape can hide the history" was true and still
insufficient — the hole had moved from a checkout shape to an error path, which is the third distinct
door onto the same defect.

**The non-ASCII refusal was the mirror image of a bug this repo already had**, the same default
having once made a guard silently accept what it was there to refuse.

---

### CB-57 — the merge gate {#cb-57-гейт-мержа}

**Justifies the rules** "The merge gate is keyed on `GITHEAD_`", "`GITHEAD_` is not always a NAME",
"The sanctioned-type rule governs LOCAL branches" and the two limits recorded with them —
`CLAUDE.md` → `## Workflow`.

**CB-57's own prescription turned out to be wrong, verified by running it.** The card said to
validate "the branch behind `MERGE_HEAD`" in a `pre-merge-commit` hook, and on git 2.53 that file
does not exist on a clean merge at all.

**A cross-model disagreement settled by measurement.** One reviewer asserted that `GITHEAD_` carries a `branch 'main' of <url>` description when the merge head comes from a pull. It does not, on this
version — the other reviewer reproduced the raw OID, and so did I. Measured, not argued. The first
draft therefore refused every pull, while its own comment and this section both promised pulls were
fine.

**The remote-ref rule cost two bugs in the same three lines.** Requiring all refs to qualify refused
a real `git pull` whenever upstream had another branch cut at that commit; the `refs/remotes/<r>/HEAD` alias then disqualified the very pull the fallback existed for. Both were
reproduced, and the second was caught by this repo's own test minutes after the first fix.

**The trusted-ref scope was narrowed twice under review.** It first trusted **any** `<remote>/main`, then any **configured** remote's — at which point `git remote add junk <anything>` plus a fetch was
still a two-command bypass. Only main's declared upstream counts now.

**The local-branch rule survived three review rounds** before reaching the form recorded beside it.

---

### The shared predicate, and the honest scope {#общий-предикат-и-честная-область}

**Justifies the rules** "Two of the three hooks share a predicate", "What this does NOT do" and
"`install-hooks.sh` sets `merge.ff=false` before anything arming-related can abort" — `CLAUDE.md` → `## Workflow`.

**Why the byte-identity test compares blocks verbatim.** The earlier substring test was shown
insufficient by rewriting the merge hook as a prefix test while leaving the regex assignment in
place, which kept it green.

**Why `git subtree add` is in the bypass list.** Round-4 review landed content on main with it while
the list did not mention it.

**The paragraph that listed what the harness does NOT do was itself wrong about one entry.** Its
first draft claimed a clean cherry-pick or revert *does* run `commit-msg`, so the plan-note naming
rule fired on it. Measured on git 2.53: it does not. The claim was written into the very paragraph
that exists to list what the harness cannot do, and it survived a green suite because nothing pinned
it — a gate described better than it behaves, in the section whose subject is exactly that.

**The installer ordering was reproduced in review and verified fixed by running it. What was
reproduced, since the rule states only that the order is load-bearing:** with `merge.ff=false` set
LAST, a clone missing `tools/pre-merge-commit-hook.sh` — an older main, a `git checkout
<old-commit>`, the CB-57 bootstrap window itself — armed the pre-commit hook, printed its tick, then
exited 1 at the merge-hook step and left `merge.ff` **unset**. The installer could therefore skip
the one mechanism no hook can replace, and skip it while reporting a tick: git fires no hook on a
fast-forward, so nothing catches the omission afterwards either.

**Re-measured 2026-08-31 (T-132) against the CURRENT order**, in a throwaway clone carrying every
tool file except the merge hook: `[1/4]` sets `merge.ff=false`, `[2/4]` symlinks the pre-commit
hook, `[3/4]` prints `✗` and exits 1, `[4/4]` is never reached — so afterwards `merge.ff` is
`false`, `pre-commit` is armed, and `pre-merge-commit` and `commit-msg` are not. **Two of the three
things the historical failure did are therefore still live and only the third was removed**, which
is why the rule is worded as a contrast rather than as a denial: a flat "such a clone cannot arm the
pre-commit hook, exit 1 and leave `merge.ff` unset" is true only if the negation is read over the
whole conjunction, and false on the reading that takes the three separately. An earlier draft of
that line read "a step that cannot fail goes first", and review pointed out that four fallible
commands precede it, which is why the rule now states the precise claim instead.

---

### T-23 — naming a plan note in the commit message {#t-23-именование-заметки-плана}

**Justifies the rules** "A plan note landing on main must be NAMED in the commit message" and the
five that follow it — `CLAUDE.md` → `## Workflow`.

**Why it stopped being prose.** The convention was adopted and broken again; a convention broken four
times after adoption is this section's own opening lesson.

**Neither auto-generated source was foreseen when the rule was specified.** git's default template
would have passed every editor-based commit vacuously, and `git commit -v` would have satisfied the
gate from its own diff.

**The matcher hole, reproduced by cross-model review.** With `a b.md` and `b.md` both staged and only `a b.md` named, the stranger's note landed unnamed — measured: rc=0, both files committed.

**The cost of refusing odd basenames was measured before it was accepted.** 0 of this repo's 94 plan
notes carried a space or ASCII punctuation outside `[A-Za-z0-9._-]`; the convention is already ASCII
slugs.

**The bootstrap wall, for the third time.** `_guard_enforcement_armed` reads `REPO_ROOT/tools/<hook>`
from the PRIMARY checkout and gates on whether the path has history, so adding the clause in the same
change that introduced the source would have made that change unlandable by the harness it extends —
and `install-hooks.sh` could not pre-arm it either, because it symlinks into main's `tools/`, where
the file did not exist yet. So the hook landed first, armed by the installer alone, and the guard
followed once `tools/commit-msg-hook.sh` had history on main. The paragraph this replaced said the
opposite, for a reason that was true at the time.

---

### CB-135 / CB-140 — pinning the interpreter {#cb-135-закрепление-интерпретатора}

**Justifies the rules** "`.python-version` is the SINGLE SOURCE for the interpreter" and the eleven that follow it — `CLAUDE.md` → `## Workflow`.

**The incident, 2026-08-22.** A manager reported "1943 passed" from a worktree on Python 3.13.3
while the same suite on the landed main, under the documented command, gave "1 failed, 1942 passed"
on 3.14.4. The red was on main BEFORE the merge, and no finish could ever have seen it.

**The second, unnamed variable.** The rule "never validate a worktree's changes from main", because
`pythonpath=["src"]` resolves against the checkout you run in, is correct for its own reason — and it
is exactly what introduced *which python*. Before the pin there were three untracked trees: main took
the system interpreter its `.venv` was built with, a fresh worktree took uv's default (the newest uv-MANAGED install, which is a different thing), and `.github/workflows/ci.yml` named no version at all. `uv.lock` had already fixed the *dependency* versions, which is what made the interpreter the
conspicuous remaining one — it is **not** true that everything else is nailed down, and an earlier
draft of that sentence said so. uv's own version, the platform, and the BUILD of a given CPython all
still vary.

**Why 3.14.4 and not another version.** It is what main already ran, so landing the pin moved no
environment and opened no window in which main was stale. The suite is green on it (1949 passed,
measured on main after CB-134 landed) and equally green on 3.11.12, 3.12.10 and 3.13.3, so no red
version forced the choice, and the newest stable CPython is where the tested surface should sit.
Measured: `cpython-3.14.4-linux-x86_64-gnu` is `<download available>`, which is what makes the pin
reachable in CI.

**The UV_PYTHON clause was a cross-model review finding**; the first draft had only the existence
check.

**How uv behaves, measured.** With `.venv` at 3.13.3 and the pin at 3.14.4, a plain `uv run` printed "Removed virtual environment", recreated it and ran — no `uv sync` needed. The design brief had
assumed the opposite.

**Why the older fail-closed test stopped discriminating its mutant (CB-140).** The reason is NOT that
the checks were reordered: the shape check on `wt_ver` still runs first, exactly where it always did,
and the pin check sits further down, unchanged in position. What changed is that the pin check ALSO
refuses an empty `wt_ver` on its own — `""` is unequal to the pin and does not extend it with a dot —
so with the shape check neutered by a mutant, execution falls through to the still-present pin check
(both probes failing with `"" == ""` on each side is the state it was meant to catch, and the final
comparison it guards is `wt_ver == main_ver`), which independently produces the same exit 14. The test asserts only the return code, so it
cannot tell which of the two refused: measured, a mutant turning `_interpreter_version_is_sane` into `return 0` left that test, and the entire 248-test harness suite, green. **The `pyproject.toml` requirement was measured.** `uv run` from a worktree missing that file answered with main's interpreter **and** imported `codebugs` from main's `src/`. **The phase anchor was earned in review.** A reviewer moved the call between the `git merge` and the
echo announcing it, and the first version of the structural test stayed green.

**The in-lock second assertion was also found by cross-model review.**

---

### How the harness itself is tested {#как-проверяется-сам-харнес}

**Justifies the rules** "How the harness itself is tested, and where that stops", "The branch
predicate is constructed FOUR times across THREE files" and "Byte-identical is not the same claim" —
`CLAUDE.md` → `## Workflow`. **A test can be worse than absent: it can be vacuous AND leave litter.** `TestKnownLimits` — the pin for the one limit this design chose to document — passed `"--git-path hooks"` to `git rev-parse` as a single argv token. `rev-parse` echoes an unrecognised option-looking argument back and exits 0, so
the "hooks directory" resolved to a *relative path with that literal name*, the hook was copied into
a directory called `--git-path hooks` in the repo root, the test repo got no hook at all, and asserting `rc == 0` could never fail. It stayed green even with the entire merge hook reverted. Worse, the suite **committed** that 11 KB directory to the branch, and `git status` stayed clean
because every run regenerated it byte-identically. Found by round-3 review, not by the suite.

**Why the predicate count moved from files to sites.** The sentence used to say "three copies", which
was the *file* count, and the test it credited counted per file too. Round-3 review showed the
consequence: degrading `pre-commit-hook.sh`'s own regex to a prefix test left that test **green**,
because the shared block's copy still matched the grep.

**What the arguments-divergence round cost.** Review reproduced `git branch fix/tmp <untyped-sha>; git merge fix/tmp --no-ff` landing on the clean path while the identical state was refused on the
conflicted one.

**What the behavioural fixture caught that structural reading could not.** The sibling-branch
regression in the merge-subject derivation was found by review, but it is the behavioural fixture
that holds the line.

---

### Marker-file exemptions and the bootstrap wall {#исключения-маркеров-и-бутстрап}

**Justifies the rules** "Cherry-pick and revert have no marker-file exemption", "The same fail-closed
validation is NOT scoped to main" and "The bootstrap is a real constraint" — `CLAUDE.md` → `## Workflow`. **What review reproduced.** Only `MERGE_HEAD` had been hardened. `: > .git/CHERRY_PICK_HEAD` then `git commit` landed arbitrary staged content on main **and** skipped the branch-type check.

**The row that described a gate that cannot fire.** An earlier draft read "cherry-pick / revert get
**no** exemption on main … exit 1" — the identical category error this section corrects for
`main-invariants.yml`, committed in the same table two rows apart. **CB-50 was merged by hand once**, with `git merge --no-ff`, after the harness had run its whole
pipeline on the branch and refused at the lock — the first bootstrap wall.

**CB-57 hit the same wall in miniature and was designed around it rather than merged by hand.**
`_guard_enforcement_armed` runs *before* the merge that first puts `tools/pre-merge-commit-hook.sh`
on main, so an unconditional check would have made the commit introducing the hook unlandable by the
harness it extends. That is why the condition had to be the monotonic one; **its three attempts are
recorded once, under `#сторожа-читают-fail-closed` above, and are not repeated here** — the same
reason the rule itself is stated at one site. The half this
section owns is the one the bootstrap wall turns on: gating on the file EXISTING meant one
`rm tools/pre-merge-commit-hook.sh` both dangled the installed hook (git skips a dangling hook
silently) **and** made the guard skip its check and return 0, so the wall would have been got past
by disarming the thing it protects — reproduced end to end by both reviewers.

---

### CB-58 and CB-116 — the working procedure {#cb-58-и-cb-116-порядок-работы}

**Justifies the rules** "Create", "The integration message follows …" and "Every re-run hint echoes
back the `--merge-msg`" — `CLAUDE.md` → `## Workflow`. **CB-58: the claim used to be no claim at all.** The Create bullet read "flips an `open` card to `in_progress` … a best-effort status write, not a claim", which was accurate then and is the defect
that was fixed. The status flip is not gone, it is *subsumed* — it now arrives as the claim's
projection — while mutual exclusion moved from nobody to the partial unique index.

**CB-116: the incident.** The old derivation read `git log -1 --no-merges` on the worktree tip, which
the forward-merge two steps earlier had just filled with main's commits: landing CB-111 produced a
merge closing CB-111 whose subject was an unrelated plan note naming CB-113/114/115. Reproduced end
to end in a throwaway repo before the fix and gone after it.

**The defect was never topological.** `git log` orders by commit date, so it only bites when main's
commit is NEWER than the branch's last — the ordinary case, and the reason a fixture whose commits
share one second is green against the bug.

**Both adversarial reviewers reproduced the sibling-branch regression independently** against the
first draft, which restricted the range without following first parents.

**Why the FIRST commit wins, measured over main's own first-parent line.** Of the 47 integration
merges whose branch carried ≥2 commits, the first commit's subject was judged closer to the message a
human wrote in 38 cases and the last in 7. That split is a **judgement and does not partition the
47** — two are unclassified either way, and the 38/7 cannot be re-derived mechanically; only the 47
and the five `wip(cb-NN): checkpoint before …` openers reproduce.

**Rejected: refusing to derive whenever main moved.** Level-(2) sessions commit plan notes to main
continuously, so "main moved" is the common case, and that form would have turned a default into a
mandatory argument on nearly every finish while the correct subject was sitting right there in the
range.

**How the observed CB-111 subject was actually produced.** The exit-13 refusal used to print the bare
short form of the re-run command, so the refusal routed the operator into the derivation that main's
move had just broken.
