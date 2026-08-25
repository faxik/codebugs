"""CB-69 mutation harness — proves the new tests can actually fail.

Same three checks as the CB-31 harness, for the same recorded reasons: a mutation
must be verified to have LANDED (an unmatched replace is a vacuous row), must
COMPILE before the suite runs (a duplicate keyword argument parses under ast.parse
and fails only at compile time), and must be restored in a finally.

CB-173: before any of that, `mutation_guard.require_clean_tree` checks that every
file this harness is about to overwrite has no uncommitted changes. A run that
completes normally restores this content correctly — the danger is a run that
gets interrupted (killed, timed out) before its `finally` runs: the mutation is
left stranded on disk, and cleaning that up with `git checkout --` discards any
uncommitted work on the same file along with it. That destroyed uncommitted
work five times (CB-173's cited incidents). Pass --allow-dirty (or set
CODEBUGS_MUTATION_ALLOW_DIRTY=1) to run anyway.

Run:  uv run --extra dev python tests/manual/mutate_cb69.py
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from mutation_guard import require_clean_tree

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src" / "codebugs"

MUTATIONS = [
    (
        "M1 wrapper reverts to the two-call pairing (findings)",
        SRC / "findings.py",
        "                deferred_ids, deferred_counts = blockers.deferred_ids_and_counts(\n"
        "                    conn, ENTITY_FINDING, id=id, ids=ids\n"
        "                )",
        "                deferred_ids = blockers.deferred_id_restriction(\n"
        "                    conn, ENTITY_FINDING, id=id, ids=ids\n"
        "                )\n"
        "                deferred_counts = blockers.blocker_counts_for(\n"
        "                    conn, ENTITY_FINDING, deferred_ids\n"
        "                )",
        "tests/test_blockers.py::TestDeferredWrappersUseTheSinglePass",
    ),
    (
        "M2 counts are NOT restricted to the returned ids",
        SRC / "blockers.py",
        "    return restricted, {i: counts[i] for i in restricted}",
        "    return restricted, counts",
        "tests/test_blockers.py::TestDeferredIdsAndCountsDifferential",
    ),
    (
        "M3 the id intersection is dropped",
        SRC / "blockers.py",
        "    return sorted(deferred & requested) if requested else sorted(deferred)",
        "    return sorted(deferred)",
        "tests/test_blockers.py::TestDeferredIdsAndCountsDifferential "
        "tests/test_blockers.py::TestDeferredEmptyIntersection",
    ),
    (
        "M4 _active_counts ignores is_active",
        SRC / "blockers.py",
        "    for b in evaluated:\n        if b[\"is_active\"]:\n"
        "            counts[b[\"item_id\"]] = counts.get(b[\"item_id\"], 0) + 1",
        "    for b in evaluated:\n        if True:\n"
        "            counts[b[\"item_id\"]] = counts.get(b[\"item_id\"], 0) + 1",
        "tests/test_blockers.py::TestDeferredIdsAndCountsDifferential "
        "tests/test_blockers.py::TestDeferredHelpers",
    ),
    (
        "M5 query_deferred_entities re-grows its own counting loop",
        SRC / "blockers.py",
        "    active_counts = _active_counts(evaluated)",
        "    active_counts = {}\n"
        "    for _b in evaluated:\n"
        "        if _b[\"is_active\"]:\n"
        "            active_counts[_b[\"item_id\"]] = active_counts.get(_b[\"item_id\"], 0) + 1",
        "tests/test_blockers.py::TestActiveCountsIsTheSingleDefinition",
    ),
    (
        "M6 the wrapper reverts (reqs)",
        SRC / "reqs.py",
        "                deferred_ids, deferred_counts = blockers.deferred_ids_and_counts(\n"
        "                    conn, ENTITY_REQUIREMENT, id=id, ids=ids\n"
        "                )",
        "                deferred_ids = blockers.deferred_id_restriction(\n"
        "                    conn, ENTITY_REQUIREMENT, id=id, ids=ids\n"
        "                )\n"
        "                deferred_counts = blockers.blocker_counts_for(\n"
        "                    conn, ENTITY_REQUIREMENT, deferred_ids\n"
        "                )",
        "tests/test_blockers.py::TestDeferredWrappersUseTheSinglePass",
    ),
    (
        "M7 delete the reqs blocker_count annotation entirely",
        SRC / "reqs.py",
        "            if deferred_ids is not None and not result.get(\"grouped\"):\n"
        "                for row in result[\"requirements\"]:\n"
        "                    row[\"blocker_count\"] = deferred_counts.get(row[\"id\"], 0)\n",
        "",
        "tests/test_blockers.py::TestDeferredWrappersUseTheSinglePass",
    ),
    (
        "M8 get_deferred_counts stops using the shared aggregation",
        SRC / "blockers.py",
        "    active_counts = _active_counts(evaluated)\n"
        "    all_items = {b[\"item_id\"] for b in evaluated}",
        "    _hand = {b[\"item_id\"] for b in evaluated if b[\"is_active\"]}\n"
        "    active_counts = dict.fromkeys(_hand, 1)\n"
        "    all_items = {b[\"item_id\"] for b in evaluated}",
        "tests/test_blockers.py::TestActiveCountsIsTheSingleDefinition",
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
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-x", *targets.split()],
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
