"""Tests for db.py infrastructure: connect, _find_db_root, _db_path, init_project."""

import os
import subprocess
import sys
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
