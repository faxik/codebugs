"""Tests for db.py infrastructure: connect, _find_db_root, _db_path."""

import os

import pytest

from codebugs import db, findings


@pytest.fixture
def tmp_project(tmp_path):
    """Provide a temporary project directory with a fresh DB."""
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
        conn = db.connect(project)
        assert os.path.exists(os.path.join(project, ".codebugs", "findings.db"))
        conn.close()

    def test_idempotent_connect(self, tmp_path):
        project = str(tmp_path)
        c1 = db.connect(project)
        findings.add_finding(c1, severity="low", category="x", file="a.py", description="d")
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

    def test_db_path_falls_back_to_cwd_when_no_root_found(self, tmp_path, monkeypatch):
        empty = tmp_path / "nowhere"
        empty.mkdir()
        monkeypatch.chdir(empty)
        assert db._db_path() == os.path.join(os.getcwd(), ".codebugs", "findings.db")

    def test_db_path_explicit_project_dir_short_circuits_walk(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        sub = repo / "src"
        sub.mkdir()
        monkeypatch.chdir(sub)
        explicit = str(tmp_path / "explicit")
        assert db._db_path(explicit) == os.path.join(explicit, ".codebugs", "findings.db")

    def test_connect_warns_on_auto_create(self, tmp_path, monkeypatch, capsys):
        empty = tmp_path / "fresh"
        empty.mkdir()
        monkeypatch.chdir(empty)
        c = db.connect()
        try:
            captured = capsys.readouterr()
            assert "created fresh .codebugs/" in captured.err
            assert "no existing DB found" in captured.err
        finally:
            c.close()

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

    def test_connect_silent_when_project_dir_explicit(self, tmp_path, capsys):
        """Explicit project_dir = caller opted-in; no warning even if DB is new."""
        c = db.connect(str(tmp_path))
        try:
            captured = capsys.readouterr()
            assert captured.err == ""
        finally:
            c.close()
