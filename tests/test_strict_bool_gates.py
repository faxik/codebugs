"""CB-151 ratchet: every bool-carrying MCP tool parameter is either declared
STRICT or listed below with a reason.

WHY THIS FILE EXISTS. CB-151 found that `milestone_reconcile`'s `apply`
parameter carried the annotation `bool | int | str | None`, and pydantic's
lax mode coerces a JSON float (1.0, 0.0) into a real `bool` BEFORE the
handwritten `isinstance(apply, bool)` check ever runs -- so the one dry-run
gate standing between an MCP client and a bulk mutation could not fire for
exactly the two literals that most look like a mistaken boolean. The DIR-1
holder measured the true population by walking every `@mcp.tool`-decorated
function by AST and found 14 bool-carrying parameters, ALL of them coercible
(a plain `bool` coerces even more silently than the union did: `1.0 -> True`,
`1 -> True`, `'true' -> True`, `'false' -> False`). Fixing `apply` alone and
declaring victory would be exactly the enumeration-instead-of-primitive
defect CB-151 itself exists to correct (CLAUDE.md: "a rule expressed as an
enumeration gets fixed at the sites someone enumerated, and the population is
always larger than the list").

THE FIX. `Annotated[bool, Field(strict=True)]` refuses any non-bool JSON
value at the pydantic validation boundary, before the tool body runs, and
its JSON Schema is byte-identical to a plain `bool` field's
(`{"type": "boolean", ...}`) -- verified by building two real MCPServer
instances and comparing `list_tools()[0].input_schema`. So the wire golden
does not move for a parameter that was ALREADY a plain `bool` (the other 13
in the population); `milestone_reconcile.apply` is the one exception, because
removing its now-dead `bool | int | str | None` union necessarily narrows its
own schema too (pydantic raises at schema-build time if `Field(strict=True)`
is applied directly to a Union -- there is no way to keep the union AND make
it strict). That one schema change is deliberate, understood, and reported
separately; it is not what this ratchet exists to police.

THE RATCHET. `_collect_bool_params` walks `db.get_tool_providers(mode="all")`
-- the ACTUAL REGISTRY every real MCP server (`server.py`) and the golden
generator (`tests/_mcp_schema.py`) both consume -- rather than a hardcoded
list of domain modules. A `_SignatureCapture` object stands in for the real
`MCPServer` at registration time and records the real Python function object
each provider hands to `.tool()`, so this reads the SAME annotations pydantic
itself will read; it never has to guess at pydantic's internal FieldInfo
representation, because `_is_strict_bool` answers the question the way
CB-151 itself was diagnosed -- by building a real `pydantic.TypeAdapter` for
the exact annotation and checking, behaviourally, whether it accepts `True`/
`False` and refuses the coercible literals that broke `apply`. This is
"keying on the primitive, not enumerating values" applied to the RATCHET
itself, not just to the fix: the probe set below is fixed, but what varies
per parameter is only the annotation being tested, never a per-tool special
case.

DECLARED EXCEPTIONS. A bool-carrying parameter that is not (yet) strict must
appear in `DECLARED_EXCEPTIONS` below, keyed by (tool_name, param_name), with
a non-empty reason naming who owns the gap and why. An exception without a
reason is refused by this file's own tests -- an exceptions table that can
grow silently is the same hole this ratchet exists to close, one level up.
The table is SELF-DELETING: once a parameter is made strict, its row must be
removed (a stale row -- one naming a parameter that is already strict, or
that no longer exists at all -- is refused too), so the table can only ever
shrink towards the true, growing set of strict parameters.
"""

from __future__ import annotations

import inspect
import typing

import pydantic

from codebugs import db

# ---------------------------------------------------------------------------
# Declared exceptions -- non-strict bool-carrying parameters this unit did
# NOT make strict, and why. Every row must carry a real, current reason.
# ---------------------------------------------------------------------------

# DIR-2 territory (findings.py / loc.py): this unit's brief (L3-BRIEF-DIR-1-
# T-52-cb151-strict-bool-gates.md, CB-151) is explicit that these belong to
# the other direction and must not be touched here -- "территория DIR-2, юнит
# передаётся куратором". DIR-2 has since taken the two `anchor_recapture` rows
# (T-50, BT-7 Т-c): both bools are strict now, and a third one landed strict
# beside them rather than growing this table, which may only shrink. `categories_normalize.apply` is named in the
# same brief as an exact twin of THIS card's own defect (a mass re-key with a
# dry-run default), so it is the highest-priority row in this table when
# DIR-2 picks it up.
DECLARED_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("add", "new_category"): (
        "CB-151: DIR-2 territory (findings.py). Gates minting a new category "
        "(CB-60); owned by the direction working findings.py/loc.py, not this "
        "unit."
    ),
    ("batch_add", "new_category"): (
        "CB-151: DIR-2 territory (findings.py), same gate as add.new_category "
        "applied batch-wide."
    ),
    ("categories_normalize", "apply"): (
        "CB-151: DIR-2 territory (findings.py). EXACT TWIN of this card's own "
        "defect -- a mass re-key with a dry-run default -- so this is the "
        "highest-priority row in this table for DIR-2 to pick up."
    ),
    # The four codesweep_* rows that used to live here (codesweep_mark.processed,
    # codesweep_list_items.include_archived/archived_only, codesweep_list.
    # include_archived) are GONE, not merely emptied of reason: CB-154 made
    # strictness a property of codebugs.surfacegen.emit_tools itself -- every
    # declared `type=bool` parameter is widened to
    # Annotated[bool, Field(strict=True)] in `_signature` before a tool is
    # built, so sweep_surface.py's plain `bool` declarations are strict at
    # registration time with no change to the declaration grammar. Ownership
    # is no longer "undetermined": surfacegen.py and sweep_surface.py are
    # DIR-1 territory (BT-6 pilot artifacts), which is what let this unit
    # close the table rather than merely re-word it.
}


# ---------------------------------------------------------------------------
# Registry walk -- goes through db.get_tool_providers, never a module list.
# ---------------------------------------------------------------------------


class _SignatureCapture:
    """Stands in for an `MCPServer` at registration time.

    Every domain module's `register_tools` calls only `mcp.tool(...)` at
    registration (server.py's own `_NormalizedDescriptions` docstring: "68
    times, verified by sweep"), so recording the function each call decorates
    is a faithful, minimal stand-in -- no real MCPServer, no schema building,
    no pydantic model construction needed just to enumerate what exists.
    """

    def __init__(self) -> None:
        self.functions: dict[str, typing.Callable] = {}

    def tool(self, name: str | None = None, **_kwargs: object):
        def decorator(fn: typing.Callable) -> typing.Callable:
            key = name or fn.__name__
            self.functions[key] = fn
            return fn

        return decorator


def _dummy_conn_factory():
    raise AssertionError(
        "the ratchet only inspects signatures at registration time; "
        "it must never actually call a tool body"
    )


def _is_bool_carrying(annotation: object) -> bool:
    """True if `annotation`'s declared type includes `bool` at all.

    Unwraps one level of `Annotated[...]` (the strict-bool form and the
    plain-bool form both need this to compare on the same footing), then
    checks either a bare `bool` or a `bool` member of a Union (covering the
    pre-fix `bool | int | str | None` shape, so a NEW union-shaped hole would
    still be found by this ratchet even though it is not this card's fix).

    **Union MEMBERS are unwrapped too, and until CB-197 they were not — which
    made this ratchet stop seeing the very parameter that card changed.** The
    member test used to be `bool in typing.get_args(base)`, an identity test
    against the bare builtin, so a Union whose member is `Annotated[bool, ...]`
    answered False and the parameter left the population SILENTLY: not a
    failure, not an "undeclared" verdict, simply one fewer row. `OPT_BOOL`
    (`surfacegen.OPT_BOOL = STRICT_BOOL | None`) is the first annotation of that
    shape in the package, so before CB-197 the case was unreachable and the
    identity test was sufficient.

    Measured, because the direction is the whole point and the first report of
    this said only that coverage was "lost": the NAIVE dangerous spelling
    `bool | None` was still caught either way (carrying=True, strict=False, so
    the ratchet goes red), so a future author simplifying `OPT_BOOL` was never
    unguarded. What the old test could not see is a Union carrying an
    ANNOTATED-but-NOT-STRICT bool — `Annotated[bool, Field()] | None` — which is
    both invisible to it and coercible, and which only became writable once
    CB-197 established the Union-of-Annotated pattern for others to copy. That
    is the hole this closes, and `TestBoolCarryingSeesThroughAnnotatedUnionMembers`
    pins all three spellings so the distinction cannot be re-flattened.
    """
    base = annotation
    if typing.get_origin(annotation) is typing.Annotated:
        base = typing.get_args(annotation)[0]
    if base is bool:
        return True
    origin = typing.get_origin(base)
    if origin is typing.Union:
        for member in typing.get_args(base):
            if member is bool:
                return True
            if typing.get_origin(member) is typing.Annotated:
                if typing.get_args(member)[0] is bool:
                    return True
    return False


# The exact probe set CB-151 itself was diagnosed with (see the card body's
# own `TypeAdapter(...).validate_python(...)` transcript). `1.0`/`0.0` are
# the literals a plain `bool` OR the old union both coerce silently; the rest
# round out the JSON scalar space a client might plausibly send in place of a
# real boolean.
_COERCIBLE_PROBES: tuple[object, ...] = (1.0, 0.0, 1, 0, "true", "false", "1", "0", "")


def _is_strict_bool(annotation: object) -> bool:
    """Behavioural, not structural: build a real `pydantic.TypeAdapter` for
    the exact annotation and ask it whether it accepts True/False and
    refuses every coercible probe.

    This is deliberately the SAME technique the CB-151 card and this unit's
    brief used to diagnose and verify the defect (a real pydantic validation
    path, not a hand-parsed read of `FieldInfo.metadata`), so the ratchet
    cannot disagree with the mechanism it exists to police. Building a
    `TypeAdapter` for the pre-fix `bool | int | str | None` union raises
    `RuntimeError` if `Field(strict=True)` metadata is attached to it, which
    is exactly why that union had to be REMOVED rather than patched; this
    function's `try/except` classifies any such build failure as "not
    strict", which is the correct verdict for a parameter this ratchet has
    not yet been shown a working strict form for.
    """
    try:
        adapter = pydantic.TypeAdapter(annotation)
    except Exception:
        return False
    try:
        if adapter.validate_python(True) is not True:
            return False
        if adapter.validate_python(False) is not False:
            return False
    except pydantic.ValidationError:
        return False
    for probe in _COERCIBLE_PROBES:
        try:
            adapter.validate_python(probe)
        except pydantic.ValidationError:
            continue
        return False
    return True


def _collect_bool_params() -> list[tuple[str, str, object]]:
    """(tool_name, param_name, annotation) for every registered MCP tool
    parameter whose declared type includes `bool`.

    Walks `db.get_tool_providers(mode="all")` -- the registry, not a name a
    human enumerated. This is the load-bearing structural property CLAUDE.md
    demands of this ratchet (see `TestRatchetWalksTheRegistry` below).
    """
    found: list[tuple[str, str, object]] = []
    for provider in db.get_tool_providers(mode="all"):
        capture = _SignatureCapture()
        provider.register_fn(capture, _dummy_conn_factory)
        for tool_name, fn in capture.functions.items():
            hints = typing.get_type_hints(fn, include_extras=True)
            for pname in inspect.signature(fn).parameters:
                annotation = hints.get(pname)
                if annotation is None:
                    continue
                if _is_bool_carrying(annotation):
                    found.append((tool_name, pname, annotation))
    return found


# ---------------------------------------------------------------------------
# The ratchet itself
# ---------------------------------------------------------------------------


class TestStrictBoolGateRatchet:
    def test_every_bool_carrying_param_is_strict_or_declared(self):
        found = _collect_bool_params()
        # Sanity: the sweep must find *something*, or every assertion below
        # would pass vacuously and this file would be silently dead.
        assert len(found) >= 14, (
            f"expected at least the 14 bool-carrying parameters measured for "
            f"CB-151, found {len(found)}: {[(t, p) for t, p, _ in found]}"
        )

        undeclared = [
            (tool_name, pname)
            for tool_name, pname, annotation in found
            if not _is_strict_bool(annotation)
            and (tool_name, pname) not in DECLARED_EXCEPTIONS
        ]
        assert not undeclared, (
            "bool-carrying MCP tool parameter(s) are coercible (not strict) "
            f"and not declared as an exception: {undeclared}. Either make "
            "them Annotated[bool, Field(strict=True)] or add a reasoned row "
            "to DECLARED_EXCEPTIONS in this file."
        )

    def test_bool_carrying_sees_through_annotated_union_members(self):
        """CB-197: the ratchet must not lose a parameter to its own spelling.

        Three spellings of "an optional bool", and the middle column is what the
        pre-CB-197 identity test got wrong. The row that MATTERS is the third:
        it is coercible AND was invisible, so a parameter declared that way
        would have left the population without failing anything — a gate quietly
        not looking at the thing it exists to look at.

        The first row is here to keep the fix honest in the other direction: it
        was ALREADY caught before this change, so a report of "CB-197 blinded the
        ratchet" that does not distinguish these two rows is overstated. Both are
        asserted, so neither can be dropped as redundant.
        """
        strict_opt = typing.Annotated[bool, pydantic.Field(strict=True)] | None
        lax_opt = typing.Annotated[bool, pydantic.Field()] | None

        # 1. Bare bool in a Union: seen before and after, and correctly not strict.
        assert _is_bool_carrying(bool | None)
        assert not _is_strict_bool(bool | None)

        # 2. The shipped `OPT_BOOL` shape: now SEEN, and it passes on its merits.
        #    Reconstructed here rather than imported from `surfacegen`: this file
        #    is held to importing `db` and nothing else from the package (see
        #    `test_ratchet_walks_the_registry_not_a_module_list`), and widening
        #    that allowlist would buy nothing — the REAL constant is reached
        #    through the registry by the population test below, and any lax
        #    respelling of it fails this class's main assertion regardless.
        assert _is_bool_carrying(strict_opt)
        assert _is_strict_bool(strict_opt)

        # 3. The hole CB-197 made reachable and this closes: annotated, in a
        #    Union, and NOT strict. Seen now, and the ratchet's own assertion
        #    above would refuse it.
        assert _is_bool_carrying(lax_opt)
        assert not _is_strict_bool(lax_opt)

    def test_the_opt_bool_parameter_is_actually_in_the_population(self):
        """The composition, which the predicate test above cannot establish.

        `_is_bool_carrying` being right about the annotation in isolation does
        not put `codesweep_mark.processed` back into `_collect_bool_params()`:
        that walks the registry and reads `get_type_hints`, and a parameter can
        drop out anywhere along the way. Named explicitly rather than left to
        the `>= 14` floor, which a single missing row cannot cross.
        """
        found = _collect_bool_params()
        assert ("codesweep_mark", "processed") in [(t, p) for t, p, _ in found]

    def test_every_declared_exception_carries_a_non_empty_reason(self):
        empty = [key for key, reason in DECLARED_EXCEPTIONS.items() if not reason.strip()]
        assert not empty, (
            f"DECLARED_EXCEPTIONS row(s) with no reason: {empty} -- a table "
            "without reasons is the same hole this ratchet exists to close, "
            "one level up."
        )

    def test_every_declared_exception_still_names_a_real_non_strict_bool_param(self):
        """Self-deleting table: once a direction fixes its parameter, the row
        must be REMOVED, not left to rot. A stale row -- naming a parameter
        that is already strict, or that no longer exists -- is refused, so
        the table cannot silently grow into permission to skip real work."""
        found_by_key = {(t, p): a for t, p, a in _collect_bool_params()}
        stale = []
        for key in DECLARED_EXCEPTIONS:
            annotation = found_by_key.get(key)
            if annotation is None:
                stale.append((key, "no such bool-carrying parameter exists any more"))
            elif _is_strict_bool(annotation):
                stale.append((key, "parameter is already strict -- remove this row"))
        assert not stale, f"stale DECLARED_EXCEPTIONS row(s): {stale}"

    def test_ratchet_walks_the_registry_not_a_module_list(self):
        """Structural: `_collect_bool_params` must go through
        `db.get_tool_providers`, never a hardcoded list of domain modules --
        the exact defect one level up (an AST sweep for `@mcp.tool()` that
        cannot see sweep.py's surfacegen-declared tools) that this ratchet
        exists to avoid repeating inside its own implementation."""
        src = inspect.getsource(_collect_bool_params)
        assert "db.get_tool_providers" in src

        # And this file's own IMPORTS (parsed as actual import statements,
        # not a prose substring search -- this docstring itself mentions
        # "codebugs.milestones" in prose, which a naive substring check would
        # misread as an import) name only `db` from the package. Nothing here
        # could enumerate domain modules even by accident.
        import ast

        import tests.test_strict_bool_gates as _self

        tree = ast.parse(inspect.getsource(_self))
        imported_codebugs_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "codebugs":
                imported_codebugs_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported_codebugs_names.update(
                    alias.name for alias in node.names if alias.name.startswith("codebugs.")
                )
        assert imported_codebugs_names == {"db"}, (
            f"this ratchet file must import only `db` from codebugs, never a "
            f"domain module by name: found {imported_codebugs_names}"
        )


class TestMutantOracle:
    """CB-151 section 6: the mutant proof. These are pinned as ordinary
    ratchet-behaviour tests (mutating source under test is out of scope for
    a normal pytest run), but each corresponds to a mutation named in the
    unit's brief and its expected verdict."""

    def test_removing_strictness_from_a_declared_param_is_caught(self):
        """Simulates 'снять строгость с одного параметра' by asking the
        oracle directly about a plain (non-strict) bool annotation for a
        tool/param pair that is NOT in the exceptions table -- the ratchet's
        core predicate must flag it."""
        assert not _is_strict_bool(bool)
        assert ("milestone_reconcile", "apply") not in DECLARED_EXCEPTIONS

    def test_the_pre_fix_union_shape_is_never_classified_strict(self):
        """The exact shape CB-151 was filed against: `bool | int | str |
        None`, whether or not `Field(strict=True)` metadata is attached
        (pydantic refuses to build that combination at all, which
        `_is_strict_bool`'s try/except must classify as `False`, not raise)."""
        assert not _is_strict_bool(bool | int | str | None)
        annotated_union = typing.Annotated[
            bool | int | str | None, pydantic.Field(strict=True)
        ]
        assert not _is_strict_bool(annotated_union)

    def test_the_actual_fixed_annotations_are_classified_strict(self):
        """Every annotation this unit actually wrote must pass the oracle --
        if this test is red, the source edits and the ratchet disagree about
        what 'strict' means, which is worse than either being wrong alone."""
        strict = typing.Annotated[bool, pydantic.Field(strict=True)]
        assert _is_strict_bool(strict)

    def test_a_new_undeclared_bool_parameter_is_caught(self):
        """Simulates 'добавь новый bool-параметр без строгости': a
        synthetic non-strict bool parameter on a synthetic tool, run through
        the same population-check shape as the real ratchet."""

        class _Fake:
            def tool(self, name=None, **_kw):
                def deco(fn):
                    return fn

                return deco

        def fake_tool(new_param: bool = False) -> dict:
            return {}

        capture = _SignatureCapture()
        capture.tool()(fake_tool)
        hints = typing.get_type_hints(fake_tool, include_extras=True)
        annotation = hints["new_param"]
        assert _is_bool_carrying(annotation)
        assert not _is_strict_bool(annotation)
        assert ("fake_tool", "new_param") not in DECLARED_EXCEPTIONS
