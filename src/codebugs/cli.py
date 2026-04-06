"""Codebugs CLI — thin orchestrator over domain modules."""

from __future__ import annotations

import argparse
import sys

from codebugs import db


def main() -> None:
    """CLI entry point with mode-based command discovery."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "bench", "blockers", "all"],
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

    for provider in db.get_cli_providers(mode=pre_args.mode):
        provider.register_fn(sub, commands)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    commands[args.command](args)


if __name__ == "__main__":
    main()
