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

import os
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
        lock_at = src.index("exec 9>")
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
    def _plan(repo: Path, name: str, body: str = "note\n") -> str:
        d = repo / ".claude" / "plans"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body, encoding="utf-8")
        return f".claude/plans/{name}"

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
        assert "not a two-parent merge" in r.stdout, r.stdout[-4000:]
        assert "between the in-lock" not in r.stdout, (
            "the alarm blamed the pre-merge window for a post-merge commit"
        )
        assert not wt.exists()

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
        tip_at = src.index('_ALARM_TIP=$(git')
        arm_at = src.index("trap _alarm_speak EXIT")
        # The LAST unlock: the three before it are refusal paths, and anchoring
        # on the first one after the merge finds the merge-failed branch, where
        # nothing has landed and there is nothing for an alarm to read.
        unlock_at = src.rindex("flock -u 9\n")
        assert merge_at < tip_at < arm_at < unlock_at, (
            "the alarm must read main's tip after the merge and arm before the unlock"
        )

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

    def test_the_alarm_is_delivered_by_an_exit_trap(self) -> None:
        """A trailing `if` is preemptable by `set -e`; a trap is not.

        The cleanup ends with a `git log … | sed` pipeline, and any statement a
        future edit inserts is another way to die between detection and speech.
        The behavioural half is
        TestPostMergeAlarm::test_a_cleanup_failure_cannot_swallow_the_alarm.
        """
        src = code_only(self.FINISH.read_text())
        assert "trap _alarm_speak EXIT" in src, "the alarm is not armed as an exit trap"
        assert src.count("trap ") == 1, "a second trap could replace this one silently"
        body = self._speak_body()
        assert "exit 15" in body, "the alarm no longer exits with its own code"
        # And it must leave an ordinary run's status alone.
        assert 'return "${rc}"' in body, (
            "the trap does not preserve the exit status when it has nothing to say"
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
        """Prose under the table, per the same treatment `main-invariants.yml` got."""
        md = self.CLAUDE_MD.read_text()
        assert "CB-121" in md
        assert "exit 15" in md
        at = md.index("CB-121")
        window = md[at - 2000 : at + 4000]
        assert "alarm" in window.lower(), "CB-121 is mentioned but the alarm is not named"
        assert "check-then-act" in window.lower()
