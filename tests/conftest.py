"""Suite-wide protections that no single test file could hold on its own.

THIS FILE IS A DELIBERATE EXCEPTION to the project's "no shared conftest.py"
convention. It admits exactly one KIND of inhabitant: a property that protects
the whole suite, whose failure mode is silent or unattributable, and which every
future test file would otherwise have to remember for itself. Ordinary fixtures
are not that, and still belong in the file that uses them.

Both inhabitants are the same defect wearing two faces — a test that NAMES one
state and gets another, because `db.connect()` resolves against ambient state
the test never declared.

FIRST: neutralize an ambient tracker DECLARATION. `CODEBUGS_ROOT` redirects
every `db.connect()` in this process *and* in any subprocess that inherits the
environment. Test modules shell out to the CLI and run mutating verbs — `update`
in `test_findings.py`, `claim`/`release` in `test_claims.py` — relying on the
subprocess binding to its own `cwd`. With the variable exported, they bind to
whatever it names instead. Verified before this file existed, not theorized:
with `CODEBUGS_ROOT` pointing at a scratch tracker, running the findings CLI
tests rewrote that tracker's CB-1 from `low`/`open` to `high`/`fixed`. Pointed
at a developer's real tracker, `pytest` silently corrupts real findings.

SECOND: refuse the run outright when the DISCOVERY WALK — the channel the first
fixture deliberately leaves alone, because it is the product behaviour under
test — would capture the temporary tree (CB-204). See the guard below.

A per-file fixture would have to be remembered by every test module added later,
and the cost of forgetting is silent destruction of the developer's own data, or
a thousand failures pointing at code that is fine — exactly the kind of rule
that must not be an enumeration. Tests that exercise the override set the
variable themselves *after* the first fixture has run.
"""

import pytest

from codebugs import db


@pytest.fixture(autouse=True)
def _no_ambient_tracker_root(monkeypatch):
    monkeypatch.delenv(db.ENV_ROOT, raising=False)
    monkeypatch.setattr(db, "_tracker_root_override", None)


# --- CB-204: the discovery walk must not reach out of the temporary tree -----


def _hermeticity_refusal(basetemp: str, foreign_root: str) -> str:
    """The whole diagnostic, as one string, so the test can read it back.

    A gate with no way out is a wall rather than a diagnostic, so both exits
    are spelled out with the real paths filled in.
    """
    return (
        "\n"
        "codebugs test suite REFUSED TO RUN: the environment is not hermetic.\n"
        "\n"
        f"  A tracker was found at:  {foreign_root}/.codebugs\n"
        f"  pytest's temporary root: {basetemp}\n"
        "\n"
        "`db.connect()` walks UP from wherever it is called, looking for an\n"
        "existing `.codebugs/`. That is intended product behaviour, and it is\n"
        "why the tracker above captures every `tmp_path` fixture in this suite:\n"
        "a test that means to build a one-off tracker in its own temporary\n"
        "directory binds to the one above instead. Measured 2026-08-26, on the\n"
        "suite as it stood: 1071 of 2739 tests fail or error in that state,\n"
        "none for a reason that has anything to do with the code under test.\n"
        "\n"
        "This is refused once, here, instead of being discovered a thousand\n"
        "times in the middle of the run. Two ways out:\n"
        "\n"
        f"  * If {foreign_root}/.codebugs is litter — an empty directory some\n"
        "    tool left behind — delete it. Check first that it holds nothing:\n"
        f"        codebugs --tracker-root {foreign_root} stats\n"
        "\n"
        "  * If it is a real tracker you want to keep, move the temporary root\n"
        "    out from under it instead:\n"
        "        pytest tests/ --basetemp=/some/other/place\n"
        "    or export TMPDIR=/some/other/place before running pytest.\n"
        "\n"
        "Note the same refusal fires for `--basetemp` pointing INSIDE this\n"
        "repository, and that case is not a false alarm: the suite would bind\n"
        "to the project's own tracker and rewrite real findings.\n"
    )


@pytest.fixture(scope="session", autouse=True)
def _temporary_tree_is_not_captured_by_a_foreign_tracker(tmp_path_factory):
    """Refuse the session when the product's own walk escapes the temp tree.

    THE WALK IS ASKED OF THE PRODUCT, NEVER RE-IMPLEMENTED (CB-204, brief §5).
    `db._find_db_root(start)` is the single function `db._resolve_db` uses for
    the discovery route, and `cli.py` already calls it with an explicit start
    argument in exactly this shape, so this is the product's rule rather than a
    second copy of it. That distinction is the whole point of the guard: a
    hand-rolled climb to the filesystem root would be wrong in BOTH directions,
    and both are pinned as oracle rows in `tests/test_suite_hermeticity.py` —
    it would FALSELY ALARM on a tracker sitting above a `.git` directory (the
    walk stops there, so that tracker is unreachable) and it would MISS one
    reachable only by following a `.git` FILE to a linked worktree's main
    checkout (the walk jumps, a parent loop does not).

    The start point is `tmp_path_factory.getbasetemp()` — the same factory the
    `tmp_path` fixture itself is built on, so it cannot drift from the directory
    the fixtures actually use. It is not `/tmp`: measured, `--basetemp` and
    `TMPDIR` both move it, and a guard hardcoding `/tmp` would be a gate that
    cannot fire under either.

    THE DECLARED CHANNELS ARE DELIBERATELY NOT CHECKED HERE, and that is not an
    omission. `db._db_path` resolves an argument, then `--tracker-root`, then
    `CODEBUGS_ROOT`, then the walk. The first is per-call and a test supplies
    its own; the middle two are cleared before every test by the fixture above.
    The walk is the one channel left live, so it is the one that needs a guard,
    and refusing on a declared root that the suite has already neutralized would
    be a false alarm — the fastest way to get a guard deleted by the first person
    it inconveniences.

    There is no off switch on purpose. Every exit the message offers repairs the
    condition rather than hiding it, and an environment variable that turns this
    off would be read as permission to run the suite in a state where a thousand
    of its results mean nothing.
    """
    basetemp = str(tmp_path_factory.getbasetemp())
    foreign_root = db._find_db_root(basetemp)
    if foreign_root is not None:
        pytest.exit(
            _hermeticity_refusal(basetemp, foreign_root),
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
