"""Database layer — sweep batch-iteration for codebugs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from codebugs.types import utc_now


SCHEMA = """\
CREATE TABLE IF NOT EXISTS codesweep_sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id TEXT UNIQUE NOT NULL,
    name TEXT,
    description TEXT NOT NULL DEFAULT '',
    default_batch_size INTEGER NOT NULL DEFAULT 10,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_codesweep_sweeps_name
    ON codesweep_sweeps(name) WHERE name IS NOT NULL;

CREATE TABLE IF NOT EXISTS codesweep_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id TEXT NOT NULL REFERENCES codesweep_sweeps(sweep_id),
    item TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    processed INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE(sweep_id, item)
);

CREATE INDEX IF NOT EXISTS idx_codesweep_items_next
    ON codesweep_items(sweep_id, processed, position);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the codesweep tables if they don't exist."""
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def _resolve_sweep(conn: sqlite3.Connection, ref: str) -> str:
    """Resolve a sweep reference (SW-N or name) to a sweep_id.

    Raises ValueError if not found.
    """
    row = conn.execute(
        "SELECT sweep_id FROM codesweep_sweeps WHERE sweep_id = ? OR name = ?",
        (ref, ref),
    ).fetchone()
    if not row:
        raise ValueError(f"Sweep not found: {ref}")
    return row["sweep_id"]


def create_sweep(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    description: str = "",
    default_batch_size: int = 10,
) -> dict[str, Any]:
    """Create a new sweep. Returns the created sweep as a dict."""
    if default_batch_size < 1:
        raise ValueError("Batch size must be at least 1")

    if name is not None:
        existing = conn.execute(
            "SELECT 1 FROM codesweep_sweeps WHERE name = ?", (name,),
        ).fetchone()
        if existing:
            raise ValueError(f"Sweep name already exists: {name}")

    now = utc_now()
    cursor = conn.execute(
        """INSERT INTO codesweep_sweeps
           (sweep_id, name, description, default_batch_size, status, created_at, updated_at)
           VALUES ('_placeholder', ?, ?, ?, 'active', ?, ?)""",
        (name, description, default_batch_size, now, now),
    )
    sweep_id = f"SW-{cursor.lastrowid}"
    conn.execute(
        "UPDATE codesweep_sweeps SET sweep_id = ? WHERE id = ?",
        (sweep_id, cursor.lastrowid),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM codesweep_sweeps WHERE id = ?", (cursor.lastrowid,),
    ).fetchone()
    return _sweep_to_dict(row)


def _sweep_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sweep row to a dict."""
    return {
        "sweep_id": row["sweep_id"],
        "name": row["name"],
        "description": row["description"],
        "default_batch_size": row["default_batch_size"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _next_position(conn: sqlite3.Connection, sweep_id: str) -> int:
    """Return the next insertion position for a sweep."""
    row = conn.execute(
        "SELECT MAX(position) as max_pos FROM codesweep_items WHERE sweep_id = ?",
        (sweep_id,),
    ).fetchone()
    return (row["max_pos"] + 1) if row["max_pos"] is not None else 0


def add_items(
    conn: sqlite3.Connection,
    sweep_ref: str,
    items: list[str],
    *,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Add items to a sweep. Duplicates are silently skipped."""
    sweep_id = _resolve_sweep(conn, sweep_ref)

    status = conn.execute(
        "SELECT status FROM codesweep_sweeps WHERE sweep_id = ?", (sweep_id,),
    ).fetchone()["status"]
    if status == "archived":
        raise ValueError(f"Cannot add items to archived sweep: {sweep_id}")

    now = utc_now()
    tags_json = json.dumps(tags or [])
    pos = _next_position(conn, sweep_id)
    added = 0
    duplicates = 0

    for item in items:
        try:
            conn.execute(
                """INSERT INTO codesweep_items
                   (sweep_id, item, tags, processed, position, created_at)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (sweep_id, item, tags_json, pos, now),
            )
            pos += 1
            added += 1
        except sqlite3.IntegrityError:
            duplicates += 1

    conn.execute(
        "UPDATE codesweep_sweeps SET updated_at = ? WHERE sweep_id = ?",
        (now, sweep_id),
    )
    conn.commit()
    return {"sweep_id": sweep_id, "added": added, "duplicates_skipped": duplicates}


def next_batch(
    conn: sqlite3.Connection,
    sweep_ref: str,
    *,
    limit: int | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return the next batch of unprocessed items in insertion order."""
    sweep_id = _resolve_sweep(conn, sweep_ref)

    row = conn.execute(
        "SELECT default_batch_size FROM codesweep_sweeps WHERE sweep_id = ?",
        (sweep_id,),
    ).fetchone()
    batch_size = limit if limit is not None else row["default_batch_size"]

    conditions = ["sweep_id = ?", "processed = 0"]
    params: list[Any] = [sweep_id]

    if tags:
        tag_conditions = []
        for tag in tags:
            tag_conditions.append(
                "EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)"
            )
            params.append(tag)
        conditions.append(f"({' OR '.join(tag_conditions)})")

    where = f"WHERE {' AND '.join(conditions)}"

    rows = conn.execute(
        f"SELECT item, tags, position FROM codesweep_items {where} ORDER BY position LIMIT ?",
        params + [batch_size],
    ).fetchall()

    items = [
        {"item": r["item"], "tags": json.loads(r["tags"]), "position": r["position"]}
        for r in rows
    ]

    # Count total remaining unprocessed (with same tag filter) minus what we just returned
    total_unprocessed = conn.execute(
        f"SELECT COUNT(*) as c FROM codesweep_items {where}",
        params,
    ).fetchone()["c"]
    remaining = total_unprocessed - len(items)

    return {"sweep_id": sweep_id, "items": items, "remaining": remaining}


def mark_items(
    conn: sqlite3.Connection,
    sweep_ref: str,
    items: list[str],
    *,
    processed: bool = True,
) -> dict[str, Any]:
    """Mark items as processed or unprocessed."""
    sweep_id = _resolve_sweep(conn, sweep_ref)
    now = utc_now()

    for item in items:
        if processed:
            cursor = conn.execute(
                "UPDATE codesweep_items SET processed = 1, processed_at = ? WHERE sweep_id = ? AND item = ?",
                (now, sweep_id, item),
            )
        else:
            cursor = conn.execute(
                "UPDATE codesweep_items SET processed = 0, processed_at = NULL WHERE sweep_id = ? AND item = ?",
                (sweep_id, item),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Item not found in sweep {sweep_id}: {item}")

    conn.execute(
        "UPDATE codesweep_sweeps SET updated_at = ? WHERE sweep_id = ?",
        (now, sweep_id),
    )
    conn.commit()
    return {"sweep_id": sweep_id, "updated": len(items)}


def get_status(
    conn: sqlite3.Connection,
    sweep_ref: str,
) -> dict[str, Any]:
    """Return sweep overview with progress and per-tag breakdown."""
    sweep_id = _resolve_sweep(conn, sweep_ref)
    sw = conn.execute(
        "SELECT * FROM codesweep_sweeps WHERE sweep_id = ?", (sweep_id,),
    ).fetchone()

    counts = conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) as processed
           FROM codesweep_items WHERE sweep_id = ?""",
        (sweep_id,),
    ).fetchone()
    total = counts["total"]
    processed = counts["processed"] or 0

    # Per-tag breakdown
    tag_rows = conn.execute(
        """SELECT jt.value as tag,
                  COUNT(*) as total,
                  SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) as done
           FROM codesweep_items, json_each(tags) as jt
           WHERE sweep_id = ?
           GROUP BY jt.value""",
        (sweep_id,),
    ).fetchall()
    by_tag = {
        r["tag"]: {"total": r["total"], "processed": r["done"]}
        for r in tag_rows
    }

    return {
        "sweep_id": sweep_id,
        "name": sw["name"],
        "status": sw["status"],
        "default_batch_size": sw["default_batch_size"],
        "total": total,
        "processed": processed,
        "remaining": total - processed,
        "by_tag": by_tag,
    }


def archive_sweep(
    conn: sqlite3.Connection,
    sweep_ref: str,
) -> dict[str, Any]:
    """Archive a sweep."""
    sweep_id = _resolve_sweep(conn, sweep_ref)
    conn.execute(
        "UPDATE codesweep_sweeps SET status = 'archived', updated_at = ? WHERE sweep_id = ?",
        (utc_now(), sweep_id),
    )
    conn.commit()
    return {"sweep_id": sweep_id, "status": "archived"}


def list_sweeps(
    conn: sqlite3.Connection,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """List all sweeps with summary counts."""
    condition = "" if include_archived else "WHERE s.status = 'active'"
    rows = conn.execute(
        f"""SELECT s.sweep_id, s.name, s.status, s.default_batch_size,
                   COUNT(i.id) as total,
                   SUM(CASE WHEN i.processed = 1 THEN 1 ELSE 0 END) as processed
            FROM codesweep_sweeps s
            LEFT JOIN codesweep_items i ON s.sweep_id = i.sweep_id
            {condition}
            GROUP BY s.sweep_id
            ORDER BY s.id""",
    ).fetchall()
    sweeps = []
    for r in rows:
        total = r["total"] or 0
        processed = r["processed"] or 0
        sweeps.append({
            "sweep_id": r["sweep_id"],
            "name": r["name"],
            "status": r["status"],
            "total": total,
            "processed": processed,
            "remaining": total - processed,
        })
    return {"sweeps": sweeps}


from codebugs.db import register_schema, register_tool_provider, register_cli_provider  # noqa: E402

register_schema("sweep", ensure_schema)


def register_tools(mcp, conn_factory) -> None:
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
        with conn_factory() as conn:
            return create_sweep(
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
        with conn_factory() as conn:
            return add_items(conn, sweep_ref, items, tags=tags)

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
        with conn_factory() as conn:
            return next_batch(conn, sweep_ref, limit=limit, tags=tags)

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
        with conn_factory() as conn:
            return mark_items(conn, sweep_ref, items, processed=processed)

    @mcp.tool()
    def codesweep_status(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Sweep overview — total, processed, remaining counts, per-tag breakdown.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        with conn_factory() as conn:
            return get_status(conn, sweep_ref)

    @mcp.tool()
    def codesweep_archive(
        sweep_ref: str,
    ) -> dict[str, Any]:
        """Archive a sweep. Archived sweeps are excluded from codesweep_list by default.

        Args:
            sweep_ref: Sweep ID (SW-N) or name
        """
        with conn_factory() as conn:
            return archive_sweep(conn, sweep_ref)

    @mcp.tool()
    def codesweep_list(
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List all sweeps with summary counts.

        Args:
            include_archived: Include archived sweeps (default: false)
        """
        with conn_factory() as conn:
            return list_sweeps(conn, include_archived=include_archived)


register_tool_provider("sweep", register_tools)


# --- CLI ---

def register_cli(sub, commands) -> None:
    """Register sweep CLI subcommands."""
    import argparse
    import sys
    from codebugs import db
    from codebugs.fmt import format_table

    def _parse_tags(args: argparse.Namespace) -> list[str] | None:
        """Parse comma-separated --tags argument."""
        return [t.strip() for t in args.tags.split(",")] if args.tags else None

    def _cmd_sweep_create(args: argparse.Namespace) -> None:
        conn = db.connect()
        kwargs: dict = {}
        if args.name:
            kwargs["name"] = args.name
        if args.description:
            kwargs["description"] = args.description
        if args.batch_size:
            kwargs["default_batch_size"] = args.batch_size
        try:
            result = create_sweep(conn, **kwargs)
            print(f"Created: {result['sweep_id']}" + (f" ({result['name']})" if result["name"] else ""))
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_add(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = add_items(conn, args.sweep, args.items, tags=_parse_tags(args))
            print(f"Added {result['added']} items, {result['duplicates_skipped']} duplicates skipped.")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_next(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = next_batch(conn, args.sweep, limit=args.limit, tags=_parse_tags(args))
            if not result["items"]:
                print("(no unprocessed items)")
                return
            data = [{"item": i["item"], "tags": ",".join(i["tags"])} for i in result["items"]]
            print(format_table(data, ["item", "tags"], max_widths={"item": 60}))
            print(f"\n{result['remaining']} remaining.")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_mark(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = mark_items(conn, args.sweep, args.items, processed=not args.undo)
            action = "Unmarked" if args.undo else "Marked"
            print(f"{action} {result['updated']} items.")
        except (ValueError, KeyError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_status(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            s = get_status(conn, args.sweep)
            print(f"Sweep: {s['sweep_id']}" + (f" ({s['name']})" if s["name"] else ""))
            print(f"Status: {s['status']}")
            print(f"Items:  {s['processed']}/{s['total']} processed, {s['remaining']} remaining")
            if s["by_tag"]:
                print("\nBy tag:")
                for tag, counts in sorted(s["by_tag"].items()):
                    print(f"  {tag:20s}  {counts['processed']}/{counts['total']}")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_archive(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = archive_sweep(conn, args.sweep)
            print(f"Archived: {result['sweep_id']}")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_sweep_list(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = list_sweeps(conn, include_archived=args.all)
            if not result["sweeps"]:
                print("(no sweeps)")
                return
            data = [
                {
                    "sweep_id": s["sweep_id"],
                    "name": s["name"] or "",
                    "status": s["status"],
                    "progress": f"{s['processed']}/{s['total']}",
                    "remaining": str(s["remaining"]),
                }
                for s in result["sweeps"]
            ]
            print(format_table(data, ["sweep_id", "name", "status", "progress", "remaining"]))
        finally:
            conn.close()

    # Argparse setup
    p = sub.add_parser("sweep-create", help="Create a new sweep")
    p.add_argument("--name", help="Optional sweep name")
    p.add_argument("--description", help="Sweep description")
    p.add_argument("--batch-size", type=int, help="Default batch size (default: 10)")

    p = sub.add_parser("sweep-add", help="Add items to a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to add")
    p.add_argument("--tags", help="Comma-separated tags")

    p = sub.add_parser("sweep-next", help="Get next batch of unprocessed items")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("--limit", type=int, help="Batch size override")
    p.add_argument("--tags", help="Filter by tags (comma-separated)")

    p = sub.add_parser("sweep-mark", help="Mark items as processed")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to mark")
    p.add_argument("--undo", action="store_true", help="Unmark items instead")

    p = sub.add_parser("sweep-status", help="Sweep progress overview")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-archive", help="Archive a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-list", help="List sweeps")
    p.add_argument("--all", action="store_true", help="Include archived sweeps")

    commands.update({
        "sweep-create": _cmd_sweep_create,
        "sweep-add": _cmd_sweep_add,
        "sweep-next": _cmd_sweep_next,
        "sweep-mark": _cmd_sweep_mark,
        "sweep-status": _cmd_sweep_status,
        "sweep-archive": _cmd_sweep_archive,
        "sweep-list": _cmd_sweep_list,
    })


register_cli_provider("sweep", register_cli)
