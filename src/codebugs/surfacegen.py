"""Surface generator — build an MCP tool and a CLI verb from ONE declaration.

WHAT THIS IS. Every domain module in this package writes its exposure twice: a
`@mcp.tool()`-decorated function whose signature, docstring and return type are
the MCP surface, and an `add_parser`/`add_argument` block whose flags, `nargs`,
`choices`, defaults and `help=` are the CLI surface. This module takes a
DECLARATION — a plain Python data structure naming both sides once — and emits
both. It is the pilot instrument for BT-6: the question it exists to answer is
whether a module's code SHRINKS when its surface is declared instead of written.

WHAT IT DELIBERATELY IS NOT. It is not a dispatcher over handler names. The
declaration carries the surface CONTENT — the tool name, the whole docstring,
every parameter with its real annotation and default, every argparse keyword —
and this module is what turns that content into a registered tool and a built
parser. A declaration may name a handwritten BODY (`manual_handler=`), and that
body carries LOGIC only: it registers nothing, it names nothing, and removing
this module's emitters removes the surface regardless of what those bodies say.
The distinction matters because the reverse arrangement — a declaration holding
a name and an installer holding the content — passes every behavioural check
while migrating nothing, and BT-6 spent seven review rounds establishing that.

WHY THE ANNOTATION VOCABULARY LIVES HERE (a measured cost of the pilot, not a
preference). The declaration file is held to a restricted grammar so that the
prose counter can read it invariantly (`module_surface.py --lint-declarations`),
and that grammar admits DATA only: no operator, no subscript. `str | None` is a
binary operator and `list[str]` is a subscript, so a declaration file cannot
spell an optional or a parameterised type at all. The names below are that
vocabulary, imported by the declaration file and used as bare names. Ordinary
types that need neither spelling (`str`, `int`, `bool`) are written directly.

WHY REAL TYPE OBJECTS, NOT STRINGS. The SDK builds each tool's argument model
with `inspect.signature(func, eval_str=True)` AT REGISTRATION TIME, so the
emitted function must carry genuine objects in `__signature__`/`__annotations__`.
A stringly-typed declaration would also hand the prose counter a payload of
"code written inside string literals", which is precisely the accounting the
pilot must not do.

WHY `__doc__` AND NOT `description=`. `server._NormalizedDescriptions` dedents a
tool's docstring only when the caller passes no `description=`, while the golden
collector registers on a RAW server and dedents by itself. A generated tool
passing `description=` would therefore match the golden byte for byte and still
ship un-dedented text to clients — CB-73 resurrected behind the very gate built
to catch it. So the emitter sets `__doc__` (and `__name__`, which the golden
reads through `inputSchema.title`) and passes no description.

SCOPE. Generic: it knows nothing about any domain module, holds no schema, and
issues no queries of any kind. It is imported BY a domain module and never
registers itself.
"""

from __future__ import annotations

import inspect
from typing import Any

# The annotation vocabulary. See the module docstring for why it is here and not
# in the declaration file.
OPT_TEXT = str | None
OPT_INT = int | None
OPT_TEXT_LIST = list[str] | None
OPT_OBJECT = dict[str, Any] | None
OPT_TEXT_OR_ARRAY = str | list | None

#: Every tool in this package answers with a JSON object, so the return
#: annotation is the generator's, not the declaration's.
RESULT = dict[str, Any]

_REQUIRED = object()


class DeclarationError(ValueError):
    """A declaration the emitters refuse to build a surface from.

    Raised at REGISTRATION time, which is startup: a malformed declaration must
    kill the server rather than yield a module that is quietly missing a tool.
    """


#: The two sides a capability can be exposed on. A declaration may carry one or
#: both — an asymmetric capability (CLI-only, MCP-only) is a real shape in this
#: package — but it must carry at least one, and it may carry nothing else.
SIDES = ("mcp", "cli")

_FACET_KEYS = {
    "mcp": ({"name", "doc", "params"}, {"calls", "manual_handler"}),
    "cli": ({"name", "help", "args"}, {"manual_handler"}),
}


def _validate_declaration(index: int, decl: Any) -> None:
    """Refuse a declaration whose keys this module does not recognise.

    THE UNRECOGNISED KEY IS THE WHOLE POINT, and this is FAIL-OPEN WITHOUT IT.
    Reading a side with `decl.get(side)` and skipping on `None` means a MISSPELLED
    side key — `cl` for `cli` — silently emits one surface fewer and exits 0.
    Cross-model review reproduced exactly that: three verbs instead of four, no
    diagnostic anywhere. For a module whose entire job is exposure, "one
    capability quietly stopped existing" is the worst answer available, and it
    cannot be distinguished from the LEGITIMATE one-sided declaration by any
    later check — so the unknown is refused here, before anything is built.
    """
    if not isinstance(decl, dict):
        raise DeclarationError(
            f"declaration {index}: must be a dict, got {type(decl).__name__}"
        )
    unknown = sorted(set(decl) - set(SIDES))
    if unknown:
        raise DeclarationError(
            f"declaration {index}: unknown key(s) {unknown} — a declaration carries "
            f"only {list(SIDES)} (a misspelled side would silently drop a surface)"
        )
    if not any(decl.get(side) is not None for side in SIDES):
        raise DeclarationError(f"declaration {index}: names no side at all")


def _validate_facet(index: int, side: str, facet: Any) -> None:
    """Refuse a facet with a missing or unrecognised key.

    Required keys are checked HERE rather than left to a `KeyError` at build
    time, because `DeclarationError` is what this module's contract promises and
    half the malformations reaching a caller as `KeyError` makes that contract
    half true. Unknown keys are refused for `_validate_declaration`'s reason one
    level down: a misspelled `manual_handler` would otherwise leave a tool with
    neither body form, and a misspelled `default` would make a parameter
    required.
    """
    if not isinstance(facet, dict):
        raise DeclarationError(
            f"declaration {index}: {side!r} facet must be a dict, got {type(facet).__name__}"
        )
    required, optional = _FACET_KEYS[side]
    missing = sorted(required - set(facet))
    if missing:
        raise DeclarationError(f"declaration {index}: {side!r} facet is missing {missing}")
    unknown = sorted(set(facet) - required - optional)
    if unknown:
        raise DeclarationError(
            f"declaration {index}: {side!r} facet has unknown key(s) {unknown} — "
            f"accepted: {sorted(required | optional)}"
        )


def _signature(params) -> tuple[inspect.Signature, dict[str, Any]]:
    """Build the real `Signature` and `__annotations__` for a declared parameter list.

    A parameter with no `default` key is REQUIRED — absence is the declaration,
    because a sentinel default written in the declaration file would be a value
    the grammar cannot express and the counter would book as data.
    """
    seen: set[str] = set()
    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    for spec in params:
        name = spec["name"]
        if name in seen:
            raise DeclarationError(f"parameter {name!r} declared twice")
        seen.add(name)
        annotation = spec["type"]
        default = spec.get("default", _REQUIRED)
        kind = inspect.Parameter.POSITIONAL_OR_KEYWORD
        if default is _REQUIRED:
            sig_params.append(inspect.Parameter(name, kind, annotation=annotation))
        else:
            sig_params.append(inspect.Parameter(name, kind, annotation=annotation, default=default))
        annotations[name] = annotation
    annotations["return"] = RESULT
    return inspect.Signature(sig_params, return_annotation=RESULT), annotations


def build_tool(facet: dict[str, Any], conn_factory) -> Any:
    """One callable carrying the declared MCP surface, ready to register.

    Two body forms, and the declaration picks: `calls=<domain function>` forwards
    every declared parameter by name inside a connection, which is the whole body
    for a surface that only delegates; `manual_handler=<function>` hands the
    connection FACTORY to a handwritten body, so a body that must validate before
    it connects, or dispatch on which argument arrived, keeps doing exactly that.
    """
    name = facet["name"]
    doc = facet["doc"]
    params = facet["params"]
    calls = facet.get("calls")
    manual = facet.get("manual_handler")
    if (calls is None) == (manual is None):
        raise DeclarationError(
            f"tool {name!r} must declare exactly one of calls= / manual_handler="
        )

    signature, annotations = _signature(params)

    # BIND, then APPLY DEFAULTS, and only then call. `__signature__` is what the
    # SDK reads to build the argument model, but it is not what Python enforces
    # at the call — the emitted callable really takes `*args, **kwargs`, so a
    # caller that omits an optional argument (the raw-function path
    # `tests/test_bench.py` uses, and any in-process caller) would otherwise
    # reach the body with the parameter simply MISSING rather than defaulted.
    # Binding through the declared signature makes the emitted callable behave as
    # the hand-written `def` it replaces, including the TypeError on a missing
    # required argument or an unknown name. ONE DECLARED COST, because it is a
    # real difference and not a claim of equivalence: the TEXT of that TypeError
    # is `Signature.bind`'s ("missing a required argument: 'benchmark'"), not
    # CPython's call-site text naming the function. Nothing in the exposed
    # surface carries it — the MCP path validates through pydantic long before
    # the call — so it is visible only to an in-process caller reading the
    # message, and closing it would mean generating and `exec`ing real source.
    def tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        if manual is not None:
            return manual(conn_factory, **bound.arguments)
        with conn_factory() as conn:
            return calls(conn, **bound.arguments)

    tool.__signature__ = signature
    # `__annotations__` is INSURANCE and a mutant that corrupts it SURVIVES the
    # suite — said that way round rather than claimed as covered. `__signature__`
    # is what `inspect.signature` returns first, so the SDK never reads this
    # attribute today; it is here so a future reader (`get_type_hints`, a
    # documentation tool) does not find the emitted callable annotation-less.
    tool.__annotations__ = annotations
    tool.__name__ = name
    tool.__qualname__ = name
    tool.__doc__ = doc
    return tool


def _facets(declarations, side: str) -> list[dict[str, Any]]:
    """Every facet of one side, VALIDATED AS A SET before anything is emitted.

    The duplicate check runs here rather than after the emission loop, and the
    difference is not cosmetic: registering first and validating afterwards
    leaves the server or the parser half-built when the refusal arrives —
    argparse raises its own conflict error before `DeclarationError` can, and an
    MCP registrar may simply overwrite. A declaration set is either buildable or
    it is refused, and it must be refused before it has mutated anything.
    """
    facets: list[dict[str, Any]] = []
    for index, decl in enumerate(declarations):
        _validate_declaration(index, decl)
        facet = decl.get(side)
        if facet is None:
            continue
        _validate_facet(index, side, facet)
        facets.append(facet)
    names = [f["name"] for f in facets]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise DeclarationError(f"duplicate {side} name(s) declared: {duplicates}")
    return facets


def emit_tools(mcp, conn_factory, declarations) -> list[str]:
    """Register every declared MCP tool. Returns the names, in declaration order.

    The loop binds each declaration into `build_tool`'s frame rather than into a
    closure over the loop variable — late binding in exactly this shape is the
    classic way a generated surface ends up with N copies of the last tool.
    """
    facets = _facets(declarations, "mcp")
    built = [build_tool(facet, conn_factory) for facet in facets]
    for fn in built:
        mcp.tool()(fn)
    return [fn.__name__ for fn in built]


def emit_cli(sub, commands, declarations) -> list[str]:
    """Build every declared CLI verb's parser and wire its handler.

    Argparse keywords pass through UNTRANSLATED: whatever the declaration writes
    beside `flags` is what `add_argument` receives. That is deliberate — a
    translation layer here would be a second vocabulary to keep in agreement with
    argparse's, and the declaration would stop being a readable statement of the
    surface.
    """
    facets = _facets(declarations, "cli")
    for facet in facets:
        if facet.get("manual_handler") is None:
            raise DeclarationError(f"verb {facet['name']!r} declares no manual_handler")
    for facet in facets:
        name = facet["name"]
        parser = sub.add_parser(name, help=facet["help"])
        for arg in facet["args"]:
            flags = arg["flags"]
            options = {key: value for key, value in arg.items() if key != "flags"}
            parser.add_argument(*flags, **options)
        commands[name] = facet["manual_handler"]
    return [facet["name"] for facet in facets]
