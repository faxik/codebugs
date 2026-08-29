"""CB-215 oracle: a run that judged two different trees says so, and otherwise is silent.

The suite is re-run by an acceptor in the MAIN checkout — the one place other
directions land their branches. Structural tests here read source files from
disk, so a merge arriving mid-run produces a partial red that is indistinguishable
from a real regression, and the reader goes off to debug code that is fine.

This unit does not fix that race; it cannot be fixed from inside a test suite.
It stops the run being SILENT about it. So the oracle has two halves and the
second is the one that is easy to lose:

  * the tree moved   → the summary says so, names the paths, and changes NOTHING
                       about the exit status
  * the tree is still → not one line, not an empty header

HOW MANY WAYS CAN A TREE MOVE UNDER A RUN? At least ten were named while this
was written: a neighbouring merge landing on `main`, `git checkout`, `git pull`,
`git stash` and `git stash pop`, resolving a conflict, an editor save, a
formatter, a code generator, and another agent's own edit. They are NOT covered
one by one, because an enumeration is the letter and the letter cannot decide —
this repository's most-repeated lesson. All ten reach the fingerprint as exactly
three observable primitives: a path APPEARS, a path DISAPPEARS, or a path's
(size, mtime) CHANGES. All three are covered end to end below, against the real
`tests/conftest.py` loaded by a real pytest session.

TESTS THAT TOUCH THE REAL TREE ARE NET-ZERO FOR ONE PROCESS AT A TIME, and that
qualification is the whole of CB-258. Each such row creates its own probe in the
live tree, lets the inner run move it, and removes it in `finally`, so by the
time the OUTER run takes its second sample nothing this module did is left to
see. That is not tidiness: mutating a tracked file's mtime here would make the
full suite set off this very alarm on every run, and an alarm that always fires
is one nobody reads by the second week.

The sentence above used to end "BY CONSTRUCTION", with no qualification, and it
was wider than its measurement — the defect class this file is itself about. It
holds for the run that OWNS the probe and fails for any OTHER reader of the same
tree, which is an ordinary state here rather than an exotic one: this suite is
re-run in the main checkout while branches land on it, and one row below copies
the live tree wholesale. A second reader overlapping a probe's window sees a
path appear or disappear and prints this very alarm.

ONE PROBE WAS WORSE THAN THE REST IN KIND RATHER THAN IN DEGREE, and it is the
one that has moved out. The CB-226 row needs a directory that CANNOT BE LISTED,
and an unreadable directory does not merely move a second reader's fingerprint:
it makes the second reader FAIL. Both halves were built and measured on a copy
of this tree, with this file as it stood — `shutil.copytree` of the live tree
died with `shutil.Error [Errno 13] Permission denied`, which is exactly what the
neighbouring row does to read the tree; and a second pytest run overlapping the
window reported `tests/_cb226_probe_blinddir — could not be listed (Permission
denied)`, turning all three rows of "silent on a still tree" red. So that row
now builds its own throwaway COPY of the tree and keeps the unreadable directory
inside it, where nothing else will ever walk.

WHAT IS STILL TRUE AND WHAT IS NOT, said rather than quietly widened. The other
probes are ordinary FILES, readable by anyone, and a concurrent second reader
still sees them appear and disappear; making this module safe for two concurrent
readers is CB-255's subject and not a promise made here. What this file
guarantees is narrower and checkable: it leaves nothing in the live tree that
another reader cannot READ.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import (
    _PRUNED_NAMES,
    _PRUNED_PATHS,
    _head_signature,
    _tree_difference,
    _tree_fingerprint,
    _tree_moved_report,
    _TREE_MOVED_ANCHOR,
    _TREE_MOVED_LIMIT,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# A cheap real node that touches no database, so a red row tells us about the
# alarm rather than about the canary.
CANARY = "tests/test_types.py::TestConstants::test_terminal_statuses_keys"

# Three DIFFERENT selections, because "silent" has to hold over a non-empty set
# of runs rather than over the single one that happened to be tried.
QUIET_SELECTIONS = [
    "tests/test_types.py",
    "tests/test_entities.py",
    CANARY,
]

# The plugin runs INSIDE the inner pytest and moves the tree exactly once, while
# a test is executing — after the session fixture has taken its first sample and
# well before the terminal summary takes the second.
_MUTATOR = """
import os

_fired = False


def pytest_runtest_call(item):
    global _fired
    if _fired:
        return
    _fired = True
    {action}
"""


def _mutator(tmp_path, action):
    """Write the mid-run mutation plugin and return the directory to import it from."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "cb215_mutator.py").write_text(_MUTATOR.format(action=action))
    return plugin_dir


def _inner_pytest(basetemp, *, selection=CANARY, plugin_dir=None, cwd=REPO_ROOT):
    """Run one real pytest session against a real tree, optionally moving it mid-run."""
    env = {**os.environ}
    argv = [sys.executable, "-m", "pytest", selection, "-q", "--basetemp", str(basetemp)]
    if plugin_dir is not None:
        # PYTHONPATH rather than the `pythonpath` ini key: `-p` is resolved at
        # interpreter start, before any ini setting has been applied.
        env["PYTHONPATH"] = str(plugin_dir)
        argv += ["-p", "cb215_mutator"]
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, env=env)


def _output(proc):
    return proc.stdout + proc.stderr


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


# What a throwaway copy deliberately does not carry. This is NOT `_PRUNED_NAMES`
# and is deliberately not derived from it: pruning says what the fingerprint must
# not JUDGE, this says what a copy need not CARRY, and the two are one edit from
# disagreeing about `.git` — pruned from the fingerprint, and required to be
# ABSENT from the copy, because the row below asserts a tree with no git at all
# still works.
_NOT_COPIED = (".git", ".venv", ".worktrees", "__pycache__", ".pytest_cache", ".codebugs")


def _throwaway_tree_copy(tmp_path, name):
    """A disposable copy of the live tree — the only place a probe may be left.

    Two rows need a real tree carrying this repository's real `conftest.py`, and
    both leave a probe lying in that tree while an inner pytest session runs. See
    the module docstring for what a probe left in the LIVE tree does to a second
    reader, and why an unreadable one is worse in kind than the rest.

    The copy is made from the WORKING tree rather than with `git archive`,
    deliberately: an archive is made from `HEAD`, so on a branch whose
    `conftest.py` is not yet committed these rows would exercise the previous
    version of the very code under test and pass vacuously.

    Cost was measured before the live tree was given up, because "it is cheaper"
    is the argument that would otherwise have kept the probe where it was: 466
    files, 17 MB, 0.08s per copy against an inner pytest session of ~2.5s.
    """
    copy = tmp_path / name
    shutil.copytree(REPO_ROOT, copy, ignore=shutil.ignore_patterns(*_NOT_COPIED))
    return copy


class TestTheAlarmSpeaksWhenTheTreeMoves:
    """The three primitives, each end to end through a real pytest session."""

    def test_a_path_that_appears_mid_run_is_named(self, tmp_path):
        probe = REPO_ROOT / "tests" / "_cb215_probe_added.tmp"
        plugin_dir = _mutator(tmp_path, f"open({str(probe)!r}, 'w').write('probe\\n')")
        try:
            proc = _inner_pytest(tmp_path / "bt", plugin_dir=plugin_dir)
        finally:
            probe.unlink(missing_ok=True)
        out = _output(proc)
        assert _TREE_MOVED_ANCHOR in out, out[-3000:]
        assert "added" in out and "_cb215_probe_added.tmp" in out, out[-3000:]

    def test_a_path_that_disappears_mid_run_is_named(self, tmp_path):
        probe = REPO_ROOT / "tests" / "_cb215_probe_removed.tmp"
        probe.write_text("probe\n")
        plugin_dir = _mutator(tmp_path, f"os.remove({str(probe)!r})")
        try:
            proc = _inner_pytest(tmp_path / "bt", plugin_dir=plugin_dir)
        finally:
            probe.unlink(missing_ok=True)
        out = _output(proc)
        assert _TREE_MOVED_ANCHOR in out, out[-3000:]
        assert "removed" in out and "_cb215_probe_removed.tmp" in out, out[-3000:]

    def test_a_path_whose_timestamp_alone_moves_is_named(self, tmp_path):
        """MTIME ALONE has to be enough, and this row is what says so.

        The probe's SIZE is identical on both samples — the file is not written,
        only stamped — so nothing but the modification time separates the two.
        That is the same shape as a `git checkout` restoring a file to a version
        of the same length, and it is why the fingerprint carries `mtime_ns`
        rather than a size and a name.
        """
        probe = REPO_ROOT / "tests" / "_cb215_probe_changed.tmp"
        probe.write_text("probe\n")
        plugin_dir = _mutator(tmp_path, f"os.utime({str(probe)!r})")
        try:
            proc = _inner_pytest(tmp_path / "bt", plugin_dir=plugin_dir)
        finally:
            probe.unlink(missing_ok=True)
        out = _output(proc)
        assert _TREE_MOVED_ANCHOR in out, out[-3000:]
        assert "changed" in out and "_cb215_probe_changed.tmp" in out, out[-3000:]

    def test_a_note_landing_in_the_plans_directory_is_named(self, tmp_path):
        """`.claude/plans/` IS WATCHED, and this is the row that keeps it watched.

        Excluding it is the tempting judgement call — notes land there all day
        and every one of them will set this off. It is also measurably wrong:
        `tests/test_exposure_matrix.py` reads `.claude/plans/exposure-scripts/`
        out of the real tree, so "the suite does not look there" is exactly the
        unchecked premise this alarm exists to stop people acting on.
        """
        probe = REPO_ROOT / ".claude" / "plans" / "_cb215_probe_note.md"
        plugin_dir = _mutator(tmp_path, f"open({str(probe)!r}, 'w').write('probe\\n')")
        try:
            proc = _inner_pytest(tmp_path / "bt", plugin_dir=plugin_dir)
        finally:
            probe.unlink(missing_ok=True)
        out = _output(proc)
        assert _TREE_MOVED_ANCHOR in out, out[-3000:]
        assert "_cb215_probe_note.md" in out, out[-3000:]


class TestTheAlarmIsAnAlarmAndNotAGate:
    def test_the_exit_status_of_a_passing_run_is_untouched(self, tmp_path):
        """A moved tree is ordinary traffic; refusing over it would be a new false red.

        The canary passes, the tree moves, and the run must still exit 0 with
        the report printed. A mutant that turns the report into a refusal —
        `pytest.exit`, a rewritten `session.exitstatus` — turns this row red.
        """
        probe = REPO_ROOT / "tests" / "_cb215_probe_rc.tmp"
        plugin_dir = _mutator(tmp_path, f"open({str(probe)!r}, 'w').write('probe\\n')")
        try:
            proc = _inner_pytest(tmp_path / "bt", plugin_dir=plugin_dir)
        finally:
            probe.unlink(missing_ok=True)
        out = _output(proc)
        assert _TREE_MOVED_ANCHOR in out, out[-3000:]
        assert proc.returncode == 0, f"rc={proc.returncode}\n{out[-3000:]}"

    def test_the_report_says_in_words_that_it_changed_nothing(self):
        report = _tree_moved_report([("changed", "src/codebugs/db.py")], "aaaaaaa", "bbbbbbb")
        assert "EXIT STATUS OF THIS RUN HAS NOT BEEN TOUCHED" in report


class TestTheAlarmIsSilentOnAStillTree:
    """Half the value of this unit is here — an alarm that always fires is unread."""

    @pytest.mark.parametrize("selection", QUIET_SELECTIONS)
    def test_a_run_that_moved_nothing_prints_nothing(self, tmp_path, selection):
        proc = _inner_pytest(tmp_path / "bt", selection=selection)
        out = _output(proc)
        assert proc.returncode == 0, out[-3000:]
        assert _TREE_MOVED_ANCHOR not in out, out[-3000:]
        # Not even a header, an empty section or a "0 paths" line.
        assert "codebugs test suite: THE TREE" not in out
        assert "path(s) differ" not in out

    def test_an_unmoved_fingerprint_yields_no_changes(self):
        snapshot = _tree_fingerprint(REPO_ROOT)
        assert _tree_difference(snapshot, dict(snapshot)) == []


class TestTheDiscriminatorIsTheFilesAndNotTheCommitName:
    def test_a_tree_with_no_git_at_all_still_works_and_prints_no_signature(self, tmp_path):
        """The git-less copy: no exception, no false alarm, and the fingerprint still bites.

        This row is ALSO the one CB-258 was reported through: it reads the live
        tree with `shutil.copytree`, so any other run leaving an unreadable
        directory there killed it outright with `Permission denied`. Its own
        probe stays inside the copy, which is why it was the victim and never
        the cause.
        """
        copy = _throwaway_tree_copy(tmp_path, "nogit")
        assert not (copy / ".git").exists()
        assert _head_signature(copy) is None

        quiet = _inner_pytest(tmp_path / "bt-quiet", cwd=copy)
        quiet_out = _output(quiet)
        assert quiet.returncode == 0, quiet_out[-3000:]
        assert _TREE_MOVED_ANCHOR not in quiet_out, quiet_out[-3000:]
        assert "Traceback" not in quiet_out, quiet_out[-3000:]

        probe = copy / "tests" / "_cb215_probe_nogit.tmp"
        plugin_dir = _mutator(tmp_path, f"open({str(probe)!r}, 'w').write('probe\\n')")
        moved = _inner_pytest(tmp_path / "bt-moved", plugin_dir=plugin_dir, cwd=copy)
        moved_out = _output(moved)
        assert _TREE_MOVED_ANCHOR in moved_out, moved_out[-3000:]
        assert "_cb215_probe_nogit.tmp" in moved_out, moved_out[-3000:]
        assert moved.returncode == 0, moved_out[-3000:]
        # The signature is simply absent — it never becomes a failure.
        assert "HEAD at the start" not in moved_out, moved_out[-3000:]

    def test_main_moving_under_a_worktree_is_silent_while_an_edit_is_not(self, tmp_path):
        """The separating case, and the reason `HEAD` is not the discriminator.

        A merge landing on `main` does not touch a linked worktree's files, so a
        run there must stay silent — the fingerprint is, because nothing in that
        directory moved. The second half is the mirror image: an ordinary
        uncommitted edit moves the fingerprint while `HEAD` does not move at all,
        which is the case a commit-name discriminator would be blind to.
        """
        origin = tmp_path / "origin"
        origin.mkdir()
        _git("init", "-b", "main", cwd=origin)
        (origin / "kept.txt").write_text("one\n")
        _git("add", "-A", cwd=origin)
        _git("commit", "-m", "first", cwd=origin)

        worktree = tmp_path / "wt"
        _git("worktree", "add", "-b", "side", str(worktree), cwd=origin)

        before = _tree_fingerprint(worktree)
        head_before = _head_signature(worktree)

        (origin / "landed.txt").write_text("a neighbouring unit\n")
        _git("add", "-A", cwd=origin)
        _git("commit", "-m", "a merge lands on main", cwd=origin)

        assert _tree_difference(before, _tree_fingerprint(worktree)) == []

        (worktree / "kept.txt").write_text("two\n")
        assert _tree_difference(before, _tree_fingerprint(worktree)) == [("changed", "kept.txt")]
        assert _head_signature(worktree) == head_before

    def test_premise_an_ordinary_merge_moves_the_modification_time(self, tmp_path):
        """§4(b): the premise the whole form rests on, measured rather than assumed.

        The branch rewrites the file to content of the SAME LENGTH, so size
        cannot be what separates the two samples. If a future git, or a
        filesystem with a coarser clock, stopped moving `mtime` here, this row
        goes red instead of the alarm quietly going blind.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", "-b", "main", cwd=repo)
        target = repo / "a.txt"
        target.write_text("aaaa\n")
        _git("add", "-A", cwd=repo)
        _git("commit", "-m", "first", cwd=repo)

        _git("checkout", "-b", "side", cwd=repo)
        target.write_text("bbbb\n")
        _git("commit", "-am", "same length, different content", cwd=repo)
        _git("checkout", "main", cwd=repo)

        before = os.lstat(target)
        _git("merge", "--no-ff", "-m", "merge side", "side", cwd=repo)
        after = os.lstat(target)

        assert after.st_size == before.st_size
        assert after.st_mtime_ns != before.st_mtime_ns


class TestTheReportNamesPathsWithoutJudgingThem:
    def test_every_kind_of_difference_is_reported(self):
        before = {"kept": (1, 10), "gone": (1, 10), "same": (1, 10)}
        after = {"kept": (1, 20), "new": (1, 10), "same": (1, 10)}
        assert _tree_difference(before, after) == [
            ("removed", "gone"),
            ("changed", "kept"),
            ("added", "new"),
        ]

    def test_a_file_that_became_unreadable_counts_as_a_change(self):
        assert _tree_difference({"x": (1, 10)}, {"x": "unreadable (PermissionError)"}) == [
            ("changed", "x")
        ]

    def test_a_long_list_is_capped_and_the_remainder_counted(self):
        changes = [("changed", f"f{i:03d}") for i in range(_TREE_MOVED_LIMIT + 7)]
        report = _tree_moved_report(changes, None, None)
        assert "... and 7 more" in report
        assert f"{len(changes)} path(s) differ" in report
        assert "f000" in report
        assert f"f{_TREE_MOVED_LIMIT + 6:03d}" not in report

    def test_the_signature_is_printed_when_git_answers_and_omitted_when_it_does_not(self):
        with_git = _tree_moved_report([("changed", "x")], "aaaaaaa", "bbbbbbb")
        assert "HEAD at the start: aaaaaaa" in with_git and "HEAD now: bbbbbbb" in with_git
        without_git = _tree_moved_report([("changed", "x")], None, None)
        assert "HEAD" not in without_git

    def test_no_path_is_sorted_into_important_and_unimportant(self):
        """Property 4: the list is what was found, in one order, with one verb each."""
        report = _tree_moved_report(
            [("changed", "src/codebugs/db.py"), ("added", ".claude/plans/note.md")], None, None
        )
        assert "src/codebugs/db.py" in report and ".claude/plans/note.md" in report
        for word in ("important", "ignore", "harmless", "probably"):
            assert word not in report.lower()


class TestTheAlarmNamesADirectoryItCannotList:
    """CB-226 oracle item 1, built exactly as the brief's §4 П1 demands.

    A directory `chmod 0300` BEFORE the run starts is absent from the "before"
    fingerprint AND from the "after" one — `os.walk`'s default behaviour
    swallows the listing failure — so the diff between two identical absences
    is empty. Before this unit, a file rewritten to a different length INSIDE
    that directory, while it stayed unlistable throughout, produced NOTHING in
    the terminal summary: this is the row that used to pass silently over a
    tree that had, in fact, changed under it in a way the run could not see.

    THE UNREADABLE DIRECTORY LIVES IN A THROWAWAY COPY, NEVER IN THE LIVE TREE
    (CB-258). What this row needs is a real tree with a real `conftest.py` in
    it, and a copy is one; what it must not do is leave a directory nobody else
    can list in the tree every other reader is walking. The module docstring
    carries the two measurements that decided it.
    """

    def test_an_unlistable_directory_is_named_and_the_run_is_not_reported_clean(self, tmp_path):
        copy = _throwaway_tree_copy(tmp_path, "blinddir")
        probe_dir = copy / "tests" / "_cb226_probe_blinddir"
        # CB-258, asserted rather than trusted to the line above: a mutant that
        # puts the probe back in the live tree turns this row red HERE, before
        # it can break anybody else's run.
        assert not probe_dir.resolve().is_relative_to(REPO_ROOT), probe_dir
        probe_file = probe_dir / "f.py"
        probe_dir.mkdir()
        probe_file.write_text("original\n")
        plugin_dir = _mutator(
            tmp_path,
            f"open({str(probe_file)!r}, 'w').write("
            "'a considerably longer replacement body than before\\n')",
        )
        os.chmod(probe_dir, 0o300)
        try:
            proc = _inner_pytest(tmp_path / "bt", plugin_dir=plugin_dir, cwd=copy)
        finally:
            # Restored, never removed: the whole copy is disposable, but pytest
            # deletes old `--basetemp` trees itself, and a 0300 directory left
            # behind makes that deletion fail long after this run is over.
            os.chmod(probe_dir, 0o700)
        out = _output(proc)
        assert proc.returncode == 0, out[-3000:]
        assert _TREE_MOVED_ANCHOR in out, out[-3000:]
        assert "_cb226_probe_blinddir" in out, out[-3000:]
        assert "could not be listed" in out, out[-3000:]


class TestTheTruncationTailNamesEveryTopLevelDirectory:
    """CB-226 oracle item 2: heterogeneous paths, built as the brief's §5 demands.

    The old tail let the alphabet decide what survives a truncated list: with
    changes under `.claude/plans`, `src/` and `tests/` all present and more of
    them than the limit, the dot in `.claude` sorted every one of its entries
    ahead of the other two, and the file that actually mattered — the one
    under `src/` — vanished with no trace anywhere in the report.
    """

    def test_no_top_level_directory_disappears_under_truncation(self):
        changes = [("changed", f".claude/plans/note{i:02d}.md") for i in range(25)]
        changes.append(("changed", "src/codebugs/db.py"))
        changes.append(("changed", "tests/conftest.py"))
        changes.sort(key=lambda t: t[1])
        assert len(changes) > _TREE_MOVED_LIMIT

        report = _tree_moved_report(changes, None, None)

        assert "src/codebugs/db.py" not in report, "the itemised list is still capped"
        assert "tests/conftest.py" not in report, "same cap, same reason"
        assert ".claude (25)" in report, "but the DIRECTORY must survive the cap"
        assert "src (1)" in report, "the one that used to vanish entirely"
        assert "tests (1)" in report

    def test_a_homogeneous_flat_list_keeps_the_old_bare_tail(self):
        """The pre-existing oracle row, unaffected: flat names have no directory.

        `_top_level_directory` returns `None` for a path with no `os.sep`, so a
        list of bare filenames — this repo's own existing fixture shape — gets
        no per-directory breakdown at all, and the tail is exactly what it was.
        """
        changes = [("changed", f"f{i:03d}") for i in range(_TREE_MOVED_LIMIT + 7)]
        report = _tree_moved_report(changes, None, None)
        assert "... and 7 more" in report
        assert f"f{_TREE_MOVED_LIMIT + 6:03d}" not in report


class TestTheReportNamesUnexaminedDirectories:
    """CB-226: the `unexamined` channel, exercised directly on the pure function."""

    def test_unexamined_directories_appear_even_with_no_itemised_changes(self):
        report = _tree_moved_report(
            [],
            None,
            None,
            (("src/blinddir", "could not be listed (Permission denied)"),),
        )
        assert "src/blinddir" in report
        assert "could not be listed" in report
        assert "0 path(s) differ" not in report

    def test_no_unexamined_places_prints_nothing_about_them(self):
        report = _tree_moved_report([("changed", "x")], None, None)
        assert "could not be listed" not in report
        assert "could not be LISTED" not in report


class TestNothingIsPrunedByJudgement:
    def test_every_pruned_entry_carries_its_reason(self):
        for table in (_PRUNED_NAMES, _PRUNED_PATHS):
            for key, reason in table.items():
                assert isinstance(reason, str) and len(reason.split()) >= 5, key

    def test_the_directories_the_suite_actually_reads_are_not_pruned(self):
        """A structural pin beside the behavioural row above, for the same rule.

        `.claude/plans` is read by `test_exposure_matrix.py`, `.github` by the
        CI-invariant test, `tests/golden` by the wire golden. None of them may
        acquire an exclusion by anyone's judgement about relevance.
        """
        pruned = set(_PRUNED_NAMES) | set(_PRUNED_PATHS)
        for kept in (".claude", "plans", ".github", "src", "tests", "tools", "docs", "golden"):
            assert kept not in pruned
        assert os.path.join(".claude", "plans") not in pruned

    def test_the_git_pointer_of_a_linked_worktree_is_pruned_like_the_directory(self, tmp_path):
        """`.git` is a DIRECTORY in the main checkout and a FILE in a worktree.

        Found by running this, not by reading it: the first draft pruned
        directories only, so the same name was invisible in the main checkout
        and fingerprinted in every worktree. This alarm exists because the
        acceptor re-runs in main what an executor ran in a worktree, so a rule
        that answers differently in the two is exactly the wrong rule to have
        here. Asserted against a real worktree, so it cannot pass vacuously
        wherever the suite happens to be running from.
        """
        origin = tmp_path / "origin"
        origin.mkdir()
        _git("init", "-b", "main", cwd=origin)
        (origin / "kept.txt").write_text("one\n")
        _git("add", "-A", cwd=origin)
        _git("commit", "-m", "first", cwd=origin)
        worktree = tmp_path / "wt"
        _git("worktree", "add", "-b", "side", str(worktree), cwd=origin)

        assert (worktree / ".git").is_file()
        assert ".git" not in _tree_fingerprint(worktree)
        assert "kept.txt" in _tree_fingerprint(worktree)

    def test_the_pruned_directories_are_really_absent_from_the_fingerprint(self):
        fingerprint = _tree_fingerprint(REPO_ROOT)
        for path in fingerprint:
            parts = path.split(os.sep)
            assert not set(parts) & set(_PRUNED_NAMES), path
