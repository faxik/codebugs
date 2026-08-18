"""Database layer — typed, retractable relations between findings.

WHY THIS TABLE EXISTS. Relations were being recorded in ad-hoc JSON ``meta``
keys — 164 distinct key names for roughly five concepts, measured over the
reference corpus. That substrate cannot answer "what is related to CB-123", and
it cannot forget: ``meta`` writes are merge-only (``findings.py:923-924`` is
``dict.update``), so a key can be overwritten but never removed. A relation that
must be queryable AND retractable needs a table.

ORIENTATION IS NOT COSMETIC. Two relations are symmetric and are stored in
canonical orientation so one edge exists per pair. The rest are directed, and
``duplicate_of`` is the one that bites: it names loser -> survivor, so
canonicalising it lexicographically SWAPS which card survives. Measured on the
live tracker, all three real ``duplicate_of`` facts invert under a lexicographic
rule (CB-878->CB-877, CB-2946->CB-2935, CB-2251->CB-2227). This was the
highest-severity finding of the design's adversarial review.

MIGRATION OF THE LEGACY KEYS IS DELIBERATELY NOT HERE. See
``.claude/plans/PLAN-relations-migration-2026-08-18.md``, which carries twelve
open defects and is not implementable yet. This module is the ledger only; the
50 human-labelled edges in the gold set can be seeded through ``relate()``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from codebugs import db
from codebugs.entities import EntityRef
from codebugs.types import utc_now


RELATIONS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS finding_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id TEXT NOT NULL,
    rel TEXT NOT NULL
        CHECK(rel IN ('duplicate_of', 'split_from', 'follow_up_of',
                      'found_during', 'distinct_from', 'related_to')),
    dst_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT,
    retracted_at TEXT,
    retracted_by TEXT,
    retracted_reason TEXT,
    CHECK (src_id != dst_id),
    CHECK ((retracted_at IS NULL) = (retracted_by IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_live
    ON finding_relations(src_id, rel, dst_id) WHERE retracted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_relations_src ON finding_relations(src_id);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON finding_relations(dst_id);
"""

#: The full vocabulary. Mirrored by the CHECK constraint above — the DB is the
#: authority, this tuple is for validation messages and the CLI/tool surface.
RELATIONS: tuple[str, ...] = (
    "duplicate_of",
    "split_from",
    "follow_up_of",
    "found_during",
    "distinct_from",
    "related_to",
)

#: Stored with the lexicographic min as ``src`` so the unique index enforces one
#: edge per pair and no reader has to search both directions. Everything else is
#: directed and its orientation is DATA — see the module docstring.
SYMMETRIC: frozenset[str] = frozenset({"distinct_from", "related_to"})

#: Terms that belong to another owner. Refused with a pointer rather than
#: silently accepted, because each would create a second authority for a fact
#: that already has one.
#:
#: This deliberately does NOT use the self-registration idiom the package uses
#: for runtime-minted ``meta`` keys (``db.register_pre_add_resolver`` /
#: ``db.resolver_reserved_meta_keys``). That machinery exists because any module
#: can mint a meta key at runtime, so collisions must be detected live. This
#: vocabulary is closed: it can only change by editing this file and its CHECK
#: constraint together, under review. A fifth registry for three fixed strings
#: would be indirection without a reader.
_NOT_OURS: dict[str, str] = {
    "recurrence_of": (
        "'recurrence_of' is core-owned: it is written by the identity machinery and "
        "guarded by findings._RESERVED_META_KEYS, whose comment names the spoofing "
        "attack verbatim. It cannot be asserted through this ledger."
    ),
    "blocked_by": (
        "'blocked_by' is owned by the blockers module, which carries trigger and "
        "lifecycle semantics this table does not. Use blockers-add / blockers_add."
    ),
    "similar_to": (
        "'similar_to' is annotator-owned (similarity.py) and is an advisory snapshot, "
        "not a ratified fact."
    ),
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the finding_relations table if it doesn't exist."""
    for stmt in RELATIONS_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def _validate_rel(rel: str) -> None:
    if rel in _NOT_OURS:
        raise ValueError(_NOT_OURS[rel])
    if rel not in RELATIONS:
        raise ValueError(
            f"Unknown relation {rel!r}. Expected one of: {', '.join(RELATIONS)}."
        )


def _require_exists(conn: sqlite3.Connection, entity_id: str) -> None:
    """Endpoint validation lives here, not in a foreign key.

    ``db._open`` never enables ``PRAGMA foreign_keys``, and ``findings.py``'s
    legacy status migration toggles it OFF/ON, so enforcement is
    per-connection nondeterministic. A declared FK would be decorative.
    """
    if not EntityRef.of(entity_id).exists(conn):
        raise ValueError(f"No such finding: {entity_id}")


def _orient(rel: str, src_id: str, dst_id: str) -> tuple[str, str]:
    """Canonicalize symmetric relations; leave directed ones exactly as passed."""
    if rel in SYMMETRIC:
        return (src_id, dst_id) if src_id <= dst_id else (dst_id, src_id)
    return src_id, dst_id


def _live_edge(
    conn: sqlite3.Connection, src_id: str, rel: str, dst_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM finding_relations "
        "WHERE src_id = ? AND rel = ? AND dst_id = ? AND retracted_at IS NULL",
        (src_id, rel, dst_id),
    ).fetchone()


def _guard_contradictions(
    conn: sqlite3.Connection, rel: str, src_id: str, dst_id: str
) -> None:
    """Refuse a fact that contradicts a live one about the same pair.

    Two shapes, both real:
      * ``duplicate_of`` vs ``distinct_from`` — these assert opposite things.
      * a reciprocal ``duplicate_of`` — both cards cannot be the loser.

    A RETRACTED edge asserts nothing and therefore vetoes nothing.
    """
    if rel == "duplicate_of":
        lo, hi = sorted((src_id, dst_id))
        if _live_edge(conn, lo, "distinct_from", hi):
            raise ValueError(
                f"Refusing duplicate_of: a live distinct_from already asserts "
                f"{lo} and {hi} are different defects. Retract it first."
            )
        if _live_edge(conn, dst_id, "duplicate_of", src_id):
            raise ValueError(
                f"Refusing reciprocal duplicate_of: {dst_id} is already recorded as a "
                f"duplicate of {src_id}, and both cards cannot be the loser."
            )
    elif rel == "distinct_from":
        for a, b in ((src_id, dst_id), (dst_id, src_id)):
            if _live_edge(conn, a, "duplicate_of", b):
                raise ValueError(
                    f"Refusing distinct_from: a live duplicate_of already asserts "
                    f"{a} is a duplicate of {b}. Retract it first."
                )


def relate(
    conn: sqlite3.Connection,
    src_id: str,
    rel: str,
    dst_id: str,
    source: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Assert a relation between two findings.

    Idempotent on a live edge: re-relating returns the existing row unchanged.
    The original ``source``/``note`` win, because a second opinion is a note
    append, not an overwrite.

    Args:
        src_id: For ``duplicate_of`` this is the LOSER — the card that dies.
        rel: One of RELATIONS.
        dst_id: For ``duplicate_of`` this is the SURVIVOR.
        source: Who is asserting this. Never hardcode it; an audited ledger
                whose actor is a placeholder is not audited.
        note: Optional free text explaining the assertion.
    """
    _validate_rel(rel)
    if src_id == dst_id:
        raise ValueError(f"A finding cannot relate to itself: {src_id}")

    with db.txn(conn):
        _require_exists(conn, src_id)
        _require_exists(conn, dst_id)

        src_id, dst_id = _orient(rel, src_id, dst_id)

        existing = _live_edge(conn, src_id, rel, dst_id)
        if existing is not None:
            return db.row_to_dict(existing)

        _guard_contradictions(conn, rel, src_id, dst_id)

        row = conn.execute(
            "INSERT INTO finding_relations "
            "(src_id, rel, dst_id, created_at, source, note) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING *",
            (src_id, rel, dst_id, utc_now(), source, note),
        ).fetchone()
        return db.row_to_dict(row)


def unrelate(
    conn: sqlite3.Connection,
    src_id: str,
    rel: str,
    dst_id: str,
    retracted_by: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Retract a relation by tombstoning it. Never a DELETE.

    The most dangerous write in this table is a wrong ``distinct_from``: it
    suppresses a pair from every future discovery path, and a suppression that
    should not exist is invisible by construction. So retraction stays auditable.
    """
    _validate_rel(rel)

    with db.txn(conn):
        src_id, dst_id = _orient(rel, src_id, dst_id)
        existing = _live_edge(conn, src_id, rel, dst_id)
        if existing is None:
            raise ValueError(
                f"No live {rel} edge from {src_id} to {dst_id} to retract."
            )
        row = conn.execute(
            "UPDATE finding_relations "
            "SET retracted_at = ?, retracted_by = ?, retracted_reason = ? "
            "WHERE id = ? RETURNING *",
            (utc_now(), retracted_by, reason, existing["id"]),
        ).fetchone()
        return db.row_to_dict(row)


def query_relations(
    conn: sqlite3.Connection,
    entity_id: str | None = None,
    rel: str | None = None,
    include_retracted: bool = False,
) -> dict[str, Any]:
    """List relations, in BOTH directions for a given entity.

    A directed edge must be visible from either endpoint — otherwise a survivor
    could not see the cards merged into it.

    ``rel="distinct_from"`` with no entity is the active-suppressions view.
    """
    if rel is not None:
        _validate_rel(rel)

    where: list[str] = []
    params: list[Any] = []
    if entity_id is not None:
        where.append("(src_id = ? OR dst_id = ?)")
        params += [entity_id, entity_id]
    if rel is not None:
        where.append("rel = ?")
        params.append(rel)
    if not include_retracted:
        where.append("retracted_at IS NULL")

    sql = "SELECT * FROM finding_relations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"

    rows = [db.row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    return {"count": len(rows), "relations": rows}


import json  # noqa: E402
from codebugs.db import (  # noqa: E402
    register_cli_provider,
    register_schema,
    register_tool_provider,
)

register_schema("relations", ensure_schema, depends_on=("findings",))


def register_tools(mcp, conn_factory) -> None:
    """Register relation tools on the given MCP server."""

    @mcp.tool()
    def relations_relate(
        src_id: str,
        rel: str,
        dst_id: str,
        source: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Assert a typed relation between two findings.

        Args:
            src_id: Source finding (e.g. "CB-5"). For duplicate_of this is the
                    LOSER — the card that dies.
            rel: duplicate_of, split_from, follow_up_of, found_during,
                 distinct_from, or related_to.
            dst_id: Target finding. For duplicate_of this is the SURVIVOR.
            source: Who is asserting this (e.g. "owner", "goldset-2026-08-17").
            note: Optional reasoning.
        """
        with conn_factory() as conn:
            return relate(conn, src_id=src_id, rel=rel, dst_id=dst_id,
                          source=source, note=note)

    @mcp.tool()
    def relations_unrelate(
        src_id: str,
        rel: str,
        dst_id: str,
        retracted_by: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Retract a relation. Tombstones it — the row and its history remain.

        Args:
            src_id: Source finding
            rel: The relation to retract
            dst_id: Target finding
            retracted_by: Who is retracting it
            reason: Why
        """
        with conn_factory() as conn:
            return unrelate(conn, src_id=src_id, rel=rel, dst_id=dst_id,
                            retracted_by=retracted_by, reason=reason)

    @mcp.tool()
    def relations_query(
        entity_id: str | None = None,
        rel: str | None = None,
        include_retracted: bool = False,
    ) -> dict[str, Any]:
        """List relations touching a finding, in both directions.

        Args:
            entity_id: Finding to look up (e.g. "CB-5"). Omit to list all.
            rel: Filter by relation. `rel="distinct_from"` alone lists every
                 live suppression, which is worth reviewing periodically.
            include_retracted: Include tombstoned edges (default: false)
        """
        with conn_factory() as conn:
            return query_relations(conn, entity_id=entity_id, rel=rel,
                                   include_retracted=include_retracted)


register_tool_provider("relations", register_tools)


def register_cli(sub, commands):
    """Register relation CLI subcommands."""
    p = sub.add_parser("relations-relate", help="Assert a relation between two findings")
    p.add_argument("src_id", help="Source finding (LOSER for duplicate_of)")
    p.add_argument("rel", choices=list(RELATIONS))
    p.add_argument("dst_id", help="Target finding (SURVIVOR for duplicate_of)")
    p.add_argument("--source", required=True, help="Who is asserting this")
    p.add_argument("--note", help="Optional reasoning")
    commands["relations-relate"] = _cmd_relations_relate

    p = sub.add_parser("relations-unrelate", help="Retract a relation (tombstone)")
    p.add_argument("src_id")
    p.add_argument("rel", choices=list(RELATIONS))
    p.add_argument("dst_id")
    p.add_argument("--retracted-by", required=True, help="Who is retracting it")
    p.add_argument("--reason", help="Why")
    commands["relations-unrelate"] = _cmd_relations_unrelate

    p = sub.add_parser("relations-query", help="List relations touching a finding")
    p.add_argument("--entity-id", help="Finding to look up")
    p.add_argument("--rel", choices=list(RELATIONS))
    p.add_argument("--include-retracted", action="store_true")
    commands["relations-query"] = _cmd_relations_query


def _cmd_relations_relate(args):
    conn = db.connect()
    try:
        result = relate(conn, src_id=args.src_id, rel=args.rel, dst_id=args.dst_id,
                        source=args.source, note=args.note)
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


def _cmd_relations_unrelate(args):
    conn = db.connect()
    try:
        result = unrelate(conn, src_id=args.src_id, rel=args.rel, dst_id=args.dst_id,
                          retracted_by=args.retracted_by, reason=args.reason)
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


def _cmd_relations_query(args):
    conn = db.connect()
    try:
        result = query_relations(conn, entity_id=args.entity_id, rel=args.rel,
                                 include_retracted=args.include_retracted)
        print(json.dumps(result, indent=2))
    finally:
        conn.close()


register_cli_provider("relations", register_cli)
