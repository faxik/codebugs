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

import os
import pathlib
import signal
import subprocess
import sys
import tomllib

import pytest

from codebugs import cli, db

pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGPIPE"), reason="SIGPIPE does not exist on this platform"
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    """A clean environment for a CLI subprocess.

    `PYTHONUNBUFFERED` is removed rather than left to chance: it would silently
    turn the block-buffered case into the unbuffered one, collapsing the two
    parametrised runs into the same experiment while both still passed.
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
            project, "add", "-f", "x.py", "-c", "test", "-s", "low", "-d", desc,
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
        conn = db.connect(str(project))
        try:
            from codebugs import findings

            for i in range(60):
                findings.add_finding(
                    conn, severity="low", category="test",
                    description=f"row {i} " + "padding " * 40, file="x.py",
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
        cli.main()
        assert signal.getsignal(signal.SIGPIPE) is before

    def test_run_is_the_only_place_the_disposition_is_installed(self):
        """Structural, and deliberately so: a behavioural test of `run` would have
        to install SIG_DFL in the pytest process to observe it, which is the very
        thing the test above forbids."""
        import inspect

        source = pathlib.Path(inspect.getfile(cli)).read_text()
        assert source.count("signal.signal(") == 1, (
            "the SIGPIPE disposition must be installed in exactly one place; "
            "a second site is how the two entry points start disagreeing"
        )
        assert "signal.signal(" in inspect.getsource(cli.run)
        assert "signal.signal(" not in inspect.getsource(cli.main)


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
