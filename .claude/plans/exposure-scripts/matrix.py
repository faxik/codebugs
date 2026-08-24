#!/usr/bin/env python3
"""Capability x surface matrix for codebugs -- a HYBRID of AST and the real registry.

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

THE HYBRID BOUNDARY (CB-153, T-55) -- STATED EXPLICITLY, BECAUSE A HYBRID THAT
DOES NOT NAME ITS OWN SPLIT IS THE NEXT BLINDNESS.
    This script used to find MCP tools and CLI verbs by AST SYNTAX alone
    (`@mcp.tool(...)` decorators, `sub.add_parser("...")` calls). After BT-6's
    surfacegen pilot, `bench` and `sweep` register their whole surface through
    DATA DECLARATIONS (`bench_surface.py`, `sweep_surface.py`) consumed by
    `codebugs.surfacegen.emit_tools`/`emit_cli` at import time -- there is no
    decorator and no `add_parser` call anywhere in `bench.py`/`sweep.py` for the
    AST pass to see, so it reported 16 real capabilities (7 bench + 9 sweep) as
    reachable by NEITHER surface, when both existed. CB-153.

    The fix is NOT teaching the AST pass a third registration shape (that was
    considered and REJECTED -- a third form would return the same blindness by
    construction, and this repo has paid for that exact mistake before). Instead:

    SURFACE EXISTENCE (which MCP tool names and CLI verb names are real, right
    now) comes from the REGISTRY, not from source syntax:
      MCP : `tests/_mcp_schema.collect_tool_schemas()` -- builds a real server
            per provider through `db.get_tool_providers(mode="all")` and asks it.
      CLI : `codebugs.cli.build_parser(mode="all")` -- the exact primitive
            `cli.main()` itself calls, extracted by T-51 (CB-146) precisely so a
            surface snapshot need not re-implement subparser assembly.
    Both are keyed on "what is REALLY registered", which closes the blindness
    for ANY future registration form, not just the two known today (decorator
    and surfacegen) -- a decorator-recognizer would need a fourth clause the
    day a third form appears; the registry cannot be behind a form it did not
    know about, because it IS what runs.

    LINKING a registry-real surface name back to the domain capability(-ies) it
    calls, and every SIZE metric (LOC, docstring/body/signature partition, T1/
    T2/T3 derivability tiers, CLI boilerplate) STILL come from AST, and this is
    the honestly-stated other half of the hybrid: a generated tool's registered
    callable is built by `surfacegen.build_tool` at runtime and carries no
    source span of its own to measure -- there is no "MCP wrapper body" to
    partition into docstring/code/signature for a function synthesized from a
    declaration dict. So the SIZE section below (unchanged) keeps operating on
    the decorator/`add_parser`-detected surface set exactly as before, and does
    NOT grow to cover `bench`/`sweep`'s generated tools. The registry-only
    surfaces are linked to capabilities separately (see `resolve_live_impl`),
    by unwrapping `surfacegen`'s runtime closure (`calls`/`manual` cells) back
    to the real Python function it ultimately invokes, then either matching
    that function's identity directly against the capability set or -- for a
    dispatching handler like `_tool_bench_list` -- walking ITS ast body with the
    same `resolve_calls` machinery CLI handlers already use. This is registry
    inspection plus a runtime-to-AST bridge, not decorator pattern-matching, so
    it generalizes the same way the existence check does: whatever a THIRD
    registration form does, as long as it ultimately calls `mcp.tool()(fn)` and
    `commands[name] = fn`, this still finds the real name and the real callable.

    Cost accepted (brief SS3): this script is no longer purely static. It
    imports the `codebugs` package and `tests/_mcp_schema`, and needs a working
    Python environment for that half of its output. The GAPS section (BOTH/MCP
    only/CLI only/NEITHER) and the top-line "MCP tools (registered)"/"CLI verbs
    (registered)" counts are therefore registry-truth; the SIZE section and its
    own "MCP tools (AST-visible)"/"CLI verbs (AST-visible)" counts stay AST-only
    and are printed under that explicit label so the two halves are never
    conflated as one number.

    `module_surface.py` is a SEPARATE, untouched instrument (CB-153 SS2): it
    keys on `register_tools`/`register_cli` function SPANS, not the decorator,
    so it is not blind here -- its wiring ratio for `sweep` answers a different
    question (how much of the registrar itself is left) and changed meaning,
    not correctness, when the surface moved into declarations.

SURFACE DETECTION (AST HALF, used for SIZE only -- see above)
    MCP : an inner FunctionDef inside `register_tools(mcp, conn_factory)`
          decorated with `@mcp.tool(...)`.
    CLI : `sub.add_parser("<verb>")` inside `register_cli(sub, commands)`, joined
          to its handler by `commands["<verb>"] = <handler>`.
    Both layers are then linked to domain capabilities by resolving the calls in
    their bodies, using each module's import map, falling back to a unique
    package-wide name match, and REPORTING ambiguity rather than guessing.

BLINDNESS, printed rather than hidden
    Dynamic registration, calls through variables, and re-exported aliases are
    not resolved by the AST half. Every unresolved call is counted and listed
    under BLIND SPOTS. The registry half closes the ONE known dynamic-form
    blindness (surfacegen); anything it cannot resolve is printed under
    REGISTRY-ONLY SURFACES rather than silently dropped.

USAGE
    python3 .claude/plans/exposure-scripts/matrix.py
    python3 .claude/plans/exposure-scripts/matrix.py --root src/codebugs
    python3 .claude/plans/exposure-scripts/matrix.py --check   # self-check only
    python3 .claude/plans/exposure-scripts/matrix.py --ast-only  # skip the
        registry half entirely (old, blind-to-surfacegen behaviour) -- kept so
        the CB-153 mutant probe can reproduce the pre-fix report on demand.

Prints to stdout; writes nothing anywhere. No longer stdlib-only for the
registry half (imports the `codebugs` package and `tests/_mcp_schema`).
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

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
    span = end_of(fn) - def_start(fn) + 1
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


def def_start(fn: ast.FunctionDef) -> int:
    """First line of the definition INCLUDING its decorators.

    `ast.FunctionDef.lineno` points at the `def`, not at `@mcp.tool()` above it, so
    `end_lineno - lineno + 1` silently drops one line per decorated tool -- 74 lines
    across the MCP layer. The partition then "checked out" (860 + 426 + 388 = 1674)
    while 1674 itself was short, which is the worst kind of arithmetic: internally
    consistent and wrong at the boundary. Found by cross-model review after the
    partition had already been "fixed" once.
    """
    if fn.decorator_list:
        return min(d.lineno for d in fn.decorator_list)
    return fn.lineno


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
    all_funcs: dict[tuple[str, str], ast.FunctionDef] = {}
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
            # Collect EVERY function, then resolve handlers from the `commands`
            # wiring below. Keying on the name prefix `_cmd_` was the last name-based
            # enumeration in this script, and it leaked: `findings._print_fold_report`,
            # `sweep._parse_csv` and `sweep._parse_tags` are nested inside `register_cli`,
            # are not named `_cmd_*`, and were therefore charged to "parser declaration"
            # -- 59 LOC of per-verb rendering logic counted as boilerplate. The wiring
            # NAMES the handler, so the wiring is the primitive; the prefix was a guess
            # that happened to be right 62 times out of 65.
            for anynode in ast.walk(tree):
                if isinstance(anynode, ast.FunctionDef):
                    all_funcs[(mod, anynode.name)] = anynode

            # Walk the WHOLE tree, not `tree.body`. Module-level-only scanning was
            # the second surviving name/scope enumeration: a registrar defined inside
            # a class or a factory would be invisible, and the CLI measurement used
            # ast.walk on one half and tree.body on the other -- two scopes for one
            # question. Review caught the claim "a fifth form of this class cannot
            # exist" being false while it was printed in the document.
            for node in ast.walk(tree):
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
                # `register_tools`, and matched on THIS node only. Scanning
                # `ast.walk(node)` from inside a loop that is itself `ast.walk(tree)`
                # collected every tool TWICE -- once via its registrar and once via
                # itself -- which silently doubled the MCP layer. Caught by the numbers
                # jumping implausibly, not by the tests: the script has none.
                if any(decorator_is_mcp_tool(d) for d in node.decorator_list):
                    for inner in (node,):
                        surfaces.append(
                            Surface(
                                kind="mcp",
                                module=mod,
                                name=mcp_tool_name(inner),
                                lineno=def_start(inner),
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

    # Resolve handlers from the wiring: a handler is whatever `commands[...]` maps to.
    for s_ in surfaces:
        if s_.kind == "cli" and s_.handler:
            fn = all_funcs.get((s_.module, s_.handler))
            if fn is not None:
                handlers[(s_.module, s_.handler)] = fn

    return caps, by_short, surfaces, handlers, imports, trees, files, all_funcs


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
# registry half (CB-153, T-55) -- surface EXISTENCE and its LINKING back to
# domain capabilities, sourced from what is really registered rather than from
# source syntax. See the module docstring's "THE HYBRID BOUNDARY" section for
# the full reasoning; this section is the implementation of that boundary.
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """This file lives at <repo>/.claude/plans/exposure-scripts/matrix.py."""
    return Path(__file__).resolve().parents[3]


def _load_registry_primitives():
    """Import the two ALREADY-BUILT primitives named in the T-55 brief, plus
    `codebugs.db` for the closure-capture pass below. Raises ImportError with
    the repo-relative context if the package/tests aren't importable -- this
    is the accepted cost of the hybrid (module docstring, "Cost accepted").
    """
    root = _repo_root()
    src_dir = str(root / "src")
    tests_dir = str(root / "tests")
    for p in (src_dir, tests_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    from _mcp_schema import collect_tool_schemas  # tests/_mcp_schema.py
    from codebugs import cli as codebugs_cli
    from codebugs import db as codebugs_db

    return collect_tool_schemas, codebugs_cli, codebugs_db


class _CapturingMCP:
    """Stands in for the real MCP server object passed to `register_tools`.

    `provider.register_fn(capturing_mcp, conn_factory)` calls
    `capturing_mcp.tool(...)` exactly as it would call the real server's --
    whether that happens via a `@mcp.tool()` decorator on a hand-written
    function OR via `surfacegen.emit_tools`'s `mcp.tool()(fn)` makes no
    difference to this class, which is the whole point: it captures the REAL
    (wire name -> real callable) pair regardless of how registration got
    there, the same way `collect_tool_schemas` does for the schema, without
    needing the `mcp` SDK's own server/pydantic machinery (this script does
    not otherwise depend on the `mcp` package).
    """

    def __init__(self) -> None:
        self.captured: dict[str, object] = {}

    def tool(self, *_args, **kwargs):
        def deco(fn):
            name = kwargs.get("name") or fn.__name__
            self.captured[name] = fn
            return fn

        return deco


def collect_registered_surfaces(
    root: str,
) -> tuple[set[str], set[str], dict[str, object], dict[str, object]]:
    """The REGISTRY's answer to "what MCP tools and CLI verbs really exist right
    now", plus the real callable behind each name.

    Returns (mcp_names, cli_names, mcp_fn_by_name, cli_fn_by_name).

    `mcp_names` is sourced from `tests/_mcp_schema.collect_tool_schemas()` --
    the SAME function the wire-schema golden gate uses -- so this script's
    headline MCP count can never quietly diverge from what that gate already
    treats as ground truth. `cli_names` and `cli_fn_by_name` are sourced from
    `codebugs.cli.build_parser(mode="all")`, the exact primitive `cli.main()`
    itself calls (CB-146/T-51) -- `commands` already maps every verb to its
    real handler callable, no capture needed.

    `mcp_fn_by_name` is captured SEPARATELY via `_CapturingMCP`, because
    `collect_tool_schemas` returns schemas, not callables, and this script
    needs the callable to walk back to a domain capability. Both walks go
    through the SAME registry primitive (`db.get_tool_providers(mode="all")`
    is what `collect_tool_schemas` calls internally too), so a self-check row
    below asserts the two name sets are identical -- if `_CapturingMCP` were
    ever wrong about what got registered, that row would go red rather than
    silently reporting a different tool count than the golden gate does.
    """
    collect_tool_schemas, codebugs_cli, codebugs_db = _load_registry_primitives()

    mcp_schemas = collect_tool_schemas()
    mcp_names = {t["name"] for t in mcp_schemas}

    capturer = _CapturingMCP()
    for provider in codebugs_db.get_tool_providers(mode="all"):
        provider.register_fn(capturer, lambda: None)  # conn_factory unused: not calling
    mcp_fn_by_name = capturer.captured

    _parser, sub, commands = codebugs_cli.build_parser(mode="all")
    cli_names = set(sub.choices.keys())
    cli_fn_by_name = dict(commands)

    return mcp_names, cli_names, mcp_fn_by_name, cli_fn_by_name


def _unwrap_generated(fn):
    """If `fn` is a wrapper built by `surfacegen.build_tool`, return the real
    implementer it closes over; otherwise return `fn` unchanged.

    `build_tool`'s emitted `tool(...)` closure always carries exactly the
    free variables `calls` and `manual` (see `surfacegen.py`): whichever one
    is not None is the real Python function the declaration named -- either
    the domain capability directly (`calls=query`) or a handwritten dispatch
    body (`manual=_tool_bench_list`). A hand-written `@mcp.tool()` function
    has no such free-variable pair, so it passes through unchanged and is
    resolved exactly as `fn` itself, same as any CLI handler already is.
    """
    freevars = fn.__code__.co_freevars
    if fn.__closure__ and "calls" in freevars and "manual" in freevars:
        cells = dict(zip(freevars, fn.__closure__))
        manual = cells["manual"].cell_contents
        calls = cells["calls"].cell_contents
        return manual if manual is not None else calls
    return fn


def _live_qual(fn) -> str | None:
    """The capability-table qualname (`"<module>.<name>"`) for a live function
    object, using the SAME module-naming convention `module_name()` derives
    from source paths (`codebugs.milestones.reconcile` -> `milestones.reconcile`).
    Returns None for anything outside the `codebugs` package.
    """
    mod = getattr(fn, "__module__", None) or ""
    if mod != "codebugs" and not mod.startswith("codebugs."):
        return None
    mod_short = mod[len("codebugs") :].lstrip(".") or "__init__"
    # A nested def's __qualname__ is "register_tools.<locals>.add" -- the
    # capability table is keyed on the bare def name, so take the last segment.
    name = fn.__qualname__.rsplit(".", 1)[-1]
    return f"{mod_short}.{name}"


def resolve_live_impl(
    fn,
    caps: dict[str, "Capability"],
    by_short: dict[str, list[str]],
    imports: dict[str, dict[str, str]],
    all_funcs: dict[tuple[str, str], ast.FunctionDef],
) -> tuple[set[str], set[str]]:
    """Resolve one REGISTRY-real callable (an MCP tool's or a CLI verb's actual
    handler) to the domain capabilities it reaches. Mirrors `resolve_calls`'s
    (resolved, unresolved) contract.

    Two paths, both ending in the same AST call-resolution the rest of this
    script already trusts:
      1. IDENTITY match -- the live function's own qualname is itself a
         capability (`calls=query` in a SURFACE declaration is literally the
         `bench.query` capability function; a hand-written `@mcp.tool()` body
         that delegates through nothing is not, so this path is a shortcut,
         not the general case).
      2. AST FALLBACK -- locate the SAME function's def node (matched by the
         module+name this live object itself reports, not by guessing) in the
         `all_funcs` index `collect()` already built, and run `resolve_calls`
         over its body exactly as a CLI handler's body is resolved today. This
         is what finds e.g. `_tool_bench_list` calling BOTH `list_runs` and
         `list_benchmarks` in its two branches.
    """
    impl = _unwrap_generated(fn)
    qual = _live_qual(impl)
    if qual and qual in caps:
        return {qual}, set()

    mod = getattr(impl, "__module__", None) or ""
    mod_short = mod[len("codebugs") :].lstrip(".") if mod.startswith("codebugs") else None
    name = getattr(impl, "__name__", None)
    node = all_funcs.get((mod_short, name)) if mod_short and name else None
    if node is None:
        label = f"{mod_short or '?'}.{name or '?'}"
        return set(), {f"{label} (registry-real callable has no matching AST def)"}
    return resolve_calls(node, mod_short, caps, by_short, imports)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

BAR = "=" * 100
DASH = "-" * 100


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join("src", "codebugs"))
    ap.add_argument("--check", action="store_true", help="print only the self-check")
    ap.add_argument(
        "--ast-only",
        action="store_true",
        help=(
            "skip the registry half (CB-153) and report exactly the old, "
            "surfacegen-blind numbers -- kept for the mutant probe's before/after"
        ),
    )
    args = ap.parse_args()

    caps, by_short, surfaces, handlers, imports, _trees, files, all_funcs = collect(
        args.root
    )

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

    # ---- registry half (CB-153, T-55) ----------------------------------
    # AST-visible names are exactly what mcp_tools/cli_verbs above already
    # found (decorator / add_parser detection, unchanged). Anything the
    # REGISTRY reports that the AST pass did not see is a surface a dynamic
    # registration form (surfacegen today) built -- resolve those separately
    # and fold them into the SAME `reach` dict the GAPS section reads, so the
    # cells below reflect what is really registered. `mcp_tools`/`cli_verbs`
    # themselves are NOT extended -- the SIZE section further down keeps
    # using them exactly as before (module docstring, "Cost accepted").
    registry_mcp_names: set[str] = set()
    registry_cli_names: set[str] = set()
    registry_extra: list[tuple[str, str, str, set[str], set[str]]] = []
    # (kind, name, resolved-as-text, resolved quals, unresolved labels)
    registry_consistency: list[tuple[str, bool, bool, str]] = []
    if not args.ast_only:
        (
            registry_mcp_names,
            registry_cli_names,
            mcp_fn_by_name,
            cli_fn_by_name,
        ) = collect_registered_surfaces(args.root)

        registry_consistency.append(
            (
                "MCP capture agrees with tests/_mcp_schema.collect_tool_schemas()",
                set(mcp_fn_by_name) == registry_mcp_names,
                True,
                f"captured={len(mcp_fn_by_name)} schema={len(registry_mcp_names)}",
            )
        )

        ast_mcp_names = {s.name for s in mcp_tools}
        ast_cli_names = {s.name for s in cli_verbs}
        missing_mcp = sorted(registry_mcp_names - ast_mcp_names)
        missing_cli = sorted(registry_cli_names - ast_cli_names)

        for name in missing_mcp:
            fn = mcp_fn_by_name.get(name)
            if fn is None:
                registry_extra.append(("mcp", name, "NOT CAPTURED", set(), {"capture miss"}))
                continue
            resolved, unresolved = resolve_live_impl(fn, caps, by_short, imports, all_funcs)
            for q in resolved:
                reach[q]["mcp"].append(name)
            registry_extra.append(("mcp", name, ",".join(sorted(resolved)) or "-", resolved, unresolved))

        for name in missing_cli:
            fn = cli_fn_by_name.get(name)
            if fn is None:
                registry_extra.append(("cli", name, "NOT CAPTURED", set(), {"capture miss"}))
                continue
            resolved, unresolved = resolve_live_impl(fn, caps, by_short, imports, all_funcs)
            for q in resolved:
                reach[q]["cli"].append(name)
            registry_extra.append(("cli", name, ",".join(sorted(resolved)) or "-", resolved, unresolved))

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
        print("CAPABILITY x SURFACE MATRIX  --  HYBRID: registry for existence, AST for size")
        print(BAR)
        print(f"root              : {args.root}")
        print(f"python            : {sys.version.split()[0]}")
        print(f"files scanned     : {files}")
        print(f"capabilities      : {len(caps)}   (public module-level fn, first param `conn`)")
        if args.ast_only:
            print(f"MCP tools         : {len(mcp_tools)}   (AST/decorator only; --ast-only)")
            print(f"CLI verbs         : {len(cli_verbs)}   (AST/add_parser only; --ast-only)")
        else:
            print(
                f"MCP tools (registered)   : {len(registry_mcp_names):>3}"
                "   (ground truth: tests/_mcp_schema.collect_tool_schemas())"
            )
            print(
                f"CLI verbs (registered)   : {len(registry_cli_names):>3}"
                "   (ground truth: codebugs.cli.build_parser(mode='all'))"
            )
            print(
                f"MCP tools (AST-visible)  : {len(mcp_tools):>3}"
                "   (decorator-detected; SIZE section below uses only these)"
            )
            print(
                f"CLI verbs (AST-visible)  : {len(cli_verbs):>3}"
                "   (add_parser-detected; SIZE section below uses only these)"
            )
            print(
                f"  -> {len(registry_mcp_names) - len(mcp_tools)} MCP tool(s) and "
                f"{len(registry_cli_names) - len(cli_verbs)} CLI verb(s) are registered but "
                "AST-invisible (CB-153: surfacegen-generated bench/sweep tools). GAPS below "
                "use the registered set; SIZE below uses the AST-visible set -- see the "
                "module docstring's HYBRID BOUNDARY."
            )
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

        if not args.ast_only:
            print()
            print(BAR)
            print(
                f"REGISTRY-ONLY SURFACES  --  registered but AST-invisible: {len(registry_extra)}"
            )
            print(BAR)
            print("(CB-153: these exist per the provider registry but carry no decorator/")
            print(" add_parser for the AST pass to see -- e.g. surfacegen-generated bench/")
            print(" sweep tools. Folded into the GAPS cells above. UNRESOLVED here means the")
            print(" registry proves the surface exists but this script could not trace which")
            print(" capability it calls -- a real blind spot, printed rather than hidden.)")
            for kind, name, resolved_text, _resolved, unresolved in sorted(
                registry_extra, key=lambda t: (t[0], t[1])
            ):
                extra = f"  UNRESOLVED={sorted(unresolved)}" if unresolved else ""
                print(f"      {kind}:{name} -> {resolved_text}{extra}")
            if not registry_extra:
                print("      (none -- every registered surface was already AST-visible)")

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
        src_callers: dict[str, int] = {}
        for q in undeclared_neither:
            short = q.rsplit(".", 1)[1]
            n = 0
            for _m2, t2 in _trees.items():
                for node2 in ast.walk(t2):
                    if isinstance(node2, ast.Call):
                        _q2, nm2 = call_name(node2)
                        if nm2 == short:
                            n += 1
            src_callers[q] = n
        unreachable = [q for q in undeclared_neither if src_callers[q] == 0]
        unreachable_loc = sum(caps[q].loc for q in unreachable)
        print()
        print("  BUILT BUT UNREACHABLE -- no surface AND no in-package caller:")
        for q in sorted(unreachable):
            print(f"      {q}   ({caps[q].loc} LOC)")
        print(f"    total: {unreachable_loc} LOC across {len(unreachable)} functions")
        print()
        print("    COMPUTED, not hand-picked. The document's option (E) quoted 424 LOC over")
        print("    FOUR functions chosen by eye from this same list, silently leaving out")
        print("    two others in the identical state -- enumeration-vs-population, in the")
        print("    one row the document recommended. Review caught it.")
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
        # The block that stood here printed a THIRD parser figure (the sum of
        # `add_parser` CALL spans, 106) and a fourth total (3357) beside the
        # registrar-span figure (647) and its total (3898) computed below. One run,
        # two answers to "how many LOC is the parser layer", both labelled exact.
        # Found by cross-model review. This is the THIRD time in one session I added a
        # corrected measurement and left the superseded one standing -- the same shape
        # as the stale summary block removed earlier and the stale "CLI verbs: 60" left
        # in the document. Superseding a number means DELETING the old one.
        print()
        print(f"      open a connection themselves    : {agg['connects']:>3}"
              "   (db.connect() + close, copied per handler)")
        print(f"      carry try/finally close         : {agg['try_finally_close']:>3}")
        print(f"      carry an except ladder          : {agg['has_except']:>3}")
        print(f"      carry the JSONDecodeError arm   : {agg['has_jsondecode_arm']:>3}"
              "   <-- CB-55's copy-maintained ordering")
        print(f"      call sys.exit                   : {agg['exits']:>3}")
        # The "SURFACE LAYER TOTAL (corrected)" line that stood here was itself
        # superseded: it summed the `add_parser` CALL spans (106) rather than the
        # registrar spans minus nested handlers (647), giving 3357 against the 3898
        # printed below. Two totals in one run, each labelled corrected. Deleted, not
        # relabelled -- the surviving total is computed in the block below.
        print()
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

        # MEASURED DIRECTLY: the spans of the `add_parser`/`add_argument` statements
        # themselves. The previous version computed `registrar spans - nested handlers`,
        # a SET-DIFFERENCE RESIDUE, and labelled the result "pure parser declaration".
        # Review decomposed the 647 it produced: only 333 was parser statements; the
        # rest was 119 blank lines, 94 LOC of `commands` wiring, 59 LOC of non-handler
        # helper functions, 25 imports, 12 def lines and 5 comments. A residue is not a
        # measurement, and this one inflated the ceiling by ~2x.
        parser_decl = 0
        reg_span = 0
        for _mod, tree in _trees.items():
            for fn in ast.walk(tree):
                if not (isinstance(fn, ast.FunctionDef) and _calls_add_parser(fn)):
                    continue
                reg_span += end_of(fn) - fn.lineno + 1
                nested_h = [
                    h for (hm, _hn), h in handlers.items()
                    if hm == _mod and h.lineno > fn.lineno and end_of(h) <= end_of(fn)
                ]
                for n in ast.walk(fn):
                    if not isinstance(n, ast.Call):
                        continue
                    if any(h.lineno <= n.lineno <= end_of(h) for h in nested_h):
                        continue
                    _q, nm = call_name(n)
                    if nm in ("add_parser", "add_argument"):
                        parser_decl += end_of(n) - n.lineno + 1

        help_loc = 0
        for _mod, tree in _trees.items():
            for fn in ast.walk(tree):
                if not (isinstance(fn, ast.FunctionDef) and _calls_add_parser(fn)):
                    continue
                nested_h = [
                    h for (hm, _hn), h in handlers.items()
                    if hm == _mod and h.lineno > fn.lineno and end_of(h) <= end_of(fn)
                ]
                for n in ast.walk(fn):
                    if not isinstance(n, ast.Call):
                        continue
                    if any(h.lineno <= n.lineno <= end_of(h) for h in nested_h):
                        continue
                    _q, nm = call_name(n)
                    if nm not in ("add_parser", "add_argument"):
                        continue
                    for kw in n.keywords:
                        if kw.arg == "help":
                            help_loc += end_of(kw.value) - kw.value.lineno + 1

        jde_loc = 0
        for key in seen_handlers:
            fn = handlers.get(key)
            if fn is None:
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.ExceptHandler) and n.type is not None:
                    if "JSONDecodeError" in ast.dump(n.type):
                        jde_loc += end_of(n) - n.lineno + 1

        print()
        print("  CLI SIDE, REMOVABLE -- measured in LOC, not in site counts:")
        print(f"    connect + try/finally/except boilerplate : {cli_boiler:>5} LOC"
              f"  across {len(seen_handlers)} handlers")
        print(f"      of which the JSONDecodeError arms      : {jde_loc:>5} LOC"
              "  <-- NOT freely removable")
        print("        CB-55's arms, whose ORDERING CLAUDE.md makes load-bearing and whose")
        print("        centralisation CB-86 considered and REFUSED (a central arm cannot")
        print("        tell a post-commit failure from an input error). Option (B) is")
        print("        required to preserve them -- so they cannot also be counted as")
        print("        guaranteed-removable for option (A). Review caught the document")
        print("        naming this risk for (B) while spending the same lines on (A).")
        print(f"    pure parser declaration                  : {parser_decl:>5} LOC"
              "  (add_parser/add_argument, outside handlers)")
        print(f"      of which `help=` TEXT                  : {help_loc:>5} LOC"
              "  <-- RELOCATES, like a docstring")
        print(f"      structural remainder                   : {parser_decl - help_loc:>5} LOC")
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
        print(f"    LOW    (MCP T1 body + CLI boilerplate)      : {floor:>5} LOC")
        print(f"    LOW minus the CB-86-protected arms          : {floor - jde_loc:>5} LOC"
              "  <-- the defensible low end")
        print(f"    MID    (+ T1/T2 signatures and T2 bodies)   : {mid:>5} LOC")
        print(f"    HIGH   (+ parser declaration)               : {ceil_:>5} LOC")
        print(f"    HIGH minus relocating `help=` text          : {ceil_ - help_loc:>5} LOC"
              "  <-- the comparable upper bound")
        print()
        print("    The last line exists because the earlier HIGH was accounted")
        print("    INCONSISTENTLY: 860 LOC of MCP docstrings were EXCLUDED on the grounds")
        print("    that they relocate rather than vanish, while the same-natured `help=`")
        print("    text inside the parser figure was INCLUDED. Two objects of one kind,")
        print("    counted two ways -- caught by cross-model review.")
        print()
        print("    'LOW' is an ESTIMATE, not a proven floor, and the label was corrected")
        print("    under review. The CLI boilerplate figure counts whole `except` handler")
        print("    spans, which include user-visible diagnostics and `sys.exit` calls --")
        print("    behaviour a generator would have to REPRODUCE, not delete. It is a")
        print("    syntactic bucket. Calling it a floor claimed a guarantee the measurement")
        print("    does not make.")
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
    # CB-107 was CLOSED by unit T-45 (merge f16764e): `reconcile_all` gained the MCP
    # wrapper `milestone_reconcile`, so it now reaches BOTH surfaces. This row used to
    # assert the CLI-only state and, once the card was fixed, reported MISMATCH on every
    # run — the self-check doing exactly what its own header promises ("a mismatch is
    # either a script bug or a stale card -- both are findings"), and the finding was the
    # stale card claim. It is restated against today's truth rather than deleted: a row
    # that goes quiet teaches nothing, and a standing MISMATCH is an alarm crying wolf,
    # which masks the real one. If this ever flips back, the wrapper was lost.
    check(
        "CB-107 (fixed by T-45, f16764e): reconcile_all reaches BOTH surfaces",
        bool(r_rec["cli"]) and bool(r_rec["mcp"]),
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

    # CB-153 (T-55): the regression this whole card is about. THIS IS INSURANCE, NOT
    # THE CURE -- it is an ENUMERATION of two module names, exactly the shape the
    # card's own SS4 says a self-check cannot use to close a class. The cure is the
    # registry half above (GAPS is now computed from what is really registered); this
    # row exists only to make a FUTURE regression on these two specific modules loud
    # instead of silent, the way the old SELF-CHECK failed to for CB-153 itself.
    if not args.ast_only:
        for mod in ("bench", "sweep"):
            mod_mcp = any(reach[q]["mcp"] for q in caps if caps[q].module == mod)
            mod_cli = any(reach[q]["cli"] for q in caps if caps[q].module == mod)
            check(
                f"CB-153 insurance (not the fix): `{mod}` has non-empty MCP AND CLI "
                "surface",
                mod_mcp and mod_cli,
                True,
                f"mcp_present={mod_mcp} cli_present={mod_cli}  -- an enumeration of two "
                "module names; the class-level fix is the registry half above",
            )
        for label, got, want, detail in registry_consistency:
            check(label, got, want, detail)

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
