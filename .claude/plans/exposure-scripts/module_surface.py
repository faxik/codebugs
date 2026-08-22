#!/usr/bin/env python3
"""Per-module surface measurement for BT-6 (surface-generator pilot).

Two reports, both computed, neither hand-written (BT-1/BT-3 lesson: a claim
about what a predicate sees is produced by RUNNING the predicate):

1. TIER TABLE per module: capabilities, MCP tools by derivability tier
   (T1/T2/T3 from matrix.derivability_tier), CLI verbs.  `matrix.py` prints
   tiers only in aggregate; this is the bespoke pass BT-6 §2 cites.

2. WIRING PARTITION for one module (`--module bench`): the spans of the
   top-level `register_tools` and `register_cli` functions, partitioned line
   by line into  code / docstring / help-text / comment / blank.
   The awk range `'/^def register_tools/,/^register_tool_provider/'` that BT-6
   v1 used is a FILE-LAYOUT range: it counts whatever sits between two
   markers, so declarations placed there would be counted as baseline and a
   removed function yields 0.  This partition is keyed on the AST node, not
   on file layout, and the headline N_pilot is the `code` column only —
   docstrings and help= text RELOCATE into a declaration, they do not vanish.

Usage:
    PYTHONPATH=src uv run python .claude/plans/exposure-scripts/module_surface.py
    PYTHONPATH=src uv run python .claude/plans/exposure-scripts/module_surface.py --module bench
Writes nothing; stdlib only; run from the repo root.
"""
from __future__ import annotations

import argparse
import ast
import collections
import io
import os
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matrix as M  # noqa: E402


def tier_table(root: str) -> None:
    caps, _by_short, surfaces, *_rest = M.collect(root)
    cap_names = {c.name for c in caps.values()}
    rows: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: dict(caps=0, mcp=0, T1=0, T2=0, T3=0, cli=0)
    )
    for c in caps.values():
        rows[c.module]["caps"] += 1
    for s in surfaces:
        r = rows[s.module]
        if s.kind == "mcp":
            r["mcp"] += 1
            if s.node is not None:
                r[M.derivability_tier(s.node, cap_names)] += 1
        else:
            r["cli"] += 1
    print(f"{'module':24}{'caps':>5}{'mcp':>4}{'T1':>4}{'T2':>4}{'T3':>4}{'cli':>4}")
    for mod, r in sorted(rows.items(), key=lambda kv: (-kv[1]["mcp"], kv[0])):
        print(
            f"{mod:24}{r['caps']:>5}{r['mcp']:>4}{r['T1']:>4}{r['T2']:>4}{r['T3']:>4}{r['cli']:>4}"
        )


def _help_text_lines(fn: ast.FunctionDef) -> set[int]:
    """Lines occupied by `help=` / `description=` keyword string values."""
    out: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in ("help", "description") and isinstance(kw.value, ast.Constant):
                    out.update(range(kw.value.lineno, M.end_of(kw.value) + 1))
    return out


def _docstring_lines(fn: ast.FunctionDef) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
                node.body[0].value, ast.Constant
            ) and isinstance(node.body[0].value.value, str):
                d = node.body[0]
                out.update(range(d.lineno, M.end_of(d) + 1))
    return out


def partition(path: str, func_names: tuple[str, ...]) -> None:
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src, filename=path)
    comment_lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            comment_lines.add(tok.start[0])
    total = collections.Counter()
    print(f"{'block':18}{'span':>6}{'code':>6}{'docstr':>8}{'help':>6}{'comment':>9}{'blank':>7}")
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in func_names:
            continue
        start, end = M.def_start(node), M.end_of(node)
        doc = _docstring_lines(node)
        helpl = _help_text_lines(node)
        c = collections.Counter()
        for ln in range(start, end + 1):
            text = lines[ln - 1].strip()
            if not text:
                c["blank"] += 1
            elif ln in doc:
                c["docstr"] += 1
            elif ln in helpl:
                c["help"] += 1
            elif ln in comment_lines and text.startswith("#"):
                c["comment"] += 1
            else:
                c["code"] += 1
        span = end - start + 1
        c["span"] = span
        total.update(c)
        print(
            f"{node.name:18}{span:>6}{c['code']:>6}{c['docstr']:>8}{c['help']:>6}"
            f"{c['comment']:>9}{c['blank']:>7}"
        )
    print(
        f"{'TOTAL':18}{total['span']:>6}{total['code']:>6}{total['docstr']:>8}{total['help']:>6}"
        f"{total['comment']:>9}{total['blank']:>7}"
    )
    print("  headline N_pilot baseline = `code` column; docstr/help RELOCATE, comment/blank are not wiring")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join("src", "codebugs"))
    ap.add_argument("--module", help="module file stem to partition, e.g. bench")
    args = ap.parse_args()
    print("TIER TABLE per module (computed via matrix.derivability_tier)")
    tier_table(args.root)
    if args.module:
        print()
        print(f"WIRING PARTITION for {args.module}.py (AST-keyed, not file-layout)")
        partition(os.path.join(args.root, args.module + ".py"), ("register_tools", "register_cli"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
