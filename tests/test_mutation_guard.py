"""Tests for tests/manual/mutation_guard.py (CB-173).

The two harnesses in tests/manual/ (mutate_cb69.py, mutate_cb31.py) each write a
mutated copy of a source file to disk, run pytest, and restore the original in a
`finally`. Nothing checked the tree was clean before that first write, and that
destroyed an agent's uncommitted work five times (CB-173's cited incidents, four
in one day, a fifth days later). `tests/manual/mutation_guard.require_clean_tree`
is the fix: a fail-closed guard both harnesses call as the first thing they do.

These tests run against a REAL temporary git repository (tmp_path + `git init` +
one commit), never a mocked `git` call — a mocked subprocess would only verify
our own format string, not the guard's actual behaviour against real git output.
`tests/manual/` is not collected by the suite (see tests/manual/README or the
harnesses' own docstrings), so this file — not a file under tests/manual/ — is
where the guard's behaviour is pinned.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANUAL_DIR = REPO_ROOT / "tests" / "manual"
GUARD_PATH = MANUAL_DIR / "mutation_guard.py"
HARNESS_PATH = MANUAL_DIR / "mutate_cb69.py"

sys.path.insert(0, str(MANUAL_DIR))
import mutation_guard  # noqa: E402
from mutation_guard import DirtyTreeError, require_clean_tree  # noqa: E402


def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture
def git_repo(tmp_path):
    """A real one-commit git repo with two tracked files: `target.py` (the
    file a probe is about to mutate) and `other.py` (a bystander)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    target = repo / "target.py"
    target.write_text("original\n")
    other = repo / "other.py"
    other.write_text("other\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    return repo, target, other


class TestRequireCleanTree:
    def test_clean_tree_passes(self, git_repo):
        repo, target, _other = git_repo
        require_clean_tree([target], cwd=repo)  # must not raise

    def test_dirty_target_refuses_and_names_the_file(self, git_repo):
        repo, target, _other = git_repo
        target.write_text("dirty\n")
        with pytest.raises(DirtyTreeError) as exc_info:
            require_clean_tree([target], cwd=repo)
        assert "target.py" in str(exc_info.value)

    def test_dirty_bystander_file_does_not_refuse(self, git_repo):
        """Only the files the caller names as mutation targets are checked —
        an unrelated dirty file elsewhere in the tree must not block a probe
        that never touches it."""
        repo, target, other = git_repo
        other.write_text("also dirty, but not a mutation target\n")
        require_clean_tree([target], cwd=repo)  # must not raise

    def test_allow_dirty_proceeds_on_a_dirty_target(self, git_repo):
        repo, target, _other = git_repo
        target.write_text("dirty\n")
        require_clean_tree([target], cwd=repo, allow_dirty=True)  # must not raise

    def test_missing_git_refuses(self, git_repo, tmp_path, monkeypatch):
        repo, target, _other = git_repo
        empty_path_dir = tmp_path / "empty-path"
        empty_path_dir.mkdir()
        monkeypatch.setenv("PATH", str(empty_path_dir))
        with pytest.raises(DirtyTreeError):
            require_clean_tree([target], cwd=repo)

    def test_non_executable_git_refuses(self, git_repo, tmp_path, monkeypatch):
        """A `git` on PATH that exists but cannot be executed must read as
        dirty, not as clean. The fake `git` must be the ONLY thing on PATH —
        with a real git also reachable, CPython's exec just skips the broken
        one and finds the real binary, silently proving nothing (CLAUDE.md,
        CB-79 note)."""
        repo, target, _other = git_repo
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text("#!/bin/sh\nexit 0\n")
        fake_git.chmod(0o644)  # deliberately not executable
        monkeypatch.setenv("PATH", str(fake_bin))
        with pytest.raises(DirtyTreeError):
            require_clean_tree([target], cwd=repo)

    def test_git_error_exit_refuses(self, git_repo, tmp_path, monkeypatch):
        """git reachable and executable, but answering with a non-zero exit
        (e.g. run outside any repository) must also read as dirty."""
        repo, target, _other = git_repo
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()
        with pytest.raises(DirtyTreeError):
            require_clean_tree([target], cwd=not_a_repo)

    def test_subprocess_timeout_refuses(self, git_repo, monkeypatch):
        """`subprocess.SubprocessError` (e.g. a `git` call that times out) is
        NOT a subclass of `OSError` (CB-79 in CLAUDE.md), so it must be
        caught in its own arm and must also read as dirty. Until now no
        test exercised this branch at all — every existing failure case
        (missing git, non-executable git, non-zero exit) raises through
        `OSError` or a plain return code, never through
        `subprocess.SubprocessError` — so a mutant dropping it from the
        `except (OSError, subprocess.SubprocessError)` tuple survived.
        Injecting a real 30-second `git status` timeout would make this
        test itself take 30 seconds; monkeypatching `subprocess.run` to
        raise `TimeoutExpired` exercises the exact same except arm without
        the wait."""
        repo, target, _other = git_repo

        def _raise_timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=30)

        monkeypatch.setattr(mutation_guard.subprocess, "run", _raise_timeout)
        with pytest.raises(DirtyTreeError):
            require_clean_tree([target], cwd=repo)


class TestComposedHarnessRefusesWithoutWriting:
    """A harness invoked with a dirty mutation target must not touch that
    file even once before it refuses — a refusal that happens after the
    first write is formally a refusal but has already eaten the work
    (CB-173). This exercises the ACTUAL mutate_cb69.py end to end (copied
    into an isolated one-commit git repo, so the real script runs and reads
    real git output), not a reimplementation of its control flow."""

    def test_dirty_target_survives_a_full_harness_run(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "tests" / "manual").mkdir(parents=True)
        (repo / "src" / "codebugs").mkdir(parents=True)
        shutil.copy(GUARD_PATH, repo / "tests" / "manual" / "mutation_guard.py")
        shutil.copy(HARNESS_PATH, repo / "tests" / "manual" / "mutate_cb69.py")
        # Minimal stand-ins for the files mutate_cb69.py names as mutation
        # targets, so the guard has real, tracked files to check. Their
        # content does not matter for findings.py/reqs.py: the guard must
        # refuse before the harness ever reads any of them for mutation.
        for name in ("findings.py", "blockers.py", "reqs.py"):
            (repo / "src" / "codebugs" / name).write_text(f"# {name}\n")
        _git("init", "-q", cwd=repo)
        _git("config", "user.email", "test@example.com", cwd=repo)
        _git("config", "user.name", "Test", cwd=repo)
        _git("add", "-A", cwd=repo)
        _git("commit", "-q", "-m", "initial", cwd=repo)

        # blockers.py's dirtied (uncommitted) content deliberately carries
        # mutate_cb69.py's own M2 pattern verbatim (the exact `old` string of
        # its "M2 counts are NOT restricted to the returned ids" mutation).
        # Without a real pattern here every mutation in MUTATIONS reports
        # "NOT-APPLIED" regardless of the guard's placement, so `run()` never
        # calls `path.write_text()` at all and the test cannot tell "the
        # guard refused before writing" apart from "there was never anything
        # to write" — CB-173's correction found the assertion below passed
        # even with the guard call moved to AFTER the mutation loop, for
        # exactly this reason.
        target = repo / "src" / "codebugs" / "blockers.py"
        target.write_text(
            "# blockers.py\n"
            "# dirtied by the test, uncommitted\n"
            "def deferred_ids_and_counts(conn, kind, *, id=None, ids=None):\n"
            "    restricted = []\n"
            "    counts = {}\n"
            "    return restricted, {i: counts[i] for i in restricted}\n"
        )
        dirty_bytes = target.read_bytes()
        # A `finally: path.write_text(original)` restores identical BYTES
        # whether or not the harness ever actually wrote to the file first —
        # a full, uninterrupted run() cycle (write mutated, test, restore)
        # ends at the same content as a run that never touched the file at
        # all. Content equality alone therefore cannot tell "the guard
        # refused before the first write" apart from "the guard refused
        # after a completed write-then-restore cycle" — exactly CB-173's
        # correction: moving the guard call to AFTER the mutation loop left
        # this test green even with a genuine mutation pattern present,
        # because every write that loop performs is undone by its own
        # `finally` before the script exits. `write_text()` bumps the
        # file's mtime on every call regardless of whether the bytes it
        # writes end up identical to what was there before (measured), so
        # comparing mtime catches a write-then-restore cycle that content
        # comparison cannot.
        dirty_mtime_ns = target.stat().st_mtime_ns

        proc = subprocess.run(
            [sys.executable, str(repo / "tests" / "manual" / "mutate_cb69.py")],
            cwd=repo,
            capture_output=True,
            text=True,
        )

        assert proc.returncode != 0
        combined_output = proc.stdout + proc.stderr
        assert "blockers.py" in combined_output
        assert target.read_bytes() == dirty_bytes, (
            "the harness wrote to (or restored over) the mutation target "
            "before refusing — a refusal after the first write has already "
            "eaten the uncommitted work"
        )
        assert target.stat().st_mtime_ns == dirty_mtime_ns, (
            "the harness opened the mutation target for writing before "
            "refusing — content came back byte-identical only because "
            "`finally` restored it after a completed mutate/test/restore "
            "cycle, which is exactly the 'refusal after the first write' "
            "shape this test exists to catch"
        )
