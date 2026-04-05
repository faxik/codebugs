"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import contextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from codebugs import db, reqs, bench, blockers


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


def _get_main_head() -> str:
    """Get current main branch HEAD SHA. Used by merge tools that need git."""
    result = _git_rev_parse("main")
    assert result is not None
    return result


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


def register_reqs_tools(mcp: FastMCP) -> None:
    """Register requirements-tracker tools on the given MCP server."""

    @mcp.tool()
    def reqs_add(
        req_id: str,
        description: str,
        section: str = "",
        priority: str = "Should",
        status: str = "Planned",
        source: str = "",
        test_coverage: str = "",
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a requirement.

        Args:
            req_id: Requirement ID (e.g. FR-001)
            description: What the system shall do
            section: Section name (e.g. "1.10 Document Sorting")
            priority: Must, Should, or Could
            status: Planned, Partial, Implemented, Verified, Superseded, Obsolete
            source: Where this requirement came from (e.g. Take 26, NEW)
            test_coverage: Test file name(s)
            tags: Optional tags
            meta: Optional metadata
        """
        with _conn() as conn:
            return reqs.add_requirement(
                conn, req_id=req_id, description=description, section=section,
                priority=priority, status=status, source=source,
                test_coverage=test_coverage, tags=tags, meta=meta,
            )

    @mcp.tool()
    def reqs_update(
        req_id: str,
        status: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        section: str | None = None,
        test_coverage: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        meta_update: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a requirement's status, description, or metadata.

        Args:
            req_id: Requirement ID (e.g. FR-001)
            status: New status: Planned, Partial, Implemented, Verified, Superseded, Obsolete
            description: Updated description
            priority: Updated priority: Must, Should, Could
            section: Updated section name
            test_coverage: Updated test file reference
            notes: Notes (stored in meta.notes)
            tags: Replace tags
            meta_update: Merge metadata keys
        """
        with _conn() as conn:
            result = reqs.update_requirement(
                conn, req_id, status=status, description=description,
                priority=priority, section=section, test_coverage=test_coverage,
                notes=notes, tags=tags, meta_update=meta_update,
            )
            if status and result.get("status") in blockers.TERMINAL_STATUSES.get(blockers.ENTITY_REQUIREMENT, set()):
                unblocked = blockers.get_unblocked_by(conn, req_id, blockers.ENTITY_REQUIREMENT)
                if unblocked:
                    result["unblocked_items"] = unblocked
            return result

    @mcp.tool()
    def reqs_query(
        status: str | None = None,
        priority: str | None = None,
        section: str | None = None,
        search: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        group_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search and filter requirements.

        Args:
            status: Filter by status (Planned, Partial, Implemented, Verified, Superseded, Obsolete, deferred).
                    Use 'deferred' to find requirements with active blockers.
            priority: Filter by priority (Must, Should, Could)
            section: Filter by section (substring match)
            search: Search in description and ID
            source: Filter by source (substring match)
            tag: Filter by tag
            group_by: Group by: section, status, priority, source
            limit: Max results (default 100)
            offset: Pagination offset
        """
        with _conn() as conn:
            if status == "deferred":
                return blockers.query_deferred_entities(conn, blockers.ENTITY_REQUIREMENT, limit=limit, offset=offset)
            return reqs.query_requirements(
                conn, status=status, priority=priority, section=section,
                search=search, source=source, tag=tag,
                group_by=group_by, limit=limit, offset=offset,
            )

    @mcp.tool()
    def reqs_stats(group_by: str = "status") -> dict[str, Any]:
        """Aggregated requirement counts by status x priority.

        Args:
            group_by: Group by: status, priority, section, source
        """
        with _conn() as conn:
            return reqs.get_reqs_stats(conn, group_by=group_by)

    @mcp.tool()
    def reqs_summary() -> dict[str, Any]:
        """Dashboard overview — status breakdown, priority split,
        section progress, requirements without tests, deferred counts. Start here."""
        with _conn() as conn:
            result = reqs.get_reqs_summary(conn)
            result.update(blockers.get_deferred_counts(conn, blockers.ENTITY_REQUIREMENT))
            return result

    @mcp.tool()
    def reqs_verify(
        checks: list[str] | None = None,
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """Verify requirements for issues.

        Runs automated checks to find problems:
        - tests: do referenced test files actually exist?
        - ids: duplicate IDs, numbering gaps
        - status: contradictions (description says superseded but status says Planned)

        Args:
            checks: List of checks to run (default: all). Options: tests, ids, status
            project_dir: Project root for test file verification (default: cwd)
        """
        with _conn() as conn:
            return reqs.verify_requirements(conn, project_dir=project_dir, checks=checks)

    @mcp.tool()
    def reqs_import(
        markdown_path: str,
    ) -> dict[str, Any]:
        """Import requirements from a REQUIREMENTS.md file.

        Parses markdown tables with columns:
        | ID | Requirement | Priority | Status | Source | Test Coverage |

        Uses INSERT OR REPLACE, so re-importing updates existing entries.

        Args:
            markdown_path: Path to the REQUIREMENTS.md file
        """
        with _conn() as conn:
            return reqs.import_markdown(conn, markdown_path)

    @mcp.tool()
    def reqs_embed(
        req_id: str,
        embedding: list[float],
    ) -> dict[str, Any]:
        """Store an embedding vector for a requirement.

        The caller generates the embedding (e.g. via an embedding API).
        Enables semantic search across requirements via reqs_search_similar.

        Args:
            req_id: Requirement ID
            embedding: Float vector (any dimensionality)
        """
        with _conn() as conn:
            return reqs.store_embedding(conn, req_id, embedding)

    @mcp.tool()
    def reqs_batch_embed(
        embeddings: dict[str, list[float]],
    ) -> dict[str, Any]:
        """Store embeddings for multiple requirements at once.

        Args:
            embeddings: Dict mapping requirement ID to float vector
        """
        with _conn() as conn:
            return reqs.batch_store_embeddings(conn, embeddings)

    @mcp.tool()
    def reqs_search_similar(
        query_embedding: list[float],
        limit: int = 10,
        min_similarity: float = 0.3,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find requirements semantically similar to a query.

        Pass a query embedding (from the same model used to embed requirements).
        Returns requirements ranked by cosine similarity.

        Args:
            query_embedding: Query vector
            limit: Max results (default 10)
            min_similarity: Minimum cosine similarity (default 0.3)
            status: Optional status filter
        """
        with _conn() as conn:
            return reqs.search_similar(
                conn, query_embedding, limit=limit,
                min_similarity=min_similarity, status=status,
            )

    @mcp.tool()
    def reqs_embedding_stats() -> dict[str, Any]:
        """Report on embedding coverage — how many requirements have embeddings."""
        with _conn() as conn:
            return reqs.embedding_stats(conn)


def register_merge_tools(mcp: FastMCP) -> None:
    """Register merge-coordination tools on the given MCP server."""

    @mcp.tool()
    def codemerge_start(
        session_id: str,
        branch: str,
        description: str = "",
        base_commit: str = "",
        repo_root: str = "",
        allow_restart: bool = False,
    ) -> dict[str, Any]:
        """Start a new merge session for a branch.

        Args:
            session_id: Unique identifier for this merge session
            branch: Git branch name being merged
            description: Human-readable description of the work
            base_commit: Git commit SHA this branch diverged from
            repo_root: Repo root path (default: cwd)
            allow_restart: If True, restart an existing active session
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.start_session(
                conn,
                session_id=session_id,
                branch=branch,
                description=description,
                base_commit=base_commit,
                repo_root=repo_root,
                allow_restart=allow_restart,
            )

    @mcp.tool()
    def codemerge_claim(
        session_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Claim a file as being modified by this session.

        Args:
            session_id: The merge session ID
            file_path: File path being modified (relative to repo root)
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.add_claim(conn, session_id, file_path)

    @mcp.tool()
    def codemerge_check(
        session_id: str,
        main_changed_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check for overlapping file claims with other sessions.

        Returns whether the session is clean to proceed, lists any conflicts,
        and records the current main HEAD for CAS comparison at merge time.

        Args:
            session_id: The merge session ID
            main_changed_files: Files changed on main since base (optional, for overlap check)
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.check_overlaps(
                conn,
                session_id,
                main_changed_files=main_changed_files,
                current_main_head_fn=_get_main_head,
            )

    @mcp.tool()
    def codemerge_merge(
        session_id: str,
        expected_main_head: str,
    ) -> dict[str, Any]:
        """Acquire the merge lock and proceed with merging.

        Uses compare-and-swap on main HEAD to prevent races. If main has moved
        since check, returns proceed=False with reason='main_moved'. If another
        session holds the lock, returns proceed=False with reason='lock_held'.

        Args:
            session_id: The merge session ID
            expected_main_head: The main HEAD SHA recorded during codemerge_check
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.merge(
                conn,
                session_id,
                expected_main_head=expected_main_head,
                current_main_head_fn=_get_main_head,
            )

    @mcp.tool()
    def codemerge_finish(
        session_id: str,
        success: bool = True,
    ) -> dict[str, Any]:
        """Finish a merge session and release the lock.

        Args:
            session_id: The merge session ID
            success: True if merge succeeded (status→done), False if it failed (status→abandoned)
        """
        from codebugs import merge
        with _conn() as conn:
            return merge.finish(conn, session_id, success=success)


def register_sweep_tools(mcp: FastMCP) -> None:
    """Register sweep batch-iteration tools on the given MCP server."""

    @mcp.tool()
    def codesweep_create(
        name: str | None = None,
        description: str = "",
        default_batch_size: int = 10,
    ) -> dict[str, Any]:
        """Create a new sweep for batch iteration over items.

        Args:
            name: Optional human-readable name (must be unique)
            description: What this sweep is for
            default_batch_size: Default items per batch (default: 10)
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.create_sweep(
                conn, name=name, description=description,
                default_batch_size=default_batch_size,
            )

    @mcp.tool()
    def codesweep_add(
        sweep_ref: str,
        items: list[str],
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add items to a sweep. Duplicates are silently skipped.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            items: Item identifiers to add
            tags: Optional tags applied to all items in this batch
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.add_items(conn, sweep_ref, items, tags=tags)

    @mcp.tool()
    def codesweep_next(
        sweep_ref: str,
        limit: int | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get next batch of unprocessed items in insertion order.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            limit: Batch size (overrides sweep default)
            tags: Filter to items matching any of these tags
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.next_batch(conn, sweep_ref, limit=limit, tags=tags)

    @mcp.tool()
    def codesweep_mark(
        sweep_ref: str,
        items: list[str],
        processed: bool = True,
    ) -> dict[str, Any]:
        """Mark items as processed or unprocessed.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
            items: Item identifiers to mark
            processed: True to mark processed (default), False to unmark
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.mark_items(conn, sweep_ref, items, processed=processed)

    @mcp.tool()
    def codesweep_status(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Sweep overview — total, processed, remaining counts, per-tag breakdown.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.get_status(conn, sweep_ref)

    @mcp.tool()
    def codesweep_archive(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Archive a sweep. Archived sweeps are excluded from codesweep_list by default.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.archive_sweep(conn, sweep_ref)

    @mcp.tool()
    def codesweep_list(
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List all sweeps with summary counts.

        Args:
            include_archived: Include archived sweeps (default: false)
        """
        from codebugs import sweep
        with _conn() as conn:
            return sweep.list_sweeps(conn, include_archived=include_archived)


def register_bench_tools(mcp: FastMCP) -> None:
    """Register benchmark result tools on the given MCP server."""

    @mcp.tool()
    def codebench_import(
        benchmark: str,
        csv_data: str | None = None,
        json_data: str | None = None,
        date: str | None = None,
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Import benchmark results from CSV or JSON.

        CSV convention: first column is the row label, remaining columns are
        metric names with numeric values.

        JSON convention: array of objects, first key is the row label, rest
        are metric keys with numeric values.

        Args:
            benchmark: Benchmark name (e.g. "search-perf")
            csv_data: CSV string (header + data rows). Provide csv_data OR json_data.
            json_data: JSON array string. Provide csv_data OR json_data.
            date: Run date (default: today, ISO format YYYY-MM-DD)
            tags: Optional tags (e.g. ["nightly", "v2.1"])
            meta: Optional metadata (e.g. {"git_sha": "abc123", "ci_url": "..."})
        """
        if not csv_data and not json_data:
            raise ValueError("Provide either csv_data or json_data")
        if csv_data and json_data:
            raise ValueError("Provide csv_data or json_data, not both")
        with _conn() as conn:
            if csv_data:
                return bench.import_csv(
                    conn, benchmark=benchmark, csv_data=csv_data,
                    date=date, tags=tags, meta=meta,
                )
            return bench.import_json(
                conn, benchmark=benchmark, json_data=json_data,
                date=date, tags=tags, meta=meta,
            )

    @mcp.tool()
    def codebench_query(
        benchmark: str,
        runs: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        metrics: list[str] | None = None,
        rows: list[str] | None = None,
        group_by: str = "row",
        last_n: int | None = None,
        format: str = "json",
    ) -> dict[str, Any]:
        """Query and pivot benchmark results.

        group_by="row": original table shape (row_labels as rows, metrics as
        columns). Returns one table per run.

        group_by="run": trend view (runs as rows, metrics as columns).
        Returns one table per row_label.

        Args:
            benchmark: Benchmark name to query
            runs: Specific run IDs (default: all matching)
            date_from: Start date filter (inclusive, YYYY-MM-DD)
            date_to: End date filter (inclusive, YYYY-MM-DD)
            metrics: Which metrics to include (default: all)
            rows: Which row_labels to include (default: all)
            group_by: Pivot axis — "row" or "run"
            last_n: Limit to last N runs by date
            format: Output — "json" or "csv"
        """
        with _conn() as conn:
            return bench.query(
                conn, benchmark=benchmark, runs=runs,
                date_from=date_from, date_to=date_to,
                metrics=metrics, rows=rows, group_by=group_by,
                last_n=last_n, format=format,
            )

    @mcp.tool()
    def codebench_list(
        benchmark: str | None = None,
        last_n: int | None = None,
    ) -> dict[str, Any]:
        """List benchmarks or runs.

        Without benchmark: lists all benchmark names with run counts.
        With benchmark: lists runs for that benchmark.

        Args:
            benchmark: If provided, list runs for this benchmark
            last_n: Limit to last N runs (only when benchmark is provided)
        """
        with _conn() as conn:
            if benchmark:
                return bench.list_runs(conn, benchmark=benchmark, last_n=last_n)
            return bench.list_benchmarks(conn)

    @mcp.tool()
    def codebench_delete(
        run_id: str | None = None,
        benchmark: str | None = None,
    ) -> dict[str, Any]:
        """Delete a single run or all runs for a benchmark.

        Args:
            run_id: Delete a specific run (e.g. "BE-1")
            benchmark: Delete all runs for a benchmark name
        """
        if not run_id and not benchmark:
            raise ValueError("Provide run_id or benchmark")
        if run_id and benchmark:
            raise ValueError("Provide run_id or benchmark, not both")
        with _conn() as conn:
            if run_id:
                return bench.delete_run(conn, run_id)
            return bench.delete_benchmark(conn, benchmark)


def register_blockers_tools(mcp: FastMCP) -> None:
    """Register blocker/dependency tools on the given MCP server."""

    @mcp.tool()
    def blockers_add(
        item_id: str,
        reason: str,
        blocked_by: str | None = None,
        trigger_type: str | None = None,
        trigger_at: str | None = None,
    ) -> dict[str, Any]:
        """Defer an item by adding a blocker.

        Args:
            item_id: The blocked entity (e.g. "CB-5", "FR-012")
            reason: Why it's blocked
            blocked_by: Dependency entity (e.g. "CB-3"). Required for entity_resolved triggers.
            trigger_type: entity_resolved, date, or manual.
                          Defaults to entity_resolved if blocked_by provided, manual otherwise.
            trigger_at: Date/datetime for date triggers (e.g. "2026-04-10"). Normalized to UTC.
        """
        with _conn() as conn:
            return blockers.add_blocker(
                conn, item_id=item_id, reason=reason, blocked_by=blocked_by,
                trigger_type=trigger_type, trigger_at=trigger_at,
            )

    @mcp.tool()
    def blockers_query(
        item_id: str | None = None,
        blocked_by: str | None = None,
        trigger_type: str | None = None,
        active_only: bool = True,
    ) -> dict[str, Any]:
        """List blockers with filters. Each result includes computed satisfaction state.

        Args:
            item_id: Filter by blocked item (e.g. "CB-5")
            blocked_by: Filter by dependency ("what does CB-3 unblock?")
            trigger_type: Filter by trigger type (entity_resolved, date, manual)
            active_only: Only unsatisfied, uncancelled blockers (default: true)
        """
        with _conn() as conn:
            return blockers.query_blockers(
                conn, item_id=item_id, blocked_by=blocked_by,
                trigger_type=trigger_type, active_only=active_only,
            )

    @mcp.tool()
    def blockers_check() -> dict[str, Any]:
        """Scan for currently actionable items — items whose blockers are all satisfied.

        Returns actionable items (all blockers met), partially unblocked items
        (some blockers met), and overdue date triggers.
        """
        with _conn() as conn:
            return blockers.check_blockers(conn)

    @mcp.tool()
    def blockers_resolve(
        blocker_id: int,
        action: str,
    ) -> dict[str, Any]:
        """Cancel or manually resolve a blocker.

        Args:
            blocker_id: The blocker row ID
            action: 'cancel' (any trigger type) or 'resolve' (manual triggers only)
        """
        with _conn() as conn:
            return blockers.resolve_blocker(conn, blocker_id=blocker_id, action=action)


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
        register_reqs_tools(server)
    if args.mode in ("merge", "all"):
        register_merge_tools(server)
    if args.mode in ("sweep", "all"):
        register_sweep_tools(server)
    if args.mode in ("bench", "all"):
        register_bench_tools(server)
    if args.mode in ("blockers", "all"):
        register_blockers_tools(server)

    server.run()


if __name__ == "__main__":
    main()
