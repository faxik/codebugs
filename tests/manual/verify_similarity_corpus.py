"""Reproduce the CB-45 similarity calibration against a real tracker corpus.

Not collected by pytest (tests/manual/); run explicitly from the repo root:

    uv run python tests/manual/verify_similarity_corpus.py [db_path]

Exact tolerance (adversarial-review mandate): at threshold 0.7 on the
2026-08-16 autosorter corpus (3162 rows), family count and collapse count must
match EXACTLY — 11 multi-row families, 102 rows collapse, and the 115-row
post_merge_gate_failure category groups 111/115 rows into 10 subfamilies. A
deviation in family/collapse counts means the shipped normalization diverged
from the calibrated one — investigate, never hand-wave as "corpus drift".

The exact gate is ENFORCED (nonzero exit), not merely printed (Codex diff
review: a verifier that only prints exits 0 on a regression). It arms only
while the corpus still matches the snapshot's row totals; once the live corpus
grows past them, exactness is undefined and the script says so out loud
instead of failing spuriously — re-pin the reference from a fresh
investigated run in that case.
"""

import sqlite3
import sys

sys.path.insert(0, "src")
from codebugs import similarity  # noqa: E402

# Pinned 2026-08-16 snapshot. REF_ROWS / REF_GATE_ROWS arm the gate; the rest
# are what the gate asserts. (Row total re-pinned same day at 3168 — the corpus
# gained 6 rows after the morning calibration run; every asserted count was
# verified identical at both totals.)
REF_ROWS = 3168
REF_FAMILIES = 11
REF_COLLAPSE = 102
REF_GATE_ROWS = 115
REF_GATE_GROUPED = 111
REF_GATE_FAMILIES = 10

DB = sys.argv[1] if len(sys.argv) > 1 else "/home/faxik/w/autosorter/.codebugs/findings.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

# The calibration measured ALL statuses (the corpus is mostly terminal rows) —
# the report's default population is deliberately live-only, so widen with the
# explicit "all" sentinel to compare like with like.
full = similarity.group_report(conn, status="all")
print(
    f"ALL-STATUS totals: rows={full['rows_considered']} "
    f"skipped_short={full['rows_skipped_short']} "
    f"collapse={full['collapse_count']} families={full['families_total']}"
)
for fam in full["families"][:12]:
    print(
        f"  n={fam['size']:3d} min_pair={fam['min_pair_score']:.3f} "
        f"cat={fam['category'][:32]:32s} ids={[m['id'] for m in fam['members'][:3]]}"
    )

gate = similarity.group_report(conn, status="all", category="post_merge_gate_failure")
grouped = sum(f["size"] for f in gate["families"])
print(
    f"\ngate category: rows={gate['rows_considered']} grouped={grouped} "
    f"families={gate['families_total']}"
)

if full["rows_considered"] != REF_ROWS or gate["rows_considered"] != REF_GATE_ROWS:
    print(
        f"\nCORPUS DRIFTED from the 2026-08-16 snapshot "
        f"(rows {full['rows_considered']} vs {REF_ROWS}, "
        f"gate {gate['rows_considered']} vs {REF_GATE_ROWS}); exact gate NOT armed. "
        f"Investigate the numbers above by hand and re-pin the reference."
    )
    sys.exit(0)

failures = []
for label, actual, expected in [
    ("families", full["families_total"], REF_FAMILIES),
    ("collapse", full["collapse_count"], REF_COLLAPSE),
    ("gate grouped", grouped, REF_GATE_GROUPED),
    ("gate families", gate["families_total"], REF_GATE_FAMILIES),
]:
    if actual != expected:
        failures.append(f"{label}: expected {expected}, got {actual}")

if failures:
    print("\nCALIBRATION REGRESSION — the shipped normalization diverged:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)

print(
    f"\nOK: exact match against the 2026-08-16 reference "
    f"({REF_FAMILIES} families, {REF_COLLAPSE} collapse, "
    f"gate {REF_GATE_GROUPED}/{REF_GATE_ROWS} in {REF_GATE_FAMILIES} subfamilies)"
)
