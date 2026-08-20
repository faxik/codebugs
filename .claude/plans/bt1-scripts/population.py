#!/usr/bin/env python3
"""BT-1: find the population by the RIGHT unit of count, by executing an AST pass.

WHAT THIS ANSWERS
    Which functions in `src/codebugs/**/*.py` separate a READ from a WRITE by a
    TRANSACTION BOUNDARY (family F1), and which return the result of a read taken
    AFTER a transaction boundary (family F2).

WHY THIS QUESTION MUST NOT BE ANSWERED BY READING CODE
    The previous attempt enumerated "commit sites" (`grep -n 'conn.commit()'`) and
    was STRUCTURALLY BLIND: after CB-40 correct code commits through
    `with db.txn(conn)`, so a function whose read sits OUTSIDE that block contains
    no commit spelling at all. It is invisible to the enumeration that was used to
    define the population, and live sites were therefore missed. The unit of count
    is not a spelling; it is a RELATION between two statements and a boundary, and
    a relation over ~17k lines and ~29 modules cannot be established by eye — three
    consecutive manual claims about "what the predicate sees" were falsified by
    execution. Every number below is computed, and the run is repeatable.

BOUNDARY, as defined for this run
    - entry/exit of a `with db.txn(...)` block, and
    - any of the six commit spellings: `.commit()`, `.execute("COMMIT")`,
      `.executescript(...)`, assignment to `.isolation_level`, assignment to
      `.autocommit`, and a bare `with <conn>:` context manager.

READS
    `execute`/`executemany` whose SQL starts with SELECT or WITH (counted as reads
    for classification), PRAGMA (listed, FLAGGED, and deliberately NOT counted as a
    SELECT), and calls to functions that read transitively (the call graph is built
    over the whole package and iterated to a fixed point).

USAGE
    python3 .claude/plans/bt1-scripts/population.py [--root src/codebugs]
    python3 .claude/plans/bt1-scripts/population.py --json     # machine-readable

Writes nothing anywhere; prints to stdout; stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# SQL classification
# --------------------------------------------------------------------------

READ_KW = ("SELECT", "WITH")
WRITE_KW = ("INSERT", "UPDATE", "DELETE", "REPLACE", "UPSERT")
DDL_KW = ("CREATE", "DROP", "ALTER", "REINDEX", "VACUUM", "ANALYZE")
TXNCTL_KW = ("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "END TRANSACTION")

KIND_READ = "read"
KIND_PRAGMA = "pragma"
KIND_WRITE = "write"
KIND_DDL = "ddl"
KIND_TXNCTL = "txnctl"
KIND_UNKNOWN = "unknown"


def classify_sql(text: str | None) -> str:
    if text is None:
        return KIND_UNKNOWN
    s = text.strip().lstrip("(").strip()
    up = " ".join(s.split()).upper()
    if up.startswith("PRAGMA"):
        return KIND_PRAGMA
    for kw in TXNCTL_KW:
        if up.startswith(kw):
            return KIND_TXNCTL
    for kw in READ_KW:
        if up.startswith(kw):
            # `WITH ... INSERT/UPDATE/DELETE` is a write with a CTE.
            if kw == "WITH" and any(w in up for w in ("INSERT ", "UPDATE ", "DELETE ")):
                return KIND_WRITE
            return KIND_READ
    for kw in WRITE_KW:
        if up.startswith(kw):
            return KIND_WRITE
    for kw in DDL_KW:
        if up.startswith(kw):
            return KIND_DDL
    return KIND_UNKNOWN


def excerpt(text: str | None, n: int = 62) -> str:
    if text is None:
        return "<unresolved>"
    s = " ".join(text.split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------
# Extracting SQL text out of an argument node
# --------------------------------------------------------------------------


def sql_from_node(node: ast.AST, names: dict[str, tuple[str, str]] | None = None) -> tuple[
    str | None, str
]:
    """Return (sql_text_or_None, form).

    `names` resolves a bare Name to SQL text assigned elsewhere (function-local
    assignment, module-level constant, or a loop variable over a schema string).
    Without it, every `conn.execute(sql, params)` reads as unresolved and the
    script would be blind exactly where the package builds its statements.
    """
    names = names or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, "literal"
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append(" {} ")
        return "".join(parts), "fstring"
    if isinstance(node, ast.Name) and node.id in names:
        txt, base = names[node.id]
        return txt, f"name->{base}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, lf = sql_from_node(node.left, names)
        right, _ = sql_from_node(node.right, names)
        if left is not None:
            return left + (right or " {} "), lf
        return None, "unknown"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        # e.g. "\n".join(parts) / textwrap.dedent(SQL)
        if node.func.attr in ("format", "strip", "rstrip", "lstrip") and node.args == []:
            return sql_from_node(node.func.value, names)
    return None, "unknown"


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class SqlOp:
    line: int
    kind: str
    form: str  # literal | fstring | name->literal | name->fstring | unknown | helper
    method: str  # execute | executemany | executescript | <helper key>
    sql: str | None
    node: ast.AST = field(repr=False, default=None)
    rid: int = -1  # read id, for reads only


@dataclass
class Boundary:
    line: int
    kind: str  # txn_enter | txn_exit | commit_call | execute_COMMIT | executescript
    #            | isolation_level | autocommit | with_conn
    detail: str = ""


@dataclass
class Func:
    key: str
    module: str
    qualname: str
    path: str
    lineno: int
    end_lineno: int
    node: ast.AST = field(repr=False, default=None)
    txn_blocks: list[tuple[int, int]] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    ops: list[SqlOp] = field(default_factory=list)
    calls: list[tuple[int, str | None, str]] = field(default_factory=list)
    # filled by analysis
    helper_reads: list[SqlOp] = field(default_factory=list)
    helper_writes: list[SqlOp] = field(default_factory=list)
    f1_pairs: list[tuple[SqlOp, SqlOp, str]] = field(default_factory=list)
    f1_unproven: list[tuple[SqlOp, SqlOp, str]] = field(default_factory=list)
    f2_reads: list[tuple[SqlOp, int]] = field(default_factory=list)  # (read, boundary line)
    mod_consts: dict = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        return f"{self.module}.{self.qualname}"

    @property
    def loc(self) -> str:
        return f"{self.path}:{self.lineno}"

    @property
    def reads(self) -> list[SqlOp]:
        return [o for o in self.ops if o.kind == KIND_READ] + self.helper_reads

    @property
    def selects(self) -> list[SqlOp]:
        return [o for o in self.ops if o.kind == KIND_READ]

    @property
    def pragmas(self) -> list[SqlOp]:
        return [o for o in self.ops if o.kind == KIND_PRAGMA]

    @property
    def own_writes(self) -> list[SqlOp]:
        return [o for o in self.ops if o.kind in (KIND_WRITE, KIND_DDL)]

    @property
    def writes(self) -> list[SqlOp]:
        return self.own_writes + self.helper_writes

    @property
    def unknowns(self) -> list[SqlOp]:
        return [o for o in self.ops if o.kind == KIND_UNKNOWN]

    def in_txn(self, line: int) -> bool:
        return any(a <= line <= b for a, b in self.txn_blocks)


# --------------------------------------------------------------------------
# Per-module scan
# --------------------------------------------------------------------------


def own_nodes(fnode: ast.AST) -> tuple[list[ast.AST], dict[int, ast.AST]]:
    """Nodes belonging to this function, EXCLUDING nested def/class/lambda bodies."""
    out: list[ast.AST] = []
    parent: dict[int, ast.AST] = {}

    def rec(n: ast.AST) -> None:
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            out.append(c)
            parent[id(c)] = n
            rec(c)

    rec(fnode)
    return out, parent


def is_db_txn(call: ast.AST) -> bool:
    if not isinstance(call, ast.Call):
        return False
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr == "txn":
        return True
    if isinstance(f, ast.Name) and f.id == "txn":
        return True
    return False


def collect_defs(tree: ast.AST, module: str, path: str) -> list[Func]:
    out: list[Func] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + child.name
                out.append(
                    Func(
                        key=f"{module}.{q}",
                        module=module,
                        qualname=q,
                        path=path,
                        lineno=child.lineno,
                        end_lineno=getattr(child, "end_lineno", child.lineno),
                        node=child,
                    )
                )
                walk(child, q + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def module_sql_constants(tree: ast.AST) -> dict[str, tuple[str, str]]:
    """Module-level `NAME = "<sql>"` and `NAME = ["<sql>", ...]`."""
    out: dict[str, tuple[str, str]] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        t = stmt.targets[0]
        if not isinstance(t, ast.Name):
            continue
        txt, form = sql_from_node(stmt.value)
        if txt is not None:
            out[t.id] = (txt, f"const:{form}")
            continue
        v = stmt.value
        if isinstance(v, (ast.List, ast.Tuple)) and v.elts:
            first, f0 = sql_from_node(v.elts[0])
            if first is not None:
                out[t.id] = (first, f"const-list[{len(v.elts)}]:{f0}")
    return out


def scan_function(fn: Func, mod_consts: dict[str, tuple[str, str]]) -> None:
    nodes, _parent = own_nodes(fn.node)

    # Name -> SQL text: module constants, then function-local assignments, then
    # loop variables iterating over a schema constant (`for stmt in SCHEMA.split(";")`).
    name_sql: dict[str, tuple[str, str]] = dict(mod_consts)
    for n in nodes:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            txt, form = sql_from_node(n.value, name_sql)
            if txt is not None:
                name_sql.setdefault(n.targets[0].id, (txt, form))
        elif isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            it = n.iter
            base = None
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute):
                base = it.func.value  # X.split(";")
            elif isinstance(it, (ast.Name, ast.List, ast.Tuple)):
                base = it
            if base is not None:
                txt, form = sql_from_node(base, name_sql)
                if txt is None and isinstance(base, (ast.List, ast.Tuple)) and base.elts:
                    txt, form = sql_from_node(base.elts[0], name_sql)
                if txt is not None:
                    name_sql.setdefault(n.target.id, (txt, f"loopvar/{form}"))

    for n in nodes:
        # --- with-blocks -------------------------------------------------
        if isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                ce = item.context_expr
                if is_db_txn(ce):
                    fn.txn_blocks.append((n.lineno, getattr(n, "end_lineno", n.lineno)))
                    fn.boundaries.append(Boundary(n.lineno, "txn_enter", "with db.txn"))
                    fn.boundaries.append(
                        Boundary(getattr(n, "end_lineno", n.lineno), "txn_exit", "with db.txn")
                    )
                elif isinstance(ce, (ast.Name, ast.Attribute)):
                    fn.boundaries.append(
                        Boundary(n.lineno, "with_conn", ast.unparse(ce))
                    )

        # --- assignments that are boundaries ------------------------------
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr in ("isolation_level", "autocommit"):
                    fn.boundaries.append(Boundary(n.lineno, t.attr, ast.unparse(t)))

        # --- calls ---------------------------------------------------------
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                recv = ast.unparse(f.value)
                if f.attr in ("execute", "executemany", "executescript"):
                    arg = n.args[0] if n.args else None
                    txt, form = (None, "unknown")
                    if arg is not None:
                        txt, form = sql_from_node(arg, name_sql)
                    kind = classify_sql(txt)
                    if f.attr == "executescript":
                        # executescript is BOTH a write and a commit boundary.
                        fn.boundaries.append(
                            Boundary(n.lineno, "executescript", f"{recv}.executescript")
                        )
                        if kind in (KIND_UNKNOWN, KIND_TXNCTL):
                            kind = KIND_DDL
                    if kind == KIND_TXNCTL and txt and txt.strip().upper().startswith("COMMIT"):
                        fn.boundaries.append(
                            Boundary(n.lineno, "execute_COMMIT", f"{recv}.{f.attr}")
                        )
                    fn.ops.append(
                        SqlOp(
                            line=n.lineno,
                            kind=kind,
                            form=form,
                            method=f"{recv}.{f.attr}",
                            sql=txt,
                            node=n,
                        )
                    )
                elif f.attr == "commit" and not n.args:
                    fn.boundaries.append(Boundary(n.lineno, "commit_call", f"{recv}.commit()"))

            # record the call for the call graph
            if isinstance(f, ast.Name):
                fn.calls.append((n.lineno, None, f.id))
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                fn.calls.append((n.lineno, f.value.id, f.attr))

    fn.boundaries.sort(key=lambda b: (b.line, b.kind))
    fn.ops.sort(key=lambda o: o.line)


# --------------------------------------------------------------------------
# Import maps + call-graph fixpoint
# --------------------------------------------------------------------------


def module_name(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    rel = rel[: -len(".py")]
    parts = rel.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1] or ["__init__"]
    return ".".join(parts)


def build_import_maps(tree: ast.AST, module: str) -> tuple[dict[str, str], dict[str, str]]:
    """(alias -> module) for `import x as y` / `from p import mod`,
    (name -> module) for `from p.mod import name`."""
    alias_mod: dict[str, str] = {}
    name_mod: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            base = n.module
            if not base.startswith("codebugs"):
                continue
            short = base[len("codebugs.") :] if base.startswith("codebugs.") else ""
            for a in n.names:
                # `from codebugs import db`  -> alias db == module "db"
                if base == "codebugs":
                    alias_mod[a.asname or a.name] = a.name
                else:
                    name_mod[a.asname or a.name] = short
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("codebugs"):
                    short = a.name[len("codebugs.") :] if a.name != "codebugs" else ""
                    alias_mod[a.asname or a.name.split(".")[-1]] = short
    return alias_mod, name_mod


def resolve_call(
    fn: Func,
    recv: str | None,
    name: str,
    alias_mod: dict[str, dict[str, str]],
    name_mod: dict[str, dict[str, str]],
    toplevel: dict[str, dict[str, str]],
) -> str | None:
    mod = fn.module
    if recv is None:
        # local module-level function?
        if name in toplevel.get(mod, {}):
            return toplevel[mod][name]
        # imported by name?
        tgt = name_mod.get(mod, {}).get(name)
        if tgt is not None and name in toplevel.get(tgt, {}):
            return toplevel[tgt][name]
        return None
    tgt = alias_mod.get(mod, {}).get(recv)
    if tgt is not None and name in toplevel.get(tgt, {}):
        return toplevel[tgt][name]
    return None


# --------------------------------------------------------------------------
# Dependency analysis inside one function
# --------------------------------------------------------------------------


def analyze_function(fn: Func) -> None:
    """Fill f1_pairs / f1_unproven / f2_reads.

    Read-dependence is tracked by propagating READ IDS through assignments
    (data dependence) and through guards/branches (control dependence).
    """
    reads = [o for o in fn.ops if o.kind == KIND_READ] + fn.helper_reads
    reads.sort(key=lambda o: o.line)
    for i, r in enumerate(reads):
        r.rid = i
    read_by_id = {r.rid: r for r in reads}
    read_nodes = {id(r.node): r.rid for r in reads if r.node is not None}

    derived: dict[str, set[int]] = {}
    ctrl: dict[int, set[int]] = {}

    def ids_in(node: ast.AST | None) -> set[int]:
        if node is None:
            return set()
        out: set[int] = set()
        for n in ast.walk(node):
            if id(n) in read_nodes:
                out.add(read_nodes[id(n)])
            if isinstance(n, ast.Name):
                out |= derived.get(n.id, set())
        return out

    def target_names(t: ast.AST) -> list[str]:
        if isinstance(t, ast.Name):
            return [t.id]
        if isinstance(t, (ast.Tuple, ast.List)):
            out = []
            for e in t.elts:
                out += target_names(e)
            return out
        return []

    def terminates(body: list[ast.stmt]) -> bool:
        return bool(body) and isinstance(
            body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break)
        )

    def process(body: list[ast.stmt], guard: set[int]) -> None:
        g = set(guard)
        for stmt in body:
            ctrl[id(stmt)] = set(g)
            if isinstance(stmt, ast.Assign):
                src = ids_in(stmt.value)
                for t in stmt.targets:
                    for nm in target_names(t):
                        derived.setdefault(nm, set())
                        derived[nm] |= src | g
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                src = ids_in(stmt.value)
                for nm in target_names(stmt.target):
                    derived.setdefault(nm, set())
                    derived[nm] |= src | g
            elif isinstance(stmt, ast.AugAssign):
                src = ids_in(stmt.value)
                for nm in target_names(stmt.target):
                    derived.setdefault(nm, set())
                    derived[nm] |= src | g
            elif isinstance(stmt, ast.If):
                t_ids = ids_in(stmt.test)
                process(stmt.body, g | t_ids)
                process(stmt.orelse, g | t_ids)
                if terminates(stmt.body) or terminates(stmt.orelse):
                    # a check-then-act guard: everything after it is controlled
                    g |= t_ids
            elif isinstance(stmt, ast.While):
                t_ids = ids_in(stmt.test)
                process(stmt.body, g | t_ids)
                process(stmt.orelse, g | t_ids)
            elif isinstance(stmt, ast.For):
                src = ids_in(stmt.iter)
                for nm in target_names(stmt.target):
                    derived.setdefault(nm, set())
                    derived[nm] |= src | g
                process(stmt.body, g | src)
                process(stmt.orelse, g | src)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                src: set[int] = set()
                for item in stmt.items:
                    src |= ids_in(item.context_expr)
                    if item.optional_vars is not None:
                        for nm in target_names(item.optional_vars):
                            derived.setdefault(nm, set())
                            derived[nm] |= ids_in(item.context_expr) | g
                process(stmt.body, g | src)
            elif isinstance(stmt, ast.Try):
                process(stmt.body, g)
                for h in stmt.handlers:
                    process(h.body, g)
                process(stmt.orelse, g)
                process(stmt.finalbody, g)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue

    body = getattr(fn.node, "body", [])
    for _ in range(3):  # cheap fixed point over loops / forward refs
        process(body, set())

    # map every node to its enclosing recorded statement
    enclosing: dict[int, ast.stmt] = {}

    def map_enclosing(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            for n in ast.walk(stmt):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                enclosing.setdefault(id(n), stmt)
            for fieldname in ("body", "orelse", "finalbody"):
                sub = getattr(stmt, fieldname, None)
                if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                    map_enclosing(sub)
            for h in getattr(stmt, "handlers", []) or []:
                map_enclosing(h.body)

    map_enclosing(body)

    def dep_of(node: ast.AST) -> set[int]:
        ids = ids_in(node)
        st = enclosing.get(id(node))
        if st is not None:
            ids |= ctrl.get(id(st), set())
        return ids

    # ---- F1 --------------------------------------------------------------
    def separation(r: SqlOp, w: SqlOp) -> str | None:
        r_in = fn.in_txn(r.line)
        w_in = fn.in_txn(w.line)
        if not r_in and w_in:
            for a, b in fn.txn_blocks:
                if a <= w.line <= b and a > r.line:
                    return "A:read-outside/write-inside-txn"
            return None
        if not r_in and not w_in:
            mid = [
                b
                for b in fn.boundaries
                if r.line < b.line <= w.line and b.kind not in ("txn_enter", "txn_exit")
            ]
            if mid:
                return f"B:both-outside, {mid[0].kind}@{mid[0].line} between"
            return "B:both-outside, no transaction at all"
        if r_in and not w_in:
            for a, b in fn.txn_blocks:
                if a <= r.line <= b and b < w.line:
                    return "C:read-inside/write-after-txn"
            return None
        if r_in and w_in:
            for a, b in fn.txn_blocks:
                if a <= r.line <= b and a <= w.line <= b:
                    return None  # same block: protected
            return "D:different-txn-blocks"
        return None

    for w in fn.writes:
        deps = dep_of(w.node) if w.node is not None else set()
        for r in reads:
            if r.line >= w.line:
                continue
            sep = separation(r, w)
            if sep is None:
                continue
            if r.rid in deps:
                fn.f1_pairs.append((r, w, sep))
            else:
                fn.f1_unproven.append((r, w, sep))

    # ---- F2 --------------------------------------------------------------
    returned: set[int] = set()
    own, _p = own_nodes(fn.node)  # excludes nested defs, so a nested `return` cannot leak in
    for n in own:
        if isinstance(n, ast.Return) and n.value is not None:
            returned |= ids_in(n.value)
    post_boundaries = [
        b.line for b in fn.boundaries if b.kind in ("txn_exit", "commit_call", "execute_COMMIT",
                                                    "executescript", "isolation_level",
                                                    "autocommit", "with_conn")
    ]
    for r in reads:
        if r.rid not in returned:
            continue
        if fn.in_txn(r.line):
            continue
        earlier = [b for b in post_boundaries if b < r.line]
        if earlier:
            fn.f2_reads.append((r, max(earlier)))


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def build(root: str) -> tuple[list[Func], list[Func], dict]:
    """Scan `root` and return (all functions, population, meta).

    Exposed as a function so `predicates.py` computes coverage over EXACTLY the
    population printed here -- two scripts deriving the population separately is
    how the two halves of a claim start disagreeing.
    """
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for nm in sorted(names):
            if nm.endswith(".py"):
                files.append(os.path.join(dirpath, nm))
    files.sort()

    funcs: list[Func] = []
    alias_mod: dict[str, dict[str, str]] = {}
    name_mod: dict[str, dict[str, str]] = {}
    toplevel: dict[str, dict[str, str]] = {}

    for path in files:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, filename=path)
        mod = module_name(root, path)
        a, b = build_import_maps(tree, mod)
        alias_mod[mod] = a
        name_mod[mod] = b
        mfuncs = collect_defs(tree, mod, path)
        consts = module_sql_constants(tree)
        toplevel.setdefault(mod, {})
        for f in mfuncs:
            if "." not in f.qualname:
                toplevel[mod][f.qualname] = f.key
            f.mod_consts = consts
        funcs.extend(mfuncs)

    for f in funcs:
        scan_function(f, f.mod_consts)

    # --- transitive "this function reads" / "this function writes" --------
    # Both directions are needed. Resolving only reads made a function whose
    # WRITE also happens inside a helper (`add_finding` -> `_add_one`) invisible,
    # which is the same structural blindness the commit-spelling sweep had.
    edges: dict[str, set[str]] = {}
    for f in funcs:
        tgts = set()
        for line, recv, nm in f.calls:
            k = resolve_call(f, recv, nm, alias_mod, name_mod, toplevel)
            if k is not None and k != f.key:
                tgts.add(k)
        edges[f.key] = tgts

    def fixpoint(direct: dict[str, bool]) -> tuple[dict[str, bool], int]:
        trans = dict(direct)
        changed, rounds = True, 0
        while changed:
            changed = False
            rounds += 1
            for k, tgts in edges.items():
                if trans.get(k):
                    continue
                if any(trans.get(t) for t in tgts):
                    trans[k] = True
                    changed = True
        return trans, rounds

    reads_trans, rounds = fixpoint({f.key: bool(f.selects) for f in funcs})
    writes_trans, wrounds = fixpoint({f.key: bool(f.own_writes) for f in funcs})
    rounds = max(rounds, wrounds)

    def call_node(f: Func, line: int, recv: str | None, nm: str) -> ast.AST | None:
        for n in ast.walk(f.node):
            if isinstance(n, ast.Call) and n.lineno == line:
                fu = n.func
                if isinstance(fu, ast.Name) and fu.id == nm and recv is None:
                    return n
                if isinstance(fu, ast.Attribute) and fu.attr == nm:
                    return n
        return None

    for f in funcs:
        for line, recv, nm in f.calls:
            k = resolve_call(f, recv, nm, alias_mod, name_mod, toplevel)
            if k is None or k == f.key:
                continue
            node = None
            if reads_trans.get(k) or writes_trans.get(k):
                node = call_node(f, line, recv, nm)
            if reads_trans.get(k):
                f.helper_reads.append(
                    SqlOp(line=line, kind=KIND_READ, form="helper", method=k, sql=None, node=node)
                )
            if writes_trans.get(k):
                f.helper_writes.append(
                    SqlOp(line=line, kind=KIND_WRITE, form="helper", method=k, sql=None, node=node)
                )

    for f in funcs:
        analyze_function(f)

    pop = [f for f in funcs if f.f1_pairs or f.f2_reads]
    pop.sort(key=lambda f: (f.path, f.lineno))
    meta = {"files": len(files), "rounds": rounds, "root": root}
    return funcs, pop, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join("src", "codebugs"))
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON too")
    args = ap.parse_args()

    root = args.root
    if not os.path.isdir(root):
        print(f"no such root: {root}", file=sys.stderr)
        return 2

    funcs, pop, meta = build(root)
    n_files, rounds = meta["files"], meta["rounds"]

    # ---------------------------------------------------------------- print
    print("=" * 100)
    print("BT-1 POPULATION  --  computed by AST, not by reading")
    print("=" * 100)
    print(f"root              : {root}")
    print(f"python            : {sys.version.split()[0]}")
    print(f"files scanned     : {n_files}")
    print(f"functions scanned : {len(funcs)}  (nested defs counted separately)")
    print(f"call-graph fixpoint rounds: {rounds}")
    print()
    print("UNIT OF COUNT: a FUNCTION in which a read and a write are separated by a")
    print("transaction boundary (F1), or which returns a read taken after one (F2).")
    print("A commit SPELLING is not the unit -- `with db.txn` functions carry none.")
    print()

    # ---- table
    hdr = (
        f"{'#':>3}  {'fam':<5} {'dep':<3} {'sel':>3} {'prg':>3} {'hR':>3} {'wr':>3} {'hW':>3}  "
        f"{'txn':<11} {'function':<52} {'file:line'}"
    )
    print("-" * 100)
    print(hdr)
    print("-" * 100)
    for i, f in enumerate(pop, 1):
        fam = ("F1" if f.f1_pairs else "") + ("+" if f.f1_pairs and f.f2_reads else "") + (
            "F2" if f.f2_reads else ""
        )
        dep = "yes" if f.f1_pairs else ("-" if not f.f1_unproven else "no?")
        txn = ",".join(f"{a}-{b}" for a, b in f.txn_blocks) or "none"
        print(
            f"{i:>3}  {fam:<5} {dep:<3} {len(f.selects):>3} {len(f.pragmas):>3} "
            f"{len(f.helper_reads):>3} {len(f.own_writes):>3} {len(f.helper_writes):>3}  "
            f"{txn:<11} {f.name:<52} {f.loc}"
        )
    print("-" * 100)
    print()

    # ---- legend
    print("LEGEND  (number -> file:line -> function)")
    print("-" * 100)
    for i, f in enumerate(pop, 1):
        print(f"[{i:>3}] {f.loc:<44} {f.name}")
    print("-" * 100)
    print()

    # ---- per-site detail
    print("PER-SITE DETAIL")
    print("=" * 100)
    for i, f in enumerate(pop, 1):
        fam = ("F1" if f.f1_pairs else "") + ("+" if f.f1_pairs and f.f2_reads else "") + (
            "F2" if f.f2_reads else ""
        )
        print(f"[{i:>3}] {f.name}   {f.loc}   family={fam}")
        if f.txn_blocks:
            for a, b in f.txn_blocks:
                print(f"        with db.txn : lines {a}..{b}")
        else:
            print("        with db.txn : NONE")
        for b in f.boundaries:
            if b.kind in ("txn_enter", "txn_exit"):
                continue
            print(f"        boundary    : {f.path}:{b.line}  {b.kind}  {b.detail}")
        for o in f.ops:
            if o.kind == KIND_TXNCTL:
                continue
            tag = "IN-TXN " if f.in_txn(o.line) else "outside"
            flag = ""
            if o.kind == KIND_PRAGMA:
                flag = "  <-- PRAGMA, NOT a SELECT"
            print(
                f"        {o.kind:<7} {tag} {f.path}:{o.line}  [{o.form}] "
                f"{o.method}  {excerpt(o.sql)}{flag}"
            )
        for o in f.helper_reads:
            tag = "IN-TXN " if f.in_txn(o.line) else "outside"
            print(f"        read    {tag} {f.path}:{o.line}  [via helper] -> {o.method}")
        for o in f.helper_writes:
            tag = "IN-TXN " if f.in_txn(o.line) else "outside"
            print(f"        write   {tag} {f.path}:{o.line}  [via helper] -> {o.method}")
        for r, w, sep in f.f1_pairs:
            print(
                f"        F1 PAIR : read@{r.line} -> write@{w.line}  ({sep})  "
                f"dependence=proven"
            )
        for r, w, sep in f.f1_unproven[:4]:
            print(
                f"        f1?     : read@{r.line} -> write@{w.line}  ({sep})  "
                f"dependence=NOT proven"
            )
        for r, bl in f.f2_reads:
            print(
                f"        F2 READ : read@{r.line} after boundary@{bl}, result reaches return "
                f"[{r.form}]"
            )
        print()

    # ---- counters
    f1 = [f for f in pop if f.f1_pairs]
    f2 = [f for f in pop if f.f2_reads]
    both = [f for f in pop if f.f1_pairs and f.f2_reads]
    only_unproven = [f for f in funcs if f.f1_unproven and not f.f1_pairs and not f.f2_reads]
    unknown_sql = [(f, o) for f in funcs for o in f.unknowns]

    print("=" * 100)
    print("COUNTERS")
    print("=" * 100)
    print(f"functions scanned                                : {len(funcs)}")
    print(f"functions touching SQL at all                    : "
          f"{sum(1 for f in funcs if f.ops)}")
    print(f"functions with a `with db.txn` block             : "
          f"{sum(1 for f in funcs if f.txn_blocks)}")
    print(f"functions with any commit spelling               : "
          f"{sum(1 for f in funcs if any(b.kind not in ('txn_enter','txn_exit') for b in f.boundaries))}")
    print()
    print(f"POPULATION (F1 or F2)                            : {len(pop)}")
    print(f"  F1 (read/write split by a boundary, dep proven): {len(f1)}")
    print(f"  F2 (post-boundary read reaching a return)      : {len(f2)}")
    print(f"  both F1 and F2                                 : {len(both)}")
    print(f"  F1 only                                        : {len(f1) - len(both)}")
    print(f"  F2 only                                        : {len(f2) - len(both)}")
    print()
    print(f"separated read/write pairs with dependence NOT proven, in functions")
    print(f"  that are otherwise NOT in the population       : {len(only_unproven)}")
    for f in only_unproven:
        r, w, sep = f.f1_unproven[0]
        print(f"      {f.name:<50} {f.path}:{r.line}->{w.line} ({sep})")
    print()
    f2_forms: dict[str, int] = {}
    for f in f2:
        for r, _b in f.f2_reads:
            f2_forms[r.form] = f2_forms.get(r.form, 0) + 1
    print("F2 read forms (the question 'how does the read get its SQL'):")
    for k in sorted(f2_forms):
        print(f"  {k:<16} {f2_forms[k]}")
    print()
    print(f"UNRESOLVED SQL text (script blindness, printed rather than hidden): "
          f"{len(unknown_sql)}")
    for f, o in unknown_sql[:40]:
        print(f"      {f.path}:{o.line}  {o.method}  in {f.name}")
    print()

    # ---- self-check against the sites the brief names
    print("=" * 100)
    print("SELF-CHECK -- sites the brief names as MANDATORY finds")
    print("=" * 100)
    print("(keyed by function, NOT by line number: line numbers in the brief were")
    print(" taken from an earlier revision and a stale number would hide a real find)")
    print()
    expected = [
        ("findings.add_finding", "F1"),
        ("findings.batch_add_findings", "F1"),
        ("milestones.closegate.mark_branch_only", "F2"),
        ("milestones.triage.triage_promote", "F2"),
        ("reqs.add_requirement", "F2"),
        ("reqs._migrate_to_lowercase", "F1"),
        ("milestones.foundation.create_milestone", "F2"),
        ("milestones.foundation.update_milestone", "F1/F2"),
        ("milestones.foundation.add_milestone_item", "F2"),
    ]
    popkeys = {f.name for f in pop}
    for nm, want in expected:
        f = next((x for x in funcs if x.name == nm), None)
        if f is None:
            print(f"  MISSING FUNCTION  {nm:<45} (not found under {root})")
            continue
        got = ("F1" if f.f1_pairs else "") + ("+" if f.f1_pairs and f.f2_reads else "") + (
            "F2" if f.f2_reads else ""
        )
        mark = "IN POPULATION " if f.name in popkeys else "NOT IN POP    "
        detail = ""
        if f.f1_pairs:
            r, w, sep = f.f1_pairs[0]
            detail = f"read@{r.line}->write@{w.line} {sep}"
        elif f.f2_reads:
            r, bl = f.f2_reads[0]
            detail = f"read@{r.line} after boundary@{bl}"
        else:
            txn = ",".join(f"{a}-{b}" for a, b in f.txn_blocks) or "no txn"
            detail = (
                f"txn={txn}; selects={[o.line for o in f.selects]}; "
                f"helper-reads={[o.line for o in f.helper_reads]}; "
                f"own-writes={[o.line for o in f.own_writes]}; "
                f"helper-writes={[o.line for o in f.helper_writes]}"
            )
        print(f"  {mark} want={want:<6} got={got or '-':<5} {nm:<45} {f.loc}")
        print(f"        {detail}")
    print()

    if args.json:
        blob = [
            {
                "n": i,
                "name": f.name,
                "file": f.path,
                "line": f.lineno,
                "f1": [(r.line, w.line, sep) for r, w, sep in f.f1_pairs],
                "f2": [(r.line, b, r.form) for r, b in f.f2_reads],
                "txn": f.txn_blocks,
            }
            for i, f in enumerate(pop, 1)
        ]
        print("JSON")
        print(json.dumps(blob, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
