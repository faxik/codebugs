"""Findings domain — CRUD, query, stats, MCP tools, and CLI for code findings."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from typing import Any

from codebugs import db
from codebugs.types import SEVERITIES, resolve_finding_status, utc_now

SCHEMA = """\
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    category TEXT NOT NULL,
    file TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
    description TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'human',
    tags TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    reported_at_commit TEXT,
    reported_at_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Initialize the findings schema (tables, indexes, migrations)."""
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    _migrate_statuses(conn)
    _migrate_findings_add_provenance_columns(conn)


def _migrate_statuses(conn: sqlite3.Connection) -> None:
    """Add 'in_progress' to the status CHECK constraint on existing databases."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='findings'"
    ).fetchone()
    if row is None:
        return
    ddl = row[0] or ""
    if "in_progress" in ddl:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """CREATE TABLE findings_new (
            id TEXT PRIMARY KEY,
            severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
            category TEXT NOT NULL,
            file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
            description TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'human',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            reported_at_commit TEXT,
            reported_at_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """INSERT INTO findings_new
           (id, severity, category, file, status, description, source, tags, meta, created_at, updated_at)
           SELECT id, severity, category, file, status, description, source, tags, meta, created_at, updated_at
           FROM findings"""
    )
    conn.execute("DROP TABLE findings")
    conn.execute("ALTER TABLE findings_new RENAME TO findings")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _migrate_findings_add_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add provenance columns to existing databases that already passed status migration.

    Schema ownership follows table ownership: these columns live on the findings table,
    so the migration lives here even though the columns are used by provenance.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
    if "reported_at_commit" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_commit TEXT")
    if "reported_at_ref" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_ref TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)"
        )
    conn.commit()


def _next_id(conn: sqlite3.Connection) -> str:
    """Generate next CB-N id."""
    row = conn.execute(
        "SELECT id FROM findings WHERE id LIKE 'CB-%' ORDER BY CAST(SUBSTR(id, 4) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if row:
        match = re.search(r"CB-(\d+)", row["id"])
        n = int(match.group(1)) + 1 if match else 1
    else:
        n = 1
    return f"CB-{n}"


def add_finding(
    conn: sqlite3.Connection,
    *,
    severity: str,
    category: str,
    file: str,
    description: str,
    source: str = "human",
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    finding_id: str | None = None,
    reported_at_commit: str | None = None,
    reported_at_ref: str | None = None,
) -> dict[str, Any]:
    """Add a single finding. Returns the created finding as a dict."""
    if severity not in SEVERITIES:
        raise ValueError(f"Invalid severity: {severity}. Must be one of {SEVERITIES}")

    fid = finding_id or _next_id(conn)
    now = utc_now()
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps(meta or {})

    conn.execute(
        """INSERT INTO findings (id, severity, category, file, status, description,
           source, tags, meta, reported_at_commit, reported_at_ref, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fid,
            severity,
            category,
            file,
            description,
            source,
            tags_json,
            meta_json,
            reported_at_commit,
            reported_at_ref,
            now,
            now,
        ),
    )
    result = db.row_to_dict(conn.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone())
    db.run_post_add_hooks(conn, result)
    conn.commit()
    return result


def batch_add_findings(
    conn: sqlite3.Connection,
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add multiple findings at once. Returns list of created findings.

    Contract: N INSERTs, one bulk SELECT, N hook fires, exactly ONE commit.
    MUST NOT delegate to add_finding() in a loop (that produces N commits).
    """
    now = utc_now()
    results = []
    for f in findings:
        severity = f.get("severity", "medium")
        if severity not in SEVERITIES:
            raise ValueError(f"Invalid severity: {severity}")

        fid = f.get("id") or _next_id(conn)
        tags_json = json.dumps(f.get("tags", []))
        meta_json = json.dumps(f.get("meta", {}))
        reported_at_commit = f.get("reported_at_commit")
        reported_at_ref = f.get("reported_at_ref")

        conn.execute(
            """INSERT INTO findings (id, severity, category, file, status, description,
               source, tags, meta, reported_at_commit, reported_at_ref, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fid,
                severity,
                f["category"],
                f["file"],
                f["description"],
                f.get("source", "human"),
                tags_json,
                meta_json,
                reported_at_commit,
                reported_at_ref,
                now,
                now,
            ),
        )
        results.append(fid)

    rows = conn.execute(
        f"SELECT * FROM findings WHERE id IN ({','.join('?' for _ in results)})",
        results,
    ).fetchall()
    finding_dicts = [db.row_to_dict(r) for r in rows]
    for fd in finding_dicts:
        db.run_post_add_hooks(conn, fd)
    conn.commit()
    return finding_dicts


def update_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    *,
    status: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    meta_update: dict[str, Any] | None = None,
    reported_at_ref: str | None = None,
) -> dict[str, Any]:
    """Update a finding. Returns updated finding.

    Note: reported_at_commit is intentionally excluded — it is immutable after insert.
    """
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        raise KeyError(f"Finding not found: {finding_id}")

    updates = []
    params: list[Any] = []

    if status is not None:
        status = resolve_finding_status(status)
        updates.append("status = ?")
        params.append(status)

    if notes is not None:
        existing_meta = json.loads(row["meta"])
        existing_meta["notes"] = notes
        updates.append("meta = ?")
        params.append(json.dumps(existing_meta))

    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags))

    if meta_update is not None:
        existing_meta = json.loads(row["meta"])
        existing_meta.update(meta_update)
        updates.append("meta = ?")
        params.append(json.dumps(existing_meta))

    if reported_at_ref is not None:
        updates.append("reported_at_ref = ?")
        params.append(reported_at_ref)

    if not updates:
        return db.row_to_dict(row)

    updates.append("updated_at = ?")
    params.append(utc_now())
    params.append(finding_id)

    conn.execute(f"UPDATE findings SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return db.row_to_dict(
        conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    )


def get_finding(conn: sqlite3.Connection, finding_id: str) -> dict[str, Any]:
    """Fetch a single finding by ID. Raises KeyError if not found."""
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        raise KeyError(f"Finding not found: {finding_id}")
    return db.row_to_dict(row)


def query_findings(
    conn: sqlite3.Connection,
    *,
    id: str | None = None,
    ids: list[str] | None = None,
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
    """Query findings with filters. Returns results or grouped counts.

    `id` / `ids` are AND-combined with other filters; missing IDs are silently absent.
    """
    conditions: list[str] = []
    params: list[Any] = []

    if id:
        conditions.append("id = ?")
        params.append(id)
    if ids:
        conditions.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
        if limit < len(ids):
            limit = len(ids)
    if status:
        conditions.append("status = ?")
        params.append(resolve_finding_status(status))
    if severity:
        conditions.append("severity = ?")
        params.append(severity)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if file:
        conditions.append("file LIKE ?")
        params.append(f"%{file}%")
    if source:
        conditions.append("source = ?")
        params.append(source)
    if tag:
        conditions.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)")
        params.append(tag)
    if meta_key and meta_value:
        conditions.append("json_extract(meta, ?) = ?")
        params.append(f"$.{meta_key}")
        params.append(meta_value)
    elif meta_key:
        conditions.append("json_extract(meta, ?) IS NOT NULL")
        params.append(f"$.{meta_key}")
    if commit:
        if not re.fullmatch(r"[0-9a-fA-F]+", commit):
            raise ValueError(f"commit filter must be hex, got: {commit!r}")
        conditions.append("reported_at_commit LIKE ? || '%'")
        params.append(commit.lower())
    if ref:
        conditions.append("reported_at_ref = ?")
        params.append(ref)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if group_by:
        valid_groups = ("file", "category", "severity", "status", "source")
        if group_by not in valid_groups:
            raise ValueError(f"Invalid group_by: {group_by}. Must be one of {valid_groups}")
        rows = conn.execute(
            f"SELECT {group_by} as group_key, COUNT(*) as count FROM findings {where} GROUP BY {group_by} ORDER BY count DESC",
            params,
        ).fetchall()
        return {"grouped": True, "group_by": group_by, "groups": [dict(r) for r in rows]}

    count = conn.execute(f"SELECT COUNT(*) as c FROM findings {where}", params).fetchone()["c"]
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM findings {where} ORDER BY severity, created_at DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return {
        "grouped": False,
        "total": count,
        "limit": limit,
        "offset": offset,
        "findings": [db.row_to_dict(r) for r in rows],
    }


def get_stats(
    conn: sqlite3.Connection,
    *,
    group_by: str = "severity",
) -> dict[str, Any]:
    """Aggregated counts. Returns cross-tabulated stats."""
    valid_groups = ("severity", "category", "status", "file", "source")
    if group_by not in valid_groups:
        raise ValueError(f"Invalid group_by: {group_by}. Must be one of {valid_groups}")

    rows = conn.execute(
        f"""SELECT {group_by} as grp, severity, COUNT(*) as cnt
            FROM findings
            GROUP BY grp, severity
            ORDER BY grp, severity"""
    ).fetchall()

    groups: dict[str, dict[str, int]] = {}
    for r in rows:
        grp = r["grp"]
        if grp not in groups:
            groups[grp] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        groups[grp][r["severity"]] = r["cnt"]
        groups[grp]["total"] += r["cnt"]

    return {"group_by": group_by, "groups": groups}


def get_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Dashboard-style overview."""
    total = conn.execute("SELECT COUNT(*) as c FROM findings").fetchone()["c"]
    by_status = {}
    for r in conn.execute("SELECT status, COUNT(*) as c FROM findings GROUP BY status"):
        by_status[r["status"]] = r["c"]

    by_severity = {}
    for r in conn.execute(
        "SELECT severity, COUNT(*) as c FROM findings WHERE status = 'open' GROUP BY severity ORDER BY severity"
    ):
        by_severity[r["severity"]] = r["c"]

    open_count = by_status.get("open", 0)

    top_categories = []
    for r in conn.execute(
        "SELECT category, COUNT(*) as c FROM findings WHERE status = 'open' GROUP BY category ORDER BY c DESC LIMIT 5"
    ):
        top_categories.append({"category": r["category"], "count": r["c"]})

    hottest_files = []
    for r in conn.execute(
        """SELECT file, COUNT(*) as total_open,
                  SUM(CASE WHEN severity IN ('critical', 'high') THEN 1 ELSE 0 END) as crit_high
           FROM findings WHERE status = 'open'
           GROUP BY file ORDER BY crit_high DESC, total_open DESC LIMIT 5"""
    ):
        hottest_files.append(
            {
                "file": r["file"],
                "open": r["total_open"],
                "critical_high": r["crit_high"],
            }
        )

    return {
        "total": total,
        "open": open_count,
        "resolved": total - open_count,
        "by_status": by_status,
        "open_by_severity": by_severity,
        "top_categories": top_categories,
        "hottest_files": hottest_files,
    }


def get_categories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List all categories with counts, for consistency checking."""
    rows = conn.execute(
        """SELECT category, COUNT(*) as total,
                  SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_count,
                  SUM(CASE WHEN status = 'fixed' THEN 1 ELSE 0 END) as fixed_count
           FROM findings GROUP BY category ORDER BY total DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def register_tools(mcp, conn_factory) -> None:
    """Register finding-tracker tools on the given MCP server."""
    from codebugs import blockers

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
            reported_at_commit = db.git_rev_parse("HEAD", silent=True)
        with conn_factory() as conn:
            return add_finding(
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
        default_commit = (
            reported_at_commit
            if reported_at_commit is not None
            else db.git_rev_parse("HEAD", silent=True)
        )
        enriched = []
        for f in findings:
            f = {**f}
            if "reported_at_commit" not in f:
                f["reported_at_commit"] = default_commit
            if "reported_at_ref" not in f and reported_at_ref is not None:
                f["reported_at_ref"] = reported_at_ref
            enriched.append(f)
        with conn_factory() as conn:
            return batch_add_findings(conn, enriched)

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
        with conn_factory() as conn:
            result = update_finding(
                conn,
                finding_id,
                status=status,
                notes=notes,
                tags=tags,
                meta_update=meta_update,
                reported_at_ref=reported_at_ref,
            )
            if status and result.get("status") in blockers.TERMINAL_STATUSES.get(
                blockers.ENTITY_FINDING, set()
            ):
                unblocked = blockers.get_unblocked_by(conn, finding_id, blockers.ENTITY_FINDING)
                if unblocked:
                    result["unblocked_items"] = unblocked
            return result

    @mcp.tool()
    def query(
        id: str | None = None,
        ids: list[str] | None = None,
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

        Supports lookup by ID via `id=` (single) or `ids=` (batch). Missing IDs
        are silently absent from the result so the caller can diff. For a strict
        single-ID fetch that errors on miss, use `get` instead.

        Args:
            id: Fetch a single finding by exact ID (e.g. CB-1383)
            ids: Fetch multiple findings by ID list; missing IDs are skipped
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
        with conn_factory() as conn:
            if status == "deferred":
                return blockers.query_deferred_entities(
                    conn, blockers.ENTITY_FINDING, limit=limit, offset=offset
                )
            return query_findings(
                conn,
                id=id,
                ids=ids,
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
    def get(finding_id: str) -> dict[str, Any]:
        """Fetch a single finding by ID with full body (description, severity,
        status, tags, meta, timestamps, commit refs).

        Raises a not-found error if the ID does not exist. For lenient batch
        lookup that silently drops missing IDs, use `query(ids=[...])`.

        Args:
            finding_id: The finding ID (e.g. CB-1383)
        """
        with conn_factory() as conn:
            return get_finding(conn, finding_id)

    @mcp.tool()
    def stats(group_by: str = "severity") -> dict[str, Any]:
        """Aggregated cross-tabulated counts.

        Args:
            group_by: Group by: severity, category, status, file, source
        """
        with conn_factory() as conn:
            return get_stats(conn, group_by=group_by)

    @mcp.tool()
    def summary() -> dict[str, Any]:
        """Dashboard overview — open/resolved counts, severity breakdown,
        top categories, hottest files, deferred counts. Start here for orientation."""
        with conn_factory() as conn:
            result = get_summary(conn)
            result.update(blockers.get_deferred_counts(conn, blockers.ENTITY_FINDING))
            return result

    @mcp.tool()
    def categories() -> list[dict[str, Any]]:
        """List all existing categories with counts.
        Call this before adding findings to reuse consistent category names."""
        with conn_factory() as conn:
            return get_categories(conn)


def register_cli(sub, commands) -> None:
    """Register findings CLI subcommands."""
    import argparse
    from codebugs.fmt import format_table

    def _cmd_add(args: argparse.Namespace) -> None:
        conn = db.connect()
        meta = {}
        if args.lines:
            meta["lines"] = args.lines
        if args.meta:
            meta.update(json.loads(args.meta))

        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

        result = add_finding(
            conn,
            severity=args.severity,
            category=args.category,
            file=args.file,
            description=args.description,
            source=args.source or "human",
            tags=tags,
            meta=meta or None,
        )
        conn.close()
        print(f"Added: {result['id']}")

    def _cmd_update(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = update_finding(
                conn,
                args.id,
                status=args.status,
                notes=args.notes,
            )
            print(f"Updated: {result['id']} (status={result['status']})")
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_query(args: argparse.Namespace) -> None:
        conn = db.connect()
        ids = [s.strip() for s in args.id.split(",") if s.strip()] if args.id else None
        result = query_findings(
            conn,
            ids=ids,
            status=args.status,
            severity=args.severity,
            category=args.category,
            file=args.file,
            source=args.source,
            group_by=args.group_by,
            limit=args.limit or 100,
        )
        conn.close()

        if result.get("grouped"):
            data = [{"group": r["group_key"], "count": str(r["count"])} for r in result["groups"]]
            print(format_table(data, ["group", "count"]))
        else:
            findings = result["findings"]
            if not findings:
                print("(no findings match)")
                return
            data = [
                {
                    "id": f["id"],
                    "sev": f["severity"],
                    "category": f["category"],
                    "file": f["file"],
                    "status": f["status"],
                    "description": f["description"],
                }
                for f in findings
            ]
            print(
                format_table(
                    data,
                    ["id", "sev", "category", "file", "status", "description"],
                    max_widths={"description": 60, "file": 40, "category": 25},
                )
            )
            print(f"\n{result['total']} finding(s) total.")

    def _cmd_get(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = get_finding(conn, args.id)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        print(json.dumps(result, indent=2, sort_keys=True))

    def _cmd_stats(args: argparse.Namespace) -> None:
        conn = db.connect()
        result = get_stats(conn, group_by=args.by or "severity")
        conn.close()

        groups = result["groups"]
        if not groups:
            print("(no findings)")
            return

        header = f"{'':30s} {'critical':>8s} {'high':>8s} {'medium':>8s} {'low':>8s} {'total':>8s}"
        print(header)
        print("-" * len(header))
        totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        for grp in sorted(groups):
            d = groups[grp]
            print(
                f"{grp:30s} {d['critical']:>8d} {d['high']:>8d} {d['medium']:>8d} {d['low']:>8d} {d['total']:>8d}"
            )
            for k in totals:
                totals[k] += d[k]
        print("-" * len(header))
        print(
            f"{'TOTAL':30s} {totals['critical']:>8d} {totals['high']:>8d} {totals['medium']:>8d} {totals['low']:>8d} {totals['total']:>8d}"
        )

    def _cmd_summary(args: argparse.Namespace) -> None:
        conn = db.connect()
        s = get_summary(conn)
        conn.close()

        print("Codebugs Summary")
        print("=" * 50)
        print(f"Findings:  {s['open']} open / {s['resolved']} resolved / {s['total']} total")
        print()
        print("Open by severity:")
        for sev in ("critical", "high", "medium", "low"):
            c = s["open_by_severity"].get(sev, 0)
            bar = "#" * min(c, 40)
            print(f"  {sev:10s}  {c:>4d}  {bar}")
        if s["top_categories"]:
            print()
            print("Top categories:")
            for cat in s["top_categories"]:
                print(f"  {cat['category']:30s}  {cat['count']:>4d}")
        if s["hottest_files"]:
            print()
            print("Hottest files:")
            for f in s["hottest_files"]:
                print(f"  {f['file']:50s}  {f['critical_high']} crit/high, {f['open']} open")

    def _cmd_categories(args: argparse.Namespace) -> None:
        conn = db.connect()
        cats = get_categories(conn)
        conn.close()

        if not cats:
            print("(no categories yet)")
            return
        data = [
            {
                "category": c["category"],
                "total": str(c["total"]),
                "open": str(c["open_count"]),
                "fixed": str(c["fixed_count"]),
            }
            for c in cats
        ]
        print(format_table(data, ["category", "total", "open", "fixed"]))

    def _cmd_import_csv(args: argparse.Namespace) -> None:
        conn = db.connect()
        imported = 0
        with open(args.file, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                severity = (row.get("severity") or row.get("Severity") or "medium").strip().lower()
                category = (row.get("category") or row.get("Category") or "").strip()
                filepath = (row.get("file") or row.get("File") or "").strip()
                description = (row.get("description") or row.get("Description") or "").strip()
                source = (row.get("source") or row.get("Source") or "import").strip()

                if not filepath or not description or not category:
                    continue

                meta = {}
                lines = (row.get("lines") or row.get("Lines") or "").strip()
                if lines:
                    meta["lines"] = lines

                add_finding(
                    conn,
                    severity=severity,
                    category=category,
                    file=filepath,
                    description=description,
                    source=source,
                    meta=meta or None,
                )
                imported += 1

        conn.close()
        print(f"Imported {imported} findings.")

    def _cmd_export_csv(args: argparse.Namespace) -> None:
        conn = db.connect()
        result = query_findings(conn, limit=100000)
        conn.close()

        output = args.file or "codebugs_export.csv"
        with open(output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "severity",
                    "category",
                    "file",
                    "status",
                    "description",
                    "source",
                    "tags",
                    "meta",
                    "created_at",
                    "updated_at",
                ]
            )
            for finding in result["findings"]:
                writer.writerow(
                    [
                        finding["id"],
                        finding["severity"],
                        finding["category"],
                        finding["file"],
                        finding["status"],
                        finding["description"],
                        finding["source"],
                        json.dumps(finding["tags"]),
                        json.dumps(finding["meta"]),
                        finding["created_at"],
                        finding["updated_at"],
                    ]
                )
        print(f"Exported {len(result['findings'])} findings to {output}")

    p = sub.add_parser("add", help="Add a finding")
    p.add_argument("-s", "--severity", required=True, help="critical|high|medium|low")
    p.add_argument("-c", "--category", required=True, help="Finding category")
    p.add_argument("-f", "--file", required=True, help="File path")
    p.add_argument("-d", "--description", required=True, help="Description")
    p.add_argument("-l", "--lines", help="Line range (stored in meta)")
    p.add_argument("--source", help="Source (default: human)")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--meta", help="JSON metadata string")

    p = sub.add_parser("update", help="Update a finding")
    p.add_argument("id", help="Finding ID")
    p.add_argument("--status", help="New status")
    p.add_argument("--notes", help="Notes")

    p = sub.add_parser("query", help="Search findings")
    p.add_argument("--id", help="Filter by ID (single CB-N or comma-separated list)")
    p.add_argument("--status", help="Filter by status")
    p.add_argument("--severity", "-s", help="Filter by severity")
    p.add_argument("--category", "-c", help="Filter by category")
    p.add_argument("--file", "-f", help="Filter by file (substring)")
    p.add_argument("--source", help="Filter by source")
    p.add_argument("--group-by", help="Group by: file|category|severity|status|source")
    p.add_argument("--limit", type=int, help="Max results")

    p = sub.add_parser("get", help="Fetch a single finding by ID")
    p.add_argument("id", help="Finding ID (e.g. CB-1383)")

    p = sub.add_parser("stats", help="Cross-tabulated summary")
    p.add_argument("--by", help="Group by: severity|category|status|file|source")

    sub.add_parser("summary", help="Dashboard overview")
    sub.add_parser("categories", help="List all categories with counts")

    p = sub.add_parser("import-csv", help="Import findings from CSV")
    p.add_argument("file", help="CSV file path")

    p = sub.add_parser("export-csv", help="Export findings to CSV")
    p.add_argument("file", nargs="?", help="Output file (default: codebugs_export.csv)")

    commands.update(
        {
            "add": _cmd_add,
            "update": _cmd_update,
            "query": _cmd_query,
            "get": _cmd_get,
            "stats": _cmd_stats,
            "summary": _cmd_summary,
            "categories": _cmd_categories,
            "import-csv": _cmd_import_csv,
            "export-csv": _cmd_export_csv,
        }
    )


db.register_schema("findings", ensure_schema)
db.register_tool_provider("findings", register_tools)
db.register_cli_provider("findings", register_cli)
