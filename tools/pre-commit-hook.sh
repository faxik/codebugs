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
if [[ "${branch}" == "main" && -e "${git_dir}/MERGE_HEAD" ]]; then
    _bad=""
    while read -r _sha; do
        [[ -z "${_sha}" ]] && continue
        _names=$(git for-each-ref --points-at "${_sha}" --format='%(refname)' \
            refs/heads/ refs/remotes/ \
            | sed -e 's#^refs/heads/##' -e 's#^refs/remotes/[^/]*/##' | sort -u)
        _ok=""
        while read -r _n; do
            [[ -z "${_n}" ]] && continue
            if [[ "${_n}" == "main" || "${_n}" =~ ${_type_re} ]]; then _ok=1; break; fi
        done <<< "${_names}"
        [[ -n "${_ok}" ]] && continue
        _bad="${_bad}  ${_sha:0:12} ${_names:-(no branch points at it)}"$'\n'
    done < "${git_dir}/MERGE_HEAD"

    if [[ -n "${_bad}" ]]; then
        echo "ERROR: refusing to complete a merge onto main from an untyped branch." >&2
        echo "" >&2
        printf '%s' "${_bad}" >&2
        echo "  Expected one of: ${_BRANCH_TYPES[*]/%//*}" >&2
        echo "" >&2
        echo "  A CONFLICTED merge is completed with 'git commit', which never" >&2
        echo "  reaches the pre-merge-commit hook — so this check lives here." >&2
        echo "" >&2
        echo "  Abort, rename, retry:" >&2
        echo "    git merge --abort && git branch -m <old> fix/<slug>" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi
fi

for in_progress in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
    [[ -e "${git_dir}/${in_progress}" ]] && exit 0
done

if [[ "${branch}" == "main" ]]; then
    # --diff-filter excludes nothing: a deletion on main is as much an edit as
    # an addition. Compare against HEAD, so this reads the staged set only.
    staged=$(git diff --cached --name-only)
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
# `_type_re` is built once at the top of this file and used by both checks —
# the merge-completion gate above and this one. Two constructions of the same
# regex in one file would be the duplicated-check hazard at its silliest.
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
