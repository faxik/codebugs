"""CB-157 ratchet: a parameter an MCP tool DECLARES must reach that tool's body.

WHY THIS FILE EXISTS. The acceptor of unit T-50 built mutant M11: hardcode
`include_unanchored=False` inside the body of the `anchor_recapture` MCP tool
while leaving the parameter in the signature and in
`tests/golden/mcp_schema.json`. The full suite stayed GREEN (2353 passed). A
client sees the parameter, the wire schema carries it, and the tool's behaviour
does not depend on it -- a success-shaped lie of the CB-15/CB-16 family, reached
through neither validation nor routing but through the body simply not reading
what it advertised.

THE CLASS. A check of a DECLARATION is not a check of an ACTION. The wire golden
is structurally incapable of telling a forwarded parameter from an ignored one,
because only the signature reaches the snapshot. This repository knows the shape
under other names: `additionalProperties: True` makes the golden useless as a
gate on RESPONSE shape (BT-5 answered with a behavioural MCP-result test), and
`TestKnownLimits` was vacuous because its fixture never asserted its own
existence. What makes THIS instance quiet is that the CLI half is healthy -- the
same mutant on the CLI handler fails loudly -- so a glance sees "the flag is
tested".

THE POPULATION WAS MEASURED BEFORE ANYTHING WAS FIXED, and the measurement is
the reason this file has the shape it has. `tests/manual/measure_cb157_forwarding.py`
reports it and `.claude/plans/T61-CB-157-survey.md` records the numbers. Over the
whole registry: 83 tools, 262 declared parameters, ZERO of which fail the gate
below. So CB-157's live population is EMPTY -- M11 is a mutant, not a finding --
and what this unit owes is therefore a mechanism that keeps it empty, not a fix.

THE ENUMERATION IS A REGISTRY WALK, NEVER AN AST SWEEP FOR `@mcp.tool`, and that
correction cost this unit its first measurement. An AST sweep for the decorator
finds 70 tools; the registry holds 83. The missing 13 are emitted by
`codebugs.surfacegen.emit_tools` from a data declaration (`sweep_surface.py`,
`bench_surface.py`) and never appear as a literal decorated `def`, so a
syntax-shaped sweep is structurally blind to them -- the identical blind spot
`tests/test_strict_bool_gates.py` records for CB-151's own population count.
Walking `db.get_tool_providers(mode="all")` with a stand-in recorder finds every
tool regardless of HOW it was registered, which is what a real server does.

THE TWO PREDICATES, and why the gate is the weaker of them.

  `_read_names`     -- the body LOADS the parameter's name at least once.
  `_call_arg_names` -- the parameter is handed DIRECTLY to some call, positionally
                       or by keyword, under any name.

`_read_names` is the GATE because it is SOUND: if a body never loads a name, the
value bound to it provably cannot influence anything the body does. That is a
theorem, not a heuristic, so a violation is always a defect and an exceptions
table for it would be incoherent. The one way to defeat it is dynamic access
(`locals()`, `vars()`, `eval`, `exec`), so the gate refuses a body that uses any
of them rather than quietly returning a verdict it cannot support; the tree
contains none today.

`_call_arg_names` is the stronger RATCHET and it is the one that needs
`ASSEMBLED_BY_THE_WRAPPER` below, because §3 of this unit's brief is right: not
every parameter is OBLIGED to reach the domain call. Four do not, all because the
wrapper ASSEMBLES the value into a different argument -- a payload it rebuilds, a
dict it fills in.

It resolves ONE local rebinding, which is what keeps a pure RENAME out of the
table: `cap = capacity or {...}` followed by `capacity=cap` satisfies the ratchet
with no row at all. That is not a convenience. A ratchet that demands a declared
exception for a rename is friction carrying no information, and this repository's
own lesson is that such a gate gets turned off by the first person it obstructs --
so the cheap, common, provably-fine shape must pass silently and only the shapes
where forwarding is genuinely invisible at the boundary should cost a line of
prose. Two hops, a subscript store or a loop target still cost one.

The naive alternative -- "passed as `name=name`" -- was measured and REJECTED: it
has 22 violations here, nearly all of them parameters passed POSITIONALLY, and
declaring 22 rows of amnesty for a difference of spelling would make the table
exactly the place defects hide.

WHAT `surfacegen` CONTRIBUTES, and why it is not 13 more rows. An emitted tool's
body is `calls(conn, **bound.arguments)` or `manual(conn_factory, **bound.arguments)`
-- ONE body, shared by all 13, forwarding every declared parameter by
construction. So the `calls=` form satisfies both predicates universally rather
than per-tool, and `test_the_emitted_body_forwards_by_construction` pins that
emitter body so the universal claim cannot quietly stop being true. The
`manual_handler=` form forwards into a HANDWRITTEN handler, where the M11 hole is
reachable one level down, so the check FOLLOWS the parameters into that handler's
own body. Stopping at the emitted wrapper would have been a gate that cannot
fire for exactly the tools whose bodies a human wrote.

THE TABLE IS SELF-DELETING, by the standard `tests/test_strict_bool_gates.py`
set for CB-151. A row must name a tool that still exists, a parameter that tool
still declares, a parameter the body still READS, and a parameter that still is
NOT handed to a call. Fail any of those four and the row is STALE and this file
REFUSES it -- so the table can only ever shrink, and a row cannot become a place
to park a real defect. A table that can only grow is not a set of exceptions, it
is an amnesty.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import typing

import pytest

from codebugs import db, surfacegen

# ---------------------------------------------------------------------------
# Declared exceptions to the CALL-ARGUMENT ratchet.
#
# Keyed by (tool_name, param_name). Every row names a parameter whose value the
# WRAPPER ASSEMBLES into a different argument instead of handing it to a call
# directly. Every row must carry a real, current reason, and every row is
# re-verified against the tree on each run (see the four staleness tests).
#
# This table is SELF-DELETING and may only ever SHRINK.
# ---------------------------------------------------------------------------
ASSEMBLED_BY_THE_WRAPPER: dict[tuple[str, str], str] = {
    ("batch_add", "findings"): (
        "CB-157: the wrapper iterates the declared list and builds a NEW list "
        "(`enriched`) carrying the per-row defaults it stamps in; that list is "
        "what reaches `batch_add_findings`. The declared value reaches the "
        "domain as the CONTENT of another argument."
    ),
    ("batch_add", "reported_at_commit"): (
        "CB-157: becomes `default_commit` and is stamped into every element of "
        "`enriched` that does not carry its own, so it reaches the domain "
        "inside the payload rather than as an argument of its own."
    ),
    ("batch_add", "reported_at_ref"): (
        "CB-157: stamped into every element of `enriched` that does not carry "
        "its own, by key assignment; same shape as reported_at_commit."
    ),
    ("milestone_add_item", "linked_frs"): (
        "CB-157: assembled into the `meta` dict (`meta['linked_frs']`), which "
        "is what `add_milestone_item` receives. `pull_next` eligibility reads "
        "it back out of meta, so the value really does reach the domain."
    ),
}

# Dynamic-access builtins that would let a body reach a parameter without ever
# loading its name, which is the one way the soundness argument for the READ
# gate can fail. A body using any of them is REFUSED rather than judged.
_DYNAMIC_ACCESS = frozenset({"locals", "vars", "eval", "exec", "globals"})


class _ToolCapture:
    """Stands in for an `MCPServer` at registration time.

    Recording the function each `mcp.tool(...)` call decorates is the same
    minimal stand-in `tests/test_strict_bool_gates.py` uses, and for the same
    reason: it walks the ACTUAL registry a real server consumes, so a tool is
    found however it was registered -- handwritten decorator or emitted by
    `surfacegen`.
    """

    def __init__(self) -> None:
        self.functions: dict[str, typing.Callable] = {}

    def tool(self, name: str | None = None, **_kwargs: object):
        def decorator(fn: typing.Callable) -> typing.Callable:
            self.functions[name or fn.__name__] = fn
            return fn

        return decorator


def _dummy_conn_factory():
    raise AssertionError(
        "this ratchet inspects signatures and source at registration time; "
        "it must never actually call a tool body"
    )


def _registered_tools() -> dict[str, typing.Callable]:
    capture = _ToolCapture()
    for provider in db.get_tool_providers(mode="all"):
        provider.register_fn(capture, _dummy_conn_factory)
    return capture.functions


def _is_emitted(fn: typing.Callable) -> bool:
    """True for a tool built by `surfacegen.build_tool`.

    Identified by its CODE OBJECT's origin rather than by a name or a marker
    attribute, because `build_tool` overwrites `__name__`/`__qualname__` with
    the declared tool name -- so those carry no trace of where the callable
    came from, while `co_filename`/`co_name` cannot be rewritten by the
    emitter's own bookkeeping.
    """
    code = getattr(fn, "__code__", None)
    if code is None:
        return False
    return code.co_filename == surfacegen.__file__ and code.co_name == "tool"


def _emitted_target(fn: typing.Callable) -> tuple[str, typing.Callable | None]:
    """('calls'|'manual', target) for an emitted tool, read from its closure."""
    code = fn.__code__
    cells = dict(zip(code.co_freevars, (c.cell_contents for c in fn.__closure__ or ())))
    if cells.get("calls") is not None:
        return "calls", cells["calls"]
    return "manual", cells.get("manual")


def _declared_params(fn: typing.Callable) -> list[str]:
    """The parameter names a client can send, from the signature the SDK reads.

    `inspect.signature` honours `__signature__`, which is exactly what the SDK
    itself reads to build the argument model, so an emitted tool reports its
    DECLARED surface rather than the `*args, **kwargs` its emitted body takes.
    """
    return [
        name
        for name, param in inspect.signature(fn).parameters.items()
        if param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]


def _body_ast(fn: typing.Callable) -> ast.FunctionDef:
    """The parsed `def` of a handwritten function.

    Dedented before parsing because every MCP tool here is a function nested
    inside `register_tools`, so its source arrives indented and `ast.parse`
    would refuse it outright.
    """
    source = textwrap.dedent(inspect.getsource(fn))
    node = ast.parse(source).body[0]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    return node


def _statements(fn_node: ast.FunctionDef) -> ast.Module:
    return ast.Module(body=list(fn_node.body), type_ignores=[])


def _read_names(fn_node: ast.FunctionDef) -> set[str]:
    """Every name the body LOADS."""
    return {
        node.id
        for node in ast.walk(_statements(fn_node))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _direct_call_arg_names(fn_node: ast.FunctionDef) -> set[str]:
    """Every name handed DIRECTLY to a call, positionally or by keyword."""
    out: set[str] = set()
    for node in ast.walk(_statements(fn_node)):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name):
                out.add(arg.id)
            elif isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                out.add(arg.value.id)
        for kw in node.keywords:
            if isinstance(kw.value, ast.Name):
                out.add(kw.value.id)
    return out


def _local_aliases(fn_node: ast.FunctionDef) -> dict[str, set[str]]:
    """local name -> the names its defining expression READS.

    One hop only, and deliberately so. It exists to forgive the single most
    common legitimate shape at an MCP boundary: a parameter RENAMED or given a
    default before being forwarded (`cap = capacity or {...}` -> `capacity=cap`,
    `effective = max_rows if max_rows is not None else limit` -> `limit=...`).
    Without it the ratchet demands a declared row for a pure rename, which is
    friction with no information in it -- and a ratchet that annoys without
    informing is one the next author turns off.

    It does NOT attempt dataflow. Two hops, a subscript store (`meta['k'] = p`)
    or a loop target still need a declared row, which is correct: at that point
    the forwarding is genuinely not visible at the boundary and saying so in one
    line is the whole point of the table.
    """
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(_statements(fn_node)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        reads = {
            child.id
            for child in ast.walk(node.value)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        aliases.setdefault(target.id, set()).update(reads)
    return aliases


def _call_arg_names(fn_node: ast.FunctionDef) -> set[str]:
    """Names that reach a call, directly or through ONE local rebinding."""
    direct = _direct_call_arg_names(fn_node)
    reached = set(direct)
    for local, sources in _local_aliases(fn_node).items():
        if local in direct:
            reached |= sources
    return reached


def _dynamic_access_used(fn_node: ast.FunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(_statements(fn_node))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _DYNAMIC_ACCESS
    }


class _Verdict(typing.NamedTuple):
    tool: str
    param: str
    read: bool
    call_arg: bool
    dynamic: frozenset[str]
    # 'handwritten' | 'emitted-manual'; 'emitted-calls' never yields a verdict,
    # because that body forwards by construction (see the module docstring).
    origin: str


def survey() -> list[_Verdict]:
    """One verdict per (tool, declared parameter) that has a body to judge."""
    verdicts: list[_Verdict] = []
    for tool_name, fn in sorted(_registered_tools().items()):
        params = _declared_params(fn)
        if _is_emitted(fn):
            kind, target = _emitted_target(fn)
            if kind == "calls":
                # Forwarded by construction; pinned once, not per tool.
                continue
            if target is None:  # pragma: no cover - refused by surfacegen itself
                raise AssertionError(f"emitted tool {tool_name!r} has neither calls nor manual")
            node, origin = _body_ast(target), "emitted-manual"
        else:
            node, origin = _body_ast(fn), "handwritten"
        read, call_args = _read_names(node), _call_arg_names(node)
        dynamic = frozenset(_dynamic_access_used(node))
        for param in params:
            verdicts.append(
                _Verdict(tool_name, param, param in read, param in call_args, dynamic, origin)
            )
    return verdicts


@pytest.fixture(scope="module")
def verdicts() -> list[_Verdict]:
    return survey()


class TestPopulationIsWhatWeThinkItIs:
    """The enumeration itself, because a gate over the wrong set is no gate."""

    def test_the_registry_holds_more_tools_than_an_ast_decorator_sweep_finds(self):
        """Pins the correction that cost this unit its first measurement.

        If `surfacegen` ever stops emitting tools this becomes an equality and
        the test should be DELETED, not relaxed -- at which point an AST sweep
        would be sound again. It is here so nobody re-derives the blind spot.
        """
        registered = _registered_tools()
        emitted = [name for name, fn in registered.items() if _is_emitted(fn)]
        assert emitted, (
            "no emitted tools found -- either surfacegen stopped being used, or "
            "`_is_emitted` stopped recognising its output. The second case "
            "would make every emitted tool silently unjudged."
        )
        assert len(registered) > len(emitted)

    def test_every_registered_tool_yields_a_body_or_is_forwarded_by_construction(self):
        """No tool may fall through the survey unjudged and unnoticed."""
        registered = _registered_tools()
        judged = {v.tool for v in survey()}
        by_construction = {
            name
            for name, fn in registered.items()
            if _is_emitted(fn) and _emitted_target(fn)[0] == "calls"
        }
        # A tool declaring NO parameters yields no verdicts and is vacuously fine.
        parameterless = {name for name, fn in registered.items() if not _declared_params(fn)}
        unaccounted = set(registered) - judged - by_construction - parameterless
        assert not unaccounted, f"tools reached by no check at all: {sorted(unaccounted)}"


class TestDeclaredParametersReachTheBody:
    """The GATE. Sound, and therefore admitting no exceptions table."""

    def test_no_body_defeats_the_read_check_dynamically(self, verdicts):
        """`locals()`/`eval` would let a body use a name it never loads.

        Refused rather than judged: the soundness argument for the gate below
        does not survive dynamic access, so a body using it would receive a
        verdict this file cannot support.
        """
        offenders = {(v.tool, tuple(sorted(v.dynamic))) for v in verdicts if v.dynamic}
        assert not offenders, (
            f"tool body/bodies using dynamic name access: {sorted(offenders)}. "
            "The READ gate cannot judge these; decide deliberately rather than "
            "letting the gate return a verdict it cannot support."
        )

    def test_every_declared_parameter_is_read_by_the_body(self, verdicts):
        """CB-157's gate, and the mutant M11 killer.

        There is NO exceptions table for this one and there must never be: a
        body that never loads a name provably cannot honour it, so a violation
        is a defect every time.
        """
        unread = [(v.tool, v.param, v.origin) for v in verdicts if not v.read]
        assert not unread, (
            "MCP parameter(s) declared to clients (and carried in "
            "tests/golden/mcp_schema.json) whose tool body never reads them, so "
            f"the tool's behaviour cannot depend on them: {sorted(unread)}. "
            "Either forward the parameter or remove it from the signature -- "
            "there is no third option and no exceptions table for this rule."
        )


class TestDeclaredParametersReachACall:
    """The RATCHET. Stronger, and the one that earns a self-deleting table."""

    def test_every_parameter_reaches_a_call_or_is_declared_assembled(self, verdicts):
        undeclared = [
            (v.tool, v.param)
            for v in verdicts
            if not v.call_arg and (v.tool, v.param) not in ASSEMBLED_BY_THE_WRAPPER
        ]
        assert not undeclared, (
            f"MCP parameter(s) never handed to any call: {sorted(undeclared)}. "
            "If the wrapper legitimately ASSEMBLES the value into another "
            "argument, add a row to ASSEMBLED_BY_THE_WRAPPER in this file "
            "naming what it is assembled into. If it does not, this is CB-157."
        )

    def test_every_declared_exception_carries_a_non_empty_reason(self):
        empty = [key for key, reason in ASSEMBLED_BY_THE_WRAPPER.items() if not reason.strip()]
        assert not empty, (
            f"ASSEMBLED_BY_THE_WRAPPER row(s) with no reason: {empty} -- a "
            "table whose rows need no justification is an amnesty, not a set "
            "of exceptions."
        )

    def test_every_declared_exception_still_describes_the_tree(self, verdicts):
        """SELF-DELETING, on all four axes at once.

        A row is stale -- and refused -- when the tool is gone, when the tool no
        longer declares the parameter, when the body stopped reading it (which
        makes it a GATE violation, not an exception), or when it now DOES reach
        a call (which makes the row simply obsolete). The table may only shrink.
        """
        by_key = {(v.tool, v.param): v for v in verdicts}
        stale: list[tuple[tuple[str, str], str]] = []
        for key in ASSEMBLED_BY_THE_WRAPPER:
            verdict = by_key.get(key)
            if verdict is None:
                stale.append((key, "no such tool, or the tool no longer declares it"))
            elif not verdict.read:
                stale.append((key, "body no longer READS it -- that is a gate violation"))
            elif verdict.call_arg:
                stale.append((key, "now reaches a call directly -- delete this row"))
        assert not stale, (
            f"stale ASSEMBLED_BY_THE_WRAPPER row(s): {stale}. This table may "
            "only ever shrink; remove the row rather than adjusting its reason."
        )


class TestTheEmittedBodyIsForwardedByConstruction:
    """`surfacegen`'s single shared body is what excuses 10 tools from the survey.

    That excuse is a claim about ONE function, so it is pinned here rather than
    trusted. Without this, deleting the `**bound.arguments` splat would silently
    stop forwarding every emitted tool's parameters while this file reported a
    clean run -- the "gate that cannot fire" shape, aimed at the tools whose
    bodies nobody reads because nobody wrote them.
    """

    def test_the_emitted_body_forwards_by_construction(self):
        node = _body_ast(surfacegen.build_tool)
        inner = [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.FunctionDef) and child.name == "tool"
        ]
        assert len(inner) == 1, "surfacegen.build_tool no longer defines exactly one `tool` body"
        # Every value the emitted body RETURNS is a call into the declared
        # target, and each must splat `**bound.arguments` -- a keyword with
        # arg=None whose value is the attribute `arguments` on the name `bound`.
        # Anchored on the RETURN statements and not on every splat in the body,
        # because `signature.bind(*args, **kwargs)` legitimately splats a plain
        # name and asserting over all splats fails on the emitter's own binding
        # step rather than on what reaches the target.
        returns = [
            node
            for node in ast.walk(_statements(inner[0]))
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
        ]
        assert returns, "the emitted body no longer returns a call into its target"
        for node in returns:
            splats = [kw for kw in node.value.keywords if kw.arg is None]
            assert len(splats) == 1 and (
                isinstance(splats[0].value, ast.Attribute)
                and splats[0].value.attr == "arguments"
                and isinstance(splats[0].value.value, ast.Name)
                and splats[0].value.value.id == "bound"
            ), (
                "an emitted body's return no longer forwards exactly "
                "`**bound.arguments`, so emitted tools can no longer be excused "
                "from the per-parameter survey by construction. Either restore "
                "the splat or make the survey judge emitted bodies individually."
            )

    def test_the_emitted_body_applies_declared_defaults_before_forwarding(self):
        """`bind` alone leaves an omitted optional MISSING rather than defaulted.

        Without `apply_defaults()` the splat forwards only what the caller sent,
        so a declared parameter the caller omitted would not reach the domain at
        all -- CB-157's defect arriving through the emitter instead of a body.
        """
        source = textwrap.dedent(inspect.getsource(surfacegen.build_tool))
        assert "apply_defaults()" in source
