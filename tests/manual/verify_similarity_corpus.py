"""Reproduce the CB-45 similarity calibration against a real tracker corpus.

Not collected by pytest (tests/manual/); run explicitly from the repo root:

    uv run python tests/manual/verify_similarity_corpus.py [db_path]

Exact tolerance (adversarial-review mandate): at threshold 0.7 on the
2026-08-16 autosorter corpus (3162 rows), family count and collapse count must
match EXACTLY — 11 multi-row families, 102 rows collapse, and the 115-row
post_merge_gate_failure category groups 111/115 rows into ~10 subfamilies.
Only total row counts may drift as the live corpus grows. A deviation in
family/collapse counts means the shipped normalization diverged from the
calibrated one — investigate, never hand-wave as "corpus drift".
"""

import sqlite3
import sys

sys.path.insert(0, "src")
from codebugs import similarity  # noqa: E402

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

print("\ncalibration reference (2026-08-16): 11 families, 102 collapse, gate 111/115 grouped")
