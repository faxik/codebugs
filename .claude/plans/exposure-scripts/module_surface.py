#!/usr/bin/env python3
"""Per-module surface measurement for BT-6 (surface-generator pilot).

Two reports, both computed, neither hand-written (BT-1/BT-3 lesson: a claim
about what a predicate sees is produced by RUNNING the predicate):

1. TIER TABLE per module: capabilities, MCP tools by derivability tier
   (T1/T2/T3 from matrix.derivability_tier), CLI verbs.  `matrix.py` prints
   tiers only in aggregate; this is the bespoke pass BT-6 §2 cites.

2. SLOC (`--sloc FILE...`): ONE counter, applied identically BEFORE and
   AFTER, over every production file the pilot touches.  A source line counts
   when it carries at least one token that is not a comment, not whitespace,
   and not part of a docstring statement.  `help="..."` on an `add_argument`
   line counts as code (the line IS code); a docstring does not.  The headline
   answer to the owner's question ("does the code shrink") is the NET delta of
   this number — no relocation accounting, because two independent attackers
   showed the v1 partition (below) charged relocated text to the generator
   while refusing to credit it to the baseline: `git diff --numstat -M`
   cannot see a docstring moved out of a file that survives (measured), and
   every one of bench's 23 `help=` lines is also an add_argument call.

3. HANDLERS (`--handlers NAME,...`): SLOC of named module-level functions
   wherever they live — the instrument for H_manual.  The declaration file
   must NAME its manual handlers; this resolves the names by AST and sums
   their spans, so moving a handler out of `register_*` cannot hide it.

4. WIRING PARTITION (`--module bench`), SECONDARY and kept for provenance:
   spans of top-level `register_tools`/`register_cli` split into
   code / docstring / help-text / comment / blank.  KNOWN LIMIT, stated rather
   than fixed: a physical line carrying both a call and a `help=` literal is
   booked as `help`, so the `code` column UNDERCOUNTS CLI wiring (bench: 23 of
   23 help lines are add_parser/add_argument calls).  Do not use `code` as a
   headline; use SLOC.

Usage:
    PYTHONPATH=src uv run python .claude/plans/exposure-scripts/module_surface.py
    PYTHONPATH=src uv run python .claude/plans/exposure-scripts/module_surface.py --module bench
    PYTHONPATH=src uv run python .claude/plans/exposure-scripts/module_surface.py --sloc src/codebugs/bench.py
    PYTHONPATH=src uv run python .claude/plans/exposure-scripts/module_surface.py --handlers _cmd_bench_import --in src/codebugs/bench.py
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
    print("  SECONDARY view. `help` lines are also calls (known undercount of `code`); headline = --sloc")


def _docstring_stmt_lines(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                out.update(range(body[0].lineno, M.end_of(body[0]) + 1))
    return out


def sloc_of(src: str, *, lo: int = 1, hi: int | None = None) -> int:
    """Lines in [lo, hi] carrying a token that is not comment/NL/docstring."""
    tree = ast.parse(src)
    doc = _docstring_stmt_lines(tree)
    code_lines: set[int] = set()
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in skip:
            continue
        for ln in range(tok.start[0], tok.end[0] + 1):
            if ln in doc:
                continue
            if hi is not None and not (lo <= ln <= hi):
                continue
            code_lines.add(ln)
    return len(code_lines)


def sloc_report(paths: list[str]) -> None:
    total = 0
    print(f"{'file':48}{'sloc':>7}")
    for p in paths:
        try:
            n = sloc_of(open(p, encoding="utf-8").read())
        except FileNotFoundError:
            n = 0
            p = p + "  (absent)"
        total += n
        print(f"{p:48}{n:>7}")
    print(f"{'TOTAL':48}{total:>7}")
    print("  headline = NET delta of TOTAL between the pinned base and the pilot tip, same file list")


def handlers_report(path: str, names: list[str]) -> None:
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = sloc_of(src, lo=M.def_start(node), hi=M.end_of(node))
    print(f"{'handler':40}{'sloc':>7}")
    for n in names:
        print(f"{n:40}{found.get(n, 0):>7}{'' if n in found else '   NOT FOUND (fail loud)'}")
    print(f"{'H_manual':40}{sum(found.values()):>7}")
    missing = [n for n in names if n not in found]
    if missing:
        raise SystemExit(f"declared manual handler(s) not found: {missing}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join("src", "codebugs"))
    ap.add_argument("--module", help="module file stem to partition (secondary view), e.g. bench")
    ap.add_argument("--sloc", nargs="+", metavar="FILE", help="SLOC per file + total (headline)")
    ap.add_argument("--handlers", help="comma-separated manual handler names (H_manual)")
    ap.add_argument("--in", dest="in_file", help="file to resolve --handlers in")
    args = ap.parse_args()
    if args.sloc:
        print("SLOC (one counter, before == after)")
        sloc_report(args.sloc)
        return 0
    if args.handlers:
        if not args.in_file:
            ap.error("--handlers needs --in FILE")
        handlers_report(args.in_file, [n.strip() for n in args.handlers.split(",") if n.strip()])
        return 0
    print("TIER TABLE per module (computed via matrix.derivability_tier)")
    tier_table(args.root)
    if args.module:
        print()
        print(f"WIRING PARTITION for {args.module}.py (AST-keyed, not file-layout)")
        partition(os.path.join(args.root, args.module + ".py"), ("register_tools", "register_cli"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
