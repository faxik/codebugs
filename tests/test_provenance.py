"""Tests for staleness detection (provenance module)."""

import os
import subprocess

import pytest

from codebugs import db, findings, provenance


@pytest.fixture
def git_project(tmp_path):
    """Create a temporary git repo with a tracked file and some commits."""
    project = str(tmp_path)
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=project, check=True, capture_output=True
    )

    test_file = os.path.join(project, "src", "auth.py")
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    with open(test_file, "w") as f:
        f.write("# auth module\n")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)

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


class TestFileStatus:
    """Test the provenance.file_status helper directly."""

    def test_current_file(self, git_project):
        project, initial_sha = git_project
        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit=initial_sha,
            project_dir=project,
        )
        assert result["file_status"] == "current"

    def test_modified_file(self, git_project):
        project, initial_sha = git_project
        with open(os.path.join(project, "src", "auth.py"), "a") as f:
            f.write("def login(): pass\n")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add login"], cwd=project, check=True, capture_output=True
        )

        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit=initial_sha,
            project_dir=project,
        )
        assert result["file_status"] == "modified"
        assert "1 commit" in result["reason"]

    def test_deleted_file(self, git_project):
        project, initial_sha = git_project
        os.remove(os.path.join(project, "src", "auth.py"))
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "remove auth"], cwd=project, check=True, capture_output=True
        )

        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit=initial_sha,
            project_dir=project,
        )
        assert result["file_status"] == "deleted"

    def test_renamed_file(self, git_project):
        project, initial_sha = git_project
        os.rename(
            os.path.join(project, "src", "auth.py"),
            os.path.join(project, "src", "authentication.py"),
        )
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "rename auth"], cwd=project, check=True, capture_output=True
        )

        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit=initial_sha,
            project_dir=project,
        )
        assert result["file_status"] == "renamed"
        assert "authentication.py" in result["reason"]

    def test_unknown_no_commit(self, git_project):
        project, _ = git_project
        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit=None,
            project_dir=project,
        )
        assert result["file_status"] == "unknown"
        assert result["reason"] == "no_provenance"

    def test_unknown_bad_commit(self, git_project):
        project, _ = git_project
        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit="deadbeef" * 5,
            project_dir=project,
        )
        assert result["file_status"] == "unknown"


class TestCheckFindings:
    """Test provenance.check_findings end-to-end."""

    def test_check_single_finding(self, git_project, conn):
        project, initial_sha = git_project
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="src/auth.py",
            description="auth bug",
            reported_at_commit=initial_sha,
        )

        result = provenance.check_findings(conn, project, finding_id="CB-1")
        assert len(result["findings"]) == 1
        assert result["findings"][0]["file_status"] == "current"

    def test_check_filters_by_status(self, git_project, conn):
        project, initial_sha = git_project
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="src/auth.py",
            description="open bug",
            reported_at_commit=initial_sha,
        )
        findings.update_finding(conn, "CB-1", status="fixed")
        findings.add_finding(
            conn,
            severity="low",
            category="style",
            file="src/auth.py",
            description="open style",
            reported_at_commit=initial_sha,
        )

        result = provenance.check_findings(conn, project, status="open")
        assert len(result["findings"]) == 1
        assert result["findings"][0]["finding_id"] == "CB-2"

    def test_check_batches_by_file(self, git_project, conn):
        """Multiple findings on the same file should not cause redundant git calls."""
        project, initial_sha = git_project
        for i in range(3):
            findings.add_finding(
                conn,
                severity="high",
                category="bug",
                file="src/auth.py",
                description=f"bug {i}",
                reported_at_commit=initial_sha,
            )

        result = provenance.check_findings(conn, project)
        assert len(result["findings"]) == 3
        statuses = {f["file_status"] for f in result["findings"]}
        assert statuses == {"current"}
