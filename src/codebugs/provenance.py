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
#: failed question produce a confident verdict — the failure this module already
#: carries three fix-comments about (CB-79, CB-85, CB-88).
_GIT_UNAVAILABLE = object()

#: `check_findings` resolved the worktree root and failed. Threaded to
#: `file_status` so a batch does not re-run the same failing probe per finding.
ROOT_UNRESOLVED = object()


def _repo_root(cwd: str) -> str | None:
    """The worktree root containing `cwd`, physically resolved. None if git
    cannot say — no repo, a bare repo, or git unusable."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return os.path.realpath(out) if out else None


def _resolve_candidate(cwd: str, file_path: str) -> str:
    """The filesystem path a `file` value denotes — the same one `os.stat` gets.

    Only the PARENT is resolved physically. That keeps a tracked symlink in
    scope (git can answer about its own blob) while refusing an in-repo
    symlinked *directory* that escapes the worktree: `<repo>/etcout/hosts`
    where `etcout -> /etc` is lexically inside and physically is not.
    A final `.`/`..`/`` names a directory, so there is no symlink to preserve
    and the whole path is resolved.
    """
    joined = os.path.join(cwd, file_path)
    base = os.path.basename(joined)
    if base in ("", ".", ".."):
        return os.path.realpath(joined)
    return os.path.join(os.path.realpath(os.path.dirname(joined)), base)


def _kind_at_commit(cwd: str, commit: str, rel: str) -> str | None | object:
    """What `rel` was in `commit`'s tree: `"blob"`, `"other"` (a tree or a
    submodule gitlink), None (git says: not there), or `_GIT_UNAVAILABLE`.

    `rel` is repo-relative and canonical because it is derived from the resolved
    candidate, never from the caller's spelling — git canonicalizes the name it
    prints (`./a/b`, `a//b` and an absolute path all come back as `a/b`), so
    comparing its output to the input is what turned valid `current` files into
    `unknown` in two earlier drafts of this fix.

    `--literal-pathspecs` because `:` and `:(exclude)…` are pathspec MAGIC, not
    paths; `--full-tree` because `rel` is root-relative, and pairing it with a
    cwd-relative spelling asks about a different file entirely; `-z` because it
    also suppresses `core.quotePath` C-quoting (the hazard CB-92 records for the
    rename reader below, which still lacks it).
    """
    if rel == ".":
        return "other"  # the worktree root: always a tree, and git has no record named "."
    try:
        out = subprocess.check_output(
            ["git", "--literal-pathspecs", "ls-tree", "-z", "--full-tree", commit, "--", rel],
            cwd=cwd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        return _GIT_UNAVAILABLE

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
    once per batch and passes it (or `ROOT_UNRESOLVED` when its own probe
    failed, so 10 000 findings do not each retry it). It is not a public knob —
    a caller supplying a wrong root would disable the only resolution of the
    scope boundary while the function kept reporting confident verdicts.
    """
    cwd = project_dir or _ambient_cwd()

    if not reported_at_commit:
        return {"file_status": "unknown", "reason": "no_provenance"}

    # This function is the one caller that cannot simply pass `cwd` through:
    # `os.path.join(cwd, file_path)` below would raise TypeError on None. The
    # check sits AFTER the no-provenance return so a finding with no commit
    # still reports the more specific reason.
    if cwd is None:
        return {"file_status": "unknown", "reason": "no_cwd"}

    # Refuse only what is INHERENTLY unanswerable, and refuse it before any
    # syscall. An empty pathspec makes git exit 128, and a NUL makes `os.stat`
    # and `subprocess` raise ValueError — which is outside the
    # (SubprocessError, OSError) contract every caller of this module is
    # written against, so it escaped as a traceback. Whitespace is PRESERVED:
    # `git log -- ' '` exits 0 and a whitespace-only filename is a legal path,
    # so stripping would refuse valid input (measured; an earlier draft did).
    if file_path == "":
        return {"file_status": "unknown", "reason": "empty_path"}
    if "\0" in file_path:
        return {"file_status": "unknown", "reason": "invalid_path"}

    def _scope() -> tuple[str, str] | dict[str, Any]:
        """(root, rel) for an in-scope path, or the refusal to return."""
        root = _repo_root_hint
        if root is None:
            root = _repo_root(cwd)
        elif root is ROOT_UNRESOLVED:
            root = None
        if not isinstance(root, str):
            # No worktree root means scope cannot be decided at all — a bare
            # repo, or git unusable. Refusing is the honest answer; inventing
            # one is how a guard reports clean because it could not look.
            return {"file_status": "unknown", "reason": "git_error"}
        candidate = _resolve_candidate(cwd, file_path)
        try:
            inside = os.path.commonpath([root, candidate]) == root
        except ValueError:
            inside = False  # different drives, or a mix of absolute and relative
        if not inside:
            return {"file_status": "unknown", "reason": "out_of_repo"}
        return root, os.path.relpath(candidate, root)

    # An ABSOLUTE value's scope is decidable without reference to the commit,
    # and absolute values are the whole measured CB-89 population — including
    # its sharpest case, a real commit in the repo that owns the file, which
    # `cat-file` would otherwise report as `unreachable_commit`. A relative
    # value is scoped AFTER `cat-file`, because that call is the first git call
    # today and `TestNarrowTupleCompatibility` pins "git missing →
    # unreachable_commit"; putting a probe in front of it rewrites that
    # contract for every caller.
    scoped: tuple[str, str] | None = None
    if os.path.isabs(file_path):
        outcome = _scope()
        if isinstance(outcome, dict):
            return outcome
        scoped = outcome

    try:
        subprocess.check_output(
            ["git", "cat-file", "-t", reported_at_commit],
            cwd=cwd,
            text=True,
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
        return {"file_status": "unknown", "reason": "unreachable_commit"}

    if scoped is None:
        outcome = _scope()
        if isinstance(outcome, dict):
            return outcome
        scoped = outcome
    _root, rel = scoped

    try:
        log_output = subprocess.check_output(
            # `--literal-pathspecs`: without it `:` is git's NULL pathspec and
            # this call matches the WHOLE history, so a `file` value that is not
            # a path at all produced a non-empty range and the verdict was then
            # decided by every other file in the repo. The pathspec stays the
            # RAW input, cwd-relative, exactly as before — reinterpreting it
            # against the project root is CB-93, deliberately not this change.
            ["git", "--literal-pathspecs", "log", "--oneline"]
            + [f"{reported_at_commit}..HEAD", "--", file_path],
            cwd=cwd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return {"file_status": "unknown", "reason": "git_error"}

    if not log_output:
        # An empty range used to mean `current` outright — a confident
        # "unchanged since <sha>" for any value git had never heard of,
        # including prose, a glob, and a regular file that simply was not in
        # that commit. Disk existence is not historical identity, so the claim
        # now requires git to confirm the path was there.
        kind = _kind_at_commit(cwd, reported_at_commit, rel)
        if kind is _GIT_UNAVAILABLE:
            return {"file_status": "unknown", "reason": "git_error"}
        if kind is None:
            return {"file_status": "unknown", "reason": "not_in_commit"}
        return {
            "file_status": "current",
            "reason": f"{file_path} unchanged since {reported_at_commit[:12]}",
        }

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
        st_mode = os.stat(os.path.join(cwd, file_path)).st_mode
    except (FileNotFoundError, NotADirectoryError):
        # Genuinely absent — including a path component that is not a
        # directory, which means the target cannot exist. Today's answer is
        # right, so fall through to the rename/deleted branches unchanged.
        st_mode = None
    except OSError:
        return {"file_status": "unknown", "reason": "stat_error"}

    def _modified() -> dict[str, Any]:
        s = "commit" if commit_count == 1 else "commits"
        return {
            "file_status": "modified",
            "reason": f"{file_path} modified in {commit_count} {s} since {reported_at_commit[:12]}",
        }

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
            kind = _kind_at_commit(cwd, reported_at_commit, rel)
            if kind is _GIT_UNAVAILABLE:
                return {"file_status": "unknown", "reason": "git_error"}
            if kind != "blob":
                return _modified()
        else:
            # A fifo, socket or device that exists. `git log` says something
            # under this path changed, but no blob/deleted answer describes it.
            return {"file_status": "unknown", "reason": "unsupported_path_kind"}

    try:
        rename_output = subprocess.check_output(
            [
                "git",
                "diff",
                "--diff-filter=R",
                "-M",
                "--name-status",
                f"{reported_at_commit}..HEAD",
            ],
            cwd=cwd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        # NOT `rename_output = ""` (CB-79). That conflated "git found no renames"
        # with "git could not be asked", and the fall-through below then reports
        # a confident `deleted` — the repo's own "a guard reporting clean because
        # it could not look" failure, stated as a fact about the file. Widening
        # the tuple above made it reachable through PermissionError and a deleted
        # cwd, so the honest answer has to replace the silent one.
        return {"file_status": "unknown", "reason": "git_error"}

    if rename_output:
        for line in rename_output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[1] == file_path:
                new_path = parts[2]
                return {
                    "file_status": "renamed",
                    "reason": f"{file_path} renamed to {new_path}",
                }

    return {
        "file_status": "deleted",
        "reason": f"{file_path} deleted since {reported_at_commit[:12]}",
    }


def check_findings(
    conn: sqlite3.Connection,
    project_dir: str | None = None,
    *,
    finding_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    """Batched staleness check across findings. Caches per (file, reported_at_commit).

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
    batch_root: str | None | object = ROOT_UNRESOLVED
    if cwd is not None:
        batch_root = _repo_root(cwd) or ROOT_UNRESOLVED

    staleness_by_key: dict[tuple[str, str | None], dict[str, Any]] = {}
    results = []

    for f in findings_list:
        cache_key = (f["file"], f.get("reported_at_commit"))
        if cache_key not in staleness_by_key:
            staleness_by_key[cache_key] = file_status(
                file_path=f["file"],
                reported_at_commit=f.get("reported_at_commit"),
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
            text=True,
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
