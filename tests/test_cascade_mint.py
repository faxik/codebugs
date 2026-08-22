"""Tests for `tools/cascade-mint.sh` — the cascade-ID allocator.

WHAT THIS PINS, AND WHY EACH TEST HAD TO EXIST.

The registry `.claude/plans/CASCADE-IDS.md` is an append-only allocator whose
header promised that a concurrent mint yields a merge conflict. It cannot: both
directions commit plan notes DIRECTLY on main, sequentially, and sequential
direct commits never merge. Three collisions followed, each with a narrower
cause; the third happened with the READ of the tail correctly protected, because
the NUMBER had been typed by the author as a literal beforehand. So the script
COMPUTES the number and writes it in one operation under a lock, and the tests
below hold the properties that make that better than the discipline it replaces:

  * max+1 over EVERY line, annulment annotations included (a spent number stays
    spent);
  * the Cyrillic 'Т' (U+0422) is read alongside a Latin typo and written as the
    Cyrillic one, and the Latin arm must not match inside 'BT-4';
  * zero ids found is an ERROR, never "start from 1";
  * two concurrent mints get two DIFFERENT numbers — and removing the `flock`
    must turn that test RED, or it is green by construction;
  * the commit carries ONLY the registry even when a parallel session has its
    own files staged, and its generated message passes the LIVE commit-msg hook;
  * the test seam cannot write to a path at all (CB-138) — pointing its marker
    at the real registry has no effect, because the marker's value is never
    used as a path any more, only as a boolean that gates one stderr line.

Everything runs in a throwaway git repository with a fixture registry. The real
registry is never read, written or committed by this file.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "cascade-mint.sh"
PRE_COMMIT_HOOK = REPO_ROOT / "tools" / "pre-commit-hook.sh"
COMMIT_MSG_HOOK = REPO_ROOT / "tools" / "commit-msg-hook.sh"

REGISTRY_REL = ".claude/plans/CASCADE-IDS.md"

CYRILLIC_TE = "Т"  # 'Т' — the registry's unit prefix, NOT ASCII 'T'.

# The highest unit number here (8) appears ONLY inside an annulment annotation,
# and the last entry line carries a LOWER number (7). An allocator that reads
# "the number on the last line" hands out Т-8 — a number the registry has
# already spent — while max+1 over every line correctly gives Т-9.
FIXTURE_REGISTRY = f"""# CASCADE-IDS — fixture registry

- DIR-1 — direction one
- BT-3 — sub-topic three
- {CYRILLIC_TE}-5 — (DIR-1) unit five
- {CYRILLIC_TE}-6 — (DIR-2) unit six
- КОЛЛИЗИЯ №1: строка «{CYRILLIC_TE}-8» АННУЛИРОВАНА — номер занят другим
  направлением; переминт строкой ниже. Номер остаётся израсходованным.
- {CYRILLIC_TE}-7 — (DIR-1) ПЕРЕМИНТ аннулированной строки выше
"""


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "t29",
            "GIT_AUTHOR_EMAIL": "t29@example.invalid",
            "GIT_COMMITTER_NAME": "t29",
            "GIT_COMMITTER_EMAIL": "t29@example.invalid",
        }
    )
    env.pop("CASCADE_MINT_TEST_DELAY", None)
    env.pop("CASCADE_MINT_TEST_MARKER", None)
    return env


def _make_repo(tmp_path: Path, registry_text: str | None = FIXTURE_REGISTRY) -> Path:
    """A throwaway repo on `main` with a fixture registry and the real hooks armed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.name", "t29")
    _git(repo, "config", "user.email", "t29@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")

    (repo / ".claude" / "plans").mkdir(parents=True)
    if registry_text is not None:
        (repo / REGISTRY_REL).write_text(registry_text, encoding="utf-8")
        _git(repo, "add", "--", REGISTRY_REL)
    else:
        # An initial commit still has to exist, so commit an unrelated note.
        (repo / ".claude" / "plans" / "seed.md").write_text("seed\n", encoding="utf-8")
        _git(repo, "add", "--", ".claude/plans/seed.md")
    _git(repo, "commit", "-q", "-m", "seed the fixture registry")

    # Hooks are armed AFTER the seed commit, so the bootstrap needs no exemption
    # and every later commit in the test faces the real gates.
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").symlink_to(PRE_COMMIT_HOOK)
    (hooks / "commit-msg").symlink_to(COMMIT_MSG_HOOK)
    return repo


def _mint(repo: Path, *args: str, delay: str | None = None) -> subprocess.CompletedProcess[str]:
    env = _env()
    if delay is not None:
        env["CASCADE_MINT_TEST_DELAY"] = delay
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )


def _registry(repo: Path) -> str:
    return (repo / REGISTRY_REL).read_text(encoding="utf-8")


class TestFixtureIsReal:
    """A test that sets up its own fixture must ASSERT the fixture exists.

    `TestKnownLimits` in this repo's worktree harness once passed while its hook
    was never installed at all, so the probe could not fail. These two assertions
    are what keep the hook-facing tests below from being green by construction.
    """

    def test_the_commit_msg_hook_is_actually_armed(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / ".claude" / "plans" / "control.md").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "--", ".claude/plans/control.md")
        refused = subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "a message naming nothing"],
            capture_output=True,
            text=True,
            env=_env(),
        )
        assert refused.returncode != 0
        assert "does not name" in refused.stderr

    def test_the_pre_commit_hook_is_actually_armed(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "--", "src.py")
        refused = subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "code on main"],
            capture_output=True,
            text=True,
            env=_env(),
        )
        assert refused.returncode != 0


class TestNumberIsComputed:
    def test_max_plus_one_counts_annulled_lines_too(self, tmp_path: Path) -> None:
        """§4.1 — an annulled number is a SPENT number.

        The fixture's last entry is Т-7 while Т-8 survives only inside the
        annulment annotation. "Next after the last line" would re-issue Т-8.
        """
        repo = _make_repo(tmp_path)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == f"{CYRILLIC_TE}-9"

    def test_a_latin_typo_in_the_registry_is_not_invisible(self, tmp_path: Path) -> None:
        """Reading accepts both spellings; a spent number written with ASCII 'T'
        must not be handed out again."""
        text = FIXTURE_REGISTRY + "- T-40 — (DIR-2) unit forty, minted with a LATIN T by mistake\n"
        repo = _make_repo(tmp_path, text)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == f"{CYRILLIC_TE}-41"

    def test_writing_always_uses_the_cyrillic_letter(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        out = _mint(repo, "--prefix", "T", "--dry-run")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == f"{CYRILLIC_TE}-9"
        assert "T-9" not in out.stdout

    def test_the_latin_arm_does_not_match_inside_bt(self, tmp_path: Path) -> None:
        """Without a left boundary, 'BT-90' feeds 90 into the unit family."""
        text = FIXTURE_REGISTRY + "- BT-90 — (DIR-2) a sub-topic with a large number\n"
        repo = _make_repo(tmp_path, text)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == f"{CYRILLIC_TE}-9"

    def test_other_prefixes_are_allocated_independently(self, tmp_path: Path) -> None:
        """DIR is 3, not 2: the fixture's `(DIR-2)` attribution on a unit line is
        a real occurrence of that id, and the real registry attributes every unit
        the same way. A family's number is spent wherever it appears, which is
        the same rule that makes an annulled line count."""
        repo = _make_repo(tmp_path)
        assert _mint(repo, "--prefix", "BT", "--dry-run").stdout.strip() == "BT-4"
        assert _mint(repo, "--prefix", "DIR", "--dry-run").stdout.strip() == "DIR-3"

    def test_a_number_in_guillemets_is_still_seen(self, tmp_path: Path) -> None:
        """The registry quotes annulled ids as «Т-21»; a non-ASCII neighbour is a
        boundary, so the number must remain visible to the allocator."""
        text = FIXTURE_REGISTRY.replace(f"«{CYRILLIC_TE}-8»", f"«{CYRILLIC_TE}-88»")
        assert f"{CYRILLIC_TE}-88" in text
        repo = _make_repo(tmp_path, text)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.stdout.strip() == f"{CYRILLIC_TE}-89"


class TestFailClosed:
    """§4.3 — zero found is an error. Distinguishing an empty result from an
    error is a rule this repository has paid for three times."""

    def test_no_ids_for_the_prefix_is_an_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, "# CASCADE-IDS — fixture with no ids at all\n")
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.returncode != 0
        assert "ZERO FOUND IS AN ERROR" in out.stderr
        assert out.stdout.strip() == ""

    def test_a_missing_registry_is_an_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path, None)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.returncode != 0
        assert "registry not found" in out.stderr

    def test_an_unreadable_registry_is_an_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        target = repo / REGISTRY_REL
        target.chmod(0o000)
        try:
            if os.access(target, os.R_OK):  # running as root — the probe is moot
                pytest.skip("cannot make a file unreadable as this user")
            out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
            assert out.returncode != 0
            assert "readable" in out.stderr or "could not read" in out.stderr
        finally:
            target.chmod(0o644)

    def test_a_prefix_with_a_metacharacter_is_refused(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        out = _mint(repo, "--prefix", ".*", "--dry-run")
        assert out.returncode != 0
        assert "not allowed" in out.stderr

    def test_an_id_too_large_for_shell_arithmetic_is_refused(self, tmp_path: Path) -> None:
        """`$((10#...))` wraps silently: 18446744073709551616 becomes 0, so max+1
        would be 1 — "start from 1" arriving past the fail-closed check."""
        text = FIXTURE_REGISTRY + f"- {CYRILLIC_TE}-18446744073709551616 — a corrupt line\n"
        repo = _make_repo(tmp_path, text)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.returncode != 0
        assert "digit id" in out.stderr
        assert out.stdout.strip() == ""

    def test_a_newline_in_the_text_is_refused(self, tmp_path: Path) -> None:
        """A second line could carry its own id and make the next mint skip it."""
        repo = _make_repo(tmp_path)
        injected = f"(DIR-1) a normal line\n- {CYRILLIC_TE}-999 — a smuggled entry"
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--text", injected)
        assert out.returncode != 0
        assert "single line" in out.stderr
        assert f"{CYRILLIC_TE}-999" not in _registry(repo)

    def test_dry_run_writes_nothing_and_commits_nothing(self, tmp_path: Path) -> None:
        """The `--help` contract, asserted rather than believed."""
        repo = _make_repo(tmp_path)
        before_text = _registry(repo)
        before_head = _git(repo, "rev-parse", "HEAD").strip()
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--dry-run")
        assert out.returncode == 0, out.stderr
        assert _registry(repo) == before_text
        assert _git(repo, "rev-parse", "HEAD").strip() == before_head
        assert _git(repo, "status", "--porcelain", "--", REGISTRY_REL).strip() == ""

    def test_minting_off_main_is_refused(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "fix/somewhere-else")
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--text", "(DIR-1) something")
        assert out.returncode != 0
        assert "main" in out.stderr

    def test_a_dirty_registry_is_refused(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        with (repo / REGISTRY_REL).open("a", encoding="utf-8") as fh:
            fh.write("- Т-99 — someone else's in-flight edit\n")
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--text", "(DIR-1) something")
        assert out.returncode != 0
        assert "uncommitted" in out.stderr

    def test_a_refused_commit_rolls_the_append_back(self, tmp_path: Path) -> None:
        """A refused mint must leave no half-allocated number behind."""
        repo = _make_repo(tmp_path)
        before = _registry(repo)
        (repo / ".git" / "hooks" / "pre-commit").unlink()
        (repo / ".git" / "hooks" / "pre-commit").write_text(
            "#!/bin/sh\nexit 1\n", encoding="utf-8"
        )
        (repo / ".git" / "hooks" / "pre-commit").chmod(0o755)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--text", "(DIR-1) something")
        assert out.returncode != 0
        assert "rolled back" in out.stderr
        assert _registry(repo) == before
        assert _git(repo, "status", "--porcelain", "--", REGISTRY_REL).strip() == ""


class TestTheMintCommit:
    def test_it_appends_the_computed_id_and_the_given_text(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--text", "(DIR-1) a new unit")
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == f"{CYRILLIC_TE}-9"
        assert _registry(repo).splitlines()[-1] == f"- {CYRILLIC_TE}-9 — (DIR-1) a new unit"

    def test_the_generated_message_passes_the_live_commit_msg_hook(self, tmp_path: Path) -> None:
        """§4.5 — probed against the real hook, not reasoned about.

        `TestFixtureIsReal` proves the same hook refuses a message that names
        nothing, so a pass here is the hook accepting, not the hook being absent.
        """
        repo = _make_repo(tmp_path)
        out = _mint(repo, "--prefix", CYRILLIC_TE, "--text", "(DIR-1) a new unit")
        assert out.returncode == 0, out.stderr
        subject = _git(repo, "log", "-1", "--format=%s").strip()
        assert "CASCADE-IDS.md" in subject
        assert f"{CYRILLIC_TE}-9" in subject

    def test_it_commits_only_the_registry(self, tmp_path: Path) -> None:
        """§4.4 — a parallel session's staged note is neither committed nor unstaged.

        This is also a second, independent probe of the commit-msg hook: had the
        mint committed the index instead of the pathspec, the stranger's note
        would have been in the commit and the hook would have refused it.
        """
        repo = _make_repo(tmp_path)
        stranger = repo / ".claude" / "plans" / "stranger-note.md"
        stranger.write_text("another session's note\n", encoding="utf-8")
        _git(repo, "add", "--", ".claude/plans/stranger-note.md")

        out = _mint(repo, "--prefix", CYRILLIC_TE, "--text", "(DIR-1) a new unit")
        assert out.returncode == 0, out.stderr

        committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
        assert committed == [REGISTRY_REL]

        still_staged = _git(repo, "diff", "--cached", "--name-only").split()
        assert ".claude/plans/stranger-note.md" in still_staged

    def test_help_names_the_hole_the_lock_does_not_close(self, tmp_path: Path) -> None:
        """§4.6 — the uncovered case is two different checkouts.

        It also has to name the two writers on main the lock does not serialise
        either, since the first draft of the help claimed the lock covered every
        process in one working tree, which it does not.
        """
        out = _mint(tmp_path, "--help")
        assert out.returncode == 0
        assert "DOES NOT CLOSE" in out.stdout
        body = out.stdout.lower()
        assert "checkout" in body
        assert "worktree-finish.sh" in body
        assert "git commit" in body


class TestSeamCannotWriteAnywhere:
    """CB-138 — the seam used to `: > "$MARKER"` at a caller-supplied PATH, and
    an acceptance walked it straight into the real registry: rc=0, a "minted"
    report, and the registry truncated from 45 lines to one — landed on main
    by the very commit this script issues, because the seam sat AFTER the
    "registry is clean" gate and BEFORE the rollback trap was armed, so
    neither defence saw it.

    The fix removes the path capability outright: `CASCADE_MINT_TEST_MARKER`
    is a boolean now, and its only effect is one line on the process's OWN
    stderr. This test performs the ORIGINAL attack verbatim — pointing the
    variable at the real registry's path — and proves it does nothing: the
    mint completes normally and the registry carries exactly the one line the
    mint itself appended, never a truncation.
    """

    def test_the_marker_pointed_at_the_registry_does_not_touch_it(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        before = _registry(repo)
        env = _env()
        # The exact value the T-29 acceptance used: the real registry's path.
        env["CASCADE_MINT_TEST_MARKER"] = str(repo / REGISTRY_REL)
        out = subprocess.run(
            ["bash", str(SCRIPT), "--prefix", CYRILLIC_TE, "--text", "(DIR-1) something"],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        after = _registry(repo)
        assert after.splitlines()[:-1] == before.splitlines(), (
            "the registry's original lines were disturbed — the marker touched "
            "the path it was pointed at"
        )
        assert after.splitlines()[-1] == f"- {CYRILLIC_TE}-9 — (DIR-1) something"
        assert len(after.splitlines()) == len(before.splitlines()) + 1


class TestConcurrentMint:
    """§4.2 — the discriminating test, and the one the mutation probe targets.

    Two mints race. The first is given `CASCADE_MINT_TEST_DELAY`, which sleeps
    between COMPUTING the number and WRITING it — the exact window collision #3
    fell through. With the lock, the second caller blocks before computing and
    therefore recomputes after the first has committed. Without it, both have
    already computed the SAME number and this test goes red.

    Removing `flock -w 60 9` from the script must make this test fail; that
    mutation was run, and the assertion below is what caught it.

    CB-138: the handshake used to be a FILE the first process was told to
    create at a caller-supplied path — the seam an acceptance walked into the
    real registry. It is now a line on the first process's OWN stderr, read by
    a background thread that drains the pipe continuously (so the child can
    never block on a full pipe buffer) and hands the line to the main thread
    over a `queue.Queue`. Waiting on that queue is still an event — a blocking
    read that returns the instant the child writes, never a poll — not a timer;
    a bounded `queue.Empty` retry loop is what turns "the first mint never
    reached the window" into an assertion instead of a hang.
    """

    def test_two_concurrent_mints_get_two_different_ids(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        slow_env = _env()
        slow_env["CASCADE_MINT_TEST_DELAY"] = "5"
        slow_env["CASCADE_MINT_TEST_MARKER"] = "1"
        first = subprocess.Popen(
            ["bash", str(SCRIPT), "--prefix", CYRILLIC_TE, "--text", "(DIR-1) first"],
            cwd=str(repo),
            env=slow_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Drain first's stderr continuously from a background thread — both to
        # detect the handshake line and so the child can never deadlock writing
        # to a pipe nobody is reading while it sleeps out the delay.
        stderr_lines: list[str] = []
        handshake_q: "queue.Queue[str]" = queue.Queue()

        def _pump() -> None:
            assert first.stderr is not None
            for line in first.stderr:
                stderr_lines.append(line)
                handshake_q.put(line)

        pump_thread = threading.Thread(target=_pump, daemon=True)
        pump_thread.start()

        # HANDSHAKE, not a timer. A fixed sleep only HOPES the first mint has
        # reached the window; on a loaded machine the second could finish first,
        # and the two ids would then differ with no lock at all — a green test
        # over a broken gate. The line is written inside the lock, after the
        # number has been computed, so blocking on it is proof of the state
        # this test needs.
        deadline = time.monotonic() + 60
        handshake_seen = False
        while time.monotonic() < deadline:
            assert first.poll() is None, "the first mint exited before it computed an id"
            try:
                line = handshake_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if "CASCADE_MINT_TEST_MARKER" in line:
                handshake_seen = True
                break
        assert handshake_seen, "the first mint never reached the window"

        second = subprocess.Popen(
            ["bash", str(SCRIPT), "--prefix", CYRILLIC_TE, "--text", "(DIR-2) second"],
            cwd=str(repo),
            env=_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert first.wait(timeout=180) == 0, "".join(stderr_lines)
        out1 = first.stdout.read() if first.stdout is not None else ""
        pump_thread.join(timeout=5)
        out2, err2 = second.communicate(timeout=180)

        assert second.returncode == 0, err2

        id1, id2 = out1.strip(), out2.strip()
        assert id1 != id2, (
            "both mints computed the same id — the window between computing the "
            "number and writing it was not serialised"
        )
        assert {id1, id2} == {f"{CYRILLIC_TE}-9", f"{CYRILLIC_TE}-10"}

        text = _registry(repo)
        assert f"- {id1} — " in text
        assert f"- {id2} — " in text
        assert _git(repo, "status", "--porcelain", "--", REGISTRY_REL).strip() == ""


class TestScriptIsPresentAndExecutable:
    def test_the_script_exists_and_parses(self) -> None:
        assert SCRIPT.exists(), f"{SCRIPT} is missing"
        assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"
        assert shutil.which("flock") is not None, "flock(1) is required by the mint script"
        parsed = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert parsed.returncode == 0, parsed.stderr

    def test_the_lock_is_taken_before_the_number_is_computed(self) -> None:
        """Structural pin: computing INSIDE the lock is the whole unit.

        Collision #3 happened with the read protected and the computation not,
        so a refactor that moves `_compute_next_id` above the `flock` restores
        the defect while every behavioural test still passes on a fast machine.
        """
        lines = SCRIPT.read_text(encoding="utf-8").splitlines()
        # By STRIPPED LINE, not by substring: `true  # flock -w 60 9` satisfies a
        # substring search while doing nothing, so the earlier version of this
        # assert would have certified the very mutant it exists to catch
        # (cross-model review).
        lock_at = [i for i, ln in enumerate(lines) if ln.strip() == "if ! flock -w 60 9; then"]
        compute_at = [
            i for i, ln in enumerate(lines) if ln.strip().startswith("NEXT_ID=$(_compute_next_id)")
        ]
        assert len(lock_at) == 1, f"expected exactly one flock acquisition, found {len(lock_at)}"
        assert len(compute_at) == 1, f"expected exactly one compute call, found {len(compute_at)}"
        assert lock_at[0] < compute_at[0]
