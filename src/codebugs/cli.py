"""Codebugs CLI — thin orchestrator over domain modules."""

from __future__ import annotations

import argparse
import sys

from codebugs import db


def _cmd_init(args: argparse.Namespace) -> None:
    result = db.init_project(args.directory)
    verb = "Initialized" if result["created"] else "Already initialized:"
    print(f"{verb} codebugs tracker at {result['path']}")


def _register_init(sub, commands: dict) -> None:
    """Register `init`, the one command that may create a tracker.

    Lives here rather than in a domain module: it bootstraps the DB every other
    command needs, so it must work when no DB exists yet, in every --mode.
    """
    p = sub.add_parser("init", help=f"Create a {db.DB_DIR}/ tracker in this directory")
    p.add_argument("directory", nargs="?", default=None, help="Directory (default: cwd)")
    commands["init"] = _cmd_init


def main() -> None:
    """CLI entry point with mode-based command discovery."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--mode",
        choices=["findings", "provenance", "reqs", "merge", "sweep", "bench", "blockers", "milestones", "all"],
        default="all",
    )
    pre_args, _ = pre_parser.parse_known_args()

    parser = argparse.ArgumentParser(
        description="codebugs — AI-native code finding & requirements tracker",
        prog="codebugs",
        parents=[pre_parser],
    )
    sub = parser.add_subparsers(dest="command")
    commands: dict = {}

    _register_init(sub, commands)
    for provider in db.get_cli_providers(mode=pre_args.mode):
        provider.register_fn(sub, commands)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    try:
        commands[args.command](args)
    except db.DatabaseNotFoundError as e:
        print(f"codebugs: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
