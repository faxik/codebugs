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

import re
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

    # -- the pre-merge-commit half (CB-57) ---------------------------------

    def _arm_merge_hook(self, repo: Path) -> Path:
        """Add the second hook, the way install-hooks.sh step [2/3] does."""
        canonical = repo / "tools" / "pre-merge-commit-hook.sh"
        shutil.copy(REPO_ROOT / "tools" / "pre-merge-commit-hook.sh", canonical)
        canonical.chmod(0o755)
        hook = self._hook_path(repo).with_name("pre-merge-commit")
        if hook.exists() or hook.is_symlink():
            hook.unlink()
        hook.symlink_to(canonical)
        return hook

    def test_both_hooks_armed_passes(self, repo: Path) -> None:
        self._arm(repo)
        self._arm_merge_hook(repo)
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0

    def test_merge_hook_missing_is_refused_once_its_source_exists(self, repo: Path) -> None:
        """The case a clone armed before CB-57 is actually in.

        pre-commit installed, merge.ff set, everything looks armed — and the
        merge gate is simply absent. Once main carries the source, that is a
        real missing half and must refuse, not warn.
        """
        self._arm(repo)
        canonical = repo / "tools" / "pre-merge-commit-hook.sh"
        shutil.copy(REPO_ROOT / "tools" / "pre-merge-commit-hook.sh", canonical)
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "pre-merge-commit" in result.stderr

    def test_merge_hook_not_demanded_before_its_source_lands(self, repo: Path) -> None:
        """The BOOTSTRAP, and it is load-bearing rather than a loophole.

        This guard runs BEFORE the merge that first brings
        tools/pre-merge-commit-hook.sh onto main. Demanding the hook
        unconditionally would make the commit that introduces it unlandable by
        the harness it extends — the CB-50 bootstrap repeating. So a repo where
        the path has never existed and has no history still passes.

        Read this together with the test below: the condition is MONOTONIC, and
        an earlier version of this test pinned a hole open instead.
        """
        self._arm(repo)
        assert not (repo / "tools" / "pre-merge-commit-hook.sh").exists()
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0

    def test_tilde_core_hookspath_is_not_mistaken_for_relative(self, repo: Path) -> None:
        """A false REFUSAL: git expands `~`, so `~/hooks` is an absolute path.

        Reading the raw config value classed it as relative and refused a clone
        that was genuinely armed. The guard was resolving the same setting two
        ways in one function — `--git-path` (which agrees with git) for the hooks
        dir, and the raw string for this test. `--type=path` makes them agree.
        """
        self._arm(repo)
        git(repo, "config", "core.hooksPath", "~/hooks")
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert "RELATIVE" not in result.stderr, (
            "a tilde path was classed relative; git expands it to an absolute one"
        )

    def test_relative_core_hookspath_is_refused(self, repo: Path) -> None:
        """A relative core.hooksPath resolves PER WORKING TREE, so it is unverifiable.

        `core.hooksPath=.githooks` means one directory in the primary checkout and
        a different one in every linked worktree. Review reproduced: armed in the
        primary, main checked out in a linked worktree with no .githooks there,
        guard rc=0, and a source commit straight onto main rc=0. The guard cannot
        honestly say "this clone is armed" about a per-worktree path, so it
        declines.
        """
        self._arm(repo)
        githooks = repo / ".githooks"
        githooks.mkdir()
        shutil.copy(repo / "tools" / "pre-commit-hook.sh", githooks / "pre-commit")
        (githooks / "pre-commit").chmod(0o755)
        git(repo, "config", "core.hooksPath", ".githooks")
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12, "a relative core.hooksPath passed as verifiable"
        assert "RELATIVE" in result.stderr

    def test_disarm_still_refused_in_a_clone_without_a_local_main(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """The 'monotonic' gate read the literal ref `main` — and lost to a clone.

        `git clone --single-branch --branch fix/…` gives a clone with no LOCAL
        main. `git log -1 main -- <path>` then fatals, the condition collapses to
        file-existence, and review reproduced the full round-2 disarm again: rm
        the source, guard rc=0, untyped branch merged onto main. `--all` consults
        every ref, so no checkout shape hides the history.
        """
        self._arm(repo)
        self._arm_merge_hook(repo)
        git(repo, "add", "-A")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-m", "land the hooks"],
            check=True,
            capture_output=True,
        )
        git(repo, "branch", "fix/cb-57-work")
        clone = tmp_path / "clone2"
        subprocess.run(
            ["git", "clone", "-q", "--single-branch", "--branch", "fix/cb-57-work",
             str(repo), str(clone)],
            check=True,
        )
        assert "main" not in git(clone, "branch", "--format=%(refname:short)").split()
        git(clone, "config", "merge.ff", "false")
        hooks = Path(git(clone, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
        for name in ("pre-commit", "pre-merge-commit"):
            link = hooks / name
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(clone / "tools" / f"{name}-hook.sh")
        assert run_guard("_guard_enforcement_armed", str(clone)).returncode == 0

        (clone / "tools" / "pre-merge-commit-hook.sh").unlink()
        result = run_guard("_guard_enforcement_armed", str(clone))
        assert result.returncode == 12, (
            "a clone with no local main let deleting the hook source disarm the check"
        )

    def test_deleting_the_merge_hook_source_does_not_disarm_the_check(self, repo: Path) -> None:
        """The disarm path adversarial review reproduced end to end.

        The first version gated on "does tools/pre-merge-commit-hook.sh exist".
        A single `rm` then did two things at once: dangled
        .git/hooks/pre-merge-commit (git skips a dangling hook silently) AND
        made this guard skip the check and return 0. Permanent, flagless, and
        landable on a perfectly typed branch — the exact silent-skip shape
        _guards.sh says three times that it was hardened against.

        The condition is now whether the PATH HAS HISTORY on main, which cannot
        be undone by deleting the file. Deletion becomes a reported problem
        ("cannot verify the hook identity"), not a vanishing check.
        """
        self._arm(repo)
        self._arm_merge_hook(repo)
        git(repo, "add", "-A")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-m", "land the merge hook"],
            check=True,
            capture_output=True,
        )
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0

        (repo / "tools" / "pre-merge-commit-hook.sh").unlink()
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12, "deleting the hook source silently disarmed the check"
        assert "pre-merge-commit" in result.stderr

    def test_impostor_merge_hook_refused(self, repo: Path) -> None:
        """A regular file at the path is not evidence it is THIS hook.

        The `#!/bin/sh; exit 0` impostor passes every existence and executable
        check while enforcing nothing — the same hole cross-model review found
        on the pre-commit side, which is why both go through one identity check.
        """
        self._arm(repo)
        canonical = repo / "tools" / "pre-merge-commit-hook.sh"
        shutil.copy(REPO_ROOT / "tools" / "pre-merge-commit-hook.sh", canonical)
        hook = self._hook_path(repo).with_name("pre-merge-commit")
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "pre-merge-commit" in result.stderr

    def test_both_hooks_broken_are_reported_separately(self, repo: Path) -> None:
        """Two faults must read as two lines, not one run-together blob.

        `$( )` strips trailing newlines, so accumulating two command
        substitutions without an explicit separator concatenates the last line
        of one onto the first line of the next — and an operator then sees one
        garbled fault instead of the two they have to fix.
        """
        git(repo, "config", "merge.ff", "false")
        tools = repo / "tools"
        tools.mkdir(exist_ok=True)
        for name in ("pre-commit-hook.sh", "pre-merge-commit-hook.sh"):
            shutil.copy(REPO_ROOT / "tools" / name, tools / name)
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        problems = [ln for ln in result.stderr.splitlines() if "is not installed" in ln]
        assert len(problems) == 2, f"expected two distinct problem lines, got: {problems}"

    def test_core_hookspath_redirect_is_detected(self, repo: Path) -> None:
        """The guard whose job is 'this clone is armed' reported ARMED for none.

        `git config core.hooksPath <empty-dir>` redirects where git looks for
        hooks. The guard resolved `--git-common-dir`/hooks, which does NOT follow
        the redirect, so it found the installed hook, returned 0, and a commit of
        arbitrary content on main then succeeded. Reproduced in adversarial
        review. `--git-path hooks` follows the redirect; verified that the
        common-dir form does not.
        """
        self._arm(repo)
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0
        elsewhere = repo / "elsewhere"
        elsewhere.mkdir()
        git(repo, "config", "core.hooksPath", str(elsewhere))
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12, "core.hooksPath redirect left the guard reporting armed"
        assert "pre-commit" in result.stderr

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


class TestPreMergeCommitHook:
    """CB-57 — the half `pre-commit` structurally cannot cover.

    git does not run pre-commit for a merge it completes itself, and the
    pre-commit hook deliberately exits 0 while MERGE_HEAD exists so a conflicted
    merge can be finished by hand. So `git merge <untyped-branch>` onto main was
    read by nothing: merge.ff=false gave it a merge COMMIT and no mechanism
    looked at the NAME. These tests replay the 2026-08-16 incident directly.
    """

    @staticmethod
    def _install(repo: Path) -> None:
        hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / "tools" / "pre-merge-commit-hook.sh", hooks / "pre-merge-commit")
        (hooks / "pre-merge-commit").chmod(0o755)

    @staticmethod
    def _branch_with_work(repo: Path, name: str, content: str = "work\n") -> None:
        git(repo, "checkout", "-q", "-b", name)
        (repo / "work.txt").write_text(content)
        git(repo, "add", "work.txt")
        git(repo, "commit", "-q", "-m", f"work on {name}")
        git(repo, "checkout", "-q", "main")

    @staticmethod
    def _merge(repo: Path, ref: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), "merge", ref, "--no-ff", "--no-edit"],
            capture_output=True,
            text=True,
        )

    def test_untyped_branch_merged_onto_main_refused(self, repo: Path) -> None:
        """The exact 2026-08-16 incident, by its real branch name."""
        self._install(repo)
        self._branch_with_work(repo, "worktree-cb-45-similarity-seam")
        result = self._merge(repo, "worktree-cb-45-similarity-seam")
        assert result.returncode != 0
        assert "sanctioned type" in result.stderr
        # And nothing landed: main must not have moved.
        assert git(repo, "log", "--oneline", "-1", "--format=%s") == "seed"

    @pytest.mark.parametrize(
        "branch",
        ["fix/cb-57-merge-gate", "feature/x", "refactor/y-1", "docs/z.2"],
    )
    def test_typed_branch_merged_onto_main_allowed(self, repo: Path, branch: str) -> None:
        self._install(repo)
        self._branch_with_work(repo, branch)
        result = self._merge(repo, branch)
        assert result.returncode == 0, result.stderr

    def test_nested_slug_refused_like_the_other_two_predicates(self, repo: Path) -> None:
        """`fix/a/b` is refused by _guard_branch_type; the hook must agree.

        Three copies of the branch predicate exist. If this one used a prefix
        test instead of the full shape, a branch could clear the finish guard
        and then be refused here — after the whole suite had run.
        """
        self._install(repo)
        self._branch_with_work(repo, "fix/a/b")
        assert self._merge(repo, "fix/a/b").returncode != 0

    def test_merge_into_a_worktree_branch_is_untouched(self, repo: Path) -> None:
        """worktree-finish.sh forward-merges main INTO the worktree.

        That merge lands on a typed branch, not on main, and must always pass —
        including when main itself is what is being merged in. Scoping the hook
        to HEAD==main is what leaves it alone.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/cb-57-work")
        (repo / "work.txt").write_text("branch\n")
        git(repo, "add", "work.txt")
        git(repo, "commit", "-q", "-m", "branch work")
        git(repo, "checkout", "-q", "main")
        (repo / "other.txt").write_text("main\n")
        git(repo, "add", "other.txt")
        git(repo, "commit", "-q", "-m", "main work")
        git(repo, "checkout", "-q", "fix/cb-57-work")
        assert self._merge(repo, "main").returncode == 0

    @staticmethod
    def _with_upstream(repo: Path, tmp_path: Path) -> None:
        """Give `repo` a real origin with main ahead of it, ready to pull."""
        bare = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True
        )
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "push", "-q", "-u", "origin", "main")
        clone = tmp_path / "other"
        subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
        git(clone, "config", "user.email", "t@t")
        git(clone, "config", "user.name", "t")
        (clone / "upstream.txt").write_text("upstream\n")
        git(clone, "add", "upstream.txt")
        git(clone, "commit", "-q", "-m", "upstream work")
        git(clone, "push", "-q", "origin", "main")

    def test_a_real_git_pull_is_allowed(self, repo: Path, tmp_path: Path) -> None:
        """The FALSE REFUSAL adversarial review found, now pinned both ways.

        The previous test here ran `git merge origin/main` and claimed in its
        docstring to cover `git pull`. It does not: that sets
        GITHEAD_<sha>=origin/main and takes an entirely different branch of the
        hook. A real pull hands merge the raw OID from FETCH_HEAD, so
        GITHEAD_<sha>=<sha>, `rev-parse --symbolic-full-name` resolves nothing,
        and the first version of this hook refused every pull — while its own
        comment and CLAUDE.md both promised pulls were allowed. Measured on git
        2.53, not assumed.
        """
        self._install(repo)
        self._with_upstream(repo, tmp_path)
        (repo / "local.txt").write_text("local\n")
        git(repo, "add", "local.txt")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-q", "-m", "local"], check=True
        )
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "pull.rebase=false", "pull", "--no-edit"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"git pull refused on main: {result.stderr}"

    def test_merge_of_a_remote_tracking_main_allowed(self, repo: Path, tmp_path: Path) -> None:
        """The named-ref twin of the pull above: refs/remotes/origin/main.

        NOTE this test passes against the round-1 hook too — it pins behaviour
        the fixes deliberately PRESERVED, not behaviour they introduced. Said
        explicitly because CLAUDE.md's testing rule requires it: otherwise a
        reader cannot tell it from a vacuous test.
        """
        self._install(repo)
        self._with_upstream(repo, tmp_path)
        git(repo, "fetch", "-q", "origin")
        assert self._merge(repo, "origin/main").returncode == 0

    def test_pull_allowed_when_upstream_has_another_branch_at_that_commit(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """"All refs must qualify" was too strict and refused real pulls.

        If upstream happens to have cut `release-1.0` at the commit you are
        pulling, that ref joins the candidate set and — under the first fix —
        disqualified the whole pull, because it carries no sanctioned type. But
        upstream's branch names are not this repo's to govern. A non-`main`
        remote ref now neither qualifies nor disqualifies.
        """
        self._install(repo)
        self._with_upstream(repo, tmp_path)
        subprocess.run(
            ["git", "-C", str(tmp_path / "other"), "branch", "release-1.0"], check=True
        )
        subprocess.run(
            ["git", "-C", str(tmp_path / "other"), "push", "-q", "origin", "release-1.0"],
            check=True,
        )
        (repo / "local.txt").write_text("local\n")
        git(repo, "add", "local.txt")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-q", "-m", "local"], check=True
        )
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "pull.rebase=false", "pull", "--no-edit"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"pull refused over an upstream sibling: {result.stderr}"

    def test_pull_allowed_with_a_local_bookmark_at_the_fetched_commit(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """A remote `main` must WIN over a non-qualifying local branch.

        Otherwise a stray local bookmark left at the commit being pulled refuses
        the pull — the same shape as the laundering bypass but the opposite
        intent, which is why "is there a remote main here" is the discriminator
        rather than "are all local branches clean".
        """
        self._install(repo)
        self._with_upstream(repo, tmp_path)
        git(repo, "fetch", "-q", "origin")
        git(repo, "branch", "bookmark", "origin/main")
        (repo / "local.txt").write_text("local\n")
        git(repo, "add", "local.txt")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-q", "-m", "local"], check=True
        )
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "pull.rebase=false", "pull", "--no-edit"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"pull refused over a local bookmark: {result.stderr}"

    def test_unconfigured_remote_namespace_is_not_stripped_to_main(
        self, repo: Path
    ) -> None:
        """`refs/remotes/junk/main` must not collapse to the accepted `main`.

        A blind `${rest#*/}` strips whatever sits before the first slash, so any
        ref anyone can write by hand launders arbitrary content onto main.
        Reproduced in adversarial review. Only a CONFIGURED remote's name is
        stripped now.
        """
        self._install(repo)
        self._branch_with_work(repo, "worktree-untyped")
        git(repo, "update-ref", "refs/remotes/junk/main", "worktree-untyped")
        result = self._merge(repo, "junk/main")
        assert result.returncode != 0, "an invented remote namespace laundered an untyped branch"

    def test_unattributable_head_refused(self, repo: Path) -> None:
        """FAIL CLOSED: a bare SHA merged onto main has no provenance.

        "I could not tell" must never read as "allowed" — that is the
        silent-skip shape _guards.sh was hardened against three times.
        """
        self._install(repo)
        self._branch_with_work(repo, "fix/cb-57-temp")
        sha = git(repo, "rev-parse", "fix/cb-57-temp")
        git(repo, "branch", "-D", "fix/cb-57-temp")
        result = self._merge(repo, sha)
        assert result.returncode != 0
        assert "resolves to no branch at all" in result.stderr

    def test_premise_merge_head_is_absent_on_a_clean_merge(self, repo: Path) -> None:
        """PIN THE PREMISE. CB-57's proposed mechanism does not exist.

        The card said to validate "the branch behind MERGE_HEAD". On git 2.53 a
        CLEAN merge is resolved in memory and $GIT_DIR/MERGE_HEAD is NEVER
        WRITTEN — so a hook keyed on that file exits 0 on every clean merge, a
        gate that cannot fire. This asserts the absence directly, so that if a
        future git starts writing it, this test goes red and someone re-reads
        the hook's reasoning instead of discovering it the hard way.
        """
        hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        probe = hooks / "pre-merge-commit"
        probe.write_text(
            '#!/bin/sh\n[ -e "$(git rev-parse --git-dir)/MERGE_HEAD" ] '
            '&& echo PRESENT >&2 || echo ABSENT >&2\nexit 1\n'
        )
        probe.chmod(0o755)
        self._branch_with_work(repo, "fix/cb-57-probe")
        assert "ABSENT" in self._merge(repo, "fix/cb-57-probe").stderr

    def test_premise_githead_env_names_the_merged_ref(self, repo: Path) -> None:
        """PIN THE PREMISE. GITHEAD_<sha> is the hook's only honest input.

        It is git's own record of what is being merged — the same thing the
        merge strategies read — and this hook is built on it because the
        alternative (parsing the commit MESSAGE) is the name-matching heuristic
        CB-57 refused. If a git upgrade stops exporting it the hook fails
        CLOSED, which would wedge every merge; this test turns red first.
        """
        hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        probe = hooks / "pre-merge-commit"
        probe.write_text('#!/bin/sh\nenv | grep "^GITHEAD_" >&2\nexit 1\n')
        probe.chmod(0o755)
        self._branch_with_work(repo, "fix/cb-57-probe")
        sha = git(repo, "rev-parse", "fix/cb-57-probe")
        stderr = self._merge(repo, "fix/cb-57-probe").stderr
        assert f"GITHEAD_{sha}=fix/cb-57-probe" in stderr

    def test_a_typed_alias_at_the_same_commit_cannot_launder_the_refusal(
        self, repo: Path
    ) -> None:
        """The three-command bypass adversarial review reproduced.

        When this hook refuses, git does NOT abort — it leaves MERGE_HEAD
        written and says "use 'git commit' to complete the merge", which routes
        the operator straight into pre-commit. While pre-commit accepted a head
        if ANY ref at that SHA was typed, the whole gate came apart:

            git merge untyped --no-ff     # refused here, merge left in progress
            git branch fix/tmp untyped    # a typed ref at the same commit
            git commit --no-edit          # -> landed, no --no-verify typed

        The shared predicate now requires EVERY ref at an unnamed head to
        qualify, so the untyped branch still disqualifies it.
        """
        self._install(repo)
        hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        shutil.copy(REPO_ROOT / "tools" / "pre-commit-hook.sh", hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)

        git(repo, "checkout", "-q", "-b", "worktree-untyped")
        (repo / "work.txt").write_text("work\n")
        git(repo, "add", "work.txt")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-q", "-m", "work"], check=True
        )
        git(repo, "checkout", "-q", "main")

        assert self._merge(repo, "worktree-untyped").returncode != 0
        git(repo, "branch", "fix/tmp", "worktree-untyped")
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-edit"], capture_output=True, text=True
        )
        assert result.returncode != 0, (
            "a typed branch created at the same commit laundered an untyped merge"
        )
        assert git(repo, "log", "-1", "--format=%s") == "seed", "the merge landed anyway"

    def test_no_verify_is_the_documented_escape(self, repo: Path) -> None:
        """Same contract as pre-commit: the hook stops the accident.

        An operator typing --no-verify has stated an intent, and the flag is
        the record of it. Pinned so nobody 'hardens' the hook into something
        that cannot be overridden in an emergency.
        """
        self._install(repo)
        self._branch_with_work(repo, "worktree-cb-45-similarity-seam")
        result = subprocess.run(
            [
                "git", "-C", str(repo), "merge", "worktree-cb-45-similarity-seam",
                "--no-ff", "--no-edit", "--no-verify",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


class TestKnownLimits:
    """States what the local gate CANNOT do, and pins that it still cannot.

    Same shape as `TestSeparateGitDirMisbinding` in tests/test_db_infra.py: when
    a rule cannot be decided from local evidence, this repo documents the limit
    and pins the reproduction rather than deepening the guess. If one of these
    starts passing, the comment that explains it has gone stale and someone
    should re-read the design instead of celebrating.
    """

    def test_a_hand_written_remote_main_is_trusted(self, repo: Path, tmp_path: Path) -> None:
        """A remote-tracking `main` is trusted, and nothing local can verify it.

        `git update-ref refs/remotes/origin/main <any-sha>` then `git merge
        origin/main` lands anything on main in two commands, with no
        `--no-verify`. Reproduced in adversarial review against both a junk
        remote and the real origin URL.

        This is NOT fixable here: the hook cannot tell a fetched ref from a
        hand-written one without contacting the remote, and the alternative —
        refusing remote refs — breaks `git pull`, which is the worse failure. The
        remedy is CB-59's server-side protection.
        """
        # `"--git-path hooks"` as ONE argv token is what made the first version of
        # this test VACUOUS: `git rev-parse` echoes an unrecognised option-looking
        # argument back verbatim and exits 0, so this resolved to the RELATIVE
        # path "--git-path hooks", the hook was copied into a directory of that
        # name in the repo root, the test repo got no hook at all, and asserting
        # rc == 0 could never fail. Worse, the suite then COMMITTED that directory
        # to the branch — and `git status` stayed clean because every run
        # regenerated it identically. Two tokens, and resolve against the repo.
        hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / "tools" / "pre-merge-commit-hook.sh", hooks / "pre-merge-commit")
        (hooks / "pre-merge-commit").chmod(0o755)
        assert (hooks / "pre-merge-commit").exists(), "the hook was not installed in the test repo"
        git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))

        git(repo, "checkout", "-q", "-b", "worktree-untyped")
        (repo / "work.txt").write_text("work\n")
        git(repo, "add", "work.txt")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-q", "-m", "work"], check=True
        )
        git(repo, "checkout", "-q", "main")
        git(repo, "update-ref", "refs/remotes/origin/main", "worktree-untyped")

        result = subprocess.run(
            ["git", "-C", str(repo), "merge", "origin/main", "--no-ff", "--no-edit"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "the hand-written-remote-main limit no longer reproduces — re-read "
            "the KNOWN LIMIT comment in the shared merge-gate block"
        )


class TestUntrackedScratchAtRoot:
    def test_clean_status_passes(self) -> None:
        assert run_guard("_guard_untracked_scratch_at_root", " M src/codebugs/db.py").returncode == 0

    def test_untracked_root_py_refused(self) -> None:
        assert run_guard("_guard_untracked_scratch_at_root", "?? scratch.py").returncode == 4

    def test_untracked_py_in_subdir_passes(self) -> None:
        """Only TOP-LEVEL untracked .py files are suspect.

        A new module under src/ or a new test is the normal way this repo
        grows; refusing those would fire on nearly every feature branch.
        """
        assert run_guard("_guard_untracked_scratch_at_root", "?? src/codebugs/new.py").returncode == 0
        assert run_guard("_guard_untracked_scratch_at_root", "?? tests/test_new.py").returncode == 0

    def test_the_tempfile_signature_that_actually_reached_main_is_refused(self) -> None:
        """CB-83, from the incident and not from imagination.

        A zero-byte `tmpy_efkp4t` (tempfile.mkstemp's DEFAULT prefix, `tmp` +
        random) was swept up by `git add -A`, committed, and merged onto main.
        The guard existed, was named for this exact failure class, and matched
        only `*.py`.
        """
        assert run_guard("_guard_untracked_scratch_at_root", "?? tmpy_efkp4t").returncode == 4
        assert run_guard(
            "_guard_untracked_scratch_at_root", "?? .codebugs-export-ab12cd34"
        ).returncode == 4

    def test_ordinary_extensionless_root_files_are_NOT_refused(self) -> None:
        """The false-refusal boundary, and the reason this was not widened to
        "any extensionless file at root".

        main already tracks LICENSE; Makefile and Dockerfile are ordinary
        additions. Refusing those would be the false refusal this repo keeps
        recording as the worse failure — so the widening matches only the two
        machine-generated temp signatures, never authored names.
        """
        for name in ("LICENSE", "Makefile", "Dockerfile", "README", "tmpfile"):
            assert run_guard("_guard_untracked_scratch_at_root", f"?? {name}").returncode == 0, name

    def test_a_temp_named_file_in_a_subdirectory_passes(self) -> None:
        """Same scoping rule as the .py half: only TOP-LEVEL is suspect."""
        assert run_guard("_guard_untracked_scratch_at_root", "?? tests/tmpy_efkp4t").returncode == 0


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
            "tools/pre-merge-commit-hook.sh",
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
            "tools/pre-merge-commit-hook.sh",
        ],
    )
    def test_script_is_executable(self, script: str) -> None:
        assert (REPO_ROOT / script).stat().st_mode & 0o111, f"{script} is not executable"

    def test_branch_types_agree_across_the_harness(self) -> None:
        """`_BRANCH_TYPES` is declared three times and the copies must not diverge.

        Neither hook can source the library — each runs from `.git/hooks/` as a
        symlink and must work even if `tools/` is missing from the checked-out
        tree (a `git checkout` of an older commit). So each carries its own
        copy, and this test is what keeps them honest. Same shape as the repo's
        other duplicated-check rule: a check that is duplicated rather than
        shared is one drift away from disagreeing with itself.
        """
        decl = "_BRANCH_TYPES=(fix feature refactor docs)"
        for rel in (
            "tools/_guards.sh",
            "tools/pre-commit-hook.sh",
            "tools/pre-merge-commit-hook.sh",
        ):
            assert decl in (REPO_ROOT / rel).read_text(), f"{rel} drifted from the others"

    @staticmethod
    def _shared_gate_block(rel: str) -> str:
        """The merge-gate predicate as it appears in one hook, verbatim."""
        text = (REPO_ROOT / rel).read_text()
        start = text.index("# ---8<--- SHARED MERGE-GATE PREDICATE")
        end = text.index("# ---8<--- END SHARED MERGE-GATE PREDICATE")
        return text[start:end]

    def test_the_shared_merge_gate_block_is_byte_identical_in_both_hooks(self) -> None:
        """The two hooks must not merely 'agree' — they must be the same text.

        This replaces a weaker test that grepped each file for the substring
        `/[A-Za-z0-9._-]+$`. Adversarial review showed that was insufficient by
        rewriting the merge hook's check as a prefix test while leaving the
        regex ASSIGNMENT in place: the grep still found the substring and the
        test stayed green.

        It matters because the two hooks cover disjoint halves of one rule and
        git routes an operator from one to the other — when pre-merge-commit
        refuses, the merge is left in progress and `git commit` goes to
        pre-commit. Review reproduced a three-command bypass off exactly that
        seam while the predicates differed.
        """
        a = self._shared_gate_block("tools/pre-merge-commit-hook.sh")
        b = self._shared_gate_block("tools/pre-commit-hook.sh")
        assert a == b, "the shared merge-gate predicate has drifted between the two hooks"
        assert "_head_is_acceptable()" in a, "the block no longer defines the predicate"

    def test_every_branch_predicate_construction_uses_the_full_shape(self) -> None:
        """Same types is NOT the same predicate, and the difference has teeth.

        A prefix test (`${branch} == fix/*`) accepts `fix/a/b`, which the
        full-shape regex refuses.

        COUNTED PER SITE, not per file. The earlier version asserted the shape
        appeared *somewhere* in each file, and review showed that was blind:
        `pre-commit-hook.sh` constructs the regex TWICE (its own branch check, and
        the copy inside the shared block), so degrading the first to a prefix test
        left this test green. Note also that the count is four across three files
        — CLAUDE.md used to say "three copies", which was the file count, not the
        construction count.
        """
        shape = "/[A-Za-z0-9._-]+$"
        expected = {
            "tools/_guards.sh": 1,
            "tools/pre-commit-hook.sh": 2,
            "tools/pre-merge-commit-hook.sh": 1,
        }
        for rel, n in expected.items():
            text = (REPO_ROOT / rel).read_text()
            # Count only real assignments, not the prose that explains them.
            sites = [
                ln
                for ln in text.splitlines()
                if shape in ln and not ln.lstrip().startswith("#")
            ]
            assert len(sites) == n, (
                f"{rel}: expected {n} full-shape construction(s), found {len(sites)}: {sites}"
            )

    def test_installer_and_guard_agree_on_where_hooks_live(self) -> None:
        """Both must use `--git-path hooks`, or one arms where git will not look.

        Round 3's vacuity sweep found the installer's half of this unpinned: only
        the guard's switch was covered by a test, so the installer could regress to
        `--git-common-dir`/hooks and silently install into a directory git ignores
        when `core.hooksPath` is set.
        """
        for rel in ("tools/install-hooks.sh", "tools/_guards.sh"):
            text = (REPO_ROOT / rel).read_text()
            sites = [
                ln
                for ln in text.splitlines()
                if "hooks" in ln and "rev-parse" in ln and not ln.lstrip().startswith("#")
            ]
            assert sites, f"{rel} does not resolve a hooks directory"
            for ln in sites:
                assert "--git-path" in ln, (
                    f"{rel} resolves hooks without --git-path, so core.hooksPath is ignored: {ln}"
                )

    def test_both_readers_disable_path_quoting(self) -> None:
        """The CI half of the C-quoting fix was unpinned while the hook's was not.

        `test_ci_and_hook_both_defeat_rename_detection` covers `--no-renames` in
        both readers, but `core.quotePath=false` was asserted only for the hook —
        the elements-vs-composition asymmetry inside a test whose own name says
        "both". Round 3 caught it.
        """
        for rel in (
            ".github/workflows/main-invariants.yml",
            "tools/pre-commit-hook.sh",
        ):
            # Comment lines excluded, or the prose that EXPLAINS the flag keeps
            # the test green after the flag itself is deleted. Verified: removing
            # it from the workflow's `git show` while leaving the comment left the
            # earlier version of this test passing. Its siblings in this class
            # already filtered comments; this one did not.
            code = [
                ln
                for ln in (REPO_ROOT / rel).read_text().splitlines()
                if not ln.lstrip().startswith("#")
            ]
            assert any("core.quotePath=false" in ln for ln in code), (
                f"{rel} will C-quote non-ASCII paths and misread a legitimate plan note"
            )

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

    def test_finish_does_not_skip_its_own_merge_hook(self) -> None:
        """CB-57: the harness must not be the one caller exempt from the gate.

        `--no-verify` on the integration merge was harmless while no
        pre-merge-commit hook existed. The moment one does, it makes the branch
        -name check apply to every merge EXCEPT the one this repo actually uses
        to land work — a gate with a hole exactly the shape of its main caller.

        Asserted against the SOURCE, not behaviour: a behavioural test would
        need the hook installed in the developer's own clone to discriminate,
        and an unarmed clone would pass while the flag sat right there.
        """
        finish = (REPO_ROOT / "tools" / "worktree-finish.sh").read_text()
        merges = [
            line
            for line in finish.splitlines()
            if 'merge "${BRANCH}"' in line and line.strip().startswith(("if git", "git"))
        ]
        assert merges, "could not find the integration merge in worktree-finish.sh"
        for line in merges:
            assert "--no-verify" not in line, (
                f"integration merge skips the pre-merge-commit hook: {line.strip()}"
            )

    def test_installer_arms_the_merge_hook_too(self) -> None:
        """An installed pre-commit hook is not evidence the clone is armed.

        Both hooks are needed and neither covers the other: git runs
        pre-commit for authored commits and pre-merge-commit for merges. An
        installer that armed only the first would leave every clone in exactly
        the CB-57 state while printing '=== armed ==='.
        """
        src = (REPO_ROOT / "tools" / "install-hooks.sh").read_text()
        assert 'MERGE_HOOK_SRC="${REPO_ROOT}/tools/pre-merge-commit-hook.sh"' in src
        assert 'ln -sfn "${MERGE_HOOK_SRC}" "${HOOK_DIR}/pre-merge-commit"' in src
        assert '"${_SCRIPT_DIR}/pre-merge-commit-hook.sh"' not in src, (
            "installer points the merge hook at the invoking checkout; it will dangle"
        )

    def test_ci_workflow_asserts_the_first_parent_invariant(self) -> None:
        """CB-59's server-side half must exist and must carry a real baseline.

        The check is only as good as its baseline: a baseline moved forward to
        the current tip would silently launder every violation before it. This
        pins that the workflow exists, runs the documented assertion, and that
        the baseline is a real commit in this repository.
        """
        wf = REPO_ROOT / ".github" / "workflows" / "main-invariants.yml"
        assert wf.exists(), "CB-59's CI workflow is missing"
        text = wf.read_text()
        assert "--first-parent --no-merges" in text
        assert r"^\.claude/plans/[^/]+\.md$" in text

        match = re.search(r"BASELINE:\s*([0-9a-f]{40})", text)
        assert match, "workflow declares no 40-char baseline SHA"
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{match.group(1)}^{{commit}}"],
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"workflow baseline {match.group(1)} is not a commit in this repo"
        )

    def test_invariants_job_is_not_subscribed_to_pull_request(self) -> None:
        """A skipped job is reported as PASSING for required status checks.

        The first draft ran this job `on: pull_request` and guarded it with
        `if: github.event_name != 'pull_request'`. Marking it required would
        then have produced a check that can never fail on the only path where
        branch protection evaluates it — the exact 'gate that cannot fire'
        failure this change exists to remove, reintroduced inside its own fix.
        Both reviewers flagged it independently.

        The repair is structural: do not subscribe to the event at all, so the
        job cannot be chosen as a vacuous required check. The PR-time required
        check is ci.yml's `tests` job, which has no `if:`.
        """
        def code(path: Path) -> str:
            """The file with comment lines stripped.

            Comments must not count: these workflows explain the defect at
            length and name the event a dozen times, so a naive substring test
            fails on its own documentation.
            """
            return "\n".join(
                ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")
            )

        def triggers(path: Path) -> str:
            return code(path).split("jobs:", 1)[0]

        wf = REPO_ROOT / ".github" / "workflows" / "main-invariants.yml"
        assert "pull_request" not in triggers(wf), (
            "main-invariants subscribes to pull_request; a skipped job passes a required check"
        )
        assert "if: github.event_name" not in code(wf), (
            "the job is guarded by an event `if:` again — that is the vacuous-check shape"
        )

        # The POSITIVE half, which review found missing: asserting only "not
        # subscribed to pull_request" let the whole trigger be deleted with the
        # suite still green — "gate that cannot fire" returning as "workflow that
        # never fires". Verified: removing `push: branches: [main]` left 126
        # tests passing.
        assert "push" in triggers(wf) and "main" in triggers(wf), (
            "main-invariants no longer runs on pushes to main — it fires never"
        )

        ci = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert ci.exists(), "the PR-time check (ci.yml) is missing"
        assert "pull_request" in triggers(ci), (
            "ci.yml must run on pull_request — it is the check meant to be required"
        )

    def test_ci_job_proves_it_examined_every_commit(self) -> None:
        """A scan that examines nothing must not report clean.

        The loop used to be `for sha in ${commits}`, which depends on IFS
        word-splitting: bash splits, zsh does not. Running this very assertion by
        hand under zsh made the body execute ONCE with every SHA concatenated,
        `git show` fail, and the script print "clean" having examined nothing.
        Actions runs bash so the job was correct in situ — but a gate whose
        correctness depends on which shell invokes it is one `shell:` key away
        from vacuous, which is the defect class this change exists to remove.

        Two properties are pinned: the loop no longer relies on word-splitting,
        and the job compares what it examined against what it should have.
        """
        wf = (REPO_ROOT / ".github" / "workflows" / "main-invariants.yml").read_text()
        code = "\n".join(
            ln for ln in wf.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "for sha in ${commits}" not in code, (
            "the scan loop depends on IFS word-splitting again"
        )
        assert 'examined=$((examined + 1))' in code, "the job does not count what it examined"
        assert '"${examined}" -ne "${expected}"' in code, (
            "the job does not compare examined against expected — a partial scan reports clean"
        )

    def test_ci_and_hook_both_defeat_rename_detection(self) -> None:
        """`--name-only` prints only the DESTINATION path for a rename.

        So `git mv src/keep.py .claude/plans/keep.md` presents one allowlisted
        path, passes both the hook and the CI job, and quietly deletes source
        from main. Reproduced in adversarial review against both readers.
        `--no-renames` makes git report the delete and the add separately.
        """
        wf = (REPO_ROOT / ".github" / "workflows" / "main-invariants.yml").read_text()
        assert "--no-renames" in wf, "CI job still collapses renames to the destination path"

        # Assert the FLAGS on the staged-diff line, not one exact command string:
        # a literal match breaks the moment an unrelated flag is added (it did,
        # when core.quotePath=false landed) without the property being lost.
        hook_lines = (REPO_ROOT / "tools" / "pre-commit-hook.sh").read_text().splitlines()
        staged = [ln for ln in hook_lines if "diff --cached" in ln and "staged=" in ln]
        assert len(staged) == 1, f"expected one staged-diff line, found {staged}"
        assert "--no-renames" in staged[0], (
            "pre-commit still collapses renames to the destination path"
        )
        assert "core.quotePath=false" in staged[0], (
            "pre-commit will C-quote non-ASCII paths and refuse a legitimate plan note"
        )

    def test_installer_sets_merge_ff_before_anything_that_can_fail(self) -> None:
        """merge.ff=false is the one mechanism no hook can replace.

        With it last, a clone missing tools/pre-merge-commit-hook.sh (an older
        main, a `git checkout <old-commit>`, the CB-57 bootstrap window) armed
        pre-commit, printed its tick, then exited 1 at the merge-hook step —
        leaving merge.ff UNSET. The installer could skip the irreplaceable step.
        Reproduced in adversarial review. A step that cannot fail goes first.
        """
        src = (REPO_ROOT / "tools" / "install-hooks.sh").read_text()
        ff = src.index("config merge.ff false")
        first_exit = src.index("exit 1")
        assert ff < first_exit, (
            "install-hooks.sh can exit before setting merge.ff=false"
        )


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
            "_guard_untracked_scratch_at_root",
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

    def test_conflicted_merge_from_an_untyped_branch_refused(self, repo: Path) -> None:
        """CB-57's other half — the one route pre-merge-commit never sees.

        git stops a conflicted merge and the operator finishes it with `git
        commit`, which fires pre-commit and NOT pre-merge-commit. So if the
        merge-in-progress exemption above were unconditional, the branch-name
        rule would hold for every merge onto main EXCEPT the one that had a
        conflict — enforcement lapsing exactly when the operator is already
        distracted. The pair with the test above is the discriminator: same
        flow, same conflict, only the branch NAME differs.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "worktree-cb-45-similarity-seam")
        (repo / "seed.txt").write_text("branch side\n")
        # --no-verify: the hook would refuse this commit for the branch NAME,
        # and then there would be no divergence and so no conflict to reach the
        # path under test. This mirrors how the real branch got created in
        # 2026-08-16 — before the pre-commit hook existed at all.
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-am", "branch side"], check=True
        )
        git(repo, "checkout", "-q", "main")
        (repo / "seed.txt").write_text("main side\n")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-am", "main side"], check=True
        )
        subprocess.run(
            [
                "git", "-C", str(repo), "merge", "worktree-cb-45-similarity-seam",
                "--no-ff", "--no-edit",
            ],
            capture_output=True,
        )
        (repo / "seed.txt").write_text("resolved\n")
        subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-edit"], capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "untyped branch" in result.stderr

    def test_empty_merge_head_is_refused_not_waved_through(self, repo: Path) -> None:
        """The accidental bypass: MERGE_HEAD exists but the loop reads nothing.

        `while read` over an EMPTY MERGE_HEAD ran zero times, left the refusal
        flag at 0, and the merge-in-progress exemption below then let arbitrary
        staged content land on main with no merge involved at all — and an
        interrupted git can leave an empty MERGE_HEAD behind, so this is
        reachable without anyone attacking anything.
        """
        self._install(repo)
        (repo / ".git" / "MERGE_HEAD").write_text("")
        (repo / "evil.py").write_text("x = 1\n")
        result = self._commit(repo, "evil.py")
        assert result.returncode != 0, "empty MERGE_HEAD waved a source commit onto main"
        assert "names no merge head" in result.stderr

    def test_unterminated_merge_head_is_not_silently_dropped(self, repo: Path) -> None:
        """`read` returns non-zero on an unterminated last line.

        So a MERGE_HEAD written without a trailing newline made the loop skip
        its only entry, and an untyped branch landed as a real two-parent merge
        commit. Reproduced in adversarial review; the `|| [[ -n "$_sha" ]]`
        idiom is what keeps the last line.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "worktree-untyped")
        (repo / "work.txt").write_text("work\n")
        git(repo, "add", "work.txt")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-q", "-m", "work"], check=True
        )
        sha = git(repo, "rev-parse", "worktree-untyped")
        git(repo, "checkout", "-q", "main")
        subprocess.run(
            ["git", "-C", str(repo), "merge", "--no-commit", "--no-edit", "worktree-untyped"],
            capture_output=True,
        )
        # No trailing newline — the shape that defeated the loop.
        (repo / ".git" / "MERGE_HEAD").write_text(sha)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-edit", "-m", "m"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "an unterminated MERGE_HEAD line was dropped"

    @pytest.mark.parametrize("marker", ["CHERRY_PICK_HEAD", "REVERT_HEAD"])
    @pytest.mark.parametrize("body", ["", "deadbeef\n"])
    def test_cherry_pick_and_revert_markers_do_not_exempt_main(
        self, repo: Path, marker: str, body: str
    ) -> None:
        """The MERGE_HEAD hardening left its two siblings untouched.

        The exemption used to exit 0 on mere EXISTENCE of any of the three
        markers, so `: > .git/CHERRY_PICK_HEAD` waved arbitrary staged content
        onto main — and skipped the branch-type check too. Reachable the same way
        empty MERGE_HEAD was: a conflicted cherry-pick leaves the file in place
        until --continue/--abort.

        Completing a MERGE onto main is the sanctioned landing path; cherry-pick
        and revert directly onto main are 'editing main directly', so they get no
        exemption at all now.
        """
        self._install(repo)
        (repo / ".git" / marker).write_text(body)
        (repo / "backdoor.py").write_text("x = 1\n")
        result = self._commit(repo, "backdoor.py")
        assert result.returncode != 0, f"{marker} waved a source commit onto main"

    def test_empty_merge_head_does_not_exempt_a_branch_either(self, repo: Path) -> None:
        """The MERGE_HEAD twin of the test below, and it was missing.

        The fail-closed validation was scoped to `branch == main` while the
        exemption it guards fires on ANY branch, so `: > .git/MERGE_HEAD` on an
        untyped branch still skipped the branch-type check. Reproduced in review;
        reachable by an interrupted merge on a hand-made branch.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "another-untyped-thing")
        (repo / ".git" / "MERGE_HEAD").write_text("")
        (repo / "src2.py").write_text("x = 1\n")
        assert self._commit(repo, "src2.py").returncode != 0

    def test_cherry_pick_marker_does_not_exempt_the_branch_type_rule(
        self, repo: Path
    ) -> None:
        """The same empty marker also disabled the hook's OTHER rule.

        The exemption returned before the branch-type check, so a commit on an
        untyped branch succeeded while the marker sat there. Two rules off from
        one empty file.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "totally-untyped")
        (repo / ".git" / "CHERRY_PICK_HEAD").write_text("")
        (repo / "src.py").write_text("x = 1\n")
        assert self._commit(repo, "src.py").returncode != 0

    def test_a_conflicted_merge_is_still_exempt(self, repo: Path) -> None:
        """Discriminator for the change above: MERGE_HEAD must STILL exempt.

        Narrowing the exemption to MERGE_HEAD could have been over-narrowed into
        'no exemption at all', which would break the documented landing path
        exactly when there is a conflict. This is the case that must keep passing.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/cb-57-conflict")
        (repo / "seed.txt").write_text("branch side\n")
        self._commit(repo, "seed.txt")
        git(repo, "checkout", "-q", "main")
        (repo / "seed.txt").write_text("main side\n")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-am", "main side"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "merge", "fix/cb-57-conflict", "--no-ff", "--no-edit"],
            capture_output=True,
        )
        (repo / "seed.txt").write_text("resolved\n")
        subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-edit"], capture_output=True, text=True
        )
        assert result.returncode == 0, f"conflicted merge blocked on main: {result.stderr}"

    def test_non_ascii_plan_note_can_land_on_main(self, repo: Path) -> None:
        """A false REFUSAL, and the mirror of a bug this repo already had.

        `git diff --cached --name-only` C-quotes a non-ASCII path by default
        (".claude/plans/\\321\\202….md"), the allowlist regex misses it, and a
        perfectly legitimate plan note cannot be committed. The same C-quoting
        default once made _guard_conflict_markers silently ACCEPT a marker, so
        the failure mode flips depending on which side of the check it lands on.
        """
        self._install(repo)
        plans = repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "заметка.md").write_text("# note\n")
        result = self._commit(repo, ".claude/plans/заметка.md")
        assert result.returncode == 0, f"non-ASCII plan note refused: {result.stderr}"

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
