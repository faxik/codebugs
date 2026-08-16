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
# on every clean merge: a gate that cannot fire, which is worse than no gate,
# because the table in CLAUDE.md would then claim a rule nothing enforces.
#
# What git DOES provide is `GITHEAD_<sha>=<the ref the caller named>`, set per
# merge head — git's own record of what is being merged, and what the merge
# strategies themselves read. Not the commit MESSAGE: parsing "Merge branch
# 'x'" is the name-matching heuristic CB-57 explicitly refused to ship, and it
# would be blind here anyway, since worktree-finish.sh passes its own `-m`.
#
# The ref is then resolved with `git rev-parse --symbolic-full-name`, so the
# hook judges what git says the name IS, not the string the caller typed:
#   refs/heads/fix/cb-1-x     -> fix/cb-1-x        typed, accepted
#   refs/remotes/origin/main  -> main              a pull, accepted
#   (unresolvable)            -> a bare SHA or tag, REFUSED
#
# FAILS CLOSED on a head it cannot attribute. "I could not tell" must never
# read as "allowed" — that is the silent-skip shape _guards.sh was hardened
# against three separate times.
#
# ---------------------------------------------------------------------------
# WHAT THIS HOOK DOES NOT COVER, and what does.
#
# A CONFLICTED merge never reaches pre-merge-commit: git stops, the operator
# resolves and runs `git commit`, which fires PRE-COMMIT. So the conflicted
# path is enforced in tools/pre-commit-hook.sh, which reads MERGE_HEAD — a file
# that, on that path, genuinely does exist. The two hooks cover disjoint halves
# of the same rule and neither is redundant.
#
# SCOPED TO MAIN ON PURPOSE. worktree-finish.sh forward-merges main INTO the
# worktree so conflicts surface in safe space; that merge lands on a typed
# branch and must always pass.
#
# Escape hatch: `git merge --no-verify`. Same contract as pre-commit — the hook
# stops the accident, and an operator typing the flag has stated an intent.

set -euo pipefail

_BRANCH_TYPES=(fix feature refactor docs)

branch=$(git symbolic-ref --short -q HEAD || echo "")

# Not on main: nothing to say. Covers the forward-merge into a worktree, and a
# detached HEAD (a rebase or a bisect), which has no branch to protect.
[[ "${branch}" != "main" ]] && exit 0

_IFS_SAVE="$IFS"; IFS='|'; _types="${_BRANCH_TYPES[*]}"; IFS="$_IFS_SAVE"
# The PREDICATE must match tools/_guards.sh:_guard_branch_type and
# tools/pre-commit-hook.sh exactly — not merely the list of types. A prefix test
# accepts `fix/a/b`, which _guard_branch_type then refuses, so the three would
# disagree about what a legal branch is at three different moments.
_type_re="^(${_types})/[A-Za-z0-9._-]+$"

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

    # What git says this ref IS, not the string the caller typed.
    full=$(git rev-parse --symbolic-full-name "${named}" 2>/dev/null || true)

    case "${full}" in
        refs/heads/*)      name="${full#refs/heads/}" ;;
        refs/remotes/*)    name="${full#refs/remotes/}"; name="${name#*/}" ;;
        *)                 name="" ;;
    esac

    if [[ -z "${name}" ]]; then
        echo "ERROR: refusing to merge '${named}' (${sha:0:12}) onto main." >&2
        echo "" >&2
        echo "  It does not resolve to a branch, so it is a bare SHA or a tag." >&2
        echo "  Merging one onto main leaves nothing naming where the work came" >&2
        echo "  from, which is the provenance the --no-ff rule exists to keep." >&2
        rc=1
        continue
    fi

    # `main` itself is accepted: that is a pull, or a re-merge of main. Under
    # merge.ff=false even a would-be fast-forward pull becomes a merge commit
    # and lands here, so refusing it would break `git pull` outright.
    [[ "${name}" == "main" ]] && continue
    [[ "${name}" =~ ${_type_re} ]] && continue

    echo "ERROR: refusing to merge onto main from a branch with no sanctioned type." >&2
    echo "" >&2
    echo "  Branch:   ${name}" >&2
    echo "  Ref:      ${full}" >&2
    echo "  Expected: ${_BRANCH_TYPES[*]/%//*}" >&2
    echo "" >&2
    echo "  This is the surviving half of the 2026-08-16 incident (CB-57):" >&2
    echo "  merge.ff=false gives an untyped branch a merge COMMIT, but until" >&2
    echo "  this hook existed nothing read its NAME." >&2
    echo "" >&2
    echo "  Rename the branch, then merge again:" >&2
    echo "    git branch -m ${name} fix/<slug>" >&2
    echo "" >&2
    echo "  Deliberate exception: git merge --no-verify" >&2
    rc=1
done

exit "${rc}"
