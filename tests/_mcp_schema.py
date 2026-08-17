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


def dedent_docstring(doc: str) -> str:
    """Strip the common source indentation from a docstring, as CPython 3.13 does.

    CPython 3.13 dedents docstrings at compile time; 3.11 and 3.12 leave the
    source indentation in `__doc__`, and the mcp SDK passes `__doc__` through
    untouched. `requires-python` admits all three, so without this the tool
    descriptions — and therefore the golden — differ purely by interpreter
    (CB-70).

    This deliberately reproduces the compiler's rule and nothing more: take the
    minimum indentation over the non-blank lines AFTER the first, remove exactly
    that prefix from those lines, and leave the first line alone (it begins
    immediately after the opening quotes, so it carries no indentation to strip).
    `inspect.cleandoc` is the tempting shortcut and is wrong here: it also drops
    boundary blank lines and expands tabs, which would both rewrite 61 of the 68
    golden descriptions and blind the gate to whitespace changes clients can see.
    """
    lines = doc.split("\n")
    indent = None
    for line in lines[1:]:
        stripped = line.lstrip(" \t")
        if stripped:
            margin = len(line) - len(stripped)
            indent = margin if indent is None else min(indent, margin)
    if not indent:
        return doc
    return "\n".join([lines[0]] + [line[indent:] for line in lines[1:]])


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
                        "description": dedent_docstring(t.description or ""),
                        # mcp 2.0 renamed the attribute to input_schema; the wire
                        # field is still inputSchema, so the golden keeps that name.
                        "inputSchema": t.input_schema,
                    }
                )
        all_tools.sort(key=lambda x: x["name"])
        return all_tools

    return asyncio.run(collect())
