"""Provenance — staleness checks + commit-trailer resolution for findings."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from typing import Any

from codebugs import db, findings, types


def head_sha(*, project_dir: str | None = None) -> str | None:
    """Current HEAD SHA for provenance auto-population. Returns None if git unavailable."""
    return db.git_rev_parse("HEAD", silent=True, cwd=project_dir)


def file_status(
    *,
    file_path: str,
    reported_at_commit: str | None,
    project_dir: str | None = None,
) -> dict[str, Any]:
    """Check staleness of a single file against a commit. Returns file_status dict.

    file_status is one of: current, modified, renamed, deleted, unknown.
    """
    cwd = project_dir or os.getcwd()

    if not reported_at_commit:
        return {"file_status": "unknown", "reason": "no_provenance"}

    try:
        subprocess.check_output(
            ["git", "cat-file", "-t", reported_at_commit],
            cwd=cwd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"file_status": "unknown", "reason": "unreachable_commit"}

    try:
        log_output = subprocess.check_output(
            ["git", "log", "--oneline", f"{reported_at_commit}..HEAD", "--", file_path],
            cwd=cwd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"file_status": "unknown", "reason": "git_error"}

    if not log_output:
        return {
            "file_status": "current",
            "reason": f"{file_path} unchanged since {reported_at_commit[:12]}",
        }

    commit_count = len(log_output.splitlines())
    file_exists = os.path.isfile(os.path.join(cwd, file_path))

    if file_exists:
        s = "commit" if commit_count == 1 else "commits"
        return {
            "file_status": "modified",
            "reason": f"{file_path} modified in {commit_count} {s} since {reported_at_commit[:12]}",
        }

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
    except (subprocess.SubprocessError, FileNotFoundError):
        rename_output = ""

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
    cwd = project_dir or os.getcwd()

    if finding_id:
        findings_list = [findings.get_finding(conn, finding_id)]
    else:
        query_kwargs: dict[str, Any] = {"limit": 10000}
        query_kwargs["status"] = status if status else "open"
        if category:
            query_kwargs["category"] = category
        if file:
            query_kwargs["file"] = file
        result = findings.query_findings(conn, **query_kwargs)
        findings_list = result["findings"]

    current_head = db.git_rev_parse("HEAD", silent=True, cwd=cwd)

    staleness_by_key: dict[tuple[str, str | None], dict[str, Any]] = {}
    results = []

    for f in findings_list:
        cache_key = (f["file"], f.get("reported_at_commit"))
        if cache_key not in staleness_by_key:
            staleness_by_key[cache_key] = file_status(
                file_path=f["file"],
                reported_at_commit=f.get("reported_at_commit"),
                project_dir=cwd,
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
_CB_ID_RE = re.compile(r"\bCB-\d+\b")


def _parse_trailers(
    rev_range: str, *, project_dir: str | None = None
) -> list[tuple[str, str, str, str]]:
    """Return ``(verb, cb_id, sha, subject)`` for trailers in *rev_range*.

    ``verb`` is lowercased (``resolves`` / ``tightens``). Reads commit bodies
    via ``git log --no-merges``; returns ``[]`` if git is unavailable or the
    range is empty. Field-separated with control chars so subjects/bodies with
    newlines parse unambiguously.
    """
    cwd = project_dir or os.getcwd()
    fmt = "%x1e%H%x1f%s%x1f%B"
    try:
        out = subprocess.check_output(
            ["git", "log", "--no-merges", f"--pretty=format:{fmt}", rev_range],
            cwd=cwd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    trailers: list[tuple[str, str, str, str]] = []
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
                trailers.append((verb, cb_id, sha, subject))
    return trailers


def _append_note(finding: dict[str, Any], line: str) -> str:
    """Return the finding's existing notes with *line* appended (newline-joined)."""
    meta = finding.get("meta") or {}
    existing = meta.get("notes") if isinstance(meta, dict) else None
    return f"{existing}\n{line}" if existing else line


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
    seen: set[tuple[str, str]] = set()
    for verb, cb_id, sha, subject in _parse_trailers(rev_range, project_dir=project_dir):
        if (verb, cb_id) in seen:
            continue
        seen.add((verb, cb_id))
        try:
            current = findings.get_finding(conn, cb_id)
        except KeyError:
            report["missing"].append(cb_id)
            continue
        if verb == "resolves" and current["status"] in types.FINDING_TERMINAL:
            report["skipped"].append(cb_id)
            continue
        verb_label = "Resolved" if verb == "resolves" else "Tightened"
        note = _append_note(current, f"{verb_label} by commit {sha[:12]} ({subject}).")
        if not dry_run:
            findings.update_finding(
                conn,
                cb_id,
                status="fixed" if verb == "resolves" else None,
                notes=note,
            )
        report["resolved" if verb == "resolves" else "tightened"].append(cb_id)
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
        - unknown: can't determine (no provenance data, unreachable commit)

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
