"""CB-204 oracle: the suite refuses to run when its temporary tree is captured.

`db.connect()` walks UP looking for an existing `.codebugs/`. That is intended
product behaviour and is not what this unit changes. What it changes is that a
tracker sitting at or above pytest's temporary root — an empty `/tmp/.codebugs`
some tool left behind was the real, twice-observed case — no longer presents
itself as a thousand failures spread through the run. It is one named refusal,
before any test executes.

THE ROWS BELOW ARE A PROPERTY, NOT A LIST OF CAUSES (brief §6). The state
"a foreign tracker is above the temporary root" is reachable several ways, and
they are covered as one predicate — *would the product's own resolution escape
the temporary tree?* — rather than as four hand-enumerated causes:

  * an EMPTY `.codebugs/` directory                     → refuse
  * a `.codebugs/` holding a real `findings.db`         → refuse
  * one reachable only by following a `.git` FILE to a
    linked worktree's main checkout                     → refuse
  * one sitting ABOVE a `.git` DIRECTORY, so the walk
    stops before it and it is unreachable               → do NOT refuse
  * a root declared through `CODEBUGS_ROOT`             → do NOT refuse
  * nothing above at all                                → do NOT refuse

The last three are the FALSE-ALARM oracle and they are not a formality. A guard
that goes red on a healthy machine is removed by the first person it
inconveniences, and then the defect it was written for comes straight back.

Each row runs a REAL pytest session against this repository's own `tests/`
directory, so the `tests/conftest.py` under test is the one that is loaded.
Running a copied conftest in a scratch directory would test a copy — which is
the exact failure mode this whole unit exists to close.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codebugs import db
from tests.conftest import _hermeticity_refusal

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFTEST = REPO_ROOT / "tests" / "conftest.py"

# A cheap real test from this suite. It touches no database, so a row that
# fails tells us about the guard rather than about the canary.
CANARY = "tests/test_types.py::TestConstants::test_terminal_statuses_keys"

# A canary that DOES depend on the declared-root fixture: it shells out to
# `codebugs where` and asserts a healthy discovered tracker. With `CODEBUGS_ROOT`
# live it would resolve through the declaration instead and exit 1, so this row
# cannot pass vacuously.
CANARY_DECLARED_ROOT = (
    "tests/test_db_infra.py::TestCb182WhereWritabilityStreamParity::test_row1_healthy_plain"
)

REFUSAL_ANCHOR = "REFUSED TO RUN"


def _inner_pytest(basetemp, *, node=CANARY, env=None):
    """Run one real pytest session of THIS repository under a chosen temp root."""
    environ = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "--basetemp", str(basetemp)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=environ,
    )


def _output(proc):
    return proc.stdout + proc.stderr


def _layout(tmp_path, name):
    """A private stage with a place for a foreign tracker and a temp root below it.

    Everything lives inside the caller's own `tmp_path`, so no row can leave a
    stray `.codebugs/` where another agent's run would walk into it — the very
    accident under repair.
    """
    stage = tmp_path / name
    (stage / "bt").mkdir(parents=True)
    return stage, stage / "bt"


class TestTheGuardRefusesACapturedTemporaryTree:
    def test_an_empty_foreign_tracker_above_the_temp_root_refuses(self, tmp_path):
        """The literal twice-observed incident: an empty `.codebugs` left behind."""
        stage, basetemp = _layout(tmp_path, "empty")
        (stage / ".codebugs").mkdir()

        proc = _inner_pytest(basetemp)

        assert proc.returncode == pytest.ExitCode.USAGE_ERROR, _output(proc)
        out = _output(proc)
        assert REFUSAL_ANCHOR in out
        assert str(stage) in out, "the refusal must name the tracker it found"
        assert str(basetemp) in out, "and the temporary root it was judging"

    def test_a_foreign_tracker_holding_a_real_database_refuses(self, tmp_path):
        """SAME state, different way of building it — a real initialized tracker.

        `_find_db_root` keys on the DIRECTORY, so this row and the one above must
        agree; if they ever diverge, the guard has grown a second rule.
        """
        stage, basetemp = _layout(tmp_path, "real")
        db.init_project(str(stage))

        proc = _inner_pytest(basetemp)

        assert proc.returncode == pytest.ExitCode.USAGE_ERROR, _output(proc)
        assert REFUSAL_ANCHOR in _output(proc)
        assert str(stage) in _output(proc)

    def test_a_tracker_reachable_only_through_a_worktree_pointer_refuses(self, tmp_path):
        """The walk JUMPS; a parent loop does not (brief §5).

        A linked worktree's `.git` is a FILE pointing at the main repository, and
        `db._find_db_root` follows it. Here the tracker is NOT an ancestor of the
        temporary root by any parent chain — it is only reachable through that
        jump — so a guard that had re-implemented the walk as a climb to `/`
        would MISS it and report a captured tree as clean.
        """
        repo = tmp_path / "repo"
        (repo / ".codebugs").mkdir(parents=True)
        _git("init", "-b", "main", ".", cwd=repo)
        (repo / "f.txt").write_text("x")
        _git("add", "f.txt", cwd=repo)
        _git("commit", "-m", "init", cwd=repo)
        wt = tmp_path / "elsewhere"
        _git("worktree", "add", str(wt), "-b", "wt", cwd=repo)
        assert (wt / ".git").is_file(), "premise: git writes a .git FILE for a worktree"
        basetemp = wt / "bt"

        proc = _inner_pytest(basetemp)

        assert proc.returncode == pytest.ExitCode.USAGE_ERROR, _output(proc)
        assert str(repo.resolve()) in _output(proc)


class TestTheGuardIsSilentOnAHealthyMachine:
    """The false-alarm oracle (brief §6). A guard nobody trusts is a guard nobody keeps."""

    def test_a_clean_temporary_tree_runs(self, tmp_path):
        _stage, basetemp = _layout(tmp_path, "clean")

        proc = _inner_pytest(basetemp)

        assert proc.returncode == 0, _output(proc)
        assert REFUSAL_ANCHOR not in _output(proc)

    def test_a_tracker_above_a_git_directory_does_not_refuse(self, tmp_path):
        """A `.git` DIRECTORY ends the walk, so the tracker above it is unreachable.

        This is the row a naive parent-climb fails in the other direction: it
        would see `.codebugs` overhead and refuse a run that is perfectly fine.
        The premise itself is measured rather than assumed, immediately below.
        """
        stage = tmp_path / "boundary"
        (stage / ".codebugs").mkdir(parents=True)
        inner = stage / "inner"
        (inner / ".git").mkdir(parents=True)
        basetemp = inner / "bt"
        basetemp.mkdir()
        assert db._find_db_root(str(basetemp)) is None, (
            "premise: the walk stops at a .git directory, so this tracker is unreachable"
        )

        proc = _inner_pytest(basetemp)

        assert proc.returncode == 0, _output(proc)
        assert REFUSAL_ANCHOR not in _output(proc)

    def test_a_declared_root_does_not_refuse_and_is_still_neutralized(self, tmp_path):
        """`CODEBUGS_ROOT` reaches the tests through a different channel entirely.

        The autouse fixture in `conftest.py` clears it before every test, so it
        cannot capture anything and refusing on it would be a false alarm. The
        canary is chosen so this cannot pass vacuously: it shells out to
        `codebugs where` and asserts a healthy DISCOVERED tracker, which a live
        declaration would turn into an unresolved root at exit 1.
        """
        stage, basetemp = _layout(tmp_path, "declared")
        declared = stage / "declared-elsewhere"
        declared.mkdir()

        proc = _inner_pytest(basetemp, node=CANARY_DECLARED_ROOT, env={db.ENV_ROOT: str(declared)})

        assert proc.returncode == 0, _output(proc)
        assert REFUSAL_ANCHOR not in _output(proc)


class TestTheGuardUsesTheProductsOwnWalk:
    """Structural pins, because the behavioural rows above cannot see a COPY.

    A hand-rolled climb would pass every refusing row here and every silent row
    except the two that were built precisely to discriminate it. These pins say
    the rule outright: the guard asks the product, and starts where pytest
    itself starts.
    """

    def _guard(self):
        tree = ast.parse(CONFTEST.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and "captured_by_a_foreign_tracker" in node.name:
                return node
        raise AssertionError("the CB-204 session guard is gone from tests/conftest.py")

    def test_the_walk_is_the_products_own_function(self):
        called = {
            n.func.attr
            for n in ast.walk(self._guard())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "_find_db_root" in called, (
            "the guard must ask db for the walk, never re-implement it: the walk "
            "stops at a .git directory and jumps through a .git file, and a copy "
            "would both alarm falsely and miss real captures"
        )

    def test_the_start_point_is_the_factory_pytest_itself_uses(self):
        called = {
            n.func.attr
            for n in ast.walk(self._guard())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "getbasetemp" in called, (
            "the start point must come from tmp_path_factory, not from a literal "
            "/tmp: measured, --basetemp and TMPDIR both move it"
        )

    def test_no_hand_rolled_parent_climb_survives_beside_it(self):
        guard = self._guard()
        attrs = {n.attr for n in ast.walk(guard) if isinstance(n, ast.Attribute)}
        assert not ({"parent", "parents", "dirname"} & attrs), (
            "a parent climb next to the delegated walk is a second copy of the rule"
        )


NOTE_ANCHOR = "Note the same refusal fires"


class TestTheWayOutIsSafeToCopyInAHurry:
    """CB-214: the exits are read by someone who has just been stopped.

    The guard above is right to refuse, and right to offer a way out — a gate
    with no way out is a wall rather than a diagnostic. But one of the two exits
    it offered names a pytest flag that DELETES the directory it is given,
    recursively and without a word. Measured 2026-08-26 on this tree's pytest:
    a file and a whole subdirectory placed under a `--basetemp` path are gone
    after one run, at exit 0 and under a cheerful `95 passed`, while the same
    two under `TMPDIR` survive untouched and pytest merely ADDS its own subtree
    beside them. The two exits nonetheless stood as equals, with the
    directory-deleting one first, and whoever reads this message has just been
    told the suite will not run: they are in a hurry and they copy the first
    line that fits. `/some/other/place` is then an existing directory, because
    that is how the word "place" reads.

    So the repair is a property of the TEXT rather than of the guard's decision,
    and that is why these rows read the message instead of running a session:
    the exits run safe → destructive → deleting by hand, the deletion is named
    where it is offered, and the emptiness check says what its own refusal
    means. Each row is written so that undoing one of those three turns exactly
    that row red.
    """

    def _message(self):
        """Rendered with placeholder paths, so no row depends on this machine."""
        return _hermeticity_refusal("/BASETEMP", "/FOREIGN")

    def _ways_out(self):
        """The two exits, cut off BEFORE the trailing note about this repository.

        Cutting at the note rather than at the end of the string is deliberate,
        not tidiness: that note mentions `--basetemp` as well, so folding it
        into the last bullet would let a warning that lives only down there
        satisfy a row asking about the ADVICE — a check that cannot fail.
        """
        msg = self._message()
        assert NOTE_ANCHOR in msg, "the trailing note is the boundary these rows cut at"
        bullets = msg[msg.index("ways out") : msg.index(NOTE_ANCHOR)].split("\n  * ")[1:]
        assert len(bullets) == 2, f"the message must offer exactly two exits, found {len(bullets)}"
        return bullets

    def test_the_exits_run_from_safe_to_destructive(self):
        safe, litter = self._ways_out()
        assert "TMPDIR" in safe, "the first exit must be the one that destroys nothing"
        assert "is litter" in litter, "and deleting a directory by hand must come after it"
        assert safe.index("TMPDIR") < safe.index("--basetemp"), (
            "inside the surviving exit the safe spelling must still be read first: "
            "swapping those two lines is the same defect at a smaller grain, and a "
            "hurried reader never reaches the second one"
        )

    def test_the_flag_that_deletes_says_so_where_it_is_offered(self):
        """Named at the point of use, because that is the line being copied."""
        safe, _litter = self._ways_out()
        warning = safe[safe.index("--basetemp") :]
        assert "DELETES" in warning, (
            "`--basetemp` must state that pytest destroys the directory it names"
        )
        assert "recursively" in warning, "and that it goes the whole way down, not one level"
        assert "mktemp -d" in warning, (
            "and hand over a whole safe form to copy: a reader in a hurry copies "
            "rather than composes, so advice that must be adapted is advice that "
            "will be pointed at an existing directory"
        )

    def test_the_emptiness_check_says_what_its_own_refusal_means(self):
        """The check answers correctly and LOOKS like a mistake (brief §2(3)).

        `codebugs --tracker-root <dir> stats` over a `.codebugs/` holding no
        database exits 1 saying `holds no findings.db`. That is the right answer
        to the question asked — there is no tracker there, the directory is
        litter — but at a glance it reads as "you typed the command wrong", and
        the reader stops. No verb can be swapped in to avoid it: a DECLARED root
        fails closed before any verb body runs (CB-23), measured the same day on
        `stats`, `where`, `summary` and `categories` alike. So the message has to
        carry the reading, in both directions.
        """
        _safe, litter = self._ways_out()
        assert "holds no findings.db" in litter, "the answer the reader will actually see"
        assert "not a typo" in litter, "said to be a confirmation rather than a mistake"
        assert "prints statistics" in litter, (
            "and the other direction too — a real tracker must not be deleted, so "
            "an answer with rows in it has to be named as the stop signal"
        )

    def test_the_note_about_a_basetemp_inside_the_repository_survives_once(self):
        """Untouched by this unit, and it must not have been duplicated either."""
        msg = self._message()
        assert msg.count(NOTE_ANCHOR) == 1
        assert "not a false alarm" in msg


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )
