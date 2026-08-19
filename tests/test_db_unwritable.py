"""CB-86 — an unwritable tracker reports itself, and is not told to run `init`.

THE CLASSIFIER IS TESTED THROUGH REAL `sqlite3.OperationalError` INSTANCES, never
a stub class. `sqlite_errorcode` is assignable on CPython 3.13 (measured), and
`tests/test_claims.py:482` already does exactly this for `is_contention`. A stub
that is not an `OperationalError` instance could never be caught by the
`except sqlite3.OperationalError` arm the fix adds, so a stub-based test would be
VACUOUS with respect to that arm while looking like coverage.

THE FOUR END-TO-END SHAPES RAISE FROM FOUR DIFFERENT STATEMENTS, ACROSS THE TWO
`except` HANDLERS `_open` HAS — and it is the second number the coverage argument
actually rests on, so both are named:
    A  read-only DIRECTORY, walk route  -> `PRAGMA journal_mode=WAL`   | handler 2
    B  read-only FILE, walk route       -> `entry.ensure_fn(conn)`,     | handler 2
                                           i.e. `merge.ensure_schema`, several frames down
    C  chmod 000, named route           -> `sqlite3.connect(uri)`       | handler 1
    C2 chmod 000, walk route            -> `sqlite3.connect(path)`      | handler 2
Shape B is the one that had to be verified rather than assumed: it raises from
another module and still lands inside `_open`, which is the property the whole
design rests on. Shape C is the only one on handler 1, which is also the only
handler that must choose between two exception types.

POSIX-only: every shape is built from Unix permission bits.
"""

from __future__ import annotations

import ast
import os
import pathlib
import sqlite3
import subprocess
import sys
import types

import pytest

from codebugs import db

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="every shape here is built from POSIX permission bits"
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _op_error(code: int, message: str = "boom") -> sqlite3.OperationalError:
    """A REAL OperationalError carrying `code`. See the module docstring."""
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = code
    return exc


def _cli(project: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
    env.pop(db.ENV_ROOT, None)
    return subprocess.run(
        [sys.executable, "-m", "codebugs.cli", *args],
        capture_output=True, text=True, cwd=str(project), env=env,
    )


@pytest.fixture()
def project(tmp_path):
    """An initialized tracker whose permissions are ALWAYS restored.

    The restore is unconditional on purpose: a test that fails midway would
    otherwise leave a read-only directory inside `tmp_path` for pytest's retention
    sweep and for anything else that walks the tree.
    """
    db.init_project(str(tmp_path))
    root = tmp_path / db.DB_DIR
    dbfile = root / db.DB_FILE
    try:
        yield types.SimpleNamespace(dir=tmp_path, root=root, dbfile=dbfile)
    finally:
        if root.exists():
            os.chmod(root, 0o755)
        if dbfile.exists():
            os.chmod(dbfile, 0o644)


class TestIsEnvironmental:
    """The allowlist, and — more importantly — what it must REFUSE."""

    @pytest.mark.parametrize(
        "code,name",
        [(8, "SQLITE_READONLY"), (10, "SQLITE_IOERR"), (13, "SQLITE_FULL"), (14, "SQLITE_CANTOPEN")],
    )
    def test_environmental_codes_are_classified(self, code, name):
        assert db._is_environmental(_op_error(code)) is True, name

    def test_the_extended_readonly_directory_code_masks_down(self):
        """1544 is SQLITE_READONLY_DIRECTORY, which a read-only tracker directory
        actually raises — measured. Without the `& 0xFF` mask the most common
        shape of this whole card would be unclassified."""
        assert 1544 & 0xFF == 8
        assert db._is_environmental(_op_error(1544)) is True

    def test_a_programming_error_is_refused(self):
        """SQLITE_ERROR (1) is what a bug in this package raises — `no such
        column`, `cannot start a transaction within a transaction`. Classifying it
        would launder a real defect into "check your permissions" and delete the
        traceback that finds it."""
        assert db._is_environmental(_op_error(1, "no such column: nope")) is False

    @pytest.mark.parametrize("code", [5, 6, 517])
    def test_contention_is_not_environmental(self, code):
        """The two sets must stay disjoint. `claims.py` tells a caller to re-issue
        an `undetermined` call; telling it to retry a full disk forever is the
        opposite of what that contract needs."""
        assert db._is_environmental(_op_error(code)) is False
        assert db.is_contention(_op_error(code)) is True

    def test_an_exception_with_no_code_is_refused(self):
        assert db._is_environmental(ValueError("not a sqlite error")) is False


class TestCantopenIsAmbiguousAndExistenceDecides:
    """SQLITE_CANTOPEN (14) means BOTH "not there" and "there, but not for you".

    Measured: `exists=False` and `exists=True` both give
    `SQLITE_CANTOPEN: unable to open database file`. So the code alone cannot
    choose the message, and classifying 14 as environmental unconditionally would
    tell someone whose tracker is genuinely MISSING that their permissions are
    wrong. These two tests are the split, and the second is the regression guard.
    """

    def test_an_unreadable_but_present_database_is_unwritable(self, project):
        os.chmod(project.dbfile, 0o000)
        with pytest.raises(db.TrackerUnwritableError) as excinfo:
            db._open(str(project.dbfile), create=False)
        assert "for writing" in str(excinfo.value)
        assert "codebugs init" not in str(excinfo.value)

    def test_the_second_handler_also_raises_the_TYPE(self, tmp_path, monkeypatch):
        """Both handlers must produce `TrackerUnwritableError`, and only the direct
        tests can assert the TYPE — the end-to-end tests see a subprocess's stderr,
        so their gate is the substring `"for writing"`, which cannot tell the two
        arms apart. Without this, handler 2 was pinned only by that substring.
        """
        boom = types.SimpleNamespace(
            name="readonly",
            ensure_fn=lambda conn: (_ for _ in ()).throw(
                _op_error(1544, "attempt to write a readonly database")
            ),
        )
        monkeypatch.setattr(db, "_resolved_order", lambda: [boom])
        with pytest.raises(db.TrackerUnwritableError) as excinfo:
            db._open(str(tmp_path / "fresh.db"), create=True)
        assert "codebugs init" not in str(excinfo.value)

    def test_a_missing_database_is_still_NOT_FOUND(self, tmp_path):
        """`_open(create=False)` on a path that is not there must keep saying
        `run codebugs init`. This is the CB-23 race window — the resolver's
        `isfile` check passed and the file vanished before the open — and it is
        the one case the existence split exists to protect."""
        missing = tmp_path / "gone.db"
        with pytest.raises(db.DatabaseNotFoundError) as excinfo:
            db._open(str(missing), create=False)
        assert "codebugs init" in str(excinfo.value)


class TestNonEnvironmentalFailuresKeepTheirTraceback:
    """The allowlist must fail toward the STATUS QUO, not toward a wrong answer.

    BOTH TESTS HERE PASS AGAINST THE UNFIXED TREE TOO, and that is correct rather
    than a gap — say it in the docstring, per CLAUDE.md, or a reader cannot tell
    these from broken tests. They pin behaviour this change deliberately
    PRESERVES: before the fix `_open` had no `except` at all, so of course these
    errors propagated. There is no state in which they discriminate, because the
    defect being fixed is about which failures get CONVERTED, and these are the
    ones that must not be. What they guard is the future: a later widening of the
    allowlist, or a `try` moved one line outwards, turns them red.

    (An earlier draft of the first test also asserted `is_environmental(...) is
    False`, which made it appear to fail against main — but on an `AttributeError`
    for the missing helper, not on behaviour. A test that looks discriminating for
    the wrong reason is worse than one honestly labelled, so the assertion is
    gone.)
    """

    def test_a_schema_error_inside_the_ensure_loop_still_propagates(self, tmp_path, monkeypatch):
        """The `ensure_fn` loop is the deepest of the three raise sites, so it is
        where a mis-scoped `except` would do the most damage — swallowing a real
        SQLITE_ERROR from a module's own schema and reporting it as a permissions
        problem."""
        boom = types.SimpleNamespace(
            name="exploding", ensure_fn=lambda conn: (_ for _ in ()).throw(_op_error(1, "no such table"))
        )
        monkeypatch.setattr(db, "_resolved_order", lambda: [boom])
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            db._open(str(tmp_path / "fresh.db"), create=True)
        assert "no such table" in str(excinfo.value)

    def test_contention_inside_the_ensure_loop_still_propagates(self, tmp_path, monkeypatch):
        """Re-raised untouched so `cli.main`'s exit-5 arm still sees it. Converting
        contention here would turn "retry shortly" into "check your permissions"."""
        boom = types.SimpleNamespace(
            name="busy", ensure_fn=lambda conn: (_ for _ in ()).throw(_op_error(5, "database is locked"))
        )
        monkeypatch.setattr(db, "_resolved_order", lambda: [boom])
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            db._open(str(tmp_path / "fresh.db"), create=True)
        assert db.is_contention(excinfo.value) is True


class TestTheFourShapesEndToEnd:
    """Four shapes, four code paths — see the module docstring.

    `"Traceback" not in stderr` is the assertion that DISCRIMINATES: exit 1 is
    satisfied by the unfixed tree too, because an uncaught exception already exits
    1. `tests/test_bench.py:710-718` records that lesson in this repo's own words.
    """

    def _assert_clean_refusal(self, r):
        assert r.returncode == 1, r.stdout + r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert "for writing" in r.stderr, r.stderr
        assert "codebugs init" not in r.stderr, r.stderr

    def test_A_read_only_tracker_directory(self, project):
        os.chmod(project.root, 0o555)
        self._assert_clean_refusal(
            _cli(project.dir, "add", "-f", "x.py", "-c", "t", "-s", "low", "-d", "shape A")
        )

    def test_B_read_only_database_file(self, project):
        os.chmod(project.dbfile, 0o444)
        self._assert_clean_refusal(
            _cli(project.dir, "add", "-f", "x.py", "-c", "t", "-s", "low", "-d", "shape B")
        )

    def test_C2_unopenable_database_on_the_walk_route(self, project):
        os.chmod(project.dbfile, 0o000)
        self._assert_clean_refusal(_cli(project.dir, "stats"))

    def test_C_unopenable_database_on_the_named_route(self, project, tmp_path):
        """The named route is the one that was ALREADY quiet and ALREADY wrong: it
        printed `run codebugs init` for a permission failure — advice that cannot
        work, over a tracker that exists. So here the discriminator is not the
        absence of a traceback (there never was one) but the absence of that
        instruction."""
        os.chmod(project.dbfile, 0o000)
        elsewhere = tmp_path.parent
        r = _cli(elsewhere, "--tracker-root", str(project.dir), "stats")
        self._assert_clean_refusal(r)


class TestTheHealthyPathIsUntouched:
    """A pure-refactor guard: the fix wraps the body of the one function every
    command goes through, so the cheapest way to break everything is a mis-scoped
    `try`."""

    def test_a_writable_tracker_still_works(self, project):
        r = _cli(project.dir, "add", "-f", "x.py", "-c", "t", "-s", "low", "-d", "healthy")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Added:" in r.stdout, r.stdout


class TestTheClassifierStaysInsideDb:
    """The rejected design must stay a DELIBERATE act, not a two-line tidy-up.

    CB-86's whole point is that environmental failures are classified inside
    `_open` and raised as a TYPE — never classified at the `cli.main` boundary,
    which structurally cannot tell a pre-write failure from a post-commit one.
    `cli.py` already has an `except sqlite3.OperationalError` arm, so if the
    classifier were reachable from outside `db`, re-introducing the rejected
    design would be one `if` inside an arm that already exists, and it would look
    like an obvious cleanup. It would silently delete the discriminator
    `tests/test_bench.py:789` protects.

    Prose cannot refuse that, which this repo has established several times over —
    `TestOpenCallSitesRatchet`, the `BEGIN IMMEDIATE` count in `test_claims.py`,
    and `TestWriteCallSitesRatchet`. So it is pinned here.

    BY AST, NOT BY SOURCE TEXT — and the first draft of this ratchet was the text
    version, which CB-99 then tripped FALSELY: that card's fix carries a comment
    explaining *why it does not* reach for this classifier, and merely naming it
    in prose turned the suite red. CLAUDE.md already records this exact lesson for
    `TestWriteCallSitesRatchet` ("the first draft of that ratchet grepped source
    text and matched `open(path, "w")` inside three of `fsio.py`'s own
    docstrings"), so it was made one iteration after being cited. A guard that
    cannot tell a reference from a mention punishes the documentation that keeps
    the rule understood.

    RESIDUAL LIMIT, stated rather than implied: this is still a NAME check, now
    over the syntax tree. A caller could re-derive the predicate by hand or reach
    it through `getattr`. It bounds accident, not intent — the honest claim for
    every ratchet in this repo that keys on a name rather than on a value like a
    file mode.
    """

    def test_only_db_names_the_environmental_classifier(self):
        src = pathlib.Path(db.__file__).parent
        offenders = []
        for path in sorted(src.rglob("*.py")):
            if path.name == "db.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                named = (
                    node.attr
                    if isinstance(node, ast.Attribute)
                    else node.id
                    if isinstance(node, ast.Name)
                    else None
                )
                if named == "_is_environmental":
                    offenders.append(
                        f"{path.relative_to(src.parent).as_posix()}:{node.lineno}"
                    )
        assert offenders == [], (
            "the environmental classifier is private to db.py on purpose — a caller "
            f"at the CLI boundary is the design CB-86 rejected. Found: {offenders}"
        )

    def test_the_public_surface_did_not_grow(self):
        """`is_contention` is public because `cli.main` must call it. Its sibling
        is not, and a rename to `is_environmental` would quietly re-open the door
        the test above closes."""
        assert hasattr(db, "is_contention")
        assert not hasattr(db, "is_environmental")
