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

5. DECLARATION GRAMMAR LINT (`--lint-declarations FILE`) plus the interpreter
   pin (`--require-python X.Y[.Z]`, and `--sloc` refuses without one).  The
   PROSE column is invariant only inside a restricted grammar; the lint REFUSES
   outside it.  Ratified 2026-08-22 (Э-11, option (А)) after five review passes
   showed that no counter is representation-neutral on its own.

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


# --------------------------------------------------------------------------
# 5. DECLARATION GRAMMAR LINT (`--lint-declarations FILE`) — ratified 2026-08-22
#    (Э-11, option (А)).  The PROSE column is invariant only for a verbatim
#    string literal; five review passes measured three forms that break it
#    (implicit concatenation, escaped `\n`, f-strings).  Fixing the counter was
#    the wrong move four times; restricting the GRAMMAR of the one authored file
#    is the decision the owner ratified.  This lint is that restriction, and it
#    REFUSES — it does not warn, because a warned-past file yields a number that
#    looks like every other number.
#
#    The guarantee it buys, stated as the sentence it proves: FOR EVERY STRING
#    LITERAL IN THIS FILE, THE NUMBER OF `\n`-SEGMENTS THE COUNTER SEES EQUALS
#    THE NUMBER OF NEWLINES IN THE VALUE AT RUNTIME, AND EACH LITERAL IS
#    ATTRIBUTED EXACTLY ONCE.
#
#    Four rules.  R1-R3 are token-level (the forms measured to break the
#    invariant); R4 is the structural one that closes the class rather than the
#    three known members — a check that validates elements cannot validate their
#    composition, so the composition is what R4 states.
# --------------------------------------------------------------------------

_CONTAINER_CALLS = frozenset({"dict", "list", "tuple", "set", "frozenset"})


def _string_prefix(text: str) -> str:
    return text[: len(text) - len(text.lstrip("rRbBuUfF"))].lower()


def _escapes_in(text: str) -> bool:
    """A backslash in a NON-RAW literal.  Deliberately wider than the newline
    escape alone: the hex, named-codepoint and octal spellings of a line feed,
    and a trailing line continuation, all produce a newline the counter cannot
    see, and enumerating the spellings is the letter, not the intent.
    """
    prefix = _string_prefix(text)
    if "r" in prefix:
        return False
    body = text[len(prefix) :]
    return "\\" in body


def _lint_tokens(src: str, path: str) -> list[str]:
    """R1 f-strings, R2 implicit concatenation, R3 escapes in non-raw literals."""
    bad: list[str] = []
    prev_string_end: tuple[int, int] | None = None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in _FSTRING_TYPES:
            if tok.type == getattr(tokenize, "FSTRING_START", -1):
                bad.append(f"{path}:{tok.start[0]}: R1 f-string literal")
            continue
        if tok.type == tokenize.STRING:
            if "f" in _string_prefix(tok.string):
                bad.append(f"{path}:{tok.start[0]}: R1 f-string literal")
            if _escapes_in(tok.string):
                bad.append(f"{path}:{tok.start[0]}: R3 backslash escape in a non-raw literal")
            if prev_string_end is not None:
                bad.append(
                    f"{path}:{tok.start[0]}: R2 implicit concatenation with the literal at "
                    f"line {prev_string_end[0]} (ruff format MERGES these: measured 8 -> 2)"
                )
            prev_string_end = tok.start
            continue
        if tok.type in _SKIP:
            continue
        prev_string_end = None
    return bad


_ALLOWED_NODES: tuple[type, ...] = (
    ast.Module, ast.Expr, ast.Assign, ast.AnnAssign, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set, ast.keyword,
    ast.Import, ast.ImportFrom, ast.alias, ast.Call,
)


def _lint_structure(src: str, path: str) -> list[str]:
    """R4: the file may contain ONLY data.

    THE SHAPE OF THIS RULE IS THE RULE.  Its first version walked UP from every
    string literal and named the ancestors it refused -- an ENUMERATION, in the
    very function whose comment claimed to close a class by composition.  Both
    reviewers of the sixth pass walked straight past it, and the holder
    reproduced them: a string with NO literal to walk up from is invisible to
    that direction of travel.  Measured, rc 0 on both:

        doc=chr(65) + chr(10) + chr(66)      prose 1, runtime 2 lines
        doc=RAW[:5]                          prose 4, runtime 1 line

    So the rule now travels DOWN over every node and admits an allowlist:
    assignments, names, container literals, keyword arguments, imports, and a
    call whose callee is a bare container constructor.  Nothing that can COMPUTE
    a string survives -- no operator, no attribute, no subscript, no
    comprehension, no conditional, no lambda, no function or class body -- so a
    prose field is a literal or the file is refused.  `bytes` is refused with
    the same sentence: the counter books a bytes token as prose, and no
    declarations file has a use for one."""
    tree = ast.parse(src, filename=path)
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, bytearray)):
            bad.append(f"{path}:{node.lineno}: R4 bytes literal (the counter books it as prose)")
            continue
        if isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Name) and func.id in _CONTAINER_CALLS):
                where = getattr(node, "lineno", "?")
                bad.append(
                    f"{path}:{where}: R4 call to something other than a container constructor "
                    f"({sorted(_CONTAINER_CALLS)}) — it could compute a string the counter cannot see"
                )
            continue
        if not isinstance(node, _ALLOWED_NODES):
            where = getattr(node, "lineno", "?")
            bad.append(
                f"{path}:{where}: R4 {type(node).__name__} is not admitted — a declarations file "
                f"holds DATA only (no operator, attribute, subscript, comprehension, "
                f"conditional, lambda, def or class)"
            )
    return bad


def lint_declarations(path: str) -> int:
    """Refuse a declarations file whose prose the counter cannot read invariantly.

    Returns the process exit code.  Loud on both outcomes: a silent pass would be
    indistinguishable from a lint that was never run, which is the failure this
    repository keeps re-filing."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    bad = _lint_tokens(src, path) + _lint_structure(src, path)
    if bad:
        print(f"DECLARATION GRAMMAR: REFUSED — {len(bad)} violation(s) in {path}")
        for line in sorted(set(bad)):
            print(f"  {line}")
        print("  the PROSE column is meaningless outside the restricted grammar (BT-6 v6, Э-11 (А))")
        return 1
    print(f"DECLARATION GRAMMAR: OK — {path} (R1 no f-strings, R2 no implicit concatenation,")
    print("  R3 no escapes in non-raw literals, R4 literals reached through containers only)")
    return 0


def require_python(spec: str) -> None:
    """Pin the interpreter, and REFUSE on a mismatch.

    At least MAJOR.MINOR is required: the first version accepted a bare `3`,
    which matched every interpreter and printed `pin OK` -- a guard reporting
    clean because it could not look, which is the exact shape this instrument
    exists to refuse.  Reproduced by the sixth pass and by the holder.  The
    counter is version-sensitive (f-string tokenization changed in 3.12), so a
    number carried across versions is one object counted two ways."""
    parts = spec.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts) or len(parts) > 3:
        raise SystemExit(
            f"interpreter pin: {spec!r} is not a version — give at least MAJOR.MINOR "
            f"(a bare major matches every interpreter and pins nothing)"
        )
    actual = ".".join(str(n) for n in sys.version_info[:3])
    want = [int(p) for p in parts]
    have = list(sys.version_info[: len(want)])
    if have != want:
        raise SystemExit(
            f"interpreter pin: required {spec}, running {actual} — refusing "
            f"(the count is version-sensitive; run both sides on one interpreter)"
        )
    print(f"  interpreter pin OK: required {spec}, running {actual}")


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
    ap.add_argument("--lint-declarations", dest="lint_decl", metavar="FILE",
                    help="REFUSE a declarations file outside the restricted prose grammar "
                         "(BT-6 v6 / E-11 option A): R1 no f-strings, R2 no implicit "
                         "concatenation, R3 no escapes in non-raw literals, R4 literals "
                         "reached through containers only")
    ap.add_argument("--require-python", metavar="X.Y[.Z]",
                    help="refuse unless the running interpreter matches (dotted prefix)")
    ap.add_argument("--no-pin", action="store_true",
                    help="run --sloc without an interpreter pin: a NAMED opt-out, because an "
                         "unpinned count silently compares two tokenizers")
    args = ap.parse_args()
    if args.require_python:
        require_python(args.require_python)
    if args.lint_decl:
        # Compose, never shadow: with --sloc alongside, the lint used to return
        # first and the measurement was silently skipped — and the two are meant
        # to be ONE protocol step (lint the declarations file, then count).
        if args.sloc:
            # The linted file must be one of the files being counted, or the
            # protocol proves nothing: lint A, measure B is a green light for an
            # unlinted number.  Same shape as the pin — a step that can be
            # satisfied beside the object it is about is not a step.
            want = os.path.realpath(args.lint_decl)
            if want not in {os.path.realpath(f) for f in args.sloc}:
                raise SystemExit(
                    f"--lint-declarations {args.lint_decl} is not among the --sloc files: "
                    f"refusing (linting one file and counting another proves nothing)"
                )
        rc = lint_declarations(args.lint_decl)
        if rc != 0 or not args.sloc:
            return rc
    if args.sloc:
        if not args.require_python and not args.no_pin:
            # A protocol that says "pass the pin on both sides" is a convention,
            # and a convention is not a rule (this repo's own Н3).  Refuse.
            ap.error("--sloc needs --require-python X.Y[.Z] (or an explicit --no-pin): "
                     "the count is version-sensitive and the two sides must be one interpreter")
        print("SLOC = code + prose (one counter, before == after; ruff-formatted copy)")
        sloc_report(args.sloc, expect_absent=set(args.absent))
        return 0
    if args.handlers is not None or args.declarations or args.surface:
        if not args.in_file:
            ap.error("--handlers/--declarations/--surface need --in FILE")
        print(f"  interpreter {sys.version.split()[0]} — the count is version-sensitive "
              f"(f-string tokens); pin it with --require-python on both sides")
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
