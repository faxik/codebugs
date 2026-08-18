"""Tests for staleness detection (provenance module)."""

import json
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


class TestAmbientOSErrorSources:
    """CB-79 — `OSError` from sources the "guard the `open()`" sweep cannot see.

    Which side each test can fail on, stated because this repo ships vacuous
    tests otherwise:

      * `test_a_non_executable_git_*` and `test_a_deleted_cwd_*` MUST fail
        against the pre-fix tree (recorded runs in their docstrings).
      * `test_a_git_error_during_rename_detection_*` is a FIX-GUARD: pre-fix it
        fails by raising, and against a NAIVE widening (one that keeps
        `rename_output = ""`) it fails by reporting `deleted`. Both are wrong,
        for different reasons.
      * `TestNarrowTupleCompatibility` below passes on both sides by design.
    """

    def test_a_non_executable_git_degrades_instead_of_raising(self, monkeypatch, git_project):
        """Recorded run at 4ee8c6c: `PermissionError: [Errno 13] Permission
        denied: 'git'` escaped `file_status` entirely.

        NEGATIVE RESULT worth not re-deriving: putting a `chmod 000 git`
        EARLIER on PATH does not reproduce this. CPython's exec continues the
        PATH search on EACCES and silently finds the real git, so the
        non-executable one must be the ONLY git on PATH — which is what this
        test arranges.
        """
        project, sha = git_project
        bindir = os.path.join(project, "fakebin")
        os.makedirs(bindir)
        gitpath = os.path.join(bindir, "git")
        with open(gitpath, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(gitpath, 0o000)
        assert os.path.exists(gitpath), "fixture did not create the fake git"
        monkeypatch.setenv("PATH", bindir)

        result = provenance.file_status(
            file_path="src/auth.py", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "unknown", result

    def test_a_git_error_during_rename_detection_reports_unknown_not_deleted(
        self, monkeypatch, git_project
    ):
        """FIX-GUARD. The rename lookup used to swallow its failure into
        `rename_output = ""`, and the fall-through then stated `deleted` as a
        fact about the file — "a guard reporting clean because it could not
        look". Widening the tuple made it reachable, so the swallow had to go.

        Injects on the rename command's ARGV, not on a call index. It used to
        fire on the third `check_output`, which was the rename lookup at the
        time — and CB-88 then added a `ls-tree` probe on some paths, which would
        have silently moved the third call to a different branch. The assertion
        and the reason string are identical for the `git log` branch, so the
        test would have stayed green while pinning something else entirely:
        vacuous, the `TestKnownLimits` failure this repo already recorded once.
        """
        project, sha = git_project
        os.remove(os.path.join(project, "src", "auth.py"))
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "delete"], cwd=project, check=True, capture_output=True
        )

        real = subprocess.check_output
        calls = {"n": 0}

        def flaky(cmd, *a, **kw):
            if "--diff-filter=R" in cmd:
                calls["n"] += 1
                raise PermissionError(13, "Permission denied")
            return real(cmd, *a, **kw)

        monkeypatch.setattr(subprocess, "check_output", flaky)
        result = provenance.file_status(
            file_path="src/auth.py", reported_at_commit=sha, project_dir=project
        )
        assert calls["n"] >= 1, "the rename lookup was never reached"
        assert result["file_status"] == "unknown", result
        assert result["reason"] == "git_error", result

    def test_a_deleted_cwd_degrades_rather_than_raising(self, monkeypatch, git_project):
        """`os.getcwd()` raises once the directory is deleted out from under the
        process — a long-lived MCP server outlives the worktree it started in.
        Pre-fix this escaped as FileNotFoundError.

        `os.getcwd` is monkeypatched rather than actually deleting the cwd,
        which would affect the whole test process.
        """
        _project, sha = git_project

        def gone():
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(os, "getcwd", gone)
        result = provenance.file_status(file_path="src/auth.py", reported_at_commit=sha)
        assert result == {"file_status": "unknown", "reason": "no_cwd"}


class TestNarrowTupleCompatibility:
    """Compatibility pins. These pass on BOTH sides — they exist to show the
    widening from `FileNotFoundError` to `OSError` is a STRICT superset, not a
    behaviour change."""

    def test_a_missing_git_still_degrades_exactly_as_before(self, monkeypatch, git_project):
        project, sha = git_project
        monkeypatch.setenv("PATH", "/nonexistent")
        result = provenance.file_status(
            file_path="src/auth.py", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "unknown"
        assert result["reason"] == "unreachable_commit"

    def test_subprocess_errors_are_still_caught(self, monkeypatch, git_project):
        """`subprocess.SubprocessError` is NOT an OSError subclass, so it has to
        stay in the tuple; dropping it would let CalledProcessError and
        TimeoutExpired escape."""
        project, sha = git_project
        assert not issubclass(subprocess.SubprocessError, OSError)

        for exc in (
            subprocess.CalledProcessError(1, "git"),
            subprocess.TimeoutExpired("git", 10),
        ):
            def boom(*a, _e=exc, **kw):
                raise _e

            monkeypatch.setattr(subprocess, "check_output", boom)
            result = provenance.file_status(
                file_path="src/auth.py", reported_at_commit=sha, project_dir=project
            )
            assert result["file_status"] == "unknown", (exc, result)


class TestStatErrorIsNotDeleted:
    """CB-85 — an unanswerable stat must not become a confident `deleted`.

    MUST fail against 8ba8c2a: `os.path.isfile` swallows every OSError into
    False, so the same still-present file reported `modified` when readable and
    `deleted` when its parent directory was not. The second route to the answer
    CB-79 closed one line below.
    """

    def _modified_file(self, project):
        """Give the tracked file a later commit, so file_status reaches the
        existence check instead of returning `current` first."""
        return _commit_a_change(project)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_an_unreadable_parent_directory_reports_unknown_not_deleted(self, git_project):
        project, sha = git_project
        self._modified_file(project)
        parent = os.path.join(project, "src")

        os.chmod(parent, 0o000)
        try:
            result = provenance.file_status(
                file_path="src/auth.py", reported_at_commit=sha, project_dir=project
            )
        finally:
            os.chmod(parent, 0o755)

        assert result["file_status"] != "deleted", (
            "reported a file as deleted on the strength of a stat it could not perform"
        )
        assert result == {"file_status": "unknown", "reason": "stat_error"}, result

    def test_a_genuinely_deleted_file_is_still_reported_deleted(self, git_project):
        """The other half, and the reason FileNotFoundError keeps today's path:
        the `deleted` answer is correct when the stat actually answers."""
        project, sha = git_project
        path = self._modified_file(project)
        os.remove(path)
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "delete"], cwd=project, check=True, capture_output=True
        )

        result = provenance.file_status(
            file_path="src/auth.py", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "deleted", result

    def test_a_present_modified_file_is_unchanged(self, git_project):
        """Compatibility pin — passes on both sides. `S_ISREG` must agree with
        `isfile` for every case where the stat succeeds."""
        project, sha = git_project
        self._modified_file(project)
        result = provenance.file_status(
            file_path="src/auth.py", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "modified", result

    def test_a_directory_that_still_exists_reports_modified(self, git_project):
        """INVERTED BY CB-88, deliberately. This test used to assert that a
        directory does NOT report `modified` — it was written for CB-85, where
        the point was that `S_ISREG` must agree with `isfile` on every case, and
        a directory was treated as an incidental "unrelated case".

        CB-88 is the card saying that belief is wrong for this field: a `file`
        value naming a directory is deliberate, widespread usage ("this finding
        is about this subsystem"), and the old assertion was satisfied by the
        very `deleted` false positive CB-88 was filed for.

        CB-85's actual concern is untouched and still pinned by
        `test_an_unreadable_parent_directory_reports_unknown_not_deleted`: an
        `OSError` must never become a verdict.
        """
        project, sha = git_project
        self._modified_file(project)
        result = provenance.file_status(
            file_path="src", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "modified", result


def _commit_a_change(project, rel="src/auth.py", text="# changed\n"):
    """Give `rel` a commit after the fixture's base sha, so `git log` is
    non-empty and `file_status` reaches the existence branches."""
    path = os.path.join(project, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(text)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=project, check=True, capture_output=True)
    return path


class TestPathIsResolvedBeforeItIsJudged:
    """CB-88 + CB-89 — the function may not answer for a path it never resolved.

    Every test here MUST fail against main (2774e16): the recorded "main today"
    value is in each docstring, measured, not predicted. Two of them were
    predicted wrong in the plan's first drafts and corrected by measurement,
    which is why the values are written down.
    """

    def test_a_directory_that_exists_is_not_deleted(self, git_project):
        """main: `deleted` — the CB-88 headline. `os.stat` succeeds on a
        directory and `S_ISREG` is False, so the existence branch is skipped and
        control falls into the unconditional `deleted`."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="src", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "modified", result

    def test_a_directory_with_a_trailing_slash_is_not_deleted(self, git_project):
        """main: `deleted`. THE MAJORITY CASE — measured on the real autosorter
        tracker, 155 findings across 51 distinct paths (71 live) spell a
        directory with a trailing slash, versus 47 without one. A discriminator
        calibrated on `src` alone repairs the minority and leaves this."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="src/", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "modified", result

    def test_a_glob_valued_file_is_unknown_not_deleted(self, git_project):
        """main: `deleted`. A glob cannot be answered by `stat` at all, so the
        old code borrowed the answer of a path that was never examined."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="src/*.py", reported_at_commit=sha, project_dir=project
        )
        assert result == {"file_status": "unknown", "reason": "not_in_commit"}, result

    def test_free_text_in_the_file_field_is_unknown_not_current(self, git_project):
        """main: `current` — "unchanged since <sha>" about a path that does not
        exist. On neither CB-88 nor CB-89; CB-89 assumed this landed in
        `unknown`. The empty-log early return asserts freshness for anything git
        has never heard of."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="context-mode plugin PreToolUse hook (subagent routing)",
            reported_at_commit=sha,
            project_dir=project,
        )
        assert result == {"file_status": "unknown", "reason": "not_in_commit"}, result

    def test_an_untracked_regular_file_is_unknown_not_current(self, git_project):
        """main: `current`. Disk existence is not historical identity — the file
        was never in the reported commit, so "unchanged since" is a claim about
        a state that never existed. Found by the Codex/Sol review."""
        project, sha = git_project
        _commit_a_change(project)
        with open(os.path.join(project, "scratch_untracked.py"), "w") as fh:
            fh.write("x = 1\n")
        result = provenance.file_status(
            file_path="scratch_untracked.py", reported_at_commit=sha, project_dir=project
        )
        assert result == {"file_status": "unknown", "reason": "not_in_commit"}, result

    def test_pathspec_magic_is_not_interpreted(self, git_project):
        """main: `deleted`. `:` is git's null pathspec: without
        `--literal-pathspecs`, `git log -- ':'` matches the WHOLE tree, so the
        range is non-empty for a path that does not exist. The verdict is then
        decided by a question about every other file in the repo."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path=":", reported_at_commit=sha, project_dir=project
        )
        assert result == {"file_status": "unknown", "reason": "not_in_commit"}, result

    def test_an_empty_file_value_is_refused_before_git(self, git_project):
        """main: `git_error` — git exits 128 on an empty pathspec, and the
        failure is reported as if git had been asked a real question."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="", reported_at_commit=sha, project_dir=project
        )
        assert result == {"file_status": "unknown", "reason": "empty_path"}, result

    def test_a_nul_byte_does_not_escape_as_valueerror(self, git_project):
        """main: raises `ValueError: embedded null byte` straight out of the
        domain function — outside the `(SubprocessError, OSError)` contract
        every caller here is written against. Found by the Codex/Sol review."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="src/auth\0.py", reported_at_commit=sha, project_dir=project
        )
        assert result == {"file_status": "unknown", "reason": "invalid_path"}, result

    def test_an_absolute_path_outside_the_repo_is_out_of_scope(
        self, git_project, tmp_path_factory
    ):
        """main: `git_error` — a fabricated provenance failure of the FINDING
        for a file that exists and is version-controlled elsewhere. CB-89's
        measured population: 7 open cards on the autosorter tracker."""
        project, sha = git_project
        _commit_a_change(project)
        foreign = str(tmp_path_factory.mktemp("foreign"))
        with open(os.path.join(foreign, "app.py"), "w") as fh:
            fh.write("b = 1\n")
        result = provenance.file_status(
            file_path=os.path.join(foreign, "app.py"),
            reported_at_commit=sha,
            project_dir=project,
        )
        assert result["file_status"] == "unknown", result
        # The reason names the worktree that made the decision — a scope
        # boundary you cannot see is one you cannot debug (CB-11/CB-49).
        assert result["reason"].startswith("out_of_repo"), result
        assert os.path.realpath(project) in result["reason"], result

    def test_a_foreign_path_with_a_foreign_commit_is_out_of_scope(
        self, git_project, tmp_path_factory
    ):
        """main: `unreachable_commit` — the CB-2831 shape, and the sharpest one:
        the commit is REAL, in the repo that owns the file. It is "unreachable"
        only because reachability was tested against the wrong repository. This
        is why the scope check runs before `cat-file` for an absolute value."""
        project, _sha = git_project
        foreign = str(tmp_path_factory.mktemp("foreignrepo"))
        subprocess.run(["git", "init"], cwd=foreign, check=True, capture_output=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=foreign, check=True, capture_output=True)
        with open(os.path.join(foreign, "app.py"), "w") as fh:
            fh.write("b = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=foreign, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "c"], cwd=foreign, check=True, capture_output=True)
        foreign_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=foreign, text=True
        ).strip()

        result = provenance.file_status(
            file_path=os.path.join(foreign, "app.py"),
            reported_at_commit=foreign_sha,
            project_dir=project,
        )
        assert result["file_status"] == "unknown", result
        assert result["reason"].startswith("out_of_repo"), result

    def test_a_RELATIVE_path_into_another_repo_is_also_out_of_scope(
        self, git_project, tmp_path_factory
    ):
        """main: `unreachable_commit`. The spelling must not decide the answer.

        An earlier draft of this fix scoped an ABSOLUTE value before the
        reachability check and a relative one after it, so this — the natural
        spelling for a cross-repo card filed from a subdirectory — kept
        reporting a provenance failure of the finding, which is precisely what
        CB-89 was filed against. Caught by adversarial review of the fix, not by
        the fix's own tests, because every other case used an absolute path.
        """
        project, _sha = git_project
        foreign = str(tmp_path_factory.mktemp("relforeign"))
        subprocess.run(["git", "init"], cwd=foreign, check=True, capture_output=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "config", k, v], cwd=foreign, check=True, capture_output=True)
        with open(os.path.join(foreign, "app.py"), "w") as fh:
            fh.write("b = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=foreign, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "c"], cwd=foreign, check=True, capture_output=True)
        foreign_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=foreign, text=True
        ).strip()

        relative = os.path.relpath(os.path.join(foreign, "app.py"), project)
        assert relative.startswith(".."), relative  # the fixture must actually be relative

        result = provenance.file_status(
            file_path=relative, reported_at_commit=foreign_sha, project_dir=project
        )
        assert result["file_status"] == "unknown", result
        assert result["reason"].startswith("out_of_repo"), result

    def test_an_in_repo_symlink_escaping_the_worktree_is_out_of_scope(self, git_project):
        """main: `current`. The path is lexically inside the repo, so a
        containment check that trusts the spelling admits it and `os.stat` then
        reads a file git has no idea about. Resolving the PARENT physically is
        what refuses this while keeping a tracked symlink itself in scope."""
        project, sha = git_project
        _commit_a_change(project)
        os.symlink("/etc", os.path.join(project, "etcout"))
        result = provenance.file_status(
            file_path="etcout/hosts", reported_at_commit=sha, project_dir=project
        )
        assert result["reason"].startswith("out_of_repo"), result

    def test_the_batch_resolves_the_worktree_root_once(self, monkeypatch, conn, git_project):
        """The anti-retry property of the private `_repo_root_hint`, which was
        otherwise asserted only in prose.

        `check_findings` permits 10 000 rows and probes the root once for the
        batch. The third state matters most when the probe FAILS: without it
        every finding retries the same failing `rev-parse`, so the cost claim
        inverts exactly when the machine is already unhappy.
        """
        project, sha = git_project
        _commit_a_change(project)
        for i in range(4):
            findings.add_finding(
                conn,
                severity="low",
                category="c",
                file=f"src/f{i}.py",
                description=f"finding {i}",
                reported_at_commit=sha,
            )

        calls = {"n": 0}
        real = provenance._repo_root

        def counted(cwd):
            calls["n"] += 1
            return real(cwd)

        monkeypatch.setattr(provenance, "_repo_root", counted)
        provenance.check_findings(conn, project)
        assert calls["n"] == 1, f"probed the worktree root {calls['n']} times for one batch"

    def test_a_tracked_path_replaced_by_a_fifo_is_unknown_not_deleted(self, git_project):
        """main: `deleted`. The fixture matters and the plan's first draft got it
        wrong: a PLAIN fifo is untracked, so its log is empty and main answers
        `current`. Only a path that was tracked and is now a fifo reaches the
        branch under test."""
        project, sha = git_project
        target = os.path.join(project, "src", "sock.py")
        with open(target, "w") as fh:
            fh.write("s = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=project, check=True, capture_output=True)
        os.remove(target)
        os.mkfifo(target)

        result = provenance.file_status(
            file_path="src/sock.py", reported_at_commit=sha, project_dir=project
        )
        assert result == {"file_status": "unknown", "reason": "unsupported_path_kind"}, result

    def test_a_parent_traversal_landing_on_the_worktree_root_is_not_deleted(self, git_project):
        """main: `deleted` — for a path that IS the repository. The worktree root
        is inside the worktree; `rel == "."` needs no git call to know it is a
        tree."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="src/..", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "modified", result


class TestResolutionPinsThatMustNotRegress:
    """Green on BOTH sides, deliberately. Four of these pin exactly what an
    earlier draft of the CB-88 fix would have broken — each was reproduced
    during adversarial review — and two are contract pins that discriminate
    against no design considered. The distinction is stated per test, because a
    test that passes first try is otherwise indistinguishable from a broken one.
    """

    def test_an_absolute_path_inside_the_repo_still_answers(self, git_project):
        """REGRESSION PIN. Draft 1 refused every absolute path as `out_of_repo`;
        draft 2 then matched git's emitted name against the caller's spelling,
        and git prints `src/auth.py` for an absolute pathspec, so the same
        verdict was lost a second way. Absoluteness is not the criterion —
        containment is."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path=os.path.join(project, "src", "auth.py"),
            reported_at_commit=sha,
            project_dir=project,
        )
        assert result["file_status"] == "modified", result

    def test_a_dot_prefixed_spelling_still_answers(self, git_project):
        """REGRESSION PIN, same mechanism: git canonicalizes `./src/auth.py` to
        `src/auth.py` on output. Deriving the git spelling from the resolved
        path rather than from the input is what makes every spelling agree."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="./src/auth.py", reported_at_commit=sha, project_dir=project
        )
        assert result["file_status"] == "modified", result

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_an_unchanged_file_under_an_unreadable_parent_is_still_current(self, git_project):
        """REGRESSION PIN. Draft 1 moved the `stat` above the `git log`, which
        turned this correct `current` into `unknown`/`stat_error`: the stat is
        not needed to answer an empty range, so it must not run first."""
        project, sha = git_project
        parent = os.path.join(project, "src")
        os.chmod(parent, 0o000)
        try:
            result = provenance.file_status(
                file_path="src/auth.py", reported_at_commit=sha, project_dir=project
            )
        finally:
            os.chmod(parent, 0o755)
        assert result["file_status"] == "current", result

    def test_a_deleted_file_is_still_deleted_from_a_subdirectory_cwd(self, git_project):
        """REGRESSION PIN, REWRITTEN BY CB-93 — a DECLARED contract change, not
        a repair. Read this before "fixing" it back.

        Its purpose is unchanged and still load-bearing: a subdirectory cwd must
        not turn a correct `deleted` into `unknown`. Draft 1 of CB-88 broke
        exactly that by asking `ls-tree --full-tree` with a cwd-relative
        pathspec.

        What changed is the SPELLING the pin uses. It asserted `file="auth.py"`
        with cwd `<repo>/src`, i.e. a value relative to the SUBDIRECTORY — which
        `findings.py`'s "File path relative to project root" never sanctioned,
        and which only worked because every operation was cwd-anchored. CB-93
        ratified option (a): the documented contract wins, so the pin now uses
        the documented spelling and the subdirectory cwd is what it was always
        meant to be — irrelevant to the answer.

        Measured before the change: 0 of 3307 findings across the codebugs and
        autosorter trackers used a subdirectory-relative value, so the changed
        population is empty in practice. The old spelling's new answer is pinned
        as honest rather than merely different by
        `TestFileIsRootRelative::test_a_subdirectory_relative_value_is_unknown_never_confident`.
        """
        project, sha = git_project
        path = _commit_a_change(project)
        os.remove(path)
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "delete"], cwd=project, check=True, capture_output=True
        )
        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit=sha,
            project_dir=os.path.join(project, "src"),
        )
        assert result["file_status"] == "deleted", result

    def test_a_blob_that_became_a_directory_never_reports_modified(self, git_project):
        """CONTRACT PIN — green against every design considered, and named as
        such. A path that was a blob and is now a directory has genuinely lost
        its blob. The plan's first draft claimed it "stays `deleted`"; measured,
        git reports `R100` when the content survives the move and the answer is
        `renamed`, which is strictly better information. What must never happen
        is `modified` or `current`."""
        project, sha = git_project
        target = os.path.join(project, "src", "swap.py")
        with open(target, "w") as fh:
            fh.write("aaaa\nbbbb\ncccc\ndddd\n")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=project, check=True, capture_output=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip()
        os.remove(target)
        os.makedirs(target)
        with open(os.path.join(target, "inner.py"), "w") as fh:
            fh.write("aaaa\nbbbb\ncccc\ndddd\n")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "swap"], cwd=project, check=True, capture_output=True)

        result = provenance.file_status(
            file_path="src/swap.py", reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] in {"renamed", "deleted"}, result

    def test_a_path_alive_only_on_an_unmerged_branch_is_never_deleted(self, git_project):
        """CONTRACT PIN — green against every design considered. CB-89's
        observation 2: the obvious way to fix CB-88 badly is to start reporting
        these as `deleted`. The blob is known at the reported commit, so an
        empty range answers `current` and the deleted branch is unreachable."""
        project, _sha = git_project
        subprocess.run(
            ["git", "checkout", "-q", "-b", "feature"], cwd=project, check=True, capture_output=True
        )
        branch_file = os.path.join(project, "src", "only_on_branch.py")
        with open(branch_file, "w") as fh:
            fh.write("z = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "branch work"], cwd=project, check=True, capture_output=True
        )
        branch_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip()

        result = provenance.file_status(
            file_path="src/only_on_branch.py",
            reported_at_commit=branch_sha,
            project_dir=project,
        )
        assert result["file_status"] != "deleted", result


def _create_and_rename(project, old_rel, new_rel):
    """Create `old_rel`, commit (returning that sha), then `git mv` it to
    `new_rel` and commit. The returned sha is a commit whose tree holds
    `old_rel` as a blob, so `file_status` reaches the rename probe."""
    src = os.path.join(project, old_rel)
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w") as fh:
        fh.write("alpha\nbeta\ngamma\ndelta\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add"], cwd=project, check=True, capture_output=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()
    dst = os.path.join(project, new_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    subprocess.run(["git", "mv", src, dst], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "rename"], cwd=project, check=True, capture_output=True)
    return base


class TestRenameProbeParsing:
    """CB-92. Four routes into one wrong answer: the rename probe TAB-splits
    `git diff --name-status` and compares field 1 against the caller's RAW
    spelling, so a C-quoted path, a path holding a TAB, a non-canonical
    spelling, and a subdirectory cwd all miss the rename branch and fall
    through to the unconditional `deleted` return.

    That return is a POSITIVE claim that the file is gone, derived from a
    comparison that silently failed — this module's most-repeated defect
    (CB-79, CB-85, CB-88). Every test here was verified to FAIL against
    `git show main:src/codebugs/provenance.py`; the control was verified to
    pass on both sides and is named as such.
    """

    def test_a_non_ascii_path_is_renamed_not_deleted(self, git_project):
        """`core.quotePath` defaults true, so git prints `"src/\\303\\244.py"`
        — with literal quotes and octal escapes — and the equality test against
        the raw stored value cannot match."""
        project, _ = git_project
        base = _create_and_rename(project, "src/ä.py", "src/renamed.py")
        result = provenance.file_status(
            file_path="src/ä.py", reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] == "renamed", result

    def test_a_path_containing_a_tab_is_renamed_not_deleted(self, git_project):
        """A TAB in the name breaks the field split outright — the parse
        hazard `-c core.quotePath=false` would NOT have fixed, which is why
        the fix is `-z` and not that."""
        project, _ = git_project
        base = _create_and_rename(project, "src/a\tb.py", "src/renamed.py")
        result = provenance.file_status(
            file_path="src/a\tb.py", reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] == "renamed", result

    def test_a_non_canonical_spelling_is_renamed_not_deleted(self, git_project):
        """No quoting involved at all: git canonicalizes the name it prints, so
        `./src/x.py` is emitted as `src/x.py`. This route is NOT on CB-92 and is
        the evidence that CB-92 and CB-93 share one cause."""
        project, _ = git_project
        base = _create_and_rename(project, "src/moved.py", "src/renamed.py")
        result = provenance.file_status(
            file_path="./src/moved.py", reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] == "renamed", result

    def test_a_rename_is_found_from_a_subdirectory_cwd(self, git_project):
        """`git diff --name-status` prints ROOT-relative paths regardless of
        cwd (measured), so from a subdirectory the comparison against a
        cwd-relative spelling can never match for a nested path."""
        project, _ = git_project
        base = _create_and_rename(project, "src/moved.py", "src/renamed.py")
        result = provenance.file_status(
            file_path="src/moved.py",
            reported_at_commit=base,
            project_dir=os.path.join(project, "src"),
        )
        assert result["file_status"] == "renamed", result

    def test_a_plain_rename_is_still_found(self, git_project):
        """CONTROL — green on BOTH sides, deliberately. It pins the behaviour
        the change preserves; without it a parser that finds nothing at all
        would still turn the four tests above green only by accident."""
        project, _ = git_project
        base = _create_and_rename(project, "src/moved.py", "src/renamed.py")
        result = provenance.file_status(
            file_path="src/moved.py", reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] == "renamed", result
        assert "src/renamed.py" in result["reason"], result


class TestFileIsRootRelative:
    """CB-93, option (a) — RATIFIED by the user on 2026-08-18. `findings.py`
    documents `file` as "File path relative to project root"; every operation
    here now honours that, so the answer no longer depends on where the process
    happens to stand.

    The behaviour change is DECLARED, not incidental: a subdirectory-relative
    value stops resolving. Measured before ratifying — across the codebugs and
    autosorter trackers (3307 findings) not one value is subdirectory-relative,
    so the changed population is empty in practice.
    """

    def test_the_documented_spelling_answers_from_a_subdirectory(self, git_project):
        """The headline. Before this change the contract-correct spelling was
        the one that did NOT work from a subdirectory."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="src/auth.py",
            reported_at_commit=sha,
            project_dir=os.path.join(project, "src"),
        )
        assert result["file_status"] == "modified", result

    def test_the_ambient_path_honours_the_root(self, git_project, monkeypatch):
        """The production route: `staleness_check` passes `project_dir=None`,
        `check_findings` falls back to `_ambient_cwd()`, and `db.connect()`'s
        walk-up permits the server to start in any subdirectory. So a
        long-lived MCP server one level down misreported the whole tracker."""
        project, sha = git_project
        _commit_a_change(project)
        monkeypatch.chdir(os.path.join(project, "src"))
        result = provenance.file_status(file_path="src/auth.py", reported_at_commit=sha)
        assert result["file_status"] == "modified", result

    def test_a_subdirectory_relative_value_is_unknown_never_confident(self, git_project):
        """The declared cost of option (a), pinned so it stays HONEST rather
        than merely changed. `auth.py` is no longer read as `<root>/src/auth.py`
        — but it must degrade to `unknown` with a reason, never to a confident
        `deleted`/`current` about a path this repo has no record of."""
        project, sha = git_project
        _commit_a_change(project)
        result = provenance.file_status(
            file_path="auth.py",
            reported_at_commit=sha,
            project_dir=os.path.join(project, "src"),
        )
        assert result["file_status"] == "unknown", result
        assert result["reason"] == "not_in_commit", result

    def test_the_batch_path_honours_the_root_too(self, git_project, conn):
        """`check_findings` resolves the worktree root once and threads it as a
        hint; the hint must not reintroduce the cwd anchor for the batch."""
        project, sha = git_project
        _commit_a_change(project)
        findings.add_finding(
            conn,
            severity="high",
            category="test",
            file="src/auth.py",
            description="root-relative by contract",
            reported_at_commit=sha,
        )
        result = provenance.check_findings(conn, os.path.join(project, "src"))
        assert result["total"] == 1, result
        assert result["findings"][0]["file_status"] == "modified", result


class TestPremiseGitRecordShapes:
    """PREMISE PINS, in this repo's established `test_premise_*` shape. The
    parser above is built on two measured facts about git's own output. Neither
    is documented as stable, and if either changes the parser desynchronizes and
    silently reports NO renames — a confident `deleted` for every renamed file,
    which is precisely the defect CB-92 fixed. A git upgrade that moves either
    must turn this suite red rather than quietly disarm the fix.
    """

    def test_premise_name_status_is_root_relative_regardless_of_cwd(self, git_project):
        """The rename comparison uses `rel` because of THIS. `git diff` prints
        root-relative paths from anywhere (only `--relative` changes it), so a
        cwd-relative spelling can never match from a subdirectory."""
        project, _ = git_project
        base = _create_and_rename(project, "src/moved.py", "src/renamed.py")
        for cwd in (project, os.path.join(project, "src")):
            out = subprocess.check_output(
                ["git", "diff", "--diff-filter=R", "-M", "--name-status", f"{base}..HEAD"],
                cwd=cwd,
                text=True,
            )
            assert "src/moved.py\tsrc/renamed.py" in out, (cwd, out)

    def test_premise_z_uses_nul_separated_triples_for_renames(self, git_project):
        """`-z` emits `<status>\\0<old>\\0<new>\\0` — status NUL-separated from
        the first path, not TAB-separated. The parser consumes 3 fields per
        rename on this basis."""
        project, _ = git_project
        base = _create_and_rename(project, "src/moved.py", "src/renamed.py")
        out = subprocess.check_output(
            ["git", "diff", "--diff-filter=R", "-M", "--name-status", "-z", f"{base}..HEAD"],
            cwd=project,
            text=True,
        )
        fields = [f for f in out.split("\0") if f]
        assert "\t" not in out, out
        assert fields[0].startswith("R"), fields
        assert fields[1:3] == ["src/moved.py", "src/renamed.py"], fields

    def test_premise_z_emits_raw_bytes_that_break_strict_decoding(self, git_project):
        """Why `errors="surrogateescape"` is not optional. Without `-z` git
        C-quotes to pure ASCII; WITH it the raw bytes arrive and strict decoding
        raises `UnicodeDecodeError` — a `ValueError`, which this module's
        `(SubprocessError, OSError)` guards do NOT catch."""
        project, _ = git_project
        name = os.path.join(project, "src", b"bad\xff.py".decode("utf-8", "surrogateescape"))
        with open(name, "w") as fh:
            fh.write("alpha\nbeta\ngamma\ndelta\n")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=project, check=True, capture_output=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip()
        subprocess.run(
            ["git", "mv", name, os.path.join(project, "src", "ok.py")],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "commit", "-m", "mv"], cwd=project, check=True, capture_output=True)

        argv = ["git", "diff", "--diff-filter=R", "-M", "--name-status", "-z", f"{base}..HEAD"]
        with pytest.raises(UnicodeDecodeError) as exc:
            subprocess.check_output(argv, cwd=project, text=True)
        assert not isinstance(exc.value, (subprocess.SubprocessError, OSError))
        assert isinstance(exc.value, ValueError)
        # ...and the parameter the fix uses does round-trip it.
        out = subprocess.check_output(argv, cwd=project, text=True, errors="surrogateescape")
        assert "\0" in out, out


class TestUndecodablePathsDoNotKillTheBatch:
    """HOSTAGE TEST for the FATAL found by adversarial review of this change.
    The rename probe takes NO pathspec, so it reads every rename in the range.
    One undecodable path anywhere therefore poisoned every finding — including
    plain-ASCII ones — by raising out of `file_status` instead of degrading.
    """

    UNDECODABLE = os.path.join("src", b"other\xff.py".decode("utf-8", "surrogateescape"))

    def _repo_with_an_unrelated_undecodable_rename(self, project):
        return _create_and_rename(project, self.UNDECODABLE, "src/other_ok.py")

    def test_an_unrelated_undecodable_rename_does_not_raise(self, git_project):
        """The ASCII finding under test is genuinely deleted; the answer must be
        `deleted`, reached without an exception, despite the undecodable rename
        sharing the range."""
        project, _ = git_project
        base = self._repo_with_an_unrelated_undecodable_rename(project)
        os.remove(os.path.join(project, "src", "auth.py"))
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "rm"], cwd=project, check=True, capture_output=True)

        result = provenance.file_status(
            file_path="src/auth.py", reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] == "deleted", result

    def test_an_undecodable_path_is_itself_still_renamed(self, git_project):
        """And the undecodable path's own answer is correct, not merely
        non-fatal — `surrogateescape` round-trips it on both sides of the
        comparison."""
        project, _ = git_project
        spelling = self.UNDECODABLE
        base = self._repo_with_an_unrelated_undecodable_rename(project)
        result = provenance.file_status(
            file_path=spelling, reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] == "renamed", result


class TestNewlineInAPathIsParsed:
    """CB-92's third parse hazard. `-c core.quotePath=false` would have fixed
    the quoting and left THIS open, which is why the fix is `-z`. Named
    separately because it is the one route where the old `.splitlines()` parser
    and a field-count-only fix diverge."""

    def test_a_path_containing_a_newline_is_renamed_not_deleted(self, git_project):
        project, _ = git_project
        base = _create_and_rename(project, "src/a\nb.py", "src/renamed.py")
        result = provenance.file_status(
            file_path="src/a\nb.py", reported_at_commit=base, project_dir=project
        )
        assert result["file_status"] == "renamed", result


class TestAReasonStringAlwaysSerializes:
    """A verdict that cannot be returned is not a verdict. `surrogateescape`
    makes an undecodable git path COMPARABLE, but a lone surrogate cannot be
    encoded back to UTF-8 — and `reason` is serialized to JSON by the MCP layer.
    Measured: pydantic's `dump_json` (what the SDK uses) raises
    `PydanticSerializationError`, `json.dumps(ensure_ascii=False)` raises
    `UnicodeEncodeError`.

    Reachable on the ordinary MCP path via the rename DESTINATION: a stored
    `file` can never hold a surrogate (SQLite refuses it on write), but
    `new_path` comes from git. Found by following up a cross-model review
    question, not by the suite — the fix answered correctly and died on the way
    out.
    """

    def test_a_rename_to_an_undecodable_name_still_serializes(self, git_project):
        project, _ = git_project
        src = os.path.join(project, "src", "plain.py")
        with open(src, "w") as fh:
            fh.write("alpha\nbeta\ngamma\ndelta\n")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=project, check=True, capture_output=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip()
        dst = os.path.join(project, "src", b"weird\xff.py".decode("utf-8", "surrogateescape"))
        subprocess.run(["git", "mv", src, dst], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "mv"], cwd=project, check=True, capture_output=True)

        result = provenance.file_status(
            file_path="src/plain.py", reported_at_commit=base, project_dir=project
        )
        # The verdict is still correct...
        assert result["file_status"] == "renamed", result
        # ...and it can actually leave the process.
        assert not any(0xD800 <= ord(c) <= 0xDFFF for c in result["reason"]), result
        json.dumps(result, ensure_ascii=False).encode("utf-8")


class TestRenameRecordParser:
    """Unit tests for the pure parser. The desync branch is UNREACHABLE through
    real git (`--diff-filter=R` with no `-C` guarantees rename triples), so it
    could only be pinned by testing the function directly — which is why it was
    extracted. A branch that cannot be tested is a branch nobody can trust."""

    def test_a_well_formed_rename_record_parses(self):
        out = "R100\0src/old.py\0src/new.py\0"
        assert provenance._parse_rename_records(out) == [("src/old.py", "src/new.py")]

    def test_several_records_parse(self):
        out = "R100\0a\0b\0R087\0c\0d\0"
        assert provenance._parse_rename_records(out) == [("a", "b"), ("c", "d")]

    def test_empty_output_is_no_renames_not_a_failure(self):
        """The ordinary case when nothing was renamed. Must be `[]`, never None
        — None means "could not answer" and would turn every genuinely deleted
        file into `unknown`."""
        assert provenance._parse_rename_records("") == []

    def test_a_truncated_record_is_unparseable_not_empty(self):
        """The distinction the whole extraction exists for: `[]` would be read
        as "not renamed" and become a confident `deleted`."""
        assert provenance._parse_rename_records("R100\0src/old.py\0") is None

    def test_a_non_rename_status_is_unparseable_not_skipped(self):
        """A status this command cannot emit means the premise is broken, so
        the honest answer is "no answer", not a best-effort skip."""
        assert provenance._parse_rename_records("M\0src/a.py\0") is None
        assert provenance._parse_rename_records("R100\0a\0b\0M\0c\0") is None

    def test_paths_containing_tabs_and_newlines_survive(self):
        """What `-z` buys over `-c core.quotePath=false`."""
        out = "R100\0src/a\tb.py\0src/c\nd.py\0"
        assert provenance._parse_rename_records(out) == [("src/a\tb.py", "src/c\nd.py")]


class TestEveryVerdictSerializes:
    """RATCHET. `TestAReasonStringAlwaysSerializes` pinned the one route where a
    surrogate had actually been observed; this asserts the general property its
    name claims, across every verdict route the module can produce.

    It is what makes `_verdict` load-bearing rather than decorative: hardening
    `db.git_rev_parse` made `out_of_repo (worktree {root})` the next candidate
    bomb, and nothing but a boundary sanitizer plus this ratchet would have
    caught the one after that.
    """

    def _assert_serializes(self, result):
        assert not any(0xD800 <= ord(c) <= 0xDFFF for c in result["reason"]), result
        json.dumps(result, ensure_ascii=False).encode("utf-8")

    def test_every_route_out_of_file_status_serializes(self, git_project):
        project, sha = git_project
        undecodable = os.path.join("src", b"n\xff.py".decode("utf-8", "surrogateescape"))
        cases = {
            "no_provenance": dict(file_path="src/auth.py", reported_at_commit=None),
            "empty_path": dict(file_path="", reported_at_commit=sha),
            "invalid_path": dict(file_path="a\0b", reported_at_commit=sha),
            "out_of_repo": dict(file_path="/etc/hosts", reported_at_commit=sha),
            "unreachable": dict(file_path="src/auth.py", reported_at_commit="dead" * 10),
            "current": dict(file_path="src/auth.py", reported_at_commit=sha),
            "undecodable_input": dict(file_path=undecodable, reported_at_commit=sha),
        }
        for label, kwargs in cases.items():
            result = provenance.file_status(project_dir=project, **kwargs)
            self._assert_serializes(result)
            assert isinstance(result["file_status"], str), (label, result)

    def test_a_repo_whose_ROOT_NAME_is_undecodable_does_not_raise(self, tmp_path):
        """The third `-z`/decoding reader, missed by this branch's own first
        sibling sweep because the fixture used a non-UTF-8 path INSIDE the repo
        — and `git rev-parse --show-toplevel` only ever prints the root's OWN
        name, so that fixture structurally could not fail.

        Unfixed, this escaped as `UnicodeDecodeError` out of `db.git_rev_parse`,
        which is a `ValueError` and so outside the `(SubprocessError, OSError)`
        contract — killing a whole `check_findings` batch as a traceback.
        """
        repo = os.path.join(str(tmp_path), b"proj\xff".decode("utf-8", "surrogateescape"))
        os.makedirs(repo)
        for argv in (
            ["git", "init", "-q", "."],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(argv, cwd=repo, check=True, capture_output=True)
        with open(os.path.join(repo, "a.py"), "w") as fh:
            fh.write("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "a"], cwd=repo, check=True, capture_output=True)
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        with open(os.path.join(repo, "a.py"), "w") as fh:
            fh.write("x = 2\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "b"], cwd=repo, check=True, capture_output=True)

        result = provenance.file_status(
            file_path="a.py", reported_at_commit=base, project_dir=repo
        )
        assert result["file_status"] == "modified", result
        self._assert_serializes(result)
