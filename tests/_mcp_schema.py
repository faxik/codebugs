"""One implementation of "what does the MCP tool surface look like right now".

Both the golden generator (`tests/dump_schema.py`) and the gate that compares
against it (`tests/test_boundary.py::TestMcpWireSchema`) import from here. They
used to carry separate copies of this logic, which is one drift away from the
two disagreeing about the very thing they exist to keep in agreement.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from codebugs import db
# ONE definition, and it lives in src now (CB-73): the server emits normalized
# descriptions, so a second copy here would be one drift away from the gate and
# the server disagreeing about the thing they exist to keep in agreement.
# `normalize_description` is the whole composition (dedent + CB-156's Markdown
# sections); call it rather than its steps, so this side cannot normalize a
# different amount than the server does.
from codebugs.server import normalize_description

# Re-exported, not used here: `tests/test_boundary.py` asserts the golden is
# dedent-stable with it, and `tests/test_server.py` pins that this module's name
# IS the server's object — the CB-73 anti-drift check. Importing it here is what
# makes that pin mean anything.
from codebugs.server import dedent_docstring  # noqa: F401


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def collect_tool_schemas(providers=None) -> list[dict[str, Any]]:
    """Every registered MCP tool as {name, description, inputSchema}, sorted by name.

    `providers` defaults to the whole registry. It is injectable so a test can
    register a synthetic tool and observe what this function does to it — with a
    global registry there would be no way to see the normalization applied.
    """
    from mcp.server.mcpserver import MCPServer

    if providers is None:
        providers = db.get_tool_providers(mode="all")

    async def collect():
        all_tools = []
        for provider in providers:
            # One server PER PROVIDER, deliberately. The real server (server.py)
            # builds a single shared one; switching to that topology here would
            # change the golden, and it is the only thing that would surface a
            # tool-name collision across providers — which this gate therefore
            # cannot catch. Names are all distinct today, so the two agree.
            server = MCPServer(provider.name)
            provider.register_fn(server, _conn)
            for t in await server.list_tools():
                all_tools.append(
                    {
                        "name": t.name,
                        "description": normalize_description(t.description or ""),
                        # mcp 2.0 renamed the attribute to input_schema; the wire
                        # field is still inputSchema, so the golden keeps that name.
                        "inputSchema": t.input_schema,
                    }
                )
        all_tools.sort(key=lambda x: x["name"])
        return all_tools

    return asyncio.run(collect())
