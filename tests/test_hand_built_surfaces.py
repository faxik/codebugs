"""The suite's own guard against judging a hand-built MCP surface (CB-311).

`tests/conftest.py` refuses a `call_tool` into a tool that was registered on a
bare `MCPServer` instead of through `server.build_registrar`. This file is what
keeps that guard from being indistinguishable from no guard at all: it fires the
refusal on purpose, confirms the production route is NOT refused, and holds the
allowance table to the CB-179 discipline.

WHY THE ALLOWANCE TABLE IS EMPTY, AND WHY THAT IS A MEASUREMENT. Three places in
this suite build a bare server deliberately — `test_boundary.py` inspects
un-normalized descriptions, `test_findings.py` enumerates tool names,
`test_add_lines_surface.py` uses a stand-in that is not an `MCPServer` at all —
and every one of them stops short of calling a tool. The guard keys on the CALL,
which is the act that claims "this is the boundary a client meets", so all three
stay legal without a row. Rows would have had to be maintained; a predicate that
does not reach them does not.
"""

from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools.base import Tool

from codebugs import findings, server
from tests import conftest


def _live_sites() -> set[str]:
    """Every `<file>::<function>` the test tree currently contains.

    The staleness half compares a ROW against this, rather than taking the row
    apart and checking the pieces: a check built on the pieces cannot be seen to
    hold the KEY against the live tree — by `tests/test_exception_table_discipline.py`,
    which measured exactly that and refused this file's first draft — and, more to
    the point, a row whose file and function both exist but never sat together
    would have passed it.
    """
    sites: set[str] = set()
    for path in sorted(Path(conftest._TESTS_DIR).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                sites.add(f"{path.name}::{node.name}")
    return sites


def _factory():
    @contextmanager
    def factory():
        raise AssertionError(
            "the guard must refuse before the tool body runs, so no connection is ever needed"
        )
        yield  # pragma: no cover - unreachable, keeps this a context manager

    return factory


def _why_the_call_failed(raised: BaseException) -> str:
    """Every reason text behind `raised`, the CHAINED ones included (CB-318).

    Deliberately not `str(raised)`. When a tool BODY crashes, the SDK decides
    how much of that crash the caller may read, and the two admitted versions
    decide differently: `mcp` 2.0.0 appends the body's text to its `ToolError`
    message, while 2.1.1 raises an `UnexpectedToolError` whose message is the
    bare `Error executing tool <name>`. The second is a deliberate design
    choice, not an accident — `Tool.run`'s own docstring there says a crash
    keeps its text off the message "so nothing from an unexpected exception
    reaches the client". Measured on both versions as well as read.

    So the POSITIVE half of each pair below, taken off `str(raised)`, answers a
    question about the installed SDK rather than about this suite's guard:
    under 2.1.1 the body's marker is absent from that string however healthy
    the guard is, so the assertion fails on a correct tree and the test is red
    whatever the guard does. A test that is red either way discriminates
    nothing — the defect these tests exist to forbid, rebuilt inside them.

    THE NEGATIVE HALF WAS NEVER BROKEN, and recording that keeps the SDK's
    boundary in the right place for the next reader. A guard refusal never
    passes through the SDK at all (see the paragraph after next), so its text
    carries the `CB-311` marker on both versions — measured, and visible in
    this file: the refusal tests assert that marker's PRESENCE off `str` and
    are green under 2.1.1 today. Reading that half off the chain as well is a
    small strengthening — a larger haystack for an absence test — not a repair.

    What both versions preserve is the CHAIN, and 2.1.1 states it as a
    contract rather than leaving it incidental: every failure it wraps is
    raised `from` the original, so the body's own `AssertionError` hangs off
    `__cause__`. `__context__` is walked too, covering a wrapper that some day
    omits the explicit `from` — the chain is implicit then, and without that
    branch this file would go red on a perfectly healthy guard, CB-318 rebuilt
    inside its own fix.

    THE SECOND COPY OF THIS IDIOM IS NAMED RATHER THAN HIDDEN.
    `tests/test_refusal_classification.py::_causes` walks the same two links,
    bounding itself by depth where this bounds itself by identity. It was not
    importable here and merging them would be wrong: it returns exception
    OBJECTS because its question is a link's CLASS, while this one needs the
    TEXT. Same traversal, two questions.

    The GUARD's refusal needs no chain at all: it is raised in
    `conftest._call_tool_refusing_a_hand_built_surface` BEFORE the SDK's tool
    invocation is reached, so it arrives as itself, marker intact, on both
    versions — which is why every test here that needs only a refusal never
    noticed any of this, and why only the three that need the BODY's text did.

    ONE BOUNDARY, because it decides what may be asserted with this. The
    question answered here is "did the call reach the tool body", NOT "could a
    client read the reason" — and under 2.1.1 the honest answer to the second
    is no, by the SDK's design. That second question belongs to
    `tests/test_cb310_refusal_text.py`, which owns the client boundary.
    Conflating the two is what made these tests depend on the SDK's
    presentation in the first place.
    """
    texts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = raised
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        texts.append(str(current))
        current = current.__cause__ or current.__context__
    return "\n".join(texts)


class TestTheGuardFires:
    def test_a_hand_registered_tool_is_refused_at_the_call(self):
        """The mutant this file exists to be: a surface assembled by hand."""
        mcp = MCPServer("cb311-hand-built")
        findings.register_tools(mcp, _factory())
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("get", {"finding_id": "CB-1"}))
        assert "CB-311" in str(refusal.value)
        assert "registered by hand" in str(refusal.value)
        assert "build_registrar" in str(refusal.value)

    def test_the_refusal_names_the_place_and_the_tool(self):
        mcp = MCPServer("cb311-hand-built-named")
        findings.register_tools(mcp, _factory())
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("query", {}))
        message = str(refusal.value)
        assert "'query'" in message
        assert "test_hand_built_surfaces.py::test_the_refusal_names_the_place_and_the_tool" in (
            message
        ), message

    def test_a_tool_registered_under_an_EXPLICIT_name_is_caught_too(self):
        """The name is reconstructed the way the SDK reconstructs it, and that
        reconstruction is a SECOND COPY of the SDK's rule — the guard's one
        silent failure direction. Both registration shapes must therefore be
        exercised: every other test here registers through `register_tools`,
        which uses `@mcp.tool()` with no name at all, so a reconstruction that
        handled only that shape would look fully tested."""
        mcp = MCPServer("cb311-explicit-name")

        def probe() -> dict:
            """A hand-registered tool named explicitly."""

        mcp.tool(name="cb311_named_probe")(probe)
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("cb311_named_probe", {}))
        assert "CB-311" in str(refusal.value)
        assert "'cb311_named_probe'" in str(refusal.value)

    def test_a_tool_added_through_the_public_add_tool_is_caught(self):
        """GAP ONE, found by cross-model review and reproduced before fixing.

        `add_tool` is a PUBLIC method that registers without going through the
        decorator at all. The first version of this guard wrapped the decorator,
        so a test taking this route left no record and its call sailed through —
        the exact scenario the guard exists to forbid, reached by a documented
        API rather than by anything exotic.
        """
        mcp = MCPServer("cb311-add-tool")

        def probe() -> dict:
            """Registered through the public add_tool."""

        mcp.add_tool(probe, name="cb311_added_probe")
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("cb311_added_probe", {}))
        assert "CB-311" in str(refusal.value)

    def test_half_the_production_build_is_caught(self):
        """GAP TWO: `build_registrar` composes TWO adapters.

        Wrapping only the outer one gives bodies that carry the production
        decorator — so the per-tool half of the check is satisfied — while
        description normalization, the other half of what ships, is absent. The
        server half is what catches it, which is why the guard needs both.
        """
        mcp = MCPServer("cb311-half-build")
        half = server._RefusalsReachTheClient(mcp)
        findings.register_tools(half, _factory())
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("query", {}))
        assert "CB-311" in str(refusal.value)

    def test_one_hand_added_tool_on_an_OTHERWISE_correct_server_is_caught(self):
        """What the PER-TOOL half is for, and it needed its own case.

        The two gap tests above are both caught by the SERVER half, because both
        build a bare server — so removing the per-tool half entirely would leave
        them green and the half would be untested. This is the shape only the
        per-tool half sees: a server assembled correctly, carrying correctly
        registered tools, with ONE further tool added by hand afterwards. The
        correct tools must still work; the hand-added one must be refused.
        """
        mcp = MCPServer("cb311-mixed")
        findings.register_tools(server.build_registrar(mcp), _factory())

        def smuggled() -> dict:
            """Added by hand onto an otherwise correct server."""

        mcp.add_tool(smuggled, name="cb311_smuggled")

        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("cb311_smuggled", {}))
        assert "CB-311" in str(refusal.value)

        # DISCRIMINATING, not merely "some exception". Asserting only that the
        # marker is absent would be satisfied by any unrelated failure raised
        # before the body — including one caused by the guard itself in some
        # future edit. The connection factory's own message proves the call
        # reached the tool body, which is the thing being claimed.
        #
        # The chain, not `str(raised.value)`: that string measures the SDK
        # rather than the guard, for the reason `_why_the_call_failed` sets
        # out (CB-318).
        with pytest.raises(BaseException) as raised:
            asyncio.run(mcp.call_tool("query", {}))
        reason = _why_the_call_failed(raised.value)
        assert "CB-311" not in reason, f"the correctly built tools must still work: {reason!r}"
        assert "no connection is ever needed" in reason, (
            "the call must reach the tool body, not fail somewhere before it -- an SDK that "
            "stopped chaining the body's exception would land here too, and is the one other "
            "thing this can mean"
        )

    def test_an_unreadable_registry_REFUSES_rather_than_admitting(self):
        """Fail-closed on blindness, which the first version got backwards.

        The guard reads the SDK's registry to see which body a call will run.
        When that read fails it cannot tell a production surface from a hand
        one — and the earlier code turned exactly that into "no such tool" and
        let the call through with nothing checked. Every gate in this repository
        refuses when it cannot tell; this one now does too.
        """
        mcp = MCPServer("cb311-blind")
        findings.register_tools(server.build_registrar(mcp), _factory())
        mcp._tool_manager._tools = None  # what an SDK rename looks like from here
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("query", {}))
        assert "CB-311" in str(refusal.value)
        assert "could not determine" in str(refusal.value)

    def test_a_marked_server_carrying_a_hand_body_is_still_refused(self):
        """The COMPOSITION neither half covers alone.

        The other cases pin "mark present, body hand-built" and "no mark, body
        production" separately. This one is the state a bypass actually reaches:
        the server carries the production mark AND the stored body is a hand
        one. Both halves must be consulted for this to be refused — reading
        either alone admits it.
        """
        mcp = MCPServer("cb311-marked-but-hand")
        server.build_registrar(mcp)  # marks the server, registers nothing
        findings.register_tools(mcp, _factory())  # hand registration onto it
        assert mcp in conftest._FULLY_BUILT, "premise: the server carries the mark"
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("query", {}))
        assert "CB-311" in str(refusal.value)

    def test_re_registering_correctly_does_NOT_launder_a_hand_built_surface(self):
        """The shape a previous round of this guard got WRONG, kept as a pin.

        Registering by hand and then "correctly" under the same names LOOKS like
        a repair and is not one: `ToolManager.add_tool` does not replace a tool
        whose name it already holds — it logs and returns the incumbent — so the
        server still runs the hand-built bodies. A guard that cleared its record
        on the second registration therefore turned itself off, and the call
        went into the hand body with nothing said. Measured on this tree: zero
        of the ten tools carried a production body afterwards.

        The refusal must survive the false repair, which it does now only
        because the verdict reads the stored body rather than a record.
        """
        mcp = MCPServer("cb311-relaunder")
        findings.register_tools(mcp, _factory())
        findings.register_tools(server.build_registrar(mcp), _factory())

        stored = mcp._tool_manager._tools["query"].fn
        assert stored.__code__ not in conftest._PRODUCTION_WRAPPER_CODES, (
            "premise of this test: the SDK kept the hand-registered body"
        )
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("query", {}))
        assert "CB-311" in str(refusal.value)

    def test_a_tool_smuggled_in_through_the_CONSTRUCTOR_is_caught(self):
        """`MCPServer(tools=[…])` fills the registry without calling `add_tool`.

        The constructor hands its list straight to the tool manager, so no
        registration hook of any kind observes it. The guard catches it anyway,
        because the verdict is taken from the stored body at call time rather
        than from what registration recorded — which is the whole reason that
        design was chosen over bookkeeping.
        """

        def smuggled() -> dict:
            """Placed in the registry by the constructor."""

        mcp = MCPServer("cb311-ctor", tools=[Tool.from_function(smuggled, name="cb311_ctor")])
        server.build_registrar(mcp)
        with pytest.raises(AssertionError) as refusal:
            asyncio.run(mcp.call_tool("cb311_ctor", {}))
        assert "CB-311" in str(refusal.value)

    def test_a_production_built_surface_is_not_refused(self):
        """The other half: a guard that refused everything would also 'fire'.

        The tool body here is reached — the connection factory raises, and that
        raise arriving is the proof the guard let the call through rather than
        stopping it. Which raise arrived is read off the chain rather than off
        the outermost exception's text, for the reason `_why_the_call_failed`
        sets out (CB-318).
        """
        mcp = MCPServer("cb311-production")
        findings.register_tools(server.build_registrar(mcp), _factory())
        with pytest.raises(BaseException) as raised:
            asyncio.run(mcp.call_tool("query", {}))
        reason = _why_the_call_failed(raised.value)
        assert "CB-311" not in reason, f"a production surface was refused: {reason!r}"
        assert "no connection is ever needed" in reason, "the call never reached the tool body"


class TestTheAllowanceTableIsDisciplined:
    def test_every_row_carries_a_reason(self):
        empty = [
            site
            for site, reason in conftest._HAND_BUILT_SURFACES_ALLOWED.items()
            if not reason.strip()
        ]
        assert not empty, (
            f"allowance row(s) with no reason: {empty} -- a table whose rows can lose their "
            "justification becomes the place hand-built surfaces are parked silently."
        )

    def test_no_row_names_a_place_that_no_longer_exists(self):
        live = _live_sites()
        stale = [site for site in conftest._HAND_BUILT_SURFACES_ALLOWED if site not in live]
        assert not stale, (
            f"stale allowance row(s): {stale} -- the place they license is gone, so the row "
            "now licenses nothing and hides the next one that appears under the same name."
        )

    def test_a_row_actually_licenses_the_place_it_names(self):
        """The table is READ by the guard, not merely declared beside it.

        With the table empty this cannot be shown from the live rows, so it is
        shown on the mechanism: the refusal is keyed by the same site string the
        table is keyed by, so adding that string licenses exactly that place.

        The surface here IS hand-built, so without the row the guard refuses;
        the pair below says the row turned that refusal off and let the call
        through to the body. Both halves read the chain rather than the
        outermost exception's text (CB-318, see `_why_the_call_failed`).
        """
        site = "test_hand_built_surfaces.py::test_a_row_actually_licenses_the_place_it_names"
        mcp = MCPServer("cb311-licensed")
        findings.register_tools(mcp, _factory())
        conftest._HAND_BUILT_SURFACES_ALLOWED[site] = "temporary row, this test only"
        try:
            with pytest.raises(BaseException) as raised:
                asyncio.run(mcp.call_tool("query", {}))
            reason = _why_the_call_failed(raised.value)
            assert "CB-311" not in reason, f"the row licensed nothing: {reason!r}"
            assert "no connection is ever needed" in reason, (
                "the licensed call never reached the tool body"
            )
        finally:
            del conftest._HAND_BUILT_SURFACES_ALLOWED[site]
