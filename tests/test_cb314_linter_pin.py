"""CB-314: the linter version is decided in ONE place, and the "newest dependencies"
job names every library `src/` imports by name.

WHY THIS FILE EXISTS AT ALL. CB-314's whole subject is a state that must be
unrepresentable: the linter CI runs and the linter the merge guard runs being two
different versions. The change that made it unrepresentable is four lines of
configuration spread over two files, and configuration is exactly what gets
"tidied" by someone who does not know what it holds up. A change whose subject is
gates that cannot fire, shipped without a gate of its own, would be this
repository's own recurring defect committed inside its own fix.

WHAT IS NOT TESTED HERE, AND WHY EACH ONE IS ABSENT ON PURPOSE.

* That `uv.lock` resolved ruff to the version `pyproject.toml` pins. Under ordinary
  uv behaviour this is a test that cannot fail: the suite runs through `uv run`,
  which re-locks before pytest starts, so by the time any assertion executes the two
  agree by construction. *Not "cannot fail" in the absolute* — a hand-resolved
  lockfile can record the constraint while holding an excluded version, and uv
  accepts it — but a green light that means nothing on every ordinary path is worse
  than no light.
* That ruff at that version passes on this tree. `ruff check` is the lint gate and
  runs as its own step; re-running it from inside pytest would double a four-second
  cost to restate a result the gate already owns.
* That the workflow, or the merge guard, ACTUALLY EXECUTES. Every assertion here
  reads committed text. A `run:` line that is syntactically the sanctioned command
  and semantically dead — the file renamed, the job removed from the trigger, the
  runner label wrong — passes. This is the standing limit of every static workflow
  test in this suite, and it is the reason the assertions below insist on the whole
  `run:` value rather than a substring: within the text, at least, there is nothing
  left to hide behind.
* Dynamic imports. `_third_party_modules_imported_by_src` walks the AST, which sees
  every STATIC import shape — inside functions, conditionals, `TYPE_CHECKING`
  blocks, `try/except ImportError`. It does NOT see `importlib.import_module`,
  `__import__` bound to a name, or anything else resolved at run time, so a
  dependency introduced that way never enters the list. It also over-counts in the
  other direction: an import used only for typing still demands its name in the
  workflow. Both are stated rather than chased; the sibling ratchet in
  `tests/test_no_network_capability.py` has the same boundary for the same reason.

WHY THESE ASSERTIONS READ THE FILES AS TEXT rather than through a YAML parser: the
project declares no YAML dependency, and adding one to test a workflow would be a
new runtime requirement bought for one assertion. Comments are stripped before
matching, which is not a formality here — the comments in `ci.yml` and
`pyproject.toml` deliberately name `uvx` while explaining why it was removed, and a
test that grepped the raw bytes would read that explanation as the thing it forbids.
**Both whole-line AND trailing comments are stripped, and the second half is
load-bearing:** with whole-line stripping alone,
`uv lock -P mcp -P mcp-types  # -P pydantic` reads as naming three packages while
the shell runs two, and `: # uv run --extra dev ruff check src/ tests/` reads as the
sanctioned lint command while the shell does nothing at all. Both were live holes
here until a cross-model review constructed them. The cut is at the first ` #`, so
a `#` that is part of a command rather than a comment would be lost — no command in
this workflow contains one, and a future one must not.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
SRC = REPO_ROOT / "src"

# Module name as it is written in an `import` statement -> distribution name as an
# installer and `uv lock --upgrade-package` know it. Deliberately a small explicit
# table and NOT `importlib.metadata.packages_distributions()`: that function answers
# from the environment the tests happen to run in, so the assertion would change
# meaning with the environment, and a structural claim about the SOURCE must not.
_MODULE_TO_DISTRIBUTION = {
    "mcp": "mcp",
    "mcp_types": "mcp-types",
    "pydantic": "pydantic",
}


LINT_COMMAND = "uv run --extra dev ruff check src/ tests/"
FINISH_GUARD = REPO_ROOT / "tools" / "worktree-finish.sh"

# Flags that would leave every assertion below green while changing which version
# actually runs. `--with` layers a second requirement over the project's own;
# `--frozen` / `--no-sync` run whatever the environment already holds; a version
# written onto the invocation bypasses the pin outright.
_PIN_BYPASSING_FLAGS = ("--with", "--frozen", "--no-sync", "ruff==", "ruff@")

# Flags and settings that keep all three names in the command while deliberately
# resolving something other than the newest admissible release, which is the one
# thing the `newest-sdk` job exists to run against.
_RESOLUTION_NARROWING = ("--resolution", "--exclude-newer", "--frozen", "--no-sync")


def _strip_comments(text: str) -> str:
    """Drop whole comment lines AND trailing comments. See the module docstring.

    Whole-line stripping alone lets a trailing comment smuggle text into a command
    that the shell never runs, in both directions: adding a name the command omits,
    and hiding a command behind a no-op. Cutting at the first ` #` closes both.
    """
    kept: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        head, sep, _ = line.partition(" #")
        kept.append(head if sep else line)
    return "\n".join(kept)


def _run_values(stripped: str) -> list[str]:
    """Every `run:` value in the workflow, as the shell would receive its first line.

    A block scalar (`run: |`) yields the empty string here and its body is matched
    separately; the assertions that use this helper are about single-line commands.
    """
    return [m.group(1).strip() for m in re.finditer(r"^\s*run:[ \t]*(.*)$", stripped, re.M)]


def _third_party_modules_imported_by_src() -> set[str]:
    """Every top-level module `src/` imports that is neither stdlib nor this package.

    Reads the SOURCE, not the environment: nothing has to be installed for this to
    answer, and the answer cannot drift with someone's virtualenv.
    """
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                # `level > 0` is a relative import: within this package by definition.
                if node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
    return {m for m in found if m not in sys.stdlib_module_names and m != "codebugs"}


def _upgrade_package_names(stripped_workflow: str) -> tuple[set[str], str]:
    """The distributions the `uv lock` step names, and the line that names them."""
    lines = [ln for ln in stripped_workflow.splitlines() if "uv lock" in ln]
    assert len(lines) == 1, (
        f"expected exactly one `uv lock` command in ci.yml, found {len(lines)}: {lines}. "
        "This helper reads the names off that single line; a second command means the "
        "assertions below would silently be judging the wrong one. If a second `uv lock` "
        "is legitimate (`--check`, say), teach this helper which one is the upgrade step "
        "rather than relaxing the count — the cost of this shape is stated in the module "
        "docstring and it is a formatting invariant, deliberately, because the cheap "
        "alternative is a fourth copy of the job-slicing helper this suite already has "
        "three of."
    )
    # `-P` is uv's documented alias; accepting only the long form would fail a
    # legitimate edit while catching nothing.
    return set(re.findall(r"(?:--upgrade-package|-P)[ \t]+(\S+)", lines[0])), lines[0]


class TestTheLinterVersionIsDecidedInOnePlace:
    """The pin exists, is exact, and nothing executable names a version beside it."""

    def test_the_dev_extra_pins_ruff_to_an_exact_patch_version(self):
        """A bare `ruff`, or a range, reopens CB-314.

        EXACT PATCH rather than `~=0.15` for the reason `.python-version` carries a
        full patch: a MAJOR.MINOR range leaves a divergent state representable, and
        making it unrepresentable is the entire point of the pin.
        """
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        dev = data["project"]["optional-dependencies"]["dev"]
        pins = [spec for spec in dev if re.fullmatch(r"ruff==\d+\.\d+\.\d+", spec)]
        assert pins, (
            "pyproject.toml's `dev` extra must pin ruff to an exact patch version "
            f"(`ruff==X.Y.Z`); it declares {dev!r}. Without the pin, one ordinary "
            "`uv lock --upgrade` moves the linter the merge guard runs while CI, "
            "which reads the same pin, does not follow — CB-314."
        )

    def test_no_executable_line_of_the_workflow_names_a_ruff_version(self):
        """The number lives in `pyproject.toml`; a second copy is what drifted."""
        code = _strip_comments(WORKFLOW.read_text(encoding="utf-8"))
        offenders = [ln for ln in code.splitlines() if re.search(r"ruff\s*[@=]=?\s*\d", ln)]
        assert not offenders, (
            "an executable line of ci.yml names a ruff version: "
            f"{offenders}. The version is decided in pyproject.toml and nowhere else."
        )

    def test_the_workflow_runs_ruff_from_the_project_environment(self):
        """`uvx <tool>@<version>` downloads its own copy and ignores the pin.

        This is the half a pin alone cannot cover, and the half that makes the pin
        mean anything in CI: `uvx` reads no lockfile and no `pyproject.toml`, so
        with it in place the two sides stay independent however tightly the project
        is pinned.
        """
        code = _strip_comments(WORKFLOW.read_text(encoding="utf-8"))
        assert LINT_COMMAND in _run_values(code), (
            "ci.yml must lint through the project environment with EXACTLY "
            f"`{LINT_COMMAND}` as the whole `run:` value, so that it runs the version "
            "pyproject.toml pins — the same one tools/worktree-finish.sh runs. Found: "
            f"{[v for v in _run_values(code) if 'ruff' in v]}. Matching a SUBSTRING "
            "instead would accept `uv run --extra dev --with ruff==<other> ruff check …`, "
            "which layers a second requirement over the project's own and reopens the "
            "split this test exists to close."
        )
        assert "uvx" not in code, (
            "an executable line of ci.yml calls `uvx`, which downloads its own copy "
            "of a tool and reads neither uv.lock nor pyproject.toml. That is exactly "
            "how the linter version came to live in two independent places (CB-314)."
        )

    def test_the_merge_guard_runs_the_same_pinned_linter(self):
        """The OTHER side of "cannot be driven apart", which nothing else checks.

        `tests/test_worktree_harness.py` asserts that the finish script CALLS ruff,
        and accepts any uv command line containing a standalone `ruff`. So
        `uv run --extra dev --with ruff==<other> ruff check …` on that line leaves
        the whole suite green while CI and the merge guard run different linters —
        exactly the state CB-314 was filed about, rebuilt on the guard's side. That
        gap was found by a cross-model review of this very file, not by the mutants.
        """
        lines = [
            ln.strip()
            for ln in _strip_comments(FINISH_GUARD.read_text(encoding="utf-8")).splitlines()
            if "ruff check" in ln
        ]
        invocations = [ln for ln in lines if "uv run" in ln]
        assert invocations, (
            "tools/worktree-finish.sh no longer invokes ruff through `uv run`; the merge "
            "guard and CI would then be reading the pin from different places, if at all."
        )
        for line in invocations:
            assert LINT_COMMAND in line, (
                f"the merge guard's linter command is not `{LINT_COMMAND}`: {line}"
            )
            for flag in _PIN_BYPASSING_FLAGS:
                assert flag not in line, (
                    f"the merge guard's linter command carries `{flag}`, which lets it run a "
                    f"different ruff from the one `pyproject.toml` pins: {line}"
                )

    def test_no_step_of_the_workflow_is_disabled_or_made_advisory(self):
        """A gate that reports success without running is this repo's oldest defect.

        `continue-on-error: true` turns a red step into a pass; `if:` can make a job
        or step skip entirely — and a skipped job is reported as PASSING to branch
        protection, which is why `main-invariants.yml` deliberately does not subscribe
        to `pull_request`. Either one, applied to `newest-sdk`, would keep every other
        assertion in this file green while making failures on the versions users
        actually install unable to refuse anything.

        THE SCOPE IS THE WHOLE FILE, not just `newest-sdk`, and that is the cheap
        choice rather than the precise one: slicing one job's body out by indentation
        would be this suite's FOURTH copy of that helper. The cost is real and named —
        a future legitimate `if:` anywhere in `ci.yml` fails here and must be argued
        for in this test rather than added silently.
        """
        code = _strip_comments(WORKFLOW.read_text(encoding="utf-8"))
        offenders = [
            ln.strip()
            for ln in code.splitlines()
            if re.match(r"^\s*(continue-on-error|if)\s*:", ln)
        ]
        assert not offenders, (
            f"ci.yml carries {offenders}. A step with `continue-on-error: true` cannot "
            "refuse anything, and a job skipped by an `if:` is reported as PASSING for "
            "required-status-check purposes. If one of these is genuinely wanted, say so "
            "here first."
        )


class TestTheNewestDependenciesJobNamesEveryDirectImport:
    """The `newest-sdk` list is not a hand-kept enumeration that quietly goes stale.

    A list of strings is the letter; the intent is one sentence — *every library the
    product itself imports by name is exercised at the version an installer would
    resolve*. So the test derives the population from the source and compares, and a
    NEW direct import fails CLOSED with an instruction rather than passing silently.
    """

    def test_the_job_names_exactly_the_distributions_src_imports(self):
        modules = _third_party_modules_imported_by_src()
        unknown = modules - set(_MODULE_TO_DISTRIBUTION)
        assert not unknown, (
            f"src/ now imports {sorted(unknown)}, which this test cannot map to a "
            "distribution name. That is deliberate: a new direct import is a new "
            "version an installer resolves freely and nothing tests. Add it to "
            "_MODULE_TO_DISTRIBUTION and to the `uv lock --upgrade-package` list in "
            "ci.yml's `newest-sdk` job, or say in this test why it does not belong."
        )
        expected = {_MODULE_TO_DISTRIBUTION[m] for m in modules}
        named, line = _upgrade_package_names(_strip_comments(WORKFLOW.read_text("utf-8")))
        assert named == expected, (
            f"ci.yml's `newest-sdk` job upgrades {sorted(named)}, but src/ imports "
            f"{sorted(expected)} by name. Every one of those arrives in an installed "
            f"copy at whatever version the installer resolved — pipx and pip do not "
            f"read uv.lock — so any name missing here is a version users run and "
            f"nothing tests (CB-314). The command was: {line.strip()}"
        )

    def test_every_row_of_the_module_table_carries_a_usable_distribution_name(self):
        """The table's VALUES are read, not just its keys.

        A row whose value is blank or is not a string would not raise: it would
        flow into `expected` below as an empty name, and the comparison against
        `ci.yml` would fail with a message about a missing library rather than
        about a broken row — a true red for a false reason, which is worse than
        either a pass or an honest failure.
        """
        for module, distribution in _MODULE_TO_DISTRIBUTION.items():
            assert isinstance(distribution, str) and distribution.strip(), (
                f"_MODULE_TO_DISTRIBUTION maps {module!r} to {distribution!r}, which is "
                "not a usable distribution name. Every row must name the distribution "
                "an installer and `uv lock --upgrade-package` know."
            )

    def test_no_row_of_the_module_table_survives_the_import_that_justified_it(self):
        """The table cannot only grow — the direction the sibling test misses.

        `test_the_job_names_exactly_the_distributions_src_imports` fails closed on a
        module the table does not know. That is one direction. This is the other: a
        module the table still knows but `src/` no longer imports. Without it, a
        dependency dropped from the product keeps its row here and its name in
        `ci.yml`'s `--upgrade-package` list forever, and the job goes on resolving a
        library nothing uses while the list quietly stops describing the product.
        """
        live = _third_party_modules_imported_by_src()
        stale = [module for module in _MODULE_TO_DISTRIBUTION if module not in live]
        assert not stale, (
            f"_MODULE_TO_DISTRIBUTION still carries {sorted(stale)}, which nothing under "
            "src/ imports any more. Drop the row, and drop the matching name from the "
            "`uv lock --upgrade-package` list in ci.yml's `newest-sdk` job."
        )

    def test_the_job_resolves_versions_rather_than_naming_them(self):
        """A written-in version goes stale the day the library releases again.

        Then the job tests a version nobody runs, which is the defect it exists to
        close, rebuilt inside its own fix. `tests/test_cb310_refusal_text.py` asserts
        the same property for `mcp` alone; this one covers the whole list, and the
        two are kept separate because that file belongs to CB-310's subject.
        """
        _, line = _upgrade_package_names(_strip_comments(WORKFLOW.read_text("utf-8")))
        assert "==" not in line, (
            "the `uv lock --upgrade-package` command names a version constant: "
            f"{line.strip()}. It must re-resolve against the declared ranges instead."
        )
        # Refusing a written-in version is NOT the same as resolving the NEWEST one:
        # `--resolution lowest` and `--exclude-newer` keep all three names and pass
        # the assertion above while deliberately picking something older. Found by a
        # cross-model review; the mutants did not reach it.
        for flag in _RESOLUTION_NARROWING:
            assert flag not in line, (
                f"the `uv lock` command carries `{flag}`, which keeps the names but stops "
                f"the job resolving the NEWEST admissible release — the only thing it runs "
                f"for: {line.strip()}"
            )

    def test_nothing_in_the_project_file_narrows_resolution_behind_the_job(self):
        """The same attack as above, moved out of the command and into settings.

        `[tool.uv] resolution = "lowest"` or an `exclude-newer` cutoff makes the
        workflow command resolve something other than the newest release while every
        assertion about that command stays green. There is no `[tool.uv]` table here
        today, so this FAILS CLOSED on the appearance of one rather than trying to
        enumerate the settings that would matter — a list of settings is the letter,
        and the intent is one sentence: nothing may quietly narrow what `newest-sdk`
        resolves. A legitimate `[tool.uv]` therefore has to be argued for here.
        """
        text = PYPROJECT.read_text(encoding="utf-8")
        tables = re.findall(r"^\[tool\.uv[^\]]*\]", text, re.M)
        assert not tables, (
            f"pyproject.toml now declares {tables}. That table can narrow what the "
            "`newest-sdk` job resolves (`resolution = \"lowest\"`, `exclude-newer`) or "
            "replace a dependency outright (`override-dependencies`) without changing a "
            "single line this suite reads. Check what it actually sets, then relax this "
            "assertion to the settings you have decided are safe."
        )
