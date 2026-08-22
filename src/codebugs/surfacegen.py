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


def _facet(decl: dict[str, Any], side: str) -> dict[str, Any] | None:
    facet = decl.get(side)
    if facet is None:
        return None
    if not isinstance(facet, dict):
        raise DeclarationError(f"{side!r} facet must be a dict, got {type(facet).__name__}")
    return facet


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
    # required argument or an unknown name.
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


def emit_tools(mcp, conn_factory, declarations) -> list[str]:
    """Register every declared MCP tool. Returns the names, in declaration order.

    The loop binds each declaration into `build_tool`'s frame rather than into a
    closure over the loop variable — late binding in exactly this shape is the
    classic way a generated surface ends up with N copies of the last tool.
    """
    emitted: list[str] = []
    for decl in declarations:
        facet = _facet(decl, "mcp")
        if facet is None:
            continue
        fn = build_tool(facet, conn_factory)
        mcp.tool()(fn)
        emitted.append(fn.__name__)
    if len(set(emitted)) != len(emitted):
        raise DeclarationError(f"duplicate tool name among {emitted}")
    return emitted


def emit_cli(sub, commands, declarations) -> list[str]:
    """Build every declared CLI verb's parser and wire its handler.

    Argparse keywords pass through UNTRANSLATED: whatever the declaration writes
    beside `flags` is what `add_argument` receives. That is deliberate — a
    translation layer here would be a second vocabulary to keep in agreement with
    argparse's, and the declaration would stop being a readable statement of the
    surface.
    """
    emitted: list[str] = []
    for decl in declarations:
        facet = _facet(decl, "cli")
        if facet is None:
            continue
        name = facet["name"]
        parser = sub.add_parser(name, help=facet["help"])
        for arg in facet["args"]:
            flags = arg["flags"]
            options = {key: value for key, value in arg.items() if key != "flags"}
            parser.add_argument(*flags, **options)
        handler = facet.get("manual_handler")
        if handler is None:
            raise DeclarationError(f"verb {name!r} declares no manual_handler")
        commands[name] = handler
        emitted.append(name)
    if len(set(emitted)) != len(emitted):
        raise DeclarationError(f"duplicate verb name among {emitted}")
    return emitted
