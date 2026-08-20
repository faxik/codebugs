#!/usr/bin/env python3
"""BT-1: what each CANDIDATE predicate actually covers, measured over the population.

WHAT THIS ANSWERS
    For five candidate detectors -- P_naive, P_naive_fstring, P_common_ancestor
    (and its defect-detecting negation), P_f2_body, P_f2_resolved -- how many
    members of the population from `population.py` each one MARKS, how many it
    MISSES, which ones BY NAME, how many non-population functions it also marks,
    and how the marks break down per family.

WHY THIS QUESTION MUST NOT BE ANSWERED BY READING CODE
    "What would this predicate see?" was asserted three times in a row without
    running it, and was wrong three times in a row -- "100% of the population is
    invisible", then "10 are visible", then "4 of 7". A predicate's coverage is an
    intersection between a syntactic test and a semantic population spread over
    ~29 modules; the eye cannot compute an intersection, and each of those three
    wrong numbers was refuted by execution, never by another reading. So coverage
    is not argued here. It is computed, printed by name, and re-runnable.

    The population is imported from `population.py` rather than re-derived, so the
    two halves of the claim cannot drift apart.

USAGE
    python3 .claude/plans/bt1-scripts/predicates.py [--root src/codebugs]

Writes nothing anywhere; prints to stdout; stdlib only.
"""

from __future__ import annotations

import argparse
import os
import sys

# Importing a sibling module must not leave a __pycache__ behind: this script is
# required to write nothing into the repository, and that includes bytecode.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import population as P  # noqa: E402


# --------------------------------------------------------------------------
# Candidate predicates
# --------------------------------------------------------------------------


def has_commit_spelling(f: P.Func) -> bool:
    return any(b.kind not in ("txn_enter", "txn_exit") for b in f.boundaries)


def literal_selects(f: P.Func) -> list[P.SqlOp]:
    return [o for o in f.ops if o.kind == P.KIND_READ and o.form == "literal"]


def fstring_selects(f: P.Func) -> list[P.SqlOp]:
    return [
        o
        for o in f.ops
        if o.kind == P.KIND_READ and (o.form == "fstring" or o.form.startswith("name->"))
    ]


def p_naive(f: P.Func) -> bool:
    """A commit call in the body AND a literal conn.execute("SELECT ...")."""
    return has_commit_spelling(f) and bool(literal_selects(f))


def p_naive_fstring(f: P.Func) -> bool:
    """Same, but an f-string / resolved-name SELECT also counts."""
    return has_commit_spelling(f) and bool(literal_selects(f) + fstring_selects(f))


def p_common_ancestor(f: P.Func) -> bool:
    """LITERAL reading of the candidate: a read and a write share a `with db.txn`
    ancestor. This marks the PROTECTED shape, so it is reported as written and its
    negation is reported directly below it."""
    for a, b in f.txn_blocks:
        r = any(a <= o.line <= b for o in f.reads)
        w = any(a <= o.line <= b for o in f.writes)
        if r and w:
            return True
    return False


def p_common_ancestor_neg(f: P.Func) -> bool:
    """The defect-detecting form: SOME read/write pair (read first) shares no
    `with db.txn` ancestor."""
    for r in f.reads:
        for w in f.writes:
            if r.line >= w.line:
                continue
            shared = any(
                a <= r.line <= b and a <= w.line <= b for a, b in f.txn_blocks
            )
            if not shared:
                return True
    return False


def body_only_families(f: P.Func) -> tuple[list, list]:
    """Re-run the analysis with helper reads/writes removed -> (f1_pairs, f2_reads)
    as they look to a predicate that does NOT resolve helper calls."""
    saved = (f.helper_reads, f.helper_writes, f.f1_pairs, f.f1_unproven, f.f2_reads)
    f.helper_reads, f.helper_writes = [], []
    f.f1_pairs, f.f1_unproven, f.f2_reads = [], [], []
    P.analyze_function(f)
    out = (list(f.f1_pairs), list(f.f2_reads))
    (f.helper_reads, f.helper_writes, f.f1_pairs, f.f1_unproven, f.f2_reads) = saved
    return out


# --------------------------------------------------------------------------


def family(f: P.Func) -> str:
    if f.f1_pairs and f.f2_reads:
        return "F1+F2"
    if f.f1_pairs:
        return "F1"
    if f.f2_reads:
        return "F2"
    return "-"


def report(
    label: str,
    doc: str,
    marked_keys: set[str],
    pop: list[P.Func],
    funcs: list[P.Func],
) -> dict[str, int]:
    popkeys = {f.key for f in pop}
    hits = [f for f in pop if f.key in marked_keys]
    misses = [f for f in pop if f.key not in marked_keys]
    extra = [f for f in funcs if f.key in marked_keys and f.key not in popkeys]

    print("=" * 100)
    print(f"PREDICATE {label}")
    print(f"  definition: {doc}")
    print("-" * 100)
    print(f"  population size            : {len(pop)}")
    print(f"  MARKS (population)         : {len(hits)}")
    print(f"  MISSES (population)        : {len(misses)}")
    print(f"  also marks, NOT population : {len(extra)}")
    print()
    print(f"  MARKED ({len(hits)}):")
    if not hits:
        print("      <none>")
    for f in hits:
        print(f"      [{family(f):<5}] {f.name:<50} {f.loc}")
    print()
    print(f"  MISSED ({len(misses)}):")
    if not misses:
        print("      <none>")
    for f in misses:
        print(f"      [{family(f):<5}] {f.name:<50} {f.loc}")
    print()
    print(f"  MARKED OUTSIDE THE POPULATION ({len(extra)}):")
    if not extra:
        print("      <none>")
    for f in extra[:30]:
        print(f"      {f.name:<50} {f.loc}")
    if len(extra) > 30:
        print(f"      ... and {len(extra) - 30} more")
    print()
    return {
        "F1": sum(1 for f in hits if family(f) == "F1"),
        "F2": sum(1 for f in hits if family(f) == "F2"),
        "F1+F2": sum(1 for f in hits if family(f) == "F1+F2"),
        "total": len(hits),
        "missed": len(misses),
        "extra": len(extra),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join("src", "codebugs"))
    args = ap.parse_args()
    if not os.path.isdir(args.root):
        print(f"no such root: {args.root}", file=sys.stderr)
        return 2

    funcs, pop, meta = P.build(args.root)

    print("=" * 100)
    print("BT-1 PREDICATE COVERAGE  --  computed, not reasoned about")
    print("=" * 100)
    print(f"root              : {meta['root']}")
    print(f"python            : {sys.version.split()[0]}")
    print(f"functions scanned : {len(funcs)}")
    print(f"population        : {len(pop)}  "
          f"(F1-only {sum(1 for f in pop if family(f) == 'F1')}, "
          f"F2-only {sum(1 for f in pop if family(f) == 'F2')}, "
          f"both {sum(1 for f in pop if family(f) == 'F1+F2')})")
    print()
    print("POPULATION, numbered (the same numbering predicates are reported against)")
    print("-" * 100)
    for i, f in enumerate(pop, 1):
        print(f"[{i:>3}] [{family(f):<5}] {f.loc:<44} {f.name}")
    print("-" * 100)
    print()

    # body-only families, computed once
    body_f = {}
    for f in funcs:
        body_f[f.key] = body_only_families(f)

    rows = {}

    rows["P_naive"] = report(
        "P_naive",
        'a commit spelling in the body AND a literal conn.execute("SELECT ...")',
        {f.key for f in funcs if p_naive(f)},
        pop,
        funcs,
    )
    rows["P_naive_fstring"] = report(
        "P_naive_fstring",
        "as P_naive, but an f-string / resolved-name SELECT also counts",
        {f.key for f in funcs if p_naive_fstring(f)},
        pop,
        funcs,
    )
    rows["P_common_ancestor"] = report(
        "P_common_ancestor",
        "a read and a write share a `with db.txn` ancestor (marks the PROTECTED shape)",
        {f.key for f in funcs if p_common_ancestor(f)},
        pop,
        funcs,
    )
    rows["P_common_ancestor_NEG"] = report(
        "P_common_ancestor_NEG",
        "some read/write pair shares NO `with db.txn` ancestor (defect-detecting form)",
        {f.key for f in funcs if p_common_ancestor_neg(f)},
        pop,
        funcs,
    )
    rows["P_f2_body"] = report(
        "P_f2_body",
        "a read between a boundary and a return, VISIBLE IN THE BODY (helpers unresolved)",
        {k for k, (f1, f2) in body_f.items() if f2},
        pop,
        funcs,
    )
    rows["P_f2_resolved"] = report(
        "P_f2_resolved",
        "same, with helper calls resolved through the package-wide 'reads' map",
        {f.key for f in funcs if f.f2_reads},
        pop,
        funcs,
    )

    # ---------------------------------------------------------------- matrix
    n_f1 = sum(1 for f in pop if family(f) == "F1")
    n_f2 = sum(1 for f in pop if family(f) == "F2")
    n_both = sum(1 for f in pop if family(f) == "F1+F2")
    print("=" * 100)
    print("MATRIX  predicate x family   (cells = population members MARKED)")
    print("=" * 100)
    print(f"{'predicate':<24} {'F1':>6} {'F2':>6} {'F1+F2':>7} {'total':>7} "
          f"{'missed':>7} {'extra':>7}")
    print("-" * 100)
    print(f"{'(population size)':<24} {n_f1:>6} {n_f2:>6} {n_both:>7} {len(pop):>7} "
          f"{'-':>7} {'-':>7}")
    for name, r in rows.items():
        print(
            f"{name:<24} {r['F1']:>6} {r['F2']:>6} {r['F1+F2']:>7} {r['total']:>7} "
            f"{r['missed']:>7} {r['extra']:>7}"
        )
    print("-" * 100)
    print("extra = functions the predicate marks that are NOT in the population.")
    print()

    # ------------------------------------------------- F2 body vs resolved
    print("=" * 100)
    print("F2: WHAT HELPER RESOLUTION BUYS  (per member)")
    print("=" * 100)
    print(f"{'member':<52} {'body-only':>10} {'resolved':>10}")
    print("-" * 100)
    for f in pop:
        if not f.f2_reads and not body_f[f.key][1]:
            continue
        print(f"{f.name:<52} {len(body_f[f.key][1]):>10} {len(f.f2_reads):>10}")
    print("-" * 100)
    forms: dict[str, int] = {}
    for f in pop:
        for r, _b in f.f2_reads:
            forms[r.form] = forms.get(r.form, 0) + 1
    print("resolved F2 reads by form:")
    for k in sorted(forms):
        print(f"    {k:<18} {forms[k]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
