"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from codebugs import db


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def install_strict_arguments(server: MCPServer) -> None:
    """Refuse a `tools/call` that carries argument names the tool does not declare.

    WHY THIS EXISTS (CB-15). The SDK builds each tool's argument model with
    pydantic's default ``extra="ignore"``, so an unknown argument NAME is dropped
    during validation and the tool runs without it — returning a success payload
    with the caller's data discarded. An unknown VALUE, by contrast, raises. That
    asymmetry turns a singular/plural typo (`note=` for `notes=`) into invisible
    data loss, which is the one failure a tracker cannot afford. Setting
    ``additionalProperties: false`` does not help: the server never validates
    arguments against the JSON Schema.

    THE SDK COUPLING LIVES HERE AND NOWHERE ELSE. `MCPServer.middleware` is public
    but its signature is documented as provisional, so if a future SDK release
    breaks this, this one function is the only thing to repair.
    """
    declared: dict[str, set[str]] = {}

    async def reject_unknown_arguments(ctx: Any, call_next: Any) -> Any:
        if ctx.method == "tools/call" and isinstance(ctx.params, Mapping):
            name = ctx.params.get("name")
            arguments = ctx.params.get("arguments")
            if isinstance(name, str) and isinstance(arguments, Mapping):
                if not declared:
                    for tool in await server.list_tools():
                        declared[tool.name] = set(tool.input_schema.get("properties", {}))
                # An unknown tool name is not ours to answer — let the SDK's own
                # "Unknown tool" error stay authoritative.
                known = declared.get(name)
                if known is not None:
                    unknown = sorted(set(arguments) - known)
                    if unknown:
                        raise MCPError(
                            code=INVALID_PARAMS,
                            message=(
                                f"Unknown argument(s) for tool {name!r}: {', '.join(unknown)}. "
                                f"Accepted: {', '.join(sorted(known))}. "
                                "Refused rather than ignored — a dropped argument would "
                                "otherwise look like a successful write."
                            ),
                        )
        return await call_next(ctx)

    server.middleware.append(reject_unknown_arguments)


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

    # After registration, so the middleware sees the full tool catalogue.
    install_strict_arguments(server)

    server.run()


if __name__ == "__main__":
    main()
