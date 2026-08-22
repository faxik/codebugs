#!/usr/bin/env python3
"""Per-module surface measurement for BT-6 (surface-generator pilot).

Two reports, both computed, neither hand-written (BT-1/BT-3 lesson: a claim
about what a predicate sees is produced by RUNNING the predicate):

1. TIER TABLE per module: capabilities, MCP tools by derivability tier
   (T1/T2/T3 from matrix.derivability_tier), CLI verbs.  `matrix.py` prints
   tiers only in aggregate; this is the bespoke pass BT-6 §2 cites.

2. SLOC (`--sloc FILE...`): ONE counter, applied identically BEFORE and
   AFTER, over every production file the pilot touches, on a `ruff format`ed
   TEMP copy, reported as TWO columns: CODE (lines with a non-string token)
   and PROSE (newline segments inside string tokens). See `measure()` for
   why: every single-number variant tried (v2 partition, v3 no-docstrings,
   v4 NBNC) charged a relocated prose block differently depending on its
   surrounding syntax; the split bounds that to <=2 code lines per block.  The headline
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

    Two attackers measured a 7x swing in the count from layout alone. The repo
    gates `ruff check`, not `ruff format`, so the counter normalizes BOTH sides
    itself. If ruff is unavailable or fails the counter REFUSES: a fourth pass
    measured 917 raw vs 931 formatted on bench.py, so a silent fallback would
    switch metrics mid-measurement.
    """
    if not path.endswith(".py"):
        raise SystemExit(f"refusing non-Python file (a JSON blob counts as 1 line): {path}")
    src = open(path, encoding="utf-8").read()
    import shutil
    import subprocess
    import tempfile

    ruff = shutil.which("ruff")
    if ruff is None:
        raise SystemExit("ruff not found: refusing to count unformatted source (metric would change)")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
        tf.write(src)
        tmp = tf.name
    try:
        r = subprocess.run([ruff, "format", "--line-length", "100", "-q", tmp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"ruff format failed on {path}: {r.stderr.strip()}")
        return open(tmp, encoding="utf-8").read()
    finally:
        os.unlink(tmp)


_SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
         tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}


def measure(src: str, *, lo: int = 1, hi: int | None = None,
            keep=None) -> tuple[int, int]:
    """(code, prose) over the lines selected by [lo, hi] and/or `keep(ln)`.

    CODE  = physical lines carrying at least one token that is neither
            comment/whitespace NOR a string literal.  A docstring statement is
            0 code lines; `description="..."` is 1 (the field name IS code);
            `fn.__doc__ = "..."` is 1.  So relocating a prose block between
            those three forms moves CODE by at most 2 lines per block (the key line
            and a closing-punctuation line: a triple-quote followed by a comma) — measured 2/3/7 with
            the surrounding `dict(...)` accounting for the rest — a bound,
            not a hope, and those lines are genuine declaration syntax.
    PROSE = newline-separated segments inside string tokens (a 6-line
            docstring = 6; the same 6 lines as a field value = 6; as an
            assignment = 6), attributed to the token's START line so a
            multi-line string is counted once.  Invariant ONLY for a verbatim
            triple-quoted literal moved between those forms (measured 7/7/7).
            NOT invariant under implicit concatenation (`ruff format` MERGES
            adjacent literals: 6 -> 1), escaped `\\n` in a one-line string
            (counts real newlines only: 6 -> 1), or f-strings (skipped on
            3.12+: 6 -> 0). Fifth review pass measured all three. A declaration
            grammar that forbids those three forms is the precondition for the
            number to mean anything; that is a DECISION, not an instrument fix.  Empty
            segments (blank lines inside a string) count — the author wrote them.
    Fourth review pass measured the previous NBNC counter at 8 vs 6 (Opus) and
    2/5/1 (Codex) for byte-identical prose in three forms; this split is the
    structural answer, and the headline reports BOTH plus their sum.
    """
    def _in(ln: int) -> bool:
        if hi is not None and not (lo <= ln <= hi):
            return False
        return keep(ln) if keep else True

    code: set[int] = set()
    prose = 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in _SKIP:
            continue
        if tok.type == tokenize.STRING:
            if _in(tok.start[0]):
                prose += tok.string.count("\n") + 1
            continue
        if tok.type in _FSTRING_TYPES:
            continue
        code.update(ln for ln in range(tok.start[0], tok.end[0] + 1) if _in(ln))
    return len(code), prose


_FSTRING_TYPES = {getattr(tokenize, n) for n in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
                  if hasattr(tokenize, n)}


def sloc_of(src: str, *, lo: int = 1, hi: int | None = None) -> int:
    c, _p = measure(src, lo=lo, hi=hi)
    return c


def sloc_report(paths: list[str], *, expect_absent: set[str]) -> None:
    """Same file list both sides. A missing file counts 0 ONLY if named in
    --absent; an unexpected absence is refused (typo vs. intentional)."""
    tc = tp = 0
    print(f"  interpreter {sys.version.split()[0]} — use the SAME one on both sides "
          f"(tokenizer differs across versions on f-strings)")
    print(f"{'file':44}{'code':>7}{'prose':>7}{'total':>7}")
    for p in paths:
        if not os.path.exists(p):
            if p in expect_absent:
                print(f"{p + '  (absent, expected)':44}{0:>7}{0:>7}{0:>7}")
                continue
            raise SystemExit(f"unexpected absent file (name it in --absent if intended): {p}")
        c, pr = measure(_normalized_source(p))
        tc += c
        tp += pr
        print(f"{p:44}{c:>7}{pr:>7}{c + pr:>7}")
    print(f"{'TOTAL':44}{tc:>7}{tp:>7}{tc + tp:>7}")
    print("  headline = NET delta of each column between the pinned base and the pilot tip")
    print("  (relocating a prose block moves `code` by <= 2 lines per block, `prose` by 0)")


def declared_manual_handlers(path: str) -> list[str]:
    """Extract `manual_handler=<Name>` from a declaration file by AST.

    Refuses anything that is not a bare Name (lambda, attribute, partial,
    call), a non-.py file, and a name declared twice.
    """
    if not path.endswith(".py"):
        raise SystemExit(f"declaration file must be .py: {path}")
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "manual_handler":
                    if not isinstance(kw.value, ast.Name):
                        raise SystemExit(
                            f"{path}:{kw.value.lineno}: manual_handler must be a bare Name, "
                            f"got {type(kw.value).__name__}"
                        )
                    if kw.value.id in names:
                        raise SystemExit(f"{path}: manual_handler {kw.value.id!r} declared twice")
                    names.append(kw.value.id)
    return names


def _resolve_spans(src: str, path: str, names: list[str]) -> dict[str, tuple[int, int]]:
    """ONE validation path for --handlers and --surface: missing, duplicate
    (in the input list or in the file), and overlapping spans all refuse.
    The fourth pass found --surface accepting a missing name (165 -> 184, rc 0)
    because validation lived only in the other mode."""
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate handler name in input: {names}")
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
    items = sorted(spans.items(), key=lambda kv: kv[1])
    for (a, (a0, a1)), (b, (b0, b1)) in zip(items, items[1:]):
        if b0 <= a1:
            raise SystemExit(f"spans overlap: {a} [{a0}-{a1}] contains/abuts {b} [{b0}-{b1}]")
    return spans


def handlers_report(path: str, names: list[str]) -> None:
    src = _normalized_source(path)
    spans = _resolve_spans(src, path, names)
    print(f"{'handler':40}{'code':>7}{'prose':>7}")
    tc = tp = 0
    for n in names:
        lo, hi = spans[n]
        c, pr = measure(src, lo=lo, hi=hi)
        tc += c
        tp += pr
        print(f"{n:40}{c:>7}{pr:>7}")
    print(f"{'H_manual':40}{tc:>7}{tp:>7}")


def surface_report(path: str, exclude: list[str]) -> None:
    """register_tools + register_cli MINUS the named handler spans (which must
    resolve — same validation as --handlers). The declared drop of this figure
    is what makes 'reduced to a loop' measurable (T_second bar)."""
    src = _normalized_source(path)
    ex = _resolve_spans(src, path, exclude) if exclude else {}
    tree = ast.parse(src, filename=path)
    reg: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("register_tools", "register_cli"):
            if node.name in reg:
                raise SystemExit(f"duplicate {node.name} in {path}: refusing")
            reg[node.name] = (M.def_start(node), M.end_of(node))
    if not reg:
        raise SystemExit(f"no register_tools/register_cli in {path}")
    exl: set[int] = set()
    for e0, e1 in ex.values():
        exl.update(range(e0, e1 + 1))
    tc = tp = 0
    print(f"{'block':40}{'code':>7}{'prose':>7}")
    for name, (lo, hi) in reg.items():
        c, pr = measure(src, lo=lo, hi=hi, keep=lambda ln: ln not in exl)
        tc += c
        tp += pr
        print(f"{name:40}{c:>7}{pr:>7}")
    print(f"{'wiring (excl. manual handlers)':40}{tc:>7}{tp:>7}")


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
        print("SLOC = code + prose (one counter, before == after; ruff-formatted copy)")
        sloc_report(args.sloc, expect_absent=set(args.absent))
        return 0
    if args.handlers is not None or args.declarations or args.surface:
        if not args.in_file:
            ap.error("--handlers/--declarations/--surface need --in FILE")
        print(f"  interpreter {sys.version.split()[0]} — the count is version-sensitive "
              f"(f-string tokens; measured 42-line skew 3.11 vs 3.14); pin it on both sides")
        names = [n.strip() for n in (args.handlers or "").split(",") if n.strip()]
        if args.handlers is not None and not names:
            # An empty list from a shell variable that expanded to nothing once
            # printed the TIER TABLE at rc 0 and, with --surface, a 2x-wrong
            # figure (230 for 110). Fifth review pass. Refuse.
            raise SystemExit("--handlers given but empty: refusing (would silently un-exclude)")
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
