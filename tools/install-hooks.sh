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
#
# Resolved with `--git-path hooks` rather than `--git-common-dir`/hooks so that
# it follows `core.hooksPath`. Installing into the common dir while git reads a
# redirected directory would arm nothing while reporting success — the same
# mismatch that made _guard_enforcement_armed lie (adversarial review). If
# core.hooksPath is set, this installs where git will actually look.
HOOK_DIR="$(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-path hooks)"
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
#    It is also what makes [3/4] reachable at all: a fast-forward creates no
#    commit, so pre-merge-commit never fires. The two are one mechanism —
#    merge.ff=false guarantees a merge commit exists, the hook reads its parent.
#
#    ORDER IS LOAD-BEARING, and it was wrong first time round: with this step
#    last, a clone missing tools/pre-merge-commit-hook.sh (an older main, a
#    `git checkout <old-commit>`, the CB-57 bootstrap window) armed the
#    pre-commit hook, printed its ✓, then exited 1 at the merge-hook step —
#    leaving merge.ff UNSET. The installer could skip the one thing no hook can
#    replace. Reproduced in adversarial review.
#
#    Precisely: this is the first step that can FAIL FOR A REASON THIS SCRIPT IS
#    ABOUT. Four commands still precede it — sourcing _guards.sh, resolving the
#    repo root, resolving the hooks dir, mkdir -p — and each is fatal under
#    `set -e`. Review pointed out that "a step that cannot fail goes first" was
#    therefore not literally true, and it is not; what matters is that nothing
#    ARMING-RELATED can abort before merge.ff is set.
#
#    An explicit `git merge --ff` still works, which is the intended escape.
echo "[1/4] merge.ff=false"
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
echo "[2/4] pre-commit hook"
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
#    Same symlink discipline and the same reason as [2/4].
echo ""
echo "[3/4] pre-merge-commit hook"
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

# 4. commit-msg — the half NEITHER of the other two can cover, and the reason is
#    a measured property of git rather than a division of labour: at pre-commit
#    time the message being written does not exist anywhere. COMMIT_EDITMSG then
#    holds the PREVIOUS commit's message, so a naming rule placed in pre-commit
#    would be wired to someone else's input.
#
#    This one was armed by the installer ALONE at first: _guard_enforcement_armed
#    could not demand it in the same change that introduced the source without
#    making that change unlandable by the harness it extends — the CB-50/CB-57
#    bootstrap for a third time. Since T-21 the guard demands it too, under the
#    same monotonic condition as [2/4], so a clone that never re-ran this script
#    is refused at its next finish instead of silently lacking a third hook.
#
#    Same symlink discipline and the same reason as [2/4].
echo ""
echo "[4/4] commit-msg hook"
MSG_HOOK_SRC="${REPO_ROOT}/tools/commit-msg-hook.sh"
if [[ ! -f "${MSG_HOOK_SRC}" ]]; then
    echo "  ✗ ${MSG_HOOK_SRC} does not exist."
    echo "    The plan-note naming gate has not landed on main yet. Re-run this"
    echo "    after the branch is integrated — a hook symlinked into a worktree"
    echo "    would dangle silently when that worktree is removed."
    exit 1
fi
ln -sfn "${MSG_HOOK_SRC}" "${HOOK_DIR}/commit-msg"
chmod +x "${MSG_HOOK_SRC}"
echo "  ✓ ${HOOK_DIR}/commit-msg → ${MSG_HOOK_SRC}"
echo "    refuses a .claude/plans/*.md commit on main whose message does not"
echo "    name the note (K-3: add plan notes BY NAME, never by directory)"

echo ""
echo "=== armed ==="
echo ""
echo "Verify:"
echo "  git -C ${REPO_ROOT} config --get merge.ff        # false"
echo "  ls -l $(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-path hooks)/pre-commit"
echo "  ls -l $(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-path hooks)/pre-merge-commit"
echo "  ls -l $(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-path hooks)/commit-msg"
