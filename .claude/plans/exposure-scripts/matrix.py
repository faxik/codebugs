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
# Declared non-capabilities.
#
# The first version admitted "4 infrastructure functions pollute the denominator"
# and stopped auditing there. Cross-model review found a fifth in one grep -- and
# the docstring it found declares a SIXTH in the same sentence. That is this
# repository's signature failure: a rule expressed as an enumeration gets checked
# at the entries someone enumerated.
#
# The fix is the shape the repo already uses for surface holes (SURFACE_GAPS in
# tests/test_update_parity.py): every exclusion is DECLARED WITH A REASON, and
# anything undeclared stays counted. The letter cannot decide silently, because
# the undeclared remainder is printed.
# ---------------------------------------------------------------------------

NON_CAPABILITIES: dict[str, str] = {
    # -- infrastructure / extension points: plumbing, not product capability --
    "db.txn": "transaction context manager; the mechanism capabilities run INSIDE",
    "db.run_post_add_hooks": "hook runner (extension point), not a capability",
    "db.run_pre_add_resolvers": "resolver runner (extension point), not a capability",
    "db.run_status_change_hooks": "hook runner (extension point), not a capability",
    "findings.similarity_candidates": (
        "the sanctioned read accessor for similarity.py; CLAUDE.md: 'All row access "
        "goes through the public accessor findings.similarity_candidates'"
    ),
    "findings.grouping_candidates": (
        "sibling accessor, 'the sanctioned read surface for grouping.py' (own docstring)"
    ),
    "milestones.reconcile.live_source_clause": (
        "returns a SQL FRAGMENT for callers to compose; a seam, not an operation"
    ),
    # -- superseded, retained only as a differential-test baseline --
    "blockers.blocker_counts_for": (
        "own docstring: 'No production caller remains' -- kept as the CB-69 "
        "before/after baseline. Measured: 0 src callers."
    ),
    "blockers.deferred_id_restriction": (
        "declared test-only in the SAME docstring sentence as blocker_counts_for -- "
        "the sixth impurity, found because the fifth was found"
    ),
    "milestones.reconcile.source_is_terminal": (
        "row-wise predicate superseded by live_source_clause (CB-31). "
        "Measured: 0 src callers, 2 test refs."
    ),
}


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
    handler_lineno: int = 0  # CLI only: the handler's own span, kept SEPARATE from
    handler_endline: int = 0  # the parser span so neither is reported as the other
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


def _calls_add_parser(fn: ast.FunctionDef) -> bool:
    """Any function that calls `.add_parser("...")` is a CLI registrar, whatever it is named."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            _q, nm = call_name(n)
            if nm == "add_parser" and n.args and isinstance(n.args[0], ast.Constant):
                return True
    return False


def derivability_tier(fn: ast.FunctionDef, cap_names: set[str]) -> str:
    """T1 / T2 / T3 -- how much of this wrapper a DECLARATION could generate.

    A single boolean was the fatal flaw of the first version: it recognised exactly
    one AST shape and then asserted every other wrapper "cannot" be generated. That
    conflates "this generator model cannot" with "no generator can", and it
    understated the removable code. Cross-model review reproduced the gap and refused
    the conclusion built on it. Three tiers, each with a STATED criterion:

      T1  strict delegation   -- one `with conn_factory()` whose body is one `return call`.
                                 A declaration with no adapters generates these.
      T2  delegation + trivial pre/post -- exactly ONE domain call, and every other
                                 statement is an assignment or a return: no branch, no
                                 loop, no try. These are argument normalisation and
                                 result wrapping, which a declaration format WITH
                                 defaults / input adapters / output adapters generates.
                                 Calling these ungeneratable is what overstated the case.
      T3  genuine logic       -- branching, looping, error handling, or more than one
                                 domain call. A generator cannot synthesise these, and
                                 this is the only tier where that claim is safe.

    T2 is deliberately a CANDIDATE tier, not a promise: it says the body contains no
    control flow, not that a specific declaration language expresses it. Reported as a
    range endpoint, never as the headline.
    """
    stmts = list(fn.body)
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]

    _d, _c, strict = body_shape(fn)
    if strict:
        return "T1"

    has_control = any(
        isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.Raise, ast.Assert))
        for n in ast.walk(fn)
    )
    domain_calls = 0
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            _q, nm = call_name(n)
            if nm in cap_names:
                domain_calls += 1
    only_simple = all(
        isinstance(x, (ast.Assign, ast.AnnAssign, ast.Return, ast.With, ast.Expr))
        for x in stmts
    )
    if not has_control and domain_calls == 1 and only_simple:
        return "T2"
    return "T3"


def signature_loc(fn: ast.FunctionDef) -> int:
    """LOC of the decorator + `def` line + parameter declarations -- i.e. everything in
    the def span that is neither docstring nor body statement.

    Reported because the first version's decomposition did NOT partition: docstring +
    body-statement LOC came to 1286 of a 1674-LOC layer, leaving 388 LOC silently
    outside the accounting while the denominator kept them. Those 388 lines are largely
    parameter declarations -- which is precisely what a declaration format REPLACES, so
    omitting them biased the answer against generation.
    """
    span = end_of(fn) - fn.lineno + 1
    d, c, _ = body_shape(fn)
    return span - d - c


def collect_method_capabilities(root: str) -> list[tuple[str, int, str, list[str]]]:
    """Public CLASS METHODS whose first non-self parameter is `conn`.

    The module-level definition is blind to these, and the blindness was found by
    cross-model review, not by this script's own impurity audit -- which looked only
    for things the definition OVER-counts and never asked what it MISSES. An audit
    that checks one direction is half an audit.

    `EntityRef.set_status` is among them, and CLAUDE.md calls it "the one sanctioned
    cross-table status write" -- i.e. the definition missed a function the project
    documents as load-bearing. None are surface-exposed (they are reached through an
    EntityRef instance, never registered), so they do not move the four cells; they
    are reported so the blindness is closed and visible rather than silently absent.
    """
    out: list[tuple[str, int, str, list[str]]] = []
    for dirpath, _d, names in os.walk(root):
        for n in sorted(names):
            if not n.endswith(".py"):
                continue
            path = os.path.join(dirpath, n)
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            mod = module_name(root, path)
            for cls in [x for x in ast.walk(tree) if isinstance(x, ast.ClassDef)]:
                for fn in [y for y in cls.body if isinstance(y, ast.FunctionDef)]:
                    if fn.name.startswith("_"):
                        continue
                    args = [a.arg for a in fn.args.posonlyargs + fn.args.args]
                    if "conn" in args[:2]:
                        out.append((f"{mod}.{cls.name}.{fn.name}", fn.lineno, path, args))
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
                # Keyed on the DECORATOR, not on an enclosing function named
                # `register_tools`. Scoping to a registrar NAME is an enumeration of
                # registrar names, and this script has already been wrong four times by
                # enumerating a form and believing the list complete. The primitive
                # cannot be missed the way a name can.
                if True:
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
                # Same correction: ANY function that calls `add_parser` is a registrar.
                # Scoping to the name `register_cli` missed `cli._register_builtins`,
                # which registers `init` and `where` -- and CLAUDE.md states that in
                # plain text under **CLI**. The form was documented and still missed,
                # which is the argument for keying on the primitive.
                if _calls_add_parser(node):
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
                # Do NOT overwrite the parser span with the handler span. The earlier
                # version did, then summed the result and labelled it "parser + handler"
                # -- so the printed CLI figure was handler-only under a label claiming
                # otherwise. Keep both and report them separately.
                s.handler_lineno = fn.lineno
                s.handler_endline = end_of(fn)

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

        declared = [q for q in caps if q in NON_CAPABILITIES]
        undeclared_neither = [q for q in neither if q not in NON_CAPABILITIES]
        print()
        print(BAR)
        print("DENOMINATOR HYGIENE  --  declared non-capabilities, with a reason each")
        print(BAR)
        print(f"  raw capabilities                 : {len(caps)}")
        print(f"  declared NOT capabilities        : {len(declared)}")
        print(f"  adjusted capability population   : {len(caps) - len(declared)}")
        print(f"  'neither surface' raw            : {len(neither)}")
        print(f"  'neither surface' after exclusion: {len(undeclared_neither)}"
              "   <-- the real unexposed set")
        print()
        for q in sorted(declared):
            print(f"      {q}")
            print(f"          {NON_CAPABILITIES[q]}")
        print()
        print("  UNDECLARED remainder of 'neither' -- genuinely unexposed, NOT excused:")
        for q in sorted(undeclared_neither):
            print(f"      {q}   ({caps[q].loc} LOC)")
        print()
        print("  Anything not in NON_CAPABILITIES stays counted. An exclusion needs a")
        print("  written reason to exist, so the denominator cannot be quietly trimmed")
        print("  toward whatever answer the author wanted.")

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
        # The stale summary block that stood here printed a CLI figure under a label it
        # no longer matched and a surface/domain ratio over non-comparable operands.
        # Leaving it beside the corrected breakdown would have made the script
        # contradict itself inside one run -- the exact "described better than it
        # behaves" defect this whole exercise is about. Removed; the partitioned
        # breakdown below is the only size report.
        mcp_loc = sum(s.loc for s in mcp_tools)
        cap_loc = sum(c.loc for c in caps.values())
        cap_names = {c.name for c in caps.values()}
        tiers = {"T1": [], "T2": [], "T3": []}
        tdoc = {"T1": 0, "T2": 0, "T3": 0}
        tbody = {"T1": 0, "T2": 0, "T3": 0}
        tsig = {"T1": 0, "T2": 0, "T3": 0}
        for s_ in mcp_tools:
            if s_.node is None:
                continue
            t = derivability_tier(s_.node, cap_names)
            d, c, _ = body_shape(s_.node)
            tiers[t].append(s_)
            tdoc[t] += d
            tbody[t] += c
            tsig[t] += signature_loc(s_.node)

        print()
        print("  MCP LAYER, FULLY PARTITIONED (docstring + body + signature = span):")
        print(f"    {'tier':<6}{'tools':>6}{'docstring':>11}{'body':>7}{'signature':>11}{'span':>7}")
        for t, label in (("T1", "T1"), ("T2", "T2"), ("T3", "T3")):
            span = tdoc[t] + tbody[t] + tsig[t]
            print(f"    {label:<6}{len(tiers[t]):>6}{tdoc[t]:>11}{tbody[t]:>7}{tsig[t]:>11}{span:>7}")
        alld, allb, alls = sum(tdoc.values()), sum(tbody.values()), sum(tsig.values())
        print(f"    {'ALL':<6}{len(mcp_tools):>6}{alld:>11}{allb:>7}{alls:>11}{alld + allb + alls:>7}")
        print(f"    (partition check: {alld} + {allb} + {alls} = {alld + allb + alls}"
              f"  vs summed spans {mcp_loc} -> "
              f"{'OK' if (alld + allb + alls) == mcp_loc else 'MISMATCH'})")
        print()
        print("  WHAT A GENERATOR COULD REMOVE -- as a RANGE, not a single number:")
        lo = tbody["T1"]
        hi = tbody["T1"] + tbody["T2"] + tsig["T1"] + tsig["T2"]
        print(f"    LOWER bound (T1 body only, no adapters)        : {lo:>5} LOC")
        print("    UPPER bound (T1+T2 body AND their signatures,")
        print(f"                 i.e. params replaced by a decl)   : {hi:>5} LOC")
        print(f"    NEVER removable (T3 body, real logic)          : {tbody['T3']:>5} LOC")
        print(f"    MOVES but does not vanish (all docstrings)     : {alld:>5} LOC")
        print()
        print("    The single number the first version reported was the LOWER bound,")
        print("    presented as a ceiling. Both bounds are stated now; which one applies")
        print("    depends on a declaration format that does not exist yet, so neither")
        print("    is a promise and the gap between them is the honest uncertainty.")
        print()
        print("  T3 wrappers (the only ones a generator provably cannot produce):")
        for s_ in sorted(tiers["T3"], key=lambda x: -x.loc)[:10]:
            print(f"      {s_.module}.{s_.name}  ({s_.loc} LOC)")
        if len(tiers["T3"]) > 10:
            print(f"      ... and {len(tiers['T3']) - 10} more")

        agg = {"connects": 0, "try_finally_close": 0, "has_except": 0,
               "has_jsondecode_arm": 0, "exits": 0}
        seen_handlers = set()
        hspan = 0
        for s_ in cli_verbs:
            key = (s_.module, s_.handler)
            if s_.handler is None or key in seen_handlers:
                continue
            fn = handlers.get(key)
            if fn is None:
                continue
            seen_handlers.add(key)
            hspan += end_of(fn) - fn.lineno + 1
            sh = cli_handler_shape(fn)
            for k in agg:
                agg[k] += 1 if sh[k] else 0
        pspan = sum(s_.endline - s_.lineno + 1 for s_ in cli_verbs)
        print()
        print("  CLI LAYER -- parser and handler counted SEPARATELY and each exactly once:")
        print(f"    parser declarations             : {pspan:>5} LOC over {len(cli_verbs)} verbs")
        print(f"    distinct handlers               : {hspan:>5} LOC over {len(seen_handlers)} handlers")
        print(f"    CLI layer total                 : {pspan + hspan:>5} LOC")
        print("    (the first version overwrote the parser span with the handler span and")
        print("     printed the handler figure under the label 'parser + handler')")
        print()
        print(f"      open a connection themselves    : {agg['connects']:>3}"
              "   (db.connect() + close, copied per handler)")
        print(f"      carry try/finally close         : {agg['try_finally_close']:>3}")
        print(f"      carry an except ladder          : {agg['has_except']:>3}")
        print(f"      carry the JSONDecodeError arm   : {agg['has_jsondecode_arm']:>3}"
              "   <-- CB-55's copy-maintained ordering")
        print(f"      call sys.exit                   : {agg['exits']:>3}")
        print()
        print(f"  SURFACE LAYER TOTAL (corrected) : {mcp_loc + pspan + hspan} LOC")
        print(f"  domain capability bodies        : {cap_loc} LOC over {len(caps)} functions")
        print()
        print("  The surface/domain RATIO is deliberately NOT printed. Review showed the")
        print("  operands are not comparable: the domain figure is full def spans of")
        print("  public conn-first functions only, excluding every private helper the")
        print("  surfaces also rest on. A ratio over non-comparable populations reads as")
        print("  an architectural fact and is not one.")

        cli_boiler = 0
        for key in seen_handlers:
            fn = handlers.get(key)
            if fn is None:
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Try):
                    cli_boiler += 1
                    if n.finalbody:
                        cli_boiler += 1 + sum(end_of(x) - x.lineno + 1 for x in n.finalbody)
                    for h in n.handlers:
                        cli_boiler += end_of(h) - h.lineno + 1
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                    _q, nm = call_name(n.value)
                    if nm == "connect":
                        cli_boiler += end_of(n) - n.lineno + 1

        reg_span = 0
        for _mod, tree in _trees.items():
            for fn in ast.walk(tree):
                if isinstance(fn, ast.FunctionDef) and _calls_add_parser(fn):
                    reg_span += end_of(fn) - fn.lineno + 1
        nested = 0
        for (hmod, _hname), hfn in handlers.items():
            tree = _trees.get(hmod)
            if tree is None:
                continue
            for outer in ast.walk(tree):
                if (
                    isinstance(outer, ast.FunctionDef)
                    and _calls_add_parser(outer)
                    and outer.lineno < hfn.lineno
                    and end_of(hfn) <= end_of(outer)
                ):
                    nested += end_of(hfn) - hfn.lineno + 1
                    break
        parser_decl = reg_span - nested

        print()
        print("  CLI SIDE, REMOVABLE -- measured in LOC, not in site counts:")
        print(f"    connect + try/finally/except boilerplate : {cli_boiler:>5} LOC"
              f"  across {len(seen_handlers)} handlers")
        print(f"    pure parser declaration                  : {parser_decl:>5} LOC"
              "  (add_parser/add_argument, outside handlers)")
        print()
        print("    The first version reported this side as SITE COUNTS only (50 / 52 / 35)")
        print("    and never in LOC, while option (A) generates BOTH surfaces. Comparing a")
        print("    LOC figure on one side against a headcount on the other is how the")
        print("    removable total came out ~3x too small.")
        print()
        print("  CORRECTED REMOVABLE RANGE, both surfaces:")
        floor = tbody["T1"] + cli_boiler
        mid = floor + tsig["T1"] + tsig["T2"] + tbody["T2"]
        ceil_ = mid + parser_decl
        surf_total = mcp_loc + hspan + parser_decl
        print(f"    FLOOR  (MCP T1 body + CLI boilerplate)      : {floor:>5} LOC")
        print(f"    MID    (+ T1/T2 signatures and T2 bodies)   : {mid:>5} LOC")
        print(f"    CEILING(+ parser declaration)               : {ceil_:>5} LOC")
        print(f"    corrected surface layer total               : {surf_total:>5} LOC")
        print(f"    => removable is {100.0 * floor / surf_total:.0f}%-{100.0 * ceil_ / surf_total:.0f}%"
              " of the surface layer, NOT the 14% first reported.")
        print()
        print("    NONE of this is a net saving: the COST of a generator plus a")
        print("    declaration format is still unmeasured. A range for N with no M is not")
        print("    a decision -- it is the reason the decision cannot be made yet.")

        methods = collect_method_capabilities(args.root)
        print()
        print(BAR)
        print(f"UNDER-COUNT CHECK  --  public CLASS METHODS taking `conn`: {len(methods)}")
        print(BAR)
        print("(invisible to the module-level definition; found by review, not by this")
        print(" script's own audit, which only looked for OVER-counting)")
        for q, ln, path, _a in methods:
            print(f"      {q}   ({path.replace(args.root + os.sep, '')}:{ln})")
        print()
        print("  None is registered on any surface, so the four cells are unaffected.")
        print("  Reported because a definition's blind spot must be visible, not absent.")

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
        "CB-107: reconcile_all is CLI-only, no MCP wrapper",
        bool(r_rec["cli"]) and not r_rec["mcp"],
        True,
        f"mcp={r_rec['mcp']} cli={r_rec['cli']}",
    )

    blockers_cli = sorted(s_.name for s_ in cli_verbs if s_.module == "blockers")
    check(
        "blockers HAS CLI verbs (CB-6's headline says zero -- stale since 2026-08-14)",
        len(blockers_cli) == 4,
        True,
        f"{blockers_cli}",
    )

    # CLAUDE.md, section CLI: "Two commands are built into cli.py rather than owned by
    # a domain module, registered by _register_builtins: init ... and where".
    builtins = sorted(s_.name for s_ in cli_verbs if s_.module == "cli")
    check(
        "CLAUDE.md: cli.py itself registers exactly `init` and `where`",
        builtins == ["init", "where"],
        True,
        f"{builtins}  (this row is why the fourth registrar can no longer hide)",
    )

    # CLAUDE.md, Architecture migration: "New domain modules must call
    # register_schema(), register_tool_provider() and register_cli_provider()".
    emb_src = os.path.join(args.root, "embeddings.py")
    emb_txt = open(emb_src, encoding="utf-8").read() if os.path.exists(emb_src) else ""
    check(
        "CLAUDE.md's registration rule holds for embeddings.py",
        "register_tool_provider(" in emb_txt,
        True,
        "embeddings.py defines register_tools but registers NO provider; its tools "
        "arrive via reqs.py. Expected to FAIL: the rule is violated in the tree.",
    )

    print()
    print("  Rows are sourced from CLAUDE.md and from tracker cards -- artefacts OUTSIDE")
    print("  this script -- so a row can fail. Review found the previous version carried")
    print("  three rows that could not fail in any plausible state of the repo (they")
    print("  asserted the script's own scan back to itself) and a closing sentence")
    print("  claiming TWO rows were expected to mismatch when only one ever did.")
    print("  A self-check that cannot fail is decoration; one that miscounts its own")
    print("  failures is worse, because it reads as rigour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
