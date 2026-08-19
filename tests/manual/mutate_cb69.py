"""CB-69 mutation harness — proves the new tests can actually fail.

Same three checks as the CB-31 harness, for the same recorded reasons: a mutation
must be verified to have LANDED (an unmatched replace is a vacuous row), must
COMPILE before the suite runs (a duplicate keyword argument parses under ast.parse
and fails only at compile time), and must be restored in a finally.

Run:  uv run --extra dev python tests/manual/mutate_cb69.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

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
