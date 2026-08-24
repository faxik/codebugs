#!/usr/bin/env python3
"""CB-157 population survey: does every DECLARED MCP parameter reach the body?

Run: PYTHONPATH=src python tests/manual/measure_cb157_forwarding.py

Measures TWO predicates over every `@mcp.tool()`-decorated function in
`src/codebugs/`, because the delta between them is exactly the population of
"legitimately does not reach the domain call" that the brief (§3) warns about:

  P_read      the parameter name is LOADED at least once anywhere in the body.
              SOUND: a name the body never loads provably cannot affect what
              the tool does, so a violation is a defect with no exceptions.
  P_forwarded the parameter is passed as a keyword argument to some call in the
              body, under its own name. STRICT: renaming at the boundary,
              wrapper-consumed flags and **kwargs all violate it legitimately.

The M11 mutant of the card (hardcode `include_unanchored=False` in the body of
`anchor_recapture` while leaving it in the signature) violates BOTH.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "codebugs"


def _is_mcp_tool(fn: ast.FunctionDef) -> bool:
    """True for `@mcp.tool()` / `@mcp.tool` decorated definitions."""
    for dec in fn.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(node, ast.Attribute) and node.attr == "tool":
            return True
    return False


def _declared_params(fn: ast.FunctionDef) -> list[str]:
    a = fn.args
    names = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return names


def _loaded_names(fn: ast.FunctionDef) -> set[str]:
    """Every name the body READS, excluding nested tool definitions."""
    out: set[str] = set()
    for node in ast.walk(ast.Module(body=list(fn.body), type_ignores=[])):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            out.add(node.id)
    return out


def _dynamic_escape(fn: ast.FunctionDef) -> bool:
    """`locals()` / `eval` / `vars()` would defeat a static read check."""
    for node in ast.walk(ast.Module(body=list(fn.body), type_ignores=[])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"locals", "vars", "eval", "exec"}:
                return True
    return False


def _forwarded_names(fn: ast.FunctionDef) -> set[str]:
    """Params passed as `name=name` keywords, or splatted via `**name`."""
    out: set[str] = set()
    for node in ast.walk(ast.Module(body=list(fn.body), type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg is None:
                if isinstance(kw.value, ast.Name):
                    out.add(kw.value.id)
            elif isinstance(kw.value, ast.Name) and kw.value.id == kw.arg:
                out.add(kw.arg)
    return out


def _call_argument_names(fn: ast.FunctionDef) -> set[str]:
    """Params handed DIRECTLY to some call, positionally or by keyword.

    Strictly weaker than `_forwarded_names` (it forgives renaming at the
    boundary and positional passing) and strictly stronger than "the body reads
    the name" (a value read into a local and then dropped does not qualify).
    """
    out: set[str] = set()
    for node in ast.walk(ast.Module(body=list(fn.body), type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name):
                out.add(arg.id)
            elif isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                out.add(arg.value.id)
        for kw in node.keywords:
            if isinstance(kw.value, ast.Name):
                out.add(kw.value.id)
    return out


def survey() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not _is_mcp_tool(node):
                continue
            loaded = _loaded_names(node)
            forwarded = _forwarded_names(node)
            argnames = _call_argument_names(node)
            rows.append(
                {
                    "tool": node.name,
                    "module": str(path.relative_to(SRC)),
                    "line": node.lineno,
                    "dynamic_escape": _dynamic_escape(node),
                    "params": [
                        {
                            "name": p,
                            "read": p in loaded,
                            "forwarded": p in forwarded,
                            "call_arg": p in argnames,
                        }
                        for p in _declared_params(node)
                    ],
                }
            )
    return rows


def main() -> None:
    rows = survey()
    params = [(r, p) for r in rows for p in r["params"]]
    unread = [(r, p) for r, p in params if not p["read"]]
    unforwarded = [(r, p) for r, p in params if not p["forwarded"]]
    noncall = [(r, p) for r, p in params if not p["call_arg"]]
    print(f"tools:              {len(rows)}")
    print(f"declared params:    {len(params)}")
    print(f"read by the body:   {len(params) - len(unread)}")
    print(f"NOT read (defects): {len(unread)}")
    print(f"forwarded as kw:    {len(params) - len(unforwarded)}")
    print(f"NOT forwarded:      {len(unforwarded)}  <- read-but-not-forwarded = legitimate pop.")
    print(f"reaches a call:     {len(params) - len(noncall)}")
    print(f"NOT a call arg:     {len(noncall)}")
    print(f"dynamic escapes:    {sum(1 for r in rows if r['dynamic_escape'])}")
    print()
    print("--- NOT READ (a body that never loads the name cannot honour it) ---")
    for r, p in unread:
        print(f"  {r['module']}:{r['line']} {r['tool']}({p['name']})")
    if not unread:
        print("  (none)")
    print()
    print("--- READ BUT NEVER HANDED TO A CALL (renamed / wrapper-consumed) ---")
    for r, p in noncall:
        print(f"  {r['module']}:{r['line']} {r['tool']}({p['name']})")
    if not noncall:
        print("  (none)")
    print()
    print("--- READ BUT NOT FORWARDED BY ITS OWN NAME (the §3 population) ---")
    for r, p in unforwarded:
        if p["read"]:
            print(f"  {r['module']}:{r['line']} {r['tool']}({p['name']})")
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
