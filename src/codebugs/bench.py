"""Database layer — codebench benchmark result storage for codebugs.

Stores benchmark results in EAV (entity-attribute-value) form:
- codebench_runs: one row per import (name, date, optional metadata)
- codebench_results: one row per (run, row_label, metric, value)

Convention: CSV first column = row_label, remaining columns = metric names.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any


SCHEMA = """\
CREATE TABLE IF NOT EXISTS codebench_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    benchmark TEXT NOT NULL,
    date TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_codebench_runs_benchmark
    ON codebench_runs(benchmark);
CREATE INDEX IF NOT EXISTS idx_codebench_runs_date
    ON codebench_runs(date);

CREATE TABLE IF NOT EXISTS codebench_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES codebench_runs(run_id),
    row_label TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    UNIQUE(run_id, row_label, metric)
);

CREATE INDEX IF NOT EXISTS idx_codebench_results_run
    ON codebench_results(run_id);
CREATE INDEX IF NOT EXISTS idx_codebench_results_metric
    ON codebench_results(metric);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_run_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT run_id FROM codebench_runs WHERE run_id LIKE 'BE-%' "
        "ORDER BY CAST(SUBSTR(run_id, 4) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if row:
        match = re.search(r"BE-(\d+)", row["run_id"])
        n = int(match.group(1)) + 1 if match else 1
    else:
        n = 1
    return f"BE-{n}"


def import_csv(
    conn: sqlite3.Connection,
    *,
    benchmark: str,
    csv_data: str,
    date: str | None = None,
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Import benchmark results from CSV string.

    Convention: first column is the row label, remaining columns are metrics.
    All metric columns must contain numeric values.

    Args:
        benchmark: Benchmark name (e.g. "search-perf")
        csv_data: CSV content as string (header + data rows)
        date: Run date (default: now, ISO format)
        tags: Optional tags
        meta: Optional metadata (git_sha, ci_url, etc.)
        run_id: Optional explicit run ID (default: auto-generated)
    """
    reader = csv.DictReader(io.StringIO(csv_data))
    if not reader.fieldnames or len(reader.fieldnames) < 2:
        raise ValueError("CSV must have at least 2 columns (row_label + one metric)")

    label_col = reader.fieldnames[0]
    metric_cols = reader.fieldnames[1:]

    rows = list(reader)
    if not rows:
        raise ValueError("CSV contains no data rows")

    rid = run_id or _next_run_id(conn)
    run_date = date or _now()[:10]
    now = _now()

    conn.execute(
        "INSERT INTO codebench_runs (run_id, benchmark, date, tags, meta, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rid, benchmark, run_date, json.dumps(tags or []), json.dumps(meta or {}), now),
    )

    result_count = 0
    for row in rows:
        row_label = row[label_col]
        if not row_label:
            raise ValueError("Row label (first column) must not be empty")
        for metric in metric_cols:
            raw = row[metric]
            if raw is None or raw.strip() == "":
                continue
            try:
                value = float(raw)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Non-numeric value '{raw}' in column '{metric}', row '{row_label}'"
                )
            conn.execute(
                "INSERT INTO codebench_results (run_id, row_label, metric, value) "
                "VALUES (?, ?, ?, ?)",
                (rid, row_label, metric, value),
            )
            result_count += 1

    conn.commit()
    return {
        "run_id": rid,
        "benchmark": benchmark,
        "date": run_date,
        "metrics": metric_cols,
        "rows": len(rows),
        "results_stored": result_count,
    }


def import_json(
    conn: sqlite3.Connection,
    *,
    benchmark: str,
    json_data: str | list,
    date: str | None = None,
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Import benchmark results from JSON string or list.

    Expected format: list of objects, each with a row_label key (first key)
    and metric keys with numeric values.

    Args:
        benchmark: Benchmark name
        json_data: JSON array string or pre-parsed list of dicts
        date: Run date (default: now)
        tags: Optional tags
        meta: Optional metadata
        run_id: Optional explicit run ID
    """
    if isinstance(json_data, list):
        if not json_data:
            raise ValueError("JSON must be a non-empty array of objects")
        data = json_data
    else:
        data = json.loads(json_data)
        if not isinstance(data, list) or not data:
            raise ValueError("JSON must be a non-empty array of objects")

    # Convert JSON to CSV and delegate
    keys = list(data[0].keys())
    if len(keys) < 2:
        raise ValueError("Each object must have at least 2 keys (row_label + one metric)")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys)
    writer.writeheader()
    writer.writerows(data)

    return import_csv(
        conn,
        benchmark=benchmark,
        csv_data=buf.getvalue(),
        date=date,
        tags=tags,
        meta=meta,
        run_id=run_id,
    )


def query(
    conn: sqlite3.Connection,
    *,
    benchmark: str,
    runs: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    metrics: list[str] | None = None,
    rows: list[str] | None = None,
    group_by: str = "row",
    last_n: int | None = None,
    format: str = "json",
) -> dict[str, Any]:
    """Query benchmark results with filtering and pivot.

    Args:
        benchmark: Benchmark name to query
        runs: Specific run IDs (default: all)
        date_from: Start date filter (inclusive)
        date_to: End date filter (inclusive)
        metrics: Which metrics to include (default: all)
        rows: Which row_labels to include (default: all)
        group_by: Pivot axis — "row" (original table shape) or "run" (trend view)
        last_n: Limit to last N runs (by date)
        format: Output format — "json" or "csv"
    """
    if group_by not in ("row", "run"):
        raise ValueError("group_by must be 'row' or 'run'")
    if format not in ("json", "csv"):
        raise ValueError("format must be 'json' or 'csv'")

    # Find matching runs
    run_conditions = ["r.benchmark = ?"]
    run_params: list[Any] = [benchmark]

    if runs:
        placeholders = ",".join("?" for _ in runs)
        run_conditions.append(f"r.run_id IN ({placeholders})")
        run_params.extend(runs)
    if date_from:
        run_conditions.append("r.date >= ?")
        run_params.append(date_from)
    if date_to:
        run_conditions.append("r.date <= ?")
        run_params.append(date_to)

    run_where = " AND ".join(run_conditions)
    order = "ORDER BY r.date DESC"
    limit_clause = f"LIMIT {last_n}" if last_n else ""

    matched_runs = conn.execute(
        f"SELECT run_id, date FROM codebench_runs r WHERE {run_where} {order} {limit_clause}",
        run_params,
    ).fetchall()

    if not matched_runs:
        return {"benchmark": benchmark, "runs_matched": 0, "data": [], "format": format}

    run_ids = [r["run_id"] for r in matched_runs]
    run_dates = {r["run_id"]: r["date"] for r in matched_runs}

    # Fetch results
    res_conditions = [f"res.run_id IN ({','.join('?' for _ in run_ids)})"]
    res_params: list[Any] = list(run_ids)

    if metrics:
        placeholders = ",".join("?" for _ in metrics)
        res_conditions.append(f"res.metric IN ({placeholders})")
        res_params.extend(metrics)
    if rows:
        placeholders = ",".join("?" for _ in rows)
        res_conditions.append(f"res.row_label IN ({placeholders})")
        res_params.extend(rows)

    res_where = " AND ".join(res_conditions)
    result_rows = conn.execute(
        f"SELECT res.run_id, res.row_label, res.metric, res.value "
        f"FROM codebench_results res WHERE {res_where} "
        f"ORDER BY res.run_id, res.row_label, res.metric",
        res_params,
    ).fetchall()

    # Pivot
    if group_by == "row":
        data = _pivot_by_row(result_rows, run_ids, run_dates)
    else:
        data = _pivot_by_run(result_rows, run_ids, run_dates)

    result: dict[str, Any] = {
        "benchmark": benchmark,
        "runs_matched": len(run_ids),
        "run_ids": run_ids,
        "group_by": group_by,
        "format": format,
    }

    if format == "csv":
        result["csv"] = _to_csv(data)
    else:
        result["data"] = data

    return result


def _pivot_by_row(
    result_rows: list[sqlite3.Row],
    run_ids: list[str],
    run_dates: dict[str, str],
) -> list[dict[str, Any]]:
    """Pivot: rows = row_labels, columns = metrics. One table per run."""
    tables: list[dict[str, Any]] = []
    # Group by run
    by_run: dict[str, dict[str, dict[str, float]]] = {}
    for r in result_rows:
        run_id = r["run_id"]
        by_run.setdefault(run_id, {}).setdefault(r["row_label"], {})[r["metric"]] = r["value"]

    for run_id in run_ids:
        if run_id not in by_run:
            continue
        run_data = by_run[run_id]
        table_rows = []
        for label, metrics in run_data.items():
            row = {"row_label": label, **metrics}
            table_rows.append(row)
        tables.append({
            "run_id": run_id,
            "date": run_dates[run_id],
            "rows": table_rows,
        })
    return tables


def _pivot_by_run(
    result_rows: list[sqlite3.Row],
    run_ids: list[str],
    run_dates: dict[str, str],
) -> list[dict[str, Any]]:
    """Pivot: rows = runs, columns = metric values. One table per row_label."""
    # Group by row_label
    by_label: dict[str, dict[str, dict[str, float]]] = {}
    for r in result_rows:
        by_label.setdefault(r["row_label"], {}).setdefault(r["run_id"], {})[r["metric"]] = r["value"]

    tables: list[dict[str, Any]] = []
    for label, runs_data in by_label.items():
        table_rows = []
        for run_id in run_ids:
            if run_id not in runs_data:
                continue
            row = {"run_id": run_id, "date": run_dates[run_id], **runs_data[run_id]}
            table_rows.append(row)
        tables.append({
            "row_label": label,
            "rows": table_rows,
        })
    return tables


def _to_csv(data: list[dict[str, Any]]) -> str:
    """Convert pivoted data to CSV string."""
    if not data:
        return ""

    buf = io.StringIO()
    # Use first table's first row to determine columns
    first_rows = data[0].get("rows", [])
    if not first_rows:
        return ""

    fieldnames = list(first_rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)

    for table in data:
        # Write a header comment identifying the table
        table_label = table.get("run_id") or table.get("row_label", "")
        table_date = table.get("date", "")
        if table_label:
            buf.write(f"# {table_label}")
            if table_date:
                buf.write(f" ({table_date})")
            buf.write("\n")
        writer.writeheader()
        writer.writerows(table.get("rows", []))
        buf.write("\n")

    return buf.getvalue().strip()


def list_benchmarks(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """List all benchmark names with run counts and date ranges."""
    rows = conn.execute(
        "SELECT benchmark, COUNT(*) as run_count, "
        "MIN(date) as first_date, MAX(date) as last_date "
        "FROM codebench_runs GROUP BY benchmark ORDER BY benchmark"
    ).fetchall()
    return {
        "benchmarks": [
            {
                "benchmark": r["benchmark"],
                "run_count": r["run_count"],
                "first_date": r["first_date"],
                "last_date": r["last_date"],
            }
            for r in rows
        ],
    }


def list_runs(
    conn: sqlite3.Connection,
    *,
    benchmark: str | None = None,
    last_n: int | None = None,
) -> dict[str, Any]:
    """List runs, optionally filtered by benchmark name."""
    conditions: list[str] = []
    params: list[Any] = []

    if benchmark:
        conditions.append("r.benchmark = ?")
        params.append(benchmark)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_clause = f"LIMIT {last_n}" if last_n else ""

    rows = conn.execute(
        f"SELECT r.run_id, r.benchmark, r.date, r.tags, r.meta, r.created_at, "
        f"COUNT(res.id) as result_count "
        f"FROM codebench_runs r "
        f"LEFT JOIN codebench_results res ON r.run_id = res.run_id "
        f"{where} GROUP BY r.run_id ORDER BY r.date DESC {limit_clause}",
        params,
    ).fetchall()

    return {
        "runs": [
            {
                "run_id": r["run_id"],
                "benchmark": r["benchmark"],
                "date": r["date"],
                "tags": json.loads(r["tags"]),
                "meta": json.loads(r["meta"]),
                "result_count": r["result_count"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


def delete_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> dict[str, Any]:
    """Delete a run and all its results."""
    row = conn.execute(
        "SELECT run_id, benchmark FROM codebench_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Run not found: {run_id}")

    result_count = conn.execute(
        "SELECT COUNT(*) as c FROM codebench_results WHERE run_id = ?", (run_id,)
    ).fetchone()["c"]

    conn.execute("DELETE FROM codebench_results WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM codebench_runs WHERE run_id = ?", (run_id,))
    conn.commit()

    return {
        "deleted": run_id,
        "benchmark": row["benchmark"],
        "results_removed": result_count,
    }


def delete_benchmark(
    conn: sqlite3.Connection,
    benchmark: str,
) -> dict[str, Any]:
    """Delete all runs for a benchmark."""
    run_ids = [
        r["run_id"]
        for r in conn.execute(
            "SELECT run_id FROM codebench_runs WHERE benchmark = ?", (benchmark,)
        ).fetchall()
    ]
    if not run_ids:
        raise KeyError(f"Benchmark not found: {benchmark}")

    placeholders = ",".join("?" for _ in run_ids)
    result_count = conn.execute(
        f"SELECT COUNT(*) as c FROM codebench_results WHERE run_id IN ({placeholders})",
        run_ids,
    ).fetchone()["c"]

    conn.execute(
        f"DELETE FROM codebench_results WHERE run_id IN ({placeholders})", run_ids
    )
    conn.execute("DELETE FROM codebench_runs WHERE benchmark = ?", (benchmark,))
    conn.commit()

    return {
        "deleted_benchmark": benchmark,
        "runs_removed": len(run_ids),
        "results_removed": result_count,
    }


from codebugs.db import register_schema, register_tool_provider, register_cli_provider  # noqa: E402

register_schema("bench", ensure_schema)


def register_tools(mcp, conn_factory) -> None:
    """Register benchmark result tools on the given MCP server."""

    @mcp.tool()
    def codebench_import(
        benchmark: str,
        csv_data: str | None = None,
        json_data: str | list | None = None,
        date: str | None = None,
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Import benchmark results from CSV or JSON.

        CSV convention: first column is the row label, remaining columns are
        metric names with numeric values.

        JSON convention: array of objects, first key is the row label, rest
        are metric keys with numeric values.

        Args:
            benchmark: Benchmark name (e.g. "search-perf")
            csv_data: CSV string (header + data rows). Provide csv_data OR json_data.
            json_data: JSON array string. Provide csv_data OR json_data.
            date: Run date (default: today, ISO format YYYY-MM-DD)
            tags: Optional tags (e.g. ["nightly", "v2.1"])
            meta: Optional metadata (e.g. {"git_sha": "abc123", "ci_url": "..."})
        """
        if csv_data is None and json_data is None:
            raise ValueError("Provide either csv_data or json_data")
        if csv_data is not None and json_data is not None:
            raise ValueError("Provide csv_data or json_data, not both")
        with conn_factory() as conn:
            if csv_data:
                return import_csv(
                    conn, benchmark=benchmark, csv_data=csv_data,
                    date=date, tags=tags, meta=meta,
                )
            return import_json(
                conn, benchmark=benchmark, json_data=json_data,
                date=date, tags=tags, meta=meta,
            )

    @mcp.tool()
    def codebench_query(
        benchmark: str,
        runs: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        metrics: list[str] | None = None,
        rows: list[str] | None = None,
        group_by: str = "row",
        last_n: int | None = None,
        format: str = "json",
    ) -> dict[str, Any]:
        """Query and pivot benchmark results.

        group_by="row": original table shape (row_labels as rows, metrics as
        columns). Returns one table per run.

        group_by="run": trend view (runs as rows, metrics as columns).
        Returns one table per row_label.

        Args:
            benchmark: Benchmark name to query
            runs: Specific run IDs (default: all matching)
            date_from: Start date filter (inclusive, YYYY-MM-DD)
            date_to: End date filter (inclusive, YYYY-MM-DD)
            metrics: Which metrics to include (default: all)
            rows: Which row_labels to include (default: all)
            group_by: Pivot axis — "row" or "run"
            last_n: Limit to last N runs by date
            format: Output — "json" or "csv"
        """
        with conn_factory() as conn:
            return query(
                conn, benchmark=benchmark, runs=runs,
                date_from=date_from, date_to=date_to,
                metrics=metrics, rows=rows, group_by=group_by,
                last_n=last_n, format=format,
            )

    @mcp.tool()
    def codebench_list(
        benchmark: str | None = None,
        last_n: int | None = None,
    ) -> dict[str, Any]:
        """List benchmarks or runs.

        Without benchmark: lists all benchmark names with run counts.
        With benchmark: lists runs for that benchmark.

        Args:
            benchmark: If provided, list runs for this benchmark
            last_n: Limit to last N runs (only when benchmark is provided)
        """
        with conn_factory() as conn:
            if benchmark:
                return list_runs(conn, benchmark=benchmark, last_n=last_n)
            return list_benchmarks(conn)

    @mcp.tool()
    def codebench_delete(
        run_id: str | None = None,
        benchmark: str | None = None,
    ) -> dict[str, Any]:
        """Delete a single run or all runs for a benchmark.

        Args:
            run_id: Delete a specific run (e.g. "BE-1")
            benchmark: Delete all runs for a benchmark name
        """
        if not run_id and not benchmark:
            raise ValueError("Provide run_id or benchmark")
        if run_id and benchmark:
            raise ValueError("Provide run_id or benchmark, not both")
        with conn_factory() as conn:
            if run_id:
                return delete_run(conn, run_id)
            return delete_benchmark(conn, benchmark)


register_tool_provider("bench", register_tools)


# --- CLI ---

def register_cli(sub, commands) -> None:
    """Register bench CLI subcommands."""
    import argparse
    import sys
    from codebugs import db
    from codebugs.fmt import format_table

    def _cmd_bench_import(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            if not args.file and not args.json_file:
                print("Provide either a file path or --json-file", file=sys.stderr)
                sys.exit(1)

            kwargs: dict[str, Any] = {
                "benchmark": args.benchmark,
                "date": args.date,
            }
            if args.tags:
                kwargs["tags"] = [t.strip() for t in args.tags.split(",")]
            if args.meta:
                kwargs["meta"] = json.loads(args.meta)

            path = args.json_file or args.file
            is_json = bool(args.json_file) or path.endswith(".json")
            with open(path) as f:
                data = f.read()
            if is_json:
                result = import_json(conn, json_data=data, **kwargs)
            else:
                result = import_csv(conn, csv_data=data, **kwargs)

            print(f"Imported: {result['run_id']} ({result['rows']} rows, {result['results_stored']} values)")
        except (ValueError, json.JSONDecodeError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_bench_query(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            kwargs: dict[str, Any] = {
                "benchmark": args.benchmark,
                "group_by": args.group_by or "row",
                "format": args.format or "json",
            }
            if args.runs:
                kwargs["runs"] = args.runs
            if args.date_from:
                kwargs["date_from"] = args.date_from
            if args.date_to:
                kwargs["date_to"] = args.date_to
            if args.metrics:
                kwargs["metrics"] = [m.strip() for m in args.metrics.split(",")]
            if args.rows:
                kwargs["rows"] = [r.strip() for r in args.rows.split(",")]
            if args.last_n:
                kwargs["last_n"] = args.last_n

            result = query(conn, **kwargs)

            if result["runs_matched"] == 0:
                print("(no matching runs)")
                return

            if result["format"] == "csv":
                print(result["csv"])
            else:
                print(json.dumps(result["data"], indent=2))
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_bench_list(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            if args.benchmark:
                result = list_runs(conn, benchmark=args.benchmark, last_n=args.last_n)
                if not result["runs"]:
                    print("(no runs)")
                    return
                data = [
                    {
                        "run_id": r["run_id"],
                        "date": r["date"],
                        "results": str(r["result_count"]),
                        "tags": ",".join(r["tags"]),
                    }
                    for r in result["runs"]
                ]
                print(format_table(data, ["run_id", "date", "results", "tags"]))
            else:
                result = list_benchmarks(conn)
                if not result["benchmarks"]:
                    print("(no benchmarks)")
                    return
                data = [
                    {
                        "benchmark": b["benchmark"],
                        "runs": str(b["run_count"]),
                        "first": b["first_date"],
                        "last": b["last_date"],
                    }
                    for b in result["benchmarks"]
                ]
                print(format_table(data, ["benchmark", "runs", "first", "last"]))
        finally:
            conn.close()

    def _cmd_bench_delete(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            if args.run_id:
                result = delete_run(conn, args.run_id)
                print(f"Deleted run {result['deleted']} ({result['results_removed']} results)")
            elif args.benchmark:
                result = delete_benchmark(conn, args.benchmark)
                print(f"Deleted benchmark {result['deleted_benchmark']} ({result['runs_removed']} runs, {result['results_removed']} results)")
            else:
                print("Provide --run-id or --benchmark", file=sys.stderr)
                sys.exit(1)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    # Argparse setup
    p = sub.add_parser("bench-import", help="Import benchmark results from CSV/JSON")
    p.add_argument("file", nargs="?", help="CSV or JSON file path")
    p.add_argument("--json-file", help="JSON benchmark file (always treated as JSON)")
    p.add_argument("-b", "--benchmark", required=True, help="Benchmark name")
    p.add_argument("--date", help="Run date (YYYY-MM-DD, default: today)")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--meta", help="JSON metadata string")

    p = sub.add_parser("bench-query", help="Query and pivot benchmark results")
    p.add_argument("benchmark", help="Benchmark name")
    p.add_argument("--runs", nargs="+", help="Specific run IDs")
    p.add_argument("--date-from", help="Start date (YYYY-MM-DD)")
    p.add_argument("--date-to", help="End date (YYYY-MM-DD)")
    p.add_argument("--metrics", help="Comma-separated metric names")
    p.add_argument("--rows", help="Comma-separated row labels")
    p.add_argument("--group-by", choices=["row", "run"], default="row", help="Pivot axis")
    p.add_argument("--last-n", type=int, help="Last N runs only")
    p.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")

    p = sub.add_parser("bench-list", help="List benchmarks or runs")
    p.add_argument("benchmark", nargs="?", help="List runs for this benchmark")
    p.add_argument("--last-n", type=int, help="Last N runs")

    p = sub.add_parser("bench-delete", help="Delete a run or benchmark")
    p.add_argument("--run-id", help="Delete a specific run (e.g. BE-1)")
    p.add_argument("--benchmark", help="Delete all runs for a benchmark")

    commands.update({
        "bench-import": _cmd_bench_import,
        "bench-query": _cmd_bench_query,
        "bench-list": _cmd_bench_list,
        "bench-delete": _cmd_bench_delete,
    })


register_cli_provider("bench", register_cli)
