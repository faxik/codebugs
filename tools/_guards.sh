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
#                                    14  worktree interpreter != main's
#
# 15 shares this NUMBER SPACE and is deliberately absent from the table above,
# because it is not a guard: nothing in this file returns it and no guard can.
# It is worktree-finish.sh's post-merge alarm (CB-121), and its meaning is the
# opposite of every code listed here — THE MERGE STEP ALREADY RAN, and the
# "tested state == landed state" premise could not be confirmed afterwards.
# Note the precise wording: it does NOT promise a merge commit exists, because
# one of the states it reports is a `git merge` that had nothing left to do.
# Recorded here only so the next guard added does not reuse the number; a guard
# returning 15 would make two incompatible meanings share one code.

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
    echo "" >&2
    echo "  THIS MAY NOT BE YOUR MESS. The commonest cause is a commit REFUSED" >&2
    echo "  by the pre-commit hook in another session: git stages before the" >&2
    echo "  hook runs and a refusal unstages nothing, so the files stay in" >&2
    echo "  main's index and block every worktree in this clone, not just the" >&2
    echo "  session that left them. A staged entry is one with a non-space in" >&2
    echo "  the FIRST column above." >&2
    echo "" >&2
    echo "  See the whole picture, untracked files included:" >&2
    echo "    git -C \"${repo_root}\" status --porcelain" >&2
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
# Renamed from _guard_untracked_py_at_root (CB-83). The old name described one
# spelling of the failure and the guard only matched that spelling, so a
# zero-byte `tmpy_efkp4t` — the tempfile.mkstemp DEFAULT prefix — was swept up
# by a `git add -A`, committed, and rode a merge onto main. A name that
# undersells what a guard must catch is how the enumeration stays narrow.
#
# NOT widened to "any extensionless file at root": main legitimately tracks
# LICENSE, and Makefile/Dockerfile are ordinary additions. Refusing those would
# be the false refusal this repo repeatedly records as the worse failure. Only
# the two temp-file signatures are added, both of which are machine-generated
# and never authored by hand.
_guard_untracked_scratch_at_root() {
    local status_output="$1"
    local bad
    bad=$(echo "${status_output}" | grep -E '^\?\? ([^/]+\.py|tmp[A-Za-z0-9_]{6,}|\.codebugs-export-[A-Za-z0-9_]+)$' || true)
    [[ -z "${bad}" ]] && return 0
    echo "ERROR: refusing to auto-stage untracked top-level scratch file(s):" >&2
    echo "${bad}" | sed 's/^/  /' >&2
    echo "" >&2
    echo "  A stray temp file reached main this way once (CB-83). If intentional," >&2
    echo "  add to .gitignore or commit it by hand inside the worktree first," >&2
    echo "  then re-run." >&2
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

# Is a hook's SOURCE known to this repo — so that the hook must be armed?
# Returns 0 (demand the hook) or 1 (bootstrap window: not yet demanded).
#
# THE CONDITION IS MONOTONIC ON PURPOSE, and the obvious version was a real
# defect: gating on "does the file exist" meant a single `rm
# tools/pre-merge-commit-hook.sh` both dangled the installed hook (git skips
# a dangling hook silently) AND made the guard skip the check and return 0
# — a permanent, flagless disarm, reproduced end to end in adversarial
# review. Gating on whether the PATH HAS HISTORY cannot be undone by
# deleting the file: history only grows, so once the hook has landed the check
# is permanent, and a missing source then reports as "cannot verify the hook
# identity" instead of vanishing.
#
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
#
# The `-e <src>` arm is the BOOTSTRAP: a source present in the tree but not
# yet in any ref (the worktree that introduces it) is demanded too, and it is
# also why the harness's own test fixtures — which have no history of any
# hook — can exercise the demanding branch at all.
#
# args: <repo_root> <source path relative to repo root>
_hook_source_known() {
    local repo_root="$1" rel="$2"
    local known log_ok=""
    if known=$(git -C "${repo_root}" log -1 --format=%H --all -- "${rel}" 2>/dev/null); then
        log_ok=1
    else
        known=""
    fi
    [[ -z "${log_ok}" || -n "${known}" || -e "${repo_root}/${rel}" ]]
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

    # pre-merge-commit and commit-msg each need a bootstrap, because this guard
    # runs BEFORE the merge that first brings the hook's source onto main —
    # demanding the hook unconditionally would make the commit introducing it
    # unlandable by the very harness it extends (CB-57, same shape as CB-50;
    # the commit-msg hook hit the identical wall and was left out of this guard
    # until its source had history — T-23 closes that follow-up).
    #
    # The condition lives in ONE function, _hook_source_known, and is called
    # once per gated hook. Two copies of a four-review-round condition would be
    # two rules one edit apart — the `entities._SAFE_IDENT` vs `types._IDENT`
    # shape this repo already paid for. pre-commit above is deliberately NOT
    # gated: it predates the harness and is demanded unconditionally.
    local merge_hook_src="${repo_root}/tools/pre-merge-commit-hook.sh"
    if _hook_source_known "${repo_root}" tools/pre-merge-commit-hook.sh; then
        _p="$(_hook_problems \
            "${hook_dir}/pre-merge-commit" "${merge_hook_src}" "pre-merge-commit")"
        [[ -n "${_p}" ]] && problems="${problems}${_p}"$'\n'
    fi

    local msg_hook_src="${repo_root}/tools/commit-msg-hook.sh"
    if _hook_source_known "${repo_root}" tools/commit-msg-hook.sh; then
        _p="$(_hook_problems \
            "${hook_dir}/commit-msg" "${msg_hook_src}" "commit-msg")"
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

# ---------------------------------------------------------------------------
# NEW (CB-135): the interpreter that runs the suite in the WORKTREE must be the
# interpreter installed in MAIN.
#
# The gate runs `uv run --extra dev python -m pytest` in the worktree and the
# work lands in main. Those are two statements, and on 2026-08-22 they came
# apart: a manager reported "1943 passed" from a worktree on CPython 3.13.3
# while the same suite on the landed main, under the documented command, gave
# "1 failed, 1942 passed" on 3.14.4. The red existed on main BEFORE the merge
# and no finish could ever have seen it. CLAUDE.md forbids validating a
# worktree's change from main (pythonpath=["src"] resolves against the checkout
# you run in) — a correct rule, which silently introduced a SECOND, unnamed
# variable: the interpreter version.
#
# The fix has two halves and this is only the second one. The first is
# `.python-version`, committed at the repo root, which makes `uv` choose the
# same interpreter for main, for every worktree and for CI. That makes the
# divergent state hard to reach; this guard makes it impossible to LAND.
#
# WHAT IS COMPARED, and why the two sides are probed differently.
#
#   worktree: `uv run --extra dev python -c <probe>` — byte for byte the
#             launcher that [6/7] uses for pytest, so the answer IS the
#             interpreter the suite will run under. This call also SYNCS the
#             worktree to the pin (measured: uv removes and recreates a .venv
#             whose version does not match `.python-version`), which is wanted
#             here — the worktree is ours and is about to be tested.
#
#   main:     `<repo_root>/.venv/bin/python` executed directly. NOT `uv run`,
#             deliberately: `uv run` in main would REBUILD main's environment,
#             and main is a shared checkout other sessions are working in. A
#             guard must not mutate the tree it is judging. Executing the
#             installed interpreter also answers the exact question the CB-135
#             incident asked — what main ACTUALLY has, not what it would
#             acquire next time somebody ran something there.
#
# `pyvenv.cfg` is deliberately not read: it describes an environment rather
# than being one, and a hand-edited or half-written file would be believed.
#
# FAIL-CLOSED, in the form of _guard_enforcement_armed. No `.venv` in main, no
# `uv` on PATH, a non-zero rc, an empty answer, an unparseable answer — every
# one of them REFUSES. "Could not look, so reported clean" is the precise
# defect this card exists to close, and a version-comparison guard that treats
# an unknown version as a match would reintroduce it inside its own fix.
#
# The pin file itself is demanded too. A single source that can silently vanish
# is a convention again, and this repo's opening lesson is that a convention
# which exists only as a pattern is not a rule.
#
# args: <worktree_path> <repo_root>
# ---------------------------------------------------------------------------

# Read as a bare version on STDOUT rather than `python -V`: `-V` prints
# "Python X.Y.Z" and older interpreters printed it to STDERR, which is also
# where `uv run` writes its own progress on the run that matters most — the one
# where the pin forces a rebuild. tests/test_worktree_harness.py carries the
# same probe; if they ever diverge the fixture stops measuring the guard.
_INTERPRETER_PROBE='import sys; print("%d.%d.%d" % sys.version_info[:3])'

_interpreter_version_is_sane() {
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

_guard_interpreter_matches_main() {
    local worktree="$1" repo_root="$2"

    if [[ ! -f "${worktree}/.python-version" ]]; then
        echo "ERROR: no .python-version in the tree about to become main." >&2
        echo "  That file is the SINGLE SOURCE for the interpreter of main, of" >&2
        echo "  every worktree and of CI (CB-135). Landing a tree without it" >&2
        echo "  puts each checkout back to choosing for itself." >&2
        echo "  Fix: restore it on the branch, then re-run." >&2
        return 14
    fi

    # The worktree must own its project file, and this is NOT tidiness.
    # `uv run` walks UP for a pyproject.toml, and every worktree lives INSIDE
    # the repo (.worktrees/<slug>), so a worktree missing its own would resolve
    # against MAIN's project: measured, `uv run` from such a directory answered
    # with main's interpreter AND imported codebugs from main's src/. The guard
    # would then compare main to main, agree, and pass — CB-135's own shape,
    # one level deeper and completely silent.
    if [[ ! -f "${worktree}/pyproject.toml" ]]; then
        echo "ERROR: ${worktree} has no pyproject.toml of its own." >&2
        echo "  uv resolves a project by walking UP, and a worktree lives inside" >&2
        echo "  the repo, so this probe would answer about MAIN instead — a" >&2
        echo "  comparison of main against itself, which can only agree." >&2
        echo "  Fix: restore pyproject.toml on the branch, then re-run." >&2
        return 14
    fi

    # main's environment must not BE this worktree's. Compare the venv
    # DIRECTORIES after resolution, never the interpreters they point at: two
    # legitimate venvs built from the same system python resolve to one
    # /usr/bin/python3.14, so an interpreter-level test would refuse every
    # ordinary case. A shared .venv, by contrast, makes this a comparison of
    # main with itself — the same can-only-agree shape as the walk-up above.
    # (Cross-model review, 2026-08-22.)
    local main_venv_real wt_venv_real
    main_venv_real=$(readlink -f "${repo_root}/.venv" 2>/dev/null || true)
    wt_venv_real=$(readlink -f "${worktree}/.venv" 2>/dev/null || true)
    if [[ -n "${main_venv_real}" && "${main_venv_real}" == "${wt_venv_real}" ]]; then
        echo "ERROR: main's .venv and this worktree's .venv are the same directory." >&2
        echo "  ${main_venv_real}" >&2
        echo "  The two sides of this comparison would be one environment, so it" >&2
        echo "  could only ever agree — and the worktree removal at the end of" >&2
        echo "  the finish would leave main pointing at nothing." >&2
        echo "  Fix: give main its own environment, then re-run." >&2
        return 14
    fi

    # THE PIN MUST BE WHAT ACTUALLY DECIDED, and this is the half a first draft
    # left open (cross-model review, 2026-08-22). `UV_PYTHON` OUTRANKS
    # `.python-version` — measured, and documented in CLAUDE.md — so with it
    # exported both probes answer with the override, they agree, and the gate
    # passes while the branch is landing a DIFFERENT pin that main will pick up
    # on its next `uv run`. That is CB-135 reconstituted through the one
    # mechanism this change documents, and merely checking that the pin file
    # EXISTS cannot see it. So the worktree's answer is required to be the
    # answer the pin file asks for.
    local pin
    pin=$(sed -e 's/#.*//' -e 's/[[:space:]]//g' "${worktree}/.python-version" \
        | grep -m1 -v '^$' || true)
    if [[ ! "${pin}" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]; then
        echo "ERROR: .python-version does not name a plain CPython version." >&2
        echo "  Read: '${pin}'" >&2
        echo "  This guard compares versions, so it understands 'X', 'X.Y' and" >&2
        echo "  'X.Y.Z' and refuses anything else rather than guess what an" >&2
        echo "  implementation or platform request would resolve to." >&2
        return 14
    fi

    local wt_ver main_ver
    # Same launcher [6/7] uses for pytest — see the header for why the two
    # sides are probed differently. uv's own progress goes to stderr and is
    # left visible: it is the message that explains a rebuild.
    wt_ver=$(cd "${worktree}" && uv run --extra dev python -c "${_INTERPRETER_PROBE}") || wt_ver=""
    if ! _interpreter_version_is_sane "${wt_ver}"; then
        echo "ERROR: cannot determine the interpreter the suite would run under." >&2
        echo "  Probed with: (cd ${worktree} && uv run --extra dev python -c ...)" >&2
        echo "  Got: '${wt_ver}'" >&2
        echo "  Refusing rather than assuming it matches main — a guard that" >&2
        echo "  reports clean because it could not look is CB-135 itself." >&2
        return 14
    fi

    if [[ "${wt_ver}" != "${pin}" && "${wt_ver}" != "${pin}."* ]]; then
        echo "ERROR: something outranked .python-version." >&2
        echo "  .python-version asks for: ${pin}" >&2
        echo "  uv actually chose:        ${wt_ver}" >&2
        echo "" >&2
        echo "  UV_PYTHON (and \`--python\`) beat the pin file, so the two trees" >&2
        echo "  can be made to agree on an interpreter that is NOT the one this" >&2
        echo "  branch is landing — main would move to ${pin} on its next" >&2
        echo "  \`uv run\`, untested. The pin is only a single source if nothing" >&2
        echo "  silently overrides it." >&2
        echo "  Fix: unset UV_PYTHON, then re-run." >&2
        return 14
    fi

    # %q, because the repair line is a claim that a refusal hands back a
    # command you can paste. A repo at /tmp/code bugs otherwise gets one that
    # cannot run (cross-model review).
    local q_root
    q_root=$(printf '%q' "${repo_root}")
    local main_py="${repo_root}/.venv/bin/python"
    if [[ -x "${main_py}" ]]; then
        main_ver=$("${main_py}" -c "${_INTERPRETER_PROBE}" 2>/dev/null) || main_ver=""
    else
        main_ver=""
    fi
    if ! _interpreter_version_is_sane "${main_ver}"; then
        echo "ERROR: cannot determine main's installed interpreter." >&2
        echo "  Probed with: ${main_py} -c ..." >&2
        echo "  Got: '${main_ver}'" >&2
        echo "  Fix: (cd ${q_root} && UV_PYTHON=${wt_ver} uv sync --extra dev)" >&2
        return 14
    fi

    [[ "${wt_ver}" == "${main_ver}" ]] && return 0

    echo "ERROR: the suite would run under a different interpreter than main has." >&2
    echo "  worktree (uv run --extra dev python): ${wt_ver}" >&2
    echo "  main     (.venv/bin/python):          ${main_ver}" >&2
    echo "" >&2
    echo "  A green gate on ${wt_ver} says nothing about the tree this merge" >&2
    echo "  lands on (CB-135). Bring main to the tested interpreter:" >&2
    echo "  Fix: (cd ${q_root} && UV_PYTHON=${wt_ver} uv sync --extra dev)" >&2
    echo "" >&2
    echo "  UV_PYTHON rather than a bare 'uv sync' because a branch may be" >&2
    echo "  CHANGING .python-version, and a bare sync re-reads main's OLD pin" >&2
    echo "  and puts the old interpreter straight back." >&2
    return 14
}
