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

  * CLI text — `format_help()` for each verb AND for the parent parser, plus the
    UNSORTED registration order, captured with `COLUMNS` and `LC_ALL` pinned by
    this script itself, because the seventh pass measured `COLUMNS=80` and
    `COLUMNS=120` producing different hashes.  Unpinned, byte-equality is a
    false-refusal generator rather than a constraint; and without the parent's
    help and the unsorted order, swapping two declarations changes what a user
    sees at `codebugs --help` while leaving this document identical.

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

# Pinned BEFORE argparse formats anything, and pinned UNCONDITIONALLY.  It was
# `setdefault` first, which is not a pin: an ambient `COLUMNS=120` survived it,
# and `COLUMNS=80` versus `COLUMNS=120` really do produce different hashes
# (measured).  A snapshot whose determinism depends on the caller's environment
# is exactly the false-refusal generator this artefact exists to avoid, so the
# script pins the value itself and the `COLUMNS=80` in the documented invocation
# is now belt-and-braces rather than the mechanism.
os.environ["COLUMNS"] = "80"
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
    return {
        "verbs": verbs,
        # SORTED, so a reordering does not show up here...
        "command_names": sorted(commands),
        # ...and UNSORTED beside it, because it does show up to a user. Every
        # per-verb view above is keyed or sorted by name, so with only those, two
        # declarations swapped would leave this document byte-identical while the
        # top-level `--help` listed the verbs in the new order. Cross-model review
        # found that: the artefact claimed to constrain "the whole CLI help
        # surface" and constrained every part of it except the order.
        "registration_order": list(commands),
        # The PARENT parser's own help, for the same reason: the child parsers'
        # `format_help()` never contains the subcommand list.
        "root_format_help": parser.format_help(),
    }


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
