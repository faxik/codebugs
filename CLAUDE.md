# Codebugs

AI-native code finding & requirements tracker. SQLite-backed, exposed via MCP server + CLI.

## Workflow — `main` is never edited directly

**Every code edit happens on a short-lived branch, in a worktree, and `tools/` enforces it.**
Borrowed from `../autosorter` (2026-08-16), including a scaled-down port of its
`tools/worktree-*.sh` harness.

**This section once said the harness was unnecessary — "plain git is enough" — and that claim was
falsified within two hours by the very rule it was introducing (CB-50).** It landed at 13:37 on
2026-08-16 (`2957070`) mandating a typed branch and `git merge --no-ff`; at 15:30 main was advanced
by `merge worktree-cb-45-similarity-seam: Fast-forward` — no type prefix, no merge commit, and by
then pointing at main's own SHA, so every further merge would fast-forward again. Two sentences
earlier the same section had stated the reason — **a convention that exists only as a pattern in the
log is not a rule** — and then declined to bind it. **Prose cannot enforce prose**; the harness below
is the fix.

**What is now mechanically enforced** (`tools/install-hooks.sh` arms it; run once per clone):

| Rule | Mechanism | Refuses with |
|---|---|---|
| Branch carries `fix/`\|`feature/`\|`refactor/`\|`docs/` | `_guard_branch_type` (7) + pre-commit hook (1) | exit 7 / 1 |
| Nothing but `.claude/plans/*.md` or `.claude/plans/briefs/*.html` is committed on main | pre-commit hook | exit 1 |
| A plan note committed on main is NAMED in the commit message | commit-msg hook | exit 1 |
| A cascade id added to `.claude/plans/CASCADE-IDS.md` on main is the one `tools/cascade-mint.sh` would have computed (`max+1` per family, annulled lines and mentions included) | pre-commit hook | exit 1 |
| A merge onto main comes from a typed local branch, or from main's own upstream `main` | pre-merge-commit hook (clean merge) + pre-commit hook (conflicted merge) | exit 1 |
| An in-progress cherry-pick/revert marker no longer exempts a commit | pre-commit hook | exit 1 |
| Integration never fast-forwards | `--no-ff` + `git config merge.ff false` | — |
| One integration at a time | `flock` on `.worktrees/.integrate.lock` | exit 1 |
| The tested state still matched at the moment it was re-checked | in-lock SHA re-check | exit 13 |
| The suite ran under the interpreter main has | `_guard_interpreter_matches_main` | exit 14 |
| This clone is actually armed | `_guard_enforcement_armed` | exit 12 |
| Main has main checked out, and is clean | `_guard_workspace_on_main`, `_guard_main_clean` | exit 8, 11 |
| The branch actually carries a change | `_guard_nonempty_diff` | exit 9 |
| No conflict markers, no scratch or temp file at root, no stale base | `_guard_conflict_markers`, `_guard_untracked_scratch_at_root`, `_guard_stale_base` | exit 5, 4, 6 |

**`.github/workflows/main-invariants.yml` is deliberately NOT in that table**, and the reason is the
table's own title. It asserts that main's first-parent line carries nothing but merges and plan notes
— but a workflow **cannot refuse a push**, it reports afterwards, so listing it under *"what is now
mechanically enforced"* with a *"refuses with"* column was a category error inside the very table
meant to be precise (round-3 review). It is an **alarm**. The gate is branch protection on
`origin/main`; see the CI limits below.

**The re-check row was NARROWED, and what closes the remaining gap is a second alarm — not a gate
(CB-121).** That row used to read *"The tested state is the landed state"*, and it overclaimed: the
in-lock re-check is a **check-then-act**. It proves main and the branch were still the tested ones
**at the moment of the check**; two statements later `git merge "${BRANCH}"` resolves both refs
again, by NAME, for itself. Nothing carries a verified SHA into the merge and porcelain git has no
`--expect-old-oid`, so a window sits between them. The flock serializes **finishes against each
other** and nothing else, and the traffic that walks into that window is **ordinary sanctioned
work**: level-(2) sessions commit plan notes to main continuously, and since 2026-08-22
`tools/cascade-mint.sh` does it automatically while holding a *different* lock. The narrowed row is
still a checkable claim — the state matched under the lock, and a mismatch there really does refuse
with exit 13 before anything lands. It simply no longer promises the interval it cannot cover.

The gap is covered by a **post-merge alarm**, an alarm for the same reason `main-invariants.yml` is:
by the time it can look, **the merge step has already run**, so it cannot refuse anything and gets no
row in the table above. Immediately after the integration merge and **before** `flock -u 9` — after
the unlock another finish could move main, and the alarm would start lying in the way it exists to
catch — `worktree-finish.sh` asks whether the merge that just ran has `TESTED_MAIN` as its first
parent and `TESTED_HEAD` as its second. Both parents, because what lands is a merge and checking one
of them while asserting the premise is this section's own recurring defect. It then lets the cleanup
finish (worktree removal, claim release) and speaks at the very end, with a loud block and `exit 15`
— deliberately not `exit 13`, which means *nothing landed, re-run*. `exit 15` means *the merge step
already ran and the premise is unconfirmed*, and the block says in words not to re-run: a second
finish after a landed merge is a worse outcome than the defect being reported.

**Four details are load-bearing, each a cross-model review finding rather than foresight, and each is
a way the alarm can lie.**

1. **Identity, not shape.** A two-parent tip does not establish that main's tip *is the merge this run
   made*: an off-harness merge landing a moment after ours has two parents too, and its first parent
   is *our* merge — so its parents would be reported as ours, with a confident and wrong story.
   `ORIG_HEAD` supplies identity: `git merge` sets it to the HEAD it merged into, which **is** the
   merge's first parent by construction. So the tip is ours exactly when it has two parents and its
   first parent is `ORIG_HEAD`. Anything else is one verdict, `tip-not-ours`, which says what it does
   not know instead of inventing a cause — and that single verdict covers a stranger's commit or
   merge, an *Already up to date* merge (which sets `ORIG_HEAD` to the **current** tip, so it cannot
   masquerade as a match), an octopus and a root commit. Both git behaviours are pinned as premise
   tests.
2. **`tip-not-ours` is usually benign, and the text says so.** A plan note landing on main in the
   moment after a perfectly correct merge produces it. The block therefore tells the operator to read
   the log rather than to fix anything, and only the two real mismatches carry the *fix it forward on
   a new branch* advice.
3. **The block is delivered from an `EXIT` trap armed the instant the merge returns**, not from a
   trailing `if`. Under `set -euo pipefail` any failure in the cleanup — its own final
   `git log … | sed`, or any statement a later edit inserts — would otherwise kill the script between
   detecting the condition and reporting it, presenting a landed merge as an ordinary failure. That
   is CB-41's rule again: make the bad state unrepresentable rather than re-establish discipline at
   each insertion point. The initial verdict is the pessimistic `unreadable`, so a signal arriving
   before the verdict is computed still reports *could not look*. The residual is stated rather than
   claimed away: the interval between `git merge` returning and `trap` executing is two assignments
   wide, and nothing in the script can close that.
4. **The reads are `--no-replace-objects` and stdout-only.** Replace refs and `info/grafts` make `^@`
   answer with parents that are not in the commit's own header, so without the flag the "an object's
   parents are immutable" argument would be true of the object and false of the answer. Stdout-only
   is the one place fail-closed is deliberately **not** applied — folding stderr in would make any
   `warning:` git emitted unparseable and fire the alarm on an honest finish, and an alarm that cries
   wolf is one nobody reads; the rc still separates an error from an empty answer. Every answer is
   shape-checked as well, because `rev-parse` echoes an argument it does not recognise back at you
   and exits 0.

Rejected forms, with their reasons: making the merge name the pinned `TESTED_HEAD` closes only the
branch half and pays a false refusal, since `pre-merge-commit` refuses a head with no ref; and a real
CAS needs `git update-ref` over `commit-tree`, which this repo's own CI alarm treats as a
hook-bypassing shape.

`merge.ff=false` is the one no hook could replace: **git fires no hook on a fast-forward at all**,
because no commit is created, so nothing can catch it after the fact. Verified by replaying the
incident in a throwaway repo — default config gives `Fast-forward` and zero merge commits;
`merge.ff=false` gives a merge commit. Two precise limits, because the first draft of this section
overstated it: it does nothing when the branch is already an ancestor of main (git says "Already up
to date" and main does not move, which is harmless), and it is *configuration*, so
`git config merge.ff true` turns it off without anyone typing `--ff`.

**The merge gate closed, and CB-57's own prescription turned out to be wrong (verified by running
it).** The card said to validate "the branch behind `MERGE_HEAD`" in a `pre-merge-commit` hook. On
git 2.53 **a clean merge never writes `MERGE_HEAD`** — the merge is resolved in memory, the git dir
holds `AUTO_MERGE`, `ORIG_HEAD` and `COMMIT_EDITMSG`, and `git rev-parse MERGE_HEAD` fails outright.
A hook keyed on that file exits 0 on every clean merge: a gate that cannot fire, which is worse than
no gate, because the table above would then claim a rule nothing enforces. What git *does* provide
is **`GITHEAD_<sha>=<what the caller named>`**, set per merge head — git's own record, and what the
merge strategies themselves read. Not the commit *message*: parsing `Merge branch 'x'` is the
name-matching heuristic CB-57 refused, and it would be blind anyway because `worktree-finish.sh`
passes its own `-m`. `tests/…::test_premise_merge_head_is_absent_on_a_clean_merge` and
`…::test_premise_githead_env_names_the_merged_ref` pin both premises, so a git upgrade that changes
either turns the suite red instead of silently disarming the hook.

**`GITHEAD_` is not always a NAME, and assuming it was broke `git pull` — a false refusal, which is
the worse failure.** Measured on git 2.53 against a real remote: `git merge origin/main` gives
`GITHEAD_<sha>=origin/main`, but **`git pull` and `git merge FETCH_HEAD` give the raw OID**. The
first draft therefore refused every pull, while its own comment and this section both promised pulls
were fine. (One cross-model reviewer asserted `GITHEAD_` carries a `branch 'main' of <url>`
description in that case; it does not, on this version — the other reviewer reproduced the OID, and
so did I. Measured, not argued.) So `GITHEAD_` is used only to LEARN WHICH COMMITS are being merged;
the decision is made from the refs that point at each of them.

**The rule that survived three review rounds: the sanctioned-type rule governs LOCAL branches.**
Remote-tracking refs are *upstream's* namespace, which this repo does not name, so exactly one of them
is consulted — main's own upstream — and only to recognise a pull. Concretely, given a merge head:

- Candidates are **every ref pointing at that head**, always. There is no "judge the named ref
  instead" branch any more; see the byte-identity note below for why that had to go.
- **Every local branch must qualify**, because with "any qualifies" review reproduced a
  **three-command bypass**: `git merge untyped` (refused), `git branch fix/tmp untyped`, `git commit`.
  Git does not abort after a refusal; it leaves the merge in progress and says "use `git commit` to
  complete the merge", routing the operator into `pre-commit`, where one typed alias at the same
  commit laundered the whole thing.
- A **remote ref other than main's upstream `main` neither qualifies nor disqualifies.** Requiring
  *all* refs to qualify refused a real `git pull` whenever upstream happened to have another branch cut
  at that commit (`origin/release-1.0`), and `refs/remotes/<r>/HEAD` — the default-branch alias —
  disqualified the very pull the fallback existed for. Both reproduced; the second was caught by this
  repo's own test minutes after the first fix, two bugs in the same three lines.
- Upstream **`main` wins** over a non-qualifying local branch, so a stray local bookmark left at the
  commit being pulled cannot refuse the pull.
- The trusted ref is matched **exactly** (`refs/remotes/<branch.main.remote|origin>/main`). Nothing is
  "stripped": a blind `${rest#*/}` once collapsed `refs/remotes/junk/main` to the accepted literal
  `main`, and the intermediate fix — trusting any *configured* remote's `main` — still left
  `git remote add junk <anything>` plus a fetch as a two-command bypass.

**A merge head with NO ref at all is refused, and that is a real cost, not a free win.** It is what
catches a bare SHA or a tag, but it also refuses four legitimate-if-rare flows, verified: a one-shot
`git pull --no-rebase <URL> main`, `git merge FETCH_HEAD` with no tracking ref, a `branch.main.remote`
set to a URL rather than a remote name, and `git merge <tag>` where no branch points at the tagged
commit. Ordinary `git pull` against a configured remote is unaffected, which is why this is documented
rather than fixed: closing it means trusting `.git/FETCH_HEAD`'s description text, and adding a new
trust path with no review round left to attack it is a worse trade than a rare `--no-verify`. **Use
`git merge --no-verify` for a one-shot pull from a URL.**

**And one limit that cannot be closed here, stated rather than papered over.** *Main's own upstream*
`main` is **trusted** — `branch.main.remote`, defaulting to `origin` — and nothing local can prove
what it contains or how it got there: `git update-ref refs/remotes/origin/main <any-sha>`, a mistyped
fetch refspec, a rewritten `remote.origin.fetch` (which then re-arms on every ordinary `git fetch`),
or simply an upstream whose `main` holds untyped work all land content here. Reproduced. There is no
local discriminator, and refusing remote refs instead would break `git pull` — the worse failure.
Same shape as the `--separate-git-dir` misbinding: **when a rule cannot be decided from local
evidence, supply external metadata rather than deepening the guess.** The external metadata is
CB-59's server-side protection — **but only the half of it that was actually enabled.** The ratified
scope (CI limits, item 4) refuses a force-push and a deletion of `origin/main`, so upstream's history
cannot be rewritten under you; an upstream `main` that simply *holds untyped work* — the failure mode
named two sentences above — is untouched, because require-PR is deliberately off. That half of the
limit therefore stays open, and `TestKnownLimits` pins that the bypass still reproduces so the day it
stops being true someone re-reads this instead of trusting a stale claim.

Note the scope was narrowed twice under review. It first trusted **any** `<remote>/main`, then any
**configured** remote's — at which point `git remote add junk <anything>` plus a fetch was still a
two-command bypass. Only main's declared upstream counts now.

**`MERGE_HEAD` is read fail-closed, and it was not at first.** The conflicted-merge gate is a `while
read` over that file, and two states made it run **zero** times, leave the refusal flag at `0`, and
fall through to the merge-in-progress exemption: an **empty** `MERGE_HEAD` (which an interrupted git
can leave behind, so this was reachable by accident) let arbitrary staged content land on main with
no merge at all; and a `MERGE_HEAD` with **no trailing newline** — `read` returns non-zero on an
unterminated last line — landed a real two-parent merge of an untyped branch. Neither typed
`--no-verify`. Both reproduced. The loop now uses `|| [[ -n "$_sha" ]]` and counts what it saw, and
refuses when it saw nothing: the "guard reporting clean because it could not look" shape that the CI
job and the `pre-merge-commit` hook were *already* hardened against in this same change — `pre-commit`
was the one place left failing open.

**`core.hooksPath` made `_guard_enforcement_armed` lie**, which matters more than the other findings
because that guard's entire job is *this clone is actually armed*. It resolved
`--git-common-dir`/hooks, which does **not** follow the redirect, so `git config core.hooksPath
<empty-dir>` left the guard returning `0` while nothing was installed and a commit of arbitrary
content on main then succeeded. Both the guard and `install-hooks.sh` now use `git rev-parse
--git-path hooks`, which does follow it (verified both ways) — **and a RELATIVE value is refused
outright**, because git resolves one against the top of *each* working tree, so
`core.hooksPath=.githooks` names a different directory in the primary checkout and in every linked
worktree. Round 3 reproduced that too: armed in the primary, main checked out in a linked worktree
with no `.githooks` there, guard `0`, source commit onto main `0`. "This clone is armed" is not a
statement the guard can make about a per-worktree path, so it declines to make it. The value is read
with **`--type=path`**, so git does its own `~` expansion first: reading it raw classed `~/hooks` as
relative and refused a genuinely armed clone, while the same function was resolving the same setting
through `--git-path` two lines earlier — one setting, two answers. Known residual: with
`extensions.worktreeConfig` and an *absolute* per-worktree value the asymmetry returns, bounded because
the integration merge runs in the primary where the gate does fire.

**The bootstrap gate's "monotonic" condition took three attempts.** It first gated on the file
existing, so one `rm` was a permanent silent disarm (round 2). It then read the literal ref `main`, so
any clone with no *local* main — `git clone --single-branch --branch fix/…` is enough, and
`origin/main` being present does not help — collapsed it straight back (round 3). It now reads
`--all`, **and distinguishes an ERROR from an empty result**, because `2>/dev/null || true` made those
identical: round 4 reproduced the full disarm once more through a `--filter=tree:0` clone whose
promisor remote had gone away, where `git log --all -- <path>` exits 128. The claim "no checkout shape
can hide the history" was true and still insufficient — the hole had moved from a checkout shape to an
error path. It now fails closed on the error, which is the third distinct door onto the same defect.

**A non-ASCII plan note could not land on main** — a false refusal, and the mirror image of a bug
this repo already had. `git diff --cached --name-only` C-quotes such a path by default, the allowlist
regex misses it, and the commit is refused; the same default once made `_guard_conflict_markers`
silently *accept* a conflict marker. Both readers now pass `-c core.quotePath=false`. **A third
reader has since joined them** — the commit-msg gate below derives a BASENAME from that same staged
set, so a C-quoted path there yields a basename no human could ever type, which is a *permanent*
false refusal of every non-ASCII plan note rather than a one-off. The test that pins this no longer
says "both readers", because a count in a name is a count that goes stale.

**A plan note landing on main must be NAMED in the commit message, and the mechanism is a
`commit-msg` hook — NOT the pre-commit hook originally specified.** The rule this mechanises is that
parallel sessions add files to main **by name, never by directory**: `.claude/plans/` is the one
place they may all write, and `git add .claude/plans/` swept an UNTRACKED note belonging to another
direction into a commit describing unrelated work. The bytes survived; the **provenance** did not.
The convention was then adopted and broken again — a convention broken four times after adoption is
this section's own opening lesson, so it had to stop being prose. Naming is the discriminator
because git records nothing about *how* a path was staged: the index cannot be asked whether
`git add` was given a file or a directory. What separates the two cases is the author — you cannot
name a file you did not know was there.

**The phase moved on a measurement, and the measurement is the whole argument.** On git 2.53, at
`pre-commit` time the message being written does not exist anywhere: `$GIT_DIR/COMMIT_EDITMSG` holds
the **PREVIOUS** commit's message, and on a clone's first commit it does not exist at all. A
pre-commit naming check is therefore not a gate that fails open — it is a gate wired to someone
else's input, which passes a sweeping commit whose predecessor happened to name the file and refuses
a correct one whose predecessor did not. That is worse than absent, because it looks like
enforcement. `commit-msg` receives the final message as `$1`, after `-m`, `-F` and the editor have
all had their say. `test_premise_pre_commit_cannot_see_the_message` pins it, so a git that changes
the behaviour turns the suite red instead of silently justifying a move back.

**Two auto-generated sources inside the message file would each have made this a gate that cannot
fire, and neither was foreseen when the rule was specified.** git's default template lists the staged paths as comment lines
(`#	new file:   .claude/plans/foo.md`), so every editor-based commit would have passed vacuously;
and `git commit -v` appends the whole diff below the scissors line, where every hunk header names
its file — and `git stripspace --strip-comments` does **not** remove that, because a diff is not a
comment. So the message is truncated at the scissors **first**, then comment-stripped. The scissors
test is `>8` and `---` on one line rather than git's exact string, because the comment character is
configurable and anchoring on `#` would let a repo with `core.commentChar=;` keep its diff;
over-truncating costs a loud refusal, under-truncating costs the gate. Comment stripping is
delegated to `git stripspace`, which reads the same `core.commentChar` git itself will use, so the
two cannot disagree.

**Matching is by TOKEN, and a word boundary is the wrong tool.** A substring test passes on an
ordinary case, not a contrived one: `plan.md` is a substring of `my-plan.md`, so a sweeping commit
naming its own note launders the stranger's note sitting beside it — and the swept file is by
construction the one nobody wrote down. A regex `\b` does not fix it either, because `-` and `.` are
non-word characters, so `\bplan\.md\b` matches *inside* `my-plan.md`. The match must be flanked by
a boundary: the string edge, or an ASCII byte that cannot occur in the name. Every **non-ASCII** byte
counts as part of a name, so an ambiguous neighbour refuses rather than matches; the stated cost is
that a filename hugged by typographic quotes or dashes (`«plan.md»`) is not recognised and needs a
space or an ASCII quote around it. `LC_ALL=C` pins byte semantics so the verdict cannot depend on the
committer's locale — **honest scope: that line is determinism insurance and no test discriminates
it**, since under a UTF-8 locale codepoint-wise classification happens to agree on every case here.

**And the matcher decides which names it will judge, which is the same predicate and not a second
one — cross-model review reproduced the hole that made this necessary.** A space is a boundary, so
with `a b.md` and `b.md` both staged and only `a b.md` named, the occurrence of `b.md` INSIDE it is
flanked by a space and the token end — two boundaries — and the stranger's note landed unnamed
(measured: rc=0, both files committed). So a staged basename containing a space or ASCII punctuation
outside `[A-Za-z0-9._-]` is **refused outright** rather than judged by a rule that cannot see it.
That closes the class BY CONSTRUCTION, and the proof is two lines: if every staged basename is made
only of name bytes, an occurrence of one strictly inside a longer one always has a name byte on at
least one side, so it can never be flanked by two boundaries. The cost was measured before it was
accepted — **0 of this repo's 94 plan notes carry such a character**, the convention is already ASCII
slugs, and non-ASCII names are untouched because a non-ASCII byte is a NAME byte. The general shape
is one this document keeps restating: a check that validates elements cannot validate their
composition, and here the composition is *the matcher plus the set of names it is asked to match*.

**Scope, and what it deliberately does not touch.** Only `main`, and only `.claude/plans/*.md` or
`.claude/plans/briefs/*.html` (the second widened by CB-266 to match pre-commit-hook.sh's own
widening, on the same reasoning: `git add .claude/plans/` recursively sweeps `briefs/`, so once a
brief can land at all, this hook's reason for existing — an untracked stranger's file losing its
provenance — reaches it too) — on a branch there are no foreign untracked notes to sweep, so the
rule there would be pure friction on every `wip` commit, and everything else on main is pre-commit's
to refuse (duplicating that judgement would give one state two refusals that could drift).
**Deletions are in scope**, because
`git add <dir>` stages a removal too and deleting a stranger's note damages the same provenance.
**A merge is exempt**, and the discriminator differs from `pre-merge-commit`'s in a way that would
have inverted the rule if assumed: this section records that a clean merge writes no `MERGE_HEAD` —
true at `pre-merge-commit` time, which runs earlier and resolves the merge in memory, but by
`commit-msg` time git **has** written it, for clean and conflicted merges alike (measured, and
pinned, because if a future git stops doing it every integration would be refused). It is read
fail-closed with a count, exactly like pre-commit's arm: an empty `MERGE_HEAD` must not read as an
exempt one. **What the exemption therefore costs, said plainly:** a *deliberate* operator can put the
repo into a merge state (`git merge --no-commit`, or any conflicted merge), stage an unnamed note,
and commit — the naming rule is skipped. That is not a hole this gate opened; `pre-commit`'s merge
exemption already waves the whole staged set through on that path, which is the same evil-merge blind
spot the CI-limits list records for `main-invariants.yml`. The gate is an accident-stopper, and a
merge state is not something one enters by accident.

**`_guard_enforcement_armed` demands this hook too, since T-23 — and the paragraph this replaces
said the opposite, for a reason that was true at the time.** The guard reads `REPO_ROOT/tools/<hook>`
from the PRIMARY checkout and gates on whether the path has history, so adding the clause in the same
change that introduced the source would have made that change unlandable by the harness it extends —
the bootstrap wall for the third time — and `install-hooks.sh` could not pre-arm it either, because it
symlinks into main's `tools/`, where the file did not exist yet. So the hook landed first, armed by the
installer alone, and the guard followed once `tools/commit-msg-hook.sh` had history on main. The
condition is the SAME monotonic one `pre-merge-commit` uses — extracted into `_hook_source_known` and
called once per gated hook rather than copied, because a four-review-round condition in two places is
two rules one edit apart; `test_bootstrap_condition_is_one_function_called_per_gated_hook` counts the
call sites. A clone armed before T-23 is therefore refused at its next finish until
`tools/install-hooks.sh` is re-run, which is correct: it really is missing a third of its enforcement.
**What stays open:** the gate is invisible to the CI alarm, which reads paths and not messages, and
to `--amend`: an amend that changes only the message stages nothing against HEAD, so a note already
landed under a naming message can have that message rewritten. Both are authored acts rather than
accidents, which is what this hook is for.

**Two of the three hooks share a predicate — disjoint halves, neither redundant, and they must not
disagree.** (The third, commit-msg, shares nothing with them: it reads the message, they read refs,
and the paragraphs above are its whole story.) A CONFLICTED merge
never reaches `pre-merge-commit`, and neither does a merge this hook has already refused: both are
finished with `git commit`, which fires `pre-commit`. So the predicate is duplicated **byte-identically**
into `pre-commit-hook.sh` between `# ---8<--- SHARED MERGE-GATE PREDICATE` markers, and a test
compares the two blocks verbatim rather than grepping for a substring — the earlier substring test
was shown insufficient by rewriting the merge hook as a prefix test while leaving the regex
assignment in place, which kept it green. `worktree-finish.sh` no longer passes `--no-verify` to its
own merge: leaving it would have made the harness the single caller exempt from the gate.

**What this does NOT do, stated plainly because the honest scope is the point.** The local half is
CLIENT-SIDE and PER-CLONE: hooks and git config cannot be committed. A fresh clone has none of it
until `tools/install-hooks.sh` is run — which is why `_guard_enforcement_armed` refuses to integrate
from an unarmed clone, the one moment being unarmed can cost anything. **It checks all three hooks**
— pre-commit unconditionally, pre-merge-commit and commit-msg once their source is KNOWN (it has history, or the file is present, or the history probe itself failed — fail closed) — so a
clone armed before CB-57 or before T-23 is refused until `install-hooks.sh` is re-run. Even armed, all of
these move or publish `main` without passing any hook: `git rebase`, `git am`, `git reset --hard`,
`git push`, `core.hooksPath`, **`git subtree add`** (which commits via `commit-tree` plumbing — added
to this list because round-4 review landed content on main with it and the list did not mention it),
and **a CLEAN `git cherry-pick` or `git revert`**, where git's sequencer commits directly. Note the
case split on those last two, because an earlier version of this list was half-wrong: *clean* skips
the hook entirely, while the *conflicted* form is finished with `git commit` and **is** gated.

**That case split covers `commit-msg` too, and the first draft of this paragraph said the opposite.**
It claimed a clean cherry-pick or revert *does* run `commit-msg`, so the plan-note naming rule fires
on it. Measured on git 2.53: it does **not**. The sequencer commits directly and reaches **neither**
hook, so a clean `git cherry-pick` or `git revert` lands an unnamed plan note at exit 0; only the
*conflicted* form, finished with `git commit`, is gated. The claim was written into the very
paragraph that exists to list what the harness does NOT do, and it survived a green suite because
nothing pinned it — a gate described better than it behaves, in the section whose subject is exactly
that. `tests/test_worktree_harness.py::TestGitSequencerPremises` now pins both directions, so a git
version that starts running the hook turns the suite red instead of quietly making this paragraph
true. A typed
branch committed in the *primary* checkout also satisfies `pre-commit` while ignoring the worktree rule
entirely. **Most of these are what the CI job is for** — they flatten a non-merge commit onto main's
first-parent line, which is what `.github/workflows/main-invariants.yml` asserts against.

**`install-hooks.sh` sets `merge.ff=false` before anything arming-related can abort.** With it last, a
clone missing `tools/pre-merge-commit-hook.sh` — an older main, a `git checkout <old-commit>`, the
CB-57 bootstrap window itself — armed the pre-commit hook, printed its tick, then exited 1 at the
merge-hook step and left `merge.ff` **unset**: the installer could skip the one mechanism no hook can
replace. Reproduced in review, verified fixed by running it. Note the precise claim: four commands
still precede it (sourcing the guards, resolving the repo root, resolving the hooks dir, `mkdir -p`)
and each is fatal under `set -e`, so "a step that cannot fail goes first" — as an earlier draft of
this line put it — is not literally true, and review said so.

**The CI job's own limits, because a gate described better than it behaves is the failure this
section exists to record.**

1. It is scoped to a **pinned baseline SHA**, since main's history predates the rule. Moving the
   baseline forward is how a violation would be laundered, so it is a deliberate, reviewable edit,
   and a test asserts the SHA is a real commit here.
2. **Anything merge-shaped is invisible to it**, and `amend`/`rebase`/`reset` do **not** necessarily
   leave a non-merge commit on the first-parent line: `git commit --amend` on a *merge* stays a
   merge, `git rebase --rebase-merges` recreates merges, and a force-push to a fabricated merge-only
   history passes — `--no-merges` excludes all of it by construction. **DAG inspection cannot prove
   how a merge commit reached the ref; only a protected ref can.** An **evil merge** (content in
   neither parent) is invisible for a second reason: `git show --name-only` on a merge prints
   nothing.
3. It uses **`--no-renames`**, and that is not cosmetic: with rename detection on, `--name-only`
   prints only the *destination* path, so `git mv src/keep.py .claude/plans/keep.md` shows one
   allowlisted path and deletes source from main. Both this job and the pre-commit hook had that
   defect; both are fixed and both are pinned.
4. **A workflow cannot refuse a push by itself** — it reports afterwards. So this job is an
   **alarm**; the **gate** is branch protection on `origin/main`, ON since 2026-08-21 at a
   deliberately narrower scope than this list originally demanded. **Enabled: force pushes refused,
   branch deletion refused. NOT enabled: require-pull-request**, and with it the two settings that
   only mean anything behind a PR — marking `ci.yml`'s `tests` job required, and disabling squash-
   and rebase-merging. That scope was **ratified by the owner as sufficient for CB-59**, on this
   reasoning: force-push and deletion are the class **nothing local can catch**, because they
   rewrite or destroy history every local hook has already approved, whereas require-PR and a
   required check constrain *how work arrives* — already governed by `merge.ff=false`, the three
   hooks and `_guard_enforcement_armed` **for a clone that has run `tools/install-hooks.sh`**.
   CB-59 is closed at that scope, not at this paragraph's original four items.
   **One residual open, one closed, both measured rather than assumed.** Still open: an **unarmed**
   clone can push a non-merge commit straight to `main`, since require-PR is off —
   `main-invariants.yml` is the alarm for that, not a gate. Closed on 2026-08-22:
   **`enforce_admins` is now `true`**, so the protection binds the owner too, where it had been
   advisory against his own credentials. **The cost of that switch is accepted and named:** an
   emergency rewrite of `origin/main`'s history now requires first turning `enforce_admins` off, an
   explicit repository-settings act rather than a `git push --force` typed in a hurry — which is
   exactly the friction the setting buys. **All of this is repository configuration, not committed
   state, so nothing in this tree can verify or restore it, and a later measurement — not this
   paragraph — is the authority.**
5. **`main-invariants.yml` deliberately does not subscribe to `pull_request`.** A job skipped by an
   `if:` is reported as **passing** for required-status-check purposes, so marking it required would
   have produced a check that can never fail on the only path where protection evaluates it — this
   section's own "gate that cannot fire", reintroduced inside its own fix. Lint and tests therefore
   live in a separate `ci.yml` which does run on PRs. The trigger split is pinned **in both
   directions**, because a test asserting only the negative half left deleting
   `push: branches: [main]` outright green, turning "gate that cannot fire" into "workflow that
   never fires".
6. It needs **`fetch-depth: 0`** because the AUDIT step reads history: with a shallow checkout the
   baseline commit is absent and `origin/main` may not exist, dropping the audit back to `HEAD`.
   **`ci.yml` needs the same key for a DIFFERENT reason, and that asymmetry is why it was missed for
   months (CB-139): there the history is read by the SUITE itself.** Exactly one test in the suite
   reads this repository's real history — `test_ci_workflow_asserts_the_first_parent_invariant` —
   so under the default depth-1 checkout `ci.yml`'s `tests` job was red in CI **always** and green
   in every local run, and a gate that cannot pass hides the regressions it exists to catch.
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
   whole `tests` job off is the same shape and this test does not look at it. `contracts` stays
   shallow deliberately: it runs `tests/test_cli_signals.py` and `tests/test_fsio.py`, neither of
   which reads history.

→ почему именно так, с замерами и раундами ревю:
`docs/claude-md-rationale/workflow.md#пределы-ci-задачи`

**`.python-version` is the SINGLE SOURCE for the interpreter — of main, of every worktree and of
CI — and `_guard_interpreter_matches_main` refuses to land work the two of them did not agree on
(CB-135).** "Single source" is a claim about what DECIDED, not merely about what is written down,
and it needs that reading because **`UV_PYTHON` and `--python` outrank the file** (measured). So the
guard does not stop at "the pin exists": it requires the interpreter uv actually chose to be the one
the pin asked for, and refuses when something outranked it. Without that clause an exported
`UV_PYTHON` made BOTH trees answer with the override, they agreed, the gate passed, and the branch
landed a different pin that main would adopt on its next `uv run` — CB-135 rebuilt out of the very
mechanism this section documents. Cross-model review found it; the first draft had only the
existence check. The gate runs the suite in the WORKTREE and the work lands in MAIN; those are two
statements, and on 2026-08-22 they came apart. A manager reported "1943 passed" from a worktree on
3.13.3 while the same suite on the landed main, under the documented command, gave "1 failed, 1942
passed" on 3.14.4. The red was on main BEFORE the merge and no finish could ever have seen it. The
rule immediately above — never validate a worktree's changes from main, because `pythonpath=["src"]`
resolves against the checkout you run in — is correct for its own reason and is exactly what
introduced a second, unnamed variable: **which python**. Before the pin there were three untracked
trees — main took the system interpreter its `.venv` was built with, a fresh worktree took uv's
default (the newest uv-MANAGED install, which is a different thing), and
`.github/workflows/ci.yml` named no version at all. `uv.lock` had already fixed the *dependency*
versions, which is what made the interpreter the conspicuous remaining one; it is **not** true that
everything else is nailed down, and an earlier draft of this sentence said so. uv's own version,
the platform, and the BUILD of a given CPython all still vary, and the guard below compares a
version STRING, so two builds of 3.14.4 read as identical to it.

**The pin is `3.14.4`, full patch, and both halves of that were chosen rather than defaulted.**
*Which version:* it is what main already runs, so landing the pin moved no environment and opened no
window in which main was stale; the suite is green on it (1949 passed, measured on main after CB-134
landed) and equally green on 3.11.12, 3.12.10 and 3.13.3, so no red version forced the choice and
the newest stable CPython is where the tested surface should sit. *Full patch rather than `3.14`:*
this unit's whole subject is making a divergent state unrepresentable, and `MAJOR.MINOR` leaves one
representable — uv resolves it to whatever 3.14.x a given machine happens to have, so two machines
legitimately differ. The cost is real and is the ordinary cost of a pin: it must be bumped by a
deliberate, reviewable edit, and a machine without that exact build downloads one (measured:
`cpython-3.14.4-linux-x86_64-gnu` is `<download available>`, which is also what makes the pin
reachable in CI — note `uv python list` shows only the newest patch per minor, so checking
downloadability needs `--all-versions`).

**`uv` rebuilds a mismatched environment by itself, and the design brief assumed the opposite.**
Measured: with `.venv` at 3.13.3 and the pin at 3.14.4, a plain `uv run` printed "Removed virtual
environment", recreated it and ran — no `uv sync` needed. So the pin does most of the work on its
own and the guard is there for what it cannot reach. Two consequences worth knowing before touching
this: `UV_PYTHON=` OUTRANKS the pin file, and a subsequent plain `uv run` snaps the tree back to the
pin (both measured), which is why the repair command below is written with `UV_PYTHON=` and why its
effect only has to survive until the finish completes.

**What the guard compares, and why the two sides are probed differently.** The worktree side is
`uv run --extra dev python -c <probe>` — byte for byte the launcher `[6/7]` uses for pytest, so the
answer IS the interpreter the suite will run under, and the call syncs the worktree to the pin as a
side effect, which is wanted on a tree we are about to test. The main side is
`<repo_root>/.venv/bin/python` executed DIRECTLY, deliberately not through `uv run`: `uv run` in main
would rebuild a checkout other sessions are working in, and a guard must not mutate the tree it is
judging. It also answers the exact question the incident asked — what main ACTUALLY has, not what it
would acquire next time somebody ran something there. `pyvenv.cfg` is not read: it describes an
environment rather than being one.

**Fail-closed, in the form of `_guard_interpreter_matches_main` itself.** No `.venv` in main, no `uv`
on `PATH`, a non-zero rc, an empty answer, an unparseable answer — every one refuses with **exit
14**. The version-shape check (`_interpreter_version_is_sane`) was meant to earn its keep in exactly
one state — both probes failing with `""` on each side, which `"" == ""` would otherwise wave through
as agreement — and `test_two_undeterminable_sides_refuse_rather_than_agree` was written to pin it.
**That test stopped discriminating the mutant once the UV_PYTHON-outranks-the-pin check (above,
`.python-version` bumping section) existed alongside it, and the reason is NOT that the checks got
reordered (CB-140).** The shape check on `wt_ver` still runs first in the function, exactly where it
always did; the pin check sits further down and is unchanged in position. What changed is that the
pin check ALSO refuses an empty `wt_ver` on its own — `""` is unequal to the pin and does not extend
it with a dot — so with the shape check neutered by a mutant, execution simply falls through to the
still-present pin check, which independently produces the same **exit 14**. The test asserts only the
return code, so it cannot tell which of the two refused: measured, a mutant turning
`_interpreter_version_is_sane` into `return 0` left that test, and the entire 248-test harness suite,
green. The state the PIN check alone cannot catch — where only the shape check stands between a pass
and CB-135 recurring — is a NON-version that PREFIX-MATCHES the pin: a bare pin like `"3"` accepts
anything spelled `"3."` + more as if it were a legitimate patch release, so a stub `"3.0"` on both
sides clears the pin check by looking like one while still failing the strict `X.Y.Z` shape the
sanity check demands — there the shape check is the only backstop, and neutering it is the only way
to reach the final `wt_ver == main_ver` comparison and get exit 0.
`test_two_prefix_matching_non_versions_refuse_rather_than_agree` is what actually holds the
version-shape check now; the older test is kept as a premise fixture (both probes genuinely absent)
but is no longer sufficient on its own.

**The guard also demands that the worktree carry its own `pyproject.toml`, and that is not
tidiness.** `uv run` resolves a project by walking UP, and every worktree lives INSIDE the repo at
`.worktrees/<slug>`, so a worktree missing that file resolves against MAIN's project: measured, `uv
run` from such a directory answered with main's interpreter **and** imported `codebugs` from main's
`src/`. The guard would then have compared main against main — an agreement that can only ever hold,
which is a gate that cannot fire, in the change whose subject is precisely that.

**Phase, and `--skip-checks`.** The call sits in `[5/7]` AFTER the forward-merge — a
`.python-version` arriving from main must be in the tree before it is judged — and BEFORE `[6/7]`,
so a refusal costs seconds instead of the ~70s suite run it is declaring meaningless. It is outside
the `--skip-checks` branch: that flag skips ruff and pytest, which are CHECKS, and this is what
decides whether running them would have meant anything. Both bounds are pinned structurally and the
`--skip-checks` half behaviourally as well, by a class that runs the script end to end. **Anchor
that structural test on the `git merge` and not on the echo announcing it** — review moved the call
between the two and the first version stayed green, because "after the text that says a merge is
coming" is not "after the merge".

**And it is asserted a SECOND time, inside the lock, because a pre-check is not an invariant at
landing time.** main's `.venv` is gitignored, so `_guard_main_clean` cannot see it move and the
in-lock SHA re-checks are about commits: a `UV_PYTHON=… uv sync` in main during the ~90s suite run
would land work tested on one interpreter onto a main that now has another. That is exactly the skew
`TESTED_MAIN` exists for, reaching main through the one piece of state neither guard watches, so it
gets the same answer — re-assert where nothing can intervene before the merge. It is a second CALL
rather than a stored sample (~100ms) so it cannot drift from the thing it is checking. Also found by
cross-model review.

**A shared `.venv` is refused, and the comparison is between DIRECTORIES.** Point main's `.venv` at a
worktree's and the two sides become one environment: it can only agree, and the worktree removal at
the end of the finish then leaves main's link dangling. Compare the resolved venv *directories*,
never the interpreters they resolve to — two honest venvs built from one system python both resolve
to a single `/usr/bin/pythonX.Y`, so an interpreter-level test would refuse every ordinary case.

**Bumping the pin is a two-step procedure, and the guard makes the order mandatory.** A branch that
changes `.python-version` is refused until main is brought to the NEW interpreter first —
`(cd <repo_root> && UV_PYTHON=<new> uv sync --extra dev)`, then re-run the finish. A bare `uv sync`
is not enough there: it re-reads main's OLD pin and puts the old interpreter straight back. The
refusal prints that command with both versions filled in, because a gate with no way out is a wall
rather than a diagnostic.

**The usual bootstrap wall applies, and it is milder than CB-57's.** `worktree-finish.sh` runs from
the REPO ROOT, so the script that lands this change is MAIN's copy — which does not yet contain the
call. The commit introducing the guard is therefore not gated by it; the first finish AFTER it lands
is the first gated one. Nothing special is required (unlike CB-57, no re-run of `install-hooks.sh`),
because this guard is in the script rather than in an installed hook.

**What this does NOT do.** It is per-clone and client-side like the rest of the harness — it says
nothing about the interpreter any other machine or CI actually used, only that these two trees
agree. It compares a version string, so two builds of the same version with different compile-time
options read as identical. A `.python-version` naming a build this machine cannot obtain fails at
`uv run`, which the guard reports as undeterminable — correctly, but the message will be uv's rather
than a diagnosis of the pin. And the pin is required to be a plain `X`, `X.Y` or `X.Y.Z`: uv also
accepts implementation and platform requests (`pypy@3.11`, a full `cpython-…-linux-…` triple), and
rather than guess what one of those resolves to the guard refuses and says so.

- **Create:** `tools/worktree-setup.sh <type>/<slug> [base]`, which validates the name, refuses a
  card already carried by another branch, **claims every card the branch names through the claims
  ledger**, creates `.worktrees/<type>-<slug>`, and primes the worktree's own dev environment.
  **The claim is a real claim now (CB-58), and this bullet used to say the opposite** — it read
  "flips an `open` card to `in_progress` … a best-effort status write, not a claim", which was
  accurate then and is the defect that was fixed. The status flip is not gone, it is *subsumed*:
  it arrives as the claim's projection (`EntityKind.busy_status`), so the card still reads
  `in_progress` while the branch holds it. What is new is that the write now carries a **holder
  triple** (`--holder <branch> --holder-kind branch --repo <root>`), mutual exclusion is the
  partial unique index rather than nobody, and there is a release path — including
  `_auto_release_on_terminal`, so closing the card releases the claim in the same transaction.
  **Order is load-bearing: the claim happens BEFORE `git worktree add`,** for the same reason
  `_guard_branch_type` does — otherwise the losing side of a race owns a branch and a directory by
  the time it is told no. **Exit codes are handled as the API they are**: `3` (held by someone
  else) is FATAL and prints the incumbent's triple — this is the **setup gate**, the one tracker
  call in the harness allowed to abort; `4` (already resolved) warns and proceeds, because a
  follow-up branch on a closed card is legitimate; `5` (undetermined) is retried **once** with the
  identical call, which converges rather than double-claiming because the primitive is an
  idempotent upsert. An **EXIT trap** releases whatever the run took if setup aborts, armed after
  the first successful claim and **disarmed on success** — leaving it armed would make every setup
  that *worked* release its own claim on the way out. `CODEBUGS_SETUP_NO_CLAIM=1` still skips the
  tracker entirely and is the documented escape hatch past a `3`; **`--allow-duplicate`
  deliberately does not punch through it**, because it answers a different question (another
  *branch* carries the id) and, since this repo never deletes merged branches, it is needed for
  ordinary follow-up work — overloading it would make the claim gate routinely bypassed. The
  *branch-name* collision check remains, and it is still the half that works with no tracker at
  all, because it is pure git. **What this does NOT do, and the honest scope is the point: a branch
  abandoned AFTER a successful setup still leaves a live claim.** Steal and expiry stay deferred by
  design (Claims module, below). That is strictly better than the anonymous `in_progress` it
  replaces — `codebugs who-holds` names the holder and repo, and any close releases it — but it is
  not the claim disappearing. One concern per branch; a card-driven branch carries its id
  (`fix/cb-48-tracker-root-init`). Work already started on main moves over with `git stash push
  <files>` → setup → `git stash pop` in the worktree; the stash is shared across worktrees because
  it lives in the common git dir.
- **Worktrees live in `.worktrees/`,** slug = branch with `/`→`-`, matching autosorter. Both that
  directory and the legacy `.claude/worktrees/` are gitignored; the legacy path still works and
  `worktree-finish.sh` resolves either, but new worktrees go in `.worktrees/`.
- **Then work there, entirely.** Check which checkout you are in before any `Edit`/`Write` to a
  source file. **A surgical `git checkout <branch> -- <files>` onto main is editing main directly**,
  wearing a hat. Conflicts get resolved *inside* the worktree, never by committing a resolution on
  main.
- **Tests and lint run in the worktree, and it needs its own environment.** `uv run --extra dev
  python -m pytest tests/ -q` — **`--extra dev` is not optional there.** `pytest` and `ruff` live in
  `project.optional-dependencies`, which `uv run` does not install by default, so a fresh worktree
  dies with `No module named pytest` while main — synced long ago — works without the flag; the
  documented commands under **Testing** below are written for main and are incomplete here. `uv run`
  does build the worktree's own editable install pointing at the worktree, so once the extra is
  there, the isolation is real. **Never validate a worktree's changes by running the suite from
  main**: `pythonpath = ["src"]` resolves against the checkout you run in, so that tests main's
  source and passes on a tree you did not touch. The mirror-image trap is at the MCP-registration
  rules — from a worktree, a bare `python` reaches `codebugs` through main's editable install, which
  is why `tests/dump_schema.py` must be run with `PYTHONPATH=src`.
- **Integrate with `tools/worktree-finish.sh <slug> ['commit msg'] [--merge-msg '…']`.** It commits
  any dirty state, runs the guards, forward-merges main *into the worktree* so conflicts surface in
  safe space, runs `ruff check` and the full suite there against the combined tree, then merges onto
  main with `--no-ff` under the lock and removes the worktree. The merge
  commit is what makes a card's whole iteration recoverable as one unit; a fast-forward scatters it.
  **Never delete the branch** — no merged branch has ever been deleted here, and that is the record;
  the script removes the worktree only.
- **The integration message follows `Merge <branch>: <what changed> (CB-NN)`, and when it is not
  given it is derived from `main..<branch> --first-parent --no-merges --reverse` — the FIRST commit
  on the branch's OWN line among the commits main does not have (CB-116). This bullet used to say
  "the branch and last subject", which was the defect.** The old derivation read `git log -1
  --no-merges` on the worktree tip, which the forward-merge two steps earlier had just filled with
  main's commits: landing CB-111 produced a merge closing CB-111 whose subject was an unrelated plan
  note naming CB-113/114/115. Reproduced end to end in a throwaway repo before the fix and gone after
  it. **The defect was never topological** — `git log` orders by commit date, so it only bites when
  main's commit is NEWER than the branch's last, which is the ordinary case and which is why a
  fixture whose commits share one second is green against the bug. Three things follow.
  **`--first-parent` is load-bearing, not decoration, and restricting the range alone is NOT enough**
  — the first draft did exactly that and both adversarial reviewers reproduced the same regression
  independently: a branch that merges a SIBLING branch absorbs its commits into the range, and if the
  sibling is older (the ordinary case) date order puts it first, so the derived subject names the
  sibling's card. On that shape the range-only fix is **worse** than the `log -1` code it replaced.
  Following first parents skips every absorbed lineage, main's forward-merge included.
  **The FIRST commit of that line wins, not the last**, measured over main's own first-parent line:
  of the 47 integration merges whose branch carried ≥2 commits, the first commit's subject was judged
  closer to the message a human wrote in 38 and the last in 7. That split is a **judgement and does
  not partition the 47** — two are unclassified either way, and the 38/7 cannot be re-derived
  mechanically; only the 47 and the five `wip(cb-NN): checkpoint before …` openers reproduce. Branches
  here end on review fixups ("close the altitude findings"), which describe an iteration's tail rather
  than its subject. Do **not** write that as `--reverse -1`: git applies the count BEFORE reversing,
  so it returns the NEWEST commit and silently restores the behaviour this removed (measured).
  **A branch with no commit of its own carrying a subject is REFUSED rather than guessed** —
  reachable when the content arrived through a merge commit, since `_guard_nonempty_diff` has already
  proved the content is real — and the derivation therefore runs at the `TESTED_MAIN`/`TESTED_HEAD`
  sample rather than under the lock, so that refusal costs nothing instead of the whole ~70s gate run.
  The refusal tests the POPULATION, not its first line: `git commit --allow-empty-message` puts a
  blank line at the head, and reading that as an empty population produced a false refusal that also
  asserted something untrue about the repository. It cannot drift from what lands: both inputs are the
  pinned `TESTED_*` values and the in-lock re-checks refuse with exit 13 if either moved.
  **Rejected: refusing to derive whenever main moved.** Level-(2) sessions commit plan notes to main
  continuously, so "main moved" is the common case, and that form would have turned a default into a
  mandatory argument on nearly every finish while the correct subject was sitting right there in the
  range. **One limit stays open and is documented rather than guessed at:** `worktree-setup.sh
  <branch> [base]` can cut a branch from a NON-MAIN base, whose commits sit on this branch's own
  first-parent line, so the derivation names the base's first commit. No ordering flag reaches it
  (measured: `--first-parent` and `--topo-order` both pick the base commit) because it is not a
  traversal question — the commits really are this branch's ancestry and this merge really does land
  them. Pass `--merge-msg` on a branch cut from a non-main base.
- **Every re-run hint echoes back the `--merge-msg` the aborted run was given**, and that half is
  orthogonal to the derivation — it would be needed even if the derivation were perfect. The exit-13
  refusal fires precisely BECAUSE main moved, and it used to print the bare short form, so the
  refusal routed the operator into the derivation that main's move had broken. That is how the
  observed CB-111 subject was produced. One `_retry_hint` builds the line for all four refusal paths
  (forward-merge conflict, main moved, branch moved, merge failed) and a test refuses the exact
  literal `echo "      tools/worktree-finish.sh ${SLUG}"` — the spelling that regressed — while the
  helper-call count is what holds the other three sites. It echoes the `--merge-msg` and nothing
  else, deliberately: `--skip-checks` and `--allow-stale-base` are relaxations, so dropping them
  makes a retry stricter, and the positional commit message applies only to a still-dirty worktree.
- **`ruff check` is the lint gate; `ruff format` is deliberately not**, because a large part of the
  existing tree is non-conformant to it and gating on it would refuse every finish. Pin ruff 0.15.7:
  0.16.x flags the whole repo.
- **Session end:** `git status` clean in main *and* in every worktree, then `git worktree remove
  <path>`. Never `--force`: a removal that refuses is telling you work is uncommitted there.
- **The only thing that may land on main directly** is a `.claude/plans/*.md` note — one level, not
  a subtree — or, since CB-266, a `.claude/plans/briefs/*.html` daily brief — one level under
  `briefs/`, not a subtree of it either, and no other extension. The pre-commit hook holds that
  line. **Name the note in the commit message, and add it to the index by name**:
  `git add -- .claude/plans/<note>.md`, never `git add .claude/plans/`.
  The commit-msg hook refuses a plan note the message does not name, which is the mechanised form of
  that rule (see the Workflow paragraphs above for why naming is the discriminator). `git commit
  --no-verify` remains the escape hatch for both hooks: they exist to stop the accident, and an
  operator typing the flag has stated an intent.

**How the harness itself is tested, and where that stops.**
`tests/test_worktree_harness.py` covers every guard on both sides — the state it must refuse and the
state it must allow — **and separately asserts that `worktree-finish.sh` actually calls each one**.
That second class exists because it had to: two adversarial reviews deleted guard *invocations* from
the script, including the branch-type guard that exists for the 2026-08-16 incident, and the whole
suite stayed green, because nothing executed the script. Every guard was unit-tested and the
composition was not — this repo's own rule (*a check that validates elements cannot validate their
composition*) turned on its own harness. Do not read the per-guard tests as covering the wiring.

**A test can be worse than absent: it can be vacuous AND leave litter.** `TestKnownLimits` — the pin
for the one limit this design chose to document — passed `"--git-path hooks"` to `git rev-parse` as a
single argv token. `rev-parse` echoes an unrecognised option-looking argument back and exits 0, so the
"hooks directory" resolved to a *relative path with that literal name*, the hook was copied into a
directory called `--git-path hooks` in the repo root, the test repo got no hook at all, and asserting
`rc == 0` could never fail. It stayed green even with the entire merge hook reverted. Worse, the suite
**committed** that 11 KB directory to the branch, and `git status` stayed clean because every run
regenerated it byte-identically. Found by round-3 review, not by the suite. Two lessons worth keeping:
a test that sets up its own fixture must **assert the fixture exists**, and `git rev-parse` is not a
safe place to be sloppy with argv.

Executing the whole script in a test is impractical (it merges onto main and runs the full suite),
so the wiring tests are structural: they read the script and assert each guard is invoked with
`|| exit $?`, in the right phase. Said plainly rather than left to look behavioural. **That
"impractical" is narrower than it reads, and CB-116 is the proof**: `TestMergeSubjectDerivation` runs
`worktree-finish.sh` end to end in a throwaway repo under `--skip-checks`, which disables ruff and
pytest and *not* the safety guards, and the merge it lands is onto that repo's main. So a property of
the SCRIPT'S OUTPUT — the subject it writes — can be tested behaviourally, and had to be: the CB-116
defect was invisible to every structural test here, because the defective code called `git log`,
which is exactly what a structural test would look for. It also caught what structural reading could
not — the sibling-branch regression above was found by review, but it is the behavioural fixture that
holds the line. What stays impractical is the gate run itself, not the script. Three more
structural tests landed with CB-57, all of the same kind: the integration merge must **not** carry
`--no-verify`, the installer must arm the merge hook and point it at main's checkout, and the CI
workflow must carry a baseline SHA that is a real commit in this repository. Each pins a property
whose failure mode is silent — a gate present in the tree and absent in effect.

**The branch predicate is constructed FOUR times across THREE files** — `_guards.sh` once,
`pre-commit-hook.sh` twice (its own branch check, plus the copy inside the shared merge-gate block),
`pre-merge-commit-hook.sh` once — because neither hook may source the library (each runs from
`.git/hooks/` as a symlink and must work when `tools/` is missing from the checked-out tree). This
sentence used to say "three copies", which was the *file* count; the test it credited counted per
file too, and round-3 review showed the consequence: degrading `pre-commit-hook.sh`'s own regex to a
prefix test left that test **green**, because the shared block's copy still matched the grep. It now
counts constructions per site. Same types is *not* the same predicate — a prefix test accepts
`fix/a/b`, which `_guard_branch_type` refuses — so a divergence would let a branch clear the finish
guard and then be refused by the merge hook, after the whole suite had already run.

**Byte-identical is not the same claim as "the two hooks agree", and that cost a round.** The shared
predicate used to take a second argument — what the caller typed, from `GITHEAD_` — and judge that
ref alone when it resolved. Identical code, two different rules, because only `pre-merge-commit`
*has* that argument: review reproduced `git branch fix/tmp <untyped-sha>; git merge fix/tmp --no-ff`
landing on the clean path while the identical state was refused on the conflicted one, and the
byte-identity test structurally could not see it because the divergence lived in the arguments. The
predicate now takes only the merge head, so both callers pass identical information. **The general
form, which this repo keeps relearning: sharing an implementation does not share a decision if the
callers supply different inputs.**

**Cherry-pick and revert lost their exemption — and the honest scope is narrower than "they are now
refused".** The merge-in-progress exemption used to fire on mere existence of `MERGE_HEAD`,
`CHERRY_PICK_HEAD` *or* `REVERT_HEAD`. Only the first was hardened, and review reproduced the rest:
`: > .git/CHERRY_PICK_HEAD` then `git commit` landed arbitrary staged content on main **and** skipped
the branch-type check, so one empty file turned off both of this hook's rules — reachable the same way
empty `MERGE_HEAD` was, since a conflicted cherry-pick leaves the file until `--continue`/`--abort`.
The fix was to stop exempting them.

**But a CLEAN `git cherry-pick` or `git revert` onto main never reaches `pre-commit` at all** — git's
sequencer commits directly — so it still lands, verified by running both. An earlier draft of the row
above read "cherry-pick / revert get **no** exemption on main … exit 1", which described a gate that
cannot fire: the identical category error this section corrects for `main-invariants.yml`, committed
in the same table two rows apart. What the change actually buys is that a *marker file* no longer
launders a commit. Clean cherry-pick and revert onto main are caught only by the CI alarm (they leave
a single-parent commit on the first-parent line), and that is the honest statement.

**The same fail-closed validation is NOT scoped to main**, because the exemption it guards is not:
while it was, `: > .git/MERGE_HEAD` on an untyped branch still skipped the branch-type check. Only the
head-*acceptability* rules — typed branch, or upstream `main` — are about main.

**The bootstrap is a real constraint, not an oversight.** `worktree-finish.sh` cannot land the
commit that first creates `tools/` — `_guard_enforcement_armed` refuses, because main has no
`tools/pre-commit-hook.sh` for the hook to point at. CB-50 was therefore merged by hand once, with
`git merge --no-ff`, after the harness had run its whole pipeline on the branch and refused at the
lock. **CB-57 hit the same wall in miniature and was designed around it rather than merged by hand** —
`_guard_enforcement_armed` runs *before* the merge that first puts `tools/pre-merge-commit-hook.sh` on
main, so an unconditional check would have made the commit introducing the hook unlandable by the
harness it extends. **The condition must be MONOTONIC, and the obvious version was a live defect:**
gating on "does the file exist" meant one `rm tools/pre-merge-commit-hook.sh` both dangled the
installed hook (git skips a dangling hook silently) *and* made the guard skip its check and return 0
— a permanent, flagless disarm, landable on a perfectly typed branch, reproduced end to end by both
reviewers. The gate is now whether **the path has history on main**, which deleting the file cannot
undo: after CB-57 the check is genuinely unconditional, and a missing source reports as "cannot verify
the hook identity" instead of vanishing. So **run `tools/install-hooks.sh` right after that merge** or
the next finish refuses — correctly, since a clone armed before CB-57 really is missing part of its
enforcement.
Every landing after that goes through the harness. If `tools/` is ever rewritten the same way,
expect the same one-time manual merge.

## Releasing

1. Bump `version` in `pyproject.toml` and `__version__` in `src/codebugs/__init__.py` —
   `tests/test_release_version.py` refuses a disagreement, the installed distribution included.
2. Retitle `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] — <date>`, leave an empty
   `## [Unreleased]` above it, and open the section with a highlights paragraph written for a user.
3. **After** the branch lands, tag the merge commit from the primary checkout
   (`git tag -a vX.Y.Z <merge-sha>`) — a tag made on the branch points at a commit that never landed.

## Architecture

- **Domain modules** (`src/codebugs/`): `db.py` (findings + shared infra), `reqs.py`, `bench.py`, `blockers.py`, `merge.py`, `sweep.py`, `embeddings.py` (vector storage/similarity search, delegates from reqs), `milestones.py` (releases / streams / capacity-aware pull)
- **Shared types** (`types.py`): Entity constants (statuses, priorities, severities), resolver functions, terminal states. Zero-dependency — safe to import from anywhere
- **MCP server** (`server.py`): Thin `MCPServer` orchestrator (~48 lines). Discovers tool providers via registry, filters by `--mode` flag. Requires the mcp 2.x SDK (`mcp.server.mcpserver.MCPServer`, which replaced 1.x's `mcp.server.fastmcp.FastMCP`)
- **CLI** (`cli.py`): Thin argparse orchestrator. Discovers CLI providers via registry, filters by `--mode` flag. Two entry points, and the split is load-bearing: `main()` is the importable body (three test modules call it in-process), while `run()` — what `[project.scripts]` and `python -m codebugs.cli` reach — first restores the POSIX `SIGPIPE` disposition (CB-78) and then refuses to run at all when stdout is already closed (CB-134). This line used to claim "~40 lines"; it was 159 before that change and is larger now, so the count is dropped rather than re-guessed
- **Formatting** (`fmt.py`): Shared CLI output utilities (ASCII table formatting). Text for a stream, nothing else — file writing deliberately does NOT live here (CB-76)
- **Filesystem output** (`fsio.py`): `atomic_write` — the only sanctioned way a CLI handler writes a file. Owns tempfile lifecycle, destination classification and atomic replacement; imports nothing from the package. See the export rule under **CLI** below
- **Storage**: Single SQLite DB at `.codebugs/findings.db`; each domain module owns its schema via `ensure_schema(conn)`

### Known architectural debt

- ~~Staleness/provenance logic pending extraction~~ — **done, with one seam left.** `provenance.py` owns the staleness and commit-trailer logic (`file_status`, `check_findings`, `resolve_trailers`) and registers its own tools and CLI; `provenance` is a first-class `--mode`. This entry claimed the logic still sat in `db.py` long after it had moved (CB-4). Remaining seam, not worth its own card yet: `provenance.head_sha` only delegates to `db.git_rev_parse` and has no callers, while findings' provenance auto-capture (`findings.py:546`, `:581`) calls `db.git_rev_parse` directly.
- **`db.connect()` import trigger**: `_ensure_modules_loaded()` still imports all known domain modules so their `register_schema()`, `register_tool_provider()`, and `register_cli_provider()` calls execute. All three registries are complete (ARCH-001 + ARCH-002 + ARCH-004). This trigger will be replaced by auto-discovery.
- ~~`blockers.py` cross-module reach into private `_row_to_dict`~~ — **resolved.** `blockers.py` calls the public `db.row_to_dict()` (`blockers.py:87`, `:307`, `:442`) and does not reach into `reqs` at all; no private `_row_to_dict` exists anywhere in the package. This entry outlived the code it described (CB-5).
- **Findings naming exception**: The findings domain predates the naming conventions. Its MCP tools (`add`, `query`, `stats`, etc.) lack the domain prefix that most other modules use (`reqs_add`, `codebench_import`). Renaming MCP tools is a breaking change for clients. Findings is not quite alone — `provenance.py` exposes an unprefixed `staleness_check` too — so treat the prefix as the rule for new tools rather than as an invariant that currently holds.
- **Milestones naming exception**: The milestones spec mandates spec-canonical tool names (`pull_next`, `release_item`, `triage_dismiss`, `mark_branch_only`, `wip_status`). These are kept verbatim because external consumers (autosorter's `worktree-setup.sh` / `worktree-finish.sh`) call them by name. Milestone management tools (`milestone_create`, `milestone_status`, `milestone_close`, ...) do carry the domain prefix.
- **Post-add hook**: `db.register_post_add_hook(name, fn)` is the extension point that lets `milestones.auto_route_finding` run inside `add_finding` / `batch_add_findings` before the final commit, so the finding and its `stream/triage` link land atomically. Other modules may register additional hooks. Since CB-43, hooks fire **only on genuine inserts** — a deduplicated observation (bump/reopen) does not re-fire them, because the matched card already has its projection.
- **Pre-add resolver seam: built with its first consumer (CB-45; CB-44 had refused to build it speculatively).** `db.register_pre_add_resolver(name, fn, *, meta_keys, updatable_keys=())` runs resolvers inside `_add_one`'s insert path — ANNOTATE-ONLY: a resolver returns a meta patch or `None`, and redirect is deliberately inexpressible (identity is core, CB-44 ratified; a future redirect variant is a new negotiated contract). The never-commit contract is ENFORCED, not documented: the runner refuses to run outside an open transaction (outside one, `SAVEPOINT`+`RELEASE` *is* a commit — verified), detects a resolver that closed the caller's transaction after every call (and again after its own `RELEASE`) and raises OUTSIDE the failure-swallow, and guards its own `ROLLBACK TO` cleanup so it never masks the real error (all three were cross-model review findings). Each resolver runs under a SAVEPOINT named with a per-call NONCE plus index — index-only naming let a resolver commit and recreate the runner's savepoint by its predictable name, turning the runner's own `RELEASE` into a commit (Codex diff review); a failure rolls back only that resolver's writes and is stamped QUERYABLY into `meta.resolver_errors` (`query(meta_key="resolver_errors")`), stderr being only the immediate channel. Each resolver receives a DEEP COPY of the observation, and validated outcomes are snapshotted into the patch — the runner reads `at` for its own error stamp and the patch becomes the row's meta, so a shared reference let a failing resolver poison the stamp and abort the add at serialization. Outcomes are validated inside the savepoint (dict, string keys, `json.dumps(..., allow_nan=False)`) so a bad patch can never abort the add later at `meta_final` serialization. `db.resolver_reserved_meta_keys()` AND `run_pre_add_resolvers()` both call `_ensure_modules_loaded()` first — neither the reserved set nor the resolver registry may depend on which modules a process imported (the common `meta=None` add path never touches the reserved set, so a bare library connection used to run with an empty registry and silently skip annotation). The FIRING RULE has one predicate: `finding_id is None and annotate` — an explicit id bypasses the whole observation machinery, and `dedup_action` is context, never the predicate (an explicit-id insert also carries `"created"`). CSV import passes `annotate=False` (an import is not an observation) and strips the DYNAMIC reserved union from stored meta, or an annotated export would be refused on re-import.
- **Status-change hook**: `db.register_status_change_hook(name, fn)` is the update-side twin of the post-add hook — same registration discipline, same in-transaction contract, same swallow-and-log policy. `findings.update_finding` and `reqs.update_requirement` fire it **only when the write actually changed the row** (a status was requested, `rowcount == 1`, and the value differs). `claims._auto_release_on_terminal` uses it to close a claim in the same transaction as the status write that resolved the entity, and `milestones.reconcile._reconcile_on_terminal` uses it to project that status onto the entity's milestone items.
- **A DERIVED queue must not be trusted to a write-time hook alone (CB-26).** `milestone_items` is a *projection* of a finding or requirement, but routing ran once at add time and nothing moved the item when the source resolved: 19 of 23 open `stream/triage` rows pointed at terminal findings, so `triage_inbox` was ~83% stale and `pull_next` could hand an agent finished work. The hook above is half the fix; the other half is the read-side filter — **since CB-31 that is `reconcile.live_source_clause`, one SQL fragment**, not a per-row Python predicate re-applied by hand. The call sites are enumerated by `TestLiveSourceClauseCallSites`, not here — a count belongs in a test, and the two places this document left one in prose it was wrong. **That second half is not belt-and-braces — it is the only reason the invariant holds**, because five writers bypass the hook entirely: `add_milestone_item` inserts `open` regardless of the source, `set_item_status` and `release_item(status='abandoned')` reopen, the requirements bulk and markdown importers write statuses without going through `update_requirement`, and `EntityRef.set_status` deliberately never fires hooks. **The general rule: eager reconciliation keeps stored state honest; only a read-side filter can make a guarantee, and you must enumerate the bypass writers before claiming one.** Four traps, each of which cost a review round: **(1)** select by `item_kind` as well as `item_ref` — `_validate_item_ref` skips externals and `UNIQUE` includes `item_kind`, so `(bug, CB-1)` and `(external, CB-1)` are both legal and only the first is a projection; **(2)** the predicate is `status != target`, not "not terminal" — both updaters permit `fixed → wont_fix`, which must remap `done → dismissed` — while `deferred` is excluded, because no queue returns a deferred row and closing one only destroys the deferral record; **(3)** decrement capacity *before* clearing `assigned_agent`, since the row is the only record of who held the slot; **(4)** `run_status_change_hooks` swallows and the caller commits anyway, so a multi-row hook needs a `SAVEPOINT` or it commits a partial reconciliation behind a success-shaped return — and "it logs to stderr, so it's visible" is false for an MCP caller, which is why the failure is recorded as an audit row instead. The hook is scoped to `kind='stream'` because `milestone_close`'s unfinished gate reads only item status and `done_commit` is never a gate, so auto-marking a release item `done` would let a release close over a missed integration (CB-32).

- **The read-side filter is now a SEAM, and its justification is the WRITE LOCK, not anti-drift (CB-31).** `source_is_terminal` cost two queries per row — a `sqlite_master` probe plus a status `SELECT` — and `capacity._candidates` ran it *per candidate row inside `pull_next`'s `BEGIN IMMEDIATE` window*, so every concurrent agent waited behind it. `reconcile.live_source_clause(conn, *, alias)` folds the exclusion into SQL. **Anti-drift alone would NOT have justified a new API**: an AST ratchet over the existing call sites buys the same "record the decision" property with no new SQL at all, so a seam has to earn its keep on cost. Four rules, each earned: **(1) `NOT EXISTS`, never `status NOT IN (...)` over a LEFT JOIN or a scalar subquery** — a missing source row yields NULL, `NULL NOT IN (...)` is NULL, and `WHERE NULL` EXCLUDES, inverting fail-open into a queue that hides work; a row is hidden only on affirmative proof (recognised kind, existing table, matching row, terminal status). **(2) `alias` is REQUIRED, a bare identifier, and validated** — unqualified, the correlated `item_kind`/`item_ref` resolve against the SOURCE table first, and that is harmless today *only* because `findings`/`requirements` happen to lack those column names. Measured with an `item_kind` column added to `findings`: the subquery stops referencing the outer `item_kind` altogether and hides an `external` row that must stay live — failing **closed**, hiding live work. `EntityKind` validates `table` at construction (CB-22); nothing validated `alias`. **(3) Build it ONCE per traversal** — it probes `sqlite_master` per kind, so per-bucket construction adds eight reads inside the exclusive-lock hold, making the defect worse. **(4) The SET-WISE spelling lives on `EntityKind`, beside the row-wise one** — `EntityKind.terminal_exists_sql` next to `EntityRef.is_resolved`, because `entities.py` owns those tables, that status column and that terminal vocabulary. CB-31's first implementation built the subquery inside `milestones/`, which reached into another module's tables, bypassed the `readable_cols` allowlist, and carried a `# noqa: S608` justified by validation in a file it did not own — three of this document's own rules, broken by one function. **Co-location is the anti-drift mechanism; the DIFFERENTIAL test is a sample, not a proof** — it would miss a change to one side that every fixture row agrees on. That test is non-vacuous only because of two specific rows: an `external` pointing at a **terminal** finding, and a `bug` whose source row is **missing**. Measured: the realistic NULL-unsafe mutant is caught by those two and by nothing else, so an "externals are covered" fixture whose external points at a *live* finding proves nothing. **(5) The caller owns null-safety on its own discriminator** — the fragment is never NULL, but ANDing it with `item_kind = ?` is, and `NOT NULL` is NULL, and `WHERE NULL` excludes. Use `IS`. Deliberately still unfiltered, both named: `get_milestone_status` (a rollup reports stored state) and `closegate`'s unfinished gate (a false refusal is correct there — CB-32).
- **A VIEW was rejected for a measured reason, not the obvious one.** The obvious objection — a view's DDL would hardcode the terminal sets — is false; it could be regenerated from `kind.terminal` on every schema init. The real one: `CREATE VIEW` over a missing source table **succeeds**, and the first `SELECT` from it raises `no such table`. A view therefore fails **closed, with a crash**, for exactly the raw-connection callers this design must keep working.

## Code rules

### Module structure
- Each domain module owns its schema, constants, and public functions. No module should reach into another module's tables directly.
- `db.py` is infrastructure — it provides `connect()`, ID generation, and findings CRUD. It must NOT import domain modules at the top level.
- Domain modules may import `db` for connection/ID utilities. They must NOT import each other's private functions — only public interfaces.

### Naming and style
- Python 3.11+. Type hints on all public function signatures.
- `ruff` for linting/formatting, line length 100.
- Public functions use keyword-only args after `conn`: `def f(conn, *, name, ...)`.
- MCP tool functions are prefixed with the domain: `codebench_import`, `reqs_add`, `blockers_check`. (Exception: findings tools lack prefix — see known debt above.)
- CLI handlers are named `cmd_<domain>_<action>()`. (Exception: findings handlers lack domain prefix — see known debt above.)

### Database
- **DB discovery**: `db.connect()` walks up from cwd for an existing `.codebugs/`. No tracker found raises `db.DatabaseNotFoundError`, and `init_project` — the package's single `os.makedirs` — is the only function that creates the `.codebugs/` **directory**. A `.git/` **directory** stops the walk (submodules must not hijack the parent's DB); a `.git` **file** is followed via its `gitdir:`/`commondir` pointer, so git worktrees resolve to the main repo's DB.
- **What counts as "a tracker" differs by how you got there, and the asymmetry is the point (CB-23).** On the **walk**, the `.codebugs/` *directory* is the opt-in: find one holding no `findings.db` and the database is created inside it. On a **named or declared** root (`project_dir`/`--repo`, `--tracker-root`, `$CODEBUGS_ROOT`) the *file* is the tracker, and a directory without one fails closed naming the channel. Standing in a directory is evidence about where you are; a named path is an assertion that can be stale, inherited by an unrelated subprocess, or mistyped — and that is where a silent second empty tracker does the damage CB-8 was filed for. This was not a free choice between semantics: `_db_path`'s own docstring already promised the named branch would refuse, while the code checked only `os.path.isdir`. The benign half matters too — `init_project` creates the directory *before* the database, so a Ctrl-C'd `init` leaves exactly this state, and the walk self-healing it is the right answer. **State the creation rule at the call site, not as a slogan.** `_open(path, create=...)` is the only function that opens a connection, and **exactly two callers pass `create=True`**: `init_project`, which has just made the directory, and `connect` on the walk route, where the directory was already there. Neither invents a directory. "`init_project` is the only creator" is false as a flat statement — `connect` creates, by design, and an earlier draft of this very bullet asserted both halves of that contradiction two sentences apart. `tests/test_db_infra.py::TestOpenCallSitesRatchet` pins the call-site count so a third creating caller cannot appear quietly, the same shape as the `BEGIN IMMEDIATE` allowlist. The split exists because `init` used to create its database *by way of* `connect`, so tightening the resolver broke the one caller that must create.
- **Discovery is a heuristic, so it has a declared override — and every override channel outranks the walk.** Resolution order in `_db_path` is: an explicit `project_dir` argument (what `--repo` passes; per-call, so it beats ambient state), then `--tracker-root`, then `$CODEBUGS_ROOT`, then the walk. Entry points call `db.set_tracker_root()` once; **nothing else may**, because ~50 `db.connect()` call sites pass no arguments and `db` is therefore the only place that can honor a declaration. **`set_tracker_root` validates nothing** — a root may be named before its tracker exists, and dying at startup for that would destroy the lazy-connect self-healing CB-11 protects; the check belongs at use. A declared root that resolves to no tracker **fails closed and names the channel that set it**, because the value is ambient (an export inherited by an unrelated subprocess) and "no tracker there" must never quietly become a second empty tracker — CB-8's failure mode arriving through a new door. A blank value in either channel is not a declaration, same convention as an empty query filter.
- **Some layouts are provably undiscoverable, and the honest response is an escape hatch, not a better guess.** `_worktree_main_root` accepts any commondir whose basename is `.git` — git's own heuristic — so a `--separate-git-dir` repo whose git dir is named `.git` binds to the admin directory instead of the checkout (CB-13). There is no local discriminator: git reports that directory as a valid work tree too, so any "fix" would be a different guess. The heuristic stays, `TestSeparateGitDirMisbinding` pins that it still reproduces (if that premise test ever fails, the fix below is moot), and a declared root is the remedy. **The general shape: when a rule cannot be decided from local evidence, supply external metadata rather than deepening the guess.**
- **A binding you cannot see is a binding you cannot debug.** `db.describe_root()` never raises and reports `{root, source, source_label, path, exists, exists_reason, error, writable, dir_writable, unexamined}`; `codebugs where` and the MCP startup preflight are its only two consumers, deliberately — one resolver means the diagnostic and the server can never disagree about where the process is pointed. **Every key is unconditional, and that is the invariant rather than the list**: a key is always present, and its empty or `None` value means *the question was asked and there is nothing to say*, never *this channel does not exist*. Consumers therefore compare with `is` and print only what a reader needs — `writable` and `dir_writable` only when `False`, `unexamined` only when non-empty.
- **`exists` IS THREE-VALUED, and the third value is the DEFAULT rather than an extra case (CB-203).** `os.path.isfile` answers a three-valued question with two values — it swallows every `OSError` the underlying stat raises — so *could not look* came back as *nothing is there*, and both consumers printed a promise on it. `db._path_state` returns `absent` on exactly ONE condition (`ENOENT` on the name itself) and `None` — undetermined, with a human reason in `exists_reason` — on every other way of failing to see a regular file, **so a mechanism nobody enumerated lands in *could not tell* by construction. That default is the whole fix**: three ways to break a tracker were known when the unit started and measurement found five more, each with its own errno, and there is no reason to think eight is the population. **`lstat` runs before `stat`, and the order is load-bearing**: `os.stat` on a DANGLING SYMLINK raises `FileNotFoundError` byte for byte like an empty name, and there the next command creates a database at the link's *target*, so a stat-first guard would call that proven absence. **The same predicate replaced `isfile`/`isdir` at every site that INSPECTS AN ALREADY-RESOLVED ROOT**, so the declared (`--tracker-root`/`$CODEBUGS_ROOT`) and named (`--repo`) routes stay fail-closed and simply stop asserting an absence they could not establish.

  **THE PROPERTY, STATED AS A PROPERTY RATHER THAN AS A COUNT OF SITES (CB-218, CB-224).** Every question this module asks about what is at a path is either three-valued, or declared in an exceptions table with a reason — **and a test checks which**. Prose could not hold it: each time the claim was re-asserted as a universal it was false within one edit, and every count this bullet ever carried rotted the same way, so **a number that decides anything belongs in a test, not in this paragraph.** The property is held by `tests/test_two_valued_path_gate.py`, on the model of `test_no_network_capability.py`/`test_strict_bool_gates.py`: every two-valued read in `db.py` is either routed through `_path_state`, or named in that file's own self-deleting `DECLARED_EXCEPTIONS` table with a reason per row. Two rows exist today — `init_project`'s purely informational `created` flag, and `_open`'s CB-86 message-selection branch — each documented at the site it belongs to.

  **THE GATE KEYS ON CAPABILITY, NOT ON SPELLING, BECAUSE AN ENUMERATION OF SPELLINGS IS THE SAME DEFECT ONE LEVEL UP (CB-227).** A call is resolved through the file's own import bindings and compared as an OBJECT, so `from os.path import isdir`, `import os.path as osp`, `from os import path`, and `posixpath`/`genericpath` — which are not aliases but literally the same functions — all land on one capability. An `except` clause is judged by asking the LIVE class hierarchy (`issubclass(caught, OSError) or issubclass(OSError, caught)`), so `IOError`, `EnvironmentError`, `os.error`, `Exception`, a bare `except:`, an `except*` group and the concrete errnos are all covered with no alias list anywhere, while `ValueError` and this module's own exception classes are correctly refused. A row is keyed `(function, primitive, call text)`, and **a row matching more than one call site is REFUSED rather than quietly stretched over both** — keyed `(function, primitive)`, one row licensed every call of that primitive in that function.

  **THE PROPERTY, AT THE WIDTH IT IS ACTUALLY HELD AND NO WIDER.** The gate holds one sentence: *inside `src/codebugs/db.py`, a call asking WHAT IS AT A PATH and answering with two values is either fixed or declared.* Four evasions sit outside it, each reproduced rather than assumed. **(1)** An indirection that hides the NAME — `getattr(os.path, "isdir")(p)`, a primitive held in a dict or a tuple, `functools.partial`, `operator.methodcaller`, `eval` — needs value tracking, the same boundary `test_no_network_capability.py` draws around `__import__`; note a SIMPLE binding (`f = os.path.isdir` then `f(p)`) IS caught, because that is name resolution, while a conditional or container-held one is not. **(2)** A swallow returning through anything but a literal — `ok = False; return ok`, `return bool(x)`, a flag set in a `finally`, `contextlib.suppress(OSError)` — needs data-flow inside the function. **(3)** The same read MOVED into a sibling module `db.py` imports: the gate reads one file, by decision, and `provenance.py` keeps its own schedule. **(4) THE SEMANTIC SENTRY, and it is what CB-227's live harm actually was.** A function can answer three-valued perfectly — `except OSError: return None` — while its CALLER reads that `None` as *definitely not there*; the meaning lives in the caller, so no predicate over the reading function can ever see it. That is why CB-227 needed a behavioural oracle beside the gate, and why **the gate must never be described as covering it.**

  **A SECOND KIND OF QUESTION WAS NEVER IN THIS VOCABULARY AT ALL, WHICH IS HOW TWO DOORS STAYED OPEN THROUGH CB-224'S OWN FIX: WHAT DOES THIS FILE SAY.** `_path_state` answers *what is at this path*; reading the `.git` file's `gitdir:` pointer and reading `commondir`'s contents are `read_text` calls that no stat can stand in for, so no swap of primitives was ever going to reach them — and both swallowed their failure into a confident negative. Both now return the `(path, detail)` third value this route already had; `_resolve_db` no longer asserts anything about a main checkout it could not locate; and `_enclosing_worktree_root` now RETURNS that third value instead of dropping it, which is what lets `init_project`'s already-ratified `WorktreeTrackerError` fire — **no policy was decided there, a refusal that already existed was simply unreachable**, and the same drop is why CB-224 closed only the `where` half of its own defect and left the `init` half creating a doomed tracker. A `.git` whose PATH cannot be examined at all still records-and-continues, because that is CB-218's ratified answer and not this card's to overturn.

  → почему именно так, с замерами и раундами ревю:
  `docs/claude-md-rationale/database.md#cb-203-трёхзначный-exists`
- **THE UPWARD WALK IS THREE-VALUED TOO, AND WHAT IT DOES WITH THE THIRD VALUE IS THE WHOLE DECISION (CB-218).** `_walk_db_root` asks three questions per directory — is there a `.codebugs/`, is there a `.git` directory, is there a `.git` file — and `Path.is_dir()`/`is_file()` swallow the underlying `OSError` exactly as `os.path.isdir` does, so *could not look* arrived spelled *not there* in all three. Measured on the unfixed tree: with the execute bit off a directory that HOLDS the project's tracker and an unrelated `.codebugs/` one level above it, `codebugs where` printed a clean binding to the stranger at exit 0 with no warning, and `stats` answered about the stranger's empty population. The `.git` half is the same harm through a second door — an unanswerable `.git` reads as *no boundary here* and the walk crosses the repository boundary — and it was reproduced in ISOLATED form, with the repository directory fully readable, its `.codebugs/` provably absent and a symbolic-link loop at `.git`, so nothing else could be blamed. **On an undetermined answer the walk RECORDS AND CONTINUES; it does not stop.** Stopping would turn today's harmless case — one filesystem error on an unrelated ancestor between the caller and their real tracker — into a refusal to work at all, and a false refusal is the worse outcome here. Continuing is safe; staying silent is not, because silence is the entire mechanism by which the measured state became a wrong bind rather than a visible one. The skipped candidates travel out as `describe_root`'s `unexamined`, both consumers name them, and the "no tracker anywhere" refusal stops claiming an absence about parents nobody could look at — while the refusal on a route that examined everything stays byte-identical. **Nothing is truncated in the human text, and a truncating version was written first and refuted by measurement**: one wall makes every question below it unanswerable too (the walk asks with absolute paths, each of which traverses it), the list runs deepest-first, and a cap keeping the first entries kept the wall's shadows while dropping the entry naming the wall itself. **When CB-218 landed**, `_enclosing_worktree_root` took the same primitive with NO behaviour change — said that way round because no test could discriminate it — since it then only chose between two refusal sentences and was reached only after the walk had already recorded the same prefix. **That is history now, and it is kept in the PAST TENSE rather than deleted because it is the reason the wording was chosen** (CB-239): CB-227 later made that function RETURN the third value instead of dropping it — the bullet above — so it no longer merely picks a refusal sentence, and while both sentences stood in the present tense the document said the opposite of itself two bullets apart and a reader could not tell which half held today. Every claim here about live code is written so its as-of is visible. **Still two-valued, deliberately out of scope, and named so it is not mistaken for covered:** `_find_db_root`, the thin wrapper that discards the list, feeds `init_project`'s shadow guard and `cli`'s `--force` variant of it, so an unexaminable ancestor still lets `init` create a tracker a real one above would shadow. That is the same defect in a different decision, and it needs its own answer rather than a silent inheritance of this one's. **The residual is named, not closed**: this reports on the PATH, never on the CONTENTS — a corrupt `findings.db` still reads as `writable: True` and a clean binding, and the failure surfaces only when a verb opens it (CB-201 item 1, now carried by its own card). `_writable_probe`'s docstring names that boundary and the rest of the `_is_environmental` family it does and does not cover, and declares its `except` arm as measured-dead insurance rather than implying coverage. The preflight (`server._preflight`) is **warn-only and must stay that way**: it writes to stderr because MCP clients log stderr while tool responses cannot carry a startup diagnostic, and it is silent on the ordinary discovered path but announces a declared root, since a non-default binding is what a reader needs to see later. Before it, a misconfigured server looked healthy at startup and failed every call forever with no single moment naming the cause (CB-11). **`exists` is separate from `error` because resolving is not the same as being there**: on the walk route a `.codebugs/` holding no database resolves cleanly and the next write *creates* the tracker, so nothing errors and nothing is visible — the CB-13 misbinding's exact shape, since the wrong root there is a stray directory. Both consumers now say so, and that is the one case where the preflight speaks on a `discovery` binding. **A path in a diagnostic is only a report if its coordinate system travels with it (CB-49)**: `declared_tracker_root()` returns the declared value absolutized — lexical `abspath`, never `realpath`, because a declared root is often a deliberately symlinked path and the job is to pin the coordinate system, not rewrite the declaration — so `where`, the preflight line, and the fail-closed error texts all report a root readable without knowing the process cwd. The one exception is deliberate: with a deleted cwd `abspath` itself needs `os.getcwd()`, so `_absolutized` falls back to the raw value rather than violate `describe_root`'s never-raises contract.
- **An ENVIRONMENTAL sqlite failure is classified inside `_open` and raised as a TYPE, never classified at the CLI boundary (CB-86).** `sqlite3.OperationalError` derives from `sqlite3.Error → Exception`, **not** `OSError`, so CB-71's `open(` sweep and CB-79's `OSError` widening were both structurally blind to it — the third vocabulary of "the CLI crashed at an I/O boundary", closed three times without anyone enumerating the family. `db._is_environmental` — PRIVATE, deliberately, because a caller at the boundary is the rejected design — keys on `{8 READONLY, 10 IOERR, 13 FULL, 14 CANTOPEN}` with the same `& 0xFF` mask as `is_contention` (load-bearing: a read-only *directory* raises the extended `1544`), and `_open` converts a match into `db.TrackerUnwritableError`, a **sibling** of `DatabaseNotFoundError`. **The rejected design is the instructive part**: adding a `sqlite3.OperationalError` arm at `cli.main` was refuted by this repo's own `tests/test_bench.py:789`, which ratifies the traceback as the discriminator between a post-commit failure and an input error — a central arm cannot tell those apart, which is verbatim CB-55's constraint applied to a different exception class, and the "exit code is unchanged so no new lie is possible" argument **proves too much** (it would equally license the central `except OSError` CB-55 forbids). A type raised from `_open` carries its provenance structurally instead: `_open` raises before it returns a connection. State the claim precisely though — it means *this failed while opening a connection*, not *nothing was written anywhere*; no handler connects twice today, but that is a property of the call sites. Three raise sites live inside `_open` and one of them is `merge.ensure_schema` several frames down through the `_resolved_order()` loop — verified by running it, **and that is what once made one classification point suffice for a read-only database FILE as well. It no longer does** (CB-199, the next bullet): CB-195 made that seed write conditional on a read, so once the seed rows exist `_open` attempts no write of its own and the failure surfaces later, at the domain's own INSERT. The live claim is the narrowed one stated below — one classification point covers *opening a connection* — and this sentence is put in the past tense so the two bullets cannot be read as disagreeing (CB-213). `SQLITE_PERM` (3) is deliberately absent (measured: `chmod 000` yields 14, and no CLI path produces 3) and `SQLITE_NOTADB` (26) cannot be added because it arrives as `DatabaseError`; both absences were measured rather than reasoned, after a sibling card added three prose-sourced entries in the paragraph congratulating itself for avoiding exactly that. **`SQLITE_CANTOPEN` is ambiguous** — identical code and message for a missing file and an unopenable one — so `os.path.exists` picks the message, for message selection only; an unreadable *parent* still reads as "not found", stated at the site rather than discovered later.
- **One classification point covers OPENING A CONNECTION — never a write on a connection already open (CB-199, open).** `_open` classifies an environmental sqlite failure and raises `TrackerUnwritableError`, so a read-only DATABASE FILE reached by the walk is **not** detected at connect time once the seed rows exist: `_open` then attempts no write of its own, because CB-195 made the seed write conditional on a read so that a purely reading `db.connect()` never takes the write lock for a redundant insert. **What the narrowing costs, exactly, because all three halves are load-bearing:** a READ on a read-only tracker now SUCCEEDS — a capability gained, not a defect; a WRITE verb still refuses, still at exit 1, still with nothing landed; and only the MESSAGE narrows, the failure surfacing from the domain's own INSERT *outside* `_open`'s try/except as a raw traceback instead of the clean one-liner. `tests/test_db_unwritable.py::TestTheFourShapesEndToEnd::test_B_read_only_database_file` pins exactly that three-way shape rather than hiding the narrowing or refusing the read-side gain over it. **CB-199 stays open by design:** any write-based probe restoring early detection would have to attempt a write on every `db.connect()`, reintroducing the write-lock contention CB-195 removed, so it is not a two-line fix.
  **"Steady state" is a real qualifier and has a real other side (CB-202).** While a seed row is MISSING — the first open of any tracker, and any whose seed rows were removed — the insert does run, and a reading `db.connect()` waits out a concurrent writer exactly as it did before (measured; the figures are in the archive). That is one open per tracker and no read-first rule can avoid it, so it is a BOUNDARY rather than a residual defect. `src/codebugs/db.py:_open`'s own comment carries the same narrowing at the site, and says in words not to widen it back.
  **A schema-init function must not execute a string it did not itself check against the database, and the ratchet holding that keys on a PRIMITIVE rather than on a spelling (CB-202).** `tests/test_db_infra.py::TestSchemaInitRunsNoUncheckedDml` resolves SQL text through constants, loops, `split`/`strip`, f-string and concatenation prefixes and `executescript` bodies; derives its entry points from the `register_schema` calls rather than from the NAME `ensure_schema`, which had left every migration helper those functions call entirely unread; follows the module-local closure; and accepts a branch only when a READ could have fed it. **It is fail-closed on what it cannot resolve, and its docstring enumerates what it still cannot see** — a ratchet whose promise is wider than its check is the defect CB-202 exists to close, so the enumeration is part of the fix rather than a caveat on it. **How many helpers there are, and how many execute sites they hold, is a question for the ratchet and deliberately not for this paragraph:** both counts that once stood here were stale by the time anyone re-ran them.
  → почему именно так: `docs/claude-md-rationale/database.md#cb-199-одна-точка-классификации`
- Each module defines its schema as a module-level string (`SCHEMA` or `<DOMAIN>_SCHEMA`) and provides `ensure_schema(conn)`.
- All schema changes must be additive (new tables, new columns with defaults) or use explicit migration functions.
- Use parameterized queries exclusively. Never interpolate values into SQL.
- SQLite WAL mode is enabled. `db.connect()` also sets `busy_timeout=5000` explicitly — it used to be inherited from `sqlite3.connect(timeout=5.0)`'s default and appear nowhere in the source. That timeout is what turns a losing writer into a clean `rowcount=0` instead of an `OperationalError`.
- **Never write a plain `BEGIN`.** It pins a read snapshot, and the later write upgrade dies with `SQLITE_BUSY_SNAPSHOT`, which `busy_timeout` cannot rescue. Use `db.txn(conn)`, which issues `BEGIN IMMEDIATE`, saves/restores `isolation_level`, and is reentrant (it yields `False` and does nothing when a transaction is already open). **`db.txn` is now the ONLY executable `BEGIN IMMEDIATE` in the package** — the two grandfathered sites are gone (CB-40), and `tests/test_claims.py::test_24` now *counts* occurrences rather than deduplicating `(filename, statement)`, because the old set-based check would have passed any number of raw sites inside an already-allowed file, and zero as well.
- **Assigning `conn.isolation_level` COMMITS an open transaction — so the save/restore idiom is not a neutral wrapper (CB-40).** `merge.merge` and `capacity.pull_next` both opened with `conn.isolation_level = None`, which meant that calling either from inside a caller's transaction silently committed the caller's unrelated work before starting its own — the exact inverse of the reentrancy contract every `db.txn` user advertises. Verified by running it: `in_transaction` goes `True → False` and a subsequent `ROLLBACK` finds nothing to undo. Both now use `db.txn`. **Both also refuse an ambient transaction outright, with a `raise` and not an `assert`** (`assert` is stripped under `-O`): each is an *acquisition* — a merge lock, a work claim — and under an ambient transaction `db.txn` yields `False`, so they would report success for a row no other connection can see yet. A gate that says "you hold the lock" before the lock is committed is worse than the defect being fixed.
- **A refusal path that writes nothing needs no rollback machinery — and if it does write, reorder it so it doesn't.** The two raw sites existed because three paths did "roll back and return a value", which a plain `return` inside `with db.txn(...)` cannot do (it commits). An earlier design added a `TxnAbort` sentinel for this and was rejected in review: `db.txn` deliberately swallows a failed `ROLLBACK` (correct, so cleanup never masks the real exception), which would have let a refusal-shaped result come back with the transaction still live. The real fix was ordering — `merge.merge` now runs its head check **before** marking a stale holder abandoned, so `main_moved` has nothing to undo, and `lock_held` / no-candidate never wrote anything. All three simply return and commit an empty transaction.
- **A deadline computed in Python is a defect waiting for something slow to happen — compute it in SQL (CB-41, and it took THREE review rounds to learn).** `merge()` writes a lease `expires_at`. Round 1 sampled it at the top of the function, before the write lock and before the injected git callback. Round 2 moved it below those — and left the stale-holder `abandoned` UPDATE between the sample and the write. Each time the lease landed **already expired**, the call returned `proceed: True`, and the next contender saw the lock reclaimable and *also* got `proceed: True`: two agents merging at once, which is the one thing the lock exists to prevent. **Point-of-use discipline is the wrong enforcement layer**, because it must be re-established every time a statement is inserted between the sample and the write — and twice it silently wasn't. The fix is to make the bad state unrepresentable: `strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)` **inside the UPDATE**, so sampling and writing are one operation and no code can be inserted between them. `merge.py` now imports no clock at all. A comparison timestamp may still be read in Python — reading it early is *conservative* (an early `now` makes a lease look live, so you refuse rather than over-grant) — but anything **written as a deadline** goes in SQL. Note the corresponding test must assert the **SQL template**, not behaviour: any Python-sampled deadline still looks fresh unless real time passes during the call, which is exactly why the first regression test for this passed against the defect it was written for.
- **An idempotency affordance can defeat the gate it sits in front of (CB-41).** `merge.merge`'s "already merging" short-circuit compared only `session_id` and never read `expires_at`, while the acquisition path below it treated an expired lease as reclaimable. So an expired holder retrying got `proceed: True` from the first branch while a competitor reclaimed the lease and got `proceed: True` from the second — two agents pushing to main, defeating the singleton lock without ever racing inside it. Every existing test used a *fresh* lease, where the two branches agree. **The remedy is renewal**: owning the lock renews it regardless of expiry, so expiry no longer decides anything on the self-owned path and the branches cannot disagree. Cost, accepted deliberately: the TTL still reclaims from a holder that **died** (a dead holder does not retry) but no longer bounds one that is alive, wedged and retrying — that needs liveness detection, not a timestamp.
- **A value computed in Python from a row you just read must be written back inside ONE transaction — and that transaction owns the commit.** `update_finding` / `update_requirement` merge `meta` in Python over the row read at the top, so with the read and the write in separate statements two writers both read the stale value, both report success, and the later erases the earlier (CB-24). `busy_timeout` serializes the *writes*; it never touches the read that preceded them, so it cannot help. Wrap the whole body in `db.txn`, which takes the write lock before the read. Three consequences, each of which cost a real failure here: **(1) delete the function's own `conn.commit()`** — `db.txn` yields `False` under an ambient transaction and committing then commits the *caller's* work (`milestones.triage_dismiss` is such a caller, and it gained atomicity from this change); **(2) convert the returned row AFTER the block**, because `row_to_dict` raises `json.JSONDecodeError` on malformed stored `meta` and inside the block that rolls back a write the contract promises has landed — the CB-16 lie in a new place, and three existing tests caught it; **(3) a one-statement read-modify-write is not an instance of this** — `SET n = n + 1` or a SQL-side `json_patch` is already atomic. Note the no-op path now holds the write lock for one SELECT: deriving "will this write?" from the arguments beforehand was rejected because it duplicates the argument list, the same fragility the lazy-meta guard warns about.
- **The CB-24 population is ~19 sites, not the four that got fixed — and two more consequences fall out of wrapping a function that RETURNS a row (CB-27, CB-30).** CB-24 fixed four sites; CB-27 was then filed as "nothing stops a FIFTH". A mechanical sweep (`grep -rn "conn.commit()"` → 43 executable sites, vs 7 `db.txn` users, then read every committing function) found **19 instances, 13 still unfixed** — in `blockers.py`, `merge.py`, `sweep.py` and three milestone modules no card had named. **This repo's recurring lesson, now for the sixth time: a rule expressed as an enumeration gets fixed at the sites someone enumerated, and the population is always larger than the list.** The outstanding 13 are on CB-36 with `file:line`; what would mechanically enforce the rule is CB-37, still undecided (the obvious AST predicate *certifies the very bug it was built to catch*, and is blind both to reads behind helpers like `_get_item_by_ref` and to cross-table check-then-act). Two consequences beyond CB-24's original three, both of which cost a review round: **(4) read the row RAW inside the block.** `_get_item_by_ref` calls `_row_to_item`, which `json.loads`es `meta_json` — *before* any write. Wrapping such a function without swapping to a non-parsing lookup (`_spine._get_item_row_by_ref`) leaves consequence (2) unsatisfiable, because there is no state in which the write lands and the parse then fails. Beware `sqlite3.Row` is not a `dict`: it has no `.get()`. **(5) capture the mutated row with `UPDATE … RETURNING *`, never re-read after the commit.** `release_item` re-resolved by `item_ref` *after* committing, so a newer attachment inserted in that window was returned instead — reporting `status='open'` for an item it had just marked `done`. Note what this does and does not buy: the returned row is now the row written (self-consistency), but *which* attachment was selected is still arbitrary — that is CB-33. `pull_next` has the identical window and is CB-39. And a statement that gains `RETURNING` can never again have its `rowcount` read (next bullet), which forecloses rowcount-based hardening of `_decrement_capacity` (CB-38).
- **The `RETURNING` rule.** A statement either carries `RETURNING` and its outcome is read by fetching, or it carries no `RETURNING` and its outcome is read from `cursor.rowcount`. Never both: on a `RETURNING` statement `rowcount` is `0` until the cursor is exhausted, so reading it reports "nothing happened" *while the write has already landed* — strictly worse than a no-op.
- **One assignment per column in a built `SET` clause.** Update functions assemble `SET` from an `updates`/`sets` list, and SQLite silently accepts `SET meta = ?, meta = ?` — applying only the **last** assignment. Two branches that each write the same column therefore destroy each other's work with a success-shaped return value. So: a column that more than one argument can affect must be **accumulated into a single value first and appended exactly once**, and that value must be built by mutating **one** object, never by re-reading the pre-update row per branch (a stale re-read loses data even after the duplicate assignment is gone — both faults have to go). `update_finding` and `update_requirement` are the worked example (CB-16); their docstrings carry the ordering contract, and `TestUpdateMetaComposition` guards it in both test files. **Assert such a guard against the SQL template, not the executed statement** — `set_trace_callback` reports parameters already expanded, so a guard reading it cannot tell a real assignment from the same text inside a value, and gives both false passes and false failures. The `RecordingConnection` subclass in each test file captures templates by overriding `execute`, which makes `sql.count("meta = ?") == 1` exact.
- **A column settable at INSERT should be settable at UPDATE, or documented as immutable — and the two entities must be checked against each other, not each against itself.** That second clause is the actual lesson: `severity` was write-once on findings while the sibling `priority` was already mutable on requirements (CB-17), and the asymmetry was invisible from inside `findings.py`. `reported_at_commit` is the worked example of the other branch: deliberately immutable, and *said so* in the docstring. **The rule is now DECLARED and GATED, and this bullet used to say the opposite (CB-21).** It read *"this rule is currently violated … read it as a target with a known outstanding debt"*, because `update_finding` reaches only `status, severity, tags, meta, reported_at_ref` while `update_requirement` can already rewrite `description` — the identical asymmetry as CB-17 — and `source` is INSERT-settable on **both** entities yet appeared in neither update contract. Nothing anywhere stated the intended matrix, which is why three independent passes over the same function each found a DIFFERENT missing column: **enumeration by inspection does not converge, and prose is the wrong enforcement layer for a defect whose defining property is invisibility from inside one file.** `tests/test_update_parity.py` is that enforcement layer. For BOTH entities it reads `PRAGMA table_info`, the `update_*` signature, the MCP wrapper's signature and the CLI parser's argparse dests, and fails on any column that is neither declared MUTABLE (naming the parameters that write it) nor declared IMMUTABLE **with a reason** — so a new column, a new writing parameter or a new surface argument turns a test red instead of waiting for a fourth inspection. The residual findings cells were closed by DECLARATION rather than by widening the function: `description`, `category` and `file` are the three inputs of the derived `auto:v1` fingerprint, so an argument for any of them would make `update_finding` a RE-KEY of identity, and re-keying is a separate negotiated contract (CB-43 item 6; CB-61 negotiated exactly one such operation, `normalize_categories`, which issues its own UPDATE for precisely that reason). **That corrects the card's own premise** — it recommended making `file`/`description` mutable because "there is no integrity argument for freezing" them, and there is one. `source` carries BT-4's first-reporter reason on both entities. **A declaration is not a verdict**: whether `description`/`file` should become mutable stays OPEN, the `IMMUTABLE` docstring says so, and the gate's job is to force that answer to be stated rather than rediscovered. The surface axis is declared but deliberately NOT closed — the CLI `update` verb has no `--tags`/`--meta-update`/`--reported-at-ref` and `reqs-update` no `--section`/`--tags`/`--meta-update`, each recorded in `SURFACE_GAPS` with its reason, and a hole declared for an argument that is in fact present fails the gate too, so the list cannot rot into permission to skip a surface. That axis is CB-6.
- **Match a field's own insert contract, not its neighbour's — and when you unify, unify every site at once.** This rule used to read "`severity` is exact-match on update, because that is what `add_finding` enforces". **CB-19 closed that**: `resolve_severity` now runs on `add`, `batch_add`, `update`, CSV import *and* the query filters, so severity normalizes exactly like `status`. The rule survives as the reason it was done that way — making the update path lenient while insert stayed strict would have created a worse, same-field inconsistency, so the seam had to move in one step rather than per-site. What normalization forgives is still **spelling, never meaning**: severity has no aliases, so `crit`/`P0`/`sev1` raise.
- **Never `ORDER BY` a vocabulary column directly — it sorts alphabetically.** `severity` and `priority` are TEXT with a CHECK constraint, not ordered types, so `ORDER BY severity` yields `critical, high, low, medium` and `ORDER BY priority` yields `could, must, should` — the latter inverting the ranking outright. Under a `LIMIT` this **truncates the rows that matter** rather than merely displaying them oddly, and nothing signals it (CB-20). Use `types.rank_case_sql(column, vocabulary)`, which derives the rank from the tuple so the SQL cannot drift from the vocabulary, binds the values rather than interpolating them, and sends unknown values last. **Its params must be spliced at the fragment's textual position, not prepended** — in `query_findings` that is after the WHERE params and before `LIMIT`/`OFFSET`; getting it wrong corrupts *filtered* queries only, so unfiltered tests keep passing. `blockers.query_deferred_entities` orders by `EntityKind.sort_col`, whose precedence is declared alongside it as `sort_vocabulary`; keep the pair together. Note `findings.get_stats` and `reqs.get_reqs_stats` are immune only because they pre-seed their output dict with the vocabulary — that is load-bearing, not decoration.
- **A vocabulary must resolve on BOTH sides of the entity — the write path and the query filter.** Normalizing writes while a filter compares raw text against a canonical column is worse than doing neither, because the caller can store a value and then be unable to find it by the same spelling, and the failure is *silent*: `query_requirements(priority="SHOULD")` returned zero rows for a row `update_requirement(priority="SHOULD")` had just written as `should`, and "no requirements" is indistinguishable from an empty queue (CB-19). The resolvers live in `types.py` — `resolve_severity`, `resolve_priority`, `resolve_finding_status`, `resolve_requirement_status` — and every one of the five write sites and four filter sites goes through them. **`_resolve` is also where non-string input is refused**, so `None` raises `ValueError` (the documented contract) rather than `AttributeError` from `.lower()`; guarding per-resolver would leave the next one to re-acquire the hole. Two things this rule does *not* say: an empty-string filter is still "no filter" and is never validated (now because `is_vocabulary_filter_active` says so explicitly — see the next bullet; it used to be because the `if severity:` guard short-circuited first, which was the CB-25 defect), and normalization forgives **spelling, never meaning** — severity has no aliases, so `crit`/`P0`/`sev1` still raise, and adding aliases needs evidence of real callers.
- **"No filter" is `None` and `""` — never truthiness, and never decided with `!=`.** A vocabulary filter guarded by `if severity:` conflates *not supplied*, *the documented empty filter*, and *wrong input*: a falsey non-string skipped the condition entirely, so `query_findings(severity=0)` returned the **whole table**, and an unfiltered queue is indistinguishable from a correctly filtered one (CB-25). The resolver could not help — it is never reached. `types.is_vocabulary_filter_active` is the one definition, and **it is type-based on purpose: deciding this with `value is not None and value != ""` reintroduces the defect inside its own fix**, because `unittest.mock.ANY` is truthy yet compares equal to `""`, and a `str` subclass overriding `__ne__` does the same to a valid `"open"` — so the predicate must never run equality (or `len`) on the value. Both traps are pinned in `tests/test_types.py::TestIsVocabularyFilterActive`; without those two cases the wrong predicate passes every other test. Three consequences: **(1)** it is scoped to *vocabulary* filters and must not be reached for on `ids`/`tags`, where an empty list legitimately means "no filter" and where an active empty filter emits `id IN ()` — valid SQLite that returns **zero** rows, i.e. a silent empty queue replacing a silent full one, quieter and worse; **(2)** a caller whose contract is *default*, not *no filter*, still uses it but keeps its own default — `provenance.check_findings` maps `None`/`""` to `"open"`; **(3)** the sweep found three more filters validating their vocabulary on the **write side only** — `merge.get_sessions` (whose `types.MERGE_STATUSES` existed but was **dead code**, so the CHECK constraint was the only enforcement), `milestones.list_milestones` (which had `MILESTONE_KINDS`/`MILESTONE_STATES` all along and simply never consulted them on query), and `blockers.query_blockers`, whose `TRIGGER_TYPES` check sat *inside* the truthy guard and so was skipped entirely by a falsey value. **Sweep for the SHAPE, not for the names** — the first pass grepped `if status:|if severity:|if priority:|…`, an enumeration of the filters already known, and therefore could not find `trigger_type`; the shape (`if <name>:` wrapping a vocabulary check) finds it in one grep. That is this repo's recurring lesson in a new place: a rule expressed as an enumeration is the letter, and the letter cannot decide. Free-text filters are **not** fixed and are tracked as CB-29; a filter discarded by *routing* rather than by validation is CB-28.
- **On a WRITE path, `None` is the only "not supplied" — and that is deliberately NOT the query-side rule (CB-82).** `types.is_vocabulary_filter_active` treats `None` **and** `""` as "no filter", which is right for a filter: an absent filter matches everything. A stored value is the opposite — absent means *invent one* — so resolving it with truthiness lets a falsey WRONG TYPE be silently replaced by a default. `bench.import_csv` did exactly that with `date or utc_now()[:10]` and `run_id or _next_run_id(conn)`, so `date=[]`, `date={}` and `date=""` all stored today's date and reported success (measured; **the card itself got this wrong**, claiming the dict cases raised — `{}` is falsey and took the same silent path). Validate every non-payload argument **before anything is parsed or written**, so a refusal costs no partial work, and raise the `ValueError` the module contract promises rather than leaking `sqlite3.ProgrammingError` or `json.dumps`' `TypeError`. Two rules the guard itself must follow: **serialize a JSON container ONCE and store that exact string** — validating with one `json.dumps` and storing with a second leaves a window where a mutable or `__iter__`-overriding subclass shows different data to each, which is CB-74's "validating one view while consuming another" in a new place (Codex diff review; pinned by a test whose list mutates between iterations); and **check member types explicitly**, because `json.dumps` complains about neither — it writes `[1, 2]` for tags and silently COERCES a non-string dict key to a string, and a non-string tag later crashes `bench-list`'s `",".join(tags)`. **Do not narrow beyond the card while you are in there**: the first draft added `allow_nan=False`, which would have refused `meta={"x": nan}` that stores and round-trips fine today — an unrequested behaviour change riding along inside a validation fix. Note the guard makes the downstream `x or default` unreachable for bad input, so rewriting it as `x is None` is defence-in-depth that **no test can discriminate** while the guard holds; say that rather than claiming it is covered.
- **Findings have an identity function (CB-43): `add` is an upsert, not an insert.** A FINDING is a defect; an OCCURRENCE is one observation of it. Every observation routes through a fingerprint: a hit on a LIVE row (`open`/`in_progress`/`stale`) bumps `occurrence_count` and returns that row (`was_new: False`); a hit on `fixed` REOPENS it as a regression, and the status-change hook must fire `milestones.reconcile._reconcile_on_reopen` to reopen the stream item, because the terminal reconciler early-returns on nonterminal and the add-side router is `INSERT OR IGNORE` — without it the reopened card is invisible to every queue, strictly worse than a duplicate; a hit on `wont_fix`/`not_a_bug` files a new row carrying `meta.recurrence_of` (a decision stays decided).

  **(1)** The branch table is TOTAL over `FINDING_STATUSES`, pinned by `tests/test_dedup.py::TestBranchTotality` — an unclassified status silently resumes the duplicate explosion.

  **(2)** At most one live row per fingerprint is a **partial unique index** (`ux_findings_fingerprint_live`), declared in `_POST_MIGRATION_INDEXES` and NEVER in `SCHEMA`: SCHEMA runs before `_migrate_statuses`' hardcoded rebuild, which would either crash on the missing column or silently drop the index.

  **(3)** An explicit `finding_id` is an assertion of identity and BYPASSES both derivation and matching. Most test call sites build fixtures from identical tuples, **so a helper that wants N distinct entities must vary its default description.**

  **(4)** The `auto:v1:` fallback hashes a canonical JSON array — never a joined string, since separators are ambiguous in free text — of category, file, and the description normalized by stripping the observation's OWN meta string values, then ISO timestamps (BEFORE lowercasing: the pattern anchors on `T`/`Z`), then digit-bearing hex runs. **General numbers are KEPT**, because `rc=124` versus `rc=1` is a real family split.

  **(5)** `update_finding` pre-checks terminal→live transitions against the index and raises a domain `ValueError` naming the blocking row: `db.is_contention` matches codes {5,6} and `SQLITE_CONSTRAINT` is 19, so a leaked `IntegrityError` is unclassifiable everywhere.

  **(6)** `fingerprint` is INSERT-settable and `update_finding` documents it as immutable. **Exactly one re-key is sanctioned** (CB-61): `findings.normalize_categories` (MCP `categories_normalize`, CLI `categories-normalize`), and it issues its own UPDATE rather than routing through the updater, so no caller acquires a re-key by argument. **Its boundaries are the point:** DRY RUN BY DEFAULT (no write transaction is opened at all without `apply=True`/`--apply`); only an `auto:v1` hash is re-derived, only from the CATEGORY input and only with the SAME normalizer version, so a `NULL` and a caller-SUPPLIED hash stay byte-identical; a row whose stored inputs no longer reproduce its stored hash is skipped WHOLE — its category is not rewritten either — and reported as `unverifiable`; and a fold that would put two live rows on one fingerprint is REPORT-AND-STOP, writing nothing and naming the pairs, because an automatic merge would have to invent a winner.

  **(7) An import is not an observation, and `findings.import_findings` — not the CLI handler — is where that means something (CB-51).** A fingerprint hit on a reopen-status row is SKIPPED; a live hit still bumps and a `wont_fix` hit still files a recurrence, neither of which was a defect. The id guard compares CONTENT as well as id, so a colliding foreign row lands with a fresh local id and `meta.imported_id`. **The id half cannot simply be deleted:** a row written with an explicit id stores `fingerprint = NULL`, NULL matches nothing, so a fingerprint-only skip cannot see it and every pre-CB-43 row and every explicit-id row would duplicate on each re-import. The whole loop runs in ONE `db.txn` (CB-77), so a read failure lands nothing and **the rollback path must print no count**. **`batch_add_findings` is deliberately NOT the seam**: it has no `annotate` parameter, so import would silently run the similarity resolver per row inside the held write lock, and it validates every member before the transaction opens, which forbids per-row error partitioning. A faithful backup RESTORE — id, status, `occurrence_count`, `created_at` verbatim — is a different seam again (a raw INSERT bypassing identity, resolvers and post-add hooks) and is CB-97, not this.

  **(8) SEVERITY IS MONOTONIC UNDER OBSERVATION, and it is the only column a bump refreshes (CB-52).** `_bump_row` writes the more severe of (stored, observed) — **escalation only**, so a `critical` card re-observed `low` stays `critical`; use `update_finding` to downgrade deliberately. The comparison goes through `types.severity_rank`, derived from the `SEVERITIES` tuple exactly as `rank_case_sql` is, because a second hand-written precedence table is one drift from disagreeing with the first (CB-22) — and **the direction is the trap**: `SEVERITIES` runs most-severe-first, so the worse of two is `min` over ranks and `max()` is backwards in both spellings. **`escalate=False` has exactly one call site, `import_findings`** — an import is not an observation (CB-51), so a peer's CSV records the occurrence but must not re-rate a local card on foreign evidence; `escalate` is deliberately NOT exposed on `add_finding` (where `annotate` is), so no MCP or CLI caller can turn the invariant off by argument, and the count is pinned by `tests/test_dedup.py::TestEscalateOptOutRatchet`, **read by AST rather than grep**. **The parameter-ordering hazard is closed structurally rather than documented:** every fragment of a built `SET` clause is appended with its own parameter, `meta` included — point-of-use discipline is the wrong enforcement layer (CB-41). The general form of that abstraction across the package's seven string-built SET clauses is CB-37's question, not this card's. **Two things this rule does NOT do, both deliberate and both ratified:** `reported_at_commit` stays frozen (CB-53's separate answer — readers consult the ring), and **milestone routing is NOT re-evaluated** — `stream/security` placement is decided once at filing time, which is CB-35's open question.

  **(9) TAGS UNION UNDER OBSERVATION (BT-4): a bump merges the observation's tags into the `tags` column — on live AND reopen bumps, because a regression is an observation.** Union is exact string equality (no casefold — `Tag` and `tag` both live), first-encountered order with stored tags before observed, deduplicated; the merged container is `json.dumps`ed ONCE and that exact string is the bound parameter (CB-82), and `tags = ?` is appended INSIDE the sets builder paired with its own parameter, exactly once (CB-16). **`promote_tags=False` has exactly one call site, `import_findings`** — foreign tags stay out of the local column while the ring still records them; it is absent from the `add_finding`/`batch_add_findings` signatures, so no surface can turn the union off by argument, and the count is pinned by `tests/test_dedup.py::TestPromoteTagsOptOutRatchet` (AST, same shape as the escalate ratchet). **Stored tags are STRICT-parsed pre-write, on the promote path only** — the union cannot be computed from a value that does not parse, so a bump over malformed stored tags fails with nothing landed: this MOVED the malformed-stored-tags corruption class from post-commit (`PostCommitCorruptionError`, which stays as the defensive classifier with an honest reachability note) to pre-write `json.JSONDecodeError`. On the import path the column is neither read nor written, so an import's live-hit on a corrupt row still lands. A valid non-list stored value is displaced, not merged (the ring guard's convention), never a `TypeError`. The manual `update_finding` path acquired NO union — it never calls `_bump_row`, and a pin test holds that a status write leaves the column untouched. **Tag REMOVAL is deliberately not built**: `update_finding(tags=)` stays a full replace, so a hand-removed tag returns with the next observation carrying it — the sub-decision (a cap / tombstones / a `finding_tags` table) is OPEN with the owner.

  **(10) THREE MORE FIELDS ARE OBSERVATION-FROZEN, DECLARED IN WORDS (BT-4).** `source` = the FIRST reporter, frozen by design — later observations' sources live only in the ring, and an imported observation's ring source can be a peer tracker's; this closes CB-21's `source` cell as a DECLARED immutability, and `tests/test_update_parity.py` carries `source` in `IMMUTABLE` with this reason, on both entities. `reported_at_ref` = observation-frozen but manually mutable BY DESIGN via `update_finding(reported_at_ref=)`, since a release is tagged after filing — do not confuse it with the immutable `reported_at_commit` — and `query(ref=)` matches the first-observed-or-manually-assigned ref exactly, never the ring; no ring reader is built until a consumer of latest-observation semantics appears. Top-level `meta` = the row's AUTHORED state — a re-observation's meta lands only as per-occurrence evidence in `meta.occurrences[*].meta`, `query(meta_key=)`/`meta_value` read the authored column, and promoting specific keys into the row is a future allowlist by measured demand, not a general merge. Pinned by `tests/test_boundary.py::TestBt4FreshnessDeclarations` (prose↔code) and `tests/test_dedup.py::TestObservationFrozenFields` (behaviour).

  **(11) THE `attention` BLOCK — a serious divergence between an observation and the card it matched becomes a STRUCTURAL, top-level field instead of something a reader has to dig out of the body (BT-5).** The key is present in EVERY `add`/`batch_add` response, on all four branches, and `[]` is a normal answer meaning *evaluated, nothing fired* — never *no such channel*; the precedent is `claims._response`, whose keys are all unconditional. The signal vocabulary is CLOSED, and the signal×branch matrix is DERIVED and LIVE: `_ATTENTION_SIGNALS_BY_ACTION` is read by the builder, so a wrong cell changes the RESPONSE and not merely a test, and an unclassified action raises `KeyError` — fail-closed, because *evaluated, nothing fired* is the one meaning a new dedup branch must not be able to borrow. Two signals today: `severity_escalated` (`from`/`to`, branches `bumped`/`reopened`) and `category_divergence` (`observed`/`stored`, every branch that HAS a matched row — `recurrence_of_closed` included, where the comparison is against the dismissed twin — with BOTH sides normalized, so a difference of spelling is not a signal while a difference of name is, and a non-string stored category is skipped for `_existing_categories`' reason). Transport is `AddOutcome`/`BumpOutcome`, and two properties of it are load-bearing: **the single severity comparison stays inside `_bump_row`** (a second one anywhere is the drift CB-41/CB-52 exist to foreclose), and signals are assembled INSIDE the transaction so `_finalize_add` stays MECHANICAL — the post-commit conversion path must not acquire a new way to fail. Import carries no block BY CONSTRUCTION (`import_findings` reads the outcome directly and never calls `_finalize_add`), which is why there is no opt-out flag: it would be dead code. Audience is MCP-only — the CLI prints fixed lines and does not serialize the response, and there is no batch verb there. **The wire golden is NOT the gate on the response shape**: no `outputSchema` is snapshotted and the live schema carries `additionalProperties: True`, so extending it would be a gate that cannot fire; the gate is the behavioural MCP-result test. Exact numbers — cells, signals — live in the tests and deliberately not here.

  **(12) STRIP WITH VISIBILITY, ON THE ADD PATH ONLY (CB-56/CB-60, closed by the wire pin at CB-160).** `add`/`batch_add` do not REFUSE a caller-supplied `meta` key that is identity machinery's own OUTPUT (`occurrences`, `occurrences_dropped`, `regressed`, `recurrence_of`, `category_minted`, `fingerprint_refusals`, plus any extension's own reserved keys via `db.resolver_reserved_meta_keys()`) — a `get` → modify → `add` round trip is a real caller shape. What gets stripped is reported, never silent: `stripped_meta_keys` follows the `attention` discipline exactly — a top-level list, unconditional on every branch, `[]` meaning "checked, nothing to strip" and never "no such channel" — because a caller must be able to tell from the response alone which of its own keys did not land (the CB-15 "discarded caller data" shape applies to a silent strip exactly as to a silent refusal). **`resolver_errors` stays an outright REFUSAL rather than a strip**, because it reports a FAILURE state — a resolver's annotation attempt did not land — rather than machinery input, and silently discarding a caller's belief "my last observation's resolver failed" is exactly what stripping-with-visibility exists to avoid for everything else. **The UPDATE path is untouched**: `update`'s `meta_update` refuses every reserved key outright (a resolver-declared UPDATABLE key like `similar_to` is the one exception, from the registry, never a literal), because an unrepairable stamp surviving under a silent strip is the CB-26 shape. **One boundary is named rather than claimed closed**: CSV import strips the same dynamic reserved union INCLUDING `resolver_errors` — silently, with no response key, because import is not an observation (CB-51) — so "one behaviour on every ingestion surface" is NOT what this achieves; the divergence is narrowed to one key and pinned by test rather than reconciled. Both tool descriptions name `stripped_meta_keys` explicitly.

  **(13) THE THIRD OPT-OUT OF THE SAME FAMILY, AND THE ONE THAT REFUSES A COMBINATION RATHER THAN NARROWING A MERGE (CB-230).** Read it with items (8) and (9): what makes the three a family is *a keyword-only opt-out that turns off one write this package makes on its own initiative, has exactly one call site, is absent from every surface, and has an AST ratchet pinning the count*. **The PATH is where the three differ, and the defining clause must not flatten that**: `escalate` and `promote_tags` sit on the observation path, `authored` on the UPDATE path (CB-247). `update_finding(..., authored=False)` is a SERVICE write governing EXACTLY ONE COLUMN — under it the `updated_at = ?` assignment is simply not appended to the built `SET`, and everything below that `if` (the id parameter, the UPDATE, the hook firing, the re-read) is outside it deliberately, so the flag can change whether the row claims a human last touched it and never what lands. **`authored=False` has exactly one call site, `loc.py`'s anchor refresh** — an anchor is this module's own output rather than something a person or a commit wrote, so refreshing it must not make the card look recently changed; the count is pinned by `tests/test_cb230_service_write.py::TestServiceWriteCallSiteRatchet`, and `authored` is deliberately absent from the MCP and CLI surfaces, which that same ratchet asserts against the MCP wrapper's SIGNATURE and the CLI's argparse DESTS rather than against any text. **Do not re-check that with a grep over the wire goldens**: `tests/golden/mcp_schema.json` carries the word `authored` once as ordinary prose inside `query`'s description, so a raw text search answers a different question from the one being asked. **Where it differs from its two siblings: they NARROW what a merge does, while this one makes a COMBINATION unrepresentable.** `status=` together with `authored=False` is REFUSED, and the refusal sits ABOVE the transaction, so nothing is written before it fires: a status change is an authored act, and the one way this flag could ever have erased a real date is foreclosed by construction rather than by discipline at the point of use — CB-41's rule applied to a flag instead of to a deadline.

  → почему именно так: `docs/claude-md-rationale/database.md#cb-43-функция-тождества-находок`

- **Category spelling is normalized and MINTING a new category is gated — on the OBSERVATION path only (CB-60).** `types.normalize_category` (casefold, strip, hyphen/whitespace runs → `_`) runs in `add_finding`/`batch_add_findings` when `finding_id is None` — the same predicate as dedup and the pre-add resolvers — and BEFORE `auto:v1` derivation, so twin spellings (`process-improvement` vs `process_improvement`, the measured identity fork) hash and store one canonical name. A category the table does not already hold (compared on normalized forms, so a pre-CB-60 stored spelling still legitimizes its normalized twin) requires `new_category=True` (on both domain functions, MCP `add`/`batch_add`, CLI `--new-category`): without it a near-miss is refused naming the canonical spelling (Levenshtein with a conservative length-scaled threshold — the flag escapes either refusal, so the threshold only shapes the message, never blocks a determined mint) and a genuinely new name is refused listing the nearest existing ones. A permitted mint stamps `meta.category_minted: true`, so `query(meta_key="category_minted")` counts minting events; the stamp is reserved on ADD only (spoof-proof) and deliberately writable via `update_finding(meta_update=)` — an unrepairable stamp is the CB-26 shape. `""` stays a legal, ungated category (similarity's pool matches it exactly). NOT normalized and NOT gated, each deliberately: explicit-`finding_id` adds (asserted identity — fixtures file verbatim), `import_findings` (CB-51 verbatim contract; a foreign `category_minted` is stripped like the other reserved keys) and `restore_findings` (raw INSERT). **The ADD path never rewrites a stored row** — that half is unchanged and it is exactly why an old-spelling row does not fingerprint-match a new normalized observation. What is no longer true is the sentence that used to follow it, "deliberately left open": the retro-fold EXISTS, as a separate and explicit operation (`findings.normalize_categories`, CB-61 — see item (6) of the CB-43 bullet above for its boundaries), not as anything the add path acquired. **Running it on a live tracker is the OWNER's decision, not a consequence of the code landing** — it is dry-run by default and `--apply` is his to type. That separation is the rule, and it is also what happened: the run was ratified as its own decision after the code had landed, and this tracker's corpus was folded once (17 rows, no collisions, nothing unverifiable), which is why it no longer carries variant spellings. A tracker that has not been folded still does.
- **Requirements deliberately have NO identity function** — DECIDED on CB-45 (the card delegated it verbatim: "decide … or documents why not"): requirement rows are authored artifacts with caller-assigned ids on every write path (the same explicit-id bypass that skips dedup for findings), no automated filer emits requirement observations, and reqs similarity already exists via embeddings — a fingerprint column with zero writers would be dead code. Revisit trigger: an automated requirements filer appearing.
- **Similarity extension (CB-45): `similarity.py` is the package's FIRST self-registering non-domain module — legal because it issues ZERO SQL.** All row access goes through the public accessor `findings.similarity_candidates` (raw rows, `meta_json` as the stored STRING per CB-24 consequence 4, deterministic `ORDER BY created_at, id`); no other module may SELECT from findings. Detector: char-trigram Jaccard over `similarity.normalize_text` = the fingerprint normalization (public wrapper `findings.normalized_identity_text`) + ANSI-remnant strip — the extra cleanup lives in the extension because `auto:v1` is versioned and must not drift. `DEFAULT_THRESHOLD = 0.7` is CALIBRATED, not chosen: on the 3162-row autosorter corpus it collapses 102 rows into 11 coherent families and splits the 115-row gate category into its ~10 genuinely distinct failure tails; the CB-45 card's 0.95 was measured and REJECTED (77 rows, target family never unifies — unifying it would be the false merge CB-43's RISK section forbids), notified per the letter-fix protocol; `tests/manual/verify_similarity_corpus.py` reproduces the numbers exactly. `MIN_TEXT_LEN = 40` lives in the SCORING layer (resolver, report, and check share one policy — trigram Jaccard scores "Bug 1"/"Bug 2" ≈ 0.8 and two empty strings 1.0). The file-time resolver stamps `meta.similar_to = [{id, score, status}]` from a pool of live ∪ {wont_fix, not_a_bug} rows in the same category (a "resembles CB-N, already dismissed" link is the most valuable annotation; `fixed` stays out — exact matches already reopen), newest 500, trigrams memoized BY CONTENT (an (id, created_at) key collides across databases within one whole-second timestamp). The pool's category is a VALUE, not a filter: findings permit `category=""`, which the accessor's `category=` filter convention reads as "no filter", so the resolver passes the explicit-tuple twin `categories=("",)` and matches exactly — otherwise every empty-category observation pooled the whole table (Codex diff review; same round replaced `group_report`'s bare `status == "all"` sentinel test with a type-pinned one, CB-25's `mock.ANY` trap). `similar_to` is reserved on ADD only and writable via `update_finding(meta_update=)` — an unrepairable annotation is the CB-26 shape; `resolver_errors` is refused on both paths. The update-side exemption is DECLARED at registration (`updatable_keys=("similar_to",)`) and read from the registry (`db.resolver_updatable_meta_keys()`), never hardcoded in findings — core must not know an extension's key names (same-day review). The annotation pool is likewise DERIVED (`LIVE_STATUSES + RECURRENCE_STATUSES`, both public from findings), so `TestBranchTotality`'s classification guarantee reaches the pool instead of a re-spelled enumeration. `group_report` (MCP `similarity_report`, CLI `similarity-report`) is CB-46's dry run and reports its own evidence: per-family `min_pair_score` is the DIAMETER over all member pairs (sub-threshold included — recorded edges are ≥ threshold by construction and can never reveal union-find chaining; the corpus's 43-row family hides a 0.392 pair behind 0.7+ edges), plus the edge list and member description excerpts; default population is LIVE rows (`status="all"` widens — grouping decided rows into a merge dry run would contradict decision-stays-decided). Embedding vectors are caller-supplied and OFFLINE-only (`group_report(vectors=)`) — an MCP client cannot practically pass thousands of vectors per call.
- **The one sanctioned cross-table status write** is `entities.EntityRef.set_status(conn, new_status=…, expected=…)`. It runs inside the caller's transaction, must not commit, and returns whether the row moved. Domain modules keep owning their own tables; this exists so the claims ledger can project a status without importing a domain module.
- **An interpolated SQL identifier is validated where it is DECLARED, not where it is used — and `types.is_sql_identifier` is the only copy of that pattern.** Values are always bound; identifiers (table, column, `ORDER BY` target) sometimes cannot be, and **only some** such sites carry `# noqa: S608` — most do not, `bench.py`'s run-listing query and `blockers.py`'s trigger-type query among the unmarked ones, and there is no inventory anywhere naming which sites got a marker and which did not. **That marker checks nothing today, and this is not an aside — `S608` is simply not in this repository's enabled lint rules.** `pyproject.toml` carries no `[tool.ruff.lint]` section at all, so `ruff check` runs its default selection and never evaluates `S608`; a `# noqa: S608` therefore suppresses a warning the linter was never going to raise in the first place. **The actual protection is validation at the identifier's point of DECLARATION, not the linter** — `EntityKind.__post_init__`, described next, and `types.is_sql_identifier` itself. **Turning `S608` on is a separate, deliberately accepted debt (CB-172), and it has a measured cost rather than a free one**: doing it lights up a batch of unsuppressed hits spread across many source files in a single change — this project's first lint-rule configuration ever — together with a comparable batch of dead markers belonging to OTHER rules that `RUF100` surfaces the moment anything is enabled. `EntityKind.__post_init__` validates `table`, `sort_col` and **every member** of `readable_cols`, so a malformed kind dies at construction — including via `dataclasses.replace()`, which the tests use. Before CB-22 the comment claimed all three were guarded and only `sort_col` was, inside `order_by()`; a kind carrying `readable_cols={"(SELECT meta FROM findings)"}` passed the membership check and `field()` returned the `meta` column. Note **an allowlist membership check guards the caller's argument, never the allowlist's own contents** — those are two different obligations, and only the first is visible at the query site. Two related traps: **anchor with `fullmatch`, not `^…$`** (`$` also matches before a trailing newline, so the old pattern accepted `"findings\n"`), and a check that is *duplicated* rather than *shared* is one drift away from disagreeing with itself — `entities._SAFE_IDENT` and `types._IDENT` were byte-identical and compiled to the same object only because `re` caches on the pattern string. The same shape recurs wherever a column name is composed: `milestones/capacity.py` builds `f"{size}_held"` and goes through `_held_col()`, because when it didn't, an unknown size raised `OperationalError` if the agent had a capacity row and **silently lost the increment while returning success** if it did not.

### Error handling
- Domain functions raise `ValueError` for invalid input and `KeyError` for missing entities.
- MCP tools let exceptions propagate to the MCP server's built-in error handling.
- CLI handlers catch domain exceptions and print to stderr with `sys.exit(1)`.
- **A failure raised AFTER the commit must never be reported through the input-validation arm.** A domain update commits its write and only *then* can raise while serializing the return value from a row with malformed stored `meta`/`tags`. Reporting that as bad input prints a tidy one-line error and exits 1 for a mutation that **already landed** — a failure-shaped signal for a successful write, the same class of lie as CB-15/CB-16. **The rule is encoded exactly once, in `cli.domain_errors()`** — every CLI handler that touches a domain call routes it through `with domain_errors():` rather than catching exceptions itself, and its two `except` arms must stay in this order: `json.JSONDecodeError` re-raises unchanged FIRST (a matched row's corrupted stored `meta`/`tags`, discovered while serializing a write that already committed), and only THEN does a plain `(ValueError, KeyError)` arm print one line and `sys.exit(1)`. Reversing or collapsing the two loses exactly this distinction, because `json.JSONDecodeError` **is** a `ValueError` subclass — enumerate what subclasses a widened catch before trusting it. Two pins hold the order at different grains, and both are needed: `tests/test_findings.py::TestDomainErrorsOrderingPin` exercises `domain_errors()` directly, in isolation (CB-159, filed because this paragraph once named only the end-to-end pin, leaving the wrapper itself unexercised), and `TestRetriageCliContract::test_a_committed_write_is_never_reported_as_bad_input` re-confirms it through the real `update` CLI verb and a corrupted database — measured against this exact mutant: removing the `except json.JSONDecodeError: raise` arm turns that one test red while 5 of the class's other 6 are unaffected, which is what "the ordering is load-bearing" means concretely. `_cmd_query` and `_cmd_reqs_query` carry the same ordering, added when their vocabulary filters began resolving (CB-19) — until then neither caught `ValueError` at all, so an unknown `--status` printed a **raw traceback** and leaked the connection. That is the other half of this rule: **a handler that catches nothing violates it just as surely as one that catches in the wrong order.** `_cmd_reqs_update` was the last asymmetry and is closed (T-57, merge `7e46180`): it routes through the same wrapper rather than catching `KeyError` alone.
- **`OSError` arrives from ambient sources, not just from `open()` — and a guard spelled as one errno is an enumeration (CB-79).** CB-71 swept for `open(` and closed five sites; that spelling structurally cannot see `os.getcwd()`, `subprocess`, `Path.read_text` or `sqlite3.connect`. Two holes it missed, both reproduced: `reqs-verify` from a **deleted cwd** printed a raw `FileNotFoundError` (a long-lived MCP server outlives the worktree it started in), and a **non-executable git** raised `PermissionError` out of `provenance.file_status`, whose guard caught only `FileNotFoundError` — *git is missing* and nothing else. All five subprocess guards (`provenance.py` ×4, `db.git_rev_parse`) now catch `OSError`: a **strict widening**, since `FileNotFoundError` is an `OSError`, while `subprocess.SubprocessError` must stay in each tuple because it is **not** an `OSError` subclass and dropping it loses `CalledProcessError`/`TimeoutExpired`. **Widening a guard can expose a latent wrong answer, and here it did**: the rename lookup swallowed its failure into `rename_output = ""` and the fall-through then reported `deleted` — the "guard reporting clean because it could not look" shape, stated as a fact about the file; it returns `unknown` now. **Degrade or raise is decided by the CALLER's contract, not by the failure**: `provenance` degrades (`file_status` → `unknown`, `_parse_trailers` → `[]`) because that is already what it does when git is unreachable, and `db._db_path` raises `DatabaseNotFoundError` because its callers all handle that; `verify_requirements` raises because it has **no** unknown vocabulary, so a false clean would be the worse answer. **Resolve an ambient value where it is USED, not at the top of the function** — `root` is consumed only by the `tests` check, so an eager `os.getcwd()` broke `checks=["ids"]` for a check that never looks at a directory. And `_cmd_reqs_verify` needs the `json.JSONDecodeError`-before-`ValueError` ordering like its siblings, because `verify_requirements` calls `db.row_to_dict`. **A negative result worth not re-deriving:** a `chmod 000 git` placed *earlier* on `PATH` does not reproduce the PermissionError — CPython's exec continues the `PATH` search on `EACCES` and finds the real git, so the non-executable one must be the only one on `PATH`.
- **A per-row swallow inside an import loop catches the row-level exception CLASS, never the tree (CB-99).** `reqs.import_markdown` guarded its per-row INSERT with `except sqlite3.Error` — every SQLite exception there is — so an environmental failure arriving mid-import was counted as a malformed ROW and the import reported success: measured with a simulated `SQLITE_FULL`, `{'imported': 0, 'skipped': 2}`, no exception, `Imported 0 requirements, skipped 2.` at exit 0. **Strictly worse than the traceback CB-86 removes**, because a traceback is loud. The narrowing is to `sqlite3.IntegrityError`, **the class for a row that violates the table's constraints** — written as that sentence and deliberately NOT as a list of codes, because review measured the list wrong in both directions (`SQLITE_MISMATCH` is also an `IntegrityError`; `SQLITE_TOOBIG` is a `DataError`, a sibling). What the split rests on is that CPython routes every environmental code to `OperationalError`, so nothing environmental is inside the arm; a test pins that a CHECK violation on `requirements` really is an `IntegrityError`, as a premise rather than an argument. **No classifier is involved, and that is better than reusing `_is_environmental`**: the exception TREE already draws this line, so reaching for a predicate would have meant exporting a deliberately private one or growing a second copy of its enumeration. Two things to know before touching it: with the resolvers normalising `status`/`priority` before the INSERT and `INSERT OR REPLACE` foreclosing UNIQUE, **no parseable markdown row can currently violate a constraint at all** — measured, so the arm is a safety net for a future schema, not a live path; and because the commit is at the END of the loop, propagating rather than swallowing means a mid-import failure now lands **nothing** instead of a partial import reported as success. **Do not read that as "`skipped` stays 0"** — an earlier draft of this bullet did, and it was wrong in a user-facing way: `skipped` has a second, live producer in the `len(cells) < 4` guard, reachable by construction because `_ROW_RE` anchors only on the leading id cell (measured: a two-column row plus a full row gives `{'imported': 1, 'skipped': 1}`). It is the INTEGRITY contribution that is expected to stay 0. The whole-package sweep for this shape (`grep -rn "except sqlite3\." src/`) found **one** instance, which is worth recording precisely because this repo's usual answer is "the population is larger than the list".
- All MCP tools return `dict[str, Any]`.

### Testing
- Tests live in `tests/test_<module>.py`. Most test classes use a fresh in-memory DB via a `conn` fixture.
- Tests requiring `db.connect()`, cross-module schemas, or git operations use `tmp_path` file-based DBs.
- Each test file defines its own fixtures. `tests/conftest.py` is not a shared-fixture drawer: it admits **exactly one KIND of inhabitant** — a property that protects the whole suite, whose failure mode is silent or unattributable, and which every future test file would otherwise have to remember for itself. Ordinary fixtures are not that, and still belong in the file that uses them. **A safety property whose failure mode is silent corruption must not be an enumeration every future file has to remember**, and neither must one whose failure mode is a thousand failures pointing at code that is fine. **The rule is stated as a KIND, never as a COUNT (CB-204)**: it read *"exists for exactly one thing and should stay that way"* and had to be rewritten the first time a second qualifying property appeared — a count in prose is the thing this document has twice been wrong about, so the property is a sentence instead: **every inhabitant answers one question in a different place — WHAT DID THIS RUN ACTUALLY JUDGE?** It is asked of the TRACKER by an ambient-state fixture and by the CB-204 session guard, where the failure is a test that NAMES one state and gets another because `db.connect()` resolves against ambient state the test never declared; and of the SOURCE TREE by the CB-215 alarm below.

  The ambient-state fixture clears `CODEBUGS_ROOT` and the tracker-root override, because three modules shell out to the CLI with mutating verbs and a forgotten guard silently rewrites the developer's real tracker — verified, not theorized: with the variable exported, the findings CLI tests moved a real CB-1 from `low`/`open` to `high`/`fixed`. The CB-204 session guard asks **the product's own walk** (`db._find_db_root`, the single function `_resolve_db` uses for the discovery route, called with an explicit start exactly as `cli.py:91` calls it) whether a `.codebugs/` sits at or above `tmp_path_factory.getbasetemp()`, and refuses the whole session with one named diagnostic if so. Three things about it are load-bearing and each was measured. **The walk is asked, never re-implemented**: a parent climb to `/` would falsely alarm on a tracker above a `.git` DIRECTORY (the walk stops there) and would MISS one reachable only by following a `.git` FILE to a linked worktree's main checkout (the walk jumps) — both are oracle rows, and a structural pin fails if the delegation is replaced. **The start point comes from the factory the `tmp_path` fixture is built on**, not from the literal `/tmp`: `--basetemp` and `TMPDIR` both move it, so a hardcoded `/tmp` would be a gate that cannot fire. **The declared channels are deliberately NOT checked** — the first fixture already neutralizes them before every test, so refusing on one would be the false alarm that gets a guard deleted by the first person it inconveniences. What it does NOT do, said plainly: the tests are not hermetic afterwards, they merely stop lying about why they failed. Measured 2026-08-26 by running it: with an empty `.codebugs/` directly above the temporary root, **1071 of 2739 tests** fail or error, and after the guard that same state is one refusal in 0.7s at exit 4.

  **The CB-215 alarm is an ALARM rather than a guard for the reason this document keeps drawing that line**: by the time the two samples can be compared the run is over, so there is nothing left to refuse. It fingerprints every file in the tree — path, size, `mtime_ns` — before the first test and again in the terminal summary, and prints what differs. It exists because the suite is re-run by an acceptor **in the main checkout**, which is exactly where other directions land their branches, while structural tests here read source files from disk: measured on main's own history, the median gap between first-parent commits is 141 seconds against a run of ~170, so a merge arriving mid-run is an ordinary Tuesday and the partial red it produces is indistinguishable from a regression. Four properties are load-bearing. **The exit status is never touched, and the message says so in words** — a moved tree is ordinary traffic, and refusing over it would manufacture a false red out of noise. **The discriminator is the FILES, not `HEAD`**, measured: `git rev-parse` fails outright in a tree unpacked without a git directory, does not move in a worktree when `main` moves (the case that must stay silent), and cannot see an editor or a formatter writing a file nobody committed; the commit name is printed as a SIGNATURE when git answers, and its absence is never a failure. **Nothing is pruned by judgement** — `.claude/plans/` is deliberately watched, because `tests/test_exposure_matrix.py` really does read `.claude/plans/exposure-scripts/matrix.py` off the real tree, so *"the suite does not look there"* is precisely the unchecked premise the alarm exists to stop people acting on; the two prune tables hold only what is not a source of anything (git's own directory, the virtual environment, the two worktree directories, the tracker, and caches), each with the sentence saying why, and a bare list with no reasons becomes the place inconvenient paths are hidden. **And on a still tree it prints nothing at all** — not a header, not an empty section; measured over the full suite, 2878 tests, silent. Two boundaries, both found by running rather than by reading: the same name is pruned as a FILE and as a DIRECTORY by ONE predicate, because `.git` is a directory in the main checkout and a file in every linked worktree, and a rule that answered differently in the two would be the wrong rule for a defect whose whole subject is main-versus-worktree; and the alarm cannot stop the race, only report it, over a window running from the first test to the last — a tree that moved during collection is invisible to it.
- Test the domain module's public API, not internal helpers.
- **A concurrency test's ASSERTION is the hard part, not its scheduling (CB-27, CB-30).** Three separate drafts in one iteration could not have failed against the unfixed code, which is the failure this repo keeps shipping. Three rules, each earned: **(a) check that the final STATE actually discriminates.** In the `mark_items` race the item ends at `b` both before and after the fix, so the only real discriminator is *which writer is refused* — capture the competing thread's exception and assert on it. **(b) Never wait unboundedly on the losing writer.** After the fix it blocks at `BEGIN IMMEDIATE` and can never complete, so "let B finish" just burns `busy_timeout`. Copy the bounded three-event interleave in `tests/test_findings.py:504-547`, whose docstring explains why the `b_started` guard before the 1.0s `b_read` wait is what stops a false pass. **(c) To probe a commit seam, hook BOTH seams.** Unfixed code closes with `conn.commit()`; `db.txn` closes with `conn.execute("COMMIT")`. A hook keyed on one gives a vacuous pass on the other. `CommitPausingConnection` in `tests/test_milestones.py` does both, fires *after* the underlying commit (firing before it leaves the write lock held, so the injecting connection deadlocks), and is single-threaded — no timing luck. Corollary: **a test that passes on both sides can still be right**, but only when it pins behaviour the change deliberately preserved; say so in its name or docstring, or a reader cannot tell it from a broken one.
- Run tests: `uv run python -m pytest tests/ -v`
- Run lint: `uv run ruff check src/ tests/`
- Run format: `uv run ruff format src/ tests/`

### MCP tool registration
- Each domain module defines `register_tools(mcp, conn_factory)` and calls `register_tool_provider()` at module level.
- `server.py` discovers providers via the registry and passes `_conn` as the `conn_factory`.
- Tool parameters that accept JSON should use `str | list | None` (not just `str`) so MCP clients can pass native types.
- New modules: define `register_tools(mcp, conn_factory)`, call `register_tool_provider("name", register_tools)` at module level.
- **A declared argument must reach its query, or the call must fail — routing is not an excuse (CB-28).** The rule below covers *unknown* argument names. Its twin failure is a **known, correctly spelled, correctly typed** argument that a branch simply never forwards: `query(status="deferred", severity="critical")` returned **every** deferred finding, and the caller read that as the critical ones. Same success-shaped lie, reached through routing instead of validation, and no validation layer can see it. Two different repairs, and picking the wrong one is how this becomes a stopgap: **forward when a path exists, refuse only when none could.** `deferred` is a *pseudo-status*, so it resolves to an id restriction — via `blockers.deferred_ids_and_counts` since CB-69, which returns the restricted ids and their active-blocker counts from ONE evaluation — and the **owning domain** applies its own filters; blockers never learns what `severity` or `priority` mean. That shape was already specified in `docs/2026-04-04-blockers-design.md:278-291` and `get_deferred_item_ids` was already written for it; the wrappers just never used it, and `provenance.check_findings`' docstring had promised it all along. **Check for an existing design before concluding the clean fix is infeasible** — the first plan here proposed refusing at every site, and cross-model review showed that was a cheaper substitute for a fix the repo had already designed. Refusal is right only where nothing could honour the argument: an abandoned milestone item has no `done_commit` column, `set_item_status`' no-op path performs no write (use `mark_integrated`), and a lone `meta_value` has no key to look up. **The empty intersection is the trap**: an empty `ids` list means "no filter" to every domain query, so forwarding one returns the whole table — this defect reappearing inside its own fix, exactly as the naive predicate reintroduced CB-25 inside its. Short-circuit to an empty page; `TestDeferredEmptyIntersection` pins it.
- **Unknown argument names are refused, not ignored.** `server.install_strict_arguments()` runs after registration and rejects any `tools/call` carrying an argument the tool does not declare. Without it the SDK builds each tool's argument model with pydantic's default `extra="ignore"`, so a typo'd name is dropped during validation and the tool returns a **success payload with the caller's data discarded** — while a bad *value* raises (CB-15). Note `additionalProperties: false` is not an alternative: the server never validates arguments against the JSON Schema, verified by injecting it and watching the call still succeed. This is the one place the project touches `MCPServer.middleware`, whose signature the SDK documents as **provisional** — if an upgrade breaks it, repair that function and `tests/test_server.py`, nothing else.
- **What a client SEES must not depend on which interpreter built the server (CB-73).** The SDK reads `Tool.description` from `__doc__`; CPython 3.13 dedents docstrings at compile time and 3.11/3.12 do not, so on the older hosts `requires-python` admits, clients received the source indentation — and because MCP clients render descriptions as Markdown, CommonMark turned a 4-space-indented line after a blank line into an **indented code block**, rendering most tools' whole prose body as monospaced code. Measured on both interpreters: 64/68 descriptions differed and 61/68 carried the code-block pattern; both are 0 after the fix, and 3.13 output is byte-identical before and after — which is exactly why the wire golden did not move. `server._NormalizedDescriptions` wraps the registrar and passes `description=`, a **public, declared** parameter of `MCPServer.tool()`. **Two alternatives were rejected for reasons worth keeping**: rewriting `fn.__doc__` is a global side effect on another module's objects, and rewriting the registered `Tool` objects afterwards reaches into the SDK's PRIVATE `_tool_manager._tools` — a worse coupling than the provisional-but-public one `install_strict_arguments` already documents. An explicit `description=` from a caller still wins, because the adapter normalizes and does not decide. `dedent_docstring` now lives in `server.py` and `tests/_mcp_schema` imports it: while it normalized only the comparison a test-side copy was harmless, but now that the server emits normalized text a second definition would be one drift away from the gate and the server disagreeing about the very thing they exist to keep in agreement.
- **A parameter that exists in the domain layer is not reachable until it is declared here.** `append_note` sat unexposed behind the destructive `notes` for a long time (CB-18). When adding one, update the MCP wrapper, the CLI parser *and* handler, then regenerate the wire golden with `PYTHONPATH=src uv run python tests/dump_schema.py > tests/golden/mcp_schema.json` — from a worktree a bare `python` resolves `codebugs` through the editable install pointing at the main checkout and would snapshot the wrong tree.

### CLI
- Each domain module defines `register_cli(sub, commands)` and calls `register_cli_provider()` at module level.
- `cli.py` discovers providers via the registry and filters by `--mode` flag.
- New modules: define `register_cli(sub, commands)`, call `register_cli_provider("name", register_cli)` at module level.
- **Two commands are built into `cli.py` rather than owned by a domain module**, registered by `_register_builtins`: `init`, which bootstraps the DB every other command needs, and `where`, which diagnoses the case where that DB cannot be found at all. Both must work in every `--mode` and *before any tracker is reachable*, which is exactly what a domain module cannot promise. `--tracker-root` is likewise global: it lives on the `pre_parser`, so it is parsed before subcommand dispatch and binds every verb, not just `where`.
- **The process entry point is `cli.run`, not `cli.main`, and the split exists so a signal disposition never leaks into an importable function (CB-78).** `run` restores the POSIX `SIGPIPE` default and calls `main`; `[project.scripts]` and the `__main__` guard both point at it. Before this, a dead READER on stdout made every verb report a **committed** write as a failure — exit 1 with a `BrokenPipeError` traceback unbuffered, and exit 120 with "Exception ignored on flushing sys.stdout" block-buffered, the latter raised at interpreter shutdown where no `except` can reach it. `SIG_DFL` fixes both with one line and yields **141** (`128 + SIGPIPE`), which is deliberately distinguishable from `1`; see the exit-code API under **Claims module** for what that means to shell callers. **Two properties are load-bearing and both were established by measurement, not argued.** `main` must stay signal-free because `tests/test_fsio.py`, `tests/test_findings.py` and `tests/manual/repro_cb76_truncation.py` call it in-process — installed there, the disposition is an unrestored process-global mutation and the whole pytest session inherits it (reproduced: `pytest -q -s . | head -2` dies at 141 mid-suite). And `run` must **never restore** it: doing so in a `finally` puts the block-buffered case back to exit 120, because that write is the shutdown flush and happens after `main` returns. "Do not mutate process-global state" and "fix the block-buffered case" are incompatible inside one function — the split is what makes both true. `server.main` is deliberately excluded (its stdout is the JSON-RPC transport), and says so at the call site. Costs, stated because they are real: `export-csv /dev/stdout` and `reqs-export` into a dead reader lose their one-line diagnostic, and an install predating the change keeps the old behaviour until `pipx reinstall` regenerates the console shim.
- **A CLOSED stdout is a different state from a dead reader, and until CB-134 the same declared contract meant four different things across two interpreters.** CB-78's `SIG_DFL` covers a pipe whose reader went away. It cannot cover a stdout that is already closed, because no write reaches the kernel and no signal is raised — so that case fell through to whatever the stdlib did that release. Measured on 3.13.3 and 3.14.4, one mutating verb, the two spellings of "closed": `sys.stdout.close()` gives **exit 1 + a raw traceback** on both, but the write **lands on 3.13 and not on 3.14** (3.14's argparse touches stdout while the parser is being BUILT — `add_argument` → `_get_validation_formatter` → `_colorize.can_colorize` → `os.isatty(file.fileno())`, and `can_colorize` guards only `OSError` while a closed object raises `ValueError`); `fd 1` closed at exec gives **120** with "Exception ignored on flushing sys.stdout" on 3.13 and **0, silently, with the write landed** on 3.14. **That last cell is the dangerous one and it is the newest**: 3.14 sets `sys.stdout` to `None` for an invalid fd 1, `print` is a documented no-op against `None`, and the colour probe short-circuits on `hasattr(None, "fileno")` — so every verb runs, discards its whole output and reports success. That is the **"silent exit 0" CB-78's ratification rejected by name**, reached by upgrading the interpreter rather than by changing any code here: `codebugs export-csv /dev/stdout | gzip > backup.gz` reports success over a backup that was never written. `cli.run` now REFUSES at the process entry, before any work, with the same **141** — one vocabulary for one condition ("the reader of my output is gone"), uniform on **3.11 through 3.14**. That range used to read *"every interpreter `requires-python` admits"*, and CB-135's pin is what made the wider wording indefensible: every subprocess in `tests/test_cli_signals.py` is spawned with `sys.executable`, so the suite measures the one interpreter it runs under, and before the pin the range was covered only by different people happening to run different versions. Pin that variable and nobody ever runs the others again — a claim about a range, held up by an accident that had just been removed. The `contracts` matrix in `.github/workflows/ci.yml` replaces the accident with a measurement (`test_cli_signals.py` + `test_fsio.py`, 38 tests, ~1.7s per version). **Honest scope: 3.15 and later are admitted by `requires-python` and are NOT verified** until they are added to that matrix. The alternative — narrowing this sentence to the pinned version alone — was rejected as the more expensive of the two, since it would have left `requires-python = ">=3.11"` advertising a range nothing checks. **The price is a real behaviour change on 3.13 and is named rather than absorbed**: a closed-object stdout there used to let the write land and then fail on output, and now lands nothing — which is the point, since with the refusal ahead of the work there is no committed write left to misreport. **`sys.stdout = None` before `sys.exit` is INSURANCE, and a mutant deleting it SURVIVES** — said that way round because the first draft of this sentence claimed it was load-bearing and no test could discriminate it. The mechanism is real and measured on both interpreters: with content already buffered on a bad descriptor, finalization's flush fails and rewrites the status 141 → **120**, which is where the 3.13 fd-closed cell's 120 came from. It is simply not reachable from the gate, because nothing has written to stdout by the time the gate refuses, so the buffer is empty and the flush succeeds. The line stays as one-line insurance against a future in which something prints earlier, and `test_premise_a_failed_shutdown_flush_rewrites_the_exit_status` pins the mechanism rather than pretending the gate exercises it. It lives in `run` and could not live in `main` for the same reason `signal.signal` could not. **The probe reads the descriptor's ACCESS MODE, and `fstat` was measured insufficient**: with fd 1 closed at exec, CPython's own startup opens a file onto fd 1 (the lowest free descriptor) — `/sys/kernel/mm/transparent_hugepage/enabled`, **read-only** — so `fstat` succeeds on a descriptor that raises `EBADF` on the first write. **A stream with NO descriptor is treated as USABLE** (`StringIO`, a pytest capture object), which is deliberately the opposite of this repo's fail-closed default: here the conservative direction is to do the work, not to refuse it. **The predicate claims less than its name, and cross-model review rejected the first draft for claiming more**: `False` means *proven unusable*, `True` means *not provably broken* — never *a write will succeed*, the same affirmative-proof shape as `reconcile.live_source_clause`. Four residuals, each measured, none a regression (every one of them proceeds today too): `fileno()` does not govern `write()` — `io.TextIOBase()` raises `UnsupportedOperation` from both, so it is accepted and then fails, and refusing it instead would refuse every pytest capture object; a writable descriptor can still fail to be written (`/dev/full`, a full filesystem, a hung-up PTY) — that is a **write failure, not a closed stdout**, and needs its own non-input-error outcome as a separate negotiation; a file opened for WRITING landing on fd 1 passes the probe and takes the output; and **the 141 is not unconditional** — finalization also flushes `sys.stderr`, and a failing stderr flush rewrites the status to 120 even with `sys.stdout = None` (measured on both interpreters), reachable only by installing an stderr in-process before `run`, so no CLI invocation reaches it and making it unconditional would mean `os._exit`.
- **A CLI handler that writes a file uses `fsio.atomic_write`, never a bare `open(path, "w")` (CB-76).** `open(w)` truncates the destination *before* the first byte, so any write failure destroys the previous file — measured: a 34-byte export ends at 0 bytes on a simulated `ENOSPC`, and the `OSError` escaped as a raw traceback besides. **The obvious guard is a trap and the card exists to say so**: `except OSError` alone converts that traceback into one tidy line *over a file that is now empty*, and `import_markdown` (`reqs.py:564-566`) silently `continue`s past unmatched lines, so a truncated export round-trips as a successful, empty import. The helper writes a temp beside the destination and `os.replace`s it only after the handle **closed** successfully — quota and `ENOSPC` failures usually surface at flush/close, so replacing before that would install a bad file while reporting failure. Four asymmetries with `open(w)` are handled rather than discovered later, each from cross-model review: it **refuses a read-only destination inside a writable directory** (`open` authorizes on the file, `os.replace` on the directory, so without the check the fix would overwrite what the old code refused) using `os.access` with **effective** ids, since the default real-uid check would falsely refuse under setuid; it **writes in place, never replaces, when the destination is a FIFO/char device or an inode this process already holds open** — that second clause is what keeps `export-csv /dev/stdout > out.csv` working, and a node-kind check cannot substitute for it because `realpath("/dev/stdout")` resolves to the redirect target, an ordinary **regular** file (measured); it treats **only `FileNotFoundError`** as "missing", so a symlink cycle's `ELOOP` refuses instead of being classified as absent and replacing the link; and it resolves the path **before** taking `dirname` so the temp lands beside the *resolved* target (same filesystem, and the right directory for a symlinked destination). **`/dev/stdout` needs BOTH halves of the alias check, and that cost a review round**: with stdout redirected to a **file**, `realpath` yields that regular file and only the held-open-inode test catches it; with stdout on a **pipe**, `realpath` yields `/proc/<pid>/fd/pipe:[N]`, which does not exist, so `os.stat` raises `FileNotFoundError` and a stat-based classifier reads "new file to create" and tries to `mkstemp` inside `/proc`. Two resolutions of one path, neither check sufficient alone — the fd-directory test therefore runs **before** the stat. Note the *false* reason an earlier draft gave for the ordering: `mkstemp(dir="")` does **not** fail, it creates in the cwd (measured). **Deliberate narrowings — three, not two**: a writable file inside a **non-writable directory** exported before and now fails cleanly, because atomicity is impossible there and an errno-keyed fallback cannot tell that case from `ENOSPC`/`EDQUOT` — the very conditions where the following write fails and the old file is lost; **block devices** are refused (a partial direct write corrupts persistent bytes); and a **socket** changes only its errno, since `open(sock,"w")` already fails today with `ENXIO` — an earlier draft of this sentence counted two narrowings and lumped sockets in with block devices, which is this repo's own enumeration failure committed inside the bullet that cites it. What replacement cannot carry — ownership, ACLs, xattrs, hard-link aliases, and `fsync` durability — reaches users through the **CHANGELOG** entry; the module docstring carries the same list for the next maintainer, which is a different audience, not a user-visible channel. **`tests/test_fsio.py::TestWriteCallSitesRatchet` enforces this rule by AST** rather than leaving it as prose — the first draft of that ratchet grepped source text and matched `open(path, "w")` inside three of `fsio.py`'s own docstrings.
- **Where `init` creates is decided by the CHANNEL, not by the fact that a root was declared (CB-48).** This bullet used to read "`init` creates where you stand, and a declared root redirects only reads", and that flattened two channels `db.declared_tracker_root()` already tells apart. `$CODEBUGS_ROOT` is **ambient** — exported into a shell days ago, inherited by an unrelated subprocess — so it still redirects reads only: ambient state must never conjure a tracker in a directory the user is not in. `--tracker-root DIR` is typed on the command line being run, so it is an assertion about *this* invocation, exactly as `project_dir`/`--repo` is, and `--tracker-root DIR init` therefore initializes DIR. Precedence is one rule for reads and writes alike — argument > flag > env > walk — so a positional `init DIR` still outranks the flag. **Any surviving mismatch is announced on stderr**, because otherwise `init` reports success for a tracker every other command will ignore — a success-shaped signal for a dead end, the same class of lie as CB-15/CB-16. **The defect this fixed was worse than the ignored flag itself**: the warning fired on the path where the flag had been dropped, so it printed "commands will read DIR, not CWD" immediately *after* initializing CWD — two adjacent lines asserting the opposite of what was on disk. A test that asserts only "the target got a tracker" cannot see that; `TestInitUnderTheTrackerRootFlag` asserts the directory that must **not** have one on every case.

## Architecture migration (in progress)

We are migrating toward a plugin architecture in phases. Query with `reqs_query --section "Architecture Migration"` or MCP tool `reqs_query(section="Architecture Migration")` for the full plan (ARCH-001 through ARCH-005).

**All phases complete**: schema registry (ARCH-001) -> tool registration (ARCH-002) -> entity types (ARCH-003) -> CLI unification (ARCH-004) -> embedding separation (ARCH-005).

**Current rules for new code:**
- New domain modules must call `register_schema()`, `register_tool_provider()`, and `register_cli_provider()` at module level — do NOT edit `db.connect()`, `server.py`, or `cli.py`.
- Add the new module import to `_ensure_modules_loaded()` in `db.py` (temporary, until auto-discovery).
- Add the new module's mode slug to `SERVER_NAMES` (`server.py`) and to the `--mode` allowlist (`cli.py`) so it can be loaded in isolation.
- Prefer self-contained modules that register themselves over central wiring.

## Embeddings

`embeddings.py` stores a vector per requirement and answers similarity queries over them.
**There is no embedding provider in this package, and that is the fact every other rule here
follows from.** The CALLER computes the vector, in its own process, and passes finished numbers as
`embedding: list[float]`; the tools never receive the requirement's TEXT at all. Measured before
this section was written: the only declared runtime dependency is `mcp`, no module of the package
imports one of the socket-opening names the gate below enumerates, and the vector arrives as an
argument. **That sentence used to end "imports anything that could open a socket", which is a claim
about every socket-opener and not about a list** — measured against the gate's own function,
`from logging.handlers import SocketHandler`, the same module's `HTTPHandler` and
`from multiprocessing.connection import Client` all return an empty result, so the wider spelling
was false in the paragraph that calls itself a measurement (CB-190).

**The safety claim is bounded to this package's own code and to the vector's route, and the bound
is load-bearing rather than modest.** Do not write "codebugs cannot reach the network": the `mcp`
dependency carries a network transport of its own — `server.py` says so, an HTTP mode exists and
this project runs over stdio — and `subprocess` is used legitimately for git, which can of course
run `curl`. What is true and checkable is **two narrower statements about this package's SOURCE
TEXT, not one about its capability**: no module under `src/codebugs/` imports one of the
socket-opening modules the gate ENUMERATES, and — since CB-190 — no module imports anything at all
from outside the package and the standard library that is not DECLARED there by exact dotted name
with a reason. The vector's own route is a third, separate claim, and it stays as it was: from the
caller's argument into this tracker's SQLite file and nowhere else. A claim wider than its
measurement is the defect class this direction exists to close; stating it precisely matters more
than stating it loudly. **The bound is about naming, not about the process, and that is measured
rather than hedged**: importing the one declared MCP name already puts BOTH SSE transports —
`mcp.server.sse` and `mcp.client.sse` — into `sys.modules` along with most of the SDK, so what the
ratchet buys is that the source cannot NAME a second one without a row somebody reads, never that
the transport is absent. No module count is quoted here on purpose: the first draft quoted one,
and it was wrong because the measuring predicate matched the prefix `mcp` without the dot and swept
in `mcp_types`, a separate distribution — the exact confusion the gate's own table is written to
prevent.

**Those claims are held by a gate, because a safety assertion with no gate behind it is a "gate that
cannot fire" written as prose** — the literal subject of CB-159/CB-160.
`tests/test_no_network_capability.py` walks every package module by AST and holds **two mechanisms
that answer different questions, so neither may be deleted in favour of the other** (CB-190), plus
a refusal of `__import__`/`importlib.import_module`/`exec`/`eval`, which no check reading import
statements could see.

The first is an **enumeration of socket-opening module names**, and it keys on the CAPABILITY
rather than on the module name — the naive form was measured dead on arrival: `src/codebugs/db.py`
carries `from urllib.request import pathname2url`, so a name-keyed check would refuse the package
in its present, entirely healthy state. `pathname2url` is a pure string function that opens
nothing; `import urllib.request` binds the module and hands you `urlopen`. So a FROM-import of a
network module is judged name by name against a `DECLARED_EXCEPTIONS` table carrying a reason per
row, and a plain import of one is refused outright. **Being an enumeration is its defining limit,
and it is the whole of CB-190**: a client nobody listed walks straight past it, measured —
`cohere`, `ollama` and `httplib2` were green against it, and a planted module carrying all three
left the file reporting 25 passed.

The second is a **third-party import ratchet**, which is not an enumeration of what to refuse: it
refuses by default and enumerates what is ALLOWED. Every import whose dotted name leaves both this
package and the standard library must be named in `DECLARED_THIRD_PARTY` with a reason — five rows
today. **The key is the EXACT DOTTED NAME, never the top-level one**, and that is load-bearing
twice over: a top-level `mcp` row would have licensed `mcp.server.sse` and `mcp.client.sse`, so the
first row of a table meant to stop network imports being parked would itself have been a parked
network capability; and it would have made the table unable to go stale, since some `mcp` import
always exists. **The package's own name is DERIVED from `codebugs.__name__` and may not be written
as a row** — a row naming this package would lie about what the table declares, and, being live
forever, would defeat self-deletion. Both tables are **self-deleting** — a row naming an import
that is no longer there fails — because otherwise a table becomes the place real imports are
parked, which is the hole these mechanisms exist to close, one level up.

**One property is new and is declared rather than counted as covered.** Before CB-190 the verdict
was a pure function of the source text; the ratchet classifies the standard library by
`sys.stdlib_module_names`, so it is now a function of the source text AND the interpreter version.
On this tree nothing diverges — the **three** foreign top-level names (`mcp`, `mcp_types`,
`pydantic`) are foreign on every admitted version, measured on 3.11 as well as on the pinned
interpreter — but the CI matrix runs only `test_cli_signals.py` and `test_fsio.py` across
3.11–3.14, so this file is executed on the pinned interpreter alone and nothing would notice if
that stopped being true. `codebugs` is the fourth top-level name the tree imports and is
deliberately absent from that list: it is excluded by DERIVATION rather than by foreignness, and
conflating the two is what an earlier draft of this sentence did — it said "four" and then listed
three. Relatedly, `telnetlib`,
`nntplib`, `asyncore`, `asynchat` and `smtpd` are kept in the enumeration precisely because the two
mechanisms disagree about them by version: measured, all five are in `sys.stdlib_module_names` on
3.11 (3.12 keeps the first two), and none is on 3.13 or 3.14 — so the ratchet refuses them by
itself on the newer half while the enumeration is the sole catcher on the older half.

**RULE, ratified 2026-08-25 with the owner's task: if an embedding provider ever lands INSIDE this
package, it is configurable from its first day, its default is a local option, and its binding is
VISIBLE** — an existing way to ask the running system which provider it is currently pointed at, on
the model of `codebugs where` and the MCP startup preflight ("a binding you cannot see is a binding
you cannot debug", CB-11). Not a preference, and not something to be added afterwards: a provider
that ships hardcoded acquires callers before it acquires a switch. **The rule and the gate are two
halves of one thing — the day either table above needs a new row is the day this rule starts
applying**, which is why they are written together rather than left to find each other later.
**This used to name `DECLARED_EXCEPTIONS` alone, and that trigger was broken** (CB-190): a provider
built on a client the enumeration never listed would have needed no row at all, so the rule would
have sat there un-armed while the provider landed. The ratchet is what repairs it, and for a reason
that does not depend on anyone predicting the client — **a provider arrives as a DEPENDENCY
whatever network shape it has**, so it needs a `DECLARED_THIRD_PARTY` row by construction.

**The write validates the vector, on BOTH paths, and the two kinds of check sit in different places
on purpose (CB-174).** `store_embedding` **and** `batch_store_embeddings` — a rule expressed as one
call site is this repository's most-repeated failure. *The vector's own unfitness* (empty,
non-numeric, `NaN`, `inf`) is decidable from the argument, so it runs BEFORE any transaction opens:
a refusal must not take the write lock, which is the reason `store_embedding` already packed its
vector above `db.txn`. *Agreement with what the tracker already holds* needs a READ, so it is a
check-then-act and lives INSIDE the same transaction as the write — outside one, two concurrent
writers of different widths both read an empty table, both pass, and both write, building the exact
mixed state the check exists to prevent. That is CB-24 verbatim, and `busy_timeout` cannot help
because it serializes the writes and never touches the read before them; `db.txn`'s `BEGIN
IMMEDIATE` takes the lock first. **A third check is easy to miss and is not a special case of the
second: the BATCH must be homogeneous with ITSELF**, since in an empty tracker there is nothing to
compare against and one call could otherwise create the mixed state in a single operation.
Placement is pinned STRUCTURALLY, for CB-41's reason — a comparison made before the lock looks
correct until two writers overlap, so behaviour cannot discriminate the defect.

**ONE QUANTITY DECIDES ON BOTH SIDES, AND IT IS BYTES.** The write guard reads
`length(embedding)` and compares byte widths, exactly as `search_similar`'s `WHERE` does; dividing
by four in the guard would make them two rules a rounding apart, because a blob whose length is not
a whole number of components (reachable only by writing the column directly, but reachable) divides
to the same component count as a well-formed neighbour. A component-wise write guard would then
ACCEPT a vector beside a row the byte-wise read guard EXCLUDES — uniform to the writer, mixed to the
reader. `embedding_stats` reports the byte count beside the component count for the same reason: a
report that folded them would say `mixed: False` over a table SQL treats as two populations. This
was found by reading the change end to end as one thing rather than section by section, and it is
the CB-22/CB-52 "two copies of one precedence table" shape in a new place.

**`NaN` was the quieter half and the card did not name it.** Measured: `struct.pack` accepts it, the
row stores, `cosine_similarity` returns `nan`, and `nan >= min_similarity` is `False` — so the row
VANISHES from the results with no error anywhere, and a `NaN` in the QUERY vector removes every row,
making "nothing is similar" indistinguishable from an empty tracker. That is the silent-empty-queue
shape (CB-19/CB-25), which this repository treats as worse than the loud failure the card described.
The query vector is validated for the same reason, which is one step past the letter of CB-174 and
kept deliberately: the write-side fix cannot reach a `NaN` that only ever exists in a caller's query.

**The read-side guard is SQL, and `cosine_similarity`'s `raise` is preserved rather than removed.**
`search_similar` folds `length(embedding) = ?` (the width BOUND, never interpolated) into its
`WHERE`, so a foreign row never reaches the comparison — the form's precedent is
`reconcile.live_source_clause`, where an exclusion is likewise SQL rather than a per-row Python
predicate. The pairwise `raise` is a ratified decision: `zip()` would truncate the dot product while
the norms stayed full, returning a plausible wrong number instead of an error. **The defect was
never that refusal, it was the COMPOSITION** — one foreign row aborted the whole loop and discarded
the rows already scored, in an order nothing controls. Making the `raise` UNREACHABLE from the
search path is the fix; removing it would be a worse change. The premise the SQL rests on —
`length()` on a BLOB counts BYTES — is pinned as a premise test, like the git and argparse
behaviours elsewhere in this tree.

**The cost of that guard is that excluded rows are INVISIBLE, so the visibility channel is
`embedding_stats`, not the search.** `search_similar` returns a LIST and has nowhere to carry a
count of what it dropped, the way `add` carries `stripped_meta_keys`; breaking the response shape
for it would cost more than it buys. `embedding_stats` already returns a dict, so it reports
`dimensions` (which widths are present, and how many rows each) and `mixed`. Both keys are
UNCONDITIONAL, following the `attention`/`stripped_meta_keys` discipline: an empty list means
*looked, nothing stored*, never *no such channel*. `reqs_embedding_stats` takes no input at all and
is therefore not a privacy surface — said explicitly rather than left as an omission a reader has to
interpret.

**AND THAT CHANNEL IS NOT ENOUGH ON A UNIFORM TRACKER, WHICH IS WHERE THE FIX RE-CREATED THE VERY
DEFECT IT REMOVES.** Adversarial review measured it: with every stored vector the same width — the
ordinary case, and the one the write guard now guarantees — a query of a DIFFERENT width used to
raise loudly from `cosine_similarity` and, with the SQL filter in place, returned `[]`. "Nothing is
similar" about a full tracker, and `embedding_stats` says `mixed: False`, i.e. everything is fine.
So `search_similar` refuses instead, on AFFIRMATIVE PROOF only — the result is empty AND the tracker
holds vectors AND none of them is this width. An empty tracker still answers `[]`, because there an
empty answer is true; a mixed tracker where some rows matched never reaches the branch, so CB-174's
degrade-instead-of-fail behaviour is preserved rather than undone; and the branch keys on the WIDTH,
never on the emptiness, so a right-width query whose status filter matched nothing is still an
honest empty page. The general lesson is the one this repository keeps paying for: **a fix aimed at
one silent-empty-queue can open another one, and only an adversary looking at the composition
notices** — every element here was correct, and the elements together answered a lie.

**RESIDUAL, NAMED AND NOT CLOSED: once a tracker holds vectors of one width, there is no sanctioned
way to change embedding model.** No clear-and-re-embed operation exists here, and building one with
no caller asking for it was refused on the direct precedent — CB-44 declined to build the resolver
seam speculatively and CB-45 built it with its first consumer. The refusal message says so itself,
because a gate with no way out is a wall rather than a diagnostic. **Today this locks nobody in**:
measured on 2026-08-25 across every reachable tracker — codebugs 6 requirements, both autosorter
trackers 1401 each — the embedded count is **0**, so CB-174 was a dormant breach rather than live
damage, and the "first vector sets the width" rule had no migration cost at the moment it landed.

**Three residuals found by adversarial review and NAMED rather than closed, because closing each is
a separate negotiated decision.** (1) The network gate matches a CALL SITE by the name being called,
so an indirection that hides the name — `getattr(importlib, "import_module")(...)`, or
`find_spec`/`module_from_spec`/`exec_module` — is not seen; both were reproduced, and closing them
means tracking values rather than names, a much larger check. The prose above is written to the
width the gate actually holds and no wider. (2) A ZERO-LENGTH blob is accepted as an authoritative
width, so a tracker that received `store_embedding(conn, id, [])` from a pre-CB-174 version now
refuses every real vector, with no clear operation to escape — the same residual as the model
switch, reached by a different door, and bounded today by the measured zero population. (3) The
gate reads `src/codebugs/` only, so `tools/` and `tests/` are outside it by design.

**Scope note for anyone extending this: `batch_store_embeddings` is still missing half of the
hardening its twin received (CB-184).** A requirement that does not exist is silently counted as
not-stored there, where `store_embedding` raises `KeyError` (CB-125). CB-174 gave the batch the
`db.txn` it needed for the width check to be an atomic check-then-act, and deliberately left that
counter-versus-`KeyError` contract alone — it is a behaviour change with its own test and its own
CHANGELOG entry.

## Claims module

`claims.py` answers "who currently holds this entity" for findings and requirements, so parallel
agents can refuse to duplicate each other's work. One table, `entity_claims`; mutual exclusion is a
**partial unique index** (`entity_id` WHERE `released_at IS NULL`), so at most one live claim per
entity is a database guarantee rather than a matter of transaction discipline. Release is a soft
delete, so `release_reason` (`explicit` | `terminal:<status>`) is a queryable record.

- **Outcomes, not booleans**: `claim` → `claimed | already_mine | held_by_other | entity_terminal |
  undetermined`; `release` → `released | not_yours | not_claimed | undetermined`. Every response is
  built by the single `_response()` constructor and carries all fifteen `_COMMON_KEYS`.
  `undetermined` means the database was too contended to tell — **re-issue the identical call**; the
  primitive is an idempotent upsert, so a replay converges on `already_mine` and can never
  double-claim.
- **Ownership is the triple** `(holder, holder_kind, holder_repo)`, compared NULL-safely. Both
  claim and release authorize on the full triple: a same-text holder of another kind or in another
  repo is a different claimant.
- **The discriminator is `touch_count`, never a timestamp.** `utc_now()` is whole-second, so a
  retry inside one second is indistinguishable by clock.
- **Two layers.** `_claim_core` / `_release_core` emit statements and never open or commit a
  transaction — that is what the terminal hook calls, since it runs inside `update_finding`'s open
  transaction. `claim` / `release` wrap the core in `db.txn` and classify contention.
  **Ambient-transaction invariant: every caller of the public layer must hold a connection with no
  open transaction.** On a connection with an implicitly-opened transaction the write happens,
  nothing commits, and the call still reports success. It is unreachable today only because
  `server.py`'s `_conn` and every CLI handler open a fresh connection.
- **Projection is declarative**: `EntityKind.busy_status` (`in_progress` for findings, `None` for
  requirements — a requirement's status vocabulary has no in-progress value and its CHECK is not
  rebuilt). A kind declaring it must satisfy P1-P4, documented on `EntityRef.set_status`.
- **Exit codes are the API for shell callers**: `0` proceed, `1` error, `3` held by someone else,
  `4` already resolved, `5` contended (retry). `codebugs claims --format ids` prints bare ids and
  exits 0 on an empty list so a shell loop needs no parsing. **`141` was added package-wide by
  CB-78** and is not a claims outcome — it is documented here only because this is where the
  exit-code list lives; the **CLI** section owns it. It is `128 + SIGPIPE`, meaning *the reader of
  my stdout **or stderr** went away* (the disposition is process-wide, so `codebugs bad-verb 2>&1 |
  head -0` yields it too), and it can come back from any verb. It is deliberately distinguishable from `1` — that
  distinction is the whole reason the alternative "silent exit 0" was rejected, since a
  `codebugs export-csv /dev/stdout | gzip > backup.gz` whose `gzip` dies must never report success
  over a truncated backup. A `| while read` loop that `break`s now kills the producer at 141 rather
  than 1; both are non-zero, so no `set -e` script changes behaviour. Observable only when the
  reader closes without draining (any size) or un-drained output exceeds the 64 KB pipe buffer.
  **`74` was added by CB-136** and is not a claims outcome either — same reason it is recorded here,
  same **CLI** ownership. It is `EX_IOERR` from `sysexits(3)`, meaning *my output could not be
  WRITTEN* on a descriptor that was healthy at the process entry — `/dev/full`, a filesystem that
  filled while the verb ran, a wedged PTY — and it deliberately asserts **nothing** about whether the
  command's effect landed, because the write that failed is usually the line reporting a mutation
  that has already committed. It replaces the two codes that state produced before it (`1`
  unbuffered, with a raw traceback; `120` block-buffered, with "Exception ignored while flushing
  sys.stdout"), the first of which is this package's code for **bad input** printed over a landed
  write — the CB-15/CB-16 lie. `141` is deliberately not reused: there the reader is gone, here it is
  present and the medium is full, and blurring that is what CB-78 refused. When a verb had already
  chosen its own non-zero code, `74` wins, since the caller never received the output that code
  describes. Three limits, each measured rather than assumed, because the first draft of this
  paragraph overclaimed and cross-model review said so. **`EPIPE` is excluded and reports `141`**:
  `cli.run` restores the SIGPIPE *disposition* but cannot clear an inherited signal *mask*, so a
  caller that blocked SIGPIPE gets `EPIPE` back from the write instead of dying by signal, and
  calling that "the medium is full" would undo CB-78 inside CB-136's own fix. **It covers what goes
  through `sys.stdout`** — `print` and the `csv` writer, i.e. every verb's ordinary output — and NOT
  `export-csv <path>`, where `fsio.atomic_write` writes through its own file object and CB-76's arm
  still reports exit 1, `export-csv /dev/stdout` included; that is unchanged behaviour rather than a
  hole this opened, and nothing is committed on that path, so it is not the CB-15/CB-16 lie. **A verb
  that CRASHES** keeps its traceback and its own code, so a still-buffered stdout can reach `120`
  there as before — trading a crash's traceback for a tidy code is the worse of the two.
- **Adoption**: autosorter's `worktree-setup.sh` claims every card in the branch name (and in
  `--items`) **before** `git worktree add`, with an EXIT trap that releases them if setup aborts;
  `worktree-finish.sh` releases whatever the branch still holds. Exactly one of those calls may be
  fatal — the setup gate. Everything else is guarded, so a missing or contended tracker can never
  abort a finish after the merge has landed.
  **This repo's own `tools/worktree-*.sh` now follow the same shape (CB-58)** — see the Workflow
  section for the exit-code handling and the trap. One detail worth carrying to any third adopter,
  because it was only obvious after building it: **the fatal/guarded asymmetry is about WHEN, not
  about importance** — setup may abort because nothing has been created yet and a refusal is free,
  while finish runs after the merge has landed, where a false failure over tracker bookkeeping is
  the worse outcome.
  **Two places codebugs deliberately diverges from `FINAL-DESIGN.md` §6.2–§6.3, both because that
  section was written for autosorter's script and one of its premises does not hold here.** Do not
  "fix" either back without reading this.
  1. **`--allow-duplicate` does NOT clear a `held_by_other` refusal** (design §6.2(a) has it clear
     both `3` and `4`). That flag also clears the pure-git branch guard, and this repo never deletes
     merged branches, so it is needed for *ordinary follow-up work* — one flag for both jobs would
     turn the claim gate off exactly when people are doing normal work. `CODEBUGS_SETUP_NO_CLAIM=1`
     is the typed alternative and it builds with **no** claim rather than stealing one. **Ratified
     by the owner, 2026-08-19**, against the design doc, on this reasoning.
  2. **Finish leaves restore ON** (design §6.3 passes `--no-restore`). The design's own text says
     why the difference is correct: there, `[7b/9] auto-resolve-codebugs.py` has already flipped the
     card to `fixed` from a `Fixes:` trailer, so the release is a no-op and `--no-restore` guards a
     rare case. **This repo has no auto-resolve step** — `worktree-finish.sh` tells the operator to
     close the card by hand — so the card is typically still `in_progress`, and `--no-restore` would
     leave every finished branch's card `in_progress` with no holder: CB-58's own defect,
     reintroduced by CB-58's fix. Restore is a CAS against the projected value, so it still cannot
     resurrect a card someone already closed; the operator-closed case returns `not_claimed` at
     exit 0 and writes nothing.
- Deferred by design, not forgotten: `steal`, claim history queries, audit/divergence tooling,
  retention, `expected_status`/`changed`, and `pull_next` integration.
  See `docs/superpowers/plans/design-council-entity-claims/FINAL-DESIGN.md` §10.

## Milestones module

Releases ("release/1.1") and standing streams ("stream/triage", "stream/maintenance", "stream/security") give parallel-agent work a durable bucket. `milestones.py` owns four tables (`milestones`, `milestone_items`, `milestone_audit`, `agent_capacity`) and 20 MCP tools across three phases:

1. **Foundation** — milestone & item CRUD, audit log, auto-routing every new finding into `stream/triage` (or `stream/security` for `severity=critical && category.startswith("security:")`).
2. **Triage + pull** — `triage_inbox` / `triage_dismiss` / `triage_promote`, plus `pull_next(agent_id, capacity)` which atomically claims the highest-priority eligible item for the calling agent. Concurrency is enforced by `db.txn` (CB-40 — it no longer copies `merge.py`'s raw save/restore pattern, which had the `isolation_level` commit hazard; `merge.py` does not have that pattern any more either). It refuses an ambient transaction, and returns the claimed row from the UPDATE's `RETURNING` rather than re-reading by `item_ref` after the commit (CB-39).
3. **Close gate + branch tracking** — `mark_branch_only(item, branch)` / `mark_integrated(item, commit)` keep the release container honest. `milestone_close` refuses on unfinished, branch-only, or blocker-gated items unless `force=True` is set (with a logged reason). Streams cannot be closed.

`pull_next` eligibility: item is `open`, no active blockers (skipped for `item_kind='external'`), acceptance required for `size='large'`, and large bugs in release milestones must declare `linked_frs` whose ids resolve to rows in `requirements`. Agent capacity is tracked per `(agent_id, size)` and decremented by `release_item`.

For the design and adversarial-review history, see `docs/superpowers/plans/2026-05-11-milestones-streams.md` and the source spec at `../autosorter/.claude/plans/codebugs-milestones-streams-v1.md`.
