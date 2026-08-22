#!/usr/bin/env python3
"""BT-6 / Т-36: ONE artefact that is the module's exposed SURFACE.

The pilot's headline is `Δcode`; the surface is a CONSTRAINT, not a metric —
before and after must be BYTE-EQUAL.  Seven review passes established what that
artefact may and may not contain, and this script is that decision in code:

  * MCP side — the serialized `tools/list` for the module's provider, built
    through the REAL server path (`server._NormalizedDescriptions` +
    `server.install_strict_arguments`), dumped whole (name, title, description,
    inputSchema, outputSchema, annotations, meta), sorted, `sort_keys=True`.
    Not the golden's three fields: the golden is blind to `outputSchema` and to
    the FORM of registration, and this pilot changes the form.

  * CLI side — STRUCTURAL introspection of the built parser (`dest`, `nargs`,
    `const`, `choices`, `default`, `type`, `required`, `metavar`, `help`, and
    the action class), because the seventh pass measured that `dest`/`nargs`
    are not printed by `--help` AT ALL: naming a text render and a structural
    introspection in one breath is two artefacts, not one.

  * CLI text — `format_help()` for each verb, captured with `COLUMNS` and
    `LC_ALL` PINNED, because the seventh pass measured `COLUMNS=80` and
    `COLUMNS=120` producing different hashes.  Unpinned, byte-equality is a
    false-refusal generator rather than a constraint.

Run identically on the base checkout and on the pilot tip:

    LC_ALL=C COLUMNS=80 PYTHONPATH=src python3 \\
        .claude/plans/exposure-scripts/bt6_surface_snapshot.py --provider bench

Writes nothing; stdlib + the package only; run from a checkout root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Pinned BEFORE argparse builds anything: HelpFormatter reads the terminal width
# once, at construction.  Setting it later would capture the ambient width.
os.environ.setdefault("COLUMNS", "80")
os.environ["LC_ALL"] = "C"


def _describe(value: object) -> object:
    """A JSON-safe, stable description of an argparse attribute.

    `type=int` must not serialize as a repr carrying a memory address, and
    `choices` may be any sequence.  Everything unknown degrades to `repr`, which
    is stable for the values argparse actually holds.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_describe(v) for v in value]
    name = getattr(value, "__name__", None)
    if name is not None:
        return name
    return repr(value)


def _actions(parser: argparse.ArgumentParser) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public reader
        out.append(
            {
                "class": type(action).__name__,
                "option_strings": list(action.option_strings),
                "dest": action.dest,
                "nargs": _describe(action.nargs),
                "const": _describe(action.const),
                "default": _describe(action.default),
                "type": _describe(action.type),
                "choices": _describe(action.choices),
                "required": bool(action.required),
                "help": action.help,
                "metavar": _describe(action.metavar),
            }
        )
    out.sort(key=lambda a: (str(a["dest"]), str(a["option_strings"])))
    return out


def cli_surface(provider_name: str) -> dict[str, object]:
    from codebugs import db

    parser = argparse.ArgumentParser(prog="codebugs", description="codebugs")
    sub = parser.add_subparsers(dest="command")
    commands: dict = {}
    for provider in db.get_cli_providers(mode=provider_name):
        if provider.name != provider_name:
            continue
        provider.register_fn(sub, commands)

    # The `help=` passed to add_parser lives on the parent's pseudo-actions, not
    # on the child parser, so it is invisible to a child-only walk.
    listed = {}
    for pseudo in sub._choices_actions:  # noqa: SLF001
        listed[pseudo.dest] = pseudo.help

    verbs: dict[str, object] = {}
    for name, child in sorted(sub.choices.items()):
        verbs[name] = {
            "parser_help": listed.get(name),
            "prog": child.prog,
            "description": child.description,
            "actions": _actions(child),
            "handler": getattr(commands.get(name), "__name__", None),
            "format_help": child.format_help(),
        }
    return {"verbs": verbs, "command_names": sorted(commands)}


def mcp_surface(provider_name: str) -> list[dict[str, object]]:
    from mcp.server.mcpserver import MCPServer

    from codebugs import db, server

    async def collect():
        raw = MCPServer("codebugs")
        adapter = server._NormalizedDescriptions(raw)  # noqa: SLF001 — the real path
        for provider in db.get_tool_providers(mode=provider_name):
            if provider.name != provider_name:
                continue
            provider.register_fn(adapter, server._conn)  # noqa: SLF001
        server.install_strict_arguments(raw)
        tools = await raw.list_tools()
        out = []
        for t in tools:
            out.append(json.loads(t.model_dump_json(by_alias=True)))
        out.sort(key=lambda d: str(d.get("name")))
        return out

    return asyncio.run(collect())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", required=True, help="provider/--mode name, e.g. bench")
    ap.add_argument("--part", choices=["all", "mcp", "cli"], default="all")
    args = ap.parse_args()

    payload: dict[str, object] = {"provider": args.provider}
    if args.part in ("all", "mcp"):
        payload["mcp_tools_list"] = mcp_surface(args.provider)
    if args.part in ("all", "cli"):
        payload["cli"] = cli_surface(args.provider)

    json.dump(payload, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
