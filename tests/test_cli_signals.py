"""CB-78 — a closed stdout must not report a committed write as a failure.

WHY THIS FILE IS SUBPROCESS-BASED AND WHY THAT IS NOT NEGOTIABLE. The defect
lives in the process's SIGPIPE disposition and, in the block-buffered case, in
the interpreter's shutdown flush — which happens after `main()` has returned, so
no in-process caller and no `except` anywhere in this package can observe it.
`tests/test_fsio.py` deliberately runs the CLI in-process to inject failures;
that technique is blind to everything measured here.

WHAT DISCRIMINATES. Against the unfixed tree these same runs give exit **1** (a
`BrokenPipeError` traceback) unbuffered and exit **120** with "Exception ignored
on flushing sys.stdout" block-buffered, with the finding already in the tracker
both times. So `returncode == -SIGPIPE` and `stderr == ""` are each sufficient to
fail against main; the landed-row assertion is the premise that makes the defect
a LIE rather than merely ugly output, and is kept for that reason rather than as
a discriminator.

Every test here is POSIX-only: Windows has no SIGPIPE, which is exactly why
`cli.run` guards on `hasattr`.
"""

from __future__ import annotations

import ast
import os
import pathlib
import signal
import subprocess
import sys
import tomllib

import pytest

from codebugs import cli, db
from codebugs.cli import _NO_READER_EXIT

pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGPIPE"), reason="SIGPIPE does not exist on this platform"
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    """A clean environment for a CLI subprocess.

    `PYTHONUNBUFFERED` is removed rather than left to chance: it would silently
    turn the block-buffered case into the unbuffered one, collapsing the two
    parametrised runs into the same experiment while both still passed.

    Dropping `CODEBUGS_ROOT` is belt-and-braces, NOT coverage — say it that way
    rather than claiming the case is handled twice. `tests/conftest.py`'s autouse
    fixture already deletes it from this process before `os.environ` is copied
    here, so no test can discriminate the two while that fixture holds. It stays
    because the failure mode it guards is silent corruption of the developer's
    real tracker, which CLAUDE.md records actually happening.
    """
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
    env.pop("PYTHONUNBUFFERED", None)
    env.pop(db.ENV_ROOT, None)
    return env


def _run_into_a_dead_reader(project: pathlib.Path, *args: str, unbuffered: bool) -> tuple[int, str]:
    """Run the CLI with stdout on a pipe nobody reads, and return (rc, stderr).

    Closing the parent's read end leaves the child holding the only handle on the
    pipe, so its first write — or its shutdown flush — meets a reader-gone pipe.
    This is `| true`, expressed without a shell so the child's own status is
    observable: a shell would report the RIGHT-hand command's status instead.
    """
    interp = [sys.executable] + (["-u"] if unbuffered else [])
    proc = subprocess.Popen(
        [*interp, "-m", "codebugs.cli", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(project),
        env=_env(),
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.close()
    stderr = proc.stderr.read().decode()
    proc.stderr.close()
    proc.wait(timeout=60)
    return proc.returncode, stderr


def _count(project: pathlib.Path, description: str) -> int:
    conn = db.connect(str(project))
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM findings WHERE description = ?", (description,)
        ).fetchone()["n"]
    finally:
        conn.close()


@pytest.fixture()
def project(tmp_path):
    db.init_project(str(tmp_path))
    return tmp_path


class TestAClosedReaderKillsTheProcessBySignal:
    """The card's own reproducer, both buffering modes, on the real entry point."""

    @pytest.mark.parametrize("unbuffered", [True, False], ids=["unbuffered", "block-buffered"])
    def test_a_committed_add_dies_at_141_with_no_output(self, project, unbuffered):
        desc = f"cb-78 pipe {unbuffered}"
        rc, stderr = _run_into_a_dead_reader(
            project, "add", "-f", "x.py", "-c", "test", "-s", "low", "-d", desc, "--new-category",
            unbuffered=unbuffered,
        )

        # The premise. Without it there is no lie to detect, only a crash.
        assert _count(project, desc) == 1, "premise broken: nothing committed"

        assert rc == -signal.SIGPIPE, f"expected death by SIGPIPE, got rc={rc}, stderr={stderr!r}"
        assert stderr == "", stderr

    def test_the_run_that_had_nothing_to_commit_behaves_the_same(self, project):
        """`query` has no write, so this pins that the fix is about the STREAM,
        not about the verb — an implementation that special-cased write verbs
        would pass every test above and fail this one."""
        rc, stderr = _run_into_a_dead_reader(project, "query", "--status", "open", unbuffered=True)
        assert rc == -signal.SIGPIPE, f"rc={rc}, stderr={stderr!r}"
        assert stderr == ""


class TestExportToDevStdout:
    """The one command where CB-78 REMOVES a working diagnostic rather than
    replacing a traceback — pinned so the trade stays visible.

    `export-csv /dev/stdout` writes in place through `fsio.atomic_write`'s
    held-open-inode branch, so before CB-78 a dead reader raised
    `BrokenPipeError`, was caught by the CB-76 arm, and printed
    `codebugs: [Errno 32] Broken pipe` at exit 1. It now dies at 141 in silence.
    That was re-ratified by the user on 2026-08-19 knowing this; if someone later
    decides the message was worth keeping, this test is where the decision is
    recorded, not a comment.
    """

    def test_export_csv_to_dev_stdout_dies_at_141_without_a_message(self, project):
        # THREE rows, and the size is deliberately irrelevant — an earlier draft
        # padded this to ~21 KB, which reads as a threshold someone would later
        # "tune" against the 64 KB pipe buffer. `_run_into_a_dead_reader` closes
        # the read end BEFORE the child writes anything, so this is the
        # close-without-draining branch, which fires at any output size (see
        # `cli.run`'s docstring for both branches). The rows exist only so the
        # exporter has something to stream through `atomic_write`'s
        # held-open-inode path, which is the mechanism under test.
        conn = db.connect(str(project))
        try:
            from codebugs import findings

            for i in range(3):
                findings.add_finding(
                    conn, severity="low", category="test",
                    description=f"row {i} for the /dev/stdout export path", file="x.py", new_category=True,
                )
        finally:
            conn.close()

        rc, stderr = _run_into_a_dead_reader(
            project, "export-csv", "/dev/stdout", unbuffered=True
        )
        assert rc == -signal.SIGPIPE, f"rc={rc}, stderr={stderr!r}"
        assert stderr == "", stderr


class TestMainDoesNotTouchProcessSignalState:
    """`main` is imported and called IN-PROCESS by three test modules, so a
    signal disposition installed inside it would leak into the whole pytest
    session — reproduced during review as `pytest -q -s . | head -2` dying at
    141 mid-suite. This is the assertion that keeps the wrapper split honest;
    without it, folding `run`'s body back into `main` is a green change.
    """

    def test_calling_main_leaves_the_sigpipe_disposition_alone(self, project, monkeypatch):
        before = signal.getsignal(signal.SIGPIPE)
        monkeypatch.setattr(sys, "argv", ["codebugs", "--tracker-root", str(project), "stats"])
        try:
            cli.main()
            assert signal.getsignal(signal.SIGPIPE) is before
        finally:
            # RESTORE UNCONDITIONALLY. Without this, the one failure this test
            # exists to catch — `main` acquiring the disposition — leaves the
            # pytest process under SIG_DFL for every test after it, which is
            # exactly the mid-suite `pytest … | head` cascade the class docstring
            # cites as the reason the wrapper split exists. One clear red would
            # become a confusing multi-failure run whose cause is the test that
            # already named it.
            signal.signal(signal.SIGPIPE, before)

    def test_the_sigpipe_disposition_is_installed_in_exactly_one_place(self):
        """Structural, and deliberately so: a behavioural test of `run` would have
        to install SIG_DFL in the pytest process to observe it, which is the very
        thing the test above forbids.

        BY AST OVER THE WHOLE PACKAGE, and both halves of that were earned. The
        first draft read `cli.py` as TEXT and counted `"signal.signal("`, which
        CLAUDE.md's CLI section already ratifies against for the sibling ratchet:
        `TestWriteCallSitesRatchet` went to AST after its text draft matched
        `open(path, "w")` inside `fsio.py`'s own docstrings. `cli.run`'s docstring
        escaped that only by never spelling the call with parentheses — one
        editorial change away from a red suite over no defect.

        And it swept ONE FILE while its failure message promised "exactly one
        place", so a `signal.signal(SIGPIPE, …)` added in `server.py` or any
        domain module was invisible to the guard written to hold the composition
        — this repo's own "a check that validates elements cannot validate their
        composition", committed inside the check meant to prevent it.

        MUTATION-VERIFIED against three spellings injected into `server.py`
        (`signal.signal(...)`, `import signal as _s; _s.signal(...)`, and
        `from signal import signal`), each of which turns this red. The first
        AST draft caught only the first of the three, because it required the
        callee's module to literally be named `signal` — found by running the
        mutation rather than by reading the predicate.

        RESIDUAL LIMIT, stated rather than implied by silence: a dynamic call
        (`getattr(signal, "signal")(...)`) is still invisible. Unlike
        `TestWriteCallSitesRatchet`, which keys on a mode string and genuinely
        cannot be spelled around, this one keys on a name and therefore bounds
        accident, not intent.
        """
        sites: list[tuple[str, str]] = []
        for path in sorted((_REPO_ROOT / "src" / "codebugs").rglob("*.py")):
            tree = ast.parse(path.read_text())
            owner: dict[int, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    for child in ast.walk(node):
                        owner[id(child)] = node.name
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                # The callee's FINAL NAME only — never the module it was reached
                # through. Keying on `signal.signal` was this ratchet's own first
                # defect, caught by mutating `server.py` with
                # `import signal as _s; _s.signal(_s.SIGPIPE, _s.SIG_DFL)`: the
                # guard stayed green against a real second install site. Matching
                # the attribute name also covers `from signal import signal`.
                called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if called == "signal" and "SIGPIPE" in ast.unparse(node):
                    sites.append(
                        (
                            path.relative_to(_REPO_ROOT).as_posix(),
                            # "<module>" would be a real finding, not a miss: a
                            # module-level install fires on import, which is far
                            # worse than one in `main`.
                            owner.get(id(node), "<module>"),
                        )
                    )
        assert sites == [("src/codebugs/cli.py", "run")], (
            f"the SIGPIPE disposition must be installed in exactly one place, `cli.run`; "
            f"found {sites}. A second site is how the two entry points start disagreeing."
        )


class TestConsoleScriptTargetsTheWrapper:
    """A gate present in the tree and absent in effect is this repo's recurring
    failure, and this one is invisible from the diff: `main` still exists and
    still works, so pointing the console script at it would ship a fix that never
    runs for anyone using the installed `codebugs` command.
    """

    def test_pyproject_entry_point_is_run_not_main(self):
        data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
        assert data["project"]["scripts"]["codebugs"] == "codebugs.cli:run"

    def test_module_main_guard_calls_run(self):
        source = (_REPO_ROOT / "src" / "codebugs" / "cli.py").read_text()
        tail = source.split('if __name__ == "__main__":')[-1]
        assert "run()" in tail and "main()" not in tail, tail


class TestAClosedStdoutIsRefusedAtTheProcessEntry:
    """CB-134 — the CB-78 contract must mean the SAME thing on every interpreter
    `requires-python` admits, and before this it meant four different things.

    A closed stdout is not a dead pipe: nothing raises SIGPIPE, so `cli.run`'s
    disposition never fires and the process falls into whatever the stdlib
    happens to do that release. Measured on this repo's own two interpreters,
    for one mutating verb, across the two ways a stdout can be closed:

        state                       3.13.3                      3.14.4
        ----------------------------------------------------------------------
        sys.stdout.close()          rc 1, traceback, LANDED     rc 1, traceback, nothing
        fd 1 closed at exec (>&-)   rc 120, "Exception          rc 0, SILENT, LANDED
                                    ignored ... EBADF", LANDED

    Not one of those four is the declared outcome, and they disagree on all
    three axes at once — exit code, whether stderr carries a raw traceback, and
    whether the write landed.

    THE 3.14 `>&-` CELL IS THE WORST OF THE FOUR AND IS NOT WHAT THE CARD
    RECORDS. 3.14 sets `sys.stdout` to `None` when fd 1 is invalid at startup,
    `print()` is a documented no-op against `None`, and `argparse`'s colour probe
    short-circuits on `hasattr(None, "fileno")` — so every verb runs to
    completion, discards its whole output, and exits **0**. That is the "silent
    exit 0" the CB-78 ratification rejected by name, arrived at by upgrading the
    interpreter: `codebugs export-csv /dev/stdout | gzip > backup.gz` reports
    success over a backup that was never written.

    THE CONTRACT THIS PINS. `cli.run` refuses at the process entry, before any
    work, with **141** — the code CB-78 already declares for "the reader of my
    output is gone". Uniform across interpreters, no raw traceback, never 1,
    never a silent 0.

    THE PRICE, STATED RATHER THAN LEFT TO BE DISCOVERED. On 3.13 a closed-object
    stdout used to let the write LAND and then fail on output; it now lands
    nothing. That is a real behaviour change and it is the point: with the
    refusal before the work there is no committed write left to misreport, so
    the CB-15/CB-16 success-shaped lie becomes unrepresentable on this path
    instead of merely being caught. `run` is the only place this can live —
    `main` is called in-process by three test modules (see
    `TestMainDoesNotTouchProcessSignalState`).
    """

    EXPECTED = 128 + signal.SIGPIPE  # 141

    @staticmethod
    def _closed_fd(project, *args) -> tuple[int, str]:
        """fd 1 closed at exec — what a shell's `>&-` actually does.

        `exec` is load-bearing: it replaces the shell, so the status observed is
        the CLI's own and not `sh`'s report of it.
        """
        import shlex

        cmd = " ".join(shlex.quote(a) for a in [sys.executable, "-m", "codebugs.cli", *args])
        proc = subprocess.run(
            ["sh", "-c", f"exec {cmd} >&-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            cwd=str(project), env=_env(), timeout=60,
        )
        return proc.returncode, proc.stderr.decode()

    @staticmethod
    def _closed_object(project, *args) -> tuple[int, str]:
        """`sys.stdout.close()` before the entry point — the in-process spelling,
        and the one `tests/test_bench.py` used to reach for."""
        script = (
            "import sys\n"
            f"sys.argv = {['codebugs', *args]!r}\n"
            "from codebugs.cli import run\n"
            "sys.stdout.close()\n"
            "run()\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            cwd=str(project), env=_env(), timeout=60,
        )
        return proc.returncode, proc.stderr.decode()

    @pytest.mark.parametrize("how", ["closed_fd", "closed_object"])
    def test_a_closed_stdout_exits_141_without_a_traceback(self, project, how):
        rc, stderr = getattr(self, f"_{how}")(project, "query", "--status", "open")
        assert rc == self.EXPECTED, f"rc={rc}, stderr={stderr!r}"
        assert "Traceback" not in stderr, stderr
        assert stderr == "", stderr

    @pytest.mark.parametrize("how", ["closed_fd", "closed_object"])
    def test_a_mutating_verb_refuses_before_it_writes_anything(self, project, how):
        """The declared half of the price. This is the assertion that would have
        to be edited — not merely re-run — if someone later decided the write
        should land, so the decision cannot be reversed silently."""
        desc = f"cb-134 closed stdout {how}"
        rc, stderr = getattr(self, f"_{how}")(
            project, "add", "-f", "x.py", "-c", "test", "-s", "low", "-d", desc, "--new-category",
        )
        assert rc == self.EXPECTED, f"rc={rc}, stderr={stderr!r}"
        assert stderr == "", stderr
        assert _count(project, desc) == 0, "the refusal must happen before any work"

    @pytest.mark.parametrize("neutralise", [False, True], ids=["without", "with"])
    def test_premise_a_failed_shutdown_flush_rewrites_the_exit_status(self, tmp_path, neutralise):
        """PREMISE, not a property of the gate — and the distinction is the whole
        reason this test is worded this way.

        `cli.run` sets `sys.stdout = None` before exiting, and the justification
        is that interpreter finalization flushes the std files and rewrites the
        process status to 120 when that flush fails. That mechanism is REAL and
        measured here on both interpreters. What is NOT true is that the gate
        needs it: at the moment `run` refuses, nothing has written to stdout, so
        the buffer is empty, the flush performs no syscall and succeeds — a
        mutant that deletes the line SURVIVES the four behavioural tests above,
        on 3.13.3 and 3.14.4 alike. Measured, not assumed.

        So the line is INSURANCE against a future in which something prints
        before the gate, and this test pins the mechanism that would make it
        matter rather than pretending the gate covers it. Saying so is cheaper
        than a comment claiming coverage that no test can discriminate.
        """
        script = (
            "import sys, os\n"
            "sys.stdout.write('x' * 10)\n"  # buffered; no syscall yet
            "os.close(1)\n"  # the descriptor the pending flush will use
            + ("sys.stdout = None\n" if neutralise else "")
            + f"sys.exit({self.EXPECTED})\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(tmp_path), env=_env(), timeout=60,
        )
        assert proc.returncode == (self.EXPECTED if neutralise else 120), proc.returncode


def _status(project: pathlib.Path, finding_id: str) -> str | None:
    conn = db.connect(str(project))
    try:
        row = conn.execute(
            "SELECT status FROM findings WHERE id = ?", (finding_id,)
        ).fetchone()
        return None if row is None else row["status"]
    finally:
        conn.close()


class TestAFullDeviceReportsALostOutputAndNotBadInput:
    """CB-136 — the THIRD state, the one CB-78 and CB-134 are each blind to by
    construction: a descriptor that was HEALTHY at the process entry and refuses
    the WRITE.

    `/dev/full` is the reproducer. It is a real, open, writable character device
    whose every write returns ENOSPC — the same shape as a filesystem that fills
    while the verb runs. CB-78's cure is `signal(SIGPIPE, SIG_DFL)`, and there is
    no signal here at all; CB-134's cure is a gate at the entry, and at the entry
    this descriptor genuinely is fine (pinned below, because that is a PREMISE of
    this card, not a defect in that gate). Measured on the unfixed tree, one
    mutating verb whose write had already COMMITTED:

        unbuffered (-u)      raw OSError traceback              rc 1
        block (the default)  "Exception ignored while           rc 120
                             flushing sys.stdout"

    `1` is this package's code for BAD INPUT, printed over a landed mutation —
    the CB-15/CB-16 lie. Both cells must now be **74** (`EX_IOERR`), which claims
    only that the output was lost.

    WHAT DISCRIMINATES, so a reader can tell these from vacuous tests: against
    the unfixed tree every assertion below on `rc == 74` fails with 1 or 120.
    THE MUTANT: delete either half of the classification — the
    `except _StdoutWriteFailed` arm in `cli.run` (case A) or the
    `_flush_stdout_or_exit()` calls (case B) — and
    `test_a_committed_mutation_is_not_reported_as_bad_input` goes red for the
    corresponding buffering mode. Removing `_ClassifyingStdout.write`'s
    conversion reddens the unbuffered parametrisation of every test here.
    """

    # `/dev/full` is the reproducer, and a platform without it cannot reach the
    # state at all — a skip there is honest, not a disarm. The file is already
    # POSIX-only (module-level `pytestmark` guards on SIGPIPE).
    pytestmark = pytest.mark.skipif(
        not os.path.exists("/dev/full"), reason="/dev/full is the CB-136 reproducer"
    )

    EXPECTED = 74

    @staticmethod
    def _on_dev_full(
        project: pathlib.Path, *args: str, unbuffered: bool, stderr_too: bool = False
    ) -> tuple[int, str]:
        """Run the CLI with stdout redirected onto `/dev/full`.

        The parent opens `/dev/full` and hands the DESCRIPTOR to the child, which
        is what a shell's `>` does: the child receives fd 1 already pointing at
        the device, and the interpreter then chooses the buffering it always
        chooses for a character device. That is what makes the block-buffered
        cell the DEFAULT case here rather than an exotic one, and it is why
        `_env()` removes `PYTHONUNBUFFERED` — with it set, both parametrisations
        would silently run the same experiment.

        (An earlier draft of this docstring claimed a parent-opened file object
        was deliberately NOT used, which is the opposite of the code four lines
        below. Cross-model review caught it.)
        """
        interp = [sys.executable] + (["-u"] if unbuffered else [])
        with open("/dev/full", "wb") as full:
            proc = subprocess.run(
                [*interp, "-m", "codebugs.cli", *args],
                stdout=full,
                stderr=full if stderr_too else subprocess.PIPE,
                cwd=str(project),
                env=_env(),
                timeout=60,
            )
        return proc.returncode, "" if stderr_too else proc.stderr.decode()

    @pytest.fixture()
    def a_finding(self, project):
        conn = db.connect(str(project))
        try:
            from codebugs import findings

            findings.add_finding(
                conn, severity="low", category="test",
                description="cb-136 subject", file="x.py", new_category=True,
            )
        finally:
            conn.close()
        assert _status(project, "CB-1") == "open"
        return "CB-1"

    @pytest.mark.parametrize("unbuffered", [False, True], ids=["block", "unbuffered"])
    def test_a_committed_mutation_is_not_reported_as_bad_input(
        self, project, a_finding, unbuffered
    ):
        """The card's own reproducer. The landed-row assertion is the PREMISE
        that makes this a lie rather than ugly output, exactly as in
        `TestAClosedReaderKillsTheProcessBySignal`."""
        rc, stderr = self._on_dev_full(
            project, "update", a_finding, "--status", "fixed", unbuffered=unbuffered
        )
        assert _status(project, a_finding) == "fixed", "premise: the write landed"
        assert rc == self.EXPECTED, f"rc={rc}, stderr={stderr!r}"
        assert "Traceback" not in stderr, stderr
        assert "Exception ignored" not in stderr, stderr

    @pytest.mark.parametrize("unbuffered", [False, True], ids=["block", "unbuffered"])
    def test_the_stderr_line_says_the_output_was_lost_and_refuses_to_guess(
        self, project, a_finding, unbuffered
    ):
        """The contract is the CODE plus what the code is allowed to CLAIM. 74
        asserts the output was lost and NOTHING about the effect, because the CLI
        cannot know: here the effect landed, and the very line that would have
        said so is what failed to write."""
        rc, stderr = self._on_dev_full(
            project, "update", a_finding, "--status", "fixed", unbuffered=unbuffered
        )
        assert rc == self.EXPECTED, f"rc={rc}, stderr={stderr!r}"
        assert "74" in stderr, stderr
        assert "LOST" in stderr, stderr
        assert "says nothing about whether the command's effect landed" in stderr, stderr

    @pytest.mark.parametrize("unbuffered", [False, True], ids=["block", "unbuffered"])
    def test_a_read_verb_reports_the_same_way(self, project, a_finding, unbuffered):
        """Nothing was committed here, and the code is the same on purpose: 74 is
        about the OUTPUT, so it must not vary with what the verb did."""
        rc, stderr = self._on_dev_full(project, "query", unbuffered=unbuffered)
        assert rc == self.EXPECTED, f"rc={rc}, stderr={stderr!r}"
        assert "Traceback" not in stderr, stderr

    @pytest.mark.parametrize("unbuffered", [False, True], ids=["block", "unbuffered"])
    def test_a_failing_stderr_does_not_change_the_code(self, project, a_finding, unbuffered):
        """`verb >/dev/full 2>/dev/full` — one full filesystem serves both
        descriptors, so the diagnostic must not become the next failure.

        This is not hypothetical hardening: measured, the first draft exited
        **120** here, because stderr is LINE-buffered and the failed diagnostic
        stayed pending until finalization flushed it. The cure had reproduced the
        disease on the neighbouring descriptor.
        """
        rc, _ = self._on_dev_full(
            project, "update", a_finding, "--status", "fixed",
            unbuffered=unbuffered, stderr_too=True,
        )
        assert rc == self.EXPECTED, f"rc={rc}"

    @pytest.mark.parametrize("unbuffered", [False, True], ids=["block", "unbuffered"])
    def test_a_closed_stderr_does_not_push_the_diagnostic_into_the_broken_stdout(
        self, project, a_finding, unbuffered
    ):
        """`verb >/dev/full 2>&-` — the spelling `2>/dev/full` cannot reach.

        With fd 2 closed at exec the interpreter sets `sys.stderr` to None, and
        `print(file=None)` is DOCUMENTED to fall back to stdout. The diagnostic
        therefore went into the stream that had just failed, raised a second
        `_StdoutWriteFailed` out of the handler, and restored exit 1 (unbuffered)
        / 120 (block) verbatim — the cure reproducing the disease one descriptor
        over. Found by cross-model review and reproduced here before the fix:
        rc was 1 and 120 respectively.

        `2>/dev/full` does NOT cover this, which is the point of a separate test:
        there stderr is a live object that merely fails, so the `(OSError, ...)`
        arm catches it and the None branch is never taken.
        """
        import shlex

        argv = [sys.executable] + (["-u"] if unbuffered else [])
        argv += ["-m", "codebugs.cli", "update", a_finding, "--status", "fixed"]
        cmd = " ".join(shlex.quote(a) for a in argv)
        proc = subprocess.run(
            ["sh", "-c", f"exec {cmd} >/dev/full 2>&-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(project), env=_env(), timeout=60,
        )
        assert proc.returncode == self.EXPECTED, proc.returncode

    def test_a_dead_reader_reports_141_even_when_sigpipe_is_blocked(self, project):
        """EPIPE is not ENOSPC, and 74 must never be said about a GONE reader.

        `run` restores the SIGPIPE DISPOSITION (CB-78); it does not clear the
        signal MASK, and POSIX preserves a mask across `exec`. A caller that
        blocked SIGPIPE therefore gets `EPIPE` returned from the write instead of
        dying by signal — and the new classifier, catching every `OSError`, called
        that "the medium is full". Found by cross-model review, measured at 74
        before the fix.

        The unblocked control is what makes this non-vacuous: it must still die
        by SIGNAL, so a "fix" that routed both cases through the classifier would
        fail here.
        """
        def _run(block: bool) -> int:
            read_fd, write_fd = os.pipe()
            os.close(read_fd)
            pre = None
            if block:
                def pre():  # noqa: E306 - defined only for the blocked case
                    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGPIPE})
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-u", "-m", "codebugs.cli", "query"],
                    stdout=write_fd, stderr=subprocess.PIPE,
                    cwd=str(project), env=_env(), preexec_fn=pre,
                )
            finally:
                os.close(write_fd)
            proc.communicate(timeout=60)
            return proc.returncode

        assert _run(block=False) == -signal.SIGPIPE, "control: CB-78 still holds"
        assert _run(block=True) == _NO_READER_EXIT

    def test_writelines_does_not_bypass_the_classifier(self, project):
        """`__getattr__` hands out methods bound to the UNDERLYING stream, so
        every stream method the proxy does not define is a bypass. `writelines`
        is the one a future handler is most likely to reach for; measured at
        exit 1 with a raw traceback before the proxy defined it.

        `sys.stdout.buffer.write` remains a bypass and is documented as one on
        `_ClassifyingStdout` — wrapping the binary layer is a bigger change than
        this card, and nothing in `src/` uses it.
        """
        script = (
            "import sys\n"
            "from codebugs import cli\n"
            "cli.main = lambda: sys.stdout.writelines(['x' * 100 + chr(10)])\n"
            "cli.run()\n"
        )
        with open("/dev/full", "wb") as full:
            proc = subprocess.run(
                [sys.executable, "-u", "-c", script],
                stdout=full, stderr=subprocess.PIPE,
                cwd=str(project), env=_env(), timeout=60,
            )
        assert proc.returncode == self.EXPECTED, proc.stderr.decode()
        assert "Traceback" not in proc.stderr.decode(), proc.stderr.decode()

    def test_premise_dev_full_passes_the_closed_stdout_gate(self, project):
        """PREMISE, and the reason this card is not a tail of CB-134.

        `_stdout_is_usable` must keep answering True here. At the process entry
        the descriptor is open, writable and perfectly valid — the failure is in
        the future. Widening that gate to catch this would be a different (and
        undecidable) question, and would break the 3.11-3.14 contracts it was
        measured against. If this assertion ever flips, CB-136's whole design
        rests on a premise that no longer holds.
        """
        script = (
            "import sys\n"
            "from codebugs.cli import _stdout_is_usable\n"
            "print('usable=%r' % _stdout_is_usable(), file=sys.stderr)\n"
        )
        with open("/dev/full", "wb") as full:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                stdout=full, stderr=subprocess.PIPE,
                cwd=str(project), env=_env(), timeout=60,
            )
        assert "usable=True" in proc.stderr.decode(), proc.stderr.decode()

    def test_premise_a_block_buffered_stdout_can_fail_inside_print(self, project):
        """PREMISE: case (A) is NOT a `-u` curiosity, so `_ClassifyingStdout` is
        not dead weight beside the flush.

        Measured on this interpreter: a SINGLE 50 KB `print` to a block-buffered
        failing stdout returns without raising (the failure waits for the
        shutdown flush — case B), but repeated small `print`s raise ENOSPC inside
        `print` as soon as the buffer fills — case A, reached with the DEFAULT
        buffering and no flag at all.
        """
        script = (
            "import sys\n"
            "raised = None\n"
            "for i in range(2000):\n"
            "    try:\n"
            "        print('z' * 100)\n"
            "    except OSError as e:\n"
            "        raised = e.errno; break\n"
            "print('raised=%r' % raised, file=sys.stderr)\n"
            "sys.stdout = None\n"
        )
        with open("/dev/full", "wb") as full:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                stdout=full, stderr=subprocess.PIPE,
                cwd=str(project), env=_env(), timeout=60,
            )
        assert "raised=28" in proc.stderr.decode(), proc.stderr.decode()

    def test_export_csv_to_dev_stdout_still_writes_in_place(self, project, tmp_path):
        """CB-76's held-open-inode branch must survive `sys.stdout` being wrapped.

        `fsio.atomic_write` recognises `/dev/stdout` by (st_dev, st_ino) against
        `/proc/self/fd` and writes IN PLACE rather than replacing, which is what
        keeps `export-csv /dev/stdout > out.csv` working. That detection never
        touches the `sys.stdout` OBJECT, so a proxy cannot reach it — but the
        claim is cheap to assert and expensive to rediscover.

        THIS DOCSTRING USED TO SAY the redirected file is not a pristine CSV —
        that the handler's own "Exported N findings" confirmation lands on top
        of the header, byte-identically on main and on this branch, and that
        asserting a clean header "would be asserting a repair nobody made."
        CB-143 is that repair: `atomic_write` now returns the SAME
        held-open-inode classification this test's own docstring describes, and
        both CLI export handlers use it to steer the confirmation to stderr
        precisely when the destination is that alias — never recomputing the
        classification a second time. So the file IS pristine now, and the
        confirmation is checked on stderr, not absent. `tests/test_fsio.py::
        TestCB143DiagnosticDoesNotCorruptTheFile` carries the dedicated
        real-file-redirect reproduction and the two required mutants; this test
        stays what CB-136 wrote it as — a regression guard that the
        held-open-inode WRITE path keeps working — updated only where CB-143
        changed the observable contract.
        """
        conn = db.connect(str(project))
        try:
            from codebugs import findings

            for i in range(3):
                findings.add_finding(
                    conn, severity="low", category="test",
                    description=f"row {i} for the /dev/stdout export path",
                    file="x.py", new_category=True,
                )
        finally:
            conn.close()

        out = tmp_path / "out.csv"
        with open(out, "wb") as fh:
            proc = subprocess.run(
                [sys.executable, "-m", "codebugs.cli", "export-csv", "/dev/stdout"],
                stdout=fh, stderr=subprocess.PIPE,
                cwd=str(project), env=_env(), timeout=60,
            )
        assert proc.returncode == 0, proc.stderr.decode()
        lines = out.read_text().splitlines()
        assert sum(1 for line in lines if line.startswith("CB-")) == 3, lines
        assert lines[0].startswith("id,severity,category"), (
            f"CB-143: header must no longer be corrupted, got {lines[0]!r}"
        )
        assert not any("Exported" in line for line in lines), (
            "the confirmation must not land inside the data file any more"
        )
        assert "Exported 3 findings" in proc.stderr.decode(), proc.stderr.decode()

    def test_the_code_is_declared_in_claude_md(self):
        """The exit codes are an API for shell callers, and CLAUDE.md is where
        that list lives (`Claims module`, "Exit codes are the API for shell
        callers"). A code that exists only in the source is a contract nobody
        can look up — the same reason `141` was written down there by CB-78."""
        text = (_REPO_ROOT / "CLAUDE.md").read_text()
        assert str(cli._WRITE_FAILURE_EXIT) == "74"
        assert "74" in text
        assert "EX_IOERR" in text
