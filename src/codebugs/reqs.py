"""Database layer — requirements tracking for codebugs."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any

from codebugs import db, entities
from codebugs.types import (
    ENTITY_REQUIREMENT,
    is_vocabulary_filter_active,
    require_row_limit,
    resolve_priority,
    resolve_requirement_status,
    utc_now,
    validate_batch_payload,
)


REQS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS requirements (
    id TEXT PRIMARY KEY,
    section TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'should'
        CHECK(priority IN ('must', 'should', 'could')),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK(status IN ('planned', 'partial', 'implemented', 'verified', 'superseded', 'obsolete')),
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
    _migrate_to_lowercase(conn)


def _migrate_to_lowercase(conn: sqlite3.Connection) -> None:
    """Rebuild legacy CHECK constraints from capitalized to lowercase.

    Guard must be case-sensitive: lowercasing the stored SQL first would make
    legacy ``'Must'`` match ``'must'`` and skip the migration (CB-1038).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'"
    ).fetchone()
    if row is None:
        return
    sql = row[0]
    if "'planned'" in sql and "'must'" in sql:
        return
    # Drop any orphan requirements_new left by a prior aborted migration.
    conn.execute("DROP TABLE IF EXISTS requirements_new")
    conn.executescript("""
        CREATE TABLE requirements_new (
            id TEXT PRIMARY KEY,
            section TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'should'
                CHECK(priority IN ('must', 'should', 'could')),
            status TEXT NOT NULL DEFAULT 'planned'
                CHECK(status IN ('planned', 'partial', 'implemented', 'verified', 'superseded', 'obsolete')),
            source TEXT NOT NULL DEFAULT '',
            test_coverage TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            embedding BLOB,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO requirements_new
            SELECT id, section, description, LOWER(priority), LOWER(status),
                   source, test_coverage, tags, meta, embedding, created_at, updated_at
            FROM requirements;
        DROP TABLE requirements;
        ALTER TABLE requirements_new RENAME TO requirements;
        CREATE INDEX IF NOT EXISTS idx_reqs_status ON requirements(status);
        CREATE INDEX IF NOT EXISTS idx_reqs_section ON requirements(section);
        CREATE INDEX IF NOT EXISTS idx_reqs_priority ON requirements(priority);
    """)


def add_requirement(
    conn: sqlite3.Connection,
    *,
    req_id: str,
    description: str,
    section: str = "",
    priority: str = "should",
    status: str = "planned",
    source: str = "",
    test_coverage: str = "",
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add a single requirement.

    Returns the row THIS call wrote, captured from the INSERT's own ``RETURNING``
    inside ``db.txn`` (CB-117 — the same shape as CB-111's fix to
    ``merge.abandon_session``). This used to close with a bare ``conn.commit()``
    followed by a fresh ``SELECT * FROM requirements WHERE id = ?``, so a write
    from another connection (``update_requirement`` is exposed over MCP the same
    way) landing in the window between the commit and that SELECT was reported as
    this call's own result. Unlike CB-111's abandon_session, there was never a
    benign period here: the whole dict always went straight to the MCP client, so
    the race was live from the start, not merely a future hazard.

    Do not restore ``conn.commit()``: ``db.txn`` yields ``False`` under an ambient
    transaction, and committing then would commit the caller's unrelated pending
    work (CB-24 consequence (1)).

    ``fetchone()`` on the ``RETURNING`` cursor, and the ``row_to_dict`` conversion,
    both stay INSIDE the ``db.txn`` block — deliberately, unlike
    ``update_requirement`` (CB-24 consequence (2)), where the conversion is moved
    OUTSIDE because it can fail on a row this call did not write. That risk does
    not apply here: this is a fresh INSERT, and the stored ``tags``/``meta`` are
    the exact JSON strings this call just serialized two lines below, never a
    previously-stored value another writer could have left malformed.
    """
    priority = resolve_priority(priority)
    status = resolve_requirement_status(status)

    now = utc_now()
    with db.txn(conn):
        row = conn.execute(
            """INSERT INTO requirements (id, section, description, priority, status,
               source, test_coverage, tags, meta, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               RETURNING *""",
            (
                req_id, section, description, priority, status,
                source, test_coverage, json.dumps(tags or []),
                json.dumps(meta or {}), now, now,
            ),
        ).fetchone()
        result = db.row_to_dict(row)
    return result


def batch_add_requirements(
    conn: sqlite3.Connection,
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add multiple requirements. Returns list of created requirements.

    Raises ``ValueError`` (this package's contract for invalid input) if
    ``requirements`` is not a list of objects — CB-80. Checked BEFORE anything
    is read from a member: a ``str`` payload is iterable, so a check that only
    tests each iterated element would still walk its CHARACTERS rather than
    stop at the boundary; see ``types.validate_batch_payload``.
    """
    requirements = validate_batch_payload(requirements, label="requirements")
    now = utc_now()
    ids = []
    for r in requirements:
        req_id = r["id"]
        priority = resolve_priority(r.get("priority", "should"))
        status = resolve_requirement_status(r.get("status", "planned"))

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
    return [db.row_to_dict(r) for r in rows]


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
    """Update a requirement.

    ``notes`` replaces the notes wholesale; ``meta_update`` merges last, so an
    explicit ``meta_update["notes"]`` still wins. Both compose over a single dict:
    building one per branch would emit a second ``meta = ?`` in the same UPDATE
    and silently discard the first (CB-16). Unlike findings, there is no
    ``append_note`` here.

    The whole body sits in ``db.txn`` for the same reason its findings twin does
    (CB-24): ``meta`` is merged in Python from the row read at the top, so unless
    that read and the write are one transaction, two writers each merging over the
    same stale row both report success and the later erases the earlier. Do not
    restore a ``conn.commit()`` — ``db.txn`` yields ``False`` under an ambient
    transaction, and committing then would commit the caller's work.
    """
    # Argument-only validation runs BEFORE the transaction, same reason as the
    # findings twin: these resolvers need no row, and opening BEGIN IMMEDIATE first
    # would turn an invalid priority into an OperationalError after a five-second
    # wait under write contention, instead of an immediate ValueError.
    if status is not None:
        status = resolve_requirement_status(status)
    if priority is not None:
        priority = resolve_priority(priority)

    with db.txn(conn):
        row = conn.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)).fetchone()
        if not row:
            raise KeyError(f"Requirement not found: {req_id}")

        updates = []
        params: list[Any] = []

        if section is not None:
            updates.append("section = ?")
            params.append(section)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)
        if test_coverage is not None:
            updates.append("test_coverage = ?")
            params.append(test_coverage)
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))
        # One dict, one `meta = ?`. See the docstring for the ordering contract.
        #
        # Parsed lazily so an update touching no meta argument still reaches its own
        # SQL on a row whose stored meta is malformed (the column carries no
        # json_valid constraint). The call still raises at the row_to_dict conversion;
        # only the write is preserved. The guard must list every meta-writing argument
        # below it, or a new one becomes a silent no-op when used alone.
        if notes is not None or meta_update is not None:
            new_meta = json.loads(row["meta"])
            if notes is not None:
                new_meta["notes"] = notes
            if meta_update is not None:
                new_meta.update(meta_update)
            updates.append("meta = ?")
            params.append(json.dumps(new_meta))

        if not updates:
            final_row = row
        else:
            updates.append("updated_at = ?")
            params.append(utc_now())
            params.append(req_id)

            old_status = row["status"]
            cur = conn.execute(
                f"UPDATE requirements SET {', '.join(updates)} WHERE id = ?", params
            )
            # Same seam as findings.update_finding: fire iff the write changed the row.
            # Both writers fire it — that is what makes this a seam over the entity layer
            # rather than a findings-specific callback wearing a general name.
            if status is not None and cur.rowcount == 1 and status != old_status:
                db.run_status_change_hooks(conn, req_id, old_status, status)
            final_row = conn.execute(
                "SELECT * FROM requirements WHERE id = ?", (req_id,)
            ).fetchone()

    # Converted OUTSIDE the block — same reason as the findings twin: row_to_dict
    # raises on malformed stored meta, and inside the block that would roll back a
    # write the contract promises has landed (CB-16). Under an ambient transaction
    # nothing has committed yet and the raise abandons the caller's unit too, which
    # is the intended behaviour for a compound caller, not an oversight.
    return db.row_to_dict(final_row)


def get_requirement(conn: sqlite3.Connection, req_id: str) -> dict[str, Any]:
    """Fetch a single requirement by ID. Raises KeyError if not found."""
    row = conn.execute("SELECT * FROM requirements WHERE id = ?", (req_id,)).fetchone()
    if not row:
        raise KeyError(f"Requirement not found: {req_id}")
    return db.row_to_dict(row)


def query_requirements(
    conn: sqlite3.Connection,
    *,
    id: str | None = None,
    ids: list[str] | None = None,
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
    """Query requirements with filters.

    `id` / `ids` are AND-combined with other filters; missing IDs are silently absent.
    """
    # CB-196, and ABOVE the `ids` widening below for the reason spelled out on
    # `findings.query_findings`: that widening rewrites a negative limit to
    # `len(ids)` and would hide the refusal from every call carrying an id list.
    limit = require_row_limit("limit", limit)

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
    # Both resolved, matching the write paths (CB-19 sibling sweep). Left raw, these
    # filters compared the caller's spelling against a canonical column while
    # `add_requirement` and `update_requirement` had ALWAYS normalized through
    # `resolve_priority` / `resolve_requirement_status` — so
    # `update_requirement(priority="SHOULD")` stored `should` and
    # `query_requirements(priority="SHOULD")` then returned ZERO rows. A tracker
    # reporting "no requirements" for a value it just wrote is the worst answer it
    # can give, and it is indistinguishable from an empty queue.
    if is_vocabulary_filter_active(status):
        conditions.append("status = ?")
        params.append(resolve_requirement_status(status))
    if is_vocabulary_filter_active(priority):
        conditions.append("priority = ?")
        params.append(resolve_priority(priority))
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
        "requirements": [db.row_to_dict(r) for r in rows],
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
            groups[grp] = {"must": 0, "should": 0, "could": 0, "total": 0}
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
           WHERE status = 'implemented' AND (test_coverage = '' OR test_coverage = '--')"""
    ).fetchone()["c"]

    sections = conn.execute(
        """SELECT section, COUNT(*) as total,
                  SUM(CASE WHEN status IN ('implemented', 'verified') THEN 1 ELSE 0 END) as done
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
    issues: list[dict[str, str]] = []

    rows = conn.execute("SELECT * FROM requirements ORDER BY id").fetchall()
    reqs = [db.row_to_dict(r) for r in rows]

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
        # Resolved HERE, not at the top of the function (CB-79). `root` is used
        # by this check and no other, so an eager `os.getcwd()` made
        # `verify_requirements(checks=["ids"])` fail from a deleted cwd for a
        # check that never needed a directory. A deleted cwd is not
        # hypothetical: a long-lived MCP server outlives the worktree it was
        # started in.
        #
        # This one RAISES where provenance degrades, and the difference is the
        # contract, not the failure: this function has no "unknown" vocabulary,
        # so reporting no issues because we could not look for the tests would
        # be exactly the false-clean answer the repo keeps recording.
        if project_dir is None:
            try:
                project_dir = os.getcwd()
            except OSError as e:
                raise ValueError(
                    f"cannot determine the current directory ({e}) — it may have been "
                    f"deleted; cd somewhere that exists, or pass project_dir / --repo"
                ) from e
        tests_dir = os.path.join(project_dir, "tests")
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
            if "superseded" in desc_lower and status not in ("superseded", "obsolete"):
                issues.append({"check": "status", "severity": "high", "id": r["id"],
                               "message": f"Description mentions 'superseded' but status is '{status}'"})

            # "deprecated" in description but still implemented
            if "deprecated" in desc_lower and status == "implemented":
                issues.append({"check": "status", "severity": "medium", "id": r["id"],
                               "message": "Description mentions 'deprecated' but status is 'implemented'"})

            # Implemented but no test coverage (must priority)
            tc = r["test_coverage"].strip()
            if status == "implemented" and r["priority"] == "must" and (not tc or tc == "--"):
                issues.append({"check": "status", "severity": "medium", "id": r["id"],
                               "message": "Must-priority requirement implemented without test coverage"})

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
    now = utc_now()

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
        priority = cells[2] if len(cells) > 2 else "should"
        status = cells[3] if len(cells) > 3 else "planned"
        source = cells[4] if len(cells) > 4 else ""
        test_coverage = cells[5] if len(cells) > 5 else ""

        # Normalize status and priority via resolvers
        try:
            status = resolve_requirement_status(status)
        except ValueError:
            status = "planned"

        try:
            priority = resolve_priority(priority)
        except ValueError:
            priority = "should"

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
        except sqlite3.IntegrityError:
            # CB-99. This used to be `except sqlite3.Error`, which is the whole
            # sqlite exception tree — so a full disk or an I/O error arriving
            # mid-import was counted as a malformed ROW and the import reported
            # success. Measured against 593c924 with a simulated SQLITE_FULL:
            # `{'imported': 0, 'skipped': 2}`, no exception, and the CLI printing
            # "Imported 0 requirements, skipped 2." at exit 0. A success-shaped
            # lie on a path whose entire job is to write rows, and strictly worse
            # than the traceback CB-86 exists to remove — a traceback is loud.
            #
            # `IntegrityError` is exactly "this ROW is wrong": CHECK, NOT NULL,
            # UNIQUE and FK violations, and nothing else. Measured that a CHECK
            # violation on this table is an `IntegrityError` and not an
            # `OperationalError`, so narrowing here preserves the affordance the
            # arm was written for while dropping every failure that is about the
            # statement or the environment.
            #
            # NO CLASSIFIER IS NEEDED, and that is better than this card's own
            # prescription (which asked to reuse CB-86's `_is_environmental`).
            # Reaching for it would have meant exporting a predicate that is
            # deliberately private — the CLI boundary calling it is the design
            # CB-86 rejected — or growing a second copy of that enumeration. The
            # exception TREE already draws the line this needs.
            #
            # HONEST SCOPE: with the resolvers above normalising `status` and
            # `priority` before the INSERT, and `INSERT OR REPLACE` foreclosing
            # UNIQUE, no markdown row reachable through `_ROW_RE` can currently
            # violate a constraint at all — measured, a row with a bogus priority
            # and a bogus status imports cleanly. So this arm is a safety net for
            # a future schema, not a live path, and `skipped` is expected to stay
            # 0. It is kept rather than deleted because a new NOT NULL or CHECK
            # column is exactly the change that would need it.
            #
            # A CONSEQUENCE WORTH NAMING: the commit is at the end of the loop, so
            # letting the failure propagate now means a mid-import environmental
            # failure lands NOTHING instead of a partial import reported as
            # success. That is a real improvement, not just a louder error.
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


from codebugs.db import register_schema, register_tool_provider, register_cli_provider  # noqa: E402

register_schema("reqs", ensure_schema)


def register_tools(mcp, conn_factory):
    """Register requirements-tracker tools on the given MCP server."""

    @mcp.tool()
    def reqs_add(
        req_id: str,
        description: str,
        section: str = "",
        priority: str = "should",
        status: str = "planned",
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
            priority: must, should, or could
            status: planned, partial, implemented, verified, superseded, obsolete
            source: Where this requirement came from (e.g. Take 26, NEW)
            test_coverage: Test file name(s)
            tags: Optional tags
            meta: Optional metadata
        """
        with conn_factory() as conn:
            return add_requirement(
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
            status: New status: planned, partial, implemented, verified, superseded, obsolete
            description: Updated description
            priority: Updated priority: must, should, could
            section: Updated section name
            test_coverage: Updated test file reference
            notes: Notes (stored in meta.notes). REPLACES the stored notes
                   wholesale. If meta_update also carries a "notes" key, the
                   meta_update value is the one that lands — see meta_update.
            tags: Replace tags
            meta_update: Merge metadata keys. notes and meta_update compose over
                         ONE dict: notes replaces first, meta_update merges
                         LAST. So passing both notes= and
                         meta_update={"notes": ...} in a single call is neither
                         an error nor a refusal — meta_update wins the
                         collision, on every key it names. That precedence is
                         deliberate: meta_update names the storage key directly,
                         so it is the repair path for a key no other argument
                         can write. Unlike the findings `update` tool there is
                         no append_note here, so there is no third writer.
        """
        from codebugs import blockers

        with conn_factory() as conn:
            result = update_requirement(
                conn, req_id, status=status, description=description,
                priority=priority, section=section, test_coverage=test_coverage,
                notes=notes, tags=tags, meta_update=meta_update,
            )
            if status and entities.EntityRef.of(req_id).is_resolved(conn):
                unblocked = blockers.get_unblocked_by(conn, req_id, ENTITY_REQUIREMENT)
                if unblocked:
                    result["unblocked_items"] = unblocked
            return result

    @mcp.tool()
    def reqs_query(
        id: str | None = None,
        ids: list[str] | None = None,
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

        Supports lookup by ID via `id=` (single) or `ids=` (batch). Missing IDs
        are silently absent from the result. For a strict single-ID fetch that
        errors on miss, use `reqs_get`.

        Args:
            id: Fetch a single requirement by exact ID (e.g. FR-001)
            ids: Fetch multiple requirements by ID list; missing IDs are skipped
            status: Filter by status (planned, partial, implemented, verified, superseded, obsolete, deferred).
                    Use 'deferred' to find requirements with active blockers.
            priority: Filter by priority (must, should, could)
            section: Filter by section (substring match)
            search: Search in description and ID
            source: Filter by source (substring match)
            tag: Filter by tag
            group_by: Group by: section, status, priority, source
            limit: Max results (default 100). 0 means NO results, EXCEPT when
                    `id`/`ids` is given, where the id list sets a floor and a
                    smaller limit is raised to fit it. A negative value is an
                    error (it used to mean "no limit").
            offset: Pagination offset
        """
        from codebugs import blockers

        with conn_factory() as conn:
            # CB-196, the twin of the findings wrapper: the `deferred` branch
            # returns without reaching `query_requirements`, so a negative limit
            # used to be accepted at exit 0 whenever no deferred row existed.
            limit = require_row_limit("limit", limit)

            deferred_ids: list[str] | None = None
            if status == "deferred":
                # Pseudo-status resolved to an id restriction so the ordinary query
                # applies `priority` and every sibling filter. The twin of the
                # findings wrapper above; see its comment for why (CB-28).
                # ONE evaluation for both halves (CB-69) — see the findings twin.
                deferred_ids, deferred_counts = blockers.deferred_ids_and_counts(
                    conn, ENTITY_REQUIREMENT, id=id, ids=ids
                )
                if not deferred_ids:
                    # MUST NOT fall through as `ids=[]` — that reads as "no filter".
                    return {
                        "grouped": False, "total": 0, "limit": limit,
                        "offset": offset, "requirements": [],
                    }
                id, ids, status = None, deferred_ids, None
            result = query_requirements(
                conn, id=id, ids=ids, status=status, priority=priority, section=section,
                search=search, source=source, tag=tag,
                group_by=group_by, limit=limit, offset=offset,
            )
            if deferred_ids is not None and not result.get("grouped"):
                for row in result["requirements"]:
                    row["blocker_count"] = deferred_counts.get(row["id"], 0)
            return result

    @mcp.tool()
    def reqs_get(req_id: str) -> dict[str, Any]:
        """Fetch a single requirement by ID with full body.

        Raises a not-found error if the ID does not exist. For lenient batch
        lookup that silently drops missing IDs, use `reqs_query(ids=[...])`.

        Args:
            req_id: The requirement ID (e.g. FR-001)
        """
        with conn_factory() as conn:
            return get_requirement(conn, req_id)

    @mcp.tool()
    def reqs_stats(group_by: str = "status") -> dict[str, Any]:
        """Aggregated requirement counts by status x priority.

        Args:
            group_by: Group by: status, priority, section, source
        """
        with conn_factory() as conn:
            return get_reqs_stats(conn, group_by=group_by)

    @mcp.tool()
    def reqs_summary() -> dict[str, Any]:
        """Dashboard overview --- status breakdown, priority split,
        section progress, requirements without tests, deferred counts. Start here."""
        from codebugs import blockers

        with conn_factory() as conn:
            result = get_reqs_summary(conn)
            result.update(blockers.get_deferred_counts(conn, ENTITY_REQUIREMENT))
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
        - status: contradictions (description says superseded but status says planned)

        Args:
            checks: List of checks to run (default: all). Options: tests, ids, status
            project_dir: Project root for test file verification (default: cwd)
        """
        with conn_factory() as conn:
            return verify_requirements(conn, project_dir=project_dir, checks=checks)

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
        with conn_factory() as conn:
            return import_markdown(conn, markdown_path)

    # Delegate embedding tools to the embeddings module
    from codebugs.embeddings import register_tools as embedding_tools
    embedding_tools(mcp, conn_factory)


register_tool_provider("reqs", register_tools)


# --- CLI ---

def register_cli(sub, commands) -> None:
    """Register requirements CLI subcommands."""
    import argparse
    import sys
    from codebugs import db
    from codebugs.fmt import empty_page_line, format_table
    from codebugs.fsio import atomic_write, diagnostic_stream
    from codebugs.types import REQUIREMENT_STATUSES, PRIORITIES

    def _cmd_reqs_add(args: argparse.Namespace) -> None:
        conn = db.connect()
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
        result = add_requirement(
            conn, req_id=args.id, description=args.description,
            section=args.section or "", priority=args.priority or "should",
            status=args.status or "planned", source=args.source or "",
            test_coverage=args.test_coverage or "", tags=tags,
        )
        conn.close()
        print(f"Added: {result['id']}")

    def _cmd_reqs_update(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        # Routed through the shared wrapper so a bad `--priority`/`--status`
        # prints one line and exits 1 instead of a raw traceback — the
        # CLAUDE.md-named debt ("reproduces the bug the day someone adds a
        # ValueError arm for resolve_priority"), closed here rather than by a
        # hand-written local ValueError arm.
        conn = db.connect()
        try:
            with domain_errors():
                result = update_requirement(
                    conn, args.id, status=args.status,
                    description=args.description, priority=args.priority,
                    test_coverage=args.test_coverage, notes=args.notes,
                )
                print(f"Updated: {result['id']} (status={result['status']})")
        finally:
            conn.close()

    def _cmd_reqs_query(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        ids = [s.strip() for s in args.id.split(",") if s.strip()] if args.id else None
        try:
            # json.JSONDecodeError re-raises (domain_errors, cli.py): a corrupt
            # stored row is not bad user input — see the same contract on
            # `findings._cmd_query` and `_cmd_update`. `--status`/`--priority`
            # are free text and resolve, so an unknown value names itself and
            # exits 1 rather than printing a traceback (CB-19).
            with domain_errors():
                result = query_requirements(
                    conn, ids=ids,
                    status=args.status, priority=args.priority,
                    section=args.section, search=args.search,
                    group_by=args.group_by,
                    # CB-124: `or 100` silently turned `--limit 0` into 100
                    # rows. `argparse` gives None only when the flag is
                    # absent.
                    limit=args.limit if args.limit is not None else 100,
                )
        finally:
            conn.close()

        if result.get("grouped"):
            data = [{"group": r["group_key"], "count": str(r["count"])} for r in result["groups"]]
            print(format_table(data, ["group", "count"]))
        else:
            items = result["requirements"]
            if not items:
                # CB-210 -- see `fmt.empty_page_line` for the predicate and why
                # both halves of its condition are load-bearing.
                print(
                    empty_page_line(
                        args.limit,
                        result.get("total", 0),
                        empty="(no requirements match)",
                        requested=(
                            "(limit was 0, so no rows were requested — "
                            "{n} requirement(s) match)"
                        ),
                    )
                )
                return
            data = [
                {
                    "id": r["id"], "priority": r["priority"],
                    "status": r["status"], "section": r["section"],
                    "description": r["description"],
                }
                for r in items
            ]
            print(format_table(
                data, ["id", "priority", "status", "section", "description"],
                max_widths={"description": 60, "section": 30},
            ))
            print(f"\n{result['total']} requirement(s) total.")

    def _cmd_reqs_get(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        try:
            with domain_errors():
                result = get_requirement(conn, args.id)
        finally:
            conn.close()
        print(json.dumps(result, indent=2, sort_keys=True))

    def _cmd_reqs_stats(args: argparse.Namespace) -> None:
        conn = db.connect()
        result = get_reqs_stats(conn, group_by=args.by or "status")
        conn.close()

        groups = result["groups"]
        if not groups:
            print("(no requirements)")
            return

        header = f"{'':30s} {'must':>8s} {'should':>8s} {'could':>8s} {'total':>8s}"
        print(header)
        print("-" * len(header))
        totals = {"must": 0, "should": 0, "could": 0, "total": 0}
        for grp in sorted(groups):
            d = groups[grp]
            print(f"{grp:30s} {d['must']:>8d} {d['should']:>8d} {d['could']:>8d} {d['total']:>8d}")
            for k in totals:
                totals[k] += d[k]
        print("-" * len(header))
        print(f"{'TOTAL':30s} {totals['must']:>8d} {totals['should']:>8d} {totals['could']:>8d} {totals['total']:>8d}")

    def _cmd_reqs_summary(args: argparse.Namespace) -> None:
        conn = db.connect()
        s = get_reqs_summary(conn)
        conn.close()

        print("Requirements Summary")
        print("=" * 50)
        print(f"Total: {s['total']}")
        print()
        print("By status:")
        for status in REQUIREMENT_STATUSES:
            c = s["by_status"].get(status, 0)
            bar = "#" * min(c, 40)
            print(f"  {status:12s}  {c:>4d}  {bar}")
        print()
        print("By priority:")
        for p in PRIORITIES:
            print(f"  {p:12s}  {s['by_priority'].get(p, 0):>4d}")
        if s["implemented_without_tests"]:
            print(f"\nImplemented without tests: {s['implemented_without_tests']}")
        if s["sections"]:
            print("\nSection progress:")
            for sec in s["sections"]:
                pct = (sec["done"] / sec["total"] * 100) if sec["total"] else 0
                print(f"  {sec['section']:40s}  {sec['done']}/{sec['total']} ({pct:.0f}%)")

    def _cmd_reqs_verify(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        checks = args.checks.split(",") if args.checks else None
        try:
            # json.JSONDecodeError re-raises (domain_errors, cli.py):
            # JSONDecodeError SUBCLASSES ValueError, and this one comes from
            # `db.row_to_dict` on a row with malformed stored tags/meta — a
            # corrupt-data fault, not bad input. Reporting it as bad input
            # would print a tidy line for a genuine data problem. The
            # deleted-cwd refusal from the "tests" check (CB-79) is a
            # ValueError and prints as one line.
            with domain_errors(prefix="codebugs: "):
                result = verify_requirements(conn, project_dir=args.project_dir, checks=checks)
        finally:
            # Was absent entirely: any exception leaked the connection, exactly
            # as _cmd_reqs_import did before CB-71.
            conn.close()

        print(f"Verified {result['total_requirements']} requirements.")
        if not result["issues"]:
            print("No issues found.")
            return

        print(f"\n{result['issues_found']} issue(s) found:\n")
        data = [
            {"check": i["check"], "sev": i["severity"], "id": i["id"], "message": i["message"]}
            for i in result["issues"]
        ]
        print(format_table(data, ["check", "sev", "id", "message"], max_widths={"message": 70}))

    def _cmd_reqs_import(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            # An unreadable path escaped as a raw traceback here, and this handler
            # had no arm at all (CB-71). Guarding the whole call is safe ONLY
            # because import_markdown materializes the file eagerly —
            # `f.readlines()` at reqs.py:537 — before it writes a single row or
            # commits (reqs.py:606), so no OSError can arise after the write and
            # be laundered into an input error. That premise is pinned by
            # TestImportMarkdownReadIsEagerPremise; convert the read to lazy
            # iteration and the suite goes red instead of this becoming the
            # CB-15/CB-16 lie silently.
            result = import_markdown(conn, args.file)
        except OSError as e:
            print(f"codebugs: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            # Was absent: any exception leaked the connection.
            conn.close()
        print(f"Imported {result['imported']} requirements, skipped {result['skipped']}.")

    def _cmd_reqs_export(args: argparse.Namespace) -> None:
        conn = db.connect()
        md = export_markdown(conn)
        conn.close()

        if args.file:
            # CB-76, twin of findings._cmd_export_csv. The stdout branch below
            # stays OUTSIDE both the helper and the guard.
            #
            # CB-78 ANSWERED the open question this comment used to defer: a
            # closed pipe now kills the process by SIGPIPE (exit 141, empty
            # stderr) before any `BrokenPipeError` exists to catch, because
            # `cli.run` installs `SIG_DFL`. So the reason for keeping the stdout
            # branch outside has changed rather than gone away — it is no longer
            # "that decision belongs to CB-78", it is that wrapping it could only
            # ever catch a NON-pipe stdout failure, and that arm below is
            # correspondingly unreachable for a pipe or FIFO destination. Left in
            # place for regular-file and device destinations; see CB-55, which
            # owns the consolidation of these six arms and carries this note.
            try:
                with atomic_write(args.file) as (f, dest):
                    f.write(md)
            except OSError as e:
                print(f"codebugs: {e}", file=sys.stderr)
                sys.exit(1)
            # CB-143/CB-194, twin of findings._cmd_export_csv: when `args.file`
            # is an alias of this process's own stdout AND/OR stderr,
            # `atomic_write` wrote the markdown through a fresh open of the
            # same inode, a second and independent file offset. Printing this
            # line to whichever inherited descriptor is ALSO that inode would
            # land inside the file we just wrote. `diagnostic_stream` is the
            # single place that turns `dest` into a channel choice — never
            # recompute that choice here — and it can answer "print nothing"
            # when both stdout and stderr are the destination.
            stream = diagnostic_stream(dest)
            if stream is not None:
                print(f"Exported to {args.file}", file=stream)
        else:
            print(md)

    # Argparse setup
    p = sub.add_parser("reqs-add", help="Add a requirement")
    p.add_argument("id", help="Requirement ID (e.g. FR-001)")
    p.add_argument("-d", "--description", required=True, help="Description")
    p.add_argument("--section", help="Section name")
    p.add_argument("--priority", help="Must|Should|Could")
    p.add_argument("--status", help="Planned|Partial|Implemented|Verified|Superseded|Obsolete")
    p.add_argument("--source", help="Source reference")
    p.add_argument("--test-coverage", help="Test file name(s)")
    p.add_argument("--tags", help="Comma-separated tags")

    p = sub.add_parser("reqs-update", help="Update a requirement")
    p.add_argument("id", help="Requirement ID")
    p.add_argument("--status", help="New status")
    p.add_argument("--description", help="Updated description")
    p.add_argument("--priority", help="Updated priority")
    p.add_argument("--test-coverage", help="Updated test coverage")
    p.add_argument("--notes", help="Notes")

    p = sub.add_parser("reqs-query", help="Search requirements")
    p.add_argument("--id", help="Filter by ID (single FR-N or comma-separated list)")
    p.add_argument("--status", help="Filter by status")
    p.add_argument("--priority", help="Filter by priority")
    p.add_argument("--section", help="Filter by section (substring)")
    p.add_argument("--search", help="Search in description/ID")
    p.add_argument("--group-by", help="Group by: section|status|priority|source")
    p.add_argument(
        "--limit",
        type=int,
        help="Max results (0 for none unless --id/--ids is given; negative is an error)",
    )

    p = sub.add_parser("reqs-get", help="Fetch a single requirement by ID")
    p.add_argument("id", help="Requirement ID (e.g. FR-001)")

    p = sub.add_parser("reqs-stats", help="Requirements cross-tab")
    p.add_argument("--by", help="Group by: status|priority|section|source")

    sub.add_parser("reqs-summary", help="Requirements dashboard")

    p = sub.add_parser("reqs-verify", help="Verify requirements for issues")
    p.add_argument("--checks", help="Comma-separated: tests,ids,status (default: all)")
    p.add_argument("--project-dir", help="Project root for test file checks")

    p = sub.add_parser("reqs-import", help="Import from REQUIREMENTS.md")
    p.add_argument("file", help="Markdown file path")

    p = sub.add_parser("reqs-export", help="Export as markdown")
    p.add_argument("file", nargs="?", help="Output file (default: stdout)")

    commands.update({
        "reqs-add": _cmd_reqs_add,
        "reqs-update": _cmd_reqs_update,
        "reqs-query": _cmd_reqs_query,
        "reqs-get": _cmd_reqs_get,
        "reqs-stats": _cmd_reqs_stats,
        "reqs-summary": _cmd_reqs_summary,
        "reqs-verify": _cmd_reqs_verify,
        "reqs-import": _cmd_reqs_import,
        "reqs-export": _cmd_reqs_export,
    })


register_cli_provider("reqs", register_cli)
