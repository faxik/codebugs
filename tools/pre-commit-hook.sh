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
# The integration merge in worktree-finish.sh passes --no-verify, and git does
# not run pre-commit for a merge in any case (it runs pre-merge-commit).
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

# A merge/cherry-pick/revert IN PROGRESS is being COMPLETED, not authored, and
# completing a merge onto main is the sanctioned way work lands here.
#
# git does not run pre-commit for a merge it can complete by itself (it runs
# pre-merge-commit, which this repo does not install), so a CLEAN `git merge
# --no-ff` was always allowed. A CONFLICTED merge is finished by hand with
# `git commit`, which DOES run pre-commit — so without this check the hook
# blocked exactly the flow CLAUDE.md documents, and only when there was a
# conflict. Verified both ways in a throwaway repo before and after this fix;
# a peer session hit the clean path first, which is why the asymmetry surfaced
# as a question rather than as an outage.
git_dir=$(git rev-parse --git-dir)
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
# The PREDICATE must match tools/_guards.sh:_guard_branch_type exactly, not
# merely the list of types. A prefix test (`${branch} == ${pfx}/*`) accepts
# `fix/a/b`, which the finish guard's full-shape regex then REFUSES — so a
# session could commit for hours and be turned away at the last step, which is
# the worst possible moment to learn the name is wrong (cross-model review).
# tests/test_worktree_harness.py drives BOTH through the same case table.
_IFS_SAVE="$IFS"; IFS='|'; _types="${_BRANCH_TYPES[*]}"; IFS="$_IFS_SAVE"
[[ "${branch}" =~ ^(${_types})/[A-Za-z0-9._-]+$ ]] && exit 0

echo "ERROR: branch '${branch}' does not carry a sanctioned type." >&2
echo "  Expected: ${_BRANCH_TYPES[*]/%//*}" >&2
echo "  A card-driven branch carries its id: fix/cb-50-worktree-harness" >&2
echo "" >&2
echo "  Rename now, while nothing references the name yet:" >&2
echo "    git branch -m ${branch} fix/<slug>" >&2
echo "" >&2
echo "  Deliberate exception: git commit --no-verify" >&2
exit 1
