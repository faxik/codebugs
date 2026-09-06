"""CB-310: an expected refusal's TEXT must reach the MCP CLIENT on every admitted SDK version.

WHAT THIS FILE IS FOR, AND WHY IT IS NOT THE PRIMARY ORACLE. Ten tests already
in this suite assert that a refusal's reason survives to the MCP surface, and
under `mcp` 2.1.1 all ten went red before this unit's fix (measured on a still
tree: 10 failed, 3368 passed). They are the primary oracle and are deliberately
NOT restated here. What they cannot reach is the CLIENT: every one of them calls
`MCPServer.call_tool`, an in-process entry point that RAISES, while a real
client speaks JSON-RPC and READS a result carrying text. Two paths, two shapes,
and the promise CB-310 is about is made to the second one.

THE HEADLINE CASE IS COVERED BY NONE OF THE TEN. "No tracker in this directory"
is the refusal whose loss cost an owner a server reconnection: the client saw
`Error executing tool <name>`, diagnosed a dead server, and reconnected a server
that was healthy and merely pointed at a directory with no tracker. It is
`db.DatabaseNotFoundError`, a `RuntimeError` subclass, and it is exercised here
through the product's own discovery walk rather than through a stand-in.

WHAT THE WIRE CANNOT DISCRIMINATE, SAID PLAINLY SO NOBODY LOOKS FOR IT HERE.
The two-sided half of the gate — an UNEXPECTED exception must keep its text off
the wire — is not assertable over the wire on both versions, because `mcp`
2.0.0's SDK appends every exception's text to the client's message no matter
what this package does. There is no code in this package that changes that. So
the two-sidedness is pinned where it is actually decided: on the CLASS of the
exception leaving the tool body, which is version-independent and is this
package's own decision. Asserting absence of a marker over the wire would pass
on 2.1.1, fail on 2.0.0, and would be measuring the SDK rather than the fix.

AND THE SAME BOUNDARY CUTS THE OTHER WAY, WHICH IS THE MORE IMPORTANT HALF TO
SAY OUT LOUD. The four wire tests below cannot fail under `mcp` 2.0.0 either:
delete a class from `server._EXPECTED_REFUSALS` and the SDK of that version
still appends the text, so all four stay green. Under the LOCKED version they
are therefore a record of the client-facing promise rather than a gate on it —
which is CB-310's own subject reappearing inside CB-310's tests. Two things
answer it, and both are needed: `TestTheClassificationIsTranslatedAtTheWrapper`
asks the version-independent question (what CLASS leaves the body), and the
`newest-sdk` job in `.github/workflows/ci.yml` runs this whole suite on the
newest `mcp` the dependency range admits, which is where these four bite.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib
import re

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.memory import create_client_server_memory_streams

from codebugs import db, server, usage


@pytest.fixture
def tracker(tmp_path):
    """A real tracker on disk, plus the connection factory a built server takes."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = str(project_dir)
    db.init_project(project)

    @contextlib.contextmanager
    def _conn():
        conn = db.connect(project)
        try:
            yield conn
        finally:
            conn.close()

    return project, _conn


def call_over_the_wire(built: MCPServer, name: str, arguments: dict) -> tuple[bool, str]:
    """Drive `built` through a REAL client session and return `(is_error, text)`.

    Not `MCPServer.call_tool`: that is the in-process entry the ten existing
    tests use, and this unit's whole subject is what a CLIENT ends up holding.
    The server side is served through `_lowlevel_server`, which is exactly what
    `MCPServer.run_stdio_async` does — the public class exposes no way to serve
    an arbitrary stream pair, and speaking over real stdio instead would add a
    process boundary without adding a single assertion this cannot make.
    """

    async def go() -> tuple[bool, str]:
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            client_read, client_write = client_streams
            server_read, server_write = server_streams
            options = built._lowlevel_server.create_initialization_options()
            async with anyio.create_task_group() as task_group:

                async def serve() -> None:
                    await built._lowlevel_server.run(server_read, server_write, options)

                task_group.start_soon(serve)
                async with ClientSession(client_read, client_write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
            return bool(result.is_error), (result.content[0].text if result.content else "")

    return asyncio.run(go())


class TestTheClientReceivesTheReason:
    """Every class in the accepted classification, over the real client boundary.

    The classification is `server._EXPECTED_REFUSALS`, and it is not authored
    there: `ValueError`/`KeyError` come from `cli.domain_errors`' definition of
    bad input, and the two typed `RuntimeError` subclasses come from `db.py`,
    where they exist precisely to carry a text addressed to a person.
    """

    def test_an_unknown_vocabulary_value_names_itself(self, tracker):
        """`ValueError` — the refusal a client hits by typing a wrong status."""
        _, factory = tracker
        built = server._build_server("findings", factory)
        is_error, text = call_over_the_wire(
            built, "update", {"finding_id": "CB-1", "status": "bogus"}
        )
        assert is_error is True
        assert "bogus" in text, text
        assert "Invalid finding status" in text, text

    def test_a_missing_card_names_the_id(self, tracker):
        """`KeyError` — the refusal a client hits by asking for a card that is gone."""
        _, factory = tracker
        built = server._build_server("findings", factory)
        is_error, text = call_over_the_wire(built, "get", {"finding_id": "CB-999999"})
        assert is_error is True
        assert "CB-999999" in text, text
        assert "not found" in text.lower(), text

    def test_no_tracker_in_this_directory_reaches_the_client(self, tmp_path, monkeypatch):
        """`db.DatabaseNotFoundError` — THE case CB-310 was filed for.

        Reached through the product's own resolution rather than through a
        stand-in factory: the server is built with `server._conn`, the module's
        real connection factory, and the tracker root is declared to point at a
        directory holding no tracker. That is the state the owner was in when
        the bare `Error executing tool get` made him diagnose a dead server.

        `db.set_tracker_root` is process-global; `tests/conftest.py`'s autouse
        ambient-state fixture resets the override before every test, which is
        what makes setting it here safe rather than a leak into the next file.
        """
        empty = tmp_path / "no-tracker-here"
        empty.mkdir()
        monkeypatch.setattr(db, "_tracker_root_override", str(empty))

        built = server._build_server("findings")
        is_error, text = call_over_the_wire(built, "get", {"finding_id": "CB-1"})
        assert is_error is True
        assert str(empty) in text, text
        # The reason, not merely the path: a client must be able to tell "there
        # is no tracker where I am pointed" from "the server is dead".
        assert "no" in text.lower() and "codebugs" in text.lower(), text

    def test_an_unwritable_tracker_reaches_the_client(self, tracker):
        """`db.TrackerUnwritableError` — the sibling refusal, raised from `db._open`.

        The server and the tool are real; only the CONNECTION fails, which is
        the honest reproduction of this refusal's own scope (CB-86 ratifies that
        it is raised from `_open` and from nowhere else). Reproducing a
        read-only database on disk would exercise `db._open`'s classifier, which
        `tests/test_db_unwritable.py` already owns; what is under test here is
        whether the class reaches a client with its text.
        """

        @contextlib.contextmanager
        def refusing_factory():
            raise db.TrackerUnwritableError(
                "codebugs: the tracker at /somewhere/.codebugs is not writable"
            )
            yield  # pragma: no cover - unreachable, keeps the generator shape

        built = server._build_server("findings", refusing_factory)
        is_error, text = call_over_the_wire(built, "get", {"finding_id": "CB-1"})
        assert is_error is True
        assert "not writable" in text, text


class TestTheClassificationIsTranslatedAtTheWrapper:
    """Every accepted class becomes a `ToolError` carrying its own text.

    THIS IS THE HALF THAT DISCRIMINATES ON BOTH SDK VERSIONS, and it exists
    because the wire tests above do NOT. Under `mcp` 2.0.0 the SDK appends every
    exception's text to the client's message, so removing a class from
    `server._EXPECTED_REFUSALS` leaves all four wire tests GREEN there — they
    only go red under 2.1.1. Measured, not feared: that is precisely the state
    CB-310 describes, a gate that cannot fire because nobody runs it on the
    version the defect lives on, and the CI job `newest-sdk` is the other half
    of the answer. This class closes the locked version's side of it by asking
    the question the SDK cannot smother: what CLASS does the body raise.
    """

    @pytest.mark.parametrize(
        "raised, marker",
        [
            (ValueError("VALUE-MARKER"), "VALUE-MARKER"),
            (KeyError("KEY-MARKER"), "KEY-MARKER"),
            (db.DatabaseNotFoundError("no .codebugs/ found in /tmp/NOTRACKER"), "NOTRACKER"),
            (db.TrackerUnwritableError("tracker at /tmp/UNWRITABLE is not writable"), "UNWRITABLE"),
        ],
        ids=["ValueError", "KeyError", "DatabaseNotFoundError", "TrackerUnwritableError"],
    )
    def test_the_class_is_translated_and_keeps_its_text(self, raised, marker):
        def body():
            raise raised

        wrapped = server._refusal_reaches_the_client(body)
        with pytest.raises(ToolError) as caught:
            wrapped()
        assert marker in str(caught.value), str(caught.value)


class TestTheGateIsTwoSided:
    """What is NOT an anticipated refusal stays a crash, and the order says which.

    Pinned at the wrapper rather than over the wire, for the reason this file's
    module docstring gives: on `mcp` 2.0.0 the SDK puts every exception's text
    in front of the client regardless, so the wire cannot discriminate. What
    this package decides — and the only thing it decides — is the CLASS of the
    exception that leaves the tool body.
    """

    def test_an_unexpected_exception_is_not_reclassified(self):
        """A plain `RuntimeError` is a crash and must leave as one.

        `db.DatabaseNotFoundError` and `db.TrackerUnwritableError` ARE
        `RuntimeError` subclasses, so a wrapper that caught `RuntimeError`
        wholesale would pass every test in the class above while putting
        arbitrary internal text — another caller's data included — in front of
        a client. This is the assertion that tells the two apart.
        """

        def body():
            raise RuntimeError("INTERNAL-MARKER")

        wrapped = server._refusal_reaches_the_client(body)
        with pytest.raises(RuntimeError) as caught:
            wrapped()
        assert not isinstance(caught.value, ToolError)
        assert str(caught.value) == "INTERNAL-MARKER"

    def test_a_post_commit_serialization_failure_is_not_a_refusal(self):
        """`json.JSONDecodeError` IS a `ValueError`, and the arms' ORDER decides it.

        It means the write already landed and only the serialization of the
        return value then failed (CB-16/CB-86). Presenting that to a client as
        an anticipated refusal reports "bad input" for a mutation that already
        committed — the same lie `cli.domain_errors` refuses at the CLI
        boundary, which is why this wrapper repeats that boundary's arm order
        rather than inventing its own.

        The pairing is what makes this test discriminate the ORDER instead of
        merely repeating it: the same call shape with a plain `ValueError`
        must come back as a `ToolError`. Swap the two arms and this half stays
        green while the half below it goes red.
        """

        def decode_failure():
            raise json.JSONDecodeError("POST-COMMIT-MARKER", "{}", 0)

        wrapped = server._refusal_reaches_the_client(decode_failure)
        with pytest.raises(json.JSONDecodeError) as caught:
            wrapped()
        assert not isinstance(caught.value, ToolError)

        def plain_value_error():
            raise ValueError("INPUT-MARKER")

        wrapped_input = server._refusal_reaches_the_client(plain_value_error)
        with pytest.raises(ToolError) as translated:
            wrapped_input()
        assert "INPUT-MARKER" in str(translated.value)

    def test_an_async_tool_body_is_translated_too(self):
        """The coroutine branch, which no tool in this package exercises today.

        It is here rather than deferred because the failure it forecloses is
        SILENT: a single synchronous wrapper over a coroutine function returns
        the coroutine object without awaiting it, the `except` arms never run,
        and the wrapper protects nothing while looking exactly like a wrapper
        that does. An untested branch guarding an invisible failure is worse
        than no branch, so the branch and this test arrive together.
        """

        async def body():
            raise ValueError("ASYNC-MARKER")

        wrapped = server._refusal_reaches_the_client(body)
        with pytest.raises(ToolError) as caught:
            asyncio.run(wrapped())
        assert "ASYNC-MARKER" in str(caught.value)

    def test_a_successful_call_is_untouched(self):
        """The wrapper is transparent when nothing is raised — the boring half, stated."""

        def body(a, b=2):
            """Doc."""
            return {"sum": a + b}

        wrapped = server._refusal_reaches_the_client(body)
        assert wrapped(1) == {"sum": 3}
        assert wrapped.__doc__ == "Doc."
        assert wrapped.__name__ == "body"


class TestEveryRegisteredToolIsTranslated:
    """The whole surface, by NAME — never by count and never by reading the source.

    Thirteen of the tools are emitted by `surfacegen` rather than written out,
    and they are the likeliest place for a registration-time wrapper to miss:
    they arrive through `emit_tools`, not through a `@mcp.tool()` decorator in a
    domain module. A count would be satisfied by wrapping thirteen of the wrong
    ones, so the assertion compares SETS.
    """

    @staticmethod
    def _record_registrations(monkeypatch):
        """Watch BOTH seams and return what each saw.

        Two observations are needed and neither substitutes for the other. The
        REGISTRAR seam gives the name the client will see — which is not
        `fn.__name__`: the milestones module registers ten tools as
        `@mcp.tool(name="milestone_defer")` over a body called
        `_milestone_defer`, so a name comparison built from the function object
        alone reports a phantom mismatch on a healthy tree. The TRANSLATOR seam
        gives the function objects that actually went through the wrapper.
        Comparing the two sets of objects is what turns "everything passed
        through the adapter class" into "everything was translated".
        """
        names: list[str] = []
        offered: list[object] = []
        translated: list[object] = []
        real_tool = server._RefusalsReachTheClient.tool
        real_translate = server._refusal_reaches_the_client

        def spy_tool(self, *args, **kwargs):
            explicit = kwargs.get("name")
            inner = real_tool(self, *args, **kwargs)

            def register(fn):
                names.append(explicit or fn.__name__)
                offered.append(fn)
                return inner(fn)

            return register

        def spy_translate(fn):
            translated.append(fn)
            return real_translate(fn)

        monkeypatch.setattr(server._RefusalsReachTheClient, "tool", spy_tool)
        monkeypatch.setattr(server, "_refusal_reaches_the_client", spy_translate)
        return names, offered, translated

    def test_the_registered_names_are_exactly_the_translated_ones(self, tracker, monkeypatch):
        _, factory = tracker
        names, offered, translated = self._record_registrations(monkeypatch)
        built = server._build_server("all", factory)
        registered = {tool.name for tool in asyncio.run(built.list_tools())}

        assert set(names) == registered, sorted(set(names) ^ registered)
        assert len(names) == len(registered), f"{len(names)} registered via the adapter"
        # Object identity, not names: this is the half that says the bodies were
        # translated rather than merely routed past the class that translates.
        assert [id(fn) for fn in offered] == [id(fn) for fn in translated]

    def test_the_generated_surface_is_among_them(self, tracker, monkeypatch):
        """Named separately, because the set equality above would still hold if
        `surfacegen` stopped emitting anything at all — an empty contribution
        equals an empty contribution. This asserts the contribution is there."""
        from codebugs import sweep_surface

        _, factory = tracker
        names, _, _ = self._record_registrations(monkeypatch)
        server._build_server("all", factory)

        generated = {
            facet["mcp"]["name"] for facet in sweep_surface.SURFACE if facet.get("mcp")
        }
        assert generated, "the generated surface declares no tools — premise gone"
        assert generated <= set(names), sorted(generated - set(names))


class TestTheNewestSdkJobExists:
    """The other half of the fix, and the half that had been missing all along.

    The translation makes a refusal survive; this job is what makes anyone
    NOTICE when it stops surviving. Without it the ten existing tests go on
    being green on the locked version and red on the version users install, and
    nobody sees the second half — which is the state CB-310 was filed from.

    COMMENTS DO NOT COUNT, and here that is not a formality: this job's own
    comment block contains the literal string `mcp==2.1.1` while explaining why
    a pinned version is the wrong answer. A test that grepped the raw file would
    read that comment as the constant it forbids and go red on a healthy tree —
    or, spelled the other way round, would go green over a job that really had
    been pinned. Stripping is whole-line only, matching
    `tests/test_worktree_harness.py`'s own convention.
    """

    WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

    @staticmethod
    def _code(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))

    def _job(self) -> str:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        _, _, body = text.partition("\njobs:")
        assert body, "ci.yml declares no `jobs:` block"
        bounds = list(re.finditer(r"^  ([A-Za-z_][\w-]*):[ \t]*(#.*)?$", body, re.M))
        for i, match in enumerate(bounds):
            if match.group(1) == "newest-sdk":
                end = bounds[i + 1].start() if i + 1 < len(bounds) else len(body)
                return self._code(body[match.end() : end])
        raise AssertionError(
            f"ci.yml declares no `newest-sdk` job; found {[b.group(1) for b in bounds]}"
        )

    def test_it_resolves_the_newest_version_rather_than_naming_one(self):
        job = self._job()
        assert "uv lock --upgrade-package mcp" in job, job
        # The assertion that keeps this job honest as the SDK releases again: a
        # version constant here would silently turn it back into a job that
        # tests a version nobody runs.
        assert "mcp==" not in job, job
        assert "2.1.1" not in job, job

    def test_it_runs_the_whole_suite_not_a_chosen_few(self):
        job = self._job()
        assert "python -m pytest tests/ -q" in job, job
        # `fetch-depth: 0` for the same reason the `tests` job needs it: one test
        # in this suite reads the repository's real history (CB-139).
        assert "fetch-depth: 0" in job, job


class TestUsageTrackingIsUnchanged:
    """The translation must not change what `codebugs usage` records (CB-310 §4.5).

    `install_usage_tracking` recognises a tool's own failure by the SHAPE of the
    returned result, not by an exception, because `_handle_call_tool` catches
    the body's exception before any middleware sees it. A `ToolError` is caught
    by that same handler and returned as the same shape, so the recorded row is
    identical — measured before and after the fix, both versions, and pinned
    here so a future change to the wrapper cannot quietly turn a counted failure
    into an uncounted one.
    """

    def test_a_translated_refusal_is_still_recorded_as_a_failed_call(self, tracker):
        project, factory = tracker
        built = server._build_server("findings", factory)
        is_error, _ = call_over_the_wire(built, "get", {"finding_id": "CB-999999"})
        assert is_error is True

        conn = db.connect(project)
        try:
            usage.ensure_schema(conn)
            rows = conn.execute(
                "SELECT tool_name, success, error_type FROM tool_calls WHERE tool_name = 'get'"
            ).fetchall()
        finally:
            conn.close()
        assert [tuple(row) for row in rows] == [("get", 0, "ToolError")], rows
