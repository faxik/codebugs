#!/usr/bin/env bash
# codebugs pre-commit hook — installed as .git/hooks/pre-commit by
# tools/install-hooks.sh (symlink, so edits here take effect immediately).
#
# This is the git-level half of "main is never edited directly". It is a git
# hook rather than a Claude Code hook on purpose: it binds the user, every
# agent, and every subprocess identically, and it cannot be forgotten by a
# session that never read CLAUDE.md.
#
# It enforces exactly two things, both already written in CLAUDE.md:
#
#   1. On main, the ONLY thing that may be committed is a .claude/plans/*.md
#      note. Everything else belongs on a branch, in a worktree.
#   2. On any other branch, the branch must carry a sanctioned type.
#
# It does NOT run tests or lint. tools/worktree-finish.sh is the quality gate
# and runs them in the worktree against the post-forward-merge tree; duplicating
# them here would add seconds to every commit to re-check a state that is not
# the one being landed.
#
# git does not run pre-commit for a merge it completes itself (it runs
# pre-merge-commit, which tools/pre-merge-commit-hook.sh now provides). This
# hook therefore owns the OTHER half: a CONFLICTED merge, which git stops and
# the operator finishes with `git commit`.
#
# Escape hatch: `git commit --no-verify`. Deliberately left open — this hook
# exists to stop the accidental case, and an operator typing --no-verify has
# stated an intent. The record of that intent is the flag itself.

set -euo pipefail

_BRANCH_TYPES=(fix feature refactor docs)

branch=$(git symbolic-ref --short -q HEAD || echo "")

# A detached HEAD is a rebase, a bisect, or a git-split2 intermediate. Nothing
# to say here; the finish script's _guard_finishable_branch catches the case
# that actually matters (shipping from one).
[[ -z "${branch}" ]] && exit 0

_IFS_SAVE="$IFS"; IFS='|'; _types="${_BRANCH_TYPES[*]}"; IFS="$_IFS_SAVE"
# The PREDICATE must match tools/_guards.sh:_guard_branch_type and
# tools/pre-merge-commit-hook.sh exactly, not merely the list of types. A prefix
# test (`${branch} == ${pfx}/*`) accepts `fix/a/b`, which the finish guard's
# full-shape regex then REFUSES — so a session could commit for hours and be
# turned away at the last step, which is the worst possible moment to learn the
# name is wrong (cross-model review). tests/test_worktree_harness.py drives all
# three through the same case table.
_type_re="^(${_types})/[A-Za-z0-9._-]+$"

# ---8<--- SHARED MERGE-GATE PREDICATE — byte-identical in pre-commit-hook.sh and
# pre-merge-commit-hook.sh. Do not edit one copy.
# tests/test_worktree_harness.py compares the two blocks verbatim; they are
# duplicated rather than sourced because each hook runs from .git/hooks and must
# not depend on tools/ being present in the checked-out tree.
#
# Decides whether one merge head may land on main. ONE input, the merge head —
# and that is a deliberate narrowing, not a simplification for its own sake.
#
# IT USED TO TAKE `named` TOO (what the caller typed, from GITHEAD_) and judge
# THAT ref alone when it resolved. Byte-identical code then still produced two
# DIFFERENT rules, because the two callers pass different arguments:
# pre-merge-commit knows the typed name, pre-commit (a separate `git commit`
# process completing a conflicted merge) does not. Review reproduced the
# divergence — `git branch fix/tmp <untyped-sha>; git merge fix/tmp --no-ff`
# landed on the clean path while the identical state was refused on the
# conflicted path — so "the predicate is byte-identical" was NOT the same claim
# as "the two hooks agree", and the byte-identity test structurally could not see
# it. A named ref is by definition a ref AT the merge head, so collecting refs
# at the head loses nothing and makes the callers pass identical information.
#
# THE RULE: the sanctioned-type rule governs LOCAL branches. Remote-tracking
# refs are UPSTREAM's namespace, which this repo does not name, so exactly one of
# them is consulted — main's own upstream — and only to recognise a pull.
#
# Why each clause, all of it earned in review:
#   * EVERY local branch at the head must qualify, not just one. With "any", a
#     typed branch created at the same commit launders an untyped merge. Git does
#     not abort after a refusal — it leaves the merge in progress and says "use
#     `git commit` to complete the merge", routing the operator into pre-commit.
#   * A NON-upstream-main remote ref neither qualifies nor disqualifies.
#     Requiring ALL refs to qualify refused a legitimate `git pull` whenever
#     upstream had another branch cut at that commit (`origin/release-1.0`), and
#     `refs/remotes/<r>/HEAD` — the default-branch alias — disqualified the very
#     pull the fallback existed to allow.
#   * Upstream main WINS over a non-qualifying local branch, so a stray local
#     bookmark at the commit being pulled cannot refuse the pull.
#   * ONLY `refs/remotes/<main's upstream>/main` counts, resolved from
#     `branch.main.remote` and defaulting to `origin`. Trusting *any* configured
#     remote's `main` meant `git remote add junk <anything>` plus a fetch was a
#     two-command bypass; an earlier version trusting any `<r>/main` at all was
#     worse still.
#
# KNOWN LIMIT, stated rather than papered over: upstream's `main` is TRUSTED, and
# nothing local can prove what it contains or how it got there. `git update-ref
# refs/remotes/origin/main <any-sha>`, a mistyped fetch refspec, a rewritten
# `remote.origin.fetch` (which then re-arms on every ordinary `git fetch`), or
# simply an upstream whose main holds untyped work — all land content here. There
# is no local discriminator, and refusing remote refs instead would break `git
# pull`, which is the worse failure. The remedy is CB-59's server-side
# protection. TestKnownLimits pins that this reproduces.
_head_is_acceptable() {
    local sha="$1"
    local _ifs_save _types _re
    _ifs_save="$IFS"; IFS='|'; _types="${_BRANCH_TYPES[*]}"; IFS="$_ifs_save"
    # The full SHAPE, not a prefix test: a prefix test accepts `fix/a/b`, which
    # _guard_branch_type refuses, and the two must not disagree.
    _re="^(${_types})/[A-Za-z0-9._-]+$"

    local _upstream _candidates
    _upstream=$(git config --get branch.main.remote 2>/dev/null || true)
    [[ -z "${_upstream}" ]] && _upstream="origin"

    _candidates=$(git for-each-ref --points-at "${sha}" --format='%(refname)' \
        refs/heads/ refs/remotes/ 2>/dev/null | sort -u)
    if [[ -z "${_candidates}" ]]; then
        echo "  ${sha:0:12} resolves to no branch at all — a bare SHA or a tag." >&2
        return 1
    fi

    local _ref _name _local_ok="" _local_bad="" _upstream_main=""
    while read -r _ref; do
        [[ -z "${_ref}" ]] && continue
        case "${_ref}" in
            refs/heads/*)
                _name="${_ref#refs/heads/}"
                if [[ "${_name}" == "main" ]] || [[ "${_name}" =~ ${_re} ]]; then
                    _local_ok=1
                else
                    _local_bad="${_local_bad}    ${_ref}"$'\n'
                fi
                ;;
            "refs/remotes/${_upstream}/main")
                _upstream_main=1
                ;;
        esac
    done <<< "${_candidates}"

    [[ -n "${_upstream_main}" ]] && return 0

    if [[ -n "${_local_bad}" ]]; then
        echo "  merge head ${sha:0:12} is named by local branch(es) with no sanctioned type:" >&2
        printf '%s' "${_local_bad}" >&2
        return 1
    fi
    [[ -n "${_local_ok}" ]] && return 0
    echo "  ${sha:0:12} is named by no local branch — only by upstream or alias refs." >&2
    return 1
}
# ---8<--- END SHARED MERGE-GATE PREDICATE

# A merge/cherry-pick/revert IN PROGRESS is being COMPLETED, not authored, and
# completing a merge onto main is the sanctioned way work lands here.
#
# git does not run pre-commit for a merge it can complete by itself (it runs
# pre-merge-commit), so a CLEAN `git merge --no-ff` was always allowed. A
# CONFLICTED merge is finished by hand with `git commit`, which DOES run
# pre-commit — so without this exemption the hook blocked exactly the flow
# CLAUDE.md documents, and only when there was a conflict. Verified both ways in
# a throwaway repo before and after that fix; a peer session hit the clean path
# first, which is why the asymmetry surfaced as a question rather than an outage.
git_dir=$(git rev-parse --git-dir)

# ...but the exemption must not become the hole (CB-57). The conflicted path is
# the ONE merge route pre-merge-commit never sees, so if the exemption were
# unconditional the branch-name rule would hold for every merge onto main
# EXCEPT the one that had a conflict — enforcement that lapses precisely when
# the operator is already distracted. Here MERGE_HEAD genuinely does exist (git
# writes it when it stops), so the ref is resolvable, unlike in the clean case.
# NOTE THE CONDITION IS NOT SCOPED TO MAIN, and that was a defect when it was.
# The exemption below fires on ANY branch, so while this validation was
# main-only, `: > .git/MERGE_HEAD` on an untyped branch still skipped the
# branch-type check and let a source commit through — the same "one empty file
# turns off a rule" shape as the cherry-pick markers, on the other side of the
# condition, reachable by an interrupted merge on a hand-made branch. Review
# reproduced it. So the merge STATE is validated everywhere; only the
# head-ACCEPTABILITY rules (typed branch / upstream main) are about main.
if [[ -e "${git_dir}/MERGE_HEAD" ]]; then
    _refused=0
    _seen=0
    # `|| [[ -n "${_sha}" ]]` — `read` returns non-zero on an UNTERMINATED last
    # line, so a plain `while read` DROPS it. And `_seen` exists because the
    # loop running zero times used to leave _refused=0, after which the
    # merge-in-progress exemption below waved the commit straight through. Two
    # reproduced bypasses, one reachable BY ACCIDENT:
    #   (a) an empty MERGE_HEAD (an interrupted git can leave one) let arbitrary
    #       staged content land on main with no merge involved at all;
    #   (b) `printf '%s' <sha> > .git/MERGE_HEAD` — no trailing newline — landed
    #       a real two-parent merge of an untyped branch.
    # Neither typed --no-verify. This is the "guard reporting clean because it
    # could not look" shape that the CI job and the pre-merge-commit hook were
    # both already hardened against in this very change; pre-commit was the one
    # place left failing OPEN.
    while read -r _sha || [[ -n "${_sha}" ]]; do
        [[ -z "${_sha}" ]] && continue
        _seen=$((_seen + 1))
        # No `named` here: this is a separate `git commit` process and the ref
        # the operator typed is long gone, so the predicate judges EVERY ref at
        # the head. That strictness is the point — see the bypass recorded in
        # the shared block's comment.
        # The head rules are about MAIN. On a branch, completing a merge is not
        # authoring, and which branch was merged in is that branch's business.
        [[ "${branch}" == "main" ]] || continue
        _head_is_acceptable "${_sha}" || _refused=1
    done < "${git_dir}/MERGE_HEAD"

    if [[ "${_seen}" -eq 0 ]]; then
        echo "ERROR: ${git_dir}/MERGE_HEAD exists but names no merge head." >&2
        echo "" >&2
        echo "  Refusing rather than assuming there is nothing to check — an" >&2
        echo "  unreadable merge state must not read as a clean one." >&2
        echo "" >&2
        echo "  If a merge was interrupted, clear it:  git merge --abort" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi

    if [[ "${_refused}" -ne 0 ]]; then
        echo "" >&2
        echo "ERROR: refusing to complete a merge onto main from an untyped branch." >&2
        echo "  Expected one of: ${_BRANCH_TYPES[*]/%//*} (or 'main' itself)." >&2
        echo "" >&2
        echo "  A CONFLICTED merge — and a merge the pre-merge-commit hook has" >&2
        echo "  already refused — is completed with 'git commit', which never" >&2
        echo "  reaches that hook. So this check lives here too, with the same" >&2
        echo "  predicate; when the two differed, review reproduced a bypass in" >&2
        echo "  three commands." >&2
        echo "" >&2
        echo "  Abort, rename, retry:" >&2
        echo "    git merge --abort && git branch -m <old> fix/<slug>" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi
fi

# THE EXEMPTION IS FOR A MERGE, AND ONLY A MERGE.
#
# It used to read `for in_progress in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD`
# and exit 0 on mere EXISTENCE of any of them. The MERGE_HEAD arm above was
# hardened against exactly that shape; its two siblings were not, and review
# reproduced the consequence:
#
#     : > "$(git rev-parse --absolute-git-dir)/CHERRY_PICK_HEAD"
#     git add backdoor.py && git commit -m "…"       # rc=0, lands on main
#
# An EMPTY CHERRY_PICK_HEAD or REVERT_HEAD waved arbitrary staged content onto
# main — and it also skipped the BRANCH-TYPE check below, so a commit on
# `totally-untyped` succeeded too. Both hook rules, off, from one empty file.
# Reachable the same way empty MERGE_HEAD was: a conflicted `git cherry-pick`
# leaves the file until `--continue`/`--abort`, so an operator who is
# interrupted and later commits unrelated staged work lands it on main.
#
# The fix is not to harden them but to STOP EXEMPTING THEM. Completing a merge
# onto main is the sanctioned landing path; cherry-picking or reverting DIRECTLY
# onto main is "editing main directly" — the thing this file exists to refuse.
# So those two now fall through to the ordinary checks: on main the staged set
# must still be a plan note, and on a branch the name must still carry a type.
# A deliberate revert on main remains possible with --no-verify, which is the
# documented way to state that intent.
#
# NOTE the distinction from CLAUDE.md's bypass list, which names cherry-pick and
# revert as commands that "move main without passing any hook". That is a
# different and weaker statement: here the hook DOES run, and used to wave the
# commit through on purpose.
[[ -e "${git_dir}/MERGE_HEAD" ]] && exit 0

if [[ "${branch}" == "main" ]]; then
    # --diff-filter excludes nothing: a deletion on main is as much an edit as
    # an addition. Compare against HEAD, so this reads the staged set only.
    #
    # --no-renames IS LOAD-BEARING. With rename detection on (the default),
    # `--name-only` prints ONLY the destination path, so `git mv src/keep.py
    # .claude/plans/keep.md` presents a single allowlisted path and the source
    # file silently leaves main. Reproduced in adversarial review, against both
    # this hook and the CI job. Without renames, git reports the delete and the
    # add separately and the delete is caught.
    #
    # core.quotePath=false, because the DEFAULT is a false REFUSAL: a plan note
    # with non-ASCII in its name comes back C-quoted (".claude/plans/\321\202….md"),
    # the allowlist regex misses it, and a legitimate note cannot land on main.
    # This repo has already been bitten by C-quoting once, in
    # _guard_conflict_markers, where it silently ACCEPTED a conflict marker.
    staged=$(git -c core.quotePath=false diff --cached --no-renames --name-only)
    [[ -z "${staged}" ]] && exit 0

    offending=$(echo "${staged}" | grep -vE '^\.claude/plans/[^/]+\.md$' || true)
    if [[ -n "${offending}" ]]; then
        echo "ERROR: refusing to commit on main." >&2
        echo "" >&2
        echo "  Files outside .claude/plans/*.md:" >&2
        echo "${offending}" | sed 's/^/    /' >&2
        echo "" >&2
        echo "  CLAUDE.md: every code edit happens on a short-lived branch, in a" >&2
        echo "  worktree. The only thing that may land on main directly is a" >&2
        echo "  .claude/plans/*.md note." >&2
        echo "" >&2
        echo "  Move the work onto a branch (the stash is shared across worktrees," >&2
        echo "  because it lives in the common git dir):" >&2
        echo "    git stash push -- <files>" >&2
        echo "    tools/worktree-setup.sh fix/<slug> main" >&2
        echo "    cd .worktrees/fix-<slug> && git stash pop" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi
    exit 0
fi

# Not main: the branch must be one this repo sanctions. Checked at commit time
# rather than only at finish time, because by the time a branch has commits on
# it, renaming means every reference to it in a plan or handoff is already stale.
#
# `_type_re` here is the BRANCH-NAME check. The shared merge-gate block above
# builds its own `_re` inside `_head_is_acceptable`, because that block must stay
# byte-identical with pre-merge-commit-hook.sh and so cannot reach for a variable
# only this file defines. So the regex genuinely is constructed twice in this
# file, and that is a consequence of the byte-identity requirement rather than an
# oversight — an earlier version of this comment claimed the opposite, which was
# true before the shared block landed and false after (caught in review).
# TestHarnessIntegrity pins that all three copies use the full shape, and
# TestPreCommitHook::test_hook_and_guard_agree_on_nested_branch pins this one
# behaviourally.
[[ "${branch}" =~ ${_type_re} ]] && exit 0

echo "ERROR: branch '${branch}' does not carry a sanctioned type." >&2
echo "  Expected: ${_BRANCH_TYPES[*]/%//*}" >&2
echo "  A card-driven branch carries its id: fix/cb-50-worktree-harness" >&2
echo "" >&2
echo "  Rename now, while nothing references the name yet:" >&2
echo "    git branch -m ${branch} fix/<slug>" >&2
echo "" >&2
echo "  Deliberate exception: git commit --no-verify" >&2
exit 1
