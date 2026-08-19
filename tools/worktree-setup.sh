#!/usr/bin/env bash
# Worktree Setup — tools/worktree-setup.sh <branch-name> [base-branch]
#
# Creates the worktree CLAUDE.md's workflow requires, validates the branch name,
# refuses a card already being built on another branch, primes the worktree's
# own dev environment, and claims the card in codebugs.
#
# Ported from ../autosorter/tools/worktree-setup.sh (2026-08-16). Two deliberate
# divergences, both load-bearing — see [3/5] (venv) and the branch-type guard.
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

    # The registry pre-check that used to live here is GONE (CB-58). It read the
    # card's status and admitted only `open` ones to the claim list — a second
    # gate answering the same question as `codebugs claim` itself, from a
    # scraped status field rather than from the ledger. Two gates that can
    # disagree is this repo's shared-predicate lesson; the claim's own outcomes
    # (held_by_other / entity_terminal) report strictly more, so the exit-code
    # handling in [1/5] below is now the single reader of that question.
    :
done

# ---------------------------------------------------------------------------
# [1/5] Claim the cards — BEFORE anything is created (CB-58).
#
# This used to be step [4/4], a loop of `codebugs update --status in_progress`,
# and the comment above it claimed "creating the worktree IS the claim, which
# makes the registry authoritative by construction". It was neither. A status
# flip carries no holder, so nothing could say WHO was building the card; it
# has no release path, so an abandoned branch left the card `in_progress`
# forever; and it offers no exclusion, so two setups could build one card past
# the tracker — only the pure-git branch guard above had teeth.
#
# `codebugs claim` supplies all three: the holder triple names us, mutual
# exclusion is a partial unique index (a database guarantee, not discipline),
# and `release` exists. The status flip is not lost — it comes free as the
# claim's PROJECTION (EntityKind.busy_status), so the card still reads
# `in_progress` while we hold it, and closing the card auto-releases the claim
# in the same transaction.
#
# ORDER IS THE POINT. Claiming after `git worktree add` would mean the losing
# side of a race had already created a branch and a directory before being told
# no. Claiming first makes the refusal free, which is the same reason
# _guard_branch_type runs before anything is created.
#
# WHAT THIS DOES NOT DO, stated because the honest scope matters: a branch
# abandoned AFTER a successful setup still leaves a live claim. Steal and expiry
# are deliberately deferred (CLAUDE.md, Claims module → deferred by design). That
# is strictly better than the anonymous `in_progress` it replaces — the claim
# names holder and repo, `codebugs who-holds` reports it, and any close releases
# it — but it is not the same as the claim disappearing.
# ---------------------------------------------------------------------------
_claimed_ids=""

# EXIT trap: release everything this run took, if this run does not finish.
# Armed only after the first successful claim, disarmed on success. Without it
# an abort between the claim and a ready worktree leaks a claim that names a
# branch which does not exist — worse than the leak it replaces, because it
# looks authoritative.
_release_claims_on_abort() {
    local rc=$?
    [[ -z "${_claimed_ids}" ]] && return "${rc}"
    echo "" >&2
    echo "Setup did not complete — releasing the claim(s) it took:" >&2
    for _cb in ${_claimed_ids}; do
        if codebugs release "${_cb}" --holder "${BRANCH_NAME}" --holder-kind branch \
            --repo "${REPO_ROOT}" --reason "worktree-setup aborted" >/dev/null 2>&1; then
            echo "  ✓ ${_cb} released" >&2
        else
            # Guarded: a failed cleanup must not mask the real abort. Print the
            # exact command instead, so the leak is recoverable by hand.
            echo "  ⚠ ${_cb} NOT released — run:" >&2
            echo "      codebugs release ${_cb} --holder ${BRANCH_NAME} \\" >&2
            echo "          --holder-kind branch --repo ${REPO_ROOT}" >&2
        fi
    done
    return "${rc}"
}

echo "[1/5] Claiming cards..."
if [[ -z "${CB_IDS}" ]]; then
    echo "  (branch names no card — nothing to claim)"
elif ! command -v codebugs >/dev/null 2>&1; then
    # Distinguish "nothing to do" from "could not look" — they printed the same
    # line once, so a machine without the CLI reported the same success as a
    # branch carrying no card id (cross-model review).
    echo "  ⚠ codebugs CLI not on PATH — ${CB_IDS//$'\n'/ } NOT claimed."
    echo "    The branch-name collision guard above still ran; it is pure git."
elif [[ -n "${CODEBUGS_SETUP_NO_CLAIM:-}" ]]; then
    echo "  (claiming disabled by CODEBUGS_SETUP_NO_CLAIM)"
else
    for cb in ${CB_IDS}; do
        # Exit codes ARE the API here (CLAUDE.md, Claims module): 0 proceed,
        # 3 held by someone else, 4 already resolved, 5 too contended to tell.
        _rc=0
        codebugs claim "${cb}" --holder "${BRANCH_NAME}" --holder-kind branch \
            --repo "${REPO_ROOT}" --note "worktree-setup" >/dev/null 2>&1 || _rc=$?

        if [[ "${_rc}" -eq 5 ]]; then
            # `undetermined` means the database was too contended to answer, not
            # that the claim failed. The primitive is an idempotent upsert, so
            # re-issuing the IDENTICAL call converges on already_mine and can
            # never double-claim. One retry, then degrade.
            #
            # SLEEP FIRST — FINAL-DESIGN.md §6.2(a) has it and my first draft did
            # not. An immediate retry is the one most likely to meet the SAME
            # contention: `undetermined` means another connection holds the write
            # lock, and nothing has changed a microsecond later. Retrying without
            # waiting makes the retry mostly decorative.
            sleep 1
            _rc=0
            codebugs claim "${cb}" --holder "${BRANCH_NAME}" --holder-kind branch \
                --repo "${REPO_ROOT}" --note "worktree-setup" >/dev/null 2>&1 || _rc=$?
        fi

        case "${_rc}" in
            0)
                _claimed_ids="${_claimed_ids} ${cb}"
                trap _release_claims_on_abort EXIT
                echo "  ✓ ${cb} claimed by ${BRANCH_NAME} (status projected to in_progress)"
                ;;
            3)
                # THE SETUP GATE — the one tracker call in this harness that may
                # be fatal. Everything else degrades, because everything else
                # runs where a false refusal costs more than a missed write.
                #
                # THE ESCAPE HATCH IS RATIFIED (owner, 2026-08-19) AND DIVERGES
                # FROM FINAL-DESIGN.md §6.2(a) ON PURPOSE. That doc clears both
                # `3` and `4` with `--allow-duplicate`. Here it must not, and the
                # reason is local: `--allow-duplicate` clears the pure-git guard
                # above, which fires whenever another branch carries this id —
                # and this repo NEVER deletes merged branches (:93-94), so the
                # flag is needed for ORDINARY follow-up work. One flag for both
                # jobs would mean an operator typing it for the routine reason
                # silently punches through a LIVE concurrent claim, i.e. the gate
                # would be off exactly when people are doing normal work.
                # CODEBUGS_SETUP_NO_CLAIM is the deliberate, typed alternative:
                # it builds with NO claim rather than stealing someone else's.
                echo "" >&2
                echo "ERROR: ${cb} is already claimed by someone else:" >&2
                codebugs who-holds "${cb}" 2>/dev/null | sed 's/^/    /' >&2
                echo "" >&2
                echo "  Two sessions building one card duplicates the work. Either have" >&2
                echo "  the holder release it (the holder triple above is what release" >&2
                echo "  authorizes on):" >&2
                echo "      codebugs release ${cb} --holder <holder> \\" >&2
                echo "          --holder-kind <kind> --repo <repo>" >&2
                echo "" >&2
                echo "  or, to build WITHOUT holding the card at all:" >&2
                echo "      CODEBUGS_SETUP_NO_CLAIM=1 $0 ${BRANCH_NAME}" >&2
                echo "" >&2
                echo "  Note --allow-duplicate deliberately does NOT punch through this." >&2
                echo "  It answers a different question (another BRANCH carries the id)," >&2
                echo "  and since this repo never deletes merged branches it is needed" >&2
                echo "  for ordinary follow-up work — overloading it would make the" >&2
                echo "  claim gate routinely bypassed." >&2
                exit 3
                ;;
            4)
                # entity_terminal: the card is already resolved. A warning, not a
                # refusal — a follow-up branch on a fixed card is legitimate, and
                # claiming would be the thing that wrongly reopened it.
                echo "  ⚠ ${cb} is already resolved — building without a claim."
                echo "    A follow-up branch on a closed card is fine; nothing was"
                echo "    reopened. Use --allow-terminal by hand if you really want it."
                ;;
            5)
                echo "  ⚠ ${cb} — tracker too contended to claim, twice. Proceeding"
                echo "    UNCLAIMED. The branch-name collision guard above still ran."
                ;;
            *)
                echo "  ⚠ ${cb} — claim failed (exit ${_rc}). Proceeding UNCLAIMED."
                ;;
        esac
    done
fi

# The container directory is created HERE, not before the claim: a refused
# setup must leave nothing at all behind, and that includes .worktrees/.
mkdir -p "${WORKTREE_DIR}"

echo "[2/5] Creating worktree..."
git -C "${REPO_ROOT}" worktree add -b "${BRANCH_NAME}" "${WORKTREE_PATH}" "${BASE_BRANCH}"

# ---------------------------------------------------------------------------
# DISARM HERE — the instant the worktree exists, and not one step later.
#
# The disarm point is RATIFIED, not a matter of taste:
# docs/superpowers/plans/design-council-entity-claims/FINAL-DESIGN.md §6.2(d)
# places it immediately after `git worktree add`, and §6.4 states the reason —
# a trap armed to the END OF THE SCRIPT "releases ownership while a real
# worktree sits on disk", which is the WORSE failure. This script's first draft
# disarmed at [5/5] and was exactly that rejected alternative: the verify step
# below assigns from an unguarded `$(git ... rev-parse HEAD)`, so under `set -e`
# a failure there would abort with the worktree on disk AND hand the card back.
#
# The residual is deliberate and documented in §6.4: an abort between here and
# the end leaves the claim held. That is correct — the branch genuinely exists,
# so the claim is not lying, and `who-holds` plus one `release` recovers it.
# ---------------------------------------------------------------------------
trap - EXIT

# ---------------------------------------------------------------------------
# [3/5] DIVERGENCE FROM AUTOSORTER, and the reason is in CLAUDE.md.
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
echo "[3/5] Priming the worktree's own dev environment (uv sync --extra dev)..."
if command -v uv >/dev/null 2>&1; then
    if (cd "${WORKTREE_PATH}" && uv sync --extra dev >/dev/null 2>&1); then
        echo "  ✓ .venv built in the worktree with the dev extra"
    else
        echo "  ⚠ uv sync failed — run 'uv sync --extra dev' in the worktree by hand"
    fi
else
    echo "  ⚠ uv not found — the worktree has no environment yet"
fi

echo "[4/5] Verifying..."
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
# [5/5] Report what is held. The trap was disarmed back at [2/5], the moment the
# worktree existed — see the rationale there; it is a ratified design point, not
# a placement choice.
# ---------------------------------------------------------------------------
echo "[5/5] Handing over..."
if [[ -n "${_claimed_ids}" ]]; then
    echo "  Holding:${_claimed_ids} — released automatically when the card is closed,"
    echo "  or by hand: codebugs release <CB-N> --holder ${BRANCH_NAME} \\"
    echo "      --holder-kind branch --repo ${REPO_ROOT}"
else
    echo "  No claim held by this worktree."
fi

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
