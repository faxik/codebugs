"""Probe: what a CLIENT receives when tool-argument validation refuses (П-2 / DIR-1).

Not a test — a measurement, run by hand under each admitted `mcp` version:

    uv run --extra dev python tests/manual/probe_p2_argument_refusal.py
    uv run --with 'mcp==2.1.1' --extra dev python tests/manual/probe_p2_argument_refusal.py

It drives a really-built server through a REAL client session, exactly as
`tests/test_cb310_refusal_text.py::call_over_the_wire` does, and prints what the
client ends up holding for four shapes of bad arguments. The question П-2 asks is
whether the text is the project's or a third party's.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata as md
import tempfile

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from codebugs import db, server


def _tracker(root: str):
    db.init_project(root)

    @contextlib.contextmanager
    def _conn():
        conn = db.connect(root)
        try:
            yield conn
        finally:
            conn.close()

    return _conn


def call_over_the_wire(built, name: str, arguments: dict) -> tuple[bool, str]:
    async def go() -> tuple[bool, str]:
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            client_read, client_write = client_streams
            server_read, server_write = server_streams
            options = built._lowlevel_server.create_initialization_options()
            async with anyio.create_task_group() as task_group:

                async def serve() -> None:
                    await built._lowlevel_server.run(server_read, server_write, options)

                task_group.start_soon(serve)
                async with ClientSession(client_read, client_write) as session:
                    await session.initialize()
                    try:
                        result = await session.call_tool(name, arguments)
                    except BaseException as exc:  # noqa: BLE001
                        return "RAISED", f"{type(exc).__name__}: {exc}"
            return bool(result.is_error), (result.content[0].text if result.content else "")

    return asyncio.run(go())


CASES = [
    ("missing a required field", "add", {"category": "x", "file": "y", "description": "z"}),
    ("wrong type for a declared field", "add",
     {"severity": 5, "category": "x", "file": "y", "description": "z"}),
    ("undeclared argument name", "add",
     {"severity": "low", "category": "x", "file": "y", "description": "z", "bogus": 1}),
    ("a domain refusal, for contrast", "update",
     {"finding_id": "CB-1", "status": "bogus"}),
]


def main() -> None:
    print(f"mcp        {md.version('mcp')}")
    try:
        print(f"mcp-types  {md.version('mcp-types')}")
    except Exception:
        pass
    print(f"pydantic   {md.version('pydantic')}")
    print()
    with tempfile.TemporaryDirectory() as root:
        factory = _tracker(root)
        built = server._build_server("findings", factory)
        for label, tool, args in CASES:
            try:
                is_error, text = call_over_the_wire(built, tool, args)
            except BaseException as exc:  # noqa: BLE001
                inner = exc
                while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
                    inner = inner.exceptions[0]
                is_error, text = "RAISED-OUTER", f"{type(inner).__name__}: {inner}"
            print(f"--- {label} ({tool}) ---")
            print(f"is_error={is_error}")
            print(f"text: {text!r}")
            print()


if __name__ == "__main__":
    main()
