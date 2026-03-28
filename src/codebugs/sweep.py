"""Database layer — sweep batch-iteration for codebugs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    now = _now()
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
