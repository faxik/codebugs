"""Database layer — requirements tracking for codebugs."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import struct
from datetime import datetime, timezone
from typing import Any


REQS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS requirements (
    id TEXT PRIMARY KEY,
    section TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'Should'
        CHECK(priority IN ('Must', 'Should', 'Could')),
    status TEXT NOT NULL DEFAULT 'Planned'
        CHECK(status IN ('Planned', 'Partial', 'Implemented', 'Verified', 'Superseded', 'Obsolete')),
    source TEXT NOT NULL DEFAULT '',
    test_coverage TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    embedding BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reqs_status ON requirements(status);
CREATE INDEX IF NOT EXISTS idx_reqs_section ON requirements(section);
CREATE INDEX IF NOT EXISTS idx_reqs_priority ON requirements(priority);
"""

VALID_PRIORITIES = ("Must", "Should", "Could")
VALID_STATUSES = ("Planned", "Partial", "Implemented", "Verified", "Superseded", "Obsolete")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the requirements table if it doesn't exist."""
    for stmt in REQS_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    # Migration: add embedding column if missing (for DBs created before embeddings)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requirements)").fetchall()}
    if "embedding" not in cols:
        conn.execute("ALTER TABLE requirements ADD COLUMN embedding BLOB")
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d["tags"]) if isinstance(d["tags"], str) else d["tags"]
    d["meta"] = json.loads(d["meta"]) if isinstance(d["meta"], str) else d["meta"]
    return d


def add_requirement(
    conn: sqlite3.Connection,
    *,
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
    """Add a single requirement."""
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority}. Must be one of {VALID_PRIORITIES}")
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Must be one of {VALID_STATUSES}")

    now = _now()
    conn.execute(
        """INSERT INTO requirements (id, section, description, priority, status,
           source, test_coverage, tags, meta, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            req_id, section, description, priority, status,
            source, test_coverage, json.dumps(tags or []),
            json.dumps(meta or {}), now, now,
        ),
    )
    conn.commit()
    return _row_to_dict(conn.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)).fetchone())


def batch_add_requirements(
    conn: sqlite3.Connection,
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add multiple requirements. Returns list of created requirements."""
    now = _now()
    ids = []
    for r in requirements:
        req_id = r["id"]
        priority = r.get("priority", "Should")
        status = r.get("status", "Planned")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority: {priority}")
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")

        conn.execute(
            """INSERT OR REPLACE INTO requirements (id, section, description, priority, status,
               source, test_coverage, tags, meta, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                req_id, r.get("section", ""), r["description"],
                priority, status,
                r.get("source", ""), r.get("test_coverage", ""),
                json.dumps(r.get("tags", [])), json.dumps(r.get("meta", {})),
                now, now,
            ),
        )
        ids.append(req_id)

    conn.commit()
    rows = conn.execute(
        f"SELECT * FROM requirements WHERE id IN ({','.join('?' for _ in ids)})", ids,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_requirement(
    conn: sqlite3.Connection,
    req_id: str,
    *,
    status: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    section: str | None = None,
    test_coverage: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    meta_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a requirement."""
    row = conn.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)).fetchone()
    if not row:
        raise KeyError(f"Requirement not found: {req_id}")

    updates = []
    params: list[Any] = []

    if section is not None:
        updates.append("section = ?")
        params.append(section)
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}. Must be one of {VALID_STATUSES}")
        updates.append("status = ?")
        params.append(status)
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if priority is not None:
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority: {priority}. Must be one of {VALID_PRIORITIES}")
        updates.append("priority = ?")
        params.append(priority)
    if test_coverage is not None:
        updates.append("test_coverage = ?")
        params.append(test_coverage)
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
    params.append(req_id)

    conn.execute(f"UPDATE requirements SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return _row_to_dict(conn.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)).fetchone())


def query_requirements(
    conn: sqlite3.Connection,
    *,
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
    """Query requirements with filters."""
    conditions: list[str] = []
    params: list[Any] = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if priority:
        conditions.append("priority = ?")
        params.append(priority)
    if section:
        conditions.append("section LIKE ?")
        params.append(f"%{section}%")
    if search:
        conditions.append("(description LIKE ? OR id LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if source:
        conditions.append("source LIKE ?")
        params.append(f"%{source}%")
    if tag:
        conditions.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)")
        params.append(tag)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if group_by:
        valid_groups = ("section", "status", "priority", "source")
        if group_by not in valid_groups:
            raise ValueError(f"Invalid group_by: {group_by}. Must be one of {valid_groups}")
        rows = conn.execute(
            f"SELECT {group_by} as group_key, COUNT(*) as count FROM requirements {where} GROUP BY {group_by} ORDER BY count DESC",
            params,
        ).fetchall()
        return {"grouped": True, "group_by": group_by, "groups": [dict(r) for r in rows]}

    count = conn.execute(f"SELECT COUNT(*) as c FROM requirements {where}", params).fetchone()["c"]
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM requirements {where} ORDER BY id LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return {
        "grouped": False,
        "total": count,
        "limit": limit,
        "offset": offset,
        "requirements": [_row_to_dict(r) for r in rows],
    }


def get_reqs_stats(
    conn: sqlite3.Connection,
    *,
    group_by: str = "status",
) -> dict[str, Any]:
    """Aggregated counts by status×priority."""
    valid_groups = ("status", "priority", "section", "source")
    if group_by not in valid_groups:
        raise ValueError(f"Invalid group_by: {group_by}. Must be one of {valid_groups}")

    rows = conn.execute(
        f"""SELECT {group_by} as grp, priority, COUNT(*) as cnt
            FROM requirements
            GROUP BY grp, priority
            ORDER BY grp, priority"""
    ).fetchall()

    groups: dict[str, dict[str, int]] = {}
    for r in rows:
        grp = r["grp"]
        if grp not in groups:
            groups[grp] = {"Must": 0, "Should": 0, "Could": 0, "total": 0}
        groups[grp][r["priority"]] = r["cnt"]
        groups[grp]["total"] += r["cnt"]

    return {"group_by": group_by, "groups": groups}


def get_reqs_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Dashboard overview of requirements."""
    total = conn.execute("SELECT COUNT(*) as c FROM requirements").fetchone()["c"]
    by_status: dict[str, int] = {}
    for r in conn.execute("SELECT status, COUNT(*) as c FROM requirements GROUP BY status"):
        by_status[r["status"]] = r["c"]

    by_priority: dict[str, int] = {}
    for r in conn.execute("SELECT priority, COUNT(*) as c FROM requirements GROUP BY priority"):
        by_priority[r["priority"]] = r["c"]

    no_tests = conn.execute(
        """SELECT COUNT(*) as c FROM requirements
           WHERE status = 'Implemented' AND (test_coverage = '' OR test_coverage = '--')"""
    ).fetchone()["c"]

    sections = conn.execute(
        """SELECT section, COUNT(*) as total,
                  SUM(CASE WHEN status IN ('Implemented', 'Verified') THEN 1 ELSE 0 END) as done
           FROM requirements WHERE section != ''
           GROUP BY section ORDER BY section"""
    ).fetchall()

    return {
        "total": total,
        "by_status": by_status,
        "by_priority": by_priority,
        "implemented_without_tests": no_tests,
        "sections": [{"section": s["section"], "total": s["total"], "done": s["done"]} for s in sections],
    }


def verify_requirements(
    conn: sqlite3.Connection,
    *,
    project_dir: str | None = None,
    checks: list[str] | None = None,
) -> dict[str, Any]:
    """Verify requirements for issues.

    Checks (all by default):
      - tests: referenced test files exist on disk
      - ids: duplicate IDs, numbering gaps
      - status: contradictions (description vs status)
    """
    all_checks = checks or ["tests", "ids", "status"]
    root = project_dir or os.getcwd()
    issues: list[dict[str, str]] = []

    rows = conn.execute("SELECT * FROM requirements ORDER BY id").fetchall()
    reqs = [_row_to_dict(r) for r in rows]

    if "ids" in all_checks:
        # Duplicate IDs (should be impossible with PK, but check imported data)
        seen_ids: dict[str, int] = {}
        for r in reqs:
            seen_ids[r["id"]] = seen_ids.get(r["id"], 0) + 1
        for rid, count in seen_ids.items():
            if count > 1:
                issues.append({"check": "ids", "severity": "critical", "id": rid,
                               "message": f"Duplicate ID: {rid} appears {count} times"})

        # Numbering gaps
        fr_numbers = []
        for r in reqs:
            m = re.match(r"FR-(\d+)", r["id"])
            if m:
                fr_numbers.append(int(m.group(1)))
        if fr_numbers:
            fr_numbers.sort()
            gaps = []
            for i in range(len(fr_numbers) - 1):
                gap_start = fr_numbers[i] + 1
                gap_end = fr_numbers[i + 1] - 1
                if gap_end - gap_start >= 4:  # Only report gaps of 5+
                    gaps.append(f"FR-{gap_start:03d}..FR-{gap_end:03d}")
            if gaps:
                issues.append({"check": "ids", "severity": "medium", "id": "--",
                               "message": f"Numbering gaps (5+): {', '.join(gaps)}"})

    if "tests" in all_checks:
        tests_dir = os.path.join(root, "tests")
        for r in reqs:
            tc = r["test_coverage"].strip()
            if not tc or tc == "--":
                continue
            # May be comma-separated or contain extra info like "(54 tests)"
            for part in re.split(r"[,;]", tc):
                filename = re.sub(r"\s*\(.*\)", "", part).strip()
                if not filename or filename == "--":
                    continue
                filepath = os.path.join(tests_dir, filename)
                if not os.path.exists(filepath):
                    issues.append({"check": "tests", "severity": "high", "id": r["id"],
                                   "message": f"Test file not found: {filename}"})

    if "status" in all_checks:
        for r in reqs:
            desc_lower = r["description"].lower()
            status = r["status"]

            # "superseded" in description but not status
            if "superseded" in desc_lower and status not in ("Superseded", "Obsolete"):
                issues.append({"check": "status", "severity": "high", "id": r["id"],
                               "message": f"Description mentions 'superseded' but status is '{status}'"})

            # "deprecated" in description but still Implemented
            if "deprecated" in desc_lower and status == "Implemented":
                issues.append({"check": "status", "severity": "medium", "id": r["id"],
                               "message": f"Description mentions 'deprecated' but status is 'Implemented'"})

            # Implemented but no test coverage (Must priority)
            tc = r["test_coverage"].strip()
            if status == "Implemented" and r["priority"] == "Must" and (not tc or tc == "--"):
                issues.append({"check": "status", "severity": "medium", "id": r["id"],
                               "message": f"Must-priority requirement implemented without test coverage"})

    return {
        "total_requirements": len(reqs),
        "issues_found": len(issues),
        "issues": issues,
    }


# --- Markdown import/export ---

_SECTION_RE = re.compile(r"^###\s+(?:(\d+\.\d+\w*)\s+)?(.+?)\s*\(N?FR-\d+")
_SECTION_L2_RE = re.compile(r"^##(?!#)\s+(?:(\d+)\.\s+)?(.+)$")
_ROW_RE = re.compile(r"^\|\s*(N?FR-\d+\w*)\s*\|")


def import_markdown(
    conn: sqlite3.Connection,
    markdown_path: str,
) -> dict[str, Any]:
    """Parse a REQUIREMENTS.md file and import into the database.

    Expects markdown tables with columns:
    | ID | Requirement | Priority | Status | Source | Test Coverage |
    """
    with open(markdown_path) as f:
        lines = f.readlines()

    current_section = ""
    imported = 0
    skipped = 0
    now = _now()

    for line in lines:
        line = line.rstrip()

        # Section header: ### (level-3, most sections)
        sm = _SECTION_RE.match(line)
        if sm:
            section_num = sm.group(1)
            section_title = sm.group(2).strip()
            current_section = f"{section_num} {section_title}" if section_num else section_title
            continue

        # Section header: ## (level-2, e.g. "## 2. Non-Functional Requirements")
        sm2 = _SECTION_L2_RE.match(line)
        if sm2:
            section_num = sm2.group(1)
            section_title = sm2.group(2).strip()
            current_section = f"{section_num}. {section_title}" if section_num else section_title
            continue

        # Table row
        rm = _ROW_RE.match(line)
        if not rm:
            continue

        cells = [c.strip() for c in line.split("|")]
        # Filter empty cells from leading/trailing pipes
        cells = [c for c in cells if c]
        if len(cells) < 4:
            skipped += 1
            continue

        req_id = cells[0]
        description = cells[1]
        priority = cells[2] if len(cells) > 2 else "Should"
        status = cells[3] if len(cells) > 3 else "Planned"
        source = cells[4] if len(cells) > 4 else ""
        test_coverage = cells[5] if len(cells) > 5 else ""

        # Normalize status
        status_map = {
            "planned": "Planned", "partial": "Partial",
            "implemented": "Implemented", "verified": "Verified",
            "superseded": "Superseded", "obsolete": "Obsolete",
        }
        status = status_map.get(status.lower().strip(), status)
        if status not in VALID_STATUSES:
            status = "Planned"

        priority_map = {"must": "Must", "should": "Should", "could": "Could"}
        priority = priority_map.get(priority.lower().strip(), priority)
        if priority not in VALID_PRIORITIES:
            priority = "Should"

        try:
            conn.execute(
                """INSERT OR REPLACE INTO requirements
                   (id, section, description, priority, status, source,
                    test_coverage, tags, meta, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?, ?)""",
                (req_id, current_section, description, priority, status,
                 source, test_coverage, now, now),
            )
            imported += 1
        except sqlite3.Error:
            skipped += 1

    conn.commit()
    return {"imported": imported, "skipped": skipped, "section": current_section}


def export_markdown(
    conn: sqlite3.Connection,
) -> str:
    """Export requirements as markdown tables grouped by section."""
    rows = conn.execute(
        "SELECT * FROM requirements ORDER BY section, id"
    ).fetchall()

    if not rows:
        return "# Requirements\n\n(no requirements)\n"

    lines = ["# Requirements\n"]
    current_section = None

    for row in rows:
        section = row["section"] or "Uncategorized"
        if section != current_section:
            current_section = section
            lines.append(f"\n### {section}\n")
            lines.append("| ID | Requirement | Priority | Status | Source | Test Coverage |")
            lines.append("|----|-------------|----------|--------|--------|---------------|")

        lines.append(
            f"| {row['id']} | {row['description']} | {row['priority']} "
            f"| {row['status']} | {row['source']} | {row['test_coverage'] or '--'} |"
        )

    lines.append("")
    return "\n".join(lines)


# --- Embedding support (optional) ---


def _pack_vector(vec: list[float]) -> bytes:
    """Pack a float vector into bytes (little-endian float32)."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vector(blob: bytes) -> list[float]:
    """Unpack bytes into a float vector."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def store_embedding(
    conn: sqlite3.Connection,
    req_id: str,
    embedding: list[float],
) -> dict[str, Any]:
    """Store an embedding vector for a requirement.

    The caller is responsible for generating the embedding (e.g. via an
    embedding API). This function just stores and retrieves.
    """
    row = conn.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)).fetchone()
    if not row:
        raise KeyError(f"Requirement not found: {req_id}")

    blob = _pack_vector(embedding)
    conn.execute(
        "UPDATE requirements SET embedding = ?, updated_at = ? WHERE id = ?",
        (blob, _now(), req_id),
    )
    conn.commit()
    return {"id": req_id, "dimensions": len(embedding), "stored": True}


def batch_store_embeddings(
    conn: sqlite3.Connection,
    embeddings: dict[str, list[float]],
) -> dict[str, Any]:
    """Store embeddings for multiple requirements at once.

    Args:
        embeddings: Dict mapping req_id -> vector
    """
    now = _now()
    stored = 0
    for req_id, vec in embeddings.items():
        blob = _pack_vector(vec)
        cursor = conn.execute(
            "UPDATE requirements SET embedding = ?, updated_at = ? WHERE id = ?",
            (blob, now, req_id),
        )
        if cursor.rowcount > 0:
            stored += 1
    conn.commit()
    return {"stored": stored, "total": len(embeddings)}


def search_similar(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    *,
    limit: int = 10,
    min_similarity: float = 0.0,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Find requirements most similar to a query embedding.

    Uses brute-force cosine similarity (fine for <10K requirements).

    Args:
        query_embedding: The query vector
        limit: Max results
        min_similarity: Minimum cosine similarity threshold (0.0-1.0)
        status: Optional status filter
    """
    conditions = ["embedding IS NOT NULL"]
    params: list[Any] = []
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}"
    rows = conn.execute(
        f"SELECT * FROM requirements {where}", params,
    ).fetchall()

    scored = []
    for row in rows:
        vec = _unpack_vector(row["embedding"])
        sim = _cosine_similarity(query_embedding, vec)
        if sim >= min_similarity:
            d = _row_to_dict(row)
            d.pop("embedding", None)  # Don't return the blob
            d["similarity"] = round(sim, 4)
            scored.append(d)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:limit]


def embedding_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Report on embedding coverage."""
    total = conn.execute("SELECT COUNT(*) as c FROM requirements").fetchone()["c"]
    embedded = conn.execute(
        "SELECT COUNT(*) as c FROM requirements WHERE embedding IS NOT NULL"
    ).fetchone()["c"]
    missing = conn.execute(
        "SELECT id, section FROM requirements WHERE embedding IS NULL ORDER BY id"
    ).fetchall()
    return {
        "total": total,
        "embedded": embedded,
        "missing": total - embedded,
        "missing_ids": [{"id": r["id"], "section": r["section"]} for r in missing[:20]],
    }


from codebugs.db import register_schema  # noqa: E402

register_schema("reqs", ensure_schema)
