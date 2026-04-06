"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import contextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from codebugs import db, blockers


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _git_rev_parse(ref: str, *, silent: bool = False, cwd: str | None = None) -> str | None:
    """Run git rev-parse for a ref. Returns SHA or None if silent and git unavailable."""
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", ref],
            text=True, timeout=10,
            stderr=subprocess.DEVNULL if silent else None,
            cwd=cwd,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        if silent:
            return None
        raise


def _get_head_sha() -> str | None:
    """Get current HEAD SHA for provenance auto-population. Returns None if git unavailable."""
    return _git_rev_parse("HEAD", silent=True)


def _check_file_staleness(
    file_path: str,
    reported_at_commit: str | None,
    project_dir: str | None = None,
) -> dict[str, Any]:
    """Check staleness of a single file against a commit. Returns file_status dict."""
    import subprocess

    cwd = project_dir or os.getcwd()

    if not reported_at_commit:
        return {"file_status": "unknown", "reason": "no_provenance"}

    # Check if the commit is reachable
    try:
        subprocess.check_output(
            ["git", "cat-file", "-t", reported_at_commit],
            cwd=cwd, text=True, timeout=10, stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"file_status": "unknown", "reason": "unreachable_commit"}

    # Check if file was modified since the commit
    try:
        log_output = subprocess.check_output(
            ["git", "log", "--oneline", f"{reported_at_commit}..HEAD", "--", file_path],
            cwd=cwd, text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"file_status": "unknown", "reason": "git_error"}

    if not log_output:
        return {"file_status": "current", "reason": f"{file_path} unchanged since {reported_at_commit[:12]}"}

    commit_count = len(log_output.splitlines())

    # File was changed — check if it still exists at HEAD
    file_exists = os.path.isfile(os.path.join(cwd, file_path))

    if file_exists:
        s = "commit" if commit_count == 1 else "commits"
        return {
            "file_status": "modified",
            "reason": f"{file_path} modified in {commit_count} {s} since {reported_at_commit[:12]}",
        }

    # File doesn't exist — check for rename via git diff (not git log,
    # because log --diff-filter=R -- <old_path> won't match after rename)
    try:
        rename_output = subprocess.check_output(
            ["git", "diff", "--diff-filter=R", "-M", "--name-status",
             f"{reported_at_commit}..HEAD"],
            cwd=cwd, text=True, timeout=10, stderr=subprocess.DEVNULL,
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


def _staleness_check_impl(
    conn: sqlite3.Connection,
    project_dir: str | None,
    *,
    finding_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    """Core staleness check logic. Separated for testability."""
    cwd = project_dir or os.getcwd()

    if finding_id:
        row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        if not row:
            raise KeyError(f"Finding not found: {finding_id}")
        findings_list = [db._row_to_dict(row)]
    else:
        query_kwargs: dict[str, Any] = {"limit": 10000}
        if status:
            query_kwargs["status"] = status
        else:
            query_kwargs["status"] = "open"
        if category:
            query_kwargs["category"] = category
        if file:
            query_kwargs["file"] = file
        result = db.query_findings(conn, **query_kwargs)
        findings_list = result["findings"]

    current_head = _git_rev_parse("HEAD", silent=True, cwd=cwd)

    staleness_by_key: dict[tuple[str, str | None], dict[str, Any]] = {}
    results = []

    for f in findings_list:
        cache_key = (f["file"], f.get("reported_at_commit"))
        if cache_key not in staleness_by_key:
            staleness_by_key[cache_key] = _check_file_staleness(
                f["file"], f.get("reported_at_commit"), cwd,
            )
        staleness = staleness_by_key[cache_key]
        results.append({
            "finding_id": f["id"],
            "file": f["file"],
            "file_status": staleness["file_status"],
            "reason": staleness["reason"],
            "reported_at_commit": f.get("reported_at_commit"),
            "current_head": current_head,
        })

    return {"findings": results, "total": len(results)}


def register_findings_tools(mcp: FastMCP) -> None:
    """Register finding-tracker tools on the given MCP server."""

    @mcp.tool()
    def add(
        severity: str,
        category: str,
        file: str,
        description: str,
        source: str = "claude",
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        reported_at_commit: str | None = None,
        reported_at_ref: str | None = None,
    ) -> dict[str, Any]:
        """Add a code finding.

        Args:
            severity: critical, high, medium, or low
            category: Finding category (e.g. tz_naive_datetime, n_plus_one, missing_validation).
                      Call `categories` first to reuse existing category names.
            file: File path relative to project root
            description: What's wrong
            source: Who created this finding (default: claude)
            tags: Optional tags for grouping
            meta: Optional JSON metadata (lines, module, rule_code, etc.)
            reported_at_commit: Git SHA when finding was created (auto-detected from HEAD if omitted)
            reported_at_ref: Version/tag label (e.g. "v2.1.0"), always caller-supplied
        """
        if reported_at_commit is None:
            reported_at_commit = _get_head_sha()
        with _conn() as conn:
            return db.add_finding(
                conn,
                severity=severity,
                category=category,
                file=file,
                description=description,
                source=source,
                tags=tags,
                meta=meta,
                reported_at_commit=reported_at_commit,
                reported_at_ref=reported_at_ref,
            )

    @mcp.tool()
    def batch_add(
        findings: list[dict[str, Any]],
        reported_at_commit: str | None = None,
        reported_at_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """Add multiple findings at once.

        Args:
            findings: List of finding objects, each with keys:
                severity, category, file, description, and optionally:
                source, tags, meta, reported_at_commit, reported_at_ref
            reported_at_commit: Default commit SHA for all findings (auto-detected if omitted).
                                Per-finding values override this.
            reported_at_ref: Default version label for all findings.
                             Per-finding values override this.
        """
        default_commit = reported_at_commit if reported_at_commit is not None else _get_head_sha()
        enriched = []
        for f in findings:
            f = {**f}
            if "reported_at_commit" not in f:
                f["reported_at_commit"] = default_commit
            if "reported_at_ref" not in f and reported_at_ref is not None:
                f["reported_at_ref"] = reported_at_ref
            enriched.append(f)
        with _conn() as conn:
            return db.batch_add_findings(conn, enriched)

    @mcp.tool()
    def update(
        finding_id: str,
        status: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        meta_update: dict[str, Any] | None = None,
        reported_at_ref: str | None = None,
    ) -> dict[str, Any]:
        """Update a finding's status, notes, tags, or metadata.

        Args:
            finding_id: The finding ID (e.g. CB-1)
            status: New status: open, in_progress, fixed, not_a_bug, wont_fix, stale.
                    Aliases accepted: done/resolved/implemented/closed → fixed,
                    wontfix → wont_fix, invalid → not_a_bug,
                    active/working/in-progress → in_progress
            notes: Add/update notes (stored in meta.notes)
            tags: Replace tags list
            meta_update: Merge additional metadata keys
            reported_at_ref: Update version/tag label (e.g. "v2.1.0")
        """
        with _conn() as conn:
            result = db.update_finding(
                conn,
                finding_id,
                status=status,
                notes=notes,
                tags=tags,
                meta_update=meta_update,
                reported_at_ref=reported_at_ref,
            )
            if status and result.get("status") in blockers.TERMINAL_STATUSES.get(blockers.ENTITY_FINDING, set()):
                unblocked = blockers.get_unblocked_by(conn, finding_id, blockers.ENTITY_FINDING)
                if unblocked:
                    result["unblocked_items"] = unblocked
            return result

    @mcp.tool()
    def query(
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        file: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        meta_key: str | None = None,
        meta_value: str | None = None,
        commit: str | None = None,
        ref: str | None = None,
        group_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search and filter findings. Returns structured results.

        Args:
            status: Filter by status (open, in_progress, fixed, not_a_bug, wont_fix, stale, deferred). Aliases accepted.
                    Use 'deferred' to find items with active blockers.
            severity: Filter by severity (critical, high, medium, low)
            category: Filter by exact category
            file: Filter by file path (substring match)
            source: Filter by source (claude, ruff, human, etc.)
            tag: Filter by tag (finds findings containing this tag)
            meta_key: Filter by metadata key existence
            meta_value: Filter by metadata value (requires meta_key)
            commit: Filter by reported_at_commit (prefix match, hex validated)
            ref: Filter by reported_at_ref (exact match)
            group_by: Group results by: file, category, severity, status, source
            limit: Max results (default 100)
            offset: Pagination offset
        """
        with _conn() as conn:
            if status == "deferred":
                return blockers.query_deferred_entities(conn, blockers.ENTITY_FINDING, limit=limit, offset=offset)
            return db.query_findings(
                conn,
                status=status,
                severity=severity,
                category=category,
                file=file,
                source=source,
                tag=tag,
                meta_key=meta_key,
                meta_value=meta_value,
                commit=commit,
                ref=ref,
                group_by=group_by,
                limit=limit,
                offset=offset,
            )

    @mcp.tool()
    def stats(group_by: str = "severity") -> dict[str, Any]:
        """Aggregated cross-tabulated counts.

        Args:
            group_by: Group by: severity, category, status, file, source
        """
        with _conn() as conn:
            return db.get_stats(conn, group_by=group_by)

    @mcp.tool()
    def summary() -> dict[str, Any]:
        """Dashboard overview — open/resolved counts, severity breakdown,
        top categories, hottest files, deferred counts. Start here for orientation."""
        with _conn() as conn:
            result = db.get_summary(conn)
            result.update(blockers.get_deferred_counts(conn, blockers.ENTITY_FINDING))
            return result

    @mcp.tool()
    def categories() -> list[dict[str, Any]]:
        """List all existing categories with counts.
        Call this before adding findings to reuse consistent category names."""
        with _conn() as conn:
            return db.get_categories(conn)

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
        with _conn() as conn:
            return _staleness_check_impl(conn, None, finding_id=finding_id,
                                          status=status, category=category, file=file)


def main():
    """Run the MCP server with optional mode selection."""
    parser = argparse.ArgumentParser(description="Codebugs MCP server")
    parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "bench", "blockers", "all"],
        default="all",
        help="Which tools to expose: findings, reqs, merge, sweep, bench, blockers, or all (default: all)",
    )
    args = parser.parse_args()

    name = {"findings": "codebugs", "reqs": "codereqs", "merge": "codemerge", "sweep": "codesweep", "bench": "codebench", "blockers": "codeblockers", "all": "codebugs"}[args.mode]
    server = FastMCP(name, json_response=True)

    if args.mode in ("findings", "all"):
        register_findings_tools(server)
    if args.mode in ("reqs", "all"):
        from codebugs.reqs import register_tools as reqs_tools
        reqs_tools(server, _conn)
    if args.mode in ("merge", "all"):
        from codebugs.merge import register_tools as merge_tools
        merge_tools(server, _conn)
    if args.mode in ("sweep", "all"):
        from codebugs.sweep import register_tools as sweep_tools
        sweep_tools(server, _conn)
    if args.mode in ("bench", "all"):
        from codebugs.bench import register_tools as bench_tools
        bench_tools(server, _conn)
    if args.mode in ("blockers", "all"):
        from codebugs.blockers import register_tools as blockers_tools
        blockers_tools(server, _conn)

    server.run()


if __name__ == "__main__":
    main()
