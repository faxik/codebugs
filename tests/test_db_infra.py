"""Tests for db.py infrastructure: connect, _find_db_root, _db_path, init_project."""

import ast
import errno
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from codebugs import db, findings


# Every test here asserts on cwd-derived discovery, which an exported
# CODEBUGS_ROOT overrides by design (CB-11). The guard that neutralizes it is
# suite-wide in `conftest.py` — see that file for why it cannot live here.


@pytest.fixture
def tmp_project(tmp_path):
    """Provide a temporary project directory with an initialized tracker."""
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    """Provide a connected database."""
    c = db.connect(tmp_project)
    yield c
    c.close()


class TestConnect:
    def test_creates_db_directory(self, tmp_path):
        project = str(tmp_path)
        db.init_project(project)
        conn = db.connect(project)
        assert os.path.exists(os.path.join(project, ".codebugs", "findings.db"))
        conn.close()

    def test_idempotent_connect(self, tmp_path):
        project = str(tmp_path)
        db.init_project(project)
        c1 = db.connect(project)
        findings.add_finding(c1, severity="low", category="x", file="a.py", description="d", new_category=True)
        c1.close()

        c2 = db.connect(project)
        result = findings.query_findings(c2)
        assert result["total"] == 1
        c2.close()


class TestUpwardWalk:
    """Walk parent dirs to find an existing `.codebugs/` (git-style discovery)."""

    def test_find_db_root_in_subdir_walks_to_parent(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        sub = repo / "src" / "deep"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        assert db._find_db_root() == str(repo.resolve())

    def test_find_db_root_stops_at_git(self, tmp_path, monkeypatch):
        """A `.git/` boundary above must block the walk so we don't bind to
        an enclosing repo's `.codebugs/` from inside a vendored submodule."""
        outer = tmp_path / "outer"
        (outer / ".codebugs").mkdir(parents=True)
        sub = outer / "vendor" / "inner"
        (sub / ".git").mkdir(parents=True)
        leaf = sub / "src"
        leaf.mkdir()
        monkeypatch.chdir(leaf)
        assert db._find_db_root() is None

    def test_find_db_root_returns_none_when_nothing_found(self, tmp_path, monkeypatch):
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.chdir(empty)
        assert db._find_db_root() is None

    def test_find_db_root_codebugs_takes_priority_at_repo_root(self, tmp_path, monkeypatch):
        """If `.codebugs/` and `.git/` live in the same dir, `.codebugs/` wins."""
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        (repo / ".git").mkdir()
        sub = repo / "src"
        sub.mkdir()
        monkeypatch.chdir(sub)
        assert db._find_db_root() == str(repo.resolve())

    def test_db_path_uses_upward_walk(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        sub = repo / "src"
        sub.mkdir()
        monkeypatch.chdir(sub)
        expected = os.path.join(str(repo.resolve()), ".codebugs", "findings.db")
        assert db._db_path() == expected

    def test_db_path_refuses_when_no_root_found(self, tmp_path, monkeypatch):
        """No `.codebugs/` anywhere above cwd is a refusal, not a cwd fallback."""
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.chdir(empty)
        with pytest.raises(db.DatabaseNotFoundError):
            db._db_path()

    def test_db_path_explicit_project_dir_short_circuits_walk(self, tmp_path, monkeypatch):
        """An explicit dir is used as-is — it must not inherit cwd's project."""
        repo = tmp_path / "repo"
        repo.mkdir()
        db.init_project(str(repo))
        sub = repo / "src"
        sub.mkdir()
        monkeypatch.chdir(sub)
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        db.init_project(str(explicit))
        assert db._db_path(str(explicit)) == os.path.join(str(explicit), ".codebugs", "findings.db")

    def test_connect_silent_when_db_exists(self, tmp_path, monkeypatch, capsys):
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        sub = repo / "src"
        sub.mkdir()
        monkeypatch.chdir(repo)
        c1 = db.connect()
        c1.close()
        capsys.readouterr()  # discard the first-create warning
        monkeypatch.chdir(sub)
        c2 = db.connect()
        try:
            captured = capsys.readouterr()
            assert captured.err == ""
        finally:
            c2.close()

    def test_connect_silent_after_init(self, tmp_path, capsys):
        """Opening an initialized tracker says nothing on stderr."""
        db.init_project(str(tmp_path))
        capsys.readouterr()
        c = db.connect(str(tmp_path))
        try:
            captured = capsys.readouterr()
            assert captured.err == ""
        finally:
            c.close()


def _git(*args: str, cwd) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


def _repo_with_worktree(tmp_path, worktree_path, *, tracker: bool = True):
    """A real git repo plus a real linked worktree.

    `tracker=False` builds the CB-10 case: a main checkout that has not been
    initialized yet, so discovery from the worktree legitimately finds nothing.
    """
    repo = tmp_path / "repo"
    (repo / ".codebugs").mkdir(parents=True) if tracker else repo.mkdir(parents=True)
    _git("init", "-b", "main", ".", cwd=repo)
    (repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    _git("worktree", "add", str(worktree_path), "-b", "wt", cwd=repo)
    return repo


class TestWorktreeDiscovery:
    """A linked worktree's `.git` is a FILE, not a directory (CB-8).

    Discovery must follow its `gitdir:` pointer to the main repo instead of
    treating it as a repo root and giving up.
    """

    def test_worktree_git_entry_really_is_a_file(self, tmp_path):
        """Guard the premise: if git ever stops writing a `.git` file, these tests lie."""
        _repo_with_worktree(tmp_path, tmp_path / "wt")
        assert (tmp_path / "wt" / ".git").is_file()

    def test_find_db_root_from_worktree_inside_repo(self, tmp_path, monkeypatch):
        wt = tmp_path / "repo" / ".worktrees" / "wt"
        repo = _repo_with_worktree(tmp_path, wt)
        monkeypatch.chdir(wt)
        assert db._find_db_root() == str(repo.resolve())

    def test_find_db_root_from_worktree_outside_repo(self, tmp_path, monkeypatch):
        """The parent walk alone cannot reach the repo — only the gitdir pointer can."""
        wt = tmp_path / "elsewhere"
        repo = _repo_with_worktree(tmp_path, wt)
        monkeypatch.chdir(wt)
        assert db._find_db_root() == str(repo.resolve())

    def test_find_db_root_from_subdir_of_worktree(self, tmp_path, monkeypatch):
        wt = tmp_path / "elsewhere"
        repo = _repo_with_worktree(tmp_path, wt)
        deep = wt / "src" / "codebugs"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert db._find_db_root() == str(repo.resolve())

    def test_connect_from_worktree_uses_the_repo_db(self, tmp_path, monkeypatch):
        """The end-to-end claim: a finding filed from a worktree lands in the repo's DB."""
        wt = tmp_path / "elsewhere"
        repo = _repo_with_worktree(tmp_path, wt)
        monkeypatch.chdir(wt)
        c = db.connect()
        try:
            findings.add_finding(c, severity="low", category="x", file="a.py", description="d", new_category=True)
        finally:
            c.close()
        assert not (wt / ".codebugs").exists(), "no throwaway DB in the worktree"
        c2 = db.connect(str(repo))
        try:
            assert findings.query_findings(c2)["total"] == 1
        finally:
            c2.close()

    def test_submodule_git_file_still_stops_the_walk(self, tmp_path, monkeypatch):
        """A submodule also has a `.git` FILE — it must NOT reach the parent's DB."""
        outer = tmp_path / "outer"
        (outer / ".codebugs").mkdir(parents=True)
        (outer / ".git" / "modules" / "inner").mkdir(parents=True)
        inner = outer / "vendor" / "inner"
        inner.mkdir(parents=True)
        (inner / ".git").write_text("gitdir: ../../.git/modules/inner\n")
        monkeypatch.chdir(inner)
        assert db._find_db_root() is None

    def test_git_file_without_a_gitdir_pointer_stops_the_walk(self, tmp_path, monkeypatch):
        """A readable but pointerless `.git` file is a boundary, not an invitation to climb."""
        outer = tmp_path / "outer"
        (outer / ".codebugs").mkdir(parents=True)
        inner = outer / "weird"
        inner.mkdir(parents=True)
        (inner / ".git").write_text("not a gitdir pointer\n")
        monkeypatch.chdir(inner)
        assert db._find_db_root() is None

    def test_unreadable_git_file_stops_the_walk(self, tmp_path, monkeypatch):
        """The OSError branch: a `.git` file we cannot read at all."""
        outer = tmp_path / "outer"
        (outer / ".codebugs").mkdir(parents=True)
        inner = outer / "locked"
        inner.mkdir(parents=True)
        git_file = inner / ".git"
        git_file.write_text("gitdir: /somewhere\n")
        git_file.chmod(0o000)
        monkeypatch.chdir(inner)
        try:
            if os.access(git_file, os.R_OK):
                pytest.skip("cannot make a file unreadable as this user (running as root?)")
            assert db._find_db_root() is None
        finally:
            git_file.chmod(0o644)

    def test_worktree_of_a_bare_repo_is_refused(self, tmp_path, monkeypatch):
        """Locks in the bare-repo guard: refuse rather than guess a checkout root.

        Without the guard this binds to whatever `.codebugs/` sits beside the
        bare repo, which is not the worktree's project.
        """
        (tmp_path / ".codebugs").mkdir()
        bare = tmp_path / "bare.git"
        _git("init", "--bare", str(bare), cwd=tmp_path)
        seed = tmp_path / "seed"
        seed.mkdir()
        _git("init", "-b", "main", ".", cwd=seed)
        (seed / "f.txt").write_text("x")
        _git("add", "f.txt", cwd=seed)
        _git("commit", "-m", "init", cwd=seed)
        _git("push", str(bare), "main", cwd=seed)
        wt = tmp_path / "wt"
        _git("worktree", "add", str(wt), "main", cwd=bare)
        monkeypatch.chdir(wt)
        assert db._find_db_root() is None

    def test_pointer_cycle_terminates(self, tmp_path, monkeypatch):
        """Locks in the cycle guard: crafted mutual pointers must not loop forever."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        for d, other in ((a, b), (b, a)):
            gitdir = d / "gitdir"
            gitdir.mkdir(parents=True)
            (gitdir / "commondir").write_text(str(other / ".git"))
            (d / ".git").write_text(f"gitdir: {gitdir}\n")
        monkeypatch.chdir(a)
        assert db._find_db_root() is None


class TestRefusesToAutoCreate:
    """Auto-creating on the implicit path is what made CB-8 silent (CB-8 fix 1)."""

    def test_connect_refuses_when_no_project_found(self, tmp_path, monkeypatch):
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.chdir(empty)
        with pytest.raises(db.DatabaseNotFoundError) as exc:
            db.connect()
        msg = str(exc.value)
        assert str(empty.resolve()) in msg, "error must name the directory it searched from"
        assert "codebugs init" in msg, "error must name the way out"

    def test_refusal_inside_a_worktree_does_not_advise_initializing_here(
        self, tmp_path, monkeypatch
    ):
        """CB-10 residue: `init` here is the one thing a worktree user must NOT do.

        The generic message tells them to run `codebugs init` in the current
        directory; inside a worktree that produces a tracker which dies with the
        worktree. The diagnostic has to name the main checkout instead.
        """
        wt = tmp_path / "elsewhere"
        repo = _repo_with_worktree(tmp_path, wt, tracker=False)
        monkeypatch.chdir(wt)
        with pytest.raises(db.DatabaseNotFoundError) as exc:
            db.connect()
        msg = str(exc.value)
        assert "worktree" in msg.lower(), "must say why this directory is special"
        assert str(repo.resolve()) in msg, "must name the main checkout to initialize instead"

    def test_refusal_creates_nothing_on_disk(self, tmp_path, monkeypatch):
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.chdir(empty)
        with pytest.raises(db.DatabaseNotFoundError):
            db.connect()
        assert not (empty / ".codebugs").exists()

    def test_connect_creates_db_inside_an_existing_codebugs_dir(self, tmp_path, monkeypatch):
        """`.codebugs/` present but no DB file yet — that directory IS the opt-in.

        The deliberate asymmetry, and the reason the two refusal tests below do
        NOT contradict it (CB-23): standing inside a directory is evidence about
        where you are, while a named or declared path is an assertion that can be
        stale or mistyped. This is also what makes a Ctrl-C'd ``init`` self-heal
        on the next command — ``init_project`` creates the directory before the
        database — instead of demanding a second ``init``.
        """
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        monkeypatch.chdir(repo)
        c = db.connect()
        try:
            assert os.path.exists(repo / ".codebugs" / "findings.db")
        finally:
            c.close()

    def test_connect_refuses_an_explicit_dir_with_no_tracker(self, tmp_path):
        """A named directory is NOT an opt-in to creation: it may be a user's typo.

        `init_project` is the only function allowed to make a tracker.
        """
        with pytest.raises(db.DatabaseNotFoundError) as exc:
            db.connect(str(tmp_path))
        assert str(tmp_path) in str(exc.value)
        assert not (tmp_path / ".codebugs").exists()

    def test_connect_refuses_an_explicit_dir_whose_tracker_dir_has_no_db(self, tmp_path):
        """`--repo <path>` carries user input, so a half-made tracker is a typo target.

        The sibling test above refuses when there is no `.codebugs/` at all. This
        one refuses when the directory exists but holds no database — which is
        the same claim, since the directory carries no findings (CB-23).
        """
        (tmp_path / ".codebugs").mkdir()
        with pytest.raises(db.DatabaseNotFoundError) as exc:
            db.connect(str(tmp_path))
        assert str(tmp_path) in str(exc.value)
        assert not (tmp_path / ".codebugs" / "findings.db").exists()

    def test_a_named_root_cannot_create_even_if_the_db_vanishes_after_the_check(
        self, tmp_path, monkeypatch
    ):
        """The resolver's `isfile` is check-then-act; `mode=rw` at the open closes it.

        Deleting the database between the check and the open simulates the other
        agent this project is built to run alongside. With the check alone, the
        open would build a fresh empty tracker at that path and every later write
        would report success — CB-23's failure mode surviving CB-23's fix.
        """
        db.init_project(str(tmp_path))
        real_path = tmp_path / ".codebugs" / "findings.db"
        assert real_path.exists()

        original = db._resolve_db

        def resolve_then_delete(project_dir=None):
            result = original(project_dir)
            real_path.unlink()  # the window between the check and the open
            return result

        monkeypatch.setattr(db, "_resolve_db", resolve_then_delete)
        with pytest.raises(db.DatabaseNotFoundError):
            db.connect(str(tmp_path))
        assert not real_path.exists(), "the refusal must not have created one"

    def test_connect_opens_an_explicit_dir_after_init(self, tmp_path):
        db.init_project(str(tmp_path))
        c = db.connect(str(tmp_path))
        try:
            assert os.path.exists(tmp_path / ".codebugs" / "findings.db")
        finally:
            c.close()

    def test_cli_repo_flag_refuses_instead_of_creating(self, tmp_path):
        """`resolve-trailers --repo <path>` passes user input straight into connect().

        A typo there used to silently create a second tracker — CB-8's own failure
        mode on a shipping code path.
        """
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "codebugs.cli",
                "resolve-trailers",
                "--range",
                "HEAD~1..HEAD",
                "--repo",
                str(tmp_path),
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
        assert not (tmp_path / ".codebugs").exists(), "must not create a tracker on a typo'd path"


class TestInitProject:
    """Creating a tracker becomes a deliberate act (CB-8 fix 2)."""

    def test_init_creates_tracker_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = db.init_project()
        assert result["created"] is True
        assert result["path"] == str(tmp_path / ".codebugs" / "findings.db")
        assert os.path.exists(result["path"])

    def test_init_makes_the_refusal_go_away(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(db.DatabaseNotFoundError):
            db.connect()
        db.init_project()
        c = db.connect()
        c.close()

    def test_init_is_idempotent_and_preserves_findings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db.init_project()
        c = db.connect()
        findings.add_finding(c, severity="low", category="x", file="a.py", description="d", new_category=True)
        c.close()

        result = db.init_project()
        assert result["created"] is False

        c2 = db.connect()
        try:
            assert findings.query_findings(c2)["total"] == 1
        finally:
            c2.close()

    def test_init_refuses_to_shadow_an_enclosing_tracker(self, tmp_path, monkeypatch):
        """A nested tracker permanently hides the real one — CB-8's failure class.

        `_find_db_root` finds the nearest `.codebugs/`, so once a nested one exists
        every caller below it is silently cut off from the project's findings.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        db.init_project(str(repo))
        sub = repo / "src" / "deep"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        with pytest.raises(db.TrackerExistsError) as exc:
            db.init_project()
        assert str(repo.resolve()) in str(exc.value)
        assert not (sub / ".codebugs").exists()

    def test_init_in_a_worktree_refuses(self, tmp_path, monkeypatch):
        """The likeliest real-world trigger: an agent in a worktree told to run `init`."""
        wt = tmp_path / "elsewhere"
        _repo_with_worktree(tmp_path, wt)
        monkeypatch.chdir(wt)
        with pytest.raises(db.TrackerExistsError):
            db.init_project()
        assert not (wt / ".codebugs").exists()

    def test_init_in_a_worktree_refuses_even_when_the_main_repo_has_no_tracker(
        self, tmp_path, monkeypatch
    ):
        """CB-10/N1: the shadow guard cannot lean on `_find_db_root` alone.

        With no tracker in the main checkout, discovery returns None, so the
        enclosing-tracker check passes and `init` used to create a tracker
        INSIDE the worktree — which dies with the worktree, taking its findings.
        """
        wt = tmp_path / "elsewhere"
        repo = _repo_with_worktree(tmp_path, wt, tracker=False)
        monkeypatch.chdir(wt)
        with pytest.raises(db.TrackerExistsError) as exc:
            db.init_project()
        assert not (wt / ".codebugs").exists(), "no tracker may be created inside a worktree"
        assert not (repo / ".codebugs").exists(), "and none may be invented in the main repo"
        assert str(wt.resolve()) in str(exc.value), "error must name the worktree it refused in"

    def test_init_in_a_worktree_subdir_refuses(self, tmp_path, monkeypatch):
        """The guard must walk up, not just look at cwd."""
        wt = tmp_path / "elsewhere"
        _repo_with_worktree(tmp_path, wt, tracker=False)
        sub = wt / "src" / "deep"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        with pytest.raises(db.TrackerExistsError):
            db.init_project()
        assert not (sub / ".codebugs").exists()

    def test_init_force_still_works_inside_a_worktree(self, tmp_path, monkeypatch):
        """The refusal must stay overridable — deliberate worktree-local trackers are legal."""
        wt = tmp_path / "elsewhere"
        _repo_with_worktree(tmp_path, wt, tracker=False)
        monkeypatch.chdir(wt)
        result = db.init_project(force=True)
        assert result["created"] is True
        assert os.path.exists(wt / ".codebugs" / "findings.db")

    def test_init_in_the_main_checkout_is_unaffected(self, tmp_path, monkeypatch):
        """The guard keys on linked worktrees only — a normal repo root still initializes."""
        wt = tmp_path / "elsewhere"
        repo = _repo_with_worktree(tmp_path, wt, tracker=False)
        monkeypatch.chdir(repo)
        result = db.init_project()
        assert result["created"] is True
        assert os.path.exists(repo / ".codebugs" / "findings.db")

    def test_init_force_creates_a_nested_tracker(self, tmp_path, monkeypatch):
        """Deliberately nesting stays possible — it just cannot happen by accident."""
        repo = tmp_path / "repo"
        repo.mkdir()
        db.init_project(str(repo))
        sub = repo / "src"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        result = db.init_project(force=True)
        assert result["created"] is True
        assert os.path.exists(sub / ".codebugs" / "findings.db")

    def test_init_refuses_a_nonexistent_directory(self, tmp_path):
        """`codebugs init ../proejct` must not silently make a tracker in a typo."""
        missing = tmp_path / "proejct"
        with pytest.raises(ValueError) as exc:
            db.init_project(str(missing))
        assert str(missing) in str(exc.value)
        assert not missing.exists()

    def test_cli_init_shadow_refusal_is_a_clean_error(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db.init_project(str(repo))
        sub = repo / "src"
        sub.mkdir(parents=True)
        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "init"],
            cwd=str(sub),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
        assert not (sub / ".codebugs").exists()

    def test_cli_init_on_a_codebugs_file_is_a_clean_error(self, tmp_path):
        """`.codebugs` existing as a FILE raised a raw FileExistsError traceback."""
        (tmp_path / ".codebugs").write_text("not a directory\n")
        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr

    def test_cli_init_command(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert os.path.exists(tmp_path / ".codebugs" / "findings.db")

    def test_cli_refusal_is_a_clean_error_not_a_traceback(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "stats"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
        assert "codebugs init" in proc.stderr


def _separate_git_dir_worktree(tmp_path):
    """Build CB-13's layout: a `--separate-git-dir` repo whose git dir IS named `.git`.

    `admin/.git` is the git directory; `repo/` is the real checkout and holds the
    real tracker — a real one, with a database, because a declared root now has to
    resolve to an actual `findings.db` (CB-23). A stray `.codebugs/` DIRECTORY
    sits in `admin/`, and stays a bare directory deliberately: the walk still
    binds on the directory alone, which is what makes the misbinding reproduce.
    Returns (repo, admin, wt).
    """
    admin = tmp_path / "admin"
    repo = tmp_path / "repo"
    admin.mkdir()
    repo.mkdir()
    _git("init", "-b", "main", f"--separate-git-dir={admin / '.git'}", str(repo), cwd=tmp_path)
    (repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    db.init_project(str(repo), force=True)  # force: CB-13's own layout reads as a worktree
    (admin / ".codebugs").mkdir()
    wt = tmp_path / "wt"
    _git("worktree", "add", str(wt), "-b", "wt", cwd=repo)
    return repo, admin, wt


def _tracker(parent, name):
    """Create `parent/name` and initialize a tracker in it. Returns the Path."""
    root = parent / name
    root.mkdir(parents=True, exist_ok=True)
    db.init_project(str(root))
    return root


class TestTrackerRootOverride:
    """An explicitly declared tracker root, the remedy for CB-11 and CB-13.

    Discovery guesses; a declaration does not. These tests pin the precedence
    (`--tracker-root` > `CODEBUGS_ROOT` > cwd walk), the fail-closed contract,
    and the one layout where discovery is provably unable to find the right
    answer on its own.
    """

    def test_env_var_overrides_the_cwd_walk(self, tmp_path, monkeypatch):
        declared = _tracker(tmp_path, "declared")
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / ".codebugs").mkdir(parents=True)
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv(db.ENV_ROOT, str(declared))
        assert db._db_path() == str(declared / ".codebugs" / "findings.db")

    def test_flag_beats_the_env_var(self, tmp_path, monkeypatch):
        flag_root = _tracker(tmp_path, "flag")
        env_root = _tracker(tmp_path, "env")
        monkeypatch.setenv(db.ENV_ROOT, str(env_root))
        monkeypatch.setattr(db, "_tracker_root_override", str(flag_root))
        assert db.declared_tracker_root() == (str(flag_root), "flag")
        assert db._db_path() == str(flag_root / ".codebugs" / "findings.db")

    def test_explicit_project_dir_still_beats_a_declared_root(self, tmp_path, monkeypatch):
        """`--repo <path>` is per-call and more specific than ambient state."""
        declared = _tracker(tmp_path, "declared")
        named = _tracker(tmp_path, "named")
        monkeypatch.setenv(db.ENV_ROOT, str(declared))
        assert db._db_path(str(named)) == str(named / ".codebugs" / "findings.db")

    def test_a_declared_root_with_no_tracker_fails_closed(self, tmp_path, monkeypatch):
        """The mitigation for a stale export: refuse, never create a second tracker."""
        empty = tmp_path / "empty"
        empty.mkdir()
        here = _tracker(tmp_path, "here")
        monkeypatch.chdir(here)
        monkeypatch.setenv(db.ENV_ROOT, str(empty))
        with pytest.raises(db.DatabaseNotFoundError) as exc:
            db._db_path()
        assert not (empty / ".codebugs").exists()
        assert str(empty) in str(exc.value)

    def test_the_refusal_names_the_channel_that_set_it(self, tmp_path, monkeypatch):
        """Without the channel name, a wrong bind is a mystery: the value is ambient."""
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv(db.ENV_ROOT, str(empty))
        with pytest.raises(db.DatabaseNotFoundError) as env_exc:
            db._db_path()
        assert db.ENV_ROOT in str(env_exc.value)

        monkeypatch.setattr(db, "_tracker_root_override", str(empty))
        with pytest.raises(db.DatabaseNotFoundError) as flag_exc:
            db._db_path()
        assert "--tracker-root" in str(flag_exc.value)
        assert db.ENV_ROOT not in str(flag_exc.value)

    def test_a_declared_root_whose_tracker_dir_has_no_db_fails_closed(self, tmp_path, monkeypatch):
        """A `.codebugs/` with no `findings.db` is not a tracker (CB-23).

        The directory alone carries no findings, so accepting it lets an ambient
        declaration quietly become the second, empty tracker this whole branch
        exists to prevent. That the directory exists is not evidence a tracker
        does: a Ctrl-C'd ``init`` leaves exactly this state, because
        ``init_project`` creates the directory before the database.

        The upward walk deliberately does NOT get this check — see
        ``TestRefusesToAutoCreate`` for why the two branches differ.
        """
        half = tmp_path / "half"
        (half / ".codebugs").mkdir(parents=True)
        here = _tracker(tmp_path, "here")
        monkeypatch.chdir(here)
        monkeypatch.setenv(db.ENV_ROOT, str(half))

        with pytest.raises(db.DatabaseNotFoundError) as exc:
            db._db_path()
        assert str(half) in str(exc.value)
        assert db.ENV_ROOT in str(exc.value), "the channel must be named: the value is ambient"
        assert not (half / ".codebugs" / "findings.db").exists(), "must not have created one"

    def test_a_declared_root_that_is_not_a_directory_fails_closed(self, tmp_path, monkeypatch):
        missing = tmp_path / "typo"
        monkeypatch.setenv(db.ENV_ROOT, str(missing))
        with pytest.raises(db.DatabaseNotFoundError) as exc:
            db._db_path()
        assert str(missing) in str(exc.value)

    def test_a_blank_declaration_is_no_declaration(self, tmp_path, monkeypatch):
        """Uniform with the project's 'an empty filter is no filter' convention."""
        here = _tracker(tmp_path, "here")
        monkeypatch.chdir(here)
        monkeypatch.setenv(db.ENV_ROOT, "   ")
        assert db.declared_tracker_root() == (None, "discovery")
        assert db._db_path() == str(here / ".codebugs" / "findings.db")

    def test_declaration_is_validated_at_use_not_at_set_time(self, tmp_path):
        """Lazy self-healing: a root that appears later must still work (CB-11).

        A server told about a root before the tracker exists must not die at
        startup — that is the constraint recorded on the card.
        """
        later = tmp_path / "later"
        db.set_tracker_root(str(later))
        try:
            with pytest.raises(db.DatabaseNotFoundError):
                db._db_path()
            later.mkdir()
            db.init_project(str(later))
            assert db._db_path() == str(later / ".codebugs" / "findings.db")
        finally:
            db.set_tracker_root(None)

    def test_findings_land_in_the_declared_tracker(self, tmp_path, monkeypatch):
        """End to end: the override moves real writes, not just path resolution."""
        declared = _tracker(tmp_path, "declared")
        elsewhere = _tracker(tmp_path, "elsewhere")
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv(db.ENV_ROOT, str(declared))
        c = db.connect()
        try:
            findings.add_finding(c, severity="low", category="x", file="a.py", description="d", new_category=True)
        finally:
            c.close()
        for root, expected in ((declared, 1), (elsewhere, 0)):
            c2 = db.connect(str(root))
            try:
                assert findings.query_findings(c2)["total"] == expected
            finally:
                c2.close()


class TestSeparateGitDirMisbinding:
    """CB-13: the one layout where discovery provably cannot find the right answer.

    `_worktree_main_root` accepts any commondir whose basename is `.git` — git's
    own heuristic. When a `--separate-git-dir` repo's git dir is literally named
    `.git`, that resolves to the ADMIN directory, not the checkout. There is no
    local discriminator, so the heuristic stays; the declaration is the fix.
    """

    def test_the_misbinding_still_reproduces(self, tmp_path, monkeypatch):
        """Guard the premise. If this ever stops failing, the fix below is moot."""
        repo, admin, wt = _separate_git_dir_worktree(tmp_path)
        monkeypatch.chdir(wt)
        assert db._find_db_root() == str(admin.resolve()), "CB-13 no longer reproduces"

    def test_a_declared_root_defeats_the_misbinding(self, tmp_path, monkeypatch):
        repo, admin, wt = _separate_git_dir_worktree(tmp_path)
        monkeypatch.chdir(wt)
        monkeypatch.setenv(db.ENV_ROOT, str(repo))
        assert db._db_path() == str((repo / ".codebugs" / "findings.db").resolve())

    def test_where_reports_the_misbinding(self, tmp_path, monkeypatch):
        """The bug is invisible by definition; `where` is what makes it visible."""
        repo, admin, wt = _separate_git_dir_worktree(tmp_path)
        monkeypatch.chdir(wt)
        info = db.describe_root()
        assert info["source"] == "discovery"
        assert info["root"] == str(admin.resolve())
        # The mis-bound root is a stray DIRECTORY with no database, so the
        # diagnostic has a second, sharper thing to say: the tracker it names is
        # not there, and the next write would invent it (CB-23). Reporting the
        # wrong root as healthy is how this stayed invisible.
        assert info["exists"] is False
        assert info["error"] is None, "it resolves — that is exactly why it is dangerous"


class TestDescribeRoot:
    """One resolver, two consumers: `codebugs where` and the MCP preflight."""

    def test_reports_a_discovered_root(self, tmp_path, monkeypatch):
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["source"] == "discovery"
        assert info["root"] == str(tmp_path.resolve())
        assert info["path"] == str(tmp_path / ".codebugs" / "findings.db")
        assert info["error"] is None

    def test_reports_a_declared_root(self, tmp_path, monkeypatch):
        db.init_project(str(tmp_path))
        monkeypatch.setenv(db.ENV_ROOT, str(tmp_path))
        info = db.describe_root()
        assert info["source"] == "env"
        assert info["root"] == str(tmp_path)

    def test_reports_failure_without_raising(self, tmp_path, monkeypatch):
        """The preflight must be warn-only, so this may never raise (CB-11)."""
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["path"] is None
        assert info["root"] is None
        assert "codebugs init" in info["error"]

    def test_reports_a_declared_root_that_does_not_resolve(self, tmp_path, monkeypatch):
        monkeypatch.setenv(db.ENV_ROOT, str(tmp_path / "nope"))
        info = db.describe_root()
        assert info["source"] == "env"
        assert info["path"] is None
        assert db.ENV_ROOT in info["error"]

    def test_a_deleted_working_directory_is_reported_not_raised(self, tmp_path):
        """A long-lived server outlives the worktree it started in.

        `os.getcwd()` then raises FileNotFoundError. Escaping, that would bypass
        every DatabaseNotFoundError handler and make the preflight fatal — the
        one thing CB-11 forbids.
        """
        doomed = tmp_path / "doomed"
        doomed.mkdir()
        original = os.getcwd()
        os.chdir(doomed)
        try:
            doomed.rmdir()
            info = db.describe_root()  # must not raise
            assert info["path"] is None
            assert info["error"]
        finally:
            os.chdir(original)

    def test_a_deleted_working_directory_is_a_clean_domain_error(self, tmp_path):
        """`_db_path` converts it too, so the CLI prints an error, not a traceback."""
        doomed = tmp_path / "doomed"
        doomed.mkdir()
        original = os.getcwd()
        os.chdir(doomed)
        try:
            doomed.rmdir()
            with pytest.raises(db.DatabaseNotFoundError):
                db._db_path()
        finally:
            os.chdir(original)

    def test_a_declared_root_survives_a_deleted_working_directory(self, tmp_path):
        """The escape hatch has to work in exactly the case discovery cannot."""
        declared = _tracker(tmp_path, "declared")
        doomed = tmp_path / "doomed"
        doomed.mkdir()
        original = os.getcwd()
        os.chdir(doomed)
        try:
            doomed.rmdir()
            db.set_tracker_root(str(declared))
            assert db._db_path() == str(declared / ".codebugs" / "findings.db")
        finally:
            db.set_tracker_root(None)
            os.chdir(original)

    def test_a_relative_env_root_is_reported_absolute(self, tmp_path, monkeypatch):
        """CB-49: the report is read without knowing the process cwd.

        `init` prints the absolute path (`init_project` abspaths), so a relative
        report answers about the same binding in a different coordinate system —
        and the MCP preflight's reader provably cannot know the server's cwd.
        """
        _tracker(tmp_path, "declared")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(db.ENV_ROOT, "declared")
        info = db.describe_root()
        assert info["source"] == "env"
        assert info["root"] == str(tmp_path / "declared")
        assert info["path"] == str(tmp_path / "declared" / ".codebugs" / "findings.db")

    def test_a_relative_flag_root_is_reported_absolute(self, tmp_path, monkeypatch):
        """CB-49, the flag channel — same rule as the env channel above."""
        _tracker(tmp_path, "declared")
        monkeypatch.chdir(tmp_path)
        db.set_tracker_root("declared")
        try:
            info = db.describe_root()
            assert info["source"] == "flag"
            assert info["root"] == str(tmp_path / "declared")
        finally:
            db.set_tracker_root(None)

    def test_a_relative_root_error_names_the_absolute_path(self, tmp_path, monkeypatch):
        """CB-49: the error's `codebugs init <root>` advice must survive a cwd change."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(db.ENV_ROOT, "nope")
        info = db.describe_root()
        assert info["path"] is None
        assert str(tmp_path / "nope") in info["error"]

    def test_a_relative_declared_root_survives_a_deleted_cwd(self, tmp_path):
        """Passes on both sides of CB-49 — it pins behaviour the fix must preserve.

        `abspath` on a relative value needs `os.getcwd()`, which raises once the
        cwd is deleted; normalization must fall back to the raw value rather
        than let that escape, because `describe_root` may NEVER raise (CB-11).
        """
        doomed = tmp_path / "doomed"
        doomed.mkdir()
        original = os.getcwd()
        os.chdir(doomed)
        try:
            doomed.rmdir()
            db.set_tracker_root("../somewhere")
            info = db.describe_root()  # must not raise
            assert info["path"] is None
            assert info["error"]
        finally:
            db.set_tracker_root(None)
            os.chdir(original)


class TestWritabilityProbe:
    """CB-100: `describe_root()['writable']` — advisory, tri-state, silence-shaped.

    The owner reproduced three states by hand, outside this repository, before
    this unit existed: `chmod 000` on `findings.db`, `chmod 444` on the same
    file, and `chmod 555` on the `.codebugs/` DIRECTORY. In every one,
    `codebugs where` printed a clean binding (rc=0, no warning) while every
    verb refused with a precise "for writing" message — the diagnostic and the
    thing it diagnoses disagreeing. These three tests pin that fix; the fourth
    (directory) is the one that actually decides the mechanism, because
    `os.access` on the FILE alone cannot see it.
    """

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_chmod_000_file_is_reported_unwritable(self, tmp_path, monkeypatch):
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        findings_path = tmp_path / ".codebugs" / "findings.db"
        findings_path.chmod(0o000)
        try:
            info = db.describe_root()
            assert info["exists"] is True
            assert info["writable"] is False
        finally:
            findings_path.chmod(0o644)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_chmod_444_file_is_reported_unwritable(self, tmp_path, monkeypatch):
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        findings_path = tmp_path / ".codebugs" / "findings.db"
        findings_path.chmod(0o444)
        try:
            info = db.describe_root()
            assert info["writable"] is False
        finally:
            findings_path.chmod(0o644)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_chmod_555_directory_is_reported_unwritable(self, tmp_path, monkeypatch):
        """The state that decides the mechanism. The FILE's own bits are
        untouched (still 0644) — only the DIRECTORY refuses — and sqlite still
        fails every write with "attempt to write a readonly database"
        (measured, outside this repository). A file-only `os.access` check
        reports True here, a false positive; the directory check catches it.
        """
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        codebugs_dir = tmp_path / ".codebugs"
        codebugs_dir.chmod(0o555)
        try:
            info = db.describe_root()
            assert info["writable"] is False
        finally:
            codebugs_dir.chmod(0o755)

    def test_a_writable_tracker_with_real_rows_is_reported_writable(self, tmp_path, monkeypatch):
        """Half the oracle, and the half most likely to go vacuously green: a
        healthy, NONEMPTY tracker must still read as writable (CB-100 §7 — an
        empty tracker would pass this trivially and prove nothing).
        """
        db.init_project(str(tmp_path))
        conn = db.connect(str(tmp_path))
        findings.add_finding(
            conn, severity="low", category="x", file="f.py", description="d", new_category=True
        )
        conn.close()
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["exists"] is True
        assert info["writable"] is True

    def test_writable_is_none_when_the_database_does_not_exist_yet(self, tmp_path, monkeypatch):
        """The CB-23 'not there yet' state must not grow a second, colliding
        writability line — `writable` stays None (not True, not False) when
        `exists` is False, so `where`/preflight print only the existing note.
        """
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        monkeypatch.chdir(repo)
        info = db.describe_root()
        assert info["exists"] is False
        assert info["writable"] is None

    def test_writable_is_none_on_the_error_branch(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["error"] is not None
        assert info["writable"] is None


def _skip_if_root():
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits")


def _state_dir_traverse_bit(cb_dir):
    _skip_if_root()
    cb_dir.chmod(0o666)  # rw, no execute: the directory cannot be entered
    return lambda: cb_dir.chmod(0o755)


def _state_dir_no_permissions(cb_dir):
    _skip_if_root()
    cb_dir.chmod(0o000)
    return lambda: cb_dir.chmod(0o755)


def _state_file_no_permissions(cb_dir):
    _skip_if_root()
    (cb_dir / "findings.db").chmod(0o000)
    return lambda: (cb_dir / "findings.db").chmod(0o644)


def _state_node_is_directory(cb_dir):
    (cb_dir / "findings.db").unlink()
    (cb_dir / "findings.db").mkdir()
    return None


def _state_node_is_fifo(cb_dir):
    (cb_dir / "findings.db").unlink()
    os.mkfifo(cb_dir / "findings.db")
    return None


def _state_node_is_socket(cb_dir):
    (cb_dir / "findings.db").unlink()
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(cb_dir / "findings.db"))
    finally:
        sock.close()
    return None


def _state_symlink_loop(cb_dir):
    (cb_dir / "findings.db").unlink()
    (cb_dir / "tmp.db").symlink_to(cb_dir / "findings.db")
    (cb_dir / "tmp.db").rename(cb_dir / "findings.db")
    return None


def _state_dangling_symlink(cb_dir):
    (cb_dir / "findings.db").unlink()
    (cb_dir / "findings.db").symlink_to(cb_dir / "gone.db")
    return None


def _state_genuinely_absent(cb_dir):
    (cb_dir / "findings.db").unlink()
    return None


def _state_healthy(cb_dir):
    return None


# The §4 state table, and the third column is the ORACLE. `occupied` says what
# the FIXTURE put at the database's path — a fact the code under test never
# sees — so the assertion below compares the verb's claim against ground truth
# rather than against another reading of the same code.
#
# The rows are chosen as distinct MECHANISMS, not as a list of `chmod` calls:
# a permission bit removed at three different objects, a wrong node type in
# three flavours, a link that does not resolve in two, plus both controls. A
# test that enumerated the three known permission states would be green on the
# other five, which is exactly how CB-203 survived to be found by hand.
_TRACKER_STATES = {
    "dir_traverse_bit_removed": (_state_dir_traverse_bit, True),
    "dir_has_no_permissions": (_state_dir_no_permissions, True),
    "file_has_no_permissions": (_state_file_no_permissions, True),
    "a_directory_is_in_its_place": (_state_node_is_directory, True),
    "a_named_pipe_is_in_its_place": (_state_node_is_fifo, True),
    "a_socket_is_in_its_place": (_state_node_is_socket, True),
    "a_symlink_loop_is_in_its_place": (_state_symlink_loop, True),
    "a_dangling_symlink_is_in_its_place": (_state_dangling_symlink, True),
    "nothing_is_there_at_all": (_state_genuinely_absent, False),
    "a_healthy_database_is_there": (_state_healthy, True),
}

_PROMISE = "no database there yet — the next command creates one"


class TestUnreachableTrackerIsNeverReportedAsAbsent:
    """CB-203 — ONE property, checked on every state, stated in one sentence:

        `where` claims a database will be created at that path only when it was
        PROVEN that nothing is at that name.

    The defect it pins was measured by hand before this class existed: with the
    execute bit taken off a `.codebugs/` directory (`chmod 666`), `codebugs
    where` printed exactly that promise at exit 0, over a populated tracker that
    every other verb refused to open. Strictly worse than CB-100 and CB-182,
    where a warning merely went missing — here a false statement stands in its
    place.

    WHY THE TABLE IS BUILT FROM MECHANISMS AND NOT FROM COMMANDS. Three ways to
    make a tracker unreachable were known when this unit started (the file's
    bits, the directory's write bit, the directory's execute bit). Measurement
    found five more, each with its own errno and its own `sqlite3` message, and
    there is no reason to believe eight is the whole population either. So the
    fix is not "handle these eight": `_path_state` proves absence on exactly one
    condition and answers *could not tell* on every other, which is what makes a
    ninth mechanism safe by construction. `test_an_unenumerated_failure_reads_as
    _undetermined` is where that claim is checked directly.
    """

    def _where(self, cwd):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "where"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env={**os.environ},
        )

    @pytest.mark.parametrize("state", sorted(_TRACKER_STATES))
    def test_the_creation_promise_needs_proof_that_nothing_is_there(self, state, tmp_path):
        build, occupied = _TRACKER_STATES[state]
        db.init_project(str(tmp_path))
        restore = build(tmp_path / ".codebugs")
        try:
            proc = self._where(tmp_path)
        finally:
            if restore:
                restore()
        assert proc.returncode == 0, proc.stderr
        promised = _PROMISE in proc.stdout
        assert promised is not occupied, (
            f"{state}: `where` said {'nothing is' if promised else 'something may be'} "
            f"there, and the fixture put {'something' if occupied else 'nothing'} there"
        )

    @pytest.mark.parametrize("state", sorted(_TRACKER_STATES))
    def test_exists_is_false_only_on_proof_of_absence(self, state, tmp_path, monkeypatch):
        """The same property one layer down, where both consumers read it.

        `describe_root` is the shared resolver, so a consumer that got this
        right and one that got it wrong would be a divergence this repository
        deliberately made impossible (CB-11). Checking the dict as well as the
        printed line is what keeps the MCP preflight covered without a second
        table.
        """
        build, occupied = _TRACKER_STATES[state]
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        restore = build(tmp_path / ".codebugs")
        try:
            info = db.describe_root()  # must never raise (CB-11)
        finally:
            if restore:
                restore()
        assert (info["exists"] is False) is not occupied, f"{state}: {info}"
        # And the third value always carries its reason, so a caller never has
        # to print "could not tell" with nothing after it.
        if info["exists"] is None:
            assert isinstance(info["exists_reason"], str) and info["exists_reason"]
        else:
            assert info["exists_reason"] is None

    def test_an_unenumerated_failure_reads_as_undetermined(self, tmp_path, monkeypatch):
        """THE TEST THAT IS NOT A LIST. Every row above names a mechanism someone
        thought of; this one names none.

        An errno this tree has never handled — `EOVERFLOW` here, but the point is
        that it could be any of them, from a filesystem nobody here has mounted —
        must land in *could not tell* rather than in *there is nothing there*.
        That is a property of the classifier's DEFAULT arm, not of its list of
        cases, and it is the only reason the fix is expected to outlive the eight
        states that were measured.
        """
        path = str(tmp_path / ".codebugs" / "findings.db")

        def exploding_lstat(_p, *a, **kw):
            raise OSError(errno.EOVERFLOW, os.strerror(errno.EOVERFLOW), _p)

        monkeypatch.setattr(os, "lstat", exploding_lstat)
        kind, detail = db._path_state(path)
        assert kind is None
        assert "Value too large" in detail or detail

    def test_proof_of_absence_still_works(self, tmp_path):
        """The other half of the oracle, and the half a lazy fix would break.

        Answering *could not tell* to everything would satisfy every assertion
        above about untruth while destroying the CB-23 line the promise exists
        for. This is the pin that a fix which merely stopped talking would fail.
        """
        kind, detail = db._path_state(str(tmp_path / "nothing-here"))
        assert kind == db.PATH_ABSENT
        assert detail is None

    def test_a_dangling_symlink_is_not_proof_of_absence(self, tmp_path):
        """`lstat` before `stat`, and this is the state that decides the order.

        `os.stat` on a dangling symlink raises `FileNotFoundError` — byte for
        byte the answer an empty name gives — so a stat-first guard would call
        this proven absence. It is not: something IS at that name, and what
        happens next differs (a database gets created at the LINK'S TARGET, in
        another directory, or the open fails outright). Measured both ways.
        """
        link = tmp_path / "link.db"
        link.symlink_to(tmp_path / "does-not-exist.db")
        kind, detail = db._path_state(str(link))
        assert kind is None
        assert "symbolic link" in detail

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_the_declared_route_refuses_without_inventing_an_absence(self, tmp_path):
        """The same predicate, the other resolution route (CB-203 + CB-201).

        `_declared_db_path` and the `--repo` branch each answered the same
        two-valued question with `os.path.isdir`/`isfile`, so they too reported
        a tracker they could not look at as one that is not there — a refusal
        that sends the operator to run `init` over a tracker that already
        exists. Fixing only `describe_root` would have left two sites carrying
        the defect, which is this repository's most-repeated shape.

        It still REFUSES, and that half must not change: a declared root is
        ambient state, and fail-closed is what stops a stale export from
        quietly becoming a second, empty tracker (CB-8).
        """
        db.init_project(str(tmp_path))
        (tmp_path / ".codebugs").chmod(0o666)
        try:
            with pytest.raises(db.DatabaseNotFoundError) as excinfo:
                db._declared_db_path(str(tmp_path), "flag")
        finally:
            (tmp_path / ".codebugs").chmod(0o755)
        message = str(excinfo.value)
        assert "could not be determined" in message
        assert "holds no findings.db" not in message

    def test_a_name_too_long_is_not_reported_as_a_missing_tracker(self, tmp_path):
        """A mechanism that is not a permission at all, and was invisible.

        `os.path.isdir` folds `ENAMETOOLONG` into a plain False, so a declared
        root longer than the kernel's limit was refused with "which has no
        .codebugs/" — a statement about a tracker, for a cause that has nothing
        to do with one. Measured: the message now names the real reason.
        """
        with pytest.raises(db.DatabaseNotFoundError) as excinfo:
            db._declared_db_path("/" + "x" * 4900, "flag")
        assert "could not be determined" in str(excinfo.value)

    def test_a_nul_byte_in_the_path_is_reported_not_raised(self, tmp_path):
        """`os.path.isfile` swallowed `ValueError` for free; a stat does not.

        `describe_root` may NEVER raise (CB-11), so the guard that replaced
        `isfile` has to catch the one exception `os.lstat` raises that is not an
        `OSError`. Without this the never-raises contract would have been broken
        by the change that was supposed to make the function more honest.
        """
        kind, detail = db._path_state("/tmp/no\0pe")
        assert kind is None
        assert detail


class TestWritabilityProbeBoundaries:
    """CB-201 — the probe's undeclared edges, each measured before being named.

    The probe answers ONE question ("do the permission bits and the mount allow
    a write here"), and its docstring used to name only the check-then-act
    caveat while `_is_environmental`'s docstring, in the same file, listed the
    whole family it sits in front of. A boundary nobody wrote down is one the
    next reader assumes is covered.
    """

    def test_a_corrupt_database_still_reads_as_writable(self, tmp_path, monkeypatch):
        """The measured boundary, pinned so the docstring cannot quietly rot.

        `findings.db` full of non-database bytes: the permission bits are
        perfect, so the probe says True and `where` prints a clean binding,
        while the first verb to open the file dies. That is not a wrong answer
        to the probe's question — it is the question being narrower than "is
        this tracker usable", and no `os.access` call can widen it. Filed
        separately rather than papered over here.
        """
        db.init_project(str(tmp_path))
        (tmp_path / ".codebugs" / "findings.db").write_bytes(b"not a database at all\n" * 10)
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["exists"] is True
        assert info["writable"] is True

    def test_the_probe_reports_rather_than_raising_on_a_nul_byte(self):
        """CB-201 item 4: the tri-state's "could not look" arm caught `OSError`
        and nothing else, while the ONE exception `os.access` actually raises is
        a `ValueError` for a NUL byte in the path. The arm claimed a coverage it
        did not have. It is unreachable from `describe_root` by construction —
        `_path_state` refuses such a path first — so this exercises the function
        directly, and says so rather than implying the caller reaches it.
        """
        assert db._writable_probe("/tmp/no\0pe") is None


def _wall_traverse_bit(directory):
    """The measured CB-218 mechanism: a directory that cannot be ENTERED."""
    _skip_if_root()
    directory.chmod(0o666)
    return lambda: directory.chmod(0o755)


def _wall_symlink_loop(directory, name):
    """`name` is a symbolic link that points at itself through a second link."""
    (directory / name).symlink_to(directory / "loop")
    (directory / "loop").symlink_to(directory / name)
    return None


def _wall_dangling_symlink(directory, name):
    """`name` is a symbolic link to something that is not there.

    Worth having beside the permission mechanisms because it needs NO
    permission trick at all, so it is the one row of this table that also runs
    as root — and it is the state CB-203's docstring calls out as the reason
    `lstat` runs before `stat`.
    """
    (directory / name).symlink_to(directory / "no-such-target")
    return None


class TestTheWalkIsThreeValued:
    """CB-218: the walk decides WHICH tracker is found, and it did so two-valued.

    CB-203 made five sites three-valued, and every one of them looks at a root
    the walk has ALREADY chosen. This class is about the step before that. The
    harm is different in kind, not in degree: those five could print a wrong
    sentence about the right tracker, while this one silently binds to the wrong
    tracker and then answers, honestly and uselessly, about someone else's data.

    The states are built with the process ALREADY INSIDE the directory before the
    wall goes up, which is not a flourish — a directory whose ancestor lost its
    execute bit cannot be entered from outside, so a subprocess with `cwd=` set
    to it fails to start. That is also why these run in-process.
    """

    def _bind(self, deep, wall_up):
        """`describe_root()` as seen from `deep`, with the wall raised around it."""
        restore = wall_up()
        try:
            return db.describe_root()
        finally:
            if restore:
                restore()

    def _project(self, tmp_path, name):
        deep = tmp_path / name / "b" / "c"
        deep.mkdir(parents=True)
        db.init_project(str(tmp_path / name))
        return tmp_path / name, deep

    def test_nothing_above_it_the_refusal_stops_asserting_absence(self, tmp_path, monkeypatch):
        """State (1): a wall, and genuinely no tracker above it.

        Before: "no .codebugs/ found in <cwd> or any parent" at exit 1 — a claim
        about parents nobody could look at, which is CB-203's defect one function
        earlier. The tracker was sitting inside the wall the whole time.
        """
        project, deep = self._project(tmp_path, "A")
        monkeypatch.chdir(deep)
        info = self._bind(deep, lambda: _wall_traverse_bit(project))
        assert info["error"], "the walk must still find nothing — that half is unchanged"
        # THE HEAD CLAUSE, asserted as a literal, and it had to be. The first
        # version of this line read `"or any parent;" not in ...` — a NEGATIVE
        # about the old spelling — and a mutant that deleted the qualifier while
        # leaving the caveat appended SURVIVED it, because the caveat starts with
        # an em dash and the semicolon never returned. The test was weaker than
        # its own name: it pinned "a caveat is present", which mutant 5 already
        # covers, and not "the absence claim is qualified", which is this test's
        # whole subject. The sibling test on a healthy route asserts the
        # UNqualified spelling, so the pair pins both directions.
        assert "or any parent that could be examined" in info["error"]
        assert "could not be examined" in info["error"]
        assert str(project / ".codebugs") in info["error"]
        assert info["unexamined"], "the key must carry what the message names"

    def test_a_stranger_above_the_wall_is_bound_and_the_wall_is_named(self, tmp_path, monkeypatch):
        """State (2), the card's `high` half and this unit's central case.

        The project's own tracker is inside the wall; an unrelated one sits above
        it. Nothing errors, nothing is refused, and every line of the binding is
        correct about a tracker the user never meant. Only naming the place the
        walk could not look makes that visible — so BOTH halves are asserted: the
        binding still resolves (this is a diagnostic, not a refusal) and the
        skipped candidate is reported.
        """
        stranger = tmp_path / "P"
        project, deep = self._project(stranger, "A")
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        info = self._bind(deep, lambda: _wall_traverse_bit(project))
        assert info["error"] is None
        assert info["root"] == str(stranger), "the walk's own answer must not change"
        skipped = [path for path, _why in info["unexamined"]]
        assert str(project / ".codebugs") in skipped, (
            "the directory holding the real tracker was skipped and never mentioned"
        )
        assert all(why for _path, why in info["unexamined"]), "every entry carries a reason"

    def test_the_git_probe_is_the_same_defect_through_a_second_door(self, tmp_path, monkeypatch):
        """CB-218 §2's extension: a `.git` answer nobody got reads as "no boundary".

        This is the ISOLATED form, and that is what makes it evidence. The
        repository directory is perfectly readable, its `.codebugs/` is provably
        absent, and the ONLY question that cannot be answered is whether `.git`
        is there — so the walk crossing the boundary cannot be blamed on
        anything else. Fixing the `.codebugs` probe alone would leave this
        untouched: validating elements instead of their composition.
        """
        stranger = tmp_path / "OUT"
        repo = stranger / "G"
        deep = repo / "w" / "x"
        deep.mkdir(parents=True)
        db.init_project(str(stranger))
        _wall_symlink_loop(repo, ".git")
        monkeypatch.chdir(deep)
        info = db.describe_root()
        assert info["root"] == str(stranger), "walking past the boundary is unchanged behaviour"
        assert [path for path, _ in info["unexamined"]] == [str(repo / ".git")], (
            "exactly one question went unanswered, and it must be the one reported"
        )

    def test_a_real_repository_boundary_still_stops_the_walk_silently(self, tmp_path, monkeypatch):
        """The other half of the oracle, and the one a careless fix would break.

        A `.git` DIRECTORY answers the question, so the walk stops exactly as it
        always has and the key stays empty — empty meaning *the route ran and
        skipped nothing*, never *no such channel*.
        """
        stranger = tmp_path / "OUT"
        repo = stranger / "G"
        deep = repo / "w" / "x"
        deep.mkdir(parents=True)
        (repo / ".git").mkdir()
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        info = db.describe_root()
        assert info["error"], "a repo boundary with no tracker inside it still refuses"
        assert "or any parent;" in info["error"], "the healthy refusal must be byte-identical"
        assert info["unexamined"] == ()

    def test_an_ordinary_tracker_reports_an_empty_list_rather_than_no_key(
        self, tmp_path, monkeypatch
    ):
        """Silence on a healthy walk is half the oracle — and the key is still there.

        A consumer must never have to tell a missing key from an empty one, so
        this asserts the key's PRESENCE and its emptiness separately.
        """
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert "unexamined" in info, "the key is unconditional"
        assert info["unexamined"] == ()

    def test_a_route_that_never_walked_reports_empty_because_it_asked_nothing(
        self, tmp_path, monkeypatch
    ):
        """The declared route resolves ONE named path and does no walking at all.

        Its empty list is therefore a fact rather than a default, which is what
        lets every consumer read empty as "the route skipped nothing".
        """
        declared = tmp_path / "declared"
        declared.mkdir()
        db.init_project(str(declared))
        monkeypatch.setattr(db, "_tracker_root_override", str(declared))
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["source"] == "flag"
        assert info["unexamined"] == ()

    @pytest.mark.parametrize(
        "mechanism",
        ["traverse_bit", "symlink_loop", "dangling_symlink"],
        ids=["traverse_bit", "symlink_loop", "dangling_symlink"],
    )
    def test_every_way_of_failing_to_look_is_recorded_not_swallowed(
        self, mechanism, tmp_path, monkeypatch
    ):
        """A TABLE, because the harm is a property of the primitive, not of one errno.

        Three mechanisms, only one of which is the permission wall the card
        measured. The dangling-symlink row needs no permission trick, so it also
        runs as root; the loop row is what proves the `.git` case above is not a
        peculiarity of permissions.
        """
        stranger = tmp_path / "P"
        holder = stranger / "A"
        deep = holder / "b" / "c"
        deep.mkdir(parents=True)
        # Inner tracker FIRST: `init_project` refuses to create one an existing
        # enclosing tracker would shadow, so the natural history is the only
        # order that builds this state without `force`.
        if mechanism == "traverse_bit":
            db.init_project(str(holder))
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        if mechanism == "traverse_bit":
            restore = _wall_traverse_bit(holder)
        elif mechanism == "symlink_loop":
            restore = _wall_symlink_loop(holder, ".codebugs")
        else:
            restore = _wall_dangling_symlink(holder, ".codebugs")
        try:
            info = db.describe_root()
        finally:
            if restore:
                restore()
        assert info["root"] == str(stranger)
        assert str(holder / ".codebugs") in [path for path, _ in info["unexamined"]], (
            f"{mechanism}: the walk read *could not look* as *nothing is there*"
        )

    def test_an_unenumerated_failure_on_the_walk_is_recorded_too(self, tmp_path, monkeypatch):
        """THE ROW THAT IS NOT A MECHANISM — the same shape CB-203 needed.

        Every row above names something someone thought of. This one names an
        errno from a filesystem nobody here has mounted, and it must land in
        *recorded* by construction rather than because it was listed. Only the
        candidate path is made to fail, so the rest of the walk is real.
        """
        stranger = tmp_path / "P"
        holder = stranger / "A"
        deep = holder / "b"
        deep.mkdir(parents=True)
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        real_lstat = os.lstat

        def selective_lstat(path, *a, **kw):
            if str(path) == str(holder / ".codebugs"):
                raise OSError(errno.EOVERFLOW, os.strerror(errno.EOVERFLOW), str(path))
            return real_lstat(path, *a, **kw)

        monkeypatch.setattr(os, "lstat", selective_lstat)
        info = db.describe_root()
        assert info["root"] == str(stranger)
        assert str(holder / ".codebugs") in [path for path, _ in info["unexamined"]]

    def test_the_directory_holding_the_tracker_reaches_the_human_text(
        self, tmp_path, monkeypatch
    ):
        """The entry that names the WALL must survive into prose, not just the key.

        One wall makes every question below it unanswerable too, so the list runs
        deepest-first and the causal entry — the directory that actually holds
        the user's tracker — sits at the END of it. A truncating formatter was
        written first and kept the deepest three, which are the wall's shadows:
        paths that never existed, while the one a reader must go and fix fell
        off. Measured, which is why nothing truncates now. This test is the pin
        on that: it asserts the SHALLOWEST entry reaches the text, which is
        exactly what any future cap would break.
        """
        stranger = tmp_path / "P"
        project, deep = self._project(stranger, "A")
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        info = self._bind(deep, lambda: _wall_traverse_bit(project))
        assert len(info["unexamined"]) > 3, (
            "fixture no longer produces a long list — it proves nothing now"
        )
        phrases = db.unexamined_phrases(info["unexamined"])
        assert len(phrases) == len(info["unexamined"]), "every entry reaches the text"
        assert any(str(project / ".codebugs") in phrase for phrase in phrases)

    def test_the_recorded_reason_is_the_real_one_not_a_generic_fallback(
        self, tmp_path, monkeypatch
    ):
        """CB-224 closes one of two blind spots T-97's own acceptance found.

        `_walk_db_root` writes `detail or "could not look at it"` — a fallback
        that only matters if `_path_state` ever returned an EMPTY detail, which
        it never does today. A mutant that drops the real `_why(e)` text and
        substitutes the generic phrase unconditionally passed every existing
        test in this class, because every one of them only asserts a reason is
        PRESENT (`all(why for _path, why in info["unexamined"])`), never that it
        is the SPECIFIC one the operating system actually gave. Asserted for the
        permission-wall mechanism here; the symlink-loop mechanism is the sibling
        test right below, so a mutant cannot pass by hardcoding one string.
        """
        stranger = tmp_path / "P"
        project, deep = self._project(stranger, "A")
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        info = self._bind(deep, lambda: _wall_traverse_bit(project))
        reasons = [why for _path, why in info["unexamined"]]
        assert any("Permission denied" in why for why in reasons), reasons

    def test_a_second_mechanism_also_carries_its_own_specific_reason(
        self, tmp_path, monkeypatch
    ):
        """The sibling of the test above, on the symlink-loop mechanism (ELOOP).

        Together the two pin that the reason text tracks WHICH failure actually
        happened, not merely THAT one happened.
        """
        stranger = tmp_path / "P"
        holder = stranger / "A"
        deep = holder / "b" / "c"
        deep.mkdir(parents=True)
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        _wall_symlink_loop(holder, ".codebugs")
        info = db.describe_root()
        reasons = [why for _path, why in info["unexamined"]]
        assert any("Too many levels of symbolic links" in why for why in reasons), reasons

    def test_the_unexamined_order_is_deepest_first_not_its_reverse(
        self, tmp_path, monkeypatch
    ):
        """CB-224 closes the second of T-97's own two blind spots.

        Every existing order-sensitive assertion in this class only checks that
        the SHALLOWEST entry (the one naming the wall itself) reaches the text —
        true whether the list is deepest-first or its exact reverse, since a
        bare `.reverse()` mutant keeps that one entry in the list, merely moved
        to the front instead of the back. This pins the FULL sequence, which a
        reversal cannot survive.
        """
        stranger = tmp_path / "P"
        project, deep = self._project(stranger, "A")
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        info = self._bind(deep, lambda: _wall_traverse_bit(project))
        expected = [
            str(deep / ".codebugs"),
            str(deep / ".git"),
            str(deep.parent / ".codebugs"),
            str(deep.parent / ".git"),
            str(project / ".codebugs"),
            str(project / ".git"),
        ]
        assert [p for p, _ in info["unexamined"]] == expected


class TestTheCreationPromiseIsChecked:
    """CB-219: `exists is False` is the one branch that PROMISES, so it needs proof.

    The classification is right — nothing IS at that name — and the inference
    drawn from it was not. Measured on the unfixed tree: an empty `.codebugs/`
    at `chmod 555` printed "the next command creates one" at exit 0, and the very
    next verb refused with "cannot open findings.db … for writing".
    """

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_an_unwritable_tracker_directory_is_reported(self, tmp_path, monkeypatch):
        (tmp_path / ".codebugs").mkdir()
        (tmp_path / ".codebugs").chmod(0o555)
        monkeypatch.chdir(tmp_path)
        try:
            info = db.describe_root()
        finally:
            (tmp_path / ".codebugs").chmod(0o755)
        assert info["exists"] is False, "the file really is absent — that half must not change"
        assert info["dir_writable"] is False

    def test_a_writable_half_made_tracker_keeps_its_ordinary_answer(self, tmp_path, monkeypatch):
        """The CB-23 self-healing state, which must stay exactly as it was."""
        (tmp_path / ".codebugs").mkdir()
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["exists"] is False
        assert info["dir_writable"] is True

    def test_the_question_is_not_asked_when_the_database_is_there(self, tmp_path, monkeypatch):
        """`None` unless `exists` is False — the mirror of `writable`'s own rule.

        Writability of a directory whose database already exists is `writable`'s
        question, and two keys answering one question is how they come to
        disagree.
        """
        db.init_project(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["exists"] is True
        assert info["dir_writable"] is None

    def test_the_key_survives_a_route_that_resolved_nothing(self, tmp_path, monkeypatch):
        """Unconditional, like every other key here — `describe_root` never raises."""
        monkeypatch.chdir(tmp_path)
        info = db.describe_root()
        assert info["error"]
        assert "dir_writable" in info
        assert info["dir_writable"] is None


class TestWhereSpeaksAboutWhatItCouldNotSee:
    """CB-218/CB-219 at the CONSUMER, because the resolver is not what a user reads.

    In-process rather than through a subprocess, and that is forced by the state
    itself: a directory whose ancestor has lost its execute bit cannot be
    ENTERED, so `subprocess.run(..., cwd=deep)` fails to start. The process has
    to be standing there before the wall goes up. `where` never writes, so
    running it in-process costs nothing.
    """

    def _where(self, monkeypatch, capsys):
        from codebugs import cli

        monkeypatch.setattr(sys, "argv", ["codebugs", "where"])
        cli.main()
        return capsys.readouterr()

    def test_a_clean_binding_over_a_wall_is_no_longer_silent(
        self, tmp_path, monkeypatch, capsys
    ):
        """THE CENTRAL CASE. Before this, every printed line was correct and the
        whole answer was a lie: a stranger's tracker, at exit 0, with no warning.
        """
        stranger = tmp_path / "P"
        project = stranger / "A"
        deep = project / "b" / "c"
        deep.mkdir(parents=True)
        db.init_project(str(project))
        db.init_project(str(stranger))
        monkeypatch.chdir(deep)
        restore = _wall_traverse_bit(project)
        try:
            out = self._where(monkeypatch, capsys)
        finally:
            restore()
        assert f"root:     {stranger}" in out.out, "the binding itself must not change"
        assert "could not be examined" in out.out
        assert str(project / ".codebugs") in out.out
        # CB-182: parenthetical continuations of this table live on stdout, so
        # `codebugs where 2>/dev/null` does not lose them.
        assert "could not be examined" not in out.err

    def test_a_healthy_tracker_gains_no_new_line(self, tmp_path, monkeypatch, capsys):
        """Silence on a healthy walk is half the oracle, and it is asserted on a
        NON-EMPTY set of ordinary states rather than on one, or it would be green
        by construction.
        """
        for name in ("plain", "nested/deeper"):
            root = tmp_path / name
            root.mkdir(parents=True)
            db.init_project(str(root))
            monkeypatch.chdir(root)
            out = self._where(monkeypatch, capsys)
            assert "could not be examined" not in out.out + out.err, name
            assert f"root:     {root}" in out.out, name

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_the_creation_promise_is_withdrawn_when_the_directory_refuses(
        self, tmp_path, monkeypatch, capsys
    ):
        """CB-219 at the consumer: the fact survives, the promise does not."""
        (tmp_path / ".codebugs").mkdir()
        (tmp_path / ".codebugs").chmod(0o555)
        monkeypatch.chdir(tmp_path)
        try:
            out = self._where(monkeypatch, capsys)
        finally:
            (tmp_path / ".codebugs").chmod(0o755)
        assert "no database there yet" in out.out, "the CB-23 fact must not vanish"
        assert out.out.count("no database there yet") == 1, "and must not be doubled"
        assert "the next command creates one" not in out.out
        assert "may not be writable" in out.out

    def test_a_writable_half_made_tracker_keeps_the_promise(self, tmp_path, monkeypatch, capsys):
        """The other half: CB-23's self-healing line is untouched where it is true."""
        (tmp_path / ".codebugs").mkdir()
        monkeypatch.chdir(tmp_path)
        out = self._where(monkeypatch, capsys)
        assert "(no database there yet — the next command creates one)" in out.out


class TestOpenCallSitesRatchet:
    """`_open` is the only door to a live connection. Guard how many hold a key.

    Everything CB-23 buys rests on `_open(create=...)` having exactly two callers:
    `init_project`, and `connect` (which passes the walk's may-create flag). A
    third one — some future "repair" or "ensure" helper reaching for `_open`
    because `connect` refused it — would reopen the hole quietly, and nothing but
    review would notice. Prose is the wrong enforcement layer for that, which this
    repo has established twice over (the `BEGIN IMMEDIATE` allowlist in
    `test_claims.py`, and `EntityKind.__post_init__`). This allowlist may shrink,
    never grow.
    """

    def test_only_two_call_sites_may_open_a_connection(self):
        src = Path(db.__file__).parent
        calls = [
            (path.name, line.strip())
            for path in src.rglob("*.py")
            for line in path.read_text().splitlines()
            if "_open(" in line
            and not line.lstrip().startswith("#")
            and not line.lstrip().startswith("def _open(")
        ]
        assert len(calls) == 2, f"new _open() call site(s) — is this deliberate? {calls}"
        assert all(name == "db.py" for name, _ in calls), calls


class TestWhereCommand:
    """`codebugs where` — the single moment that says where this process is pointed."""

    def _where(self, cwd, *args, env=None):
        environ = {**os.environ, **(env or {})}
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args, "where"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=environ,
        )

    def test_prints_the_discovered_root(self, tmp_path):
        db.init_project(str(tmp_path))
        proc = self._where(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert str(tmp_path) in proc.stdout
        assert "discovery" in proc.stdout

    def test_says_when_the_resolved_database_is_not_there_yet(self, tmp_path):
        """Resolving is not being there — and the walk resolves a bare directory.

        Without this line `where` prints a path that does not exist and calls it
        the project's tracker, which is the same shape as the CB-13 misbinding
        (where the wrong root is a stray directory). CB-23.
        """
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        proc = self._where(repo)
        assert proc.returncode == 0, proc.stderr
        assert str(repo) in proc.stdout
        assert "no database there yet" in proc.stdout
        assert not (repo / ".codebugs" / "findings.db").exists(), "`where` must not create"

    def test_is_quiet_about_existence_when_the_database_is_really_there(self, tmp_path):
        db.init_project(str(tmp_path))
        proc = self._where(tmp_path)
        assert "no database there yet" not in proc.stdout

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_warns_when_the_file_is_unwritable(self, tmp_path):
        """CB-100: the diagnostic and the thing it diagnoses must agree.

        Before this fix `where` printed a clean binding for every one of the
        three states the owner reproduced, while `stats` on the same tracker
        refused with a precise "for writing" message.

        CB-182: the warning moved from stderr to stdout, alongside the rest of
        the table (`root:`/`database:`/the sibling "no database there yet"
        note) — it is asserted IN stdout and explicitly ABSENT from stderr, so
        this test would catch a regression in either direction.
        """
        db.init_project(str(tmp_path))
        findings_path = tmp_path / ".codebugs" / "findings.db"
        findings_path.chmod(0o000)
        try:
            proc = self._where(tmp_path)
        finally:
            findings_path.chmod(0o644)
        assert proc.returncode == 0, proc.stderr
        assert "may not be writable" in proc.stdout
        assert "may not be writable" not in proc.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_warns_when_the_directory_is_unwritable(self, tmp_path):
        """The state that decides the mechanism — see TestWritabilityProbe:
        the FILE's own bits stay writable-looking, only the DIRECTORY refuses.

        CB-182: same stream move as the file-unwritable sibling above.
        """
        db.init_project(str(tmp_path))
        codebugs_dir = tmp_path / ".codebugs"
        codebugs_dir.chmod(0o555)
        try:
            proc = self._where(tmp_path)
        finally:
            codebugs_dir.chmod(0o755)
        assert proc.returncode == 0, proc.stderr
        assert "may not be writable" in proc.stdout
        assert "may not be writable" not in proc.stderr

    def test_is_quiet_about_writability_on_a_nonempty_writable_tracker(self, tmp_path):
        """Half the oracle, and the one most likely to pass vacuously: a
        healthy, NONEMPTY tracker (CB-100 §7 — an empty one proves nothing)
        must still print no writability warning at all.
        """
        db.init_project(str(tmp_path))
        conn = db.connect(str(tmp_path))
        findings.add_finding(
            conn, severity="low", category="x", file="f.py", description="d", new_category=True
        )
        conn.close()
        proc = self._where(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "may not be writable" not in proc.stdout + proc.stderr

    def test_the_no_database_yet_line_does_not_grow_a_second_writability_line(self, tmp_path):
        """The CB-23 note must neither disappear nor be joined by a second,
        colliding line about writability (CB-100 §7).
        """
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        proc = self._where(repo)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.count("no database there yet") == 1
        assert "may not be writable" not in proc.stdout + proc.stderr

    def test_names_the_env_channel(self, tmp_path):
        declared = _tracker(tmp_path, "declared")
        other = tmp_path / "other"
        other.mkdir()
        proc = self._where(other, env={db.ENV_ROOT: str(declared)})
        assert proc.returncode == 0, proc.stderr
        assert str(declared) in proc.stdout
        assert db.ENV_ROOT in proc.stdout

    def test_names_the_flag_channel(self, tmp_path):
        declared = _tracker(tmp_path, "declared")
        other = tmp_path / "other"
        other.mkdir()
        proc = self._where(other, "--tracker-root", str(declared))
        assert proc.returncode == 0, proc.stderr
        assert str(declared) in proc.stdout
        assert "--tracker-root" in proc.stdout

    def test_a_relative_flag_is_printed_absolute(self, tmp_path):
        """CB-49: `init` and `where` must answer in one coordinate system."""
        declared = _tracker(tmp_path, "declared")
        other = tmp_path / "other"
        other.mkdir()
        proc = self._where(other, "--tracker-root", "../declared")
        assert proc.returncode == 0, proc.stderr
        assert str(declared) in proc.stdout
        assert "../declared" not in proc.stdout

    def test_unbound_is_a_clean_failure(self, tmp_path):
        proc = self._where(tmp_path)
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
        assert "codebugs init" in proc.stdout + proc.stderr

    def test_the_flag_reaches_a_real_command(self, tmp_path):
        """The flag is global, not `where`-only — it must bind every verb."""
        declared = _tracker(tmp_path, "declared")
        other = tmp_path / "other"
        other.mkdir()
        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "--tracker-root", str(declared), "stats"],
            cwd=str(other),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr


class TestCb182WhereWritabilityStreamParity:
    """CB-182 oracle (L3-BRIEF-DIR-1-T88-cb182-where-stream-asymmetry.md §3).

    `_cmd_where` used to print its two parenthetical continuations of the
    three-line table on DIFFERENT streams for no reason tied to what either
    one says: the "no database there yet" note (CB-23) went to stdout, while
    the "may not be writable" note (CB-100) went to stderr, even though
    neither is an error (the exit code stays 0 on both). `codebugs where
    2>/dev/null` — the ordinary way to get a clean view or feed a script — was
    therefore silently dropping the writability warning and fully restoring
    the CB-100 defect that warning exists to close.

    Every row below captures the two output streams SEPARATELY and, where the
    oracle names the `2>/dev/null` form, redirects the REAL file descriptor 2
    to `/dev/null` before exec via `_where_devnull_stderr` — not merely a
    combined-capture test that ignores `proc.stderr`, which would stay green
    whether or not the warning had moved streams and is exactly the
    "green by construction" trap the brief warns about.
    """

    def _where(self, cwd, *args, env=None):
        environ = {**os.environ, **(env or {})}
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args, "where"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=environ,
        )

    def _where_devnull_stderr(self, cwd, *args, env=None):
        """The literal `codebugs where 2>/dev/null` a human types: stderr is
        redirected to the real null device before exec, so its bytes never
        reach a pipe this test could read even if it wanted to."""
        environ = {**os.environ, **(env or {})}
        with open(os.devnull, "wb") as devnull:
            return subprocess.run(
                [sys.executable, "-m", "codebugs.cli", *args, "where"],
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=devnull,
                text=True,
                env=environ,
            )

    # --- rows 1/2: a healthy tracker — must be silent on both forms --------

    def test_row1_healthy_plain(self, tmp_path):
        db.init_project(str(tmp_path))
        proc = self._where(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "source:" in proc.stdout
        assert "root:" in proc.stdout
        assert "database:" in proc.stdout
        assert "may not be writable" not in proc.stdout
        assert "may not be writable" not in proc.stderr
        assert "no database there yet" not in proc.stdout

    def test_row2_healthy_stderr_to_devnull(self, tmp_path):
        db.init_project(str(tmp_path))
        proc = self._where_devnull_stderr(tmp_path)
        assert proc.returncode == 0
        assert str(tmp_path) in proc.stdout
        assert "may not be writable" not in proc.stdout
        assert "no database there yet" not in proc.stdout

    # --- rows 3/4: `.codebugs/` exists, no database yet (CB-23 control) ----

    def test_row3_no_database_yet_plain(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        proc = self._where(repo)
        assert proc.returncode == 0, proc.stderr
        assert "no database there yet" in proc.stdout

    def test_row4_no_database_yet_stderr_to_devnull(self, tmp_path):
        """Control: this branch already printed to stdout before CB-182, so
        it must be unaffected — the oracle's own check that this row was
        already right."""
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        proc = self._where_devnull_stderr(repo)
        assert proc.returncode == 0
        assert "no database there yet" in proc.stdout

    # --- rows 5/6: database FILE unwritable — the core of CB-182 -----------

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_row5_file_unwritable_plain(self, tmp_path):
        db.init_project(str(tmp_path))
        findings_path = tmp_path / ".codebugs" / "findings.db"
        findings_path.chmod(0o000)
        try:
            proc = self._where(tmp_path)
        finally:
            findings_path.chmod(0o644)
        assert proc.returncode == 0, proc.stderr
        assert "may not be writable" in proc.stdout

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_row6_file_unwritable_stderr_to_devnull(self, tmp_path):
        """THE red test named by the brief (§4.1): on unfixed `main` this
        warning printed to stderr, so under a REAL `2>/dev/null` it vanished
        and an unwritable tracker looked healthy again — CB-100 fully
        restored through this one ordinary invocation form. Must be red
        before the fix in `_cmd_where`, green after.
        """
        db.init_project(str(tmp_path))
        findings_path = tmp_path / ".codebugs" / "findings.db"
        findings_path.chmod(0o000)
        try:
            proc = self._where_devnull_stderr(tmp_path)
        finally:
            findings_path.chmod(0o644)
        assert proc.returncode == 0
        assert "may not be writable" in proc.stdout

    # --- rows 7/8: a declared root that does not resolve — untouched -------
    #
    # THESE TWO ROWS USED TO BUILD A DIFFERENT STATE FROM THE ONE THEY NAME, and
    # that is CB-204's second half. They ran `where` in a bare `tmp_path` with
    # NOTHING declared, so what they actually built was "the WALK finds no
    # tracker anywhere above" — which produces the same `(unresolved)` line only
    # for as long as the machine happens to have no `.codebugs/` above pytest's
    # temporary root. On the day one appeared in `/tmp`, the walk resolved, `where`
    # exited 0, and both rows failed while asserting something true about the
    # product. A test green by construction of the ENVIRONMENT is a false
    # witness, not a coverage gap.
    #
    # They now declare the root through the channel the name promises. That
    # channel OUTRANKS the walk (`_db_path`: argument, then `--tracker-root`,
    # then `CODEBUGS_ROOT`, then discovery), so the outcome no longer depends on
    # what is or is not above the temporary tree — and the `foreign_above`
    # parameter proves exactly that by putting a tracker there on purpose and
    # demanding an identical verdict.

    @pytest.mark.parametrize("foreign_above", [False, True])
    def test_row7_unresolved_root_is_a_real_error_plain(self, tmp_path, foreign_above):
        """The error branch is explicitly NOT in scope for CB-182 (brief §2):
        it stays an error, in stderr, at exit 1."""
        cwd, declared = self._unresolved_root_stage(tmp_path, foreign_above)
        proc = self._where(cwd, "--tracker-root", str(declared))
        assert proc.returncode == 1
        assert "(unresolved)" in proc.stdout
        assert proc.stderr.strip() != ""

    @pytest.mark.parametrize("foreign_above", [False, True])
    def test_row8_unresolved_root_stdout_survives_stderr_redirect(self, tmp_path, foreign_above):
        cwd, declared = self._unresolved_root_stage(tmp_path, foreign_above)
        proc = self._where_devnull_stderr(cwd, "--tracker-root", str(declared))
        assert proc.returncode == 1
        assert "root:" in proc.stdout
        assert "(unresolved)" in proc.stdout

    def _unresolved_root_stage(self, tmp_path, foreign_above):
        """Build "a declared root that does not resolve", and nothing else.

        `--tracker-root` names a directory holding no `.codebugs/`, which fails
        closed by design (CB-23: on a NAMED root the tracker is the file, not the
        directory). With `foreign_above` the invocation additionally stands under
        a tracker the walk would happily find — the state that used to decide
        these rows, and that must now decide nothing.
        """
        declared = tmp_path / "declared-but-empty"
        declared.mkdir()
        stage = tmp_path / ("under-a-foreign-tracker" if foreign_above else "clean")
        cwd = stage / "here"
        cwd.mkdir(parents=True)
        if foreign_above:
            (stage / ".codebugs").mkdir()
            assert db._find_db_root(str(cwd)) == str(stage.resolve()), (
                "premise: without the declaration the walk really would resolve here"
            )
        return cwd, declared

    # --- row 9: a DIFFERENT way to make the tracker unwritable -------------

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_row9_directory_unwritable_agrees_with_file_unwritable(self, tmp_path):
        """CB-182 §3 mandates this row explicitly and by name: reproduce the
        'unwritable' state by a DIFFERENT mechanism than rows 5/6 — permission
        bits on the `.codebugs/` DIRECTORY rather than on the database file —
        because a prior unit's oracle in this same direction enumerated call
        forms and its acceptance found an ordinary one missing from the list
        (see brief §3). A rule expressed as an enumeration checks the
        author's imagination, not the program's behaviour; this row is the
        antidote. Both invocation forms must agree with each other and with
        the file-based rows above — if they diverge, that is a finding to
        escalate, not a brief mismatch to paper over (brief §6).
        """
        db.init_project(str(tmp_path))
        codebugs_dir = tmp_path / ".codebugs"
        codebugs_dir.chmod(0o555)
        try:
            proc_plain = self._where(tmp_path)
            proc_devnull = self._where_devnull_stderr(tmp_path)
        finally:
            codebugs_dir.chmod(0o755)
        assert proc_plain.returncode == 0, proc_plain.stderr
        assert proc_devnull.returncode == 0
        assert "may not be writable" in proc_plain.stdout
        assert "may not be writable" not in proc_plain.stderr
        assert "may not be writable" in proc_devnull.stdout


class TestInitUnderADeclaredRoot:
    """Where `init` creates depends on the CHANNEL that declared the root (CB-48).

    `$CODEBUGS_ROOT` is ambient — it may have been exported days ago and
    inherited by an unrelated subprocess — so creation driven by it is the
    failure this project refuses everywhere else, and `init` still creates where
    the user stands. `--tracker-root DIR` is typed on the same command line and
    is honoured. Either way a surviving mismatch is announced, because a tracker
    nothing ever reads is a success-shaped dead end.

    These are the ENV half; `TestInitUnderTheTrackerRootFlag` is the flag half.
    """

    def test_init_ignores_the_declared_root(self, tmp_path, monkeypatch):
        declared = _tracker(tmp_path, "declared")
        here = tmp_path / "here"
        here.mkdir()
        monkeypatch.chdir(here)
        monkeypatch.setenv(db.ENV_ROOT, str(declared))
        result = db.init_project()
        assert result["root"] == str(here)
        assert (here / ".codebugs").is_dir()

    def test_cli_init_warns_on_mismatch(self, tmp_path):
        declared = _tracker(tmp_path, "declared")
        here = tmp_path / "here"
        here.mkdir()
        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "init"],
            cwd=str(here),
            capture_output=True,
            text=True,
            env={**os.environ, db.ENV_ROOT: str(declared)},
        )
        assert proc.returncode == 0, proc.stderr
        assert db.ENV_ROOT in proc.stderr
        assert str(declared) in proc.stderr

    def test_cli_init_is_silent_when_the_declaration_agrees(self, tmp_path):
        here = tmp_path / "here"
        here.mkdir()
        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "init"],
            cwd=str(here),
            capture_output=True,
            text=True,
            env={**os.environ, db.ENV_ROOT: str(here)},
        )
        assert proc.returncode == 0, proc.stderr
        assert "warning" not in proc.stderr.lower()


class TestInitUnderTheTrackerRootFlag:
    """`--tracker-root DIR init` initializes DIR (CB-48).

    The flag half of the split documented on `TestInitUnderADeclaredRoot`: unlike
    `$CODEBUGS_ROOT`, `--tracker-root` is typed on the very command line being
    run, so it is an assertion about this invocation rather than ambient state.

    What made the old behaviour worse than a plain ignored flag: the warning
    `_cmd_init` printed said "commands will read DIR, not CWD" and the process
    then initialized CWD — two adjacent lines telling the user the opposite of
    what landed on disk. So every test here asserts on the DIRECTORY THAT DID
    NOT GET A TRACKER as well as on the one that did; asserting only the target
    would pass against the defect, which created *both* (cwd's for real, DIR's in
    the reader's head).
    """

    def _init(self, cwd, *argv):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *argv],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )

    def test_flag_initializes_the_named_directory_not_cwd(self, tmp_path):
        """The card's reproduction, both directories empty."""
        here = tmp_path / "here"
        target = tmp_path / "target"
        here.mkdir()
        target.mkdir()

        proc = self._init(here, "--tracker-root", str(target), "init")

        assert proc.returncode == 0, proc.stderr
        assert (target / ".codebugs" / db.DB_FILE).is_file()
        assert not (here / ".codebugs").exists(), "must not pollute cwd with an unasked tracker"
        assert str(target) in proc.stdout

    def test_flag_init_says_nothing_misleading(self, tmp_path):
        """Flag and result now agree, so the mismatch warning must not fire."""
        here = tmp_path / "here"
        target = tmp_path / "target"
        here.mkdir()
        target.mkdir()

        proc = self._init(here, "--tracker-root", str(target), "init")

        assert proc.returncode == 0, proc.stderr
        assert "warning" not in proc.stderr.lower(), proc.stderr

    def test_flag_init_is_unaffected_by_an_already_initialized_cwd(self, tmp_path):
        """The card's second harm: `Already initialized:` reported for the WRONG tracker.

        `init` is idempotent, so standing in an initialized project used to make
        the whole call a no-op that still exited 0 — the user reasonably read
        that as "my scratch tracker is ready" when nothing had been created.
        """
        here = tmp_path / "here"
        target = tmp_path / "target"
        here.mkdir()
        target.mkdir()
        db.init_project(str(here))

        proc = self._init(here, "--tracker-root", str(target), "init")

        assert proc.returncode == 0, proc.stderr
        assert (target / ".codebugs" / db.DB_FILE).is_file()
        assert "Initialized" in proc.stdout and "Already initialized" not in proc.stdout
        assert str(here) not in proc.stdout

    def test_flag_init_then_a_read_verb_agree(self, tmp_path):
        """The end-to-end property: the next command must find what `init` made.

        The mitigating circumstance on the card was that `where` failed loudly on
        the following step. That divergence is what this closes.
        """
        here = tmp_path / "here"
        target = tmp_path / "target"
        here.mkdir()
        target.mkdir()

        assert self._init(here, "--tracker-root", str(target), "init").returncode == 0
        proc = self._init(here, "--tracker-root", str(target), "where")

        assert proc.returncode == 0, proc.stderr
        assert str(target / ".codebugs" / db.DB_FILE) in proc.stdout

    def test_an_explicit_directory_outranks_the_flag_and_is_announced(self, tmp_path):
        """Argument > flag, the same precedence `_resolve_db` applies to reads.

        This is the one case where the flag is deliberately NOT the create
        target, so it is also the one that must keep warning: reads will go to
        the flag's tracker, not the one just made.
        """
        here = tmp_path / "here"
        flagged = _tracker(tmp_path, "flagged")
        positional = tmp_path / "positional"
        here.mkdir()
        positional.mkdir()

        proc = self._init(here, "--tracker-root", str(flagged), "init", str(positional))

        assert proc.returncode == 0, proc.stderr
        assert (positional / ".codebugs" / db.DB_FILE).is_file()
        assert "warning" in proc.stderr.lower()
        assert str(flagged) in proc.stderr

    def test_flag_naming_a_missing_directory_fails_loudly(self, tmp_path):
        """A typo must not fall back to creating a tracker in cwd."""
        here = tmp_path / "here"
        here.mkdir()
        missing = tmp_path / "nope"

        proc = self._init(here, "--tracker-root", str(missing), "init")

        assert proc.returncode == 1
        assert str(missing) in proc.stderr
        assert not (here / ".codebugs").exists()
        assert not missing.exists()

    def test_env_root_still_does_not_redirect_creation(self, tmp_path):
        """The split is real: the ambient channel keeps the old behaviour.

        Guards against "fixing" CB-48 by honouring any declaration, which would
        let a stale export inherited by an unrelated subprocess conjure a tracker
        somewhere the user never was.
        """
        here = tmp_path / "here"
        target = tmp_path / "target"
        here.mkdir()
        target.mkdir()

        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "init"],
            cwd=str(here),
            capture_output=True,
            text=True,
            env={**os.environ, db.ENV_ROOT: str(target)},
        )

        assert proc.returncode == 0, proc.stderr
        assert (here / ".codebugs" / db.DB_FILE).is_file()
        assert not (target / ".codebugs").exists()
        assert "warning" in proc.stderr.lower()

    def test_flag_beats_env_for_the_create_target(self, tmp_path):
        """Precedence is one rule, not a per-verb rule: flag > env on writes too."""
        here = tmp_path / "here"
        flagged = tmp_path / "flagged"
        env_root = tmp_path / "env_root"
        for d in (here, flagged, env_root):
            d.mkdir()

        proc = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "--tracker-root", str(flagged), "init"],
            cwd=str(here),
            capture_output=True,
            text=True,
            env={**os.environ, db.ENV_ROOT: str(env_root)},
        )

        assert proc.returncode == 0, proc.stderr
        assert (flagged / ".codebugs" / db.DB_FILE).is_file()
        assert not (env_root / ".codebugs").exists()
        assert not (here / ".codebugs").exists()


class TestConnectDoesNotWaitOnUnconditionalSchemaSeedWrite:
    """CB-195 (+ CB-192, the counter's own write, is exercised in test_server.py).

    `db.connect()` runs every registered module's `ensure_schema` on EVERY open —
    that is `_open`'s own loop over `_resolved_order()`. Two of those functions,
    `merge.ensure_schema` and `milestones._schema.ensure_schema`, used to seed
    their tables with an UNCONDITIONAL `INSERT OR IGNORE`. The row this inserts
    exists from the very first open onward, so every later insert is a
    guaranteed no-op — but SQLite still takes the write lock to attempt it, and a
    write attempt honours `busy_timeout` even when the caller only wanted to
    read. A purely-reading `db.connect()` therefore contended with ANY
    concurrent writer holding that lock, for up to the full `busy_timeout`.

    Rule (a) from CLAUDE.md's Testing section: the final state must
    DISCRIMINATE fixed from unfixed code. Here that state is wall-clock time —
    unfixed `db.connect()` blocks for roughly as long as the external writer
    holds the lock (it tries its own write internally); fixed code reads
    first, finds the row already there, and returns in low single-digit
    milliseconds because WAL readers never block on a writer at all.
    Rule (b): never wait unboundedly on a losing writer. There isn't one here —
    the external holder is not blocked by anything, so it runs on a plain,
    bounded sleep-then-release timer, and `db.connect()` itself is bounded by
    that same interval even on the unfixed path (it cannot exceed
    `busy_timeout=5000ms`, and the external hold below is far under that, so
    the unfixed path is merely SLOW here, never an outright refusal — the
    outright-refusal shape at longer holds is what CB-195's own brief measured
    separately and is not re-measured by this test).
    """

    HOLD_SECONDS = 0.35
    FAST_THRESHOLD_SECONDS = 0.15  # comfortably below HOLD_SECONDS, comfortably above noise

    def _hold_write_lock(self, db_path, *, ready: threading.Event):
        """Acquire SQLite's write lock via BEGIN IMMEDIATE and hold it briefly.

        BEGIN IMMEDIATE alone acquires the RESERVED lock, before any write
        statement is issued — exactly SQLite's documented purpose for it — so
        no further statement is needed to reproduce contention.
        """
        conn = sqlite3.connect(str(db_path))
        conn.execute("BEGIN IMMEDIATE")
        ready.set()
        time.sleep(self.HOLD_SECONDS)
        conn.execute("ROLLBACK")
        conn.close()

    def test_connect_returns_fast_while_an_external_writer_holds_the_lock(self, tmp_path):
        db.init_project(str(tmp_path))
        db_path = tmp_path / ".codebugs" / db.DB_FILE

        ready = threading.Event()
        holder = threading.Thread(target=self._hold_write_lock, args=(db_path,), kwargs={"ready": ready})
        holder.start()
        try:
            assert ready.wait(timeout=5.0), "the external writer never acquired its lock"

            start = time.perf_counter()
            conn = db.connect(str(tmp_path))
            elapsed = time.perf_counter() - start
            conn.close()
        finally:
            holder.join(timeout=5.0)
            assert not holder.is_alive()

        assert elapsed < self.FAST_THRESHOLD_SECONDS, (
            f"db.connect() took {elapsed:.3f}s while a foreign writer held the lock for "
            f"{self.HOLD_SECONDS:.3f}s — a purely reading connect() should never wait on "
            "someone else's write (CB-195): ensure_schema's seed insert is taking the "
            "write lock on the steady-state path"
        )


class TestSchemaInitRunsNoUncheckedDml:
    """CB-202, repairing CB-195's own ratchet: no schema-init function may
    execute DML that no read of its own can stop.

    WHY THIS WAS REWRITTEN. The first version of this ratchet keyed on the
    SPELLING — a string LITERAL sitting in the `conn.execute(...)` call — and an
    isolated acceptor defeated it in one line by moving the same
    `INSERT OR IGNORE` into the module's schema CONSTANT, which the already
    present `for stmt in MERGE_SCHEMA.split(";"): conn.execute(stmt)` loop then
    ran. All three of its tests stayed green with the defect fully restored.
    That was not a hole (the behavioural contention test went red) but an
    OVERPROMISE, which this repository treats as worse than an absent guard: it
    withdraws attention and gives nothing back. Measured over the tree, the
    literal rule was worse than "easy to bypass" — it was blind to the shape
    EVERY schema-init function in the package actually uses: all ten of them
    execute their DDL through a loop over a module-level constant, and not one
    passes a literal to that call.

    THE PRIMITIVE. "Does this function execute a string it did not itself
    check?" Two halves, and the acceptor broke the first:

      1. WHICH TEXTS CAN RUN. The SQL argument is RESOLVED, not pattern-matched:
         through a literal, a module-level string or tuple-of-strings constant,
         a function-local binding, `.split(";")`, `.strip()`, the leading
         constant of an f-string or of a `+` concatenation, and the loop
         variable of a `for` over any of those. `executescript` blobs are split
         into statements, so a DML buried in the MIDDLE of a rebuild script is
         seen; `executemany` is judged like `execute` (the old rule did not even
         name that attribute).
      2. WAS IT CHECKED — BY A READ. A DML text is acceptable when it is
         reachable only through a branch whose test could have come from the
         database, or after a GUARD CLAUSE (an `if` that can
         `return`/`raise`/`continue`) of that same kind. "Nested under an `if`"
         is NOT sufficient, and discovering that is what turned this rewrite
         from a bigger resolver into a different rule: the schema loop every
         module already writes is `for stmt in SCHEMA.split(";"): if stmt:
         conn.execute(stmt)`, so the acceptor's insert lands inside an `if` —
         a truthiness test on a Python string, which checks nothing about the
         tracker. A read counts when it is in the test itself, in a
         module-local function the test calls, or in a name the test reads that
         was bound from one of those. All three occur in this tree
         (`merge.ensure_schema`, `sweep._migrate`, `reqs._migrate_to_lowercase`
         respectively), and the guard-clause half is not tidiness either:
         `reqs._migrate_to_lowercase` and `findings._migrate_statuses` are
         honest code in exactly that shape, so without it widening the scope to
         helpers would have painted correct code red on the first run.

    SCOPE is derived from the registry rather than from a name. `_open` runs
    whatever `db.register_schema(...)` registered, and whatever THAT calls, on
    every single `db.connect()`. So the entry points are the functions named in
    those registration calls, and the audited set is their transitive
    MODULE-LOCAL closure. That closure is 27 execute sites wider than the old
    rule's, in `findings._migrate_statuses`, `reqs._migrate_to_lowercase` and
    `sweep._migrate` — none of which the old rule read at all.

    FAIL-CLOSED where it cannot see: an execute whose SQL argument does not
    resolve, an f-string that begins with an interpolation, and a statement
    leading with `WITH` (a CTE can front a DML) are all treated as possibly-DML,
    so they must be conditional or they are reported. A `register_schema` call
    whose function argument is not a plain name fails the test outright rather
    than silently shrinking the audited set.

    WHAT IT DOES NOT SEE, stated because a ratchet promising more than it checks
    is the defect this class exists to repair:

      - A string reaching the function as an ARGUMENT or from ANOTHER module is
        unresolvable, so it is refused when unconditional — but if the caller
        hands it to a branch, the DML is waved through unread.
      - `getattr(conn, "execute")(...)`, a cursor stored in a container, or any
        other indirection that hides the attribute name.
      - RELEVANCE. The read test says a read INFLUENCED the branch, never that
        the branch is ABOUT this statement: `if row is None or True:` passes,
        and a guard clause is accepted as excusing every statement after it in
        the function, however unrelated.
      - Taint is by NAME within one function. A read whose result travels
        through a container element, a returned tuple unpacked elsewhere, or an
        attribute is not followed, so such a branch reads as unchecked — the
        fail-closed direction, but a possible false refusal for a future shape.
      - Only `src/codebugs/` and only module-local calls: a schema-init function
        delegating to another module's helper is followed no further, and a
        function it calls there counts as neither a reader nor an executor.

    Deliberate: this is an ACCIDENT-STOPPER. Reverting the CB-195 read-before-
    write guard is a one-line change whose symptom is invisible without
    concurrency, and that is the regression this catches. Someone determined to
    write an unconditional insert can still spell it in a way no source reader
    could classify.
    """

    _DML_VERBS = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE", "UPSERT"})
    _AMBIGUOUS_VERBS = frozenset({"WITH"})  # a CTE may front an INSERT/UPDATE/DELETE
    _EXEC_ATTRS = ("execute", "executescript", "executemany")

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _leading_verb(sql: str) -> str:
        """First SQL keyword, with leading comments and whitespace removed."""
        s = sql
        while True:
            s = s.lstrip()
            if s.startswith("--"):
                nl = s.find("\n")
                if nl == -1:
                    return ""
                s = s[nl + 1 :]
                continue
            if s.startswith("/*"):
                end = s.find("*/")
                if end == -1:
                    return ""
                s = s[end + 2 :]
                continue
            break
        parts = s.split(maxsplit=1)
        return parts[0].upper() if parts else ""

    @classmethod
    def _module_constants(cls, tree: ast.Module) -> dict[str, list[tuple[str, bool]]]:
        """Module-level names bound to a string, or to a sequence of strings.

        Elements are resolved one by one rather than required to be literals:
        `findings._POST_MIGRATION_INDEXES` builds its first entry by
        concatenation, and rejecting the whole tuple for that would have made
        every statement in it unreadable — a fail-closed refusal of honest DDL.
        """
        out: dict[str, list[tuple[str, bool]]] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if isinstance(value, ast.Tuple | ast.List) and value.elts:
                per_element = [cls._resolve(e, {}, {}) for e in value.elts]
                if any(r is None for r in per_element):
                    continue
                resolved = [pair for r in per_element for pair in r]
            else:
                resolved = cls._resolve(value, {}, {})
            if not resolved:
                continue
            for t in targets:
                if isinstance(t, ast.Name):
                    out[t.id] = resolved
        return out

    @classmethod
    def _resolve(cls, node, consts, binds) -> list[tuple[str, bool]] | None:
        """Texts an expression can evaluate to, as (text, is_complete) pairs.

        `is_complete=False` means only a leading prefix is known — enough to read
        the verb, never enough to split a script. `None` means unresolvable, and
        every caller treats that fail-closed.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [(node.value, True)]
        if isinstance(node, ast.Name):
            if node.id in binds:
                return binds[node.id]
            if node.id in consts:
                return consts[node.id]
            return None
        if isinstance(node, ast.JoinedStr):
            head = node.values[0] if node.values else None
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                return [(head.value, False)]
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = cls._resolve(node.left, consts, binds)
            return None if left is None else [(t, False) for t, _ in left]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = cls._resolve(node.func.value, consts, binds)
            if base is None:
                return None
            if node.func.attr == "split" and len(node.args) == 1:
                sep = node.args[0]
                if not (isinstance(sep, ast.Constant) and isinstance(sep.value, str)):
                    return None
                out: list[tuple[str, bool]] = []
                for text, complete in base:
                    if not complete:
                        return None
                    out.extend((piece, True) for piece in text.split(sep.value))
                return out
            if node.func.attr == "strip" and not node.args:
                return [(t.strip(), c) for t, c in base]
        return None

    @staticmethod
    def _can_exit(node: ast.If | ast.While) -> bool:
        """Does this branch contain a statement that leaves the straight line?"""
        for body in (node.body, node.orelse):
            for st in body:
                for n in ast.walk(st):
                    if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                        continue
                    if isinstance(n, ast.Return | ast.Raise | ast.Continue):
                        return True
        return False

    @classmethod
    def _reading_functions(cls, funcs: dict[str, ast.FunctionDef]) -> set[str]:
        """Module-local functions that touch the database at all.

        A branch fed by `_existing_columns(conn, "t")` is fed by a read; the read
        simply happens one frame down. `sweep._migrate` is that shape, so without
        this the ratchet would redden on correct code.
        """
        direct = set()
        for name, fn in funcs.items():
            for n in ast.walk(fn):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in cls._EXEC_ATTRS
                ):
                    direct.add(name)
                    break
        readers = set(direct)
        changed = True
        while changed:
            changed = False
            for name, fn in funcs.items():
                if name in readers:
                    continue
                for n in ast.walk(fn):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                        if n.func.id in readers:
                            readers.add(name)
                            changed = True
                            break
        return readers

    @classmethod
    def _is_read_derived(cls, expr, tainted: set[str], readers: set[str]) -> bool:
        """Could this expression's value have come from the database?

        THIS is what makes the ratchet key on the primitive rather than on the
        shape of an `if`. The acceptor's bypass survives a "nested under a
        branch" rule for a reason worth stating: the schema loop's own
        `if stmt:` is a truthiness test on a Python string, which checks nothing
        about the tracker. A write is CHECKED only when a read could have stopped
        it, so the branch's test must carry a read — directly, through a
        module-local function that reads, or through a name bound from one.
        """
        for n in ast.walk(expr):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in cls._EXEC_ATTRS:
                    return True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in readers:
                return True
            if isinstance(n, ast.Name) and n.id in tainted:
                return True
        return False

    @staticmethod
    def _own_expressions(st: ast.stmt) -> list[ast.AST]:
        """Expressions evaluated by this statement itself, excluding its bodies."""
        if isinstance(st, ast.If | ast.While):
            return [st.test]
        if isinstance(st, ast.For | ast.AsyncFor):
            return [st.iter]
        if isinstance(st, ast.With | ast.AsyncWith):
            return [item.context_expr for item in st.items]
        if isinstance(st, ast.Try):
            return []
        return [st]

    @classmethod
    def _judge(cls, call, consts, binds, checked, offenders, where):
        texts = cls._resolve(call.args[0], consts, binds) if call.args else None
        attr = call.func.attr
        if texts is None:
            if not checked:
                offenders.append(f"{where}:{call.lineno}: .{attr}(<unreadable SQL>)")
            return
        statements: list[str] = []
        for text, complete in texts:
            if attr == "executescript":
                if not complete:
                    if not checked:
                        offenders.append(f"{where}:{call.lineno}: .{attr}(<partial script>)")
                    continue
                statements.extend(text.split(";"))
            else:
                statements.append(text)
        for sql in statements:
            if not sql.strip():
                continue
            verb = cls._leading_verb(sql)
            suspicious = verb in cls._DML_VERBS or verb in cls._AMBIGUOUS_VERBS or verb == ""
            if suspicious and not checked:
                offenders.append(f"{where}:{call.lineno}: .{attr}({sql.strip()[:70]!r})")

    @classmethod
    def _scan(cls, stmts, ctx, binds, tainted, checked, offenders, where):
        """Walk one statement list in order.

        `checked` says a read could have stopped everything from here down —
        either we are inside a read-derived branch, or a read-derived guard
        clause has already been passed at this level. `tainted` carries the
        names whose values came from the database.
        """
        consts, readers = ctx
        binds, tainted = dict(binds), set(tainted)
        for st in stmts:
            if isinstance(st, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            for expr in cls._own_expressions(st):
                for node in ast.walk(expr):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in cls._EXEC_ATTRS
                    ):
                        cls._judge(node, consts, binds, checked, offenders, where)

            if isinstance(st, ast.Assign):
                resolved = cls._resolve(st.value, consts, binds)
                reads = cls._is_read_derived(st.value, tainted, readers)
                for t in st.targets:
                    for name_node in ast.walk(t):
                        if isinstance(name_node, ast.Name):
                            binds[name_node.id] = resolved
                            tainted.discard(name_node.id)
                            if reads:
                                tainted.add(name_node.id)
            elif isinstance(st, ast.If):
                inner = checked or cls._is_read_derived(st.test, tainted, readers)
                cls._scan(st.body, ctx, binds, tainted, inner, offenders, where)
                cls._scan(st.orelse, ctx, binds, tainted, inner, offenders, where)
                if inner and cls._can_exit(st):
                    checked = True
            elif isinstance(st, ast.While):
                inner = checked or cls._is_read_derived(st.test, tainted, readers)
                cls._scan(st.body, ctx, binds, tainted, inner, offenders, where)
                cls._scan(st.orelse, ctx, binds, tainted, inner, offenders, where)
            elif isinstance(st, ast.For | ast.AsyncFor):
                loop_binds = dict(binds)
                loop_tainted = set(tainted)
                items = cls._resolve(st.iter, consts, binds)
                reads = cls._is_read_derived(st.iter, tainted, readers)
                for name_node in ast.walk(st.target):
                    if isinstance(name_node, ast.Name):
                        loop_binds[name_node.id] = items
                        loop_tainted.discard(name_node.id)
                        if reads:
                            loop_tainted.add(name_node.id)
                cls._scan(st.body, ctx, loop_binds, loop_tainted, checked, offenders, where)
                cls._scan(st.orelse, ctx, binds, tainted, checked, offenders, where)
            elif isinstance(st, ast.Try):
                cls._scan(st.body, ctx, binds, tainted, checked, offenders, where)
                for handler in st.handlers:
                    cls._scan(handler.body, ctx, binds, tainted, True, offenders, where)
                cls._scan(st.orelse, ctx, binds, tainted, checked, offenders, where)
                cls._scan(st.finalbody, ctx, binds, tainted, checked, offenders, where)
            elif isinstance(st, ast.With | ast.AsyncWith):
                cls._scan(st.body, ctx, binds, tainted, checked, offenders, where)

    @staticmethod
    def _closure(entry: str, funcs: dict[str, ast.FunctionDef]) -> list[ast.FunctionDef]:
        """The entry point plus every module-local function it can reach."""
        seen: list[str] = []
        stack = [entry]
        while stack:
            name = stack.pop()
            if name in seen or name not in funcs:
                continue
            seen.append(name)
            for n in ast.walk(funcs[name]):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs:
                    stack.append(n.func.id)
        return [funcs[n] for n in seen]

    @classmethod
    def _entry_point_names(cls, src: Path) -> tuple[set[str], list[str]]:
        """Names registered with `register_schema`, plus any call we cannot read."""
        names: set[str] = set()
        unreadable: list[str] = []
        for py in sorted(src.rglob("*.py")):
            tree = ast.parse(py.read_text(), filename=str(py))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else None
                )
                if called != "register_schema":
                    continue
                arg = node.args[1] if len(node.args) > 1 else None
                for kw in node.keywords:
                    if kw.arg == "ensure_fn":
                        arg = kw.value
                if isinstance(arg, ast.Name):
                    names.add(arg.id)
                else:
                    unreadable.append(f"{py.relative_to(src)}:{node.lineno}")
        return names, unreadable

    def _audit(self, src: Path) -> tuple[list[str], list[str]]:
        """Report (offenders, audited function labels) over a package tree."""
        names, unreadable = self._entry_point_names(src)
        assert not unreadable, (
            "a register_schema() call names its schema-init function in a way this "
            "ratchet cannot read, so the audited set would silently shrink:\n"
            + "\n".join(unreadable)
        )
        assert names, "no register_schema() call found — the ratchet would audit nothing"

        offenders: list[str] = []
        audited: list[str] = []
        for py in sorted(src.rglob("*.py")):
            tree = ast.parse(py.read_text(), filename=str(py))
            consts = self._module_constants(tree)
            funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
            rel = py.relative_to(src)
            ctx = (consts, self._reading_functions(funcs))
            for entry in sorted(names & funcs.keys()):
                for fn in self._closure(entry, funcs):
                    label = f"{rel}::{fn.name}"
                    if label in audited:
                        continue
                    audited.append(label)
                    self._scan(fn.body, ctx, {}, set(), False, offenders, label)
        return offenders, audited

    def _offenders_in(self, source: str, entry: str = "ensure_schema") -> list[str]:
        """Run the detector over one synthetic module — the oracle harness."""
        tree = ast.parse(source)
        consts = self._module_constants(tree)
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        ctx = (consts, self._reading_functions(funcs))
        offenders: list[str] = []
        for fn in self._closure(entry, funcs):
            self._scan(fn.body, ctx, {}, set(), False, offenders, fn.name)
        return offenders

    @staticmethod
    def _old_ratchet_offenders(source: str) -> list[str]:
        """The predicate CB-202 replaces, kept only to pin what this unit bought.

        Verbatim shape of the deleted rule: a string LITERAL in the call, leading
        with a DML verb, not lexically nested under an `ast.If`.
        """
        offenders = []

        def walk(n, under_if):
            if isinstance(n, ast.If):
                for child in ast.iter_child_nodes(n):
                    walk(child, True)
                return
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in ("execute", "executescript"):
                    first = n.args[0] if n.args else None
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        sql = first.value.strip().upper()
                        if sql.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
                            if not under_if:
                                offenders.append(f"{n.lineno}: {sql[:60]!r}")
            for child in ast.iter_child_nodes(n):
                walk(child, under_if)

        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == "ensure_schema":
                walk(node, False)
        return offenders

    # ------------------------------------------------------- the ratchet itself

    def test_no_schema_init_runs_unchecked_dml(self):
        offenders, _ = self._audit(Path(db.__file__).parent)
        assert offenders == [], (
            "a schema-init function executes DML that no read of its own can stop, so "
            "every db.connect() takes SQLite's write lock — this is CB-195's "
            "write-lock-on-read defect:\n" + "\n".join(offenders)
        )

    def test_the_audited_set_reaches_the_helpers_the_old_rule_could_not_see(self):
        """Non-vacuity, as a composition rather than a count.

        The two functions CB-195 actually repaired must be in the audited set,
        and so must the migration helpers reachable from a schema-init entry
        point — those are 27 execute sites the name-keyed rule never read.
        """
        _, audited = self._audit(Path(db.__file__).parent)
        for required in (
            "merge.py::ensure_schema",
            "milestones/_schema.py::ensure_schema",
            "findings.py::_migrate_statuses",
            "reqs.py::_migrate_to_lowercase",
            "sweep.py::_migrate",
        ):
            assert required in audited, f"{required} is outside the audited set: {audited}"

    # ------------------- oracle: ONE state, reached by DIFFERENT spellings ----

    def test_dml_as_a_literal_in_the_call_is_flagged(self):
        """The exact pre-fix shape. The old rule caught this one, and only this."""
        source = """
def ensure_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT OR IGNORE INTO t (id) VALUES (1)")
"""
        assert self._offenders_in(source)
        assert self._old_ratchet_offenders(source), "premise: the old rule saw this spelling"

    def test_dml_moved_into_the_schema_constant_is_flagged(self):
        """THE PURCHASE OF THIS UNIT — the acceptor's one-line bypass.

        Identical end state, reached by putting the insert in the module's
        schema constant that the existing loop already executes. The old rule is
        green here; that difference is what CB-202 bought, so it is asserted in
        both directions rather than described.
        """
        source = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO t (id) VALUES (1)
"""


def ensure_schema(conn):
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
'''
        assert self._offenders_in(source), "the acceptor's bypass is still invisible"
        assert self._old_ratchet_offenders(source) == [], (
            "premise: the rule CB-202 replaces was GREEN on this state — if it is not, "
            "this unit's motivating measurement no longer reproduces"
        )

    def test_dml_in_a_constant_tuple_of_statements_is_flagged(self):
        source = '''
_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_t ON t (id)",
    "INSERT OR IGNORE INTO t (id) VALUES (1)",
)


def ensure_schema(conn):
    for stmt in _POST_MIGRATION_INDEXES:
        conn.execute(stmt)
'''
        assert self._offenders_in(source)

    def test_dml_built_as_an_fstring_is_flagged(self):
        source = """
def ensure_schema(conn):
    table = "t"
    conn.execute(f"INSERT OR IGNORE INTO {table} (id) VALUES (1)")
"""
        assert self._offenders_in(source)

    def test_dml_buried_in_the_middle_of_an_executescript_is_flagged(self):
        """`executescript` commits implicitly and carries many statements, so a
        rule reading only the blob's first verb sees a CREATE and stops."""
        source = '''
def ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO t (id) VALUES (1);
        CREATE INDEX IF NOT EXISTS ix_t ON t (id)
    """)
'''
        assert self._offenders_in(source)
        assert self._old_ratchet_offenders(source) == [], (
            "premise: leading-verb matching on the whole blob read this as a CREATE"
        )

    def test_dml_through_executemany_is_flagged(self):
        source = """
def ensure_schema(conn):
    conn.executemany("INSERT OR IGNORE INTO t (id) VALUES (?)", [(1,), (2,)])
"""
        assert self._offenders_in(source)
        assert self._old_ratchet_offenders(source) == [], (
            "premise: the old rule did not even name the executemany attribute"
        )

    def test_dml_inside_a_helper_called_by_the_entry_point_is_flagged(self):
        source = """
def ensure_schema(conn):
    _migrate(conn)


def _migrate(conn):
    conn.execute("INSERT OR IGNORE INTO t (id) VALUES (1)")
"""
        assert self._offenders_in(source)
        assert self._old_ratchet_offenders(source) == [], (
            "premise: a name-keyed rule never reads the helper"
        )

    def test_dml_held_in_a_local_variable_is_flagged(self):
        source = """
def ensure_schema(conn):
    sql = "INSERT OR IGNORE INTO t (id) VALUES (1)"
    conn.execute(sql)
"""
        assert self._offenders_in(source)
        assert self._old_ratchet_offenders(source) == [], (
            "premise: the old rule read only the literal sitting in the call"
        )

    def test_a_branch_not_fed_by_a_read_does_not_excuse_dml(self):
        """The half that actually defeats the acceptor's bypass.

        `if stmt:` is the guard the real schema loop already carries, so a rule
        content with "nested under an `if`" waves the insert straight through.
        A write is checked only when a READ could have stopped it.
        """
        source = """
def ensure_schema(conn, wanted):
    if wanted:
        conn.execute("INSERT OR IGNORE INTO t (id) VALUES (1)")
"""
        assert self._offenders_in(source)
        assert self._old_ratchet_offenders(source) == [], (
            "premise: nesting under any `if` satisfied the rule CB-202 replaces"
        )

    def test_a_cte_fronted_write_is_flagged(self):
        source = """
def ensure_schema(conn):
    conn.execute("WITH src AS (SELECT 1 AS id) INSERT INTO t (id) SELECT id FROM src")
"""
        assert self._offenders_in(source)

    def test_unreadable_sql_is_refused_rather_than_certified(self):
        """Fail-closed: what the resolver cannot read, it does not wave through."""
        source = """
def ensure_schema(conn):
    _apply(conn, "INSERT OR IGNORE INTO t (id) VALUES (1)")


def _apply(conn, sql):
    conn.execute(sql)
"""
        assert self._offenders_in(source)

    # --------------------------- oracle: honest code must stay green ---------

    def test_a_read_gated_insert_is_not_flagged(self):
        """The CB-195 fix's own shape — the mirror premise. A ratchet that
        reddens on correct code is removed by the first person it obstructs."""
        source = """
def ensure_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
    if conn.execute("SELECT 1 FROM t WHERE id = 1").fetchone() is None:
        conn.execute("INSERT OR IGNORE INTO t (id) VALUES (1)")
"""
        assert self._offenders_in(source) == []

    def test_a_guard_clause_makes_the_rest_of_the_function_conditional(self):
        """`reqs._migrate_to_lowercase`'s real shape, reduced.

        The write is not nested under anything; it is guarded by two early
        returns fed by a read. Widening the scope to helpers without this half
        would have painted correct code red on the very first run.
        """
        source = '''
def ensure_schema(conn):
    _migrate_to_lowercase(conn)


def _migrate_to_lowercase(conn):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name='t'").fetchone()
    if row is None:
        return
    if "'must'" in row[0]:
        return
    conn.executescript("""
        CREATE TABLE t_new (id INTEGER PRIMARY KEY);
        INSERT INTO t_new SELECT id FROM t;
        DROP TABLE t
    """)
'''
        assert self._offenders_in(source) == []

    def test_pure_ddl_and_reads_are_not_flagged(self):
        source = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);
CREATE INDEX IF NOT EXISTS ix_t ON t (id)
"""


def ensure_schema(conn):
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(t)").fetchall()}
    if "extra" not in cols:
        conn.execute("ALTER TABLE t ADD COLUMN extra TEXT")
'''
        assert self._offenders_in(source) == []
