#!/usr/bin/env bash
# Worktree Finish — tools/worktree-finish.sh <slug> [commit-message] [flags]
#
# Runs the gates, forward-merges main into the worktree so conflicts surface in
# safe space, then integrates onto main with `git merge --no-ff` under a flock
# so parallel agents cannot race, and removes the worktree.
#
# Ported from ../autosorter/tools/worktree-finish.sh (2026-08-16), which is
# ~1400 lines because it carries traceability, changelog, testmon, citation and
# deferred-post-merge-gate machinery this repo does not have. What survives is
# the part that makes the workflow a rule instead of a habit: the guards, the
# forward-merge-first ordering, --no-ff, and the lock.
#
# Example:
#   tools/worktree-finish.sh fix-cb-50-worktree-harness 'feat: harness' \
#       --merge-msg 'Merge fix/cb-50-worktree-harness: worktree harness (CB-50)'

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/_guards.sh
source "${_SCRIPT_DIR}/_guards.sh"

REPO_ROOT="$(_guards_resolve_repo_root "${_SCRIPT_DIR}")"
WORKTREE_DIR="${REPO_ROOT}/.worktrees"
LOCK_FILE="${WORKTREE_DIR}/.integrate.lock"

SKIP_CHECKS=false
ALLOW_STALE_BASE=false
MERGE_MSG=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-checks)      SKIP_CHECKS=true ;;
        --allow-stale-base) ALLOW_STALE_BASE=true ;;
        --merge-msg)        MERGE_MSG="${2:-}"; shift ;;
        --merge-msg=*)      MERGE_MSG="${1#--merge-msg=}" ;;
        *)                  POSITIONAL+=("$1") ;;
    esac
    shift
done
set -- "${POSITIONAL[@]+"${POSITIONAL[@]}"}"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <slug> [commit-message] [flags]"
    echo ""
    echo "  slug:                worktree directory name under .worktrees/"
    echo "  commit-message:      used only if the worktree is dirty"
    echo "  --merge-msg MSG:     integration commit message. Default follows"
    echo "                       CLAUDE.md: 'Merge <branch>: <subject> (CB-NN)'"
    echo "  --skip-checks:       skip ruff + pytest (NOT the safety guards)"
    echo "  --allow-stale-base:  proceed past the stale-base refusal"
    echo ""
    echo "  Env: CODEBUGS_STALE_BASE_MAX (default ${CODEBUGS_STALE_BASE_MAX})"
    echo ""
    echo "Available worktrees:"
    git -C "${REPO_ROOT}" worktree list --porcelain 2>/dev/null \
        | grep "^worktree " | grep -v "^worktree ${REPO_ROOT}$" | sed 's|^worktree .*/||; s|^|  |'
    exit 1
fi

SLUG="$1"
COMMIT_MSG="${2:-}"

# Resolve slug → path, accepting either the full directory name
# (fix-cb-50-thing) or the branch suffix (cb-50-thing).
_resolve_worktree_path() {
    local slug="$1" all found stripped
    all=$(git -C "${REPO_ROOT}" worktree list --porcelain 2>/dev/null \
        | grep "^worktree " | sed 's/^worktree //')
    _wt_match() { echo "${all}" | grep "/${1}\$" | head -1; }

    found=$(_wt_match "${slug}"); [[ -n "$found" ]] && { echo "$found"; return 0; }
    for pfx in "${_BRANCH_TYPES[@]}"; do
        stripped="${slug#"${pfx}-"}"
        if [[ "${stripped}" != "${slug}" ]]; then
            found=$(_wt_match "${stripped}"); [[ -n "$found" ]] && { echo "$found"; return 0; }
        fi
    done
    for pfx in "${_BRANCH_TYPES[@]}"; do
        found=$(_wt_match "${pfx}-${slug}"); [[ -n "$found" ]] && { echo "$found"; return 0; }
    done
    echo "${WORKTREE_DIR}/${slug}"
}

WORKTREE_PATH=$(_resolve_worktree_path "${SLUG}")
if [[ ! -d "${WORKTREE_PATH}" ]]; then
    echo "ERROR: no worktree for slug '${SLUG}' (last tried: ${WORKTREE_PATH})"
    echo "Available worktrees:"
    git -C "${REPO_ROOT}" worktree list --porcelain 2>/dev/null \
        | grep "^worktree " | grep -v "^worktree ${REPO_ROOT}$" | sed 's|^worktree .*/||; s|^|  |'
    exit 2
fi

BRANCH=$(git -C "${WORKTREE_PATH}" branch --show-current)
echo "=== Worktree Finish ==="
echo "  Path:   ${WORKTREE_PATH}"
echo "  Branch: ${BRANCH}"
echo ""

# Both branch guards run BEFORE the auto-commit below, so a trip leaves the
# worktree exactly as the operator left it.
_guard_finishable_branch "${BRANCH}" || exit $?
_guard_branch_type "${BRANCH}" || exit $?

# ---------------------------------------------------------------------------
echo "[1/7] Working tree status..."
STATUS=$(git -C "${WORKTREE_PATH}" status --short)
if [[ -n "${STATUS}" ]]; then
    echo "${STATUS}" | sed 's/^/    /'
    if [[ -z "${COMMIT_MSG}" ]]; then
        echo ""
        echo "  Uncommitted changes and no commit message given."
        echo "  Example: $0 ${SLUG} 'fix: what changed'"
        exit 1
    fi
    _guard_untracked_py_at_root "${STATUS}" || exit $?
    echo "  Committing: ${COMMIT_MSG}"
    git -C "${WORKTREE_PATH}" add -A
    _guard_leaked_repr "${WORKTREE_PATH}" || exit $?
    git -C "${WORKTREE_PATH}" commit --no-verify -m "${COMMIT_MSG}"
    echo "  ✓ Committed"
else
    echo "  ✓ Clean"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[2/7] Latest commit:"
git -C "${WORKTREE_PATH}" log --oneline -1 | sed 's/^/  /'

echo ""
echo "[3/7] Changed files (vs main):"
CURRENT_MAIN=$(git -C "${REPO_ROOT}" rev-parse main)
CHANGED_FILES=$(git -C "${WORKTREE_PATH}" diff "${CURRENT_MAIN}..HEAD" --name-only 2>/dev/null || true)
[[ -n "${CHANGED_FILES}" ]] && echo "${CHANGED_FILES}" | sed 's/^/  /'

# Runs AFTER [1/7]'s auto-commit, so a dirty worktree holding real work is
# already on HEAD and is not mistaken for an empty branch.
_guard_nonempty_diff "${WORKTREE_PATH}" "${CURRENT_MAIN}" || exit $?
_guard_conflict_markers "${WORKTREE_PATH}" "${CURRENT_MAIN}" || exit $?

# ---------------------------------------------------------------------------
echo ""
echo "[4/7] Divergence:"
WORKTREE_BASE=$(git -C "${WORKTREE_PATH}" merge-base HEAD "${CURRENT_MAIN}" 2>/dev/null || echo "unknown")
if [[ "${WORKTREE_BASE}" == "${CURRENT_MAIN}" ]]; then
    echo "  ✓ Up to date with main"
else
    BEHIND_COUNT=$(git -C "${REPO_ROOT}" rev-list "${WORKTREE_BASE}..${CURRENT_MAIN}" --count 2>/dev/null || echo "?")
    echo "  ⚠ ${BEHIND_COUNT} commits behind main"
    _guard_stale_base "${BEHIND_COUNT}" "${CODEBUGS_STALE_BASE_MAX}" "${ALLOW_STALE_BASE}" || exit $?
fi

# ---------------------------------------------------------------------------
# Forward-merge main into the worktree FIRST. Conflicts get resolved inside the
# worktree, never by committing a resolution on main — CLAUDE.md is explicit
# that a surgical fix on main is editing main directly, wearing a hat.
echo ""
echo "[5/7] Forward-merging main into worktree..."
if [[ "${WORKTREE_BASE}" != "${CURRENT_MAIN}" ]]; then
    if git -C "${WORKTREE_PATH}" merge "${CURRENT_MAIN}" --no-edit >/dev/null 2>&1; then
        echo "  ✓ Merged main into worktree"
    else
        echo "  ✗ Conflicts. Resolve them IN THE WORKTREE, then re-run:"
        echo "      cd ${WORKTREE_PATH}"
        echo "      # resolve, then: git add <files> && git commit"
        echo "      tools/worktree-finish.sh ${SLUG}"
        exit 1
    fi
else
    echo "  ✓ Already current"
fi

# SAMPLE THE TESTED STATE HERE — before the gates, not after them.
#
# These two values are the definition of "what the gates below are about to run
# against", and they are only that if nothing can happen between the sample and
# the run. Sampling them AFTER pytest returns is the CB-41 defect exactly: a
# concurrent finish landing during the ~70s test window moves main, the
# post-test sample records the NEW main, and the in-lock comparison then
# compares new-main to new-main and passes — so the skew guard silently
# certifies the untested combination it was written to refuse.
#
# That is not hypothetical here: round 1 of this card's review wrote the guard
# with the sample below the gates, and the round-2 adversary reproduced the
# skew deterministically with a stubbed slow test command. CB-41 took three
# rounds to learn the same lesson about a lease deadline, and the rule it
# produced applies verbatim: point-of-use discipline is the wrong enforcement
# layer, because it has to be re-established every time a statement is inserted
# between the sample and the use. TESTED_MAIN is now the SAME VALUE the
# forward-merge used, so it cannot drift from it by construction.
TESTED_MAIN="${CURRENT_MAIN}"
TESTED_HEAD=$(git -C "${WORKTREE_PATH}" rev-parse HEAD)

# ---------------------------------------------------------------------------
# Gates run in the WORKTREE, on the post-forward-merge tree — the combined
# state that is about to become main, not the branch in isolation.
echo ""
if [[ "${SKIP_CHECKS}" == true ]]; then
    echo "[6/7] Checks SKIPPED (--skip-checks)"
else
    echo "[6/7] Running checks in the worktree..."
    # '--extra dev' is not optional here: pytest and ruff live in
    # project.optional-dependencies, which `uv run` does not install by default.
    #
    # `ruff check` only. `ruff format` is deliberately NOT a gate — a large part
    # of the existing tree is non-conformant to it, so adding it would refuse
    # every finish including ones that changed nothing formatting-related.
    if ! (cd "${WORKTREE_PATH}" && uv run --extra dev ruff check src/ tests/); then
        echo "  ✗ ruff check failed — fix in the worktree, then re-run."
        exit 1
    fi
    echo "  ✓ ruff check clean"

    if ! (cd "${WORKTREE_PATH}" && uv run --extra dev python -m pytest tests/ -q); then
        echo "  ✗ pytest failed — fix in the worktree, then re-run."
        exit 1
    fi
    echo "  ✓ tests pass"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[7/7] Integrating into main (under lock)..."

# Fail fast before waiting on the lock, then re-assert inside it.
_guard_enforcement_armed "${REPO_ROOT}" || exit $?
_guard_workspace_on_main "${REPO_ROOT}" || exit $?
_guard_main_clean "${REPO_ROOT}" "${WORKTREE_PATH}" "${CURRENT_MAIN}" || exit $?

mkdir -p "${WORKTREE_DIR}"
exec 9>"${LOCK_FILE}"
if ! flock -w 60 9; then
    echo "  ERROR: could not acquire the integration lock after 60s."
    echo "  Another integration may be stuck. Check: ${LOCK_FILE}"
    exit 1
fi
echo "  ✓ Lock acquired"

# Re-assert everything time-dependent inside the lock. Up to 60s passed, and
# the gates above ran BEFORE the lock existed.
_guard_workspace_on_main "${REPO_ROOT}" || exit $?
_guard_main_clean "${REPO_ROOT}" "${WORKTREE_PATH}" "${CURRENT_MAIN}" || exit $?
CURRENT_MAIN=$(git -C "${REPO_ROOT}" rev-parse main)

# THE LOCK ONLY SERIALIZES THE MERGE, NOT THE TESTING — so verify the tested
# state is still the state being landed (cross-model review).
#
# The race, with two finishers: A and B both forward-merge and test against
# main M0. A takes the lock and lands, producing M1. B takes the lock, and
# without this check merges into M1 a tree that was never tested against it.
# That is a green-looking integration of an untested combination, which is the
# same success-shaped lie the rest of this harness exists to prevent.
#
# Refusing and asking for a re-run is correct rather than lazy: the re-run's
# [5/7] forward-merges the NEW main and re-runs the gates against it, which is
# precisely the work that would otherwise be skipped. Acquiring the lock before
# the tests instead would hold it for the whole ~70s suite and serialize every
# concurrent finish behind it.
if [[ "${CURRENT_MAIN}" != "${TESTED_MAIN}" ]]; then
    echo "  ✗ main moved while this finish was running:"
    echo "      tested against: ${TESTED_MAIN:0:9}"
    echo "      main is now:    ${CURRENT_MAIN:0:9}"
    echo "    Landing now would merge a tree that was never tested against the"
    echo "    current main. Re-run — it will forward-merge and re-test:"
    echo "      tools/worktree-finish.sh ${SLUG}"
    flock -u 9
    exit 13
fi

# Same argument for the branch: merge the SHA that was tested, not whatever the
# name points at now (a concurrent session in that worktree can commit).
CURRENT_HEAD=$(git -C "${WORKTREE_PATH}" rev-parse HEAD)
if [[ "${CURRENT_HEAD}" != "${TESTED_HEAD}" ]]; then
    echo "  ✗ ${BRANCH} moved while this finish was running:"
    echo "      tested: ${TESTED_HEAD:0:9}   now: ${CURRENT_HEAD:0:9}"
    echo "    Re-run to test what is actually there."
    flock -u 9
    exit 13
fi

# The integration message CLAUDE.md specifies: "Merge <branch>: <what changed>
# (CB-NN)". The merge commit is what makes a card's whole iteration recoverable
# as one unit — which is exactly what the 2026-08-16 fast-forward destroyed.
if [[ -z "${MERGE_MSG}" ]]; then
    # --no-merges: after [5/7] the tip is usually the forward-merge, whose
    # auto-subject is "Merge commit '<sha>' into <branch>". Using it produced
    #   Merge fix/cb-99-x: Merge commit '54bfab9…' into fix/cb-99-x (CB-99)
    # — the "what changed" slot filled with noise, in the common case where the
    # branch was behind main. The merge commit is the ONE artifact this card
    # exists to protect, so it gets the last real subject, not the plumbing.
    _subject=$(git -C "${WORKTREE_PATH}" log -1 --no-merges --format=%s 2>/dev/null || true)
    [[ -z "${_subject}" ]] && _subject=$(git -C "${WORKTREE_PATH}" log -1 --format=%s)
    _ids=$(printf '%s' "${BRANCH}" | grep -oiE 'cb-?[0-9]+' \
        | tr '[:upper:]' '[:lower:]' | sed -E 's/^cb-?/CB-/' | sort -u | paste -sd, - || true)
    MERGE_MSG="Merge ${BRANCH}: ${_subject}"
    [[ -n "${_ids}" ]] && MERGE_MSG="${MERGE_MSG} (${_ids})"
fi

# ALWAYS --no-ff. A fast-forward scatters the iteration across main's history
# and leaves no commit that names the branch — on 2026-08-16 exactly that
# happened, and the branch then sat at main's own SHA where every subsequent
# merge would silently ff again. --no-ff makes the bad state unrepresentable
# here; `git config merge.ff false` (set by tools/install-hooks.sh) covers the
# off-harness case.
#
# --no-verify was REMOVED here (CB-57). It was harmless while no merge hook
# existed, and became a hole the moment one did: the harness would have been the
# single caller exempt from the branch-name check, i.e. the gate would apply to
# every merge except the one this repo actually uses. _guard_branch_type has
# already refused an untyped branch by this point, so the hook is a second,
# independent reader of the same rule rather than a new obstacle — and if the
# two ever disagree, that disagreement is a defect worth failing on, not
# suppressing.
if git -C "${REPO_ROOT}" merge "${BRANCH}" --no-ff -m "${MERGE_MSG}"; then
    echo "  ✓ Merged: ${MERGE_MSG}"
else
    echo "  ✗ Merge failed (conflict, or the pre-merge-commit hook refused it)."
    echo "    Resolve IN THE WORKTREE, never on main:"
    echo "      cd ${REPO_ROOT} && git merge --abort"
    echo "      cd ${WORKTREE_PATH} && git merge ${CURRENT_MAIN}"
    echo "      # resolve, git add <files> && git commit"
    echo "      tools/worktree-finish.sh ${SLUG}"
    flock -u 9
    exit 1
fi
flock -u 9

# ---------------------------------------------------------------------------
# Remove the worktree. NEVER the branch: no merged branch has ever been deleted
# in this repo, and the merge commit plus the branch ref are together what makes
# an iteration recoverable.
#
# Never --force either: a removal that refuses is telling you work is
# uncommitted in there.
echo ""
echo "Cleaning up..."
if git -C "${REPO_ROOT}" worktree remove "${WORKTREE_PATH}" 2>/dev/null; then
    echo "  ✓ Worktree removed (branch ${BRANCH} kept, as this repo never deletes branches)"
    echo "  NOTE: ${WORKTREE_PATH} no longer exists. A shell sitting there does not"
    echo "        error — it silently resolves to the MAIN workspace, so the next"
    echo "        relative-path command would run against main."
    echo "        Run: cd ${REPO_ROOT}"
else
    echo "  ⚠ Could not remove the worktree — that usually means uncommitted work"
    echo "    or untracked files are still in it. Look before forcing:"
    echo "      git -C ${WORKTREE_PATH} status --short"
fi

echo ""
echo "=== Integration complete ==="
git -C "${REPO_ROOT}" log --oneline -1 | sed 's/^/  /'
echo ""
echo "Remaining by hand: close the card (codebugs update CB-NN --status fixed)"
echo "and add the ledger row in .claude/plans/BUGFIX-LOOP-LEDGER.md."
