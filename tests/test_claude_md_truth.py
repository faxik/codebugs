"""A gate on the TRUTH of the two normative files, beside the gate on their SIZE (CB-302).

WHY A SECOND GATE. `tests/test_claude_md_size_ceiling.py` bounds how much doctrine
is injected; nothing bounded whether it is TRUE. The two are orthogonal: a file can
sit comfortably under its ceiling and still send every session to the wrong place.
Measured cost of having no such gate: CB-292 recorded `server.py` at 694 lines on
2026-09-01; by 2026-09-06 it held 883, so the file drifted a further 189 lines away
from the prose WHILE the card about the wrong number was open. Fixing numbers one
at a time is what that measurement already proved useless.

THE HARM IS NOT THE WRONG NUMBER, IT IS WHERE IT SENDS A SESSION. "Thin orchestrator
(~48 lines)" is an instruction about where NOT to look; a reader who needs the
protocol server's logic skips a file eighteen times larger than advertised.

--------------------------------------------------------------------------------
WHY THIS GATE CANNOT USE THE TRICK EVERY OTHER RATCHET HERE USES.

`tests/test_fsio.py::TestWriteCallSitesRatchet` opens with this repository's first
principle -- *prose cannot enforce prose* -- and records that its own first draft
searched text with a regular expression and failed on a phrase inside three
docstrings of the module it was policing. It was rewritten to walk the syntax tree,
and its lesson is written there in words: **a ratchet that trips over prose is a
ratchet nobody keeps alive.** Every ratchet in this suite escapes the same way: the
AST does not see comments or docstrings, so the noise is removed BY CONSTRUCTION
rather than by a filter.

**That refuge does not exist here, because prose IS the subject.** There is no
syntax tree of an English paragraph to hide in. So the recognition problem has to be
solved in the open, and this docstring says exactly how, what the mechanism sees,
what it ignores, and where it will be wrong first.

--------------------------------------------------------------------------------
STEP ONE: TOKENIZATION. FOUR LEXICAL RULES, AND EACH IS ABOUT SPELLING, NEVER MEANING.

A "number token" is a run of digits, or a cardinal number-word (`two` .. `thousand`).
Four rules remove text that is not a number at all. Each one is justifiable as
tokenization or as document structure -- none of them asks what a sentence MEANS,
which is the line that separates this from the shape filter the next section rejects:

  1. IDENTIFIER. A whitespace-delimited run mixing digits and letters is one name:
     `sqlite3`, `auto:v1`, `CB-121`, `S608`, `adcf354`, `0xFF`, `P1-P4`, `3.14.4`.
     `sqlite3` no more contains the number 3 than `x2` contains 2.
  2. ORDERED-LIST MARKER. `1.` at the start of a line is Markdown structure.
  3. ENUMERATION MARKER. `**(4)**` is this corpus's own inline enumeration.
  4. COMPOUND ADJECTIVE. A cardinal word hyphenated to a following word is one
     lexeme: `three-valued`, `two-step`, `two-line`. STATED COST: a genuine claim
     spelled `three-module package` would be swallowed by this rule.

STEP TWO: ACCOUNTING. EVERY SURVIVING TOKEN MUST BE COVERED BY EXACTLY ONE ROW.

Four buckets, and the union must be TOTAL over the token population. An uncovered
token FAILS -- the default is refusal, not permission:

  LIVE (class 1)        a claim about TODAY's tree. The row carries the value as
                        WRITTEN and a function that derives the admissible values
                        FROM the tree; they must agree.
  LIVE, subcategory 1b  a named exit code. Checked by REACHABILITY -- the code is
                        really produced by the named script or module -- not by a
                        count. (The three-class scheme did not foresee this shape;
                        the level-(2) holder classified it here.)
  HISTORICAL (class 2)  a measurement of a PAST state. Never recomputed -- a guard
                        that recomputed it would be wrong forever, which is the
                        very defect this module exists to close. What IS checked is
                        that the measurement carries a stamp pinning what was
                        measured: a date and a commit for this tree, a tool version
                        for an external tool.
  NOT_A_CLAIM           a number that says nothing about this tree: a literal inside
                        quoted code, a POSIX/SQLite/Markdown constant, a
                        cross-reference, a decision date. Every row carries the
                        reason it says nothing.

Class 3 of the scheme -- "a number cheaper to delete" -- leaves no row by
construction: the number is gone from the prose and there is nothing to account for.

WHY THIS IS NOT A FILTER ON SPELLING, WHICH IS THE THIRD PATH AND THE WRONG ONE.
A shape filter says "anything matching `exit \\d+` is fine" and therefore waves
through the next number nobody examined, as long as it wears a familiar costume.
**Every row here is keyed on a VERBATIM ANCHOR -- literal bytes lifted out of the
file -- together with the exact number of times those bytes occur.** A row
generalizes over NOTHING. It licenses the occurrences a human read and no others: a
seventh `exit 1` in a paragraph nobody classified is uncovered, and uncovered is
red. The four lexical rules above are the only generalizing step in the whole
module, and they are confined to spelling.

WHERE IT IS WRONG FIRST, NAMED RATHER THAN DISCOVERED LATER.
  * Rewording a sentence around an already-classified number reddens this file even
    though nothing changed in substance. That is the commonest false alarm and it is
    the price of having no shape generalization -- the repair is one edited anchor,
    and it forces the number to be re-affirmed by whoever touched the paragraph.
  * `one` is NOT in the cardinal vocabulary. In this English prose it is
    overwhelmingly a pronoun ("the one that", "no one"), and admitting it would bury
    the population in noise. The cost is exact and real: a claim spelled "exactly one
    call site" is invisible to the COMPLETENESS half. Several such claims are
    nonetheless carried in LIVE below, so the per-claim half is deliberately WIDER
    than the completeness half at that spot.
  * A quantity written as neither digits nor a listed cardinal -- "a handful", "a
    couple", "a dozen or so" -- is invisible to the tokenizer.

WHAT THIS GATE DOES NOT PROMISE (C-14, ratified in the package brief). It answers
whether the text is TRUE. It says nothing about how long an already-running session
keeps a superseded copy: the harness injects these files and the injected copy
carries no version, no commit and no age, so a session cannot tell which edition it
holds. That is repaired in the harness, not in this tree.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

CORPUS = ("CLAUDE.md", "src/codebugs/CLAUDE.md")

# `tests/CLAUDE.md` is the THIRD normative file in this tree and is deliberately
# NOT in the corpus: package P-11 names two files, and the size gate next door
# discovers all three by itself. The asymmetry is declared, not hidden.


# --------------------------------------------------------------------------- #
# reading the tree
# --------------------------------------------------------------------------- #
def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _shell_exit_codes(rel: str) -> set[str]:
    """Every status a shell script can leave through, as literal text.

    `exit N` and `return N` both count: this repository's guards are functions in
    `tools/_guards.sh` that RETURN their status and are exited on by the caller.
    """
    return set(re.findall(r"\b(?:exit|return)\s+(\d+)\b", _read(rel)))


def _literal_present(rel: str, needle: str, value: str) -> set[str]:
    """`value` if `needle` occurs in the named file, otherwise nothing.

    Fail-closed by construction: an absent needle yields an empty set, and an empty
    set can never contain the declared value.
    """
    return {value} if needle in _read(rel) else set()


def _regex_values(rel: str, pattern: str) -> set[str]:
    return set(re.findall(pattern, _read(rel), re.MULTILINE))


def _call_sites(rel: str, func: str) -> set[str]:
    """How many times `func` is CALLED in a module, as a decimal string (AST)."""
    tree = ast.parse(_read(rel))
    n = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == func or getattr(node.func, "attr", None) == func)
    )
    return {str(n)}


def _milestone_tool_count() -> set[str]:
    """MCP tools the milestones package registers, counted from the tree."""
    pkg = REPO_ROOT / "src/codebugs/milestones"
    n = 0
    for path in pkg.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any("tool" in ast.unparse(d) for d in node.decorator_list):
                    n += 1
    return {str(n)}


def _milestone_tables() -> set[str]:
    pkg = REPO_ROOT / "src/codebugs/milestones"
    src = "".join(p.read_text(encoding="utf-8") for p in pkg.glob("*.py"))
    names = set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)", src))
    return {str(len(names))}


def _tuple_len(rel: str, name: str) -> set[str]:
    """Length of a module-level tuple/list literal, read from the syntax tree."""
    for node in ast.walk(ast.parse(_read(rel))):
        if isinstance(node, ast.Assign):
            targets = [getattr(t, "id", None) for t in node.targets]
            if name in targets and isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                return {str(len(node.value.elts))}
    return set()


def _installed_hook_count() -> set[str]:
    hooks = set(re.findall(r"\b(pre-commit|pre-merge-commit|commit-msg)\b", _read("tools/install-hooks.sh")))
    return {str(len(hooks))}


def _ci_matrix_interpreters() -> set[str]:
    """The interpreter versions `ci.yml`'s contracts matrix actually runs."""
    found = re.search(r"python:\s*\[([^\]]*)\]", _read(".github/workflows/ci.yml"))
    if not found:
        return set()
    return {v.strip().strip('"').strip("'") for v in found.group(1).split(",") if v.strip()}


def _lock_version(package: str) -> set[str]:
    found = re.search(rf'name = "{package}"\nversion = "([^"]+)"', _read("uv.lock"))
    return {found.group(1)} if found else set()


def _braced_numbers(rel: str, pattern: str) -> set[str]:
    """Every integer inside the first brace group `pattern` captures."""
    found = re.search(pattern, _read(rel), re.DOTALL)
    return set(re.findall(r"\d+", found.group(1))) if found else set()


def _module_constant(module: str, name: str) -> set[str]:
    """A constant's RUNTIME value, imported rather than read as text.

    Stronger than a regular expression over the source: `cli._NO_READER_EXIT` is
    written `(128 + signal.SIGPIPE) if hasattr(...) else 141`, so only evaluation
    answers what the process would really exit with.
    """
    import importlib

    mod = importlib.import_module(module)
    return {str(getattr(mod, name))}


def _claims_exit_codes() -> set[str]:
    """Every status `codebugs claims` can hand a shell, the fallback included."""
    from codebugs import claims

    return {str(v) for v in claims._EXIT.values()} | {"1"}


def _subprocess_guards(*rels: str) -> set[str]:
    """`except` handlers wrapping a `subprocess.*` call, counted across modules.

    A GUARD is the handler, not the call: several calls can share one `try`. The
    count is what the prose claims, so the count is what is derived.
    """
    total = 0
    for rel in rels:
        for node in ast.walk(ast.parse(_read(rel))):
            if not isinstance(node, ast.Try):
                continue
            if not any(
                isinstance(c, ast.Call) and ast.unparse(c.func).startswith("subprocess.")
                for c in ast.walk(node)
            ):
                continue
            total += len(node.handlers)
    return {str(total)}


def _dict_literal_keys(rel: str, name: str) -> set[str]:
    """The keys of a module-level dict literal, read from the syntax tree."""
    for node in ast.walk(ast.parse(_read(rel))):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            value = node.value
        elif isinstance(node, ast.Assign) and name in [
            getattr(t, "id", None) for t in node.targets
        ]:
            value = node.value
        else:
            continue
        if isinstance(value, ast.Dict):
            return {ast.literal_eval(k) for k in value.keys if isinstance(k, ast.Constant)}
    return set()


def _attention_signal_names() -> set[str]:
    """Every distinct signal the attention block can carry, from the live table."""
    from codebugs import findings

    names: set[str] = set()
    for signals in findings._ATTENTION_SIGNALS_BY_ACTION.values():
        names |= set(signals)
    return names


def _declared_exception_rows(rel: str) -> set[str]:
    """How many rows a test module's `DECLARED_EXCEPTIONS` table carries."""
    for node in ast.walk(ast.parse(_read(rel))):
        target = None
        if isinstance(node, ast.AnnAssign):
            target = getattr(node.target, "id", None)
        elif isinstance(node, ast.Assign):
            target = next((getattr(t, "id", None) for t in node.targets), None)
        if target == "DECLARED_EXCEPTIONS" and isinstance(node.value, ast.Dict):
            return {str(len(node.value.keys))}
    return set()


def _registry_functions() -> set[str]:
    """How many of the three module-level registration calls `db` actually offers."""
    names = ("register_schema", "register_tool_provider", "register_cli_provider")
    source = _read("src/codebugs/db.py")
    return {str(sum(1 for n in names if f"def {n}" in source))}


# --------------------------------------------------------------------------- #
# the tokenizer -- four lexical rules, spelling only
# --------------------------------------------------------------------------- #
CARDINALS = (
    "two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty "
    "seventy eighty ninety hundred thousand"
).split()

_CARDINAL_ALT = "|".join(CARDINALS)

_TOKEN = re.compile(r"\d[\d_,]*(?:\.\d+)?|\b(?:" + _CARDINAL_ALT + r")\b", re.IGNORECASE)

LEXICAL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "identifier",
        re.compile(r"[^\s|]*[A-Za-z][^\s|]*\d[^\s|]*|[^\s|]*\d[^\s|]*[A-Za-z][^\s|]*"),
    ),
    ("ordered-list marker", re.compile(r"(?m)^\s*\d+\.\s")),
    ("enumeration marker", re.compile(r"\*\*\(\d+\)")),
    ("compound adjective", re.compile(r"\b(?:" + _CARDINAL_ALT + r")-(?=[A-Za-z])", re.IGNORECASE)),
)


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    text: str
    line: int

    def context(self, source: str) -> str:
        return source[max(0, self.start - 70) : self.end + 55].replace("\n", " ")


def number_tokens(source: str) -> list[Token]:
    """Every number token left after the four lexical rules have run."""
    masked = bytearray(len(source))
    for _name, pattern in LEXICAL_RULES:
        for hit in pattern.finditer(source):
            masked[hit.start() : hit.end()] = b"\1" * (hit.end() - hit.start())
    out: list[Token] = []
    for hit in _TOKEN.finditer(source):
        if any(masked[i] for i in range(hit.start(), hit.end())):
            continue
        out.append(
            Token(hit.start(), hit.end(), hit.group(), source.count("\n", 0, hit.start()) + 1)
        )
    return out


# --------------------------------------------------------------------------- #
# a count that introduces its own enumeration is checked against it
# --------------------------------------------------------------------------- #
_WORD_VALUE = {word: i + 2 for i, word in enumerate(CARDINALS[:19])}

_ENUM_HEAD = re.compile(r"\*\*\((\d+)\)")
_TRAILING_CARDINAL = re.compile(r"\b(" + _CARDINAL_ALT + r")\b(?!.*\b(?:" + _CARDINAL_ALT + r")\b)", re.IGNORECASE | re.DOTALL)


def enumeration_counts(source: str) -> list[tuple[Token, int]]:
    """Pair each `N things: **(1)** ... **(N)**` head with the run it introduces.

    Discovered from the text, never declared in a table -- so it cannot go stale.
    A run with no cardinal word in front of it simply yields no pair; the word is
    then left to the NOT_A_CLAIM table, where a human says why it is not a claim.
    """
    pairs: list[tuple[Token, int]] = []
    runs: list[tuple[int, int]] = []  # (start offset of "**(1)", length of run)
    markers = [(m.start(), int(m.group(1))) for m in _ENUM_HEAD.finditer(source)]

    def _same_paragraph(a: int, b: int) -> bool:
        """A blank line ends a run. `CB-24`'s three consequences and `CB-27`'s two
        more sit in ADJACENT bullets numbered (1)-(3) then (4)-(5); without this the
        two would chain into one run of five and contradict both their own heads."""
        return "\n\n" not in source[a:b]

    i = 0
    while i < len(markers):
        if markers[i][1] != 1:
            i += 1
            continue
        j, expect = i, 1
        while (
            j < len(markers)
            and markers[j][1] == expect
            and _same_paragraph(markers[i][0], markers[j][0])
        ):
            j += 1
            expect += 1
        runs.append((markers[i][0], expect - 1))
        i = j
    for start, length in runs:
        window = source[max(0, start - 160) : start]
        found = _TRAILING_CARDINAL.search(window)
        if not found:
            continue
        offset = max(0, start - 160) + found.start(1)
        pairs.append(
            (
                Token(
                    offset,
                    offset + len(found.group(1)),
                    found.group(1),
                    source.count("\n", 0, offset) + 1,
                ),
                length,
            )
        )
    return pairs


# --------------------------------------------------------------------------- #
# the tables
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Live:
    """Class 1: a number that describes TODAY's tree.

    `declared` normally holds a comma-separated list and EVERY part must be
    derivable from the tree. `exact=True` switches to set equality — the whole
    declared string must be what the tree computes — which is what a claim about a
    complete SET needs, since a code silently dropped from it would otherwise pass.
    """

    file: str
    anchor: str
    declared: str
    compute: Callable[[], set[str]]
    reason: str
    occurrences: int = 1
    exact: bool = False


@dataclass(frozen=True)
class Removed:
    """Class 3: a number deleted from the prose, held down so it cannot come back.

    This is what makes "or deleted" a checkable half of the acceptance criterion
    rather than an assertion nobody can test. The row is a ratchet in reverse: it
    fails when the deleted text REAPPEARS.
    """

    file: str
    was: str
    reason: str


@dataclass(frozen=True)
class Historical:
    """Class 2: a measurement of a past state, never recomputed -- only stamped."""

    file: str
    anchor: str
    stamp: str
    reason: str
    occurrences: int = 1


@dataclass(frozen=True)
class NotAClaim:
    """A number that asserts nothing about this tree."""

    file: str
    anchor: str
    reason: str
    occurrences: int = 1


ROOT = "CLAUDE.md"
SUB = "src/codebugs/CLAUDE.md"


LIVE: tuple[Live, ...] = (
    # ---- the interpreter and lint pins -------------------------------------
    Live(
        ROOT,
        "**The pin is `3.14.4`, full patch",
        "3.14.4",
        lambda: {_read(".python-version").strip()},
        "the whole worktree harness compares the two trees against this pin, so a "
        "stale spelling here misdirects the repair command the guard prints",
    ),
    Live(
        ROOT,
        "Pin ruff 0.15.7",
        "0.15.7",
        lambda: _lock_version("ruff"),
        "the lint gate's version is a real pin in `uv.lock`; the prose telling a "
        "reader to pin a different one would refuse every finish",
    ),
    Live(
        ROOT,
        "line length 100",
        "100",
        lambda: _regex_values("pyproject.toml", r"line-length\s*=\s*(\d+)"),
        "a style rule a session applies while editing; it is one line of pyproject",
    ),
    Live(
        ROOT,
        "- Python 3.11+.",
        "3.11",
        lambda: {
            v.lstrip(">=")
            for v in _regex_values("pyproject.toml", r'requires-python\s*=\s*"([^"]+)"')
        },
        "the floor `requires-python` actually advertises",
    ),
    Live(
        ROOT,
        'leave `requires-python = ">=3.11"` advertising a range',
        "3.11",
        lambda: _regex_values("pyproject.toml", r'requires-python\s*=\s*"(>=[\d.]+)"').union(
            {
                v.lstrip(">=")
                for v in _regex_values("pyproject.toml", r'requires-python\s*=\s*"([^"]+)"')
            }
        ),
        "the same floor, quoted verbatim in the CLI section",
    ),
    Live(
        ROOT,
        "uniform on 3.11 through 3.14, measured by the `contracts` matrix",
        "3.11, 3.14",
        _ci_matrix_interpreters,
        "both ends of the range the `contracts` matrix really runs",
    ),
    # ---- subcategory 1b: named exit codes, checked by REACHABILITY ---------
    Live(
        ROOT,
        "`_guard_enforcement_armed` | exit 12",
        "12",
        lambda: _shell_exit_codes("tools/_guards.sh"),
        "1b: the unarmed-clone refusal status",
    ),
    Live(
        ROOT,
        "`_guard_nonempty_diff` | exit 9",
        "9",
        lambda: _shell_exit_codes("tools/_guards.sh"),
        "1b: the empty-branch refusal status",
    ),
    Live(
        SUB,
        "yields **141**",
        "141",
        lambda: _module_constant("codebugs.cli", "_NO_READER_EXIT"),
        "1b: the same status, stated in the subsystem file",
    ),
    # ---- counts of things in the tree --------------------------------------
    Live(
        ROOT,
        "**It checks all three hooks**",
        "three",
        lambda: _installed_hook_count(),
        "the armed-clone guard is only as wide as the number of hooks installed",
        # NOTE: compared as a WORD; see `_declared_as_number`.
    ),
    Live(
        ROOT,
        "`[5/7]` AFTER the forward-merge",
        "[5/7]",
        lambda: _literal_present("tools/worktree-finish.sh", "[5/7]", "[5/7]"),
        "a phase label a reader greps for; the script really prints it",
    ),
    Live(
        ROOT,
        "BEFORE `[6/7]`",
        "[6/7]",
        lambda: _literal_present("tools/worktree-finish.sh", "[6/7]", "[6/7]"),
        "the neighbouring phase label, likewise printed",
    ),
    # ---- the enforcement table's exit codes, all subcategory 1b ------------
    Live(
        ROOT,
        "`_guard_branch_type` (7)",
        "7",
        lambda: _shell_exit_codes("tools/_guards.sh"),
        "1b: the branch-type guard's refusal status",
    ),
    Live(
        ROOT,
        "pre-commit hook (1) | exit 7 / 1 |",
        "1",
        lambda: _shell_exit_codes("tools/pre-commit-hook.sh"),
        "1b: the pre-commit hook's refusal status, alongside the guard's own 7",
    ),
    Live(
        ROOT,
        "| exit 1 |",
        "1",
        lambda: _shell_exit_codes("tools/pre-commit-hook.sh")
        | _shell_exit_codes("tools/commit-msg-hook.sh")
        | _shell_exit_codes("tools/pre-merge-commit-hook.sh")
        | _shell_exit_codes("tools/worktree-finish.sh"),
        "1b: six rows of the enforcement table refuse with 1 — the three hooks and "
        "the integration lock; one row licenses all six because it is one status",
        occurrences=6,
    ),
    Live(
        ROOT,
        "exit 8, 11",
        "8, 11",
        lambda: _shell_exit_codes("tools/_guards.sh"),
        "1b: the workspace-on-main and main-clean refusals",
    ),
    Live(
        ROOT,
        "exit 5, 4, 6",
        "5, 4, 6",
        lambda: _shell_exit_codes("tools/_guards.sh"),
        "1b: the conflict-marker, scratch-file and stale-base refusals",
    ),
    Live(
        ROOT,
        "exit 13",
        "13",
        lambda: _shell_exit_codes("tools/worktree-finish.sh"),
        "1b: the in-lock re-check refusal, named three times in the same argument",
        occurrences=3,
    ),
    Live(
        ROOT,
        "exit 15",
        "15",
        lambda: _shell_exit_codes("tools/worktree-finish.sh"),
        "1b: the post-merge alarm's status, named three times in the same argument",
        occurrences=3,
    ),
    Live(
        ROOT,
        "exit 14",
        "14",
        lambda: _shell_exit_codes("tools/_guards.sh"),
        "1b: the interpreter-agreement refusal, named twice",
        occurrences=2,
    ),
    Live(
        ROOT,
        "exit 1 (CB-284)",
        "1",
        lambda: _shell_exit_codes("tools/worktree-finish.sh"),
        "1b: the finish script's refusal of a dirty worktree with no commit message",
    ),
    # ---- the claims exit-code API ------------------------------------------
    Live(
        ROOT,
        "**: `3` (held by someone else)",
        "3",
        _claims_exit_codes,
        "1b: the setup gate reads this status to abort; it is the one tracker call "
        "in the harness allowed to stop the run",
    ),
    Live(
        ROOT,
        "abort; `4` (already resolved)",
        "4",
        _claims_exit_codes,
        "1b: the already-resolved status a follow-up branch proceeds past",
    ),
    Live(
        ROOT,
        "legitimate; `5` (undetermined)",
        "5",
        _claims_exit_codes,
        "1b: the contended status that is retried once",
    ),
    Live(
        ROOT,
        "escape hatch past a `3`",
        "3",
        _claims_exit_codes,
        "1b: the same held-by-other status, referred to again",
    ),
    Live(
        ROOT,
        "`0` proceed, `1` error, `3` held by someone else,",
        "0, 1, 3",
        _claims_exit_codes,
        "1b: the first three statuses of the shell-caller API",
    ),
    Live(
        ROOT,
        "`4` already resolved, `5` contended (retry)",
        "4, 5",
        _claims_exit_codes,
        "1b: the remaining two statuses of the shell-caller API",
    ),
    Live(
        ROOT,
        "both `3` and `4`)",
        "3, 4",
        _claims_exit_codes,
        "1b: the two statuses the design would have had `--allow-duplicate` clear",
    ),
    # ---- the CLI's own two statuses ----------------------------------------
    Live(
        ROOT,
        "with the same 141**",
        "141",
        lambda: _module_constant("codebugs.cli", "_NO_READER_EXIT"),
        "1b: the closed-stdout refusal, read from the constant the process really "
        "exits with rather than from source text — it is written as an expression",
    ),
    Live(
        ROOT,
        "`141` was added package-wide by CB-78",
        "141",
        lambda: _module_constant("codebugs.cli", "_NO_READER_EXIT"),
        "1b: the same status, where the exit-code list documents it",
    ),
    Live(
        ROOT,
        "kills the producer at 141 rather than 1",
        "141",
        lambda: _module_constant("codebugs.cli", "_NO_READER_EXIT"),
        "1b: the same status in the pipeline example",
    ),
    Live(
        ROOT,
        "**`141` is deliberately not reused**",
        "141",
        lambda: _module_constant("codebugs.cli", "_NO_READER_EXIT"),
        "1b: the same status, in the sentence separating it from 74",
    ),
    Live(
        ROOT,
        "`141`*: `cli.run`",
        "141",
        lambda: _module_constant("codebugs.cli", "_NO_READER_EXIT"),
        "1b: the same status, in the EPIPE limit",
    ),
    Live(
        ROOT,
        "and **the 141 is not unconditional**",
        "141",
        lambda: _module_constant("codebugs.cli", "_NO_READER_EXIT"),
        "1b: the same status, in the residuals paragraph",
    ),
    Live(
        ROOT,
        "**`74` was added by CB-136**",
        "74",
        lambda: _module_constant("codebugs.cli", "_WRITE_FAILURE_EXIT"),
        "1b: `EX_IOERR`, the write-failure status the CLI really produces",
    ),
    Live(
        ROOT,
        "`74` wins",
        "74",
        lambda: _module_constant("codebugs.cli", "_WRITE_FAILURE_EXIT"),
        "1b: the same status, in the precedence rule",
    ),
    Live(
        ROOT,
        "those three calls never run",
        "three",
        _registry_functions,
        "the wiring procedure is only as wide as the registration functions `db` "
        "offers; a fourth would make this sentence wrong",
    ),
    Live(
        ROOT,
        "**before** `flock -u 9`",
        "9",
        lambda: _regex_values("tools/worktree-finish.sh", r"flock\s+-u\s+(\d+)"),
        "the lock descriptor the script really releases",
    ),
    Live(
        ROOT,
        "It needs **`fetch-depth: 0`**",
        "0",
        lambda: _regex_values(".github/workflows/main-invariants.yml", r"fetch-depth:\s*(\S+)"),
        "the checkout depth the audit job really uses",
    ),
    Live(
        SUB,
        "carries all fifteen `_COMMON_KEYS`",
        "fifteen",
        lambda: _tuple_len("src/codebugs/claims.py", "_COMMON_KEYS"),
        "a claims response is checked against this count by its own constructor",
    ),
    Live(
        SUB,
        "`agent_capacity`) and 19",
        "19",
        _milestone_tool_count,
        "the size of the protocol surface a client sees; it was 20 in prose and 19 "
        "in the tree, one of the four discrepancies package P-11 was opened on",
    ),
    Live(
        SUB,
        "owns four tables",
        "four",
        _milestone_tables,
        "the tables the milestones package really creates",
    ),
    Live(
        SUB,
        "`db.connect()` also sets `busy_timeout=5000`",
        "5000",
        lambda: _regex_values("src/codebugs/db.py", r"busy_timeout\s*=\s*(\d+)"),
        "the timeout that turns a losing writer into a clean rowcount=0",
    ),
    Live(
        SUB,
        "`DEFAULT_THRESHOLD = 0.7`",
        "0.7",
        lambda: _regex_values("src/codebugs/similarity.py", r"^DEFAULT_THRESHOLD\s*=\s*(\S+)"),
        "the calibrated similarity threshold, quoted from the module",
    ),
    Live(
        SUB,
        "`MIN_TEXT_LEN = 40`",
        "40",
        lambda: _regex_values("src/codebugs/similarity.py", r"^MIN_TEXT_LEN\s*=\s*(\S+)"),
        "the scoring floor, quoted from the module",
    ),
    Live(
        SUB,
        "newest 500",
        "500",
        lambda: _regex_values("src/codebugs/similarity.py", r"^CANDIDATE_POOL_LIMIT\s*=\s*(\d+)"),
        "the candidate pool size, quoted from the module",
    ),
    Live(
        SUB,
        "**exactly two callers pass `create=True`**",
        "two",
        lambda: _call_sites("src/codebugs/db.py", "_open"),
        "the whole point of the bullet is that no THIRD creating caller appears; a "
        "call site count is what makes that checkable",
    ),
    Live(
        SUB,
        "keys on `{8 READONLY, 10 IOERR, 13 FULL, 14 CANTOPEN}`",
        "8, 10, 13, 14",
        lambda: {
            ", ".join(
                sorted(
                    _braced_numbers(
                        "src/codebugs/db.py",
                        r"_ENVIRONMENTAL\w*\s*=\s*(?:frozenset\()?\{([^}]*)\}",
                    ),
                    key=int,
                )
            )
        },
        "the environmental sqlite codes `_is_environmental` really keys on; the "
        "whole set is one claim, because a code silently dropped from it is the "
        "failure the bullet exists to describe",
        exact=True,
    ),
    Live(
        SUB,
        "matches codes {5,6}",
        "5,6",
        lambda: {
            ",".join(
                sorted(
                    _braced_numbers("src/codebugs/db.py", r"def is_contention.*?\{([^}]*)\}"),
                    key=int,
                )
            )
        },
        "the contention codes `is_contention` really keys on",
        exact=True,
    ),
    Live(
        SUB,
        "All three registries are complete",
        "three",
        _registry_functions,
        "the same registration surface the root file's wiring procedure counts",
    ),
    Live(
        SUB,
        "Two rows exist today",
        "Two",
        lambda: _declared_exception_rows("tests/test_two_valued_path_gate.py"),
        "the exceptions table of the two-valued path gate; the bullet's whole point "
        "is that the table stays short, so its size is the claim",
    ),
    Live(
        SUB,
        "on all four branches",
        "four",
        lambda: {str(len(_dict_literal_keys("src/codebugs/findings.py", "_ATTENTION_SIGNALS_BY_ACTION")))},
        "the dedup branches the attention block must answer on; the signal-by-branch "
        "table is read by the response builder, so a branch added there changes the "
        "wire and this sentence at once",
    ),
    Live(
        SUB,
        "Two signals today",
        "Two",
        lambda: {str(len(_attention_signal_names()))},
        "the closed signal vocabulary, counted from the live table",
    ),
    Live(
        SUB,
        "All six subprocess guards (`provenance.py` ×5, `db.git_rev_parse`)",
        "six, 5",
        lambda: _subprocess_guards("src/codebugs/provenance.py", "src/codebugs/db.py")
        | _subprocess_guards("src/codebugs/provenance.py"),
        "the population the widening claim ranges over, counted twice over: the "
        "whole and `provenance.py`'s share. Building this row is what found the "
        "number wrong — it read five and ×4 against six handlers and five — and a "
        "claim about ALL of a population must know that population's size",
    ),
    Live(
        SUB,
        "across 3.11–3.14, so this file is executed",
        "3.11, 3.14",
        _ci_matrix_interpreters,
        "both ends of the CI matrix, quoted where the network gate declares which "
        "interpreters actually execute it",
    ),
)


HISTORICAL: tuple[Historical, ...] = (
    Historical(
        ROOT,
        "measured on git 2.53",
        "git 2.53",
        "how a CLEAN cherry-pick behaves is a property of git, not of this tree, so "
        "no commit of ours pins it; the tool version is the stamp, and it pins the "
        "measurement more tightly than a date could",
    ),
)


# Reused reasons. A reason repeated verbatim is a reason, not a shortcut: these
# three answer the same question the same way every time they are given.
_SELF_COUNT = (
    "a count of the items enumerated in the same passage, never of anything in the "
    "tree — it cannot rot from a code change, only from an edit to its own list, "
    "and this module checks such counts only where the list uses `**(N)**` markers"
)
_SHELL_LITERAL = "part of a quoted command line, where the digits are syntax rather than a quantity"
_POSIX = "a constant of POSIX, the kernel or CPython, fixed outside this repository"
_CROSS_REF = "points back at things named nearby in the document; it counts no population"
_PENDING_3 = "CLASS 3, deleted in the next commit of this branch: "
_CODE_LITERAL = "a literal inside quoted code, where the digit is the program's own text"
_SQLITE = "a SQLite result code, numbered by SQLite and not by this repository"
_GONE = (
    "counts code that no longer exists — the shape the bullet describes replacing — "
    "so there is nothing in today's tree to derive it from"
)
_SCENARIO = "an arbitrary small number inside a hypothetical; no population is counted"
_PAST_ROUND = "a fact about the history of a past card, stamped by that card's number"


NOT_A_CLAIM: tuple[NotAClaim, ...] = (
    # ---------------- root: dates of decisions, not measurements -------------
    NotAClaim(
        ROOT,
        "`../autosorter` (2026-08-16)",
        "the date this harness was borrowed from a sibling repository — provenance, "
        "not a measurement anything could recompute",
    ),
    NotAClaim(
        ROOT,
        "ON since 2026-08-21",
        "the date branch protection was switched on. It is repository CONFIGURATION, "
        "and the same paragraph already says nothing in this tree can verify or "
        "restore it",
    ),
    NotAClaim(
        ROOT,
        "Closed on 2026-08-22",
        "the date `enforce_admins` was switched on — repository configuration again",
    ),
    # ---------------- root: counts of their own lists ------------------------
    NotAClaim(ROOT, "**Two precise limits:**", _SELF_COUNT),
    NotAClaim(ROOT, "refuses four legitimate-if-rare flows", _SELF_COUNT),
    NotAClaim(ROOT, "with it the two settings", _SELF_COUNT),
    NotAClaim(ROOT, "Two consequences worth knowing", _SELF_COUNT),
    NotAClaim(ROOT, "**Four residuals, each measured", _SELF_COUNT),
    NotAClaim(ROOT, "**Three limits, each measured", _SELF_COUNT),
    NotAClaim(ROOT, "**Two places codebugs deliberately diverges", _SELF_COUNT),
    NotAClaim(ROOT, "module takes three steps", _SELF_COUNT),
    NotAClaim(ROOT, "Two entry points, and the split", _SELF_COUNT),
    # ---------------- root: cross-references ---------------------------------
    NotAClaim(ROOT, "on those last two:**", _CROSS_REF),
    NotAClaim(ROOT, "work the two of them did not agree on", _CROSS_REF),
    NotAClaim(ROOT, "the two sides become one environment", _CROSS_REF),
    NotAClaim(ROOT, "only that these two trees agree", _CROSS_REF),
    NotAClaim(ROOT, "the worse of the two.", _CROSS_REF),
    NotAClaim(
        ROOT,
        "`FINAL-DESIGN.md` §6.2–§6.3",
        "a section reference into a foreign document; this tree cannot number "
        "another repository's sections",
    ),
    NotAClaim(ROOT, "(design §6.3 passes", "the same foreign section reference"),
    # ---------------- root: constants owned elsewhere ------------------------
    NotAClaim(ROOT, "plan note at exit 0", _POSIX),
    NotAClaim(ROOT, "at exit 0 and writes nothing", _POSIX),
    NotAClaim(ROOT, "landing on fd 1 passes the probe", _POSIX),
    NotAClaim(ROOT, "rewrites the status to 120", _POSIX),
    NotAClaim(ROOT, "can reach `120`", _POSIX),
    NotAClaim(ROOT, "It is `128 + SIGPIPE`", _POSIX),
    NotAClaim(ROOT, "exceeds the 64 KB pipe buffer", _POSIX),
    NotAClaim(ROOT, "`codebugs bad-verb 2>&1 | head -0`", _SHELL_LITERAL),
    NotAClaim(ROOT, "`git log -1` code it replaced", _SHELL_LITERAL),
    # ---------------- root: interpreter versions -----------------------------
    NotAClaim(
        ROOT,
        "A bare `3.14` (i.e. `MAJOR.MINOR`)",
        "an illustrative counter-example — the shape the pin deliberately does NOT "
        "take. Binding it to the tree would assert the opposite of what it says",
    ),
    NotAClaim(
        ROOT,
        "**Honest scope: 3.15 and later",
        "the first interpreter ABOVE the verified matrix, named to bound the honest "
        "scope. It is arithmetic on the two versions already checked above, and "
        "nothing in the tree runs it — that is precisely the sentence's point",
    ),
    NotAClaim(
        ROOT,
        "behaviour change on 3.13",
        "names the interpreter whose finalization behaviour differs; a CPython fact "
        "stamped by its own version number",
    ),
    NotAClaim(
        ROOT,
        "so two machines legitimately differ",
        "an arbitrary two inside a hypothetical; no population is counted",
    ),
    NotAClaim(
        ROOT,
        "so two builds of the same version",
        "an arbitrary two inside a hypothetical; no population is counted",
    ),
    NotAClaim(
        ROOT,
        "still reports exit 1, `export-csv",
        "the pre-existing status of the file-writing arm, quoted to contrast it "
        "with 74; it is the ordinary CLI failure status the next bullet already "
        "derives from the tree",
    ),
    # ---------------- root: pending class 3 ----------------------------------
    NotAClaim(
        ROOT,
        "firing is two",
        _PENDING_3 + "it sizes a race window inside `worktree-finish.sh` in "
        "statements, which is prose precision no reader can act on",
    ),
    NotAClaim(ROOT, "exits 0 on an empty list", _POSIX),
    NotAClaim(
        ROOT,
        "stealing one.   2. **Finish",
        "the second item of a two-item ordered list whose marker sits mid-line "
        "after a reflow, so the ordered-list lexical rule cannot see it",
    ),
    NotAClaim(
        ROOT,
        "orchestrator (~48 lines)",
        _PENDING_3 + "the file holds many hundreds of lines; a line count moves on "
        "every ordinary edit, so it must be deleted rather than bound",
    ),
    NotAClaim(
        ROOT,
        "(three test modules call it in-process)",
        _PENDING_3 + "three different ways of counting the callers give three "
        "different answers, which is itself the argument for deleting the number",
    ),
    # ======================= subsystem file ==================================
    # ---------------- counts of their own lists ------------------------------
    NotAClaim(SUB, "because five writers bypass the hook", _SELF_COUNT),
    NotAClaim(SUB, "of two specific rows", _SELF_COUNT),
    NotAClaim(SUB, "its only two consumers", _SELF_COUNT),
    NotAClaim(SUB, "asks three questions per directory", _SELF_COUNT),
    NotAClaim(SUB, "HOW TWO DOORS STAYED OPEN", _SELF_COUNT),
    NotAClaim(SUB, "all three parts of it are load-bearing", _SELF_COUNT),
    NotAClaim(SUB, "and two more consequences", _SELF_COUNT),
    NotAClaim(SUB, "the three inputs of the derived", _SELF_COUNT),
    NotAClaim(SUB, "Two things this rule does *not* say", _SELF_COUNT),
    NotAClaim(SUB, "**(3)** three more filters validated", _SELF_COUNT),
    NotAClaim(SUB, "Two rules the guard itself must follow", _SELF_COUNT),
    NotAClaim(SUB, "Two things this rule does NOT do", _SELF_COUNT),
    NotAClaim(SUB, "**(10) THREE MORE FIELDS", _SELF_COUNT),
    NotAClaim(SUB, "two properties of it are load-bearing", _SELF_COUNT),
    NotAClaim(SUB, "those are two different obligations", _SELF_COUNT),
    NotAClaim(SUB, "Two related traps", _SELF_COUNT),
    NotAClaim(SUB, "two structural facts about the table", _SELF_COUNT),
    NotAClaim(SUB, "its two `except` arms", _SELF_COUNT),
    NotAClaim(SUB, "Two pins hold the order", _SELF_COUNT),
    NotAClaim(SUB, "Two things to know before touching it", _SELF_COUNT),
    NotAClaim(SUB, "Two different repairs", _SELF_COUNT),
    NotAClaim(SUB, "**Two commands are built into", _SELF_COUNT),
    NotAClaim(SUB, "**Two properties are load-bearing", _SELF_COUNT),
    NotAClaim(SUB, "**Four asymmetries with", _SELF_COUNT),
    NotAClaim(SUB, "Two resolutions of one path", _SELF_COUNT),
    NotAClaim(SUB, "narrowings — three:**", _SELF_COUNT),
    NotAClaim(SUB, "**two narrower statements", _SELF_COUNT),
    NotAClaim(SUB, "holds **two mechanisms", _SELF_COUNT),
    NotAClaim(SUB, "**Two layers.**", _SELF_COUNT),
    NotAClaim(SUB, "MCP tools across three phases", _SELF_COUNT),
    NotAClaim(SUB, "**Three more residuals", _SELF_COUNT),
    NotAClaim(SUB, "decision.** (1) The network gate", "an enumeration marker written without bold"),
    NotAClaim(SUB, "no wider. (2) A", "an enumeration marker written without bold"),
    NotAClaim(SUB, "to escape. (3) The gate reads", "an enumeration marker written without bold"),
    # ---------------- cross-references ---------------------------------------
    NotAClaim(SUB, "the two places this document left one", _CROSS_REF),
    NotAClaim(SUB, "*not there* in all three", _CROSS_REF),
    NotAClaim(SUB, "(CB-201 item 1", _CROSS_REF),
    NotAClaim(SUB, "All three simply return", _CROSS_REF),
    NotAClaim(SUB, "CB-24's original three", _CROSS_REF),
    NotAClaim(SUB, "leaves consequence (2) unsatisfiable", _CROSS_REF),
    NotAClaim(SUB, "and the two entities must be checked", _CROSS_REF),
    NotAClaim(SUB, "(CB-43 item 6)", _CROSS_REF),
    NotAClaim(SUB, "without those two cases", _CROSS_REF),
    NotAClaim(SUB, "the worse of two is `min`", _CROSS_REF),
    NotAClaim(SUB, "Read it with items (8) and (9)", _CROSS_REF),
    NotAClaim(SUB, "what makes the three a family", _CROSS_REF),
    NotAClaim(SUB, "where the three differ", _CROSS_REF),
    NotAClaim(SUB, "differs from its two siblings", _CROSS_REF),
    NotAClaim(SUB, "see item (6) of the CB-43 bullet", _CROSS_REF),
    NotAClaim(SUB, "per CB-24 consequence 4", _CROSS_REF),
    NotAClaim(SUB, "(CB-31, rule 2)", _CROSS_REF),
    NotAClaim(SUB, "remembered by two `except` clauses", _CROSS_REF),
    NotAClaim(SUB, "Reversing or collapsing the two", _CROSS_REF),
    NotAClaim(SUB, "**The three wiring steps", _CROSS_REF),
    NotAClaim(SUB, "because the two mechanisms", _CROSS_REF),
    NotAClaim(SUB, "gate are two halves of one", _CROSS_REF),
    NotAClaim(SUB, "and the two kinds of check", _CROSS_REF),
    NotAClaim(SUB, "FINAL-DESIGN.md` §10.", "a section reference into a foreign document"),
    NotAClaim(SUB, "(`findings.py:546`, `:581`)", "a line reference in this project's file:line notation"),
    # ---------------- literals inside quoted code ----------------------------
    NotAClaim(SUB, "`rowcount == 1`", _CODE_LITERAL),
    NotAClaim(SUB, "`SET n = n + 1`", _CODE_LITERAL),
    NotAClaim(SUB, '`sql.count("meta = ?") == 1`', _CODE_LITERAL),
    NotAClaim(SUB, "`rowcount` is `0`", _CODE_LITERAL),
    NotAClaim(SUB, "it writes `[1, 2]` for tags", _CODE_LITERAL),
    NotAClaim(SUB, "`len(cells) < 4`", _CODE_LITERAL),
    NotAClaim(SUB, '"`skipped` stays 0"', _CODE_LITERAL),
    NotAClaim(SUB, "expected to stay 0", _CODE_LITERAL),
    NotAClaim(SUB, "`chmod 000`", _SHELL_LITERAL),
    # ---------------- constants owned elsewhere ------------------------------
    NotAClaim(SUB, "raises the extended `1544`", _SQLITE),
    NotAClaim(SUB, "**`SQLITE_PERM` (3) is deliberately absent**", _SQLITE),
    NotAClaim(SUB, "yields 14, and no CLI path produces 3", _SQLITE),
    NotAClaim(SUB, "**`SQLITE_NOTADB` (26)", _SQLITE),
    NotAClaim(SUB, "`SQLITE_CONSTRAINT` is 19", _SQLITE),
    NotAClaim(SUB, "still at exit 1", "the ordinary CLI failure status, derived from the tree where the CLI section documents it"),
    NotAClaim(SUB, "`128 + SIGPIPE`", _POSIX),
    NotAClaim(SUB, "distinguishable from `1`", _POSIX),
    NotAClaim(SUB, "back to exit 120", _POSIX),
    NotAClaim(SUB, "the status 141 → 120", _POSIX),
    NotAClaim(SUB, "with fd 1 closed at exec", _POSIX),
    NotAClaim(SUB, "read-only file onto fd 1", _POSIX),
    NotAClaim(SUB, "CommonMark turns a 4-space-indented line", _POSIX),
    NotAClaim(SUB, "dividing by four in the guard", "the byte width of a 32-bit float, fixed by `struct`"),
    NotAClaim(SUB, "CPython 3.13 dedents docstrings", "external interpreter behaviour, stamped by its own version"),
    NotAClaim(SUB, "while 3.11/3.12 do not", "external interpreter behaviour, stamped by its own version"),
    NotAClaim(SUB, "from `mcp` 2.1.1 the SDK", "the SDK version whose behaviour is described; a foreign package's numbering"),
    NotAClaim(SUB, "**RULE, ratified 2026-08-25", "the date a rule was ratified — provenance, not a measurement"),
    # ---------------- descriptions of code that is gone ----------------------
    NotAClaim(SUB, "cost two queries per row", _GONE),
    NotAClaim(SUB, "the two grandfathered sites are gone", _GONE),
    NotAClaim(SUB, "The two raw sites existed", _GONE),
    NotAClaim(SUB, "because three paths did", _GONE),
    NotAClaim(SUB, "it took THREE review rounds", _PAST_ROUND),
    NotAClaim(SUB, "three existing tests caught it", _PAST_ROUND),
    NotAClaim(SUB, "not the four that got fixed", _PAST_ROUND),
    NotAClaim(SUB, "CB-24 fixed four sites", _PAST_ROUND),
    NotAClaim(SUB, "three independent passes over the same function", _PAST_ROUND),
    # ---------------- arbitrary numbers inside hypotheticals -----------------
    NotAClaim(SUB, "a three-valued question with two values", _SCENARIO),
    NotAClaim(SUB, "answering with two values", _SCENARIO),
    NotAClaim(SUB, "statements two writers both read", _SCENARIO),
    NotAClaim(SUB, "Two branches that each write", _SCENARIO),
    NotAClaim(SUB, "would put two live rows", _SCENARIO),
    NotAClaim(SUB, "because two copies of this procedure", _SCENARIO),
    NotAClaim(SUB, "outside one, two", _SCENARIO),
    NotAClaim(SUB, "until two writers overlap", _SCENARIO),
    NotAClaim(SUB, "two rules a rounding apart", _SCENARIO),
    NotAClaim(SUB, "treats as two", _SCENARIO),
    NotAClaim(SUB, "with two deliberate and caller-unreachable exceptions", "PENDING: the SQL-interpolation exception count, reconciled with the root rule in the last commit of this branch"),
    # ---------------- calibration and illustration ---------------------------
    NotAClaim(SUB, "the rejected 0.95", "a threshold considered and rejected during calibration; the archive records the corpus it was rejected on"),
    NotAClaim(SUB, '"Bug 1"/"Bug 2" ≈ 0.8 and two empty strings 1.0', "illustrative scores demonstrating why a minimum text length exists"),
    # ---------------- pending class 3 ----------------------------------------
    NotAClaim(SUB, "because about fifty `db.connect()` call sites", _PENDING_3 + "a call-site count that moves with ordinary work"),
    NotAClaim(SUB, "population is ~19 sites", _PENDING_3 + "this file's own rule says a number that decides something belongs in a test"),
    NotAClaim(SUB, "The outstanding 13 are on CB-36", _PENDING_3 + "same population, same rule"),
    NotAClaim(SUB, "the five write sites and four filter sites", _PENDING_3 + "two counts that move whenever a filter or a writer is added"),
    NotAClaim(SUB, "package's seven string-built SET clauses", _PENDING_3 + "a count of a shape nothing derives"),
    NotAClaim(SUB, "three test modules call it in-process", _PENDING_3 + "the second copy of the root file's wrong caller count"),
    NotAClaim(SUB, "Two consequences beyond", _SELF_COUNT),
    # ---------------- pending class 2: measurements needing a date ----------
    NotAClaim(
        SUB,
        "Measured on `adcf354`, ruff `0.15.7`",
        "PENDING CLASS 2: the S608 survey. It carries a commit and a tool version "
        "but no date; the date is added in the next commit of this branch",
    ),
    NotAClaim(
        SUB,
        'copy: `src/` carries 52 unsuppressed `S608` sites across 11 files, and **29 of them sit in nine files',
        "PENDING CLASS 2: the counts of that same survey",
    ),
    NotAClaim(
        SUB,
        "measured on `adcf354` with ruff `0.15.7`",
        "PENDING CLASS 2: the RUF100 survey, same measurement session, same gap",
    ),
    NotAClaim(
        SUB,
        "each give 56 `S608` hits",
        "PENDING CLASS 2: the count of that survey",
    ),
    NotAClaim(
        SUB,
        "(measured: 517 diagnostics",
        "PENDING CLASS 2: the preview-selection survey, same session, same gap",
    ),
)


REMOVED: tuple[Removed, ...] = ()


# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #
_WORD_TO_DIGITS = {word: str(value) for word, value in _WORD_VALUE.items()}


def _normalize(value: str) -> str:
    """Compare a written cardinal and a computed integer as the same thing."""
    return _WORD_TO_DIGITS.get(value.strip().lower(), value.strip())


def _rows_for(rel: str):
    for row in (*LIVE, *HISTORICAL, *NOT_A_CLAIM):
        if row.file == rel:
            yield row


def _anchor_spans(rel: str, source: str) -> list[tuple[int, int, object]]:
    """Where each row's anchor occurs, with the row that licenses it."""
    spans: list[tuple[int, int, object]] = []
    for row in _rows_for(rel):
        start = 0
        while True:
            at = source.find(row.anchor, start)
            if at < 0:
                break
            spans.append((at, at + len(row.anchor), row))
            start = at + 1
    return spans


def unaccounted(rel: str) -> list[Token]:
    """Every number token no row and no enumeration pairing covers."""
    source = _read(rel)
    covered = bytearray(len(source))
    for start, end, _row in _anchor_spans(rel, source):
        covered[start:end] = b"\1" * (end - start)
    for token, _length in enumeration_counts(source):
        covered[token.start : token.end] = b"\1" * (token.end - token.start)
    return [t for t in number_tokens(source) if not any(covered[i] for i in range(t.start, t.end))]


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_the_corpus_is_present_and_carries_numbers() -> None:
    """A gate that found no text would pass everything."""
    for rel in CORPUS:
        assert (REPO_ROOT / rel).is_file(), f"{rel} is not in the tree — discovery is broken"
        assert number_tokens(_read(rel)), (
            f"{rel} yielded no number tokens at all; the tokenizer is broken, and a "
            "broken tokenizer makes every accounting test below vacuous"
        )


@pytest.mark.parametrize("row", LIVE, ids=lambda r: f"{r.file}::{r.anchor[:38]}")
def test_every_live_claim_still_matches_the_tree(row: Live) -> None:
    computed = {_normalize(v) for v in row.compute()}
    wanted = [row.declared] if row.exact else [p.strip() for p in row.declared.split(",")]
    missing = [w for w in wanted if _normalize(w) not in computed]
    assert not missing, (
        f"{row.file} declares {row.declared!r} at {row.anchor!r}, but "
        f"{missing} is not what the tree says — it offers "
        f"{sorted(computed) or 'nothing at all'}.\n"
        f"Why this number is here: {row.reason}.\n"
        "Correct the prose, or — if the number now moves with ordinary work — "
        "delete it, write the sentence qualitatively, and add a REMOVED row."
    )


@pytest.mark.parametrize("row", REMOVED, ids=lambda r: f"{r.file}::{r.was[:38]}")
def test_no_deleted_number_has_come_back(row: Removed) -> None:
    """Class 3's checkable half: a number judged not worth binding stays gone."""
    assert row.was not in _read(row.file), (
        f"{row.file} carries {row.was!r} again. It was deleted deliberately: "
        f"{row.reason}.\nIf it must return, it needs a LIVE row deriving it from "
        "the tree — not a number nobody can recompute."
    )


@pytest.mark.parametrize(
    "row", (*LIVE, *HISTORICAL, *NOT_A_CLAIM), ids=lambda r: f"{r.file}:{r.anchor[:40]}"
)
def test_every_anchor_occurs_exactly_as_often_as_declared(row) -> None:
    """Self-deleting, and pinned by COUNT rather than by presence.

    A row that no longer describes anything real must fail, or the tables become
    the place stale permissions are parked. Counting rather than merely finding is
    what stops one row quietly stretching over a second, unexamined occurrence.
    """
    found = _read(row.file).count(row.anchor)
    assert found == row.occurrences, (
        f"{row.file}: the anchor {row.anchor!r} occurs {found} time(s), not the "
        f"{row.occurrences} this row declares. Re-read the paragraph and update or "
        "delete the row — an anchor that moved is a number nobody re-affirmed."
    )


def test_no_two_rows_cover_the_same_text() -> None:
    """A row matching more than its own text is REFUSED rather than stretched."""
    clashes = []
    for rel in CORPUS:
        spans = sorted(_anchor_spans(rel, _read(rel)), key=lambda s: (s[0], s[1]))
        for (a_start, a_end, a_row), (b_start, b_end, b_row) in zip(spans, spans[1:]):
            if b_start < a_end:
                clashes.append(f"{rel}: {a_row.anchor[:40]!r} overlaps {b_row.anchor[:40]!r}")
    assert not clashes, (
        "these rows cover overlapping text, so which one licenses a number is "
        f"ambiguous: {clashes}. Lengthen one anchor until the two are disjoint."
    )


@pytest.mark.parametrize("rel", CORPUS)
def test_every_number_in_the_corpus_is_accounted_for(rel: str) -> None:
    """THE COMPLETENESS HALF — the reason this module exists at all.

    The per-claim half above only ever checks the numbers somebody listed. Without
    this test the module is a LIST, and a list drifts away from the corpus exactly
    the way the corpus drifted away from the code.
    """
    source = _read(rel)
    loose = unaccounted(rel)
    assert not loose, (
        f"{len(loose)} number(s) in {rel} belong to no row:\n"
        + "\n".join(f"  line {t.line}: {t.text!r} in …{t.context(source)}…" for t in loose[:25])
        + "\n\nEvery number here is either a claim about this tree (add a LIVE row "
        "naming how to derive it), a measurement of the past (add a HISTORICAL row "
        "carrying its date and commit), or says nothing about the tree (add a "
        "NOT_A_CLAIM row saying why). Refusing the unclassified is the point: a "
        "number nobody looked at is exactly what this gate exists to catch."
    )


def test_every_historical_measurement_carries_a_stamp() -> None:
    """Class 2 is never recomputed, so its stamp is the only thing holding it.

    A measurement of THIS tree is stamped with a date and a commit; a measurement
    of an external tool is stamped with that tool's version, which pins it more
    tightly than a date could. Both are checked as text present in the anchor,
    because a stamp the prose does not carry helps no reader.
    """
    unstamped = []
    for row in HISTORICAL:
        if not row.stamp.strip():
            unstamped.append(f"{row.file}: {row.anchor[:50]!r} declares no stamp")
        elif row.stamp not in _read(row.file):
            unstamped.append(f"{row.file}: stamp {row.stamp!r} is not in the prose")
    assert not unstamped, (
        "these historical measurements are unstamped, so nothing says what state "
        f"they described: {unstamped}"
    )


def test_every_row_carries_a_reason() -> None:
    blank = [
        f"{row.file}:{row.anchor[:40]!r}"
        for row in (*LIVE, *HISTORICAL, *NOT_A_CLAIM)
        if not row.reason.strip()
    ]
    assert not blank, f"rows declared with no reason: {blank}"


@pytest.mark.parametrize("rel", CORPUS)
def test_a_count_that_introduces_an_enumeration_matches_it(rel: str) -> None:
    """`Four traps … **(1)** … **(4)**` is checkable against its own list.

    Discovered from the text rather than declared, so it cannot go stale. This is
    not a claim about the TREE — it cannot rot from a code change — which is why it
    sits outside the three classes; it is checked anyway because checking is
    cheaper than excusing it.
    """
    source = _read(rel)
    wrong = []
    for token, length in enumeration_counts(source):
        if _normalize(token.text) != str(length):
            wrong.append(f"line {token.line}: says {token.text!r} but lists {length} items")
    assert not wrong, f"{rel}: a count disagrees with the enumeration it introduces: {wrong}"

