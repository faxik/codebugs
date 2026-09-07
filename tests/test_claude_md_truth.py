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

Four buckets, and the union must be total over the token population AS THE
VOCABULARY ABOVE DEFINES IT -- read the limit below before trusting that
sentence, because the vocabulary is not every way a quantity can be written. An
uncovered token FAILS -- the default is refusal, not permission:

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

THE DECLARED LIMIT OF THE COMPLETENESS HALF, AND IT IS A LIMIT RATHER THAN A BUG.
The half is total over THE DECLARED VOCABULARY -- digits, and the cardinal words
`two` through `thousand` -- and that vocabulary is where it ends. **`one` is
deliberately NOT in it.** In this English prose `one` is overwhelmingly a pronoun
("the one that", "no one", "one of them"), and admitting it would bury the
population in false refusals: the ratchet that reddens forever is the ratchet
nobody keeps, which is the whole failure this module exists to avoid.

**The cost is exact, and here it is as an example rather than as a sentence,
because the example is worth more.** A cross-model review put this line into the
root file:

    The milestones package has exactly one module.

It is FALSE -- the package holds eight modules -- and the completeness half passed
it without a word. Say the consequence plainly: **two claims with the same meaning
get different protection depending on how they are SPELLED.** "one module" is
invisible; "eight modules" is refused until somebody classifies it. So when you
write a claim about this tree into either file, WRITE THE NUMBER AS A DIGIT or as
a cardinal from `two` up, and this gate will make you account for it.

The same limit, one step wider: a quantity written as neither digits nor a listed
cardinal -- "a handful", "a couple", "a dozen or so", "a single" -- is invisible
to the tokenizer for exactly the same reason.

Closing this properly is not a bigger word list. It is a structural markup for
facts in the prose (`<!-- fact: milestones.module_count -->` against a registry),
which makes the half exact BY CONSTRUCTION rather than by vocabulary. That trades
away the normative text's readability and works against the open card about corpus
growth, so it is an owner's decision and is carried on its own card, not here.

WHERE IT IS WRONG FIRST BESIDES THAT.
  * Rewording a sentence around an already-classified number reddens this file even
    though nothing changed in substance. That is the commonest false alarm and it is
    the price of having no shape generalization -- the repair is one edited anchor,
    and it forces the number to be re-affirmed by whoever touched the paragraph.
  * Several claims spelled with `one` ARE carried in LIVE below, so the per-claim
    half is deliberately WIDER than the completeness half at that spot -- but only
    for the claims a human listed, which is precisely the asymmetry above.

WHAT THIS GATE DOES NOT PROMISE (C-14, ratified in the package brief). It answers
whether the text is TRUE. It says nothing about how long an already-running session
keeps a superseded copy: the harness injects these files and the injected copy
carries no version, no commit and no age, so a session cannot tell which edition it
holds. That is repaired in the harness, not in this tree.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
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


def _module_dict(rel: str, name: str) -> ast.Dict | None:
    """The module-level dict literal assigned to `name`, or None.

    ONE walker, because two of them drifted apart within a single review round:
    the earlier pair spelled "find the assignment target" two different ways for
    the same question, and a future refinement would have had to be remembered
    twice.
    """
    for node in ast.walk(ast.parse(_read(rel))):
        if isinstance(node, ast.AnnAssign):
            targets = [getattr(node.target, "id", None)]
        elif isinstance(node, ast.Assign):
            targets = [getattr(t, "id", None) for t in node.targets]
        else:
            continue
        if name in targets and isinstance(node.value, ast.Dict):
            return node.value
    return None


def _dict_literal_keys(rel: str, name: str) -> set[str]:
    """The keys of a module-level dict literal, read from the syntax tree."""
    found = _module_dict(rel, name)
    if found is None:
        return set()
    return {ast.literal_eval(k) for k in found.keys if isinstance(k, ast.Constant)}


def _attention_signal_names() -> set[str]:
    """Every distinct signal the attention block can carry, from the live table."""
    from codebugs import findings

    names: set[str] = set()
    for signals in findings._ATTENTION_SIGNALS_BY_ACTION.values():
        names |= set(signals)
    return names


def _declared_exception_rows(rel: str) -> set[str]:
    """How many rows a test module's `DECLARED_EXCEPTIONS` table carries."""
    found = _module_dict(rel, "DECLARED_EXCEPTIONS")
    return {str(len(found.keys))} if found is not None else set()


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

# A COMPOUND NUMERAL is ONE number, not two. `twenty-two` used to reach the
# accounting as the token `two`, because the compound-ADJECTIVE rule swallowed
# `twenty-` and left the tail exposed: the right outcome (a refusal) arrived by the
# wrong mechanism, which is the exact defect this module exists to remove. `one` is
# admitted HERE and nowhere else — inside `twenty-one` it cannot be a pronoun.
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_UNITS = "one|two|three|four|five|six|seven|eight|nine"
_COMPOUND_NUMERAL = r"\b(?:" + _TENS + r")-(?:" + _UNITS + r")\b"

# A numeric run may CONTAIN a comma (`1,000`) but may not END on one: the trailing
# comma is punctuation, and admitting it made the token one character wider than the
# number, so a row anchored on the number alone stopped covering it.
_TOKEN = re.compile(
    _COMPOUND_NUMERAL + r"|\d(?:[\d_,]*\d)?(?:\.\d+)?|\b(?:" + _CARDINAL_ALT + r")\b",
    re.IGNORECASE,
)

LEXICAL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "identifier",
        re.compile(r"[^\s|]*[A-Za-z][^\s|]*\d[^\s|]*|[^\s|]*\d[^\s|]*[A-Za-z][^\s|]*"),
    ),
    ("ordered-list marker", re.compile(r"(?m)^\s*\d+\.\s")),
    ("enumeration marker", re.compile(r"\*\*\(\d+\)")),
    (
        "compound adjective",
        # `three-valued` is one lexeme; `twenty-two` is one NUMBER and must not be
        # eaten here, which is what the negative lookahead protects.
        re.compile(
            r"\b(?:" + _CARDINAL_ALT + r")-(?!(?:" + _UNITS + r")\b)(?=[A-Za-z])",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    text: str
    line: int

    def context(self, source: str) -> str:
        return source[max(0, self.start - 70) : self.end + 55].replace("\n", " ")


def _mask(length: int, spans: list[tuple[int, int]]) -> bytearray:
    """A byte-per-character map of what some rule has already claimed."""
    covered = bytearray(length)
    for start, end in spans:
        covered[start:end] = b"\1" * (end - start)
    return covered


def _uncovered(tokens: list[Token], covered: bytearray) -> list[Token]:
    """A token survives unless EVERY character of it is claimed by something.

    `any` here was a hole, and a cross-model review walked through it: in
    `a twenty-four-module package` the compound-ADJECTIVE rule masks the `four-`
    tail, one character of the token overlaps, and under `any` the whole numeral
    vanished — so a false claim spelled that way produced no token at all. Partial
    coverage is not coverage.
    """
    return [t for t in tokens if not all(covered[i] for i in range(t.start, t.end))]


def number_tokens(source: str) -> list[Token]:
    """Every number token left after the four lexical rules have run."""
    spans = [
        (hit.start(), hit.end())
        for _name, pattern in LEXICAL_RULES
        for hit in pattern.finditer(source)
    ]
    covered = _mask(len(source), spans)
    raw = [
        Token(hit.start(), hit.end(), hit.group(), source.count("\n", 0, hit.start()) + 1)
        for hit in _TOKEN.finditer(source)
    ]
    return _uncovered(raw, covered)


# --------------------------------------------------------------------------- #
# a count that introduces its own enumeration is checked against it
# --------------------------------------------------------------------------- #
# `two`..`twenty` are consecutive; the tens and the round numbers are not, so they
# are written out. Without the tens a compound numeral like `forty-five` would
# normalize to itself and never compare equal to anything the tree computes.
_WORD_VALUE = {word: i + 2 for i, word in enumerate(CARDINALS[:19])}
_WORD_VALUE.update(
    {
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
        "hundred": 100,
        "thousand": 1000,
    }
)

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
    of_this_tree: bool = True


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
        "with exactly FOUR sanctioned exceptions",
        "FOUR",
        lambda: {str(len(value_interpolation_sites()))},
        "the size of the sanctioned-exception list, derived from the tree by the "
        "same predicate the list itself is checked against — so the prose, the "
        "table and the code cannot disagree three ways",
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
        of_this_tree=False,
    ),
    Historical(
        SUB,
        'Measured on `adcf354` (2026-08-31), ruff `0.15.7`, a real `[tool.ruff.lint] select = ["S608"]` in a throwaway copy: `src/` carries 52 unsuppressed `S608` sites across 11 files, and **29 of them sit in nine files',
        '`adcf354` (2026-08-31)',
        "the S608 survey: how many interpolation sites `src/` carries, and how many "
        "of them sit in files that never validate an identifier. Recomputing it "
        "needs a throwaway copy of the tree carrying a lint rule this project has "
        "never enabled — which is exactly why it is a measurement and not a gate",
    ),
    Historical(
        SUB,
        'measured on `adcf354` (2026-08-31) with ruff `0.15.7`, a real `[tool.ruff.lint]` section carrying `select = ["S608"]` and one carrying `extend-select = ["S608"]` each give 56 `S608` hits',
        '`adcf354` (2026-08-31)',
        "the RUF100 survey from the same session: enabling one rule raises no "
        "dead-marker diagnostics, measured rather than reasoned",
    ),
    Historical(
        SUB,
        '(measured on `adcf354` (2026-08-31): 517 diagnostics',
        '`adcf354` (2026-08-31)',
        "the preview-selection survey from the same session, which is what stops "
        "the sentence beside it overclaiming in the other direction",
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
    NotAClaim(ROOT, "exits 0 on an empty list", _POSIX),
    NotAClaim(
        ROOT,
        "stealing one.   2. **Finish",
        "the second item of a two-item ordered list whose marker sits mid-line "
        "after a reflow, so the ordered-list lexical rule cannot see it",
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
    NotAClaim(SUB, "larger than the four that got fixed", _PAST_ROUND),
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
    # ---------------- calibration and illustration ---------------------------
    NotAClaim(SUB, "the rejected 0.95", "a threshold considered and rejected during calibration; the archive records the corpus it was rejected on"),
    NotAClaim(SUB, '"Bug 1"/"Bug 2" ≈ 0.8 and two empty strings 1.0', "illustrative scores demonstrating why a minimum text length exists"),
    # ---------------- pending class 3 ----------------------------------------
    NotAClaim(SUB, "Two consequences beyond", _SELF_COUNT),
    NotAClaim(SUB, "of TWO kinds", _SELF_COUNT),
    NotAClaim(SUB, "the odd one under the others'", _CROSS_REF),
    NotAClaim(SUB, "*(1), (2), (4), one mechanism:*", "the corpus's own enumeration of the three exceptions, written inline rather than with the `**(N)**` markers the lexical rule knows"),
    NotAClaim(SUB, "*(3), a different licence:*", "the third item of that same inline enumeration"),
    NotAClaim(SUB, "Each carries its reason at the site", _CROSS_REF),
    # ---------------- pending class 2: measurements needing a date ----------
)


REMOVED: tuple[Removed, ...] = (
    Removed(
        ROOT,
        "(~48 lines)",
        "`server.py` holds many hundreds of lines, and the word \"thin\" beside a "
        "small number was an instruction about where NOT to look. A line count "
        "moves on every ordinary edit, so binding it would redden this gate weekly; "
        "the sentence now says the file is large and says why that matters",
    ),
    Removed(
        ROOT,
        "three test modules call it in-process",
        "three ways of counting the callers of `cli.main` give three different "
        "answers — 14 files by syntax tree, 15 by text search, one of the 14 a "
        "manual script pytest never collects. A number with no single value cannot "
        "have a derivation, and the argument it supports (the split is load-bearing "
        "because the in-process callers are many) survives without it",
    ),
    Removed(
        ROOT,
        "assignments wide",
        "the width of a race window inside `worktree-finish.sh`, counted in "
        "statements. Nothing derives it and no reader can act on the difference "
        "between two statements and three",
    ),
    Removed(
        SUB,
        "three test modules call it in-process",
        "the second copy of the same wrong caller count, in the subsystem file",
    ),
    Removed(
        SUB,
        "about fifty",
        "how many `db.connect()` call sites pass no arguments. It moves with "
        "ordinary work, and the sentence needs only that MOST of them do",
    ),
    Removed(
        SUB,
        "~19 sites",
        "the size of the CB-24 population. This file's own rule says a number that "
        "decides something belongs in a test, and this one decided nothing while "
        "going stale",
    ),
    Removed(
        SUB,
        "outstanding 13",
        "the remainder of that same population; CB-36 carries the list with "
        "`file:line`, which is where a reader should look anyway",
    ),
    Removed(
        SUB,
        "five write sites and four filter sites",
        "two counts that move whenever a writer or a filter is added, in a sentence "
        "whose claim is that EVERY one of them resolves — a claim the counts do not "
        "strengthen",
    ),
    Removed(
        SUB,
        "seven string-built",
        "a count of a shape nothing in the tree derives, in a sentence pointing at "
        "an open card",
    ),
)


# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #
_WORD_TO_DIGITS = {word: str(value) for word, value in _WORD_VALUE.items()}

_ISO_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
_SHORT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


_UNIT_VALUE = {word: i + 1 for i, word in enumerate(_UNITS.split("|"))}


def _normalize(value: str) -> str:
    """Compare a written cardinal and a computed integer as the same thing."""
    text = value.strip().lower()
    if "-" in text:
        tens, _, unit = text.partition("-")
        if tens in _WORD_TO_DIGITS and unit in _UNIT_VALUE:
            return str(int(_WORD_TO_DIGITS[tens]) + _UNIT_VALUE[unit])
    return _WORD_TO_DIGITS.get(text, text)


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
    spans = [(start, end) for start, end, _row in _anchor_spans(rel, source)]
    spans += [(t.start, t.end) for t, _length in enumeration_counts(source)]
    return _uncovered(number_tokens(source), _mask(len(source), spans))


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

    A measurement OF THIS TREE must carry a date AND a commit, and both must sit
    inside the anchored text — a stamp the prose does not carry helps no reader. A
    measurement of an EXTERNAL tool carries that tool's version instead, which pins
    the measurement more tightly than a date could.
    """
    unstamped = []
    for row in HISTORICAL:
        if row.stamp not in row.anchor:
            unstamped.append(f"{row.file}: stamp {row.stamp!r} is not inside its own anchor")
        elif row.of_this_tree and not (
            _ISO_DATE.search(row.stamp) and _SHORT_SHA.search(row.stamp)
        ):
            unstamped.append(
                f"{row.file}: {row.stamp!r} measures THIS tree but names no date "
                "and commit — the pair is what makes it re-findable"
            )
    assert not unstamped, (
        "these historical measurements are unstamped, so nothing says what state "
        f"they described: {unstamped}"
    )


def test_every_row_carries_a_reason() -> None:
    """Every table in this module, of every shape, is read for its reason field.

    A reason that lives only in a comment beside a table is text no test reads, so
    `tests/test_exception_table_discipline.py` refuses a table without this check —
    and it caught SEARCH_DIRS and SANCTIONED_VALUE_INTERPOLATIONS when they had it
    only in prose above them.
    """
    blank = [
        f"{row.file}:{row.anchor[:40]!r}"
        for row in (*LIVE, *HISTORICAL, *NOT_A_CLAIM)
        if not row.reason.strip()
    ]
    blank += [f"NOT_A_PATH:{row.text!r}" for row in NOT_A_PATH if not row.reason.strip()]
    blank += [f"REMOVED:{row.was!r}" for row in REMOVED if not row.reason.strip()]
    blank += [
        f"SEARCH_DIRS:{name!r}"
        for name, reason in SEARCH_DIRS.items()
        if not isinstance(reason, str) or not reason.strip()
    ]
    blank += [
        f"SANCTIONED_VALUE_INTERPOLATIONS:{key!r}"
        for key, reason in SANCTIONED_VALUE_INTERPOLATIONS.items()
        if not isinstance(reason, str) or not reason.strip()
    ]
    blank += [
        f"PAIRED_RULES:{root_text!r}"
        for root_text, _sub, reason in PAIRED_RULES
        if not reason.strip()
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

# --------------------------------------------------------------------------- #
# THE POINTERS — the other half of a normative text that rots (oracle point 4)
#
# A number and a path fail the same way: the tree moves and the prose does not.
# Extraction is the same shape as for numbers — one declared lexical rule, then a
# total accounting in which the unclassified is REFUSED.
#
# THE LEXICAL RULE: a Markdown inline-code span with no whitespace that either
# contains a "/" or ends in a source extension is a path candidate. Everything
# else in backticks is a name, a command or a fragment of code.
#
# RESOLUTION, and every step of it is a convention this corpus really uses:
#   * a glob must match at least one tracked path;
#   * a path resolves at the repository root;
#   * or relative to the DIRECTORY OF THE FILE THAT NAMES IT — which is why the
#     subsystem file may write `db.py` and mean `src/codebugs/db.py`;
#   * or under one of SEARCH_DIRS, the directories this corpus habitually elides;
#   * or with `.py` appended, for a module named without its extension.
# Anything left must sit in NOT_A_PATH with a reason. The seven shapes a naive
# check would have reddened on are all there, each named.
# --------------------------------------------------------------------------- #
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_PATH_SHAPED = re.compile(r"^[\w./*<>-]+$")
_SOURCE_EXT = re.compile(r"\.(?:py|md|sh|yml|yaml|toml|json|html|lock|csv|db)$")

# directory -> why this corpus writes paths under it without naming it.
# SELF-DELETING: a directory nothing needs any more fails the test below.
SEARCH_DIRS: dict[str, str] = {
    "tools": "the harness scripts are named by basename throughout the Workflow section",
    "tests": "test modules are named by basename in both files",
    ".github/workflows": "`ci.yml` and `main-invariants.yml` are named by basename",
    "src/codebugs": "the subsystem file names its own siblings by basename",
}


@dataclass(frozen=True)
class NotAPath:
    file: str
    text: str
    reason: str


NOT_A_PATH: tuple[NotAPath, ...] = (
    # --- the seven shapes the reconnaissance found a naive check would redden on
    NotAPath(
        ROOT,
        ".worktrees/<slug>",
        "a TEMPLATE with a placeholder, not a path — the slug is filled in per branch",
    ),
    NotAPath(ROOT, ".worktrees/<type>-<slug>", "the same template, in its two-part form"),
    NotAPath(
        SUB,
        "tools/call",
        "not a path at all: the name of an MCP protocol method. The `tools/` prefix "
        "is what fools a pattern written for filenames",
    ),
    # (the fabricated `git mv src/keep.py .claude/plans/keep.md` example, the
    #  `*.md` / `*.html` / `worktree-*.sh` name patterns, `tests/_mcp_schema` and
    #  the `db.py:_open` file-symbol notation are all handled by RESOLUTION above
    #  rather than by a row: globs are expanded, the module gets its `.py`, and
    #  the illustrative example names two paths that really do resolve.)
    # --- runtime and gitignored paths -------------------------------------
    NotAPath(ROOT, ".worktrees/", "created by the harness and gitignored; absent in a clean clone"),
    NotAPath(ROOT, ".worktrees/.integrate.lock", "the integration lock file, created per run"),
    NotAPath(ROOT, ".claude/worktrees/", "the legacy worktree directory, likewise gitignored"),
    NotAPath(ROOT, ".codebugs/findings.db", "the tracker database, created by `codebugs init`"),
    NotAPath(SUB, ".codebugs/", "the tracker directory, likewise created at runtime"),
    NotAPath(SUB, "findings.db", "the tracker database again, named by basename"),
    NotAPath(ROOT, ".git/FETCH_HEAD", "a file git writes inside its own administrative directory"),
    # --- names that are not paths ------------------------------------------
    NotAPath(ROOT, "fix/", "a branch-name PREFIX; the trailing slash is naming, not a directory"),
    NotAPath(ROOT, "feature/", "a branch-name prefix"),
    NotAPath(ROOT, "refactor/", "a branch-name prefix"),
    NotAPath(ROOT, "fix/cb-48-tracker-root-init", "an example BRANCH name"),
    NotAPath(ROOT, "origin/main", "a git ref"),
    NotAPath(SUB, "stream/triage", "a milestone name in this product's own vocabulary"),
    NotAPath(SUB, "stream/security", "a milestone name in this product's own vocabulary"),
    NotAPath(ROOT, "briefs/", "a directory named by its last segment inside `.claude/plans/`"),
    # --- foreign trees ------------------------------------------------------
    NotAPath(
        ROOT,
        "../autosorter",
        "a SIBLING REPOSITORY. Nothing in this tree can promise what is in another "
        "checkout, or that it is checked out at all",
    ),
    NotAPath(
        ROOT,
        "FINAL-DESIGN.md",
        "a document belonging to that sibling repository, named by basename",
    ),
)


def path_candidates(rel: str) -> list[tuple[str, int]]:
    """Every path-shaped inline-code span in a corpus file, with its offset."""
    out: list[tuple[str, int]] = []
    for hit in _CODE_SPAN.finditer(_read(rel)):
        text = hit.group(1).strip()
        if not _PATH_SHAPED.match(text):
            continue
        if "/" in text or _SOURCE_EXT.search(text):
            out.append((text, hit.start()))
    return out


def _resolves_via(text: str, rel: str) -> str | None:
    """WHICH base resolves a path, or None. One resolver, two callers.

    The first draft answered yes/no here and re-spelled the candidate list inside
    the search-directory test — where the `OSError` arm was silently dropped in
    the copying. Returning the base that worked lets both callers share one arm.
    """
    if "*" in text:
        return "<glob>" if list(REPO_ROOT.glob(text)) else None
    bases: list[tuple[str, Path]] = [("<repo root>", REPO_ROOT), ("<naming file>", (REPO_ROOT / rel).parent)]
    bases += [(name, REPO_ROOT / name) for name in SEARCH_DIRS]
    for label, base in bases:
        for candidate in (base / text, base / (text + ".py")):
            try:
                if candidate.exists():
                    return label
            except OSError:
                pass
    return None


def _resolves(text: str, rel: str) -> bool:
    return _resolves_via(text, rel) is not None


def unresolved_pointers(rel: str) -> list[str]:
    excused = {row.text for row in NOT_A_PATH if row.file == rel}
    return sorted(
        {text for text, _ in path_candidates(rel) if text not in excused and not _resolves(text, rel)}
    )


@pytest.mark.parametrize("rel", CORPUS)
def test_every_pointer_either_resolves_or_says_why_not(rel: str) -> None:
    loose = unresolved_pointers(rel)
    assert not loose, (
        f"{rel} names paths that do not resolve and carry no reason: {loose}.\n"
        "Fix the pointer, or add a NOT_A_PATH row saying what the text is instead — "
        "a template, a branch name, a protocol method, a foreign tree."
    )


@pytest.mark.parametrize("rel", CORPUS)
def test_pointer_discovery_is_not_vacuous(rel: str) -> None:
    """A gate that found no paths would excuse every dangling one."""
    assert len(path_candidates(rel)) > 20, (
        f"{rel} yielded almost no path candidates; the extraction is broken, and a "
        "broken extraction makes the test above pass on any text at all"
    )


def test_no_declared_search_directory_is_unused() -> None:
    """Self-deleting: a directory nothing is resolved through must not linger."""
    needed = {
        label
        for rel in CORPUS
        for text, _offset in path_candidates(rel)
        if (label := _resolves_via(text, rel)) in SEARCH_DIRS
    }
    idle = sorted(set(SEARCH_DIRS) - needed)
    assert not idle, (
        f"nothing in the corpus resolves through {idle} any more — delete the row "
        "rather than leaving a standing permission behind"
    )


def test_no_not_a_path_row_is_stale() -> None:
    """Self-deleting: a row must still describe text the corpus really carries."""
    stale = []
    for row in NOT_A_PATH:
        if row.text not in {text for text, _ in path_candidates(row.file)}:
            stale.append(f"{row.file}: {row.text!r}")
    assert not stale, (
        f"these NOT_A_PATH rows describe nothing in the text any more: {stale}. "
        "Delete them — a table that only grows is where dangling pointers get parked."
    )


_RATIONALE_LINK = re.compile(r"`([\w./-]+\.md)#([^`]+)`")


@pytest.mark.parametrize("rel", CORPUS)
def test_every_rationale_anchor_resolves(rel: str) -> None:
    """`→ почему именно так: file.md#anchor` must land on a heading that exists.

    The archive declares its anchors EXPLICITLY as `{#slug}`, so this is an exact
    check and not a guess at how a renderer would slugify a Russian heading. It is
    the cheapest real check in this module: renaming a section in the archive is
    ordinary work, and nothing else would notice the pointers it orphaned.
    """
    broken = []
    for hit in _RATIONALE_LINK.finditer(_read(rel)):
        target, anchor = hit.group(1), hit.group(2)
        path = REPO_ROOT / target
        if not path.is_file():
            broken.append(f"{target} does not exist (anchor {anchor})")
        elif "{#" + anchor + "}" not in path.read_text(encoding="utf-8"):
            broken.append(f"{target} carries no heading anchored {{#{anchor}}}")
    assert not broken, (
        f"{rel} points into the rationale archive at headings that are gone: {broken}"
    )

# --------------------------------------------------------------------------- #
# THE ONE LIVE PAIR OF RULES: the root's absolute ban on interpolating VALUES
# into SQL, against the subsystem's three sanctioned exceptions (oracle point 5,
# and point 6's amendment by the level-(2) holder).
#
# HOW THE PREDICATE TELLS A VALUE FROM AN IDENTIFIER, said out loud because being
# unable to say it was declared an escalation rather than a licence to widen.
#
#   An interpolation puts PYTHON DATA into SQL text — a VALUE — when the
#   expression's own syntactic form can produce nothing an identifier could be:
#     * it produces a NUMBER — a numeric literal, arithmetic, or `len()`/`int()`/
#       `abs()`/`sum()`/`round()`. No SQLite identifier is a number.
#     * it RENDERS DATA AS AN SQL STRING LITERAL — an f-string with a `'` pressed
#       against both sides of a slot, `f"'{s}'"`. That is data being quoted, and
#       it is deliberately NOT "the text happens to contain a quote": SQL
#       fragments held in constants (`strftime('now')`) are full of quotes and are
#       SQL CODE, not data. Keying on the quote CHARACTER instead of on a quote
#       AROUND A SLOT was measured: it turned 3 sites into 13.
#   Everything else — a bare name, an attribute, a joined list of names, a clause
#   built elsewhere — is an identifier or a fragment, and is not this list's
#   business. Those are held by validation (`types.is_sql_identifier`) and by
#   membership of closed enumerations, which the subsystem file describes.
#
# ONE-STEP LOCAL RESOLUTION, and no more. A slot holding a bare name assigned
# EXACTLY ONCE in the same function is resolved to that assignment before the
# predicate runs — without it the two `SUBSTR(…, {prefix_len})` sites, the very
# ones the rule exists for, would read as identifiers. Two steps, a conditional
# assignment or a value crossing a function boundary are NOT followed.
#
# WHERE IT IS BLIND, AND THE DIRECTION OF THE BLINDNESS. The predicate reads the
# expression's SHAPE, never its data flow, so a value arriving through a plain
# parameter (`f"LIMIT {n}"`) reads as an identifier and is invisible. Closing
# that means value tracking — the boundary `test_no_network_capability.py` draws
# around `__import__` and `test_two_valued_path_gate.py` around `getattr`.
#
# TWO MORE BLIND SPOTS, NAMED BECAUSE A CROSS-MODEL REVIEW WALKED THEM PAST THE
# FIRST DRAFT AND BECAUSE NAMING BEATS DISCOVERING:
#   * `str.join` OVER A COMPREHENSION — `", ".join(str(v) for v in values)` spliced
#     into a query. The quote test does not fire (nothing quotes a slot) and the
#     number test does not fire (a `join` call is not arithmetic). Seeing it needs
#     to know what the comprehension yields, which is data flow.
#   * A STATEMENT FORM OUTSIDE `_SQL_STATEMENT`. The review proved this by running
#     it: `CREATE VIEW cap AS VALUES (3)` is valid SQLite and returns `[(3,)]`, and
#     a form nobody listed is a form this gate does not look at.
# Both are DECLARED rather than closed. The general answer to both is the same one,
# and it is not a longer list: refuse every interpolation by default and admit only
# an expression whose identifier type was checked. That is the shape of CB-172's
# debt (turning `S608` on across the package), which this unit is forbidden to
# enter, so it is recorded here and carried there.
# Widening instead to EVERY interpolation would take the population from 3 to 69
# across thirteen files, which is a package-wide change refused by the level-(2)
# holder for that reason. So the guarantee is stated at the width it holds: the
# declared list is EXACTLY the set of value interpolations this predicate sees,
# in both directions, and what the predicate cannot see is named here.
#
# THE REMAINING POPULATION HAS AN OWNER, so this boundary is discoverable from the
# tracker and not only from this comment: the 69 raw interpolation sites are the
# debt CB-172 carries (turning the `S608` lint rule on), and `src/codebugs/CLAUDE.md`
# describes what actually holds them today — identifier validation and membership
# of closed enumerations. This repository's own recorded lesson is that a rule
# written as an enumeration gets fixed only at the sites somebody enumerated; the
# card is what keeps the rest of the population from being forgotten here.
# --------------------------------------------------------------------------- #
PACKAGE = REPO_ROOT / "src" / "codebugs"

# NOTE THE BOUNDARIES, because the first draft got them wrong and a mutant caught
# it: writing `SELECT\s` and then a trailing `\b` demands a word boundary between
# the space and whatever follows, so `SELECT * FROM …` did NOT match while
# `SELECT claim_id FROM …` did — the gate was blind to every star-select. Each
# alternative now ends on a WORD, and the boundary is asserted after the word.
# THIS ENUMERATION IS ITSELF A DECLARED LIMIT. A statement form nobody listed is a
# statement this gate does not look at, and the cross-model review demonstrated one
# by execution: `CREATE VIEW cap AS VALUES (3)` is valid SQLite and was invisible.
# `VIEW` and `TRIGGER` are added for that reason; the general answer is not a longer
# list but refusing every interpolation by default, which is CB-172's territory.
_SQL_STATEMENT = re.compile(
    r"\b(SELECT|INSERT\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM"
    r"|CREATE\s+(UNIQUE\s+)?INDEX|CREATE\s+TABLE|CREATE\s+VIEW|CREATE\s+TRIGGER"
    r"|ALTER\s+TABLE)\b",
    re.IGNORECASE,
)

_NUMERIC_CALLS = {"len", "int", "abs", "sum", "round"}


def _flatten_sql(node: ast.expr, static: list[str], slots: list[ast.expr]) -> None:
    """Split a built SQL string into its static text and its interpolation slots.

    FOUR WAYS OF BUILDING A STRING, not one. The first draft understood only the
    f-string and `+` concatenation, and a cross-model review walked `.format()` and
    `%` straight past the gate. Both are handled here now, at the same depth.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        static.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.Constant):
                static.append(str(part.value))
            else:
                slots.append(part.value)
                static.append("\0")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        _flatten_sql(node.left, static, slots)
        _flatten_sql(node.right, static, slots)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        # `"… LIMIT %d" % n` — the template is the LEFT side, the values the right.
        _flatten_sql(node.left, static, slots)
        right = node.right
        for value in right.elts if isinstance(right, ast.Tuple) else [right]:
            slots.append(value)
            static.append("\0")
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        # `"… LIMIT {}".format(n)` — the receiver is the template.
        _flatten_sql(node.func.value, static, slots)
        for value in [*node.args, *(kw.value for kw in node.keywords)]:
            slots.append(value)
            static.append("\0")
    else:
        slots.append(node)
        static.append("\0")


def _produces_a_number(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return not isinstance(node.value, bool)
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
    ):
        return _produces_a_number(node.left) or _produces_a_number(node.right)
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) in _NUMERIC_CALLS:
        return True
    return False


def _quotes_a_slot(node: ast.expr) -> bool:
    """True when a `'` is pressed against both sides of an interpolation."""
    for inner in ast.walk(node):
        if not isinstance(inner, ast.JoinedStr):
            continue
        parts = inner.values
        for i, part in enumerate(parts):
            if not isinstance(part, ast.FormattedValue):
                continue
            before = str(parts[i - 1].value) if i and isinstance(parts[i - 1], ast.Constant) else ""
            after = (
                str(parts[i + 1].value)
                if i + 1 < len(parts) and isinstance(parts[i + 1], ast.Constant)
                else ""
            )
            if before.endswith("'") and after.startswith("'"):
                return True
    return False


def _accumulated_sql_names(scope: ast.AST) -> set[str]:
    """Names whose value, ACROSS ALL the statements that build it, holds SQL.

    A query assembled in two steps — `query = "SELECT …"` then `query += f"… {n}"`
    — hides the keyword from the fragment carrying the interpolation, and a
    cross-model review walked exactly that past the first draft. Here the pieces
    are joined per NAME before the keyword is looked for.
    """
    parts: dict[str, list[str]] = {}
    for node in ast.walk(scope):
        target = None
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign):
            target = next((t.id for t in node.targets if isinstance(t, ast.Name)), None)
        if target is None or node.value is None:
            continue
        static: list[str] = []
        _flatten_sql(node.value, static, [])
        parts.setdefault(target, []).append("".join(static))
    return {
        name
        for name, chunks in parts.items()
        if _SQL_STATEMENT.search("".join(chunks).replace("\0", ""))
    }


def value_interpolation_sites() -> dict[tuple[str, str], int]:
    """Every place in the package where PYTHON DATA is spliced into SQL text.

    Keyed by (module, the enclosing function or module-level name) rather than by
    line number: a line number is the one key an edit three screens above silently
    invalidates, and this table exists precisely to survive ordinary edits.

    DELIBERATELY NOT MEMOIZED, and this note exists so nobody "optimizes" it into
    a defect. Several tests call it, and a session-lifetime cache would let a
    SAFETY gate report clean about a snapshot rather than about the tree — the
    reasoning `tests/test_no_network_capability.py::_package_modules` already wrote
    down for the same shape of sweep, and the state `tests/CLAUDE.md`'s CB-215
    alarm exists to notice. Measured cost of not caching: about 0.14 s per call
    over the modules of the package, against a full suite of some three minutes.
    """
    found: dict[tuple[str, str], int] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))

        module_assigns: dict[str, list[ast.expr]] = {}
        for sub in tree.body:
            if isinstance(sub, ast.Assign):
                for target in sub.targets:
                    if isinstance(target, ast.Name):
                        module_assigns.setdefault(target.id, []).append(sub.value)

        def visit(
            node: ast.AST,
            where: str,
            assigns: dict[str, list[ast.expr]],
            sql_names: set[str],
            assigned: str | None,
        ) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner: dict[str, list[ast.expr]] = {}
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assign):
                        for target in sub.targets:
                            if isinstance(target, ast.Name):
                                inner.setdefault(target.id, []).append(sub.value)
                inner_sql = _accumulated_sql_names(node)
                for child in node.body:
                    visit(child, node.name, inner, inner_sql, None)
                return
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                assigned = node.target.id
            elif isinstance(node, ast.Assign):
                named = next((t.id for t in node.targets if isinstance(t, ast.Name)), None)
                if named is not None:
                    assigned = named
                    if where == "<module>":
                        where = named
            _collect(node, rel, where, assigns, found, sql_names, assigned, module_assigns)
            for child in ast.iter_child_nodes(node):
                visit(child, where, assigns, sql_names, assigned)

        module_sql = _accumulated_sql_names(tree)
        for child in tree.body:
            visit(child, "<module>", module_assigns, module_sql, None)
    return found


def _collect(
    node: ast.AST,
    rel: str,
    where: str,
    assigns: dict[str, list[ast.expr]],
    found: dict[tuple[str, str], int],
    sql_names: set[str],
    assigned: str | None,
    module_assigns: dict[str, list[ast.expr]],
) -> None:
    if not isinstance(node, (ast.JoinedStr, ast.BinOp, ast.Call)):
        return
    static: list[str] = []
    slots: list[ast.expr] = []
    _flatten_sql(node, static, slots)
    if not slots:
        return
    own_text = "".join(static).replace("\0", "")
    if not _SQL_STATEMENT.search(own_text) and assigned not in sql_names:
        return
    for slot in slots:
        resolved = slot
        if isinstance(slot, ast.Name):
            local = assigns.get(slot.id, [])
            outer = module_assigns.get(slot.id, [])
            if len(local) == 1:
                resolved = local[0]
            elif not local and len(outer) == 1:
                # A MODULE CONSTANT reached from inside a function. Without this the
                # resolution saw only the enclosing function and read a module-level
                # numeric flag as an identifier — the hole a cross-model review found
                # on `f"… json_valid(tags, {_JSON5}) …"`.
                resolved = outer[0]
        if _produces_a_number(resolved) or _quotes_a_slot(resolved):
            found[(rel, where)] = slot.lineno


# The list the subsystem rules file states in prose, written here as data so the
# two cannot drift. SELF-DELETING in both directions: a row the predicate no
# longer sees fails, and a site the predicate sees that has no row fails too.
SANCTIONED_VALUE_INTERPOLATIONS: dict[tuple[str, str], str] = {
    ("src/codebugs/claims.py", "_next_claim_id"): (
        "1 of 3: `prefix_len` is a NUMBER `len()` computes from the module constant "
        "CLAIM_ID_PREFIX, so no caller can reach the interpolated text"
    ),
    ("src/codebugs/findings.py", "_next_id"): (
        "2 of 3: the mirror image of the claims site, on FINDING_ID_PREFIX"
    ),
    ("src/codebugs/findings.py", "_membership_sql"): (
        "4 of 4, and the SAME mechanism as 1 and 2 rather than a new one: the module "
        "constant `_JSON5` is a SQLite JSON-validity FLAG, a number no caller can "
        "reach. It was MISSED by the first draft because one-step name resolution "
        "looked only inside the enclosing function and never at module constants — "
        "which made the prose's `exactly THREE` false while the gate said otherwise"
    ),
    ("src/codebugs/findings.py", "_POST_MIGRATION_INDEXES"): (
        "3 of 3, AND ITS MECHANISM IS DIFFERENT: repo-owned status literals are "
        "spliced into the WHERE of a partial unique index. The licence is not that "
        "the value is unreachable but that a table definition cannot bind parameters "
        "at all, so there is no parameterized form of the statement to prefer"
    ),
}


def _statement_span(tree: ast.Module, line: int) -> tuple[int, int]:
    """The SMALLEST statement containing a line, as (first line, last line).

    Smallest, not outermost: a reason has to sit near the interpolation, and the
    enclosing function would put the whole body in scope.
    """
    best = (line, line)
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if not node.lineno <= line <= end:
            continue
        if best == (line, line) or (end - node.lineno) < (best[1] - best[0]):
            best = (node.lineno, end)
    return best


def _comment_lines(source: str) -> set[int]:
    out: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            out.add(token.start[0])
    return out


def sites_without_a_reason() -> list[str]:
    """Value interpolations whose statement carries no comment explaining itself.

    THE LETTER OF THE REQUIREMENT WAS "a reason on the same line of code"; it is
    read here as "a reason attached to the interpolating STATEMENT" — its own line,
    any line of the statement, or the comment block directly above it. The intent
    is that a reader meets the reason where the interpolation is rather than
    hunting for it, and a multi-line `conn.execute(...)` is one statement. Read
    literally, the requirement would have been unsatisfiable without reflowing SQL.
    """
    bad = []
    parsed: dict[str, tuple[str, ast.Module, set[int]]] = {}
    for (rel, where), line in sorted(value_interpolation_sites().items()):
        if rel not in parsed:
            source = _read(rel)
            parsed[rel] = (source, ast.parse(source), _comment_lines(source))
        source, tree, comments = parsed[rel]
        first, last = _statement_span(tree, line)
        lines = source.split("\n")
        above = first - 1
        while above >= 1 and lines[above - 1].strip().startswith("#"):
            above -= 1
        if not any(c for c in comments if above < c <= last):
            bad.append(f"{rel}::{where} (line {line})")
    return bad


def test_the_value_interpolation_predicate_is_not_vacuous() -> None:
    """A predicate that saw nothing would license the whole package."""
    assert value_interpolation_sites(), (
        "the value-interpolation predicate found no site at all. It is supposed to "
        "see the three sanctioned exceptions; seeing none means it is broken, and a "
        "broken predicate makes both tests below pass over anything"
    )


def test_no_value_interpolation_lacks_a_reason() -> None:
    """The count the level-(2) holder asked for, and it must be zero."""
    bad = sites_without_a_reason()
    assert not bad, (
        f"these places splice a VALUE into SQL text and say nothing about why: {bad}.\n"
        "The root rule forbids it outright; an exception has to carry its mechanism "
        "at the site, and be listed in src/codebugs/CLAUDE.md."
    )


def test_the_sanctioned_list_matches_the_tree_in_both_directions() -> None:
    """Neither the list nor the tree may grow past the other in silence."""
    seen = set(value_interpolation_sites())
    declared = set(SANCTIONED_VALUE_INTERPOLATIONS)
    new = sorted(f"{rel}::{where}" for rel, where in seen - declared)
    gone = sorted(f"{rel}::{where}" for rel, where in declared - seen)
    assert not new, (
        f"a value is spliced into SQL at {new}, and the sanctioned list does not "
        "name it. Either bind the value, or add a row here AND a sentence to "
        "src/codebugs/CLAUDE.md — the list and the prose are one thing."
    )
    assert not gone, (
        f"the sanctioned list still names {gone}, where no value interpolation "
        "remains. Delete the row and the sentence: a list that only grows is where "
        "the next exception gets parked."
    )


# The pairs of rules the root and the subsystem file state about the same subject.
# The completeness of THIS LIST is a declared limit, exactly as the package brief
# says: no systematic comparison of every root rule against every subsystem rule
# has been done, so this table can prove a VIOLATION and never the absence of one.
PAIRED_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "Never interpolate values into SQL. Existing sanctioned",
        "Values are bound, with exactly FOUR sanctioned exceptions",
        "the root states the prescription and points at the list; the subsystem "
        "carries the list. A session that reads only the root now learns that "
        "exceptions exist and where they are, instead of meeting an absolute that "
        "the code contradicts in three places",
    ),
)


def test_every_paired_rule_is_still_stated_on_both_sides() -> None:
    broken = []
    for root_text, sub_text, _reason in PAIRED_RULES:
        if root_text not in _read(ROOT):
            broken.append(f"the root no longer says {root_text!r}")
        if sub_text not in _read(SUB):
            broken.append(f"the subsystem file no longer says {sub_text!r}")
    assert not broken, (
        f"a rule stated on both sides has lost one of them: {broken}. A session "
        "reading only the root would then meet an absolute the code contradicts."
    )
