"""Tests for staleness detection."""

import os
import subprocess

import pytest

from codebugs import db


@pytest.fixture
def git_project(tmp_path):
    """Create a temporary git repo with a tracked file and some commits."""
    project = str(tmp_path)
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True, capture_output=True)

    # Create and commit a file
    test_file = os.path.join(project, "src", "auth.py")
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    with open(test_file, "w") as f:
        f.write("# auth module\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)

    # Record the initial commit
    initial_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()

    return project, initial_sha


@pytest.fixture
def conn(git_project):
    project, _ = git_project
    c = db.connect(project)
    yield c
    c.close()


class TestCheckFileStaleness:
    """Test the _check_file_staleness helper directly."""

    def test_current_file(self, git_project):
        project, initial_sha = git_project
        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "current"

    def test_modified_file(self, git_project):
        project, initial_sha = git_project
        with open(os.path.join(project, "src", "auth.py"), "a") as f:
            f.write("def login(): pass\n")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add login"], cwd=project, check=True, capture_output=True)

        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "modified"
        assert "1 commit" in result["reason"]

    def test_deleted_file(self, git_project):
        project, initial_sha = git_project
        os.remove(os.path.join(project, "src", "auth.py"))
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "remove auth"], cwd=project, check=True, capture_output=True)

        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "deleted"

    def test_renamed_file(self, git_project):
        project, initial_sha = git_project
        os.rename(
            os.path.join(project, "src", "auth.py"),
            os.path.join(project, "src", "authentication.py"),
        )
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "rename auth"], cwd=project, check=True, capture_output=True)

        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", initial_sha, project)
        assert result["file_status"] == "renamed"
        assert "authentication.py" in result["reason"]

    def test_unknown_no_commit(self, git_project):
        project, _ = git_project
        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", None, project)
        assert result["file_status"] == "unknown"
        assert result["reason"] == "no_provenance"

    def test_unknown_bad_commit(self, git_project):
        project, _ = git_project
        from codebugs.server import _check_file_staleness
        result = _check_file_staleness("src/auth.py", "deadbeef" * 5, project)
        assert result["file_status"] == "unknown"
