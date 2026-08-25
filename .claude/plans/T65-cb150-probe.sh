#!/usr/bin/env bash
# CB-150 / T-65 — reproducible probe: mint-gate false-refusal reachability.
#
# Runs entirely inside a THROWAWAY repo outside the working tree (canon К-5).
# Never mutates the source repo it reads from.
#
# PART A (§2 of the T-65 brief): replays the mint gate's actual verdict over
# every real historical transition of .claude/plans/CASCADE-IDS.md, to
# recount the false-refusal population on THIS tree, THIS date.
#
# PART B (§3 of the T-65 brief): for each of the seven operations named in
# the brief, constructs the state FORWARD from the CURRENT registry and runs
# the real tools/pre-commit-hook.sh, unmodified, to see whether it refuses,
# and whether that refusal would be false (a legitimate operation blocked) or
# true (exactly the collision class the gate exists to catch).
#
# Usage: cb150-probe.sh <path-to-source-repo-or-worktree> <scratch-dir>
set -euo pipefail

SRC="$1"
SCRATCH="$2"
REL=".claude/plans/CASCADE-IDS.md"

mk_probe() {  # $1 = subdir name under SCRATCH; seeds hook+registry, echoes path
    local d="${SCRATCH}/$1"
    rm -rf "${d}"
    mkdir -p "${d}/.claude/plans" "${d}/tools"
    (
        cd "${d}"
        git init -q -b main
        git config user.email cb150-probe@example.com
        git config user.name "CB-150 probe"
        cp "${SRC}/tools/pre-commit-hook.sh" tools/
        cp "${SRC}/tools/cascade-mint.sh" tools/ 2>/dev/null || true
        chmod +x tools/*.sh
        cp "${SRC}/${REL}" ".claude/plans/" 2>/dev/null || true
        mkdir -p .git/hooks
        ln -sf "${d}/tools/pre-commit-hook.sh" .git/hooks/pre-commit
        git add -A
        git commit -q -m seed --no-verify
    )
    echo "${d}"
}

echo "=========================================================="
echo "PART A — replay of real history (brief §2)"
echo "=========================================================="
d="$(mk_probe partA)"
cd "${d}"
git rm -rq --cached -- "${REL}" 2>/dev/null || true
rm -f "${REL}"
git commit -q -m "partA: empty seed" --allow-empty --no-verify

mapfile -t SHAS < <(git -C "${SRC}" log --follow --format='%H' --reverse -- "${REL}")
echo "commits touching ${REL} on source main: ${#SHAS[@]}"
echo "# git-path hooks: $(git rev-parse --git-path hooks)"
echo "# symlink resolves to: $(readlink -f .git/hooks/pre-commit)"

git -C "${SRC}" show "${SHAS[0]}:${REL}" > "${REL}"
git add -- "${REL}"
git commit -q -m "seed(0): ${SHAS[0]:0:12}" --no-verify

TOTAL=0
REFUSED=0
ROWS=()
for (( i = 1; i < ${#SHAS[@]}; i++ )); do
    NEXT="${SHAS[$i]}"; PREV="${SHAS[$((i - 1))]}"
    TOTAL=$((TOTAL + 1))
    git -C "${SRC}" show "${NEXT}:${REL}" > "${REL}"
    git add -- "${REL}"
    rc=0
    bash .git/hooks/pre-commit >/dev/null 2>&1 || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        REFUSED=$((REFUSED + 1))
        ROWS+=("${TOTAL}) ${PREV:0:12} -> ${NEXT:0:12}")
    fi
    git commit -q -m "replay(${TOTAL}): ${NEXT:0:12}" --no-verify
done
echo "TRANSITIONS_CHECKED=${TOTAL}"
echo "REFUSED=${REFUSED}"
for r in "${ROWS[@]}"; do echo "  ${r}"; done

echo
echo "=========================================================="
echo "PART B — forward reachability of the seven named operations (brief §3)"
echo "=========================================================="

run_check() {  # $1=label $2=probe-dir ; expects registry already staged
    local label="$1" d="$2" rc=0 out
    (cd "${d}" && bash .git/hooks/pre-commit) >/tmp/cb150_out.$$ 2>&1 || rc=$?
    out=$(cat /tmp/cb150_out.$$); rm -f /tmp/cb150_out.$$
    echo "-- ${label}: rc=${rc}"
    if [[ "${rc}" -ne 0 ]]; then
        echo "${out}" | grep -m1 "^ERROR:" | sed 's/^/   /'
        echo "${out}" | grep -m1 -A2 "removes an allocation\|adds .* allocation lines\|is not the next id\|does not exist in HEAD\|Creating the registry" | sed 's/^/   /' || true
    fi
}

# 1. Mass edit: rename a brief reference inside several EXISTING lines at once.
d="$(mk_probe form1_mass_edit)"
python3 - "${d}/${REL}" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
for old in ("L3-BRIEF-DIR-1-T6-cb111-abandon-returning.md",
            "L3-BRIEF-DIR-1-T7-cb116-merge-subject.md",
            "L3-BRIEF-DIR-2-T8-mechanical-2026-08-20.md"):
    s = s.replace(old, old.replace(".md", "-RENAMED.md"))
open(p, "w").write(s)
PY
(cd "${d}" && git add -- "${REL}")
run_check "1. mass edit (3 lines, rename via git-mv-like text change, no id change)" "${d}"

# 2. Reorder: swap two allocation lines' positions, no add/delete.
d="$(mk_probe form2_reorder)"
python3 - "${d}/${REL}" <<'PY'
import sys
p = sys.argv[1]
lines = open(p).read().split("\n")
idxs = [i for i, l in enumerate(lines) if l.startswith("- Т-")]
i1, i2 = idxs[10], idxs[-5]
lines[i1], lines[i2] = lines[i2], lines[i1]
open(p, "w").write("\n".join(lines))
PY
(cd "${d}" && git add -- "${REL}")
run_check "2. line reorder (swap two allocation lines, no add/delete)" "${d}"

# 3. Restore: stage an OLDER historical blob over the current HEAD.
d="$(mk_probe form3_restore)"
OLD_SHA=$(git -C "${SRC}" log --follow --format='%H' -- "${REL}" | sed -n '10p')
git -C "${SRC}" show "${OLD_SHA}:${REL}" > "${d}/${REL}"
(cd "${d}" && git add -- "${REL}")
run_check "3. restore (git show <old-sha>:path staged over current HEAD)" "${d}"

# 4. Multi-line mint: two NEW allocation lines in one commit.
d="$(mk_probe form4_multiline_mint)"
printf -- '- Т-9001 — probe line A\n- Т-9002 — probe line B\n' >> "${d}/${REL}"
(cd "${d}" && git add -- "${REL}")
run_check "4. multi-line mint (2 new allocation lines, one commit)" "${d}"

# 5. Non-max+1: reissue an already-used number (id Т-8, twice-occupied historically).
d="$(mk_probe form5_nonmaxplus1)"
printf -- '- Т-8 — probe reissue of a spent number\n' >> "${d}/${REL}"
(cd "${d}" && git add -- "${REL}")
run_check "5. non-max+1 (reissue an already-occupied id)" "${d}"

# 6a. Merge, clean: branch adds 2 allocation lines, merges cleanly onto main.
d="$(mk_probe form6a_merge_clean)"
(
    cd "${d}"
    git checkout -q -b fix/probe-branch
    printf -- '- Т-9001 — branch mint A\n- Т-9002 — branch mint B\n' >> "${REL}"
    git add -- "${REL}"
    git commit -q -m "branch commit (would be refused as direct main commit)"
    git checkout -q main
    git config merge.ff false
    rc=0
    git merge --no-ff -m "merge fix/probe-branch" fix/probe-branch >/tmp/cb150_merge.$$ 2>&1 || rc=$?
    echo "-- 6a. merge, CLEAN, landing 2 allocation lines at once: rc=${rc}"
    [[ "${rc}" -ne 0 ]] && sed 's/^/   /' /tmp/cb150_merge.$$
    rm -f /tmp/cb150_merge.$$
)

# 6b. Merge, conflicted: branch and main both touch the registry; resolve by
# keeping the branch's 2-allocation-line version.
d="$(mk_probe form6b_merge_conflicted)"
(
    cd "${d}"
    git checkout -q -b fix/probe-branch-c
    printf -- '- Т-9001 — branch line X\n- Т-9002 — branch line Y\n' >> "${REL}"
    git add -- "${REL}"
    git commit -q -m "branch commit"
    git checkout -q main
    printf -- '- Т-9001 — main line CONFLICT\n' >> "${REL}"
    git add -- "${REL}"
    git commit -q -m "main edit" --no-verify
    git config merge.ff false
    rc=0
    git merge --no-ff -m "merge attempt" fix/probe-branch-c >/tmp/cb150_m1.$$ 2>&1 || rc=$?
    echo "-- 6b. merge, CONFLICTED, MERGE_HEAD present: $(test -e .git/MERGE_HEAD && echo yes || echo no) (merge command rc=${rc}, expected 1 = conflict stopped)"
    git checkout --theirs -- "${REL}" 2>/dev/null || true
    git add -- "${REL}"
    rc2=0
    git commit -m "resolve: keep branch's 2 allocation lines" >/tmp/cb150_m2.$$ 2>&1 || rc2=$?
    echo "   completing the conflicted merge (git commit): rc=${rc2}"
    [[ "${rc2}" -ne 0 ]] && sed 's/^/   /' /tmp/cb150_m2.$$
    rm -f /tmp/cb150_m1.$$ /tmp/cb150_m2.$$
)

# 7. First mint of file: registry absent from HEAD entirely.
d="${SCRATCH}/form7_first_mint"
rm -rf "${d}"; mkdir -p "${d}/tools"
(
    cd "${d}"
    git init -q -b main
    git config user.email cb150-probe@example.com; git config user.name "CB-150 probe"
    cp "${SRC}/tools/pre-commit-hook.sh" tools/; chmod +x tools/pre-commit-hook.sh
    mkdir -p .git/hooks; ln -sf "${d}/tools/pre-commit-hook.sh" .git/hooks/pre-commit
    git add -A; git commit -q -m "seed: no registry yet" --no-verify
    mkdir -p .claude/plans
    printf -- '# CASCADE-IDS\n\n- Т-1 — first ever mint\n' > "${REL}"
    git add -- "${REL}"
)
run_check "7. first mint of file (registry absent from HEAD)" "${d}"

echo
echo "=========================================================="
echo "PART C — brief §4: does the gate walk history at all?"
echo "=========================================================="
echo "git log/rev-list/merge-base EXECUTED (not merely mentioned in a comment"
echo "explaining their absence — this script's own CB-150 comment names them,"
echo "which would otherwise make this check trip on itself):"
grep -n "^[^#]*\bgit \(log\|rev-list\|merge-base\)\b" "${SRC}/tools/pre-commit-hook.sh" || echo "  (none found)"
echo "All git invocations in the hook file, for the record:"
grep -n "git symbolic-ref\|git for-each-ref\|git rev-parse\|git show\|git diff" "${SRC}/tools/pre-commit-hook.sh"
