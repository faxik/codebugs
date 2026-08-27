"""README names the same SETS the product does — four set comparisons, nothing more.

WHAT THIS GATE CHECKS, exactly and only:

1. The module set named by README's module table equals the CLI's `--mode`
   allowlist, minus `all`.
2. The two mode lists — the CLI's `--mode` allowlist and `server.SERVER_NAMES`
   — are compared with each other, and README's own account of where they
   diverge must be exactly right. See MODE DIVERGENCE below.
3. Every CLI verb appearing in a README command example exists in the CLI.
4. Every MCP tool name appearing in a README `| Tool | Purpose |` table exists
   in the wire golden.

**WHAT THIS GATE DOES NOT CHECK, and must never be read as checking.** It does
NOT verify that README is accurate. It reads name sets and compares them with
name sets. It is blind to:

- **prose** — every sentence in the file, including every sentence describing
  what a verb or a tool actually does;
- **numbers.** A count written in English prose — "these four modules", "one
  line to stderr" — is not checked here, and this bullet used to claim README
  carried none at all, which was false when it was written (CB-232). **This is
  deliberately not gated, and the reason is worth stating rather than leaving
  as an omission.** README is English, and English is full of legitimate
  numerals: "one SQLite database", "Python 3.11+", "three flavors", "1. File
  the observation". A gate over numerals would have to decide which of those
  counts something a reader could check, and it cannot — so it would either
  fire on the legitimate ones (a machine for false alarms, which is a gate
  people delete) or key on an enumeration of the phrasings someone thought of,
  which is the CB-227 defect. The answer taken instead was to REMOVE the count
  from the prose rather than to start checking counts: a claim that is not made
  cannot be wrong. Nothing stops a future edit from adding one back, and this
  gate will not notice — said plainly, because a promise wider than the check
  behind it is what CB-232 is about;
- **flags and their behaviour** — `--append-note`, `--older-than 30d`,
  `--new-category`, defaults, whether a declared flag exists at all, and
  whether a flag that exists does what the surrounding sentence says. The one
  exception is narrow and is about READING, not behaviour: an example whose
  leading token is a flag this gate does not know is REFUSED rather than
  skipped, because it cannot tell whether that flag swallows the next token
  and therefore cannot tell which token is the verb;
- **the accuracy of any example's OUTPUT** — the `categories` block, the
  `reqs-verify` block, the `milestone-status` block, the `anchor-resolve` block
  and the close-gate refusal are all reproduced from real runs, and nothing
  here would notice if the command's real output changed tomorrow;
- **whether a command actually SUCCEEDS** — a verb can exist and still refuse
  every invocation README shows, and `codebugs-mcp --mode <x>` is not a CLI
  verb at all, so no example of the SERVER's command line is read here;
- **MCP tool names mentioned in PROSE or in the module table's "Headline
  tools" column** — only the `| Tool | Purpose |` tables are read;
- **new CLI verbs and new MCP tools README never mentions.** README is not
  obliged to document every verb, so growth in that direction is silent here.
  Only the MODULE set is held to equality; the tool and verb checks are
  containments.

That list is the point of this docstring. A previous gate in this repository
was justified as "an enumeration does not converge, so check the capability"
and then turned out to be an enumeration of spellings itself, catching one
evasion in thirteen (CB-227). This gate makes the smaller promise on purpose:
name sets, checked as name sets.

**WHAT THE READERS THEMSELVES STILL CANNOT SEE (CB-232).** The two readers
below were widened from spellings to capabilities — a fence is a fence at any
indentation, in backticks or tildes, at any length of three or more, closed
only by a fence of the same character and at least the same length; a table is
recognised by its column NAMES rather than by one byte-exact header line; a
long option may carry its value with `=`. Four evasions survive that widening
and are named here rather than left to be rediscovered, because a miss that is
announced costs less than a miss that is not:

- a `codebugs` example in an **indented code block** (four spaces, no fence).
  Closing it means treating any sufficiently indented line as code, and README
  indents ordinary prose under its list items, so the fix would read paragraphs
  as commands;
- an example invoked by another **spelling of the program** — `python -m
  codebugs.cli query`, `uv run codebugs query`, an absolute path. Only a line
  whose command word is `codebugs` is read;
- an example inside a **block quote** (`> codebugs ...`), or any other
  container this line-oriented reader does not model;
- a **tool table** whose first two columns are named something other than Tool
  and Purpose. That one fails towards silence only for the tables beyond the
  first: if NO table is found at all the gate refuses outright, but a fifth
  table renamed while four remain readable simply stops being read.

MODE DIVERGENCE, AND WHY IT IS A COMPARISON RATHER THAN A SENTENCE. Two mode
lists exist and they disagree: the CLI's `--mode` choices and
`server.SERVER_NAMES`. `usage` registers a CLI provider and no tool provider,
so it is a CLI mode and not a server one, and an MCP server started with
`--mode usage` does not run with an empty catalogue — argparse refuses the
value outright and the process exits 2. README used to describe the first half
of that correctly and the second half wrongly, and no test could see it,
because the two lists were never compared with each other (CB-232). They are
now. README marks a CLI-only module in the table row itself, with the words
`CLI only`, and the set of rows so marked must equal the set difference
exactly: a mode that appears on one surface and not the other turns this gate
red instead of turning a reader's server into an exit 2. A mode that exists on
the SERVER and not in the CLI is refused outright, because README's table has
no vocabulary for that direction — inventing one silently is how the first
divergence went undescribed for as long as it did.

Note which list the MODULE table is compared against, and why the answer is
still the CLI's: it is the wider of the two, README's table documents a flag
both surfaces take, and gating on the narrower list would let `usage` be
dropped from README without a word.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from tests.cli_surface import collect_cli_surface

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
CLI_SOURCE = REPO_ROOT / "src" / "codebugs" / "cli.py"
SERVER_SOURCE = REPO_ROOT / "src" / "codebugs" / "server.py"
MCP_GOLDEN = REPO_ROOT / "tests" / "golden" / "mcp_schema.json"

#: Global options that sit between `codebugs` and the verb. A README example
#: may carry them, and the token after them is still the verb. This is an
#: enumeration, and it is only safe because an UNLISTED leading flag refuses
#: rather than being skipped — see `_verb_of`. A new global option therefore
#: costs one loud failure here instead of silently blinding the reader.
_GLOBAL_OPTS_WITH_VALUE = {"--mode", "--tracker-root"}
_GLOBAL_FLAGS = {"--version", "-h", "--help"}

#: How README marks a module that exists on one surface and not the other.
#: Matched case-insensitively, hyphen or space, so `CLI-only` and `CLI only`
#: both count; anything else is not a mark, and the mode-divergence check then
#: refuses, which is the loud direction.
_CLI_ONLY_MARK = re.compile(r"\bCLI[ -]?only\b", re.IGNORECASE)


def _readme_lines() -> list[str]:
    return README.read_text(encoding="utf-8").splitlines()


def _fence_state(lines: list[str]):
    """Yield `(line, inside_a_fenced_block)` for every line of README.

    A CODE FENCE, as a capability rather than as a spelling: any leading
    whitespace, three or more backticks or tildes, and — once open — a block
    that only a fence of the SAME character and AT LEAST the same length can
    close. That last clause is what keeps a ```` ``` ```` line inside a
    four-backtick block from ending it, and it is why the length is carried
    rather than discarded.

    The predecessor tested `line.startswith("```")` on the RAW line, so a fence
    indented by one space — the ordinary shape under a list item — was not a
    fence, every example inside it was invisible, and a command naming a verb
    the CLI does not have passed the gate in silence (CB-232). Indentation is
    not capped at CommonMark's three columns on purpose: a fence nested under a
    list item legitimately sits further in, and the cost of over-recognising a
    fence is bounded to a prose line that begins with the word `codebugs`,
    which this gate would want to read anyway.
    """
    open_char: str | None = None
    open_len = 0
    for line in lines:
        stripped = line.strip()
        char = stripped[0] if stripped else ""
        run = 0
        if char in ("`", "~"):
            while run < len(stripped) and stripped[run] == char:
                run += 1
        is_fence = run >= 3
        if open_char is None:
            if is_fence:
                open_char, open_len = char, run
                yield line, False  # the opening fence itself is not content
                continue
            yield line, False
            continue
        # inside a block: only a bare, long-enough fence of the same char closes
        if is_fence and char == open_char and run >= open_len and not stripped[run:].strip():
            open_char, open_len = None, 0
            yield line, False
            continue
        yield line, True


def _verb_of(command_line: str) -> str | None:
    """The CLI verb of one `codebugs ...` example, or `None` when there is none.

    Fails LOUDLY rather than quietly on a leading token it cannot classify. A
    long option may spell its value with `=`, so `--mode=findings` is the same
    option as `--mode findings` and is recognised as such; but a leading flag
    that is in neither global set is REFUSED, because this reader cannot know
    whether it swallows the following token and therefore cannot know which
    token is the verb. Before CB-232 such an example was silently dropped,
    which made "no verb here" and "a verb I could not find" the same answer —
    the shape this repository keeps paying for.
    """
    tokens = command_line.split("#", 1)[0].split()[1:]  # drop `codebugs`, drop comments
    while tokens:
        token = tokens[0]
        name = token.split("=", 1)[0]
        if name in _GLOBAL_OPTS_WITH_VALUE:
            tokens = tokens[2:] if "=" not in token else tokens[1:]
            continue
        if name in _GLOBAL_FLAGS:
            tokens = tokens[1:]
            continue
        if token.startswith("-"):
            pytest.fail(
                f"README example {command_line!r} leads with {token!r}, which this gate does "
                f"not know as a global option. It cannot tell whether that flag consumes the "
                f"next token, so it cannot tell which token is the verb. Add the flag to "
                f"_GLOBAL_OPTS_WITH_VALUE or _GLOBAL_FLAGS rather than leaving the example "
                f"unread."
            )
        return token
    return None


def _is_table_header(line: str, first: str, second: str) -> bool:
    """A markdown table header recognised by its COLUMN NAMES, not its bytes.

    `| Tool | Purpose |` and `|Tool|Purpose|` are the same table. Keying on one
    exact string meant a table reformatted anywhere in the file stopped being
    read, and — because the tool check only refuses when NO table is found at
    all — the tools inside it stopped being checked without a word (CB-232).
    """
    if not line.strip().startswith("|"):
        return False
    cells = [cell.strip().lower() for cell in line.strip().strip("|").split("|")]
    return len(cells) >= 2 and cells[0] == first and cells[1] == second


def _mode_allowlist() -> set[str]:
    """The `--mode` choices literal in `cli.py`, read by AST and fail-closed.

    Read from the source rather than by importing, because the list is an
    inline literal inside `main()` and there is no object to ask. Anything
    unexpected — the argument gone, the choices no longer a literal list, a
    non-string member — fails the gate rather than returning a smaller set,
    since a silently smaller set here is a gate that cannot fire.
    """
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        first = node.args[0] if node.args else None
        if not (isinstance(first, ast.Constant) and first.value == "--mode"):
            continue
        for kw in node.keywords:
            if kw.arg != "choices":
                continue
            if not isinstance(kw.value, ast.List):
                pytest.fail(
                    "the --mode choices in cli.py is no longer a list literal, so this "
                    "gate can no longer read it. Teach it the new shape rather than "
                    "letting it return an empty set."
                )
            members = set()
            for elt in kw.value.elts:
                if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
                    pytest.fail(f"non-string member in the --mode choices list: {ast.dump(elt)}")
                members.add(elt.value)
            found.append(members)
    if len(found) != 1:
        pytest.fail(
            f"expected exactly one `--mode` argument carrying `choices=` in {CLI_SOURCE}, "
            f"found {len(found)}. This gate reads that one literal; with none it would "
            f"pass vacuously and with two it would not know which one binds."
        )
    return found[0]


def _server_mode_names() -> set[str]:
    """The keys of `SERVER_NAMES` in `server.py`, read by AST and fail-closed.

    Read from the source rather than by importing, for the reason
    `_mode_allowlist` is: this gate reads text, and importing `server` would
    pull the MCP SDK in behind it, so a gate about what README SAYS would start
    depending on whether a runtime dependency imports. Anything unexpected —
    the name gone, the value no longer a dict literal, a non-string key — fails
    the gate rather than returning a smaller set, since a silently smaller set
    here would make the divergence check pass by having nothing to compare.
    """
    tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "SERVER_NAMES" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            pytest.fail(
                "SERVER_NAMES in server.py is no longer a dict literal, so this gate can no "
                "longer read it. Teach it the new shape rather than letting it return an "
                "empty set."
            )
        keys = set()
        for key in node.value.keys:
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                pytest.fail(f"non-string key in SERVER_NAMES: {ast.dump(key) if key else 'None'}")
            keys.add(key.value)
        found.append(keys)
    if len(found) != 1:
        pytest.fail(
            f"expected exactly one `SERVER_NAMES = {{...}}` assignment in {SERVER_SOURCE}, "
            f"found {len(found)}. With none this gate would pass vacuously and with two it "
            f"would not know which one the server uses."
        )
    return found[0]


def _readme_module_rows() -> dict[str, str]:
    """Module name -> its whole table row, from README's module table.

    The table is recognised by its column names. A table that is not found
    fails the gate: a renamed table must be taught to this reader, never
    silently skipped.
    """
    rows: dict[str, str] = {}
    inside = False
    saw_table = False
    for line in _readme_lines():
        if _is_table_header(line, "module", "domain"):
            inside, saw_table = True, True
            continue
        if not inside:
            continue
        if not line.strip().startswith("|"):
            inside = False
            continue
        if set(line) <= set("|-: "):  # the header separator row
            continue
        match = re.match(r"\|\s*\*\*([a-z_]+)\*\*\s*\|", line.strip())
        if match is None:
            pytest.fail(
                f"unparsed row in README's module table: {line!r}. Every row must name "
                f"its module as `| **name** |`, or this gate stops seeing that module."
            )
        rows[match.group(1)] = line
    if not saw_table:
        pytest.fail(
            "README no longer has a table whose first two columns are Module and Domain. "
            "That table is what this gate compares against the --mode allowlist."
        )
    return rows


def _readme_modules() -> set[str]:
    return set(_readme_module_rows())


def _readme_cli_only_modules() -> set[str]:
    """Modules README's table marks as existing on the CLI and not on the server."""
    return {name for name, row in _readme_module_rows().items() if _CLI_ONLY_MARK.search(row)}


def _readme_command_verbs() -> set[str]:
    """CLI verbs from `codebugs ...` lines inside README's fenced code blocks."""
    verbs: set[str] = set()
    for line, fenced in _fence_state(_readme_lines()):
        if not fenced:
            continue
        stripped = line.strip()
        if stripped.startswith("$"):  # an optional shell prompt, with or without a space
            stripped = stripped[1:].strip()
        if not stripped.startswith("codebugs "):
            continue
        verb = _verb_of(stripped)
        if verb is not None:
            verbs.add(verb)
    return verbs


def _readme_tool_names() -> set[str]:
    """Tool names from the first cell of every Tool/Purpose table row.

    All backticked tokens in that cell are taken, because a row legitimately
    names a pair (``` `codesweep_archive` / `codesweep_archive_items` ```).
    """
    names: set[str] = set()
    inside = False
    for line in _readme_lines():
        if _is_table_header(line, "tool", "purpose"):
            inside = True
            continue
        if not inside:
            continue
        if not line.strip().startswith("|"):
            inside = False
            continue
        if set(line) <= set("|-: "):
            continue
        first_cell = line.strip().split("|")[1]
        names.update(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", first_cell))
    return names


def mode_set_problems(
    *,
    cli_modes: set[str],
    server_modes: set[str],
    documented: set[str],
    cli_only_documented: set[str],
) -> list[str]:
    """Compare the two mode lists with each other and with README's account.

    A PURE function of four sets, deliberately: the divergence it checks is a
    property of the sets, so a test can substitute either side and watch the
    gate redden without having to rewrite `cli.py` or `server.py`. The readers
    above are thin adapters over it.
    """
    problems: list[str] = []

    missing_from_readme = sorted((cli_modes - {"all"}) - documented)
    if missing_from_readme:
        problems.append(
            f"valid `--mode` values with no row in README's module table: {missing_from_readme}"
        )
    not_a_mode = sorted(documented - (cli_modes - {"all"}))
    if not_a_mode:
        problems.append(f"README's module table names these, but they are not `--mode` values: {not_a_mode}")

    server_only = sorted((server_modes - {"all"}) - cli_modes)
    if server_only:
        problems.append(
            f"these are `server.SERVER_NAMES` modes that the CLI's `--mode` does not accept: "
            f"{server_only}. README's module table has no vocabulary for a mode that exists on "
            f"the MCP server and not on the CLI — it marks the opposite direction with "
            f"`CLI only`. Add one and teach this gate, rather than letting README describe a "
            f"divergence it cannot name."
        )

    actual_cli_only = (cli_modes - server_modes) - {"all"}
    if cli_only_documented != actual_cli_only:
        problems.append(
            f"README marks {sorted(cli_only_documented)} as CLI-only, but the modes the CLI "
            f"accepts and `server.SERVER_NAMES` does not are {sorted(actual_cli_only)}. A "
            f"reader who starts `codebugs-mcp --mode <x>` on a mode the server does not carry "
            f"gets argparse's exit 2, not an empty tool catalogue, so this must be exact."
        )
    return problems


class TestReadmeNamesTheSameSets:
    def test_the_module_table_is_the_mode_allowlist(self) -> None:
        """Equality, in both directions — this is the half that catches growth.

        A module added to the allowlist and not to README fails here, which is
        the reason the check is equality rather than containment: README going
        stale as the product grows is the failure mode that produced this gate,
        and a containment check cannot see it.

        Since CB-232 the same call also compares the CLI's list with the
        SERVER's; see `test_the_two_mode_lists_diverge_exactly_where_readme_says`
        for what that adds and why one function decides both.
        """
        problems = mode_set_problems(
            cli_modes=_mode_allowlist(),
            server_modes=_server_mode_names(),
            documented=_readme_modules(),
            cli_only_documented=_readme_cli_only_modules(),
        )
        assert not problems, "\n".join(problems)

    def test_the_two_mode_lists_diverge_exactly_where_readme_says(self) -> None:
        """The divergence is a set difference, so README's account of it is checkable.

        This is the same call as the test above, on purpose: one function
        decides every mode question, so the module table and the CLI-only marks
        can never be judged by two rules that drift apart. The test exists
        separately because it names the property, and because a reader looking
        for "where is the server list compared?" should find it by name.
        """
        cli, server = _mode_allowlist(), _server_mode_names()
        assert cli != server, (
            "the CLI and server mode lists are now identical. That is a legitimate state — "
            "but README's `CLI only` mark and the paragraph explaining it are then describing "
            "a divergence that no longer exists, so remove them together with this assertion."
        )
        problems = mode_set_problems(
            cli_modes=cli,
            server_modes=server,
            documented=_readme_modules(),
            cli_only_documented=_readme_cli_only_modules(),
        )
        assert not problems, "\n".join(problems)

    def test_every_verb_in_a_readme_example_exists(self) -> None:
        verbs = _readme_command_verbs()
        assert verbs, "no `codebugs ...` example found in README — this gate would be vacuous"
        real = set(collect_cli_surface())
        unknown = sorted(verbs - real)
        assert not unknown, (
            f"README shows command examples using verbs the CLI does not have: {unknown}"
        )

    def test_every_tool_named_in_a_tool_table_exists(self) -> None:
        named = _readme_tool_names()
        assert named, "no Tool/Purpose table found in README — this gate would be vacuous"
        real = {tool["name"] for tool in json.loads(MCP_GOLDEN.read_text(encoding="utf-8"))}
        unknown = sorted(named - real)
        assert not unknown, (
            f"README's tool tables name tools the MCP server does not serve: {unknown}"
        )


class TestTheModeComparisonActuallyDiscriminates:
    """Substitute each side in turn and watch the comparison redden (CB-232).

    A comparison that is only ever handed the real, agreeing sets is a gate
    nobody has seen fire. These feed `mode_set_problems` fixtures rather than
    rewriting `cli.py` or `server.py`, which is exactly why that function takes
    four sets instead of reading them itself.
    """

    #: The real shape, with README's account of it correct: `usage` is the one
    #: mode the CLI takes and the server does not, and README marks it.
    AGREES = dict(
        cli_modes={"findings", "reqs", "usage", "all"},
        server_modes={"findings", "reqs", "all"},
        documented={"findings", "reqs", "usage"},
        cli_only_documented={"usage"},
    )

    def test_the_agreeing_fixture_is_clean(self) -> None:
        """Without this, every test below could pass by always finding a problem."""
        assert mode_set_problems(**self.AGREES) == []

    def test_a_mode_the_server_gains_and_the_cli_does_not_is_refused(self) -> None:
        """The direction README has no vocabulary for: MCP-only."""
        problems = mode_set_problems(**{**self.AGREES, "server_modes": {"findings", "reqs", "loc", "all"}})
        assert any("SERVER_NAMES" in p and "loc" in p for p in problems), problems

    def test_a_mode_the_server_loses_makes_readmes_account_wrong(self) -> None:
        """`reqs` stops being a server mode, so README's CLI-only set is short by one."""
        problems = mode_set_problems(**{**self.AGREES, "server_modes": {"findings", "all"}})
        assert any("CLI-only" in p for p in problems), problems

    def test_the_symmetric_case_a_mode_the_cli_gains(self) -> None:
        """From the CLI side: a new CLI-only mode nobody marked in README."""
        problems = mode_set_problems(
            **{**self.AGREES, "cli_modes": {"findings", "reqs", "usage", "audit", "all"}}
        )
        # It is both undocumented in the table AND an unmarked divergence.
        assert any("no row in README's module table" in p for p in problems), problems
        assert any("CLI-only" in p for p in problems), problems

    def test_the_symmetric_case_a_mode_the_cli_loses(self) -> None:
        problems = mode_set_problems(**{**self.AGREES, "cli_modes": {"findings", "usage", "all"}})
        assert any("not `--mode` values" in p for p in problems), problems

    def test_readme_marking_the_wrong_module_cli_only(self) -> None:
        """The mark is on a module that the server does carry."""
        problems = mode_set_problems(**{**self.AGREES, "cli_only_documented": {"reqs"}})
        assert any("CLI-only" in p for p in problems), problems

    def test_readme_marking_nothing_at_all(self) -> None:
        """Dropping the mark — or rewording it past `_CLI_ONLY_MARK` — must be loud."""
        problems = mode_set_problems(**{**self.AGREES, "cli_only_documented": set()})
        assert any("CLI-only" in p for p in problems), problems

    def test_all_is_excluded_from_the_divergence_on_both_sides(self) -> None:
        """`all` is a mode of both surfaces and never a module row; it must not leak in."""
        assert mode_set_problems(
            cli_modes={"findings", "all"},
            server_modes={"findings", "all"},
            documented={"findings"},
            cli_only_documented=set(),
        ) == []
        # …and when only one side carries it, that is still not a divergence to report.
        assert mode_set_problems(
            cli_modes={"findings", "all"},
            server_modes={"findings"},
            documented={"findings"},
            cli_only_documented=set(),
        ) == []


class TestTheReadersSeeWhatTheyClaimTo:
    """The fence and command readers, against the forms that used to be invisible.

    Every case here was GREEN against the predecessor — an indented fence, a
    tab-indented fence, a four-backtick fence, a tilde fence, a leading flag —
    which is what CB-232's acceptance measured. They run against the reader
    functions with a synthetic document rather than against README, so they
    keep discriminating after README itself is corrected.
    """

    @staticmethod
    def _verbs(doc: str) -> set[str]:
        verbs: set[str] = set()
        for line, fenced in _fence_state(doc.splitlines()):
            if not fenced:
                continue
            stripped = line.strip()
            if stripped.startswith("$"):
                stripped = stripped[1:].strip()
            if stripped.startswith("codebugs "):
                verb = _verb_of(stripped)
                if verb is not None:
                    verbs.add(verb)
        return verbs

    def test_a_plain_fence_is_read(self) -> None:
        assert self._verbs("```\ncodebugs summary\n```") == {"summary"}

    def test_a_fence_indented_by_one_space_is_read(self) -> None:
        assert self._verbs(" ```\n codebugs summary\n ```") == {"summary"}

    def test_a_fence_indented_under_a_list_item_is_read(self) -> None:
        assert self._verbs("- item\n\n  ```bash\n  codebugs summary\n  ```") == {"summary"}

    def test_a_tab_indented_fence_is_read(self) -> None:
        assert self._verbs("\t```\n\tcodebugs summary\n\t```") == {"summary"}

    def test_a_four_backtick_fence_is_read(self) -> None:
        assert self._verbs("````\ncodebugs summary\n````") == {"summary"}

    def test_a_tilde_fence_is_read(self) -> None:
        assert self._verbs("~~~\ncodebugs summary\n~~~") == {"summary"}

    def test_a_short_fence_inside_a_long_one_does_not_close_it(self) -> None:
        """CommonMark's rule, and the reason the opening length is carried.

        Read wrongly, the inner ``` ends the block, the state inverts, and
        every later example in the file is silently skipped — a whole-document
        blinding from one line.
        """
        doc = "````\ncodebugs summary\n```\ncodebugs query\n````\n\n```\ncodebugs stats\n```"
        assert self._verbs(doc) == {"summary", "query", "stats"}

    def test_a_tilde_fence_is_not_closed_by_backticks(self) -> None:
        doc = "~~~\ncodebugs summary\n```\ncodebugs query\n~~~"
        assert self._verbs(doc) == {"summary", "query"}

    def test_an_info_string_does_not_close_the_block(self) -> None:
        """```` ```bash ```` opens; a closing fence may carry no info string."""
        assert self._verbs("```bash\ncodebugs summary\n```") == {"summary"}

    def test_a_prompt_with_and_without_a_space_is_stripped(self) -> None:
        assert self._verbs("```\n$ codebugs summary\n$codebugs query\n```") == {"summary", "query"}

    def test_prose_outside_any_fence_is_still_invisible(self) -> None:
        """The scope claim from the docstring, held as a test rather than a promise."""
        assert self._verbs("Run codebugs nosuchverb to do it.\n") == set()

    def test_a_known_global_option_is_skipped_and_the_verb_found(self) -> None:
        assert _verb_of("codebugs --mode findings summary") == "summary"
        assert _verb_of("codebugs --tracker-root /p query") == "query"
        assert _verb_of("codebugs --version") is None

    def test_a_long_option_spelled_with_equals_is_the_same_option(self) -> None:
        assert _verb_of("codebugs --mode=findings summary") == "summary"
        assert _verb_of("codebugs --tracker-root=/p query") == "query"

    def test_an_unknown_leading_flag_refuses_rather_than_skipping(self) -> None:
        """The form that used to make a whole example vanish."""
        with pytest.raises(pytest.fail.Exception, match="does not know as a global option"):
            _verb_of("codebugs --no-such-flag nosuchverb")

    def test_a_flag_after_the_verb_is_not_the_readers_business(self) -> None:
        assert _verb_of("codebugs update CB-1 --status fixed") == "update"

    def test_a_trailing_comment_is_dropped(self) -> None:
        assert _verb_of("codebugs query   # this invocation only") == "query"


class TestTheTableReaderKeysOnColumnNames:
    def test_spacing_in_the_header_does_not_hide_a_table(self) -> None:
        assert _is_table_header("| Tool | Purpose |", "tool", "purpose")
        assert _is_table_header("|Tool|Purpose|", "tool", "purpose")
        assert _is_table_header("| tool | purpose | notes |", "tool", "purpose")
        assert _is_table_header("  | Module | Domain | Headline tools |", "module", "domain")

    def test_a_different_first_column_is_not_that_table(self) -> None:
        assert not _is_table_header("| Field | Type | Description |", "tool", "purpose")
        assert not _is_table_header("| Table | Purpose |", "tool", "purpose")
        assert not _is_table_header("not a table at all", "tool", "purpose")

    def test_readme_still_has_both_tables_this_gate_needs(self) -> None:
        """Non-vacuity for the two readers above, against the real file."""
        assert _readme_modules(), "the module table reader found nothing in README"
        assert _readme_tool_names(), "the tool table reader found nothing in README"
        assert _readme_cli_only_modules(), (
            "no module row in README carries a `CLI only` mark, but the two mode lists differ"
        )
