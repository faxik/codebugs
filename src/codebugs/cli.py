"""Codebugs CLI — thin wrapper over the database layer."""

from __future__ import annotations

import argparse


def main() -> None:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "bench", "all"],
        default="all",
    )
    pre_args, _ = pre_parser.parse_known_args()

    parser = argparse.ArgumentParser(
        description="codebugs — AI-native code finding & requirements tracker",
        prog="codebugs",
        parents=[pre_parser],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    commands = {}
    if pre_args.mode in ("findings", "all"):
        from codebugs.db import register_cli as findings_cli
        findings_cli(sub, commands)
    if pre_args.mode in ("reqs", "all"):
        from codebugs.reqs import register_cli as reqs_cli
        reqs_cli(sub, commands)
    if pre_args.mode in ("merge", "all"):
        from codebugs.merge import register_cli as merge_cli
        merge_cli(sub, commands)
    if pre_args.mode in ("sweep", "all"):
        from codebugs.sweep import register_cli as sweep_cli
        sweep_cli(sub, commands)
    if pre_args.mode in ("bench", "all"):
        from codebugs.bench import register_cli as bench_cli
        bench_cli(sub, commands)

    args = parser.parse_args()
    commands[args.command](args)


if __name__ == "__main__":
    main()
