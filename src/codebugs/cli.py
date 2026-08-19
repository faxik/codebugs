"""Codebugs CLI — thin orchestrator over domain modules."""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import sys

from codebugs import db


def _cmd_init(args: argparse.Namespace) -> None:
    # WHERE `init` CREATES (CB-48). A declared root always redirects READS.
    # Whether it also redirects CREATION depends on the CHANNEL, because the two
    # channels are not the same kind of statement:
    #
    #   --tracker-root DIR   is typed on THIS command line, about THIS
    #                        invocation. `init` honours it and creates DIR.
    #   $CODEBUGS_ROOT       is ambient — exported into a shell days ago and
    #                        inherited by an unrelated subprocess. A tracker
    #                        conjured somewhere else by ambient state is the
    #                        failure this project refuses everywhere else, so
    #                        creation stays where the user is standing.
    #
    # An explicit positional outranks both, which is the precedence
    # `db._resolve_db` already applies to reads (argument > flag > env > walk).
    # Before CB-48 the flag was simply dropped here, and the mismatch warning
    # below then announced the opposite of what had just happened on disk.
    declared, source = db.declared_tracker_root()
    directory = args.directory
    if directory is None and source == "flag":
        directory = declared
    try:
        result = db.init_project(directory, force=args.force)
    except (ValueError, OSError) as e:
        print(f"codebugs: {e}", file=sys.stderr)
        sys.exit(1)
    if args.force and db._find_db_root(os.path.dirname(result["root"])) is not None:
        print(
            f"codebugs: warning — this tracker is nested inside another and will hide it "
            f"from everything under {result['root']}",
            file=sys.stderr,
        )
    # Whatever mismatch survives the routing above is announced, because
    # otherwise `init` reports success for a tracker every other command will
    # ignore — a success-shaped signal for a dead end. Two cases reach it now:
    # `$CODEBUGS_ROOT` naming somewhere else, and a positional argument
    # overriding `--tracker-root`.
    if declared is not None and os.path.realpath(declared) != os.path.realpath(result["root"]):
        print(
            f"codebugs: warning — {db.SOURCE_LABELS[source]} names {declared}, so commands "
            f"will read that tracker, not the one at {result['root']}",
            file=sys.stderr,
        )
    verb = "Initialized" if result["created"] else "Already initialized:"
    print(f"{verb} codebugs tracker at {result['path']}")


def _cmd_where(args: argparse.Namespace) -> None:
    info = db.describe_root()
    print(f"source:   {info['source_label']}")
    if info["error"]:
        print("root:     (unresolved)")
        print(f"codebugs: {info['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"root:     {info['root']}")
    print(f"database: {info['path']}")
    if not info["exists"]:
        # Only reachable on the walk route — a `.codebugs/` with no database in
        # it. Saying so is the whole job here: otherwise `where` reports a path
        # that is not there as if it were the project's tracker (CB-23).
        print("          (no database there yet — the next command creates one)")


def _register_builtins(sub, commands: dict) -> None:
    """Register the two commands that must work before any tracker is reachable.

    They live here rather than in a domain module for the same reason: `init`
    bootstraps the DB every other command needs, and `where` diagnoses the case
    where that DB cannot be found at all. Both must work when no DB exists yet,
    in every --mode.
    """
    p = sub.add_parser("init", help=f"Create a {db.DB_DIR}/ tracker in this directory")
    p.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="Directory (default: the --tracker-root DIR if given, else cwd)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Create even if an enclosing tracker already covers this directory",
    )
    commands["init"] = _cmd_init

    sub.add_parser("where", help="Show which tracker this process is bound to, and why")
    commands["where"] = _cmd_where


def main() -> None:
    """CLI entry point with mode-based command discovery."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--mode",
        choices=["findings", "provenance", "reqs", "merge", "sweep", "bench", "blockers", "milestones", "claims", "similarity", "relations", "all"],
        default="all",
    )
    pre_parser.add_argument(
        "--tracker-root",
        default=None,
        metavar="DIR",
        help=(
            f"Use the tracker in DIR instead of walking up from the current directory, "
            f"and with `init`, create it there "
            f"(overrides ${db.ENV_ROOT}; see `codebugs where`)"
        ),
    )
    pre_args, _ = pre_parser.parse_known_args()
    # Set before any command runs, and before any connection: every handler calls
    # db.connect() with no arguments, so db is the only place that can honor it.
    db.set_tracker_root(pre_args.tracker_root)

    parser = argparse.ArgumentParser(
        description="codebugs — AI-native code finding & requirements tracker",
        prog="codebugs",
        parents=[pre_parser],
    )
    sub = parser.add_subparsers(dest="command")
    commands: dict = {}

    _register_builtins(sub, commands)
    for provider in db.get_cli_providers(mode=pre_args.mode):
        provider.register_fn(sub, commands)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    try:
        commands[args.command](args)
    except (db.DatabaseNotFoundError, db.TrackerExistsError, db.TrackerUnwritableError) as e:
        # `TrackerUnwritableError` (CB-86) is a TYPE here rather than a
        # classification made at this boundary, and the difference is the whole
        # design. A `sqlite3.OperationalError` arm added here could not tell a
        # pre-write failure from a post-commit one — the constraint CB-55 states
        # and `tests/test_bench.py:789` enforces by requiring a post-commit
        # failure to keep its traceback. `db._open` raises this before it returns
        # a connection, so the type carries that provenance with it.
        print(f"codebugs: {e}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.OperationalError as e:
        # Contention is not a crash. db.connect() WRITES during schema init
        # (merge.ensure_schema's INSERT OR IGNORE), so a database held by another
        # writer for longer than busy_timeout used to kill every verb with a
        # traceback before its own code ran. Exit 5 means "retry", uniformly.
        # Anything that is not BUSY/LOCKED is a real error and still propagates.
        if not db.is_contention(e):
            raise
        print(f"codebugs: database busy, retry shortly ({e})", file=sys.stderr)
        sys.exit(5)


def run() -> None:
    """PROCESS entry point: restore POSIX SIGPIPE semantics, then run `main`.

    CB-78. Python installs ``SIG_IGN`` for ``SIGPIPE`` at startup, which converts
    the kernel's "your reader is gone" signal into an ``EPIPE`` write failure. A
    verb whose mutation had ALREADY COMMITTED then reported that as a failure —
    measured against 39d176a with the write confirmed present in the tracker
    afterwards:

        codebugs add … | true   (-u)     BrokenPipeError traceback,  exit 1
        codebugs add … | true   (block)  "Exception ignored on flushing
                                          sys.stdout: BrokenPipeError", exit 120

    ``SIG_DFL`` gives ``exit 141`` (128 + SIGPIPE) and an empty stderr in BOTH
    buffering modes. 141 is the point, not a side effect: it is the only outcome
    that stays distinguishable from a real failure (1), which "silent exit 0" and
    "silent exit 1" both destroy. Ratified by the user 2026-08-19, twice — the
    second time knowing that `export-csv /dev/stdout` into a dead reader trades a
    working one-line diagnostic for a silent 141.

    TWO PROPERTIES OF THIS FUNCTION ARE LOAD-BEARING. Neither is decoration, and
    both were established by measurement after review reproduced the failures.

    **It is separate from `main` because `main` is imported and called
    IN-PROCESS.** ``tests/test_fsio.py``, ``tests/test_findings.py`` and
    ``tests/manual/repro_cb76_truncation.py`` all call ``cli.main()`` directly and
    deliberately, so that injected failures reach it. ``signal.signal`` is an
    unrestored, process-global mutation: installed inside ``main`` it would leave
    the whole pytest session running under ``SIG_DFL``, and a two-test file
    reproduced that — ``pytest -q -s . | head -2`` died at 141 with empty stderr
    mid-suite, where the same file without the signal call exits 1. It also raises
    ``ValueError: signal only works in main thread of the main interpreter`` off
    the main thread, which the ``hasattr`` guard does not cover, so ``main`` would
    become unusable off-thread for a reason unrelated to its job.

    **It must NEVER restore the previous disposition.** The obvious "polite"
    variant — install, call ``main``, restore in a ``finally`` — was measured and
    it REINTRODUCES the defect: ``add | true`` under block buffering goes back to
    ``exit 120`` and "Exception ignored on flushing sys.stdout", because that
    write is the interpreter's shutdown flush and happens after ``main`` returns.
    "Do not mutate process-global state" and "fix the block-buffered case" are
    incompatible inside one function; splitting the executable wrapper from the
    importable body is what makes both true at once.

    ``hasattr``: Windows has no ``SIGPIPE``.

    DEPLOYMENT CAVEAT, invisible in the diff: a console shim generated before this
    change imports ``main`` BY NAME, so an existing install keeps the old
    behaviour until ``pipx reinstall codebugs`` / ``pip install -e .`` regenerates
    it. ``tests/test_cli_signals.py::TestConsoleScriptTargetsTheWrapper`` pins the
    declaration; nothing can pin someone else's installed shim.

    WHEN THIS IS OBSERVABLE AT ALL (measured; the honest condition has two
    branches and stating only the second is wrong): either the reader closes
    WITHOUT draining, at any output size — a 656-byte export into ``( exit 0 )``
    reproduces — or un-drained output exceeds the pipe buffer, 65536 bytes here
    via ``F_GETPIPE_SZ``. A one-line ``add`` or a 6.5 KB ``query`` piped to
    ``head -1`` never reaches the state, because ``head`` drains it first.

    Note ``codebugs --help | head -0`` now exits 141 rather than 0: the
    disposition is installed above argument parsing, deliberately, since argparse
    writes to stdout too.
    """
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()


if __name__ == "__main__":
    run()
