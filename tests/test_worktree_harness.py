"""Tests for the worktree harness guards (`tools/_guards.sh`).

The harness exists because a workflow rule written only in prose was violated
the same day it was written. A harness that is itself only prose-tested would
repeat that, so every guard here is exercised on BOTH sides: the state it must
refuse, and the state it must let through.

Each guard is called in a real throwaway git repo and asserted on its EXIT
CODE, not on its message. Codes are the guards' documented API (see the table
at the top of `tools/_guards.sh`); asserting on English would pass against a
guard that fired for the wrong reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARDS = REPO_ROOT / "tools" / "_guards.sh"


def run_guard(fn: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source the guard library and invoke one function.

    `set -e` is deliberately NOT set: these functions signal with a return
    code, and under `set -e` the first non-zero return would kill the shell
    before the code could be echoed back.
    """
    quoted = " ".join(f"'{a}'" for a in args)
    script = f'source "{GUARDS}"\n{fn} {quoted}\n'
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on `main`."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    git(r, "add", "seed.txt")
    git(r, "commit", "-m", "seed")
    return r


class TestBranchType:
    """The guard that would have caught `worktree-cb-45-similarity-seam`."""

    @pytest.mark.parametrize(
        "branch",
        [
            "fix/cb-50-worktree-harness",
            "feature/similarity",
            "refactor/milestones-package-split",
            "docs/ledger",
            "fix/CB-9",
        ],
    )
    def test_sanctioned_types_pass(self, branch: str) -> None:
        assert run_guard("_guard_branch_type", branch).returncode == 0

    @pytest.mark.parametrize(
        "branch",
        [
            # The real 2026-08-16 offender.
            "worktree-cb-45-similarity-seam",
            "main",
            "chore/thing",  # sanctioned in autosorter, deliberately NOT here
            "fix",  # bare type, no slug
            "fix/",  # trailing slash, empty slug
            "fix/a/b",  # nested path is not one of the four shapes
            "Fix/cb-1",  # case-sensitive: the convention is lowercase
        ],
    )
    def test_off_convention_refused(self, branch: str) -> None:
        assert run_guard("_guard_branch_type", branch).returncode == 7


class TestFinishableBranch:
    def test_named_branch_passes(self) -> None:
        assert run_guard("_guard_finishable_branch", "fix/cb-1-x").returncode == 0

    def test_detached_head_refused(self) -> None:
        # `git branch --show-current` prints empty on a detached HEAD.
        assert run_guard("_guard_finishable_branch", "").returncode == 10

    def test_git_split_scratch_branch_refused(self) -> None:
        assert run_guard("_guard_finishable_branch", "temp-split-abc123").returncode == 10


class TestNonemptyDiff:
    def test_branch_with_work_passes(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        (repo / "new.txt").write_text("work\n")
        git(repo, "add", "new.txt")
        git(repo, "commit", "-m", "work")
        assert run_guard("_guard_nonempty_diff", str(repo), base).returncode == 0

    def test_branch_identical_to_main_refused(self, repo: Path) -> None:
        """The exact shape `worktree-cb-45-similarity-seam` ended in.

        After its merge it kept receiving commits until it pointed at main's own
        SHA, at which point every further merge was a silent fast-forward.
        """
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        assert run_guard("_guard_nonempty_diff", str(repo), base).returncode == 9


class TestConflictMarkers:
    def test_clean_branch_passes(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        (repo / "f.py").write_text("x = 1\n")
        git(repo, "add", "f.py")
        git(repo, "commit", "-m", "add")
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 0

    def test_committed_markers_refused(self, repo: Path) -> None:
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        (repo / "f.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> other\n")
        git(repo, "add", "f.py")
        git(repo, "commit", "-m", "oops")
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 5

    def test_reads_the_tree_not_the_working_copy(self, repo: Path) -> None:
        """A local fix must not mask a marker that is committed on the branch.

        This is the discriminating case: both this test and the one above end
        with markers on HEAD, but here the working copy is clean, so a guard
        that grepped the filesystem would pass and miss it.
        """
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        (repo / "f.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> other\n")
        git(repo, "add", "f.py")
        git(repo, "commit", "-m", "oops")
        (repo / "f.py").write_text("a\n")  # fixed locally, NOT committed
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 5

    def test_deleted_file_does_not_break_the_scan(self, repo: Path) -> None:
        """A deleted path has no content to read; --diff-filter=d must skip it."""
        (repo / "gone.py").write_text("x = 1\n")
        git(repo, "add", "gone.py")
        git(repo, "commit", "-m", "add")
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        git(repo, "rm", "-q", "gone.py")
        git(repo, "commit", "-m", "remove")
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 0


class TestStaleBase:
    def test_within_threshold_passes(self) -> None:
        assert run_guard("_guard_stale_base", "39", "40", "false").returncode == 0

    def test_at_threshold_passes(self) -> None:
        assert run_guard("_guard_stale_base", "40", "40", "false").returncode == 0

    def test_beyond_threshold_refused(self) -> None:
        assert run_guard("_guard_stale_base", "41", "40", "false").returncode == 6

    def test_override_proceeds(self) -> None:
        assert run_guard("_guard_stale_base", "999", "40", "true").returncode == 0

    def test_non_numeric_does_not_block(self) -> None:
        """A failed merge-base is an UNCERTAIN signal, not a stale one.

        Blocking on a transient git error would wedge every finish — a worse
        and far more common hazard than the diverged history it would catch.
        """
        assert run_guard("_guard_stale_base", "?", "40", "false").returncode == 0


class TestWorkspaceOnMain:
    def test_on_main_passes(self, repo: Path) -> None:
        assert run_guard("_guard_workspace_on_main", str(repo)).returncode == 0

    def test_other_branch_refused(self, repo: Path) -> None:
        git(repo, "checkout", "-q", "-b", "fix/cb-1-x")
        assert run_guard("_guard_workspace_on_main", str(repo)).returncode == 8

    def test_detached_head_refused(self, repo: Path) -> None:
        git(repo, "checkout", "-q", "--detach")
        assert run_guard("_guard_workspace_on_main", str(repo)).returncode == 8


class TestMainClean:
    def test_clean_passes(self, repo: Path) -> None:
        assert run_guard("_guard_main_clean", str(repo)).returncode == 0

    def test_modified_tracked_file_refused(self, repo: Path) -> None:
        (repo / "seed.txt").write_text("changed\n")
        assert run_guard("_guard_main_clean", str(repo)).returncode == 11

    def test_staged_change_refused(self, repo: Path) -> None:
        (repo / "seed.txt").write_text("changed\n")
        git(repo, "add", "seed.txt")
        assert run_guard("_guard_main_clean", str(repo)).returncode == 11

    def test_untracked_only_warns_but_passes(self, repo: Path) -> None:
        """A `.claude/plans/*.md` note sits untracked in main mid-session.

        One did while this very card was being built. A merge cannot collide
        with a file git does not track, so this warns and proceeds — and the
        warning is asserted, or the branch would be untested.
        """
        (repo / "note.md").write_text("plan\n")
        result = run_guard("_guard_main_clean", str(repo))
        assert result.returncode == 0
        assert "note.md" in result.stderr


class TestUntrackedPyAtRoot:
    def test_clean_status_passes(self) -> None:
        assert run_guard("_guard_untracked_py_at_root", " M src/codebugs/db.py").returncode == 0

    def test_untracked_root_py_refused(self) -> None:
        assert run_guard("_guard_untracked_py_at_root", "?? scratch.py").returncode == 4

    def test_untracked_py_in_subdir_passes(self) -> None:
        """Only TOP-LEVEL untracked .py files are suspect.

        A new module under src/ or a new test is the normal way this repo
        grows; refusing those would fire on nearly every feature branch.
        """
        assert run_guard("_guard_untracked_py_at_root", "?? src/codebugs/new.py").returncode == 0
        assert run_guard("_guard_untracked_py_at_root", "?? tests/test_new.py").returncode == 0


class TestLeakedRepr:
    def test_normal_staged_files_pass(self, repo: Path) -> None:
        (repo / "f.py").write_text("x = 1\n")
        git(repo, "add", "f.py")
        assert run_guard("_guard_leaked_repr", str(repo)).returncode == 0

    def test_repr_filename_refused(self, repo: Path) -> None:
        leaked = repo / "<sqlite3.Connection object at 0x7f00>"
        leaked.write_text("junk\n")
        git(repo, "add", "--", leaked.name)
        assert run_guard("_guard_leaked_repr", str(repo)).returncode == 3


class TestHarnessIntegrity:
    """The scripts themselves — syntax, and the one constant that must not drift."""

    @pytest.mark.parametrize(
        "script",
        [
            "tools/_guards.sh",
            "tools/worktree-setup.sh",
            "tools/worktree-finish.sh",
            "tools/install-hooks.sh",
            "tools/pre-commit-hook.sh",
        ],
    )
    def test_script_parses(self, script: str) -> None:
        path = REPO_ROOT / script
        assert path.exists(), f"{script} is missing"
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        "script",
        [
            "tools/worktree-setup.sh",
            "tools/worktree-finish.sh",
            "tools/install-hooks.sh",
            "tools/pre-commit-hook.sh",
        ],
    )
    def test_script_is_executable(self, script: str) -> None:
        assert (REPO_ROOT / script).stat().st_mode & 0o111, f"{script} is not executable"

    def test_branch_types_agree_across_the_harness(self) -> None:
        """`_BRANCH_TYPES` is declared twice and the copies must not diverge.

        The pre-commit hook cannot source the library — it runs from
        `.git/hooks/` as a symlink and must work even if `tools/` is missing
        from the checked-out tree (a `git checkout` of an older commit). So it
        carries its own copy, and this test is what keeps the two honest. Same
        shape as the repo's other duplicated-check rule: a check that is
        duplicated rather than shared is one drift away from disagreeing with
        itself.
        """
        lib = (REPO_ROOT / "tools" / "_guards.sh").read_text()
        hook = (REPO_ROOT / "tools" / "pre-commit-hook.sh").read_text()
        decl = "_BRANCH_TYPES=(fix feature refactor docs)"
        assert decl in lib, "tools/_guards.sh changed its branch-type list"
        assert decl in hook, "tools/pre-commit-hook.sh drifted from tools/_guards.sh"

    def test_installer_targets_the_main_checkout_not_the_invoking_one(self) -> None:
        """The hook symlink must point at REPO_ROOT/tools, never $_SCRIPT_DIR.

        install-hooks.sh is normally run from a WORKTREE, because the harness
        arrives with the branch that adds it. A symlink into a worktree dangles
        the instant that worktree is removed, and git skips a dangling hook
        SILENTLY — no warning, no error, just no enforcement. That is the same
        silent-loss shape the whole harness exists to prevent, and the first
        version of the installer had it (caught by running it, not reading it).
        """
        src = (REPO_ROOT / "tools" / "install-hooks.sh").read_text()
        assert 'HOOK_SRC="${REPO_ROOT}/tools/pre-commit-hook.sh"' in src
        assert 'ln -sfn "${HOOK_SRC}"' in src
        assert '"${_SCRIPT_DIR}/pre-commit-hook.sh"' not in src, (
            "installer points the hook at the invoking checkout; it will dangle"
        )

    def test_finish_never_fast_forwards(self) -> None:
        """`--no-ff` on the integration merge is the whole point of CB-50.

        On 2026-08-16 main was advanced by a fast-forward 1h53m after the
        --no-ff rule was written. Asserting the flag's presence in the source
        is the only check that discriminates: a behavioural test would need a
        branch whose merge git could fast-forward, and `merge.ff=false` in a
        developer's clone would mask the omission.
        """
        finish = (REPO_ROOT / "tools" / "worktree-finish.sh").read_text()
        merges = [
            line
            for line in finish.splitlines()
            if 'merge "${BRANCH}"' in line and line.strip().startswith(("if git", "git"))
        ]
        assert merges, "could not find the integration merge in worktree-finish.sh"
        for line in merges:
            assert "--no-ff" in line, f"integration merge lost --no-ff: {line.strip()}"


class TestPreCommitHook:
    """The hook is what binds a session that never read CLAUDE.md."""

    @staticmethod
    def _install(repo: Path) -> None:
        hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / "tools" / "pre-commit-hook.sh", hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)

    def _commit(self, repo: Path, *paths: str) -> subprocess.CompletedProcess[str]:
        subprocess.run(["git", "-C", str(repo), "add", *paths], check=True)
        return subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "attempt"],
            capture_output=True,
            text=True,
        )

    def test_source_edit_on_main_refused(self, repo: Path) -> None:
        self._install(repo)
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        assert self._commit(repo, "src/mod.py").returncode != 0

    def test_plan_note_on_main_allowed(self, repo: Path) -> None:
        """CLAUDE.md's single stated exception, and the repo relies on it.

        Every ledger and handoff commit lands on main this way.
        """
        self._install(repo)
        plans = repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "CB-50-notes.md").write_text("# notes\n")
        assert self._commit(repo, ".claude/plans/CB-50-notes.md").returncode == 0

    def test_mixed_commit_on_main_refused(self, repo: Path) -> None:
        """A plan note does not launder the source file riding along with it."""
        self._install(repo)
        plans = repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "CB-50-notes.md").write_text("# notes\n")
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        result = self._commit(repo, ".claude/plans/CB-50-notes.md", "src/mod.py")
        assert result.returncode != 0
        assert "src/mod.py" in result.stderr

    def test_nested_plan_path_refused(self, repo: Path) -> None:
        """The allowance is `.claude/plans/*.md`, one level, not a subtree."""
        self._install(repo)
        deep = repo / ".claude" / "plans" / "sub"
        deep.mkdir(parents=True)
        (deep / "note.md").write_text("# notes\n")
        assert self._commit(repo, ".claude/plans/sub/note.md").returncode != 0

    def test_source_edit_on_sanctioned_branch_allowed(self, repo: Path) -> None:
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/cb-50-harness")
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        assert self._commit(repo, "src/mod.py").returncode == 0

    def test_off_convention_branch_refused(self, repo: Path) -> None:
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "worktree-cb-45-similarity-seam")
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        assert self._commit(repo, "src/mod.py").returncode != 0

    def test_no_verify_still_works(self, repo: Path) -> None:
        """The escape hatch is deliberate: the hook stops the ACCIDENT.

        An operator typing --no-verify has stated an intent, and the flag in
        the reflog is the record of it.
        """
        self._install(repo)
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        subprocess.run(["git", "-C", str(repo), "add", "src/mod.py"], check=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-m", "forced"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
