"""README names the same SETS the product does — three set comparisons, nothing more.

WHAT THIS GATE CHECKS, exactly and only:

1. The module set named by README's module table equals the CLI's `--mode`
   allowlist, minus `all`.
2. Every CLI verb appearing in a README command example exists in the CLI.
3. Every MCP tool name appearing in a README `| Tool | Purpose |` table exists
   in the wire golden.

**WHAT THIS GATE DOES NOT CHECK, and must never be read as checking.** It does
NOT verify that README is accurate. It reads three sets of names and compares
them with three sets of names. It is blind to:

- **prose** — every sentence in the file, including every sentence describing
  what a verb or a tool actually does;
- **numbers** — counts of modules, of tools, of anything. README deliberately
  carries none any more (four of the ten it used to carry were wrong), and if
  one is reintroduced this gate will not notice;
- **flags and their behaviour** — `--append-note`, `--older-than 30d`,
  `--new-category`, defaults, whether a declared flag exists at all, and
  whether a flag that exists does what the surrounding sentence says;
- **the accuracy of any example's OUTPUT** — the `categories` block, the
  `reqs-verify` block, the `milestone-status` block and the close-gate refusal
  are all reproduced from real runs, and nothing here would notice if the
  command's real output changed tomorrow;
- **whether a command actually SUCCEEDS** — a verb can exist and still refuse
  every invocation README shows;
- **MCP tool names mentioned in PROSE or in the module table's "Headline
  tools" column** — only the `| Tool | Purpose |` tables are read;
- **new CLI verbs and new MCP tools README never mentions.** README is not
  obliged to document every verb, so growth in that direction is silent here.
  Only the MODULE set is held to equality; the other two are containments.

That list is the point of this docstring. A previous gate in this repository
was justified as "an enumeration does not converge, so check the capability"
and then turned out to be an enumeration of spellings itself, catching one
evasion in thirteen (CB-227). This gate makes the smaller promise on purpose:
three name sets, checked as name sets.

WHICH ALLOWLIST, AND WHY. Two mode lists exist and they disagree: the CLI's
`--mode` choices carry 15 entries and `server.SERVER_NAMES` carries 14, because
`usage` registers a CLI provider and no tool provider. This gate compares
against the **CLI's** list, the wider of the two, because README's table
documents a flag both surfaces take and because gating on the narrower list
would let `usage` be dropped from README without a word. README marks it
CLI-only in the table itself.
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
MCP_GOLDEN = REPO_ROOT / "tests" / "golden" / "mcp_schema.json"

#: Global options that sit between `codebugs` and the verb. A README example
#: may carry them, and the token after them is still the verb.
_GLOBAL_OPTS_WITH_VALUE = {"--mode", "--tracker-root"}
_GLOBAL_FLAGS = {"--version", "-h", "--help"}


def _readme_lines() -> list[str]:
    return README.read_text(encoding="utf-8").splitlines()


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


def _readme_modules() -> set[str]:
    """Module names from README's module table — the `| **name** | ... |` rows.

    The table is recognised by its header. A header that is not found fails the
    gate: a renamed table must be taught to this reader, never silently skipped.
    """
    modules: set[str] = set()
    inside = False
    saw_table = False
    for line in _readme_lines():
        if line.startswith("| Module | Domain |"):
            inside, saw_table = True, True
            continue
        if not inside:
            continue
        if not line.startswith("|"):
            inside = False
            continue
        if set(line) <= set("|-: "):  # the header separator row
            continue
        match = re.match(r"\|\s*\*\*([a-z_]+)\*\*\s*\|", line)
        if match is None:
            pytest.fail(
                f"unparsed row in README's module table: {line!r}. Every row must name "
                f"its module as `| **name** |`, or this gate stops seeing that module."
            )
        modules.add(match.group(1))
    if not saw_table:
        pytest.fail(
            "README no longer has a table whose header row starts `| Module | Domain |`. "
            "That table is what this gate compares against the --mode allowlist."
        )
    return modules


def _readme_command_verbs() -> set[str]:
    """CLI verbs from `codebugs ...` lines inside README's fenced code blocks."""
    verbs: set[str] = set()
    fenced = False
    for line in _readme_lines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            continue
        stripped = line.strip()
        if stripped.startswith("$ "):
            stripped = stripped[2:].strip()
        if not stripped.startswith("codebugs "):
            continue
        tokens = stripped.split("#", 1)[0].split()[1:]  # drop `codebugs` and any comment
        while tokens:
            token = tokens[0]
            if token in _GLOBAL_OPTS_WITH_VALUE:
                tokens = tokens[2:]
                continue
            if token in _GLOBAL_FLAGS:
                tokens = tokens[1:]
                continue
            break
        if tokens and not tokens[0].startswith("-"):
            verbs.add(tokens[0])
    return verbs


def _readme_tool_names() -> set[str]:
    """Tool names from the first cell of every `| Tool | Purpose |` table row.

    All backticked tokens in that cell are taken, because a row legitimately
    names a pair (``` `codesweep_archive` / `codesweep_archive_items` ```).
    """
    names: set[str] = set()
    inside = False
    for line in _readme_lines():
        if line.strip() == "| Tool | Purpose |":
            inside = True
            continue
        if not inside:
            continue
        if not line.startswith("|"):
            inside = False
            continue
        if set(line) <= set("|-: "):
            continue
        first_cell = line.split("|")[1]
        names.update(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", first_cell))
    return names


class TestReadmeNamesTheSameSets:
    def test_the_module_table_is_the_mode_allowlist(self) -> None:
        """Equality, in both directions — this is the half that catches growth.

        A module added to the allowlist and not to README fails here, which is
        the reason the check is equality rather than containment: README going
        stale as the product grows is the failure mode that produced this gate,
        and a containment check cannot see it.
        """
        allowlist = _mode_allowlist() - {"all"}
        documented = _readme_modules()
        missing_from_readme = sorted(allowlist - documented)
        not_a_mode = sorted(documented - allowlist)
        assert not missing_from_readme, (
            f"these modules are valid `--mode` values but have no row in README's module "
            f"table: {missing_from_readme}"
        )
        assert not not_a_mode, (
            f"README's module table names these, but they are not `--mode` values: {not_a_mode}"
        )

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
        assert named, "no `| Tool | Purpose |` table found in README — this gate would be vacuous"
        real = {tool["name"] for tool in json.loads(MCP_GOLDEN.read_text(encoding="utf-8"))}
        unknown = sorted(named - real)
        assert not unknown, (
            f"README's tool tables name tools the MCP server does not serve: {unknown}"
        )
