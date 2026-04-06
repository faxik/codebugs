"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

from codebugs import db


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def main():
    """Run the MCP server with optional mode selection."""
    parser = argparse.ArgumentParser(description="Codebugs MCP server")
    parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "bench", "blockers", "all"],
        default="all",
        help="Which tools to expose: findings, reqs, merge, sweep, bench, blockers, or all (default: all)",
    )
    args = parser.parse_args()

    name = {"findings": "codebugs", "reqs": "codereqs", "merge": "codemerge", "sweep": "codesweep", "bench": "codebench", "blockers": "codeblockers", "all": "codebugs"}[args.mode]
    server = FastMCP(name, json_response=True)

    if args.mode in ("findings", "all"):
        from codebugs.db import register_tools as findings_tools
        findings_tools(server, _conn)
    if args.mode in ("reqs", "all"):
        from codebugs.reqs import register_tools as reqs_tools
        reqs_tools(server, _conn)
    if args.mode in ("merge", "all"):
        from codebugs.merge import register_tools as merge_tools
        merge_tools(server, _conn)
    if args.mode in ("sweep", "all"):
        from codebugs.sweep import register_tools as sweep_tools
        sweep_tools(server, _conn)
    if args.mode in ("bench", "all"):
        from codebugs.bench import register_tools as bench_tools
        bench_tools(server, _conn)
    if args.mode in ("blockers", "all"):
        from codebugs.blockers import register_tools as blockers_tools
        blockers_tools(server, _conn)

    server.run()


if __name__ == "__main__":
    main()
