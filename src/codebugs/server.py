"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from codebugs import db, reqs

mcp = FastMCP("codebugs", json_response=True)


def _conn():
    return db.connect()


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
    conn = _conn()
    try:
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
    finally:
        conn.close()


@mcp.tool()
def batch_add(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add multiple findings at once.

    Args:
        findings: List of finding objects, each with keys:
            severity, category, file, description, and optionally:
            source, tags, meta
    """
    conn = _conn()
    try:
        return db.batch_add_findings(conn, findings)
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return db.update_finding(
            conn,
            finding_id,
            status=status,
            notes=notes,
            tags=tags,
            meta_update=meta_update,
        )
    finally:
        conn.close()


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
    conn = _conn()
    try:
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
    finally:
        conn.close()


@mcp.tool()
def stats(group_by: str = "severity") -> dict[str, Any]:
    """Aggregated cross-tabulated counts.

    Args:
        group_by: Group by: severity, category, status, file, source
    """
    conn = _conn()
    try:
        return db.get_stats(conn, group_by=group_by)
    finally:
        conn.close()


@mcp.tool()
def summary() -> dict[str, Any]:
    """Dashboard overview — open/resolved counts, severity breakdown,
    top categories, hottest files. Start here for orientation."""
    conn = _conn()
    try:
        return db.get_summary(conn)
    finally:
        conn.close()


@mcp.tool()
def categories() -> list[dict[str, Any]]:
    """List all existing categories with counts.
    Call this before adding findings to reuse consistent category names."""
    conn = _conn()
    try:
        return db.get_categories(conn)
    finally:
        conn.close()


# --- Requirements tools ---


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
    conn = _conn()
    try:
        return reqs.add_requirement(
            conn, req_id=req_id, description=description, section=section,
            priority=priority, status=status, source=source,
            test_coverage=test_coverage, tags=tags, meta=meta,
        )
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return reqs.update_requirement(
            conn, req_id, status=status, description=description,
            priority=priority, test_coverage=test_coverage,
            notes=notes, tags=tags, meta_update=meta_update,
        )
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return reqs.query_requirements(
            conn, status=status, priority=priority, section=section,
            search=search, source=source, tag=tag,
            group_by=group_by, limit=limit, offset=offset,
        )
    finally:
        conn.close()


@mcp.tool()
def reqs_stats(group_by: str = "status") -> dict[str, Any]:
    """Aggregated requirement counts by status x priority.

    Args:
        group_by: Group by: status, priority, section, source
    """
    conn = _conn()
    try:
        return reqs.get_reqs_stats(conn, group_by=group_by)
    finally:
        conn.close()


@mcp.tool()
def reqs_summary() -> dict[str, Any]:
    """Dashboard overview — status breakdown, priority split,
    section progress, requirements without tests. Start here."""
    conn = _conn()
    try:
        return reqs.get_reqs_summary(conn)
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return reqs.verify_requirements(conn, project_dir=project_dir, checks=checks)
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return reqs.import_markdown(conn, markdown_path)
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return reqs.store_embedding(conn, req_id, embedding)
    finally:
        conn.close()


@mcp.tool()
def reqs_batch_embed(
    embeddings: dict[str, list[float]],
) -> dict[str, Any]:
    """Store embeddings for multiple requirements at once.

    Args:
        embeddings: Dict mapping requirement ID to float vector
    """
    conn = _conn()
    try:
        return reqs.batch_store_embeddings(conn, embeddings)
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return reqs.search_similar(
            conn, query_embedding, limit=limit,
            min_similarity=min_similarity, status=status,
        )
    finally:
        conn.close()


@mcp.tool()
def reqs_embedding_stats() -> dict[str, Any]:
    """Report on embedding coverage — how many requirements have embeddings."""
    conn = _conn()
    try:
        return reqs.embedding_stats(conn)
    finally:
        conn.close()


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
