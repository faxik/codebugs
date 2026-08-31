"""Every exception table under ``tests/`` carries BOTH halves of the discipline.

An EXCEPTION TABLE is a declared table each of whose rows takes one case out
from under a check. This suite has many, and until CB-179 every author
re-invented the discipline that keeps one honest. Sometimes correctly.

The discipline has two halves, and neither implies the other:

- **self-deletion** — a row naming an entity that no longer exists is a test
  failure, so the table can only SHRINK. Without it the table becomes the
  place real regressions are quietly parked.
- **a reason** — a row with a blank or missing textual justification is a
  test failure, and the reason must live in a field a test READS, never in a
  comment beside the table. Without it nobody knows why the row is there.

A gate cannot be a rule expressed as prose, because prose is what failed:
CB-165 measured the population at 9 tables, CB-179 re-measured it at 20, and
the re-measurement found that the very table CB-165 named as its EXEMPLAR had
been missed. Both counts were produced by searching for suggestive NAMES, and
``ASSEMBLED_BY_THE_WRAPPER`` carries no suggestive word at all. So this gate
keys on USE, not on names, and it is a gate rather than a helper because a
helper only reaches the files somebody carried it into: table twenty-one gets
the discipline by construction here, and by luck there.


WHAT THIS GATE DOES NOT SEE
===========================

Stated at the width the check actually holds, because a gate whose promise is
wider than its check is the defect this direction exists to close.

1. **Local variables inside a test body.** ``allowed`` in
   ``tests/test_claims.py`` is an exception list built and consumed inside one
   function; the gate reads module-level and class-level declarations only.
   A table that lives for the duration of one call is not something a later
   author can park a row in, which is the harm the discipline addresses.
2. **Anything outside ``tests/``.** ``src/`` has declared tables of its own
   (``db.py``'s resolver registries, for one) and they keep their own
   schedule.
3. **Indirection that hides the name.** A table held in a list and reached by
   position, a ``getattr(module, name)``, a name built by string arithmetic —
   the resolver follows plain names, attribute access, one level of
   registry subscript with a constant key, a ``for`` loop over a literal tuple
   of tables, and local aliases assigned from the table. It follows nothing
   else, and a candidate whose reads it cannot find at all is REFUSED rather
   than reported clean (`test_every_candidate_table_can_be_resolved`).
4. **Whether the reason is TRUE.** The gate enforces that an assertion exists,
   is non-empty and stays current. It cannot read.
5. **Whether a declared non-table really is one.** ``DECLARED_EXCEPTIONS``
   below carries the gate's own judgement calls, and it is subject to the same
   two halves as everything it exempts (`TestTheGatesOwnTable`).
6. **A half written in a shape it does not recognise.** The two halves are
   found by SHAPE, and the shapes are listed at `_analyse_function`: a reason
   gate reads the table's values through ``.items()``/``.values()``/``.get()``
   and applies a string predicate to the bound value; a self-deletion gate
   enumerates the KEYS and holds one against something that is not the table
   (a membership test, a ``.get`` on another mapping, or a set DIFFERENCE with
   the table on the left). A perfectly good gate written another way — an
   equality loop, a ``startswith`` — is reported MISSING, not accepted. That
   direction is deliberate and it cost something real while this gate was
   being built: the first self-deletion check written for ``_PRUNED_PATHS``
   used ``startswith`` and was refused, so it was rewritten as a membership
   test. A false refusal is loud and gets fixed; a false pass is the thing
   this file exists to stop.
7. **Whether the world a gate checks against is the RIGHT world.** The gate
   sees that keys are held against something external. It cannot tell a real
   oracle from a set that trivially contains every key.

The recognition rule and its cost are argued at ``_is_candidate``.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)

# Reason texts shorter than this are a declaration in form only. The bar is
# deliberately the same one `tests/test_update_parity.py` already set for its
# own tables, so two gates in one tree cannot disagree about what a reason is.
MIN_REASON_LEN = 20


# --------------------------------------------------------------------------
# The gate's own exception table.
#
# (file, qualified table name) -> why this declared table is NOT an exception
# table, and therefore owes neither half.
#
# Both halves apply to this table too, by `TestTheGatesOwnTable`: a row with no
# reason fails, and a row naming a table the recogniser no longer flags fails.
# Without that, the first inconvenient case parks here silently and the gate
# becomes the hole it exists to close, one level up.
# --------------------------------------------------------------------------
DECLARED_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("tests/manual/repro_cb52_severity_freeze.py", "OBS"): (
        "A fixture: three observations the reproduction files in order. The "
        "string values are severities, not reasons, and no check consults "
        "membership to excuse anything."
    ),
    ("tests/test_dedup.py", "TestAttentionBlock.ESCALATED"): (
        "Expected values, not exemptions: the from/to severities the attention "
        "block must report. Membership is never consulted."
    ),
    ("tests/test_server.py", "TestAttentionOverTheWire.ESCALATED"): (
        "The same expected-value fixture as its test_dedup twin, asserted over "
        "the wire instead of in-process."
    ),
    ("tests/test_milestones.py", "TestEligibilitySeam.REL"): (
        "A milestone fixture — the release row the eligibility cases are built "
        "against. Its string values are a kind and a name, not reasons."
    ),
    ("tests/test_milestones.py", "TestEligibilitySeam.STREAM"): (
        "The stream twin of REL, same fixture role, same reason."
    ),
    ("tests/test_row_limit_gate.py", "TestTheGateAgainstAnEvasionBattery.CAUGHT"): (
        "An ORACLE, and the inverse of an exemption: every row is a source "
        "snippet the gate MUST flag. A stale row here weakens no check — it "
        "fails the battery it belongs to."
    ),
    ("tests/test_row_limit_gate.py", "TestTheGateAgainstAnEvasionBattery.ESCAPES"): (
        "The declared-miss half of the same battery: snippets the gate is "
        "measured NOT to catch, asserted as such. The rows announce a known "
        "limit rather than excusing a real site from a check."
    ),
    ("tests/test_row_limit_gate.py", "TestTheGateAgainstAnEvasionBattery.QUIET"): (
        "The third arm of the same battery — snippets that must produce no "
        "finding at all. Same oracle role as CAUGHT and ESCAPES."
    ),
    ("tests/test_two_valued_path_gate.py", "_PATHLIB_PURE_PREDICATES"): (
        "A VOCABULARY the gate matches on, not an exemption from it: pathlib "
        "predicates that answer from the path string and touch no filesystem, "
        "so they were never two-valued reads to begin with."
    ),
    ("tests/test_worktree_harness.py", "CASCADE_CORPUS"): (
        "A corpus fixture: cascade-id lines the mint tests are run against. "
        "Membership excuses nothing."
    ),
    ("tests/test_readme_surface.py", "_GLOBAL_FLAGS"): (
        "A LEXER vocabulary. `_verb_of` skips global flags while walking "
        "tokens to find the verb; the `continue` advances a parser, it does "
        "not wave a case past a check."
    ),
    ("tests/test_readme_surface.py", "_GLOBAL_OPTS_WITH_VALUE"): (
        "The value-taking half of the same lexer vocabulary, consumed by the "
        "same token walk in `_verb_of`."
    ),
}


# --------------------------------------------------------------------------
# Reading the tree.
# --------------------------------------------------------------------------


def _test_sources(root: str = TESTS_DIR) -> list[tuple[str, ast.Module]]:
    """Every ``.py`` file under ``tests/``, parsed, deepest paths last.

    ``conftest.py`` is deliberately included: two of the five tables CB-179
    found incomplete live there, and excluding a file because it holds no
    tests is exactly the unchecked premise this gate exists to refuse.
    """
    base = os.path.dirname(os.path.abspath(root))
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, base).replace(os.sep, "/")
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            out.append((rel, ast.parse(source, filename=rel)))
    return out


_CONTAINER_CALLS = frozenset({"frozenset", "set", "dict", "list", "tuple"})


def _is_container(node: ast.AST) -> bool:
    if isinstance(node, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _CONTAINER_CALLS
    )


def _is_string_expr(node: ast.AST) -> bool:
    """A string literal, an f-string, or literals concatenated with ``+``."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_expr(node.left) and _is_string_expr(node.right)
    return False


@dataclass
class Table:
    file: str
    qualname: str
    name: str
    lineno: int
    is_dict: bool
    reason_shaped: bool
    exempting_continue: bool = False
    reads: list = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.qualname)


def _declared_tables(root: str = TESTS_DIR) -> list[Table]:
    """Every container assigned at module or class level under ``tests/``.

    Function bodies are not descended into: see limit 1 in the module
    docstring.
    """
    tables: list[Table] = []
    for rel, tree in _test_sources(root):

        def walk(body, scope):
            for stmt in body:
                if isinstance(stmt, ast.ClassDef):
                    walk(stmt.body, scope + [stmt.name])
                    continue
                name = value = annotation = None
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name, value = stmt.target.id, stmt.value
                    annotation = ast.unparse(stmt.annotation).replace(" ", "")
                elif (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                ):
                    name, value = stmt.targets[0].id, stmt.value
                if name is None or value is None or not _is_container(value):
                    continue
                is_dict = isinstance(value, ast.Dict)
                declared_str_dict = bool(
                    annotation
                    and annotation.startswith("dict[")
                    and annotation.rstrip("]").endswith("str")
                )
                inferred = is_dict and bool(value.values) and all(
                    _is_string_expr(v) for v in value.values
                )
                tables.append(
                    Table(
                        file=rel,
                        qualname=".".join(scope + [name]),
                        name=name,
                        lineno=stmt.lineno,
                        is_dict=is_dict,
                        reason_shaped=declared_str_dict or inferred,
                    )
                )

        walk(tree.body, [])
    return tables


# --------------------------------------------------------------------------
# Resolving reads.
#
# A read is attributed by NAME, and the attribution FAILS CLOSED. Reads inside
# the declaring file belong to that file's table; reads elsewhere belong to the
# unique declaring table when there is exactly one. When a name is declared in
# several files and read in a third, nothing is attributed and the table is
# reported unresolved rather than credited with a gate that may belong to a
# namesake — four different tables are called `DECLARED_EXCEPTIONS` in this
# suite, and crediting one's reason test to another is precisely how a gate
# reports clean because it could not look.
# --------------------------------------------------------------------------

_VIEW_CALLS = frozenset({"set", "frozenset", "sorted", "list", "tuple"})
_STRING_PREDICATE_METHODS = frozenset({"strip", "split", "lower", "casefold", "rstrip", "lstrip"})


def _registries(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level dict-of-dicts: inner key -> the table names stored there.

    `tests/test_update_parity.py` reaches eight of this suite's tables only
    through such a registry (``ENTITIES[entity]["immutable"]``), so a resolver
    that followed plain names alone would report eight complete tables as
    unresolved. Exactly one level of constant-key subscript is followed; see
    limit 3 in the module docstring.
    """
    found: dict[str, set[str]] = {}
    for stmt in tree.body:
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            value = stmt.value
        if not isinstance(value, ast.Dict):
            continue
        for outer in value.values:
            if not isinstance(outer, ast.Dict):
                continue
            for key, inner in zip(outer.keys, outer.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(inner, ast.Name)
                ):
                    found.setdefault(key.value, set()).add(inner.id)
    return found


class _Scope:
    """What each name in one function body denotes, in table terms."""

    def __init__(self, table_names: set[str], registries: dict[str, set[str]]):
        self.table_names = table_names
        self.registries = registries
        self.alias: dict[str, set[str]] = {}
        self.key_bound: dict[str, set[str]] = {}
        self.value_bound: dict[str, set[str]] = {}

    def denotes(self, node: ast.AST) -> set[str]:
        """The tables this expression stands for; empty when it stands for none."""
        if isinstance(node, ast.Name):
            if node.id in self.table_names:
                return {node.id}
            return set(self.alias.get(node.id, ()))
        if isinstance(node, ast.Attribute):
            if node.attr in self.table_names:
                return {node.attr}
            return set()
        if isinstance(node, ast.Subscript):
            slc = node.slice
            if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
                return set(self.registries.get(slc.value, ())) & self.table_names
            return set()
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _VIEW_CALLS and node.args:
                return self.denotes(node.args[0])
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("items", "values", "keys"):
                return self.denotes(node.func.value)
            return set()
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitOr, ast.BitAnd)):
            return self.denotes(node.left) | self.denotes(node.right)
        return set()

    def bind_target(self, target: ast.AST, tables: set[str], *, as_value: bool) -> None:
        store = self.value_bound if as_value else self.key_bound
        if isinstance(target, ast.Name):
            store.setdefault(target.id, set()).update(tables)
            if not as_value:
                self.alias.setdefault(target.id, set()).update(tables)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self.bind_target(element, tables, as_value=as_value)


@dataclass
class _Use:
    reason_half: bool = False
    staleness_half: bool = False


def _analyse_function(
    fn: ast.AST, table_names: set[str], registries: dict[str, set[str]]
) -> dict[str, _Use]:
    """What one function body does with each table it reads.

    THE RECOGNISED SHAPES, listed because the module docstring's limit 6 sends
    a reader here and a promise wider than its text is this file's own subject.

    A **reason gate** is: the table reached through ``.items()`` / ``.values()``
    / ``.get(...)`` / a subscript, the VALUE side bound to a name, and a string
    predicate applied to THAT name somewhere in the same function — ``.strip()``
    and its siblings, ``len(...)``, or ``isinstance(...)``. Binding the value is
    what makes this precise: a self-deletion test also iterates ``.items()``, so
    keying on the call alone would credit it with a reason gate it does not
    have.

    A **self-deletion gate** is: the table's KEYS enumerated — ``for k in T``, a
    comprehension over ``T``, ``set(T)``/``sorted(T)``, or the key half of an
    ``.items()`` unpacking — and one of those keys then held against something
    that is NOT the table:

    * a membership test whose LEFT is a key-bound name and whose comparator
      denotes no table (``k not in live``). The asymmetry is deliberate: with
      it symmetric, ``assert kept not in pruned`` — which asks the opposite
      question — would be credited as self-deletion;
    * ``<something not the table>.get(k)``;
    * a set DIFFERENCE with the table on the LEFT (``(mutable | immutable) -
      columns``). Only ``-``, and only that way round: ``set(parts) &
      set(_PRUNED_NAMES)`` asserts that no pruned name appears in the tree,
      which is a consequence check rather than a stale-row check, and an
      intersection rule would have credited it.

    A comparator that denotes the table itself never counts, so
    ``[k for k in T if k not in T]`` — vacuous by construction — earns nothing.

    Binding runs as its own pass BEFORE judging, so the verdict cannot depend
    on the order statements happen to appear in: a comprehension may read a
    name the statement above it bound, and an assertion may read a loop
    variable bound further up.
    """
    scope = _Scope(table_names, registries)
    uses: dict[str, _Use] = {}

    def use(name: str) -> _Use:
        return uses.setdefault(name, _Use())

    def bind_iteration(target: ast.AST, iter_node: ast.AST) -> None:
        # `for table in (A, B): ...` — a literal tuple of tables binds an alias.
        if isinstance(iter_node, (ast.Tuple, ast.List)):
            gathered: set[str] = set()
            for element in iter_node.elts:
                gathered |= scope.denotes(element)
            if gathered:
                scope.bind_target(target, gathered, as_value=False)
                scope.bind_target(target, gathered, as_value=True)
            return
        tables = scope.denotes(iter_node)
        if not tables:
            return
        items = (
            isinstance(iter_node, ast.Call)
            and isinstance(iter_node.func, ast.Attribute)
            and iter_node.func.attr == "items"
        )
        values_only = (
            isinstance(iter_node, ast.Call)
            and isinstance(iter_node.func, ast.Attribute)
            and iter_node.func.attr == "values"
        )
        if items and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 2:
            scope.bind_target(target.elts[0], tables, as_value=False)
            scope.bind_target(target.elts[1], tables, as_value=True)
        elif values_only:
            scope.bind_target(target, tables, as_value=True)
        else:
            scope.bind_target(target, tables, as_value=False)

    # One pass to bind, one to judge: a comprehension can use a name the
    # statement above it bound, and an assertion can read a loop variable bound
    # further up. Binding while judging would make the verdict depend on the
    # order statements happen to appear in.
    for node in ast.walk(fn):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            bind_iteration(node.target, node.iter)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for generator in node.generators:
                bind_iteration(generator.target, generator.iter)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            # `mutable, immutable = set(spec["mutable"]), set(spec["immutable"])`
            # — a parallel assignment is two bindings, and reading it as one
            # lost `FINDINGS_IMMUTABLE`'s staleness gate outright (measured).
            if (
                isinstance(target, (ast.Tuple, ast.List))
                and isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
            ):
                for sub_target, sub_value in zip(target.elts, value.elts):
                    sub_tables = scope.denotes(sub_value)
                    if sub_tables:
                        scope.bind_target(sub_target, sub_tables, as_value=False)
                continue
            tables = scope.denotes(value)
            if tables:
                scope.bind_target(target, tables, as_value=False)
            elif isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
                # `reason = spec["gaps"].get((param, layer))`
                owner = scope.denotes(value.func.value)
                if owner and value.func.attr == "get":
                    scope.bind_target(target, owner, as_value=True)
            elif isinstance(value, ast.Subscript):
                owner = scope.denotes(value.value)
                if owner:
                    scope.bind_target(target, owner, as_value=True)

    # --- half one: a reason field is read and judged as text -------------
    for node in ast.walk(fn):
        subject = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _STRING_PREDICATE_METHODS:
                subject = node.func.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "len" and node.args:
                subject = node.args[0]
            elif node.func.id == "isinstance" and node.args:
                subject = node.args[0]
        if subject is None:
            continue
        # `len(reason.strip())` — walk down to the bound name.
        while isinstance(subject, ast.Call) and isinstance(subject.func, ast.Attribute):
            subject = subject.func.value
        if isinstance(subject, ast.Name):
            for name in scope.value_bound.get(subject.id, ()):
                use(name).reason_half = True

    # --- half two: keys are enumerated and held against the world ---------
    def keys_of(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Name):
            return set(scope.key_bound.get(node.id, ()))
        return set()

    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if not isinstance(op, (ast.In, ast.NotIn)):
                    continue
                if scope.denotes(comparator):
                    continue  # held against the table itself: proves nothing
                for name in keys_of(node.left):
                    use(name).staleness_half = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args and not scope.denotes(node.func.value):
                for name in keys_of(node.args[0]):
                    use(name).staleness_half = True
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            # `(mutable | immutable) - columns` — declared minus real.
            left, right = scope.denotes(node.left), scope.denotes(node.right)
            if left and not right:
                for name in left:
                    use(name).staleness_half = True

    return uses


def _collect_uses(tables: list[Table], root: str = TESTS_DIR) -> dict[tuple[str, str], _Use]:
    """Fold every test function's verdict onto the table it actually names."""
    by_name: dict[str, list[Table]] = {}
    for table in tables:
        by_name.setdefault(table.name, []).append(table)

    verdicts: dict[tuple[str, str], _Use] = {t.key: _Use() for t in tables}
    for rel, tree in _test_sources(root):
        registries = _registries(tree)
        local = {t.name for t in tables if t.file == rel}
        visible = {name for name in by_name if name in local or len(by_name[name]) == 1}
        if not visible:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            for name, use in _analyse_function(node, visible, registries).items():
                owners = [t for t in by_name[name] if t.file == rel] or by_name[name]
                if len(owners) != 1:
                    continue
                verdict = verdicts[owners[0].key]
                verdict.reason_half |= use.reason_half
                verdict.staleness_half |= use.staleness_half
    return verdicts


# --------------------------------------------------------------------------
# Recognition.
# --------------------------------------------------------------------------


def _mark_exempting_continue(tables: list[Table], root: str = TESTS_DIR) -> None:
    """A membership test that syntactically guards a ``continue``.

    This is the second recogniser, and it exists because the FIRST one cannot
    see the case CB-179 called its most significant: `_EXCLUDED_ACTION_ATTRS`
    in `tests/cli_surface.py` is a `frozenset` of bare strings, so it carries
    no reason field to be shaped like one — which IS its defect, and a
    recogniser blind to it would have certified the very table the card was
    filed for.

    It is deliberately narrow. `if x in TABLE: return True` and
    `if x in TABLE: <expression>` are NOT read as exemptions, because a
    membership answering a question is not a membership waving a case past a
    check, and widening it here pulls in every vocabulary the suite matches on
    — `NETWORK_MODULES`, `_DML_VERBS`, `_EXEC_ATTRS`,
    `_OS_PATH_PRIMITIVE_TEXTS` and their kind, which membership SELECTS FOR a
    check rather than exempting from one. The narrow rule still catches two
    lexer vocabularies in `tests/test_readme_surface.py`, and they are declared
    out by name in `DECLARED_EXCEPTIONS`. No count is quoted here on purpose:
    what that population is today is a question for `DECLARED_EXCEPTIONS` and
    for the gate's own tests, and a number written into prose is the thing this
    repository has repeatedly been wrong about.
    """
    names = {t.name for t in tables}
    hits: set[str] = set()
    for _rel, tree in _test_sources(root):
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            if not any(isinstance(stmt, ast.Continue) for stmt in node.body):
                continue
            for sub in ast.walk(node.test):
                if not isinstance(sub, ast.Compare):
                    continue
                for op, comparator in zip(sub.ops, sub.comparators):
                    if not isinstance(op, (ast.In, ast.NotIn)):
                        continue
                    if isinstance(comparator, ast.Name) and comparator.id in names:
                        hits.add(comparator.id)
                    elif isinstance(comparator, ast.Attribute) and comparator.attr in names:
                        hits.add(comparator.attr)
    for table in tables:
        if table.name in hits:
            table.exempting_continue = True


def _is_candidate(table: Table) -> bool:
    """Is this declared table one the discipline governs?

    Two recognisers, unioned, and the union is what the population needed:

    * **reason-shaped** — a ``dict`` whose values are all strings, or one
      ANNOTATED ``dict[..., str]``. The annotation arm is not decoration: four
      of this suite's tables are legitimately EMPTY today
      (`test_row_limit_gate`'s, `test_blockers`'s, `REQS_NON_WRITING`), and an
      empty dict with the right shape is exactly the prepared bed the first
      unchecked row lands in.
    * **exempting** — membership guards a ``continue`` (see above).

    Neither recogniser reads the NAME, and that is the whole point: the two
    counts this gate replaces were both produced by searching for suggestive
    names, and both missed `ASSEMBLED_BY_THE_WRAPPER`, which carries none.

    The cost is false positives — fixtures and oracles that happen to be
    string-valued dicts. They are declared out one by one in
    `DECLARED_EXCEPTIONS` with the reason they are not exception tables, which
    is the trade this repository has taken before: a declared miss costs a
    line, an undeclared one costs the gate.
    """
    return table.reason_shaped or table.exempting_continue


def _read_anywhere(tables: list[Table], root: str = TESTS_DIR) -> dict[tuple[str, str], bool]:
    """Whether the resolver can find ANY read of each table.

    A candidate nothing appears to consult is refused rather than reported
    clean: it means the resolver lost the table, not that the table is
    unused, and a gate reporting clean because it could not look is the defect
    this whole direction exists to close.
    """
    by_name: dict[str, list[Table]] = {}
    for table in tables:
        by_name.setdefault(table.name, []).append(table)
    seen: dict[tuple[str, str], bool] = {t.key: False for t in tables}
    for rel, tree in _test_sources(root):
        registries = _registries(tree)
        local = {t.name for t in tables if t.file == rel}
        visible = {name for name in by_name if name in local or len(by_name[name]) == 1}
        scope = _Scope(visible, registries)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue  # the declaration itself is not a read
            for name in scope.denotes(node):
                owners = [t for t in by_name[name] if t.file == rel] or by_name[name]
                if len(owners) == 1:
                    seen[owners[0].key] = True
    return seen


_TABLES = _declared_tables()
_mark_exempting_continue(_TABLES)
_CANDIDATES = [t for t in _TABLES if _is_candidate(t)]
_USES = _collect_uses(_TABLES)
_READ = _read_anywhere(_TABLES)
_GOVERNED = [t for t in _CANDIDATES if t.key not in DECLARED_EXCEPTIONS]


def _describe(table: Table) -> str:
    return f"{table.file}:{table.lineno} {table.qualname}"


# --------------------------------------------------------------------------
# The gate.
# --------------------------------------------------------------------------


class TestEveryExceptionTableCarriesBothHalves:
    """The gate itself. Table twenty-one is covered by construction."""

    def test_the_recogniser_finds_a_population_at_all(self):
        """A gate that recognises nothing reports clean.

        The floor is deliberately well below today's count: this pins that the
        walk, the parse and the two recognisers all still work, not the exact
        size of a population that is expected to move.
        """
        assert len(_CANDIDATES) >= 15, [_describe(t) for t in _CANDIDATES]
        assert any(t.exempting_continue for t in _CANDIDATES), (
            "the exempting-continue recogniser found nothing — it is the only "
            "one that can see a table with no reason field at all"
        )

    def test_every_candidate_table_can_be_resolved(self):
        """Refuse what cannot be judged; never fall silent on it."""
        lost = [_describe(t) for t in _GOVERNED if not _READ[t.key]]
        assert not lost, (
            f"these declared tables are recognised as exception tables but no "
            f"read of them could be resolved: {lost}. Either they are consulted "
            f"through indirection this gate does not follow (module docstring, "
            f"limit 3) — in which case declare them in DECLARED_EXCEPTIONS with "
            f"that reason — or they are dead. Reporting them clean is the one "
            f"answer that is not available."
        )

    def test_every_exception_table_has_a_reason_gate(self):
        missing = [_describe(t) for t in _GOVERNED if not _USES[t.key].reason_half]
        assert not missing, (
            f"these exception tables have no test reading their reason field: "
            f"{missing}. A row with a blank reason, or a reason living in a "
            f"comment beside the table, must fail a test — a comment is text no "
            f"test reads. Add a check over `.items()` asserting the value is a "
            f"real string."
        )

    def test_every_exception_table_has_a_self_deletion_gate(self):
        missing = [_describe(t) for t in _GOVERNED if not _USES[t.key].staleness_half]
        assert not missing, (
            f"these exception tables have no test refusing a stale row: "
            f"{missing}. A table that only ever grows becomes the place real "
            f"regressions are parked. Add a check enumerating the KEYS and "
            f"holding each against the live tree, so a row naming something "
            f"that no longer exists fails."
        )


class TestTheGatesOwnTable:
    """`DECLARED_EXCEPTIONS` above owes both halves like everything it exempts.

    The gate proper would catch this anyway — the table is reason-shaped, it
    sits under `tests/`, and it is not declared out of itself. These two tests
    are what make that self-application PASS rather than an accident, and they
    are written here so the failure names this file rather than arriving as one
    line in a list of offenders.
    """

    def test_every_row_carries_a_reason(self):
        empty = [
            key
            for key, reason in DECLARED_EXCEPTIONS.items()
            if not isinstance(reason, str) or len(reason.strip()) < MIN_REASON_LEN
        ]
        assert not empty, (
            f"DECLARED_EXCEPTIONS row(s) with no real reason: {empty} — a table "
            "that can grow silently is the hole this gate exists to close, one "
            "level up."
        )

    def test_no_row_is_stale(self):
        live = {t.key for t in _CANDIDATES}
        stale = [key for key in DECLARED_EXCEPTIONS if key not in live]
        assert not stale, (
            f"DECLARED_EXCEPTIONS names {stale}, which the recogniser no longer "
            "flags — delete the row rather than leaving a standing exemption "
            "behind. This table may only SHRINK."
        )


@pytest.mark.parametrize("key", sorted(DECLARED_EXCEPTIONS))
def test_a_declared_non_table_is_really_declared_once(key):
    """A key may name at most one candidate, so a row cannot cover two tables."""
    owners = [t for t in _CANDIDATES if t.key == key]
    assert len(owners) == 1, (owners, key)


# --------------------------------------------------------------------------
# The gate against itself.
# --------------------------------------------------------------------------


def _verdicts(root: str) -> dict[tuple[str, str], tuple[Table, _Use, bool]]:
    """Run the whole pipeline over an arbitrary ``tests``-shaped directory."""
    tables = _declared_tables(root)
    _mark_exempting_continue(tables, root)
    uses = _collect_uses(tables, root)
    read = _read_anywhere(tables, root)
    return {t.key: (t, uses[t.key], read[t.key]) for t in tables if _is_candidate(t)}


_GOOD_TABLE = '''
DECLARED: dict[str, str] = {"alpha": "because alpha is fine"}


def live():
    return ["alpha"]


class TestIt:
    def test_the_gate(self):
        offenders = [x for x in live() if x not in DECLARED]
        assert not offenders

    def test_every_row_carries_a_reason(self):
        empty = [k for k, reason in DECLARED.items() if not reason.strip()]
        assert not empty

    def test_no_row_is_stale(self):
        stale = [k for k in DECLARED if k not in live()]
        assert not stale
'''


class TestMutantOracle:
    """A gate nothing can make fail is not a gate (CB-179 §5).

    Every case below is built in a throwaway directory rather than by editing
    the real tree, so the probe cannot leave litter behind and cannot depend
    on what the suite happens to contain today. The CONTROL matters as much as
    the mutants: without it, a pipeline that flagged everything would pass all
    three mutant cases and prove nothing.
    """

    @staticmethod
    def _plant(tmp_path, body: str, name: str = "test_planted.py") -> str:
        root = tmp_path / "tests"
        root.mkdir(exist_ok=True)
        (root / name).write_text(body, encoding="utf-8")
        return str(root)

    def test_control_a_table_with_both_halves_is_accepted(self, tmp_path):
        root = self._plant(tmp_path, _GOOD_TABLE)
        _table, use, read = _verdicts(root)[("tests/test_planted.py", "DECLARED")]
        assert read and use.reason_half and use.staleness_half

    def test_a_reasonless_row_is_caught(self, tmp_path):
        """A row with no reason must fail the table's OWN reason gate.

        The gate here is structural — it asks whether a reason check exists —
        so the mutation that proves the composition is the one that DELETES
        the check, below. This case proves the other direction: the check the
        gate demands is a real one, which fails on a real blank row.
        """
        body = _GOOD_TABLE.replace(
            '{"alpha": "because alpha is fine"}', '{"alpha": "because alpha is fine", "beta": ""}'
        ).replace('return ["alpha"]', 'return ["alpha", "beta"]')
        namespace: dict = {}
        exec(compile(body, "<mutant>", "exec"), namespace)  # noqa: S102
        with pytest.raises(AssertionError):
            namespace["TestIt"]().test_every_row_carries_a_reason()

    def test_a_row_naming_something_gone_is_caught(self, tmp_path):
        """The self-deletion half, proved the same way: a stale row must fail."""
        body = _GOOD_TABLE.replace(
            '{"alpha": "because alpha is fine"}',
            '{"alpha": "because alpha is fine", "vanished": "named a thing that left"}',
        )
        namespace: dict = {}
        exec(compile(body, "<mutant>", "exec"), namespace)  # noqa: S102
        with pytest.raises(AssertionError):
            namespace["TestIt"]().test_no_row_is_stale()

    def test_a_table_that_loses_its_reason_gate_is_flagged(self, tmp_path):
        root = self._plant(tmp_path, _GOOD_TABLE.split("    def test_every_row_carries")[0])
        _table, use, _read = _verdicts(root)[("tests/test_planted.py", "DECLARED")]
        assert not use.reason_half

    def test_a_table_that_loses_its_self_deletion_gate_is_flagged(self, tmp_path):
        root = self._plant(tmp_path, _GOOD_TABLE.split("    def test_no_row_is_stale")[0])
        _table, use, _read = _verdicts(root)[("tests/test_planted.py", "DECLARED")]
        assert use.reason_half and not use.staleness_half

    def test_a_table_held_only_against_itself_is_not_credited(self, tmp_path):
        """A key checked against the table it came from proves nothing.

        `k not in DECLARED` inside a loop over `DECLARED` is vacuous by
        construction, and a gate that accepted it would let a table declare
        its own self-deletion out of thin air.
        """
        body = _GOOD_TABLE.replace("if k not in live()", "if k not in DECLARED")
        root = self._plant(tmp_path, body)
        _table, use, _read = _verdicts(root)[("tests/test_planted.py", "DECLARED")]
        assert not use.staleness_half

    def test_a_frozenset_with_no_reason_field_is_still_recognised(self, tmp_path):
        """The `_EXCLUDED_ACTION_ATTRS` shape: no reasons, so no reason gate.

        Recognition comes from the exempting `continue`, which is the only way
        a table carrying no reason field can be seen at all — and seeing it is
        the whole reason that second recogniser exists.
        """
        body = (
            "SKIP = frozenset({'alpha'})\n\n\n"
            "def walk(items):\n"
            "    out = []\n"
            "    for key in items:\n"
            "        if key in SKIP:\n"
            "            continue\n"
            "        out.append(key)\n"
            "    return out\n\n\n"
            "def test_walk():\n"
            "    assert walk(['alpha', 'beta']) == ['beta']\n"
        )
        root = self._plant(tmp_path, body)
        table, use, read = _verdicts(root)[("tests/test_planted.py", "SKIP")]
        assert table.exempting_continue and read
        assert not use.reason_half and not use.staleness_half

    def test_an_ordinary_fixture_is_not_recognised_as_a_table(self, tmp_path):
        """The other direction: the recogniser must not sweep in everything.

        A list of strings nobody tests membership against, and a dict of
        non-string values, are not exception tables and must never reach the
        gate — otherwise `DECLARED_EXCEPTIONS` would fill with fixtures and
        stop being read.
        """
        body = (
            "NAMES = ['alpha', 'beta']\n"
            "COUNTS = {'alpha': 1, 'beta': 2}\n\n\n"
            "def test_it():\n"
            "    assert len(NAMES) == len(COUNTS)\n"
        )
        root = self._plant(tmp_path, body)
        verdicts = _verdicts(root)
        assert ("tests/test_planted.py", "NAMES") not in verdicts
        assert ("tests/test_planted.py", "COUNTS") not in verdicts
