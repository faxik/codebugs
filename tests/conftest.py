"""Suite-wide protections that no single test file could hold on its own.

THIS FILE IS A DELIBERATE EXCEPTION to the project's "no shared conftest.py"
convention. It admits exactly one KIND of inhabitant: a property that protects
the whole suite, whose failure mode is silent or unattributable, and which every
future test file would otherwise have to remember for itself. Ordinary fixtures
are not that, and still belong in the file that uses them.

Every inhabitant answers the same question in a different place: WHAT DID THIS
RUN ACTUALLY JUDGE? The first two are a test that NAMES one state and gets
another, because `db.connect()` resolves against ambient state the test never
declared. The third is the same question asked of the SOURCE TREE instead of the
tracker, and it is an alarm rather than a guard, for the reason given at it.

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

THIRD: say so when the SOURCE TREE MOVED while the run was in progress (CB-215).
Structural tests here read source files from disk, and this suite is run in the
main checkout while other branches land on `main`, so a red can be a verdict on
a file the run never started with. That is not something a test file can hold
for itself: no single test knows what the tree looked like before it started.

A per-file fixture would have to be remembered by every test module added later,
and the cost of forgetting is silent destruction of the developer's own data, or
a thousand failures pointing at code that is fine — exactly the kind of rule
that must not be an enumeration. Tests that exercise the override set the
variable themselves *after* the first fixture has run.
"""

import os
import subprocess
from pathlib import Path

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

    ORDER IS LOAD-BEARING, and so is the warning on `--basetemp` (CB-214).
    Whoever reads this has just been told the suite will not run; they are in a
    hurry and they copy the first line that fits. So the exits run from safe to
    destructive — `TMPDIR`, which only ADDS a subtree to the place it names,
    before `--basetemp`, which pytest empties by deleting the named directory
    recursively, before deleting a directory by hand. Measured 2026-08-26 on
    this tree's pytest: a file placed in the directory named by `--basetemp` is
    gone after one run, and the same file under `TMPDIR` survives untouched.
    The deletion is UNCONDITIONAL, which is worse than it first reads and was
    measured rather than assumed: it happens when `getbasetemp()` is first
    called, and the guard below calls it to ask its own question — so a run that
    this very refusal STOPS has already emptied the directory by the time the
    refusal is printed. The message does not spend a line on that, deliberately:
    it is read in irritation and length is itself a cost, and a reader who has
    been handed a whole safe form to copy never reaches the case.

    THE EXPECTED ANSWER OF THE EMPTINESS CHECK IS SPELLED OUT, because the
    check answers correctly and LOOKS like a mistake. Measured the same day:
    `codebugs --tracker-root <dir> stats` over a `.codebugs/` holding no
    database exits 1 with `holds no findings.db` — the right answer to the
    question asked, and the same rc and text that `where`, `summary` and
    `categories` give, because a DECLARED root treats the FILE as the tracker
    and fails closed before any verb body runs (CB-23). So there is no verb to
    swap in that would exit 0 here, and the only honest repair is to say what
    the answer means: on litter it CONFIRMS, and real statistics mean the
    tracker must be kept.
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
        "times in the middle of the run. Two ways out, safest first:\n"
        "\n"
        "  * Move the temporary root out from under that tracker. This is safe\n"
        "    on a directory that already holds things — pytest only ADDS its own\n"
        "    `pytest-of-<user>/` subtree under the path you name:\n"
        "        TMPDIR=/some/other/place pytest tests/\n"
        "    `--basetemp` does the same job and is NOT safe that way: pytest\n"
        "    DELETES the directory you name, recursively and without asking,\n"
        "    before the run starts. Point it only at a fresh throwaway path:\n"
        '        pytest tests/ --basetemp="$(mktemp -d)"\n'
        "\n"
        f"  * If {foreign_root}/.codebugs is litter — an empty directory some\n"
        "    tool left behind — delete it. Ask what it holds first:\n"
        f"        codebugs --tracker-root {foreign_root} stats\n"
        "    On litter that command exits 1 saying `holds no findings.db`, and\n"
        "    that answer IS the confirmation — it is not a typo in the command.\n"
        "    If it prints statistics instead, the tracker is real: leave it\n"
        "    alone and take the first way out above.\n"
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


# --- CB-215: a run that judged two different trees must say so --------------

REPO_ROOT = Path(__file__).resolve().parents[1]

_TREE_AT_START: pytest.StashKey = pytest.StashKey()

_TREE_MOVED_ANCHOR = "THE TREE MOVED WHILE THIS RUN WAS IN PROGRESS"

# How many paths the report lists before it starts counting instead.
_TREE_MOVED_LIMIT = 20

# THERE IS NO EXCLUSION BY JUDGEMENT IN EITHER TABLE, and that is the decision
# rather than an oversight. The tempting one is `.claude/plans/`, because notes
# land there constantly and every one of them will set this alarm off — and it
# is measured FALSE that the suite ignores that directory:
# `tests/test_exposure_matrix.py` reads `.claude/plans/exposure-scripts/matrix.py`
# from the real tree. "The suite does not look there" is exactly the unchecked
# premise this alarm exists to stop people acting on, so nothing is pruned for
# being *probably* irrelevant. What is pruned is only what is not a SOURCE of
# anything at all, and each entry carries the sentence saying why — a list of
# bare names becomes, within a month, the place inconvenient paths are hidden.
_PRUNED_NAMES = {
    ".git": "git's own administrative directory: every git command rewrites it, and no "
    "test reads a source file out of it",
    ".venv": "the virtual environment: built by uv from `uv.lock`, and rebuilt by the very "
    "`uv run` that starts this suite",
    ".codebugs": "the tracker's database: it moves whenever any agent files a card, which "
    "says nothing about the source under test",
    "__pycache__": "a cache written by THIS run while it imports its own test modules — "
    "including it would make the alarm fire on itself",
    ".pytest_cache": "a cache written by this run",
    ".ruff_cache": "a cache written by the linter",
    ".mypy_cache": "a cache written by a type checker",
}

# Pruned at one exact location rather than by name, because `worktrees` is an
# ordinary word and a source directory could legitimately be called that.
_PRUNED_PATHS = {
    ".worktrees": "other units' checkouts live here; they are separate trees, and their "
    "churn is not this tree moving",
    os.path.join(".claude", "worktrees"): "the legacy location of the same thing, still "
    "populated in the main checkout",
}


def _is_pruned(rel_dir: str, name: str) -> bool:
    """One predicate, asked of directories AND of files.

    It has to be one, because `.git` is a DIRECTORY in the main checkout and a
    FILE in every linked worktree — and this alarm exists precisely because the
    acceptor re-runs in the main checkout what an executor ran in a worktree.
    A rule that pruned only directories would therefore give two different
    answers about the same name depending on which checkout it was asked in,
    which is the kind of shape-dependence this repository keeps paying for.
    """
    if name in _PRUNED_NAMES:
        return True
    return os.path.normpath(os.path.join(rel_dir, name)) in _PRUNED_PATHS


def _tree_fingerprint(root=None) -> dict:
    """Map every file in the tree to (size, mtime_ns), or to why it could not be read.

    THE DISCRIMINATOR IS THE FILES, NOT THE NAME OF A COMMIT, and each half of
    that was measured (brief §2(4)). `git rev-parse HEAD` fails outright in a
    copy that carries no git directory; it does not move in a worktree when
    `main` moves, which is the one case that must stay SILENT; and it cannot see
    an editor, a formatter or another agent writing a file nobody committed —
    which is most of the ways a tree moves under a run. A commit name is still
    worth having as a SIGNATURE, so the report prints it when git answers, but
    nothing is decided by it.

    Errors are per-file and never raise: a file that became unreadable between
    the two samples is a CHANGE and is reported as one, rather than taking the
    run's summary down with it. Two boundaries, named rather than left to be
    discovered: a symlink is stat'd without following it, and a symlinked
    DIRECTORY is not descended into, so nothing inside one is fingerprinted.
    """
    root = str(root or REPO_ROOT)
    seen: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [name for name in dirnames if not _is_pruned(rel_dir, name)]
        for name in filenames:
            if _is_pruned(rel_dir, name):
                continue
            rel = os.path.normpath(os.path.join(rel_dir, name))
            try:
                stat = os.lstat(os.path.join(dirpath, name))
            except OSError as exc:
                seen[rel] = f"unreadable ({exc.__class__.__name__})"
            else:
                seen[rel] = (stat.st_size, stat.st_mtime_ns)
    return seen


def _head_signature(root=None) -> str | None:
    """The short commit name, or None wherever git cannot answer.

    None is a normal answer, not a failure: a tree unpacked without its git
    directory is one of the states this alarm must work in. `OSError` covers a
    git that is missing or not executable and `subprocess.SubprocessError`
    covers the timeout, which is not an `OSError` (CB-79).
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root or REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _tree_difference(before: dict, after: dict) -> list[tuple[str, str]]:
    """Every path whose entry differs, as (verb, path), sorted by path."""
    changes = []
    for path in sorted(set(before) | set(after)):
        was, now = before.get(path), after.get(path)
        if was == now:
            continue
        verb = "added" if was is None else "removed" if now is None else "changed"
        changes.append((verb, path))
    return changes


def _tree_moved_report(
    changes: list[tuple[str, str]],
    head_before: str | None,
    head_after: str | None,
    limit: int = _TREE_MOVED_LIMIT,
) -> str:
    """The whole alarm, as one string, so a test can read it back.

    IT REPORTS AND DOES NOT JUDGE. The paths are listed as found, with no
    attempt to sort them into important and unimportant: a reader who sees one
    plan note shrugs it off in a second, and a reader who sees a file from
    `src/` goes and re-runs. Only that reader knows which test went red, so only
    that reader can weigh the list — a rule guessing on their behalf would be
    wrong in the one case that mattered.
    """
    lines = [
        "",
        f"codebugs test suite: {_TREE_MOVED_ANCHOR}.",
        "",
        f"  {len(changes)} path(s) differ between the start of this run and now.",
    ]
    if head_before is not None or head_after is not None:
        lines.append(
            f"  HEAD at the start: {head_before or 'unknown'}     HEAD now: {head_after or 'unknown'}"
        )
    lines.append("")
    for verb, path in changes[:limit]:
        lines.append(f"    {verb:<9}{path}")
    if len(changes) > limit:
        lines.append(f"    ... and {len(changes) - limit} more")
    lines.extend(
        [
            "",
            "  Structural tests in this suite read source files FROM DISK, so any",
            "  failure above may be judging a file this run did not start with. A",
            "  merge landing on `main`, an editor save or a formatter is enough, and",
            "  none of those is a regression in the code.",
            "",
            "  THE EXIT STATUS OF THIS RUN HAS NOT BEEN TOUCHED. This is a report,",
            "  not a verdict: a moved tree is ordinary, and refusing over it would",
            "  turn everyday traffic into a false failure. Before believing a red,",
            "  re-run on a still tree.",
            "",
        ]
    )
    return "\n".join(lines)


@pytest.fixture(scope="session", autouse=True)
def _fingerprint_the_tree_at_the_start_of_the_run(request):
    """Sample the tree once, before the first test reads anything off disk.

    A session fixture rather than `pytest_configure`, deliberately: this file is
    an INITIAL conftest only when the invocation names `tests/`, so a bare
    `pytest` from the repository root would never reach a `pytest_configure`
    defined here — a gate that cannot fire under a perfectly ordinary command.
    A session fixture runs whatever the invocation looks like, and the window it
    covers is exactly the window that matters: from the first test to the last.
    """
    request.config.stash[_TREE_AT_START] = (_tree_fingerprint(), _head_signature())


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Speak in the final summary, where the failures are — or not at all.

    ON A STILL TREE THIS PRINTS NOTHING AT ALL: not a header, not an empty
    section. Half of what this unit is worth is the silence, because an alarm
    that says something on every run is one nobody reads by the second week.

    The channel is the terminal summary rather than a line at startup for the
    same reason: the reader needs it standing next to the red it explains, not
    scrolled off the top of a ninety-second run.
    """
    taken = config.stash.get(_TREE_AT_START, None)
    if taken is None:
        return
    before, head_before = taken
    changes = _tree_difference(before, _tree_fingerprint())
    if not changes:
        return
    for line in _tree_moved_report(changes, head_before, _head_signature()).splitlines():
        terminalreporter.write_line(line)
