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
# UNTRACKED files usually only WARN: `.claude/plans/*.md` notes legitimately sit
# untracked in main mid-session (one did while this very card was being built).
#
# But "a merge cannot collide with a file git does not track" — which is what an
# earlier version of this comment claimed — is FALSE, and cross-model review
# caught it. If the branch ADDS a path that exists untracked in main, git
# refuses outright: "The following untracked working tree files would be
# overwritten by merge". Verified by running it. So when the caller supplies the
# branch context, untracked files are intersected with the paths the branch adds
# and a collision is a REFUSAL, not a note. Without that context the guard keeps
# the warn-only behaviour rather than guessing.
#
# args: <repo_root> [worktree_path] [base_ref]
_guard_main_clean() {
    local repo_root="$1" wt_path="${2:-}" base_ref="${3:-}"
    local tracked untracked
    tracked=$(git -C "${repo_root}" status --porcelain --untracked-files=no)
    untracked=$(git -C "${repo_root}" ls-files --others --exclude-standard)

    if [[ -n "${untracked}" && -n "${wt_path}" && -n "${base_ref}" ]]; then
        local added collisions
        # --diff-filter=A: only paths the branch CREATES can collide this way.
        added=$(git -C "${wt_path}" diff "${base_ref}..HEAD" --name-only --diff-filter=A 2>/dev/null || true)
        if [[ -n "${added}" ]]; then
            collisions=$(comm -12 <(sort <<< "${untracked}") <(sort <<< "${added}") || true)
            if [[ -n "${collisions}" ]]; then
                echo "ERROR: untracked file(s) in main collide with paths this branch adds:" >&2
                echo "${collisions}" | sed 's/^/  /' >&2
                echo "" >&2
                echo "  git refuses such a merge outright ('untracked working tree files" >&2
                echo "  would be overwritten by merge'), so this would fail mid-integration" >&2
                echo "  rather than cleanly here. Move or remove them in main first." >&2
                return 11
            fi
        fi
    fi

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
    local f bad=""
    # -z with NUL-delimited reads, fed by PROCESS SUBSTITUTION rather than a
    # variable: under the default core.quotePath a path with non-ASCII bytes
    # comes back C-quoted ("\321\202.py"), `git show HEAD:<that>` then fails,
    # the skip swallows it, and a committed marker is silently ACCEPTED — the
    # same silent-skip shape as the SIGPIPE bug above (cross-model review found
    # both).
    #
    # It must NOT be captured into a variable first: bash DROPS NUL bytes on
    # assignment, so `files=$(git … -z)` yields one run-together string and the
    # loop reads a single bogus path. Caught by this file's own tests going red
    # the moment -z was introduced.
    while IFS= read -r -d '' f; do
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
        # FAIL CLOSED on an unreadable file. `--diff-filter=d` has already
        # excluded deletions, so every path reaching here MUST be readable from
        # the tree; a failure means a corrupt object, a permissions problem, or
        # a path this loop mis-parsed. The old code did `|| continue`, which
        # turned every such case into "no markers here" — a guard reporting
        # clean because it could not look. Cross-model review flagged three
        # guards that fail open on a git error; this is the one where the
        # failure is silent AND the guard is the last line before main.
        #
        # This is also what makes `--diff-filter=d` load-bearing rather than an
        # optimization: with `|| continue`, dropping the filter was a
        # behaviourally EQUIVALENT mutation (deletions listed, show fails, skip)
        # and no test could distinguish it.
        local content
        if ! content=$(git -C "${wt_path}" show "HEAD:${f}" 2>/dev/null); then
            echo "ERROR: cannot read '${f}' from ${wt_path} HEAD — refusing to" >&2
            echo "  report this branch clean without having scanned it." >&2
            return 5
        fi
        if grep -qE '^(<{7}|={7}|>{7})( |$)' <<< "${content}"; then
            bad="${bad}${f}"$'\n'
        fi
    done < <(git -C "${wt_path}" diff "${base_ref}..HEAD" --name-only --diff-filter=d -z 2>/dev/null || true)
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

# One hook's worth of the armed check, echoed as problem lines (empty = fine).
#
# Extracted when the pre-merge-commit hook landed (CB-57), NOT copied: this repo
# has already been bitten by a check that was duplicated rather than shared
# (`entities._SAFE_IDENT` vs `types._IDENT`), and two copies of a hook-identity
# test are two opinions about what "armed" means, one of which will rot.
#
# args: <hook_path> <expected_src> <label>
_hook_problems() {
    local hook="$1" expected="$2" label="$3"
    if [[ ! -e "${hook}" ]]; then
        # -e follows symlinks, so a DANGLING symlink lands here, not below.
        if [[ -L "${hook}" ]]; then
            printf '  %s hook is a DANGLING symlink: %s\n' "${label}" "$(readlink "${hook}")"
        else
            printf '  %s hook is not installed (%s)\n' "${label}" "${hook}"
        fi
        return 0
    fi
    if [[ ! -x "${hook}" ]]; then
        printf '  %s hook is not executable (%s)\n' "${label}" "${hook}"
        return 0
    fi
    # IDENTITY, not merely existence — an executable file at that path is not
    # evidence that it is THIS hook.
    #
    # Two ways existence lies. (1) A symlink into a WORKTREE satisfies -e and -x
    # today and dangles the moment that worktree is removed — which is the last
    # thing worktree-finish.sh does, to itself, so a finish could pass this
    # guard, land, and leave the repo silently unarmed. (2) A hand-written
    # `#!/bin/sh\nexit 0` at that path passes every existence check while
    # enforcing nothing. Both were found by cross-model review; the first was
    # live in this repo at the time.
    if [[ ! -e "${expected}" ]]; then
        printf '  %s is missing — cannot verify the %s hook identity\n' "${expected}" "${label}"
        return 0
    fi
    if [[ -L "${hook}" ]]; then
        # A SYMLINK is judged by its TARGET, never by content. Content equality
        # is not the property that matters here: a link to an identical file
        # inside a worktree is byte-for-byte correct today and gone tomorrow.
        # Only a link into main's own tools/ survives `git worktree remove`.
        local target want
        target="$(readlink -f "${hook}" 2>/dev/null || echo "${hook}")"
        want="$(readlink -f "${expected}" 2>/dev/null || echo "${expected}")"
        if [[ "${target}" != "${want}" ]]; then
            printf '  %s hook is not main'"'"'s copy: %s\n' "${label}" "${target}"
            printf '    expected %s (a link into a worktree dies with it)\n' "${expected}"
        fi
    elif ! cmp -s "${hook}" "${expected}"; then
        # A REGULAR FILE cannot dangle, so content is the right test — and it is
        # the test that rejects a hand-written `exit 0` impostor.
        printf '  %s hook differs from %s\n' "${label}" "${expected}"
    fi
}

_guard_enforcement_armed() {
    local repo_root="$1"
    local problems=""

    local ff
    ff=$(git -C "${repo_root}" config --get merge.ff || true)
    [[ "${ff}" == "false" ]] || problems="${problems}  merge.ff is '${ff:-unset}', expected 'false'"$'\n'

    local hook_dir
    # `--git-path hooks`, NOT `--git-common-dir`/hooks: git honours
    # `core.hooksPath` and the common-dir form does not. So `git config
    # core.hooksPath <empty-dir>` made this guard report ARMED (rc 0) while
    # nothing at all was installed, and a commit of arbitrary content on main
    # then succeeded — reproduced in adversarial review. This is the guard whose
    # entire job is "this clone is actually armed", so a false 0 here is exactly
    # the false-assurance class _hook_problems exists to refuse. Verified that
    # --git-path follows the redirect and --git-common-dir does not.
    hook_dir="$(git -C "${repo_root}" rev-parse --path-format=absolute --git-path hooks)"

    # A RELATIVE core.hooksPath is refused outright, because it cannot be
    # verified: git resolves it against the top of EACH working tree, so
    # `core.hooksPath=.githooks` means a different directory in the primary
    # checkout and in every linked worktree. Review reproduced the consequence —
    # armed in the primary, main checked out in a linked worktree with no
    # .githooks there, guard rc=0, and `git commit` of a source file straight onto
    # main rc=0. "This clone is armed" is not a statement this guard can make
    # about a per-worktree path, so it declines to make it.
    #
    # `--type=path`, so git does its own `~` expansion before the test. Reading
    # the RAW value classed `core.hooksPath=~/hooks` as relative and refused a
    # clone that was genuinely, uniformly armed — git expands the tilde, the hook
    # there really does fire, and the guard was resolving the same setting two
    # different ways in one function (`--git-path` for hook_dir, raw string here).
    # Review caught the inconsistency. `--type=path` still refuses `./hooks`,
    # `.githooks` and a leading-space value, which is the point.
    local _hooks_cfg
    _hooks_cfg=$(git -C "${repo_root}" config --type=path --get core.hooksPath || true)
    if [[ -n "${_hooks_cfg}" && "${_hooks_cfg}" != /* ]]; then
        problems="${problems}  core.hooksPath is RELATIVE ('${_hooks_cfg}'), which resolves"$'\n'
        problems="${problems}    per working tree — arming one checkout leaves the others bare."$'\n'
        problems="${problems}    Set an absolute path, or unset it."$'\n'
    fi

    # Append with an EXPLICIT separator: `$( )` strips trailing newlines, so
    # concatenating two non-empty results directly would run the last line of
    # one into the first line of the next and report two faults as one.
    local _p
    _p="$(_hook_problems \
        "${hook_dir}/pre-commit" "${repo_root}/tools/pre-commit-hook.sh" "pre-commit")"
    [[ -n "${_p}" ]] && problems="${problems}${_p}"$'\n'

    # pre-merge-commit needs a bootstrap, because this guard runs BEFORE the
    # merge that first brings tools/pre-merge-commit-hook.sh onto main —
    # demanding the hook unconditionally would make the commit introducing it
    # unlandable by the very harness it extends (CB-57, same shape as CB-50).
    #
    # THE CONDITION IS MONOTONIC ON PURPOSE, and the obvious version was a real
    # defect: gating on "does the file exist" meant a single `rm
    # tools/pre-merge-commit-hook.sh` both dangled the installed hook (git skips
    # a dangling hook silently) AND made this guard skip the check and return 0
    # — a permanent, flagless disarm, reproduced end to end in adversarial
    # review. Gating on whether the PATH HAS HISTORY on main cannot be undone by
    # deleting the file: history only grows, so once CB-57 has landed the check
    # is permanent, and a missing source then reports as "cannot verify the hook
    # identity" instead of vanishing.
    local merge_hook_src="${repo_root}/tools/pre-merge-commit-hook.sh"
    local merge_hook_known
    # `--all`, NOT the literal ref `main`. Reading `main` looked monotonic and was
    # not: in any clone WITHOUT A LOCAL branch named main — `git clone
    # --single-branch --branch fix/…` is enough, and origin/main being present
    # does not help — `git log -1 main -- <path>` fatals, the variable empties,
    # and the condition collapses back to `-e <src>`. That is precisely the
    # flagless disarm this gate was rewritten to close: review reproduced
    # install-hooks → rm the source → guard rc=0 → an untyped branch merged onto
    # main. `--all` consults every ref, so no checkout shape can make the history
    # invisible.
    # AND the failure of that command is NOT "no history". `2>/dev/null || true`
    # made the two indistinguishable, so any error re-opened the whole disarm:
    # review reproduced it with a `--filter=tree:0` clone whose promisor remote
    # had gone away, where `git log --all -- <path>` exits 128, the value empties,
    # the condition collapses to `-e <src>`, and `rm` + a merge of the literal
    # 2026-08-16 offender landed on main. Distinguish the two and FAIL CLOSED on
    # an error: "cannot tell" must demand the hook, not excuse it.
    local merge_hook_log_ok=""
    if merge_hook_known=$(git -C "${repo_root}" log -1 --format=%H --all \
            -- tools/pre-merge-commit-hook.sh 2>/dev/null); then
        merge_hook_log_ok=1
    else
        merge_hook_known=""
    fi
    if [[ -z "${merge_hook_log_ok}" || -n "${merge_hook_known}" || -e "${merge_hook_src}" ]]; then
        _p="$(_hook_problems \
            "${hook_dir}/pre-merge-commit" "${merge_hook_src}" "pre-merge-commit")"
        [[ -n "${_p}" ]] && problems="${problems}${_p}"$'\n'
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

# ---------------------------------------------------------------------------
# Ported from autosorter (CB-2099 there): refuse to integrate when the PRIMARY
# workspace does not have main checked out.
#
# The integration merge lands on whatever branch the main workspace has checked
# out. In autosorter a parallel session once had a CI branch checked out; the
# finish merged onto that, printed "Integration complete", and orphaned the
# commit. A detached HEAD is equally not-main and aborts through the same path.
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
