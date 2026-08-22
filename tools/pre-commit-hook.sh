#!/usr/bin/env bash
# codebugs pre-commit hook — installed as .git/hooks/pre-commit by
# tools/install-hooks.sh (symlink, so edits here take effect immediately).
#
# This is the git-level half of "main is never edited directly". It is a git
# hook rather than a Claude Code hook on purpose: it binds the user, every
# agent, and every subprocess identically, and it cannot be forgotten by a
# session that never read CLAUDE.md.
#
# It enforces three things, all already written in CLAUDE.md:
#
#   1. On main, the ONLY thing that may be committed is a .claude/plans/*.md
#      note. Everything else belongs on a branch, in a worktree.
#   2. On any other branch, the branch must carry a sanctioned type.
#   3. On main, an id appearing in .claude/plans/CASCADE-IDS.md must be the one
#      the allocator would have computed — a hand-typed cascade number is how
#      all three collisions in that registry happened (CB-137).
#
# It does NOT run tests or lint. tools/worktree-finish.sh is the quality gate
# and runs them in the worktree against the post-forward-merge tree; duplicating
# them here would add seconds to every commit to re-check a state that is not
# the one being landed.
#
# git does not run pre-commit for a merge it completes itself (it runs
# pre-merge-commit, which tools/pre-merge-commit-hook.sh now provides). This
# hook therefore owns the OTHER half: a CONFLICTED merge, which git stops and
# the operator finishes with `git commit`.
#
# Escape hatch: `git commit --no-verify`. Deliberately left open — this hook
# exists to stop the accidental case, and an operator typing --no-verify has
# stated an intent. The record of that intent is the flag itself.

set -euo pipefail

_BRANCH_TYPES=(fix feature refactor docs)

branch=$(git symbolic-ref --short -q HEAD || echo "")

# A detached HEAD is a rebase, a bisect, or a git-split2 intermediate. Nothing
# to say here; the finish script's _guard_finishable_branch catches the case
# that actually matters (shipping from one).
[[ -z "${branch}" ]] && exit 0

_IFS_SAVE="$IFS"; IFS='|'; _types="${_BRANCH_TYPES[*]}"; IFS="$_IFS_SAVE"
# The PREDICATE must match tools/_guards.sh:_guard_branch_type and
# tools/pre-merge-commit-hook.sh exactly, not merely the list of types. A prefix
# test (`${branch} == ${pfx}/*`) accepts `fix/a/b`, which the finish guard's
# full-shape regex then REFUSES — so a session could commit for hours and be
# turned away at the last step, which is the worst possible moment to learn the
# name is wrong (cross-model review). tests/test_worktree_harness.py drives all
# three through the same case table.
_type_re="^(${_types})/[A-Za-z0-9._-]+$"

# ---8<--- SHARED MERGE-GATE PREDICATE — byte-identical in pre-commit-hook.sh and
# pre-merge-commit-hook.sh. Do not edit one copy.
# tests/test_worktree_harness.py compares the two blocks verbatim; they are
# duplicated rather than sourced because each hook runs from .git/hooks and must
# not depend on tools/ being present in the checked-out tree.
#
# Decides whether one merge head may land on main. ONE input, the merge head —
# and that is a deliberate narrowing, not a simplification for its own sake.
#
# IT USED TO TAKE `named` TOO (what the caller typed, from GITHEAD_) and judge
# THAT ref alone when it resolved. Byte-identical code then still produced two
# DIFFERENT rules, because the two callers pass different arguments:
# pre-merge-commit knows the typed name, pre-commit (a separate `git commit`
# process completing a conflicted merge) does not. Review reproduced the
# divergence — `git branch fix/tmp <untyped-sha>; git merge fix/tmp --no-ff`
# landed on the clean path while the identical state was refused on the
# conflicted path — so "the predicate is byte-identical" was NOT the same claim
# as "the two hooks agree", and the byte-identity test structurally could not see
# it. A named ref is by definition a ref AT the merge head, so collecting refs
# at the head loses nothing and makes the callers pass identical information.
#
# THE RULE: the sanctioned-type rule governs LOCAL branches. Remote-tracking
# refs are UPSTREAM's namespace, which this repo does not name, so exactly one of
# them is consulted — main's own upstream — and only to recognise a pull.
#
# Why each clause, all of it earned in review:
#   * EVERY local branch at the head must qualify, not just one. With "any", a
#     typed branch created at the same commit launders an untyped merge. Git does
#     not abort after a refusal — it leaves the merge in progress and says "use
#     `git commit` to complete the merge", routing the operator into pre-commit.
#   * A NON-upstream-main remote ref neither qualifies nor disqualifies.
#     Requiring ALL refs to qualify refused a legitimate `git pull` whenever
#     upstream had another branch cut at that commit (`origin/release-1.0`), and
#     `refs/remotes/<r>/HEAD` — the default-branch alias — disqualified the very
#     pull the fallback existed to allow.
#   * Upstream main WINS over a non-qualifying local branch, so a stray local
#     bookmark at the commit being pulled cannot refuse the pull.
#   * ONLY `refs/remotes/<main's upstream>/main` counts, resolved from
#     `branch.main.remote` and defaulting to `origin`. Trusting *any* configured
#     remote's `main` meant `git remote add junk <anything>` plus a fetch was a
#     two-command bypass; an earlier version trusting any `<r>/main` at all was
#     worse still.
#
# KNOWN LIMIT, stated rather than papered over: upstream's `main` is TRUSTED, and
# nothing local can prove what it contains or how it got there. `git update-ref
# refs/remotes/origin/main <any-sha>`, a mistyped fetch refspec, a rewritten
# `remote.origin.fetch` (which then re-arms on every ordinary `git fetch`), or
# simply an upstream whose main holds untyped work — all land content here. There
# is no local discriminator, and refusing remote refs instead would break `git
# pull`, which is the worse failure. The remedy is CB-59's server-side
# protection. TestKnownLimits pins that this reproduces.
_head_is_acceptable() {
    local sha="$1"
    local _ifs_save _types _re
    _ifs_save="$IFS"; IFS='|'; _types="${_BRANCH_TYPES[*]}"; IFS="$_ifs_save"
    # The full SHAPE, not a prefix test: a prefix test accepts `fix/a/b`, which
    # _guard_branch_type refuses, and the two must not disagree.
    _re="^(${_types})/[A-Za-z0-9._-]+$"

    local _upstream _candidates
    _upstream=$(git config --get branch.main.remote 2>/dev/null || true)
    [[ -z "${_upstream}" ]] && _upstream="origin"

    _candidates=$(git for-each-ref --points-at "${sha}" --format='%(refname)' \
        refs/heads/ refs/remotes/ 2>/dev/null | sort -u)
    if [[ -z "${_candidates}" ]]; then
        echo "  ${sha:0:12} resolves to no branch at all — a bare SHA or a tag." >&2
        return 1
    fi

    local _ref _name _local_ok="" _local_bad="" _upstream_main=""
    while read -r _ref; do
        [[ -z "${_ref}" ]] && continue
        case "${_ref}" in
            refs/heads/*)
                _name="${_ref#refs/heads/}"
                if [[ "${_name}" == "main" ]] || [[ "${_name}" =~ ${_re} ]]; then
                    _local_ok=1
                else
                    _local_bad="${_local_bad}    ${_ref}"$'\n'
                fi
                ;;
            "refs/remotes/${_upstream}/main")
                _upstream_main=1
                ;;
        esac
    done <<< "${_candidates}"

    [[ -n "${_upstream_main}" ]] && return 0

    if [[ -n "${_local_bad}" ]]; then
        echo "  merge head ${sha:0:12} is named by local branch(es) with no sanctioned type:" >&2
        printf '%s' "${_local_bad}" >&2
        return 1
    fi
    [[ -n "${_local_ok}" ]] && return 0
    echo "  ${sha:0:12} is named by no local branch — only by upstream or alias refs." >&2
    return 1
}
# ---8<--- END SHARED MERGE-GATE PREDICATE

# A merge/cherry-pick/revert IN PROGRESS is being COMPLETED, not authored, and
# completing a merge onto main is the sanctioned way work lands here.
#
# git does not run pre-commit for a merge it can complete by itself (it runs
# pre-merge-commit), so a CLEAN `git merge --no-ff` was always allowed. A
# CONFLICTED merge is finished by hand with `git commit`, which DOES run
# pre-commit — so without this exemption the hook blocked exactly the flow
# CLAUDE.md documents, and only when there was a conflict. Verified both ways in
# a throwaway repo before and after that fix; a peer session hit the clean path
# first, which is why the asymmetry surfaced as a question rather than an outage.
git_dir=$(git rev-parse --git-dir)

# ...but the exemption must not become the hole (CB-57). The conflicted path is
# the ONE merge route pre-merge-commit never sees, so if the exemption were
# unconditional the branch-name rule would hold for every merge onto main
# EXCEPT the one that had a conflict — enforcement that lapses precisely when
# the operator is already distracted. Here MERGE_HEAD genuinely does exist (git
# writes it when it stops), so the ref is resolvable, unlike in the clean case.
# NOTE THE CONDITION IS NOT SCOPED TO MAIN, and that was a defect when it was.
# The exemption below fires on ANY branch, so while this validation was
# main-only, `: > .git/MERGE_HEAD` on an untyped branch still skipped the
# branch-type check and let a source commit through — the same "one empty file
# turns off a rule" shape as the cherry-pick markers, on the other side of the
# condition, reachable by an interrupted merge on a hand-made branch. Review
# reproduced it. So the merge STATE is validated everywhere; only the
# head-ACCEPTABILITY rules (typed branch / upstream main) are about main.
if [[ -e "${git_dir}/MERGE_HEAD" ]]; then
    _refused=0
    _seen=0
    # `|| [[ -n "${_sha}" ]]` — `read` returns non-zero on an UNTERMINATED last
    # line, so a plain `while read` DROPS it. And `_seen` exists because the
    # loop running zero times used to leave _refused=0, after which the
    # merge-in-progress exemption below waved the commit straight through. Two
    # reproduced bypasses, one reachable BY ACCIDENT:
    #   (a) an empty MERGE_HEAD (an interrupted git can leave one) let arbitrary
    #       staged content land on main with no merge involved at all;
    #   (b) `printf '%s' <sha> > .git/MERGE_HEAD` — no trailing newline — landed
    #       a real two-parent merge of an untyped branch.
    # Neither typed --no-verify. This is the "guard reporting clean because it
    # could not look" shape that the CI job and the pre-merge-commit hook were
    # both already hardened against in this very change; pre-commit was the one
    # place left failing OPEN.
    while read -r _sha || [[ -n "${_sha}" ]]; do
        [[ -z "${_sha}" ]] && continue
        _seen=$((_seen + 1))
        # No `named` here: this is a separate `git commit` process and the ref
        # the operator typed is long gone, so the predicate judges EVERY ref at
        # the head. That strictness is the point — see the bypass recorded in
        # the shared block's comment.
        # The head rules are about MAIN. On a branch, completing a merge is not
        # authoring, and which branch was merged in is that branch's business.
        [[ "${branch}" == "main" ]] || continue
        _head_is_acceptable "${_sha}" || _refused=1
    done < "${git_dir}/MERGE_HEAD"

    if [[ "${_seen}" -eq 0 ]]; then
        echo "ERROR: ${git_dir}/MERGE_HEAD exists but names no merge head." >&2
        echo "" >&2
        echo "  Refusing rather than assuming there is nothing to check — an" >&2
        echo "  unreadable merge state must not read as a clean one." >&2
        echo "" >&2
        echo "  If a merge was interrupted, clear it:  git merge --abort" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi

    if [[ "${_refused}" -ne 0 ]]; then
        echo "" >&2
        echo "ERROR: refusing to complete a merge onto main from an untyped branch." >&2
        echo "  Expected one of: ${_BRANCH_TYPES[*]/%//*} (or 'main' itself)." >&2
        echo "" >&2
        echo "  A CONFLICTED merge — and a merge the pre-merge-commit hook has" >&2
        echo "  already refused — is completed with 'git commit', which never" >&2
        echo "  reaches that hook. So this check lives here too, with the same" >&2
        echo "  predicate; when the two differed, review reproduced a bypass in" >&2
        echo "  three commands." >&2
        echo "" >&2
        echo "  Abort, rename, retry:" >&2
        echo "    git merge --abort && git branch -m <old> fix/<slug>" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi
fi

# THE EXEMPTION IS FOR A MERGE, AND ONLY A MERGE.
#
# It used to read `for in_progress in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD`
# and exit 0 on mere EXISTENCE of any of them. The MERGE_HEAD arm above was
# hardened against exactly that shape; its two siblings were not, and review
# reproduced the consequence:
#
#     : > "$(git rev-parse --absolute-git-dir)/CHERRY_PICK_HEAD"
#     git add backdoor.py && git commit -m "…"       # rc=0, lands on main
#
# An EMPTY CHERRY_PICK_HEAD or REVERT_HEAD waved arbitrary staged content onto
# main — and it also skipped the BRANCH-TYPE check below, so a commit on
# `totally-untyped` succeeded too. Both hook rules, off, from one empty file.
# Reachable the same way empty MERGE_HEAD was: a conflicted `git cherry-pick`
# leaves the file until `--continue`/`--abort`, so an operator who is
# interrupted and later commits unrelated staged work lands it on main.
#
# The fix is not to harden them but to STOP EXEMPTING THEM. Completing a merge
# onto main is the sanctioned landing path; cherry-picking or reverting DIRECTLY
# onto main is "editing main directly" — the thing this file exists to refuse.
# So those two now fall through to the ordinary checks: on main the staged set
# must still be a plan note, and on a branch the name must still carry a type.
# A deliberate revert on main remains possible with --no-verify, which is the
# documented way to state that intent.
#
# NOTE the distinction from CLAUDE.md's bypass list, which names cherry-pick and
# revert as commands that "move main without passing any hook". That is a
# different and weaker statement: here the hook DOES run, and used to wave the
# commit through on purpose.
[[ -e "${git_dir}/MERGE_HEAD" ]] && exit 0

# --- CASCADE-IDS MINT GATE (CB-137) -------------------------------------------
#
# tools/cascade-mint.sh computes the next cascade id under a lock and appends
# and commits it as ONE operation, so the author never types a number. That
# closes the mint MADE BY THE SCRIPT and nothing else: a hand-typed number
# committed with a plain `git commit` still lands, and that is how all three
# collisions in this registry happened — the third one with the read-the-tail
# convention satisfied BY THE LETTER, because what was protected was the
# READING of the tail and not the COMPUTATION of the number. A tool without a
# gate is a convention, and this repo's doctrine is that a convention which
# exists only as a pattern in the log is not a rule (CB-50).
#
# WHAT IS JUDGED IS THE ALLOCATION LINES THAT APPEARED, NOT THE ADDED LINES OF
# THE DIFF, AND NOT THE SET OF IDS EITHER. Both of the simpler mechanisms were
# tried and both are wrong, in opposite directions:
#
#   * ADDED LINES OF THE DIFF (the shape the card specified) cannot tell a mint
#     from an EDIT of an existing line, and this registry is edited: renaming a
#     brief inside a line (`git mv` — collision #2 did exactly that) rewrites
#     the line, so the diff shows an ADDED line carrying an ALREADY SPENT id and
#     the predicate refuses a legitimate edit. A false refusal costs far more
#     here than it looks: a refused commit on main leaves the path staged, and a
#     dirty main refuses tools/worktree-finish.sh in EVERY worktree of this
#     clone, other sessions' included (CB-130).
#   * THE SET OF IDS THAT APPEARED (`ids(staged) \ ids(HEAD)`) is blind to the
#     collisions this gate exists for. In collisions #2 and #3 the number typed
#     by hand was ALREADY IN THE REGISTRY when the commit was made — the other
#     direction had landed it minutes earlier — so it is not a NEW id at all and
#     a set difference is empty. Measured against the real registry before this
#     paragraph was written: a hand-written `- Т-21 — …` beside the existing
#     `Т-21` was accepted.
#
# So an ALLOCATION LINE is what counts, WITH MULTIPLICITY: a line that begins
# with a bullet and an id is what the tool writes and what allocates a number
# (`- Т-37 — …`), while the registry's collision notes and remarks begin with a
# word (`- КОЛЛИЗИЯ №2 …`) and merely MENTION ids. Per family:
#
#     new = allocation ids(staged) - allocation ids(HEAD)     as MULTISETS
#
#   * new empty         -> an edit, or a note that only mentions ids. Nothing
#                          to say — and mentioning a free number in a collision
#                          note stays legal, which a set-based rule would have
#                          made a refusal.
#   * exactly one       -> a mint. It must equal max+1 over EVERY id of its
#                          family ANYWHERE IN HEAD's registry — which is
#                          literally the number the tool would have computed,
#                          since the tool refuses to run unless the registry is
#                          clean and therefore reads HEAD's bytes.
#   * more than one     -> refused: the tool mints one id per run.
#
# `max` is taken over EVERY id of the family, prose mentions and ANNULLED LINES
# included, because that is the population the tool reads. A gate that were
# cleverer than the tool and skipped an annulled line would disagree with it on
# the first re-mint: an annulled line's number stayed SPENT.
#
# THE INDEX, NOT THE WORKING TREE. `git show :<path>` and `git show HEAD:<path>`
# read what is actually being committed; reading the file from disk would let an
# unstaged edit decide the verdict.
#
# THE FAMILIES ARE ENUMERATED, and that is deliberate. This registry allocates
# 'Т', 'BT' and 'DIR' and nothing else, while every line of it also carries
# foreign tokens of the same shape (CB-137, ARCH-001, ...). Deriving the family
# list from the file would let a line that first mentions a new CB card look
# like a mint of a number this allocator does not own.
#
# 'Т' READS BOTH SPELLINGS — U+0422 CYRILLIC CAPITAL LETTER TE (two bytes in
# UTF-8) and its Latin lookalike — as ONE family, the left boundary is a byte
# that cannot be part of an id, and grep runs under LC_ALL=C. All three are
# copied from tools/cascade-mint.sh together with their reasons: a Latin typo in
# the registry is a SPENT number that must not become invisible to the
# allocator; without the left boundary the Latin arm matches inside 'BT-4' and
# the BT family silently raises the unit counter; and byte semantics keep the
# verdict independent of the committer's LANG. LC_ALL is pinned per command
# rather than exported, so this gate cannot change how the rest of this hook
# reads a path.
#
# THE GATE AND THE TOOL MUST NOT DRIFT, and byte-identical code would not have
# been that claim: the two read DIFFERENT INPUTS (the tool a file in the working
# tree, this gate two blobs out of the index) and answer different questions
# (`max+1` vs `is this max+1`). This repo has already paid for "sharing an
# implementation does not share a decision when the callers supply different
# inputs" — that is the correction CB-57's shared merge predicate had to make.
# So the agreement is pinned where it is actually made, on the ANSWER:
# tests/test_worktree_harness.py::TestCascadeMintGate runs the real tool and
# this real gate over one corpus of registries and requires that the id the tool
# hands out is exactly the id this gate accepts, and that its neighbours are
# refused.
_CASCADE_REGISTRY=".claude/plans/CASCADE-IDS.md"
_CASCADE_MINT_TOOL="tools/cascade-mint.sh"
# One ERE alternation per family. The label used in messages is the first
# alternative, which is the spelling the tool WRITES.
_CASCADE_FAMILIES=('Т|T' 'BT' 'DIR')

_cascade_refuse() {
    echo "" >&2
    echo "ERROR: refusing this change to ${_CASCADE_REGISTRY}." >&2
    echo "" >&2
    while [[ $# -gt 0 ]]; do
        echo "  $1" >&2
        shift
    done
    echo "" >&2
    echo "  The number is not typed by the author. The allocator computes it" >&2
    echo "  (max+1 over the registry) and appends and commits it as one" >&2
    echo "  operation, under a lock:" >&2
    echo "" >&2
    echo "    ${_CASCADE_MINT_TOOL} --prefix Т --text '<the rest of the line>'" >&2
    echo "    ${_CASCADE_MINT_TOOL} --prefix Т --dry-run    # just show the number" >&2
    echo "" >&2
    echo "  All three collisions here were hand-typed numbers, and the third" >&2
    echo "  one satisfied the read-the-tail convention by the letter: what was" >&2
    echo "  protected was the READING, not the COMPUTATION." >&2
    echo "" >&2
    echo "  Deliberate exception: git commit --no-verify" >&2
    exit 1
}

_cascade_scan() {
    # $1 = family alternation, $2 = blob text, $3 = "all" | "alloc".
    #   all   — every id of the family anywhere in the text, the population the
    #           tool reads.
    #   alloc — only ids that OPEN a bullet line, i.e. lines that ALLOCATE a
    #           number rather than mention one.
    # Echoes each id's number, normalised (leading zeros stripped), one per
    # line, duplicates included — multiplicity is load-bearing for "alloc".
    #   0 = read (possibly zero ids)   2 = grep failed   3 = number too large
    local _alt="$1" _text="$2" _mode="$3" _re _raw="" _rc=0 _tok _n _stripped
    if [[ "${_mode}" == "alloc" ]]; then
        _re="^-[[:space:]]*(${_alt})-[0-9]+"
    else
        _re="(^|[^A-Za-z0-9-])(${_alt})-[0-9]+"
    fi
    # grep: 0 matched, 1 no match, >=2 ERROR. Three answers, and only one of
    # them means "this family has no ids here" — the tool splits them for the
    # same reason, and this repo has paid for the conflation in the bootstrap
    # gate, in MERGE_HEAD and in _guard_conflict_markers.
    _raw=$(printf '%s\n' "${_text}" | LC_ALL=C grep -oE "${_re}") || _rc=$?
    if (( _rc >= 2 )); then return 2; fi
    if (( _rc == 1 )); then return 0; fi
    while IFS= read -r _tok || [[ -n "${_tok}" ]]; do
        [[ -z "${_tok}" ]] && continue
        _n="${_tok##*-}"
        # A number the shell's arithmetic cannot hold wraps SILENTLY, and both
        # wrap directions are allocator failures. The tool refuses on the same
        # nine-digit limit, so this gate must not quietly accept what the tool
        # would not have minted.
        _stripped="${_n}"
        while [[ "${_stripped}" == 0?* ]]; do _stripped="${_stripped#0}"; done
        if (( ${#_stripped} > 9 )); then return 3; fi
        printf '%s\n' "$((10#${_n}))"
    done <<< "${_raw}"
    return 0
}

_cascade_count() {
    # $1 = number, $2 = newline-separated numbers. Echoes how many times it
    # occurs.
    local _needle="$1" _hay="$2" _x _c=0
    if [[ -n "${_hay}" ]]; then
        while IFS= read -r _x || [[ -n "${_x}" ]]; do
            [[ "${_x}" == "${_needle}" ]] && _c=$((_c + 1))
        done <<< "${_hay}"
    fi
    printf '%s\n' "${_c}"
}

_cascade_mint_gate() {
    local _staged="" _head="" _rc=0
    local _alt _label _sall _salloc _halloc _hall
    local _n _m _seen _cs _ch _newcount _newid _max _display

    if ! git rev-parse --verify -q HEAD >/dev/null 2>&1; then
        _cascade_refuse \
            "there is no HEAD to compare the staged registry against, so every" \
            "line in it is new and no allocator state can be verified."
    fi

    _staged=$(git show ":${_CASCADE_REGISTRY}" 2>/dev/null) || _rc=$?
    if (( _rc != 0 )); then
        _cascade_refuse \
            "the STAGED version of the registry could not be read — it may be" \
            "staged for deletion. Refusing rather than falling back to the" \
            "working tree, which is not what is being committed."
    fi

    _rc=0
    _head=$(git show "HEAD:${_CASCADE_REGISTRY}" 2>/dev/null) || _rc=$?
    if (( _rc != 0 )); then
        _cascade_refuse \
            "the registry does not exist in HEAD, so every line in it is new" \
            "and there is no allocator state to check max+1 against. Creating" \
            "the registry is not a mint; say so explicitly rather than having" \
            "this gate guess."
    fi

    for _alt in "${_CASCADE_FAMILIES[@]}"; do
        _label="${_alt%%|*}"

        _rc=0; _salloc=$(_cascade_scan "${_alt}" "${_staged}" alloc) || _rc=$?
        if (( _rc == 2 )); then
            _cascade_refuse \
                "the staged registry could not be scanned for '${_label}-' ids" \
                "(grep failed). Refusing rather than reading an unscannable" \
                "registry as an empty one."
        fi
        if (( _rc == 3 )); then
            _cascade_refuse \
                "the staged registry carries a '${_label}-' id with more than" \
                "nine digits. The shell's arithmetic wraps on it silently, and" \
                "the allocator refuses it too. Fix the registry line."
        fi

        _rc=0; _halloc=$(_cascade_scan "${_alt}" "${_head}" alloc) || _rc=$?
        if (( _rc != 0 )); then
            _cascade_refuse \
                "HEAD's registry could not be scanned for '${_label}-' ids." \
                "Refusing rather than treating an unreadable baseline as one" \
                "with no ids, which would make every line look new."
        fi

        # MULTISET difference: an id already in the registry that acquires a
        # SECOND allocation line is exactly collisions #2 and #3, so counting is
        # what makes this gate see them.
        _newcount=0
        _newid=""
        _display=""
        _seen=""
        while IFS= read -r _n || [[ -n "${_n}" ]]; do
            [[ -z "${_n}" ]] && continue
            [[ "${_seen}" == *"|${_n}|"* ]] && continue
            _seen="${_seen}|${_n}|"
            _cs=$(_cascade_count "${_n}" "${_salloc}")
            _ch=$(_cascade_count "${_n}" "${_halloc}")
            if (( _cs > _ch )); then
                _newcount=$((_newcount + _cs - _ch))
                _newid="${_n}"
                _display="${_display}${_label}-${_n} "
            fi
        done <<< "${_salloc}"

        if (( _newcount == 0 )); then
            continue
        fi

        if (( _newcount > 1 )); then
            _cascade_refuse \
                "this commit adds ${_newcount} '${_label}-' allocation lines at once:" \
                "${_display}" \
                "The allocator mints ONE id per run, so a commit carrying" \
                "several of them was not produced by it."
        fi

        _rc=0; _hall=$(_cascade_scan "${_alt}" "${_head}" all) || _rc=$?
        if (( _rc != 0 )); then
            _cascade_refuse \
                "HEAD's registry could not be scanned for '${_label}-' ids." \
                "Refusing rather than treating an unreadable baseline as one" \
                "with no ids."
        fi

        _max=-1
        while IFS= read -r _m || [[ -n "${_m}" ]]; do
            [[ -z "${_m}" ]] && continue
            if (( _m > _max )); then _max=${_m}; fi
        done <<< "${_hall}"

        if (( _max < 0 )); then
            _cascade_refuse \
                "HEAD's registry carries no '${_label}-' id at all, so there is" \
                "nothing to compute max+1 from. ZERO FOUND IS AN ERROR, NOT AN" \
                "EMPTY ALLOCATOR: the tool refuses in this state rather than" \
                "restart at 1, and this gate refuses to accept what the tool" \
                "would not have produced."
        fi

        if (( _newid != _max + 1 )); then
            _cascade_refuse \
                "'${_label}-${_newid}' is not the next id. The highest" \
                "'${_label}-' id in HEAD's registry is '${_label}-${_max}', so" \
                "the next one is '${_label}-$((_max + 1))'." \
                "Annulled lines and mentions count: their numbers stayed spent."
        fi
    done
}

if [[ "${branch}" == "main" ]]; then
    # --diff-filter excludes nothing: a deletion on main is as much an edit as
    # an addition. Compare against HEAD, so this reads the staged set only.
    #
    # --no-renames IS LOAD-BEARING. With rename detection on (the default),
    # `--name-only` prints ONLY the destination path, so `git mv src/keep.py
    # .claude/plans/keep.md` presents a single allowlisted path and the source
    # file silently leaves main. Reproduced in adversarial review, against both
    # this hook and the CI job. Without renames, git reports the delete and the
    # add separately and the delete is caught.
    #
    # core.quotePath=false, because the DEFAULT is a false REFUSAL: a plan note
    # with non-ASCII in its name comes back C-quoted (".claude/plans/\321\202….md"),
    # the allowlist regex misses it, and a legitimate note cannot land on main.
    # This repo has already been bitten by C-quoting once, in
    # _guard_conflict_markers, where it silently ACCEPTED a conflict marker.
    staged=$(git -c core.quotePath=false diff --cached --no-renames --name-only)
    [[ -z "${staged}" ]] && exit 0

    offending=$(echo "${staged}" | grep -vE '^\.claude/plans/[^/]+\.md$' || true)
    if [[ -n "${offending}" ]]; then
        echo "ERROR: refusing to commit on main." >&2
        echo "" >&2
        echo "  Files outside .claude/plans/*.md:" >&2
        echo "${offending}" | sed 's/^/    /' >&2
        echo "" >&2
        echo "  THOSE FILES ARE STILL STAGED. A refused commit unstages nothing:" >&2
        echo "  git does not, and this hook deliberately does not either — a hook" >&2
        echo "  that rewrites your index turns a refusal into an action. So main's" >&2
        echo "  working tree counts as dirty from here on, and _guard_main_clean" >&2
        echo "  reads exactly that index: until you clear it, EVERY" >&2
        echo "  tools/worktree-finish.sh in EVERY worktree of this clone is refused" >&2
        echo "  with exit 11 — other sessions' included. That cost is paid by" >&2
        echo "  people who did not make it: measured 2026-08-22, ~40 minutes of two" >&2
        echo "  blocked integrations (CB-130)." >&2
        echo "" >&2
        echo "  Clear it now, even if you are moving the work later:" >&2
        echo "    git restore --staged -- <files>" >&2
        echo "" >&2
        echo "  CLAUDE.md: every code edit happens on a short-lived branch, in a" >&2
        echo "  worktree. The only thing that may land on main directly is a" >&2
        echo "  .claude/plans/*.md note." >&2
        echo "" >&2
        echo "  Move the work onto a branch (the stash is shared across worktrees," >&2
        echo "  because it lives in the common git dir):" >&2
        echo "    git stash push -- <files>" >&2
        echo "    tools/worktree-setup.sh fix/<slug> main" >&2
        echo "    cd .worktrees/fix-<slug> && git stash pop" >&2
        echo "" >&2
        echo "  Deliberate exception: git commit --no-verify" >&2
        exit 1
    fi

    # The path allowlist has already passed, so everything staged is a plan
    # note. If one of them is the cascade registry, the number in it is the
    # allocator's to compute — see the CASCADE-IDS MINT GATE above.
    _touches_registry=0
    while IFS= read -r _staged_path || [[ -n "${_staged_path}" ]]; do
        [[ "${_staged_path}" == "${_CASCADE_REGISTRY}" ]] && _touches_registry=1
    done <<< "${staged}"
    if [[ "${_touches_registry}" -eq 1 ]]; then
        _cascade_mint_gate
    fi

    exit 0
fi

# Not main: the branch must be one this repo sanctions. Checked at commit time
# rather than only at finish time, because by the time a branch has commits on
# it, renaming means every reference to it in a plan or handoff is already stale.
#
# `_type_re` here is the BRANCH-NAME check. The shared merge-gate block above
# builds its own `_re` inside `_head_is_acceptable`, because that block must stay
# byte-identical with pre-merge-commit-hook.sh and so cannot reach for a variable
# only this file defines. So the regex genuinely is constructed twice in this
# file, and that is a consequence of the byte-identity requirement rather than an
# oversight — an earlier version of this comment claimed the opposite, which was
# true before the shared block landed and false after (caught in review).
# TestHarnessIntegrity pins that all three copies use the full shape, and
# TestPreCommitHook::test_hook_and_guard_agree_on_nested_branch pins this one
# behaviourally.
[[ "${branch}" =~ ${_type_re} ]] && exit 0

echo "ERROR: branch '${branch}' does not carry a sanctioned type." >&2
echo "  Expected: ${_BRANCH_TYPES[*]/%//*}" >&2
echo "  A card-driven branch carries its id: fix/cb-50-worktree-harness" >&2
echo "" >&2
echo "  Rename now, while nothing references the name yet:" >&2
echo "    git branch -m ${branch} fix/<slug>" >&2
echo "" >&2
echo "  Deliberate exception: git commit --no-verify" >&2
exit 1
