"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from codebugs import db

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
        status: New status: open, fixed, not_a_bug, wont_fix, stale
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
        status: Filter by status (open, fixed, not_a_bug, wont_fix, stale)
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


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
