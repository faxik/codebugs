"""The bench module's exposed surface, declared once for both sides.

Every entry names ONE capability and carries the whole of what a client or a
shell sees of it: the MCP tool name, its complete description, and every
parameter with its annotation and default; the CLI verb name, its one-line help,
and every argparse keyword. `codebugs.surfacegen` turns this data into the
registered tool and the built parser — nothing here registers anything, and
nothing in `bench.py` describes anything.

`manual_handler=` names a handwritten BODY, never an installer: the body decides
what the capability DOES, this file decides what it LOOKS LIKE. Three of the four
tools need one, because they dispatch on which argument arrived; `codebench_query`
needs none, so its body is generated from `calls=`.

GRAMMAR. This file is held to a restricted grammar and checked by
`module_surface.py --lint-declarations`: no f-strings, no implicit
concatenation, no escapes in non-raw literals, and every literal reached through
a container, a keyword argument or an assignment. The grammar is what makes the
BT-6 prose column mean anything, and it is a constraint on THIS FILE ONLY — it
is an instrument of the pilot, not a proposal for the repository.

Its measured cost, worth naming because the pilot exists to price things: the
grammar admits data only, so `str | None` (an operator) and `list[str]` (a
subscript) cannot be written here at all. The optional and parameterised
annotations therefore arrive as the named vocabulary imported below.
"""

from codebugs.bench import (
    _cmd_bench_delete,
    _cmd_bench_import,
    _cmd_bench_list,
    _cmd_bench_query,
    _tool_bench_delete,
    _tool_bench_import,
    _tool_bench_list,
    query,
)
from codebugs.surfacegen import (
    OPT_INT,
    OPT_OBJECT,
    OPT_TEXT,
    OPT_TEXT_LIST,
    OPT_TEXT_OR_ARRAY,
)

CODEBENCH_IMPORT_DOC = """Import benchmark results from CSV or JSON.

CSV convention: first column is the row label, remaining columns are
metric names with finite numeric values.

JSON convention: array of objects, first key is the row label, rest
are metric keys with finite numeric values.

Each (row label, metric) pair may appear only once per import, and
NaN/Infinity are refused: a non-finite measurement is not one.

Args:
    benchmark: Benchmark name (e.g. "search-perf")
    csv_data: CSV string (header + data rows). Provide csv_data OR json_data.
    json_data: JSON array string. Provide csv_data OR json_data.
    date: Run date (default: today, ISO format YYYY-MM-DD)
    tags: Optional tags (e.g. ["nightly", "v2.1"])
    meta: Optional metadata (e.g. {"git_sha": "abc123", "ci_url": "..."})
"""

CODEBENCH_QUERY_DOC = """Query and pivot benchmark results.

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

CODEBENCH_LIST_DOC = """List benchmarks or runs.

Without benchmark: lists all benchmark names with run counts.
With benchmark: lists runs for that benchmark.

Args:
    benchmark: If provided, list runs for this benchmark
    last_n: Limit to last N runs (only when benchmark is provided)
"""

CODEBENCH_DELETE_DOC = """Delete a single run or all runs for a benchmark.

Args:
    run_id: Delete a specific run (e.g. "BE-1")
    benchmark: Delete all runs for a benchmark name
"""

SURFACE = [
    dict(
        mcp=dict(
            name="codebench_import",
            doc=CODEBENCH_IMPORT_DOC,
            params=[
                dict(name="benchmark", type=str),
                dict(name="csv_data", type=OPT_TEXT, default=None),
                dict(name="json_data", type=OPT_TEXT_OR_ARRAY, default=None),
                dict(name="date", type=OPT_TEXT, default=None),
                dict(name="tags", type=OPT_TEXT_LIST, default=None),
                dict(name="meta", type=OPT_OBJECT, default=None),
            ],
            manual_handler=_tool_bench_import,
        ),
        cli=dict(
            name="bench-import",
            help="Import benchmark results from CSV/JSON",
            args=[
                dict(flags=["file"], nargs="?", help="CSV or JSON file path"),
                dict(flags=["--json-file"], help="JSON benchmark file (always treated as JSON)"),
                dict(flags=["-b", "--benchmark"], required=True, help="Benchmark name"),
                dict(flags=["--date"], help="Run date (YYYY-MM-DD, default: today)"),
                dict(flags=["--tags"], help="Comma-separated tags"),
                dict(flags=["--meta"], help="JSON metadata string"),
            ],
            manual_handler=_cmd_bench_import,
        ),
    ),
    dict(
        mcp=dict(
            name="codebench_query",
            doc=CODEBENCH_QUERY_DOC,
            params=[
                dict(name="benchmark", type=str),
                dict(name="runs", type=OPT_TEXT_LIST, default=None),
                dict(name="date_from", type=OPT_TEXT, default=None),
                dict(name="date_to", type=OPT_TEXT, default=None),
                dict(name="metrics", type=OPT_TEXT_LIST, default=None),
                dict(name="rows", type=OPT_TEXT_LIST, default=None),
                dict(name="group_by", type=str, default="row"),
                dict(name="last_n", type=OPT_INT, default=None),
                dict(name="format", type=str, default="json"),
            ],
            calls=query,
        ),
        cli=dict(
            name="bench-query",
            help="Query and pivot benchmark results",
            args=[
                dict(flags=["benchmark"], help="Benchmark name"),
                dict(flags=["--runs"], nargs="+", help="Specific run IDs"),
                dict(flags=["--date-from"], help="Start date (YYYY-MM-DD)"),
                dict(flags=["--date-to"], help="End date (YYYY-MM-DD)"),
                dict(flags=["--metrics"], help="Comma-separated metric names"),
                dict(flags=["--rows"], help="Comma-separated row labels"),
                dict(
                    flags=["--group-by"], choices=["row", "run"], default="row", help="Pivot axis"
                ),
                dict(flags=["--last-n"], type=int, help="Last N runs only"),
                dict(
                    flags=["--format"],
                    choices=["json", "csv"],
                    default="json",
                    help="Output format",
                ),
            ],
            manual_handler=_cmd_bench_query,
        ),
    ),
    dict(
        mcp=dict(
            name="codebench_list",
            doc=CODEBENCH_LIST_DOC,
            params=[
                dict(name="benchmark", type=OPT_TEXT, default=None),
                dict(name="last_n", type=OPT_INT, default=None),
            ],
            manual_handler=_tool_bench_list,
        ),
        cli=dict(
            name="bench-list",
            help="List benchmarks or runs",
            args=[
                dict(flags=["benchmark"], nargs="?", help="List runs for this benchmark"),
                dict(flags=["--last-n"], type=int, help="Last N runs"),
            ],
            manual_handler=_cmd_bench_list,
        ),
    ),
    dict(
        mcp=dict(
            name="codebench_delete",
            doc=CODEBENCH_DELETE_DOC,
            params=[
                dict(name="run_id", type=OPT_TEXT, default=None),
                dict(name="benchmark", type=OPT_TEXT, default=None),
            ],
            manual_handler=_tool_bench_delete,
        ),
        cli=dict(
            name="bench-delete",
            help="Delete a run or benchmark",
            args=[
                dict(flags=["--run-id"], help="Delete a specific run (e.g. BE-1)"),
                dict(flags=["--benchmark"], help="Delete all runs for a benchmark"),
            ],
            manual_handler=_cmd_bench_delete,
        ),
    ),
]
