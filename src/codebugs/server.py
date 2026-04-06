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


SERVER_NAMES = {
    "findings": "codebugs",
    "reqs": "codereqs",
    "merge": "codemerge",
    "sweep": "codesweep",
    "bench": "codebench",
    "blockers": "codeblockers",
    "all": "codebugs",
}


def main():
    """Run the MCP server with optional mode selection."""
    parser = argparse.ArgumentParser(description="Codebugs MCP server")
    parser.add_argument(
        "--mode",
        choices=list(SERVER_NAMES),
        default="all",
        help="Which tools to expose (default: all)",
    )
    args = parser.parse_args()

    server = FastMCP(SERVER_NAMES[args.mode], json_response=True)

    db._ensure_modules_loaded()
    for provider in db._tool_providers:
        if args.mode == "all" or provider.name == args.mode:
            provider.register_fn(server, _conn)

    server.run()


if __name__ == "__main__":
    main()
