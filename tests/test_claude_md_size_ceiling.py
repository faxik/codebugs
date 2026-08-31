"""A MECHANICAL ceiling on every injected doctrine file (T-131, §3 п. 5).

WHY THIS EXISTS AT ALL. The very first line of this repository's own `CLAUDE.md`
says that prose cannot enforce prose. The benchmark project we copied the nested
form from states a forty-line limit in its subsystem template and then breaks it
in **48 of its 76 tracked nested files (63%), the largest at 382 lines** —
measured 2026-08-31, not quoted. A size rule that lives only in prose regrows.

WHY BYTES AND NOT LINES. The cost this ceiling exists to bound is the context
injected into every session, which is paid per byte (per token, of which bytes
are the honest proxy). A line count is not that quantity and is evaded by
reflowing: one 4,000-byte line is one line. So the benchmark's own unit is
deliberately not reused.

THE CEILING IS TWO-SIDED, AND THE SECOND SIDE IS THE POINT.
  (1) actual <= declared            — the file has not outgrown its ceiling.
  (2) declared - actual <= SLACK    — the ceiling is not a hollow number.
Without (2) a ceiling set generously once is a gate that cannot fire, which is
this repository's most-repeated defect. With (2), a declared ceiling must be
re-derived from the size actually ACHIEVED, which is what makes the number mean
something. The stated cost, accepted rather than hidden: a deliberate removal of
more than SLACK bytes turns this test red and requires editing the constant
below. That is intended — a reviewable edit, on the model of the pinned baseline
SHA in `.github/workflows/main-invariants.yml`.

SLACK IS DERIVED FROM MEASUREMENT, NOT CHOSEN ROUND. Over the 142 substantive
paragraphs of the root file (2026-08-31) the median is 502 bytes and the 90th
percentile 2,805. SLACK = 4,000 therefore admits one unusually large addition
plus an ordinary one before it refuses, while eight median paragraphs of
accumulation cannot pass. It reddens on REGROWTH, not on a paragraph.

DISCOVERY ASKS GIT, AND THAT REPLACED A HAND-MAINTAINED PRUNE LIST. An earlier
draft walked the tree and skipped a declared set of directories (`.venv`,
`.worktrees`, `dist`, `node_modules`, ...). `tests/test_exception_table_discipline.py`
refused it, correctly: that table had no self-deletion gate and could not have
one, because most of its rows name directories that legitimately do not exist in
a given checkout — so it was a list that could only ever grow, which is the place
inconvenient paths get parked. Asking git for TRACKED files answers the same
question with no table at all: a build artifact, a virtual environment and a
sibling worktree are all untracked here, and a doctrine file that is not
committed governs nobody but its author. Measured on the benchmark project, the
difference between the two is not cosmetic — a raw walk finds 1,206 files and
6.2 MB, of which 1,105 are duplicate copies inside `.worktrees`; git reports the
honest 76.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SLACK = 4_000

DEFAULT_CEILING = 8_000

# path relative to the repo root -> (ceiling in bytes, why this number)
#
# THESE NUMBERS ARE NOT TRUSTED TO THIS COMMENT, and the T-131 landing is what
# demonstrated it rather than argued it. Before the split the root entry recorded
# the pre-split size; the moment the split landed, the file dropped to a fifth of
# it and `test_no_declared_ceiling_is_stale` went red on that very commit —
# 38,072 bytes under a ceiling of 199,123, some 161,000 of slack against the
# 4,000 allowed — and stayed red until the number was re-derived. The comment did
# not force that; a comment is text no test reads. The staleness half did.
# The same mechanism now guards these post-split numbers.
CEILINGS: dict[str, tuple[int, str]] = {
    "CLAUDE.md": (
        42_188,
        "achieved 38,188 after the T-131 directive/depth split plus one SLACK; "
        "the root is injected into EVERY session, so this is the number that "
        "matters most",
    ),
    "src/codebugs/CLAUDE.md": (
        115_018,
        "achieved 111,018 plus one SLACK. Far above the benchmark's ~6 KB average "
        "nested file, and deliberately so: this package is 25 modules in ONE flat "
        "directory, so there is a single hook point for the whole domain. It is "
        "paid only by a session that reads a file in src/codebugs/, never at "
        "startup",
    ),
    "tests/CLAUDE.md": (
        9_565,
        "achieved 5,565 plus one SLACK; declared rather than left to the default "
        "so a change of size is a deliberate edit",
    ),
}


def _discovered() -> dict[str, int]:
    """Every TRACKED CLAUDE.md, by path relative to the repo root, with its size.

    Fails closed: if git cannot answer, this raises rather than returning an
    empty mapping, because an empty mapping would make every ceiling below pass
    vacuously.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", "*CLAUDE.md"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found: dict[str, int] = {}
    for rel in out.split("\0"):
        if not rel or Path(rel).name != "CLAUDE.md":
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            found[rel] = path.stat().st_size
    return found


def test_discovery_is_not_vacuous() -> None:
    """A gate that found nothing would pass everything.

    The root file is the one path this repository is certain to have, so its
    absence means discovery itself is broken — the failure mode this whole
    module would otherwise hide.
    """
    found = _discovered()
    assert found, "no tracked CLAUDE.md discovered at all — discovery is broken"
    assert "CLAUDE.md" in found, f"the ROOT CLAUDE.md was not discovered; found {sorted(found)}"


@pytest.mark.parametrize("rel", sorted(_discovered()))
def test_file_is_within_its_ceiling(rel: str) -> None:
    actual = _discovered()[rel]
    ceiling, _reason = CEILINGS.get(rel, (DEFAULT_CEILING, "undeclared — default applies"))
    assert actual <= ceiling, (
        f"{rel} is {actual} bytes, over its ceiling of {ceiling}.\n"
        "This file is injected into every session that reaches it, so growth is "
        "paid on every run. Either move the new material to its subsystem's "
        "nested CLAUDE.md or to docs/claude-md-rationale/, or — if the growth is "
        "genuinely directive — raise the ceiling here deliberately, with a reason."
    )


def test_no_declared_ceiling_is_stale() -> None:
    """A ceiling far above reality is a gate that cannot fire."""
    found = _discovered()
    hollow = []
    for rel, (ceiling, _reason) in CEILINGS.items():
        actual = found.get(rel)
        if actual is not None and ceiling - actual > SLACK:
            hollow.append(f"{rel}: {actual} bytes under a ceiling of {ceiling}")
    assert not hollow, (
        f"these ceilings sit more than {SLACK} bytes above the file they govern: "
        f"{hollow}.\nRe-derive each from the size actually achieved, so the "
        "number keeps meaning something."
    )


def test_no_declared_ceiling_names_a_missing_file() -> None:
    """Self-deleting: the table cannot outlive the files it governs."""
    found = _discovered()
    stale = [rel for rel in CEILINGS if rel not in found]
    assert not stale, (
        f"these paths have a declared ceiling but are not tracked files: {stale}. "
        "Remove the entry, or restore the file."
    )


def test_every_declared_ceiling_carries_a_reason() -> None:
    blank = []
    for rel, (_ceiling, reason) in CEILINGS.items():
        if not isinstance(reason, str) or not reason.strip():
            blank.append(rel)
    assert not blank, f"these ceilings are declared with no reason: {blank}"
