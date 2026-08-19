"""CB-31 mutation harness — proves the new tests can actually fail.

Every mutation is (1) checked to have LANDED (an unmatched replace is the vacuous
row this exists to prevent), (2) COMPILED before the suite runs — a duplicate
keyword argument parses under ast.parse and only fails at compile time, which once
scored a vacuous kill here — and (3) restored in a finally.

Run:  uv run --extra dev python tests/manual/mutate_cb31.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src" / "codebugs" / "milestones"

MUTATIONS = [
    (
        "M1 unqualify the correlated columns",
        SRC / "reconcile.py",
        'f"WHERE {alias}.item_kind = ? AND _src.id = {alias}.item_ref "',
        'f"WHERE item_kind = ? AND _src.id = item_ref "',
        "TestLiveSourceClauseAlias",
    ),
    (
        "M2 NOT EXISTS -> NULL-unsafe scalar subquery",
        SRC / "reconcile.py",
        'f"NOT EXISTS (SELECT 1 FROM {kind.table} _src "\n'
        '            f"WHERE {alias}.item_kind = ? AND _src.id = {alias}.item_ref "\n'
        '            f"AND _src.status IN ({placeholders}))"',
        'f"(CASE WHEN {alias}.item_kind = ? THEN "\n'
        '            f"(SELECT _src.status FROM {kind.table} _src '
        'WHERE _src.id = {alias}.item_ref) "\n'
        '            f"NOT IN ({placeholders}) ELSE 1 END)"',
        "TestLiveSourceClauseDifferential",
    ),
    (
        "M3 drop the alias validation",
        SRC / "reconcile.py",
        "    if not is_sql_identifier(alias):\n"
        "        raise ValueError(\n"
        '            f"alias must be a plain SQL identifier, got {alias!r}"\n'
        "        )\n",
        "",
        "TestLiveSourceClauseAlias::test_alias_must_be_a_bare_identifier",
    ),
    (
        "M4 rebuild the clause per bucket (inside BEGIN IMMEDIATE)",
        SRC / "capacity.py",
        "    for pattern, _ in buckets:\n        rows = conn.execute(",
        '    for pattern, _ in buckets:\n'
        '        live_clause, live_params = live_source_clause(conn, alias="mi")\n'
        "        rows = conn.execute(",
        "TestLiveSourceClauseCost::test_candidates_builds_the_clause_once"
        "_for_all_four_buckets",
    ),
    (
        "M5 revert triage_inbox to the per-row Python filter",
        SRC / "triage.py",
        '    clause, params = live_source_clause(conn, alias="mi")\n'
        '    rows = conn.execute(\n'
        '        "SELECT mi.* FROM milestone_items mi "\n'
        "        \"WHERE mi.milestone_id = 'stream/triage' AND mi.status = 'open' \"\n"
        '        f"AND ({clause}) "\n'
        '        "ORDER BY mi.created_at ASC, mi.id ASC",\n'
        "        params,\n"
        "    ).fetchall()\n"
        "    return [_row_to_item(r) for r in rows[:limit]]",
        "    from codebugs.milestones.reconcile import source_is_terminal\n"
        "    rows = conn.execute(\n"
        '        "SELECT * FROM milestone_items "\n'
        "        \"WHERE milestone_id = 'stream/triage' AND status = 'open' \"\n"
        '        "ORDER BY created_at ASC",\n'
        "    ).fetchall()\n"
        "    live = [\n"
        "        r for r in rows\n"
        '        if not source_is_terminal(conn, r["item_kind"], r["item_ref"])\n'
        "    ]\n"
        "    return [_row_to_item(r) for r in live[:limit]]",
        "TestLiveSourceClauseCost TestLiveSourceClauseCallSites",
    ),
    (
        "M6 emit a fragment for an absent source table",
        SRC / "reconcile.py",
        "        if not _table_exists(conn, kind.table):\n            continue",
        "        if False:\n            continue",
        "TestLiveSourceClauseTableAvailability",
    ),
    (
        "M7 unsorted terminal statuses (EXPECTED SURVIVOR)",
        SRC / "reconcile.py",
        "        terminal = sorted(kind.terminal)",
        "        terminal = list(kind.terminal)",
        "TestLiveSourceClauseDifferential TestLiveSourceClauseTableAvailability",
    ),
    (
        "M8 splice foundation's seam params at the WRONG position",
        SRC / "foundation.py",
        "        params.extend(live_params)",
        "        params[:0] = live_params",
        "TestLiveSourceClauseAdoption::test_list_milestone_items_filters_before_offset",
    ),
]


def run(label, path, old, new, targets):
    original = path.read_text()
    if old not in original:
        return label, "NOT-APPLIED", "pattern absent - VACUOUS ROW"
    mutated = original.replace(old, new, 1)
    if mutated == original:
        return label, "NOT-APPLIED", "replace was a no-op"
    try:
        path.write_text(mutated)
        assert path.read_text() == mutated, "mutation did not land on disk"
        try:
            compile(mutated, str(path), "exec")
        except SyntaxError as e:
            return label, "INVALID", f"mutation does not compile: {e}"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-x",
             *(f"tests/test_milestones_reconcile.py::{t}" for t in targets.split())],
            cwd=ROOT, capture_output=True, text=True,
        )
        return label, ("KILLED" if proc.returncode != 0 else "SURVIVED"), \
            proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    finally:
        path.write_text(original)
        assert path.read_text() == original, "RESTORE FAILED - tree is dirty!"


if __name__ == "__main__":
    print(f"interpreter: {sys.executable}\n")
    results = [run(*m) for m in MUTATIONS]
    for label, verdict, detail in results:
        print(f"{verdict:<12} {label}\n             {detail}")
    killed = sum(v == "KILLED" for _, v, _ in results)
    print(f"\n{killed}/{len(results)} killed")
    bad = [r for r in results if r[1] in ("NOT-APPLIED", "INVALID")]
    if bad:
        print("PROBLEM ROWS (vacuous, not genuine survivors):")
        for label, verdict, detail in bad:
            print(f"  {verdict} {label}: {detail}")
        sys.exit(2)
