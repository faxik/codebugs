"""Codebugs CLI — thin orchestrator over domain modules."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import sys

from codebugs import __version__, db


@contextlib.contextmanager
def domain_errors(*, prefix: str = ""):
    """Encode the CB-55 / CB-86 CLI-boundary rule exactly once.

    A domain call wrapped in ``with domain_errors():`` can raise three different
    things, and CLAUDE.md's Error handling section is precise that they must be
    told apart rather than folded into one arm:

    - ``json.JSONDecodeError`` IS a ``ValueError`` subclass, but here it means
      the write already landed and only the RETURN VALUE's serialization then
      failed — ``row_to_dict`` parsing a matched row's corrupted stored
      ``meta``/``tags`` (CB-16). Reporting that through the input-validation
      arm would print a tidy one-line "bad input" message and exit 1 for a
      mutation that already committed, which CB-86 names as the same lie as
      CB-15/CB-16. So it is re-raised, unchanged, and reaches the user as a
      loud traceback — the discriminator `tests/test_bench.py` pins between a
      post-commit failure and an input error.
    - Plain ``ValueError`` / ``KeyError`` are genuine bad input (an unknown
      vocabulary value, a missing id) and print one line to stderr, then
      ``sys.exit(1)``.
    - Everything else — ``OSError`` from a git subprocess (CB-79),
      ``db.TrackerUnwritableError`` (CB-86, classified in ``db._open``),
      ``BrokenPipeError``/the SIGPIPE path (CB-78, CB-134) — is untouched: it
      is not caught here and propagates to whatever already decides it. A
      central arm on ``cli.main`` cannot make any of those calls, which is
      exactly why this wrapper is scoped to one domain call's region and not
      hoisted further up the stack (CB-55's own rejected-design note).

    ``prefix`` exists ONLY to preserve an individual handler's existing
    message text (some print ``str(e)`` bare, some ``f"codebugs: {e}"``, some
    ``f"Error: {e}"``) — it is formatting, not part of the rule, and the rule
    itself never changes with it.
    """
    try:
        yield
    except json.JSONDecodeError:
        raise
    except (ValueError, KeyError) as e:
        print(f"{prefix}{e}", file=sys.stderr)
        sys.exit(1)


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
        with domain_errors(prefix="codebugs: "):
            result = db.init_project(directory, force=args.force)
    except OSError as e:
        # Kept as its own arm, outside domain_errors: `db.init_project`'s
        # OSError is a file-I/O failure (CB-79's OTHER vocabulary — not the
        # git-subprocess one), and the CB-55 wrapper deliberately never
        # touches OSError at all (see its docstring), so it must stay caught
        # here or it would escape as a traceback for a state this command has
        # always reported cleanly.
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
    elif info["writable"] is False:
        # CB-100: an unwritable tracker used to look identical to a healthy one
        # here, while every verb refused it. `writable` is advisory (os.access
        # is check-then-act), so this is worded as a warning to investigate, not
        # a verdict — and it is silent on True/None, on purpose: see
        # db.describe_root's docstring for why only the negative answer prints.
        #
        # CB-182: this used to print to stderr while the sibling "no database
        # there yet" note two branches up prints to stdout — two parenthetical
        # continuations of the same three-line table, split across streams for
        # no reason tied to what either one says. Neither branch is an error
        # (the exit code stays 0 on both), so `codebugs where 2>/dev/null` — the
        # ordinary way to get a clean view or feed a script — used to make this
        # warning vanish entirely, silently resurrecting the exact CB-100 defect
        # this line exists to close. It now prints alongside the rest of the
        # table, in stdout, like its sibling.
        print(
            "          (may not be writable — check permissions on the file "
            "and its .codebugs/ directory)"
        )


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


def build_parser(mode: str = "all", pre_parser: argparse.ArgumentParser | None = None):
    """Build the subparser tree through the SAME two primitives `main()` uses.

    CB-146. This exists so the CLI-surface snapshot (`tests/cli_surface.py`) can
    capture the real, wired parser without hand-assembling subparsers by calling
    each domain's `register_cli` directly — that shortcut would miss a wiring
    defect in `_register_builtins` or in the `db.get_cli_providers(mode=)` loop
    itself, exactly the two things `main()` also depends on. `mode="all"` (the
    default) is what an ordinary invocation with no `--mode` gets, and it walks
    the CLI-provider REGISTRY rather than any hardcoded domain list, so a domain
    that forgot to register — or one added tomorrow — changes what this returns
    without this function naming it anywhere.

    `pre_parser` is accepted so `main()` can pass its own `--mode`/`--tracker-root`
    parser in as the parent (unchanged behaviour there). When omitted — the
    snapshot's case — a bare `add_help=False` parser is used instead: those two
    global flags don't affect which SUBPARSERS get built for a given `mode`, so
    the snapshot has no reason to duplicate their declaration (which lives, once,
    inside `main()` — see `tests/test_relations.py` and
    `tests/test_grouping_surface.py`, which pin domain names appearing literally
    inside `cli.main`'s own source and would break if that declaration moved).

    Returns `(parser, sub, commands)`, exactly the three names `main()` used to
    build inline.
    """
    if pre_parser is None:
        pre_parser = argparse.ArgumentParser(add_help=False)
    parser = argparse.ArgumentParser(
        description="codebugs — AI-native code finding & requirements tracker",
        prog="codebugs",
        parents=[pre_parser],
        epilog=(
            "Getting started: `codebugs init` creates a tracker in this directory; "
            "`codebugs add -s <severity> -c <category> -f <file> -d <description>` "
            "files your first finding; `codebugs query` or `codebugs recent` shows "
            "what is already in the queue."
        ),
    )
    # Declared on the TOP-LEVEL parser this function builds, not on `pre_parser`
    # (the `--mode`/`--tracker-root` parent). `pre_parser` is shared with every
    # subcommand via `parents=[...]`, so an option declared there answers for
    # every verb — `codebugs add --version` would print the version for a verb
    # that never declared it. It also cannot be declared on both: argparse
    # raises an option-string conflict when a parent and its child both define
    # the same flag.
    parser.add_argument(
        "--version",
        action="version",
        version=f"codebugs {__version__}",
        help="Show the version of this codebugs and exit",
    )
    sub = parser.add_subparsers(dest="command")
    commands: dict = {}

    _register_builtins(sub, commands)
    for provider in db.get_cli_providers(mode=mode):
        provider.register_fn(sub, commands)

    return parser, sub, commands


def main() -> None:
    """CLI entry point with mode-based command discovery."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--mode",
        choices=["findings", "provenance", "reqs", "merge", "sweep", "bench", "blockers", "milestones", "claims", "similarity", "grouping", "relations", "loc", "usage", "all"],
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

    parser, _sub, commands = build_parser(mode=pre_args.mode, pre_parser=pre_parser)

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
        # Contention is not a crash, and this arm is still needed — but NARROWER
        # than the sentence that used to stand here, which read "db.connect()
        # WRITES during schema init" as a live fact. CB-195 made those seed
        # inserts conditional on the seed row being missing, so on a tracker that
        # has been opened before, connect() takes no write lock at all and a
        # reading verb no longer contends with anyone. What survives is the FIRST
        # open of a tracker (and any tracker whose seed rows were removed): there
        # the insert really does run, so a foreign writer holding the lock makes
        # connect() wait out the whole hold and, past busy_timeout, raise here
        # before the verb's own code runs. Measured on this tree: 734ms under a
        # 700ms foreign hold with the seed row absent, against 0.8ms with it
        # present. Exit 5 means "retry", uniformly, and anything that is not
        # BUSY/LOCKED is a real error and still propagates.
        if not db.is_contention(e):
            raise
        print(f"codebugs: database busy, retry shortly ({e})", file=sys.stderr)
        sys.exit(5)


# CB-134. The exit code for "the reader of my output is gone". CB-78 established
# it as 128 + SIGPIPE for a dead PIPE; a stdout that is already closed is the
# same event observed earlier, so it reports the same way rather than inventing a
# second vocabulary for one condition. The literal is the Windows fallback only:
# there is no SIGPIPE there, and a platform-specific number would make the
# contract mean two things again, which is the whole defect this closes.
_NO_READER_EXIT = (128 + signal.SIGPIPE) if hasattr(signal, "SIGPIPE") else 141


def _stdout_is_usable() -> bool:
    """Is stdout PROVABLY unwritable? A CHECK — it mutates nothing.

    The question is asked in that direction on purpose. "Can this process write
    to stdout" is what an earlier summary line here claimed to answer, and no
    preflight can: see WHAT THE PREDICATE ACTUALLY CLAIMS below. `False` is a
    proof; `True` is the absence of one.

    CB-134. A closed stdout is NOT a dead pipe: no write ever reaches the kernel,
    so nothing raises SIGPIPE and `run`'s disposition never fires. What happened
    instead was whatever the stdlib did that release, and it differed on every
    axis. Measured on this repo's two interpreters, one mutating verb:

        state                      3.13.3                     3.14.4
        --------------------------------------------------------------------
        sys.stdout.close()         rc 1, traceback, LANDED    rc 1, traceback, nothing
        fd 1 closed at exec        rc 120, EBADF at the       rc 0, SILENT, LANDED
                                   shutdown flush, LANDED

    The 3.14 fd cell is the dangerous one and it is the newest: 3.14 sets
    `sys.stdout` to None when fd 1 is invalid at startup, `print` is a no-op
    against None, and argparse's colour probe short-circuits on
    `hasattr(None, "fileno")` — so every verb runs, discards its entire output
    and exits 0. That is the "silent exit 0" CB-78's ratification rejected by
    name, reached by upgrading the interpreter rather than by changing any code
    here.

    THREE STATES, because one predicate cannot see all three (each was measured,
    not reasoned):

    1. `sys.stdout is None` — 3.14 with fd 1 closed at exec.
    2. The OBJECT is closed — `sys.stdout.close()`. `fileno()` raises
       `ValueError`, which is exactly what `_colorize.can_colorize` does NOT
       catch (it guards only `OSError`), which is why 3.14 dies during parser
       construction.
    3. The wrapper is open over a descriptor that CANNOT BE WRITTEN — 3.13 with
       fd 1 closed at exec, where `closed` is False and `fileno()` returns 1
       quite happily. `fstat` does NOT see this and the first draft of this
       function was wrong for exactly that reason: measured, CPython's own
       startup opens a file onto fd 1 (the lowest free descriptor) — here
       `/sys/kernel/mm/transparent_hugepage/enabled`, READ-ONLY — so fd 1 is a
       perfectly valid open descriptor that reports mode 0o100644 and raises
       EBADF on the first write. The descriptor's ACCESS MODE is the only local
       evidence that discriminates it, so that is what is read.

    A STREAM WITH NO DESCRIPTOR IS USABLE, and that direction matters more than
    the refusals: `StringIO`, a pytest capture object and a pipe wrapper all
    raise from `fileno()` while being perfectly writable. Failing closed there
    would refuse ordinary runs, so the unknown resolves to "usable" — the
    opposite of this repo's usual fail-closed default, because here the
    conservative direction is to do the work, not to refuse it.

    WHAT THIS DELIBERATELY DOES NOT CATCH, narrowed to what is actually true.
    The fd-reuse case above is caught only while the reused descriptor is
    read-only, which is what CPython's own startup happens to produce. If some
    file opened for WRITING lands on fd 1, this returns True and the verb's
    output is written into that file. Nothing local can discriminate it — the
    descriptor is genuinely open, genuinely writable, and genuinely not stdout.
    That is a platform fd-hygiene hazard, stated rather than papered over with a
    guess; the remedy is not to close fd 1 and then exec something.
    """
    out = sys.stdout
    if out is None:
        return False
    if getattr(out, "closed", False):
        return False
    try:
        fd = out.fileno()
    except (AttributeError, OSError, ValueError):
        # No OS-level descriptor. `io.UnsupportedOperation` subclasses both
        # OSError and ValueError, so it is covered by this tuple.
        return True
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        # No F_GETFL there. Fall back to mere existence: the read-only-reuse
        # case this guards is a POSIX fd-allocation behaviour to begin with.
        try:
            os.fstat(fd)
        except OSError:
            return False
        return True
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError:
        return False  # EBADF: the descriptor really is gone
    return flags & os.O_ACCMODE in (os.O_WRONLY, os.O_RDWR)


# CB-136. The exit code for "my output could not be WRITTEN", on a descriptor
# that was perfectly healthy when the process started: `/dev/full`, a filesystem
# that filled up while the verb ran, a wedged PTY. 74 is `EX_IOERR` from
# sysexits(3).
#
# It says exactly one thing — this process could not put its output where it was
# told to — and it deliberately asserts NOTHING about whether the command's
# effect landed, because the CLI in general cannot know: the write that failed is
# often the line REPORTING a mutation that has already committed. That is the
# whole point. It replaces a code that asserted something FALSE (`1`, which every
# handler in this package uses for bad input, printed over a committed write —
# the CB-15/CB-16 class of lie) with one that asserts nothing false.
#
# Why not a code already in the vocabulary, so nobody re-opens this cheaply:
#   0    the silent success over lost output that CB-78's ratification rejected
#        by name (`export-csv /dev/stdout | gzip > backup.gz` with a dead gzip).
#   141  "the reader of my output is GONE". Here the reader is PRESENT and the
#        medium is full. It would be a lie of the same size, and it would blur
#        the one distinction CB-78 was built to preserve.
#   1 + a tidy stderr line   the human reading stderr learns the truth; the
#        calling script reading `$?` does not. Distinguishability of the CODE is
#        exactly what CB-78 refused to trade away.
_WRITE_FAILURE_EXIT = 74


class _StdoutWriteFailed(Exception):
    """An ``OSError`` that PROVABLY arose from writing to THIS process's stdout.

    CB-136. This is the CB-86 shape applied to a second failure family: classify
    at the SOURCE and raise a TYPE, rather than classify at the CLI boundary. A
    central ``except OSError`` in ``main`` is forbidden by name in ``CLAUDE.md``
    for the reason that decides this design too — it cannot tell an output
    failure from an unrelated ``OSError`` that escaped a handler, and the
    argument "the exit code does not change, so no new lie is possible" proves
    too much. Here the provenance is structural: this exception exists only
    because ``_ClassifyingStdout`` caught the ``OSError`` from the very
    ``write``/``flush`` call it delegated to the process's stdout. Nothing else
    can raise it.

    It is deliberately NOT an ``OSError`` subclass. Handlers in this package
    catch ``OSError`` for their own I/O (``_cmd_init``, ``fsio``), and being
    swallowed there is precisely how an output failure becomes a tidy "bad
    input" line again.
    """

    def __init__(self, cause: OSError) -> None:
        super().__init__(str(cause))
        self.cause = cause


class _ClassifyingStdout:
    """``sys.stdout`` with its write failures given a type. Installed by ``run``.

    Only ``write`` and ``flush`` are intercepted; everything else — ``fileno``,
    ``isatty``, ``encoding``, ``buffer``, ``closed`` — is delegated untouched, so
    ``argparse``'s colour probe and anything else that interrogates the stream
    sees the real object's answers.

    WHAT IT DOES NOT REACH, because the honest scope is the point.
    ``fsio.atomic_write`` opens ``/dev/stdout`` BY PATH and writes through its own
    file object (CB-76's held-open-inode branch), so an ``export-csv
    /dev/stdout`` failure never passes through here and keeps reporting the way
    CB-76 made it report. That is unchanged behaviour, not a hole this opened.

    Two more spellings go round it, both delegated by ``__getattr__`` and neither
    used anywhere in this package (grep over ``src/``): ``sys.stdout.buffer`` and
    ``sys.stdout.writelines``. ``print`` and the ``csv`` writer both call
    ``write``, which is why interception is placed there. If a future handler
    reaches for either, its write failure goes back to being an untyped
    ``OSError`` — so this is a place to LOOK, not a guarantee to assume.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, text):
        try:
            return self._stream.write(text)
        except OSError as exc:
            raise _StdoutWriteFailed(exc) from exc

    def writelines(self, lines):
        # Delegated through `write` rather than to the stream's own
        # `writelines`, which `__getattr__` would otherwise hand out bound to the
        # UNDERLYING object — a bypass that puts the raw traceback straight back
        # (reproduced by cross-model review). Nothing in this package calls it
        # today; `print` and `csv` both go through `write`.
        for line in lines:
            self.write(line)

    def flush(self):
        try:
            return self._stream.flush()
        except OSError as exc:
            raise _StdoutWriteFailed(exc) from exc

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _exit_output_lost(cause: BaseException) -> None:
    """Announce the lost output on stderr and leave the process with 74."""
    # EPIPE IS NOT ENOSPC, and conflating them would undo CB-78 inside CB-136's
    # own fix. A dead reader normally kills the process by signal, so this arm is
    # never reached — UNLESS the caller left SIGPIPE BLOCKED in the signal mask it
    # `exec`d us with, which `run`'s `SIG_DFL` does not undo (a disposition is not
    # a mask). The write then returns EPIPE instead of raising a signal, and
    # reporting 74 there would say "the medium is full" about a reader that is
    # GONE. Reproduced by cross-model review; `_NO_READER_EXIT` is the code
    # CB-78/CB-134 already declare for exactly that event.
    code = _NO_READER_EXIT if isinstance(cause, BrokenPipeError) else _WRITE_FAILURE_EXIT

    # The diagnostic MUST NOT raise, and `file=sys.stderr` is not enough to
    # guarantee it. With fd 2 closed at exec the interpreter sets `sys.stderr` to
    # None, `print(file=None)` is documented to fall back to STDOUT, and the
    # message then went into the very stream that just failed — raising a second
    # `_StdoutWriteFailed` out of the handler and restoring the old exit 1 / 120
    # verbatim. Measured by cross-model review (`verb >/dev/full 2>&-`), which is
    # the trap this card names — a cure reproducing the disease on the
    # neighbouring descriptor — arriving through the ONE spelling the /dev/full
    # test cannot see, because there stderr is a live object.
    #
    # THE `None` GUARD IS THE FIX; `_StdoutWriteFailed` IN THE ARM BELOW IS
    # INSURANCE, and the difference was measured rather than assumed. Deleting
    # the guard alone reddens the block-buffered case only (`print` buffers, then
    # `None.flush()` raises `AttributeError`, which the arm does not catch);
    # deleting the arm's `_StdoutWriteFailed` alone reddens NOTHING, because with
    # the guard standing the diagnostic never reaches a failing stream. So that
    # entry stays for the cost of one word, not because a test covers it — the
    # same honesty this file already applies to `sys.stdout = None` in the
    # CB-134 branch above.
    err = sys.stderr
    try:
        if err is not None:
            print(
                f"codebugs: could not write my output ({cause}). Exit "
                f"{code} means the output was LOST; it says nothing "
                f"about whether the command's effect landed — check the tracker.",
                file=err,
            )
            err.flush()
    except (OSError, ValueError, _StdoutWriteFailed):
        # stderr is failing too — one full filesystem serves both descriptors,
        # and this is reachable in one command (`verb >/dev/full 2>/dev/full`).
        # Swallowing is not enough: stderr is LINE-buffered, so the failed write
        # leaves the message pending, and finalization then flushes it, fails,
        # and rewrites the status 74 -> 120 — the exact code this card removes,
        # reintroduced by its own diagnostic. Measured, not reasoned: without
        # this line that command exits 120.
        sys.stderr = None
    # Whatever is still buffered can never be written. Letting finalization try
    # again rewrites the process status to 120, which is the very code this card
    # removes — pinned by
    # `test_premise_a_failed_shutdown_flush_rewrites_the_exit_status`.
    sys.stdout = None
    sys.exit(code)


def _flush_stdout_or_exit() -> None:
    """Force the pending output out NOW, while an ``except`` can still see it.

    CASE (B) of CB-136, and the position of this call is the whole of it. Under
    block buffering — the default for any redirected stdout — ``print`` performs
    no syscall, so the failure surfaces at the interpreter's shutdown flush,
    AFTER ``main`` has returned and after every ``except`` in this package is out
    of scope. That is where "Exception ignored while flushing sys.stdout" and
    exit 120 come from. Flushing here moves that failure back inside a frame that
    can classify it, and the failure at THIS point is unambiguous: nothing is
    left to write but stdout's own buffer.

    It must be called AFTER ``main`` returns, never before: at entry the buffer
    is empty, no syscall happens, and the probe would pass by construction.
    """
    try:
        sys.stdout.flush()
    except _StdoutWriteFailed as exc:
        _exit_output_lost(exc.cause)
    except OSError as exc:
        # NOT the central `except OSError` CLAUDE.md forbids, and the difference
        # is the position: the only statement this arm guards is stdout's own
        # flush, so an OSError here is a stdout write failure by construction,
        # whatever object `sys.stdout` currently is. It exists because the proxy
        # is not guaranteed to still be installed — a handler that swapped
        # `sys.stdout` would otherwise put case (B) straight back to a traceback.
        _exit_output_lost(exc)


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

    CB-134 ADDED A SECOND JOB HERE, AND IT CHANGED BEHAVIOUR ON 3.13. The
    disposition above covers a dead PIPE. It cannot cover a stdout that is
    already CLOSED, because no write ever reaches the kernel and no signal is
    ever raised — so that case fell through to whatever the stdlib did that
    release, and the four measured cells disagreed on exit code, on whether a
    raw traceback appeared, and on whether the write landed (the table is in
    ``_stdout_is_usable``). ``run`` now REFUSES such a run at the entry, before
    any work, with the same 141.

    The price, because it is a real behaviour change and not a bug fix on 3.13:
    a closed-object stdout there used to let the write LAND and then fail on
    output, and now lands nothing. That is the point rather than a regression —
    with the refusal ahead of the work there is no committed write left to
    misreport, so the CB-15/CB-16 success-shaped lie is unrepresentable on this
    path instead of merely being caught downstream. On 3.14 the change is
    strictly a repair: the fd-closed spelling exited **0** in silence with the
    write landed, which is the "silent exit 0" this card's own ratification
    rejected by name.

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

    CB-136 ADDED A THIRD JOB, FOR THE STATE NEITHER OF THE OTHER TWO CAN SEE: a
    descriptor that was HEALTHY at the process entry and refuses the WRITE.
    ``/dev/full``, a filesystem that filled while the verb ran, a wedged PTY.
    ``SIG_DFL`` is blind to it by construction (there is no signal — ENOSPC is a
    plain write error), and ``_stdout_is_usable`` is blind to it correctly (at
    entry the descriptor really is fine, and widening that gate would break the
    contracts it was measured against). Two doors shut, a third open. Measured on
    this tree before the fix, one mutating verb with stdout on ``/dev/full``:

        buffering            what the user saw                  rc   write
        ----------------------------------------------------------------------
        unbuffered (-u)      raw OSError traceback              1    LANDED
        block (the default)  "Exception ignored while           120  LANDED
                             flushing sys.stdout"

    Byte for byte the CB-78 profile with a different errno, and `1` is this
    package's code for BAD INPUT — printed over a mutation already committed.

    Both cells now report ``74``; see ``_WRITE_FAILURE_EXIT`` for why that code
    and not one already in the vocabulary. The two halves are separate mechanisms
    because the two failures surface in different places: ``_ClassifyingStdout``
    types the failure that reaches a handler (unbuffered, or a buffer that filled
    mid-run), and ``_flush_stdout_or_exit`` drags the block-buffered one back
    from interpreter finalization into a frame that can still classify it.

    ONE RESIDUAL, STATED RATHER THAN LEFT TO BE FOUND. A verb that CRASHES with a
    non-``SystemExit`` exception keeps its traceback and its exit code untouched
    — this repo ratifies the traceback as the discriminator between a
    post-commit failure and an input error (``tests/test_bench.py:789``) — so on
    that path a still-buffered stdout can be flushed at finalization and rewrite
    the status to 120, exactly as before. Trading a crash's traceback for a 74
    would hide the crash to fix its exit code, which is the worse of the two.
    """
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    if not _stdout_is_usable():
        # CB-134. Refuse BEFORE any work. The alternative — run the verb and
        # discard its output — is the silent success CB-78 rejected, and on 3.14
        # it is what the interpreter already does on its own.
        #
        # `sys.stdout = None` is INSURANCE, and the honest scope matters more
        # than the mechanism. The mechanism is real and measured on both
        # interpreters: with content already buffered on a bad descriptor,
        # finalization's flush of the std files fails and rewrites the process
        # status 141 -> 120 (pinned by
        # `test_premise_a_failed_shutdown_flush_rewrites_the_exit_status`).
        # But it is NOT reachable from HERE — at this point nothing has written
        # to stdout, so the buffer is empty and the flush succeeds. A mutant
        # deleting this line SURVIVES every behavioural test in that class on
        # 3.13.3 and 3.14.4. It stays because it costs one line and makes the
        # exit code independent of whether anything ever prints before the gate;
        # it does not stay because a test covers it.
        #
        # This is a process-global mutation, which is precisely why it lives
        # here and could not live in `main`: `main` is called in-process by
        # three test modules, and the same reasoning that keeps `signal.signal`
        # out of it keeps this out of it.
        sys.stdout = None
        sys.exit(_NO_READER_EXIT)
    # CB-136. From here on a stdout write failure carries its own type, so it can
    # be classified without a central `except OSError` that could not tell it
    # from an unrelated failure. Installed HERE and not in `main` for the same
    # reason as everything else in this function: `main` is called in-process by
    # three test modules, and `sys.stdout` is process-global state.
    sys.stdout = _ClassifyingStdout(sys.stdout)
    try:
        main()
    except _StdoutWriteFailed as exc:
        # CASE (A): the write raised inside a handler, because the buffer had
        # filled (or there is none). Unfixed, this left `main` as a bare OSError
        # and the interpreter printed a traceback and exited 1.
        _exit_output_lost(exc.cause)
    except SystemExit:
        # A verb that chose its own code still owes its output to the device.
        # 74 WINS over that code when the flush fails, deliberately: the caller
        # did not receive the output, so a code describing the verb's own verdict
        # would be read against text that never arrived. The cost is real and
        # named — a `5` (database busy, retry) becomes a 74 — and 74 is the more
        # actionable of the two there anyway, since retrying a full disk does not
        # help.
        _flush_stdout_or_exit()
        raise
    else:
        _flush_stdout_or_exit()


if __name__ == "__main__":
    run()
