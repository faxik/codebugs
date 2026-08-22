#!/usr/bin/env bash
# tools/cascade-mint.sh — mint the next cascade ID into
# .claude/plans/CASCADE-IDS.md as ONE indivisible operation, under a lock.
#
# WHY THIS EXISTS. The registry is an append-only allocator whose header
# promised that a concurrent mint yields a merge conflict. That promise CANNOT
# be kept: both directions commit plan notes DIRECTLY on main, sequentially, and
# sequential direct commits never merge — so the conflict mechanism cannot fire
# at all. Three collisions followed, each with a NARROWER cause than the last:
#
#   #1 (T-8)  both sides appended at EOF; no merge happened, so no conflict.
#   #2 (T-21) the tail was read after `git pull`, but READ and APPEND are two
#             actions and another session's commit landed between them. Both
#             sessions share ONE checkout, so `git pull` is powerless there.
#   #3 (T-24) read and append were done in ONE move — the convention satisfied
#             by the letter AND by the mechanics — and the collision happened
#             anyway, because the NUMBER ITSELF had been typed by the author as
#             a literal minutes earlier. What was protected was the READING,
#             not the COMPUTATION.
#
# So the number is COMPUTED here, from the registry, inside the same lock that
# guards the append and the commit. The author never types a number.
#
# WHAT THIS DOES NOT CLOSE — said out loud, because a gate described better than
# it behaves is the failure this repository keeps paying for. The lock is a
# `flock` on a file under `.worktrees/`, so it serialises processes that share
# ONE working tree. TWO DIFFERENT CHECKOUTS (separate clones) racing, with no
# `git pull` between the computation and the commit, are NOT serialised by it,
# and this script cannot detect that. The measured population of this repo is
# ONE checkout carrying every parallel session, which is exactly the population
# the lock covers; claiming it covers "everything" would repeat the overclaim
# the registry header already made.
#
# This is NOT a guard of tools/worktree-finish.sh. It is run by hand, by the
# holder of a direction, at the moment a unit is minted.

set -euo pipefail

# BYTE semantics for every pattern below. The registry's unit prefix is the
# CYRILLIC letter TE (U+0422), which is two bytes in UTF-8 and visually
# identical to ASCII 'T'. Pinning C keeps grep byte-oriented so the character
# classes below mean the 65 ASCII characters they name and nothing else, and so
# the verdict cannot depend on the LANG of whoever ran the script.
export LC_ALL=C

# U+0422 CYRILLIC CAPITAL LETTER TE, written literally because bash's $'\uXXXX'
# is rendered in the CURRENT locale's charmap and we have just pinned C.
readonly CYRILLIC_TE='Т'
readonly LATIN_TE='T'

readonly REGISTRY_REL=".claude/plans/CASCADE-IDS.md"

_die() {
    echo "ERROR: $1" >&2
    shift || true
    while [[ $# -gt 0 ]]; do
        echo "  $1" >&2
        shift
    done
    exit 1
}

usage() {
    cat <<'USAGE_EOF'
cascade-mint.sh — allocate the next cascade ID and append its registry line,
computing the number and writing it in ONE operation under a lock.

USAGE
  tools/cascade-mint.sh --prefix <PREFIX> --text '<rest of the registry line>'
  tools/cascade-mint.sh --prefix <PREFIX> --dry-run

  --prefix P   The ID family. This repository uses the CYRILLIC 'Т' (U+0422)
               for units, 'BT' for direction sub-topics and 'DIR' for
               directions. A prefix of 'Т' or 'T' READS both spellings (a Latin
               typo in the registry must not become invisible to the allocator)
               and WRITES the Cyrillic one, as the registry does.
  --text S     Everything that follows the ID on the line. The script writes
                   - <ID> — <S>
               and nothing else. Required unless --dry-run.
  --dry-run    Compute and print the ID; write nothing, commit nothing.
  -h, --help   This text.

WHAT IT PRINTS
  The allocated ID on stdout and nothing else, so it can be substituted:
      ID=$(tools/cascade-mint.sh --prefix Т --text '...')
  Progress and refusals go to stderr.

EXAMPLE
  tools/cascade-mint.sh --prefix Т \
      --text '(DIR-1) CB-137: ... — 2026-08-22, DIR-1, `L3-BRIEF-...md`'

WHAT IT REFUSES, AND WHY EACH REFUSAL IS NOT "START FROM 1"
  * The registry is missing, unreadable, or contains NO recognisable ID for the
    requested prefix. Zero found is an ERROR, never an empty allocator: this
    repository has paid three times for a guard that reported clean because it
    could not look.
  * HEAD is not `main`. The registry is committed directly on main and read
    from the working tree; a mint parked on a branch is invisible to the other
    direction until it merges, which is the collision this script exists to
    stop.
  * The registry already carries uncommitted changes. Committing them would
    sweep another session's in-flight edit into this mint.

WHAT IT COMMITS
  ONLY the registry, via a pathspec commit (`git commit -- <path>`), so a
  parallel session's staged files are neither committed nor unstaged. The cost
  of that choice, stated rather than discovered later: the hooks then judge a
  temporary index holding only the registry, so a stranger's staged file is not
  refused by THIS commit — it stays staged and is their next commit's problem.
  The alternative (refusing to mint whenever the index carries anything else)
  would make the mint hostage to another session's index, which is worse.

WHAT IT DOES NOT CLOSE
  The lock serialises processes sharing ONE working tree, which is this repo's
  measured population (all parallel sessions live in one checkout). TWO
  DIFFERENT CHECKOUTS or clones racing are NOT serialised, and this script
  cannot detect that. Nothing here makes that case safe.
USAGE_EOF
}

PREFIX=""
TEXT=""
TEXT_GIVEN=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            [[ $# -ge 2 ]] || _die "--prefix needs a value."
            PREFIX="$2"
            shift 2
            ;;
        --text)
            [[ $# -ge 2 ]] || _die "--text needs a value."
            TEXT="$2"
            TEXT_GIVEN=1
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[[ -n "${PREFIX}" ]] || _die "--prefix is required." "See --help."

# The prefix is spliced into an ERE, so anything that could mean something there
# is refused rather than escaped. A prefix is a short literal name; nothing this
# repository uses needs a metacharacter, and a silently mis-parsed prefix would
# match the wrong family and mint a number that is already taken.
case "${PREFIX}" in
    *[' .[]()*+?{}|\^$-']*)
        _die "prefix '${PREFIX}' contains a character that is not allowed here." \
             "A prefix must be a plain literal (letters/digits), because it is" \
             "spliced into a regular expression. Refusing rather than guessing."
        ;;
esac

if [[ "${DRY_RUN}" -eq 0 && "${TEXT_GIVEN}" -eq 0 ]]; then
    _die "--text is required unless --dry-run is given." "See --help."
fi
if [[ "${TEXT_GIVEN}" -eq 1 && -z "${TEXT}" ]]; then
    _die "--text is empty; refusing to append a line with no content."
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) \
    || _die "not inside a git repository."

REGISTRY="${REPO_ROOT}/${REGISTRY_REL}"
LOCK_DIR="${REPO_ROOT}/.worktrees"
LOCK_FILE="${LOCK_DIR}/.cascade-mint.lock"

# READING accepts both spellings of TE; WRITING uses the Cyrillic one, which is
# what every line of the registry already carries. A Latin typo in the registry
# is a number that has been SPENT, and an allocator that cannot see it will hand
# it out again.
if [[ "${PREFIX}" == "${CYRILLIC_TE}" || "${PREFIX}" == "${LATIN_TE}" ]]; then
    SCAN_ALT="${CYRILLIC_TE}|${LATIN_TE}"
    WRITE_PREFIX="${CYRILLIC_TE}"
else
    SCAN_ALT="${PREFIX}"
    WRITE_PREFIX="${PREFIX}"
fi

# A LEFT BOUNDARY IS LOAD-BEARING, not decoration. Without it the Latin arm of
# the TE pattern matches INSIDE 'BT-4', so the BT family would silently raise
# the unit counter — today harmless only because BT's numbers are the smaller
# ones, which is a property of the data and not of the code. A boundary is the
# start of the line or any byte that cannot be part of an ID; every byte >= 0x80
# qualifies, so the guillemets the registry uses around annulled ids ("«Т-21»")
# still let the number be seen. Occurrences cannot overlap-hide each other: a
# match ends on a digit, and a digit is never a boundary, so the boundary byte
# of the next occurrence is always outside the previous match.
readonly ID_RE="(^|[^A-Za-z0-9-])(${SCAN_ALT})-[0-9]+"

_compute_next_id() {
    # Reads ${REGISTRY}; echoes the next id. Dies on anything it cannot read.
    [[ -e "${REGISTRY}" ]] || _die "registry not found: ${REGISTRY}" \
        "Refusing rather than creating an allocator that starts from 1."
    [[ -f "${REGISTRY}" && -r "${REGISTRY}" ]] || _die \
        "registry is not a readable regular file: ${REGISTRY}"

    local raw="" rc=0
    raw=$(grep -oE "${ID_RE}" "${REGISTRY}") || rc=$?
    # grep: 0 = matched, 1 = no match, >=2 = ERROR. Those are three different
    # answers and only one of them is "the registry has no ids"; conflating an
    # error with an empty result is the shape this repo has been bitten by in
    # the bootstrap gate, in MERGE_HEAD and in _guard_conflict_markers.
    if (( rc >= 2 )); then
        _die "could not read the registry while scanning for ids (grep exit ${rc})." \
             "Refusing rather than treating an unreadable registry as an empty one."
    fi
    if [[ -z "${raw}" ]]; then
        _die "no '${PREFIX}-<number>' id found anywhere in ${REGISTRY_REL}." \
             "ZERO FOUND IS AN ERROR, NOT 'start from 1'. An allocator that" \
             "restarts at 1 collides with the whole history of the registry." \
             "Check the prefix — note that the unit prefix is the CYRILLIC" \
             "letter TE (U+0422), not the Latin 'T'."
    fi

    local max=-1 tok n
    while read -r tok; do
        [[ -z "${tok}" ]] && continue
        n="${tok##*-}"
        [[ "${n}" =~ ^[0-9]+$ ]] || continue
        n=$((10#${n}))
        if (( n > max )); then max=${n}; fi
    done <<< "${raw}"

    if (( max < 0 )); then
        _die "scanned ${REGISTRY_REL} but extracted no number." \
             "Refusing rather than starting from 1."
    fi

    # ANNULLED LINES ARE SPENT NUMBERS. max+1 over EVERY line — including the
    # collision annotations that annul a line — is deliberate: the number stayed
    # consumed even though the unit it named was re-minted.
    echo "${WRITE_PREFIX}-$((max + 1))"
}

mkdir -p "${LOCK_DIR}"
exec 9>"${LOCK_FILE}"
if ! flock -w 60 9; then
    _die "could not acquire the mint lock after 60s: ${LOCK_FILE}" \
         "Another mint may be stuck."
fi

# Everything time-dependent happens from here on, inside the lock. The
# computation is inside it too — that is the whole point of the unit: collision
# #3 happened with the READ protected and the COMPUTATION unprotected.
NEXT_ID=$(_compute_next_id)

if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "${NEXT_ID}"
    exit 0
fi

branch=$(git -C "${REPO_ROOT}" symbolic-ref --short -q HEAD || echo "")
if [[ "${branch}" != "main" ]]; then
    _die "refusing to mint from '${branch:-a detached HEAD}'; the registry lives on main." \
         "The registry is read from the working tree and committed directly on" \
         "main. A mint parked on a branch is invisible to the other direction" \
         "until that branch merges — which is the collision this script exists" \
         "to stop."
fi

dirty=$(git -C "${REPO_ROOT}" status --porcelain -- "${REGISTRY_REL}")
if [[ -n "${dirty}" ]]; then
    _die "the registry already carries uncommitted changes:" \
         "${dirty}" \
         "Refusing: a pathspec commit would sweep that in-flight edit into this" \
         "mint. Land or discard it first."
fi

# TEST SEAM (tests/test_cascade_mint.py). Widens the window between computing
# the number and writing it, so the concurrency test discriminates DETERMINISTIC-
# ally instead of by timing luck: with the lock, the second caller blocks here
# and recomputes afterwards; without it, both callers have already computed the
# same number and the test goes red. It can only sleep — it cannot change any
# outcome — and it is inert unless the variable is set.
if [[ -n "${CASCADE_MINT_TEST_DELAY:-}" ]]; then
    sleep "${CASCADE_MINT_TEST_DELAY}"
fi

# Keep the append a single line by making sure the file ends with a newline
# first; a registry whose last line was truncated must not absorb the new id.
if [[ -s "${REGISTRY}" ]] && [[ "$(tail -c 1 "${REGISTRY}" | wc -l)" -eq 0 ]]; then
    printf '\n' >> "${REGISTRY}"
fi

printf -- '- %s — %s\n' "${NEXT_ID}" "${TEXT}" >> "${REGISTRY}"

# ONLY THE REGISTRY. A pathspec commit builds a temporary index from HEAD plus
# these paths, so a parallel session's staged files are neither committed nor
# unstaged — sweeping them would be the provenance damage the commit-msg hook
# exists to stop.
#
# The message names CASCADE-IDS.md flanked by a space and the end of the line,
# which is what the commit-msg hook's TOKEN match requires. Verified against the
# live hook, not reasoned about.
MSG="docs(cascade): минт ${NEXT_ID} — CASCADE-IDS.md"

if ! git -C "${REPO_ROOT}" commit -q -m "${MSG}" -- "${REGISTRY_REL}"; then
    # Roll the append back. The registry was verified clean above, so restoring
    # from the index cannot destroy anything: a refused mint must leave no
    # half-allocated number behind for the next caller to trip over.
    git -C "${REPO_ROOT}" checkout -- "${REGISTRY_REL}" || true
    _die "the mint commit was refused; the appended line has been rolled back." \
         "Nothing was allocated."
fi

echo "minted ${NEXT_ID} in ${REGISTRY_REL}" >&2
echo "${NEXT_ID}"
