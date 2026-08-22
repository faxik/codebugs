"""Recalibrate `loc.MIN_ANCHOR_CHARS` on FIRST-PARTY code (BT-7 Р6, debt item 7).

WHY THIS EXISTS. `BT-7-MEASUREMENTS-2026-08-22.md` §6.2 derived
`MIN_ANCHOR_CHARS = 24` from a 52 260-file Python corpus that was ~93%
third-party (it swept the filesystem, so vendored and virtualenv code
dominated). The design doc declares that the single calibration debt of v6 and
charges the capture unit with repaying it. This script is the repayment: same
PREDICATE, first-party population.

THE PREDICATE, unchanged from §6.2 so the two numbers are comparable: for every
line position in the corpus, normalize the line by `loc.normalize_lines` (the
`norm: "v1"` normalizer the anchor object names) and ask whether that
normalized body occurs MORE THAN ONCE inside its OWN file. Bucket the positions
by the body's character length. The reported curve is "% of positions whose
normalized body is not unique in its file", by length bucket.

WHAT FIRST-PARTY MEANS HERE, stated as a rule a re-run reproduces rather than
as a judgement: a Python file that is TRACKED BY GIT in the repository. That is
what excludes `.venv`, `node_modules` and vendored trees by construction — they
are not committed — without anyone maintaining a list of directory names to
skip. The contaminated corpus's mistake was walking the filesystem instead.

WHAT THE NUMBER IS FOR, restated because it decides how to read the curve:
`MIN_ANCHOR_CHARS` is NOT a uniqueness mechanism. §6.2 measured that even at
60+ characters 14.4% of lines repeat inside their own file, so no threshold
makes an anchor unique — context does that work (6.8x more of it). The
threshold's only job is to refuse anchor text so short it is NOISE. So the
number to read off is the KNEE: the length past which one more character stops
buying a meaningful drop in ambiguity.

Run:  uv run --extra dev python tests/manual/calibrate_min_anchor_chars.py
      uv run --extra dev python tests/manual/calibrate_min_anchor_chars.py <repo> [<repo> ...]
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from codebugs.loc import normalize_lines  # noqa: E402

# The buckets of §6.2's second table, so the two runs are read off the same axis.
BUCKETS: tuple[tuple[int, int | None], ...] = (
    (0, 9),
    (10, 19),
    (20, 29),
    (30, 39),
    (40, 49),
    (50, 59),
    (60, None),
)


def tracked_python_files(repo: str) -> list[str]:
    """Every git-TRACKED .py path in `repo`. The first-party predicate."""
    out = subprocess.run(
        ["git", "-C", repo, "ls-files", "-z", "*.py"],
        capture_output=True,
        check=True,
    ).stdout
    return [os.path.join(repo, p.decode("utf-8", "replace")) for p in out.split(b"\0") if p]


def measure(paths: list[str]) -> tuple[Counter, Counter, int, int]:
    """(positions per EXACT body length, non-unique ditto, files read, blank positions).

    Kept at exact length rather than pre-bucketed: §6.2's ten-wide buckets are
    what hid the knee the first time — the whole curve below 30 characters
    collapsed into three rows, and the drop that actually matters turned out to
    sit inside the first one.
    """
    total: Counter = Counter()
    dup: Counter = Counter()
    files = 0
    blank = 0
    for path in paths:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if b"\0" in raw:
            continue
        files += 1
        bodies = [body for _, body in normalize_lines(raw.decode("utf-8", "replace").split("\n"))]
        seen = Counter(bodies)
        for body in bodies:
            if not body:
                blank += 1
                continue
            total[len(body)] += 1
            if seen[body] > 1:
                dup[body and len(body)] += 1
    return total, dup, files, blank


def rate(total: Counter, dup: Counter, lo: int, hi: int | None) -> tuple[int, float | None]:
    """(positions in [lo, hi], % of them not unique in their own file)."""
    n = sum(c for length, c in total.items() if length >= lo and (hi is None or length <= hi))
    d = sum(c for length, c in dup.items() if length >= lo and (hi is None or length <= hi))
    return n, (100.0 * d / n if n else None)


def main(argv: list[str]) -> int:
    repos = argv[1:] or [os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))]
    paths: list[str] = []
    for repo in repos:
        found = tracked_python_files(repo)
        print(f"{repo}: {len(found)} tracked .py files")
        paths += found
    total, dup, files, blank = measure(paths)
    n = sum(total.values())
    print(f"\nfiles read: {files}; non-blank positions: {n}; blank/whitespace positions: {blank}")

    print("\n§6.2's buckets, so the two calibrations are read off the same axis")
    print("body length | positions | not unique in its own file | delta vs previous")
    prev: float | None = None
    for lo, hi in BUCKETS:
        t, pct = rate(total, dup, lo, hi)
        if not t or pct is None:
            continue
        label = f"{lo}-{hi}" if hi is not None else f"{lo}+"
        print(f"{label:>11} | {t:>9} | {pct:>25.1f}% | {'' if prev is None else f'{pct - prev:+.1f} pp'}")
        prev = pct

    print("\nFINE curve — where the knee actually is (4-wide, the range that decides)")
    print("body length | positions | not unique in its own file | delta vs previous")
    prev = None
    for lo in range(0, 48, 4):
        t, pct = rate(total, dup, lo, lo + 3)
        if not t or pct is None:
            continue
        print(f"{lo:>8}-{lo + 3:<2} | {t:>9} | {pct:>25.1f}% | {'' if prev is None else f'{pct - prev:+.1f} pp'}")
        prev = pct

    print("\nWHAT A THRESHOLD BUYS — the population it refuses, and that population's noise")
    print("threshold | refused (< it) | their ambiguity | kept (>= it) | kept ambiguity")
    for cut in (8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40):
        below, below_pct = rate(total, dup, 0, cut - 1)
        above, above_pct = rate(total, dup, cut, None)
        if below and above and below_pct is not None and above_pct is not None:
            share = 100.0 * below / n
            print(
                f"{cut:>9} | {below:>7} ({share:>4.1f}%) | {below_pct:>14.1f}% "
                f"| {above:>12} | {above_pct:>13.1f}%"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
