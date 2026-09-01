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

import fcntl
import functools
import os
import re
import shutil
import signal
import subprocess
import threading
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

    def test_the_refusal_points_at_the_likely_cross_session_cause(self, repo: Path) -> None:
        """The side that is blocked is usually not the side that made the mess.

        A commit refused by the pre-commit hook leaves its files in main's
        index, and this guard then refuses every OTHER worktree's finish with
        exit 11. Told only "main's working tree has uncommitted changes", that
        side reads the sentence as a statement about its own work and waits —
        which is how ~40 minutes of two blocked integrations were spent on
        2026-08-22 (CB-130). The symmetric half of the hook's new lines.
        """
        (repo / "seed.txt").write_text("changed\n")
        git(repo, "add", "seed.txt")
        result = run_guard("_guard_main_clean", str(repo))
        assert result.returncode == 11
        err = result.stderr
        assert "another session" in err.lower(), err
        assert f'git -C "{repo}" status --porcelain' in err, err

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

    # -- the commit-msg third (T-23) ---------------------------------------
    #
    # The fixture repo has NO history of tools/commit-msg-hook.sh, so a gate
    # keyed on history alone would never fire here and every test below would
    # be green by construction. The condition is a disjunction with `-e <src>`,
    # so each refusing test PUTS THE SOURCE IN PLACE (or lands it in history)
    # before asserting — otherwise it tests the bootstrap branch, not the gate.

    def _arm_commit_msg_hook(self, repo: Path) -> Path:
        """Add the third hook, the way install-hooks.sh step [4/4] does."""
        canonical = repo / "tools" / "commit-msg-hook.sh"
        shutil.copy(REPO_ROOT / "tools" / "commit-msg-hook.sh", canonical)
        canonical.chmod(0o755)
        hook = self._hook_path(repo).with_name("commit-msg")
        if hook.exists() or hook.is_symlink():
            hook.unlink()
        hook.symlink_to(canonical)
        return hook

    def test_all_three_hooks_armed_passes(self, repo: Path) -> None:
        """Pins behaviour the change PRESERVES: a fully armed clone is rc 0.

        Green on both sides of T-23 by design — it exists so the refusing tests
        below cannot be satisfied by a gate that refuses everything.
        """
        self._arm(repo)
        self._arm_merge_hook(repo)
        self._arm_commit_msg_hook(repo)
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0

    def test_commit_msg_hook_missing_is_refused_once_its_source_exists(self, repo: Path) -> None:
        """The case a clone armed before T-23 is actually in.

        pre-commit and pre-merge-commit installed, merge.ff set — and the
        naming gate is simply absent. CLAUDE.md used to record this as a
        deliberate gap ("a clone armed before it landed … silently lacks this
        one"); once the source is on main it is a missing third and must refuse.
        """
        self._arm(repo)
        self._arm_merge_hook(repo)
        canonical = repo / "tools" / "commit-msg-hook.sh"
        shutil.copy(REPO_ROOT / "tools" / "commit-msg-hook.sh", canonical)
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "commit-msg" in result.stderr

    def test_dangling_commit_msg_symlink_refused(self, repo: Path) -> None:
        """A dangling commit-msg link is reported as DANGLING, like the other two."""
        self._arm(repo)
        self._arm_merge_hook(repo)
        hook = self._arm_commit_msg_hook(repo)
        hook.unlink()
        hook.symlink_to(repo / "nonexistent" / "commit-msg-hook.sh")
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12
        assert "DANGLING" in result.stderr
        assert "commit-msg" in result.stderr

    def test_deleting_the_commit_msg_source_does_not_disarm_the_check(self, repo: Path) -> None:
        """The third hook gets the SAME monotonic condition as the second.

        Once the path has history, `rm tools/commit-msg-hook.sh` must report
        "cannot verify the hook identity" rather than make the check vanish —
        the round-2 disarm CB-57 closed for pre-merge-commit, applied here.
        """
        self._arm(repo)
        self._arm_merge_hook(repo)
        self._arm_commit_msg_hook(repo)
        git(repo, "add", "-A")
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-m", "land all three hooks"],
            check=True,
            capture_output=True,
        )
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0

        (repo / "tools" / "commit-msg-hook.sh").unlink()
        result = run_guard("_guard_enforcement_armed", str(repo))
        assert result.returncode == 12, "deleting the commit-msg source silently disarmed the check"
        assert "commit-msg" in result.stderr

    def test_hook_source_known_fails_closed_when_git_cannot_answer(self, tmp_path: Path) -> None:
        """The third door: a history probe that ERRORS must DEMAND the hook.

        `2>/dev/null || true` once made "git failed" identical to "no history",
        and review reproduced a full disarm through a `--filter=tree:0` clone
        whose promisor remote had gone away (`git log --all` exits 128). No
        test pinned that arm: a mutant deleting `-z "${log_ok}" ||` passed the
        whole suite (Opus review of T-23). A directory that is not a repository
        is the cheapest state in which `git log` fails, so it is used here.
        """
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        assert not (not_a_repo / "tools" / "commit-msg-hook.sh").exists()
        result = run_guard("_hook_source_known", str(not_a_repo), "tools/commit-msg-hook.sh")
        assert result.returncode == 0, "a failed history probe excused the hook instead of demanding it"

    def test_hook_source_known_is_false_with_no_history_and_no_file(self, repo: Path) -> None:
        """The bootstrap window, at the helper: fresh repo, no file → 1."""
        result = run_guard("_hook_source_known", str(repo), "tools/commit-msg-hook.sh")
        assert result.returncode == 1

    def test_commit_msg_hook_not_demanded_before_its_source_lands(self, repo: Path) -> None:
        """The bootstrap branch, kept: no history, no file → not demanded.

        Two hooks armed and no `tools/commit-msg-hook.sh` anywhere is rc 0, the
        same shape `test_merge_hook_not_demanded_before_its_source_lands` pins
        for the second hook. A repo that never had the source is not a repo
        that lost it.
        """
        self._arm(repo)
        self._arm_merge_hook(repo)
        assert not (repo / "tools" / "commit-msg-hook.sh").exists()
        assert run_guard("_guard_enforcement_armed", str(repo)).returncode == 0

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
            "tools/commit-msg-hook.sh",
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
            "tools/commit-msg-hook.sh",
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

    def test_every_staged_path_reader_disables_path_quoting(self) -> None:
        """The CI half of the C-quoting fix was unpinned while the hook's was not.

        `test_ci_and_hook_both_defeat_rename_detection` covers `--no-renames` in
        both readers, but `core.quotePath=false` was asserted only for the hook —
        the elements-vs-composition asymmetry inside a test whose own name says
        "both". Round 3 caught it.

        The name no longer says "both", because it is now three: the commit-msg
        naming gate reads the same staged set and derives a BASENAME from it, so
        a C-quoted path there produces a basename nobody can ever name and a
        permanent false refusal of every non-ASCII plan note. A count in a name
        is a count that goes stale — this repo has been wrong about "three
        copies" and "four sites" already.
        """
        for rel in (
            ".github/workflows/main-invariants.yml",
            "tools/pre-commit-hook.sh",
            "tools/commit-msg-hook.sh",
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

    def test_installer_arms_the_commit_msg_hook_too(self) -> None:
        """A third hook, and the installer is the only thing that INSTALLS it.

        `_guard_enforcement_armed` now demands it too (T-23), but the guard only
        refuses — it never installs. If the installer skipped this step every
        finish would be refused with no way to arm, which is the mirror image
        of the "described better than it behaves" failure this test was written
        for while the guard did not yet know about the hook.
        """
        src = (REPO_ROOT / "tools" / "install-hooks.sh").read_text()
        assert 'MSG_HOOK_SRC="${REPO_ROOT}/tools/commit-msg-hook.sh"' in src
        assert 'ln -sfn "${MSG_HOOK_SRC}" "${HOOK_DIR}/commit-msg"' in src
        assert '"${_SCRIPT_DIR}/commit-msg-hook.sh"' not in src, (
            "installer points the commit-msg hook at the invoking checkout; it will dangle"
        )

    def test_bootstrap_condition_is_one_function_called_per_gated_hook(self) -> None:
        """The monotonic "source is known" condition is shared, not copied.

        Two hooks are gated on it (pre-merge-commit and commit-msg); pre-commit
        is demanded unconditionally. A copy of the condition per hook is one
        drift from two different rules — the `entities._SAFE_IDENT` vs
        `types._IDENT` shape — so the guard must CALL one helper, once per
        gated hook. Counted per call site, the way the branch predicate is.
        """
        src = GUARDS.read_text()
        body = src.split("_guard_enforcement_armed() {", 1)[1].split("\n}\n", 1)[0]
        # Executable lines only — a commented-out call must not count as a site.
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        # Tolerate `"${repo_root}"` / `"$repo_root"` and a quoted or bare second
        # argument; what is pinned is WHICH sources are gated and in what order.
        calls = re.findall(
            r'_hook_source_known[\s\\]+"?\$\{?repo_root\}?"?[\s\\]+"?([^\s;"]+)"?', code
        )
        assert calls == ["tools/pre-merge-commit-hook.sh", "tools/commit-msg-hook.sh"], calls
        assert "tools/pre-commit-hook.sh" not in calls, "pre-commit must not be bootstrap-gated"
        assert src.count("_hook_source_known() {") == 1
        # The condition must not ALSO be re-spelled inline beside the calls: the
        # history probe belongs to the helper and nowhere else in the guard.
        assert "log -1 --format=%H --all" not in code, "monotonic condition duplicated inline"

    def test_the_hooks_do_not_depend_on_the_tools_directory(self) -> None:
        """Each hook runs from `.git/hooks/` and must work when `tools/` is not
        in the checked-out tree — a `git checkout` of an older commit is enough.
        Sourcing `_guards.sh` would make every hook fail open on that tree,
        because git skips a hook that cannot run... after it has already exited
        non-zero, which is worse: it refuses everything instead.
        """
        for rel in (
            "tools/pre-commit-hook.sh",
            "tools/pre-merge-commit-hook.sh",
            "tools/commit-msg-hook.sh",
        ):
            code = [
                ln
                for ln in (REPO_ROOT / rel).read_text().splitlines()
                if not ln.lstrip().startswith("#")
            ]
            assert not any("_guards.sh" in ln for ln in code), (
                f"{rel} reaches for the guard library; it will break without tools/"
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
        # CB-266 widened the allowlist letter (was `.claude/plans/*.md` only) to
        # also admit the level-(1) curator's daily brief,
        # `.claude/plans/briefs/*.html`. This exact literal is the ONE existing
        # pin on the expression this whole card was filed about — an earlier
        # measurement claiming "no test pins it" was wrong by one hit, found by
        # re-running the grep rather than trusting the count. See
        # TestAllowlistRegexAgreement for the three-copy consistency check.
        assert r"^\.claude/plans/([^/]+\.md|briefs/[^/]+\.html)$" in text

        match = re.search(r"BASELINE:\s*([0-9a-f]{40})", text)
        assert match, "workflow declares no 40-char baseline SHA"
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{match.group(1)}^{{commit}}"],
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"workflow baseline {match.group(1)} is not a commit in this repo"
        )

    def test_ci_suite_job_checks_out_the_history_its_own_suite_reads(self) -> None:
        """CB-139. `actions/checkout` defaults to depth 1; this suite reads history.

        The test immediately above runs `git cat-file -e <baseline>` against the
        REAL repository. In a depth-1 clone that commit is absent, so the `tests`
        job was red in CI ALWAYS while staying green in every local run — and a
        gate that cannot pass is indistinguishable from a gate broken on the
        merits, so a genuine regression in that job is no longer visible to
        anyone. `main-invariants.yml` already carried `fetch-depth: 0` with a
        comment saying it is a requirement and not an optimisation; `ci.yml`,
        whose own suite is the thing reading history, carried nothing. The link
        was understood for one workflow and missed for the other.

        WHAT THE ASSERTIONS ARE, and why the obvious spelling of each is wrong:

        * COMMENTS DO NOT COUNT. The fix's own comment contains the literal
          `fetch-depth: 0`, so a test that greps the raw file stays GREEN after
          the real key is deleted — a gate that cannot fire, inside the fix whose
          whole subject is gates that cannot fire. Same reason `code()` exists in
          `test_invariants_job_is_not_subscribed_to_pull_request`. Stripping is
          WHOLE-LINE only (parsing YAML quoting to find an inline `#` is exactly
          the parser this repo does not have), so an INLINE comment is tolerated
          by the matchers instead, rather than silently refusing a legal edit.
        * A FILE IS NOT A COMPOSITION. `ci.yml` declares two jobs and two
          checkouts. "`fetch-depth: 0` somewhere in ci.yml" is satisfied by
          moving the key to `contracts`, which leaves the suite job shallow and
          the gate just as broken.
        * THE KEY MUST BE AN INPUT, NOT MERELY TEXT IN THE STEP. Cross-model
          review broke the first draft with a step whose multiline `name:` scalar
          contained both `fetch-depth: 0` and the test's name: valid YAML, both
          assertions green, checkout still depth 1. So the key is looked for
          inside the step's `with:` MAPPING, and the explanation is required to
          be a COMMENT LINE — a `name:` carrying the test's name is not an
          explanation travelling with the key.
        * ONE CHECKOUT, UNCONDITIONAL. The same review defeated "the first
          checkout step carries the key" with `if: ${{ false }}` on that step
          followed by a second, bare `actions/checkout`: green here, depth 1 in
          Actions. The `tests` job must therefore declare EXACTLY ONE checkout
          and it must carry no `if:`. Both refusals are deliberate: a second or
          conditional checkout in the job whose suite reads history is precisely
          the hole, so it is refused loudly rather than judged.
        * FAIL CLOSED, AND THE INDENTATION BINDING IS SAID OUT LOUD. `pyyaml` is
          not a dependency here (measured: `import yaml` under `--extra dev`
          raises ModuleNotFoundError), so the job is sliced TEXTUALLY, by the
          two-space indentation of a key under `jobs:`. If the workflow is ever
          reformatted to a different indentation this test FAILS with "cannot
          find job `tests`" rather than passing vacuously: a false refusal is
          loud and costs one edit, whereas the vacuous pass is the defect being
          repaired.

        `contracts` is deliberately left shallow: it runs
        `tests/test_cli_signals.py tests/test_fsio.py`, neither of which reads
        this repository's history, and putting the key where nothing needs it
        teaches the next reader to read it as boilerplate.

        Known and NOT closed here, so it is not discovered as a surprise: a
        job-level `if:` that switches the whole `tests` job off is the same
        "gate that cannot fire" shape and this test does not look at it.
        """

        def job_block(text: str, name: str) -> str:
            """The body of one job, bounded by the next two-space-indented key."""
            _, _, body = text.partition("\njobs:")
            assert body, "ci.yml declares no `jobs:` block"
            bounds = list(re.finditer(r"^  ([A-Za-z_][\w-]*):[ \t]*(#.*)?$", body, re.M))
            for i, m in enumerate(bounds):
                if m.group(1) == name:
                    end = bounds[i + 1].start() if i + 1 < len(bounds) else len(body)
                    return body[m.end() : end]
            raise AssertionError(
                f"cannot find job `{name}` in ci.yml; found {[b.group(1) for b in bounds]}"
            )

        def steps_of(block: str) -> list[str]:
            """The job's steps, split on the indentation of the first list item."""
            m = re.search(r"^([ \t]*)steps:[ \t]*(#.*)?$", block, re.M)
            assert m, "the `tests` job declares no `steps:`"
            rest = block[m.end() :]
            items = list(re.finditer(r"^([ \t]*)- ", rest, re.M))
            assert items, "the `tests` job's `steps:` list is empty"
            top = [x for x in items if x.group(1) == items[0].group(1)]
            return [
                rest[x.start() : (top[i + 1].start() if i + 1 < len(top) else len(rest))]
                for i, x in enumerate(top)
            ]

        def checkouts(block: str) -> list[str]:
            return [
                st
                for st in steps_of(block)
                if re.search(r"^[ \t]*(-[ \t]+)?uses:[ \t]*actions/checkout@", st, re.M)
            ]

        def with_mapping(step: str) -> str:
            """The entries strictly nested under the step's `with:` key."""
            m = re.search(r"^([ \t]*)with:[ \t]*(#.*)?$", step, re.M)
            assert m, (
                "the checkout step of the `tests` job declares no `with:` mapping, "
                "so it passes no inputs to actions/checkout at all"
            )
            depth = len(m.group(1))
            body: list[str] = []
            for ln in step[m.end() :].splitlines():
                if ln.strip() and len(ln) - len(ln.lstrip()) <= depth:
                    break
                body.append(ln)
            return "\n".join(body)

        wf = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert wf.exists(), "ci.yml is missing"
        raw = wf.read_text()
        code = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))

        found = checkouts(job_block(code, "tests"))
        assert len(found) == 1, (
            f"the `tests` job declares {len(found)} checkout steps; exactly one is "
            "required, or a shallow one can run after the deep one and the job is "
            "back to depth 1 with this test still green"
        )
        step = found[0]
        assert not re.search(r"^[ \t]*if:", step, re.M), (
            "the checkout of the `tests` job is conditional; a checkout that can be "
            "skipped is a checkout this test cannot vouch for"
        )
        # `'0'` and a trailing inline comment are both legal YAML meaning the same
        # thing, and the whole-line stripping above does not remove an inline
        # comment. A non-zero depth is still refused: only 0 is "all of it", and
        # the baseline is hundreds of commits back.
        assert re.search(
            r"^[ \t]*fetch-depth:[ \t]*['\"]?0['\"]?[ \t]*(#.*)?$", with_mapping(step), re.M
        ), (
            "the `tests` job checks the repository out SHALLOW "
            "(actions/checkout defaults to depth 1) while its own suite reads this "
            "repository's history — that job is then red in CI always and green "
            "everywhere else. See CB-139."
        )

        # The comment is not the gate; the assertion above is. But a bare
        # `fetch-depth: 0` reads as an optimisation and the next reader deletes
        # it, which is precisely how this defect is reintroduced. Pin that the
        # reason travels with the key as a COMMENT — and pin it by the test's OWN
        # name, so a rename cannot leave a stale pointer behind in the workflow.
        needs_history = (
            TestHarnessIntegrity.test_ci_workflow_asserts_the_first_parent_invariant.__name__
        )
        raw_step = checkouts(job_block(raw, "tests"))[0]
        assert any(
            needs_history in ln for ln in raw_step.splitlines() if ln.lstrip().startswith("#")
        ), (
            "no comment on the checkout step names the test that needs the history; "
            "an unexplained `fetch-depth: 0` gets deleted as dead weight"
        )

    def test_ci_tests_job_cannot_report_success_without_running_its_steps(self) -> None:
        """CB-142. `if:` on the checkout step is not the whole family.

        The test immediately above closes ONE way to defeat the `tests` job
        (a shallow checkout that makes its own suite unrunnable). GitHub's own
        documented behaviour opens a WIDER family: it reports a job skipped by
        a job-level `if:` as PASSING for required-status-check purposes — this
        repository already states that fact about `main-invariants.yml` (that
        workflow does not subscribe to `pull_request` for exactly this reason)
        and never checked it for `ci.yml`'s `tests` job, which is the one
        actually meant to become a required check.

        THE PRIMITIVE, not a list of forms: job `tests` must not be able to
        report SUCCESS without having run every one of its steps. Two known
        carriers, both closed here:

        * a job-level `if:` — the whole job is skipped and still counts green;
        * `continue-on-error: true`, at job level OR on any single step — the
          step (or every step) can fail outright and the job still reports
          success. The key's PRESENCE is refused regardless of its value,
          because `continue-on-error: ${{ <expression> }}` is exactly as
          dangerous as a literal `true` and a value-only check would miss it.

        A THIRD carrier surfaced while writing this test and is closed too: a
        step-level `if:` on any of this job's five steps. A skipped step is
        not a failed step, so `if: false` on the "Tests" step would let the
        job finish green having never run pytest at all — the same "reports
        success without running its steps" shape, one level down. This is
        deliberately scoped to the STEPS THIS JOB ALREADY HAS (checkout,
        setup-uv, interpreter pin, lint, tests): none of the five has any
        legitimate reason to be conditional, unlike a job-level `if:` (which
        this repo elsewhere refuses to ban outright, because some future job
        legitimately needs one).

        WHY THE EXTRACTION MUST BE FAIL-CLOSED, not merely correct today:
        `pyyaml` is not a dependency here (measured — `import yaml` under
        `--extra dev` raises `ModuleNotFoundError`), so this reads `ci.yml` by
        slicing text on its two-space job-key indentation, exactly like the
        CB-139 test above. That slice is brittle to reformatting BY
        CONSTRUCTION, so the extraction itself must refuse to guess: it
        requires the job key `tests:` to occur EXACTLY ONCE among the
        top-level `jobs:` children (zero is "renamed out from under the
        test", more than one is "cannot tell which block is real"), and it
        asserts the extracted body is non-empty. A rename, an indent change,
        or a moved job key therefore turns this test RED with a named
        assertion failure — never green over an empty or wrong slice. This is
        the test's own main subject, not incidental plumbing: a fix for
        "gates that report green when they should not" that itself passes
        vacuously on a reformatted file would be the same defect recreated
        one layer up.

        AVOIDING THE OTHER TRAP THIS REPO NAMES FOR ITSELF: neither this
        assertion nor its own failure messages are grepped against — the
        checks below are structural (parsed key names at a derived
        indentation depth), not `"if:" in text` or `"continue-on-error" in
        text` substring tests. A prose mention of either string anywhere in
        this file's comments (this docstring included) cannot make the
        mechanism it guards look present once it has been deleted, because
        comment lines are stripped before any of this runs and the match is
        never on raw text.
        """

        def job_block(text: str, name: str) -> str:
            """The body of exactly one job, or a loud refusal — never a guess.

            `text` must already have comment lines stripped.
            """
            _, _, body = text.partition("\njobs:")
            assert body, "ci.yml declares no `jobs:` block"
            bounds = list(re.finditer(r"^  ([A-Za-z_][\w-]*):[ \t]*$", body, re.M))
            matches = [i for i, m in enumerate(bounds) if m.group(1) == name]
            assert len(matches) == 1, (
                f"found job `{name}` {len(matches)} time(s) in ci.yml's `jobs:` block "
                f"(expected exactly one unambiguous occurrence); jobs seen: "
                f"{[b.group(1) for b in bounds]} — this test cannot tell which block, "
                "if any, is the real one, so it refuses rather than guess"
            )
            idx = matches[0]
            start = bounds[idx].end()
            end = bounds[idx + 1].start() if idx + 1 < len(bounds) else len(body)
            block = body[start:end]
            assert block.strip(), f"job `{name}` has an empty body in ci.yml"
            return block

        wf = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert wf.exists(), "ci.yml is missing"
        raw = wf.read_text()
        code = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))

        block = job_block(code, "tests")

        # Job-level keys (`runs-on:`, `steps:`, a hypothetical `if:` or
        # `continue-on-error:`) all sit at one consistent indentation. Derive
        # it from the block's own first non-blank line rather than hardcoding
        # a column count, so a change to the FILE's overall indentation still
        # gets a correct depth here (a change to `tests`'s OWN depth relative
        # to `jobs:` is caught by job_block's two-space anchor above).
        first_line = next(ln for ln in block.splitlines() if ln.strip())
        base_indent = len(first_line) - len(first_line.lstrip(" "))
        assert base_indent > 0, "job `tests`'s body is not indented under its key"
        top_level_keys = re.findall(rf"^ {{{base_indent}}}([A-Za-z_-]+):", block, re.M)

        assert "steps" in top_level_keys, "job `tests` declares no top-level `steps:` key"
        assert "if" not in top_level_keys, (
            "job `tests` carries a job-level `if:` — GitHub reports a job skipped by "
            "`if:` as PASSING for required-status-check purposes, which switches this "
            "gate off while it keeps reporting green. See CB-142."
        )
        assert "continue-on-error" not in top_level_keys, (
            "job `tests` carries a job-level `continue-on-error:` — every one of its "
            "steps can fail and the job still reports success. See CB-142."
        )

        # Split `steps:` into individual list items, the same way as the
        # CB-139 test above, so a per-step key cannot hide inside one of them.
        steps_m = re.search(rf"^ {{{base_indent}}}steps:[ \t]*$", block, re.M)
        assert steps_m, "job `tests` declares a `steps:` key with no bare-mapping form found"
        rest = block[steps_m.end() :]
        items = list(re.finditer(r"^([ \t]*)- ", rest, re.M))
        assert items, "job `tests` declares an empty `steps:` list"
        step_indent = items[0].group(1)
        top_items = [it for it in items if it.group(1) == step_indent]
        steps = [
            rest[it.start() : (top_items[i + 1].start() if i + 1 < len(top_items) else len(rest))]
            for i, it in enumerate(top_items)
        ]
        assert len(steps) >= 5, (
            f"job `tests` has {len(steps)} step(s); expected at least the 5 documented "
            "ones (checkout, setup-uv, interpreter pin, lint, tests) — fewer means this "
            "extraction is not seeing the whole list"
        )

        for i, step in enumerate(steps):
            assert not re.search(r"^[ \t]*continue-on-error:", step, re.M), (
                f"step {i} of job `tests` carries `continue-on-error:` — it can fail and "
                "the job still reports success. See CB-142."
            )
            assert not re.search(r"^[ \t]*if:", step, re.M), (
                f"step {i} of job `tests` carries `if:` — a skipped step is not a failed "
                "step, so this step can be silently excluded from a run that still "
                "reports success. See CB-142."
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

    def test_every_rerun_hint_goes_through_the_shared_helper(self) -> None:
        """CB-116(d). A refusal that prints a bare re-run drops --merge-msg.

        Behavioural coverage exists for the [5/7] conflict refusal, but the two
        exit-13 refusals cannot be forced deterministically in a test — they
        need main to move between the gates and the lock. So this counts the
        SITES: no refusal may print the re-run command itself, because the one
        that does is the one that silently drops the operator's message. The
        helper is the only place that string may be built.
        """
        src = (REPO_ROOT / "tools" / "worktree-finish.sh").read_text()
        bare = [
            ln.strip()
            for ln in src.splitlines()
            if 'echo "      tools/worktree-finish.sh ${SLUG}"' in ln
        ]
        assert not bare, f"a re-run hint bypasses _retry_hint and loses --merge-msg: {bare}"
        assert src.count("_retry_hint\n") >= 4, (
            "the four refusal paths (forward-merge conflict, main moved, branch "
            "moved, merge failed) must all print the helper's line"
        )
        # The exit-13 refusals specifically, by their own text. Both markers
        # must be DISTINCT strings that occur in different refusals: the first
        # draft paired "main moved while this finish was running" with
        # "moved while this finish", whose first occurrence is INSIDE the first
        # marker on the same line, so the loop checked one site twice and the
        # branch-moved refusal was never located (measured).
        for marker in (
            "main moved while this finish was running",
            "${BRANCH} moved while this finish was running",
        ):
            at = src.index(marker)
            assert "_retry_hint" in src[at : at + 700], f"no retry hint after: {marker}"

    def test_the_merge_subject_is_derived_from_what_main_lacks(self) -> None:
        """CB-116(a)+(b), pinned structurally as well as behaviourally.

        The behavioural class next door lands real merges, which is the real
        proof. This exists for the two ways the code could regress while still
        producing a correct-looking string on the fixtures: reading the worktree
        TIP again (which is main's after the forward-merge), and collapsing
        `--reverse | first line` into `--reverse -1`. That second one is a live
        trap, not a hypothetical: git applies the count BEFORE reversing, so
        `--reverse -1` returns the NEWEST commit and silently reinstates the
        last-commit behaviour this card removed.
        """
        raw = (REPO_ROOT / "tools" / "worktree-finish.sh").read_text()
        # CODE only. This file's own comments quote the shape being forbidden,
        # and a ratchet that reads prose is the fsio.py mistake CLAUDE.md
        # records: its first draft matched `open(path, "w")` inside three
        # docstrings.
        src = "\n".join(
            ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")
        )
        assert '"${TESTED_MAIN}..${TESTED_HEAD}"' in src, (
            "the derivation must be restricted to the commits main does not have"
        )
        assert "log --first-parent --reverse --no-merges" in src, (
            "without --first-parent the range's date order can put a commit "
            "absorbed from a sibling branch first — measured, and on that shape "
            "the range-only derivation is worse than the code it replaced"
        )
        assert "log -1 --no-merges" not in src, (
            "the derivation reads the worktree tip again, which [5/7] polluted"
        )
        assert "--reverse -1" not in src and "-1 --reverse" not in src, (
            "git applies -1 before --reverse, so this yields the NEWEST commit"
        )
        # Derived before the gates, so the empty-population refusal is free.
        assert src.index("INTEGRATION_MSG=") < src.index("pytest tests/")
        # And the merge lands the derived value, not the raw operator argument.
        assert 'merge "${BRANCH}" --no-ff -m "${INTEGRATION_MSG}"' in src

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
            "_guard_interpreter_matches_main",
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
        """Fail fast, rather than after waiting up to 60s on the lock.

        The anchor is the STATEMENT that opens the integration lock, never the
        bare `exec 9>`. CB-187's own explanatory comment names that descriptor
        in prose 400 lines earlier, and a bare-substring anchor then finds the
        COMMENT and reports the guard as running after a "lock" that is only a
        sentence about one. This is the identical hazard the sibling test
        already guards against for `--no-ff`, applied to its neighbour.
        """
        src = self.FINISH.read_text()
        assert src.index("_guard_enforcement_armed") < src.index('exec 9>"${LOCK_FILE}"')

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

    def test_interpreter_guard_runs_between_the_forward_merge_and_the_checks(self) -> None:
        """Both bounds are real, and each one alone would be wrong (CB-135).

        Before the forward-merge the worktree has not yet received the
        `.python-version` that main may have gained, so the guard would compare
        against a pin that is about to be replaced. After [6/7] the refusal
        arrives having already spent the ~70s suite run whose result it is
        declaring meaningless.
        """
        src = self.FINISH.read_text()
        call_at = src.index("_guard_interpreter_matches_main ")
        # Anchored on the MERGE, not on the echo that announces it. Cross-model
        # review moved the call between `echo "[5/7] Forward-merging…"` and the
        # `git merge` itself and this test stayed green — "after the text that
        # says a merge is coming" is not "after the merge".
        assert src.index('git -C "${WORKTREE_PATH}" merge "${CURRENT_MAIN}"') < call_at
        # Anchored on the emitted phase line, not the bare string: the comment
        # explaining this ordering names [6/7] too, and matching that would
        # make the assertion pass on the comment rather than on the code.
        assert call_at < src.index('echo "[6/7]')

    def test_the_interpreter_check_is_re_asserted_inside_the_lock(self) -> None:
        """A pre-check is not an invariant at landing time (cross-model review).

        main's `.venv` is gitignored, so `_guard_main_clean` cannot see it move
        and the in-lock SHA re-checks are about commits. A
        `UV_PYTHON=… uv sync` in main during the ~90s suite run would otherwise
        land work tested on one interpreter onto a main that now has another —
        the very skew the TESTED_MAIN re-check exists for, arriving through the
        one piece of state neither of them watches. So it gets the same answer
        those two got: assert it again where nothing can intervene.
        """
        src = self.FINISH.read_text()
        calls = [i for i in range(len(src)) if src.startswith("_guard_interpreter_matches_main ", i)]
        assert len(calls) == 2, f"expected a pre-check and an in-lock re-check, found {len(calls)}"
        # The statement that opens the lock, not the header comment that names
        # the descriptor in prose — the same precision the merge anchor below
        # already needed, and for the same reason.
        lock_at = src.index('exec 9>"${LOCK_FILE}"')
        # The integration merge itself, not the header comment that mentions
        # `--no-ff` 400 lines earlier.
        merge_at = src.index('git -C "${REPO_ROOT}" merge "${BRANCH}" --no-ff')
        assert calls[0] < lock_at, "the cheap pre-check must run before the lock is waited on"
        assert lock_at < calls[1] < merge_at, "the re-check must sit inside the lock, before the merge"

    def test_skip_checks_cannot_disable_the_interpreter_guard(self) -> None:
        """`--skip-checks` skips ruff and pytest, never a safety guard.

        Structural because the flag's whole effect is one `if` block: the guard
        is refused entry to it. The behavioural half — the script really
        exiting 14 under `--skip-checks` — is
        TestInterpreterGuardEndToEnd::test_skip_checks_still_refuses_a_mismatch.
        """
        src = self.FINISH.read_text()
        skip_block_at = src.index('if [[ "${SKIP_CHECKS}" == true ]]')
        assert src.index("_guard_interpreter_matches_main ") < skip_block_at
        # And the flag's own help text must keep saying what it does not skip.
        assert "NOT the safety guards" in src


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

    def test_the_main_refusal_names_the_cost_other_sessions_pay(self, repo: Path) -> None:
        """A refusal must name the state it LEAVES, not only the state it refused.

        `git add` stages before the hook runs, and a refusal unstages nothing —
        git does not, and this hook deliberately does not either (a hook that
        mutates the operator's index turns a refusal into an action). So the
        files sit in main's index afterwards, `_guard_main_clean` reads exactly
        that index, and one refused commit here refuses
        `tools/worktree-finish.sh` in EVERY worktree of this clone — other
        sessions' included. Measured 2026-08-22: ~40 minutes of two blocked
        DIR-2 integrations, by an operator who had been told only "refusing to
        commit on main" (CB-130).

        The hook is RUN, not grepped: a file can carry a line the hook never
        prints on this path, and a structural test would assert the wrong thing.
        """
        self._install(repo)
        hooks = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        assert (hooks / "pre-commit").is_file(), "fixture never armed the throwaway repo"

        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")
        result = self._commit(repo, "src/mod.py")
        assert result.returncode != 0, "fixture did not take: the hook let a source edit onto main"

        # The PREMISE the new lines assert, pinned rather than trusted: the
        # refusal really does leave the path staged, and that staged path really
        # is what stops every other worktree.
        assert git(repo, "status", "--porcelain", "--untracked-files=no") == "A  src/mod.py"
        assert run_guard("_guard_main_clean", str(repo)).returncode == 11

        err = result.stderr
        assert "still staged" in err.lower(), err
        assert "worktree-finish.sh" in err, err

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

    def test_daily_brief_on_main_allowed(self, repo: Path) -> None:
        """CB-266: the level-(1) curator's daily brief is a plan note by intent.

        `.claude/plans/briefs/DAILY-<date>.html` — a second path level, an
        `.html` extension — landed directly on main from 2026-08-29
        (813bb6d), and the letter of this hook refused it: the allowlist was
        `.claude/plans/*.md` only, narrower than the ratified intent ("a plan
        artefact the owner reads"). Widened by exactly this one shape.
        """
        self._install(repo)
        briefs = repo / ".claude" / "plans" / "briefs"
        briefs.mkdir(parents=True)
        (briefs / "DAILY-2026-08-30.html").write_text("<html>brief</html>\n")
        assert self._commit(repo, ".claude/plans/briefs/DAILY-2026-08-30.html").returncode == 0

    def test_nested_brief_path_refused(self, repo: Path) -> None:
        """CB-266's own oracle mutant (depth): `briefs/` is one level, not a subtree."""
        self._install(repo)
        deep = repo / ".claude" / "plans" / "briefs" / "sub"
        deep.mkdir(parents=True)
        (deep / "x.html").write_text("<html>x</html>\n")
        assert self._commit(repo, ".claude/plans/briefs/sub/x.html").returncode != 0

    def test_non_html_file_under_briefs_refused(self, repo: Path) -> None:
        """CB-266's own oracle mutant (extension): the widening is narrow ON PURPOSE.

        `briefs/` is not thrown open to any file — only `*.html` — or the next
        script to write there mints a silent bypass of the whole rule.
        """
        self._install(repo)
        briefs = repo / ".claude" / "plans" / "briefs"
        briefs.mkdir(parents=True)
        (briefs / "evil.py").write_text("import os\n")
        assert self._commit(repo, ".claude/plans/briefs/evil.py").returncode != 0

    def test_html_file_outside_briefs_still_refused(self, repo: Path) -> None:
        """CB-266's own oracle mutant (scope): `.html` alone is not the allowance —
        only `.claude/plans/briefs/*.html` is. A second-level `.html` note
        anywhere else under `.claude/plans/` is still a violation.
        """
        self._install(repo)
        other = repo / ".claude" / "plans" / "other"
        other.mkdir(parents=True)
        (other / "x.html").write_text("<html>x</html>\n")
        assert self._commit(repo, ".claude/plans/other/x.html").returncode != 0

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


# ===========================================================================
# CB-58 — the claims wiring.
#
# Two classes, matching this file's existing split: structural tests that the
# scripts CALL the right thing in the right phase, and behavioural tests that
# actually RUN worktree-setup.sh against a stub `codebugs` and assert on what
# it does. Both are needed for the reason the file's other docstrings give:
# a per-guard test cannot see the composition, and a structural test cannot
# see whether the composition behaves.
# ===========================================================================


def code_only(src: str) -> str:
    """Drop whole-line shell comments.

    A text ratchet that reads comments FALSE-REFUSES the documentation that
    keeps its own rule understood — this file's `TestWriteCallSitesRatchet`
    sibling in tests/test_fsio.py records the same lesson, and two of the tests
    below tripped on the very comments explaining why the old spelling is gone.

    Whole-line only, on purpose. Stripping inline `#` would have to parse shell
    quoting, and every string this class searches for lives in code, not in a
    trailing comment.
    """
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


def invocations(src: str, command: str) -> list[str]:
    """Logical lines where `command` is actually RUN, not merely mentioned.

    Two things a naive `command in src` gets wrong, both met while writing
    these tests:

    * A mutation that disables a call by prefixing the shell no-op — `:
      codebugs release …` — leaves the substring intact, so the assertion
      passes over a script that does nothing. Command position is the fix.
    * Real calls are wrapped in `if` and split across backslash continuations,
      so the flags belong to the same LOGICAL line as the command name.

    Comments are dropped first, then continuations joined, then each line is
    stripped of the leading keywords that still leave the next word in command
    position. Anything else — `:`, `#`, a string — is a mention.
    """
    joined = code_only(src).replace("\\\n", " ")
    out = []
    for line in joined.splitlines():
        rest = " ".join(line.split())
        for keyword in ("if ", "elif ", "then ", "! "):
            while rest.startswith(keyword):
                rest = rest[len(keyword) :]
        if rest.startswith(command + " "):
            out.append(rest)
    return out


class TestAllowlistRegexAgreement:
    """CB-266 oracle mutant #5: the allowlist regex is duplicated byte-for-byte
    in three places (tools/pre-commit-hook.sh, tools/commit-msg-hook.sh,
    .github/workflows/main-invariants.yml) because none of them can source a
    shared file — the two hooks run from `.git/hooks/` as standalone scripts
    and must work when `tools/` is absent from the checked-out tree, and the
    workflow runs on a different machine entirely. Same constraint CLAUDE.md
    already documents for the branch-type predicate's own SHARED MERGE-GATE
    PREDICATE markers, and the same reason a plain "one copy" refactor is not
    achievable here.

    Before CB-266, a grep of tests/ for this expression found exactly ONE
    hit, in test_ci_workflow_asserts_the_first_parent_invariant above — and
    that one pins only the CI copy. No test compared the three copies to each
    other, so a divergence between them (one file gets patched, the other two
    do not) would have gone undetected. This class is that comparison: the
    substitute this codebase's own convention prescribes for an "unreachable
    single copy" — a test that refuses to let the texts disagree, rather than
    a shared file that cannot exist.
    """

    @staticmethod
    def _extract(text: str, *, flag: str) -> str:
        """The literal regex string bound to `grep <flag> '...'`."""
        marker = f"grep {flag} '"
        idx = text.index(marker)
        start = idx + len(marker)
        end = text.index("'", start)
        return text[start:end]

    def test_the_three_copies_are_byte_identical(self) -> None:
        pre_commit = (REPO_ROOT / "tools" / "pre-commit-hook.sh").read_text()
        commit_msg = (REPO_ROOT / "tools" / "commit-msg-hook.sh").read_text()
        workflow = (REPO_ROOT / ".github" / "workflows" / "main-invariants.yml").read_text()

        # pre-commit-hook.sh and the workflow REFUSE what falls OUTSIDE the
        # pattern (grep -vE); commit-msg-hook.sh SELECTS what falls INSIDE it
        # (grep -E) — two decisions sharing one expression (CLAUDE.md §2 п.3).
        neg_hook = self._extract(pre_commit, flag="-vE")
        pos_hook = self._extract(commit_msg, flag="-E")
        neg_ci = self._extract(workflow, flag="-vE")

        assert neg_hook == pos_hook == neg_ci, (
            "the allowlist regex has diverged across its three copies:\n"
            f"  tools/pre-commit-hook.sh (negative):        {neg_hook!r}\n"
            f"  tools/commit-msg-hook.sh (positive):        {pos_hook!r}\n"
            f"  .github/workflows/main-invariants.yml (neg): {neg_ci!r}"
        )

    def test_the_comparison_actually_discriminates_a_divergent_copy(self) -> None:
        """A self-check on fixture text, not on the repository's real files.

        Demonstrates the exact mutant the class above exists to catch — break
        the expression in one of the three sources — without mutating the
        repository itself to prove it.
        """
        good = r"^\.claude/plans/([^/]+\.md|briefs/[^/]+\.html)$"
        mutated = r"^\.claude/plans/([^/]+\.md|briefs/[^/]+\.HTML)$"  # one place diverged
        pre_commit_text = f"offending=$(echo \"$x\" | grep -vE '{good}' || true)\n"
        commit_msg_text = f"plans=$(echo \"$x\" | grep -E '{good}' || true)\n"
        workflow_text = f"offending=$(printf '%s\\n' \"$x\" \\\n  | grep -vE '{mutated}' || true)\n"

        neg_hook = self._extract(pre_commit_text, flag="-vE")
        pos_hook = self._extract(commit_msg_text, flag="-E")
        neg_ci = self._extract(workflow_text, flag="-vE")

        assert neg_hook == pos_hook, "the fixture's two non-mutated copies must still agree"
        assert neg_ci != neg_hook, (
            "the fixture's mutant copy was not actually different from the good ones — "
            "this test would not have caught a real divergence"
        )


class TestMainInvariantsAuditScript:
    """CB-266 P2 / oracle items 1-3, exercised on the CI copy specifically.

    §13 п.18 forbids a string oracle for this unit's textual half: a mutant
    saying the same false thing in different words would leave a
    substring-in-YAML assertion green. So this class does not read the YAML —
    it extracts the audit step's own `run: |` block and executes it, in a
    throwaway repo, against exactly the shapes CB-266 is about: an honest
    brief (must pass) and the three named mutants (must each still refuse).
    TestPreCommitHook and TestCommitMsgNamingGate cover the same shapes on
    the two git-hook copies; this is the third copy, which cannot be tested
    by installing a git hook because it is not one.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "main-invariants.yml"

    @classmethod
    @functools.lru_cache(maxsize=1)
    def _extract_script(cls) -> str:
        """The step's own `run: |` block, dedented. Cached: the file is static
        for the life of the process, and this is re-derived once per test
        method otherwise (four times here) for no reason — /simplify (2026-08-30)."""
        text = cls.WORKFLOW.read_text()
        marker = "\n        run: |\n"
        idx = text.index(marker)
        body = text[idx + len(marker) :]
        lines: list[str] = []
        for ln in body.splitlines():
            if ln.strip() == "":
                lines.append("")
                continue
            assert ln.startswith(" " * 10), f"unexpected indentation in run block: {ln!r}"
            lines.append(ln[10:])
        return "\n".join(lines)

    def _run(self, repo: Path, baseline: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["BASELINE"] = baseline
        return subprocess.run(
            ["bash", "-c", self._extract_script()],
            cwd=str(repo),
            capture_output=True,
            text=True,
            env=env,
        )

    @staticmethod
    def _commit_all(repo: Path, message: str) -> None:
        git(repo, "add", "-A")
        git(repo, "commit", "-m", message)

    @pytest.mark.parametrize(
        "rel_dir,filename,content,commit_msg,expect_ok",
        [
            pytest.param(
                "briefs",
                "DAILY-2026-08-30.html",
                "<html>brief</html>\n",
                "docs(curator): DAILY-2026-08-30.html",
                True,
                id="a brief-shaped commit is accepted",
            ),
            pytest.param(
                "briefs/sub",
                "x.html",
                "<html>x</html>\n",
                "docs: nested brief path",
                False,
                id="oracle mutant #1 (depth): nested under briefs/ is refused",
            ),
            pytest.param(
                "briefs",
                "evil.py",
                "import os\n",
                "docs: not actually a brief",
                False,
                id="oracle mutant #2 (extension): wrong extension under briefs/ is refused",
            ),
            pytest.param(
                "other",
                "x.md",
                "# x\n",
                "docs: two levels, still not briefs/",
                False,
                id="oracle mutant #3 (scope): two levels outside briefs/ is refused",
            ),
        ],
    )
    def test_commit_shape_is_judged_correctly(
        self,
        repo: Path,
        rel_dir: str,
        filename: str,
        content: str,
        commit_msg: str,
        expect_ok: bool,
    ) -> None:
        baseline = git(repo, "rev-parse", "HEAD")
        d = repo / ".claude" / "plans" / rel_dir
        d.mkdir(parents=True)
        (d / filename).write_text(content)
        self._commit_all(repo, commit_msg)
        r = self._run(repo, baseline)
        if expect_ok:
            assert r.returncode == 0, r.stdout + r.stderr
            assert "clean" in r.stdout, r.stdout
        else:
            assert r.returncode != 0, r.stdout + r.stderr


CASCADE_REGISTRY_REL = ".claude/plans/CASCADE-IDS.md"
CASCADE_MINT = REPO_ROOT / "tools" / "cascade-mint.sh"

# Registries the gate and the allocator are driven over together. Each one
# carries a case that a careless scanner gets WRONG, and the point of the pair
# is that both readers must get it wrong or right together — see
# TestCascadeMintGate.test_the_gate_accepts_exactly_the_id_the_tool_hands_out.
CASCADE_CORPUS: dict[str, str] = {
    # A Latin 'T' typo is a SPENT number. A scanner that reads only the Cyrillic
    # spelling computes 9 here instead of 43 and hands out an id already used.
    "a latin typo carries the maximum": (
        "# reg\n- \u0422-3 \u2014 a\n- T-42 \u2014 latin typo\n- \u0422-9 \u2014 c\n"
    ),
    # Without a LEFT boundary the Latin arm of the unit pattern matches inside
    # 'BT-40', and the BT family silently raises the unit counter to 41.
    "a large BT id must not raise the unit counter": (
        "# reg\n- \u0422-3 \u2014 a\n- BT-40 \u2014 a sub-topic\n- \u0422-5 \u2014 c\n"
    ),
    # The tail is ANNULLED. Its number stayed spent, so the next id is 8, not 7.
    "the maximum sits on an annulled line": (
        "# reg\n- \u0422-6 \u2014 a\n- \u0422-7 \u2014 b\n"
        "- \u041a\u041e\u041b\u041b\u0418\u0417\u0418\u042f: \u0441\u0442\u0440\u043e\u043a\u0430 \u0432\u044b\u0448\u0435 \u0410\u041d\u041d\u0423\u041b\u0418\u0420\u041e\u0412\u0410\u041d\u0410\n"
    ),
    # The maximum is only MENTIONED, inside guillemets, in a collision note. The
    # allocator counts it; a gate that only looked at allocation lines when it
    # computed the maximum would hand 3 back out.
    "the maximum appears only inside guillemets": (
        "# reg\n- \u0422-2 \u2014 a\n- \u0422-3 \u2014 b\n"
        "- \u041a\u041e\u041b\u041b\u0418\u0417\u0418\u042f: \u00ab\u0422-11\u00bb \u0430\u043d\u043d\u0443\u043b\u0438\u0440\u043e\u0432\u0430\u043d\u0430\n"
    ),
    # The allocator refuses a number of more than nine digits IN WHAT IT READS,
    # but its OUTPUT may be one digit longer. A gate that applied the nine-digit
    # rule to the staged blob refused the allocator's OWN mint here.
    "the allocator's successor is one digit longer": (
        "# reg\n- \u0422-3 \u2014 a\n- \u0422-999999999 \u2014 the tail\n"
    ),
    # Leading zeros: '\u0422-007' is seven, not a separate id and not a syntax error.
    "an id written with leading zeros": (
        "# reg\n- \u0422-1 \u2014 a\n- \u0422-007 \u2014 b\n"
    ),
}

CASCADE_CASES: list[tuple[str, str, str]] = [
    ("the repository's own registry", "live", "\u0422"),
    ("the repository's own registry", "live", "BT"),
    ("the repository's own registry", "live", "DIR"),
    *[(name, name, "\u0422") for name in CASCADE_CORPUS],
    ("a large BT id must not raise the unit counter", "a large BT id must not raise the unit counter", "BT"),
]


class TestCascadeMintGate:
    """The allocator had a tool and no gate; a hand-typed number still landed.

    `tools/cascade-mint.sh` computes the number under a lock and commits it in
    one operation, which closes the mint MADE BY THE SCRIPT. All three
    collisions in `.claude/plans/CASCADE-IDS.md` were hand-typed numbers landed
    with a plain `git commit`, and the third one satisfied the read-the-tail
    convention by the letter — what was protected was the READING, not the
    COMPUTATION (CB-137).

    Every test here RUNS the hook in a throwaway repo. A structural test would
    assert that a line exists in a file, which is the failure this repository
    keeps filing cards about.
    """

    @staticmethod
    def _arm(repo: Path, registry_text: str) -> None:
        """Land a registry, THEN arm the hook: the baseline is not judged."""
        (repo / ".claude" / "plans").mkdir(parents=True, exist_ok=True)
        (repo / CASCADE_REGISTRY_REL).write_text(registry_text, encoding="utf-8")
        git(repo, "add", "--", CASCADE_REGISTRY_REL)
        git(repo, "commit", "-m", "registry")
        TestPreCommitHook._install(repo)

    @staticmethod
    def _append(repo: Path, text: str) -> None:
        with (repo / CASCADE_REGISTRY_REL).open("a", encoding="utf-8") as fh:
            fh.write(text)

    @staticmethod
    def _commit(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", CASCADE_REGISTRY_REL, *extra], check=True
        )
        return subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "attempt"],
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _mint_line(id_: str, text: str = "probe") -> str:
        return f"- {id_} \u2014 {text}\n"

    @staticmethod
    def _dry_run(repo: Path, prefix: str) -> str:
        r = subprocess.run(
            ["bash", str(CASCADE_MINT), "--prefix", prefix, "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(repo),
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    # ---- the two sides of the rule ----------------------------------------

    def test_a_hand_typed_number_is_refused_and_names_the_tool(self, repo: Path) -> None:
        """A gate with no named way out is a wall (the card's item 3)."""
        self._arm(repo, CASCADE_CORPUS["an id written with leading zeros"])
        self._append(repo, self._mint_line("\u0422-42"))
        result = self._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "tools/cascade-mint.sh" in result.stderr, result.stderr
        assert "--no-verify" in result.stderr, result.stderr

    def test_the_allocators_own_mint_passes(self, repo: Path) -> None:
        """A gate that refuses its own tool is a gate nobody can use.

        The REAL script is run against the REAL hook: it computes the number,
        appends and commits under its own lock, and the hook judges that commit.
        """
        self._arm(repo, CASCADE_CORPUS["the maximum sits on an annulled line"])
        result = subprocess.run(
            ["bash", str(CASCADE_MINT), "--prefix", "\u0422", "--text", "minted by the tool"],
            capture_output=True,
            text=True,
            cwd=str(repo),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "\u0422-8", result.stdout
        assert "\u0422-8" in (repo / CASCADE_REGISTRY_REL).read_text(encoding="utf-8")
        assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    # ---- the discriminating case: an EDIT is not a mint ---------------------

    def test_editing_an_existing_line_passes(self, repo: Path) -> None:
        """The case that killed the card's own mechanism.

        The card said to judge the ADDED LINES of the diff. Renaming a brief
        inside a registry line — `git mv`, which is what collision #2 actually
        did — rewrites the line, so the diff shows an ADDED line carrying an
        ALREADY SPENT id and a line-based predicate refuses an edit that is not
        a mint at all. A false refusal on main is not a local cost: the path
        stays staged and every worktree of this clone is then refused by
        `_guard_main_clean` (CB-130).
        """
        self._arm(repo, "# reg\n- \u0422-4 \u2014 unit, `OLD-BRIEF.md`\n")
        path = repo / CASCADE_REGISTRY_REL
        path.write_text(
            path.read_text(encoding="utf-8").replace("OLD-BRIEF.md", "NEW-BRIEF.md"),
            encoding="utf-8",
        )
        result = self._commit(repo)
        assert result.returncode == 0, result.stderr

    def test_a_note_that_only_mentions_an_id_passes(self, repo: Path) -> None:
        """A collision note names the free number; naming is not allocating.

        This is the other half of the letter-fix: `ids(staged) \\ ids(HEAD)`
        would call this line a mint of \u0422-40 and refuse it, and writing such a
        note is exactly what happens after a collision.
        """
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n")
        self._append(repo, "- \u041a\u041e\u041b\u041b\u0418\u0417\u0418\u042f: \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0439 \u043d\u043e\u043c\u0435\u0440 \u2014 \u0422-40\n")
        assert self._commit(repo).returncode == 0

    def test_a_second_allocation_line_for_an_existing_id_is_refused(self, repo: Path) -> None:
        """Collisions #2 and #3, which a SET of ids cannot see.

        In both, the number typed by hand was ALREADY in the registry when the
        commit was made — the other direction had landed it minutes earlier — so
        `ids(staged) \\ ids(HEAD)` is EMPTY and a set-based gate accepts it.
        Measured against the real registry before this test was written: it did.
        Multiplicity over ALLOCATION LINES is what sees it.
        """
        self._arm(repo, "# reg\n- \u0422-4 \u2014 taken by the other direction\n")
        self._append(repo, self._mint_line("\u0422-4", "mine, typed by hand"))
        result = self._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "tools/cascade-mint.sh" in result.stderr

    def test_an_annulled_line_spends_its_number(self, repo: Path) -> None:
        """A gate cleverer than the tool disagrees with it on the first re-mint.

        The maximum sits on an annulled line, so the next id is 8. A gate that
        skipped annulled lines would compute 7, refuse the correct mint and
        accept a re-issue of a spent number.
        """
        text = CASCADE_CORPUS["the maximum sits on an annulled line"]
        self._arm(repo, text)
        self._append(repo, self._mint_line("\u0422-8"))
        assert self._commit(repo).returncode == 0

        git(repo, "reset", "--hard", "HEAD~1")
        self._append(repo, self._mint_line("\u0422-7", "re-using the annulled number"))
        assert self._commit(repo).returncode != 0

    # ---- fail-closed states -------------------------------------------------

    def test_two_allocation_lines_in_one_commit_are_refused(self, repo: Path) -> None:
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n")
        self._append(repo, self._mint_line("\u0422-5") + self._mint_line("\u0422-6"))
        result = self._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "mints ONE id per run" in result.stderr, result.stderr

    def test_an_indented_or_starred_bullet_is_still_an_allocation(self, repo: Path) -> None:
        """Anchoring on a bare '- ' made ONE LEADING SPACE a bypass.

        A line that OPENS with an id allocates that id whatever its bullet, so
        the shapes a hand-writer reaches for by accident are covered too. The
        registry's collision notes are unaffected: they open with a word.
        """
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n")
        for line in ("  - \u0422-9 \u2014 indented\n", "* \u0422-9 \u2014 starred\n"):
            self._append(repo, line)
            result = self._commit(repo)
            assert result.returncode != 0, (line, result.stdout)
            git(repo, "reset", "--hard")

    def test_an_id_hugging_the_bullet_is_refused(self, repo: Path) -> None:
        """The one bullet spelling the ALLOCATOR cannot see.

        `-\u0422-5` puts the id straight after the '-' bullet, and the left
        boundary excludes a '-', so `tools/cascade-mint.sh` misses that id
        entirely. Cross-model review reproduced the whole chain: the line lands,
        the allocator's `max` never sees it, and the allocator then hands the
        same number out a second time. A gate that recognised the line as an
        allocation while its own `max` could not see it would bless exactly
        that.
        """
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n")
        self._append(repo, "-\u0422-5 \u2014 hugging the bullet\n")
        result = self._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "bullet" in result.stderr, result.stderr

    def test_a_bullet_hugging_line_already_on_main_does_not_block_everything(
        self, repo: Path
    ) -> None:
        """An OLD one cancels out: a permanent refusal would block every session.

        The line is already in HEAD, so it is nobody's to fix from a commit
        hook, and the ordinary next mint must still land.
        """
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n-\u0422-9 \u2014 legacy\n")
        self._append(repo, self._mint_line("\u0422-5"))
        assert self._commit(repo).returncode == 0

    def test_one_mint_per_commit_is_counted_ACROSS_families(self, repo: Path) -> None:
        """The allocator mints one id per RUN, so one per COMMIT.

        Counting inside a family let a commit carrying the next \u0422 and the next
        BT through while the refusal text said otherwise (cross-model review).
        """
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n- BT-9 \u2014 b\n")
        self._append(repo, self._mint_line("\u0422-5") + self._mint_line("BT-10"))
        result = self._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "mints ONE id per run" in result.stderr, result.stderr

    def test_a_registry_absent_from_head_is_refused(self, repo: Path) -> None:
        """Every id is new; there is no allocator state to check against."""
        TestPreCommitHook._install(repo)
        (repo / ".claude" / "plans").mkdir(parents=True)
        (repo / CASCADE_REGISTRY_REL).write_text("# reg\n- \u0422-1 \u2014 a\n", encoding="utf-8")
        result = self._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "does not exist in HEAD" in result.stderr, result.stderr

    def test_no_verify_carries_it_through(self, repo: Path) -> None:
        """The gate is against the accident, not against a stated intent."""
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n")
        self._append(repo, self._mint_line("\u0422-999"))
        subprocess.run(["git", "-C", str(repo), "add", "--", CASCADE_REGISTRY_REL], check=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-m", "deliberate"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_a_plan_note_that_does_not_touch_the_registry_is_unaffected(
        self, repo: Path
    ) -> None:
        """The gate fires on one path; every other note commits as before."""
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n")
        (repo / ".claude" / "plans" / "note.md").write_text("# note\n")
        result = subprocess.run(
            ["git", "-C", str(repo), "add", "--", ".claude/plans/note.md"], check=True
        )
        assert (
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", "note"],
                capture_output=True,
                text=True,
            ).returncode
            == 0
        ), result

    def test_the_families_are_independent(self, repo: Path) -> None:
        """`max+1` is computed inside a family, never across the whole file."""
        self._arm(repo, "# reg\n- \u0422-4 \u2014 a\n- BT-9 \u2014 b\n- DIR-2 \u2014 c\n")
        for id_, ok in (("\u0422-5", True), ("BT-10", True), ("DIR-3", True),
                        ("\u0422-10", False), ("BT-5", False), ("DIR-10", False)):
            self._append(repo, self._mint_line(id_))
            result = self._commit(repo)
            assert (result.returncode == 0) is ok, (id_, result.returncode, result.stderr)
            git(repo, "reset", "--hard", "HEAD" if result.returncode else "HEAD~1")

    # ---- the gate and the tool must not drift -------------------------------

    @pytest.mark.parametrize(
        ("case", "corpus_key", "prefix"),
        CASCADE_CASES,
        ids=[f"{c}:{p}" for c, _k, p in CASCADE_CASES],
    )
    def test_the_gate_accepts_exactly_the_id_the_tool_hands_out(
        self, repo: Path, case: str, corpus_key: str, prefix: str
    ) -> None:
        """The agreement is pinned on the ANSWER, not on a shared spelling.

        Byte-identity between the two would not have been this claim. The two
        read DIFFERENT INPUTS — the tool a file in the working tree, the gate two
        blobs out of the index — and answer different questions (`max+1` versus
        `is this max+1`), and "sharing an implementation does not share a
        decision when the callers supply different inputs" is the correction
        CB-57's shared merge predicate had to make.

        So both are RUN, over one corpus, and the gate must accept the tool's
        answer and refuse both of its neighbours. Break either side's scanner —
        the Latin arm, the left boundary, `max+1` — and a corpus entry
        disagrees.
        """
        text = (
            (REPO_ROOT / CASCADE_REGISTRY_REL).read_text(encoding="utf-8")
            if corpus_key == "live"
            else CASCADE_CORPUS[corpus_key]
        )
        self._arm(repo, text)

        allocated = self._dry_run(repo, prefix)
        label, _, number = allocated.rpartition("-")
        n = int(number)

        self._append(repo, self._mint_line(allocated))
        assert self._commit(repo).returncode == 0, f"{case}: gate refused the tool's own id"
        git(repo, "reset", "--hard", "HEAD~1")

        for neighbour in (n + 1, n - 1):
            self._append(repo, self._mint_line(f"{label}-{neighbour}"))
            result = self._commit(repo)
            assert result.returncode != 0, f"{case}: gate accepted {label}-{neighbour}"
            git(repo, "reset", "--hard")


class TestCascadeMintGateDeletion:
    """CB-145: DELETING an allocation line returns a SPENT number to circulation.

    The multiset formula in the comment above `_cascade_mint_gate` — `new =
    allocation ids(staged) - allocation ids(HEAD)` — is ONE-DIRECTIONAL: a
    staged count below HEAD's clamps to nothing instead of going negative,
    so the `_seen` loop (which iterated only `_salloc`) never even visited an
    id whose last staged occurrence was gone. Deleting the allocation line
    carrying the family's highest number therefore made `new` empty, `_newtotal`
    stayed 0, and the gate returned 0 exactly as it does for a harmless edit or
    a mention-only note. `max` is computed over every occurrence of a family
    ANYWHERE IN HEAD, so lowering it by deleting the top occurrence means the
    next `tools/cascade-mint.sh --dry-run` hands the same number back out —
    the CB-137 collision, reachable by editing a file instead of racing one.

    Measured before this fix, in a throwaway repo with the unmodified hook:
    deleting the sole `Т-5` allocation line from a two-line registry
    committed at rc 0, and the tool's own next `--dry-run` then printed
    `Т-5` again. Every test below runs the REAL hook, like the rest of this
    class — a structural test would look for a line, and the defect it would
    have to find lives in what the code between two lines does NOT check.
    """

    # ---- must REFUSE (§4, items 1-4 of the brief) --------------------------

    def test_deleting_the_last_allocation_line_is_refused(self, repo: Path) -> None:
        """The exact shape CB-145 is about: the max-holding line goes."""
        TestCascadeMintGate._arm(repo, "# reg\n- Т-4 — a\n- Т-5 — b\n")
        path = repo / CASCADE_REGISTRY_REL
        path.write_text(
            path.read_text(encoding="utf-8").replace("- Т-5 — b\n", ""),
            encoding="utf-8",
        )
        result = TestCascadeMintGate._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "removes an allocation line" in result.stderr, result.stderr
        assert "Т-5" in result.stderr, result.stderr

        # The defect this closes, made concrete: force the deletion through
        # (the same escape hatch every other refusal in this gate has) and
        # watch the allocator's OWN tool re-issue the number that deletion
        # just freed. Before CB-145 the gate never reached the `--no-verify`
        # step at all, because it did not refuse in the first place.
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--no-verify", "-m", "forced deletion"],
            check=True,
            capture_output=True,
        )
        assert TestCascadeMintGate._dry_run(repo, "Т") == "Т-5"

    def test_deleting_a_middle_allocation_line_is_refused(self, repo: Path) -> None:
        """Not just the tail: the number stays spent wherever its line sits.

        A gate that only compared the CURRENT max against a deletion at the
        end would miss this — nothing about `max` changes when a middle line
        goes, but the number the deleted line held is exactly as reissuable.
        """
        TestCascadeMintGate._arm(
            repo, "# reg\n- Т-4 — a\n- Т-5 — b\n- Т-6 — c\n"
        )
        path = repo / CASCADE_REGISTRY_REL
        path.write_text(
            path.read_text(encoding="utf-8").replace("- Т-5 — b\n", ""),
            encoding="utf-8",
        )
        result = TestCascadeMintGate._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "Т-5" in result.stderr, result.stderr

    def test_deleting_a_line_of_a_different_family_is_refused(self, repo: Path) -> None:
        """Per family, not special-cased to Т (§4 item 3 of the brief)."""
        TestCascadeMintGate._arm(repo, "# reg\n- Т-4 — a\n- BT-9 — b\n")
        path = repo / CASCADE_REGISTRY_REL
        path.write_text(
            path.read_text(encoding="utf-8").replace("- BT-9 — b\n", ""),
            encoding="utf-8",
        )
        result = TestCascadeMintGate._commit(repo)
        assert result.returncode != 0, result.stdout
        assert "BT-9" in result.stderr, result.stderr

    def test_deleting_two_allocation_lines_at_once_is_refused(self, repo: Path) -> None:
        """Two deletions in one commit still refuse (§4 item 4 of the brief).

        The gate refuses on the FIRST id it finds with a lowered count, so
        this does not need its own counting logic — it is a corollary of the
        single-deletion check, not a new branch.
        """
        TestCascadeMintGate._arm(
            repo, "# reg\n- Т-4 — a\n- Т-5 — b\n- Т-6 — c\n"
        )
        path = repo / CASCADE_REGISTRY_REL
        text = path.read_text(encoding="utf-8")
        text = text.replace("- Т-5 — b\n", "").replace("- Т-6 — c\n", "")
        path.write_text(text, encoding="utf-8")
        result = TestCascadeMintGate._commit(repo)
        assert result.returncode != 0, result.stdout

    # ---- must keep PASSING (§3 of the brief: a false refusal here is worse) --

    def test_an_in_place_edit_still_passes(self, repo: Path) -> None:
        """The discriminator is COUNT per id, unchanged by rewriting text
        after the id — this is the case CB-145's form was chosen specifically
        not to break (§2 of the brief)."""
        TestCascadeMintGate._arm(repo, "# reg\n- Т-4 — unit, `OLD-BRIEF.md`\n")
        path = repo / CASCADE_REGISTRY_REL
        path.write_text(
            path.read_text(encoding="utf-8").replace("OLD-BRIEF.md", "NEW-BRIEF.md"),
            encoding="utf-8",
        )
        assert TestCascadeMintGate._commit(repo).returncode == 0

    def test_a_mention_only_note_still_passes(self, repo: Path) -> None:
        """A note that MENTIONS a number is not scanned in "alloc" mode at
        all, so it can never move either side of the comparison."""
        TestCascadeMintGate._arm(repo, "# reg\n- Т-4 — a\n")
        TestCascadeMintGate._append(
            repo,
            "- КОЛЛИЗИЯ: упом"
            "инание Т-4 в заме"
            "тке\n",
        )
        assert TestCascadeMintGate._commit(repo).returncode == 0

    def test_an_ordinary_mint_still_passes(self, repo: Path) -> None:
        """The real tool's own mint (§4 item 7 of the brief: run the tool, not
        an imitation of it)."""
        TestCascadeMintGate._arm(repo, "# reg\n- Т-4 — a\n")
        result = subprocess.run(
            ["bash", str(CASCADE_MINT), "--prefix", "Т", "--text", "ordinary mint"],
            capture_output=True,
            text=True,
            cwd=str(repo),
        )
        assert result.returncode == 0, result.stderr
        assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    def test_a_commit_not_touching_the_registry_is_unaffected(self, repo: Path) -> None:
        """§4 item 8 of the brief."""
        TestCascadeMintGate._arm(repo, "# reg\n- Т-4 — a\n")
        (repo / ".claude" / "plans" / "unrelated-note.md").write_text("# n\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", ".claude/plans/unrelated-note.md"],
            check=True,
        )
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "unrelated"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    # ---- §4.9: the fix must not refuse a transition that really happened -----

    def test_the_check_only_fires_on_a_lowered_count_not_on_any_edit(self, repo: Path) -> None:
        """Direct pin on the discriminator, since §4.9's full-history replay
        (run by hand against this fix — see the executor's record below) is
        not itself encoded as a test: replaying every transition in
        `.claude/plans/CASCADE-IDS.md`'s real history is a one-time oracle
        check on THIS fix, not a standing property of an ever-growing corpus,
        and the pre-existing refusals it surfaced (multi-line historical
        mints that predate the gate itself) are independent of this branch —
        confirmed by replaying the SAME history against the unmodified gate
        and getting the identical refusal set. What IS a standing property,
        and what this test pins instead, is the shape of the discriminator:
        touching a line without changing any id's COUNT never trips the new
        branch, no matter how much text around the id changes.
        """
        TestCascadeMintGate._arm(
            repo,
            "# reg\n- Т-4 — first text, `A.md`\n"
            "- Т-5 — second text, `B.md`\n"
            "- BT-1 — third text, `C.md`\n",
        )
        path = repo / CASCADE_REGISTRY_REL
        text = path.read_text(encoding="utf-8")
        text = (
            text.replace("first text, `A.md`", "renamed, `A2.md`")
            .replace("second text, `B.md`", "renamed, `B2.md`")
            .replace("third text, `C.md`", "renamed, `C2.md`")
        )
        path.write_text(text, encoding="utf-8")
        assert TestCascadeMintGate._commit(repo).returncode == 0


class TestCascadeMintGateReachabilityRecord:
    """CB-150 / T-65: the registry header must keep RECORDING the reachability
    measurement, not just have carried it once. This is a text pin, not a
    behavioural one — T-65's whole point was that the gate's BEHAVIOUR stays
    untouched (see TestCascadeMintGate / TestCascadeMintGateDeletion above,
    still green) while the REACHABILITY of a false refusal gets measured and
    written down so the next reader does not reopen the question.

    Deliberately reads the real registry at REPO_ROOT, not a fixture: the
    record this pins IS the registry's own header, and a fixture copy could
    drift from it silently.
    """

    REGISTRY = REPO_ROOT / ".claude" / "plans" / "CASCADE-IDS.md"

    def test_the_reachability_record_is_present(self) -> None:
        text = self.REGISTRY.read_text(encoding="utf-8")
        # T-70/CB-150: the bare tokens "CB-150" and "Т-65" alone are VACUOUS —
        # both already occur in the registry before this record ever existed
        # (the CB-145 paragraph mentions CB-150, and the allocation-line list
        # further down mints and later corrects Т-65), so a check for the bare
        # tokens passes even with the record deleted. Anchor on text unique to
        # the record's own opening line instead.
        assert "CB-150 / Т-65" in text and "предикат описывает канон" in text, (
            "the CB-150/T-65 reachability record is missing from the registry "
            "header — deleting it re-opens a question that was already answered "
            "by measurement (see tools/pre-commit-hook.sh's CASCADE-IDS MINT "
            "GATE comment and .claude/plans/T65-cb150-probe.sh)."
        )
        assert "достижимых сегодня" in text and "НОЛЬ" in text, (
            "the record must state the reachability VERDICT (zero achievable "
            "false refusals today), not just mention the card id."
        )
        assert "baseline-SHA" in text or "Форма (b)" in text, (
            "the record must say form (b) was considered and explicitly "
            "rejected, with its reason — a rejected form left unnamed is "
            "cheaper to silently reintroduce than one that is on record."
        )

    def test_the_probe_script_is_persisted_and_executable(self) -> None:
        script = REPO_ROOT / ".claude" / "plans" / "T65-cb150-probe.sh"
        assert script.is_file(), (
            "the reproducible measurement script referenced by the registry "
            "header and the gate comment must actually be checked in beside "
            "them — a script only described in prose cannot be reproduced by "
            "someone else's hands."
        )
        assert os.access(script, os.X_OK), "the probe script is not executable"


class TestClaimsWiringStructure:
    """The scripts must reach the claims ledger, not flip a status field.

    Before CB-58 the "claim" was `codebugs update --status in_progress`: no
    holder, no exclusion, no release path. These pin the properties whose
    failure mode is silent — a script that still runs, still prints ticks, and
    quietly holds nothing.

    Every assertion reads `code_only(...)`: the scripts document the OLD
    spelling in order to explain why it is gone, so a raw text search reports
    the defect it just fixed.
    """

    SETUP = REPO_ROOT / "tools" / "worktree-setup.sh"
    FINISH = REPO_ROOT / "tools" / "worktree-finish.sh"

    def test_setup_claims_through_the_ledger(self) -> None:
        src = code_only(self.SETUP.read_text())
        assert "codebugs claim" in src, "setup does not claim through claims.py"
        assert "--holder-kind branch" in src, (
            "the claim carries no holder KIND — ownership is the full triple"
        )
        assert '--holder "${BRANCH_NAME}"' in src, "the claim does not name the branch as holder"
        assert '--repo "${REPO_ROOT}"' in src, "the claim carries no repo — the triple is partial"

    def test_setup_no_longer_flips_status_by_hand(self) -> None:
        """The anonymous status write is GONE, not merely supplemented.

        Leaving it would give two writers for one fact, and the status flip is
        already implied by the claim's projection (EntityKind.busy_status).
        """
        src = code_only(self.SETUP.read_text())
        assert "--status in_progress" not in src, (
            "setup still flips the status directly; the claim's projection does that"
        )

    def test_setup_claims_before_it_creates_anything(self) -> None:
        """A refusal must be free.

        Same rule as _guard_branch_type running before `worktree add`: if the
        claim came after, the losing side of a race would already own a branch
        and a directory by the time it was told no.
        """
        src = code_only(self.SETUP.read_text())
        assert src.index("codebugs claim") < src.index("worktree add"), (
            "the card is claimed after the worktree is created"
        )

    def test_setup_arms_an_exit_trap_that_releases(self) -> None:
        """Without this, an abort between claim and ready worktree leaks a
        claim naming a branch that does not exist — worse than the anonymous
        `in_progress` it replaces, because it looks authoritative."""
        src = code_only(self.SETUP.read_text())
        assert "trap _release_claims_on_abort EXIT" in src, "setup arms no release trap"
        assert "codebugs release" in src, "the trap has nothing to release with"

    def test_setup_disarms_the_trap_on_success(self) -> None:
        """An EXIT trap fires on success too.

        Left armed, every setup that WORKED would release its own claim on the
        way out — the failure this whole card is about, reintroduced inside its
        own fix.
        """
        src = code_only(self.SETUP.read_text())
        assert "trap - EXIT" in src, "the EXIT trap is never disarmed"
        assert src.index("trap _release_claims_on_abort EXIT") < src.index("trap - EXIT")

    def test_the_trap_is_disarmed_the_moment_the_worktree_exists(self) -> None:
        """The disarm POINT is ratified, not a matter of taste.

        FINAL-DESIGN.md §6.2(d) puts it immediately after `git worktree add`,
        and §6.4 gives the reason: a trap armed to the end of the script
        "releases ownership while a real worktree sits on disk", which is the
        worse failure. This script's first draft disarmed at [5/5] and was
        exactly that rejected alternative — reachable, because the verify step
        assigns from an unguarded command substitution that `set -e` turns into
        an abort.

        So: nothing that can FAIL may sit between the two. Asserted as "no
        command lines between them", which is stricter than the design needs and
        cannot drift into a judgement call about which commands are guarded.
        """
        src = code_only(self.SETUP.read_text())
        add_at = src.index("worktree add -b")
        disarm_at = src.index("trap - EXIT")
        assert add_at < disarm_at, "the trap is disarmed before the worktree exists"
        between = src[src.index("\n", add_at) : disarm_at]
        assert not [ln for ln in between.splitlines() if ln.strip()], (
            "a step sits between `git worktree add` and the disarm; if it fails, the "
            f"trap hands back a card whose worktree is on disk. Found: {between!r}"
        )

    def test_setup_refusal_on_held_by_other_is_fatal(self) -> None:
        """Exit 3 is the setup gate — the one tracker call allowed to be fatal."""
        src = code_only(self.SETUP.read_text())
        assert "exit 3" in src, "held_by_other does not refuse the setup"

    def test_finish_releases_what_the_branch_held(self) -> None:
        """The call must be INVOKED, not merely present.

        The first draft asserted `"codebugs release" in src` and a mutation
        that disabled the call by prefixing it with `:` — the shell no-op —
        left this whole class green. A command is a line that STARTS with it;
        anything else is a mention.
        """
        calls = invocations(self.FINISH.read_text(), "codebugs release")
        assert calls, "finish never INVOKES `codebugs release` (a mention is not a call)"
        call = "\n".join(calls)
        assert '--holder "${BRANCH}"' in call, "the release does not name the branch as holder"
        assert "--holder-kind branch" in call
        assert '--repo "${REPO_ROOT}"' in call

    def test_setup_release_trap_actually_invokes_release(self) -> None:
        """Same shape on the setup side, for the same reason."""
        calls = invocations(self.SETUP.read_text(), "codebugs release")
        assert calls, "setup's abort trap never INVOKES `codebugs release`"
        assert any('--repo "${REPO_ROOT}"' in c for c in calls)

    def test_setup_actually_invokes_claim(self) -> None:
        calls = invocations(self.SETUP.read_text(), "codebugs claim")
        assert calls, "setup never INVOKES `codebugs claim`"

    def test_finish_release_is_never_fatal(self) -> None:
        """The merge has already landed by then.

        A missing CLI or a contended tracker must never turn a successful
        integration into a failure — the asymmetry with setup's gate is the
        design, so it is pinned rather than left to a reader's goodwill.
        """
        src = code_only(self.FINISH.read_text())
        after = src[src.index("Releasing claims held by") :]
        release_block = after[: after.index("=== Integration complete ===")]
        for bad in ("exit 1", "exit 3", "|| exit"):
            assert bad not in release_block, (
                f"finish's release path can abort the run ({bad!r}); the merge has landed"
            )

    def test_finish_releases_only_after_the_merge_landed(self) -> None:
        src = code_only(self.FINISH.read_text())
        assert src.index("merge \"${BRANCH}\" --no-ff") < src.index("codebugs release"), (
            "finish releases the claim before the merge has landed"
        )

    def test_finish_does_not_opt_out_of_restore(self) -> None:
        """Restore is left ON deliberately (see the comment at the call site).

        A card still reading `in_progress` reads that way only because our claim
        projected it there; with the worktree gone, `open` is the honest state.
        The restore is a CAS, so it can never resurrect a card someone closed.
        """
        src = code_only(self.FINISH.read_text())
        assert "--no-restore" not in src


class TestClaimsWiringBehaviour:
    """Run worktree-setup.sh for real against a stub `codebugs`.

    Structural tests cannot tell a wired script from a working one. These use a
    throwaway repo with `tools/` copied in (so the script resolves that repo as
    its root) and a stub `codebugs` on PATH that records argv and returns
    scripted exit codes — the codes are the documented shell API, so scripting
    them is scripting the real contract.
    """

    @pytest.fixture
    def harness(self, repo: Path) -> dict:
        """A throwaway repo carrying its own copy of tools/, plus stub bins."""
        shutil.copytree(REPO_ROOT / "tools", repo / "tools")
        git(repo, "add", "tools")
        git(repo, "commit", "-m", "tools")

        bin_dir = repo / "stubbin"
        bin_dir.mkdir()
        log = repo / "codebugs.log"

        stub = bin_dir / "codebugs"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$*" >> "{log}"\n'
            'case "$1" in\n'
            '  claim)   exit "${STUB_CLAIM_RC:-0}" ;;\n'
            '  release) exit "${STUB_RELEASE_RC:-0}" ;;\n'
            '  who-holds) echo "holder: someone-else"; exit 0 ;;\n'
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        stub.chmod(0o755)

        # uv is stubbed to fail fast: the venv-priming and import-check steps
        # are guarded and only print a warning, and a real `uv sync` here would
        # cost seconds per test for nothing this class asserts.
        uv = bin_dir / "uv"
        uv.write_text("#!/usr/bin/env bash\nexit 1\n")
        uv.chmod(0o755)

        # Assert the fixture exists. TestKnownLimits shipped green for a week
        # because its fixture silently never installed anything.
        assert stub.is_file() and uv.is_file()
        return {"repo": repo, "bin": bin_dir, "log": log}

    def _run(self, harness: dict, branch: str, **env_over: str):
        env = {
            "PATH": f"{harness['bin']}:/usr/bin:/bin",
            "HOME": str(harness["repo"]),
            **env_over,
        }
        return subprocess.run(
            [str(harness["repo"] / "tools" / "worktree-setup.sh"), branch],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(harness["repo"]),
        )

    def _log(self, harness: dict) -> list[str]:
        p = harness["log"]
        return p.read_text().splitlines() if p.exists() else []

    def test_claim_is_issued_with_the_full_holder_triple(self, harness: dict) -> None:
        result = self._run(harness, "fix/cb-1-thing")
        assert result.returncode == 0, result.stderr
        claims = [ln for ln in self._log(harness) if ln.startswith("claim ")]
        assert len(claims) == 1, self._log(harness)
        assert "CB-1" in claims[0]
        assert "--holder fix/cb-1-thing" in claims[0]
        assert "--holder-kind branch" in claims[0]
        assert f"--repo {harness['repo']}" in claims[0]

    def test_held_by_other_refuses_the_setup_with_code_3(self, harness: dict) -> None:
        """The setup gate. Nothing may be created when someone else holds it."""
        result = self._run(harness, "fix/cb-2-thing", STUB_CLAIM_RC="3")
        assert result.returncode == 3, (result.returncode, result.stdout, result.stderr)
        assert not (harness["repo"] / ".worktrees" / "fix-cb-2-thing").exists(), (
            "a refused setup still created the worktree"
        )
        assert "holder: someone-else" in result.stderr, (
            "the refusal does not name the incumbent holder"
        )

    def test_entity_terminal_warns_but_proceeds(self, harness: dict) -> None:
        """A follow-up branch on a closed card is legitimate work."""
        result = self._run(harness, "fix/cb-3-thing", STUB_CLAIM_RC="4")
        assert result.returncode == 0, result.stderr
        assert (harness["repo"] / ".worktrees" / "fix-cb-3-thing").exists()
        assert "already resolved" in result.stdout

    def test_undetermined_is_retried_exactly_once_then_degrades(self, harness: dict) -> None:
        """`undetermined` means the DB could not answer, not that we lost.

        The primitive is an idempotent upsert, so re-issuing the identical call
        converges rather than double-claiming — but it may not loop forever.
        """
        result = self._run(harness, "fix/cb-4-thing", STUB_CLAIM_RC="5")
        assert result.returncode == 0, result.stderr
        claims = [ln for ln in self._log(harness) if ln.startswith("claim ")]
        assert len(claims) == 2, f"expected one retry, got {len(claims)}: {claims}"
        assert claims[0] == claims[1], "the retry must be the IDENTICAL call"
        assert (harness["repo"] / ".worktrees" / "fix-cb-4-thing").exists()

    def test_the_retry_waits_before_re_issuing(self) -> None:
        """An immediate retry meets the same contention it just lost to.

        `undetermined` means another connection holds the write lock; nothing
        has changed a microsecond later, so a retry with no wait is mostly
        decoration. FINAL-DESIGN.md §6.2(a) carries the sleep; my first draft
        did not. Structural, because asserting on wall-clock would make the
        suite slow AND flaky to buy nothing.
        """
        src = code_only(
            (REPO_ROOT / "tools" / "worktree-setup.sh").read_text()
        )
        retry_at = src.index('if [[ "${_rc}" -eq 5 ]]; then')
        second_call = src.index("codebugs claim", retry_at)
        assert "sleep" in src[retry_at:second_call], (
            "the undetermined retry re-issues with no wait"
        )

    def test_abort_after_claiming_runs_the_release_trap(self, harness: dict) -> None:
        """The acceptance criterion: setup → abort leaves no claim behind.

        The abort is forced by pre-creating the branch, so `git worktree add -b`
        fails AFTER the claim has been taken — the exact window the trap exists
        for.
        """
        git(harness["repo"], "branch", "fix/cb-5-thing")
        result = self._run(harness, "fix/cb-5-thing")
        assert result.returncode != 0, "the setup was expected to abort"
        log = self._log(harness)
        assert any(ln.startswith("claim ") for ln in log), "nothing was claimed; test is vacuous"
        releases = [ln for ln in log if ln.startswith("release ")]
        assert releases, f"the abort leaked the claim — no release issued: {log}"
        assert "CB-5" in releases[0]
        assert "--holder fix/cb-5-thing" in releases[0]

    def test_successful_setup_does_not_release(self, harness: dict) -> None:
        """The mirror of the test above, and it is not redundant.

        An EXIT trap fires on success too, so a missing `trap - EXIT` would
        release the claim of every setup that worked — and every other test
        here would still pass.
        """
        result = self._run(harness, "fix/cb-6-thing")
        assert result.returncode == 0, result.stderr
        assert not [ln for ln in self._log(harness) if ln.startswith("release ")], (
            "a successful setup released its own claim"
        )

    def test_no_claim_env_var_still_skips_the_tracker_entirely(self, harness: dict) -> None:
        """Pre-existing semantics the tests rely on; they must survive."""
        result = self._run(harness, "fix/cb-7-thing", CODEBUGS_SETUP_NO_CLAIM="1")
        assert result.returncode == 0, result.stderr
        assert self._log(harness) == [], "CODEBUGS_SETUP_NO_CLAIM did not suppress the claim"
        assert (harness["repo"] / ".worktrees" / "fix-cb-7-thing").exists()

    def test_missing_cli_degrades_loudly_rather_than_silently(self, harness: dict) -> None:
        """"Could not look" must never print the same as "nothing to do"."""
        (harness["bin"] / "codebugs").unlink()
        result = self._run(harness, "fix/cb-8-thing")
        assert result.returncode == 0, result.stderr
        assert "NOT claimed" in result.stdout
        assert (harness["repo"] / ".worktrees" / "fix-cb-8-thing").exists()

    def test_a_branch_naming_no_card_claims_nothing(self, harness: dict) -> None:
        result = self._run(harness, "refactor/no-card-here")
        assert result.returncode == 0, result.stderr
        assert self._log(harness) == []
        assert (harness["repo"] / ".worktrees" / "refactor-no-card-here").exists()

    def test_a_refused_setup_creates_literally_nothing(self, harness: dict) -> None:
        """Even `.worktrees/` itself.

        Found by reading the finished script end to end rather than section by
        section: `mkdir -p "${WORKTREE_DIR}"` still sat ABOVE the claim, so the
        gate that exists to make a refusal free left a directory behind. The
        cost is trivial and the principle is not — "claim before anything is
        created" is either true or it is a slogan.
        """
        result = self._run(harness, "fix/cb-9-thing", STUB_CLAIM_RC="3")
        assert result.returncode == 3
        assert not (harness["repo"] / ".worktrees").exists(), (
            "a refused setup created the .worktrees/ container"
        )


@pytest.fixture
def armed(repo: Path, tmp_path: Path) -> dict:
    """A throwaway repo carrying tools/, armed, with a stub `codebugs`.

    Module-level rather than class-local because two classes run the finish
    script end to end now — the CB-116 subject derivation and the CB-135
    interpreter guard — and a second copy of this setup is one drift away from
    the two of them disagreeing about what an armed repo is.

    It is a REAL uv project, and that is not decoration: since CB-135 the
    script refuses to integrate a tree whose interpreter it cannot compare with
    main's, so a fixture that is not a project could only ever exercise the
    refusal. `.python-version` is copied from this repo's own pin so the
    interpreter is certain to be installed, and main's `.venv` is materialised
    for real rather than stubbed — the guard's whole subject is what main
    ACTUALLY has, and a stub there would be a fixture asserting itself.
    """
    shutil.copytree(REPO_ROOT / "tools", repo / "tools")
    (repo / ".claude" / "plans").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(_FIXTURE_PYPROJECT)
    (repo / ".python-version").write_text((REPO_ROOT / ".python-version").read_text())
    # Without this the environments uv is about to build would show up as
    # untracked content and _guard_main_clean would refuse every finish here.
    (repo / ".gitignore").write_text(".venv/\nuv.lock\n")
    git(repo, "add", "tools", "pyproject.toml", ".python-version", ".gitignore")
    git(repo, "commit", "-m", "tools")
    subprocess.run(
        [str(repo / "tools" / "install-hooks.sh")],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    # Materialise main's environment, exactly as a developer's `uv sync` would.
    subprocess.run(
        ["uv", "run", "--extra", "dev", "python", "-c", "pass"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    assert (repo / ".venv" / "bin" / "python").exists()

    # Shadow the developer's real `codebugs` so the release step at the end
    # can never reach a real tracker. It is guarded and non-fatal either way,
    # but a test must not depend on that.
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    stub = bin_dir / "codebugs"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    assert stub.is_file()
    return {"repo": repo, "bin": bin_dir}


class TestMergeSubjectDerivation:
    """Run `worktree-finish.sh` for real and read the subject it lands (CB-116).

    Structural reading cannot tell a derivation that is written correctly from
    one that produces the right string, and the CB-116 defect was invisible to
    every structural test in this file: the script called `git log`, which is
    what a structural test would check for. So these land an actual merge in a
    throwaway repo and assert on `git log -1 --format=%s main`.

    Running the whole script is affordable ONLY because `--skip-checks` exists;
    the guards it disables are ruff and pytest, not the safety guards, so the
    gate wiring these tests traverse is the real one.

    COMMIT DATES ARE SET EXPLICITLY, and that is load-bearing rather than
    tidiness: the defect is `git log`'s reverse-CHRONOLOGICAL ordering picking
    main's newer commit out of the post-forward-merge tip. With every commit
    inside one second git falls back to topology and the defect does not
    reproduce at all — the first draft of this fixture was green against the
    unfixed script for exactly that reason.
    """

    BRANCH_FIRST = "fix(cb-999): THE BRANCH'S OWN WORK, first commit"
    BRANCH_LAST = "refactor(cb-999): close the altitude findings"
    # Names OTHER.md because the commit-msg naming gate requires it — this
    # fixture models an ORDINARY, legitimate plan-note landing on main while a
    # branch is open, and an ordinary one now names its note. Reaching for
    # --no-verify here instead would have made the fixture model a state the
    # harness no longer permits, and quietly weakened a CB-116 regression test.
    FOREIGN = "docs(bt-9): FOREIGN plan note OTHER.md naming CB-777/778/779"

    @staticmethod
    def _commit(repo: Path, when: str, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GIT_AUTHOR_DATE": when,
                "GIT_COMMITTER_DATE": when,
            },
        )


    def _branch(self, armed: dict, name: str = "fix/cb-999-own-work") -> Path:
        repo = armed["repo"]
        wt = repo / ".worktrees" / name.replace("/", "-")
        git(repo, "worktree", "add", "-q", "-b", name, str(wt), "main")
        (wt / "feature.txt").write_text("work\n")
        git(wt, "add", "feature.txt")
        self._commit(wt, "2026-08-20T10:00:00Z", "commit", "--no-verify", "-m", self.BRANCH_FIRST)
        (wt / "feature.txt").write_text("work\nmore\n")
        self._commit(wt, "2026-08-20T10:05:00Z", "commit", "--no-verify", "-am", self.BRANCH_LAST)
        return wt

    def _move_main(self, armed: dict) -> None:
        """Land a foreign plan note on main, STRICTLY LATER than the branch."""
        repo = armed["repo"]
        (repo / ".claude" / "plans" / "OTHER.md").write_text("someone else's note\n")
        git(repo, "add", ".claude/plans/OTHER.md")
        self._commit(repo, "2026-08-20T11:00:00Z", "commit", "-m", self.FOREIGN)

    def _finish(self, armed: dict, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(armed["repo"] / "tools" / "worktree-finish.sh"),
                "fix-cb-999-own-work",
                "--skip-checks",
                *extra,
            ],
            cwd=str(armed["repo"]),
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
        )

    def test_derived_subject_describes_the_branch_not_the_moved_main(self, armed: dict) -> None:
        """The CB-116 defect itself, end to end.

        Landing CB-111 produced `Merge fix/cb-111-…: docs(bt-4): … CB-113/114/115
        … (CB-111)` — a merge closing one card whose subject named three others,
        because [5/7] forward-merges main into the worktree and the derivation
        then read that polluted tip.
        """
        self._branch(armed)
        self._move_main(armed)
        result = self._finish(armed)
        assert result.returncode == 0, (result.stdout, result.stderr)

        subject = git(armed["repo"], "log", "-1", "--format=%s", "main")
        assert self.FOREIGN not in subject, (
            f"the merge subject still describes main's work: {subject}"
        )
        assert "CB-777" not in subject, f"the merge names foreign cards: {subject}"
        assert subject == f"Merge fix/cb-999-own-work: {self.BRANCH_FIRST} (CB-999)", subject

        # And the merge really is the branch's work, not an empty ceremony.
        assert "feature.txt" in git(armed["repo"], "show", "--stat", "--format=", "main")

    def test_the_first_own_commit_wins_over_the_last(self, armed: dict) -> None:
        """The (b) half, separated from the (a) half so a mutant is localised.

        Restricting the population to `main..HEAD` already removes main's
        commits; choosing the FIRST of what remains is a second, independent
        decision. Measured over main's own first-parent line: of the 47
        integration merges whose branch carried two or more commits, the first
        commit's subject is closer to the message a human actually wrote in 38
        and the last in 7 — and five of those seven open with the extinct
        `wip(cb-NN): checkpoint before mutation`. Branches here END on review
        fixups, which describe the tail of an iteration rather than its subject.

        main deliberately does NOT move here, so this test isolates first-vs-last
        from the defect the previous test covers.
        """
        self._branch(armed)
        result = self._finish(armed)
        assert result.returncode == 0, (result.stdout, result.stderr)

        subject = git(armed["repo"], "log", "-1", "--format=%s", "main")
        assert self.BRANCH_FIRST in subject, subject
        assert self.BRANCH_LAST not in subject, (
            f"the derivation took the branch's LAST commit, not its first: {subject}"
        )

    def test_an_explicit_merge_msg_still_wins(self, armed: dict) -> None:
        """Derivation is a default, never a policy."""
        self._branch(armed)
        self._move_main(armed)
        result = self._finish(armed, "--merge-msg", "Merge fix/cb-999-own-work: as typed (CB-999)")
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert (
            git(armed["repo"], "log", "-1", "--format=%s", "main")
            == "Merge fix/cb-999-own-work: as typed (CB-999)"
        )

    def test_a_branch_with_no_commit_of_its_own_is_refused_not_guessed(self, armed: dict) -> None:
        """The empty population: refuse, and refuse before anything is landed.

        Constructed with plumbing because it cannot be reached by ordinary use:
        `commit-tree -p main -p main^` makes a two-parent commit — two DISTINCT
        parents, see the comment below — carrying content that is in neither
        parent, so `_guard_nonempty_diff` passes (the content is real) while
        `main..HEAD --no-merges` is empty. That is the honest
        shape of the case — the content arrived through a merge commit rather
        than through a commit of this branch — and there is no subject that
        would be true, so the script asks for one instead of inventing it.
        """
        repo = armed["repo"]
        wt = repo / ".worktrees" / "fix-cb-999-own-work"
        git(repo, "worktree", "add", "-q", "-b", "fix/cb-999-own-work", str(wt), "main")
        (wt / "feature.txt").write_text("content in neither parent\n")
        git(wt, "add", "feature.txt")
        tree = git(wt, "write-tree")
        main_sha = git(repo, "rev-parse", "main")
        # Two DISTINCT parents, or git collapses them and the result is not a
        # merge at all — measured, `-p main -p main` yields a one-parent commit
        # that `--no-merges` then includes. Both parents are reachable from
        # main, so the range holds nothing but the merge commit itself.
        evil = git(
            wt, "commit-tree", tree, "-p", main_sha, "-p", f"{main_sha}^", "-m", "Merge: evil"
        )
        git(wt, "reset", "--hard", evil)
        assert git(wt, "log", "--no-merges", "--format=%s", f"{main_sha}..HEAD") == ""

        result = self._finish(armed)
        assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
        assert "no commit of its own" in result.stdout, result.stdout
        assert git(repo, "rev-parse", "main") == main_sha, "a refused finish still moved main"

    def test_a_refusal_hint_carries_the_message_the_run_was_given(self, armed: dict) -> None:
        """Form (d): the retry line must not drop `--merge-msg`.

        Exercised on the [5/7] conflict refusal because that one is reachable
        deterministically; the exit-13 refusals share the same `_retry_hint`
        function, and `TestHarnessIntegrity` pins that they call it. Without
        this, a refusal caused by main moving hands the operator a bare re-run —
        and main having moved is precisely what used to break the derivation,
        so the refusal path routed the operator into the defect. That is how the
        observed CB-111 subject was produced.
        """
        repo = armed["repo"]
        wt = self._branch(armed)
        # A conflicting edit to the same file, on main.
        (repo / "feature.txt").write_text("main's version\n")
        git(repo, "add", "feature.txt")
        self._commit(repo, "2026-08-20T11:00:00Z", "commit", "--no-verify", "-m", self.FOREIGN)

        typed = "Merge fix/cb-999-own-work: it's typed (CB-999)"
        result = self._finish(armed, "--merge-msg", typed)
        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "Conflicts" in result.stdout, result.stdout
        assert f"--merge-msg '{typed.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" in (
            result.stdout
        ), result.stdout
        assert wt.exists()

    def test_a_commit_absorbed_from_a_sibling_branch_does_not_win(self, armed: dict) -> None:
        """Restricting the RANGE is not enough — `--first-parent` is what decides.

        Found by two independent adversarial reviews and reproduced here: a
        branch that merges a SIBLING branch absorbs its commits into
        `main..HEAD`, and if the sibling's commits are OLDER (the ordinary case
        — the sibling was cut earlier) plain date order puts the sibling first.
        The range-only derivation then names the sibling's card in a merge that
        closes this one: the CB-116 symptom arriving through a second door, and
        on this shape it is WORSE than the `log -1` code it replaced, which at
        least picked a commit of this branch.

        `--first-parent` follows only the branch's own line, so every absorbed
        lineage — the sibling here, main at [5/7] — is skipped by construction
        rather than out-raced on timestamps.
        """
        repo = armed["repo"]

        # A sibling branch whose commit is STRICTLY OLDER than the branch's own.
        sib = repo / ".worktrees" / "fix-cb-800-sibling"
        git(repo, "worktree", "add", "-q", "-b", "fix/cb-800-sibling", str(sib), "main")
        (sib / "sibling.txt").write_text("sibling work\n")
        git(sib, "add", "sibling.txt")
        self._commit(
            sib, "2026-08-20T09:10:00Z", "commit", "--no-verify", "-m", "feat(cb-800): SIBLING work"
        )

        wt = self._branch(armed)
        self._commit(
            wt,
            "2026-08-20T10:10:00Z",
            "merge",
            "--no-ff",
            "--no-verify",
            "-m",
            "Merge fix/cb-800-sibling into fix/cb-999-own-work",
            "fix/cb-800-sibling",
        )
        self._move_main(armed)

        result = self._finish(armed)
        assert result.returncode == 0, (result.stdout, result.stderr)
        subject = git(repo, "log", "-1", "--format=%s", "main")
        assert "SIBLING" not in subject, (
            f"a commit absorbed from a sibling branch won the derivation: {subject}"
        )
        assert subject == f"Merge fix/cb-999-own-work: {self.BRANCH_FIRST} (CB-999)", subject

    def test_an_empty_first_subject_is_skipped_not_read_as_an_empty_population(
        self, armed: dict
    ) -> None:
        """A blank subject is not evidence that the branch carries no commits.

        `git commit --allow-empty-message` puts an empty line at the head of the
        population. Testing the FIRST LINE instead of the population made the
        refusal fire on a branch carrying several own commits, and print
        "carries no commit of its own" plus an explanation about merge commits —
        a false refusal that also asserts something untrue about the repository.
        """
        repo = armed["repo"]
        wt = repo / ".worktrees" / "fix-cb-999-own-work"
        git(repo, "worktree", "add", "-q", "-b", "fix/cb-999-own-work", str(wt), "main")
        (wt / "feature.txt").write_text("work\n")
        git(wt, "add", "feature.txt")
        self._commit(
            wt, "2026-08-20T10:00:00Z", "commit", "--no-verify", "--allow-empty-message", "-m", ""
        )
        (wt / "feature.txt").write_text("work\nmore\n")
        self._commit(wt, "2026-08-20T10:05:00Z", "commit", "--no-verify", "-am", self.BRANCH_LAST)

        result = self._finish(armed)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "no commit of its own" not in result.stdout, result.stdout
        assert (
            git(repo, "log", "-1", "--format=%s", "main")
            == f"Merge fix/cb-999-own-work: {self.BRANCH_LAST} (CB-999)"
        )


class TestGitSequencerPremises:
    """PIN THE PREMISE: git's sequencer reaches NEITHER hook on a CLEAN replay.

    WHY THIS EXISTS. The paragraph in CLAUDE.md that lists what the harness does
    NOT do acquired a sentence claiming that a clean `git cherry-pick` or
    `git revert` **does** run `commit-msg`, and that the plan-note naming rule
    therefore fires on it. It does not. Measured on git 2.53: the sequencer
    commits directly and skips `pre-commit` AND `commit-msg` alike, so a clean
    replay lands an unnamed plan note at exit 0. Only the CONFLICTED form, which
    the operator finishes with `git commit`, is gated.

    The claim was false, it sat in the section whose entire subject is "a gate
    described better than it behaves", and the suite stayed green because
    nothing pinned it. That is the failure mode this repository keeps paying
    for: a documented guarantee with no executable witness. These two tests are
    the witness. If a future git starts running the hook, they turn RED and the
    paragraph gets rewritten deliberately, instead of quietly becoming true.

    Note the direction of the assertion. It pins the LIMIT, not the feature —
    asserting that the gate does NOT fire. A test that only ever confirmed the
    happy path could not have caught this.
    """

    @staticmethod
    def _tracer(repo: Path, name: str) -> Path:
        """A hook that records its own invocation, so absence is provable."""
        common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        hooks = common / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        marker = repo / f"{name}.fired"
        hook = hooks / name
        hook.write_text(
            "#!/usr/bin/env bash\n" + f'echo fired >> "{marker}"\n', encoding="utf-8"
        )
        hook.chmod(0o755)
        return marker

    def test_premise_a_clean_cherry_pick_runs_neither_hook(self, repo: Path) -> None:
        msg_fired = self._tracer(repo, "commit-msg")
        pre_fired = self._tracer(repo, "pre-commit")

        git(repo, "checkout", "-b", "side")
        (repo / "x.txt").write_text("x\n")
        git(repo, "add", "x.txt")
        git(repo, "commit", "-m", "side work")
        git(repo, "checkout", "main")

        # Control FIRST: prove the tracers work at all, or the assertions below
        # are vacuous -- "the hook did not fire" and "the hook was never
        # installed" are indistinguishable without this.
        (repo / "control.txt").write_text("c\n")
        git(repo, "add", "control.txt")
        git(repo, "commit", "-m", "control")
        assert msg_fired.exists(), "commit-msg tracer never fired on an ordinary commit"
        assert pre_fired.exists(), "pre-commit tracer never fired on an ordinary commit"
        msg_fired.unlink()
        pre_fired.unlink()

        git(repo, "cherry-pick", "side")

        assert not msg_fired.exists(), (
            "git 2.53 ran commit-msg on a CLEAN cherry-pick. CLAUDE.md's list of what "
            "the harness does not do says the sequencer reaches neither hook; rewrite "
            "that paragraph deliberately rather than leaving it stale."
        )
        assert not pre_fired.exists(), "clean cherry-pick unexpectedly ran pre-commit"

    def test_premise_a_clean_revert_runs_neither_hook(self, repo: Path) -> None:
        msg_fired = self._tracer(repo, "commit-msg")
        pre_fired = self._tracer(repo, "pre-commit")

        (repo / "y.txt").write_text("y\n")
        git(repo, "add", "y.txt")
        git(repo, "commit", "-m", "to be reverted")
        assert msg_fired.exists() and pre_fired.exists(), "tracers not working"
        msg_fired.unlink()
        pre_fired.unlink()

        git(repo, "revert", "--no-edit", "HEAD")

        assert not msg_fired.exists(), (
            "git 2.53 ran commit-msg on a CLEAN revert -- see the sibling test."
        )
        assert not pre_fired.exists(), "clean revert unexpectedly ran pre-commit"


class TestCommitMsgNamingGate:
    """K-3 mechanised: a plan note landing on main must be NAMED in the message.

    THE INCIDENT. `.claude/plans/` is the one directory parallel sessions may
    commit to on main, and they do it constantly. One session ran `git add
    .claude/plans/` and swept in an UNTRACKED note belonging to another
    direction, which landed inside a commit whose message described unrelated
    work. The bytes survived; the PROVENANCE did not — an artefact of one
    direction now reads as part of another's iteration. The convention "add
    files by name, never by directory" was adopted and then broken again.

    THE MECHANISM. An author committing their own note names it without
    effort; an author who swept in a stranger's file cannot name it, because
    they do not know it is there. So the message is the discriminator.

    WHY commit-msg AND NOT pre-commit. Measured on git 2.53: at pre-commit
    time `COMMIT_EDITMSG` holds the PREVIOUS commit's message, and on the
    first commit of a clone it does not exist at all. A pre-commit
    implementation would therefore validate the wrong input — worse than
    absent, because it would look like a gate. `commit-msg` receives the final
    message as `$1`, after `-m`, `-F` and the editor have all had their say.
    `test_premise_*` below pin the two git behaviours this rests on.
    """

    # ---- fixtures -------------------------------------------------------

    @staticmethod
    def _hooks_dir(repo: Path) -> Path:
        d = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _install(self, repo: Path, *, also_pre_commit: bool = False) -> None:
        """Install commit-msg alone, so a failure names this hook and not its neighbour."""
        hooks = self._hooks_dir(repo)
        shutil.copy(REPO_ROOT / "tools" / "commit-msg-hook.sh", hooks / "commit-msg")
        (hooks / "commit-msg").chmod(0o755)
        if also_pre_commit:
            shutil.copy(REPO_ROOT / "tools" / "pre-commit-hook.sh", hooks / "pre-commit")
            (hooks / "pre-commit").chmod(0o755)

    @staticmethod
    def _plan(repo: Path, name: str, body: str = "note\n", *, subdir: str = "") -> str:
        """A plan note under `.claude/plans/`, or (CB-266) a daily brief under
        `.claude/plans/briefs/` when `subdir="briefs"`."""
        d = repo / ".claude" / "plans" / subdir if subdir else repo / ".claude" / "plans"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body, encoding="utf-8")
        rel = f"{subdir}/{name}" if subdir else name
        return f".claude/plans/{rel}"

    @staticmethod
    def _commit(
        repo: Path, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        full = dict(os.environ)
        if env:
            full.update(env)
        return subprocess.run(
            ["git", "-C", str(repo), "commit", *args],
            capture_output=True,
            text=True,
            env=full,
        )

    def _add_and_commit(
        self, repo: Path, message: str, *paths: str
    ) -> subprocess.CompletedProcess[str]:
        subprocess.run(["git", "-C", str(repo), "add", "--", *paths], check=True)
        return self._commit(repo, "-m", message)

    @staticmethod
    def _editor(tmp_path: Path, subject: str) -> str:
        """An editor that PREPENDS a subject and keeps git's template below it.

        Overwriting the file would delete the very template these tests exist
        to prove the hook ignores.
        """
        script = tmp_path / "fake-editor.sh"
        script.write_text(
            "#!/bin/sh\n"
            f'{{ printf "%s\\n" {subject!r}; cat "$1"; }} > "$1.new" && mv "$1.new" "$1"\n'
        )
        script.chmod(0o755)
        return str(script)

    # ---- the rule, both sides -------------------------------------------

    def test_a_named_plan_note_lands(self, repo: Path) -> None:
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        r = self._add_and_commit(repo, "docs(dir-1): handoff in T20-brief.md", p)
        assert r.returncode == 0, r.stderr

    def test_an_unnamed_plan_note_is_refused(self, repo: Path) -> None:
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        r = self._add_and_commit(repo, "docs(dir-1): handoff", p)
        assert r.returncode != 0
        assert "T20-brief.md" in r.stderr

    def test_a_named_daily_brief_lands(self, repo: Path) -> None:
        """CB-266 §3 п.3: the naming rule was widened alongside pre-commit's.

        Not a courtesy — `git add .claude/plans/` recursively stages
        `.claude/plans/briefs/` (measured; see the sibling comment in
        pre-commit-hook.sh), so once a brief can land at all it is exactly the
        kind of untracked file this hook exists to make someone name.
        """
        self._install(repo)
        p = self._plan(repo, "DAILY-2026-08-30.html", subdir="briefs")
        r = self._add_and_commit(repo, "docs(curator): DAILY-2026-08-30.html", p)
        assert r.returncode == 0, r.stderr

    def test_an_unnamed_daily_brief_is_refused(self, repo: Path) -> None:
        """CB-266's own oracle mutant #4: a brief the message does not name."""
        self._install(repo)
        p = self._plan(repo, "DAILY-2026-08-30.html", subdir="briefs")
        r = self._add_and_commit(repo, "docs(curator): daily brief", p)
        assert r.returncode != 0
        assert "DAILY-2026-08-30.html" in r.stderr

    def test_the_incident_shape_two_notes_only_one_named(self, repo: Path) -> None:
        """The literal failure this unit mechanises.

        A session commits its own note and `git add .claude/plans/` drags a
        stranger's along. The message names the one the author knows about.
        """
        self._install(repo)
        mine = self._plan(repo, "DIR-1-handoff.md")
        theirs = self._plan(repo, "DIR-2-BT4-review.md")
        r = self._add_and_commit(repo, "docs(dir-1): DIR-1-handoff.md", mine, theirs)
        assert r.returncode != 0
        assert "DIR-2-BT4-review.md" in r.stderr
        assert "DIR-1-handoff.md" not in r.stderr.split("Deliberate exception")[0].replace(
            "DIR-2-BT4-review.md", ""
        )

    def test_the_full_path_counts_as_naming_it(self, repo: Path) -> None:
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        r = self._add_and_commit(repo, "docs: update .claude/plans/T20-brief.md", p)
        assert r.returncode == 0, r.stderr

    def test_naming_the_DIRECTORY_is_not_naming_the_file(self, repo: Path) -> None:
        """Trap 1: the naive substring check passes the very message it must catch.

        `docs: правки в .claude/plans/` mentions the path prefix and nothing
        else — it is exactly what a sweeping commit looks like.
        """
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        r = self._add_and_commit(repo, "docs: правки в .claude/plans/", p)
        assert r.returncode != 0
        assert "T20-brief.md" in r.stderr

    def test_a_longer_named_file_does_not_launder_a_shorter_one(self, repo: Path) -> None:
        """The real substring trap: `plan.md` is a substring of `my-plan.md`.

        A plain `case $msg in *$base*` passes here, and the swept-in file is
        precisely the one nobody wrote down.
        """
        self._install(repo)
        swept = self._plan(repo, "plan.md")
        mine = self._plan(repo, "my-plan.md")
        r = self._add_and_commit(repo, "docs: my-plan.md updated", mine, swept)
        assert r.returncode != 0
        assert "plan.md" in r.stderr

    def test_a_non_ascii_name_that_IS_named_is_allowed(self, repo: Path) -> None:
        """A false refusal here is as bad as a miss, and this repo has shipped
        the C-quoting bug twice: once refusing a legitimate note, once silently
        ACCEPTING a conflict marker. `core.quotePath=false` is the fix.
        """
        self._install(repo)
        p = self._plan(repo, "Т-20-заметка.md")
        r = self._add_and_commit(repo, "docs(dir-1): обновил Т-20-заметка.md по итогам", p)
        assert r.returncode == 0, r.stderr

    def test_a_non_ascii_name_that_is_NOT_named_is_refused(self, repo: Path) -> None:
        """The other side of the pair: quoting must not disable the rule either."""
        self._install(repo)
        p = self._plan(repo, "Т-20-заметка.md")
        r = self._add_and_commit(repo, "docs(dir-1): правки по итогам", p)
        assert r.returncode != 0

    def test_a_deleted_plan_note_must_also_be_named(self, repo: Path) -> None:
        """`git add <dir>` stages deletions too, so removal carries the same
        provenance risk as addition."""
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        self._add_and_commit(repo, "docs: add T20-brief.md", p)
        (repo / p).unlink()
        r = self._add_and_commit(repo, "docs: tidy up", p)
        assert r.returncode != 0
        assert "T20-brief.md" in r.stderr

    def test_a_newline_in_a_path_cannot_be_split_into_two(self, repo: Path) -> None:
        """Both hooks read the staged set LINE BY LINE, so a path carrying a
        newline would be a way to present one file as two innocent ones. It is
        not, and the reason is a git behaviour rather than anything here:
        `core.quotePath=false` stops git quoting non-ASCII BYTES but never
        control characters, so such a path arrives as ONE quoted line and the
        anchored filter rejects it. Pinned because the hooks depend on it.
        """
        self._install(repo, also_pre_commit=True)
        d = repo / ".claude" / "plans" / "x.md\n.claude" / "plans"
        d.mkdir(parents=True)
        (repo / ".claude" / "plans" / "x.md\n.claude" / "plans" / "y.md").write_text("p\n")
        subprocess.run(["git", "-C", str(repo), "add", "--", ".claude"], check=True)
        reported = git(repo, "-c", "core.quotePath=false", "diff", "--cached", "--name-only")
        assert len(reported.splitlines()) == 1, (
            f"git split a newline-bearing path across lines: {reported!r}"
        )
        assert reported.startswith('"'), reported
        assert self._commit(repo, "-m", "docs: x.md and y.md").returncode != 0

    def test_a_separator_in_the_name_cannot_launder_a_shorter_note(self, repo: Path) -> None:
        """A REPRODUCED bypass, found by cross-model review and closed here.

        `_is_boundary` treats an ASCII space as a separator, so with `a b.md`
        and `b.md` both staged and only `a b.md` named, the occurrence of
        `b.md` INSIDE `a b.md` is flanked by a space and the token end — two
        boundaries — and the stranger's note landed unnamed. Measured before
        the fix: rc=0, both files committed.
        """
        self._install(repo)
        mine = self._plan(repo, "a b.md")
        theirs = self._plan(repo, "b.md")
        r = self._add_and_commit(repo, "docs: my note a b.md", mine, theirs)
        assert r.returncode != 0

    def test_a_name_the_matcher_cannot_judge_is_refused_not_guessed(self, repo: Path) -> None:
        """The closure is BY CONSTRUCTION: the matcher and the admissible-name
        rule are one predicate, so a name containing a separator is refused
        outright rather than judged by a rule that cannot see it. Costs
        nothing today — 0 of the repo's 94 plan notes carry such a character.
        """
        self._install(repo)
        p = self._plan(repo, "a b.md")
        r = self._add_and_commit(repo, "docs: naming a b.md exactly", p)
        assert r.returncode != 0
        assert "cannot judge" in r.stderr

    def test_a_non_ascii_name_is_still_judgeable(self, repo: Path) -> None:
        """The pair for the test above: a non-ASCII BYTE is a NAME byte, so the
        new refusal must not catch the Cyrillic note names this repo uses.
        """
        self._install(repo)
        p = self._plan(repo, "Т-20-заметка.md")
        r = self._add_and_commit(repo, "docs: Т-20-заметка.md", p)
        assert r.returncode == 0, r.stderr

    # ---- fail closed: the hook must refuse when it cannot look ----------

    def test_an_empty_message_is_refused(self, repo: Path) -> None:
        """`--allow-empty-message` really does land a commit (measured), so an
        empty message is a reachable state and not a theoretical one."""
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        subprocess.run(["git", "-C", str(repo), "add", "--", p], check=True)
        r = self._commit(repo, "--allow-empty-message", "-m", "")
        assert r.returncode != 0

    def test_the_editor_TEMPLATE_does_not_satisfy_the_gate(self, repo: Path, tmp_path: Path) -> None:
        """Trap 9, and it is the one that would make this a gate that cannot fire.

        git's default template lists the staged paths as comment lines —
        `#\tnew file:   .claude/plans/T20-brief.md`. A hook that scans the raw
        message file therefore passes EVERY editor commit vacuously.
        """
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        subprocess.run(["git", "-C", str(repo), "add", "--", p], check=True)
        r = self._commit(repo, env={"GIT_EDITOR": self._editor(tmp_path, "docs: plan notes")})
        assert r.returncode != 0
        assert "T20-brief.md" in r.stderr

    def test_the_verbose_DIFF_does_not_satisfy_the_gate(self, repo: Path, tmp_path: Path) -> None:
        """Trap 10. `git commit -v` appends the diff below the scissors line,
        and every hunk header names the file. `git stripspace --strip-comments`
        does NOT remove it — the diff is not commented out — so the message
        must be truncated at the scissors before it is read.
        """
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        subprocess.run(["git", "-C", str(repo), "add", "--", p], check=True)
        r = self._commit(
            repo, "-v", env={"GIT_EDITOR": self._editor(tmp_path, "docs: plan notes")}
        )
        assert r.returncode != 0
        assert "T20-brief.md" in r.stderr

    def test_a_message_supplied_by_FILE_is_read(self, repo: Path, tmp_path: Path) -> None:
        """`-F` never touches an editor; the hook must still see the text."""
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        msg = tmp_path / "msg.txt"
        msg.write_text("docs: rewrote T20-brief.md end to end\n")
        subprocess.run(["git", "-C", str(repo), "add", "--", p], check=True)
        r = self._commit(repo, "-F", str(msg))
        assert r.returncode == 0, r.stderr

    def test_an_empty_MERGE_HEAD_is_refused(self, repo: Path) -> None:
        """The exemption must not become the hole — the shape CB-57 closed in
        pre-commit, arriving through the new hook's own door."""
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
        (git_dir / "MERGE_HEAD").write_text("")
        r = self._add_and_commit(repo, "docs: whatever", p)
        assert r.returncode != 0

    # ---- scope: main only, plan notes only, merges exempt ---------------

    def test_a_branch_is_not_governed(self, repo: Path) -> None:
        """Trap 4: on a branch there are no foreign untracked notes to sweep,
        so the rule would be pure friction."""
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/cb-1-work")
        p = self._plan(repo, "T20-brief.md")
        assert self._add_and_commit(repo, "wip", p).returncode == 0

    def test_a_commit_with_no_plan_note_is_not_governed(self, repo: Path) -> None:
        """pre-commit owns the allowlist; this hook must not double-refuse, and
        must not invent a rule for files it has no opinion about."""
        self._install(repo)
        (repo / "seed.txt").write_text("changed\n")
        assert self._add_and_commit(repo, "whatever", "seed.txt").returncode == 0

    def test_a_clean_merge_bringing_plan_notes_is_exempt(self, repo: Path) -> None:
        """The integration path. `worktree-finish.sh` writes its own merge
        subject, and a branch routinely carries plan notes; demanding they be
        named would refuse every finish that touched one.
        """
        self._install(repo)
        git(repo, "checkout", "-q", "-b", "fix/cb-1-work")
        p = self._plan(repo, "branch-note.md")
        self._add_and_commit(repo, "wip: branch-note.md", p)
        git(repo, "checkout", "-q", "main")
        r = subprocess.run(
            ["git", "-C", str(repo), "merge", "--no-ff", "-m", "Merge fix/cb-1-work: work", "fix/cb-1-work"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr

    def test_a_conflicted_merge_is_exempt(self, repo: Path) -> None:
        self._install(repo)
        p = self._plan(repo, "shared.md", "seed\n")
        self._add_and_commit(repo, "docs: shared.md", p)
        git(repo, "checkout", "-q", "-b", "fix/cb-1-work")
        self._plan(repo, "shared.md", "branch side\n")
        self._add_and_commit(repo, "wip: shared.md", p)
        git(repo, "checkout", "-q", "main")
        self._plan(repo, "shared.md", "main side\n")
        self._add_and_commit(repo, "docs: shared.md again", p)
        subprocess.run(
            ["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", "fix/cb-1-work"],
            capture_output=True,
        )
        self._plan(repo, "shared.md", "resolved\n")
        subprocess.run(["git", "-C", str(repo), "add", "--", p], check=True)
        r = self._commit(repo, "--no-edit")
        assert r.returncode == 0, r.stderr

    def test_no_verify_is_the_documented_escape(self, repo: Path) -> None:
        """CLAUDE.md: the hook stops the accident; a typed flag states an intent."""
        self._install(repo)
        p = self._plan(repo, "T20-brief.md")
        subprocess.run(["git", "-C", str(repo), "add", "--", p], check=True)
        assert self._commit(repo, "--no-verify", "-m", "docs: unnamed").returncode == 0

    def test_the_two_hooks_compose(self, repo: Path) -> None:
        """Installed together, the pair must still admit the ordinary flow and
        still refuse the sweep — a check that validates elements cannot
        validate their composition.
        """
        self._install(repo, also_pre_commit=True)
        mine = self._plan(repo, "DIR-1-handoff.md")
        assert self._add_and_commit(repo, "docs: DIR-1-handoff.md", mine).returncode == 0
        theirs = self._plan(repo, "DIR-2-review.md")
        assert self._add_and_commit(repo, "docs: more notes", theirs).returncode != 0

    # ---- premises this design rests on, pinned so a git upgrade turns red ----

    def test_premise_pre_commit_cannot_see_the_message(self, repo: Path) -> None:
        """Why the hypothesis's phase had to move.

        At pre-commit time COMMIT_EDITMSG holds the PREVIOUS commit's message
        (and on the very first commit of a clone it does not exist at all), so
        a pre-commit naming check reads an input unrelated to the commit being
        made. That is not a gate that fails open — it is a gate wired to the
        wrong signal, which can also REFUSE a correct commit. Worse than
        absent, because it looks like enforcement.
        """
        hooks = self._hooks_dir(repo)
        probe = hooks / "pre-commit"
        probe.write_text(
            "#!/usr/bin/env bash\n"
            'gd=$(git rev-parse --git-dir)\n'
            'printf "%s" "$(cat "$gd/COMMIT_EDITMSG" 2>/dev/null)" > "$gd/PROBE"\n'
            "exit 0\n"
        )
        probe.chmod(0o755)
        git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))

        (repo / "a.txt").write_text("a\n")
        self._add_and_commit(repo, "FIRST MESSAGE", "a.txt")
        assert (git_dir / "PROBE").read_text().strip() == "seed", (
            "pre-commit saw something other than the PREVIOUS message"
        )

        (repo / "b.txt").write_text("b\n")
        self._add_and_commit(repo, "SECOND MESSAGE", "b.txt")
        probed = (git_dir / "PROBE").read_text().strip()
        assert probed == "FIRST MESSAGE", probed
        assert "SECOND MESSAGE" not in probed, (
            "pre-commit can now see the message being written — re-read the phase argument"
        )

    def test_premise_merge_head_is_PRESENT_at_commit_msg_time(self, repo: Path) -> None:
        """The exemption's discriminator, and it differs from pre-merge-commit's.

        CLAUDE.md records that a CLEAN merge writes no MERGE_HEAD — true at
        `pre-merge-commit` time, which runs earlier. By `commit-msg` time git
        has written it, for clean and conflicted merges alike. That is what
        lets one condition exempt both.
        """
        hooks = self._hooks_dir(repo)
        probe = hooks / "commit-msg"
        probe.write_text(
            "#!/usr/bin/env bash\n"
            'gd=$(git rev-parse --git-dir)\n'
            '[[ -e "$gd/MERGE_HEAD" ]] && echo present > "$gd/PROBE" || echo absent > "$gd/PROBE"\n'
            "exit 0\n"
        )
        probe.chmod(0o755)
        git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))

        git(repo, "checkout", "-q", "-b", "fix/cb-1-work")
        (repo / "w.txt").write_text("w\n")
        self._add_and_commit(repo, "work", "w.txt")
        assert (git_dir / "PROBE").read_text().strip() == "absent"

        git(repo, "checkout", "-q", "main")
        subprocess.run(
            ["git", "-C", str(repo), "merge", "--no-ff", "-m", "Merge fix/cb-1-work: w", "fix/cb-1-work"],
            capture_output=True,
            check=True,
        )
        assert (git_dir / "PROBE").read_text().strip() == "present", (
            "a clean merge no longer writes MERGE_HEAD by commit-msg time — the "
            "exemption's discriminator is gone and every finish will be refused"
        )


# ---------------------------------------------------------------------------
# CB-135: the interpreter that ran the suite must be the interpreter main has.
# ---------------------------------------------------------------------------

_FIXTURE_PYPROJECT = """\
[project]
name = "t27-fixture"
version = "0.0.0"
requires-python = ">=3.11"

[project.optional-dependencies]
dev = []

[tool.uv]
package = false
"""

# Read with `python -c`, not `python -V`: `-V` writes "Python X.Y.Z" and older
# interpreters wrote it to stderr, while `uv run` writes its own progress
# ("Creating virtual environment at: .venv") to stderr on the very run that
# matters most — the one where the pin forces a rebuild. A bare version on
# stdout is the only channel neither of them shares.
_PROBE = 'import sys; print("%d.%d.%d" % sys.version_info[:3])'


def _uv_project(path: Path, pin: str | None) -> None:
    """A minimal virtual uv project — no build backend, no dependencies."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(_FIXTURE_PYPROJECT)
    if pin is not None:
        (path / ".python-version").write_text(pin + "\n")


def _fake_main_interpreter(root: Path, version: str | None, rc: int = 0) -> None:
    """Stand in for main's INSTALLED `.venv/bin/python`.

    A stub rather than a real venv because what is under test is the guard's
    reading of that answer, and a real second interpreter cannot be made to
    give a wrong one on demand.
    """
    bin_dir = root / ".venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "python"
    # UNLINK first. In a real venv this path is a SYMLINK to the interpreter,
    # so writing "through" it would edit the system python rather than the
    # venv's entry — it fails loudly here only because /usr/bin is read-only.
    exe.unlink(missing_ok=True)
    line = "" if version is None else f'echo "{version}"\n'
    exe.write_text(f"#!/usr/bin/env bash\n{line}exit {rc}\n")
    exe.chmod(0o755)


class TestInterpreterMatchesMain:
    """CB-135 — the gate ran the suite under a DIFFERENT python than main's.

    2026-08-22: a manager reported "1943 passed" from a worktree on 3.13.3
    while the same suite on the landed main under the documented command gave
    "1 failed, 1942 passed" on 3.14.4. The red existed on main BEFORE the merge
    and was invisible to every finish — a guard reporting clean because it
    looked at the wrong tree.

    Both sides are exercised, and so is every way the answer can be *missing*:
    a version this guard cannot determine must refuse, never pass. That is not
    a stylistic preference here — "could not look, so reported clean" is the
    exact defect the card is about.
    """

    @staticmethod
    def _env(**delta: str) -> dict:
        """The environment these cases run in: the PIN regime, explicitly.

        `UV_PYTHON` is stripped by default. It outranks `.python-version`, and
        since the guard now REFUSES when anything outranks the pin, a developer
        with it exported would turn every case in this class red for a reason
        none of them is about. The two cases that ARE about an override set it
        back deliberately.
        """
        env = {k: v for k, v in os.environ.items() if k != "UV_PYTHON"}
        env.update(delta)
        return env

    @classmethod
    def _guard(cls, wt: Path, main_root: Path, **delta: str) -> subprocess.CompletedProcess[str]:
        script = (
            f'source "{GUARDS}"\n'
            f"_guard_interpreter_matches_main '{wt}' '{main_root}'\n"
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=cls._env(**delta),
        )

    @pytest.fixture
    def trees(self, tmp_path: Path) -> dict:
        """A real uv project standing in for the worktree, plus a bare main."""
        wt = tmp_path / "wt"
        main_root = tmp_path / "main"
        main_root.mkdir()
        pin = (REPO_ROOT / ".python-version").read_text().strip()
        _uv_project(wt, pin)
        got = subprocess.run(
            ["uv", "run", "--extra", "dev", "python", "-c", _PROBE],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=True,
            env=self._env(),
        ).stdout.strip()
        assert re.fullmatch(r"\d+\.\d+\.\d+", got), got
        return {"wt": wt, "main": main_root, "version": got, "pin": pin}

    def test_premise_the_pin_file_decides_the_interpreter(self, trees: dict) -> None:
        """The whole design rests on `.python-version` being the single source.

        If a uv upgrade ever stops honouring it, the pin becomes decoration and
        the guard degrades into a coin toss between two trees that each chose
        for themselves. Pinned as a premise so that turns the suite red instead
        of leaving this file's docstrings quietly wrong.

        `UV_PYTHON` is removed for THIS measurement and deliberately left alone
        everywhere else in this class. It outranks the pin file — documented in
        CLAUDE.md, and the guard MUST honour it, since a developer who exports
        it is choosing the interpreter [6/7] will really use. But this test is
        about the pin, and reading it through an override measures the
        override. Found the hard way: an end-to-end probe that injected a
        divergence with `UV_PYTHON=3.13.3` turned this test red, which was the
        test being wrong about its own subject rather than the pin failing.
        """
        assert trees["version"] == trees["pin"]

    def test_agreeing_interpreters_pass(self, trees: dict) -> None:
        _fake_main_interpreter(trees["main"], trees["version"])
        r = self._guard(trees["wt"], trees["main"])
        assert r.returncode == 0, r.stderr

    def test_divergent_interpreters_refuse(self, trees: dict) -> None:
        _fake_main_interpreter(trees["main"], "3.0.1")
        r = self._guard(trees["wt"], trees["main"])
        assert r.returncode == 14, (r.returncode, r.stderr)

    def test_the_refusal_names_both_versions_and_a_repair_command(self, trees: dict) -> None:
        """A gate with no way out is a wall, not a diagnostic.

        The repair has to work for BOTH shapes of this failure: main merely
        stale against its own pin, and a branch that CHANGES the pin — where
        `uv sync` alone would re-read main's OLD `.python-version` and put the
        old interpreter straight back. `UV_PYTHON=` outranks the pin file
        (measured), so one command covers both.
        """
        _fake_main_interpreter(trees["main"], "3.0.1")
        r = self._guard(trees["wt"], trees["main"])
        assert trees["version"] in r.stderr
        assert "3.0.1" in r.stderr
        assert "UV_PYTHON" in r.stderr
        assert "uv sync --extra dev" in r.stderr

    def test_main_with_no_venv_at_all_refuses(self, trees: dict) -> None:
        """Fail-closed: no interpreter to compare is not 'the same interpreter'."""
        r = self._guard(trees["wt"], trees["main"])
        assert r.returncode == 14, (r.returncode, r.stderr)

    def test_main_interpreter_failing_refuses(self, trees: dict) -> None:
        _fake_main_interpreter(trees["main"], trees["version"], rc=3)
        r = self._guard(trees["wt"], trees["main"])
        assert r.returncode == 14, (r.returncode, r.stderr)

    def test_main_interpreter_printing_nothing_refuses(self, trees: dict) -> None:
        """rc 0 and an empty answer is the quietest form of 'could not look'."""
        _fake_main_interpreter(trees["main"], None)
        r = self._guard(trees["wt"], trees["main"])
        assert r.returncode == 14, (r.returncode, r.stderr)

    def test_main_interpreter_printing_garbage_refuses(self, trees: dict) -> None:
        """An unparseable answer must not be compared as an opaque string.

        Two trees both answering `banana` would otherwise 'agree'.
        """
        _fake_main_interpreter(trees["main"], "banana")
        r = self._guard(trees["wt"], trees["main"])
        assert r.returncode == 14, (r.returncode, r.stderr)

    def test_worktree_without_its_own_project_file_refuses(self, tmp_path: Path) -> None:
        """The subtlest way this guard could report a false agreement.

        `uv run` does NOT fail outside a project — it walks UP until it finds
        one, and every worktree lives inside the repo at `.worktrees/<slug>`.
        Measured from such a directory: uv answered with MAIN's interpreter and
        imported `codebugs` from MAIN's src/. So a worktree that lost its
        pyproject.toml would have this guard compare main against main, agree,
        and wave through exactly the divergence it exists to catch.

        The first draft of this test asserted `uv run` would fail there and was
        green against a guard that had no such check at all.
        """
        wt = tmp_path / "bare"
        wt.mkdir()
        (wt / ".python-version").write_text("3.14.4\n")
        main_root = tmp_path / "main"
        main_root.mkdir()
        _fake_main_interpreter(main_root, "3.14.4")
        r = self._guard(wt, main_root)
        assert r.returncode == 14, (r.returncode, r.stderr)
        assert "pyproject.toml" in r.stderr

    def test_uv_unreachable_refuses(self, trees: dict) -> None:
        """The tool that answers the question is itself part of the question.

        `uv` lives in ~/.local/bin here, so /usr/bin:/bin keeps git and bash
        working while removing exactly the binary under test.
        """
        _fake_main_interpreter(trees["main"], trees["version"])
        r = self._guard(trees["wt"], trees["main"], PATH="/usr/bin:/bin")
        assert r.returncode == 14, (r.returncode, r.stderr)

    def test_two_undeterminable_sides_refuse_rather_than_agree(self, trees: dict) -> None:
        """The one case where the version SANITY check is the only thing left.

        Every other fail-closed test here is satisfied by the two answers being
        UNEQUAL — an empty string does not match a real version, so the guard
        returns 14 whether or not it ever validated the shape. Neutering the
        validator therefore left the whole class green: measured, a mutant
        making `_interpreter_version_is_sane` return 0 unconditionally survived
        all fifteen tests in this file.

        It bites here, where BOTH probes fail: no `uv` on PATH and no `.venv`
        in main gives `""` on both sides, and `"" == ""` is agreement. That is
        "could not look, so reported clean" arriving through the comparison
        itself — the exact defect CB-135 is about, reconstituted inside its own
        fix. Not contrived either: a machine without uv and a clone that has
        never been synced is a fresh setup.
        """
        r = self._guard(trees["wt"], trees["main"], PATH="/usr/bin:/bin")
        assert r.returncode == 14, (r.returncode, r.stderr)

    def test_two_prefix_matching_non_versions_refuse_rather_than_agree(
        self, tmp_path: Path
    ) -> None:
        """CB-140: the state that survived the UV_PYTHON fix without a test.

        `test_two_undeterminable_sides_refuse_rather_than_agree` above was
        written to be the ONE case where the version-SHAPE check is all that
        stands between a pass and CB-135 recurring. It stopped doing that once
        the UV_PYTHON-outranks-the-pin check (this same class's HIGH finding,
        `test_an_override_that_outranks_the_pin_refuses`) existed alongside
        it — NOT because the two checks got reordered. The shape check on
        `wt_ver` still runs first, exactly where `_guards.sh` always put it;
        the pin check is unchanged further down. What changed is that an
        empty `wt_ver` is not just "not sane" — it is ALSO unequal to the pin
        and does not extend it with a dot, so the (unmoved) pin check refuses
        it too. With the shape check neutered by a mutant, execution simply
        falls through to that still-present pin check and gets the same
        exit 14 by a different route — measured, a mutant turning
        `_interpreter_version_is_sane` into `return 0` still leaves that test
        green, and left the whole 248-test suite green with it (CB-140).

        The state the pin check ALONE cannot catch — where only the shape
        check stands between this and CB-135 recurring — is a NON-version
        that PREFIX-MATCHES the pin. A bare pin of "3" accepts anything spelled
        "3." + more as if it were a legitimate patch release, so "3.0" clears
        the pin-outranking check by looking like one — while still failing
        the strict `X.Y.Z` shape `_interpreter_version_is_sane` demands, since
        it is missing its own patch component. With BOTH the worktree probe
        (`uv` itself is faked here to answer "3.0" without ever running
        python) and main's stub interpreter printing that same "3.0", the
        pin-outranking check finds nothing to object to and the final
        `wt_ver == main_ver` compares two copies of a string that was never a
        real interpreter version — exactly the shape the sanity check exists
        to refuse. Only the version-shape check can catch it, so a mutant
        that neuters it must turn this test red.
        """
        wt = tmp_path / "wt"
        _uv_project(wt, "3")
        main_root = tmp_path / "main"
        main_root.mkdir()

        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        fake_uv = fake_bin / "uv"
        fake_uv.write_text('#!/usr/bin/env bash\necho "3.0"\nexit 0\n')
        fake_uv.chmod(0o755)

        _fake_main_interpreter(main_root, "3.0")

        r = self._guard(wt, main_root, PATH=f"{fake_bin}:/usr/bin:/bin")
        assert r.returncode == 14, (r.returncode, r.stderr)
        # Not just ANY exit 14: it must be the SHAPE check's own message,
        # naming the non-version it refused. If the fake `uv` above ever
        # stopped running or stopped printing "3.0", this would still see an
        # empty `wt_ver` and exit 14 through the (unrelated) pin check —
        # quietly losing the exact discriminating power this test exists for
        # (Codex review, 2026-08-22).
        assert "cannot determine the interpreter" in r.stderr, r.stderr
        assert "Got: '3.0'" in r.stderr, r.stderr

    def test_a_tree_with_no_pin_file_refuses(self, tmp_path: Path) -> None:
        """The single source must EXIST, or it is a convention again.

        Without this the two trees could agree by luck — which is precisely the
        state that held before this card and produced the incident.
        """
        wt = tmp_path / "wt"
        _uv_project(wt, None)
        main_root = tmp_path / "main"
        main_root.mkdir()
        got = subprocess.run(
            ["uv", "run", "--extra", "dev", "python", "-c", _PROBE],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=True,
            env=self._env(),
        ).stdout.strip()
        _fake_main_interpreter(main_root, got)
        r = self._guard(wt, main_root)
        assert r.returncode == 14, (r.returncode, r.stderr)
        assert ".python-version" in r.stderr

    def test_an_override_that_outranks_the_pin_refuses(self, trees: dict) -> None:
        """The HIGH finding of the cross-model review, 2026-08-22.

        `UV_PYTHON` beats `.python-version`. With it exported, BOTH probes
        answer with the override, so they agree and a first draft of this guard
        passed — while the branch was landing a different pin that main would
        pick up on its next `uv run`. CB-135 reconstituted through the one
        mechanism this change documents, invisible to a check that only asked
        whether the pin file EXISTS.

        The main side is stubbed to the override's version deliberately: this
        must refuse even when the two interpreters genuinely DO match, because
        what is wrong is that the pin did not decide.
        """
        other = "3.13.3" if not trees["pin"].startswith("3.13") else "3.12.10"
        r = self._guard(trees["wt"], trees["main"], UV_PYTHON=other)
        assert r.returncode == 14, (r.returncode, r.stderr)
        assert "UV_PYTHON" in r.stderr
        # And it is not merely the missing .venv talking.
        _fake_main_interpreter(trees["main"], other)
        r = self._guard(trees["wt"], trees["main"], UV_PYTHON=other)
        assert r.returncode == 14, (r.returncode, r.stderr)
        assert trees["pin"] in r.stderr

    def test_a_pin_this_guard_cannot_compare_refuses(self, tmp_path: Path) -> None:
        """Fail-closed on a pin that is not a plain version.

        `.python-version` also accepts implementation and platform requests
        (`pypy@3.11`, a full `cpython-…-linux-…` triple). This guard compares
        version strings, so it says so rather than guessing what such a request
        resolves to.
        """
        wt = tmp_path / "wt"
        _uv_project(wt, None)
        (wt / ".python-version").write_text("pypy@3.11\n")
        main_root = tmp_path / "main"
        main_root.mkdir()
        _fake_main_interpreter(main_root, "3.14.4")
        r = self._guard(wt, main_root)
        assert r.returncode == 14, (r.returncode, r.stderr)
        assert "plain CPython version" in r.stderr

    def test_a_shared_venv_refuses_instead_of_agreeing_with_itself(
        self, trees: dict, tmp_path: Path
    ) -> None:
        """Cross-model review, 2026-08-22.

        Point main's `.venv` at the worktree's and the two sides of the
        comparison become one environment — it can only ever agree, and the
        worktree removal at the end of the finish would then leave main's link
        dangling. Same can-only-agree shape as the walk-up case above.

        The DIRECTORIES are compared, never the interpreters they resolve to:
        two honest venvs built from one system python both resolve to a single
        /usr/bin/pythonX.Y, so an interpreter-level test would refuse every
        ordinary case. `test_agreeing_interpreters_pass` is what holds that
        line.
        """
        (trees["main"] / ".venv").symlink_to(trees["wt"] / ".venv")
        r = self._guard(trees["wt"], trees["main"])
        assert r.returncode == 14, (r.returncode, r.stderr)
        assert "same directory" in r.stderr

    def test_the_repo_carries_the_pin(self) -> None:
        """The file this whole unit is about, asserted to exist and be a version."""
        pin = (REPO_ROOT / ".python-version").read_text().strip()
        assert re.fullmatch(r"\d+\.\d+\.\d+", pin), pin


class TestInterpreterGuardEndToEnd:
    """The SCRIPT refuses — not merely the guard when called by hand (CB-135).

    `TestGuardsAreActuallyInvoked` reads the source and asserts the call is
    there in the right phase; that is the same structural reading which, for
    CB-116, could not tell a derivation written correctly from one that
    produced the right string. So this runs `worktree-finish.sh` for real in a
    throwaway repo and reads its exit code.

    Every case here passes `--skip-checks`, deliberately: that flag is exactly
    the claim under test. It disables ruff and pytest, and this guard is not a
    check — it is what decides whether running them would have meant anything.
    """

    SLUG = "fix-cb-135-probe"
    BRANCH = "fix/cb-135-probe"

    def _branch(self, armed: dict) -> Path:
        repo = armed["repo"]
        wt = repo / ".worktrees" / self.SLUG
        git(repo, "worktree", "add", "-q", "-b", self.BRANCH, str(wt), "main")
        (wt / "feature.txt").write_text("work\n")
        git(wt, "add", "feature.txt")
        git(wt, "commit", "--no-verify", "-m", "fix(cb-135): the branch's own work")
        return wt

    def _finish(self, armed: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(armed["repo"] / "tools" / "worktree-finish.sh"),
                self.SLUG,
                "--skip-checks",
                "--merge-msg",
                f"Merge {self.BRANCH}: probe (CB-135)",
            ],
            cwd=str(armed["repo"]),
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
        )

    def test_a_matching_interpreter_lands(self, armed: dict) -> None:
        """The control, and it is what makes the refusals below discriminate.

        Without it a `14` from the cases beneath could equally come from a
        fixture that cannot finish at all — which is how a refusal test passes
        for the wrong reason.
        """
        self._branch(armed)
        r = self._finish(armed)
        assert r.returncode == 0, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        assert git(armed["repo"], "log", "-1", "--format=%s", "main").startswith("Merge ")

    def test_skip_checks_still_refuses_a_mismatch(self, armed: dict) -> None:
        self._branch(armed)
        _fake_main_interpreter(armed["repo"], "3.0.1")
        before = git(armed["repo"], "rev-parse", "main")
        r = self._finish(armed)
        assert r.returncode == 14, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        assert git(armed["repo"], "rev-parse", "main") == before, "the merge landed anyway"

    def test_main_without_an_environment_refuses(self, armed: dict) -> None:
        """Fail-closed, end to end: nothing to compare is not a match."""
        self._branch(armed)
        shutil.rmtree(armed["repo"] / ".venv")
        before = git(armed["repo"], "rev-parse", "main")
        r = self._finish(armed)
        assert r.returncode == 14, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        assert git(armed["repo"], "rev-parse", "main") == before


# ---------------------------------------------------------------------------
# CB-121 — shim bodies for TestPostMergeAlarm, kept at module level so the
# f-string that assembles the shim never has to escape `^{commit}`.
#
# Each body runs inside a transparent `git` wrapper: it looks at argv, may do
# something, and then falls through to `exec "$REAL" "$@"`. The wrapper is
# installed EARLIER on PATH than the real git, so every git the script (and its
# hooks) run passes through it.
# ---------------------------------------------------------------------------

# The integration merge is the only `git merge` in the finish that carries
# `--no-ff` (the [5/7] forward-merge is `merge <sha> --no-edit`), so it is
# identifiable without counting invocations. Both tokens are required, because
# `--no-ff` alone would also match a merge a future hook or helper ran.
#
# Landing a plan note on main right before it reproduces CB-121 exactly: main
# moved between the in-lock re-check and the merge, through the sanctioned
# level-(2) traffic that makes the window reachable in the first place. It goes
# through the hooks like any other plan note — the message NAMES the file,
# because K-3's commit-msg gate is armed in this fixture.
_SHIM_MOVE_MAIN = r"""
_is_merge=0; _is_noff=0
for _a in "$@"; do
    [[ "$_a" == "merge" ]] && _is_merge=1
    [[ "$_a" == "--no-ff" ]] && _is_noff=1
done
if (( _is_merge && _is_noff )) && [[ ! -e "$MARKER" ]]; then
    : > "$MARKER"
    printf 'landed in the window\n' > "$REPO/.claude/plans/INJECTED.md"
    "$REAL" -C "$REPO" add -- .claude/plans/INJECTED.md
    "$REAL" -C "$REPO" commit -q -m 'docs: INJECTED.md landing in the window'
fi
"""

# The branch-side half of the same window: the session that owns the worktree
# commits while the finish is running, so `git merge "${BRANCH}"` resolves the
# NAME to a head that was never tested.
_SHIM_MOVE_BRANCH = r"""
_is_merge=0; _is_noff=0
for _a in "$@"; do
    [[ "$_a" == "merge" ]] && _is_merge=1
    [[ "$_a" == "--no-ff" ]] && _is_noff=1
done
if (( _is_merge && _is_noff )) && [[ ! -e "$MARKER" ]]; then
    : > "$MARKER"
    printf 'in the window\n' >> "$WT/feature.txt"
    "$REAL" -C "$WT" commit -q --no-verify -am 'fix(cb-121): committed in the window'
fi
"""

# Cross-model review, round 1: main can also move AFTER the merge returns and
# BEFORE the alarm names the tip. Reading before `flock -u 9` stops another
# FINISH, and nothing else — plan-note writers hold a different lock or none.
# The tip is then a stranger's commit, and forcing it through the parent
# comparison would print a confident, wrong diagnosis.
_SHIM_MOVE_MAIN_AFTER_MERGE = r"""
if [[ "$*" == *'main^{commit}'* && ! -e "$MARKER" ]]; then
    : > "$MARKER"
    printf 'landed after the merge\n' > "$REPO/.claude/plans/AFTER.md"
    "$REAL" -C "$REPO" add -- .claude/plans/AFTER.md
    "$REAL" -C "$REPO" commit -q -m 'docs: AFTER.md landing after the merge'
fi
"""

# "Could not look" must not read as "clean". The parent read is the only site
# in the harness that asks git for `^@`, so failing exactly that invocation
# breaks the alarm's own read and nothing else. The spelling is load-bearing:
# if the script's rev spelling changes, the marker assertion goes red rather
# than the test silently stopping to exercise the fail-closed path.
_SHIM_BREAK_PARENT_READ = r"""
if [[ "$*" == *'^@'* ]]; then
    : > "$MARKER"
    echo "fatal: simulated - git cannot answer" >&2
    exit 128
fi
"""

# The quietest form of "could not look": exit 0 with an answer that is not an
# object name. `git rev-parse` really does echo an argument it does not
# recognise back at you and exit 0, which is how a test in this very file once
# built its fixture into a directory named after a mistyped argv token.
_SHIM_GARBAGE_TIP = r"""
if [[ "$*" == *'main^{commit}'* ]]; then
    : > "$MARKER"
    echo "banana"
    exit 0
fi
"""

# A git that is merely NOISY on stderr must not raise an alarm. Folding stderr
# into the captured answer would turn every `warning:` line into a parse
# failure, and the alarm would fire on an ordinary finish — a check failing
# because it could not parse is the mirror image of one reporting clean because
# it could not look, and it is the worse of the two here, because an alarm
# nobody believes is an alarm nobody reads.
_SHIM_NOISY_STDERR = r"""
if [[ "$*" == *'main^{commit}'* ]]; then
    : > "$MARKER"
    echo "warning: simulated noisy git on stderr" >&2
fi
"""

# Round-2 cross-model review, HIGH: a two-parent tip is SHAPE, not IDENTITY.
# An off-harness `--no-ff` merge landing on main in the moment after ours also
# has two parents, and reading its parents as ours produces a confident, wrong
# diagnosis. Fires on the alarm's own tip read, i.e. after our merge returned.
_SHIM_STRANGER_MERGE_AFTER = r"""
if [[ "$*" == *'main^{commit}'* && ! -e "$MARKER" ]]; then
    : > "$MARKER"
    "$REAL" -C "$REPO" branch -q stranger-side "main^2" 2>/dev/null || true
    "$REAL" -C "$REPO" worktree add -q --detach "$REPO/.stranger" "main^2" 2>/dev/null
    printf 'stranger\n' > "$REPO/.stranger/stranger.txt"
    "$REAL" -C "$REPO/.stranger" add -- stranger.txt
    "$REAL" -C "$REPO/.stranger" commit -q --no-verify -m 'stranger work'
    "$REAL" -C "$REPO" merge --no-verify --no-ff -q \
        -m 'Merge stranger' "$("$REAL" -C "$REPO/.stranger" rev-parse HEAD)"
fi
"""

# Break the last cleanup statement ONLY. Used to pin the other half of the
# trap's contract: with nothing to say it must leave the run's own status
# alone, or a failing cleanup would silently become a success.
_SHIM_BREAK_CLEANUP = r"""
if [[ "$1" == "-C" && "$2" == "$REPO" ]]; then
    for _a in "$@"; do
        if [[ "$_a" == "--oneline" ]]; then
            : > "$MARKER"
            echo "fatal: simulated cleanup failure" >&2
            exit 1
        fi
    done
fi
"""

# Move main in the window AND break the last cleanup statement, which is a
# `git -C REPO log --oneline -1 | sed` pipeline. Under `set -euo pipefail` that
# kills the script — so this is the case that proves the alarm is delivered by
# an EXIT trap and not by a statement the cleanup can preempt. Scoped to the
# REPO invocation: `[2/7] Latest commit` runs the same subcommand in the
# WORKTREE, and breaking that one would abort the run long before any merge.
_SHIM_MOVE_MAIN_AND_BREAK_CLEANUP = _SHIM_MOVE_MAIN + r"""
if [[ "$1" == "-C" && "$2" == "$REPO" ]]; then
    for _a in "$@"; do
        if [[ "$_a" == "--oneline" ]]; then
            echo "fatal: simulated cleanup failure" >&2
            exit 1
        fi
    done
fi
"""


class TestPostMergeAlarm:
    """CB-121 — the in-lock SHA re-check is a CHECK-THEN-ACT, and this is the alarm.

    THE DEFECT. Under the flock, `worktree-finish.sh` resolves three refs
    INDEPENDENTLY: `rev-parse main`, `rev-parse HEAD` in the worktree, and then
    `git merge "${BRANCH}"` — by NAME. Nothing carries a verified SHA into the
    merge and porcelain git has no `--expect-old-oid`, so main can move between
    the check and the merge. The flock serializes finishes against each other
    and nothing else, while this repo's ratified convention has level-(2)
    sessions committing plan notes to main continuously (and since 2026-08-22,
    `tools/cascade-mint.sh` does it automatically under a DIFFERENT lock). So
    CLAUDE.md's "The tested state is the landed state | exit 13" was a window
    described as an invariant.

    WHAT THIS IS NOT. It is not a gate. The merge step has already run by the
    time the alarm can look, so its outcome must never read as "the finish
    failed, re-run" — re-running after a landed merge is worse than the defect.
    Hence: all cleanup completes first, the block is loud, the exit code is a
    NEW one (15) meaning *landed, premise unconfirmed*, and the text forbids a
    re-run.

    HOW THESE TESTS INJECT. `TestMergeSubjectDerivation` proved that running the
    whole script end to end in a throwaway repo under `--skip-checks` is
    affordable and that the guards it traverses are the real ones, so this class
    reuses that shape rather than inventing new machinery. The only addition is
    a transparent `git` wrapper placed ahead of the real git on the PATH the
    fixture already prepends, which acts on one identified invocation and then
    execs the real thing. That is the honest reproduction: the state really does
    change inside the real window, in the real script.
    """

    SLUG = "fix-cb-121-probe"
    BRANCH = "fix/cb-121-probe"
    MERGE_MSG = "Merge fix/cb-121-probe: probe (CB-121)"

    # ---- fixtures -------------------------------------------------------

    def _branch(self, armed: dict) -> Path:
        repo = armed["repo"]
        wt = repo / ".worktrees" / self.SLUG
        git(repo, "worktree", "add", "-q", "-b", self.BRANCH, str(wt), "main")
        (wt / "feature.txt").write_text("work\n")
        git(wt, "add", "feature.txt")
        git(wt, "commit", "--no-verify", "-m", "fix(cb-121): the branch's own work")
        return wt

    def _shim(self, armed: dict, body: str) -> Path:
        """Install a transparent `git` ahead of the real one, and prove it exists.

        The fixture asserts itself deliberately: CLAUDE.md records a test in
        this very file that built its fixture into a directory named after a
        mistyped argv token, exercised nothing, and could never fail.
        """
        real = shutil.which("git")
        assert real, "no git on PATH to wrap"
        marker = armed["repo"] / ".shim-fired"
        shim = armed["bin"] / "git"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'REAL="{real}"\n'
            f'REPO="{armed["repo"]}"\n'
            f'WT="{armed["repo"] / ".worktrees" / self.SLUG}"\n'
            f'MARKER="{marker}"\n'
            f"{body}\n"
            'exec "$REAL" "$@"\n'
        )
        shim.chmod(0o755)
        assert shim.is_file() and os.access(shim, os.X_OK), shim
        return marker

    def _finish(self, armed: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(armed["repo"] / "tools" / "worktree-finish.sh"),
                self.SLUG,
                "--skip-checks",
                "--merge-msg",
                self.MERGE_MSG,
            ],
            cwd=str(armed["repo"]),
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
        )

    # ---- the two directions that make everything else discriminate ------

    def test_an_undisturbed_finish_is_silent_and_lands(self, armed: dict) -> None:
        """The control, and it is what makes every case below discriminate.

        Without it a 15 elsewhere could equally come from a fixture that cannot
        finish at all — which is how a refusal test passes for the wrong reason.
        It also pins the other half of the acceptance contract: an alarm that
        fired on an ordinary finish would be worse than none, because the exit
        code is non-zero and every caller would learn to ignore it.
        """
        wt = self._branch(armed)
        before = git(armed["repo"], "rev-parse", "main")
        r = self._finish(armed)
        assert r.returncode == 0, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        assert "POST-MERGE ALARM" not in r.stdout, r.stdout[-3000:]
        assert git(armed["repo"], "rev-parse", "main^1") == before
        assert not wt.exists(), "the worktree survived a clean finish"

    def test_main_moving_in_the_window_raises_the_alarm(self, armed: dict) -> None:
        """CB-121 itself, reproduced in the real window.

        A plan note lands on main between the in-lock re-check and the merge, so
        the merge's first parent is that note and not TESTED_MAIN. Before this
        change the script exited 0 and said "Integration complete", which is the
        success-shaped lie the rest of this harness exists to prevent.
        """
        wt = self._branch(armed)
        marker = self._shim(armed, _SHIM_MOVE_MAIN)
        tested_main = git(armed["repo"], "rev-parse", "main")

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 15, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])

        landed_p1 = git(armed["repo"], "rev-parse", "main^1")
        assert landed_p1 != tested_main, "the injection did not move main"
        # BOTH shas, in full: the operator's next step is `git show`.
        assert tested_main in r.stdout, r.stdout[-4000:]
        assert landed_p1 in r.stdout, r.stdout[-4000:]

        # The merge LANDED — that is the whole reason this is not a gate.
        assert git(armed["repo"], "rev-parse", "main^2") == git(
            armed["repo"], "rev-parse", self.BRANCH
        )

        # ALL cleanup ran first, and the alarm is the last thing on screen.
        assert not wt.exists(), "the alarm skipped the worktree removal"
        assert r.stdout.index("Integration complete") < r.stdout.index("POST-MERGE ALARM")
        assert "Releasing claims" in r.stdout, "the alarm skipped the claim release"

        # And it must not read as "re-run me".
        assert "DO NOT RE-RUN" in r.stdout, r.stdout[-4000:]
        assert "tools/worktree-finish.sh fix-cb-121-probe --merge-msg" not in r.stdout

    def test_the_branch_moving_in_the_window_raises_the_alarm(self, armed: dict) -> None:
        """The other half of the same premise, and the reason both parents are read.

        `git merge "${BRANCH}"` resolves the NAME. A session committing in its
        own worktree while the finish runs therefore lands a second parent that
        was never tested — narrower than the main-side half (it needs the owning
        session to commit mid-finish) but the identical check-then-act, and an
        alarm that read only the first parent would be describing itself better
        than it behaves.
        """
        self._branch(armed)
        marker = self._shim(armed, _SHIM_MOVE_BRANCH)
        tested_head = git(armed["repo"] / ".worktrees" / self.SLUG, "rev-parse", "HEAD")

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 15, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])
        landed_p2 = git(armed["repo"], "rev-parse", "main^2")
        assert landed_p2 != tested_head, "the injection did not move the branch"
        assert tested_head in r.stdout, r.stdout[-4000:]
        assert landed_p2 in r.stdout, r.stdout[-4000:]
        assert "SECOND PARENT" in r.stdout, r.stdout[-4000:]
        assert "DO NOT RE-RUN" in r.stdout

    # ---- the ways the alarm itself could lie ----------------------------

    def test_main_moving_after_the_merge_is_diagnosed_honestly(self, armed: dict) -> None:
        """Cross-model review round 1, HIGH: reading before the unlock is not enough.

        The unlock only holds off another FINISH. A plan-note writer holds a
        different lock or none, so main can move in the moment between the merge
        returning and the alarm naming the tip. The tip is then a stranger's
        one-parent commit. Forcing that through the parent comparison would
        print "something landed between the re-check and the merge" — a
        confident diagnosis of the wrong event. It gets its own verdict instead,
        and the verdict says what is actually known.
        """
        wt = self._branch(armed)
        marker = self._shim(armed, _SHIM_MOVE_MAIN_AFTER_MERGE)
        tested_main = git(armed["repo"], "rev-parse", "main")

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 15, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])
        # The merge itself was PERFECT — that is what makes the wrong diagnosis
        # a real cost rather than a nicety.
        assert git(armed["repo"], "rev-parse", "main^1^1") == tested_main
        assert "not the merge this run made" in r.stdout, r.stdout[-4000:]
        assert "USUALLY BENIGN" in r.stdout, (
            "an honest merge followed by a legitimate note was reported as damage"
        )
        assert "between the in-lock" not in r.stdout, (
            "the alarm blamed the pre-merge window for a post-merge commit"
        )
        assert not wt.exists()

    def test_a_strangers_merge_landing_after_ours_is_not_read_as_ours(
        self, armed: dict
    ) -> None:
        """Round-2 cross-model review, HIGH: parent COUNT is shape, not identity.

        A tip with two parents was accepted as this run's merge. An off-harness
        `--no-ff` merge landing on main in the moment after ours has two parents
        too, and its first parent is OUR merge — so the alarm compared a
        stranger's parents against TESTED_MAIN and told a confident story about
        the wrong event. `ORIG_HEAD` is what supplies identity: `git merge` sets
        it to the HEAD it merged into, which is our merge's first parent by
        construction, so a tip whose first parent is not `ORIG_HEAD` is not
        ours and gets a verdict that says exactly that.
        """
        self._branch(armed)
        marker = self._shim(armed, _SHIM_STRANGER_MERGE_AFTER)
        tested_main = git(armed["repo"], "rev-parse", "main")

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        # The stranger really did land a second merge on top of ours.
        assert len(git(armed["repo"], "rev-parse", "main^@").split()) == 2
        assert git(armed["repo"], "rev-parse", "main^1^1") == tested_main, (
            "the fixture did not build the shape this test is about"
        )
        assert r.returncode == 15, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])
        assert "not the merge this run made" in r.stdout, r.stdout[-4000:]
        assert "FIRST PARENT" not in r.stdout, (
            "a stranger's parents were reported as this run's"
        )

    def test_a_git_that_cannot_answer_is_not_read_as_clean(self, armed: dict) -> None:
        """Fail-closed. "Could not look" and "nothing wrong" must be distinct.

        This repository has paid for that distinction three times already — the
        bootstrap gate's `2>/dev/null || true`, the empty `MERGE_HEAD` arm, and
        `_guard_conflict_markers` — each time as a guard reporting clean because
        it could not look.
        """
        wt = self._branch(armed)
        marker = self._shim(armed, _SHIM_BREAK_PARENT_READ)

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 15, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])
        assert "could not look" in r.stdout.lower(), r.stdout[-4000:]
        assert "DO NOT RE-RUN" in r.stdout
        # Still not a gate: the merge landed and the cleanup completed.
        assert git(armed["repo"], "rev-parse", "main^2") == git(
            armed["repo"], "rev-parse", self.BRANCH
        )
        assert not wt.exists()

    def test_a_zero_status_non_sha_answer_is_not_read_as_clean(self, armed: dict) -> None:
        """rc 0 with garbage is the quietest "could not look" of the three.

        Without the shape check the exit code alone would accept it, and
        `banana != TESTED_MAIN` would then fire the FIRST-PARENT arm — an alarm
        with a specific, wrong story rather than an honest "I could not read
        this".
        """
        marker = self._shim(armed, _SHIM_GARBAGE_TIP)
        self._branch(armed)

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 15, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])
        assert "could not look" in r.stdout.lower(), r.stdout[-4000:]
        assert "FIRST PARENT" not in r.stdout, (
            "garbage was compared as if it were an object name"
        )

    def test_a_noisy_git_does_not_raise_a_false_alarm(self, armed: dict) -> None:
        """The alarm reads stdout only, and this is why.

        `git rev-parse` can print `warning:` lines on stderr for reasons that
        have nothing to do with the merge — an unreadable global config, a
        deprecation. Capturing them alongside the answer makes the answer
        unparseable, and an ordinary finish then exits 15. An alarm that fires
        on clean runs is worse than none, because every caller learns to ignore
        it; this is the one direction the fail-closed rule must NOT be applied
        to, and the rc is what still separates error from empty.
        """
        wt = self._branch(armed)
        marker = self._shim(armed, _SHIM_NOISY_STDERR)

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 0, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])
        assert "POST-MERGE ALARM" not in r.stdout, r.stdout[-4000:]
        assert not wt.exists()

    def test_a_cleanup_failure_cannot_swallow_the_alarm(self, armed: dict) -> None:
        """Cross-model review round 1, HIGH: `set -e` could silence a detected alarm.

        The verdict is computed under the lock and spoken at the end. With the
        block written as a trailing `if`, any failure in between — the cleanup's
        final `git log … | sed` pipeline, or any statement a future edit inserts
        — kills the script under `set -euo pipefail`, and the operator gets a
        landed merge reported as an ordinary failure with no code, no diagnosis
        and no "do not re-run". Delivering it from an EXIT trap makes that
        unrepresentable, which is the CB-41 answer rather than another rule
        someone has to remember at every insertion point.
        """
        marker = self._shim(armed, _SHIM_MOVE_MAIN_AND_BREAK_CLEANUP)
        self._branch(armed)
        tested_main = git(armed["repo"], "rev-parse", "main")

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 15, (r.returncode, r.stdout[-4000:], r.stderr[-4000:])
        assert "POST-MERGE ALARM" in r.stdout, r.stdout[-4000:]
        assert tested_main in r.stdout
        # And it says the run was already dying, rather than implying the
        # cleanup completed.
        assert "may not have completed" in r.stdout, r.stdout[-4000:]


    def test_a_silent_alarm_leaves_the_runs_own_status_alone(self, armed: dict) -> None:
        """The other half of the trap's contract, and it discriminates.

        An EXIT trap runs on every exit, including the ones that are nobody's
        business, so a trap that ends a run itself would turn a failing cleanup
        into a reported success — a success-shaped lie introduced by the very
        mechanism added to prevent one. Here the cleanup dies and main was NOT
        moved, so the alarm must stay silent and the non-zero status survive.

        The discriminating mutant is `exit 0` in the silent branch, NOT
        `return 0`: measured on bash 5.3, an EXIT trap's return value is
        discarded and the triggering status is what the shell exits with. Said
        here because the code carries `return "${rc}"`, which reads like the
        mechanism and is only the intent.
        """
        marker = self._shim(armed, _SHIM_BREAK_CLEANUP)
        self._branch(armed)

        r = self._finish(armed)

        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode != 0, "a failing cleanup was reported as success"
        assert r.returncode != 15, (
            "a failing cleanup was reported as a post-merge alarm"
        )
        assert "POST-MERGE ALARM" not in r.stdout, r.stdout[-3000:]


    def test_premise_merge_sets_orig_head_to_the_merges_first_parent(
        self, repo: Path
    ) -> None:
        """The identity premise, pinned so a git change turns the suite red.

        The alarm decides "is this tip my merge?" by comparing the tip's first
        parent with `ORIG_HEAD`. That works only because `git merge` sets
        ORIG_HEAD to the HEAD it merged into — which IS the merge's first
        parent. If a future git stops doing that, every finish would report
        `tip-not-ours` and the alarm would become noise; that must be a red
        suite, not a quiet degradation.
        """
        git(repo, "checkout", "-qb", "side")
        (repo / "b.txt").write_text("b\n")
        git(repo, "add", "b.txt")
        git(repo, "commit", "--no-verify", "-m", "side")
        git(repo, "checkout", "-q", "main")
        (repo / "c.txt").write_text("c\n")
        git(repo, "add", "c.txt")
        git(repo, "commit", "--no-verify", "-m", "main work")

        pre = git(repo, "rev-parse", "main")
        git(repo, "merge", "side", "--no-ff", "--no-verify", "-m", "merge")

        assert git(repo, "rev-parse", "ORIG_HEAD") == pre
        assert git(repo, "rev-parse", "main^1") == pre

    def test_premise_an_already_up_to_date_merge_cannot_masquerade(
        self, repo: Path
    ) -> None:
        """The one shape that could have defeated the identity check.

        If a no-op `git merge` left ORIG_HEAD STALE, a stale value could happen
        to equal the tip's first parent and a tip that is not ours would be
        accepted as ours. Measured: it sets ORIG_HEAD to the CURRENT tip
        instead, which can never equal that tip's own first parent — so the
        no-op falls into `tip-not-ours`, which is the honest answer.
        """
        git(repo, "checkout", "-qb", "side")
        (repo / "b.txt").write_text("b\n")
        git(repo, "add", "b.txt")
        git(repo, "commit", "--no-verify", "-m", "side")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "side", "--no-ff", "--no-verify", "-m", "merge")
        tip = git(repo, "rev-parse", "main")

        git(repo, "merge", "side", "--no-ff", "--no-verify", "-m", "again")

        assert git(repo, "rev-parse", "main") == tip, "the no-op merge moved main"
        assert git(repo, "rev-parse", "ORIG_HEAD") == tip
        assert git(repo, "rev-parse", "main^1") != tip


def _all_occurrences(haystack: str, needle: str) -> list[int]:
    """Every start offset of `needle`, so a pin can judge the right mention.

    A file may carry the same card id in an archive heading and in the prose the
    pin is really about; `str.index` would silently pick the first.
    """
    out, at = [], haystack.find(needle)
    while at != -1:
        out.append(at)
        at = haystack.find(needle, at + 1)
    return out


class TestPostMergeAlarmIsNotAGate:
    """The CATEGORY of the alarm, pinned in the source and in CLAUDE.md (CB-121).

    CLAUDE.md's own Workflow section records, at length, why
    `main-invariants.yml` is deliberately NOT in the enforcement table: a
    workflow cannot refuse a push, so listing it under "what is now
    mechanically enforced" with a "refuses with" column is a category error.
    The post-merge alarm has exactly that shape — it looks after the fact and
    cannot refuse anything — so the same rule binds it, and committing the
    error inside the edit that cites it would be the whole point missed.
    """

    FINISH = REPO_ROOT / "tools" / "worktree-finish.sh"
    CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

    @classmethod
    def _speak_body(cls) -> str:
        """The text of `_alarm_speak`, so assertions about the BLOCK are scoped.

        Reading the whole file instead would make "no re-run hint after the
        alarm" trivially false: the function is defined near the top, and the
        real refusals that legitimately print the hint come after it.
        """
        src = cls.FINISH.read_text()
        start = src.index("_alarm_speak() {")
        end = src.index("\n}\n", start)
        body = src[start:end]
        assert "POST-MERGE ALARM" in body, "the function no longer prints the block"
        return body

    def test_the_alarm_reads_the_tip_then_its_parents_before_the_unlock(self) -> None:
        """Phase, both bounds — and each one alone would be wrong.

        Before the merge there is nothing landed to look at. After `flock -u 9`
        another finish can take the lock and move main, so the alarm would start
        lying in precisely the way it exists to catch.
        """
        src = code_only(self.FINISH.read_text())
        merge_at = src.index('git -C "${REPO_ROOT}" merge "${BRANCH}" --no-ff')
        arm_at = src.index("trap _alarm_speak EXIT")
        tip_at = src.index('_ALARM_TIP=$(git')
        # The LAST unlock: the three before it are refusal paths, and anchoring
        # on the first one after the merge finds the merge-failed branch, where
        # nothing has landed and there is nothing for an alarm to read.
        unlock_at = src.rindex("flock -u 9\n")
        assert merge_at < arm_at < tip_at < unlock_at, (
            "arm the trap the instant the merge returns, then read before the unlock"
        )
        # And the arming must sit on the SUCCESS branch of the merge, not after
        # the `fi` — a merge that failed has landed nothing to alarm about.
        assert arm_at < src.index("Merge failed", merge_at)

    def test_the_parents_are_read_from_the_named_tip_not_from_main(self) -> None:
        """Cross-model review round 1, HIGH: three `main`-relative revs are not a snapshot.

        `rev-parse main^{commit} main^1^{commit} main^2^{commit}` resolves the
        ref three times inside one process — a tip from one commit can be
        reported beside parents from another. Naming the tip first fixes an
        OBJECT, and an object's parents are immutable.
        """
        src = code_only(self.FINISH.read_text())
        assert '"${_ALARM_TIP}^@"' in src, "the parents are not read from the named tip"
        assert "main^1" not in src and "main^2" not in src, (
            "a main-relative parent revision is back; that read cannot be coherent"
        )

    def test_the_tip_is_identified_by_orig_head_not_by_its_parent_count(self) -> None:
        """Round-2 cross-model review, HIGH: shape is not identity.

        Behavioural coverage is
        TestPostMergeAlarm::test_a_strangers_merge_landing_after_ours_is_not_read_as_ours;
        this pins the mechanism, because a regression could restore the
        count-only test and still pass every fixture where no stranger merges.
        """
        src = code_only(self.FINISH.read_text())
        assert "ORIG_HEAD^{commit}" in src, "the alarm no longer reads ORIG_HEAD"
        assert '[[ "${_ALARM_P1}" != "${_ALARM_ORIG}" ]]' in src, (
            "the tip is accepted without being identified as this run's merge"
        )

    def test_the_parent_read_ignores_replace_refs(self) -> None:
        """`^@` resolves through replace refs and grafts unless told not to.

        Without the flag the code's "an object's parents are immutable" claim
        is true of the object and false of the answer, which is the same class
        of overclaim this whole card exists to remove.
        """
        # LOGICAL lines: two of the three reads wrap, and the flag sits on the
        # first physical line while `rev-parse` sits on the second — a
        # line-wise filter reports the continuation as a bare read and the flag
        # as a read that does nothing.
        src = code_only(self.FINISH.read_text()).replace("\\\n", " ")
        reads = [ln for ln in src.splitlines() if "rev-parse" in ln and "_ALARM" in ln]
        assert len(reads) == 3, f"expected the tip, ORIG_HEAD and parent reads: {reads}"
        bare = [ln for ln in reads if "--no-replace-objects" not in ln]
        assert not bare, f"an alarm read resolves through replace refs: {bare}"

    def test_the_voice_is_defined_before_the_trap_names_it(self) -> None:
        """bash resolves a trap's command when it FIRES, not when it is set.

        With the definition below the arming, a failure in between leaves the
        trap resolving to nothing: exit 127 and no alarm, on a landed merge.
        """
        src = self.FINISH.read_text()
        assert src.index("_alarm_speak() {") < src.index("trap _alarm_speak EXIT")

    def test_the_alarm_is_delivered_by_an_exit_trap(self) -> None:
        """A trailing `if` is preemptable by `set -e`; a trap is not.

        The cleanup ends with a `git log … | sed` pipeline, and any statement a
        future edit inserts is another way to die between detection and speech.
        The behavioural half is
        TestPostMergeAlarm::test_a_cleanup_failure_cannot_swallow_the_alarm.
        """
        src = code_only(self.FINISH.read_text())
        assert "trap _alarm_speak EXIT" in src, "the alarm is not armed as an exit trap"

        # THIS REPLACED A COUNT, AND IT IS STRICTLY STRONGER — said plainly
        # because of what this edit IS: a change to a guard, made by the very
        # change that guard obstructed (CB-176). That is the shape which gets
        # waved through, so the case has to be on the page rather than in a
        # commit message.
        #
        # The line was `src.count("trap ") == 1`, whose stated intent was "a
        # second trap could replace this one silently". The landing-attempt
        # journal needs a trap of its own, armed before the first guard,
        # because every refusal that fires BEFORE the merge is otherwise
        # unrecordable — and bash has exactly one EXIT trap, so the alarm's
        # arming really does erase the journal's. What makes that legitimate is
        # that it is not SILENT: `_alarm_speak` calls the journal itself on
        # both of its paths, which test_the_alarm_hands_the_journal_its_own_
        # outcome pins. Measured, with that call removed: the journal loses
        # exactly the 0 and 15 rows and nothing else complains.
        #
        # A count was also weaker than it looked, in a way that has nothing to
        # do with this change. It cannot say WHICH traps, so it could not tell
        # a deliberate second one from a hostile one — and, measured, it passed
        # on `: "trap _alarm_speak EXIT"`, a no-op command carrying the string,
        # where the substring assertion above is satisfied and NO TRAP IS ARMED
        # AT ALL. That is precisely the state the count existed to refuse, and
        # it was green. An arming is a line that STARTS with `trap `, so that
        # is what is collected here.
        # THE LIST GREW AGAIN, FOR THE SECOND TIME AND BY THE SAME SHAPE OF
        # CHANGE (CB-237). It is written down here for the reason the CB-176
        # note above gives: this is an edit to a guard, made by the very change
        # that guard obstructed, so the case belongs on the page.
        #
        # CB-237 arms two SIGNAL traps beside the journal's EXIT trap, because
        # an EXIT trap fired by an asynchronous signal reads the `$?` of the
        # last completed command and therefore recorded a stopped finish as
        # `rc=0` — a LANDING. This assertion collects every line that STARTS
        # with `trap `, deliberately: narrowing it to EXIT armings would have
        # been the cheap way to keep it green, and it would have stopped seeing
        # a hostile `trap evil TERM` at the same time. So the expected list is
        # extended instead, which makes this test ALSO the pin that the two
        # signal traps exist and sit between the journal's arming and the
        # alarm's — the placement CB-237 depends on and the one an edit could
        # silently undo.
        # THE LIST GREW A THIRD TIME, BY THE SAME SHAPE OF CHANGE (CB-249), and
        # it is recorded here for the reason the two notes above give. CB-237
        # closed SIGINT and SIGTERM and DECLARED the remaining gap in the
        # script's own comment: SIGHUP and SIGPIPE reached the EXIT trap too and
        # were still recorded as `rc=0`, i.e. as LANDINGS. What held the line at
        # two was scope, not evidence, and CB-249 is the widening that comment
        # predicted — one line per signal, and nothing else about the mechanism
        # moves. The expected list is extended rather than loosened, for the
        # same reason as last time: narrowing this to EXIT armings, or to a
        # count, would keep it green and stop it seeing a hostile `trap evil
        # TERM` in the same stroke.
        armings = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("trap ")]
        assert armings == [
            "trap _journal_record EXIT",
            "trap 'exit 130' INT",
            "trap 'exit 143' TERM",
            "trap 'exit 129' HUP",
            "trap 'exit 141' PIPE",
            "trap _alarm_speak EXIT",
        ], f"the armed traps must be exactly these six, in this order: {armings}"
        # The journal's half of the ordering: armed before the FIRST guard, or
        # every pre-merge refusal goes unrecorded — silently, and in the
        # flattering direction. Anchored on the guard-invocation idiom rather
        # than on a guard's NAME: the structural tests in this file search the
        # raw source for a name plus a space, and naming one here would put a
        # match above the real call site (measured — that is how two of them
        # went red while this change was being written).
        # The alarm's half — after the merge, before the unlock — belongs to
        # test_the_alarm_reads_the_tip_then_its_parents_before_the_unlock and is
        # deliberately NOT duplicated here.
        assert src.index("trap _journal_record EXIT") < src.index("|| exit $?"), (
            "the journal's trap is armed after a guard could already have refused"
        )

        body = self._speak_body()
        assert "exit 15" in body, "the alarm no longer exits with its own code"
        # An EXIT trap runs on EVERY exit, so the silent branch must not carry
        # an `exit` of its own: measured on bash 5.3, a trap's RETURN value is
        # discarded and the triggering status survives, while an `exit` in the
        # trap replaces it. So this asserts the absence, which is the actual
        # mechanism, rather than the presence of `return "${rc}"`, which only
        # states the intent. The behavioural half is
        # TestPostMergeAlarm::test_a_silent_alarm_leaves_the_runs_own_status_alone.
        #
        # Sliced by LINE and over code only. The first draft of this cut the
        # body at the next occurrence of the substring "fi" — which is inside
        # the word "finish", two lines into the branch's own comment — so the
        # slice ended before any code and the assertion could not fail.
        lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
        start = next(i for i, ln in enumerate(lines) if 'if [[ -z "${_ALARM_WHY}" ]]' in ln)
        end = next(i for i, ln in enumerate(lines[start:], start) if ln.strip() == "fi")
        silent = lines[start : end + 1]
        assert len(silent) >= 2, silent
        assert not [ln for ln in silent if "exit" in ln], (
            f"the silent branch exits, so every ordinary finish takes its code: {silent}"
        )

    def test_the_alarm_hands_the_journal_its_own_outcome(self) -> None:
        """CB-176 — arming this trap REPLACES the journal's, so the alarm owes it a row.

        bash has exactly one EXIT trap and a second erases the first (measured
        on 5.3.9). The journal's trap is therefore gone from the moment the
        merge returns, and every outcome from there on reaches the journal only
        because this function calls it: the ordinary landing, whose status
        arrives on the silent branch, and the alarm's own `exit 15`.

        This is the half a COUNT of traps could never see, and it is why the
        count was replaced rather than merely relaxed — the count would have
        been equally green with the journal silently dropped on both paths.
        Measured with these two calls removed: the journal loses exactly the 0
        and the 15 rows, while every pre-merge refusal is still recorded, so
        nothing else in the suite notices.

        `15` and not the incoming status, deliberately: that is what the
        process actually exits with, and a 15 is a LANDING whose premise was
        unconfirmed — a different row from a clean 0, which is the whole reason
        this journal exists.
        """
        body = self._speak_body()
        calls = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("_journal_record")]
        assert calls == ['_journal_record "${rc}"', "_journal_record 15"], (
            f"the alarm must record the silent path's status and its own 15: {calls}"
        )

    def test_the_alarm_never_offers_a_re_run(self) -> None:
        """13 means nothing landed and asks for a re-run; 15 means the opposite.

        `_retry_hint` is the helper every refusal prints. The alarm must not
        reach it — and the count of its call sites must stay at the four
        refusals CB-116 enumerated, so this cannot drift into a fifth.
        """
        assert "_retry_hint" not in self._speak_body(), "the alarm offers a re-run"
        assert code_only(self.FINISH.read_text()).count("_retry_hint\n") == 4

    def test_the_exit_code_is_new_and_documented_where_the_codes_live(self) -> None:
        """15 must not be an existing guard code wearing a new meaning."""
        guards = (REPO_ROOT / "tools" / "_guards.sh").read_text()
        assert "return 15" not in guards, "15 has become a guard code as well"
        assert "15" in guards and "post-merge alarm" in guards, (
            "the new code is not described where the exit-code table lives"
        )

    def test_the_alarm_is_not_in_the_enforcement_table(self) -> None:
        """The category error this whole class exists to prevent.

        Rows in CLAUDE.md's enforcement table carry a "refuses with" cell. The
        alarm refuses nothing — by the time it can look, the merge has landed —
        so a row for it would assert the one thing that is false about it.
        """
        md = self.CLAUDE_MD.read_text()
        rows = [ln for ln in md.splitlines() if ln.startswith("| ")]
        assert rows, "the enforcement table has vanished"
        offenders = [r for r in rows if "exit 15" in r or "alarm" in r.lower()]
        assert not offenders, f"the post-merge alarm was listed as a gate: {offenders}"

    def test_the_narrowed_row_still_says_what_the_re_check_buys(self) -> None:
        """(d) — narrowing is not blurring.

        The old row claimed "The tested state is the landed state", which is a
        window described as an invariant. The new one must still make a
        CHECKABLE claim (the state matched when the check ran) rather than
        retreating into something unfalsifiable — and it must not claim more
        than the moment of the check either: the lock does not bind the
        plan-note writers, so "when the lock was taken" would be a second,
        smaller overclaim (cross-model review, round 1).
        """
        md = self.CLAUDE_MD.read_text()
        rows = [ln for ln in md.splitlines() if ln.startswith("| ")]
        recheck = [r for r in rows if "in-lock SHA re-check" in r]
        assert len(recheck) == 1, recheck
        row = recheck[0]
        assert "The tested state is the landed state" not in row, (
            "the unqualified claim CB-121 refuted is still in the table"
        )
        assert "re-check" in row and "exit 13" in row
        assert "when the lock was taken" not in row, (
            "the lock does not bind the writers that open this window"
        )

    def test_claude_md_names_the_window_and_calls_the_alarm_an_alarm(self) -> None:
        """Prose under the table, per the same treatment `main-invariants.yml` got.

        SURVIVED T-131's directive/depth split, and the split is why this
        docstring exists. An intermediate state of that unit had moved the
        REASONING — why the in-lock re-check is a check-then-act — out to
        `docs/claude-md-rationale/workflow.md`, leaving behind a root sentence
        ("The narrowed row is still a checkable claim") with nothing to refer
        to. The coherence pass moved the antecedent back, so the whole passage
        is in the root again and this pin asserts on the root exactly as it
        always did. What did NOT come back is the four-load-bearing-details
        list, which is rationale and now lives in the reference.

        The occurrence is searched for rather than taken with `.index()`, so a
        second mention of the card elsewhere in the file cannot make the window
        land on the wrong passage.
        """
        md = self.CLAUDE_MD.read_text()
        assert "CB-121" in md
        assert "exit 15" in md, (
            "the code an operator sees on a post-merge alarm left the root; "
            "refusal codes are directive and belong where every session loads them"
        )
        windows = [
            md[max(0, m - 2000) : m + 4000].lower()
            for m in _all_occurrences(md, "CB-121")
        ]
        assert any("alarm" in w for w in windows), (
            "CB-121 is mentioned but the alarm is not named"
        )
        assert any("alarm" in w and "check-then-act" in w for w in windows), (
            "no CB-121 passage names the alarm and the check-then-act together; "
            "the reasoning has been lost or split apart"
        )


# The journal's own injection: main moves BEFORE the in-lock re-check, so the
# finish refuses with 13 having landed nothing. Firing on the worktree's
# `rev-parse HEAD` puts the commit immediately after TESTED_MAIN is sampled,
# which is the earliest point at which the skew is real.
_SHIM_MOVE_MAIN_EARLY = r"""
_is_rp=0
for _a in "$@"; do
    [[ "$_a" == "rev-parse" ]] && _is_rp=1
done
if (( _is_rp )) && [[ "$*" == *"$WT"* && ! -e "$MARKER" ]]; then
    : > "$MARKER"
    printf 'landed before the re-check\n' > "$REPO/.claude/plans/EARLY.md"
    "$REAL" -C "$REPO" add -- .claude/plans/EARLY.md
    "$REAL" -C "$REPO" commit -q -m 'docs: EARLY.md landing before the re-check'
fi
"""


class TestLandingAttemptJournal:
    """CB-176 — every finish records its own outcome, so "how often does landing
    lose the race to a plan note" stops being an impression and becomes a number.

    The instrument is deliberately the weakest thing that can answer the
    question: one appended line per run, no output, no new verb, and NO exit
    code touched anywhere. It is FAIL-OPEN, against this repository's usual
    discipline, because an instrument able to refuse a landing would turn the
    measurement of CB-176 into a fresh instance of CB-176.
    """

    SLUG = "fix-cb-176-probe"
    BRANCH = "fix/cb-176-probe"
    MERGE_MSG = "Merge fix/cb-176-probe: probe (CB-176)"

    def _branch(self, armed: dict) -> Path:
        repo = armed["repo"]
        wt = repo / ".worktrees" / self.SLUG
        git(repo, "worktree", "add", "-q", "-b", self.BRANCH, str(wt), "main")
        (wt / "feature.txt").write_text("work\n")
        git(wt, "add", "feature.txt")
        git(wt, "commit", "--no-verify", "-m", "fix(cb-176): the branch's own work")
        return wt

    def _shim(self, armed: dict, body: str) -> Path:
        real = shutil.which("git")
        assert real, "no git on PATH to wrap"
        marker = armed["repo"] / ".shim-fired"
        shim = armed["bin"] / "git"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'REAL="{real}"\n'
            f'REPO="{armed["repo"]}"\n'
            f'WT="{armed["repo"] / ".worktrees" / self.SLUG}"\n'
            f'MARKER="{marker}"\n'
            f"{body}\n"
            'exec "$REAL" "$@"\n'
        )
        shim.chmod(0o755)
        assert shim.is_file() and os.access(shim, os.X_OK), shim
        return marker

    def _finish(self, armed: dict, slug: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(armed["repo"] / "tools" / "worktree-finish.sh"),
                slug or self.SLUG,
                "--skip-checks",
                "--merge-msg",
                self.MERGE_MSG,
            ],
            cwd=str(armed["repo"]),
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
        )

    def _rows(self, armed: dict) -> list[list[str]]:
        """The journal, split into fields. Asserts the SHAPE, not just presence.

        A line that lost a field would still 'contain' the exit code while the
        documented one-line reader mis-parses it, so every row is checked for
        exactly three fields here rather than at one call site.
        """
        log = armed["repo"] / ".worktrees" / "landing-attempts.log"
        if not log.exists():
            return []
        rows = [ln.split() for ln in log.read_text().splitlines() if ln.strip()]
        for row in rows:
            assert len(row) == 3, f"a journal row is not timestamp/slug/code: {row}"
            assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", row[0]), row
        return rows

    # ---- the four oracle rows -------------------------------------------

    def test_a_clean_landing_records_a_zero(self, armed: dict) -> None:
        """Without the 0 row there is no boundary between one landing and the next.

        "Attempts per landing" is computed at read time as lines-per-zero, so a
        journal that recorded only failures could not be divided into landings
        at all — it would report a pile of refusals with no denominator.
        """
        self._branch(armed)
        r = self._finish(armed)
        assert r.returncode == 0, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        assert self._rows(armed) == [[self._rows(armed)[0][0], self.SLUG, "0"]]

    def test_a_pre_merge_refusal_records_its_own_code(self, armed: dict) -> None:
        """exit 13 — the refusal CB-176 is actually about, in the real window."""
        self._branch(armed)
        marker = self._shim(armed, _SHIM_MOVE_MAIN_EARLY)
        r = self._finish(armed)
        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 13, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        rows = self._rows(armed)
        assert [row[1:] for row in rows] == [[self.SLUG, "13"]], rows

    def test_an_early_refusal_before_the_alarm_trap_is_recorded(self, armed: dict) -> None:
        """THE test that tells the right form from the naive one.

        exit 11 fires in [7/7], before the merge and therefore before the point
        where `trap _alarm_speak EXIT` is armed. A journal trap installed beside
        the alarm — the obvious place, since that is where the existing trap
        lives — would lose this row and every other pre-merge refusal, which is
        precisely the half of the measurement CB-176 needs. The loss would be
        silent AND in the flattering direction: fewer failed attempts recorded
        than happened.
        """
        self._branch(armed)
        # A TRACKED modification in main: _guard_main_clean reads
        # --untracked-files=no, so an untracked file would not refuse.
        (armed["repo"] / "pyproject.toml").write_text(
            (armed["repo"] / "pyproject.toml").read_text() + "\n# dirty\n"
        )
        r = self._finish(armed)
        assert r.returncode == 11, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        rows = self._rows(armed)
        assert [row[1:] for row in rows] == [[self.SLUG, "11"]], rows

    def test_the_alarm_path_records_fifteen_and_still_speaks(self, armed: dict) -> None:
        """BOTH, not one of the two — and 15 rather than the incoming status.

        Arming the alarm's trap REPLACES the journal's, so this is the path on
        which the journal exists only because `_alarm_speak` calls it. The code
        recorded is the one the process actually leaves with: a 15 is a LANDING
        whose premise was unconfirmed, and folding it into the clean 0 would
        make the journal disagree with the shell that ran it.
        """
        self._branch(armed)
        marker = self._shim(armed, _SHIM_MOVE_MAIN)
        r = self._finish(armed)
        assert marker.exists(), "the shim never fired — this test proved nothing"
        assert r.returncode == 15, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
        assert "POST-MERGE ALARM" in r.stdout, r.stdout[-3000:]
        rows = self._rows(armed)
        assert [row[1:] for row in rows] == [[self.SLUG, "15"]], rows

    # ---- fail-open, which is the whole licence for this to exist ---------

    def test_an_unwritable_journal_does_not_change_the_finish(self, armed: dict) -> None:
        """П5 — the instrument may lose its own data, never the operator's run.

        Measured on bash 5.3.9: an unguarded command failing INSIDE an EXIT trap
        under `set -euo pipefail` both truncates the trap and REWRITES the exit
        status to 1. So a careless journal would not merely drop a line, it
        would turn a clean landing into an unexplained failure — and turn the
        repeatable `exit 13` into a `1` the operator is told to treat as an
        error, sabotaging the very measurement it exists to take.
        """
        self._branch(armed)
        log = armed["repo"] / ".worktrees" / "landing-attempts.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("")
        log.chmod(0o000)
        try:
            r = self._finish(armed)
            assert r.returncode == 0, (r.returncode, r.stdout[-3000:], r.stderr[-3000:])
            assert "Integration complete" in r.stdout
            assert log.read_bytes() == b"" if os.access(log, os.W_OK) else True
        finally:
            log.chmod(0o644)
        assert log.read_text() == "", "the write was not actually refused"

    # ---- CB-237: a run STOPPED by a signal -------------------------------

    def _finish_stopped_by(self, armed: dict, signum: int) -> tuple[int, str]:
        """Run the REAL script and stop it with a signal BEFORE the merge.

        Two properties turn this from a race into a measurement, and both had to
        be established before the test could be written at all.

        THE WINDOW IS HELD OPEN BY THE INTEGRATION LOCK. This process holds
        `.worktrees/.integrate.lock` for the whole call, so once the script has
        announced `[7/7]` it cannot get past its own `flock -w 60 9`. Without
        that, the test would be signalling a script that may already have
        merged, and a green result would say nothing about the pre-merge phase
        it is meant to be about. It does not hang if the signal fails to arrive
        either: the lock wait expires after 60s and the script refuses with 1,
        which is a different row from the one asserted below.

        THE SIGNAL GOES TO THE PROCESS GROUP, NEVER TO THE SHELL ALONE. bash
        defers a trap until the current FOREGROUND command returns, so
        signalling the shell while it waits on `flock` is acted on only when
        that wait ends — measured at 19.65s against a 20s sleep, against 0.00s
        for the group. An operator's Ctrl-C and a supervisor's `kill` both
        signal the group, so this is the faithful shape and not a convenience.
        """
        repo = armed["repo"]
        wt_dir = repo / ".worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(wt_dir / ".integrate.lock", os.O_CREAT | os.O_WRONLY, 0o644)
        proc: subprocess.Popen[str] | None = None
        watchdog: threading.Timer | None = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            proc = subprocess.Popen(
                [
                    str(repo / "tools" / "worktree-finish.sh"),
                    self.SLUG,
                    "--skip-checks",
                    "--merge-msg",
                    self.MERGE_MSG,
                ],
                cwd=str(repo),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
                env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
            )
            # THE TWO READS OF THE SCRIPT'S STDOUT BELOW ARE THE ONLY UNBOUNDED
            # WAITS IN THIS HELPER, and this suite installs no global test
            # timeout, so a script that wedged before printing `[7/7]` would
            # hang the whole run rather than fail one test. The bound has to
            # come from outside the reads, because it cannot come from inside
            # them: killing the group closes the pipe, both reads return, and
            # the branch below reports what was seen. 90s against a measured
            # 0.2s, so it can only fire on a genuine wedge.
            watchdog = threading.Timer(90.0, self._kill_group_quietly, args=(proc,))
            watchdog.daemon = True
            watchdog.start()
            assert proc.stdout is not None
            seen: list[str] = []
            for line in proc.stdout:
                seen.append(line)
                if "[7/7]" in line:
                    break
            else:
                # No `wait` here on purpose: the `finally` below already reaps
                # or kills the child, and waiting first would turn the one
                # failure this branch exists to explain — stdout ended without
                # the marker — into an opaque TimeoutExpired with none of the
                # captured output attached.
                raise AssertionError(
                    "the script never reached [7/7], so nothing was signalled "
                    f"before the merge — this test proved nothing:\n{''.join(seen)}"
                )
            os.killpg(os.getpgid(proc.pid), signum)
            seen.append(proc.stdout.read())
            rc = proc.wait(timeout=60)
        finally:
            if watchdog is not None:
                watchdog.cancel()
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
        return rc, "".join(seen)

    @staticmethod
    def _kill_group_quietly(proc: subprocess.Popen[str]) -> None:
        """The watchdog's hand. It never raises, and that is the whole contract.

        This runs on a timer thread, where an exception is reported nowhere and
        would replace a legible test failure with silence. The group is already
        gone in every ordinary run — the timer is cancelled first — so a lookup
        failure here is the expected case, not an error.
        """
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass

    def _assert_stopped_run_is_recorded(self, armed: dict, signum: int, code: str) -> None:
        """One row, carrying the signal's own code — and that refuses BOTH forms.

        Asserting the whole row list rather than "a {code} is somewhere in
        there" is what lets one assertion tell the chosen form from the two
        rejected ones:

          * with the signal traps REMOVED the single row reads `0`, which the
            journal's documented reader counts as A LANDING — the defect, and
            the reason the instrument flattered us in the first place;
          * with the handler WRITING rather than merely exiting
            (`trap '_journal_record 143; exit 143' TERM`, measured: the handler
            writes and then the still-armed EXIT trap writes again) there are
            TWO rows, both correct in isolation. Landings would stop being
            overcounted and ATTEMPTS would start being, which is the same
            instrument lying through a different field.

        The process status is asserted too, and second rather than first, so
        that a run recording `0` reports the recorded CODE rather than a bare
        status mismatch.
        """
        self._branch(armed)
        rc, out = self._finish_stopped_by(armed, signum)
        rows = self._rows(armed)
        assert [row[1:] for row in rows] == [[self.SLUG, code]], (
            f"a finish stopped by signal {signum} must leave exactly one row "
            f"carrying {code}; the journal holds {rows}"
        )
        # The run must have ENDED at that status too, not merely written a line
        # about it — a fixture that does not assert its own setup is how this
        # file once shipped a test that could never fail.
        assert rc == int(code), (rc, out[-3000:])

    def test_a_run_stopped_by_sigterm_records_its_own_code(self, armed: dict) -> None:
        """CB-237 — the row a stopped finish leaves must not read as a landing.

        SIGTERM is how a supervisor, a timeout, or an agent harness ends a run,
        and it is the signal behind the one wrong `0` this clone's own journal
        carries. Before this, the EXIT trap read the `$?` of the last COMPLETED
        command instead of the status the shell was about to leave with.
        """
        self._assert_stopped_run_is_recorded(armed, signal.SIGTERM, "143")

    def test_a_run_stopped_by_sigint_records_its_own_code(self, armed: dict) -> None:
        """The operator's Ctrl-C, which the comment above the trap always claimed.

        Worth its own row rather than folded into the SIGTERM case: the two
        signals reach a shell by different routes, and SIGINT has a trap of its
        own to be inherited as IGNORED — a bash job started in the background by
        a non-interactive shell cannot trap SIGINT at all, so a probe built that
        way measures its own harness and reports that nothing happens. This test
        spawns through `subprocess`, where the child keeps the default
        disposition, which is also what an operator's terminal gives it.
        """
        self._assert_stopped_run_is_recorded(armed, signal.SIGINT, "130")

    def test_a_run_stopped_by_sighup_records_its_own_code(self, armed: dict) -> None:
        """CB-249 — a closed session is the SANCTIONED stop, not an exotic one.

        The prescribed order of work here is to start the finish, see the
        /simplify-traced reminder the PreToolUse gate inserts, and stop it by
        hand; a terminal or an agent session closing at that same moment
        delivers SIGHUP instead of SIGINT. CB-237 measured this signal, wrote
        down that the same one-line form would fix it, and left it out on
        SCOPE — so the row a dropped session leaves kept reading `0`, which the
        journal's documented reader counts as a LANDING.

        Not folded into the SIGTERM case for the reason the SIGINT test gives:
        the three reach a shell by different routes, and only running each one
        shows which of them the trap list actually covers.
        """
        self._assert_stopped_run_is_recorded(armed, signal.SIGHUP, "129")

    def test_a_run_stopped_by_sigpipe_records_its_own_code(self, armed: dict) -> None:
        """CB-249 — the fourth of the four, and the one with a vocabulary already.

        141 is not invented here: `cli.run` has meant "the reader of my output
        is gone" by that code since CB-78, so the journal and the package's own
        CLI now answer a dead reader with one number. Untrapped this recorded
        `0` exactly like the other three.
        """
        self._assert_stopped_run_is_recorded(armed, signal.SIGPIPE, "141")


class TestUnknownSlugRefusal:
    """CB-231 — a documented exit code that could not fire in the case it names.

    `worktree-finish.sh` advertises 2 for "no worktree for that slug", and
    `tools/_guards.sh` carries it in the exit-code table. Measured on bash
    5.3.9: the script printed its refusal, then died with **1**. The block ends
    by listing the worktrees the operator might have meant, and that listing's
    final `grep -v` drops the primary checkout — so in a clone with no OTHER
    worktree it selects nothing and exits 1, `set -o pipefail` lifts that to the
    pipeline, and `set -e` killed the script two lines before `exit 2`.

    The case it could not fire in is the ordinary one: a mistyped slug in a
    clone with nothing else checked out. The caller got 1, which is this
    script's code for bad input generally, so the refusal was real but
    unattributable — the "described better than it behaves" shape the Workflow
    section of CLAUDE.md exists to record.

    Found while installing the landing-attempt journal (CB-176): the journal
    recorded a 1 where the table promised a 2.
    """

    def _finish(self, armed: dict, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(armed["repo"] / "tools" / "worktree-finish.sh"), *args],
            cwd=str(armed["repo"]),
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
        )

    def test_an_unknown_slug_refuses_with_the_code_the_harness_documents(
        self, armed: dict
    ) -> None:
        # The condition that used to break it, ASSERTED rather than assumed: a
        # fixture that happened to register a second worktree would make the
        # listing non-empty and this test could never fail.
        listed = git(armed["repo"], "worktree", "list").splitlines()
        assert len(listed) == 1, f"the fixture registers more than the primary checkout: {listed}"

        r = self._finish(armed, "no-such-slug")
        assert "no worktree for slug" in r.stdout, r.stdout[-2000:]
        assert r.returncode == 2, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])

    def test_the_usage_block_still_refuses_with_one(self, armed: dict) -> None:
        """PASSES ON BOTH SIDES, and pins what the fix deliberately preserved.

        The usage block ends with the same listing and had the same latent
        defect, but its own code is 1 — so the pipeline's stray 1 was
        indistinguishable from the intended one and nothing was visibly wrong.
        Sharing the helper fixes the mechanism in both places at once; this
        asserts the outcome here did not move while that happened.
        """
        r = self._finish(armed)
        assert "Usage:" in r.stdout, r.stdout[-2000:]
        assert r.returncode == 1, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])


class TestFlockPremises:
    """What `flock -n` actually does — pinned, not assumed (CB-187).

    The worktree-finish lock rests on four properties of `flock(1)`, and the
    whole gate is worth nothing if any of them changes under a system upgrade.
    Pinned here for the same reason this file pins git's `MERGE_HEAD` and
    `GITHEAD_` behaviour: a suite that turns red is better than a gate that is
    silently disarmed.

    Measured on util-linux 2.41.3, 2026-08-31.
    """

    @staticmethod
    def _sh(script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_premise_a_free_file_is_acquired_and_a_held_one_is_refused_with_1(
        self, tmp_path: Path
    ) -> None:
        """rc 1 is what "someone else holds it" looks like — the gate's input."""
        lock = tmp_path / "x.lock"
        free = self._sh(f'exec 8>"{lock}"; flock -n 8; echo "rc=$?"')
        assert "rc=0" in free.stdout, (free.stdout, free.stderr)

        # A second process really holds it while we look. The holder is proven
        # alive before the probe runs: a holder that died would make this test
        # pass for the wrong reason, which is the vacuous-fixture trap this file
        # already learned once.
        held = self._sh(
            f'flock "{lock}" -c "sleep 5" & holder=$!\n'
            "sleep 0.4\n"
            'kill -0 "$holder" || { echo "HOLDER-DEAD"; exit 1; }\n'
            'echo "holder-alive=yes"\n'
            f'( exec 8>"{lock}"; flock -n 8; echo "rc=$?" )\n'
            'kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null; true\n'
        )
        assert "holder-alive=yes" in held.stdout, (held.stdout, held.stderr)
        assert "rc=1" in held.stdout, (held.stdout, held.stderr)

    def test_premise_a_failure_is_distinguishable_from_a_refusal(self) -> None:
        """The gate reads the result THREE-valued, and this is what lets it.

        `flock -n` on a descriptor that was never opened does NOT return 1. If
        it did, "another finish is running" and "the lock could not be read"
        would be one code and the gate would have to claim the first about the
        second — this repository's most-repeated defect, in a new place.
        """
        r = self._sh("flock -n 8; echo rc=$?")
        assert "rc=1" not in r.stdout, (r.stdout, r.stderr)
        assert re.search(r"rc=(\d+)", r.stdout), r.stdout
        assert int(re.search(r"rc=(\d+)", r.stdout).group(1)) not in (0, 1), r.stdout

    def test_premise_two_names_do_not_exclude_each_other(self, tmp_path: Path) -> None:
        """The mechanism of trap 1, in isolation.

        This is WHY the lock may not be named from `$1`: two spellings of one
        worktree would give two file names, and `flock` would happily hand a
        lock to each. The gate would be installed and unable to fire.
        """
        r = self._sh(
            f'exec 8>"{tmp_path}/a.lock"; flock -n 8; echo "first=$?"\n'
            f'( exec 7>"{tmp_path}/b.lock"; flock -n 7; echo "second=$?" )\n'
        )
        assert "first=0" in r.stdout and "second=0" in r.stdout, (r.stdout, r.stderr)

    def test_premise_unlinking_the_file_destroys_the_exclusion(self, tmp_path: Path) -> None:
        """Why the lock file is NEVER deleted, in one measurement.

        `flock` holds the INODE. Remove the name and the next process creates a
        different inode, locks that, and the exclusion is simply gone — which is
        why the exit-16 message tells the operator not to delete the file.
        """
        lock = tmp_path / "c.lock"
        r = self._sh(
            f'exec 8>"{lock}"; flock -n 8; echo "first=$?"\n'
            f'rm -f "{lock}"\n'
            f'( exec 7>"{lock}"; flock -n 7; echo "second=$?" )\n'
        )
        assert "first=0" in r.stdout, (r.stdout, r.stderr)
        assert "second=0" in r.stdout, (
            "deleting the lock file no longer defeats the lock — the message "
            "warning against it should be re-read: " + r.stdout
        )


class TestWorktreeFinishLock:
    """Two finishes of ONE worktree exclude each other (CB-187, half a).

    Behavioural, in a throwaway repo, for the reason `TestMergeSubjectDerivation`
    is: a structural test reads the code that was written, and the defect this
    closes is precisely a gate that READS correctly and cannot fire. The
    discriminating case is trap 1 — two spellings of one worktree — and no
    reading of the script reveals whether they collapse to one lock.

    The lock file's NAME is DISCOVERED from the product, never re-derived here.
    A test that spelled the naming rule out a second time would agree with a
    wrong implementation as readily as with a right one.
    """

    BRANCH = "fix/cb-999-lockrace"
    SPELLING_FULL = "fix-cb-999-lockrace"
    SPELLING_SUFFIX = "cb-999-lockrace"

    def _worktree(self, armed: dict) -> Path:
        """A worktree carrying NO commit of its own.

        Deliberate: every assertion below is about a refusal that happens
        BEFORE [1/7], so the branch's content is irrelevant to it, and an empty
        branch gives the probe run a cheap, deterministic stopping point
        (`_guard_nonempty_diff`, exit 9) at a phase LATER than the lock.
        """
        repo = armed["repo"]
        wt = repo / ".worktrees" / self.SPELLING_FULL
        git(repo, "worktree", "add", "-q", "-b", self.BRANCH, str(wt), "main")
        return wt

    def _finish(self, armed: dict, slug: str) -> subprocess.CompletedProcess[str]:
        witness = Path(armed["bin"]) / "uv-was-called"
        uv = Path(armed["bin"]) / "uv"
        if not uv.exists():
            # Any invocation of uv means the run reached a phase that costs
            # real time. The witness is how "the suite never ran" is PROVEN
            # rather than inferred from the absence of a line in stdout.
            uv.write_text(
                "#!/usr/bin/env bash\n"
                f'printf "%s\\n" "$*" >> "{witness}"\n'
                "exit 1\n"
            )
            uv.chmod(0o755)
        return subprocess.run(
            [str(armed["repo"] / "tools" / "worktree-finish.sh"), slug],
            cwd=str(armed["repo"]),
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
        )

    @staticmethod
    def _locks(repo: Path) -> list[Path]:
        return sorted((repo / ".worktrees").glob(".finish-*.lock"))

    def test_two_spellings_of_one_worktree_cannot_both_finish(self, armed: dict) -> None:
        """THE test. Without it the fix for CB-187 would be vacuous.

        `_resolve_worktree_path` accepts both the directory name and the branch
        suffix, so two legitimate invocations of one branch arrive spelled
        differently. A lock named from `$1` gives two files, two inodes and no
        exclusion at all — measured directly in TestFlockPremises. Here the
        second spelling must be refused by a lock the FIRST spelling created.
        """
        repo = armed["repo"]
        self._worktree(armed)
        main_before = git(repo, "rev-parse", "main")

        # 1. A probe run, purely to let the SCRIPT name its own lock file. It
        #    refuses at [3/7] on the empty branch, which also shows the lock is
        #    taken earlier than that guard.
        probe = self._finish(armed, self.SPELLING_FULL)
        assert probe.returncode == 9, (probe.returncode, probe.stdout[-2000:])
        locks = self._locks(repo)
        assert len(locks) == 1, f"expected exactly one lock file, found {locks}"
        lock_path = locks[0]

        # 2. Hold it, as a concurrent finish would.
        fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # 3. The SAME spelling is refused — the ordinary case.
            same = self._finish(armed, self.SPELLING_FULL)
            assert same.returncode == 16, (same.returncode, same.stdout[-3000:])

            # 4. The OTHER spelling is refused too. This is the discriminator:
            #    a `$1`-named lock passes here at exit 9.
            other = self._finish(armed, self.SPELLING_SUFFIX)
            assert other.returncode == 16, (
                "a second spelling of the same worktree was NOT excluded — the "
                "lock is named from the operator's argument rather than from "
                "the resolved worktree",
                other.returncode,
                other.stdout[-3000:],
            )
            assert "ALREADY RUNNING" in other.stdout, other.stdout[-3000:]

            # 5. Independent corroboration of the same claim, from the file
            #    system rather than from an exit code: three runs, two
            #    spellings, ONE lock file.
            assert self._locks(repo) == [lock_path], self._locks(repo)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        # 6. And nothing was landed or paid for by either refusal.
        assert git(repo, "rev-parse", "main") == main_before
        assert not (Path(armed["bin"]) / "uv-was-called").exists(), (
            "a refused finish still invoked uv, i.e. it paid for a check phase"
        )

    def test_the_refusal_costs_nothing_and_says_so(self, armed: dict) -> None:
        """Refused BEFORE [1/7], the first phase that mutates anything.

        [1/7] auto-commits a dirty worktree. A lock taken after it would let the
        loser rewrite the tree it is about to be told to leave alone, and a lock
        taken after [6/7] would defeat the entire purpose, which is not paying
        for the checks.
        """
        repo = armed["repo"]
        self._worktree(armed)
        probe = self._finish(armed, self.SPELLING_FULL)
        assert probe.returncode == 9, (probe.returncode, probe.stdout[-2000:])
        lock_path = self._locks(repo)[0]

        fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = self._finish(armed, self.SPELLING_FULL)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        assert r.returncode == 16, (r.returncode, r.stdout[-3000:])
        assert "[1/7]" not in r.stdout, (
            "the refusal arrived after the first mutating phase: " + r.stdout[-3000:]
        )
        # The text must not read as an accusation against the branch, and it
        # must not invite the one repair that turns the gate off.
        assert "Do NOT delete" in r.stdout, r.stdout[-3000:]
        assert str(lock_path) in r.stdout, r.stdout[-3000:]

    def test_the_refusal_is_recorded_in_the_journal(self, armed: dict) -> None:
        """A silent refusal would HIDE the over-count it explains (CB-176).

        The journal exists to price "landing has become painful". A finish
        refused as a duplicate is one of the two lines a race used to write, so
        suppressing it would quietly improve the number instead of making it
        attributable. With a code of its own the pair becomes readable: one real
        attempt, one rejected double.
        """
        repo = armed["repo"]
        self._worktree(armed)
        assert self._finish(armed, self.SPELLING_FULL).returncode == 9
        lock_path = self._locks(repo)[0]

        journal = repo / ".worktrees" / "landing-attempts.log"
        before = journal.read_text().splitlines() if journal.exists() else []

        fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert self._finish(armed, self.SPELLING_FULL).returncode == 16
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        after = journal.read_text().splitlines()
        assert len(after) == len(before) + 1, (before, after)
        assert after[-1].split()[2] == "16", after[-1]

    def test_a_free_lock_lets_the_finish_through(self, armed: dict) -> None:
        """The other side of the gate, so it is not one that always refuses.

        A gate asserted only on its refusals is indistinguishable from a wall.
        """
        repo = armed["repo"]
        self._worktree(armed)
        r = self._finish(armed, self.SPELLING_FULL)
        assert r.returncode == 9, (r.returncode, r.stdout[-2000:])
        assert "[1/7]" in r.stdout, r.stdout[-2000:]
        assert len(self._locks(repo)) == 1

        # And the lock is released by the process exiting, so an honest re-run
        # is not refused by its own predecessor — the retry that exit 13
        # PRESCRIBES must not be blocked by this gate.
        again = self._finish(armed, self.SPELLING_FULL)
        assert again.returncode == 9, (again.returncode, again.stdout[-2000:])
        assert "ALREADY RUNNING" not in again.stdout, again.stdout[-2000:]


# ---------------------------------------------------------------------------
# CB-285: half (b) of CB-187 — what the CHECK ARMS say when the worktree they
# were about to check has vanished under them.
#
# CB-187 landed in two halves and only one of them was tested. Half (a), the
# non-blocking per-worktree lock and its exit 16, is TestWorktreeFinishLock
# above. Half (b) — exit 17, and a report that names a vanished directory
# instead of blaming the branch — landed with no test of any kind. That is not
# merely a reporting gap: the card's own subject is that the FALSE DIAGNOSIS
# costs more than the race it comes from, because a person spent real time
# reconstructing the cause from directory mtimes and then filed a card with the
# wrong diagnosis. Code no test can turn red is code whose ABSENCE is
# indistinguishable from its health.
#
# WHY EVERY TEST HERE RUNS THE SCRIPT, AND WHY NONE PASSES --skip-checks. Both
# arms live inside the else-branch of `if [[ "${SKIP_CHECKS}" == true ]]`, so
# the flag every other end-to-end class in this file passes for speed would
# skip the entire subject. Structural reading is barred for the reason this
# file already records for CB-116 — it "cannot tell a derivation that is
# written correctly from one that produces the right string" — and here it
# would be worse than useless: worktree-finish.sh QUOTES the old false line
# inside the comment explaining why it was removed, so a grep for that text
# finds the prose and reports the defect present in a tree that is fixed.
#
# THE THREE-VALUED QUESTION IS THE POINT. `_worktree_is_provably_gone` answers
# with two values a question that has three — there, gone, and COULD NOT LOOK —
# and collapses the last into the first deliberately, so the honest message is
# printed only on affirmative proof. Reading it as two-valued (a bare
# `[[ ! -d ]]` on the path itself) inverts the diagnosis: a directory that is
# present but could not be seen would be declared gone, and a real suite
# failure would then be excused as "not your fault". Exactly one test below
# discriminates that, and it is the reason the class is three tests and not two.
class TestCheckArmsReportAVanishedWorktree:
    SLUG = "fix-cb-285-arm"
    BRANCH = "fix/cb-285-arm"

    # THE SHORT NEEDLE IS THE NAMED ONE, and the long form is COMPOSED from it
    # rather than typed again. That direction is load-bearing for a negative
    # assertion, where it is the opposite of the positive case: a LONGER needle
    # forbids LESS, so spelling the absence check with the full sentence would
    # quietly weaken it. Composing keeps one source of truth without paying
    # that price.
    GONE_MARK = "THE WORKTREE DIRECTORY IS GONE"
    GONE = f"could not run: {GONE_MARK}."
    # The second line of the same block, asserted POSITIVELY by the two tests
    # that expect it and negatively by the ones that must not see it — which is
    # what keeps it honest: a constant nothing asserts positively can drift to
    # stale wording and go on passing every absence check for ever.
    GONE_EXONERATES = "This is NOT a failure of the branch."
    OLD_LIE = "failed — fix in the worktree, then re-run."

    def _branch(self, armed: dict) -> Path:
        """A branch with one commit of its own, cut from main's tip.

        One commit is what [3/7]'s `_guard_nonempty_diff` needs; cutting from
        the tip keeps [4/7] and [5/7] no-ops, so the only phase these tests
        traverse with any behaviour of its own is the one under test.
        """
        repo = armed["repo"]
        wt = repo / ".worktrees" / self.SLUG
        git(repo, "worktree", "add", "-q", "-b", self.BRANCH, str(wt), "main")
        (wt / "feature.txt").write_text("work\n")
        git(wt, "add", "feature.txt")
        git(wt, "commit", "--no-verify", "-m", "fix(cb-285): the branch's own work")
        return wt

    def _uv(self, armed: dict, *, on_ruff: str, on_pytest: str) -> Path:
        """Put a `uv` on PATH that answers all three shapes a finish invokes.

        A finish shells out to `uv` at exactly three call sites, and they are
        named rather than counted: `_guard_interpreter_matches_main`'s probe at
        [5/7], and the ruff and pytest arms of [6/7]. The other `uv` calls under
        tools/ belong to worktree-setup.sh, which a finish never runs — so a
        grep of the directory is not the measurement, and is not what this shim
        rests on.

        THE PROBE IS FORWARDED TO MAIN'S OWN INTERPRETER, so the guard compares
        that interpreter with itself and agrees. That guard is not this class's
        subject; it merely stands between the fixture and the phase that is,
        and a real `uv run` in the worktree would add a venv build to every
        test here for nothing.

        THE `uv` SHIM THIS FILE ALREADY CARRIES CANNOT BE REUSED, and the
        reason is an ordering fact rather than a preference:
        TestWorktreeFinishLock installs a `uv` that exits 1 UNCONDITIONALLY,
        and the interpreter probe runs BEFORE both arms, so every test here
        would measure exit 14 at [5/7] and never reach the arms at all.

        BOTH ARMS ARE ANSWERED HERE BECAUSE THE REAL ONES CANNOT REACH THE
        OUTCOMES THESE TESTS NEED, and the measurement says something sharper
        than "the fixture has no ruff". The armed fixture's repo carries tools/
        and a pyproject and nothing else — no `src/`, no `tests/` — so
        `uv run --extra dev ruff check src/ tests/` FAILS in that tree either
        way: it exits 2 with "Failed to spawn: `ruff`" when no ruff is
        reachable, and exits 1 with "E902 No such file or directory" when a
        real one is on the developer's PATH (both measured, 2026-09-01). The
        arm therefore cannot pass on its own under any PATH, so the pytest arm
        is unreachable without help — and, separately, a real ruff could never
        make a directory disappear, which is the event the ruff test has to
        create. Pinning both arms here is what makes the outcome a property of
        the fixture instead of a property of the machine.

        An unrecognised shape exits 99 and says FIXTURE DRIFT on stderr. That
        is what the three-call-site claim actually rests on, and `_finish`
        asserts on that stderr so the claim is checked rather than merely
        printed: a fourth call site appearing later fails these tests loudly,
        instead of quietly reaching whatever `uv` the developer's machine
        happens to have.
        """
        calls = Path(armed["bin"]) / "uv-calls.log"
        main_python = Path(armed["repo"]) / ".venv" / "bin" / "python"
        assert main_python.is_file(), main_python
        shim = Path(armed["bin"]) / "uv"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'WT="{armed["repo"] / ".worktrees" / self.SLUG}"\n'
            f'printf "%s\\n" "$*" >> "{calls}"\n'
            'case " $* " in\n'
            f'    *" ruff "*) {on_ruff} ;;\n'
            f'    *" pytest "*) {on_pytest} ;;\n'
            f'    *" -c "*) exec "{main_python}" -c "${{@: -1}}" ;;\n'
            "esac\n"
            'echo "FIXTURE DRIFT: unrecognised uv invocation: $*" >&2\n'
            "exit 99\n"
        )
        shim.chmod(0o755)
        assert shim.is_file() and os.access(shim, os.X_OK), shim
        return calls

    @staticmethod
    def _arms(calls: Path) -> list[str]:
        """Which [6/7] arms actually reached `uv`, in order.

        THIS IS THE ARM WITNESS, and what it is worth differs per test, so it
        is stated per test rather than claimed once here. Read literally it
        answers one question only: which arms got as far as spawning `uv`.
        That is not the same as which arm FAILED — an arm whose `cd` fails
        never spawns anything and still reports — and it is not the same as
        what stdout says, since a printed line proves the script reached an
        echo, not that a command ran.

        The log is written by the shim BEFORE it dispatches, so an invocation
        is recorded even when that invocation then destroys the tree.
        """
        if not calls.exists():
            return []
        arms = []
        for line in calls.read_text().splitlines():
            padded = f" {line} "
            if " ruff " in padded:
                arms.append("ruff")
            elif " pytest " in padded:
                arms.append("pytest")
        return arms

    def _finish(self, armed: dict) -> subprocess.CompletedProcess[str]:
        r = subprocess.run(
            [str(armed["repo"] / "tools" / "worktree-finish.sh"), self.SLUG],
            cwd=str(armed["repo"]),
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{armed['bin']}{os.pathsep}{os.environ['PATH']}"},
        )
        # The shim's own alarm, checked rather than merely printed — see `_uv`.
        assert "FIXTURE DRIFT" not in r.stderr, r.stderr[-2000:]
        return r

    def test_a_worktree_gone_before_the_pytest_arm_is_reported_not_blamed(
        self, armed: dict
    ) -> None:
        """The card's own scenario, through the mechanism the script names.

        `worktree-finish.sh` describes the failure it is defending against as
        "a `(cd "${WORKTREE_PATH}" && …)` subshell whose `cd` fails when the
        directory is gone", so that is what this test builds: the ruff arm
        succeeds and takes the directory with it, and the pytest arm then never
        starts at all. It is the real race — another finish of the same
        worktree landing and running its ordinary cleanup between two arms.

        The sibling test below covers the OTHER way into the same report, where
        the directory is already gone and the command itself returns non-zero.
        Between them the two entry paths of `_report_check_failure` are both
        exercised; either alone would leave one untested.
        """
        wt = self._branch(armed)
        calls = self._uv(
            armed,
            on_ruff='rm -rf "$WT"; exit 0',
            on_pytest='echo "the pytest arm was spawned after the tree vanished" >&2; exit 1',
        )

        r = self._finish(armed)

        # THE ARM WITNESS, and here it carries the whole "the cd failed" claim:
        # the pytest arm never reached `uv`, so the non-zero status the script
        # acted on came from the failed `cd` and not from a command's exit code.
        assert self._arms(calls) == ["ruff"], (self._arms(calls), r.stdout[-3000:])
        assert "✓ ruff check clean" in r.stdout, r.stdout[-3000:]
        assert not wt.exists(), "the fixture did not remove the worktree"

        assert r.returncode == 17, (r.returncode, r.stdout[-3000:])
        assert f"pytest {self.GONE}" in r.stdout, r.stdout[-3000:]
        assert self.GONE_EXONERATES in r.stdout, r.stdout[-3000:]
        assert f"pytest {self.OLD_LIE}" not in r.stdout, r.stdout[-3000:]

    def test_a_worktree_removed_under_the_ruff_arm_is_reported_not_blamed(
        self, armed: dict
    ) -> None:
        """THE ARM THE CARD DID NOT NAME, which is why this test exists.

        CB-187 was reported against pytest. The ruff arm has the identical
        shape and reports through the identical function, and fixing only the
        arm somebody enumerated is this repository's most-repeated defect — so
        the arm nobody enumerated gets its own oracle rather than inheriting
        the other's.

        It also covers the second entry path: here `cd` succeeds, the command
        runs with the directory already gone, and the non-zero comes back from
        the command itself.
        """
        wt = self._branch(armed)
        calls = self._uv(
            armed,
            on_ruff='rm -rf "$WT"; exit 1',
            on_pytest='echo "the pytest arm ran after ruff refused" >&2; exit 1',
        )

        r = self._finish(armed)

        # `_report_check_failure` exits, so the pytest arm must never be
        # reached. The call log proves that positively — the shim records every
        # invocation before dispatching — where the absence of a line in stdout
        # would only be consistent with it.
        assert self._arms(calls) == ["ruff"], (self._arms(calls), r.stdout[-3000:])
        assert "✓ ruff check clean" not in r.stdout, r.stdout[-3000:]
        assert not wt.exists(), "the fixture did not remove the worktree"

        assert r.returncode == 17, (r.returncode, r.stdout[-3000:])
        assert f"ruff check {self.GONE}" in r.stdout, r.stdout[-3000:]
        assert self.GONE_EXONERATES in r.stdout, r.stdout[-3000:]
        assert f"ruff check {self.OLD_LIE}" not in r.stdout, r.stdout[-3000:]

    # THE THREE STATES THAT ARE NOT A PROVEN DISAPPEARANCE, kept as one table
    # because they are one contract and not three scenarios. `_worktree_is_
    # provably_gone` answers a THREE-valued question with two values, and the
    # collapse is deliberate: anything short of affirmative proof degrades to
    # the ordinary message. A test per hand-picked scenario would have covered
    # whichever states somebody happened to enumerate — which is the defect
    # this card is about, in oracle form — so the parameters walk the function's
    # own branches instead.
    #
    # ONE BRANCH IS DELIBERATELY ABSENT, and saying so is the point of writing
    # this down: `[[ -x "${parent}" ]]` cannot be reached without clearing an
    # execute bit, and that condition is simply never true for the superuser,
    # so any test of it would have to carry `skipif(os.geteuid() == 0)` and
    # would then delete itself from every build that runs as root. An
    # uncovered branch named out loud is worth more than a test that vanishes
    # where nobody looks.
    NOT_PROVEN_GONE = [
        ("exit 1", "still_there"),
        ('rm -rf "$WT"; ln -s "$WT.never-existed" "$WT"; exit 1', "dangling_symlink"),
        # This row leans on something OUTSIDE its own subject, and saying so
        # is cheaper than the day it bites: the parent it replaces is
        # `.worktrees/`, which also houses the integration lock and the landing
        # journal. It survives only because `_journal_record` wraps its write
        # in `{ … } >/dev/null 2>&1 || true` and returns 0, so a broken journal
        # cannot rewrite the exit status this row asserts on (measured), and
        # because the finish lock is held on an already-open descriptor. A
        # future change that writes into `.worktrees/` unguarded would redden
        # this row for a reason that has nothing to do with the predicate.
        (
            'PARENT="$(dirname -- "$WT")"; rm -rf "$WT"; rm -rf "$PARENT"; : > "$PARENT"; exit 1',
            "parent_is_not_a_directory",
        ),
    ]

    @staticmethod
    def _assert_premise(wt: Path, premise: str) -> None:
        """The state was BUILT, not assumed — checked before the verdict."""
        if premise == "still_there":
            assert wt.is_dir(), "the fixture destroyed the worktree it was meant to keep"
        elif premise == "dangling_symlink":
            assert wt.is_symlink(), "the fixture did not leave a symlink behind"
            assert not wt.exists(), "the symlink resolves, so it is not dangling"
        else:
            assert wt.parent.is_file(), "the fixture did not replace the parent with a file"

    @pytest.mark.parametrize("on_pytest, premise", NOT_PROVEN_GONE)
    def test_only_a_proven_disappearance_earns_the_honest_report(
        self, armed: dict, on_pytest: str, premise: str
    ) -> None:
        """Everything short of proof reads as an ordinary failure.

        This is what the whole card turns on. Getting it backwards would
        rebuild the defect with the sign flipped: a run that could not look
        would announce a disappearance, and a genuinely red suite would be
        excused as "not your fault" — a false exoneration, which the script's
        own comment calls worse than the false accusation this card is about.
        The `still_there` row is the expensive one to lose: its regression
        would fire on EVERY ordinary red check, not on a rare race.

        THE STATES ARE BUILT WITHOUT TOUCHING PERMISSIONS. The obvious way to
        make a path unexaminable is to clear an execute bit, and it is exactly
        the way that cannot be used here — see the note on the table above.
        Replacing the parent with a REGULAR FILE, or leaving a DANGLING
        SYMLINK where the worktree was, reach two other branches of the same
        function and are false for the superuser as well.

        The last two rows are unprovable for DIFFERENT reasons, and collapsing
        them would lose the point. Under the dangling symlink the directory is
        genuinely gone but the NAME still resolves as a link, so nothing has
        been proved absent. Under the replaced parent nothing can be looked up
        at all. In both the worktree really is gone, and in both the script
        still declines to say so — the declared cost written into the
        function's own comment, and asserting on it here is what keeps the cost
        declared rather than drifting.
        """
        wt = self._branch(armed)
        calls = self._uv(armed, on_ruff="exit 0", on_pytest=on_pytest)

        r = self._finish(armed)

        assert self._arms(calls) == ["ruff", "pytest"], (self._arms(calls), r.stdout[-3000:])
        self._assert_premise(wt, premise)

        # THE CODE, not only the text (a mutant printing the right message and
        # exiting 17 must still go red), and the text, not only the code (exit
        # 1 is also what an ordinary failure returns, so the code alone cannot
        # tell the two branches of `_report_check_failure` apart).
        assert r.returncode == 1, (r.returncode, r.stdout[-3000:])
        assert f"pytest {self.OLD_LIE}" in r.stdout, r.stdout[-3000:]
        assert self.GONE_MARK not in r.stdout, r.stdout[-3000:]
        assert self.GONE_EXONERATES not in r.stdout, r.stdout[-3000:]
