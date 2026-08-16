#!/usr/bin/env bash
# Worktree Setup — tools/worktree-setup.sh <branch-name> [base-branch]
#
# Creates the worktree CLAUDE.md's workflow requires, validates the branch name,
# refuses a card already being built on another branch, primes the worktree's
# own dev environment, and claims the card in codebugs.
#
# Ported from ../autosorter/tools/worktree-setup.sh (2026-08-16). Two deliberate
# divergences, both load-bearing — see [2/4] (venv) and the branch-type guard.
#
# Example: tools/worktree-setup.sh fix/cb-50-worktree-harness main

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/_guards.sh
source "${_SCRIPT_DIR}/_guards.sh"

REPO_ROOT="$(_guards_resolve_repo_root "${_SCRIPT_DIR}")"
WORKTREE_DIR="${REPO_ROOT}/.worktrees"

ALLOW_DUPLICATE=0
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --allow-duplicate) ALLOW_DUPLICATE=1 ;;
        *) POSITIONAL+=("$arg") ;;
    esac
done
set -- "${POSITIONAL[@]}"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <branch-name> [base-branch] [--allow-duplicate]"
    echo "  branch-name:       fix/… | feature/… | refactor/… | docs/…"
    echo "  base-branch:       branch to base from (default: main)"
    echo "  --allow-duplicate: proceed even if another branch carries this CB id"
    exit 1
fi

BRANCH_NAME="$1"
BASE_BRANCH="${2:-main}"
SLUG="${BRANCH_NAME//\//-}"
WORKTREE_PATH="${WORKTREE_DIR}/${SLUG}"

echo "=== Worktree Setup ==="
echo "  Branch: ${BRANCH_NAME}"
echo "  Base:   ${BASE_BRANCH}"
echo "  Path:   ${WORKTREE_PATH}"
echo ""

# Refuse an off-convention name BEFORE creating anything, so a trip costs
# nothing and leaves no half-made worktree behind.
_guard_branch_type "${BRANCH_NAME}" || exit $?

if [[ -d "${WORKTREE_PATH}" ]]; then
    echo "ERROR: worktree already exists at ${WORKTREE_PATH}"
    echo "  To remove: git worktree remove ${WORKTREE_PATH}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Collision guard. The path check above matches an exact directory, so two
# different slugs for the SAME card sail past it.
#
# `[0-9]{3,}` deliberately uncapped: a capped run TRUNCATES a longer id, so
# cb-1234 would be read as CB-123 and collide with an unrelated card. Take the
# whole digit run and compare equals; the boundary check below stops a bare
# number match from firing on a longer id or on a hex fragment.
# ---------------------------------------------------------------------------
CB_IDS=$(printf '%s' "${BRANCH_NAME}" \
    | grep -oiE 'cb-?[0-9]{1,}' \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/^cb-?/CB-/' \
    | sort -u || true)

_claim_ids=""
for cb in ${CB_IDS}; do
    num="${cb#CB-}"
    others=$(git -C "${REPO_ROOT}" branch --format='%(refname:short)' \
        | grep -iE "cb-?${num}([^0-9]|$)" \
        | grep -vx "${BRANCH_NAME}" || true)

    if [[ -n "${others}" ]]; then
        if [[ "${ALLOW_DUPLICATE}" == "1" ]]; then
            echo "⚠ ${cb} is already carried by another branch (proceeding, --allow-duplicate):"
            echo "${others}" | sed 's/^/    /'
            echo ""
        else
            echo "ERROR: ${cb} is already being worked on another branch:"
            echo "${others}" | sed 's/^/    /'
            echo ""
            echo "  Two sessions building one card duplicates the work and makes one"
            echo "  of the two merges a conflict nobody planned. Check that branch first."
            echo "  Note this repo NEVER deletes merged branches, so an old branch for a"
            echo "  closed card will also match — confirm, then --allow-duplicate."
            exit 1
        fi
    fi

    # Registry check. Best-effort: a machine without the codebugs CLI still gets
    # the pure-git guard above. CODEBUGS_SETUP_NO_CLAIM lets the tests exercise
    # the guard without writing to the real findings database.
    if command -v codebugs >/dev/null 2>&1 && [[ -z "${CODEBUGS_SETUP_NO_CLAIM:-}" ]]; then
        status=$(codebugs get "${cb}" 2>/dev/null \
            | sed -nE 's/^[[:space:]]*"status":[[:space:]]*"([^"]+)".*/\1/p' | head -1 || true)
        case "${status}" in
            open) _claim_ids="${_claim_ids} ${cb}" ;;
            in_progress)
                # A warning, not a refusal: a stale in_progress claim is common
                # enough that refusing here would train people to reach for
                # --allow-duplicate reflexively, which would blunt the branch
                # check above — the one with teeth.
                echo "⚠ ${cb} already reads in_progress in codebugs."
                echo "  No branch carries it, so this may be stale — confirm nobody"
                echo "  is mid-flight before continuing."
                echo ""
                ;;
            "") : ;;
            *) echo "  note: ${cb} currently reads '${status}' in codebugs." ;;
        esac
    fi
done

mkdir -p "${WORKTREE_DIR}"

echo "[1/4] Creating worktree..."
git -C "${REPO_ROOT}" worktree add -b "${BRANCH_NAME}" "${WORKTREE_PATH}" "${BASE_BRANCH}"

# ---------------------------------------------------------------------------
# [2/4] DIVERGENCE FROM AUTOSORTER, and the reason is in CLAUDE.md.
#
# autosorter symlinks the root .venv into each worktree, because its dependency
# tree is heavy and its editable install is not what tests resolve through.
# Doing that HERE would destroy the isolation this repo's workflow depends on:
# `uv run` builds the worktree's OWN editable install pointing at the worktree,
# which is what makes a worktree's test run actually test the worktree.
#
# What we do instead is close the trap CLAUDE.md documents: pytest and ruff live
# in project.optional-dependencies, which `uv run` does not install by default,
# so a fresh worktree dies with "No module named pytest" while main — synced
# long ago — works without the flag. Priming here means the first test command
# in the worktree works whether or not the operator remembers `--extra dev`.
# ---------------------------------------------------------------------------
echo "[2/4] Priming the worktree's own dev environment (uv sync --extra dev)..."
if command -v uv >/dev/null 2>&1; then
    if (cd "${WORKTREE_PATH}" && uv sync --extra dev >/dev/null 2>&1); then
        echo "  ✓ .venv built in the worktree with the dev extra"
    else
        echo "  ⚠ uv sync failed — run 'uv sync --extra dev' in the worktree by hand"
    fi
else
    echo "  ⚠ uv not found — the worktree has no environment yet"
fi

echo "[3/4] Verifying..."
ACTUAL_HEAD=$(git -C "${WORKTREE_PATH}" rev-parse HEAD)
EXPECTED_HEAD=$(git -C "${REPO_ROOT}" rev-parse "${BASE_BRANCH}")
if [[ "${ACTUAL_HEAD}" == "${EXPECTED_HEAD}" ]]; then
    echo "  ✓ HEAD matches base (${ACTUAL_HEAD:0:8})"
else
    echo "  ⚠ HEAD mismatch: ${ACTUAL_HEAD:0:8} vs expected ${EXPECTED_HEAD:0:8}"
fi

# Prove `import codebugs` resolves to THIS worktree, not main's editable
# install. CLAUDE.md calls this out twice as a trap in both directions, so it is
# worth one subprocess to know rather than assume.
IMPORT_PATH=$( (cd "${WORKTREE_PATH}" && uv run --extra dev python -c \
    "import codebugs, sys; sys.stdout.write(codebugs.__file__)" 2>/dev/null) || true)
if [[ "${IMPORT_PATH}" == "${WORKTREE_PATH}/src/"* ]]; then
    echo "  ✓ 'import codebugs' resolves to the worktree's src/"
else
    echo "  ⚠ import resolves to: ${IMPORT_PATH:-<failed>}"
    echo "    (expected ${WORKTREE_PATH}/src/... — tests here may be testing main)"
fi

# ---------------------------------------------------------------------------
# [4/4] Claim the card. Until now LOCK depended on a session remembering to do
# it, and a status field nobody reads stops nobody. Creating the worktree IS the
# claim, which makes the registry authoritative by construction.
#
# Only `open` cards are flipped (decided in the guard loop above): a follow-up
# branch on a `fixed` card must never silently reopen it. No --notes — that
# field is a whole-value overwrite and would destroy the card's existing notes.
# ---------------------------------------------------------------------------
echo "[4/4] Claiming cards..."
if [[ -z "${_claim_ids}" ]]; then
    # Distinguish "nothing to do" from "could not look". They printed the same
    # line before, so a machine without the CLI silently reported the same
    # success as a branch carrying no card id — a status write nobody made,
    # indistinguishable from one nobody needed (cross-model review).
    if [[ -n "${CB_IDS}" ]] && ! command -v codebugs >/dev/null 2>&1; then
        echo "  ⚠ codebugs CLI not on PATH — ${CB_IDS//$'\n'/ } NOT claimed."
        echo "    The branch-name collision guard above still ran; it is pure git."
    elif [[ -n "${CODEBUGS_SETUP_NO_CLAIM:-}" ]]; then
        echo "  (claiming disabled by CODEBUGS_SETUP_NO_CLAIM)"
    else
        echo "  (nothing to claim)"
    fi
fi
for cb in ${_claim_ids}; do
    if codebugs update "${cb}" --status in_progress >/dev/null 2>&1; then
        echo "  ✓ ${cb} → in_progress (claimed by ${BRANCH_NAME})"
    else
        echo "  ⚠ ${cb} → could not claim; mark it in_progress by hand"
    fi
done

echo ""
echo "=== Worktree ready ==="
echo "  cd ${WORKTREE_PATH}"
echo ""
echo "Tests and lint run IN THE WORKTREE, and '--extra dev' is not optional:"
echo "  uv run --extra dev python -m pytest tests/ -q"
echo "  uv run --extra dev ruff check src/ tests/"
echo ""
echo "Never validate this worktree's changes by running the suite from main —"
echo "pythonpath=[\"src\"] resolves against the checkout you run in, so that"
echo "would test main's source and pass on a tree you did not touch."
echo ""
echo "When done:  tools/worktree-finish.sh ${SLUG} 'msg' --merge-msg '…(CB-NN)'"
