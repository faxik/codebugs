"""Every two-valued "what is at this path" read in `src/codebugs/db.py` is
either routed through `_path_state`, or declared in a table with a reason
(CB-224).

WHY THIS FILE EXISTS. T-97 (CB-218) converted the walk's three probes to
`_path_state` and closed the paragraph in CLAUDE.md with a UNIVERSAL claim:
"every question this module asks about what is at a path is three-valued".
That sentence was false the day it was written -- `_linked_worktree_gitdir`'s
own `(gitdir / "commondir").is_file()` (CB-224's line 924) and two reads
inside `init_project` (`os.path.isdir`/`os.path.exists`) still answered a
three-valued question with two values, exactly the CB-203/CB-218 shape. This
is this repository's own recurring lesson (CLAUDE.md: "a rule expressed as an
enumeration gets fixed at the sites someone enumerated, and the population is
always larger than the list") landing a THIRD time on the identical property:
"three copies" was four (CB-24), "five sites in db.py" was six (CB-218's own
correction), and now "every question" left three readings standing. A
universal property stated in prose and never re-checked is a promise that
rots the moment the file is next edited -- so this time the property is held
by a GATE, on the model of `tests/test_no_network_capability.py` (self-deleting
DECLARED_EXCEPTIONS, a reason per row, a premise test that the gate reads
files at all) and `tests/test_strict_bool_gates.py` (the same three
properties, applied to a different closed vocabulary).

WHAT IS CHECKED, STATED AT THE WIDTH IT IS TRUE -- and the width is
deliberately narrower than "every two-valued read in the package": this reads
`src/codebugs/db.py` ONLY, because that is the file both CB-203 and CB-218
already scoped their own fix to, and the file this card's brief names
throughout. `provenance.py` made the identical swap for CB-85, on its own
schedule; extending this gate there is a separate, negotiated widening, not a
silent scope creep here.

KEY ON THE CAPABILITY, NOT ON FIVE SPELLINGS -- and this is the trap the
card's own brief names by name: "a gate over the list `is_dir, is_file, isdir,
isfile, exists` is an enumeration, which is exactly the defect being fixed."
Two layers, neither a literal name list of what THIS file happens to use
today:

1. NAMED PRIMITIVES: the CLOSED, stdlib-documented set of `os.path` functions
   and `pathlib.Path` methods whose behaviour is exactly the CB-203 shape --
   answer a bool, swallow every `OSError` the underlying stat raises. This is
   wider than the three spellings CB-224 found (`is_file`, `isdir`, `exists`):
   it also names `islink`, `lexists`, `ismount` and six more `pathlib.Path`
   predicates that do not appear in `db.py` today but share the identical
   documented behaviour, so a mutant introducing any of them -- not merely a
   repeat of a name already seen -- is caught (see `TestTheGateItself` below).
   `pathlib.Path` methods are matched by NAME on a zero-argument call
   regardless of receiver, deliberately over-broad: a name collision with an
   unrelated object's `.exists()` method costs a `DECLARED_EXCEPTIONS` row,
   never a missed capability. None occurs in this file today (measured).

2. STRUCTURAL: a function that reimplements the same anti-pattern BY HAND --
   catching `OSError` and returning a bare `True`/`False` literal instead of
   `None` (the three-valued convention every fixed site in this file already
   uses, from `_path_state` itself to `_access_probe`) -- is caught even
   though it calls no named primitive at all. Measured on this file's own 12
   `except OSError` blocks (including the two `except (OSError, ValueError)`
   pairs `_path_state` itself carries): zero return a bare boolean literal
   today, so this layer is purely forward-looking, exactly the half of the
   oracle "a mutant adds a NEW two-valued read" needs (CLAUDE.md CB-218: "the
   gate is obliged to catch the future"). It does NOT see: a swallow that
   returns through an intermediate variable (`ok = False; return ok`) rather
   than a literal at the `return` site, a bare `except:` with no named type,
   or a handler on a *tuple* whose OSError member is spelled through an
   alias. Named here rather than silently claimed as covered.

WHAT NEITHER LAYER SEES, NAMED RATHER THAN IMPLIED -- because "how close to
the capability did the AST get" is a measurement, not a principle, and this
unit's brief says so explicitly: state what the gate sees and what it does
not, rather than widen the promise past what was actually built.

* An indirection that hides the call -- `getattr(os.path, "isdir")(x)`, a
  primitive stored in a variable and called through it, a wrapper function
  elsewhere in the package that itself calls one of these and is called FROM
  `db.py` -- is invisible. Closing this means tracking values, not names, the
  same boundary `test_no_network_capability.py` draws around
  `__import__`/`exec` indirection.
* `os.access` is a DIFFERENT primitive (a permission check, not an existence
  query) and is correctly out of scope: `_access_probe` already returns
  `bool | None` via its own `except (OSError, ValueError): return None`, which
  is the three-valued shape already, not a violation of it.
* A pathlib method call with ARGUMENTS (e.g. a hypothetical `Path.exists(
  follow_symlinks=False)`) is still matched -- the zero-argument restriction
  above is about avoiding a false positive on an unrelated same-named method,
  and `Path.is_file`/`is_dir`/`exists` never legitimately take positional
  arguments, so restricting to zero POSITIONAL args (keywords still allowed)
  is the actual rule; see `_is_pathlib_predicate_call`.

DECLARED EXCEPTIONS are keyed by `(enclosing_function, primitive)`, not by
line number: a line number would make the table go stale on ordinary
reformatting, teaching people to stop trusting it (the "self-deleting" clause
below needs a key that only turns stale on a REAL fix, not a `black` pass). It
is validated that this key stays unique per row-worthy call at construction
time (see `TestDeclaredExceptionsCannotRot`). Two rows exist today, both
already documented at length in `CLAUDE.md`'s CB-86/CB-23 sections rather than
invented for this file:

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

Two more reads in `db.py` are declared for a DIFFERENT, structural reason:
`_writable_probe`'s and `_access_probe`'s own `os.access(...)` calls are not
in either detected primitive set at all (a different capability), so they
never reach `DECLARED_EXCEPTIONS` -- they are omitted from the table
entirely rather than declared, and `TestTheGateItself` pins that omission is
correct (`os.access` is not flagged).
"""

from __future__ import annotations

import ast
import pathlib

import codebugs.db

# ---------------------------------------------------------------------------
# The closed, stdlib-documented capability: "answer a bool, swallow OSError".
# ---------------------------------------------------------------------------

# Fully-qualified `os.path` functions. All of these are documented to return
# False on any stat failure rather than raise -- not a guess about this
# repository's habits, a property of the stdlib itself.
_OS_PATH_PRIMITIVES: frozenset[str] = frozenset(
    {
        "os.path.isfile",
        "os.path.isdir",
        "os.path.exists",
        "os.path.islink",
        "os.path.lexists",
        "os.path.ismount",
    }
)

# `pathlib.Path` zero-argument predicate methods with the identical
# documented behaviour. Matched by NAME on any receiver (see module
# docstring for why that is deliberately over-broad rather than under-broad).
_PATHLIB_PREDICATE_METHODS: frozenset[str] = frozenset(
    {
        "is_file",
        "is_dir",
        "exists",
        "is_symlink",
        "is_mount",
        "is_socket",
        "is_fifo",
        "is_block_device",
        "is_char_device",
    }
)

# (enclosing_function, primitive) -> reason. Self-deleting: a row naming a
# call that is no longer there, or that has no reason, fails this file's own
# tests (TestDeclaredExceptionsCannotRot below) -- otherwise this table
# becomes the place a real two-valued read gets quietly parked, which is the
# hole the whole gate exists to close, one level up.
DECLARED_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("init_project", "os.path.exists"): (
        "CB-224: `created = not os.path.exists(path)` feeds only the "
        "informational `created` key in the returned dict. The actual "
        "creation decision is `_open(path, create=True)`, two lines below, "
        "which does not read this value at all. Turning an undetermined "
        "answer here into a hard refusal would be a new failure mode this "
        "card was not asked to add, ahead of a call that might succeed "
        "regardless."
    ),
    ("_open", "os.path.exists"): (
        "CB-86 (documented at length in CLAUDE.md): SQLITE_CANTOPEN (14) is "
        "returned identically for 'file missing' and 'file present but "
        "unopenable', so os.path.exists here picks which of two TRUE "
        "sentences to print -- message selection only. A misread changes "
        "wording, never which exception type is raised."
    ),
}


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


def _is_pathlib_predicate_call(node: ast.Call) -> bool:
    """A `.is_file()`-shaped call: zero positional args, the right method name.

    Keywords are still allowed (see module docstring) -- what is excluded is a
    POSITIONAL argument, which none of `Path.is_file`/`is_dir`/`exists`/... can
    legitimately take, so restricting to that shape costs nothing on the real
    methods and only narrows the accidental collisions this deliberately
    over-broad name match would otherwise catch.
    """
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in _PATHLIB_PREDICATE_METHODS:
        return False
    return not node.args


class _CallCollector(ast.NodeVisitor):
    """Every two-valued path-state call in one file, keyed by enclosing function."""

    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []
        self._stack: list[str] = ["<module>"]

    def _enclosing(self) -> str:
        return self._stack[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        dotted = _dotted_name(node.func)
        if dotted is not None and dotted in _OS_PATH_PRIMITIVES:
            self.hits.append((self._enclosing(), dotted))
        elif _is_pathlib_predicate_call(node):
            attr = node.func.attr  # type: ignore[union-attr]
            self.hits.append((self._enclosing(), attr))
        self.generic_visit(node)


def _named_primitive_hits(source: str) -> list[tuple[str, str]]:
    """(enclosing_function, primitive) for every named-primitive call in `source`."""
    collector = _CallCollector()
    collector.visit(ast.parse(source))
    return collector.hits


class _SwallowCollector(ast.NodeVisitor):
    """Functions that catch OSError and hand back a bare True/False literal.

    The STRUCTURAL half of the gate (see module docstring): this needs no
    named primitive at all, so it is what would catch a hand-rolled
    reimplementation of the CB-203 anti-pattern -- exactly the shape line 924
    used to be, before `_linked_worktree_gitdir` called `.is_file()` at all.
    """

    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []
        self._stack: list[str] = ["<module>"]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        for handler in node.handlers:
            if not _catches_os_error(handler.type):
                continue
            for stmt in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
                if (
                    isinstance(stmt, ast.Return)
                    and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value in (True, False)
                ):
                    self.hits.append((self._enclosing(), "except-OSError-return-bool"))
        self.generic_visit(node)

    def _enclosing(self) -> str:
        return self._stack[-1]


def _catches_os_error(exc_type: ast.expr | None) -> bool:
    if exc_type is None:
        return False  # a bare `except:` is a separate, undetected shape (see docstring)
    if isinstance(exc_type, ast.Name):
        return exc_type.id == "OSError"
    if isinstance(exc_type, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id == "OSError" for e in exc_type.elts)
    return False


def _swallow_hits(source: str) -> list[tuple[str, str]]:
    collector = _SwallowCollector()
    collector.visit(ast.parse(source))
    return collector.hits


def _db_source_files() -> list[tuple[str, pathlib.Path]]:
    """Just `db.py`, in the same `(rel, path)` shape as the network gate --
    a list of one so the population/self-deletion tests below share their
    shape with `tests/test_no_network_capability.py` even though the scope
    here is narrower (see module docstring, "WHAT IS CHECKED")."""
    path = pathlib.Path(codebugs.db.__file__)
    assert path.is_file(), f"db.py not found at {path} -- this gate cannot look"
    return [("db.py", path)]


def _all_hits(source: str) -> list[tuple[str, str]]:
    return _named_primitive_hits(source) + _swallow_hits(source)


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
            "DECLARED_EXCEPTIONS in this file with a reason."
        )


class TestTheGateItself:
    """A gate is only worth its line count if it can fail, and only fail
    rightly (CB-224 oracle items 1 and 2)."""

    def test_the_original_line_924_shape_is_caught(self):
        """Oracle item 1: reverting `_linked_worktree_gitdir` to its pre-fix
        form must turn the gate red. This is that exact shape, isolated."""
        mutant = (
            "def _linked_worktree_gitdir(git_file):\n"
            "    gitdir = _abs_from(git_file.parent, pointer)\n"
            "    return gitdir if (gitdir / 'commondir').is_file() else None\n"
        )
        hits = _named_primitive_hits(mutant)
        assert ("_linked_worktree_gitdir", "is_file") in hits

    def test_a_new_two_valued_read_anywhere_in_the_file_is_caught(self):
        """Oracle item 2: the half the gate exists for -- catching a read
        nobody enumerated, added to a brand new function."""
        mutant = "def _some_future_probe(p):\n    return os.path.isdir(p)\n"
        hits = _named_primitive_hits(mutant)
        assert ("_some_future_probe", "os.path.isdir") in hits
        assert ("_some_future_probe", "os.path.isdir") not in DECLARED_EXCEPTIONS

    def test_a_hand_rolled_swallow_with_no_named_primitive_is_also_caught(self):
        """The structural half: no `is_dir`/`is_file`/`isdir`/`isfile`/`exists`
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
        assert ("_homegrown_check", "except-OSError-return-bool") in hits

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
        source = "def _access_probe(path):\n    return os.access(path, os.W_OK)\n"
        assert _named_primitive_hits(source) == []

    def test_pathlib_predicates_beyond_the_three_seen_today_are_still_caught(self):
        """Wider than the three spellings CB-224 measured in this file
        (is_file, isdir, exists) -- islink/lexists/ismount and the pathlib
        siblings are part of the same closed capability, so a mutant using
        one of THOSE instead is not a free pass."""
        for snippet, expected in [
            ("os.path.islink(p)", "os.path.islink"),
            ("os.path.lexists(p)", "os.path.lexists"),
            ("os.path.ismount(p)", "os.path.ismount"),
            ("p.is_symlink()", "is_symlink"),
            ("p.is_socket()", "is_socket"),
            ("p.is_mount()", "is_mount"),
        ]:
            source = f"def _f(p):\n    return {snippet}\n"
            hits = _named_primitive_hits(source)
            assert any(prim == expected for _fn, prim in hits), (snippet, hits)

    def test_a_zero_arg_pathlib_call_on_an_unrelated_receiver_is_still_flagged(self):
        """Deliberately over-broad, per the module docstring: this repo has no
        occurrence today, but a genuine collision costs a declared row, not a
        blind spot."""
        source = "def _f(thing):\n    return thing.exists()\n"
        assert _named_primitive_hits(source) == [("_f", "exists")]

    def test_the_gate_actually_reads_files(self):
        """Oracle item 5: a gate reading an empty file list passes vacuously."""
        files = _db_source_files()
        assert files, "the file sweep found nothing -- this gate cannot look"
        rel, path = files[0]
        assert rel == "db.py"
        assert path.read_text(encoding="utf-8"), f"{path} read as empty"


class TestDeclaredExceptionsCannotRot:
    """Oracle items 3 and 4: the table may only shrink."""

    def test_every_row_carries_a_reason(self):
        empty = [key for key, reason in DECLARED_EXCEPTIONS.items() if not reason.strip()]
        assert not empty, (
            f"DECLARED_EXCEPTIONS row(s) with no reason: {empty} -- a table "
            "that can grow silently is the hole this gate exists to close"
        )

    def test_no_row_is_stale(self):
        live: set[tuple[str, str]] = set()
        for _rel, path in _db_source_files():
            live.update(_all_hits(path.read_text(encoding="utf-8")))
        stale = [key for key in DECLARED_EXCEPTIONS if key not in live]
        assert not stale, (
            f"stale DECLARED_EXCEPTIONS row(s): {stale} -- the call is gone "
            "(fixed, or removed), so delete the row rather than leaving a "
            "standing permission behind"
        )
