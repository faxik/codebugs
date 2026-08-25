"""The version a USER sees must be the version this repository claims.

Three channels carry a version number here and each has a different audience, so
they can drift apart in silence:

* ``pyproject.toml`` — what the built wheel's metadata carries, i.e. what
  ``pip show codebugs`` and ``pipx list`` report;
* ``CHANGELOG.md`` — what a person reads;
* ``codebugs.__version__`` — what a Python caller reads. Nothing inside the
  package consults it, which is exactly why a drift there stays invisible until
  somebody prints it.

Before the 0.2.0 cut nothing compared them at all, and all three had to be moved
by hand. This file is the comparison.

**The COMPOSITION test is the one that matters, and it is deliberately not a
file-to-file comparison.** ``importlib.metadata.version("codebugs")`` reads the
INSTALLED distribution's metadata, which is a genuinely different channel from
the source tree: ``pythonpath = ["src"]`` means ``import codebugs`` never
consults it. That is the seam where *the file is edited* and *the user sees the
new number* come apart, and reading files alone cannot see it.

**Deliberately NOT asserted: that ``## [Unreleased]`` is EMPTY.** It is empty at
the moment of a cut and stops being empty with the next landed entry — three
directions write to this file continuously — so an emptiness assertion would
refuse ordinary work within days: a gate that fires on correct behaviour. What a
release actually needs is that the section EXISTS ABOVE the newest released one,
so a later entry can never land inside a version that already shipped. That is
what is asserted, and it holds permanently.
"""

import importlib.metadata
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import codebugs
from codebugs import db

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

_UNRELEASED_HEADING = re.compile(r"^## \[Unreleased\][ \t]*$")

# The heading is located WITHOUT requiring its date, so that a missing date fails
# the date test rather than collapsing into "there is no released section at all".
_VERSION_HEADING = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\](?P<rest>.*)$")

# `## [0.2.0] — 2026-08-25` and `## [0.1.0] - 2025-05-01` are both real spellings
# in this file: an em dash on the newer section, an ASCII hyphen on the older one.
# The pattern accepts either rather than pinning a separator nobody agreed on.
_DATE_SUFFIX = re.compile(r"^[ \t]*[-–—][ \t]*(?P<date>\d{4}-\d{2}-\d{2})[ \t]*$")


def _changelog_lines() -> list[str]:
    return CHANGELOG.read_text(encoding="utf-8").splitlines()


def _pyproject_version() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def _newest_released_heading() -> tuple[int, str, str]:
    """Line index, version and trailing text of the FIRST `## [X.Y.Z]` heading.

    First in file order, which is newest-first by this file's convention.
    """
    for index, line in enumerate(_changelog_lines()):
        match = _VERSION_HEADING.match(line)
        if match:
            return index, match.group("version"), match.group("rest")
    raise AssertionError("CHANGELOG.md carries no released `## [X.Y.Z]` section at all")


def _unreleased_index() -> int:
    for index, line in enumerate(_changelog_lines()):
        if _UNRELEASED_HEADING.match(line):
            return index
    raise AssertionError(
        "CHANGELOG.md carries no `## [Unreleased]` heading. Without one the next "
        "entry lands inside a version that has already shipped."
    )


def _installed_version() -> str:
    try:
        return importlib.metadata.version("codebugs")
    except importlib.metadata.PackageNotFoundError as exc:  # pragma: no cover - env
        raise AssertionError(
            "codebugs is not installed as a distribution in this environment, so it "
            "has no outward-facing version to compare against the source tree. Run "
            "the suite the documented way: "
            "`uv run --extra dev python -m pytest tests/ -q`."
        ) from exc


class TestReleaseVersionAgreement:
    """Element-wise: each source names the same number, in the right shape."""

    def test_pyproject_and_changelog_name_the_same_version(self):
        _, changelog_version, _ = _newest_released_heading()
        assert _pyproject_version() == changelog_version, (
            f"pyproject.toml says {_pyproject_version()!r} while the newest released "
            f"CHANGELOG.md section says {changelog_version!r}. These are the two "
            "numbers that diverge silently and then lie to everyone."
        )

    def test_an_unreleased_section_sits_above_the_newest_released_one(self):
        unreleased = _unreleased_index()
        released, version, _ = _newest_released_heading()
        assert unreleased < released, (
            f"`## [Unreleased]` is at line {unreleased + 1} and the newest released "
            f"section [{version}] at line {released + 1}. Below it, every entry a "
            "later change adds would be filed inside a version that already shipped."
        )

    def test_the_newest_released_section_carries_a_date(self):
        index, version, rest = _newest_released_heading()
        assert _DATE_SUFFIX.match(rest), (
            f"the `## [{version}]` heading at line {index + 1} carries {rest!r} where a "
            "`- YYYY-MM-DD` (or em-dash) date belongs. A release without a date cannot "
            "be placed against anything else that happened."
        )

    def test_the_package_constant_matches_the_project_metadata(self):
        assert codebugs.__version__ == _pyproject_version(), (
            f"codebugs.__version__ is {codebugs.__version__!r} while pyproject.toml "
            f"says {_pyproject_version()!r}. Nothing in the package reads that "
            "constant, so this drift is invisible until somebody prints it."
        )


class TestVersionCompositionAtTheOutwardBoundary:
    """The composition: what a USER is told, not what the files say to each other.

    Element checks compare three files. This one compares a file against the
    INSTALLED distribution, which is the only channel a user actually reads.
    """

    def test_the_version_a_user_sees_is_the_version_this_tree_claims(self):
        installed = _installed_version()
        declared = _pyproject_version()
        _, changelog_version, _ = _newest_released_heading()
        assert installed == declared == changelog_version, (
            f"the installed codebugs distribution reports {installed!r}, pyproject.toml "
            f"declares {declared!r} and the newest released CHANGELOG.md section says "
            f"{changelog_version!r}. If the first differs from the others the files were "
            "edited and the environment was never rebuilt, so every `pip show` and every "
            "wheel built from here still carries the old number."
        )


def _run_cli(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    """Spawn the real `python -m codebugs.cli` a user would type.

    `PYTHONPATH` is set explicitly because a subprocess does not inherit
    pytest's own `sys.path` (which gets `src` only from this repo's
    `pythonpath = ["src"]` pytest-ini setting) — without it the child cannot
    `import codebugs` at all. `CODEBUGS_ROOT` is stripped from the inherited
    environment so an ambient tracker-root override left in the shell cannot
    steer discovery underneath the test.
    """
    src_dir = str(Path(codebugs.__file__).resolve().parent.parent)
    env = dict(os.environ)
    env.pop(db.ENV_ROOT, None)
    env["PYTHONPATH"] = src_dir
    return subprocess.run(
        [sys.executable, "-m", "codebugs.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestVersionFlag:
    """`codebugs --version` prints what a user reads, not a hardcoded literal.

    CB-191: the flag did not exist at all. These tests spawn the real CLI
    subprocess and read its stdout — never `build_parser()` directly — because
    the point is what a USER typing the command sees, matching this file's own
    "what a user reads" framing for the rest of the version channels.
    """

    def test_version_prints_the_package_version_and_exits_zero(self, tmp_path):
        result = _run_cli(["--version"], cwd=tmp_path)
        assert result.returncode == 0, (
            f"`codebugs --version` exited {result.returncode}, stderr={result.stderr!r}"
        )
        assert codebugs.__version__ in result.stdout, (
            f"stdout was {result.stdout!r}, which does not contain "
            f"codebugs.__version__ ({codebugs.__version__!r}). The flag must report "
            "the package's actual version, never a literal that goes stale at the "
            "next release."
        )

    def test_version_works_with_no_reachable_tracker(self, tmp_path):
        """The flag must not require `db.connect()` to succeed.

        `tmp_path` is a fresh directory under the OS temp root with no
        `.codebugs/` anywhere above it and no enclosing `.git/` either, so
        `db.connect()`'s upward walk would raise `DatabaseNotFoundError` if
        anything on the `--version` path touched it.
        """
        result = _run_cli(["--version"], cwd=tmp_path)
        assert result.returncode == 0, (
            f"`codebugs --version` from a directory with no tracker exited "
            f"{result.returncode} instead of 0. stderr={result.stderr!r}"
        )
        assert codebugs.__version__ in result.stdout

    def test_version_is_independent_of_mode(self, tmp_path):
        """`--mode` selects which domains' CLI verbs get wired in, and must not
        affect a flag declared on the top-level parser itself."""
        no_flag = _run_cli(["--version"], cwd=tmp_path)
        reqs_mode = _run_cli(["--mode", "reqs", "--version"], cwd=tmp_path)
        findings_mode = _run_cli(["--mode", "findings", "--version"], cwd=tmp_path)

        for label, result in (
            ("no --mode", no_flag),
            ("--mode reqs", reqs_mode),
            ("--mode findings", findings_mode),
        ):
            assert result.returncode == 0, (
                f"{label}: `--version` exited {result.returncode}, "
                f"stderr={result.stderr!r}"
            )
            assert codebugs.__version__ in result.stdout, (
                f"{label}: stdout was {result.stdout!r}, missing "
                f"codebugs.__version__ ({codebugs.__version__!r})"
            )

        assert no_flag.stdout == reqs_mode.stdout == findings_mode.stdout, (
            "the version output differed by --mode: "
            f"no-flag={no_flag.stdout!r} reqs={reqs_mode.stdout!r} "
            f"findings={findings_mode.stdout!r}"
        )
