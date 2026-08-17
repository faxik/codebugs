#!/usr/bin/env bash
# codebugs pre-merge-commit hook — installed as .git/hooks/pre-merge-commit by
# tools/install-hooks.sh (symlink, so edits here take effect immediately).
#
# THE HOLE THIS CLOSES (CB-57). `merge.ff=false` forces every `git merge` to
# create a merge commit, but NOTHING read the merged branch's NAME, because
# install-hooks.sh installed only `pre-commit` and git runs `pre-merge-commit`
# for a merge it completes itself. So the incident the whole harness exists for
# — `merge worktree-cb-45-similarity-seam` onto main, 2026-08-16, a branch with
# no type prefix — would still land today, just with a merge commit instead of
# a fast-forward.
#
# THE RULE, one sentence: anything merged into main must come either from main
# itself (a pull) or from a branch carrying a sanctioned type.
#
# ---------------------------------------------------------------------------
# WHERE THE MERGED REF COMES FROM, and why NOT from MERGE_HEAD.
#
# CB-57 proposed "a pre-merge-commit hook validating the branch behind
# MERGE_HEAD". THAT DOES NOT WORK, and it was verified by running it rather
# than by reading the docs: on git 2.53 a CLEAN merge is resolved in memory and
# `$GIT_DIR/MERGE_HEAD` IS NEVER WRITTEN. At hook time the git dir holds
# AUTO_MERGE (a tree), ORIG_HEAD and COMMIT_EDITMSG — no MERGE_HEAD, and
# `git rev-parse MERGE_HEAD` fails outright. A hook keyed on that file exits 0
# on every clean merge: a gate that cannot fire, which is worse than no gate.
#
# What git DOES provide is `GITHEAD_<sha>=<what the caller named>`, set per
# merge head — git's own record, and what the merge strategies read. Not the
# commit MESSAGE: parsing "Merge branch 'x'" is the name-matching heuristic
# CB-57 refused, and it would be blind anyway, since worktree-finish.sh passes
# its own `-m`.
#
# BUT `GITHEAD_` IS NOT ALWAYS A NAME. Measured on git 2.53, in this repo's
# scratchpad, against a real remote:
#     git merge origin/main   ->  GITHEAD_<sha>=origin/main
#     git pull                ->  GITHEAD_<sha>=<sha>      (the raw OID)
#     git merge FETCH_HEAD    ->  GITHEAD_<sha>=<sha>      (the raw OID)
# The first draft of this hook refused every `git pull` for exactly that reason
# — a FALSE REFUSAL of the flow its own comment claimed to allow, found by
# adversarial review, reproduced, and fixed below by falling back to the refs
# that point AT the head when the caller's name is not a ref. (Note one review
# asserted `GITHEAD_` carries a "branch 'main' of <url>" description in this
# case; it does not, on this version. Measured, not assumed.)
#
# ---------------------------------------------------------------------------
# WHAT THIS HOOK DOES NOT COVER, and what does.
#
# A CONFLICTED merge never reaches pre-merge-commit: git stops, the operator
# resolves and runs `git commit`, which fires PRE-COMMIT. The same is true
# after THIS hook refuses — git leaves the merge in progress and says "use 'git
# commit' to complete the merge". So tools/pre-commit-hook.sh carries the same
# predicate for that path, and the two must not disagree: when they did, review
# reproduced a three-command bypass (refuse here, create a typed branch at the
# same commit, `git commit`). That is why the predicate below is duplicated
# BYTE-IDENTICALLY into pre-commit-hook.sh between the same markers, and why a
# test compares the two blocks rather than grepping for a substring.
#
# SCOPED TO MAIN ON PURPOSE. worktree-finish.sh forward-merges main INTO the
# worktree so conflicts surface in safe space; that merge lands on a typed
# branch and must always pass.
#
# Escape hatch: `git merge --no-verify`. Same contract as pre-commit — the hook
# stops the accident, and an operator typing the flag has stated an intent.

set -euo pipefail

_BRANCH_TYPES=(fix feature refactor docs)

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

branch=$(git symbolic-ref --short -q HEAD || echo "")

# Not on main: nothing to say. Covers the forward-merge into a worktree, and a
# detached HEAD (a rebase or a bisect), which has no branch to protect.
[[ "${branch}" != "main" ]] && exit 0

# Every GITHEAD_<sha> in the environment. `compgen` returns 1 when nothing
# matches, which under `set -e` would kill the hook, hence the guard.
_githead_vars=$(compgen -A variable GITHEAD_ 2>/dev/null || true)

if [[ -z "${_githead_vars}" ]]; then
    # Not "allow anyway". If git ever stops exporting GITHEAD_, this hook has
    # lost its only honest input and must say so rather than wave the merge
    # through — the premise is pinned by
    # tests/test_worktree_harness.py::TestPreMergeCommitHook, so a git upgrade
    # that changes it turns the suite red before it reaches anyone in anger.
    echo "ERROR: cannot determine what is being merged — no GITHEAD_* in the" >&2
    echo "  environment. This hook reads git's own record of the merged ref;" >&2
    echo "  without it, it cannot tell a typed branch from an untyped one, and" >&2
    echo "  refusing is the only answer that is not a guess." >&2
    echo "" >&2
    echo "  Deliberate exception: git merge --no-verify" >&2
    exit 1
fi

rc=0
for _var in ${_githead_vars}; do
    sha="${_var#GITHEAD_}"
    named="${!_var}"
    _head_is_acceptable "${sha}" || rc=1
done

if [[ "${rc}" -ne 0 ]]; then
    echo "" >&2
    echo "ERROR: refusing to merge onto main. Expected ${_BRANCH_TYPES[*]/%//*}" >&2
    echo "  (or 'main' itself, which is a pull)." >&2
    echo "" >&2
    echo "  This is the surviving half of the 2026-08-16 incident (CB-57):" >&2
    echo "  merge.ff=false gives an untyped branch a merge COMMIT, but until" >&2
    echo "  this hook existed nothing read its NAME." >&2
    echo "" >&2
    echo "  Rename the branch, then merge again:" >&2
    echo "    git merge --abort && git branch -m <old> fix/<slug>" >&2
    echo "" >&2
    echo "  Deliberate exception: git merge --no-verify" >&2
fi

exit "${rc}"
