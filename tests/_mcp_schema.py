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

# THE PRODUCTION REGISTRAR, not a reconstruction of it (CB-164). This module used
# to build a BARE `MCPServer`, register providers on it, and then call
# `normalize_description` on the result by hand. The two happened to agree, so
# the golden looked like a gate on what clients receive — and it was not one: the
# snapshot went past the adapter and redid its work, so a defect IN the adapter
# could never move the file. Measured by the T-63 manager on CB-156: with the
# adapter reverted to dedent-only, "golden stayed GREEN". Worse, the shape was
# already written down as a HAZARD in `surfacegen.py`'s docstring ("a generated
# tool passing `description=` would therefore match the golden byte for byte and
# still ship un-dedented text to clients — CB-73 resurrected behind the very gate
# built to catch it") and guarded by nothing but a convention. Prose cannot
# enforce prose. Registering through the same adapter the server uses makes the
# snapshot a record of what actually goes on the wire, so a tool that passes its
# own `description=` lands in the golden UNNORMALIZED and CB-156's gate in
# `tests/test_boundary.py` names it.
from codebugs.server import _NormalizedDescriptions

# Re-exported, not called here — and BOTH of them are load-bearing as names.
# `tests/test_boundary.py` asserts the golden is dedent-stable with
# `dedent_docstring`, and `tests/test_server.py` pins that THIS module's two
# attributes ARE the server's objects (the CB-73 anti-drift check: the gate and
# the server must not be able to disagree about what "normalized" means).
# `normalize_description` stopped having a call site here when the adapter took
# the job over; deleting the import would silently disarm that pin.
from codebugs.server import dedent_docstring, normalize_description  # noqa: F401


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
            # Through the adapter, exactly as `server.main` does — never onto the
            # bare server with a normalization pass bolted on afterwards. See the
            # import comment: doing it by hand is what made this snapshot a
            # reconstruction of the wire instead of a record of it (CB-164).
            provider.register_fn(_NormalizedDescriptions(server), _conn)
            for t in await server.list_tools():
                all_tools.append(
                    {
                        "name": t.name,
                        # Verbatim. Whatever normalization this description did or
                        # did not receive is now a FACT about the registration
                        # path, which is the only thing worth snapshotting.
                        "description": t.description or "",
                        # mcp 2.0 renamed the attribute to input_schema; the wire
                        # field is still inputSchema, so the golden keeps that name.
                        "inputSchema": t.input_schema,
                    }
                )
        all_tools.sort(key=lambda x: x["name"])
        return all_tools

    return asyncio.run(collect())
