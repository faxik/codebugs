"""CB-31 mutation harness — proves the new tests can actually fail.

Every mutation is (1) checked to have LANDED (an unmatched replace is the vacuous
row this exists to prevent), (2) COMPILED before the suite runs — a duplicate
keyword argument parses under ast.parse and only fails at compile time, which once
scored a vacuous kill here — and (3) restored in a finally.

CB-173: before any of that, `mutation_guard.require_clean_tree` checks that every
file this harness is about to overwrite has no uncommitted changes. A run that
completes normally restores this content correctly — the danger is a run that
gets interrupted (killed, timed out) before its `finally` runs: the mutation is
left stranded on disk, and cleaning that up with `git checkout --` discards any
uncommitted work on the same file along with it. That destroyed uncommitted
work five times (CB-173's cited incidents). Pass --allow-dirty (or set
CODEBUGS_MUTATION_ALLOW_DIRTY=1) to run anyway.

Run:  uv run --extra dev python tests/manual/mutate_cb31.py
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from mutation_guard import require_clean_tree

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src" / "codebugs" / "milestones"
ENTITIES = ROOT / "src" / "codebugs" / "entities.py"

MUTATIONS = [
    (
        "M1 unqualify the correlated ref column",
        SRC / "reconcile.py",
        'ref_expr=f"{alias}.item_ref"',
        'ref_expr="item_ref"',
        "TestLiveSourceClauseDifferential",
    ),
    (
        "M2 EXISTS -> NULL-unsafe scalar subquery (entities)",
        ENTITIES,
        'f"EXISTS (SELECT 1 FROM {self.table} _src "  # noqa: S608\n'
        '            f"WHERE _src.id = {ref_expr} AND _src.status IN ({placeholders}))"',
        'f"(SELECT _src.status FROM {self.table} _src "  # noqa: S608\n'
        '            f"WHERE _src.id = {ref_expr}) IN ({placeholders})"',
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
        "    for pattern, _ in buckets:\n        sql, params = _bucket_query(",
        '    for pattern, _ in buckets:\n'
        '        live_clause, live_params = live_source_clause(conn, alias="mi")\n'
        "        sql, params = _bucket_query(",
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
        ENTITIES,
        "        terminal = sorted(self.terminal)",
        "        terminal = list(self.terminal)",
        "TestLiveSourceClauseDifferential TestLiveSourceClauseTableAvailability",
    ),
    (
        "M9 null-unsafe `=` on the item_kind discriminator",
        SRC / "reconcile.py",
        'fragments.append(f"NOT ({alias}.item_kind IS ? AND {exists_sql})")',
        'fragments.append(f"NOT ({alias}.item_kind = ? AND {exists_sql})")',
        "TestLiveSourceClauseTableAvailability::test_a_null_item_kind_stays_live",
    ),
    (
        "M10 entities drops the readable_cols check",
        ENTITIES,
        '        for col in ("id", "status"):\n'
        "            if col not in self.readable_cols:",
        '        for col in ():\n'
        "            if col not in self.readable_cols:",
        "TestTerminalExistsSqlGuards",
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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="skip the clean-tree check (CB-173) and mutate even with uncommitted "
        "changes to a target file",
    )
    args = parser.parse_args()
    allow_dirty = args.allow_dirty or os.environ.get("CODEBUGS_MUTATION_ALLOW_DIRTY") == "1"
    require_clean_tree(
        sorted({str(path) for _label, path, _old, _new, _targets in MUTATIONS}),
        cwd=ROOT,
        allow_dirty=allow_dirty,
    )

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
