"""Provenance — staleness checks + commit-trailer resolution for findings."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import subprocess
import sys
from typing import Any, NamedTuple

from codebugs import db, findings, types


def _ambient_cwd() -> str | None:
    """The process cwd, or None when it no longer exists (CB-79).

    `os.getcwd()` raises FileNotFoundError once the directory is deleted out
    from under the process — not hypothetical here, since a long-lived MCP
    server outlives the git worktree it was started in. Every caller in this
    module degrades on None rather than raising, because that is already what
    this module does when git cannot be consulted: `file_status` reports
    `unknown`, `_parse_trailers` returns `[]`. `db.py:876` fixed the same shape
    the other way, raising `DatabaseNotFoundError`, because its callers all
    handle that — the difference is the caller's contract, not the failure.

    Returning None is safe downstream: `cwd=None` is exactly what `subprocess`
    means by "inherit", so the widened except tuples convert the resulting
    OSError into this module's own degraded answers.
    """
    try:
        return os.getcwd()
    except OSError:
        return None


#: `_kind_at_commit` could not ask git. Distinct from `None` ("git answered:
#: this path is not in that commit") because collapsing the two would let a
class _GitUnavailable(Exception):
    """A git probe could not answer.

    RAISED rather than returned, deliberately. The returned-sentinel version of
    this was reviewed and rejected: the directory branch below reads
    `kind != "blob"`, which classifies a sentinel as "not a blob" and answers
    `modified` — correct only for as long as every caller remembers a two-line
    preamble. That is exactly the shape this module has now been patched for
    three times (CB-79, CB-85, CB-88): a question that errored producing a
    confident verdict. An exception cannot be forgotten, only handled.
    """


#: `check_findings` resolved the worktree root and failed. Threaded into
#: `file_status` so a 10 000-row batch does not re-run the same failing probe
#: per finding.
_ROOT_UNRESOLVED = object()


def _repo_root(cwd: str) -> str | None:
    """The worktree root containing `cwd`, physically resolved. None if git
    cannot say — no repo, a bare repo, or git unusable.

    `db.git_rev_parse` rather than a fifth hand-written `subprocess` call in
    this module: `--show-toplevel` is an ordinary `rev-parse` argument, and that
    helper already carries the CB-79 exception tuple and the comment explaining
    it. CLAUDE.md fixes the CB-79 population as "provenance.py ×4, plus
    db.git_rev_parse"; a hand-rolled copy here would make it six, in the module
    whose own lesson is that a rule spelled as an enumeration gets fixed only at
    the sites someone enumerated.
    """
    out = db.git_rev_parse("--show-toplevel", silent=True, cwd=cwd)
    return os.path.realpath(out) if out else None


def _resolve_candidate(root: str, file_path: str) -> str:
    """The filesystem path a `file` value denotes — the same one `os.stat` gets.

    `root` is the WORKTREE ROOT, not the process cwd (CB-93). `findings.py`
    documents `file` as "File path relative to project root"; anchoring here is
    what makes that true, and it is the single place the coordinate system is
    chosen. An absolute value still wins the join, so scope is decided by
    containment rather than by spelling.

    Only the PARENT is resolved physically. That keeps a tracked symlink in
    scope (git can answer about its own blob) while refusing an in-repo
    symlinked *directory* that escapes the worktree: `<repo>/etcout/hosts`
    where `etcout -> /etc` is lexically inside and physically is not.
    A final `.`/`..`/`` names a directory, so there is no symlink to preserve
    and the whole path is resolved.
    """
    joined = os.path.join(root, file_path)
    base = os.path.basename(joined)
    if base in ("", ".", ".."):
        return os.path.realpath(joined)
    return os.path.join(os.path.realpath(os.path.dirname(joined)), base)


def _displayable(path: str) -> str:
    """Git-derived path text, made safe to return inside a `reason` string.

    `errors="surrogateescape"` is what lets an undecodable path be COMPARED
    exactly — the bytes round-trip — but a lone surrogate cannot be encoded back
    to UTF-8, and `reason` is serialized to JSON by the MCP layer. Measured:
    pydantic's `dump_json`, which the SDK uses, raises
    `PydanticSerializationError`, and `json.dumps(..., ensure_ascii=False)`
    raises `UnicodeEncodeError`. So a rename whose DESTINATION has a non-UTF-8
    name would answer correctly and then die on the way out.

    Reachable on the ordinary MCP path, unlike the mirror case: a stored `file`
    value can never hold a surrogate (SQLite refuses it on write), so the
    caller's own spelling is always safe to echo — but `new_path` comes from
    git, and an ASCII file renamed to a non-UTF-8 name puts one in the reason.

    The bytes stay exact where they are MATCHED and become U+FFFD only where
    they are DISPLAYED. Found by following up a cross-model review question
    about this fix, not by the suite.
    """
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _relative_git_env() -> str | None:
    """The name of a git environment variable set to a RELATIVE path, if any.

    Running every probe from the worktree root is what makes `rel` meaningful
    (CB-93) — but `GIT_DIR` and `GIT_WORK_TREE` are themselves resolved against
    the process cwd when they are relative, so moving cwd silently repoints them
    at a different repository. Measured: with `GIT_WORK_TREE=".."` and
    `project_dir=<repo>/src`, a genuinely modified file reported
    `current ... unchanged` — a confident wrong answer, and the one outcome this
    module treats as worse than no answer.

    Absolutizing them was considered and rejected as too clever for a path this
    rare: it means synthesizing an env for five call sites, which is the
    per-site enumeration this module keeps getting caught by. Refusing is the
    behaviour every other undecidable-scope case here already has, and it names
    the variable so the operator can fix it in one step. An ABSOLUTE value is
    cwd-independent and passes untouched.

    Found by cross-model (Codex) review of the finished diff; reproduced here
    before it was believed.
    """
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        value = os.environ.get(name)
        if value and not os.path.isabs(value):
            return name
    return None


def _verdict(status: str, reason: str) -> dict[str, Any]:
    """The ONE constructor for this module's answer, so output safety is
    structural rather than remembered per return statement.

    Sanitizing only the site where a surrogate had been OBSERVED was the wrong
    altitude, and review demonstrated it: hardening `db.git_rev_parse` (so a
    repository whose root name is not UTF-8 stops raising) immediately makes
    `out_of_repo (worktree {root})` the next string that cannot be serialized.
    Every `reason` is now cleaned on the way out, so a future branch that
    interpolates a git-derived value into one cannot reintroduce the failure by
    forgetting a call.

    Cheap: one encode/decode per `file_status` call, never per record scanned,
    and a pure-ASCII reason round-trips unchanged.
    """
    return {"file_status": status, "reason": _displayable(reason)}


def _parse_rename_records(out: str) -> list[tuple[str, str]] | None:
    """`(old, new)` pairs from `git diff --name-status -z --diff-filter=R`, or
    None when the output does not have the shape that command promises.

    `-z` emits `<status>\\0<old>\\0<new>\\0` per rename — status NUL-separated
    from the first path, not TAB-separated (pinned by
    `TestPremiseGitRecordShapes`). Because the caller filters to `R`, every
    record is a triple.

    **None, not `[]`, when a record is not a rename or a triple is truncated.**
    An earlier draft carried a status-letter dispatch that stepped 2 fields for
    a non-rename — review showed it was unreachable (`--diff-filter=R` with no
    `-C` forecloses it twice), and worse, both it and the naive 3-at-a-time
    version answered a desynchronized parse with "no renames found", which the
    caller cannot distinguish from "this file was not renamed" and turns into a
    confident `deleted`. That is the exact defect class this module has been
    patched for four times, so an unparseable answer is reported as no answer.

    Pure and total, so it is unit-testable on synthetic strings — the desync
    branch is unreachable through real git and would otherwise be untestable.
    """
    fields = [f for f in out.split("\0") if f]
    if len(fields) % 3 != 0:
        return None
    pairs = []
    for i in range(0, len(fields), 3):
        if not fields[i].startswith("R"):
            return None
        pairs.append((fields[i + 1], fields[i + 2]))
    return pairs


def _kind_at_commit(cwd: str, commit: str, rel: str) -> str | None:
    """What `rel` was in `commit`'s tree: `"blob"`, `"other"` (a tree or a
    submodule gitlink), or None — git answered, and it was not there.
    Raises `_GitUnavailable` when git could not answer at all.

    `rel` is repo-relative and canonical because it is derived from the resolved
    candidate, never from the caller's spelling — git canonicalizes the name it
    prints (`./a/b`, `a//b` and an absolute path all come back as `a/b`), so
    comparing its output to the input is what turned valid `current` files into
    `unknown` in two earlier drafts of this fix.

    `--literal-pathspecs` because `:` and `:(exclude)…` are pathspec MAGIC, not
    paths; `--full-tree` because `rel` is root-relative, and pairing it with a
    cwd-relative spelling asks about a different file entirely; `-z` because it
    also suppresses `core.quotePath` C-quoting — the hazard CB-92 records, now
    fixed in the rename reader below too.

    `errors="surrogateescape"` is the sibling-sweep half of that CB-92 fix. `-z`
    means git emits raw path bytes, so a non-UTF-8 name makes `text=True` raise
    `UnicodeDecodeError` — a `ValueError`, outside this module's
    `(SubprocessError, OSError)` contract, so it escapes as a traceback rather
    than degrading. Measured: this call DOES raise when such a path is the
    target. It is unreachable today only because `rel` derives from a stored
    `str`, which is safety by argument; the parameter makes it structural, and
    leaving one of two `-z` readers unhardened is this repo's enumeration
    failure in miniature.
    """
    if rel == ".":
        return "other"  # the worktree root: always a tree, and git has no record named "."
    try:
        out = subprocess.check_output(
            ["git", "--literal-pathspecs", "ls-tree", "-z", "--full-tree", commit, "--", rel],
            cwd=cwd,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise _GitUnavailable(str(exc)) from exc

    records = [r for r in out.split("\0") if r]
    if not records:
        return None
    if len(records) == 1:
        fields = records[0].partition("\t")[0].split()
        if len(fields) >= 2 and fields[1] == "blob":
            return "blob"
    # Known to the commit, and not a single blob. Reported as "other" rather
    # than guessing a kind: only "is it a blob" changes any verdict below.
    return "other"


def head_sha(*, project_dir: str | None = None) -> str | None:
    """Current HEAD SHA for provenance auto-population. Returns None if git unavailable."""
    return db.git_rev_parse("HEAD", silent=True, cwd=project_dir)


def file_status(
    *,
    file_path: str,
    reported_at_commit: str | None,
    project_dir: str | None = None,
    _repo_root_hint: str | None | object = None,
) -> dict[str, Any]:
    """Check staleness of a single file against a commit. Returns file_status dict.

    file_status is one of: current, modified, renamed, deleted, unknown.

    CB-88 / CB-89: the `file` field is free text and the tracker never promised
    it names a blob in this repository — directory-valued, trailing-slash,
    glob-valued, absolute-cross-repo and prose values are all in deliberate use.
    So the value is RESOLVED before it is judged, and anything this repo cannot
    answer for degrades to `unknown` with a specific reason rather than
    borrowing a confident verdict from a question that was never asked.

    `_repo_root_hint` is private: `check_findings` resolves the worktree root
    once per batch and passes it (or `_ROOT_UNRESOLVED` when its own probe
    failed, so 10 000 findings do not each retry it). It is not a public knob —
    a caller supplying a wrong root would disable the only resolution of the
    scope boundary while the function kept reporting confident verdicts.
    """
    cwd = project_dir or _ambient_cwd()

    if not reported_at_commit:
        return _verdict("unknown", "no_provenance")

    # This function is the one caller that cannot simply pass `cwd` through:
    # `os.path.join(cwd, file_path)` below would raise TypeError on None. The
    # check sits AFTER the no-provenance return so a finding with no commit
    # still reports the more specific reason.
    if cwd is None:
        return _verdict("unknown", "no_cwd")

    # Refuse only what is INHERENTLY unanswerable, and refuse it before any
    # syscall. An empty pathspec makes git exit 128, and a NUL makes `os.stat`
    # and `subprocess` raise ValueError — which is outside the
    # (SubprocessError, OSError) contract every caller of this module is
    # written against, so it escaped as a traceback. Whitespace is PRESERVED:
    # `git log -- ' '` exits 0 and a whitespace-only filename is a legal path,
    # so stripping would refuse valid input (measured; an earlier draft did).
    if file_path == "":
        return _verdict("unknown", "empty_path")
    if "\0" in file_path:
        return _verdict("unknown", "invalid_path")

    # Refused BEFORE any probe, because every probe below runs from `root` and
    # a relative git env var would mean something different there than it did
    # to the caller. See `_relative_git_env`.
    relative_env = _relative_git_env()
    if relative_env is not None:
        return _verdict("unknown", f"relative_git_env ({relative_env})")

    # SCOPE FIRST, and the ordering here is precedence between REASONS, not a
    # rule about which spelling the caller used. An earlier draft ran this
    # before `cat-file` for an absolute value and after it for a relative one,
    # to preserve the pinned "git missing -> unreachable_commit" contract. That
    # made argument spelling decide the answer: `../sibling/src/x.py` — the
    # natural spelling for a cross-repo card filed from a subdirectory — still
    # reported `unreachable_commit`, which is the very confident-wrong-channel
    # answer CB-89 was filed against. The contract is preserved instead by what
    # happens when the root CANNOT be resolved: git is then unusable for this
    # probe too, so scope simply declines to decide and the reachability check
    # below reports what it always did.
    root = _repo_root_hint if isinstance(_repo_root_hint, str) else None
    if root is None and _repo_root_hint is not _ROOT_UNRESOLVED:
        root = _repo_root(cwd)

    # `candidate` is bound beside `rel` and they live or die together: every
    # consumer below is guarded by `rel is None`, so binding `candidate` only
    # inside the branch left a name that was correct by reachability alone and
    # one statement reorder away from a `TypeError` out of `os.stat` — outside
    # this module's `(SubprocessError, OSError)` contract, which is exactly the
    # escape the NUL guard above exists to prevent (adversarial review).
    rel: str | None = None
    candidate: str | None = None
    if root is not None:
        candidate = _resolve_candidate(root, file_path)
        try:
            inside = os.path.commonpath([root, candidate]) == root
        except ValueError:
            inside = False  # different drives, or a mix of absolute and relative
        if not inside:
            # The root is named in the reason: a scope decision you cannot see
            # is a scope decision you cannot debug, and this package already
            # learned that once (CB-11/CB-49, `db.describe_root`).
            return _verdict("unknown", f"out_of_repo (worktree {root})")
        rel = os.path.relpath(candidate, root)

    try:
        subprocess.check_output(
["git", "cat-file", "-t", reported_at_commit],
            # `root or cwd`, so `root` is the only working directory git is
            # ever given once it resolves. Behaviourally identical — this probe
            # takes a commit, never a path — but leaving one probe on `cwd`
            # kept two legal spellings in scope for the next probe someone adds,
            # which is the shape this change exists to remove. `cwd` survives
            # only to DERIVE `root`, and the `root is None -> unreachable_commit`
            # ordering the CB-88 pins protect is unchanged.
            cwd=root or cwd,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        # OSError rather than FileNotFoundError, at all four sites in this module
        # and at db.git_rev_parse (CB-79). The narrow spelling covered exactly one
        # way for git to be unusable — absent — and let two others escape as
        # tracebacks: a git binary that exists but is not executable
        # (PermissionError, reproduced with a chmod-000 git as the only one on
        # PATH), and a `cwd` that has been deleted (NotADirectoryError /
        # FileNotFoundError from the exec itself). Strict widening:
        # FileNotFoundError is an OSError, so nothing that was caught stops being
        # caught. `subprocess.SubprocessError` is not an OSError subclass and has
        # to stay, or CalledProcessError and TimeoutExpired escape.
        return _verdict("unknown", "unreachable_commit")

    if rel is None or candidate is None:
        # The commit is reachable but no worktree root is — a bare repo, or a
        # cwd that is not in a repo at all. Scope cannot be decided and neither
        # can anything below it, so refusing is the honest answer; inventing one
        # is how a guard reports clean because it could not look.
        return _verdict("unknown", "git_error")

    try:
        log_output = subprocess.check_output(
            # `--literal-pathspecs`: without it `:` is git's NULL pathspec and
            # this call matches the WHOLE history, so a `file` value that is not
            # a path at all produced a non-empty range and the verdict was then
            # decided by every other file in the repo.
            #
            # The pathspec is `rel` and the cwd is `root` (CB-93). It used to be
            # the caller's raw spelling resolved against the process cwd, so the
            # DOCUMENTED root-relative spelling was the one that failed: from a
            # subdirectory `git log -- pkg/mod.py` matched nothing and a modified
            # file reported clean. `rel` is canonical and root-relative by
            # construction, so every spelling of the same file now asks git the
            # same question.
            ["git", "--literal-pathspecs", "log", "--oneline"]
            + [f"{reported_at_commit}..HEAD", "--", rel],
            cwd=root,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return _verdict("unknown", "git_error")

    if not log_output:
        # An empty range used to mean `current` outright — a confident
        # "unchanged since <sha>" for any value git had never heard of,
        # including prose, a glob, and a regular file that simply was not in
        # that commit. Disk existence is not historical identity, so the claim
        # now requires git to confirm the path was there.
        try:
            kind = _kind_at_commit(root, reported_at_commit, rel)
        except _GitUnavailable:
            return _verdict("unknown", "git_error")
        if kind is None:
            return _verdict("unknown", "not_in_commit")
        return _verdict(
            "current", f"{file_path} unchanged since {reported_at_commit[:12]}"
        )

    commit_count = len(log_output.splitlines())

    # os.stat in a guard, NOT os.path.isfile (CB-85). isfile returns False on
    # ANY OSError from the underlying stat — an unreadable parent directory,
    # ELOOP, ENAMETOOLONG, a stale NFS handle — and a False here skips the
    # `modified` branch, falls through the rename lookup, and reports a
    # confident `deleted`: a positive claim about the file derived from a
    # question that was never answered. Reproduced: chmod 000 on the parent
    # directory turns `modified` into `deleted` for a file that is still there.
    #
    # This is the SECOND route to that same wrong answer. CB-79 closed the
    # first one line below, where a subprocess failure was swallowed into an
    # empty rename result. Same "guard reporting clean because it could not
    # look" shape, different mechanism — which is why it needed its own card.
    try:
        st_mode = os.stat(candidate).st_mode
    except (FileNotFoundError, NotADirectoryError):
        # Genuinely absent — including a path component that is not a
        # directory, which means the target cannot exist. Today's answer is
        # right, so fall through to the rename/deleted branches unchanged.
        st_mode = None
    except OSError:
        return _verdict("unknown", "stat_error")

    def _modified() -> dict[str, Any]:
        s = "commit" if commit_count == 1 else "commits"
        return _verdict(
            "modified",
            f"{file_path} modified in {commit_count} {s} since {reported_at_commit[:12]}",
        )

    if st_mode is not None:
        if stat.S_ISREG(st_mode):
            return _modified()

        if stat.S_ISDIR(st_mode):
            # CB-88, the headline. `S_ISREG` is False for a directory that is
            # right there, so the existence branch was skipped, the rename
            # lookup found nothing (a directory is not a renamed blob) and
            # control fell into the unconditional `deleted` below — a positive
            # claim that the path is gone, about a path that is present.
            # Measured on the autosorter tracker: 48 of 48 `deleted` cards were
            # false, 47 of them directories.
            #
            # The one case that must NOT become `modified` is a path that was a
            # BLOB and is now a directory: its blob really is gone, so it keeps
            # falling through to rename/deleted.
            try:
                kind = _kind_at_commit(root, reported_at_commit, rel)
            except _GitUnavailable:
                return _verdict("unknown", "git_error")
            if kind != "blob":
                return _modified()
        else:
            # A fifo, socket or device that exists. `git log` says something
            # under this path changed, but no blob/deleted answer describes it.
            return _verdict("unknown", "unsupported_path_kind")

    try:
        rename_output = subprocess.check_output(
            # `-z` for the same reason `_kind_at_commit` carries it, and it is
            # what CB-92 is (this reader was left out of the sweep that fixed
            # `_guard_conflict_markers` and the pre-commit allowlist reader).
            # Default `core.quotePath` C-quotes any non-ASCII path — git prints
            # `"src/\303\244.py"`, quotes included — so the comparison below
            # missed the rename and fell through to a confident `deleted`. A TAB
            # or newline in a name broke the field/line split outright, which is
            # why `-z` is used rather than `-c core.quotePath=false`: NUL cannot
            # occur in a path, so the record shape is unambiguous for BOTH.
            [
                "git",
                "diff",
                "--diff-filter=R",
                "-M",
                "--name-status",
                "-z",
                # NO PATHSPEC, deliberately, and do not "optimize" one in now
                # that a canonical `rel` exists. Measured: git filters the
                # pathspec against the raw candidate set BEFORE pairing a
                # deletion with an addition, so `-- <old path>` — all this
                # function has — hides the new side and the pairing never
                # happens. Scoping it turns every rename into a false
                # `deleted`, i.e. straight back into CB-92.
                f"{reported_at_commit}..HEAD",
            ],
            cwd=root,
            encoding="utf-8",
            # `-z` is what makes this necessary, so it lands in the same change.
            # Suppressing the C-quoting means git emits the path's RAW bytes,
            # and a non-UTF-8 filename then made `text=True` raise
            # `UnicodeDecodeError` — a `ValueError`, so it is NOT caught by the
            # `(SubprocessError, OSError)` tuple below and escaped `file_status`
            # entirely. This probe takes no pathspec, so ONE undecodable rename
            # anywhere in the range killed a whole `check_findings` batch,
            # including plain-ASCII findings. Reproduced during adversarial
            # review of this change; the module already refuses a NUL byte for
            # this exact reason ("outside the contract every caller is written
            # against"). `surrogateescape` round-trips the bytes instead.
            errors="surrogateescape",
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        # NOT `rename_output = ""` (CB-79). That conflated "git found no renames"
        # with "git could not be asked", and the fall-through below then reports
        # a confident `deleted` — the repo's own "a guard reporting clean because
        # it could not look" failure, stated as a fact about the file. Widening
        # the tuple above made it reachable through PermissionError and a deleted
        # cwd, so the honest answer has to replace the silent one.
        return _verdict("unknown", "git_error")

    renames = _parse_rename_records(rename_output)
    if renames is None:
        # The output did not have the shape `--diff-filter=R` promises, so this
        # probe did not answer. Falling through would report a confident
        # `deleted` from a parse that failed — this module's signature defect
        # (CB-79/CB-85/CB-88/CB-92), reintroduced one layer down in its own fix.
        return _verdict("unknown", "git_error")

    for old_path, new_path in renames:
        # Compared against `rel`, never the caller's spelling: `git diff`
        # prints ROOT-relative paths regardless of cwd (measured), so the raw
        # cwd-relative value could never match from a subdirectory, and git
        # canonicalizes what it prints, so `./src/x.py` could not match either.
        if old_path == rel:
            return _verdict("renamed", f"{file_path} renamed to {new_path}")

    return _verdict("deleted", f"{file_path} deleted since {reported_at_commit[:12]}")


def _effective_commit(f: dict[str, Any]) -> Any:
    """The commit staleness is checked against: newest ring observation, else first report.

    Sister reader: `findings.query_findings(commit=)` matches ANY observation, column
    or ring (CB-128) — a different question, so the two deliberately diverge.

    CB-53 (ratified via CB-63): the ``reported_at_commit`` COLUMN is frozen at first
    report — first-report provenance, immutable at update. Re-observations land in
    the occurrence ring (``meta["occurrences"]``, CB-43), appended chronologically;
    overflow truncation keeps first+last, so the newest entries are always at the
    tail. The READER therefore consults the ring: scanning from the tail, the first
    entry carrying a usable commit wins. An observation WITHOUT a commit
    (auto-capture unavailable) must not hide an earlier one WITH a commit, because
    any ring observation is newer than the first report by construction. Rows
    predating dedup carry no ring and fall back to the frozen column — the old
    behaviour, unchanged.

    Defensive over shape, mirroring ``_bump_row``'s re-typing on the write side:
    meta and ring can be hand-written, so a non-dict meta, non-list ring, non-dict
    entry, or a None/empty/non-string commit is skipped, never raised.
    """
    meta = f.get("meta")
    ring = meta.get("occurrences") if isinstance(meta, dict) else None
    if isinstance(ring, list):
        for entry in reversed(ring):
            if isinstance(entry, dict):
                commit = entry.get("reported_at_commit")
                if isinstance(commit, str) and commit:
                    return commit
    return f.get("reported_at_commit")


def check_findings(
    conn: sqlite3.Connection,
    project_dir: str | None = None,
    *,
    finding_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    """Batched staleness check across findings. Caches per (file, checked commit).

    Each finding is checked against its newest observation's commit — the
    occurrence ring — falling back to the frozen first-report column when the
    ring carries none (`_effective_commit`, CB-53). The result reports both:
    `checked_commit` is what the verdict was computed against;
    `reported_at_commit` stays the first report.

    Filters forward to findings.query_findings; default status is 'open'.
    """
    cwd = project_dir or _ambient_cwd()

    if finding_id:
        # The docstring below has always promised "Filters forward to
        # findings.query_findings", and this branch forwarded nothing: a
        # `check_findings(finding_id="CB-1", status="fixed")` reported on CB-1
        # whatever its status (CB-28). `get_finding` is still what raises KeyError
        # for an unknown id, so that contract is unchanged; the filters are applied
        # on top of it and simply narrow the result to nothing when they exclude it.
        findings.get_finding(conn, finding_id)  # raises KeyError on an unknown id
        narrowed = findings.query_findings(
            conn,
            id=finding_id,
            status=status if types.is_vocabulary_filter_active(status) else None,
            category=category,
            file=file,
            limit=1,
        )
        findings_list = narrowed["findings"]
    else:
        query_kwargs: dict[str, Any] = {"limit": 10000}
        # `is_vocabulary_filter_active`, not truthiness: this default is for "not
        # supplied", and a plain `if status` also swallowed wrong input, so
        # `check_findings(status=0)` silently reported on open findings (CB-25). A
        # wrong value now reaches `query_findings` and raises. Unlike the five domain
        # filters, None/"" here mean "default to open", not "no filter".
        query_kwargs["status"] = status if types.is_vocabulary_filter_active(status) else "open"
        if category:
            query_kwargs["category"] = category
        if file:
            query_kwargs["file"] = file
        result = findings.query_findings(conn, **query_kwargs)
        findings_list = result["findings"]

    current_head = db.git_rev_parse("HEAD", silent=True, cwd=cwd)

    # Resolve the worktree root ONCE for the batch. `file_status` needs it to
    # decide scope, and this query permits 10 000 rows: probing per finding
    # would add a subprocess to every one of them. A failed probe is passed on
    # as ROOT_UNRESOLVED rather than None, or each finding would retry the same
    # failure — the cost claim inverting exactly when the machine is unhappy.
    batch_root: str | object = _ROOT_UNRESOLVED
    if cwd is not None:
        batch_root = _repo_root(cwd) or _ROOT_UNRESOLVED

    staleness_by_key: dict[tuple[str, str | None], dict[str, Any]] = {}
    results = []

    for f in findings_list:
        # The cache key MUST use the effective commit: two findings sharing
        # (file, first report) but re-observed at different commits would
        # otherwise share one verdict — whichever was computed first (CB-53).
        effective = _effective_commit(f)
        cache_key = (f["file"], effective)
        if cache_key not in staleness_by_key:
            staleness_by_key[cache_key] = file_status(
                file_path=f["file"],
                reported_at_commit=effective,
                project_dir=cwd,
                _repo_root_hint=batch_root,
            )
        staleness = staleness_by_key[cache_key]
        results.append(
            {
                "finding_id": f["id"],
                "file": f["file"],
                "file_status": staleness["file_status"],
                "reason": staleness["reason"],
                "reported_at_commit": f.get("reported_at_commit"),
                "checked_commit": effective,
                "current_head": current_head,
            }
        )

    return {"findings": results, "total": len(results)}


# ---------------------------------------------------------------------------
# Commit-trailer resolution — close findings cited in integrated commits.
# ---------------------------------------------------------------------------

_TRAILER_RE = re.compile(
    r"^[ \t]*(?P<verb>Resolves|Tightens)[ \t]*:[ \t]*(?P<ids>.+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_CB_ID_RE = re.compile(rf"\b{re.escape(types.FINDING_ID_PREFIX)}\d+\b")

# verb -> (note label, status input passed to update_finding, report bucket).
# Status "resolved" is an alias resolved to "fixed" by update_finding; None
# leaves the status untouched (Tightens just appends a progress note).
_VERB_ACTIONS: dict[str, tuple[str, str | None, str]] = {
    "resolves": ("Resolved", "resolved", "resolved"),
    "tightens": ("Tightened", None, "tightened"),
}


class _Trailer(NamedTuple):
    verb: str  # lowercased: "resolves" / "tightens"
    cb_id: str
    sha: str
    subject: str


def _parse_trailers(rev_range: str, *, project_dir: str | None = None) -> list[_Trailer]:
    """Return the ``Resolves:`` / ``Tightens:`` trailers found in *rev_range*.

    Reads commit bodies via ``git log --no-merges``; returns ``[]`` if git is
    unavailable or the range is empty. Field-separated with control chars so
    subjects/bodies with newlines parse unambiguously.
    """
    cwd = project_dir or _ambient_cwd()
    fmt = "%x1e%H%x1f%s%x1f%B"
    try:
        out = subprocess.check_output(
            ["git", "log", "--no-merges", f"--pretty=format:{fmt}", rev_range],
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    trailers: list[_Trailer] = []
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        parts = record.split("\x1f", 2)
        if len(parts) < 3:
            continue
        sha, subject, body = parts
        for m in _TRAILER_RE.finditer(body):
            verb = m.group("verb").lower()
            for cb_id in _CB_ID_RE.findall(m.group("ids")):
                trailers.append(_Trailer(verb, cb_id, sha, subject))
    return trailers


def resolve_trailers(
    conn: sqlite3.Connection,
    *,
    rev_range: str,
    project_dir: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Flip findings cited by ``Resolves:`` / ``Tightens:`` commit trailers.

    Parses trailers from the bodies of commits in *rev_range* (e.g.
    ``BASE..HEAD``) inside *project_dir*, then updates each cited finding:

    * ``Resolves: CB-N`` → status ``fixed`` (skipped if already terminal), with
      a note citing the commit SHA + subject.
    * ``Tightens: CB-N`` → appends a partial-progress note; status unchanged.

    Trailer syntax is case-insensitive and accepts comma-separated IDs
    (``Resolves: CB-1, CB-2``). Returns a report with ``resolved`` /
    ``tightened`` / ``skipped`` / ``missing`` ID lists. A missing finding or git
    error never aborts the batch — this runs after a successful integration and
    must be non-fatal.
    """
    report: dict[str, list[str]] = {
        "resolved": [],
        "tightened": [],
        "skipped": [],
        "missing": [],
    }
    seen: set[tuple[str, str]] = set()  # dedup report lists across repeated trailers
    for t in _parse_trailers(rev_range, project_dir=project_dir):
        if (t.verb, t.cb_id) in seen:
            continue
        seen.add((t.verb, t.cb_id))
        label, status_input, bucket = _VERB_ACTIONS[t.verb]
        try:
            current = findings.get_finding(conn, t.cb_id)
        except KeyError:
            report["missing"].append(t.cb_id)
            continue
        if status_input is not None and current["status"] in types.FINDING_TERMINAL:
            report["skipped"].append(t.cb_id)
            continue
        if not dry_run:
            findings.update_finding(
                conn,
                t.cb_id,
                status=status_input,
                append_note=f"{label} by commit {t.sha[:12]} ({t.subject}).",
            )
        report[bucket].append(t.cb_id)
    return report


def register_tools(mcp, conn_factory) -> None:
    """Register provenance tools (staleness_check) on the given MCP server."""

    @mcp.tool()
    def staleness_check(
        finding_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
        file: str | None = None,
    ) -> dict[str, Any]:
        """Check if findings are stale by comparing against git history.

        Staleness is checked against each finding's NEWEST observation: a
        deduplicated re-observation records its commit in the occurrence ring,
        and that commit — not the frozen first-report `reported_at_commit` —
        is what the file is compared from; findings with no ring fall back to
        the first report. Each result carries `checked_commit`, the commit the
        verdict was computed against.

        Returns file_status for each finding:
        - current: file unchanged since finding was reported
        - modified: file changed but still exists
        - renamed: file was renamed/moved
        - deleted: file no longer exists
        - unknown: can't determine, with a `reason` naming which question could
          not be answered — no provenance data, an unreachable commit, a path
          outside this repository's worktree, a path the reported commit never
          contained (a glob, free text, or a file added later), an empty or
          malformed value, a path that is neither a file nor a directory, or a
          git/stat call that failed

        Args:
            finding_id: Check a single finding (e.g. CB-1)
            status: Filter by finding status (default: open)
            category: Filter by category
            file: Filter by file path (substring match)
        """
        with conn_factory() as conn:
            return check_findings(
                conn, None, finding_id=finding_id, status=status, category=category, file=file
            )


def register_cli(sub, commands) -> None:
    """Register provenance CLI subcommands."""
    import argparse

    def _cmd_resolve_trailers(args: argparse.Namespace) -> None:
        conn = db.connect(args.repo)
        try:
            report = resolve_trailers(
                conn,
                rev_range=args.range,
                project_dir=args.repo,
                dry_run=args.dry_run,
            )
        finally:
            conn.close()
        prefix = "[dry-run] " if args.dry_run else ""
        for cb_id in report["resolved"]:
            print(f"{prefix}{cb_id} -> fixed")
        for cb_id in report["tightened"]:
            print(f"{prefix}{cb_id} tightened (note added)")
        for cb_id in report["skipped"]:
            print(f"{cb_id} skipped (already terminal)")
        for cb_id in report["missing"]:
            print(f"warning: {cb_id} not found", file=sys.stderr)
        updated = len(report["resolved"]) + len(report["tightened"])
        print(f"{updated} finding(s) updated, {len(report['skipped'])} skipped.")

    p = sub.add_parser(
        "resolve-trailers",
        help="Flip findings from Resolves:/Tightens: commit trailers in a git range",
    )
    p.add_argument("--range", required=True, help="git rev range, e.g. BASE..HEAD")
    p.add_argument("--repo", default=None, help="repo dir (also locates .codebugs/); default cwd")
    p.add_argument("--dry-run", action="store_true", help="parse + report without writing")

    commands.update({"resolve-trailers": _cmd_resolve_trailers})


db.register_tool_provider("provenance", register_tools)
db.register_cli_provider("provenance", register_cli)
