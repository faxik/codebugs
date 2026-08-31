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

FAIL-CLOSED ON DISCOVERY. Files are found by walking the tree, never by reading
a list, because a rule expressed as an enumeration is only ever enforced at the
sites someone enumerated. A CLAUDE.md that nobody declared does not thereby
escape: it gets DEFAULT_CEILING. And a declared entry whose file is gone fails,
so this table cannot rot into a place where inconvenient files are parked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SLACK = 4_000

# Directories that are not a source of injected doctrine. Each carries its
# reason, because a bare list becomes the place inconvenient paths are hidden.
PRUNED = {
    ".git": "git's own storage, not a checkout",
    ".venv": "installed third-party packages carry their own CLAUDE.md files",
    ".worktrees": "sibling branches' full checkouts; their files belong to them",
    ".claude/worktrees": "the legacy worktree location, same reason",
    "node_modules": "vendored packages",
    "__pycache__": "build artifacts",
    "dist": "build artifacts — copies of a source file are not extra doctrine",
}

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
    """Every CLAUDE.md that would be injected, by path relative to the repo root."""
    found: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        rel_dir = os.path.relpath(dirpath, REPO_ROOT)
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [
            d
            for d in dirnames
            if d not in PRUNED and os.path.join(rel_dir, d).replace(os.sep, "/") not in PRUNED
        ]
        if "CLAUDE.md" in filenames:
            rel = os.path.join(rel_dir, "CLAUDE.md").replace(os.sep, "/")
            rel = rel.lstrip("/")
            found[rel] = (Path(dirpath) / "CLAUDE.md").stat().st_size
    return found


def test_discovery_is_not_vacuous() -> None:
    """A gate that found nothing would pass everything.

    The root file is the one path this repository is certain to have, so its
    absence from the walk means the walk itself is broken — the failure mode
    this whole module would otherwise hide.
    """
    found = _discovered()
    assert found, "no CLAUDE.md discovered at all — the walk is broken"
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


@pytest.mark.parametrize("rel", sorted(CEILINGS))
def test_declared_ceiling_is_not_stale(rel: str) -> None:
    """A ceiling far above reality is a gate that cannot fire."""
    found = _discovered()
    actual = found[rel]  # existence is asserted by the test below
    ceiling, _reason = CEILINGS[rel]
    assert ceiling - actual <= SLACK, (
        f"{rel} is {actual} bytes but its declared ceiling is {ceiling} — "
        f"{ceiling - actual} bytes of slack, over the {SLACK} allowed.\n"
        "Re-derive the ceiling from the size actually achieved, so the number "
        "keeps meaning something."
    )


@pytest.mark.parametrize("rel", sorted(CEILINGS))
def test_declared_ceiling_names_a_file_that_exists(rel: str) -> None:
    """Self-deleting: the table cannot outlive the files it governs."""
    assert rel in _discovered(), (
        f"{rel} has a declared ceiling but no such file was discovered. "
        "Remove the entry, or restore the file."
    )


def test_every_declared_ceiling_carries_a_reason() -> None:
    for rel, (_ceiling, reason) in CEILINGS.items():
        assert reason.strip(), f"{rel} declares a ceiling with no reason"
