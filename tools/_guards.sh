#!/usr/bin/env bash
# Shared guards and repo-root resolution for the worktree harness.
#
# Sourced by tools/worktree-setup.sh, tools/worktree-finish.sh and
# tests/test_worktree_harness.py. Ported from ../autosorter/tools/worktree-*.sh
# (2026-08-16), with the guards that do not apply here dropped and two added
# that autosorter does not have (see _guard_branch_type, _guard_main_clean).
#
# WHY A SOURCED LIBRARY, where autosorter inlines each guard in the script that
# uses it: autosorter's guard test extracts a function's text by reading until
# the first column-0 `}`, which forced an INVARIANT comment onto two of its
# guards ("keep every inner closing brace indented, or the test silently
# exercises a half-function"). A test that can load half a function and pass is
# the failure mode this whole harness exists to prevent, so the guards live in
# one file that both the scripts and the tests source whole.
#
# Exit codes are distinct per guard, so a caller (or a test) can tell which one
# fired without parsing English:
#    2  worktree not found            7  branch type not one of the four
#    3  leaked repr artifact          8  primary workspace not on main
#    4  untracked top-level .py       9  branch carries no change vs main
#    5  conflict markers             10  HEAD not on a finishable branch
#    6  base too far behind main     11  main working tree dirty
#                                    12  enforcement not armed in this clone
#                                    13  main or branch moved after testing

# ---------------------------------------------------------------------------
# Repo root, resolved so every script works from any cwd, including from inside
# another worktree. --git-common-dir always points at the MAIN repo's .git.
# ---------------------------------------------------------------------------
_guards_resolve_repo_root() {
    local from="${1:-$PWD}"
    local common
    common="$(git -C "${from}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null \
        || git -C "${from}" rev-parse --git-common-dir)"
    dirname "${common}"
}

# The four branch types CLAUDE.md sanctions. Kept as one array so the setup
# script, the finish script, the pre-commit hook and the tests cannot drift into
# four different opinions about what a legal branch is.
_BRANCH_TYPES=(fix feature refactor docs)

_branch_type_re() {
    local IFS='|'
    printf '^(%s)/[A-Za-z0-9._-]+$' "${_BRANCH_TYPES[*]}"
}

# ---------------------------------------------------------------------------
# NEW vs autosorter (which has no equivalent): the branch must carry one of the
# four sanctioned types.
#
# CLAUDE.md has stated `fix/*`, `feature/*`, `refactor/*`, `docs/*` since
# 2026-08-16 13:37 (commit 2957070). At 15:30 that same day main was advanced by
# `merge worktree-cb-45-similarity-seam` — a branch with no type at all — and the
# merge message immortalized the name. Nothing could have caught it: a naming
# convention that only a reader can check is not enforced anywhere.
#
# Runs in BOTH setup (before the worktree exists, so a trip costs nothing) and
# finish (because a branch can be created by hand, and finish is the last
# moment before the name is written into main's history forever).
_guard_branch_type() {
    local branch="$1"
    local re
    re="$(_branch_type_re)"
    [[ "${branch}" =~ ${re} ]] && return 0
    echo "ERROR: branch '${branch}' does not carry a sanctioned type." >&2
    echo "  Expected one of: ${_BRANCH_TYPES[*]/%//*}" >&2
    echo "  A card-driven branch carries its id: fix/cb-50-worktree-harness" >&2
    echo "" >&2
    echo "  This is CLAUDE.md's branch rule. It was violated the day it was" >&2
    echo "  written (worktree-cb-45-similarity-seam, 2026-08-16), because" >&2
    echo "  nothing read the name before it reached main's merge message." >&2
    echo "" >&2
    echo "  Rename before finishing:" >&2
    echo "    git -C <worktree> branch -m ${branch} fix/<slug>" >&2
    return 7
}

# ---------------------------------------------------------------------------
# NEW vs autosorter: refuse to integrate onto a main whose working tree carries
# modified TRACKED files.
#
# CLAUDE.md's session-end rule already demands a clean `git status` in main and
# every worktree. Enforcing it at integration time is the cheap moment: a
# tracked-dirty main either loses the merge outright (git refuses to overwrite
# local changes) or entangles unrelated edits into the integration, and both
# read as "the harness broke" rather than "main was dirty".
#
# UNTRACKED files only WARN. `.claude/plans/*.md` notes legitimately sit
# untracked in main mid-session (one did while this very card was being built),
# and a merge cannot collide with a file git does not track.
_guard_main_clean() {
    local repo_root="$1"
    local tracked untracked
    tracked=$(git -C "${repo_root}" status --porcelain --untracked-files=no)
    untracked=$(git -C "${repo_root}" ls-files --others --exclude-standard)

    if [[ -n "${untracked}" ]]; then
        echo "  note: main has untracked file(s) — not a blocker, but the" >&2
        echo "        session-end rule wants them committed or removed:" >&2
        echo "${untracked}" | sed 's/^/          /' >&2
    fi

    [[ -z "${tracked}" ]] && return 0

    echo "ERROR: main's working tree has uncommitted changes to tracked files." >&2
    echo "${tracked}" | sed 's/^/  /' >&2
    echo "" >&2
    echo "  Integrating now would either fail outright (git refuses to clobber" >&2
    echo "  local changes) or sweep unrelated edits into the merge." >&2
    echo "  Commit or restore them in main first, then re-run." >&2
    return 11
}

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-2352 there): refuse to integrate when the worktree's
# HEAD is not on a branch worth shipping.
#
# The finish script merges whatever HEAD is on, under a message naming the
# branch. `git-split2.py` — which the global instructions MANDATE for any file
# split, so this repo will meet it — ends on an intermediate `temp-split-*`
# branch and leaves HEAD there. Nothing surfaces that: working tree correct,
# tests pass, `git status` clean, `git log` plausible.
#
# Deliberately NOT "HEAD must equal the branch matching the slug": directory
# name and branch name may legitimately differ, and that assert would refuse a
# worktree whose only sin is being named differently.
_guard_finishable_branch() {
    local branch="$1"
    if [[ -z "${branch}" ]]; then
        echo "ERROR: worktree HEAD is DETACHED — there is no branch to integrate." >&2
        echo "  Check out the feature branch inside the worktree, then re-run." >&2
        return 10
    fi
    if [[ "${branch}" == temp-split-* ]]; then
        echo "ERROR: worktree HEAD is on '${branch}', a git-split2 scratch branch." >&2
        echo "" >&2
        echo "  Integrating would ship the work under a throwaway ref and leave the" >&2
        echo "  real branch parked at the pre-split commit." >&2
        echo "" >&2
        echo "  Recover (lossless — verify ancestry FIRST):" >&2
        echo "    git -C <worktree> merge-base --is-ancestor <branch> HEAD" >&2
        echo "    git -C <worktree> branch -f <branch> HEAD" >&2
        echo "    git -C <worktree> checkout <branch>" >&2
        return 10
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-2392 there). Sharpened here, because this repo has
# already produced the state it describes.
#
# An empty diff vs main means the branch carries no work. On 2026-08-16
# `worktree-cb-45-similarity-seam` was merged, then kept receiving commits, and
# ended pointing at main's exact SHA — at which point every further "merge" of
# it was a fast-forward that moved main without recording anything.
#
# A branch ref is not a preserved change: recovery is re-applying the content
# (`git show <sha>:<path>`), never re-pointing the ref.
_guard_nonempty_diff() {
    local wt_path="$1" base_ref="$2"
    if [[ -n "$(git -C "${wt_path}" diff "${base_ref}..HEAD" --name-only 2>/dev/null)" ]]; then
        return 0
    fi
    echo "ERROR: nothing to integrate — HEAD is identical to main." >&2
    echo "" >&2
    echo "  This branch carries no change vs main, so there is no work to land." >&2
    echo "  Usual causes: the branch was already merged and then caught up to" >&2
    echo "  main, or it was parked as a bare pointer at a commit main holds." >&2
    echo "" >&2
    echo "  Inspect before doing anything:" >&2
    echo "    git -C ${wt_path} diff main --stat" >&2
    echo "    git -C ${wt_path} reflog show HEAD   # where the ref used to point" >&2
    echo "  Recover CONTENT, do not re-point the ref:" >&2
    echo "    git -C ${wt_path} show <original-sha>:<path>" >&2
    return 9
}

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-1075 there): a leaked sqlite/Connection repr
# filename in the staged set. Cheap, and this repo is sqlite-backed end to end,
# so the artifact class is live here rather than inherited.
_guard_leaked_repr() {
    local wt_path="$1"
    local bad
    bad=$(git -C "${wt_path}" diff --cached --name-only | grep -E '^<.*>|Connection object at 0x' || true)
    [[ -z "${bad}" ]] && return 0
    echo "ERROR: leaked repr artifact in the staged set:" >&2
    echo "${bad}" | sed 's/^/  /' >&2
    echo "Likely cause: 'git add -A'. Stage specific files by name." >&2
    return 3
}

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-1287 there): refuse to auto-stage an untracked
# top-level .py file. From a clean start these are almost never intentional —
# they are scratch, build wrappers, or debug leftovers.
#
# Input is `git status --short` output, so .gitignore has already filtered it.
_guard_untracked_py_at_root() {
    local status_output="$1"
    local bad
    bad=$(echo "${status_output}" | grep -E '^\?\? [^/]+\.py$' || true)
    [[ -z "${bad}" ]] && return 0
    echo "ERROR: refusing to auto-stage untracked top-level .py file(s):" >&2
    echo "${bad}" | sed 's/^/  /' >&2
    echo "" >&2
    echo "  If intentional, add to .gitignore or commit it by hand inside the" >&2
    echo "  worktree first, then re-run." >&2
    return 4
}

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-1288 there): refuse to integrate when a changed
# file still carries unresolved conflict markers. Catches the class where an
# edit fails, the commit succeeds, and the markers land in branch history.
#
# Scans the worktree's HEAD tree (not the working copy) for exactly the files
# the merge would carry, and only added/modified ones — a deleted path has no
# content to scan.
_guard_conflict_markers() {
    local wt_path="$1" base_ref="$2"
    local files f bad=""
    files=$(git -C "${wt_path}" diff "${base_ref}..HEAD" --name-only --diff-filter=d 2>/dev/null || true)
    [[ -z "${files}" ]] && return 0
    while IFS= read -r f; do
        [[ -z "${f}" ]] && continue
        # Read from the tree, so an uncommitted local fix cannot mask a marker
        # that is actually committed on the branch.
        #
        # NOT `git show … | grep -q`. `grep -q` exits at the FIRST match, which
        # can SIGPIPE `git show` (exit 141); the caller runs under `set -o
        # pipefail`, so the whole pipeline reports non-zero and the marker is
        # silently ACCEPTED. That false negative grows with file size — a
        # marker near the top of a large file is exactly when it fires — so the
        # small fixtures in the test suite would never have caught it (found by
        # cross-model review). Consume the stream fully and test the captured
        # text instead: no pipe, no early exit, no signal.
        local content
        content=$(git -C "${wt_path}" show "HEAD:${f}" 2>/dev/null) || continue
        if grep -qE '^(<{7}|={7}|>{7})( |$)' <<< "${content}"; then
            bad="${bad}${f}"$'\n'
        fi
    done <<< "${files}"
    [[ -z "${bad}" ]] && return 0
    echo "ERROR: unresolved conflict markers committed on this branch:" >&2
    echo "${bad}" | sed '/^$/d; s/^/  /' >&2
    echo "  Fix them in the worktree and commit, then re-run." >&2
    return 5
}

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-1662 there), threshold RE-DERIVED for this repo.
#
# Forward-merging a base that is hundreds of commits stale reads as reverting
# already-merged work, or fights phantom conflicts. The safe move is to port the
# diff into a fresh main-based worktree, not to finish the ancient branch.
#
# autosorter's 200 comes from ITS measured distribution and does not transfer —
# this repo has ~30 merges total, so 200 would never fire. Measured here on
# 2026-08-16 across all 20 branches: active ones sat 0-16 behind main, dormant
# already-merged ones clustered at 77-155. The band [17, 76] was EMPTY. 40 sits
# mid-band: well above normal churn, well below the dormant class.
CODEBUGS_STALE_BASE_MAX="${CODEBUGS_STALE_BASE_MAX:-40}"

# args: <behind_count> <threshold> <allow_override(true|false)>
_guard_stale_base() {
    local behind="$1" threshold="$2" allow="$3"
    # Non-numeric means the merge-base/rev-list failed. Do NOT block on an
    # uncertain signal — a transient git error that wedges every finish is a
    # worse and far more common hazard than the rare diverged-history case.
    if [[ ! "${behind}" =~ ^[0-9]+$ ]]; then
        echo "  ⚠ stale-base check skipped: behind-count is non-numeric (\"${behind}\")." >&2
        return 0
    fi
    (( behind <= threshold )) && return 0
    if [[ "${allow}" == true ]]; then
        echo "  ⚠ stale base (${behind} > ${threshold} behind main) — proceeding (--allow-stale-base)" >&2
        return 0
    fi
    echo "ERROR: branch base is ${behind} commits behind main (> ${threshold})." >&2
    echo "  Forward-merging a base this stale can REVERT already-merged work." >&2
    echo "  Port the diff into a fresh main-based worktree instead:" >&2
    echo "    tools/worktree-setup.sh fix/<slug>-v2 main" >&2
    echo "  Override only if you are SURE the merge is clean: --allow-stale-base" >&2
    return 6
}

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-2099 there): refuse to integrate when the PRIMARY
# workspace does not have main checked out.
#
# The integration merge lands on whatever branch the main workspace has checked
# out. In autosorter a parallel session once had a CI branch checked out; the
# finish merged onto that, printed "Integration complete", and orphaned the
# commit. A detached HEAD is equally not-main and aborts through the same path.
# ---------------------------------------------------------------------------
# NEW vs autosorter: refuse to integrate from a clone whose enforcement is not
# armed. This closes the hole that everything else in this file sits on.
#
# Git hooks and git config are PER-CLONE and cannot be committed. So a fresh
# clone, a clone made before `tools/install-hooks.sh` existed, or one where the
# hook symlink has come to dangle, has NO enforcement at all — and loses it
# SILENTLY, because git skips a missing or non-executable hook without a word.
# Verified while building this: the first install pointed the symlink at the
# authoring WORKTREE, so it would have dangled the moment that worktree was
# removed, leaving a repo that looked armed and was not.
#
# Checked at integration time rather than in a test, because this is the only
# moment where being unarmed can actually cost something, and because failing a
# contributor's whole suite over local config would be noise.
_guard_enforcement_armed() {
    local repo_root="$1"
    local problems=""

    local ff
    ff=$(git -C "${repo_root}" config --get merge.ff || true)
    [[ "${ff}" == "false" ]] || problems="${problems}  merge.ff is '${ff:-unset}', expected 'false'"$'\n'

    local hook
    hook="$(git -C "${repo_root}" rev-parse --path-format=absolute --git-common-dir)/hooks/pre-commit"
    if [[ ! -e "${hook}" ]]; then
        # -e follows symlinks, so a DANGLING symlink lands here, not below.
        if [[ -L "${hook}" ]]; then
            problems="${problems}  pre-commit hook is a DANGLING symlink: $(readlink "${hook}")"$'\n'
        else
            problems="${problems}  pre-commit hook is not installed (${hook})"$'\n'
        fi
    elif [[ ! -x "${hook}" ]]; then
        problems="${problems}  pre-commit hook is not executable (${hook})"$'\n'
    fi

    [[ -z "${problems}" ]] && return 0

    echo "ERROR: this clone's enforcement is not armed." >&2
    printf '%s' "${problems}" >&2
    echo "" >&2
    echo "  Hooks and git config are per-clone and are NOT committed state, so a" >&2
    echo "  fresh clone starts with no enforcement and loses it silently — git" >&2
    echo "  skips a missing or dangling hook without any message." >&2
    echo "" >&2
    echo "  Fix: ${repo_root}/tools/install-hooks.sh" >&2
    return 12
}

_guard_workspace_on_main() {
    local repo_root="$1"
    local checked_out
    checked_out=$(git -C "${repo_root}" symbolic-ref --short -q HEAD) || checked_out="(detached HEAD)"
    [[ "${checked_out}" == "main" ]] && return 0
    echo "ERROR: primary workspace has '${checked_out}' checked out, not 'main'." >&2
    echo "  The integration merge lands on the CHECKED-OUT branch of" >&2
    echo "  ${repo_root}, so integrating now would silently orphan this work." >&2
    echo "  Fix: git -C ${repo_root} checkout main   # then re-run" >&2
    return 8
}
