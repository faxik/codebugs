#!/usr/bin/env bash
# tools/install-hooks.sh — arm the local enforcement.
#
# Idempotent; safe to re-run. Run it once per clone. Nothing here is committed
# state: git hooks and git config are per-clone, which is why this has to be a
# script somebody runs rather than a file somebody merges.

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/_guards.sh
source "${_SCRIPT_DIR}/_guards.sh"
REPO_ROOT="$(_guards_resolve_repo_root "${_SCRIPT_DIR}")"

# Hooks live in the COMMON git dir, so one install covers main and every
# worktree — a worktree's .git is a file pointing here.
HOOK_DIR="$(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-common-dir)/hooks"
mkdir -p "${HOOK_DIR}"

echo "=== codebugs: installing local enforcement ==="
echo ""

# 1. merge.ff=false — FIRST, because it is the only step that cannot fail.
#
#    This is the only mechanism that can stop the failure of 2026-08-16: main
#    was advanced by a fast-forward from an already-merged branch. git fires NO
#    hook on a fast-forward — not pre-commit, not pre-merge-commit, not
#    post-merge in a way that could refuse it — because no commit is created.
#    There is nothing to catch it after the fact, so the only repair is to make
#    it impossible: with merge.ff=false every `git merge` creates a merge
#    commit, harness or not, deliberate or not.
#
#    It is also what makes [3/3] reachable at all: a fast-forward creates no
#    commit, so pre-merge-commit never fires. The two are one mechanism —
#    merge.ff=false guarantees a merge commit exists, the hook reads its parent.
#
#    ORDER IS LOAD-BEARING, and it was wrong first time round: with this step
#    last, a clone missing tools/pre-merge-commit-hook.sh (an older main, a
#    `git checkout <old-commit>`, the CB-57 bootstrap window) armed the
#    pre-commit hook, printed its ✓, then exited 1 at the merge-hook step —
#    leaving merge.ff UNSET. The installer could skip the one thing no hook can
#    replace. Reproduced in adversarial review. A step that cannot fail goes
#    first.
#
#    An explicit `git merge --ff` still works, which is the intended escape.
echo "[1/3] merge.ff=false"
git -C "${REPO_ROOT}" config merge.ff false
echo "  ✓ every 'git merge' now creates a merge commit"
echo "    (explicit 'git merge --ff' still overrides, by design)"

# 2. pre-commit — symlinked, not copied, so editing tools/pre-commit-hook.sh
#    takes effect immediately and the two cannot drift.
#
#    The target is REPO_ROOT's copy, NEVER "${_SCRIPT_DIR}" — this script is
#    usually run from a worktree (it is part of the harness a worktree brings
#    in), and a symlink into a worktree DANGLES the moment that worktree is
#    removed. git skips a dangling hook silently: no warning, no error, just no
#    enforcement, which is the exact failure class this harness exists to stop.
#    Caught by running it, not by reading it — the first install pointed here.
echo ""
echo "[2/3] pre-commit hook"
HOOK_SRC="${REPO_ROOT}/tools/pre-commit-hook.sh"
if [[ ! -f "${HOOK_SRC}" ]]; then
    echo "  ✗ ${HOOK_SRC} does not exist."
    echo "    The harness has not landed on main yet. Re-run this after the"
    echo "    branch is integrated — a hook symlinked into a worktree would"
    echo "    dangle silently when that worktree is removed."
    exit 1
fi
ln -sfn "${HOOK_SRC}" "${HOOK_DIR}/pre-commit"
chmod +x "${HOOK_SRC}"
echo "  ✓ ${HOOK_DIR}/pre-commit → ${HOOK_SRC}"
echo "    refuses commits on main outside .claude/plans/*.md, and commits on"
echo "    a branch with no sanctioned type"

# 3. pre-merge-commit — the half pre-commit structurally cannot cover (CB-57).
#
#    git does not run pre-commit for a merge it completes itself, so until this
#    hook existed `git merge <untyped-branch>` onto main was read by nothing:
#    merge.ff=false gave it a merge commit and no mechanism looked at the name.
#
#    Same symlink discipline and the same reason as [2/3].
echo ""
echo "[3/3] pre-merge-commit hook"
MERGE_HOOK_SRC="${REPO_ROOT}/tools/pre-merge-commit-hook.sh"
if [[ ! -f "${MERGE_HOOK_SRC}" ]]; then
    echo "  ✗ ${MERGE_HOOK_SRC} does not exist."
    echo "    CB-57 has not landed on main yet. Re-run this after the branch is"
    echo "    integrated — a hook symlinked into a worktree would dangle"
    echo "    silently when that worktree is removed."
    exit 1
fi
ln -sfn "${MERGE_HOOK_SRC}" "${HOOK_DIR}/pre-merge-commit"
chmod +x "${MERGE_HOOK_SRC}"
echo "  ✓ ${HOOK_DIR}/pre-merge-commit → ${MERGE_HOOK_SRC}"
echo "    refuses a merge onto main from a branch with no sanctioned type"

echo ""
echo "=== armed ==="
echo ""
echo "Verify:"
echo "  git -C ${REPO_ROOT} config --get merge.ff        # false"
echo "  ls -l $(git -C "${REPO_ROOT}" rev-parse --git-common-dir)/hooks/pre-commit"
echo "  ls -l $(git -C "${REPO_ROOT}" rev-parse --git-common-dir)/hooks/pre-merge-commit"
