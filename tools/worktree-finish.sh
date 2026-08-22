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

# EVERY re-run hint echoes back the message the aborted run was given (CB-116).
#
# The exit-13 refusal below fires precisely BECAUSE main moved during the run —
# and main moving is the condition that used to break the derived subject. So a
# hint printing the bare short form routed the operator out of one instance of
# this defect and straight into another: the observed CB-111 merge subject was
# produced on exactly such a retry. This is orthogonal to the derivation fix and
# would be needed even if the derivation were perfect, because the operator's
# EXPLICIT message must survive a refusal that was never their fault.
#
# It preserves the `--merge-msg` and NOTHING ELSE, which is deliberate rather
# than partial: `--skip-checks` and `--allow-stale-base` are relaxations, so
# dropping them makes the retry stricter, and the positional commit message only
# applies to a worktree that is still dirty, which after a first run it is not.
# An explicit message is the one argument whose loss is silent and costly.
#
# Single-quoted rather than %q-escaped: the line exists to be copy-pasted, and
# `%q` renders every space as `\ `, which a Cyrillic-heavy message turns into
# noise. The `'\''` dance is the standard POSIX single-quote escape and it round
# -trips a message containing a quote.
_retry_hint() {
    local line="tools/worktree-finish.sh ${SLUG}"
    if [[ -n "${MERGE_MSG}" ]]; then
        line="${line} --merge-msg '${MERGE_MSG//\'/\'\\\'\'}'"
    fi
    echo "      ${line}"
}

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
    _guard_untracked_scratch_at_root "${STATUS}" || exit $?
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
        _retry_hint
        exit 1
    fi
else
    echo "  ✓ Already current"
fi

# THE INTERPRETER CHECK GOES HERE, and the phase is load-bearing (CB-135).
#
# AFTER the forward-merge: `.python-version` may have arrived in it, and a
# worktree judged before that merge is judged against a pin main no longer has.
# BEFORE [6/7]: the whole claim is that a suite run under the wrong interpreter
# proves nothing about main, so paying ~70s of pytest before saying so would
# make the refusal cost more than the run it invalidates.
# OUTSIDE the --skip-checks branch: that flag skips ruff and pytest, which are
# CHECKS. This is a safety guard, and the script has never let that flag reach
# one.
_guard_interpreter_matches_main "${WORKTREE_PATH}" "${REPO_ROOT}" || exit $?

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
# THE INTEGRATION MESSAGE, derived from the commits MAIN DOES NOT HAVE (CB-116).
#
# CLAUDE.md specifies "Merge <branch>: <what changed> (CB-NN)", and calls the
# merge commit what makes a card's whole iteration recoverable as one unit. The
# derivation used to read `log -1 --no-merges` on the worktree tip — but [5/7]
# above has just merged MAIN into that tip, so the newest non-merge commit there
# is main's whenever main moved during the branch's life. Landing CB-111
# produced exactly that: a merge closing CB-111 whose subject was an unrelated
# plan note naming CB-113/114/115. A derivation whose input the same script
# rewrote two steps earlier is not a defaulting convenience, it is a guess about
# a value it destroyed.
#
# `${TESTED_MAIN}..${TESTED_HEAD}` is exactly what this merge is about to ADD to
# main: after the forward-merge main is an ancestor of HEAD, so the range is the
# whole delta and nothing main already has can appear in it. Note the defect was
# never topological — it was `git log`'s reverse-CHRONOLOGICAL order picking
# main's newer timestamp — but restricting the RANGE alone does not make the
# ordering irrelevant, and an earlier draft of this comment claimed it did.
#
# --first-parent is what makes the pick the branch's OWN line, and it is not
# decoration. Measured (both reviewers reproduced it independently, and so did
# I): a branch that merges a SIBLING branch whose commits are older puts the
# sibling's commit first in date order, so the plain range picks it — the CB-116
# symptom arriving through a second door, and on that shape the range-only fix
# is WORSE than the code it replaces. Following first parents skips every
# absorbed lineage, including the forward-merge of main itself.
#
# --reverse takes the FIRST commit of that line, not the last. Measured over
# main's own first-parent line: of the 47 integration merges whose branch
# carried two or more commits, the first commit's subject was judged closer to
# the message a human actually wrote in 38 and the last in 7 (that split is a
# JUDGEMENT and does not partition the 47 — two were not classified either way;
# the 47 itself reproduces mechanically). Five of the 47 open with the extinct
# `wip(cb-NN): checkpoint before …` form. Branches here end on review fixups
# ("close the altitude findings", "record the round-2 outcomes"), which describe
# the iteration's tail rather than its subject. Do NOT collapse this to
# `--reverse -1`: git applies the count BEFORE reversing, so that yields the
# NEWEST commit and silently reinstates the last-commit behaviour.
#
# KNOWN LIMIT, stated rather than guessed at: `worktree-setup.sh <branch> [base]`
# can cut a branch from a NON-MAIN base, and that base's commits are on this
# branch's own first-parent line, so the derivation names the base's first commit.
# No ordering flag reaches that case (measured — --first-parent and --topo-order
# both pick the base commit), because it is not a traversal question: the commits
# really are this branch's ancestry and this merge really does land them. Pass
# --merge-msg on a branch cut from a non-main base.
#
# Derived HERE rather than at the merge below so the empty-population refusal is
# free — under the lock it would cost the whole ~70s gate run first. It cannot
# drift from what is landed: both inputs are the pinned TESTED_* values, and the
# in-lock re-checks refuse with exit 13 if either has moved.
if [[ -n "${MERGE_MSG}" ]]; then
    INTEGRATION_MSG="${MERGE_MSG}"
else
    _own_subjects=$(git -C "${WORKTREE_PATH}" log --first-parent --reverse --no-merges \
        --format=%s "${TESTED_MAIN}..${TESTED_HEAD}" 2>/dev/null || true)
    # The first NON-EMPTY subject, not the first line. `git commit
    # --allow-empty-message` puts an empty line at the head of the population,
    # and testing the first line instead of the population made the refusal
    # below print "carries no commit of its own" about a branch that carries
    # several — a false refusal that also states something untrue about the
    # repository, which is worse than the refusal itself.
    _subject=$(printf '%s\n' "${_own_subjects}" | grep -m1 -v '^[[:space:]]*$' || true)
    if [[ -z "${_subject}" ]]; then
        echo ""
        echo "  ✗ ${BRANCH} carries no commit of its own, with a subject, that main"
        echo "    does not already have — so there is no subject to derive that"
        echo "    would be true."
        echo "    (_guard_nonempty_diff passed, so the CONTENT is real — it arrived"
        echo "     through a merge commit rather than through a commit of this"
        echo "     branch's own first-parent line, or through empty subjects.)"
        echo "    Say what changed explicitly:"
        echo "      tools/worktree-finish.sh ${SLUG} --merge-msg 'Merge ${BRANCH}: … (CB-NN)'"
        exit 1
    fi
    _ids=$(printf '%s' "${BRANCH}" | grep -oiE 'cb-?[0-9]+' \
        | tr '[:upper:]' '[:lower:]' | sed -E 's/^cb-?/CB-/' | sort -u | paste -sd, - || true)
    INTEGRATION_MSG="Merge ${BRANCH}: ${_subject}"
    [[ -n "${_ids}" ]] && INTEGRATION_MSG="${INTEGRATION_MSG} (${_ids})"
fi

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
# The interpreter check above was a PRE-check, and a pre-check is not an
# invariant at landing time (cross-model review, 2026-08-22). main's `.venv` is
# gitignored, so `_guard_main_clean` cannot see it change and the in-lock SHA
# re-checks are about commits; a `UV_PYTHON=… uv sync` in main during the ~90s
# suite run would land work tested on one interpreter onto a main that now has
# another. That is the same shape the TESTED_MAIN skew guard exists for, so it
# gets the same answer: re-assert it here, where nothing else can intervene
# before the merge. Re-probing costs ~100ms and cannot drift from the value it
# is checking, which is why this is a second CALL rather than a stored sample.
_guard_interpreter_matches_main "${WORKTREE_PATH}" "${REPO_ROOT}" || exit $?
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
    _retry_hint
    flock -u 9
    exit 13
fi

# Same argument for the branch: merge the SHA that was tested, not whatever the
# name points at now (a concurrent session in that worktree can commit).
CURRENT_HEAD=$(git -C "${WORKTREE_PATH}" rev-parse HEAD)
if [[ "${CURRENT_HEAD}" != "${TESTED_HEAD}" ]]; then
    echo "  ✗ ${BRANCH} moved while this finish was running:"
    echo "      tested: ${TESTED_HEAD:0:9}   now: ${CURRENT_HEAD:0:9}"
    echo "    Re-run to test what is actually there:"
    _retry_hint
    flock -u 9
    exit 13
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
if git -C "${REPO_ROOT}" merge "${BRANCH}" --no-ff -m "${INTEGRATION_MSG}"; then
    echo "  ✓ Merged: ${INTEGRATION_MSG}"
else
    echo "  ✗ Merge failed (conflict, or the pre-merge-commit hook refused it)."
    echo "    Resolve IN THE WORKTREE, never on main:"
    echo "      cd ${REPO_ROOT} && git merge --abort"
    echo "      cd ${WORKTREE_PATH} && git merge ${CURRENT_MAIN}"
    echo "      # resolve, git add <files> && git commit"
    _retry_hint
    flock -u 9
    exit 1
fi

# ---------------------------------------------------------------------------
# THE POST-MERGE ALARM (CB-121). AN ALARM, NOT A GATE — and the difference is
# the whole design, not a caveat.
#
# THE DEFECT IT COVERS. The two re-checks above are a CHECK-THEN-ACT. They prove
# main and the branch were still the tested ones AT THE MOMENT OF THE CHECK;
# the merge two statements later resolves BOTH refs again, by NAME, for itself.
# Nothing carries a verified SHA into it and porcelain git has no
# `--expect-old-oid`, so the window is real. The flock serializes FINISHES
# against each other and nothing else, while this repo's ratified cascade
# convention has level-(2) sessions committing plan notes to main continuously
# — and since 2026-08-22 `tools/cascade-mint.sh` does it automatically, holding
# a DIFFERENT lock. So the traffic that opens this window is ordinary,
# sanctioned work, not a hypothetical adversary.
#
# WHY IT IS NOT A GATE, AND MAY NEVER BECOME ONE. By the time anything here can
# look, THE MERGE HAS LANDED. Re-running the finish after a landed merge is a
# worse outcome than the defect being reported — the same asymmetry the claims
# adaptation records, where a refusal at setup is free and a refusal after the
# merge is not. So the script completes ALL of its cleanup (worktree removal,
# claim release), and only at the very end prints the block and exits 15.
# 15 means "LANDED, and the premise could not be confirmed". 13 means the exact
# opposite — "nothing landed, re-run" — and reusing it would put a new lie in
# place of the old one. For the same reason the block below never prints
# `_retry_hint`.
#
# READ BEFORE `flock -u 9`, deliberately. After the unlock another finish can
# take the lock and move main, and the alarm would then be lying about main in
# precisely the way it exists to catch.
#
# BOTH PARENTS, not just the first. The premise being checked is "the tested
# state is the landed state", and what landed is a MERGE: TESTED_MAIN is its
# first parent, TESTED_HEAD its second. CB-121 names both halves (the branch
# side is narrower — it needs the owning session to commit mid-finish — but it
# is the identical check-then-act). Reading one and asserting the premise would
# be the "described better than it behaves" shape this whole harness records.
#
# ONE `git rev-parse`, and the parents are read from the TIP THIS RUN OBSERVED:
# once a commit is named, its parents are immutable, so no second window opens
# inside the alarm itself.
#
# FAIL-CLOSED. A non-zero rc, a missing line, or a value that is not an object
# name means "could not look" — never "clean". An ERROR and an EMPTY answer are
# distinguished, because collapsing those is the defect this repository has now
# paid for at the bootstrap gate, at `MERGE_HEAD` and at
# `_guard_conflict_markers`.
#
# STDOUT ONLY. Folding stderr in would put any `warning:` git felt like emitting
# into the line count and turn an ordinary finish into a false alarm — a gate
# firing because it could not parse is the mirror of one reporting clean
# because it could not look. The rc still distinguishes error from empty, and
# the block prints the command so the operator can see git's own words.
#
# `mapfile` rather than `while read`: `<<<` always terminates its line, but
# `read` returns non-zero on an unterminated last line regardless — the exact
# hazard that made `MERGE_HEAD` readable as "nothing to check" in the
# pre-commit hook. The count below is this loop's equivalent of that fix.
_ALARM_WHY=""
_ALARM_TIP=""
_ALARM_P1=""
_ALARM_P2=""
_ALARM_RC=0
_ALARM_CMD="git -C ${REPO_ROOT} rev-parse 'main^{commit}' 'main^1^{commit}' 'main^2^{commit}'"
_ALARM_RAW=$(git -C "${REPO_ROOT}" rev-parse \
    "main^{commit}" "main^1^{commit}" "main^2^{commit}" 2>/dev/null) || _ALARM_RC=$?
if (( _ALARM_RC != 0 )); then
    _ALARM_WHY="unreadable"
else
    mapfile -t _ALARM_LINES <<< "${_ALARM_RAW}"
    if (( ${#_ALARM_LINES[@]} != 3 )); then
        _ALARM_WHY="unreadable"
    else
        _ALARM_TIP="${_ALARM_LINES[0]}"
        _ALARM_P1="${_ALARM_LINES[1]}"
        _ALARM_P2="${_ALARM_LINES[2]}"
        # `git rev-parse` echoes an argument it does not recognise back at you
        # and exits 0 (CLAUDE.md records a test in this repo that was vacuous
        # for exactly that reason), so the SHAPE of each answer is checked, not
        # merely the exit code. 40 or 64 hex digits covers sha1 and sha256.
        if ! [[ "${_ALARM_TIP}" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] \
            || ! [[ "${_ALARM_P1}" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] \
            || ! [[ "${_ALARM_P2}" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]]; then
            _ALARM_WHY="unreadable"
        elif [[ "${_ALARM_P1}" != "${TESTED_MAIN}" ]]; then
            _ALARM_WHY="first-parent"
        elif [[ "${_ALARM_P2}" != "${TESTED_HEAD}" ]]; then
            _ALARM_WHY="second-parent"
        fi
    fi
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

# ---------------------------------------------------------------------------
# Release whatever this branch still holds (CB-58).
#
# GUARDED, NEVER FATAL, and that asymmetry with worktree-setup.sh is the whole
# design. The merge has already landed by this point, so a missing CLI, an
# unreachable tracker or a contended database must never turn a successful
# integration into a failure — the worst it may do is leave a claim behind and
# say so. `codebugs claim` at setup is the ONE tracker call in this harness
# allowed to be fatal, because there nothing has been created yet and a refusal
# is free.
#
# RESTORE IS LEFT ON (no --no-restore), deliberately:
#   - If the operator already closed the card, `_auto_release_on_terminal` fired
#     inside that status write and there is no live claim left — this call
#     reports `not_claimed`, exit 0, and changes nothing.
#   - If the card is still open, its status reads `in_progress` only because our
#     claim projected it there. The worktree is about to be gone, so nobody is
#     mid-flight: restoring it to `open` is the honest state, and it is exactly
#     the `in_progress`-forever leak CB-58 was filed for.
# The restore cannot resurrect finished work: it is a CAS that only writes if the
# status still holds the value the claim projected.
# ---------------------------------------------------------------------------
_finish_cb_ids=$(printf '%s' "${BRANCH}" | grep -oiE 'cb-?[0-9]+' \
    | tr '[:upper:]' '[:lower:]' | sed -E 's/^cb-?/CB-/' | sort -u || true)

if [[ -n "${_finish_cb_ids}" ]] && command -v codebugs >/dev/null 2>&1; then
    echo ""
    echo "Releasing claims held by ${BRANCH}..."
    for _cb in ${_finish_cb_ids}; do
        _rrc=0
        codebugs release "${_cb}" --holder "${BRANCH}" --holder-kind branch \
            --repo "${REPO_ROOT}" --reason "worktree-finish" >/dev/null 2>&1 || _rrc=$?
        case "${_rrc}" in
            # 0 covers both `released` and `not_claimed` — the second is the
            # normal case when the card was closed by hand first, so it is not
            # worth a warning.
            0) echo "  ✓ ${_cb} released (or was not held)" ;;
            3) echo "  · ${_cb} is held by someone else — left alone." ;;
            *) echo "  ⚠ ${_cb} not released (exit ${_rrc}). The merge LANDED; this is"
               echo "    tracker bookkeeping only. Retry:"
               echo "      codebugs release ${_cb} --holder ${BRANCH} \\"
               echo "          --holder-kind branch --repo ${REPO_ROOT}" ;;
        esac
    done
elif [[ -n "${_finish_cb_ids}" ]]; then
    echo ""
    echo "  ⚠ codebugs CLI not on PATH — ${_finish_cb_ids//$'\n'/ } still claimed."
fi

echo ""
echo "=== Integration complete ==="
git -C "${REPO_ROOT}" log --oneline -1 | sed 's/^/  /'
echo ""
echo "Remaining by hand: close the card (codebugs update CB-NN --status fixed)"
echo "and add the ledger row in .claude/plans/BUGFIX-LOOP-LEDGER.md."

# ---------------------------------------------------------------------------
# The alarm SPEAKS here, last, after every cleanup step has run (CB-121). See
# the long comment beside the read, above the unlock: the merge has landed, so
# this is a report on a completed integration and never an instruction to try
# again.
if [[ -n "${_ALARM_WHY}" ]]; then
    echo ""
    echo "================================================================"
    echo "  !!  POST-MERGE ALARM  —  THE MERGE ALREADY LANDED  !!"
    echo "================================================================"
    case "${_ALARM_WHY}" in
        unreadable)
            echo "  Could not read the parents of main after the merge, so this run"
            echo "  CANNOT say the tested state is the landed state. This is"
            echo "  'could not look', not 'clean'."
            echo "      rc=${_ALARM_RC}, stdout: ${_ALARM_RAW//$'\n'/ | }"
            echo "      run it yourself to see git's own words:"
            echo "      ${_ALARM_CMD}"
            ;;
        first-parent)
            echo "  main's FIRST PARENT is not the main this branch was tested"
            echo "  against. Something landed on main between the in-lock re-check"
            echo "  and the merge — a plan note, a cascade mint, a hand commit."
            echo "      tested against (TESTED_MAIN): ${TESTED_MAIN}"
            echo "      landed first parent:          ${_ALARM_P1}"
            echo "      landed merge commit:          ${_ALARM_TIP}"
            ;;
        second-parent)
            echo "  The merge's SECOND PARENT is not the branch head that was"
            echo "  tested: the branch moved while this finish was running, and"
            echo "  the merge resolved the NAME, not the tested SHA."
            echo "      tested (TESTED_HEAD):  ${TESTED_HEAD}"
            echo "      landed second parent:  ${_ALARM_P2}"
            echo "      landed merge commit:   ${_ALARM_TIP}"
            ;;
    esac
    echo ""
    echo "  DO NOT RE-RUN tools/worktree-finish.sh. Exit 15 means LANDED with an"
    echo "  UNCONFIRMED premise; it is not exit 13, which means nothing landed."
    echo "  The worktree is removed and the claim released — this run is over."
    echo "  Inspect and decide by hand:"
    echo "      git -C ${REPO_ROOT} log --graph --oneline -6 main"
    echo "      git -C ${REPO_ROOT} show --stat ${_ALARM_TIP:-main}"
    echo "  The tree that landed was tested against ${TESTED_MAIN:0:9}; if what"
    echo "  arrived in the window interacts with it, fix it forward on a NEW"
    echo "  branch. Do not rewrite main."
    echo "================================================================"
    exit 15
fi
