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
# NOTE FOR THE T-131 LANDING: the root entry below records the size BEFORE the
# directive/depth split. It must be re-derived from the achieved size in the
# same commit that lands the split, and the nested files must be added here.
CEILINGS: dict[str, tuple[int, str]] = {
    "CLAUDE.md": (
        194_000,
        "pre-split size (190,441 on 2026-08-31) plus one slack; re-derive when "
        "the T-131 directive/depth split lands",
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
