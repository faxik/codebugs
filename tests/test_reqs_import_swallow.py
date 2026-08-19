"""CB-99 — `reqs-import` must not count an environmental failure as a bad row.

`import_markdown` wrapped its per-row INSERT in `except sqlite3.Error`, the whole
sqlite exception tree. A full disk or an I/O error arriving mid-import was
therefore counted as a malformed ROW, and the import reported success: measured
against 593c924, a simulated `SQLITE_FULL` gave `{'imported': 0, 'skipped': 2}`
with no exception and `Imported 0 requirements, skipped 2.` at exit 0.

WHAT DISCRIMINATES. `skipped == 2` is what the UNFIXED tree produces, so asserting
on the count alone cannot fail against it. The discriminator is that the
exception ESCAPES — `pytest.raises` — and, at the CLI, that stderr carries a
traceback rather than a success line at exit 0. Both are the inverse of the usual
assertion in this repo, and deliberately so: here the traceback is the correct
outcome, because the failure arrives after work has begun. That is the same rule
`tests/test_bench.py:789` states and the reason CB-86 refused to classify
post-connect failures.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from codebugs import db, reqs

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _env_error(code: int, message: str) -> sqlite3.OperationalError:
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = code
    return exc


class _FailingConnection(sqlite3.Connection):
    """Raises a chosen sqlite error on the requirements INSERT, and only on it.

    A `sqlite3.Connection` subclass rather than a mock, because `import_markdown`
    is handed a real connection and the arm under test catches real sqlite
    exception classes — a stub that is not one could never exercise it, which is
    the vacuity trap `tests/test_db_unwritable.py` records for the same reason.
    """

    to_raise: BaseException | None = None

    def execute(self, sql, *args):  # noqa: D102 - mirrors sqlite3.Connection
        if type(self).to_raise is not None and sql.lstrip().startswith(
            "INSERT OR REPLACE INTO requirements"
        ):
            raise type(self).to_raise
        return super().execute(sql, *args)


@pytest.fixture()
def tracker(tmp_path):
    db.init_project(str(tmp_path))
    return tmp_path


def _markdown(tmp_path: pathlib.Path, *rows: str) -> str:
    path = tmp_path / "reqs.md"
    path.write_text("## Section\n" + "".join(rows))
    return str(path)


def _connect_failing(tracker, exc) -> _FailingConnection:
    conn = sqlite3.connect(
        str(tracker / db.DB_DIR / db.DB_FILE), factory=_FailingConnection
    )
    conn.row_factory = sqlite3.Row
    _FailingConnection.to_raise = exc
    return conn


@pytest.fixture(autouse=True)
def _reset_injection():
    yield
    _FailingConnection.to_raise = None


class TestAnEnvironmentalFailureEscapes:
    """The card's own defect, now reproduced rather than reasoned about."""

    @pytest.mark.parametrize(
        "code,message",
        [(13, "database or disk is full"), (10, "disk I/O error"), (8, "attempt to write a readonly database")],
    )
    def test_it_is_not_counted_as_a_skipped_row(self, tracker, tmp_path, code, message):
        md = _markdown(
            tmp_path,
            "| FR-100 | first | must | planned | s | t |\n",
            "| FR-101 | second | must | planned | s | t |\n",
        )
        conn = _connect_failing(tracker, _env_error(code, message))
        try:
            with pytest.raises(sqlite3.OperationalError) as excinfo:
                reqs.import_markdown(conn, md)
            assert message in str(excinfo.value)
        finally:
            conn.close()

    def test_nothing_is_committed_when_it_escapes(self, tracker, tmp_path):
        """A consequence of propagating rather than swallowing, and worth pinning:
        the commit is at the END of the loop, so a mid-import environmental
        failure now lands NOTHING instead of a partial import reported as
        success."""
        md = _markdown(
            tmp_path,
            "| FR-200 | first | must | planned | s | t |\n",
            "| FR-201 | second | must | planned | s | t |\n",
        )
        conn = _connect_failing(tracker, _env_error(13, "database or disk is full"))
        try:
            with pytest.raises(sqlite3.OperationalError):
                reqs.import_markdown(conn, md)
        finally:
            conn.close()

        check = db.connect(str(tracker))
        try:
            landed = check.execute(
                "SELECT COUNT(*) AS n FROM requirements WHERE id IN ('FR-200','FR-201')"
            ).fetchone()["n"]
        finally:
            check.close()
        assert landed == 0


class TestARowLevelFailureIsStillSkipped:
    """The affordance the arm was written for survives — narrowed, not removed."""

    def test_an_integrity_error_is_counted_and_the_import_continues(self, tracker, tmp_path):
        md = _markdown(
            tmp_path,
            "| FR-300 | first | must | planned | s | t |\n",
            "| FR-301 | second | must | planned | s | t |\n",
        )
        conn = _connect_failing(tracker, sqlite3.IntegrityError("CHECK constraint failed"))
        try:
            result = reqs.import_markdown(conn, md)
        finally:
            conn.close()
        assert result == {"imported": 0, "skipped": 2, "section": "Section"}


class TestTheArmIsASafetyNetNotALivePath:
    """Measured scope, recorded so nobody later 'fixes' a skipped count that is
    correctly always zero.

    With the resolvers normalising `status` and `priority` before the INSERT, and
    `INSERT OR REPLACE` foreclosing UNIQUE, no markdown row reachable through
    `_ROW_RE` can violate a constraint on this table. This test PASSES on both
    sides of the fix, deliberately: it pins the premise that made narrowing safe,
    not the narrowing itself.
    """

    def test_bogus_vocabulary_still_imports_cleanly(self, tracker, tmp_path):
        md = _markdown(
            tmp_path,
            "| FR-400 | fine | must | planned | s | t |\n",
            "| FR-401 | bogus | NOTAPRIORITY | NOTASTATUS | s | t |\n",
        )
        conn = db.connect(str(tracker))
        try:
            result = reqs.import_markdown(conn, md)
        finally:
            conn.close()
        assert result["imported"] == 2
        assert result["skipped"] == 0

    def test_a_check_violation_on_this_table_is_an_IntegrityError(self, tracker):
        """The premise the narrowing rests on: if a constraint violation here were
        an `OperationalError`, narrowing to `IntegrityError` would have silently
        turned a row-level problem into a crash."""
        conn = db.connect(str(tracker))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT OR REPLACE INTO requirements (id, section, description, priority, "
                    "status, source, test_coverage, tags, meta, created_at, updated_at) "
                    "VALUES ('FR-9','s','d','NOPE','planned','','','[]','{}','t','t')"
                )
        finally:
            conn.close()


class TestTheCliSurfacesIt:
    """End to end, because the whole point is what the operator sees."""

    def test_a_mid_import_environmental_failure_is_not_a_success_line(self, tracker, tmp_path):
        """Run the real CLI with the failure injected through a sitecustomize-style
        patch, so this exercises `_cmd_reqs_import`'s own arms — which catch
        `OSError`, not `sqlite3.Error`, and therefore must NOT absorb this."""
        md = _markdown(tmp_path, "| FR-500 | x | must | planned | s | t |\n")
        patch = tmp_path / "inject.py"
        # The injection goes through `sqlite3.connect`'s `factory=`, NOT by
        # monkeypatching `sqlite3.Connection.execute` — that raises
        # `TypeError: cannot set 'execute' attribute of immutable type`, and the
        # first draft of this test did exactly that. Worth recording rather than
        # silently fixing: the two obvious assertions (`returncode != 0`,
        # `"Imported" not in stdout`) were BOTH satisfied by that unrelated
        # TypeError, so the test would have passed for entirely the wrong reason.
        # Only the third assertion, keyed on the injected message, could tell.
        patch.write_text(
            "import sqlite3, sys\n"
            "class Boom(sqlite3.Connection):\n"
            "    def execute(self, sql, *a):\n"
            "        if sql.lstrip().startswith('INSERT OR REPLACE INTO requirements'):\n"
            "            e = sqlite3.OperationalError('database or disk is full')\n"
            "            e.sqlite_errorcode = 13\n"
            "            raise e\n"
            "        return super().execute(sql, *a)\n"
            "_real = sqlite3.connect\n"
            "def patched(*a, **kw):\n"
            "    kw.setdefault('factory', Boom)\n"
            "    return _real(*a, **kw)\n"
            "sqlite3.connect = patched\n"
            "sys.argv = ['codebugs', 'reqs-import', sys.argv[1]]\n"
            "from codebugs.cli import main\n"
            "main()\n"
        )
        env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
        env.pop(db.ENV_ROOT, None)
        r = subprocess.run(
            [sys.executable, str(patch), md],
            capture_output=True, text=True, cwd=str(tracker), env=env,
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "Imported" not in r.stdout, r.stdout
        assert "disk is full" in r.stderr, r.stderr
