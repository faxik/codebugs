"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
import sys
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


def _preflight() -> None:
    """Say once, on stderr, when this server's tracker binding is broken or unusual.

    WHY THIS EXISTS (CB-11). `_conn` connects lazily per tool call, so a server
    started where no tracker is reachable looks perfectly healthy at startup and
    then fails every call forever — once per invocation, with no single moment
    that names the problem. stderr is the right channel because MCP clients log
    it, while tool responses are the one place the diagnostic cannot reach.

    WARN-ONLY, NEVER FATAL. Exiting here would break lazy-connect self-healing: a
    server whose project directory appears after startup must still work. So this
    reports and returns, always.

    Silent on the ordinary discovered path — one line per project per startup is
    noise. A DECLARED root is announced, because a non-default binding is exactly
    what someone reading the log later needs to see.
    """
    info = db.describe_root()
    if info["error"]:
        print(f"codebugs-mcp: {info['error']}", file=sys.stderr)
        print(
            "codebugs-mcp: serving anyway — tool calls will fail until a tracker is "
            "reachable; `codebugs where` shows the current binding",
            file=sys.stderr,
        )
        return
    # The two checks below are mutually exclusive, which is not visible from here:
    # a resolved-but-absent database can only come from the walk, since the named
    # and declared routes refuse it — so `source` is always "discovery" when
    # `exists` is False. Written as two `if`s rather than an `elif` chain because
    # they answer different questions, not because both can fire.
    if not info["exists"]:
        # Resolving is not the same as being there (CB-23). This binding does not
        # fail — the first tool call CREATES the tracker — so it is invisible in
        # exactly the way CB-11 exists to prevent, and is worth a line even though
        # nothing is broken yet.
        print(
            f"codebugs-mcp: {info['path']} does not exist yet — the first write will "
            f"create a new, empty tracker there",
            file=sys.stderr,
        )
    if info["source"] != "discovery":
        print(
            f"codebugs-mcp: tracker root {info['root']} (from {info['source_label']})",
            file=sys.stderr,
        )


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
    "similarity": "codesimilarity",
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
    parser.add_argument(
        "--tracker-root",
        default=None,
        metavar="DIR",
        help=(
            f"Serve the tracker in DIR instead of deriving it from the working "
            f"directory (overrides ${db.ENV_ROOT})"
        ),
    )
    args = parser.parse_args()
    db.set_tracker_root(args.tracker_root)
    _preflight()

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
