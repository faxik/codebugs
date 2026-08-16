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
            # PREFIXED types. Without a `^` anchor the regex matches anywhere,
            # so all of these were accepted — and no case in this table began
            # with a prefix, so the missing anchor survived mutation testing
            # until cross-model review found it. CLAUDE.md already carries this
            # trap for SQL identifiers ("anchor with fullmatch, not ^…$").
            "my-fix/cb-1",
            "wip-feature/x",
            "x-docs/y",
            # SUFFIXED, the mirror case: a missing `$` would accept these.
            "fix/cb-1 evil",
            "fix/cb-1\nmain",
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

    def test_marker_near_top_of_a_large_file(self, repo: Path) -> None:
        """A marker at the top of a big file — the SIGPIPE false negative.

        The guard used to be `git show … | grep -q`. `grep -q` exits at the
        first match, which SIGPIPEs `git show` (exit 141); the callers run
        under `set -o pipefail`, so the pipeline reported non-zero and the
        marker was silently ACCEPTED. Verified against the old code on a 4 MB
        file: it missed the marker entirely.

        The failure needs SIZE — every other test in this class uses a
        five-line fixture, where `git show` finishes before grep exits and the
        bug cannot appear. Found by cross-model review, not by this suite.
        """
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        big = repo / "big.py"
        big.write_text(
            "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n"
            + "".join(f"filler line {i}\n" for i in range(200_000))
        )
        assert big.stat().st_size > 2_000_000, "fixture too small to provoke SIGPIPE"
        git(repo, "add", "big.py")
        git(repo, "commit", "-m", "big")
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 5

    def test_large_clean_file_is_not_a_false_positive(self, repo: Path) -> None:
        """The other half: consuming the whole stream must not invent markers."""
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        (repo / "big.py").write_text("".join(f"clean line {i}\n" for i in range(200_000)))
        git(repo, "add", "big.py")
        git(repo, "commit", "-m", "big")
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 0

    def test_deleted_file_does_not_break_the_scan(self, repo: Path) -> None:
        """A deleted path has no content to read; --diff-filter=d must skip it.

        DISCRIMINATION NOTE: an earlier version of this test deleted the only
        file and asserted rc==0, which passes with or without --diff-filter=d
        (without it, `git show HEAD:gone.py` fails, `|| continue` skips, and
        `bad` stays empty — the same rc). Cross-model review flagged it as a
        test that cannot fail. It now deletes one file AND keeps a marker in
        another, so dropping the filter changes the outcome: the scan must
        still reach the surviving file and report it.
        """
        (repo / "gone.py").write_text("x = 1\n")
        git(repo, "add", "gone.py")
        git(repo, "commit", "-m", "add")
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        git(repo, "rm", "-q", "gone.py")
        (repo / "kept.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        git(repo, "add", "kept.py")
        git(repo, "commit", "-m", "remove one, add a marker in another")
        result = run_guard("_guard_conflict_markers", str(repo), base)
        assert result.returncode == 5
        assert "kept.py" in result.stderr
        assert "gone.py" not in result.stderr

    def test_pure_deletion_alone_is_clean(self, repo: Path) -> None:
        """The other half: a branch that ONLY deletes must still pass."""
        (repo / "gone.py").write_text("x = 1\n")
        git(repo, "add", "gone.py")
        git(repo, "commit", "-m", "add")
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        git(repo, "rm", "-q", "gone.py")
        git(repo, "commit", "-m", "remove")
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 0

    def test_non_ascii_path_is_scanned(self, repo: Path) -> None:
        """Default core.quotePath C-quotes non-ASCII names.

        The guard used `--name-only` without -z, so such a path came back as
        "\\321\\202.py", `git show HEAD:<that>` failed, `|| continue` skipped
        it, and a committed marker was silently accepted — the same
        silent-skip shape as the SIGPIPE bug. Found by cross-model review.
        """
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-b", "fix/cb-1-x")
        (repo / "тест.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "non-ascii path")
        assert run_guard("_guard_conflict_markers", str(repo), base).returncode == 5


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

    def test_untracked_colliding_with_a_branch_added_path_refused(self, repo: Path) -> None:
        """"git cannot collide with an untracked file" is FALSE.

        That was this guard's original stated rationale, and cross-model review
        refuted it. If the branch ADDS a path that exists untracked in main,
        git refuses the merge: "The following untracked working tree files
        would be overwritten by merge". Verified by running a real merge.
        Without this the failure lands mid-integration instead of cleanly here.
        """
        base = git(repo, "rev-parse", "HEAD")
        wt = repo.parent / "wt-adds"
        git(repo, "worktree", "add", "-q", "-b", "fix/cb-1-adds", str(wt), "main")
        (wt / "src").mkdir()
        (wt / "src" / "new.py").write_text("from branch\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-m", "add new.py")
        # main stays on main; the colliding path exists there only as untracked.
        (repo / "src").mkdir(exist_ok=True)
        (repo / "src" / "new.py").write_text("untracked in main\n")
        result = run_guard("_guard_main_clean", str(repo), str(wt), base)
        assert result.returncode == 11
        assert "src/new.py" in result.stderr

    def test_untracked_not_added_by_branch_still_passes(self, repo: Path) -> None:
        """The discriminator: only a COLLIDING untracked path is fatal.

        Otherwise the fix would ban the ordinary mid-session plan note, which
        is the case the warn-only branch exists for.
        """
        base = git(repo, "rev-parse", "HEAD")
        wt = repo.parent / "wt-other"
        git(repo, "worktree", "add", "-q", "-b", "fix/cb-1-adds", str(wt), "main")
        (wt / "other.py").write_text("from branch\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-m", "add other.py")
        (repo / "note.md").write_text("plan\n")
        assert run_guard("_guard_main_clean", str(repo), str(wt), base).returncode == 0

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


class TestEnforcementArmed:
    """Enforcement is per-clone and uncommittable — so it must be checked.

    A fresh clone has no hook and no `merge.ff`, and git skips a missing or
    dangling hook SILENTLY. Without this guard the harness would let an
    unarmed clone integrate while looking exactly like an armed one.
    """

    @staticmethod
    def _hook_path(repo: Path) -> Path:
        common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        hooks = Path(common) / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        return hooks / "pre-commit"

    def _arm(self, repo: Path) -> Path:
        """Arm the test repo the way install-hooks.sh does.

        The repo needs its OWN tools/pre-commit-hook.sh, because the guard
        checks the installed hook's IDENTITY against `<repo>/tools/` — file
        existence alone is not evidence that the hook is this hook.
        """
        git(repo, "config", "merge.ff", "false")
        tools = repo / "tools"
        tools.mkdir(exist_ok=True)
        canonical = tools / "pre-commit-hook.sh"
        shutil.copy(REPO_ROOT / "tools" / "pre-commit-hook.sh", canonical)
        canonical.chmod(0o755)
        hook = self._hook_path(repo)
        if hook.exists() or hook.is_symlink():
            hook.unlink()
        hook.symlink_to(canonical)
        return hook

    def test_fully_armed_passes(self, repo: Path) -> None:
        self._arm(repo)
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0

    def test_fresh_clone_refused(self, repo: Path) -> None:
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 12

    def test_missing_merge_ff_refused(self, repo: Path) -> None:
        self._arm(repo)
        git(repo, "config", "--unset", "merge.ff")
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "merge.ff" in result.stderr

    def test_merge_ff_true_refused(self, repo: Path) -> None:
        """`true` is not merely 'unset' — someone turned it back on."""
        self._arm(repo)
        git(repo, "config", "merge.ff", "true")
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 12

    def test_dangling_hook_symlink_refused(self, repo: Path) -> None:
        """The real defect this guard was written for.

        The first install-hooks.sh pointed the symlink at the authoring
        WORKTREE. Removing that worktree would have left a repo that looked
        armed — the symlink is right there in .git/hooks — and silently was
        not, because git skips a hook it cannot execute.
        """
        self._arm(repo)
        hook = self._hook_path(repo)
        hook.unlink()
        hook.symlink_to(repo / "nonexistent" / "pre-commit-hook.sh")
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "DANGLING" in result.stderr

    def test_non_executable_hook_refused(self, repo: Path) -> None:
        self._arm(repo)
        (repo / "tools" / "pre-commit-hook.sh").chmod(0o644)
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "not executable" in result.stderr

    def test_hook_symlinked_into_a_worktree_refused(self, repo: Path) -> None:
        """Existence is not identity — and this state was LIVE in this repo.

        The first install-hooks.sh symlinked the hook into the authoring
        worktree. Such a link satisfies -e and -x right up until that worktree
        is removed, which is the last thing worktree-finish.sh does — to
        itself. So a finish could pass this guard, land, remove its worktree,
        and leave the repo silently unarmed, with the symlink still sitting in
        .git/hooks looking correct.
        """
        self._arm(repo)
        stray = repo / "elsewhere"
        stray.mkdir()
        impostor = stray / "pre-commit-hook.sh"
        shutil.copy(REPO_ROOT / "tools" / "pre-commit-hook.sh", impostor)
        impostor.chmod(0o755)
        hook = self._hook_path(repo)
        hook.unlink()
        hook.symlink_to(impostor)
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "not main's copy" in result.stderr

    def test_noop_impostor_hook_refused(self, repo: Path) -> None:
        """A hand-written `exit 0` passes every existence check and enforces nothing."""
        self._arm(repo)
        hook = self._hook_path(repo)
        hook.unlink()
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 12

    def test_identical_copy_is_accepted(self, repo: Path) -> None:
        """Not everyone symlinks. A byte-identical copy is genuinely armed."""
        self._arm(repo)
        hook = self._hook_path(repo)
        hook.unlink()
        shutil.copy(repo / "tools" / "pre-commit-hook.sh", hook)
        hook.chmod(0o755)
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0


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

    def test_repr_filename_without_angle_brackets_refused(self, repo: Path) -> None:
        """The guard's pattern has two alternatives; both need a test.

        The original suite only had the angle-bracketed case, which matches
        `^<.*>` — so deleting the `Connection object at 0x` alternative left
        every test passing while that shape stopped being caught. That was
        found as an uncaught mutation by cross-model review, and it is exactly
        the "a check that validates elements cannot validate their
        composition" shape this repo keeps rediscovering: two alternatives,
        one covered.
        """
        leaked = repo / "Connection object at 0x7f00"
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


class TestGuardsAreActuallyInvoked:
    """Every guard is unit-tested; that does NOT test the COMPOSITION.

    Cross-model review deleted three guard CALLS from worktree-finish.sh —
    including `_guard_branch_type`, the one that exists for the 2026-08-16
    incident — and the whole suite stayed green, because nothing here executed
    the script. That is this repo's own recurring rule turned on its own
    harness: *a check that validates elements cannot validate their
    composition.*

    Executing the full script in a test is impractical (it merges onto main and
    runs a 70-second suite), so these assert the WIRING: each guard is called,
    with its return code propagated, in the right phase. Structural rather than
    behavioural, and said so plainly — but it fails when a call is deleted,
    which the previous suite did not.
    """

    FINISH = REPO_ROOT / "tools" / "worktree-finish.sh"
    SETUP = REPO_ROOT / "tools" / "worktree-setup.sh"

    @pytest.mark.parametrize(
        "guard",
        [
            "_guard_finishable_branch",
            "_guard_branch_type",
            "_guard_untracked_py_at_root",
            "_guard_leaked_repr",
            "_guard_nonempty_diff",
            "_guard_conflict_markers",
            "_guard_stale_base",
            "_guard_enforcement_armed",
            "_guard_workspace_on_main",
            "_guard_main_clean",
        ],
    )
    def test_finish_invokes_guard_and_propagates_its_code(self, guard: str) -> None:
        src = self.FINISH.read_text()
        calls = [ln.strip() for ln in src.splitlines() if ln.strip().startswith(guard + " ")]
        assert calls, f"worktree-finish.sh never calls {guard}"
        for call in calls:
            assert "|| exit $?" in call, (
                f"{guard} is called without propagating its exit code: {call}"
            )

    def test_setup_validates_the_branch_before_creating_anything(self) -> None:
        """Order matters: a refusal must leave no half-made worktree."""
        src = self.SETUP.read_text()
        guard_at = src.index("_guard_branch_type")
        create_at = src.index("worktree add")
        assert guard_at < create_at, "branch type is validated after the worktree is created"

    def test_enforcement_armed_runs_before_the_lock_is_opened(self) -> None:
        """Fail fast, rather than after waiting up to 60s on the lock."""
        src = self.FINISH.read_text()
        assert src.index("_guard_enforcement_armed") < src.index("exec 9>")

    def test_skew_check_uses_the_value_the_gates_ran_against(self) -> None:
        """CB-41's shape, reintroduced by this card's own round-1 fix.

        TESTED_MAIN was sampled AFTER pytest. A concurrent finish landing
        during the ~70s test window moved main, the post-test sample recorded
        the NEW main, and the in-lock comparison then compared new-main to
        new-main and passed — the skew guard silently certifying the untested
        combination it was written to refuse. Reproduced deterministically by
        the round-2 adversary with a stubbed slow test command.

        The repair is the CB-41 repair: make it unrepresentable rather than
        re-establish discipline at the point of use. TESTED_MAIN is now the
        SAME VALUE the forward-merge consumed, so no statement can be inserted
        between the sample and the use.
        """
        src = self.FINISH.read_text()
        assert 'TESTED_MAIN="${CURRENT_MAIN}"' in src, (
            "TESTED_MAIN must be the value the forward-merge used, not a re-sample"
        )
        assert "TESTED_MAIN=$(git" not in src, "TESTED_MAIN is re-sampled from git"
        # And it must be established before the gates, not after them.
        assert src.index('TESTED_MAIN="${CURRENT_MAIN}"') < src.index("pytest tests/")


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

    def test_clean_no_ff_merge_onto_main_allowed(self, repo: Path) -> None:
        """The documented landing path must not be blocked by the hook."""
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/cb-1-work")
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        self._commit(repo, "src/mod.py")
        git(repo, "checkout", "-q", "main")
        result = subprocess.run(
            ["git", "-C", str(repo), "merge", "fix/cb-1-work", "--no-ff", "--no-edit"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_conflicted_merge_on_main_can_be_completed(self, repo: Path) -> None:
        """The asymmetry a peer session found by hitting it.

        git runs `pre-merge-commit` for a merge it completes itself — not
        installed here, so a CLEAN merge always passed. A CONFLICTED merge is
        finished by hand with `git commit`, which DOES run pre-commit, and the
        hook then saw staged source files on main and refused. So the hook was
        merge-safe only when the merge was clean, i.e. it failed exactly when
        the operator was already mid-conflict.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/cb-1-work")
        (repo / "seed.txt").write_text("branch side\n")
        self._commit(repo, "seed.txt")
        git(repo, "checkout", "-q", "main")
        (repo / "seed.txt").write_text("main side\n")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-am", "main side"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "merge", "fix/cb-1-work", "--no-ff", "--no-edit"],
            capture_output=True,
        )
        (repo / "seed.txt").write_text("resolved\n")
        subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-edit"], capture_output=True, text=True
        )
        assert result.returncode == 0, f"conflicted merge blocked on main: {result.stderr}"

    def test_merge_allowance_did_not_make_the_hook_toothless(self, repo: Path) -> None:
        """Discriminator for the fix above: no merge in progress, still refused.

        Without this, 'allow when MERGE_HEAD exists' could be widened to
        'always allow' and every hook test above would still pass.
        """
        self._install(repo)
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        assert self._commit(repo, "src/mod.py").returncode != 0

    def test_hook_and_guard_agree_on_nested_branch(self, repo: Path) -> None:
        """`fix/a/b` must be refused by BOTH, at the same moment.

        The hook used a loose prefix test while the finish guard used a full
        shape regex, so `fix/a/b` could accumulate commits for hours and then
        be refused at integration — the worst moment to learn the name is
        wrong. Asserted against the guard's own verdict so the two cannot drift
        apart again without this failing.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/a/b")
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        assert run_guard("_guard_branch_type", "fix/a/b").returncode == 7
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
