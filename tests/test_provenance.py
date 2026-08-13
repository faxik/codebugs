"""Tests for staleness detection (provenance module)."""

import os
import subprocess

import pytest

from codebugs import db, findings, provenance


@pytest.fixture
def git_project(tmp_path):
    """Create a temporary git repo with a tracked file and some commits."""
    project = str(tmp_path)
    db.init_project(project)
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


class TestResolveTrailers:
    """Test provenance.resolve_trailers against commit trailers."""

    def _commit(self, project, message):
        readme = os.path.join(project, "README.md")
        with open(readme, "a") as f:
            f.write(message + "\n")
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", message], cwd=project, check=True, capture_output=True
        )

    def _add(self, conn):
        return findings.add_finding(
            conn, severity="high", category="bug", file="src/auth.py", description="x"
        )["id"]

    def test_resolves_flips_to_fixed(self, git_project, conn):
        project, base = git_project
        cb_id = self._add(conn)
        self._commit(project, f"fix(auth): patch\n\nResolves: {cb_id}")

        report = provenance.resolve_trailers(conn, rev_range=f"{base}..HEAD", project_dir=project)

        assert report["resolved"] == [cb_id]
        assert findings.get_finding(conn, cb_id)["status"] == "fixed"

    def test_tightens_appends_note_without_status_change(self, git_project, conn):
        project, base = git_project
        cb_id = self._add(conn)
        self._commit(project, f"wip(auth): partial\n\nTightens: {cb_id}")

        report = provenance.resolve_trailers(conn, rev_range=f"{base}..HEAD", project_dir=project)

        assert report["tightened"] == [cb_id]
        f = findings.get_finding(conn, cb_id)
        assert f["status"] == "open"  # unchanged
        assert "Tightened by commit" in f["meta"]["notes"]

    def test_dry_run_reports_without_writing(self, git_project, conn):
        project, base = git_project
        cb_id = self._add(conn)
        self._commit(project, f"fix: x\n\nResolves: {cb_id}")

        report = provenance.resolve_trailers(
            conn, rev_range=f"{base}..HEAD", project_dir=project, dry_run=True
        )

        assert report["resolved"] == [cb_id]
        assert findings.get_finding(conn, cb_id)["status"] == "open"  # not written

    def test_already_terminal_is_skipped(self, git_project, conn):
        project, base = git_project
        cb_id = self._add(conn)
        findings.update_finding(conn, cb_id, status="wont_fix")
        self._commit(project, f"fix: x\n\nResolves: {cb_id}")

        report = provenance.resolve_trailers(conn, rev_range=f"{base}..HEAD", project_dir=project)

        assert report["skipped"] == [cb_id]
        assert findings.get_finding(conn, cb_id)["status"] == "wont_fix"

    def test_missing_id_is_non_fatal(self, git_project, conn):
        project, base = git_project
        self._commit(project, "fix: x\n\nResolves: CB-9999")

        report = provenance.resolve_trailers(conn, rev_range=f"{base}..HEAD", project_dir=project)

        assert report["missing"] == ["CB-9999"]
        assert report["resolved"] == []

    def test_comma_separated_ids(self, git_project, conn):
        project, base = git_project
        a, b = self._add(conn), self._add(conn)
        self._commit(project, f"fix: two\n\nResolves: {a}, {b}")

        report = provenance.resolve_trailers(conn, rev_range=f"{base}..HEAD", project_dir=project)

        assert set(report["resolved"]) == {a, b}


class TestFalseyStatusDoesNotSilentlyDefaultToOpen:
    """CB-25 sibling, one layer above the query filters.

    `check_findings` wrote `status if status else "open"`, so the same truthiness
    conflation applied — but with a DIFFERENT correct contract from the five domain
    filters. Here `None`/`""` mean "default to open", not "no filter"; only wrong
    input should raise. Before the fix `check_findings(status=0)` silently reported
    on open findings, which reads as a successful, differently-scoped answer."""

    def _one(self, conn, project, sha):
        findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="src/auth.py",
            description="d",
            reported_at_commit=sha,
        )

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_status_raises(self, conn, git_project, falsey):
        project, sha = git_project
        self._one(conn, project, sha)
        with pytest.raises(ValueError, match="Invalid finding status"):
            provenance.check_findings(conn, project, status=falsey)

    @pytest.mark.parametrize("empty", [None, ""])
    def test_none_and_empty_string_still_default_to_open(self, conn, git_project, empty):
        """Distinct from the domain sites: absence means 'open', not 'everything'."""
        project, sha = git_project
        self._one(conn, project, sha)
        other = findings.add_finding(
            conn,
            severity="low",
            category="bug",
            file="src/auth.py",
            description="already fixed",
            reported_at_commit=sha,
        )
        findings.update_finding(conn, other["id"], status="fixed")
        got = provenance.check_findings(conn, project, status=empty)
        checked = {f["finding_id"] for f in got["findings"]}
        assert other["id"] not in checked, "the fixed finding must not be checked"
        assert len(checked) == 1


class TestFindingIdBranchHonoursItsFilters:
    """CB-28: `check_findings`'s docstring has always promised "Filters forward to
    findings.query_findings", and the finding_id branch forwarded nothing — so
    `check_findings(finding_id="CB-1", status="fixed")` reported on CB-1 whatever its
    status. Code contradicting its own stated contract is the CB-23 shape, and CB-23
    settled that the contract wins."""

    def _open_finding(self, conn, sha):
        return findings.add_finding(
            conn,
            severity="high",
            category="bug",
            file="src/auth.py",
            description="d",
            reported_at_commit=sha,
        )

    def test_status_filter_excluding_the_finding_yields_nothing(self, conn, git_project):
        project, sha = git_project
        f = self._open_finding(conn, sha)
        got = provenance.check_findings(conn, project, finding_id=f["id"], status="fixed")
        assert got["total"] == 0

    def test_status_filter_matching_the_finding_still_returns_it(self, conn, git_project):
        project, sha = git_project
        f = self._open_finding(conn, sha)
        got = provenance.check_findings(conn, project, finding_id=f["id"], status="open")
        assert [r["finding_id"] for r in got["findings"]] == [f["id"]]

    def test_no_filters_still_returns_the_named_finding(self, conn, git_project):
        project, sha = git_project
        f = self._open_finding(conn, sha)
        got = provenance.check_findings(conn, project, finding_id=f["id"])
        assert [r["finding_id"] for r in got["findings"]] == [f["id"]]

    def test_unknown_id_still_raises_keyerror(self, conn, git_project):
        """The get_finding contract is unchanged; filters narrow, they do not mask."""
        project, _ = git_project
        with pytest.raises(KeyError):
            provenance.check_findings(conn, project, finding_id="CB-99999")
