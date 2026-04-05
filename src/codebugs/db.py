"""Database layer — all SQLite operations for codebugs."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_DIR = ".codebugs"
DB_FILE = "findings.db"

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

VALID_SEVERITIES = ("critical", "high", "medium", "low")
VALID_STATUSES = ("open", "in_progress", "fixed", "not_a_bug", "wont_fix", "stale")

# Common aliases → canonical status.  Checked case-insensitively.
STATUS_ALIASES: dict[str, str] = {
    "done": "fixed",
    "resolved": "fixed",
    "implemented": "fixed",
    "closed": "fixed",
    "wontfix": "wont_fix",
    "won't_fix": "wont_fix",
    "invalid": "not_a_bug",
    "in-progress": "in_progress",
    "active": "in_progress",
    "working": "in_progress",
}


def resolve_status(status: str) -> str:
    """Resolve a status string to its canonical form.

    Accepts canonical statuses as-is, or maps known aliases.
    Raises ValueError for unrecognised values.
    """
    if status in VALID_STATUSES:
        return status
    canonical = STATUS_ALIASES.get(status.lower().replace(" ", "_"))
    if canonical:
        return canonical
    raise ValueError(
        f"Invalid status: {status}. "
        f"Must be one of {VALID_STATUSES} "
        f"(aliases: {', '.join(sorted(STATUS_ALIASES))})"
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_path(project_dir: str | None = None) -> str:
    root = project_dir or os.getcwd()
    return os.path.join(root, DB_DIR, DB_FILE)


def connect(project_dir: str | None = None) -> sqlite3.Connection:
    """Open (and initialize) the codebugs database."""
    path = _db_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    _migrate_statuses(conn)
    _migrate_provenance(conn)
    # Initialize requirements schema (same DB)
    from codebugs import reqs
    reqs.ensure_schema(conn)
    from codebugs import merge
    merge.ensure_schema(conn)
    from codebugs import sweep
    sweep.ensure_schema(conn)
    from codebugs import bench
    bench.ensure_schema(conn)
    from codebugs import blockers
    blockers.ensure_schema(conn)
    return conn


def _migrate_statuses(conn: sqlite3.Connection) -> None:
    """Add 'in_progress' to the status CHECK constraint on existing databases."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='findings'"
    ).fetchone()
    if row is None:
        return
    ddl = row[0] or ""
    if "in_progress" in ddl:
        return  # already up-to-date

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
    # Re-create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _migrate_provenance(conn: sqlite3.Connection) -> None:
    """Add provenance columns to existing databases that already passed status migration."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
    if "reported_at_commit" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_commit TEXT")
    if "reported_at_ref" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_ref TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)")
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
) -> dict[str, Any]:
    """Add a single finding. Returns the created finding as a dict."""
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity: {severity}. Must be one of {VALID_SEVERITIES}")

    fid = finding_id or _next_id(conn)
    now = _now()
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps(meta or {})

    conn.execute(
        """INSERT INTO findings (id, severity, category, file, status, description,
           source, tags, meta, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
        (fid, severity, category, file, description, source, tags_json, meta_json, now, now),
    )
    conn.commit()
    return _row_to_dict(conn.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone())


def batch_add_findings(
    conn: sqlite3.Connection,
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add multiple findings at once. Returns list of created findings."""
    now = _now()
    results = []
    for f in findings:
        severity = f.get("severity", "medium")
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity: {severity}")

        fid = f.get("id") or _next_id(conn)
        tags_json = json.dumps(f.get("tags", []))
        meta_json = json.dumps(f.get("meta", {}))

        conn.execute(
            """INSERT INTO findings (id, severity, category, file, status, description,
               source, tags, meta, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
            (
                fid,
                severity,
                f["category"],
                f["file"],
                f["description"],
                f.get("source", "human"),
                tags_json,
                meta_json,
                now,
                now,
            ),
        )
        results.append(fid)

    conn.commit()
    rows = conn.execute(
        f"SELECT * FROM findings WHERE id IN ({','.join('?' for _ in results)})",
        results,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    *,
    status: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    meta_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a finding. Returns updated finding."""
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        raise KeyError(f"Finding not found: {finding_id}")

    updates = []
    params: list[Any] = []

    if status is not None:
        status = resolve_status(status)
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

    if not updates:
        return _row_to_dict(row)

    updates.append("updated_at = ?")
    params.append(_now())
    params.append(finding_id)

    conn.execute(f"UPDATE findings SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return _row_to_dict(conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone())


def query_findings(
    conn: sqlite3.Connection,
    *,
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
    """Query findings with filters. Returns results or grouped counts."""
    conditions: list[str] = []
    params: list[Any] = []

    if status:
        conditions.append("status = ?")
        params.append(resolve_status(status))
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
        "findings": [_row_to_dict(r) for r in rows],
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
        hottest_files.append({
            "file": r["file"],
            "open": r["total_open"],
            "critical_high": r["crit_high"],
        })

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


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a Row to a dict, parsing JSON fields."""
    d = dict(row)
    d["tags"] = json.loads(d["tags"]) if isinstance(d["tags"], str) else d["tags"]
    d["meta"] = json.loads(d["meta"]) if isinstance(d["meta"], str) else d["meta"]
    return d
