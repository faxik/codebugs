#!/usr/bin/env python3
"""Capability x surface matrix for codebugs, computed by AST pass.

WHAT THIS ANSWERS
    For every DOMAIN CAPABILITY (a public module-level function whose first
    parameter is `conn`), which SURFACES reach it: the MCP tool layer, the CLI
    layer, both, or neither. Plus the inverse: which MCP tools and CLI commands
    reach no domain capability at all (they carry their own logic), which is
    where an exposure layer would NOT be a mechanical win.

WHY THIS MUST BE COMPUTED AND NOT WRITTEN BY HAND
    This direction burned four document revisions on BT-1 asserting what a
    predicate sees WITHOUT RUNNING THE PREDICATE, and was falsified every time.
    The rule that came out of it: a claim about coverage is produced by RUNNING,
    never by reasoning. A hand-written matrix also rots between authoring and
    review -- this one is a command.

UNIT OF COUNT, stated because getting it wrong is this repo's recurring failure
    A CAPABILITY is a domain function, NOT a tool and NOT a subcommand. One
    capability may carry two MCP tools and three CLI verbs, or none. Counting
    tools would measure the surface, which is the thing we are trying to explain,
    not the thing being exposed.

SURFACE DETECTION
    MCP : an inner FunctionDef inside `register_tools(mcp, conn_factory)`
          decorated with `@mcp.tool(...)`.
    CLI : `sub.add_parser("<verb>")` inside `register_cli(sub, commands)`, joined
          to its handler by `commands["<verb>"] = <handler>`.
    Both layers are then linked to domain capabilities by resolving the calls in
    their bodies, using each module's import map, falling back to a unique
    package-wide name match, and REPORTING ambiguity rather than guessing.

BLINDNESS, printed rather than hidden
    Dynamic registration, calls through variables, and re-exported aliases are
    not resolved. Every unresolved call is counted and listed under BLIND SPOTS.

USAGE
    python3 .claude/plans/exposure-scripts/matrix.py
    python3 .claude/plans/exposure-scripts/matrix.py --root src/codebugs
    python3 .claude/plans/exposure-scripts/matrix.py --check   # self-check only

Writes nothing anywhere; prints to stdout; stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


@dataclass
class Capability:
    """A public domain function taking `conn` as its first parameter."""

    module: str
    name: str
    lineno: int
    endline: int
    params: list[str]
    has_doc: bool

    @property
    def qual(self) -> str:
        return f"{self.module}.{self.name}"

    @property
    def loc(self) -> int:
        return self.endline - self.lineno + 1


@dataclass
class Surface:
    """One MCP tool or one CLI verb."""

    kind: str  # "mcp" | "cli"
    module: str
    name: str  # tool name or CLI verb
    lineno: int
    endline: int
    params: list[str]
    has_doc: bool
    handler: str | None = None  # CLI only: the _cmd_* function name
    node: object | None = None  # the def whose body we resolve calls in
    calls: set[str] = field(default_factory=set)  # resolved capability quals
    unresolved: set[str] = field(default_factory=set)

    @property
    def loc(self) -> int:
        return self.endline - self.lineno + 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def module_name(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    rel = rel[:-3] if rel.endswith(".py") else rel
    parts = [p for p in rel.split(os.sep) if p != "__init__"]
    return ".".join(parts) if parts else "__init__"


def end_of(node: ast.AST) -> int:
    return getattr(node, "end_lineno", None) or getattr(node, "lineno", 0)


def decorator_is_mcp_tool(dec: ast.AST) -> bool:
    """Match `@mcp.tool()` and `@mcp.tool`."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "tool"
        and isinstance(target.value, ast.Name)
        and target.value.id == "mcp"
    )


def mcp_tool_name(fn: ast.FunctionDef) -> str:
    """The REGISTERED tool name, which `@mcp.tool(name="x")` may override.

    Ten tools in this package rename themselves this way (the milestones spec
    mandates spec-canonical names, so the inner def is `_wip_status` while the
    wire name is `wip_status`). Reporting the def name would name a tool no
    client can call -- a matrix that is wrong about the wire is worthless.
    """
    for dec in fn.decorator_list:
        if not decorator_is_mcp_tool(dec) or not isinstance(dec, ast.Call):
            continue
        for kw in dec.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return fn.name


def body_shape(fn: ast.FunctionDef) -> tuple[int, int, bool]:
    """(docstring LOC, code LOC, is_mechanically_derivable) for a surface wrapper.

    THIS IS THE DECISIVE MEASUREMENT FOR THE EXPOSURE QUESTION, and the reason a
    raw LOC ratio would mislead. Most of an MCP wrapper is its DOCSTRING, and a
    docstring is USER-VISIBLE TEXT that a generator cannot delete -- it can only
    move it. CB-73 is the precedent: the SDK reads `Tool.description` from
    `__doc__`, and clients render it as Markdown. So "the surface layer is 3192
    LOC" is not "3192 LOC a generator could remove".

    MECHANICALLY DERIVABLE means the body, after the docstring, is exactly

        with conn_factory() as conn:
            return <one call>(conn, ...)

    and nothing else -- no validation, no branching, no reshaping of the result.
    Only these can be generated from a declaration. Everything else carries
    behaviour that would have to be written somewhere regardless.
    """
    stmts = list(fn.body)
    doc_loc = 0
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        d = stmts[0]
        doc_loc = end_of(d) - d.lineno + 1
        stmts = stmts[1:]

    code_loc = sum(end_of(x) - x.lineno + 1 for x in stmts)

    mech = False
    if len(stmts) == 1 and isinstance(stmts[0], ast.With) and len(stmts[0].items) == 1:
        item = stmts[0].items[0]
        ctx = item.context_expr
        ctx_ok = (
            isinstance(ctx, ast.Call)
            and isinstance(ctx.func, ast.Name)
            and ctx.func.id == "conn_factory"
        )
        inner = stmts[0].body
        inner_ok = (
            len(inner) == 1
            and isinstance(inner[0], ast.Return)
            and isinstance(inner[0].value, ast.Call)
        )
        mech = bool(ctx_ok and inner_ok)
    return doc_loc, code_loc, mech


def cli_handler_shape(fn: ast.FunctionDef) -> dict:
    """Anatomy of one CLI handler, because the CLI's duplication is NOT the whole
    handler -- it is the connect/close boilerplate and the except ladder.

    CB-55 names that ladder ("the JSONDecodeError-first re-raise arm is maintained
    by COPY across CLI handlers"), and CLAUDE.md's Error-handling section makes the
    ORDERING load-bearing: `json.JSONDecodeError` subclasses `ValueError`, so an
    arm in the wrong order reports a COMMITTED write as bad input. Copy-maintained
    ordering across N handlers is the defect; measuring N is the point.
    """
    out = {
        "connects": False,
        "try_finally_close": False,
        "has_except": False,
        "has_jsondecode_arm": False,
        "exits": False,
        "prints": 0,
    }
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            q, nm = call_name(node)
            if nm == "connect" and (q or "").endswith("db"):
                out["connects"] = True
            if nm == "print":
                out["prints"] += 1
            if nm == "exit" and (q or "").endswith("sys"):
                out["exits"] = True
        if isinstance(node, ast.Try):
            if node.finalbody:
                out["try_finally_close"] = True
            if node.handlers:
                out["has_except"] = True
            for h in node.handlers:
                for sub in ast.walk(h.type) if h.type else []:
                    if isinstance(sub, ast.Attribute) and sub.attr == "JSONDecodeError":
                        out["has_jsondecode_arm"] = True
                    if isinstance(sub, ast.Name) and sub.id == "JSONDecodeError":
                        out["has_jsondecode_arm"] = True
    return out


def param_names(fn: ast.FunctionDef) -> list[str]:
    a = fn.args
    return [x.arg for x in (a.posonlyargs + a.args + a.kwonlyargs)]


def call_name(node: ast.Call) -> tuple[str | None, str | None]:
    """Return (qualifier, name) for a call: foo() -> (None, 'foo');
    mod.foo() -> ('mod', 'foo'); a.b.foo() -> ('a.b', 'foo')."""
    f = node.func
    if isinstance(f, ast.Name):
        return None, f.id
    if isinstance(f, ast.Attribute):
        parts = []
        cur = f.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts)), f.attr
        return None, f.attr
    return None, None


def build_import_map(tree: ast.Module, module: str) -> dict[str, str]:
    """name-in-this-file -> module it came from (best effort, package-local)."""
    out: dict[str, str] = {}
    pkg_root = module.split(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import inside the package
                here = module.rsplit(".", 1)[0] if "." in module else ""
                base = f"{here}.{base}".strip(".") if base else here
            for alias in node.names:
                out[alias.asname or alias.name] = base
        elif isinstance(node, ast.Import):
            for alias in node.names:
                short = (alias.asname or alias.name).split(".")[-1]
                out[short] = alias.name
    out.setdefault(pkg_root, pkg_root)
    return out


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------


def collect(root: str):
    caps: dict[str, Capability] = {}
    by_short: dict[str, list[str]] = {}
    surfaces: list[Surface] = []
    handlers: dict[tuple[str, str], ast.FunctionDef] = {}
    imports: dict[str, dict[str, str]] = {}
    trees: dict[str, ast.Module] = {}
    files = 0

    for dirpath, _dirs, names in os.walk(root):
        for n in sorted(names):
            if not n.endswith(".py"):
                continue
            path = os.path.join(dirpath, n)
            mod = module_name(root, path)
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src, filename=path)
            trees[mod] = tree
            imports[mod] = build_import_map(tree, mod)
            files += 1

            # CLI handlers nest INSIDE register_cli in 9 of 12 modules and sit at
            # module level in the other 3. Collecting only module-level defs made
            # the CLI column read as empty for the majority -- caught by checking
            # the run against blockers.py, whose handlers ARE module-level and did
            # resolve. Walk the whole tree.
            for anynode in ast.walk(tree):
                if isinstance(anynode, ast.FunctionDef) and anynode.name.startswith("_cmd_"):
                    handlers[(mod, anynode.name)] = anynode

            for node in tree.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                # --- domain capability ---
                pars = param_names(node)
                if not node.name.startswith("_") and pars and pars[0] == "conn":
                    if node.name not in ("register_tools", "register_cli", "ensure_schema"):
                        c = Capability(
                            module=mod,
                            name=node.name,
                            lineno=node.lineno,
                            endline=end_of(node),
                            params=pars,
                            has_doc=ast.get_docstring(node) is not None,
                        )
                        caps[c.qual] = c
                        by_short.setdefault(c.name, []).append(c.qual)
                # --- MCP registrar ---
                if node.name == "register_tools":
                    for inner in ast.walk(node):
                        if not isinstance(inner, ast.FunctionDef):
                            continue
                        if not any(decorator_is_mcp_tool(d) for d in inner.decorator_list):
                            continue
                        surfaces.append(
                            Surface(
                                kind="mcp",
                                module=mod,
                                name=mcp_tool_name(inner),
                                lineno=inner.lineno,
                                endline=end_of(inner),
                                params=param_names(inner),
                                has_doc=ast.get_docstring(inner) is not None,
                                node=inner,
                            )
                        )
                # --- CLI registrar ---
                if node.name == "register_cli":
                    verbs: list[tuple[str, int, int]] = []
                    wired: dict[str, str] = {}
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.Call):
                            _q, nm = call_name(inner)
                            if (
                                nm == "add_parser"
                                and inner.args
                                and isinstance(inner.args[0], ast.Constant)
                                and isinstance(inner.args[0].value, str)
                            ):
                                verbs.append(
                                    (inner.args[0].value, inner.lineno, end_of(inner))
                                )
                        # shape 1: commands["verb"] = _cmd_x   (blockers, claims, relations)
                        if isinstance(inner, ast.Assign):
                            for tgt in inner.targets:
                                if (
                                    isinstance(tgt, ast.Subscript)
                                    and isinstance(tgt.value, ast.Name)
                                    and tgt.value.id == "commands"
                                    and isinstance(tgt.slice, ast.Constant)
                                    and isinstance(inner.value, ast.Name)
                                ):
                                    wired[tgt.slice.value] = inner.value.id
                        # shape 2: commands.update({...})  (the other nine modules)
                        if isinstance(inner, ast.Call):
                            q, nm = call_name(inner)
                            if nm == "update" and q == "commands":
                                for a in inner.args:
                                    if not isinstance(a, ast.Dict):
                                        continue
                                    for k, v in zip(a.keys, a.values):
                                        if (
                                            isinstance(k, ast.Constant)
                                            and isinstance(k.value, str)
                                            and isinstance(v, ast.Name)
                                        ):
                                            wired[k.value] = v.id
                    for verb, ln, en in verbs:
                        surfaces.append(
                            Surface(
                                kind="cli",
                                module=mod,
                                name=verb,
                                lineno=ln,
                                endline=en,
                                params=[],
                                has_doc=False,
                                handler=wired.get(verb),
                            )
                        )

    return caps, by_short, surfaces, handlers, imports, trees, files


def resolve_calls(
    body: ast.AST,
    mod: str,
    caps: dict[str, Capability],
    by_short: dict[str, list[str]],
    imports: dict[str, dict[str, str]],
) -> tuple[set[str], set[str]]:
    """Resolve calls in a body to capability quals. Returns (resolved, unresolved)."""
    hit: set[str] = set()
    miss: set[str] = set()
    imap = imports.get(mod, {})
    for node in ast.walk(body):
        if not isinstance(node, ast.Call):
            continue
        qual, nm = call_name(node)
        if not nm:
            continue
        # same-module first
        same = f"{mod}.{nm}"
        if same in caps:
            hit.add(same)
            continue
        # qualified: mod.fn where mod is an imported module
        if qual:
            tail = qual.split(".")[-1]
            target = imap.get(tail, tail)
            for cand in (f"{target}.{nm}", f"{tail}.{nm}"):
                if cand in caps:
                    hit.add(cand)
                    break
            else:
                cands = by_short.get(nm, [])
                if len(cands) == 1:
                    hit.add(cands[0])
                elif len(cands) > 1:
                    miss.add(f"{nm} (ambiguous: {len(cands)} capabilities share the name)")
            continue
        # bare name imported from somewhere
        cands = by_short.get(nm, [])
        if len(cands) == 1:
            hit.add(cands[0])
        elif len(cands) > 1:
            src_mod = imap.get(nm)
            picked = [c for c in cands if c.rsplit(".", 1)[0] == src_mod]
            if len(picked) == 1:
                hit.add(picked[0])
            else:
                miss.add(f"{nm} (ambiguous: {len(cands)} capabilities share the name)")
    return hit, miss


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

BAR = "=" * 100
DASH = "-" * 100


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join("src", "codebugs"))
    ap.add_argument("--check", action="store_true", help="print only the self-check")
    args = ap.parse_args()

    caps, by_short, surfaces, handlers, imports, _trees, files = collect(args.root)

    # link surfaces to capabilities
    for s in surfaces:
        if s.kind == "mcp":
            # Resolve against the def node captured at collection time. An earlier
            # draft re-parsed the file and matched on `node.name == s.name`, which
            # silently failed for every `@mcp.tool(name="x")` tool -- the def is
            # `_wip_status`, the wire name is `wip_status` -- and reported all ten
            # milestones tools as reaching no capability at all. Caught by checking
            # the run against CLAUDE.md, which says those tools exist and delegate.
            if s.node is not None:
                s.calls, s.unresolved = resolve_calls(
                    s.node, s.module, caps, by_short, imports
                )
        else:
            fn = handlers.get((s.module, s.handler or ""))
            if fn is not None:
                s.calls, s.unresolved = resolve_calls(fn, s.module, caps, by_short, imports)
                s.endline = end_of(fn)
                s.lineno = fn.lineno

    # invert: capability -> surfaces
    reach: dict[str, dict[str, list[str]]] = {
        q: {"mcp": [], "cli": []} for q in caps
    }
    for s in surfaces:
        for q in s.calls:
            reach[q][s.kind].append(s.name)

    mcp_tools = [s for s in surfaces if s.kind == "mcp"]
    cli_verbs = [s for s in surfaces if s.kind == "cli"]

    both, mcp_only, cli_only, neither = [], [], [], []
    for q, r in reach.items():
        if r["mcp"] and r["cli"]:
            both.append(q)
        elif r["mcp"]:
            mcp_only.append(q)
        elif r["cli"]:
            cli_only.append(q)
        else:
            neither.append(q)

    orphan_mcp = [s for s in mcp_tools if not s.calls]
    orphan_cli = [s for s in cli_verbs if not s.calls]

    if not args.check:
        print(BAR)
        print("CAPABILITY x SURFACE MATRIX  --  computed by AST, not by reading")
        print(BAR)
        print(f"root              : {args.root}")
        print(f"python            : {sys.version.split()[0]}")
        print(f"files scanned     : {files}")
        print(f"capabilities      : {len(caps)}   (public module-level fn, first param `conn`)")
        print(f"MCP tools         : {len(mcp_tools)}")
        print(f"CLI verbs         : {len(cli_verbs)}")
        print()
        print(DASH)
        print(f"{'capability':<52} {'MCP':<4} {'CLI':<4}  {'LOC':>4}  surfaces")
        print(DASH)
        for q in sorted(caps):
            r = reach[q]
            m = "yes" if r["mcp"] else "-"
            c = "yes" if r["cli"] else "-"
            names = ",".join(sorted(r["mcp"] + r["cli"])) or "(none)"
            if len(names) > 60:
                names = names[:57] + "..."
            print(f"{q:<52} {m:<4} {c:<4}  {caps[q].loc:>4}  {names}")
        print(DASH)
        print()

        print(BAR)
        print("GAPS  --  the four cells")
        print(BAR)
        for label, group in (
            ("BOTH surfaces", both),
            ("MCP only  (no CLI verb reaches it)", mcp_only),
            ("CLI only  (no MCP tool reaches it -- an MCP-only client cannot)", cli_only),
            ("NEITHER   (library-only; may be internal-by-design)", neither),
        ):
            print(f"\n{label}: {len(group)}")
            for q in sorted(group):
                print(f"      {q}   ({caps[q].module}, {caps[q].loc} LOC)")

        print()
        print(BAR)
        print("SURFACES THAT REACH NO CAPABILITY  --  where a generated layer would NOT help")
        print(BAR)
        print(f"\nMCP tools with no resolved domain call: {len(orphan_mcp)}")
        for s in sorted(orphan_mcp, key=lambda x: (x.module, x.name)):
            extra = f"  unresolved={sorted(s.unresolved)}" if s.unresolved else ""
            print(f"      {s.module}.{s.name}  ({s.loc} LOC){extra}")
        print(f"\nCLI verbs with no resolved domain call: {len(orphan_cli)}")
        for s in sorted(orphan_cli, key=lambda x: (x.module, x.name)):
            extra = f"  unresolved={sorted(s.unresolved)}" if s.unresolved else ""
            print(f"      {s.module}:{s.name} -> {s.handler}  ({s.loc} LOC){extra}")

        print()
        print(BAR)
        print("SIZE  --  the owner's 'improves the code AND shrinks it' question")
        print(BAR)
        mcp_loc = sum(s.loc for s in mcp_tools)
        cli_loc = sum(s.loc for s in cli_verbs)
        cap_loc = sum(c.loc for c in caps.values())
        print(f"  domain capability bodies      : {cap_loc:>6} LOC over {len(caps)} functions")
        print(f"  MCP wrapper bodies            : {mcp_loc:>6} LOC over {len(mcp_tools)} tools")
        print(f"  CLI verbs (parser + handler)  : {cli_loc:>6} LOC over {len(cli_verbs)} verbs")
        print(f"  {'-' * 60}")
        print(f"  surface layer total           : {mcp_loc + cli_loc:>6} LOC")
        if cap_loc:
            ratio = (mcp_loc + cli_loc) / cap_loc
            print(f"  surface / domain ratio        : {ratio:>6.2f}")
        mech_tools, mech_doc, mech_code = [], 0, 0
        hand_tools, hand_doc, hand_code = [], 0, 0
        for s_ in mcp_tools:
            if s_.node is None:
                continue
            d, c, m = body_shape(s_.node)
            if m:
                mech_tools.append(s_)
                mech_doc += d
                mech_code += c
            else:
                hand_tools.append(s_)
                hand_doc += d
                hand_code += c
        print()
        print("  MCP LAYER SPLIT -- docstring is user-visible text, not removable code:")
        print(f"    mechanically derivable wrappers : {len(mech_tools):>3} of {len(mcp_tools)}")
        print(f"      their docstrings              : {mech_doc:>6} LOC   (MOVES, does not vanish)")
        print(f"      their code                    : {mech_code:>6} LOC   (this is what a generator removes)")
        print(f"    wrappers carrying own behaviour : {len(hand_tools):>3} of {len(mcp_tools)}")
        print(f"      their docstrings              : {hand_doc:>6} LOC")
        print(f"      their code                    : {hand_code:>6} LOC   (a generator CANNOT remove this)")
        print()
        print(f"  >>> HONEST CEILING on the MCP side: {mech_code} LOC of {mcp_loc} "
              f"({100.0 * mech_code / mcp_loc:.0f}% of the layer)")
        print()
        print("  Non-mechanical MCP wrappers (each carries logic a declaration cannot express):")
        for s_ in sorted(hand_tools, key=lambda x: -x.loc)[:12]:
            print(f"      {s_.module}.{s_.name}  ({s_.loc} LOC)")
        if len(hand_tools) > 12:
            print(f"      ... and {len(hand_tools) - 12} more")
        agg = {"connects": 0, "try_finally_close": 0, "has_except": 0,
               "has_jsondecode_arm": 0, "exits": 0}
        seen_handlers = set()
        for s_ in cli_verbs:
            key = (s_.module, s_.handler)
            if s_.handler is None or key in seen_handlers:
                continue
            fn = handlers.get(key)
            if fn is None:
                continue
            seen_handlers.add(key)
            sh = cli_handler_shape(fn)
            for k in agg:
                agg[k] += 1 if sh[k] else 0
        n = len(seen_handlers)
        print()
        print("  CLI LAYER SPLIT -- the duplication here is boilerplate, not the whole handler:")
        print(f"    distinct handlers                 : {n:>3}")
        print(f"      open a connection themselves    : {agg['connects']:>3}"
              "   (db.connect() + close, copied per handler)")
        print(f"      carry try/finally close         : {agg['try_finally_close']:>3}")
        print(f"      carry an except ladder          : {agg['has_except']:>3}")
        print(f"      carry the JSONDecodeError arm   : {agg['has_jsondecode_arm']:>3}"
              "   <-- CB-55's copy-maintained ordering")
        print(f"      call sys.exit                   : {agg['exits']:>3}")
        print()
        print("  NOTE: this is the CEILING of what a generated exposure layer could remove,")
        print("  not a promise. Every orphan surface above carries logic a generator cannot")
        print("  synthesise, and every hand-written docstring is user-visible text.")

        blind = sorted({u for s in surfaces for u in s.unresolved})
        print()
        print(BAR)
        print(f"BLIND SPOTS  --  printed rather than hidden: {len(blind)}")
        print(BAR)
        for b in blind:
            print(f"      {b}")

    # ---------------- self-check ----------------
    print()
    print(BAR)
    print("SELF-CHECK  --  independently-known facts this run must reproduce")
    print(BAR)
    print("(sourced from tracker cards and CLAUDE.md, NOT from this script.")
    print(" A mismatch is either a script bug or a stale card -- both are findings.)")
    print()

    def check(label: str, got: bool, want: bool, detail: str = "") -> None:
        mark = "OK      " if got == want else "MISMATCH"
        print(f"  {mark}  want={want!s:<5} got={got!s:<5}  {label}")
        if detail:
            print(f"            {detail}")

    r_rec = reach.get("milestones.reconcile.reconcile_all", {"mcp": [], "cli": []})
    check(
        "CB-107: reconcile_all is CLI-only (no MCP wrapper)",
        bool(r_rec["cli"]) and not r_rec["mcp"],
        True,
        f"mcp={r_rec['mcp']} cli={r_rec['cli']}",
    )

    blockers_cli = [s for s in cli_verbs if s.module == "blockers"]
    check(
        "CB-6: CLI has ZERO blockers subcommands",
        len(blockers_cli) == 0,
        True,
        f"found {len(blockers_cli)}: {[s.name for s in blockers_cli]}",
    )

    emb_mcp = [s for s in mcp_tools if s.module == "embeddings"]
    check(
        "embeddings.py registers MCP tools",
        len(emb_mcp) > 0,
        True,
        f"found {len(emb_mcp)}: {[s.name for s in emb_mcp]}",
    )
    emb_cli = [s for s in cli_verbs if s.module == "embeddings"]
    check(
        "embeddings.py registers NO CLI verbs",
        len(emb_cli) == 0,
        True,
        f"found {len(emb_cli)}",
    )

    check(
        "at least one capability is reachable from neither surface",
        len(neither) > 0,
        True,
        f"{len(neither)} such capabilities",
    )

    print()
    print("  A self-check that only ever passes is worthless. The two rows above that")
    print("  are EXPECTED to mismatch on a healthy run are the card-sourced ones:")
    print("  they encode what a CARD claims, and cards go stale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
