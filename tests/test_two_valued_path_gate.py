"""Every two-valued "what is at this path" read in `src/codebugs/db.py` is
either routed through `_path_state`, or declared in a table with a reason
(CB-224), and the gate keys on the CAPABILITY rather than on the spelling of
the call (CB-227).

WHY THIS FILE EXISTS. T-97 (CB-218) converted the walk's three probes to
`_path_state` and closed the paragraph in CLAUDE.md with a UNIVERSAL claim:
"every question this module asks about what is at a path is three-valued".
That sentence was false the day it was written -- `_linked_worktree_gitdir`'s
own `(gitdir / "commondir").is_file()` and two reads inside `init_project`
(`os.path.isdir`/`os.path.exists`) still answered a three-valued question with
two values, exactly the CB-203/CB-218 shape. This is this repository's own
recurring lesson (CLAUDE.md: "a rule expressed as an enumeration gets fixed at
the sites someone enumerated, and the population is always larger than the
list") landing a THIRD time on the identical property: "three copies" was four
(CB-24), "five sites in db.py" was six (CB-218's own correction), and now
"every question" left three readings standing. A universal property stated in
prose and never re-checked is a promise that rots the moment the file is next
edited -- so the property is held by a GATE, on the model of
`tests/test_no_network_capability.py` (self-deleting DECLARED_EXCEPTIONS, a
reason per row, a premise test that the gate reads files at all) and
`tests/test_strict_bool_gates.py` (the same three properties, applied to a
different closed vocabulary).

AND THE FIRST VERSION OF THAT GATE WAS ITSELF AN ENUMERATION -- OF SPELLINGS
(CB-227). It compared the TEXT of a call (`os.path.isdir`) against a list of
texts, matched an `except` clause only against the literal name `OSError`, and
let one `DECLARED_EXCEPTIONS` row license every call of that primitive
anywhere in the licensed function. Measured on the two predicates side by side
over TEN bypasses -- the six this card's oracle names, plus four found in this
unit's own sweep -- the old one caught ZERO and this one catches TEN, while
both still catch the two controls (`os.path.isdir` undeclared, and
`Path(...).is_dir()`) and neither reports anything at all on the unmutated
file. The ten: a restored DECISIVE `os.path.exists` inside `init_project`,
where the informational one is licensed -- the very defect CB-224 had just
removed; `from os.path import isdir`; `import os.path as osp`; `from os import
path`; `posixpath.isdir`; an `IOError` alias; a bare `except:`; an `except*`
group; a generator that yields the bare literal; and `f = os.path.isdir`
called through `f`. So the FORM of the gate was kept and its KEY was replaced:
what follows resolves a call through the file's OWN import bindings and then
asks the LIVE Python object whether it IS one of the primitives, and asks the
LIVE class hierarchy whether an `except` clause could swallow what a stat
raises.

WHAT IS CHECKED, STATED AT THE WIDTH IT IS TRUE -- and the width is
deliberately narrower than "every two-valued read in the package": this reads
`src/codebugs/db.py` ONLY, because that is the file both CB-203 and CB-218
already scoped their own fix to, and the file this card's brief names
throughout. `provenance.py` made the identical swap for CB-85, on its own
schedule; extending this gate there is a separate, negotiated widening, not a
silent scope creep here. The direct consequence is worth saying rather than
leaving to be discovered: MOVING a swallow into a sibling module that `db.py`
imports escapes this gate completely, however good the in-file predicate is.

THREE LAYERS.

1. NAMED PRIMITIVES, RESOLVED THROUGH THIS FILE'S OWN IMPORTS AND COMPARED AS
   OBJECTS. The capability is "answer a bool about the filesystem, swallow
   every `OSError` the underlying stat raises". For `os.path` that capability
   is a set of FUNCTION OBJECTS (`_OS_PATH_PRIMITIVE_OBJECTS`), so any spelling
   that resolves to one of them is caught -- `os.path.isdir`, a `from os.path
   import isdir`, `import os.path as osp`, `from os import path`, and also
   `posixpath.isdir` and `genericpath.exists`, which are not aliases at all but
   literally the same objects (`os.path.isdir is posixpath.isdir` on this
   platform, pinned as a premise test below). Nothing here is a list of
   spellings: the list is of CAPABILITIES, and the resolver does the rest.

2. `pathlib.Path` PREDICATES, matched by NAME on a zero-positional-argument
   call regardless of receiver -- because the receiver's type is not knowable
   from one file, so an object comparison is impossible here and a name match
   is the honest substitute. Deliberately over-broad: a collision with an
   unrelated object's `.exists()` costs a `DECLARED_EXCEPTIONS` row, never a
   missed capability. The set is not left to rot: `test_a_new_pathlib_predicate
   _in_a_future_python_is_not_a_free_pass` walks `dir(pathlib.Path)` in the
   RUNNING interpreter and fails on any `is_*`/`exists` name that is in neither
   the flagged set nor the small pure set beside it -- which is how the missing
   `is_junction` (new in 3.12) was found rather than argued about. The pure set
   needs no self-deletion clause, and that asymmetry is deliberate: a stale
   entry in a DETECTION list over-detects and can hide nothing, while a stale
   row in a PERMISSION list is exactly the hole `DECLARED_EXCEPTIONS`'s
   self-deletion closes.

3. STRUCTURAL: a function that reimplements the anti-pattern BY HAND --
   catching something that could swallow what a stat raises, and handing back a
   bare `True`/`False` literal instead of `None` (the three-valued convention
   every fixed site in this file already uses, from `_path_state` itself to
   `_access_probe`) -- is caught though it calls no named primitive at all.
   "Could swallow what a stat raises" is COMPUTED, not listed: a caught class
   qualifies when `issubclass(caught, OSError) or issubclass(OSError, caught)`,
   which admits `OSError` itself, its stdlib aliases `IOError` /
   `EnvironmentError` / `os.error` (the same class object, so no alias list is
   needed), its supertypes `Exception` and `BaseException`, and its subclasses
   `PermissionError` / `FileNotFoundError` -- and refuses `ValueError` and
   every domain exception this module defines. A bare `except:` qualifies, and
   so does an `except*` group. A clause this resolver cannot resolve at all
   qualifies too: fail closed, because a swallow nobody can classify is the one
   that must not be waved through. Measured: this file returns a bare boolean
   from no handler today, so layer 3 is purely forward-looking and its
   fail-closed arm costs nothing here.

WHAT NO LAYER SEES, NAMED RATHER THAN IMPLIED -- because "how close to the
capability did the AST get" is a measurement, not a principle, and this unit's
brief says so explicitly: state what the gate sees and what it does not, rather
than widen the promise past what was actually built. FORTY distinct evasions
were swept for this unit and RUN against this predicate rather than judged;
it catches FIFTEEN. What follows is what it does NOT catch, named one by one,
because an unannounced miss costs more than an announced one. How MANY that is,
is a question for the list below rather than for this sentence: a count written
here rots at the next edit of the list it describes, which is precisely what
happened to the one that used to stand in this paragraph (CB-238). It is removed
rather than corrected, for the same reason the counts this docstring used to
carry were removed rather than corrected.

* THE SEMANTIC SENTRY, and it is the one standing in `db.py` today. A function
  can answer three-valued perfectly -- `except OSError: return None` -- while
  its CALLER reads the `None` as "definitely not there". That was CB-227's
  live harm: `_linked_worktree_gitdir` returned a bare `None` for "could not
  read the `.git` file" and `_walk_db_root` treated it as "confirmed not a
  worktree". The meaning lives in the CALLER, so no predicate over the reading
  function can ever see it, and no predicate over ONE FILE can see it when the
  caller is elsewhere. This is why CB-227 needed a behavioural oracle as well
  as this gate, and why this gate must never be described as covering it. Note
  the sweep DID report a hit on this form's sample -- on a narrow `except
  FileNotFoundError: return False` handler that happened to sit in the same
  function, which layer 3 is right to flag on its own account. The sentry
  itself produced no hit, measured in isolation. A hit for the wrong reason is
  not coverage.
* AN INDIRECTION THAT HIDES THE NAME, eleven measured spellings:
  `getattr(os.path, "isdir")(p)` and its dynamic-string twin,
  `importlib.import_module("os.path").isdir`, `__import__("os.path").isdir`,
  `sys.modules["os"].path.isdir`, `globals()["isdir"]`, a dict dispatch table,
  a tuple indexed by position, `functools.partial(os.path.isdir, ...)`,
  `operator.methodcaller("is_dir")`, a default-argument capture, a
  `staticmethod()` capture in a class body, and `eval`/`exec` of a string.
  Closing any of them means tracking VALUES rather than names -- the same
  boundary `test_no_network_capability.py` draws around `__import__`/`exec`
  indirection, and out of scope by the same decision. The boundary was
  measured rather than assumed: replacing the indirection with a plain
  `name = os.path.isdir` binding flips EVERY one of them to caught, so what
  escapes is precisely the one hop, not a wider family.
* A SWALLOW THAT RETURNS THROUGH ANYTHING BUT A LITERAL, six measured
  spellings: `ok = False; return ok`, `return bool(x)`, `return not err`,
  `return a == b`, a flag set in a `finally`, and a `with
  contextlib.suppress(OSError):` block around an assignment -- the last of
  which layer 3 also cannot see for a second reason, that it walks `Try` and
  `TryStar` and not `With`. Each needs data-flow inside the function. A
  generator that YIELDS the bare literal instead of returning it was in this
  family until the sweep found it, and is now caught: that was node coverage,
  not value tracking, so it was fixed rather than declared.
* THE SAME READ MOVED INTO A SIBLING MODULE that `db.py` imports. Measured
  both ways: the swallow WOULD be caught if the gate read that file, and the
  `db.py` side, which merely imports and calls the helper, produces no hit at
  all. This is scope, not blindness -- see "WHAT IS CHECKED" above.
* `os.access` is a DIFFERENT capability (a permission check, not an existence
  query) and is correctly out of scope: `_access_probe` already returns
  `bool | None` via its own `except (OSError, ValueError): return None`, which
  is the three-valued shape already, not a violation of it. Pinned below, so
  the exclusion is a decision rather than an oversight. Listed here for
  honesty, though it is a boundary rather than a miss.
* THIS GATE'S OWN FILE RESOLUTION, which is not an AST question at all: the
  file is found through `codebugs.db.__file__`, so a probe run against a COPY
  of the tree without entering it silently measures the ORIGINAL checkout.
  Nothing in the predicate can see that, so it is held by
  `test_the_gate_reads_the_db_py_it_claims_to` instead.

DECLARED EXCEPTIONS are keyed by `(enclosing_function, canonical_primitive,
call_text)`, and each of the three parts earns its place. Not a LINE NUMBER,
which would go stale on ordinary reformatting and teach people to stop
trusting the table. Not `(function, primitive)` alone, which was the CB-227
escape: with one informational `os.path.exists` licensed inside
`init_project`, a restored DECISIVE `os.path.exists` in the same function
inherited the licence and the gate stayed green over a defect CB-224 had just
removed. The call text comes from `ast.unparse`, so it survives comment and
whitespace edits and does not survive a rewrite of the call itself -- which is
correct, since a rewritten call is a new call and has to be re-declared. And
because two textually identical calls of one primitive could still sit in one
function, `test_no_row_licenses_more_than_one_call` REFUSES that state rather
than letting one row quietly cover both: a row is a licence for one call, and
where the key cannot distinguish two, the answer is to refuse, not to hope.

Two rows exist today, both already documented at length in `CLAUDE.md`'s
CB-86/CB-23 sections rather than invented for this file:

* `init_project`'s `created = not os.path.exists(path)` reports a purely
  INFORMATIONAL flag in the returned dict; the actual creation decision is
  made two lines later by `_open(path, create=True)`, which does not consult
  this read at all. Converting it to a hard refusal on "undetermined" would be
  a new failure mode this card was not asked to introduce, at a site where the
  real work below it might well succeed anyway.
* `_open`'s `os.path.exists(path)` inside the `SQLITE_CANTOPEN` handler picks
  which of two TRUE sentences to print (CB-86, documented in CLAUDE.md at
  length: "SQLITE_CANTOPEN is ambiguous ... os.path.exists picks the message,
  for message selection only"). A misread here changes wording, not which
  branch runs -- `TrackerUnwritableError`/`DatabaseNotFoundError` are still
  raised correctly either way.

`_writable_probe`'s and `_access_probe`'s own `os.access(...)` calls are not
in any detected capability set (see above), so they never reach
`DECLARED_EXCEPTIONS` -- they are omitted from the table entirely rather than
declared, and `TestTheGateItself` pins that the omission is correct.
"""

from __future__ import annotations

import ast
import builtins
import os.path
import pathlib
import sys

import codebugs.db

# ---------------------------------------------------------------------------
# The capability: "answer a bool about the filesystem, swallow OSError".
# ---------------------------------------------------------------------------

# `os.path` functions documented to return False on any stat failure rather
# than raise. Held as OBJECTS, not as text: every spelling that resolves to one
# of these is the same capability, including `posixpath`/`genericpath`, which
# are not aliases but the very same functions.
_OS_PATH_PRIMITIVE_NAMES: tuple[str, ...] = (
    "isfile",
    "isdir",
    "exists",
    "islink",
    "lexists",
    "ismount",
)
_OS_PATH_PRIMITIVE_OBJECTS: dict[object, str] = {
    getattr(os.path, name): f"os.path.{name}" for name in _OS_PATH_PRIMITIVE_NAMES
}
_OS_PATH_PRIMITIVE_TEXTS: frozenset[str] = frozenset(_OS_PATH_PRIMITIVE_OBJECTS.values())

# `pathlib.Path` predicates that touch the filesystem and swallow the failure.
# Matched by NAME (the receiver's type is not knowable from one file), and
# ratcheted against `dir(pathlib.Path)` so a future interpreter cannot add one
# silently -- see `_PATHLIB_PURE_PREDICATES` for the other half of that ratchet.
_PATHLIB_PREDICATE_METHODS: frozenset[str] = frozenset(
    {
        "exists",
        "is_file",
        "is_dir",
        "is_symlink",
        "is_mount",
        "is_socket",
        "is_fifo",
        "is_block_device",
        "is_char_device",
        "is_junction",
    }
)

# `is_*` names on `pathlib.Path` that are PURE -- they inspect the path string
# and never touch the filesystem, so they cannot swallow a stat failure and
# flagging them would be a false refusal. Kept beside the set above so the
# ratchet can partition every predicate-shaped name in the running interpreter.
_PATHLIB_PURE_PREDICATES: dict[str, str] = {
    "is_absolute": "pure string inspection of the path itself; opens nothing",
    "is_relative_to": "compares two paths lexically; opens nothing",
    "is_reserved": "inspects the name against reserved Windows names; opens nothing",
}

# (enclosing_function, canonical_primitive, call_text) -> reason. Self-deleting:
# a row naming a call that is no longer there, or that has no reason, fails this
# file's own tests (TestDeclaredExceptionsCannotRot below) -- otherwise this
# table becomes the place a real two-valued read gets quietly parked, which is
# the hole the whole gate exists to close, one level up.
DECLARED_EXCEPTIONS: dict[tuple[str, str, str], str] = {
    ("init_project", "os.path.exists", "os.path.exists(path)"): (
        "CB-224: `created = not os.path.exists(path)` feeds only the "
        "informational `created` key in the returned dict. The actual "
        "creation decision is `_open(path, create=True)`, two lines below, "
        "which does not read this value at all. Turning an undetermined "
        "answer here into a hard refusal would be a new failure mode this "
        "card was not asked to add, ahead of a call that might succeed "
        "regardless."
    ),
    ("_open", "os.path.exists", "os.path.exists(path)"): (
        "CB-86 (documented at length in CLAUDE.md): SQLITE_CANTOPEN (14) is "
        "returned identically for 'file missing' and 'file present but "
        "unopenable', so os.path.exists here picks which of two TRUE "
        "sentences to print -- message selection only. A misread changes "
        "wording, never which exception type is raised."
    ),
}


# ---------------------------------------------------------------------------
# Name resolution: this file's own bindings, then the live objects behind them.
# ---------------------------------------------------------------------------


def _dotted_name(node: ast.expr) -> str | None:
    """Resolve an `ast.Attribute`/`ast.Name` chain to `a.b.c`, or None."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    return ".".join(reversed(parts))


def _binding_map(tree: ast.Module) -> dict[str, str]:
    """Local name -> the dotted path it is bound to, from this file's own source.

    Three sources, and each closes a measured bypass of the text-matching gate
    this replaces:

    * `import os.path` binds `os`; `import os.path as osp` binds `osp` to
      `os.path`.
    * `from os import path` binds `path` to `os.path`; `from os.path import
      isdir [as d]` binds the local name to `os.path.isdir`.
    * a simple `name = <dotted>` assignment at ANY scope binds that name --
      which is what makes `f = os.path.isdir; f(p)` visible. That is name
      resolution, not value tracking: a conditional or repeated binding, or one
      through a container, is out of reach and is named in the module docstring.

    Every `import` in the file is read regardless of where it sits, so an
    import inside a function body or inside a `try:`/`except ImportError:`
    fallback is covered. Later bindings of one name overwrite earlier ones
    arbitrarily -- deliberate, because the failure direction of a wrong
    over-broad binding is a declared row, never a missed capability.
    """
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    bindings[top] = top
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # a relative import cannot reach the stdlib
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                bindings[local] = f"{module}.{alias.name}" if module else alias.name
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if not isinstance(node.value, (ast.Name, ast.Attribute)):
                continue
            value = _dotted_name(node.value)
            if value is not None:
                bindings[node.targets[0].id] = value
    return bindings


def _canonicalize(dotted: str, bindings: dict[str, str]) -> str:
    """Rewrite the head of a dotted name through this file's own bindings."""
    head, _, rest = dotted.partition(".")
    base = bindings.get(head)
    if base is None or base == head:
        return dotted
    return f"{base}.{rest}" if rest else base


def _resolve_object(dotted: str) -> object | None:
    """The live object a canonical dotted name refers to, or None.

    Reads `sys.modules` and never imports: everything `db.py` imports is
    already there, because this test module imports `codebugs.db`. A name that
    cannot be resolved falls back to the caller's own textual comparison rather
    than to a guess.
    """
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        module = sys.modules.get(".".join(parts[:cut]))
        if module is None:
            continue
        obj: object | None = module
        for attr in parts[cut:]:
            obj = getattr(obj, attr, None)
            if obj is None:
                break
        if obj is not None:
            return obj
    if len(parts) == 1:
        return getattr(builtins, parts[0], None)
    return None


def _canonical_primitive(node: ast.Call, bindings: dict[str, str]) -> str | None:
    """The canonical `os.path.X` name this call reaches, or None.

    Object identity first (so `posixpath.isdir` and a from-imported `isdir`
    both land on `os.path.isdir`), then a textual fallback for the case where
    the module is genuinely not importable in this process -- fail closed on
    the text rather than answer "not a primitive" because a lookup failed.
    """
    dotted = _dotted_name(node.func)
    if dotted is None:
        return None
    canonical = _canonicalize(dotted, bindings)
    resolved = _resolve_object(canonical)
    if resolved is not None and resolved in _OS_PATH_PRIMITIVE_OBJECTS:
        return _OS_PATH_PRIMITIVE_OBJECTS[resolved]
    if canonical in _OS_PATH_PRIMITIVE_TEXTS:
        return canonical
    return None


def _is_pathlib_predicate_call(node: ast.Call) -> bool:
    """A `.is_file()`-shaped call: zero positional args, a flagged method name.

    Keywords are still allowed -- what is excluded is a POSITIONAL argument,
    which none of `Path.is_file`/`is_dir`/`exists`/... can legitimately take,
    so restricting to that shape costs nothing on the real methods and only
    narrows the accidental collisions this deliberately over-broad name match
    would otherwise catch.
    """
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in _PATHLIB_PREDICATE_METHODS:
        return False
    return not node.args


# ---------------------------------------------------------------------------
# "Could this `except` clause swallow what a stat raises?" -- computed.
# ---------------------------------------------------------------------------


def _class_overlaps_oserror(cls: type) -> bool:
    """True when `except cls:` could catch something a failed stat raises.

    Both directions, and both are needed. `issubclass(OSError, cls)` admits
    `OSError` itself, its stdlib aliases (`IOError`, `EnvironmentError`,
    `os.error` -- all the same class object, which is why no alias list
    appears anywhere in this file) and its supertypes `Exception` /
    `BaseException`. `issubclass(cls, OSError)` admits the concrete errnos a
    stat actually raises -- `PermissionError`, `FileNotFoundError`,
    `NotADirectoryError` -- each of which swallows the CB-203 case just as
    completely while being invisible to a name-keyed check.
    """
    return issubclass(cls, OSError) or issubclass(OSError, cls)


def _local_class_names(tree: ast.Module) -> frozenset[str]:
    return frozenset(n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef))


def _catches_os_error(
    exc_type: ast.expr | None,
    bindings: dict[str, str],
    local_classes: frozenset[str],
) -> bool:
    """Whether this `except` clause could swallow a failed stat. Fail closed.

    A bare `except:` catches everything, so it qualifies. A tuple qualifies if
    any member does. A class DEFINED IN THIS FILE gets a determined `False`:
    a stat raises stdlib exceptions, and no locally declared class is a
    superclass of one, so `except SomeLocalError:` provably cannot swallow it
    (an `X = OSError` rebinding is an assignment, not a class, and is handled
    by `_binding_map` instead). Anything else that will not resolve to a class
    -- a type built at runtime, a name bound to a tuple -- qualifies, because a
    swallow nobody can classify is exactly the one that must not be waved
    through.
    """
    if exc_type is None:
        return True
    if isinstance(exc_type, ast.Tuple):
        return any(_catches_os_error(e, bindings, local_classes) for e in exc_type.elts)
    dotted = _dotted_name(exc_type)
    if dotted is None:
        return True
    if dotted in local_classes:
        return False
    resolved = _resolve_object(_canonicalize(dotted, bindings))
    if isinstance(resolved, type) and issubclass(resolved, BaseException):
        return _class_overlaps_oserror(resolved)
    return True


# ---------------------------------------------------------------------------
# The two collectors.
# ---------------------------------------------------------------------------

Key = tuple[str, str, str]


class _CallCollector(ast.NodeVisitor):
    """Every two-valued path-state call in one file, keyed by enclosing function."""

    def __init__(self, bindings: dict[str, str]) -> None:
        self.hits: list[Key] = []
        self._bindings = bindings
        self._stack: list[str] = ["<module>"]

    def _enclosing(self) -> str:
        return self._stack[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        primitive = _canonical_primitive(node, self._bindings)
        if primitive is not None:
            self.hits.append((self._enclosing(), primitive, ast.unparse(node)))
        elif _is_pathlib_predicate_call(node):
            attr = node.func.attr  # type: ignore[union-attr]
            self.hits.append((self._enclosing(), attr, ast.unparse(node)))
        self.generic_visit(node)


def _named_primitive_hits(source: str) -> list[Key]:
    """(function, primitive, call text) for every named-primitive call in `source`."""
    tree = ast.parse(source)
    collector = _CallCollector(_binding_map(tree))
    collector.visit(tree)
    return collector.hits


class _SwallowCollector(ast.NodeVisitor):
    """Functions that swallow a failed stat and hand back a bare True/False.

    The STRUCTURAL layer (see module docstring): this needs no named primitive
    at all, so it is what would catch a hand-rolled reimplementation of the
    CB-203 anti-pattern -- exactly the shape CB-224's line 924 used to be,
    before `_linked_worktree_gitdir` called `.is_file()` at all.
    """

    def __init__(self, bindings: dict[str, str], local_classes: frozenset[str]) -> None:
        self.hits: list[Key] = []
        self._bindings = bindings
        self._local_classes = local_classes
        self._stack: list[str] = ["<module>"]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        for handler in node.handlers:
            if not _catches_os_error(handler.type, self._bindings, self._local_classes):
                continue
            for stmt in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
                # `return False` and `yield False` are one lie in two
                # transports, and a gate reading only `ast.Return` misses the
                # generator spelling entirely -- found by this unit's own
                # bypass sweep, and closed here because it is node coverage
                # rather than the value tracking layer 3 declines to do.
                if isinstance(stmt, (ast.Return, ast.Yield)):
                    value = stmt.value
                elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield):
                    value = stmt.value.value
                else:
                    continue
                if isinstance(value, ast.Constant) and value.value in (True, False):
                    self.hits.append(
                        (self._enclosing(), "except-OSError-return-bool", ast.unparse(stmt))
                    )
        self.generic_visit(node)

    # `except*` is a different AST node, and a gate that reads only `ast.Try`
    # is one `*` away from seeing nothing (found in this unit's bypass sweep).
    visit_Try = _visit_try  # noqa: N815
    visit_TryStar = _visit_try  # noqa: N815

    def _enclosing(self) -> str:
        return self._stack[-1]


def _swallow_hits(source: str) -> list[Key]:
    tree = ast.parse(source)
    collector = _SwallowCollector(_binding_map(tree), _local_class_names(tree))
    collector.visit(tree)
    return collector.hits


def _db_source_files() -> list[tuple[str, pathlib.Path]]:
    """Just `db.py`, in the same `(rel, path)` shape as the network gate --
    a list of one so the population/self-deletion tests below share their
    shape with `tests/test_no_network_capability.py` even though the scope
    here is narrower (see module docstring, "WHAT IS CHECKED")."""
    path = pathlib.Path(codebugs.db.__file__)
    assert path.is_file(), f"db.py not found at {path} -- this gate cannot look"
    return [("db.py", path)]


def _all_hits(source: str) -> list[Key]:
    return _named_primitive_hits(source) + _swallow_hits(source)


def _rows_licensing_more_than_one_call(source: str) -> list[Key]:
    """Declared rows whose key matches two or more calls in `source`.

    ONE function, called both by the live-file test and by the mutant that
    exercises it, because a rule with two implementations is one edit away from
    disagreeing with itself -- the shape `_guards.sh` and the two git hooks pay
    for elsewhere in this repository.
    """
    counts: dict[Key, int] = {}
    for key in _all_hits(source):
        counts[key] = counts.get(key, 0) + 1
    return [key for key in DECLARED_EXCEPTIONS if counts.get(key, 0) > 1]


class TestTwoValuedPathGate:
    def test_every_two_valued_read_in_db_py_is_fixed_or_declared(self):
        undeclared = []
        for _rel, path in _db_source_files():
            source = path.read_text(encoding="utf-8")
            for key in _all_hits(source):
                if key not in DECLARED_EXCEPTIONS:
                    undeclared.append(key)
        assert not undeclared, (
            "two-valued path-state read(s) in src/codebugs/db.py, neither "
            f"routed through _path_state nor declared: {undeclared}. Either "
            "convert the call to _path_state, or -- if an undetermined answer "
            "genuinely cannot change what the caller does -- add it to "
            "DECLARED_EXCEPTIONS in this file with a reason. Note the key "
            "carries the CALL TEXT: a row licenses one call, so a rewritten "
            "call has to be declared again rather than inheriting a licence."
        )


class TestTheGateItself:
    """A gate is only worth its line count if it can fail, and only fail
    rightly (CB-224 oracle items 1 and 2; CB-227 oracle item 3)."""

    def test_the_original_line_924_shape_is_caught(self):
        """CB-224 oracle item 1: reverting `_linked_worktree_gitdir` to its
        pre-fix form must turn the gate red. This is that exact shape."""
        mutant = (
            "def _linked_worktree_gitdir(git_file):\n"
            "    gitdir = _abs_from(git_file.parent, pointer)\n"
            "    return gitdir if (gitdir / 'commondir').is_file() else None\n"
        )
        hits = _named_primitive_hits(mutant)
        assert any(fn == "_linked_worktree_gitdir" and prim == "is_file" for fn, prim, _ in hits)

    def test_a_new_two_valued_read_anywhere_in_the_file_is_caught(self):
        """The half the gate exists for -- catching a read nobody enumerated,
        added to a brand new function."""
        mutant = "import os\ndef _some_future_probe(p):\n    return os.path.isdir(p)\n"
        hits = _named_primitive_hits(mutant)
        assert ("_some_future_probe", "os.path.isdir", "os.path.isdir(p)") in hits
        assert ("_some_future_probe", "os.path.isdir", "os.path.isdir(p)") not in DECLARED_EXCEPTIONS

    def test_a_hand_rolled_swallow_with_no_named_primitive_is_also_caught(self):
        """The structural layer: no `is_dir`/`is_file`/`isdir`/`isfile`/`exists`
        anywhere in this mutant, and it is still the CB-203 anti-pattern."""
        mutant = (
            "def _homegrown_check(p):\n"
            "    try:\n"
            "        os.stat(p)\n"
            "        return True\n"
            "    except OSError:\n"
            "        return False\n"
        )
        hits = _swallow_hits(mutant)
        assert any(fn == "_homegrown_check" for fn, _prim, _text in hits)

    def test_the_three_valued_convention_is_not_flagged(self):
        """`except OSError: return None` -- the convention every fixed site in
        this file already uses -- must NOT be mistaken for the anti-pattern,
        or this gate would refuse the very fix it exists to require."""
        good = "def _ok(p):\n    try:\n        os.stat(p)\n    except OSError:\n        return None\n"
        assert _swallow_hits(good) == []

    def test_os_access_is_a_different_primitive_and_is_not_flagged(self):
        """`_access_probe`'s `os.access(...)` is a permission check, not an
        existence query, and already returns tri-state on its own -- it must
        never need a DECLARED_EXCEPTIONS row."""
        source = "import os\ndef _access_probe(path):\n    return os.access(path, os.W_OK)\n"
        assert _named_primitive_hits(source) == []

    def test_pathlib_predicates_beyond_the_three_seen_today_are_still_caught(self):
        """Wider than the three spellings CB-224 measured in this file
        (is_file, isdir, exists) -- islink/lexists/ismount and the pathlib
        siblings are part of the same capability, so a mutant using one of
        THOSE instead is not a free pass."""
        for snippet, expected in [
            ("os.path.islink(p)", "os.path.islink"),
            ("os.path.lexists(p)", "os.path.lexists"),
            ("os.path.ismount(p)", "os.path.ismount"),
            ("p.is_symlink()", "is_symlink"),
            ("p.is_socket()", "is_socket"),
            ("p.is_mount()", "is_mount"),
            ("p.is_junction()", "is_junction"),
        ]:
            source = f"import os\ndef _f(p):\n    return {snippet}\n"
            hits = _named_primitive_hits(source)
            assert any(prim == expected for _fn, prim, _text in hits), (snippet, hits)

    def test_a_zero_arg_pathlib_call_on_an_unrelated_receiver_is_still_flagged(self):
        """Deliberately over-broad, per the module docstring: this repo has no
        occurrence today, but a genuine collision costs a declared row, not a
        blind spot."""
        source = "def _f(thing):\n    return thing.exists()\n"
        assert _named_primitive_hits(source) == [("_f", "exists", "thing.exists()")]

    def test_the_gate_actually_reads_files(self):
        """A gate reading an empty file list passes vacuously."""
        files = _db_source_files()
        assert files, "the file sweep found nothing -- this gate cannot look"
        rel, path = files[0]
        assert rel == "db.py"
        assert path.read_text(encoding="utf-8"), f"{path} read as empty"

    def test_the_gate_reads_the_db_py_it_claims_to(self):
        """The trap this direction has already stood on: the file is resolved
        through `codebugs.db.__file__`, so a probe run against a COPY of the
        tree without entering it measures the ORIGINAL checkout and every
        number it reports is about the wrong file. Assert the identity rather
        than assume it, so a mis-rooted run is loud instead of quietly
        reassuring."""
        _rel, path = _db_source_files()[0]
        assert path.resolve() == pathlib.Path(codebugs.db.__file__).resolve()
        assert path.name == "db.py" and path.parent.name == "codebugs"


class TestKeyedOnTheCapabilityNotTheSpelling:
    """CB-227 oracle item 3: six mutants that the text-matching gate passed.

    Each is a measured escape of the predecessor, not a hypothetical. The
    seventh case here is the one that made the whole rewrite necessary -- a
    DECLARED_EXCEPTIONS row licensing a second, decisive call in the same
    function -- and it is checked against the live table rather than a mutant.
    """

    def test_a_declared_row_does_not_license_a_second_call_in_the_same_function(self):
        """THE escape that made the key change, and it has to be spelled with
        the SAME primitive to discriminate anything.

        `init_project` is licensed for one INFORMATIONAL `os.path.exists(path)`.
        Under the old `(function, primitive)` key, restoring a DECISIVE
        `os.path.exists` to that same function inherited the licence and the
        whole file went on reporting clean. A mutant using a DIFFERENT primitive
        proves nothing here -- measured, the old key caught that one too.
        """
        mutant = (
            "import os\n"
            "def init_project(root):\n"
            "    if not os.path.exists(root):\n"
            "        raise ValueError('no such directory')\n"
            "    created = not os.path.exists(path)\n"
        )
        hits = _named_primitive_hits(mutant)
        licensed = [k for k in hits if k in DECLARED_EXCEPTIONS]
        undeclared = [k for k in hits if k not in DECLARED_EXCEPTIONS]
        assert ("init_project", "os.path.exists", "os.path.exists(path)") in licensed
        assert ("init_project", "os.path.exists", "os.path.exists(root)") in undeclared

    def test_a_decisive_call_textually_identical_to_a_licensed_one_is_refused(self):
        """The residual of the key, closed by REFUSING rather than by hoping.

        The key cannot tell two textually identical calls of one primitive in
        one function apart -- so where that happens, the answer is a red test
        naming the row, not a licence quietly covering both. Same function as
        the live-file check, so the two cannot drift.
        """
        mutant = (
            "import os\n"
            "def init_project(root):\n"
            "    if not os.path.exists(path):\n"
            "        raise ValueError('no such directory')\n"
            "    created = not os.path.exists(path)\n"
        )
        assert _rows_licensing_more_than_one_call(mutant) == [
            ("init_project", "os.path.exists", "os.path.exists(path)")
        ]

    def test_a_from_import_of_the_primitive_is_resolved(self):
        mutant = "from os.path import isdir\ndef _f(p):\n    return isdir(p)\n"
        assert any(prim == "os.path.isdir" for _fn, prim, _t in _named_primitive_hits(mutant))

    def test_a_renamed_module_import_is_resolved(self):
        mutant = "import os.path as osp\ndef _f(p):\n    return osp.isdir(p)\n"
        assert any(prim == "os.path.isdir" for _fn, prim, _t in _named_primitive_hits(mutant))

    def test_importing_the_submodule_by_name_is_resolved(self):
        mutant = "from os import path\ndef _f(p):\n    return path.isdir(p)\n"
        assert any(prim == "os.path.isdir" for _fn, prim, _t in _named_primitive_hits(mutant))

    def test_the_ioerror_alias_still_reads_as_swallowing_a_stat(self):
        mutant = (
            "def _f(p):\n"
            "    try:\n"
            "        os.stat(p)\n"
            "        return True\n"
            "    except IOError:\n"
            "        return False\n"
        )
        assert any(fn == "_f" for fn, _p, _t in _swallow_hits(mutant))

    def test_a_bare_except_still_reads_as_swallowing_a_stat(self):
        mutant = (
            "def _f(p):\n"
            "    try:\n"
            "        os.stat(p)\n"
            "        return True\n"
            "    except:\n"
            "        return False\n"
        )
        assert any(fn == "_f" for fn, _p, _t in _swallow_hits(mutant))

    def test_the_other_stdlib_spellings_of_oserror_are_covered_without_a_list(self):
        """`EnvironmentError`, `os.error` and the concrete errno subclasses --
        none of which appears as a literal anywhere in this file, because the
        answer is computed from the class hierarchy."""
        for clause in ("EnvironmentError", "os.error", "PermissionError", "Exception"):
            mutant = (
                "import os\n"
                "def _f(p):\n"
                "    try:\n"
                "        os.stat(p)\n"
                "        return True\n"
                f"    except {clause}:\n"
                "        return False\n"
            )
            assert any(fn == "_f" for fn, _p, _t in _swallow_hits(mutant)), clause

    def test_an_unrelated_exception_class_is_not_read_as_swallowing_a_stat(self):
        """The other direction, and the reason the check is computed rather
        than a widened list: `ValueError` and this module's own exception
        classes cannot catch what a stat raises, so flagging them would be a
        false refusal."""
        for clause in ("ValueError", "KeyError"):
            mutant = (
                "def _f(p):\n"
                "    try:\n"
                "        os.stat(p)\n"
                "        return True\n"
                f"    except {clause}:\n"
                "        return False\n"
            )
            assert _swallow_hits(mutant) == [], clause
        local = (
            "class DatabaseNotFoundError(Exception):\n"
            "    pass\n"
            "def _f(p):\n"
            "    try:\n"
            "        os.stat(p)\n"
            "        return True\n"
            "    except DatabaseNotFoundError:\n"
            "        return False\n"
        )
        assert _swallow_hits(local) == []

    def test_an_except_star_group_is_not_a_free_pass(self):
        """`except*` is a different AST node; a gate reading only `ast.Try`
        would see nothing at all here."""
        mutant = (
            "def _f(p):\n"
            "    try:\n"
            "        os.stat(p)\n"
            "        return True\n"
            "    except* OSError:\n"
            "        return False\n"
        )
        assert any(fn == "_f" for fn, _p, _t in _swallow_hits(mutant))

    def test_an_exception_name_rebound_to_oserror_is_resolved(self):
        mutant = (
            "E = OSError\n"
            "def _f(p):\n"
            "    try:\n"
            "        os.stat(p)\n"
            "        return True\n"
            "    except E:\n"
            "        return False\n"
        )
        assert any(fn == "_f" for fn, _p, _t in _swallow_hits(mutant))

    def test_a_primitive_bound_to_a_plain_name_is_resolved(self):
        """Name resolution reaches this; VALUE tracking would be needed for a
        conditional or container-held binding, which is named in the docstring
        as out of reach rather than claimed."""
        mutant = "import os\n_probe = os.path.isdir\ndef _f(p):\n    return _probe(p)\n"
        assert any(prim == "os.path.isdir" for _fn, prim, _t in _named_primitive_hits(mutant))

    def test_the_same_function_under_another_module_name_is_resolved(self):
        """`os.path.isdir is posixpath.isdir` -- not an alias to be listed, the
        same object. Object identity is what makes this free."""
        for mutant in (
            "import posixpath\ndef _f(p):\n    return posixpath.isdir(p)\n",
            "import genericpath\ndef _f(p):\n    return genericpath.exists(p)\n",
        ):
            hits = _named_primitive_hits(mutant)
            assert hits, mutant
            assert all(prim.startswith("os.path.") for _fn, prim, _t in hits), mutant


class TestPremises:
    """The two facts the resolver rests on, pinned so an interpreter change
    turns the suite red instead of quietly disarming a layer."""

    def test_premise_os_path_is_the_same_object_as_the_platform_module(self):
        assert os.path.isdir is sys.modules[os.path.__name__].isdir
        assert os.path.exists is sys.modules["genericpath"].exists

    def test_premise_the_stdlib_oserror_aliases_are_one_class(self):
        assert OSError is builtins.IOError is builtins.EnvironmentError
        assert _class_overlaps_oserror(builtins.IOError)
        assert _class_overlaps_oserror(PermissionError)
        assert _class_overlaps_oserror(Exception)
        assert not _class_overlaps_oserror(ValueError)

    def test_a_new_pathlib_predicate_in_a_future_python_is_not_a_free_pass(self):
        """The ratchet that keeps layer 2 from being a list somebody forgets.

        Every predicate-shaped name on `pathlib.Path` in the RUNNING
        interpreter is either flagged or declared pure with a reason. This is
        how `is_junction` (new in 3.12) was found missing from the set CB-224
        wrote, rather than argued about. The pure set is deliberately NOT
        checked for staleness: it is an exclusion list, and an exclusion for a
        method that no longer exists can hide nothing -- unlike a stale row in
        DECLARED_EXCEPTIONS, which is a standing permission.
        """
        shaped = {n for n in dir(pathlib.Path) if n.startswith("is_") or n == "exists"}
        unclassified = shaped - _PATHLIB_PREDICATE_METHODS - set(_PATHLIB_PURE_PREDICATES)
        assert not unclassified, (
            f"pathlib.Path predicate(s) this gate has never classified: "
            f"{sorted(unclassified)} (running Python {sys.version.split()[0]}). "
            "Add each to _PATHLIB_PREDICATE_METHODS if it stats the filesystem "
            "and swallows the failure, or to _PATHLIB_PURE_PREDICATES with the "
            "reason it cannot."
        )


class TestDeclaredExceptionsCannotRot:
    """The table may only shrink, and one row may only cover one call."""

    def test_every_row_carries_a_reason(self):
        empty = [key for key, reason in DECLARED_EXCEPTIONS.items() if not reason.strip()]
        assert not empty, (
            f"DECLARED_EXCEPTIONS row(s) with no reason: {empty} -- a table "
            "that can grow silently is the hole this gate exists to close"
        )

    def test_no_row_is_stale(self):
        live: set[Key] = set()
        for _rel, path in _db_source_files():
            live.update(_all_hits(path.read_text(encoding="utf-8")))
        stale = [key for key in DECLARED_EXCEPTIONS if key not in live]
        assert not stale, (
            f"stale DECLARED_EXCEPTIONS row(s): {stale} -- the call is gone "
            "(fixed, removed, or rewritten), so delete the row rather than "
            "leaving a standing permission behind"
        )

    def test_no_row_licenses_more_than_one_call(self):
        """A row is a licence for ONE call. The key distinguishes calls by
        function, primitive and call text -- but two textually identical calls
        of one primitive could still sit in one function, and there the key
        cannot tell them apart. That state is REFUSED rather than quietly
        double-licensed: the whole point of CB-227's re-key is that a licence
        granted for a harmless read must never cover a decisive one.
        """
        doubled: list[Key] = []
        for _rel, path in _db_source_files():
            doubled += _rows_licensing_more_than_one_call(path.read_text(encoding="utf-8"))
        assert not doubled, (
            f"DECLARED_EXCEPTIONS row(s) matching more than one call: {doubled}. "
            "One row licenses one call. Distinguish the two calls (rename a "
            "variable, or move one into its own function), or fix them both -- "
            "do not let one reason stand for two decisions."
        )
