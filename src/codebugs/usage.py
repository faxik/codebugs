"""Database layer — a counter for tool-call usage (release-b, DIR-1).

WHAT THIS IS FOR. Before this module there was no answer to "which tools get
used", "which calls fail", or "what is slow" — any judgment about what to
improve next was taste, not evidence. This module owns exactly one table,
`tool_calls`, and the `codebugs usage` CLI verb that summarizes it.

WHAT IT DELIBERATELY IS NOT. It registers NO MCP tool provider
(`register_tool_provider` is never called here). The write side is a
`server.py` middleware — see `install_usage_tracking` there, right beside
`install_strict_arguments`, which is the one place this project already
touches the SDK's provisional `MCPServer.middleware`. A tool that counts
tool calls would be counting itself, which is why the counting logic lives in
the transport layer rather than as a tool of its own.

ONE COLUMN IS A DELIBERATE NARROWING: `error_type` stores the FAILED call's
exception CLASS NAME, never its message. A message can carry the caller's own
data (a category name, a finding id, arbitrary free text) and can be
arbitrarily long; the class name answers "what kind of thing broke" without
disclosing any of that.

THE HONEST GAP, STATED HERE SO A READER OF THIS FILE ALONE SEES IT (also in
CLAUDE.md and the CLI verb's own output): this table only ever receives a row
from the MCP-server middleware. A CLI invocation of a `codebugs` verb, or a
library caller importing `codebugs.findings` directly, never touches it. The
`usage` verb says so itself, every time it runs, rather than leaving that
scope invisible.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from codebugs import db
from codebugs.fmt import format_table
from codebugs.types import utc_now

USAGE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    called_at TEXT NOT NULL,
    success INTEGER NOT NULL CHECK(success IN (0, 1)),
    error_type TEXT,
    duration_ms REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_called_at ON tool_calls(called_at);
"""

#: The scope disclosure every reader of a usage summary must see, verbatim,
#: whether they read `--help` or the summary's own output. See the module
#: docstring's "honest gap" paragraph for why this cannot be left implicit.
SCOPE_NOTE = (
    "codebugs usage counts only tool calls made through the MCP server; "
    "calls from the codebugs CLI or from a direct library import are not "
    "tracked here."
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the tool_calls table if it doesn't exist."""
    for stmt in USAGE_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


db.register_schema("usage", ensure_schema)


def record_call(
    conn: sqlite3.Connection,
    *,
    tool_name: str,
    success: bool,
    error_type: str | None,
    duration_ms: float,
) -> None:
    """Record one completed tool-call attempt.

    A single INSERT + commit, deliberately not wrapped in `db.txn`: there is
    no read-then-write race to protect against (each row is independent and
    self-contained), and the middleware caller (`server.py`) is itself the
    one place responsible for swallowing this function's exceptions — never
    letting a failed write reach the tool call it is trying to describe.
    """
    conn.execute(
        "INSERT INTO tool_calls (tool_name, called_at, success, error_type, duration_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (tool_name, utc_now(), 1 if success else 0, error_type, duration_ms),
    )
    conn.commit()


def usage_summary(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Per-tool call counts, failure counts, and timing, by call count descending.

    `since` compares lexicographically against the stored `db.utc_now()`-shaped
    timestamp (`YYYY-MM-DDTHH:MM:SSZ`), which sorts correctly as a string for
    that one format — the same convention the rest of the package relies on.
    `limit` caps the number of tool rows returned (not the number of calls
    counted): every call in range still contributes to the aggregates, only
    the returned tool list is truncated.
    """
    query = (
        "SELECT tool_name, "
        "COUNT(*) AS calls, "
        "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures, "
        "SUM(duration_ms) AS total_ms, "
        "AVG(duration_ms) AS avg_ms "
        "FROM tool_calls "
    )
    params: list[Any] = []
    if since is not None:
        query += "WHERE called_at >= ? "
        params.append(since)
    query += "GROUP BY tool_name ORDER BY calls DESC, tool_name ASC "
    if limit is not None:
        query += "LIMIT ? "
        params.append(limit)

    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    return {"rows": rows, "since": since}


def register_cli(sub, commands):
    """Register the `usage` CLI verb."""
    p = sub.add_parser(
        "usage",
        help=(
            "Summarize MCP tool-call counts, failures, and timing "
            "(MCP-server calls only — see the printed note)"
        ),
    )
    p.add_argument(
        "--since",
        help="Only count calls at or after this UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)",
    )
    p.add_argument("--limit", type=int, help="Show at most this many tools")
    commands["usage"] = _cmd_usage


def _cmd_usage(args) -> None:
    conn = db.connect()
    try:
        result = usage_summary(conn, since=args.since, limit=args.limit)
    finally:
        conn.close()

    print(SCOPE_NOTE)
    data = [
        {
            "tool": r["tool_name"],
            "calls": str(r["calls"]),
            "failures": str(r["failures"]),
            "total_ms": f"{r['total_ms']:.1f}",
            "avg_ms": f"{r['avg_ms']:.1f}",
        }
        for r in result["rows"]
    ]
    print(format_table(data, ["tool", "calls", "failures", "total_ms", "avg_ms"]))


db.register_cli_provider("usage", register_cli)
