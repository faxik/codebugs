"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer

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
    "provenance": "codeprovenance",
    "reqs": "codereqs",
    "merge": "codemerge",
    "sweep": "codesweep",
    "bench": "codebench",
    "blockers": "codeblockers",
    "milestones": "codemilestones",
    "claims": "codeclaims",
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

    # mcp 2.0 renamed FastMCP -> MCPServer and dropped the constructor's
    # json_response flag; it only ever applied to streamable-http, and we run stdio.
    server = MCPServer(SERVER_NAMES[args.mode])

    for provider in db.get_tool_providers(mode=args.mode):
        provider.register_fn(server, _conn)

    server.run()


if __name__ == "__main__":
    main()
