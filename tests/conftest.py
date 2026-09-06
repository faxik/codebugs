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
tracker, and it is an alarm rather than a guard, for the reason given at it. The
fourth asks it of the MCP SURFACE: a test that builds the surface by hand is
judging a server the product never produces.

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

FOURTH: refuse a call into a tool that was registered on a BARE `MCPServer`,
bypassing `server.build_registrar` (CB-311). CB-310 measured this failure in ten
tests at once: each built its own server, each therefore measured a surface
`_build_server` never produces, and all ten stayed green against a defect that
was live on the shipped one. A test file cannot hold this for itself — the file
that needs the guard is precisely the file whose author did not know it applied
— and the failure is silent, since a hand-built surface answers every call
happily and merely answers about the wrong object. It lets a call through only
when the SERVER carries the production build's adapter pair AND the TOOL's body
carries the production decorator; see the guard below for why both halves are
needed and why each is recognised by an OBJECT rather than by how the
registration is spelled.

A per-file fixture would have to be remembered by every test module added later,
and the cost of forgetting is silent destruction of the developer's own data, or
a thousand failures pointing at code that is fine — exactly the kind of rule
that must not be an enumeration. Tests that exercise the override set the
variable themselves *after* the first fixture has run.
"""

import os
import subprocess
import sys
import weakref
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from codebugs import db, server as _server


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

    THE `--basetemp` LINE CLAIMED TO "DO THE SAME JOB" AS `TMPDIR=`, AND THAT
    WAS FALSE ON THE STATE THIS MESSAGE IS SHOWN FOR (CB-225). Copying it
    verbatim runs plain `mktemp -d`, which resolves against `$TMPDIR` (or
    `/tmp` when that is unset) — exactly the root a contaminated ancestor
    sits ABOVE when this refusal fires from an ordinary, no-flags invocation.
    So the "fresh throwaway path" it names is still a descendant of the same
    tracker this message is refusing over, and the walk finds it again.
    Built by hand and measured, not assumed (brief §4, П3): with a
    `.codebugs/` planted above `$TMPDIR`, the plain run refuses at
    `pytest.ExitCode.USAGE_ERROR` (4); copying
    `pytest tests/ --basetemp="$(mktemp -d)"` verbatim, `$TMPDIR` left as it
    was, refuses AGAIN at the SAME code — not a different message, the
    identical one — while pointing `TMPDIR` itself at a genuinely different
    place (the first way out below) exits 0. The message says this rather
    than repeating the false claim, because the reader is in exactly the
    worst position to notice the difference themselves: they followed the
    instructions and got the identical refusal back.

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
        "    `--basetemp` DELETES the directory you name, recursively and\n"
        "    without asking, before the run starts — point it only at a fresh\n"
        "    throwaway path, never at one that already holds anything:\n"
        '        pytest tests/ --basetemp="$(mktemp -d)"\n'
        "    This escapes the tracker above ONLY WHEN `$TMPDIR` (or `/tmp`\n"
        "    when it is unset) is not itself under it — plain `mktemp -d`\n"
        "    resolves against that same variable, so copying this line\n"
        "    verbatim can refuse AGAIN, at the SAME exit code, when the\n"
        "    tracker sits at or above the default temp root. If that\n"
        "    happens, the `TMPDIR=` form above is the one that works.\n"
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


def _tree_fingerprint(root=None, *, unexamined: list | None = None) -> dict:
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
    run's summary down with it.

    `unexamined` (optional; CB-226) collects every DIRECTORY this walk could
    not LIST at all — permission denied, or anything else `os.walk`'s own
    default behaviour silently swallows. Before this, such a directory was
    invisible at BOTH ends of a run: it is absent from the fingerprint taken
    before and the one taken after, so the diff between two identical
    absences is empty and the alarm stayed silent over a subtree it never
    looked at — built and measured by hand (brief §4, П1): a directory
    `chmod 0300` before the first sample, a file inside it rewritten to a
    different length while the run was "in progress", `chmod` left unchanged
    for the second sample, and the diff between the two samples came back
    empty. This repository already has a name for that shape — "a guard
    reporting clean because it could not look" (CB-203/CB-218) — landing here
    in a new place. AN UNLISTABLE DIRECTORY RECORDS AND THE WALK CONTINUES,
    THE FORM TAKEN FROM `db._walk_db_root` RATHER THAN REINVENTED (CB-218,
    CB-224; brief §4, П4): record `(path, reason)` and keep walking, because
    one unreadable directory must not cancel the whole alarm, and continuing
    is the only way the rest of the tree still gets checked. `db`'s own walk
    helper is private and answers a different question (which `.codebugs/` to
    bind to, not which file changed), so this is a three-line duplicate of
    the SHAPE rather than a call into it — the alternative, reaching into
    `db._walk_db_root`'s internals for an unrelated string-formatting detail,
    is the worse coupling.

    Four boundaries are named now, not three: a symlink is stat'd without
    being followed; a symlinked DIRECTORY is not descended into, so nothing
    inside one is fingerprinted; an unreadable FILE is recorded as a changed
    entry (`"unreadable (...)"`); and an unreadable DIRECTORY — the boundary
    this docstring used to leave unnamed — is recorded in `unexamined`
    instead of vanishing from both snapshots at once.

    STILL NOT COVERED, NAMED RATHER THAN LEFT TO BE REDISCOVERED (brief §5):
    a dangling symlink and a directory symlink were both tried by hand against
    this function and neither blinds it — `os.lstat` reports the link itself
    without raising, and a symlinked directory is already excluded from
    descent by the boundary above, not silently missed. An undecodable name
    was also tried (a byte sequence that is not valid UTF-8) and does not
    blind it either: POSIX `os.walk` round-trips such names through
    `surrogateescape`, so `os.lstat` still succeeds on them. What is NOT
    tried, and is left as a named gap rather than a silent one: a path at or
    past the operating system's length limit for a component that is a
    DIRECTORY (as opposed to a file, which the per-file `except OSError`
    above already turns into a "changed" entry) — reaching it would need a
    filesystem that permits creating such a path in the first place, which
    was not available to build this state against.
    """
    root = str(root or REPO_ROOT)
    seen: dict = {}

    def _on_walk_error(exc: OSError) -> None:
        if unexamined is None:
            return
        where = exc.filename or root
        rel = os.path.normpath(os.path.relpath(where, root))
        reason = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
        unexamined.append((rel, f"could not be listed ({reason})"))

    for dirpath, dirnames, filenames in os.walk(root, onerror=_on_walk_error):
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


def _top_level_directory(path: str) -> str | None:
    """The first path segment, or `None` for a file sitting directly at the root.

    A root-level file is not a DIRECTORY that could disappear whole from the
    truncation tail (CB-226 is about a nested path losing its entire first
    segment), so it is left out of the per-directory breakdown below rather
    than counted as a directory of one.
    """
    head, sep, _tail = path.partition(os.sep)
    return head if sep else None


def _tree_moved_report(
    changes: list[tuple[str, str]],
    head_before: str | None,
    head_after: str | None,
    unexamined: tuple[tuple[str, str], ...] = (),
    limit: int = _TREE_MOVED_LIMIT,
) -> str:
    """The whole alarm, as one string, so a test can read it back.

    IT REPORTS AND DOES NOT JUDGE. The paths are listed as found, with no
    attempt to sort them into important and unimportant: a reader who sees one
    plan note shrugs it off in a second, and a reader who sees a file from
    `src/` goes and re-runs. Only that reader knows which test went red, so only
    that reader can weigh the list — a rule guessing on their behalf would be
    wrong in the one case that mattered.

    THE TRUNCATION TAIL NAMES EVERY TOP-LEVEL DIRECTORY WITH A CHANGE, WITH A
    COUNT — NOT JUST "... and N more" (CB-226). The old tail let the alphabet
    decide what survives it: built by hand (brief §4, П2), 26 changes — 25
    under `.claude/plans`, one under `src/` — sorted alphabetically put every
    `.claude/plans` entry ahead of the `src/` one (the dot sorts below the
    letters), so the top-20 cut and the "... and 6 more" line between them
    dropped the `src/` path with no trace anywhere in the report. That is the
    same "guard reporting clean" shape CB-226's other half fixes, reached
    through a limit and an alphabet instead of a permission bit, and it is the
    more harmful of the two: brief §1 for why. The limit and the alphabetical
    order are UNCHANGED — only the tail is — and the breakdown covers every
    top-level directory with a change regardless of whether some of its paths
    already appear above the cut, so a directory's disappearance from the
    report is unrepresentable BY CONSTRUCTION rather than merely less likely.
    It still does not say which directory MATTERS — that would smuggle
    judgement back in through a new door, against `_PRUNED_NAMES`'s own rule.
    """
    lines = [
        "",
        f"codebugs test suite: {_TREE_MOVED_ANCHOR}.",
        "",
    ]
    if changes:
        lines.append(f"  {len(changes)} path(s) differ between the start of this run and now.")
    if head_before is not None or head_after is not None:
        lines.append(
            f"  HEAD at the start: {head_before or 'unknown'}     HEAD now: {head_after or 'unknown'}"
        )
    lines.append("")
    for verb, path in changes[:limit]:
        lines.append(f"    {verb:<9}{path}")
    if len(changes) > limit:
        lines.append(f"    ... and {len(changes) - limit} more")
        counts: dict[str, int] = {}
        for _verb, path in changes:
            top = _top_level_directory(path)
            if top is not None:
                counts[top] = counts.get(top, 0) + 1
        if counts:
            lines.append("")
            lines.append(
                "  Every top-level directory with a change, so a truncated list can"
            )
            lines.append("  never make one disappear from this report entirely:")
            for top in sorted(counts):
                lines.append(f"    {top} ({counts[top]})")
    if unexamined:
        lines.append("")
        noun = "directory" if len(unexamined) == 1 else "directories"
        lines.append(f"  {len(unexamined)} {noun} could not be LISTED at all during this run,")
        lines.append("  and a change inside one would not show up as a path above — this")
        lines.append("  run cannot rule that out:")
        for path, why in unexamined:
            lines.append(f"    {path} — {why}")
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

    The `unexamined` list (CB-226) is sampled here too, on the same fingerprint
    call, for the same reason the fingerprint itself is: whatever this run could
    not list at the START must be compared against whatever it could not list
    at the END, exactly like every other entry in the tree.
    """
    unexamined: list = []
    seen = _tree_fingerprint(unexamined=unexamined)
    request.config.stash[_TREE_AT_START] = (seen, _head_signature(), tuple(unexamined))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Speak in the final summary, where the failures are — or not at all.

    ON A STILL TREE THIS PRINTS NOTHING AT ALL: not a header, not an empty
    section. Half of what this unit is worth is the silence, because an alarm
    that says something on every run is one nobody reads by the second week.

    The channel is the terminal summary rather than a line at startup for the
    same reason: the reader needs it standing next to the red it explains, not
    scrolled off the top of a ninety-second run.

    STILL SILENT ONLY WHEN NOTHING CHANGED AND NOTHING WAS BLIND (CB-226). A
    directory that could not be listed at either end of the run is not, on its
    own, "the tree moved" — it might not have — but it is also not "the tree
    is still", because this run never looked. Built and measured by hand
    (brief §4, П1): before this, that exact state fell through the `if not
    changes: return` below and the run reported nothing, indistinguishable
    from a genuinely still tree.
    """
    taken = config.stash.get(_TREE_AT_START, None)
    if taken is None:
        return
    before, head_before, unexamined_before = taken
    unexamined_after: list = []
    after = _tree_fingerprint(unexamined=unexamined_after)
    changes = _tree_difference(before, after)
    # A directory blind for the WHOLE run appears in both samples; report it
    # once, by the first path it was seen at, rather than doubling every
    # persistent blind spot into a count that overstates how much is unseen.
    seen_unexamined_paths: set = set()
    unexamined: list = []
    for entry in (*unexamined_before, *unexamined_after):
        if entry[0] not in seen_unexamined_paths:
            seen_unexamined_paths.add(entry[0])
            unexamined.append(entry)
    unexamined = tuple(unexamined)
    if not changes and not unexamined:
        return
    report = _tree_moved_report(changes, head_before, _head_signature(), unexamined)
    for line in report.splitlines():
        terminalreporter.write_line(line)


# --- CB-311: a test that CALLS a tool must have built the surface for real ----
#
# WHAT IT REFUSES. Registering tools on a bare `MCPServer` and then calling one
# through `call_tool`. That server carries bodies which never passed through
# `_RefusalsReachTheClient`, so every question the test then asks is answered by
# an object `server._build_server` does not produce.
#
# WHAT IT CHECKS, IN TWO PARTS, AND WHY ONE PART WAS NOT ENOUGH. A call is let
# through only when BOTH hold: the SERVER was assembled by the full production
# build, and the TOOL's body went through production registration. The first
# round of this guard checked only the second, and a cross-model review of that
# round found two ways past it, both reproduced here before being fixed:
#
#   * `MCPServer.add_tool` is a PUBLIC method that puts a tool straight into the
#     server's manager without going through `.tool()` at all, so nothing was
#     recorded and the call sailed through. (The product never uses it — every
#     registration in `src/` goes through `.tool()` — so any use of it is by
#     definition hand assembly.)
#   * `build_registrar` composes TWO adapters, and a test that wraps only the
#     outer one gets bodies carrying an allowed code object while description
#     normalization — the other half of what ships — is absent.
#
# WHY IT KEYS ON OBJECTS, NOT ON HOW THE CALL IS WRITTEN. A guard that grepped
# for `MCPServer(` or for `build_registrar` would be the defect this repository
# has paid for repeatedly — an instrument that inspects SPELLING finds spelling.
# So the server half is recognised by the ADAPTER PAIR the production build
# constructs: the outer adapter's `__init__` is observed, and the server is
# marked only when the object handed to it is the inner adapter. That is
# independent of how the caller spelled the build — deliberately, because
# `tests/_mcp_schema.py` imports `build_registrar` by name, so patching the
# module attribute would have missed it and refused a correctly built surface.
# The tool half compares the registered body's CODE OBJECT against the two the
# production decorator emits.
#
# AN EARLIER ATTEMPT WALKED THE CALL STACK for a frame whose `self` was the
# adapter, and it was WRONG — measured, 41 false refusals on correctly built
# servers, because the adapter's inner closure captures only `inner` and never
# `self`. It is recorded here because a discarded mechanism that is not written
# down gets re-proposed.
#
# WHY IT FIRES AT `call_tool` AND NOT AT REGISTRATION. Building a bare server is
# LEGITIMATE and three places do it deliberately: `test_boundary.py` inspects
# un-normalized descriptions (the bare object IS its subject), `test_findings.py`
# enumerates tool names, and `test_add_lines_surface.py` uses a stand-in that is
# not an `MCPServer` at all. What none of them does is CALL a tool — and calling
# is exactly the act that claims "this is the boundary the client meets". Keying
# on the claim rather than on the construction is what lets the three stay legal
# with no exception rows at all, which is better than three rows that would have
# to be maintained.
#
# WHAT IT DOES NOT DO, said plainly: it is per-TOOL, so a server built through
# `build_registrar` that then has one further tool registered on it by hand is
# caught for that tool only — which is right — but a surface assembled by some
# future third route this suite does not use would be invisible. And it observes
# the RUN, so a hand-built surface that is never called is never reported.
#
# WHERE IT CAN STILL FAIL SILENTLY — and the list is short BECAUSE the verdict
# stopped depending on bookkeeping. A THIRD round of review found that it did:
# a branch that cleared a tool's record when the same name was registered again
# "correctly" turned the guard off outright, since `ToolManager.add_tool` does
# NOT replace an existing name — it logs and returns the incumbent, so the hand
# body stayed and the note was erased. Measured: zero of ten tools carried a
# production body afterwards, and the call went through. Records are now
# evidence for the MESSAGE only; the verdict reads the body the server stores.
#
# What remains: the guard reads `_tool_manager._tools`, so an SDK that renamed
# that attribute would make every lookup miss. That direction is LOUD, not
# silent — the lookup returning nothing means "unknown tool", and the SDK then
# refuses the call itself. The genuinely silent direction is narrower: a future
# SDK that stored something other than the registered function under `.fn`
# would make production bodies unrecognisable, and that fails toward REFUSING
# correct code rather than admitting wrong code.
#
# Every OTHER way this breaks is loud: renaming `_refusal_reaches_the_client`
# fails the import and reddens the whole suite, and a third branch in that
# wrapper (or its removal from `build_registrar`) makes correctly built tools
# start getting refused.
#
# WHAT IT DELIBERATELY DOES NOT COVER: assertions about the SHAPE of the surface
# — tool names, descriptions, argument schemas — which a bare server answers just
# as happily and just as wrongly. Those are legitimately checked without ever
# calling a tool (`test_boundary.py`, `test_findings.py`), so this guard cannot
# see them, and widening it to registration would refuse those three legitimate
# places. The mechanism for that class already exists and is separate: the wire
# golden in `tests/_mcp_schema.py`, which IS collected through `build_registrar`.

_HAND_BUILT_SURFACES_ALLOWED: dict[str, str] = {
    # site -> why this place may call a tool on a hand-registered surface.
    # EMPTY TODAY, and that is a measurement rather than an oversight: the three
    # legitimate bare-server places named above do not reach `call_tool`, so
    # nothing needs licensing. A row here must carry a real reason —
    # `tests/test_hand_built_surfaces.py` refuses an empty one and refuses a row
    # naming a place that no longer exists, on the CB-179 discipline.
}


def _production_wrapper_codes() -> frozenset:
    """The code objects `_refusal_reaches_the_client` produces, both branches.

    THE FIRST ATTEMPT WALKED THE CALL STACK looking for a frame whose `self` is
    a `_RefusalsReachTheClient`, and it was WRONG — measured, 41 false refusals
    on servers built correctly. The adapter's inner closure captures only
    `inner`, never `self`, so by the time `MCPServer.tool` actually runs there is
    no such frame to find. Keeping it would have been a guard that refuses the
    thing it exists to require.

    What the production stack leaves behind instead is the OBJECT it produces:
    every body it registers has been replaced by `_refusal_reaches_the_client`'s
    `wrapper`, so comparing the registered function's `__code__` against the two
    code objects that decorator can emit answers "did this body come through the
    production build" directly, about the artifact rather than about the route.
    A hand-registered body is the domain module's own function and matches
    neither.
    """
    def probe() -> dict:
        """probe."""

    async def async_probe() -> dict:
        """probe."""

    return frozenset(
        {
            _server._refusal_reaches_the_client(probe).__code__,
            _server._refusal_reaches_the_client(async_probe).__code__,
        }
    )


def hand_built_registration_site(frame) -> str:
    """The test-owned frame that registered a tool, as `<file>::<function>`."""
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename.startswith(_TESTS_DIR) and not filename.endswith("conftest.py"):
            return f"{Path(filename).name}::{frame.f_code.co_name}"
        frame = frame.f_back
    return "<outside the test tree>"


def hand_built_surface_refusal(tool: str, site: str) -> str:
    return (
        f"CB-311: {site} called the tool {tool!r} on a surface it registered by hand.\n"
        "Those tools never went through `server.build_registrar`, so their bodies carry "
        "none of the production registration stack — the exact shape CB-310 found in ten "
        "tests at once, all green against a live defect.\n"
        "Fix: `mod.register_tools(server.build_registrar(mcp), factory)`. Note what that "
        "does and does not buy: it gives the registration adapters, NOT the two "
        "middlewares `_build_server` installs on top (strict arguments, usage tracking), "
        "so a test about those must build the server the way `server.py` does.\n"
        "If this fires on a tool you DID register through `build_registrar`, the "
        "production wrapper changed and `_production_wrapper_codes` is what needs fixing, "
        "not your test.\n"
        "If this place genuinely needs the bare object AND a call, add a row to "
        "`_HAND_BUILT_SURFACES_ALLOWED` in tests/conftest.py naming the reason."
    )


_TESTS_DIR = str(Path(__file__).resolve().parent)
_HAND_BUILT: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_FULLY_BUILT: "weakref.WeakSet" = weakref.WeakSet()
_PRODUCTION_WRAPPER_CODES = _production_wrapper_codes()
_REAL_ADD_TOOL = MCPServer.add_tool
_REAL_CALL_TOOL = MCPServer.call_tool


def _add_tool_recording_who_registered(self, fn, name=None, *args, **kwargs):
    """Where a hand registration's PLACE is learned — not where the verdict is made.

    `MCPServer.tool`'s decorator body calls `self.add_tool(fn, name=…, …)`, so
    every registration made through a METHOD arrives here. That is a fact about
    the SDK rather than a guess — and it is deliberately not stated as "the one
    registration point", because it is not: `MCPServer(tools=[…])` hands its
    list straight to the tool manager, which fills its own registry without
    calling this at all. **Nothing here would see that, and nothing here needs
    to** — the verdict is taken at call time from the body the server actually
    stores, so a tool nobody recorded is judged exactly like one that was.

    What this records is the SITE, for the refusal text and for the allowance
    table's key. The name is `name or fn.__name__`, which is what
    `Tool.from_function` itself computes, so the record keys the same way the
    manager does.
    """
    site = hand_built_registration_site(sys._getframe(1))
    result = _REAL_ADD_TOOL(self, fn, name, *args, **kwargs)
    if getattr(fn, "__code__", None) not in _PRODUCTION_WRAPPER_CODES:
        _HAND_BUILT.setdefault(self, {})[name or getattr(fn, "__name__", "?")] = site
    return result


def _mark_fully_built(self, registrar, *args, **kwargs):
    """Observe the production build: the OUTER adapter wrapping the INNER one.

    Marking on the outer adapter alone would accept half a build — the review
    that found that gap is named in the comment above. The inner adapter's
    server attribute is what carries the mark, because that is the object
    `call_tool` is later invoked on.
    """
    result = _REAL_REFUSALS_INIT(self, registrar, *args, **kwargs)
    if isinstance(registrar, _server._NormalizedDescriptions):
        _FULLY_BUILT.add(registrar._server)
    return result


async def _call_tool_refusing_a_hand_built_surface(self, name, *args, **kwargs):
    """The verdict is read from the LIVE ARTEFACT, never from bookkeeping.

    A first version asked its own records ("was a violation noted for this
    name?") and that was WRONG, measured: `ToolManager.add_tool` does NOT
    replace a tool registered under a name it already holds — it logs and
    returns the incumbent — so registering by hand and then "correctly" under
    the same name leaves the HAND body in the server while a record-clearing
    branch erased the note. Two lines turned the guard off completely, and the
    test written beside them enshrined the bypass as correct.

    So the question asked here is about the object the server will actually
    run: does the body stored under this name carry the production decorator,
    and was this server assembled by the production build. Records survive only
    to say WHERE a hand registration happened, for the refusal text and for the
    allowance table's key — they can no longer decide anything.

    Reaching into `_tool_manager` is reaching into the SDK's internals, and that
    is deliberate here: `src/codebugs/server.py` forbids it for PRODUCT code,
    while this harness already replaces two of the SDK's methods and one
    adapter's `__init__`. Bookkeeping was the alternative, and bookkeeping is
    what produced the hole.
    """
    stored = getattr(self, "_tool_manager", None)
    tool = getattr(stored, "_tools", {}).get(name) if stored is not None else None
    if tool is None:
        # Unknown tool: let the SDK give its own answer rather than inventing one.
        return await _REAL_CALL_TOOL(self, name, *args, **kwargs)
    body_is_production = (
        getattr(getattr(tool, "fn", None), "__code__", None) in _PRODUCTION_WRAPPER_CODES
    )
    if not body_is_production or self not in _FULLY_BUILT:
        site = _HAND_BUILT.get(self, {}).get(name) or hand_built_registration_site(
            sys._getframe(1)
        )
        if site not in _HAND_BUILT_SURFACES_ALLOWED:
            raise AssertionError(hand_built_surface_refusal(name, site))
    return await _REAL_CALL_TOOL(self, name, *args, **kwargs)


# Installed at IMPORT rather than in a session fixture: a module-level
# registration in some future test file would run during collection, before any
# fixture, and the guard would then be blind to exactly the construction it
# exists to see.
_REAL_REFUSALS_INIT = _server._RefusalsReachTheClient.__init__
_server._RefusalsReachTheClient.__init__ = _mark_fully_built
MCPServer.add_tool = _add_tool_recording_who_registered
MCPServer.call_tool = _call_tool_refusing_a_hand_built_surface
