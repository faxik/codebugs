"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from codebugs import db, reqs


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _get_main_head() -> str:
    """Get current main branch HEAD SHA. Used by merge tools that need git."""
    import subprocess
    return subprocess.check_output(
        ["git", "rev-parse", "main"],
        text=True, timeout=10,
    ).strip()


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
        """
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
            )

    @mcp.tool()
    def batch_add(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add multiple findings at once.

        Args:
            findings: List of finding objects, each with keys:
                severity, category, file, description, and optionally:
                source, tags, meta
        """
        with _conn() as conn:
            return db.batch_add_findings(conn, findings)

    @mcp.tool()
    def update(
        finding_id: str,
        status: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        meta_update: dict[str, Any] | None = None,
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
        """
        with _conn() as conn:
            return db.update_finding(
                conn,
                finding_id,
                status=status,
                notes=notes,
                tags=tags,
                meta_update=meta_update,
            )

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
        group_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search and filter findings. Returns structured results.

        Args:
            status: Filter by status (open, in_progress, fixed, not_a_bug, wont_fix, stale). Aliases accepted.
            severity: Filter by severity (critical, high, medium, low)
            category: Filter by exact category
            file: Filter by file path (substring match)
            source: Filter by source (claude, ruff, human, etc.)
            tag: Filter by tag (finds findings containing this tag)
            meta_key: Filter by metadata key existence
            meta_value: Filter by metadata value (requires meta_key)
            group_by: Group results by: file, category, severity, status, source
            limit: Max results (default 100)
            offset: Pagination offset
        """
        with _conn() as conn:
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
        top categories, hottest files. Start here for orientation."""
        with _conn() as conn:
            return db.get_summary(conn)

    @mcp.tool()
    def categories() -> list[dict[str, Any]]:
        """List all existing categories with counts.
        Call this before adding findings to reuse consistent category names."""
        with _conn() as conn:
            return db.get_categories(conn)


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
            test_coverage: Updated test file reference
            notes: Notes (stored in meta.notes)
            tags: Replace tags
            meta_update: Merge metadata keys
        """
        with _conn() as conn:
            return reqs.update_requirement(
                conn, req_id, status=status, description=description,
                priority=priority, test_coverage=test_coverage,
                notes=notes, tags=tags, meta_update=meta_update,
            )

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
            status: Filter by status (Planned, Partial, Implemented, Verified, Superseded, Obsolete)
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
        section progress, requirements without tests. Start here."""
        with _conn() as conn:
            return reqs.get_reqs_summary(conn)

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


def main():
    """Run the MCP server with optional mode selection."""
    parser = argparse.ArgumentParser(description="Codebugs MCP server")
    parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "all"],
        default="all",
        help="Which tools to expose: findings, reqs, merge, sweep, or all (default: all)",
    )
    args = parser.parse_args()

    name = {"findings": "codebugs", "reqs": "codereqs", "merge": "codemerge", "sweep": "codesweep", "all": "codebugs"}[args.mode]
    server = FastMCP(name, json_response=True)

    if args.mode in ("findings", "all"):
        register_findings_tools(server)
    if args.mode in ("reqs", "all"):
        register_reqs_tools(server)
    if args.mode in ("merge", "all"):
        register_merge_tools(server)
    if args.mode in ("sweep", "all"):
        register_sweep_tools(server)

    server.run()


if __name__ == "__main__":
    main()
