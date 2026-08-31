# Inventory — CLAUDE.md lines 1–400 (worktree docs-t126-claude-md-compression)

L1 | RULE | The document title identifies the file as governing the "Codebugs" project.
L3 | RULE | Codebugs is an AI-native code finding & requirements tracker, SQLite-backed, exposed via an MCP server and a CLI.
L5 | RULE | The workflow section's governing invariant is that `main` is never edited directly.
L7 | RULE | Every code edit happens on a short-lived branch, in a worktree, and `tools/` mechanically enforces this.
L8-9 | IDENT | The harness is borrowed from `../autosorter` (2026-08-16), including a scaled-down port of its `tools/worktree-*.sh` harness.
L11-18 | HISTORY-LOADBEARING | The document previously claimed "plain git is enough" with no harness, and that claim was falsified within two hours by the very rule it introduced, which is why the mechanical harness below (CB-50) exists.
L12 | MEASURED | The unenforced rule landed at 13:37 on 2026-08-16 (commit `2957070`), mandating a typed branch and `git merge --no-ff`.
L13-15 | MEASURED | At 15:30 the same day, main was advanced by "merge worktree-cb-45-similarity-seam: Fast-forward" — a branch with no type prefix, integrated with no merge commit, landing on main's own SHA so every further merge would also fast-forward.
L16-17 | RULE | A convention that exists only as a pattern in the log is not a rule, and prose cannot enforce prose.
L18 | IDENT | This incident is tracked as CB-50, and the mechanical harness is its fix.
L20 | RULE | `tools/install-hooks.sh` arms all mechanical enforcement and must be run once per clone.
L22-24 | RULE | The branch name must carry a `fix/`\|`feature/`\|`refactor/`\|`docs/` type prefix, checked by `_guard_branch_type` (refuses exit 7) at setup/finish time.
L24 | RULE | The same branch-type rule is separately enforced by the pre-commit hook at commit time, refusing with exit 1.
L25 | RULE | Nothing but `.claude/plans/*.md` or `.claude/plans/briefs/*.html` may be committed while on `main`; enforced by the pre-commit hook, refusing exit 1.
L26 | RULE | A plan note committed on main must be NAMED in the commit message; enforced by the commit-msg hook, refusing exit 1.
L27 | RULE | A cascade id added to `.claude/plans/CASCADE-IDS.md` on main must equal exactly what `tools/cascade-mint.sh` would have computed (max+1 per family, counting annulled lines and mentions); enforced by the pre-commit hook, refusing exit 1.
L28 | RULE | A merge onto main must come from a typed local branch, or from main's own upstream `main`; enforced by the pre-merge-commit hook on a clean merge and by the pre-commit hook on a conflicted merge, both refusing exit 1.
L29 | RULE | An in-progress cherry-pick/revert marker file no longer exempts a commit from the branch-type/merge-source checks; enforced by the pre-commit hook, refusing exit 1.
L30 | RULE | Integration must never fast-forward; enforced by `--no-ff` plus `git config merge.ff false`, with no refusal exit code (it prevents the state rather than refusing it).
L31 | RULE | Only one integration may run at a time; enforced by an flock on `.worktrees/.integrate.lock`, refusing exit 1.
L32 | RULE | The tested state must still match at the moment it is re-checked inside the lock; enforced by an in-lock SHA re-check, refusing exit 13.
L33 | RULE | The suite must run under the interpreter main actually has; enforced by `_guard_interpreter_matches_main`, refusing exit 14.
L34 | RULE | The clone must actually be armed with hooks before integrating; enforced by `_guard_enforcement_armed`, refusing exit 12.
L35 | RULE | Main's working tree must have `main` itself checked out; enforced by `_guard_workspace_on_main`, refusing exit 8.
L35 | RULE | Main's working tree must be clean; enforced by `_guard_main_clean`, refusing exit 11.
L36 | RULE | The branch being integrated must actually carry a nonempty change; enforced by `_guard_nonempty_diff`, refusing exit 9.
L37 | RULE | No conflict markers may remain in the tree; enforced by `_guard_conflict_markers`, refusing exit 5.
L37 | RULE | No untracked scratch or temp file may sit at the repo root; enforced by `_guard_untracked_scratch_at_root`, refusing exit 4.
L37 | RULE | The branch's base must not be stale; enforced by `_guard_stale_base`, refusing exit 6.
L39-44 | BOUNDARY | `.github/workflows/main-invariants.yml` is deliberately excluded from the "mechanically enforced" table because a GitHub Actions workflow cannot refuse a push — it only reports afterward — so it is an alarm, not a gate.
L39-42 | HISTORY | Listing the CI workflow under "what is now mechanically enforced" with a "refuses with" column was itself judged a category error by round-3 review.
L43-44 | RULE | The actual gate for main-invariants is GitHub branch protection on `origin/main` (see CI limits section below).
L46-48 | HISTORY | The in-lock re-check row previously read "The tested state is the landed state," which overclaimed, since the re-check is only a check-then-act proving the state matched at the moment of the check.
L48-50 | RULE | Two statements after the in-lock re-check, `git merge "${BRANCH}"` resolves both refs again by NAME for itself, and nothing carries a verified SHA into the merge because porcelain git has no `--expect-old-oid`, leaving a window between check and merge.
L51-52 | RULE | The flock serializes finishes only against each other and nothing else — it does not close the window above.
L52-54 | MEASURED | Ordinary sanctioned traffic walks into that window: level-(2) sessions commit plan notes to main continuously, and since 2026-08-22 `tools/cascade-mint.sh` does so automatically while holding a different lock.
L54-56 | RULE | The narrowed re-check row is still a checkable claim: a mismatch under the lock refuses with exit 13 before anything lands, but the row no longer claims to cover the interval after the check.
L46 | IDENT | This narrowing is tracked as CB-121.
L58-60 | RULE | The gap left after the re-check is covered by a post-merge alarm, which — for the same reason as main-invariants.yml — cannot refuse anything because by the time it looks, the merge has already run.
L60-62 | RULE | The post-merge check must run immediately after the integration merge and before `flock -u 9`, because after unlocking, another finish could move main and make the alarm lie about what it is checking.
L62-64 | RULE | `worktree-finish.sh` verifies the just-made merge has `TESTED_MAIN` as its first parent AND `TESTED_HEAD` as its second parent — both parents are checked because checking only one while asserting a two-parent premise is this section's own recurring defect.
L65-69 | RULE | If the check fails, cleanup (worktree removal, claim release) is still allowed to finish, and only then does the script speak, with a loud block and `exit 15`, meaning "the merge already ran and the premise is unconfirmed."
L66-68 | RULE | Exit 15 is deliberately distinct from exit 13 (which means "nothing landed, re-run"); the block explicitly instructs the operator NOT to re-run, because a second finish after a landed merge is worse than the reported defect.
L71-72 | HISTORY | The four load-bearing details that follow are each a cross-model review finding rather than something foreseen in advance.
L74-76 | RULE | The post-merge alarm must first establish that main's tip IS the merge this run made — a two-parent tip alone does not prove that, since an off-harness merge landing right after ours also has two parents with our merge as its first parent.
L77-79 | RULE | `ORIG_HEAD` supplies identity because `git merge` sets it to the HEAD it merged into, which IS the merge's first parent by construction, so the tip is confirmed as ours exactly when it has two parents and its first parent equals `ORIG_HEAD`.
L79-83 | RULE | Any other state reports a single verdict, `tip-not-ours`, which covers a stranger's commit or merge, an "Already up to date" merge (whose `ORIG_HEAD` is set to the current tip, so it cannot masquerade as a match), an octopus merge, and a root commit.
L83 | IDENT | Both underlying git behaviours referenced by the identity check are pinned as premise tests.
L84-87 | BOUNDARY | `tip-not-ours` is usually benign (e.g. a plan note landing right after a correct merge); the block therefore tells the operator to read the log, and reserves "fix it forward on a new branch" advice for the two genuinely mismatched cases only.
L88-92 | RULE | The block is delivered from an EXIT trap armed the instant the merge returns, not a trailing `if`, because under `set -euo pipefail` any failure in the cleanup code (its own final `git log … | sed`, or a later inserted statement) would otherwise kill the script between detecting the condition and reporting it.
L92-93 | RULE | This restates CB-41's rule: make the bad state unrepresentable rather than re-establish discipline at every insertion point.
L93-94 | RULE | The initial verdict defaults to the pessimistic `unreadable`, so a signal arriving before the verdict is computed still reports "could not look."
L94-96 | BOUNDARY | Residual, stated rather than hidden: the interval between `git merge` returning and the trap executing is two assignments wide, and nothing in the script can close it.
L97-99 | RULE | The reads use `--no-replace-objects`, because replace refs and `info/grafts` would otherwise make `^@` answer with parents not in the commit's own header, breaking the "an object's parents are immutable" argument.
L99-102 | BOUNDARY | Stdout-only reading is the one place fail-closed is deliberately NOT applied: folding stderr in would make any git `warning:` unparseable and fire the alarm on an honest finish, and an alarm that cries wolf is one nobody reads.
L102 | RULE | The return code is still used to separate an error from an empty answer.
L103-104 | RULE | Every answer is also shape-checked, because `git rev-parse` echoes an unrecognised argument back at you and exits 0.
L106-107 | BOUNDARY | Rejected: pinning the merge to name `TESTED_HEAD` explicitly — it closes only the branch half and pays a false refusal, since pre-merge-commit refuses a head with no ref.
L107-109 | BOUNDARY | Rejected: implementing a real compare-and-swap via `git update-ref` over `commit-tree`, because this repo's own CI alarm treats that as a hook-bypassing shape.
L111-113 | RULE | `merge.ff=false` is the one protection no hook could ever replace, because git fires no hook at all on a fast-forward — no commit is created, so nothing can catch it after the fact.
L112-114 | MEASURED | Verified by replaying the incident in a throwaway repo: default config gives "Fast-forward" and zero merge commits, while `merge.ff=false` produces a merge commit.
L114-117 | BOUNDARY | Two precise limits: `merge.ff=false` does nothing when the branch is already an ancestor of main (git reports "Already up to date," harmless), and it is only local configuration, so `git config merge.ff true` silently disables it.
L119-122 | HISTORY-LOADBEARING | CB-57's card prescribed validating "the branch behind `MERGE_HEAD`" in a pre-merge-commit hook, but on git 2.53 a clean merge never writes `MERGE_HEAD` at all — it is resolved in memory, with `AUTO_MERGE`, `ORIG_HEAD`, and `COMMIT_EDITMSG` written instead, and `git rev-parse MERGE_HEAD` fails outright.
L123-124 | RULE | A hook keyed on `MERGE_HEAD` therefore exits 0 on every clean merge — a gate that cannot fire, worse than no gate, because the enforcement table would then claim a rule nothing enforces.
L124-126 | RULE | What git does provide is `GITHEAD_<sha>=<what the caller named>`, set per merge head — git's own record, and what the merge strategies themselves read.
L126-128 | RULE | The commit message is deliberately not used, because parsing "Merge branch 'x'" is the name-matching heuristic CB-57 refused, and it would be blind anyway since `worktree-finish.sh` passes its own `-m`.
L128-130 | IDENT | `test_premise_merge_head_is_absent_on_a_clean_merge` and `…::test_premise_githead_env_names_the_merged_ref` pin both premises so a git upgrade turns the suite red instead of silently disarming the hook.
L132-135 | MEASURED | Measured on git 2.53 against a real remote: `git merge origin/main` gives `GITHEAD_<sha>=origin/main`, but `git pull` and `git merge FETCH_HEAD` give the raw OID rather than a name.
L135-138 | HISTORY-LOADBEARING | The first draft therefore refused every pull, contradicting its own comment and this section's stated promise that pulls were fine; a cross-model reviewer's claim that `GITHEAD_` carries a "branch 'main' of <url>" description in that case was checked and found false — independently reproduced as the raw OID.
L138-139 | RULE | `GITHEAD_` is used only to LEARN WHICH COMMITS are being merged; the acceptance decision is made from the refs that point at each of them, not from `GITHEAD_` itself.
L141-143 | RULE | The sanctioned-type rule governs LOCAL branches only; remote-tracking refs are upstream's namespace (unnamed by this repo), so exactly one — main's own upstream — is consulted, and only to recognise a pull.
L145-146 | RULE | Candidates for judgment are every ref pointing at the merge head, always; there is no "judge the named ref instead" branch.
L147-151 | RULE | Every local branch pointing at the head must qualify as typed; allowing "any local ref qualifies" reproduced a three-command bypass: `git merge untyped` (refused) → `git branch fix/tmp untyped` → `git commit`, because git leaves the merge in progress after refusal and routes the operator to `pre-commit`, where one typed alias at the same commit laundered the whole thing.
L152-156 | RULE | A remote ref other than main's upstream `main` neither qualifies nor disqualifies a merge; requiring ALL refs to qualify refused a real `git pull` whenever upstream had another branch cut at that commit (e.g. `origin/release-1.0`), and `refs/remotes/<r>/HEAD` (the default-branch alias) disqualified the very pull the fallback existed for.
L155-156 | HISTORY | Both of those cases were reproduced; the second was caught by this repo's own test minutes after the first fix — two bugs in the same three lines.
L157-158 | RULE | Upstream `main` wins over a non-qualifying local branch, so a stray local bookmark left at the commit being pulled cannot refuse the pull.
L159-162 | RULE | The trusted ref must match EXACTLY `refs/remotes/<branch.main.remote|origin>/main`, with nothing stripped.
L159-162 | HISTORY-LOADBEARING | A blind `${rest#*/}` once collapsed `refs/remotes/junk/main` to the accepted literal `main`; an intermediate fix trusting any configured remote's `main` still left `git remote add junk <anything>` plus a fetch as a two-command bypass.
L164-168 | RULE | A merge head with NO ref pointing at it at all is refused outright, catching a bare SHA or a tag.
L164-168 | BOUNDARY | That same refusal is a real cost: it also refuses four legitimate-if-rare flows — a one-shot `git pull --no-rebase <URL> main`, `git merge FETCH_HEAD` with no tracking ref, a `branch.main.remote` set to a URL rather than a remote name, and `git merge <tag>` where no branch points at the tagged commit.
L168-171 | BOUNDARY | Ordinary `git pull` against a configured remote is unaffected; the no-ref refusal is documented rather than fixed, because closing it means trusting `.git/FETCH_HEAD`'s free-text description, and a new trust path with no review round left to attack it is a worse trade than a rare `--no-verify`.
L171 | RULE | Use `git merge --no-verify` for a one-shot pull from a URL.
L173-178 | BOUNDARY | Main's own upstream `main` (via `branch.main.remote`, defaulting to `origin`) is trusted, and nothing local can prove what it contains or how it got there — `git update-ref refs/remotes/origin/main <any-sha>`, a mistyped fetch refspec, a rewritten `remote.origin.fetch` (which re-arms on every ordinary `git fetch`), or an upstream `main` that simply holds untyped work, all land content here.
L177 | MEASURED | This limit was reproduced.
L178-179 | WHY-NARROW | There is no local discriminator for this, and refusing remote refs instead would break `git pull`, the worse failure.
L179-180 | RULE | Same shape as the `--separate-git-dir` misbinding elsewhere in this document: when a rule cannot be decided from local evidence, supply external metadata rather than deepening the guess.
L180-186 | BOUNDARY | The external metadata is CB-59's server-side protection, but only the enabled half: force-push and deletion of `origin/main` are refused, so upstream history cannot be rewritten under you — but an upstream `main` that simply holds untyped work is untouched, because require-PR is deliberately off, and that half of the limit stays open.
L185-186 | IDENT | `TestKnownLimits` pins that the untyped-upstream bypass still reproduces, so the day it stops being true someone re-reads this rather than trusting a stale claim.
L188-190 | HISTORY | The trust scope was narrowed twice under review: first it trusted ANY `<remote>/main`, then any CONFIGURED remote's `main` (still a two-command bypass via `git remote add junk` plus a fetch); only main's declared upstream counts now.
L192-197 | HISTORY-LOADBEARING | The conflicted-merge gate is a `while read` loop over `MERGE_HEAD`, and two file states made it run zero times, leave the refusal flag at 0, and fall through to the merge-in-progress exemption: an EMPTY `MERGE_HEAD` (reachable from an interrupted git) let arbitrary staged content land on main with no merge at all, and a `MERGE_HEAD` with NO TRAILING NEWLINE (since `read` returns non-zero on an unterminated last line) landed a real two-parent merge of an untyped branch.
L196-198 | RULE | Neither of those states typed `--no-verify`; both were reproduced.
L198-201 | RULE | The loop now uses `|| [[ -n "$_sha" ]]`, counts what it saw, and refuses when it saw nothing; pre-commit was the one hook among the three left failing open on this shape, while the CI job and pre-merge-commit hook were already hardened against it.
L203-207 | HISTORY-LOADBEARING | `_guard_enforcement_armed` used to resolve `--git-common-dir`/hooks, which does not follow a `core.hooksPath` redirect, so `git config core.hooksPath <empty-dir>` left the guard reporting armed (0) while nothing was installed, and a commit of arbitrary content on main then succeeded.
L207-208 | RULE | Both the guard and `install-hooks.sh` now use `git rev-parse --git-path hooks`, which does follow the redirect (verified both ways).
L208-211 | RULE | A RELATIVE `core.hooksPath` value is refused outright, because git resolves a relative value against the top of EACH working tree, so `core.hooksPath=.githooks` names a different directory in the primary checkout than in every linked worktree.
L211-213 | MEASURED | Round 3 reproduced this: armed in the primary checkout, with main checked out in a linked worktree lacking `.githooks`, the guard returned 0 and a source commit onto main from that worktree succeeded.
L213 | RULE | "This clone is armed" is not a statement the guard can make about a per-worktree path, so it declines to make it in that case.
L214-216 | RULE | The `core.hooksPath` value is read with `--type=path` so git performs its own `~` expansion first; reading it raw once classed `~/hooks` as relative and refused a genuinely armed clone, even though the same function correctly resolved the identical setting through `--git-path` two lines earlier.
L216-218 | BOUNDARY | Known residual: with `extensions.worktreeConfig` and an ABSOLUTE per-worktree value, the asymmetry returns, bounded because the integration merge itself runs in the primary checkout, where the gate does fire.
L220-221 | HISTORY-LOADBEARING | The bootstrap gate's "monotonic" condition first gated on the hook file's mere existence, so a single `rm` was a permanent silent disarm (round 2).
L221-223 | HISTORY-LOADBEARING | It then read the literal ref `main`, so any clone with no LOCAL `main` (e.g. `git clone --single-branch --branch fix/…`, even with `origin/main` present) collapsed straight back to disarmed (round 3).
L223-226 | RULE | It now reads `--all` and DISTINGUISHES AN ERROR FROM AN EMPTY RESULT, because `2>/dev/null || true` had made those identical.
L224-226 | MEASURED | Round 4 reproduced the full disarm again via a `--filter=tree:0` clone whose promisor remote had gone away, where `git log --all -- <path>` exits 128.
L226-228 | RULE | The gate now fails closed on the error path — the third distinct door onto the same disarm defect.
L230-233 | HISTORY-LOADBEARING | A non-ASCII plan note could not land on main: `git diff --cached --name-only` C-quotes such a path by default, the allowlist regex misses it, and the commit was refused — the mirror image of a bug where `_guard_conflict_markers` had silently ACCEPTED a conflict marker under the same default.
L233 | RULE | Both readers now pass `-c core.quotePath=false`.
L234-236 | RULE | A third reader has since joined them: the commit-msg gate derives a BASENAME from the same staged set, so a C-quoted path there yields a basename no human could type — a permanent false refusal of every non-ASCII plan note, not a one-off.
L236-237 | HISTORY | The pinning test no longer names "both readers" in its assertion, because a count embedded in a name is a count that goes stale.
L239-241 | RULE | A plan note landing on main must be NAMED in the commit message, and the mechanism is a `commit-msg` hook — NOT the pre-commit hook originally specified for this rule.
L241-243 | RULE | The rule mechanises that parallel sessions add files to main BY NAME, never by directory: `.claude/plans/` is the one place they may all write, and `git add .claude/plans/` once swept an untracked note belonging to another direction into a commit describing unrelated work — the bytes survived but the provenance did not.
L244-245 | HISTORY-LOADBEARING | The naming convention was adopted and then broken again — broken four times after adoption — which is this section's own opening lesson and is why the rule had to stop being prose.
L245-248 | RULE | Naming is the discriminator because git records nothing about HOW a path was staged (the index cannot say whether `git add` was given a file or a directory); the author is the only thing that separates the two cases, since one cannot name a file one did not know was there.
L250-253 | MEASURED | On git 2.53, at pre-commit time the message being written does not exist anywhere: `$GIT_DIR/COMMIT_EDITMSG` holds the PREVIOUS commit's message, and on a clone's first commit it does not exist at all.
L253-256 | RULE | A pre-commit naming check is therefore not a gate that fails open — it is a gate wired to someone else's input: it passes a sweeping commit whose predecessor happened to name the file, and refuses a correct one whose predecessor did not, which is worse than absent because it LOOKS like enforcement.
L256-258 | RULE | `commit-msg` receives the final message as `$1`, only after `-m`, `-F`, and the editor have all had their say.
L257-258 | IDENT | `test_premise_pre_commit_cannot_see_the_message` pins this premise so a git behaviour change turns the suite red instead of silently re-justifying a move back to pre-commit.
L260-263 | HISTORY-LOADBEARING | Two auto-generated sources inside the message file would each independently have made this a gate that cannot fire, and neither was foreseen: git's default template lists staged paths as comment lines (e.g. `#\tnew file: .claude/plans/foo.md`), so every editor-based commit would have passed vacuously.
L263-265 | RULE | `git commit -v` appends the whole diff below the scissors line, where every hunk header names its own file, and `git stripspace --strip-comments` does NOT remove that, because a diff is not a comment.
L265-266 | RULE | The message is therefore truncated at the scissors line FIRST, then comment-stripped — never the reverse order.
L266-268 | RULE | The scissors detector matches `>8` and `---` on one line rather than git's exact scissors string, because the comment character is configurable and anchoring on `#` would let a repo with `core.commentChar=;` keep its diff in the message.
L268-268 | BOUNDARY | Over-truncating costs a loud refusal; under-truncating costs the gate entirely — both are named as the trade-off.
L268-270 | RULE | Comment stripping is delegated to `git stripspace`, which reads the same `core.commentChar` git itself will use, so the two cannot disagree.
L272-274 | HISTORY-LOADBEARING | A substring test passes on an ordinary case but fails on a contrived one: "plan.md" is a substring of "my-plan.md", so a sweeping commit naming its own note would launder a stranger's note sitting beside it — and the swept file is by construction the one nobody wrote down.
L275-277 | RULE | A regex `\b` word boundary does not fix this either, because `-` and `.` are non-word characters, so `\bplan\.md\b` still matches INSIDE "my-plan.md".
L276-277 | RULE | The match must instead be flanked by a real boundary: the string edge, or an ASCII byte that cannot occur in the name.
L277-278 | RULE | Every non-ASCII byte counts as part of a name, so an ambiguous neighbour causes a refusal rather than a false match.
L278-280 | BOUNDARY | Stated cost: a filename hugged by typographic quotes or dashes (e.g. «plan.md») is not recognised and needs a plain space or ASCII quote around it instead.
L280-282 | BOUNDARY | `LC_ALL=C` pins byte semantics so the verdict cannot depend on the committer's locale; honest scope stated explicitly: this is determinism insurance and no test currently discriminates it, since under a UTF-8 locale codepoint-wise classification happens to agree on every case tested here.
L284-288 | HISTORY-LOADBEARING | Cross-model review reproduced a further hole: with `a b.md` and `b.md` both staged and only `a b.md` named, the occurrence of `b.md` inside it is flanked by a space and the token end (two boundaries), so the stranger's note `b.md` landed unnamed.
L288 | MEASURED | Measured: rc=0, both files committed, in the `a b.md`/`b.md` scenario.
L288-289 | RULE | A staged basename containing a space or any ASCII punctuation outside `[A-Za-z0-9._-]` is REFUSED OUTRIGHT rather than judged by a rule that cannot reliably see it.
L290-292 | RULE | This closes the class BY CONSTRUCTION: if every staged basename is made only of name bytes, an occurrence of one strictly inside a longer one always has a name byte on at least one side, so it can never be flanked by two boundaries.
L292-294 | MEASURED | Cost measured before acceptance: 0 of this repo's 94 plan notes carry a disqualifying character; the existing convention is already ASCII slugs, and non-ASCII names are untouched because a non-ASCII byte itself counts as a name byte.
L294-296 | RULE | General shape restated here: a check that validates individual elements cannot validate their composition — here the composition is the matcher plus the set of names it is asked to match.
L298-299 | RULE | Scope of the naming rule is only `main`, and only `.claude/plans/*.md` or `.claude/plans/briefs/*.html`.
L299-302 | HISTORY | The `briefs/*.html` half was widened by CB-266 to mirror pre-commit-hook.sh's own widening, on the reasoning that `git add .claude/plans/` recursively sweeps `briefs/` too, so once a brief can land at all the hook's founding reason (an untracked stranger's file losing provenance) reaches it as well.
L302-304 | WHY-NARROW | On a branch there are no foreign untracked notes to sweep, so applying this rule there would be pure friction on every `wip` commit; everything else on main is pre-commit's job to refuse, and duplicating that judgement in two hooks would give one state two refusals that could drift apart.
L305-306 | RULE | Deletions are in scope of the naming rule, because `git add <dir>` stages a removal too, and deleting a stranger's note damages the same provenance the rule protects.
L307-310 | RULE | A merge is exempt from the naming rule, and its discriminator differs from pre-merge-commit's: a clean merge writes no `MERGE_HEAD` only at pre-merge-commit time (which runs earlier, in memory) — by commit-msg time git HAS written `MERGE_HEAD`, for both clean and conflicted merges alike.
L310-311 | MEASURED | Measured and pinned: if a future git version stops writing `MERGE_HEAD` by commit-msg time, every integration would then be refused.
L311-313 | RULE | `MERGE_HEAD` is read fail-closed with a count for this exemption too, exactly like pre-commit's arm: an empty `MERGE_HEAD` must not read as an exempt state.
L313-316 | BOUNDARY | Cost of the merge exemption, stated plainly: a deliberate operator can put the repo into a merge state (`git merge --no-commit`, or any conflicted merge), stage an unnamed note, and commit — the naming rule is skipped in that path.
L316-318 | RULE | This is not a new hole; pre-commit's own merge exemption already waves the whole staged set through on that path — the same "evil merge" blind spot the CI-limits list records for `main-invariants.yml`.
L318-319 | RULE | The naming gate is an accident-stopper, and entering a merge state is not something one does by accident.
L320-326 | HISTORY-LOADBEARING | `_guard_enforcement_armed` did not originally check the commit-msg hook, and the earlier paragraph saying so was true at the time: the guard reads `REPO_ROOT/tools/<hook>` from the PRIMARY checkout and gates on that path having history, so adding the check in the same change that introduced the hook's source would have made that very change unlandable by the harness it extends — the bootstrap wall, for the third time — and `install-hooks.sh` could not pre-arm it either because it symlinks into main's `tools/`, where the file did not yet exist.
L325-327 | RULE | The commit-msg hook therefore landed first, armed by the installer alone, with the guard's check following only once `tools/commit-msg-hook.sh` had history on main.
L327-330 | RULE | The bootstrap condition is the SAME monotonic one pre-merge-commit uses, extracted into `_hook_source_known` and called once per gated hook rather than copy-pasted, because a four-review-round condition duplicated in two places is one edit away from disagreeing with itself.
L329-330 | IDENT | `test_bootstrap_condition_is_one_function_called_per_gated_hook` counts the call sites of `_hook_source_known`.
L330-331 | RULE | A clone armed before T-23 is refused at its next finish until `tools/install-hooks.sh` is re-run, correctly, since such a clone really is missing a third of its enforcement.
L332-335 | BOUNDARY | What stays open even after this fix: the commit-msg gate is invisible to the CI alarm (which reads paths, not messages), and to `--amend` (an amend touching only the message stages nothing against HEAD, so a note already landed under a naming message can have that message rewritten afterward) — both are authored acts rather than accidents, which is what this hook targets.
L337-339 | RULE | Two of the three hooks — pre-commit and pre-merge-commit — share a predicate in disjoint halves that must not disagree; the third, commit-msg, shares nothing with them since it reads the message while the other two read refs.
L339-341 | RULE | A CONFLICTED merge never reaches pre-merge-commit, and neither does a merge this hook has already refused — both such cases are finished with `git commit`, which fires pre-commit instead.
L341-343 | RULE | The shared predicate is therefore duplicated BYTE-IDENTICALLY into `pre-commit-hook.sh` between `# ---8<--- SHARED MERGE-GATE PREDICATE` markers, and a test compares the two blocks verbatim rather than grepping for a substring.
L343-345 | HISTORY-LOADBEARING | An earlier substring-based test was shown insufficient by rewriting the merge hook as a prefix test while leaving the regex assignment textually in place, which kept the (defective) test green.
L345-346 | RULE | `worktree-finish.sh` no longer passes `--no-verify` to its own merge, because leaving it would have made the harness itself the single caller exempt from the merge gate.
L348-350 | BOUNDARY | The local half of enforcement is CLIENT-SIDE and PER-CLONE: hooks and git config cannot be committed, so a fresh clone has none of it until `tools/install-hooks.sh` is run.
L350-351 | RULE | This is why `_guard_enforcement_armed` refuses to integrate from an unarmed clone — the one moment of being unarmed is the one moment that can cost anything.
L351-353 | RULE | The armed-check covers all three hooks: pre-commit unconditionally, and pre-merge-commit plus commit-msg once their source is KNOWN (it has history, or the file is present, or the history probe itself failed — fail closed in that case).
L353-354 | RULE | A clone armed before CB-57 or before T-23 is refused until `install-hooks.sh` is re-run.
L354-357 | BOUNDARY | Even in an armed clone, all of the following move or publish `main` without passing any hook: `git rebase`, `git am`, `git reset --hard`, `git push`, `core.hooksPath`, and `git subtree add` (which commits via `commit-tree` plumbing).
L355-356 | HISTORY | `git subtree add` was added to this "does not pass any hook" list because round-4 review landed content on main with it, and the list had not originally mentioned it.
L357-359 | BOUNDARY | A CLEAN `git cherry-pick` or `git revert` also bypasses all hooks entirely, because git's sequencer commits directly; only the CONFLICTED form (finished with `git commit`) is gated.
L358-359 | HISTORY | An earlier version of this "does not do" list was half-wrong about the clean/conflicted split for cherry-pick and revert.
L361-364 | HISTORY-LOADBEARING | An even earlier draft of this same paragraph claimed a clean cherry-pick or revert DOES run `commit-msg` (so the naming rule would fire); measured on git 2.53, it does NOT — the sequencer commits directly and reaches NEITHER hook.
L364-365 | RULE | A clean `git cherry-pick` or `git revert` lands an unnamed plan note at exit 0; only the CONFLICTED form, finished with `git commit`, is gated by commit-msg.
L365-368 | HISTORY-LOADBEARING | The incorrect claim was written into the very paragraph that exists to list what the harness does NOT do, and it survived a green suite because nothing pinned it — "a gate described better than it behaves," in the section whose subject is exactly that failure mode.
L368-370 | IDENT | `tests/test_worktree_harness.py::TestGitSequencerPremises` now pins both directions of the cherry-pick/revert behaviour, so a future git change turns the suite red instead of silently making the false claim true.
L370-371 | BOUNDARY | A typed branch committed in the PRIMARY checkout also satisfies pre-commit while ignoring the worktree-isolation rule entirely — a further gap the harness does not close.
L372-373 | RULE | Most of the hook-bypass gaps above are what the CI job is for: it flattens a non-merge commit onto main's first-parent line, which is exactly what `main-invariants.yml` asserts against.
L375-378 | HISTORY-LOADBEARING | `install-hooks.sh` was found to arm the pre-commit hook, print its tick, then exit 1 at the merge-hook step and leave `merge.ff` UNSET whenever `tools/pre-merge-commit-hook.sh` was missing (an older main, a checkout of an old commit, or the CB-57 bootstrap window itself) — silently skipping the one mechanism no hook can replace.
L375-378 | RULE | `install-hooks.sh` now sets `merge.ff=false` before anything arming-related can abort, closing that gap.
L379 | MEASURED | Reproduced in review, verified fixed by running it.
L379-382 | BOUNDARY | Precise claim, corrected under review: four commands still precede the `merge.ff=false` step (sourcing the guards, resolving the repo root, resolving the hooks dir, `mkdir -p`), each fatal under `set -e`, so an earlier draft's claim "a step that cannot fail goes first" is not literally true.
L384-386 | RULE | The CI job's own limits section exists because "a gate described better than it behaves" is the exact failure this whole section records; every limit listed came out of adversarial review, and the first draft of this paragraph overclaimed.
L388-391 | RULE | The CI first-parent check is scoped to a pinned BASELINE SHA, since main's history predates the rule.
L388-389 | MEASURED | Measured: 110 of the 132 first-parent non-merge commits before that baseline would fail the assertion.
L390-391 | RULE | Moving the baseline forward is how a violation would be laundered, so doing so must be a deliberate, reviewable edit; a test asserts the baseline SHA is a real commit in this repository.
L392-394 | HISTORY-LOADBEARING | An earlier draft asserted that `amend`/`rebase`/`reset` "necessarily" leave a non-merge commit on the first-parent line; they do NOT.
L394-396 | RULE | `git commit --amend` on a merge commit stays a merge; `git rebase --rebase-merges` recreates merges; a force-push to a fabricated merge-only history passes the check; `--no-merges` excludes all such cases by construction, making them all invisible to the CI job.
L396-397 | RULE | DAG inspection cannot prove HOW a merge commit reached the ref — only a protected ref can prove that.
L397-398 | RULE | An "evil merge" (content present in neither parent) is likewise invisible to the CI job, because `git show --name-only` on a merge commit prints nothing.
L399-400 | RULE | The CI job uses `--no-renames`, and that is not cosmetic: with rename detection on, `--name-only` prints only the destination path of a renamed file (sentence continues beyond line 400; not fully captured in this range).

## VERBATIM-CRITICAL

- `tools/install-hooks.sh` — L20, L207, L330, L350, L353, L375
- `_guard_branch_type` (exit 7) — L24
- pre-commit hook (exit 1) — L24-29
- `.claude/plans/*.md` / `.claude/plans/briefs/*.html` — L25, L298-299
- commit-msg hook (exit 1) — L26, L239
- `.claude/plans/CASCADE-IDS.md` — L27
- `tools/cascade-mint.sh` — L27, L54
- pre-merge-commit hook (exit 1) — L28
- `--no-ff` / `git config merge.ff false` — L30, L111, L117, L375
- `.worktrees/.integrate.lock` (flock, exit 1) — L31
- exit 13 (in-lock SHA re-check) — L32, L56, L67
- `_guard_interpreter_matches_main` (exit 14) — L33
- `_guard_enforcement_armed` (exit 12) — L34, L203, L320, L350
- `_guard_workspace_on_main` (exit 8) — L35
- `_guard_main_clean` (exit 11) — L35
- `_guard_nonempty_diff` (exit 9) — L36
- `_guard_conflict_markers` (exit 5) — L37, L230
- `_guard_untracked_scratch_at_root` (exit 4) — L37
- `_guard_stale_base` (exit 6) — L37
- `.github/workflows/main-invariants.yml` — L39, L332, L372
- CB-121 — L46
- `TESTED_MAIN` / `TESTED_HEAD` — L62-63, L106
- exit 15 — L66
- `ORIG_HEAD` — L77, L81
- `tip-not-ours` — L79, L84
- CB-41 — L92
- `unreadable` (initial verdict) — L93
- `--no-replace-objects` — L97
- CB-50 — L18
- commit `2957070` — L12
- "merge worktree-cb-45-similarity-seam: Fast-forward" — L14
- `merge.ff=false` — L111, L117
- CB-57 — L119, L353
- `MERGE_HEAD` — L120-130, L192-201, L307-313
- `AUTO_MERGE` / `ORIG_HEAD` / `COMMIT_EDITMSG` — L122
- `GITHEAD_<sha>=<what the caller named>` — L125, L132
- `test_premise_merge_head_is_absent_on_a_clean_merge` — L128
- `test_premise_githead_env_names_the_merged_ref` — L129
- `refs/remotes/<branch.main.remote|origin>/main` — L159
- `refs/remotes/<r>/HEAD` — L154
- `git merge --no-verify` — L171
- `TestKnownLimits` — L185
- `LC_ALL=C` — L280
- `[A-Za-z0-9._-]` — L289
- `# ---8<--- SHARED MERGE-GATE PREDICATE` — L342
- `core.hooksPath` — L203, L210
- `git rev-parse --git-path hooks` — L207-208
- `--type=path` — L214
- `extensions.worktreeConfig` — L217
- `git log --all -- <path>` exits 128 — L226
- `-c core.quotePath=false` — L233
- `test_premise_pre_commit_cannot_see_the_message` — L257
- `git stripspace --strip-comments` — L264
- `core.commentChar` — L267, L269
- scissors markers `>8` / `---` — L266
- T-23 — L320, L330
- `_hook_source_known` — L327
- `test_bootstrap_condition_is_one_function_called_per_gated_hook` — L329
- `git subtree add` — L355
- `tests/test_worktree_harness.py::TestGitSequencerPremises` — L368
- `--no-renames` — L399
- CB-266 — L299
- "0 of this repo's 94 plan notes" — L293
- "110 of the 132 first-parent non-merge commits" — L388-389
