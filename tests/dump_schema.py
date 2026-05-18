"""Dump FastMCP tool schemas as a flat sorted list for regression diffing.

Regenerate the golden file with:
    uv run python tests/dump_schema.py > tests/golden/mcp_schema.json
"""

import asyncio
import json
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


async def main():
    all_tools = []
    for provider in db.get_tool_providers(mode="all"):
        server = FastMCP(provider.name, json_response=True)
        provider.register_fn(server, _conn)
        tools = await server.list_tools()
        for t in tools:
            all_tools.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
            )
    all_tools.sort(key=lambda x: x["name"])
    print(json.dumps(all_tools, indent=2, sort_keys=True))


asyncio.run(main())
