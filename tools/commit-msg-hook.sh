#!/usr/bin/env bash
# codebugs commit-msg hook — installed as .git/hooks/commit-msg by
# tools/install-hooks.sh (symlink, so edits here take effect immediately).
#
# ONE rule, and it is the mechanisation of a convention that was broken four
# times after it was adopted:
#
#   ON MAIN, EVERY .claude/plans/*.md FILE IN THE STAGED SET MUST BE NAMED IN
#   THE COMMIT MESSAGE.
#
# THE INCIDENT. `.claude/plans/` is the one directory parallel sessions may
# commit to on main, and they do so constantly. One session ran
# `git add .claude/plans/` and swept in an UNTRACKED note belonging to another
# direction; it landed inside a commit whose message described unrelated work.
# The bytes survived, the PROVENANCE did not — an artefact of one direction now
# reads as part of another's iteration. The convention "add files by name, never
# by directory" was then adopted, and broken again.
#
# WHY NAMING IS THE DISCRIMINATOR. An author committing their own note names it
# without effort. An author who swept in a stranger's file CANNOT name it,
# because they do not know it is there. Nothing else the hook can see separates
# the two cases: git records no trace of whether a path was staged individually
# or by directory, so the index cannot be asked.
#
# WHY THIS IS A commit-msg HOOK AND NOT AN EXTRA RULE IN pre-commit-hook.sh —
# the one place the specification had to be changed, and it was changed on a
# measurement rather than a preference. Measured on git 2.53:
#
#   * at pre-commit time `$GIT_DIR/COMMIT_EDITMSG` holds the PREVIOUS commit's
#     message, and on a clone's first commit it does not exist at all;
#   * the message being written does not exist anywhere at pre-commit time,
#     under `-m`, `-F` or the editor alike.
#
# A pre-commit implementation would therefore be wired to the wrong signal: it
# would pass a sweeping commit whose PREDECESSOR happened to name the file, and
# refuse a correct one whose predecessor did not. That is not a gate that fails
# open, it is a gate reading someone else's input, which is worse than none —
# it looks like enforcement. `commit-msg` receives the FINAL message as `$1`,
# after `-m`, `-F` and the editor have all had their say.
# `TestCommitMsgNamingGate::test_premise_pre_commit_cannot_see_the_message`
# pins the premise, so a git that changes it turns the suite red instead of
# silently justifying a move back.
#
# Escape hatch: `git commit --no-verify`, which skips this hook and pre-commit
# alike. Deliberately left open, on the same reasoning CLAUDE.md already records
# for pre-commit: the hook exists to stop the ACCIDENT, and an operator typing
# the flag has stated an intent.

set -euo pipefail

# BYTE semantics, deliberately, and it is load-bearing for the boundary test
# below. bash's substring operators are character-aware in a UTF-8 locale and
# byte-aware in C, so without this the hook would behave differently depending
# on the LANG of whoever ran git. Pinning C also makes every byte >= 0x80 a NAME
# byte rather than a separator, which means an ambiguous neighbour PREVENTS a
# match instead of allowing one — the fail-closed direction.
export LC_ALL=C

MSG_FILE="${1:-}"

branch=$(git symbolic-ref --short -q HEAD || echo "")

# SCOPE: main only. On a branch there are no foreign untracked notes to sweep in
# — every worktree is one concern — so the rule there would be pure friction on
# every `wip` commit. A detached HEAD is a rebase or a bisect and is likewise not
# authoring on main.
[[ "${branch}" == "main" ]] || exit 0

git_dir=$(git rev-parse --git-dir)

# A MERGE IS NOT AUTHORING, so it is exempt: `worktree-finish.sh` writes its own
# subject (`Merge <branch>: <what changed> (CB-NN)`) and a branch routinely
# carries plan notes, so demanding they be named would refuse every finish that
# touched one.
#
# THE DISCRIMINATOR DIFFERS FROM pre-merge-commit's, and assuming otherwise would
# have inverted this. CLAUDE.md records that on git 2.53 a CLEAN merge writes no
# MERGE_HEAD — true at `pre-merge-commit` time, which runs earlier and resolves
# the merge in memory. By `commit-msg` time git HAS written it, for the clean and
# the conflicted merge alike (measured; pinned by
# `test_premise_merge_head_is_PRESENT_at_commit_msg_time`). One condition
# therefore covers both, and if a future git stops writing it the pin goes red
# rather than every integration being refused.
#
# READ FAIL-CLOSED, because an exemption keyed on mere EXISTENCE is how this repo
# has been bypassed before: `: > .git/MERGE_HEAD` turned off two pre-commit rules
# at once (CB-57), and an interrupted git can leave an empty one BY ACCIDENT. The
# `|| [[ -n ... ]]` is for an UNTERMINATED last line, which a plain `while read`
# drops; `_seen` is for the loop running zero times, which would otherwise fall
# through to the exemption reporting clean because it could not look.
if [[ -e "${git_dir}/MERGE_HEAD" ]]; then
    _seen=0
    while read -r _sha || [[ -n "${_sha}" ]]; do
        [[ -z "${_sha}" ]] && continue
        _seen=$((_seen + 1))
    done < "${git_dir}/MERGE_HEAD"

    if [[ "${_seen}" -eq 0 ]]; then
        echo "ERROR: ${git_dir}/MERGE_HEAD exists but names no merge head." >&2
        echo "" >&2
        echo "  Refusing rather than treating this as a merge and skipping the" >&2
        echo "  plan-note naming rule — an unreadable merge state must not read" >&2
        echo "  as an exempt one." >&2
        echo "" >&2
        echo "  If a merge was interrupted, clear it:  git merge --abort" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi
    exit 0
fi

# THE SAME READER AS pre-commit-hook.sh, and both flags are load-bearing here for
# the same reasons they are there.
#
# --no-renames: with rename detection on, `--name-only` prints only the
# DESTINATION path, so a rename INTO .claude/plans/ would hide the source.
#
# core.quotePath=false: the default C-quotes a non-ASCII path
# (".claude/plans/\321\202….md"). This hook derives a BASENAME from that path and
# looks for it in the message, so a quoted path yields a basename no human could
# ever type — a PERMANENT false refusal of every non-ASCII plan note, which in
# this repo means most of them. C-quoting has bitten here twice already, once as
# a false refusal and once as a silent ACCEPT in _guard_conflict_markers.
staged=$(git -c core.quotePath=false diff --cached --no-renames --name-only)
[[ -z "${staged}" ]] && exit 0

# Only plan notes are this hook's business. Everything else on main is
# pre-commit's to refuse, and duplicating that judgement here would give one
# state two refusals that could drift apart. Deletions are IN scope: `git add
# <dir>` stages a removal too, and deleting a stranger's note damages the same
# provenance as adding one.
plans=$(echo "${staged}" | grep -E '^\.claude/plans/[^/]+\.md$' || true)
[[ -z "${plans}" ]] && exit 0

# ---------------------------------------------------------------------------
# The message, reduced to what git will actually KEEP.
#
# Two auto-generated sources inside the message file name every staged path, and
# either one would make this a gate that cannot fire — it would pass every
# editor commit vacuously while looking like enforcement:
#
#   (1) git's default template, whose comment lines read
#       `#\tnew file:   .claude/plans/T20-brief.md`;
#   (2) `git commit -v`, which appends the whole DIFF below the scissors line,
#       and every hunk header names its file. `git stripspace --strip-comments`
#       does NOT remove it — those lines are not comments.
#
# So: truncate at the scissors FIRST, then strip comments. The scissors test is
# `>8` and `---` on one line, which is deliberately looser than git's exact
# string: the comment character is configurable (`core.commentChar`, and
# `core.commentString` may be several characters), so anchoring on `#` would let
# a repo configured with `;` keep its diff. Over-truncating a hand-written line
# that contains both tokens costs a loud refusal; under-truncating costs the
# gate. Comment stripping is delegated to `git stripspace`, which reads the same
# `core.commentChar` git itself will use, so the two cannot disagree.
if [[ ! -f "${MSG_FILE}" ]]; then
    echo "ERROR: commit-msg hook received no readable message file." >&2
    echo "  Refusing rather than assuming the message names anything." >&2
    echo "" >&2
    echo "  Deliberate exception: git commit --no-verify" >&2
    exit 1
fi

if ! body=$(awk 'index($0, ">8") > 0 && index($0, "---") > 0 { exit } { print }' \
        "${MSG_FILE}" | git stripspace --strip-comments); then
    echo "ERROR: could not read the commit message to check it." >&2
    echo "  Refusing rather than passing a message this hook never saw." >&2
    echo "" >&2
    echo "  Deliberate exception: git commit --no-verify" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Is BASE named in HAY as a token?
#
# A SUBSTRING TEST IS NOT ENOUGH, and the counter-example is ordinary rather than
# contrived: `plan.md` is a substring of `my-plan.md`, so a sweeping commit that
# names its own `my-plan.md` would launder the stranger's `plan.md` sitting
# beside it — the swept file being, by construction, the one nobody wrote down.
#
# Nor is a regex word boundary: `\b` treats `-` and `.` as non-word, so
# `\bplan\.md\b` matches inside `my-plan.md` and reintroduces the same hole.
#
# The rule that works: the match must be flanked by a BOUNDARY on both sides,
# where a boundary is the string edge or an ASCII byte that cannot occur in the
# name we are looking for. Every non-ASCII byte counts as part of a name, so an
# ambiguous neighbour refuses. Known cost of that direction, stated rather than
# discovered later: a filename hugged by typographic quotes or a dash —
# «plan.md», —plan.md — is NOT recognised, and the author must put a space or an
# ASCII quote around it. A loud, one-word fix; the other direction is a silent
# hole.
_is_boundary() {
    local c="$1"
    [[ -z "${c}" ]] && return 0                   # string edge
    [[ "${c}" == [A-Za-z0-9._-] ]] && return 1    # can be part of the name
    local n
    n=$(printf '%d' "'${c}" 2>/dev/null) || return 1
    (( n >= 0 && n < 128 ))                       # other ASCII separates; >=0x80 does not
}

_is_named() {
    local hay="$1" base="$2" pre rest before after
    rest="${hay}"
    while [[ "${rest}" == *"${base}"* ]]; do
        pre="${rest%%"${base}"*}"
        rest="${rest#*"${base}"}"
        before="${pre: -1}"
        after="${rest:0:1}"
        if _is_boundary "${before}" && _is_boundary "${after}"; then
            return 0
        fi
    done
    return 1
}

# COUNT WHAT WAS EXAMINED. `plans` is non-empty by the time we get here, so a
# loop that ran zero times means the reader and the loop disagree about the
# staged set — and reporting "nothing to check" on that is the exact shape the
# MERGE_HEAD arm above refuses. Cheap to assert, and it cannot report clean
# because it could not look.
_examined=0
_unnamed=""
while read -r _path || [[ -n "${_path}" ]]; do
    [[ -z "${_path}" ]] && continue
    _examined=$((_examined + 1))
    _base="${_path##*/}"
    _is_named "${body}" "${_base}" || _unnamed="${_unnamed}    ${_path}"$'\n'
done <<< "${plans}"

if [[ "${_examined}" -eq 0 ]]; then
    echo "ERROR: staged plan notes were found but none could be examined." >&2
    echo "  Refusing rather than reporting clean on a set this hook could not read." >&2
    echo "" >&2
    echo "  Deliberate exception: git commit --no-verify" >&2
    exit 1
fi

[[ -z "${_unnamed}" ]] && exit 0

echo "ERROR: refusing to commit a plan note that the message does not name." >&2
echo "" >&2
echo "  Not named in the commit message:" >&2
printf '%s' "${_unnamed}" >&2
echo "" >&2
if [[ -z "${body}" ]]; then
    echo "  The commit message is empty once comments and any -v diff are" >&2
    echo "  removed, so it names nothing at all." >&2
    echo "" >&2
fi
echo "  On main, .claude/plans/*.md is the one thing that may be committed" >&2
echo "  directly, and parallel sessions all write there. 'git add" >&2
echo "  .claude/plans/' has repeatedly swept an UNTRACKED note belonging to" >&2
echo "  another direction into a commit describing unrelated work: the content" >&2
echo "  survives, the provenance does not. Naming the file is the check —" >&2
echo "  you cannot name a file you did not know was there." >&2
echo "" >&2
echo "  If the file above is yours, name it in the message." >&2
echo "  If it is NOT yours, unstage it and add yours BY NAME:" >&2
echo "    git restore --staged -- <the file above>" >&2
echo "    git add -- .claude/plans/<your-note>.md" >&2
echo "" >&2
echo "  Deliberate exception: git commit --no-verify" >&2
exit 1
