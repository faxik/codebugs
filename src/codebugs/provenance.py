"""Provenance — staleness checks for findings against git history."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from typing import Any

from codebugs import db, findings


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
    """Register provenance CLI subcommands. (Currently none — staleness is MCP-only.)"""


db.register_tool_provider("provenance", register_tools)
db.register_cli_provider("provenance", register_cli)
