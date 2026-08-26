"""CB-76 — an export that fails must not destroy the export it replaces.

Both the helper (`codebugs.fsio.atomic_write`) and its two CLI consumers are
tested here rather than split across test_findings/test_reqs, because the card
is one mechanism and a helper-only suite can stay green while either handler
still calls bare `open` (a cross-model review finding).

THE THREE GROUPS ARE NOT DECORATION. Revision 1 of this card's plan claimed all
its checks "fail against the pre-fix code" and six of them passed today — the
vacuous-test pattern this repo's ledger tracks. So each class states which side
it can fail on:

  * `TestBaselineDefect`  — MUST fail at cd7d68a. Recorded runs in each docstring.
  * `TestFixGuards`       — CANNOT fail at cd7d68a, because the code path does
                            not exist there. They fail against a plausible WRONG
                            implementation of the fix; the mutation that kills
                            each is named.
  * `TestCompatibility`   — pass on BOTH sides by design. They pin behaviour the
                            change deliberately preserves (CLAUDE.md testing
                            rule: say so, or a reader cannot tell one from a
                            broken test).
"""

from __future__ import annotations

import csv
import errno
import os
import stat
import subprocess
import sys

import pytest

from codebugs import db, findings, fsio, reqs

PREVIOUS = "PREVIOUS GOOD EXPORT - 3 findings\n"


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def populated(tmp_project):
    conn = db.connect(tmp_project)
    findings.add_finding(
        conn, severity="high", category="bug", file="a.py", description="something broke", new_category=True
    )
    reqs.add_requirement(conn, req_id="FR-001", description="a requirement")
    conn.close()
    return tmp_project


class _FailingHandle:
    """Wraps a real handle and fails at a chosen point.

    Injected into BOTH seams — `builtins.open` (what the unfixed tree writes
    through) and `os.fdopen` (what the fixed tree writes through). CLAUDE.md's
    testing rule (c): a hook keyed on one seam gives a vacuous pass on the
    other, which is exactly how revision 1 would have produced a FALSE FAILURE
    against correct code.
    """

    def __init__(self, real, *, on_write: bool = False, on_close: bool = False):
        self._real = real
        self._on_write = on_write
        self._on_close = on_close

    def write(self, data):
        if self._on_write:
            raise OSError(errno.ENOSPC, "No space left on device")
        return self._real.write(data)

    def close(self):
        try:
            self._real.close()
        finally:
            if self._on_close:
                raise OSError(errno.ENOSPC, "No space left on device")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def fail_writes(monkeypatch):
    """Make every write-mode handle fail, through both seams."""

    def _install(*, on_write=False, on_close=False):
        real_open, real_fdopen = open, os.fdopen

        def fake_open(file, mode="r", *a, **kw):
            h = real_open(file, mode, *a, **kw)
            if "w" in mode:
                return _FailingHandle(h, on_write=on_write, on_close=on_close)
            return h

        def fake_fdopen(fd, mode="r", *a, **kw):
            h = real_fdopen(fd, mode, *a, **kw)
            if "w" in mode:
                return _FailingHandle(h, on_write=on_write, on_close=on_close)
            return h

        monkeypatch.setattr("builtins.open", fake_open)
        monkeypatch.setattr(os, "fdopen", fake_fdopen)

    return _install


def _run_cli(project, *args):
    return subprocess.run(
        [sys.executable, "-m", "codebugs.cli", "--tracker-root", project, *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
    )


def _call_cli(monkeypatch, project, *args) -> int:
    """Invoke the CLI in-process so injected failures reach it."""
    from codebugs import cli

    monkeypatch.setattr(sys, "argv", ["codebugs", "--tracker-root", project, *args])
    try:
        cli.main()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


# --------------------------------------------------------------------------
# Group A — must fail against the unfixed tree.
# --------------------------------------------------------------------------


class TestBaselineDefect:
    @pytest.mark.parametrize(
        ("command", "target"),
        [("export-csv", "x.csv"), ("reqs-export", "x.md")],
    )
    def test_unwritable_path_is_a_clean_error_not_a_traceback(self, populated, command, target):
        """Recorded run at cd7d68a: a raw FileNotFoundError traceback from
        findings.py:1934 / reqs.py:1047.

        `returncode == 1` is NOT the discriminator and is asserted only as a
        secondary condition: an uncaught traceback also exits 1. The CB-71
        sibling classes say exactly this (tests/test_findings.py:1616-1619) and
        revision 1 of this plan dropped the discipline three days later.
        """
        r = _run_cli(populated, command, f"/nonexistent-dir/{target}")
        assert "Traceback" not in r.stderr, r.stderr
        assert "codebugs:" in r.stderr, r.stderr
        assert f"/nonexistent-dir/{target}" in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr

    @pytest.mark.parametrize("command", ["export-csv", "reqs-export"])
    def test_a_failed_body_write_leaves_the_previous_export_intact(
        self, populated, tmp_path, monkeypatch, fail_writes, capsys, command
    ):
        """THE defect. Recorded run at cd7d68a: 34 bytes -> 0 bytes, and the
        OSError escaped as a traceback (tests/manual/repro_cb76_truncation.py
        reproduces it end to end)."""
        target = tmp_path / "export.out"
        target.write_text(PREVIOUS)
        fail_writes(on_write=True)

        rc = _call_cli(monkeypatch, populated, command, str(target))

        assert target.read_text() == PREVIOUS, "the previous export was destroyed"
        assert rc == 1
        out = capsys.readouterr()
        assert "codebugs:" in out.err
        assert "Exported" not in out.out, "a failed export must not report success"

    @pytest.mark.parametrize("command", ["export-csv", "reqs-export"])
    def test_a_failed_close_leaves_the_previous_export_intact(
        self, populated, tmp_path, monkeypatch, fail_writes, capsys, command
    ):
        """Distinct from the body-write case: ENOSPC and quota failures usually
        surface at flush/close, not at the first write (Codex). This is why the
        helper must close BEFORE it replaces — replacing first would install a
        file whose close then failed, while reporting failure."""
        target = tmp_path / "export.out"
        target.write_text(PREVIOUS)
        fail_writes(on_close=True)

        rc = _call_cli(monkeypatch, populated, command, str(target))

        assert target.read_text() == PREVIOUS, "the previous export was destroyed"
        assert rc == 1
        assert "Exported" not in capsys.readouterr().out

    @pytest.mark.parametrize("command", ["export-csv", "reqs-export"])
    def test_no_temp_file_survives_a_failed_export(
        self, populated, tmp_path, monkeypatch, fail_writes, command
    ):
        """Asserted TOGETHER with proof that a temp existed during the failure —
        alone this is green on a tree where the helper was never wired (both
        reviewers flagged the standalone version as a classic cannot-fail
        test). The `seen` list is the fixture assertion."""
        target = tmp_path / "export.out"
        target.write_text(PREVIOUS)

        seen: list[str] = []
        real_mkstemp = fsio.tempfile.mkstemp

        def spy(*a, **kw):
            fd, path = real_mkstemp(*a, **kw)
            seen.append(path)
            return fd, path

        monkeypatch.setattr(fsio.tempfile, "mkstemp", spy)
        fail_writes(on_write=True)

        _call_cli(monkeypatch, populated, command, str(target))

        assert seen, "no temp was ever created — the helper is not wired in"
        assert not any(os.path.exists(p) for p in seen), "a temp survived the failure"
        assert target.read_text() == PREVIOUS


# --------------------------------------------------------------------------
# Group B — fix-guards. Cannot fail at cd7d68a; kill a wrong fix.
# --------------------------------------------------------------------------


class TestFixGuards:
    def test_a_read_only_destination_is_still_refused(self, tmp_path):
        """Killed by the mutation "drop the os.access gate": `open(w)`
        authorizes on the FILE, `os.replace` on the DIRECTORY, so a naive
        implementation overwrites a file the old code refused. Cannot fail at
        cd7d68a, where `open(w)` refuses it for free."""
        target = tmp_path / "readonly.txt"
        target.write_text(PREVIOUS)
        target.chmod(0o444)

        with pytest.raises(PermissionError) as ei:
            with fsio.atomic_write(str(target)) as (f, _in_place):
                f.write("new")

        assert str(target) in str(ei.value), "the error must name the path the caller typed"
        assert target.read_text() == PREVIOUS

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_a_replace_failure_after_a_good_write_preserves_the_destination(
        self, tmp_path, monkeypatch
    ):
        """Killed by the mutation "unlink only on body exceptions". Revision 1's
        contract covered exceptions in the BODY only, so a replace failure —
        which happens after all the safe work — leaked the temp and reported a
        `.codebugs-export-XXXX` path the user never typed."""
        target = tmp_path / "export.txt"
        target.write_text(PREVIOUS)

        def boom(_src, _dst):
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        monkeypatch.setattr(fsio.os, "replace", boom)

        with pytest.raises(OSError) as ei:
            with fsio.atomic_write(str(target)) as (f, _in_place):
                f.write("new content")

        assert target.read_text() == PREVIOUS
        assert str(target) in str(ei.value), "must report the typed path, not the temp"
        assert not [n for n in os.listdir(tmp_path) if n.startswith(".codebugs-export-")]

    def test_a_symlink_cycle_refuses_instead_of_being_treated_as_missing(self, tmp_path):
        """Killed by the mutation `except OSError: st = None`. realpath() leaves
        a cycle unresolved and os.stat then raises ELOOP; classifying that as
        "missing" would take the atomic path and REPLACE the symlink."""
        a, b = tmp_path / "a", tmp_path / "b"
        a.symlink_to(b)
        b.symlink_to(a)

        with pytest.raises(OSError) as ei:
            with fsio.atomic_write(str(a)) as (f, _in_place):
                f.write("x")

        assert ei.value.errno == errno.ELOOP
        assert os.path.islink(a), "the symlink itself must survive"

    def test_an_inode_this_process_holds_open_is_written_in_place(self, tmp_path):
        """Killed by the mutation "classify by node kind alone". This is the
        /dev/stdout case: realpath('/dev/stdout') resolves to the redirect
        TARGET, which is an ordinary REGULAR file (measured), so a node-kind
        check never fires and os.replace would swap the inode out from under a
        still-open descriptor.

        Modelled here without /dev/stdout so the test does not depend on how
        pytest captures output: hold the destination open ourselves and assert
        the inode is preserved.
        """
        target = tmp_path / "held.txt"
        target.write_text(PREVIOUS)
        before = os.stat(target).st_ino

        with open(target, "a"):  # this process now holds the inode open
            with fsio.atomic_write(str(target)) as (f, dest):
                f.write("replacement")

        assert os.stat(target).st_ino == before, (
            "the inode was replaced while a descriptor still pointed at it"
        )
        assert target.read_text() == "replacement"
        assert dest.in_place is True, (
            "CB-143: a caller (export-csv/reqs-export) steers its post-write "
            "diagnostic off this exact flag — it must be True here or that "
            "diagnostic would go to the wrong stream"
        )

    def test_an_fd_alias_resolving_into_proc_is_written_through(self, populated):
        """Killed by the mutation "classify from os.stat alone".

        THE REGRESSION THIS BRANCH SHIPPED AND THE FOURTH REVIEW PASS CAUGHT.
        When stdout is a PIPE, realpath('/dev/stdout') is
        `/proc/<pid>/fd/pipe:[N]` — a name that does not exist — so os.stat
        raises FileNotFoundError, a stat-based classifier reads "new file to
        create", and mkstemp then tries to create inside /proc. Measured
        against both trees: main streams the CSV, the first draft of this fix
        returned `[Errno 2] No such file or directory: '/dev/stdout'`.

        Note the sibling test above covers the redirect-to-a-FILE case, where
        realpath yields an ordinary regular file instead. Two different
        resolutions of the same `/dev/stdout`, and neither check catches both.
        """
        r = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "--tracker-root", populated,
             "export-csv", "/dev/stdout"],
            capture_output=True,  # a pipe, which is the whole point
            text=True,
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )
        assert r.returncode == 0, r.stderr
        assert "id,severity,category" in r.stdout, r.stdout[:200]
        assert "No such file" not in r.stderr, r.stderr

    def test_a_directory_destination_reports_the_typed_path(self, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        with pytest.raises(IsADirectoryError) as ei:
            with fsio.atomic_write(str(d)) as (f, _in_place):
                f.write("x")
        assert str(d) in str(ei.value)
        assert not [n for n in os.listdir(tmp_path) if n.startswith(".codebugs-export-")]


# --------------------------------------------------------------------------
# Group C — compatibility pins. Green on BOTH sides, deliberately.
# --------------------------------------------------------------------------


class TestCompatibility:
    """These pin behaviour the change preserves. They pass against cd7d68a too;
    that is the point, and it is stated so nobody reads them as defect proofs."""

    def test_a_new_destination_gets_umask_mode_not_0600(self, tmp_path):
        """mkstemp creates 0600, so without the chmod a brand-new export would
        be private where `open(w)` made it umask-derived."""
        target = tmp_path / "fresh.txt"
        with fsio.atomic_write(str(target)) as (f, dest):
            f.write("x")
        assert dest.in_place is False, "a brand-new file is never written in place"
        old = os.umask(0)
        os.umask(old)
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o666 & ~old

    def test_an_existing_destination_keeps_its_mode(self, tmp_path):
        target = tmp_path / "kept.txt"
        target.write_text(PREVIOUS)
        target.chmod(0o640)
        with fsio.atomic_write(str(target)) as (f, dest):
            f.write("x")
        assert dest.in_place is False, "a plain regular file is replaced, not written in place"
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o640

    def test_a_symlink_destination_stays_a_symlink_and_its_target_receives_content(
        self, tmp_path
    ):
        real = tmp_path / "real.txt"
        real.write_text(PREVIOUS)
        link = tmp_path / "link.txt"
        link.symlink_to(real)

        with fsio.atomic_write(str(link)) as (f, dest):
            f.write("through the link")

        assert os.path.islink(link), "the link was replaced by a regular file"
        assert real.read_text() == "through the link"
        assert dest.in_place is False, "a symlink to a plain file is replaced through, not held-open"

    def test_a_fifo_destination_is_written_through_not_replaced(self, tmp_path):
        import threading

        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        received: list[str] = []

        def reader():
            with open(fifo) as r:
                received.append(r.read())

        t = threading.Thread(target=reader)
        t.start()
        with fsio.atomic_write(str(fifo)) as (f, dest):
            f.write("streamed")
        t.join(timeout=5)
        assert dest.in_place is True, "a FIFO is stream-like and must be written through"
        assert stat.S_ISFIFO(os.stat(fifo).st_mode), "the FIFO node was replaced"
        assert received == ["streamed"]

    def test_reqs_export_without_a_path_still_writes_to_stdout(self, populated, monkeypatch, capsys):
        rc = _call_cli(monkeypatch, populated, "reqs-export")
        assert rc == 0
        assert "FR-001" in capsys.readouterr().out

    def test_a_simulated_broken_pipe_on_the_stdout_branch_still_propagates(
        self, populated, monkeypatch, capsys
    ):
        """The naive version of this check — "stdout still works" — passes
        whether or not the branch was wrongly wrapped in `except OSError`
        (Codex). The discriminator is that a BrokenPipeError from the stdout
        write must NOT become `codebugs: ...` + exit 1.

        RENAMED AND RE-DOCUMENTED BY CB-78, because what it pins changed
        underneath it and the old name concealed that. This test INJECTS the
        exception by monkeypatching `print`; it does not use a real pipe. Since
        `cli.run` installs `SIG_DFL`, a REAL closed pipe no longer produces a
        `BrokenPipeError` anywhere in the process — it dies by signal (141)
        before Python sees `EPIPE`. So this exercises the handler's arm
        placement under an injected failure, which is still worth pinning (a
        future `except OSError` around the stdout branch would swallow a
        non-pipe stdout failure exactly the same way), but it is NOT evidence
        about what a closed pipe does in production. That is
        `tests/test_cli_signals.py`, which uses a real pipe and a real
        subprocess.

        It also calls `cli.main()` rather than `cli.run()`, so no signal
        disposition is installed and the injected exception is reachable at all
        — the split exists precisely so an in-process caller keeps Python's
        default behaviour.
        """
        real_print = print

        def exploding_print(*a, **kw):
            if kw.get("file") in (None, sys.stdout) and a and "FR-001" in str(a[0]):
                raise BrokenPipeError(errno.EPIPE, "Broken pipe")
            return real_print(*a, **kw)

        monkeypatch.setattr("builtins.print", exploding_print)
        with pytest.raises(BrokenPipeError):
            _call_cli(monkeypatch, populated, "reqs-export")

    def test_exported_bytes_are_identical_to_a_plain_open(self, populated, tmp_path, monkeypatch):
        """`newline=` and encoding parity across the handle change. The helper
        must not add `encoding=`: both `open()` and `os.fdopen()` take the
        locale default, and pinning one would desynchronise export from import
        on a non-UTF-8 host."""
        via_helper = tmp_path / "helper.csv"
        _call_cli(monkeypatch, populated, "export-csv", str(via_helper))

        conn = db.connect(populated)
        rows = findings.query_findings(conn, limit=100000)
        conn.close()
        assert rows["findings"], "fixture produced no rows to compare"

        via_plain = tmp_path / "plain.csv"
        import csv as _csv

        # The comparison writer derives its columns from the SAME declaration the
        # exporter uses. It used to hand-copy the list, which made it a third copy
        # of the schema and a false failure the day a column was added (CB-97) —
        # this test is about the file HANDLE, not the column set, and pinning the
        # schema here was testing something it never meant to.
        with open(via_plain, "w", newline="") as f:
            import json as _json

            w = _csv.DictWriter(f, fieldnames=list(findings._RESTORE_COLUMNS))
            w.writeheader()
            for x in rows["findings"]:
                row = {c: x.get(c) for c in findings._RESTORE_COLUMNS}
                row["tags"] = _json.dumps(x["tags"])
                row["meta"] = _json.dumps(x["meta"])
                w.writerow(row)

        assert via_helper.read_bytes() == via_plain.read_bytes()


class TestWriteCallSitesRatchet:
    """Prose cannot enforce prose — this repo's own first principle.

    CLAUDE.md now says a CLI handler writes files through `fsio.atomic_write`,
    never a bare `open(path, "w")`. That rule holds today, so this ratchet is
    green on landing and costs ~10 lines; without it the rule is one PR from a
    silent violation. Same shape as `TestOpenCallSitesRatchet` in
    test_db_infra.py and the BEGIN IMMEDIATE occurrence count in test_claims.py.

    `fsio.py` itself is the one sanctioned site: its in-place branch must call
    `open` directly, which is exactly what the rule routes everyone else away
    from.
    """

    def test_fsio_is_the_only_module_that_opens_a_file_for_writing(self):
        """Read by AST, not by regex.

        The first draft of this test grepped the source text and matched the
        phrase `open(path, "w")` inside three DOCSTRINGS of the very module it
        was policing — a ratchet that fails on prose is a ratchet nobody keeps.
        The AST sees calls, so comments and docstrings are invisible to it, and
        no line number is hardcoded.
        """
        import ast
        import pathlib

        src = pathlib.Path(__file__).resolve().parent.parent / "src" / "codebugs"
        offenders = []
        for py in sorted(src.rglob("*.py")):
            tree = ast.parse(py.read_text(), filename=str(py))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "open":  # os.fdopen is a different attr, correctly missed
                    continue
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if isinstance(mode, str) and set(mode) & set("wax"):
                    offenders.append(f"{py.relative_to(src)}:{node.lineno}")

        assert [o.split(":")[0] for o in offenders] == ["fsio.py"], (
            "a write-mode open() appeared outside fsio.atomic_write — route it "
            "through the helper (CB-76):\n" + "\n".join(offenders)
        )


class TestExportPayloadIsInHandBeforeTheOpen:
    """PREMISE PIN. The guard's safety rests on the payload being complete and
    the connection closed before the write starts — that is what makes an
    `except OSError` around the write incapable of laundering a post-commit
    failure into bad input (CB-15/CB-16). It is prose in both handlers today;
    this pins it, following tests/test_reqs.py's
    TestImportMarkdownReadIsEagerPremise.

    Break it — move `query_findings`/`export_markdown` inside the `with` — and
    this goes red instead of the lie shipping silently.
    """

    @pytest.mark.parametrize(
        ("module", "handler", "producer"),
        [(findings, "_cmd_export_csv", "query_findings"), (reqs, "_cmd_reqs_export", "export_markdown")],
    )
    def test_the_producer_runs_before_any_write_handle_is_opened(
        self, populated, tmp_path, monkeypatch, module, handler, producer
    ):
        order: list[str] = []
        real_producer = getattr(module, producer)
        real_atomic = fsio.atomic_write

        def spy_producer(*a, **kw):
            order.append("produce")
            return real_producer(*a, **kw)

        def spy_atomic(*a, **kw):
            order.append("open")
            return real_atomic(*a, **kw)

        monkeypatch.setattr(module, producer, spy_producer)
        monkeypatch.setattr(fsio, "atomic_write", spy_atomic)
        monkeypatch.setattr(module, "atomic_write", spy_atomic, raising=False)

        command = "export-csv" if module is findings else "reqs-export"
        _call_cli(monkeypatch, populated, command, str(tmp_path / "out.txt"))

        # The property is ORDERING, not the producer's call count: every "produce" must
        # precede the first "open". The assertion was `order[:2] == ["produce", "open"]`,
        # which also pinned the count — and went red when `export-csv` legitimately began
        # asking for the row total before fetching (CB-97), even though the payload was
        # still fully in hand before any handle opened. A premise pin that fails on a
        # change it does not care about teaches people to edit premise pins.
        assert "produce" in order and "open" in order, order
        assert order.index("open") > 0, order
        assert "produce" not in order[order.index("open") :], order


# --------------------------------------------------------------------------
# Group D — CB-143. The diagnostic printed AFTER a successful write must not
# land on the same channel as the data when the two are the same inode.
# --------------------------------------------------------------------------


class TestCB143DiagnosticDoesNotCorruptTheFile:
    """`codebugs export-csv /dev/stdout > out.csv` used to give rc 0, empty
    stderr, and a corrupted file. The mechanism: `atomic_write`'s held-open-
    inode branch writes the export through a FRESH open of the same path — a
    second, independent file offset on the same inode, starting at 0. The
    handler then printed its confirmation through `sys.stdout`, the INHERITED
    descriptor, whose offset is STILL 0 (nothing had ever been written through
    it) — so the confirmation landed at the start of the file just written and
    overwrote the header.

    A REAL file redirect, not a pipe, is required to reproduce this: a pipe
    has no offset, so two writers through it are simply sequential — the
    defect is invisible there (see `test_an_fd_alias_resolving_into_proc_is_
    written_through` above, which uses a pipe and is unaffected by this
    class). `_redirected_to_file` opens a real destination file and hands it
    to the child as fd 1, exactly what a shell's `>` does.

    THE ORACLE IS FILE VALIDITY, never "no diagnostic line in stdout" — that
    weaker check stays green even when the diagnostic vanished while the file
    stayed corrupt (a fix could route it nowhere and still pass a
    presence-only check). `csv.reader` over the written file is the check for
    export-csv; a markdown-header check plays the same role for reqs-export.
    """

    @staticmethod
    def _redirected_to_file(project, outfile, *args):
        with open(outfile, "wb") as out:
            return subprocess.run(
                [sys.executable, "-m", "codebugs.cli", "--tracker-root", str(project), *args],
                stdout=out,
                stderr=subprocess.PIPE,
                env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
            )

    def test_export_csv_to_dev_stdout_redirected_to_a_real_file_is_valid_csv(
        self, populated, tmp_path
    ):
        outfile = tmp_path / "out.csv"
        r = self._redirected_to_file(populated, outfile, "export-csv", "/dev/stdout")

        assert r.returncode == 0, r.stderr

        # THE ORACLE (§4 of the brief): file validity, asserted FIRST and
        # deliberately before the stderr check below. "No diagnostic in
        # stdout" is a WEAKER check that stays green even when the line just
        # vanished while the file stayed corrupt — it must not be what turns
        # this test red against mutant 1 (the diagnostic reverted to
        # unconditional stdout). Header corruption is.
        with open(outfile, newline="") as f:
            rows = list(csv.reader(f))
        assert rows, "the export produced no rows at all — the file is empty"
        assert rows[0] == list(findings._RESTORE_COLUMNS), f"header corrupted: {rows[0]!r}"
        assert len(rows) == 2, f"expected header + 1 finding, got {rows!r}"

        # The confirmation still happens — form (a) never drops it (that was
        # rejected as (b) in the brief) — it is simply steered off the data
        # channel and onto stderr, checked only once the file is known-good.
        assert b"Exported" in r.stderr, r.stderr

    def test_reqs_export_to_dev_stdout_redirected_to_a_real_file_is_intact(
        self, populated, tmp_path
    ):
        outfile = tmp_path / "out.md"
        r = self._redirected_to_file(populated, outfile, "reqs-export", "/dev/stdout")

        assert r.returncode == 0, r.stderr

        # THE ORACLE, first and deliberately before the stderr check — see the
        # sibling export-csv test's docstring for why the order is load-bearing.
        content = outfile.read_text()
        assert content.startswith("# Requirements"), f"markdown header corrupted: {content[:80]!r}"
        assert "Exported to" not in content, "the diagnostic line landed inside the data file"

        assert b"Exported" in r.stderr, r.stderr

    @pytest.mark.parametrize(
        ("command", "target_name"), [("export-csv", "plain.csv"), ("reqs-export", "plain.md")]
    )
    def test_an_ordinary_destination_still_confirms_on_stdout(
        self, populated, tmp_path, command, target_name
    ):
        """The other side of the boundary (§4, mutant 2): a destination that is
        NOT an alias of this process's own stdout must still print its
        confirmation to stdout. A fix that routed every export's diagnostic to
        stderr unconditionally would pass every test above and fail only this
        one."""
        outfile = tmp_path / target_name
        r = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "--tracker-root", str(populated),
             command, str(outfile)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )
        assert r.returncode == 0, r.stderr
        assert r.stderr == "", r.stderr
        assert "Exported" in r.stdout, (
            f"a plain-file export must still confirm on stdout, got stdout={r.stdout!r}"
        )
        assert outfile.exists() and outfile.stat().st_size > 0

    def test_export_csv_to_the_same_literal_path_as_its_own_redirect_stays_intact(
        self, populated, tmp_path
    ):
        """Oracle row 6 (`export-csv out.csv > out.csv`): the destination
        ARGUMENT is the same literal path the shell redirected stdout to, not
        a `/dev/stdout` alias — a second way to reach the identical
        held-open-inode branch `test_an_inode_this_process_holds_open_is_
        written_in_place` exercises directly on `fsio`. This is a Group-C-
        style guard: unaffected by CB-194 and must stay green through it,
        because today's classification here is already correct (stderr) and
        must not move."""
        outfile = tmp_path / "out.csv"
        r = self._redirected_to_file(populated, outfile, "export-csv", str(outfile))
        assert r.returncode == 0, r.stderr

        with open(outfile, newline="") as f:
            rows = list(csv.reader(f))
        assert rows, "the export produced no rows at all — the file is empty"
        assert rows[0] == list(findings._RESTORE_COLUMNS), f"header corrupted: {rows[0]!r}"

        assert b"Exported" in r.stderr, r.stderr


# --------------------------------------------------------------------------
# Group E — CB-194. `atomic_write` used to return a single `in_place` bit,
# and both CLI export handlers steered their confirmation to "stderr if
# in_place else stdout" without ever asking whether STDERR ITSELF was the
# destination. Two of the nine oracle rows (§5 of the brief) therefore
# corrupted the file: row 7 (only stderr aliases the destination — the
# handler picked stderr anyway) and rows 8/9 (both streams alias it, so
# EITHER choice corrupts). MUST fail against the pre-fix tree; see each
# docstring for the exact reproduction this repository measured before the
# fix (Python 3.14, this host).
# --------------------------------------------------------------------------


class TestCB194BothStreamsCanAliasTheDestination:
    """Oracle rows 7, 8, 9. Rows 1-6 are guarded above and deliberately not
    repeated here — this class exists for the three rows that were wrong."""

    @staticmethod
    def _run_single_redirect(project, *args, stderr_path):
        """`2> out.csv`, stdout left as a pipe so it can be inspected —
        mirrors oracle row 7 exactly: only fd 2 is redirected to a file."""
        with open(stderr_path, "wb") as errf:
            return subprocess.run(
                [sys.executable, "-m", "codebugs.cli", "--tracker-root", str(project), *args],
                stdout=subprocess.PIPE,
                stderr=errf,
                env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
            )

    @staticmethod
    def _run_combined_redirect(project, *args, combined_path):
        """`> out.csv 2>&1`: ONE file object handed to BOTH stdout and
        stderr, so the subprocess module dup2's both from the SAME
        underlying fd — sharing one file OFFSET, exactly like the shell
        construct it reproduces. Passing two SEPARATE opens of the same path
        would give each its own offset and hide exactly the defect this
        class exists to catch."""
        with open(combined_path, "wb") as f:
            return subprocess.run(
                [sys.executable, "-m", "codebugs.cli", "--tracker-root", str(project), *args],
                stdout=f,
                stderr=f,
                env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
            )

    def test_export_csv_to_dev_stderr_redirected_to_a_file_confirms_on_stdout(
        self, populated, tmp_path
    ):
        """Row 7: `export-csv /dev/stderr 2> out.csv`. Measured on the
        pre-fix tree: rc=0, the file BEGINS with
        `Exported 0 findings to /dev/stderr\\nscription,source,tags,...` —
        the confirmation, chosen unconditionally by `in_place`, landed on the
        one channel that IS the destination and clipped the header's first
        eight characters (`id,de` of `id,description,...`). The fix must
        route the confirmation to stdout instead — the one channel here that
        is NOT an alias of the destination.
        """
        outfile = tmp_path / "out.csv"
        r = self._run_single_redirect(
            populated, "export-csv", "/dev/stderr", stderr_path=outfile
        )
        assert r.returncode == 0, r.stderr

        with open(outfile, newline="") as f:
            rows = list(csv.reader(f))
        assert rows, "the export produced no rows at all — the file is empty"
        assert rows[0] == list(findings._RESTORE_COLUMNS), f"header corrupted: {rows[0]!r}"

        assert b"Exported" in r.stdout, (
            "stderr IS the destination here, so the confirmation has nowhere "
            f"to go but stdout — got stdout={r.stdout!r}"
        )

    def test_reqs_export_to_dev_stderr_redirected_to_a_file_confirms_on_stdout(
        self, populated, tmp_path
    ):
        """Row 7's twin on the OTHER export surface (§6.2 of the brief: a
        test on one surface is not coverage of the other — `reqs.py`
        duplicates the same channel-selection logic `findings.py` does, in
        its own call site)."""
        outfile = tmp_path / "out.md"
        r = self._run_single_redirect(
            populated, "reqs-export", "/dev/stderr", stderr_path=outfile
        )
        assert r.returncode == 0, r.stderr

        content = outfile.read_text()
        assert content.startswith("# Requirements"), f"markdown header corrupted: {content[:80]!r}"
        assert "Exported to" not in content, "no confirmation text may land inside the file"

        assert b"Exported" in r.stdout, (
            "stderr IS the destination here, so the confirmation has nowhere "
            f"to go but stdout — got stdout={r.stdout!r}"
        )

    def test_export_csv_to_dev_stdout_combined_with_stderr_prints_nothing(
        self, populated, tmp_path
    ):
        """Row 8: `export-csv /dev/stdout > out.csv 2>&1`. BOTH channels are
        the destination, so CB-194 §5 requires silence — there is no third
        channel, and any byte printed lands inside the exported file itself.
        Measured on the pre-fix tree: rc=0, a header corrupted the same way
        as row 7's.
        """
        outfile = tmp_path / "out.csv"
        r = self._run_combined_redirect(
            populated, "export-csv", "/dev/stdout", combined_path=outfile
        )
        assert r.returncode == 0

        with open(outfile, newline="") as f:
            rows = list(csv.reader(f))
        assert rows, "the export produced no rows at all — the file is empty"
        assert rows[0] == list(findings._RESTORE_COLUMNS), f"header corrupted: {rows[0]!r}"
        assert not any("Exported" in ",".join(row) for row in rows), (
            "no confirmation text may appear anywhere in the file — there is "
            "no channel left that is not the destination"
        )

    def test_reqs_export_to_dev_stderr_combined_with_stdout_prints_nothing(
        self, populated, tmp_path
    ):
        """Row 9's shape on the OTHER export surface: `reqs-export
        /dev/stderr > out.md 2>&1`. Both streams alias the destination via
        the same dup'd fd, so silence is required here too — and this is the
        surface CB-143's own fix left un-fixed for CB-194's exact defect
        class (§1 of the brief)."""
        outfile = tmp_path / "out.md"
        r = self._run_combined_redirect(
            populated, "reqs-export", "/dev/stderr", combined_path=outfile
        )
        assert r.returncode == 0

        content = outfile.read_text()
        assert content.startswith("# Requirements"), f"markdown header corrupted: {content[:80]!r}"
        assert "Exported to" not in content, "no confirmation text may land inside the file"
