#!/usr/bin/env python3
"""Per-module surface measurement for BT-6 (surface-generator pilot).

Two reports, both computed, neither hand-written (BT-1/BT-3 lesson: a claim
about what a predicate sees is produced by RUNNING the predicate):

1. TIER TABLE per module: capabilities, MCP tools by derivability tier
   (T1/T2/T3 from matrix.derivability_tier), CLI verbs.  `matrix.py` prints
   tiers only in aggregate; this is the bespoke pass BT-6 §2 cites.

2. SLOC (`--sloc FILE...`): ONE counter, applied identically BEFORE and
   AFTER, over every production file the pilot touches, on a `ruff format`ed
   TEMP copy.  A line counts when it is non-blank and non-comment — DOCSTRINGS
   AND STRING LITERALS INCLUDED (third review pass: excluding docstrings made
   a relocated docstring count 2 before / 11-14 after).  The headline
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


def _normalized_source(path: str) -> str:
    """Source after `ruff format` (line-length 100) in a TEMP copy — never in place.

    Two attackers measured a 7x swing in the count from layout alone (one
    declaration on one line vs. formatted). The repo gates `ruff check`, not
    `ruff format`, so the counter normalizes BOTH sides itself. If ruff is not
    reachable the raw text is used and that is PRINTED, never silent.
    """
    src = open(path, encoding="utf-8").read()
    if not path.endswith(".py"):
        raise SystemExit(f"refusing non-Python file (a JSON blob counts as 1 line): {path}")
    import shutil
    import subprocess
    import tempfile

    ruff = shutil.which("ruff")
    if ruff is None:
        print(f"  WARNING: ruff not found; counting {path} UNFORMATTED")
        return src
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
        tf.write(src)
        tmp = tf.name
    try:
        subprocess.run([ruff, "format", "--line-length", "100", "-q", tmp], check=True)
        return open(tmp, encoding="utf-8").read()
    finally:
        os.unlink(tmp)


def nbnc_lines(src: str) -> set[int]:
    """Non-blank, non-comment physical lines. DOCSTRINGS AND STRING LITERALS COUNT.

    Counting docstrings is the whole point: three review passes showed that any
    counter which excludes a docstring statement but counts the same text once
    it becomes `fn.__doc__ = ...` or a declaration field books a pure
    relocation as growth (measured: 2 vs 11 vs 14 lines for identical text).
    A line is counted when it carries any token other than COMMENT/NL/NEWLINE/
    INDENT/DEDENT/ENCODING/ENDMARKER — string tokens included, spanning lines.
    """
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}
    out: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in skip:
            continue
        out.update(range(tok.start[0], tok.end[0] + 1))
    return out


def sloc_of(src: str, *, lo: int = 1, hi: int | None = None) -> int:
    lines = nbnc_lines(src)
    if hi is not None:
        lines = {ln for ln in lines if lo <= ln <= hi}
    return len(lines)


def sloc_report(paths: list[str], *, expect_absent: set[str]) -> None:
    """Same file list both sides. A missing file counts 0 ONLY if named in
    --absent; an unexpected absence is refused (typo vs. intentional)."""
    total = 0
    print(f"{'file':48}{'sloc':>7}")
    for p in paths:
        if not os.path.exists(p):
            if p in expect_absent:
                print(f"{p + '  (absent, expected)':48}{0:>7}")
                continue
            raise SystemExit(f"unexpected absent file (name it in --absent if intended): {p}")
        n = sloc_of(_normalized_source(p))
        total += n
        print(f"{p:48}{n:>7}")
    print(f"{'TOTAL':48}{total:>7}")
    print("  headline = NET delta of TOTAL between the pinned base and the pilot tip, same file list")


def declared_manual_handlers(path: str) -> list[str]:
    """Extract `manual_handler=<Name>` from a declaration file by AST.

    Refuses anything that is not a bare Name (lambda, attribute, partial,
    call): H_manual must point at a def the instrument can measure, or the
    number is self-reported rather than measured.
    """
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "manual_handler":
                    if isinstance(kw.value, ast.Name):
                        names.append(kw.value.id)
                    else:
                        raise SystemExit(
                            f"{path}:{kw.value.lineno}: manual_handler must be a bare Name, "
                            f"got {type(kw.value).__name__}"
                        )
    return names


def handlers_report(path: str, names: list[str]) -> None:
    src = _normalized_source(path)
    tree = ast.parse(src, filename=path)
    spans: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            if node.name in spans:
                raise SystemExit(f"duplicate def name {node.name!r} in {path}: qualify or rename")
            spans[node.name] = (M.def_start(node), M.end_of(node))
    missing = [n for n in names if n not in spans]
    if missing:
        raise SystemExit(f"declared manual handler(s) not found: {missing}")
    # Overlap check: a nested def inside another listed def would be counted
    # twice (measured: register_cli 152 + its four nested _cmd_* 118 = 330).
    items = sorted(spans.items(), key=lambda kv: kv[1])
    for (a, (a0, a1)), (b, (b0, b1)) in zip(items, items[1:]):
        if b0 <= a1:
            raise SystemExit(f"spans overlap: {a} [{a0}-{a1}] contains/abuts {b} [{b0}-{b1}]")
    print(f"{'handler':40}{'sloc':>7}")
    total = 0
    for n in names:
        lo, hi = spans[n]
        v = sloc_of(src, lo=lo, hi=hi)
        total += v
        print(f"{n:40}{v:>7}")
    print(f"{'H_manual':40}{total:>7}")


def surface_report(path: str, exclude: list[str]) -> None:
    """SLOC of register_tools + register_cli MINUS listed nested handlers —
    the 'wiring' figure with H_manual subtracted, never overlapping it."""
    src = _normalized_source(path)
    tree = ast.parse(src, filename=path)
    reg: dict[str, tuple[int, int]] = {}
    ex: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name in ("register_tools", "register_cli"):
                reg[node.name] = (M.def_start(node), M.end_of(node))
            elif node.name in exclude:
                ex.append((M.def_start(node), M.end_of(node)))
    lines = nbnc_lines(src)
    total = 0
    print(f"{'block':40}{'sloc':>7}")
    for name, (lo, hi) in reg.items():
        inside = {ln for ln in lines if lo <= ln <= hi}
        for e0, e1 in ex:
            inside -= {ln for ln in inside if e0 <= ln <= e1}
        total += len(inside)
        print(f"{name:40}{len(inside):>7}")
    print(f"{'wiring (excl. manual handlers)':40}{total:>7}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join("src", "codebugs"))
    ap.add_argument("--module", help="module file stem to partition (secondary view), e.g. bench")
    ap.add_argument("--sloc", nargs="+", metavar="FILE", help="SLOC per file + total (headline)")
    ap.add_argument("--absent", nargs="*", default=[], help="files allowed to be absent (count 0)")
    ap.add_argument("--handlers", help="comma-separated manual handler names (H_manual)")
    ap.add_argument("--declarations", help="declaration file: extract manual_handler= names by AST")
    ap.add_argument("--in", dest="in_file", help="file to resolve handlers in")
    ap.add_argument("--surface", action="store_true",
                    help="with --in: SLOC of register_* minus the handlers named by --handlers")
    args = ap.parse_args()
    if args.sloc:
        print("SLOC (one counter, before == after; NBNC lines incl. docstrings; ruff-formatted copy)")
        sloc_report(args.sloc, expect_absent=set(args.absent))
        return 0
    if args.handlers or args.declarations:
        if not args.in_file:
            ap.error("--handlers/--declarations need --in FILE")
        names = [n.strip() for n in (args.handlers or "").split(",") if n.strip()]
        if args.declarations:
            names += declared_manual_handlers(args.declarations)
        if args.surface:
            surface_report(args.in_file, names)
        else:
            handlers_report(args.in_file, names)
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
