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

## Что в этом файле, и чего в нём нет

**Что в этом файле.** Обоснования правил из корневого `CLAUDE.md`: почему правило появилось, какой
инцидент его породил, что показали раунды состязательного ревю, какие формы были отвергнуты и по
какому замеру. С Т-131 сюда же переехала операционная глубина — устройство сторожей и хуков,
пределы алярмов, внутренности гейтов.

**Чего в этом файле НЕТ, и это важнее.** Здесь нет ни одного правила, которое нужно знать до начала
работы. Всё такое осталось в корневом `CLAUDE.md`, потому что этот файл не впрыскивается в сессию —
его открывает только тот, кого сюда послали. Если ты ищешь, как завести рабочее дерево, что значит
код отказа или что можно коммитить на `main`, — тебе не сюда, а в корень.

**Кто сюда ходит.** Тот, кто правит соответствующую подсистему, — и тот, кто собирается ослабить
правило и обязан сперва узнать, чем за него заплатили.

---

# Перенесено из корня юнитом Т-131

## Workflow — `main` is never edited directly

**This section once said the harness was unnecessary — "plain git is enough" — and that claim was
falsified within two hours by the very rule it was introducing (CB-50).** It landed at 13:37 on
2026-08-16 (`2957070`) mandating a typed branch and `git merge --no-ff`; at 15:30 main was advanced
by `merge worktree-cb-45-similarity-seam: Fast-forward` — no type prefix, no merge commit, and by
then pointing at main's own SHA, so every further merge would fast-forward again. Two sentences
earlier the same section had stated the reason — **a convention that exists only as a pattern in the
log is not a rule** — and then declined to bind it. **Prose cannot enforce prose**; the harness below
is the fix.

It proves main and the branch were still the tested ones
**at the moment of the check**; two statements later `git merge "${BRANCH}"` resolves both refs
again, by NAME, for itself. Nothing carries a verified SHA into the merge and porcelain git has no
`--expect-old-oid`, so a window sits between them. The flock serializes **finishes against each
other** and nothing else, and the traffic that walks into that window is **ordinary sanctioned
work**: level-(2) sessions commit plan notes to main continuously, and since 2026-08-22
`tools/cascade-mint.sh` does it automatically while holding a *different* lock. Both parents, because what lands is a merge and checking one
of them while asserting the premise is this section's own recurring defect.
**Four details are load-bearing, and each is a way the alarm can lie.**

1. **Identity, not shape.** A two-parent tip does not establish that main's tip *is the merge this
   run made* — an off-harness merge landing a moment after ours has two parents too, and its first
   parent is *our* merge, so its parents would be reported as ours with a confident and wrong story.
   `ORIG_HEAD` supplies identity: `git merge` sets it to the HEAD it merged into, which **is** the
   merge's first parent by construction. **So the tip is ours exactly when it has two parents and
   its first parent is `ORIG_HEAD`.** Anything else is one verdict, `tip-not-ours`, which says what
   it does not know instead of inventing a cause — and that single verdict covers a stranger's
   commit or merge, an *Already up to date* merge (which sets `ORIG_HEAD` to the **current** tip, so
   it cannot masquerade as a match), an octopus and a root commit. Both git behaviours are pinned as
   premise tests.

2. **`tip-not-ours` is usually benign, and the text says so.** A plan note landing on main in the
   moment after a perfectly correct merge produces it, so the block tells the operator to read the
   log rather than to fix anything; only the two real mismatches carry the *fix it forward on a new
   branch* advice.

3. **The block is delivered from an `EXIT` trap armed the instant the merge returns**, not from a
   trailing `if`. Under `set -euo pipefail` any failure in the cleanup — its own final
   `git log … | sed`, or any statement a later edit inserts — would otherwise kill the script
   between detecting the condition and reporting it, presenting a landed merge as an ordinary
   failure. That is CB-41's rule again: make the bad state unrepresentable rather than re-establish
   discipline at each insertion point. The initial verdict is the pessimistic `unreadable`, so a
   signal arriving before the verdict is computed still reports *could not look*. **The residual is
   stated rather than claimed away:** the interval between `git merge` returning and `trap`
   executing is two assignments wide, and nothing in the script can close it.

4. **The reads are `--no-replace-objects` and stdout-only.** Replace refs and `info/grafts` make `^@`
   answer with parents that are not in the commit's own header, so without the flag the "an object's
   parents are immutable" argument would be true of the object and false of the answer. Stdout-only
   is the one place fail-closed is deliberately **not** applied — folding stderr in would make any
   `warning:` git emitted unparseable and fire the alarm on an honest finish, and an alarm that
   cries wolf is one nobody reads; the rc still separates an error from an empty answer. Every
   answer is shape-checked as well, because `rev-parse` echoes an argument it does not recognise
   back at you and exits 0.
**The merge gate is keyed on `GITHEAD_`, not on `MERGE_HEAD`.** On git 2.53 **a clean merge never
writes `MERGE_HEAD`** — the merge is resolved in memory, the git dir holds `AUTO_MERGE`, `ORIG_HEAD`
and `COMMIT_EDITMSG`, and `git rev-parse MERGE_HEAD` fails outright — so a hook keyed on that file
exits 0 on every clean merge: a gate that cannot fire, which is worse than no gate, because the table
above would then claim a rule nothing enforces. What git *does* provide is
**`GITHEAD_<sha>=<what the caller named>`**, set per merge head, which is git's own record and what
the merge strategies themselves read. **Not the commit *message***: parsing `Merge branch 'x'` is a
name-matching heuristic, and it would be blind anyway because `worktree-finish.sh` passes its own
`-m`. `tests/…::test_premise_merge_head_is_absent_on_a_clean_merge` and
`…::test_premise_githead_env_names_the_merged_ref` pin both premises, so a git upgrade that changes
either turns the suite red instead of silently disarming the hook.

**`GITHEAD_` is not always a NAME.** Measured on git 2.53 against a real remote:
`git merge origin/main` gives `GITHEAD_<sha>=origin/main`, but **`git pull` and `git merge FETCH_HEAD` give the
raw OID**. So `GITHEAD_` is used only to LEARN WHICH COMMITS are being merged; **the decision is made
from the refs that point at each of them.** Assuming it was a name refused every pull — a false
refusal, which is the worse failure.

**The sanctioned-type rule governs LOCAL branches.** Remote-tracking refs are *upstream's* namespace,
which this repo does not name, so exactly one of them is consulted — main's own upstream — and only
to recognise a pull. Concretely, given a merge head:

- Candidates are **every ref pointing at that head**, always. There is no "judge the named ref
  instead" branch; see the byte-identity note below for why that had to go.

- **Every local branch must qualify.** With "any qualifies" there is a three-command bypass:
  `git merge untyped` (refused), `git branch fix/tmp untyped`, `git commit` — git does not abort
  after a refusal, it leaves the merge in progress and routes the operator into `pre-commit`, where
  one typed alias at the same commit launders the whole thing.

- A **remote ref other than main's upstream `main` neither qualifies nor disqualifies.** Requiring
  *all* refs to qualify refuses a real `git pull` whenever upstream happens to have another branch
  cut at that commit (`origin/release-1.0`), and `refs/remotes/<r>/HEAD` — the default-branch alias —
  disqualifies the very pull the fallback exists for.

- Upstream **`main` wins** over a non-qualifying local branch, so a stray local bookmark left at the
  commit being pulled cannot refuse the pull.

Nothing
  is "stripped": a blind `${rest#*/}` collapses `refs/remotes/junk/main` to the accepted literal
  `main`, and trusting any *configured* remote's `main` still leaves `git remote add junk <anything>`
  plus a fetch as a two-command bypass.
There is no local
discriminator, and refusing remote refs instead would break `git pull` — the worse failure. Same
shape as the `--separate-git-dir` misbinding: **when a rule cannot be decided from local evidence,
supply external metadata rather than deepening the guess.** The external metadata is CB-59's
server-side protection — **but only the half of it that was actually enabled.** The ratified scope
(CI limits, item 4) refuses a force-push and a deletion of `origin/main`, so upstream's history
cannot be rewritten under you; an upstream `main` that simply *holds untyped work* is untouched,
because require-PR is deliberately off. **That half of the limit stays open**, and `TestKnownLimits`
pins that the bypass still reproduces, so the day it stops being true someone re-reads this instead
of trusting a stale claim.
**`MERGE_HEAD` is read fail-closed.** The conflicted-merge gate is a `while read` over that file, and
two states make a naive loop run **zero** times, leave the refusal flag at `0`, and fall through to
the merge-in-progress exemption: an **empty** `MERGE_HEAD`, which an interrupted git can leave behind
and is therefore reachable by accident, and a `MERGE_HEAD` with **no trailing newline**, since `read`
returns non-zero on an unterminated last line. The loop therefore uses `|| [[ -n "$_sha" ]]`, counts
what it saw, and **refuses when it saw nothing** — the "guard reporting clean because it could not
look" shape, which the CI job and the `pre-merge-commit` hook were already hardened against.

**`core.hooksPath` can make `_guard_enforcement_armed` lie**, which matters more than the other
findings because that guard's entire job is *this clone is actually armed*. `--git-common-dir`/hooks
does **not** follow the redirect, so both the guard and `install-hooks.sh` use `git rev-parse
--git-path hooks`, which does — **and a RELATIVE value is refused outright**, because git resolves
one against the top of *each* working tree, so `core.hooksPath=.githooks` names a different directory
in the primary checkout and in every linked worktree. "This clone is armed" is not a statement the
guard can make about a per-worktree path, so it declines to make it. The value is read with
**`--type=path`**, so git does its own `~` expansion first; reading it raw classes `~/hooks` as
relative and refuses a genuinely armed clone. **Known residual:** with `extensions.worktreeConfig`
and an *absolute* per-worktree value the asymmetry returns, bounded because the integration merge
runs in the primary, where the gate does fire.

**The bootstrap gate's condition must be MONOTONIC, and this is the one place it is stated.** It
gates on whether the path has **history** on main — which deleting the file cannot undo, so a
missing source reports as "cannot verify the hook identity" instead of vanishing, whereas gating on
"does the file exist" makes one `rm` a permanent, flagless disarm, landable on a perfectly typed
branch. That history is read with `--all` — a clone with no *local* main
(`git clone --single-branch --branch fix/…` is enough, and `origin/main` being present does not
help) would otherwise collapse it — **and it distinguishes an ERROR from an empty result**, failing
closed on the error: `2>/dev/null || true` makes those identical, and `git log --all -- <path>`
exits 128 in a `--filter=tree:0` clone whose promisor remote has gone away. **Later paragraphs need
this condition and none of them restates it** — the T-23 one below, and the bootstrap wall at the
end of this section — because a four-review-round condition in two places is two rules one edit
apart, which is this section's own argument about `_hook_source_known` applied to the prose that
describes it.

**Every reader of the staged set passes `-c core.quotePath=false`.** `git diff --cached --name-only`
C-quotes a non-ASCII path by default, which makes the allowlist regex miss it and refuses the commit;
the same default once made `_guard_conflict_markers` silently *accept* a conflict marker. The
commit-msg gate below derives a BASENAME from that same staged set, so a C-quoted path there yields a
basename no human could ever type — a *permanent* false refusal of every non-ASCII plan note rather
than a one-off. The test that pins this names no count, because a count in a name is a count that
goes stale.
**Naming is the discriminator
because git records nothing about *how* a path was staged**: the index cannot be asked whether
`git add` was given a file or a directory. What separates the two cases is the author — you cannot
name a file you did not know was there.

**The phase is `commit-msg` and not `pre-commit`, and the measurement is the whole argument.** On git
2.53, at `pre-commit` time the message being written does not exist anywhere: `$GIT_DIR/COMMIT_EDITMSG`
holds the **PREVIOUS** commit's message, and on a clone's first commit it does not exist at all. A
pre-commit naming check is therefore not a gate that fails open — it is a gate wired to someone
else's input, which passes a sweeping commit whose predecessor happened to name the file and refuses
a correct one whose predecessor did not; that is worse than absent, because it looks like
enforcement. `commit-msg` receives the final message as `$1`, after `-m`, `-F` and the editor have
all had their say, and `test_premise_pre_commit_cannot_see_the_message` pins it.

**The message is truncated at the scissors FIRST, then comment-stripped**, because two auto-generated
sources inside the message file would each make this a gate that cannot fire: git's default template
lists the staged paths as comment lines (`#	new file:   .claude/plans/foo.md`), and `git commit -v`
appends the whole diff below the scissors line, where every hunk header names its file — which
`git stripspace --strip-comments` does **not** remove, because a diff is not a comment. **The
scissors test is `>8` and `---` on one line rather than git's exact string**, because the comment
character is configurable and anchoring on `#` would let a repo with `core.commentChar=;` keep its
diff; over-truncating costs a loud refusal, under-truncating costs the gate. Comment stripping is
delegated to `git stripspace`, which reads the same `core.commentChar` git itself will use, so the
two cannot disagree.

**Matching is by TOKEN, and a word boundary is the wrong tool.** `plan.md` is a substring of
`my-plan.md`, so a sweeping commit naming its own note would launder the stranger's note beside it —
and the swept file is by construction the one nobody wrote down. A regex `\b` does not fix it either,
because `-` and `.` are non-word characters, so `\bplan\.md\b` matches *inside* `my-plan.md`. `LC_ALL=C` pins byte
semantics so the verdict cannot depend on the committer's locale — **honest scope: that line is
determinism insurance and no test discriminates it**, since under a UTF-8 locale codepoint-wise
classification happens to agree on every case here.

**The matcher also decides which names it will judge, which is the same predicate and not a second
one.** A space is a boundary, so with `a b.md` and `b.md` both staged and only `a b.md` named, the
occurrence of `b.md` INSIDE it is flanked by a space and the token end — two boundaries — and the
stranger's note lands unnamed. That closes
the class BY CONSTRUCTION, and the proof is two lines: if every staged basename is made only of name
bytes, an occurrence of one strictly inside a longer one always has a name byte on at least one side,
so it can never be flanked by two boundaries. **The general shape: a check that validates elements cannot validate their
composition, and here the composition is *the matcher plus the set of names it is asked to match*.**

On a branch there are no
foreign untracked notes to sweep, so the rule there would be pure friction on every `wip` commit, and
everything else on main is pre-commit's to refuse — duplicating that judgement would give one state
two refusals that could drift. It is read fail-closed with a count, exactly like
pre-commit's arm: an empty `MERGE_HEAD` must not read as an exempt one. That is not
a hole this gate opened; `pre-commit`'s merge exemption already waves the whole staged set through on
that path. The gate is an accident-stopper, and a merge state is not something one enters by accident.

**`_guard_enforcement_armed` demands this hook too, since T-23.** The condition is the SAME monotonic
one `pre-merge-commit` uses — extracted into `_hook_source_known` and called once per gated hook
rather than copied, because a four-review-round condition in two places is two rules one edit apart;
`test_bootstrap_condition_is_one_function_called_per_gated_hook` counts the call sites. **Two of the three hooks share a predicate — disjoint halves, neither redundant, and they must not
disagree.** (The third, commit-msg, shares nothing with them: it reads the message, they read refs.)
A CONFLICTED merge never reaches `pre-merge-commit`, and neither does a merge this hook has already
refused: both are finished with `git commit`, which fires `pre-commit`. So the predicate is
duplicated **byte-identically** into `pre-commit-hook.sh` between the `# ---8<--- SHARED MERGE-GATE PREDICATE` markers, and **a test compares the two blocks verbatim rather than grepping for a
substring.** The integration merge does not pass `--no-verify`: leaving it would make the harness the
single caller exempt from the gate.
`tests/test_worktree_harness.py::TestGitSequencerPremises` pins both directions, so a git version
that starts running the hook turns the suite red instead of quietly making this paragraph true.
**`install-hooks.sh` sets `merge.ff=false` before anything arming-related can abort**, so a clone
missing `tools/pre-merge-commit-hook.sh` — an older main, a `git checkout <old-commit>`, the
CB-57 bootstrap window itself — still arms the pre-commit hook and still exits 1 at the merge-hook
step, but does so **with `merge.ff` already set**. **With that step last it left `merge.ff` unset
instead**, and the installer could skip the one mechanism no hook can replace.
**The precise claim:** four commands still precede it (sourcing the guards, resolving the repo root,
resolving the hooks dir, `mkdir -p`) and each is fatal under `set -e`, so "a step that cannot fail
goes first" is not literally true of it.

Moving the
   baseline forward is how a violation would be laundered, so it is a deliberate, reviewable edit,
   and a test asserts the SHA is a real commit here.

**DAG inspection cannot prove
   how a merge commit reached the ref; only a protected ref can.** An **evil merge** (content in
   neither parent) is invisible for a second reason: `git show --name-only` on a merge prints
   nothing.

Both this job and the pre-commit hook had that
   defect; both are fixed and both are pinned.

That scope was **ratified by the owner as sufficient for CB-59**, on this
   reasoning: force-push and deletion are the class **nothing local can catch**, because they
   rewrite or destroy history every local hook has already approved, whereas require-PR and a
   required check constrain *how work arrives* — already governed by `merge.ff=false`, the three
   hooks and `_guard_enforcement_armed` **for a clone that has run `tools/install-hooks.sh`**.
   CB-59 is closed at that scope, not at this paragraph's original four items.

The trigger split is pinned **in both
   directions**, because a test asserting only the negative half left deleting
   `push: branches: [main]` outright green, turning "gate that cannot fire" into "workflow that
   never fires".

   `test_ci_suite_job_checks_out_the_history_its_own_suite_reads` pins it, and each of its four
   properties was earned rather than chosen. **Comments do not count**, since the fix's own comment
   carries the literal `fetch-depth: 0` and a raw grep would stay green after the key itself was
   deleted — and the stripping is WHOLE-LINE only, so an inline `#` is TOLERATED by the matchers
   rather than parsed. **A file is not a composition**: two jobs, two checkouts, so "somewhere in
   `ci.yml`" is satisfied by moving the key to `contracts` and leaving the gate just as broken.
   **The key must be a `with:` INPUT and the explanation a COMMENT LINE**, or a step whose multiline
   `name:` scalar contains both strings satisfies every assertion at depth 1. **Exactly one
   checkout, carrying no `if:`**, or `if: ${{ false }}` on the first plus a second bare checkout
   defeats it. **NOT closed, and named so it is not rediscovered:** a job-level `if:` switching the
   whole `tests` job off is the same shape and this test does not look at it.
**What the guard compares, and why the two sides are probed differently.** The worktree side is
`uv run --extra dev python -c <probe>` — byte for byte the launcher `[6/7]` uses for pytest, so the
answer IS the interpreter the suite will run under, and the call syncs the worktree to the pin as a
side effect, which is wanted on a tree about to be tested. The main side is
`<repo_root>/.venv/bin/python` executed DIRECTLY, deliberately **not** through `uv run`: that would
rebuild a checkout other sessions are working in, and **a guard must not mutate the tree it is
judging**. It also answers the exact question the incident asked — what main ACTUALLY has, not what
it would acquire next time somebody ran something there. `pyvenv.cfg` is not read: it describes an
environment rather than being one.
The version-shape check
(`_interpreter_version_is_sane`) earns its keep in exactly one state the pin check cannot catch: **a
NON-version that PREFIX-MATCHES the pin.** A bare pin like `"3"` accepts anything spelled `"3."` plus
more as if it were a legitimate patch release, so a stub `"3.0"` on both sides clears the pin check
by looking like one while still failing the strict `X.Y.Z` shape — there the shape check is the only
backstop. `test_two_prefix_matching_non_versions_refuse_rather_than_agree` is what holds it;
`test_two_undeterminable_sides_refuse_rather_than_agree` is kept as a premise fixture but is **not
sufficient on its own** (CB-140).
**Anchor the structural test on the `git merge` and
not on the echo announcing it** — "after the text that says a merge is coming" is not "after the
merge".

**It is asserted a SECOND time, inside the lock, because a pre-check is not an invariant at landing
time.** main's `.venv` is gitignored, so `_guard_main_clean` cannot see it move and the in-lock SHA
re-checks are about commits: a `UV_PYTHON=… uv sync` in main during the suite run would land work
tested on one interpreter onto a main that now has another. It is a second CALL rather than a stored
sample (~100ms) so it cannot drift from the thing it is checking.
Compare the resolved venv *directories*,
never the interpreters they resolve to — two honest venvs built from one system python both resolve
to a single `/usr/bin/pythonX.Y`, so an interpreter-level test would refuse every ordinary case.

**The usual bootstrap wall applies, and it is milder than CB-57's.** `worktree-finish.sh` runs from
the REPO ROOT, so the script that lands this change is MAIN's copy, which does not yet contain the
call; the first finish AFTER it lands is the first gated one. No re-run of `install-hooks.sh` is
needed, because this guard is in the script rather than in an installed hook.

  The claim carries a **holder triple** (`--holder <branch> --holder-kind branch --repo <root>`),
  mutual exclusion is the partial unique index, and there is a release path — including
  `_auto_release_on_terminal`, so closing the card releases the claim in the same transaction.   **Order is load-bearing: the claim happens BEFORE `git worktree add`,** for the same reason
  `_guard_branch_type` does — otherwise the losing side of a race owns a branch and a directory by
  the time it is told no. An
  **EXIT trap** releases whatever the run took if setup aborts, armed after the first successful
  claim and **disarmed on success** — leaving it armed would make every setup that *worked* release
  its own claim on the way out. 

  Following first parents skips every absorbed lineage, main's forward-merge included.
  **The FIRST commit of that line wins, not the last**, because branches here end on review fixups,
  which describe an iteration's tail rather than its subject. Do **not** write that as
  `--reverse -1`: git applies the count BEFORE reversing, so it returns the NEWEST commit and
  silently restores the behaviour this removed (measured).
  **The refusal tests the POPULATION, not its first line**: `git commit --allow-empty-message` puts a
  blank line at the head, and reading that as an empty population is a false refusal that also
  asserts something untrue about the repository. It cannot drift from what lands: both inputs are the
  pinned `TESTED_*` values and the in-lock re-checks refuse with exit 13 if either moved.
No ordering flag reaches it (measured:
  `--first-parent` and `--topo-order` both pick the base commit) because it is not a traversal
  question — the commits really are this branch's ancestry and this merge really does land them.

One `_retry_hint` builds the line for all
  four refusal paths (forward-merge conflict, main moved, branch moved, merge failed); a test refuses
  the exact literal `echo "      tools/worktree-finish.sh ${SLUG}"` — the spelling that regressed —
  while the helper-call count holds the other three sites. 

**How the harness itself is tested, and where that stops.**
`tests/test_worktree_harness.py` covers every guard on both sides — the state it must refuse and the
state it must allow — **and separately asserts that `worktree-finish.sh` actually calls each one.**
That second class exists because two adversarial reviews deleted guard *invocations* from the script,
including the branch-type guard that exists for the 2026-08-16 incident, and the whole suite stayed
green, because nothing executed the script: every guard was unit-tested and the composition was not.
**Do not read the per-guard tests as covering the wiring.**

**Two lessons a vacuous test taught, both worth keeping:** a test that sets up its own fixture must
**assert the fixture exists**, and `git rev-parse` is not a safe place to be sloppy with argv — it
echoes an unrecognised option-looking argument back at you and exits 0.

The wiring tests are **structural**: they read the script and assert each guard is invoked with
`|| exit $?`, in the right phase. **But "executing the whole script is impractical" is narrower than
it reads, and CB-116 is the proof:** `TestMergeSubjectDerivation` runs `worktree-finish.sh` end to
end in a throwaway repo under `--skip-checks`, which disables ruff and pytest and *not* the safety
guards, and the merge it lands is onto that repo's main. So a property of the SCRIPT'S OUTPUT can be
tested behaviourally, and had to be: the CB-116 defect was invisible to every structural test, because
the defective code called `git log`, which is exactly what a structural test would look for. **What
stays impractical is the gate run itself, not the script.** Three more structural tests came with
CB-57, each pinning a property whose failure mode is silent — a gate present in the tree and absent
in effect: the integration merge must **not** carry `--no-verify`, the installer must arm the merge
hook and point it at main's checkout, and the CI workflow must carry a baseline SHA that is a real
commit in this repository.

**The branch predicate is constructed FOUR times across THREE files** — `_guards.sh` once,
`pre-commit-hook.sh` twice (its own branch check, plus the copy inside the shared merge-gate block),
`pre-merge-commit-hook.sh` once — because neither hook may source the library: each runs from
`.git/hooks/` as a symlink and must work when `tools/` is missing from the checked-out tree. **The
test counts constructions per SITE, not per file**, because a per-file count let a degraded regex in
`pre-commit-hook.sh` stay green on the shared block's copy. **Same types is *not* the same
predicate** — a prefix test accepts `fix/a/b`, which `_guard_branch_type` refuses — so a divergence
would let a branch clear the finish guard and then be refused by the merge hook, after the whole
suite had already run.

**Byte-identical is not the same claim as "the two hooks agree".** The shared predicate once took a
second argument — what the caller typed, from `GITHEAD_` — and judged that ref alone when it
resolved: identical code, two different rules, because only `pre-merge-commit` HAS that argument, and
the byte-identity test structurally could not see it because the divergence lived in the arguments.
The predicate now takes only the merge head, so both callers pass identical information. **The
general form, which this repo keeps relearning: sharing an implementation does not share a decision
if the callers supply different inputs.**

**Cherry-pick and revert have no marker-file exemption, and the honest scope is narrower than "they
are now refused".** The merge-in-progress exemption used to fire on mere existence of `MERGE_HEAD`,
`CHERRY_PICK_HEAD` *or* `REVERT_HEAD`, so one empty file turned off both of this hook's rules —
reachable the same way an empty `MERGE_HEAD` was, since a conflicted cherry-pick leaves the file
until `--continue`/`--abort`. **But a CLEAN `git cherry-pick` or `git revert` onto main never reaches
`pre-commit` at all** — git's sequencer commits directly — so it still lands, verified by running
both. What the change buys is that a *marker file* no longer launders a commit; clean cherry-pick and
revert onto main are caught only by the CI alarm, since they leave a single-parent commit on the
first-parent line. **That is the honest statement.**

**The same fail-closed validation is NOT scoped to main**, because the exemption it guards is not:
while it was, `: > .git/MERGE_HEAD` on an untyped branch still skipped the branch-type check. Only
the head-*acceptability* rules — typed branch, or upstream `main` — are about main.
This is the wall **the monotonic condition
stated above** exists to get past — it is stated there and deliberately not restated here. 
