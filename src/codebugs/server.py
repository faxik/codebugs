"""Codebugs MCP server — AI-native code finding tracker."""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, CallToolResult

from codebugs import db, usage


def dedent_docstring(doc: str) -> str:
    """Strip the common source indentation from a docstring, as CPython 3.13 does.

    CPython 3.13 dedents docstrings at compile time; 3.11 and 3.12 leave the
    source indentation in `__doc__`, and the mcp SDK passes `__doc__` through
    untouched. `requires-python` admits all three, so without this the tool
    descriptions clients receive differ purely by interpreter (CB-70, CB-73).

    This deliberately reproduces the compiler's rule and nothing more: take the
    minimum indentation over the non-blank lines AFTER the first, remove exactly
    that prefix from those lines, and leave the first line alone (it begins
    immediately after the opening quotes, so it carries no indentation to strip).
    `inspect.cleandoc` is the tempting shortcut and is wrong here: it also drops
    boundary blank lines and expands tabs, which would both rewrite 61 of the 68
    golden descriptions and blind the gate to whitespace changes clients can see.

    THIS IS THE ONLY COPY. It lived in `tests/_mcp_schema.py` while it normalized
    only the comparison; now that the server emits normalized text too, a second
    definition would be one drift away from the gate and the server disagreeing
    about the very thing they exist to keep in agreement — so the test helper
    imports this one.
    """
    lines = doc.split("\n")
    indent = None
    for line in lines[1:]:
        stripped = line.lstrip(" \t")
        if stripped:
            margin = len(line) - len(stripped)
            indent = margin if indent is None else min(indent, margin)
    if not indent:
        return doc
    return "\n".join([lines[0]] + [line[indent:] for line in lines[1:]])


_SECTION_HEADER = re.compile(r"^([A-Za-z][A-Za-z ]*):[ \t]*$")
_SECTION_ITEM = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*[ \t]*:([ \t]|$)")
_MIN_BODY_INDENT = 4


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _fold_section_items(body: list[str], base: int) -> list[str] | None:
    """One emitted list item per argument, or None when the body is not an item list.

    A line at the section's own indent that reads `identifier:` OPENS an item.
    Anything else — a deeper indent, or a base-indent line that is not
    item-shaped — CONTINUES the item above it, joined with a space. That second
    clause is not defensive: `claims_claim` and `claims_release` each carry a
    `Returns:` whose `outcome: …` item is followed by further sentences at the
    SAME indent, and the naive "base indent means a new item" rule split one
    sentence across two bullets. A body that opens with prose (`codesweep_add`'s
    `Returns:`) is not a list at all and is returned unchanged by the caller —
    collapsing prose into one paragraph is what a paragraph is for.
    """
    items: list[list[str]] = []
    for line in body:
        text = line.strip()
        if _indent_width(line) == base and _SECTION_ITEM.match(text):
            items.append(["- " + text])
        elif items:
            items[-1].append(text)
        else:
            return None
    return [" ".join(parts) for parts in items]


def markdown_sections(doc: str) -> str:
    """Re-emit a Google-style `Args:`/`Returns:` section as a Markdown list (CB-156).

    WHY (and the mechanism matters, because two mechanisms share one symptom).
    MCP clients render descriptions as Markdown. `Args:` sits at column 0 and its
    argument lines are indented 4 with NO blank line between, so CommonMark reads
    `Args:` as opening a PARAGRAPH and — since an indented code block cannot
    interrupt a paragraph — every argument line becomes a LAZY CONTINUATION of it:
    indentation stripped, softbreaks rendered as spaces, all arguments fused into
    one run-on line with the boundaries between them gone. Measured over the wire
    golden BY LINES: 73 descriptions carry `Args:`, 3 carry `Returns:`, and the
    blank-line-then-indent pattern that WOULD make a code block occurs in ZERO of
    the 83. So this is NOT CB-73 recurring: that leak was the source indentation
    surviving into `__doc__`, `dedent_docstring` closed it and still holds. Here
    the dedent is already correct and the text is simply not a Markdown list.

    WHY IT IS WORTH FIXING, stated precisely rather than overclaimed: a client
    configured with GFM-style hard line breaks (`breaks: true`) shows the lines
    separately and never sees the defect. The claim is therefore not "broken for
    everyone" but "correct only under a particular setting of SOMEBODY ELSE'S
    renderer" — a real Markdown list renders correctly under BOTH settings, which
    is what removes the dependency on a foreign configuration.

    WHAT IT EMITS. The header, a blank line, then one `- name: description` per
    argument. A bullet list is the right shape because — unlike an indented code
    block — a list CAN interrupt a paragraph, which three of this surface's own
    descriptions already rely on (`reqs_verify`, `staleness_check`,
    `batch_add` write column-0 bullets under a lead-in line and render fine). The
    blank line is belt and braces for a renderer stricter than CommonMark about
    that. Continuation lines are joined into their item rather than emitted as
    their own lines, so an argument stays ONE bullet under `breaks: true` too.

    ONLY MARKUP CHANGES. No word of any description is added, removed or
    reordered, and a section is left byte-identical whenever it is not an item
    list. IDEMPOTENT BY CONSTRUCTION: what this emits sits at column 0, and the
    detection below requires an INDENTED body, so a second pass sees no section.
    """
    lines = doc.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        header = _SECTION_HEADER.match(lines[i])
        follower = lines[i + 1] if i + 1 < len(lines) else ""
        if not header or not follower.strip() or _indent_width(follower) < _MIN_BODY_INDENT:
            out.append(lines[i])
            i += 1
            continue
        base = _indent_width(follower)
        j = i + 1
        body: list[str] = []
        while j < len(lines) and lines[j].strip() and _indent_width(lines[j]) >= base:
            body.append(lines[j])
            j += 1
        items = _fold_section_items(body, base)
        out.append(lines[i])
        if items is None:
            out.extend(body)
        else:
            out.append("")
            out.extend(items)
        i = j
    return "\n".join(out)


def normalize_description(doc: str) -> str:
    """The ONE composition of every normalization a description gets before the wire.

    `_NormalizedDescriptions` is the only caller on the production path, and the
    render gate in `tests/test_boundary.py` calls THIS same function to judge what
    it finds — never the two steps in sequence — so the server and the gate cannot
    drift about what "normalized" means. Same reason `dedent_docstring` has exactly
    one definition (CB-73).

    The golden GENERATOR gets this composition BY CONSTRUCTION rather than by a
    call of its own: since CB-164 `tests/_mcp_schema` registers through the adapter
    below and imports this name only so the two objects' identity can be pinned
    (`tests/test_server.py::test_the_normalizer_has_exactly_one_definition`). This
    docstring used to say the generator called this function; that stopped being
    true when the adapter took the job over (CB-178).
    """
    return markdown_sections(dedent_docstring(doc))


class _NormalizedDescriptions:
    """Registration-time adapter: every tool's description is normalized ONCE.

    WHY THIS EXISTS (CB-73). The SDK reads `Tool.description` from the function's
    `__doc__`, so on a 3.11/3.12 host clients receive the source indentation.
    MCP clients render descriptions as Markdown, and CommonMark treats a
    4-space-indented line following a blank line as an INDENTED CODE BLOCK — so
    the entire prose body of ~61 tools rendered monospaced as code on
    interpreters `requires-python` promises to support. Measured on 3.12 vs 3.13.

    IT NORMALIZES MORE THAN INDENTATION NOW (CB-156). `normalize_description` also
    re-emits Google-style `Args:`/`Returns:` sections as Markdown lists, because a
    correctly dedented Google-style section is STILL not a Markdown list — its
    argument lines are lazy continuations of the `Args:` paragraph and fuse into one
    run-on line. Same border, same seam, a different way for the wire text to be
    wrong; see `markdown_sections` for the measurement and the honest scope.

    WHY IT WRAPS RATHER THAN MUTATES. Two alternatives were rejected. Rewriting
    `fn.__doc__` in place is a global side effect on another module's objects;
    rewriting the registered `Tool` objects afterwards would reach into the SDK's
    PRIVATE `_tool_manager._tools`, which is a worse coupling than the one
    `install_strict_arguments` already documents. `description=` is a public,
    declared parameter of `MCPServer.tool()` (verified), so passing it needs no
    private API and no mutation.

    The surface is deliberately one method: providers call `mcp.tool(...)` and
    nothing else — 68 times, verified by sweep — so `__getattr__` exists only so
    a future provider that reaches for something else keeps working rather than
    failing obscurely.
    """

    def __init__(self, server: MCPServer) -> None:
        self._server = server

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        inner = self._server.tool(*args, **kwargs)

        def register(fn: Any) -> Any:
            # An explicit description always wins: a caller that passed one has
            # already said what the client should see, and second-guessing it
            # here would make this adapter a policy rather than a normalizer.
            if kwargs.get("description") is None and fn.__doc__:
                return self._server.tool(
                    *args, **{**kwargs, "description": normalize_description(fn.__doc__)}
                )(fn)
            return inner(fn)

        return register

    def __getattr__(self, name: str) -> Any:
        return getattr(self._server, name)


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def install_strict_arguments(server: MCPServer) -> None:
    """Refuse a `tools/call` that carries argument names the tool does not declare.

    WHY THIS EXISTS (CB-15). The SDK builds each tool's argument model with
    pydantic's default ``extra="ignore"``, so an unknown argument NAME is dropped
    during validation and the tool runs without it — returning a success payload
    with the caller's data discarded. An unknown VALUE, by contrast, raises. That
    asymmetry turns a singular/plural typo (`note=` for `notes=`) into invisible
    data loss, which is the one failure a tracker cannot afford. Setting
    ``additionalProperties: false`` does not help: the server never validates
    arguments against the JSON Schema.

    THE SDK COUPLING LIVES HERE AND NOWHERE ELSE. `MCPServer.middleware` is public
    but its signature is documented as provisional, so if a future SDK release
    breaks this, this one function is the only thing to repair.
    """
    declared: dict[str, set[str]] = {}

    async def reject_unknown_arguments(ctx: Any, call_next: Any) -> Any:
        if ctx.method == "tools/call" and isinstance(ctx.params, Mapping):
            name = ctx.params.get("name")
            arguments = ctx.params.get("arguments")
            if isinstance(name, str) and isinstance(arguments, Mapping):
                if not declared:
                    for tool in await server.list_tools():
                        declared[tool.name] = set(tool.input_schema.get("properties", {}))
                # An unknown tool name is not ours to answer — let the SDK's own
                # "Unknown tool" error stay authoritative.
                known = declared.get(name)
                if known is not None:
                    unknown = sorted(set(arguments) - known)
                    if unknown:
                        raise MCPError(
                            code=INVALID_PARAMS,
                            message=(
                                f"Unknown argument(s) for tool {name!r}: {', '.join(unknown)}. "
                                f"Accepted: {', '.join(sorted(known))}. "
                                "Refused rather than ignored — a dropped argument would "
                                "otherwise look like a successful write."
                            ),
                        )
        return await call_next(ctx)

    server.middleware.append(reject_unknown_arguments)


def install_usage_tracking(server: MCPServer, conn_factory: db.ConnFactory) -> None:
    """Record every `tools/call` this server completes, for `codebugs usage` (release-b, DIR-1).

    THE SDK COUPLING LIVES HERE, BESIDE `install_strict_arguments` — same reason
    as that function's own docstring: `MCPServer.middleware` is public but its
    signature is documented as provisional, and this file is already the one
    place that touches it, so a second seam anywhere else would let the two
    middlewares drift apart on what the SDK actually hands them.

    WHAT `call_next` ACTUALLY RETURNS FOR A TOOL-BODY FAILURE — the finding that
    matters most here, verified by reading `mcp.server.mcpserver.server` rather
    than assumed. `_handle_call_tool` catches every domain exception a tool
    function raises (everything except `MCPError`) BEFORE it ever reaches a
    middleware, and returns a normal `CallToolResult(is_error=True, ...)` — not
    a raised exception. So a `ValueError` from, say, `findings.update_finding`
    is invisible to `except Exception` here; it can only be seen by matching the
    RETURNED shape, exactly as the SDK's own `OpenTelemetryMiddleware`
    (`mcp/server/_otel.py`) does with `case CallToolResult(is_error=True) |
    {"isError": True}`. Because that swallow already discards the original
    exception's class (only `str(e)` survives, folded into text content this
    module must not store — see below), a call caught this way is recorded with
    the fixed marker `"ToolError"` rather than a guessed class name. A TRUE
    exception escaping to this middleware (an `MCPError`, or the strict-argument
    middleware's own refusal if it runs OUTER of this one — see the ordering
    note below) is recorded with its REAL class name, then re-raised unchanged:
    recording is never allowed to be the reason a caller's exception changes
    shape or vanishes.

    ERROR_TYPE IS A CLASS NAME, NEVER THE MESSAGE. `str(e)` can carry the
    caller's own data (a category name, an id, a whole file path) and can be
    arbitrarily long; `type(e).__name__` answers "what kind of thing broke"
    without disclosing any of it. See `usage.py`'s module docstring for the
    same rule stated from the storage side.

    ORDERING, AND THE COMPOSITION THIS PROJECT'S OWN CLAUDE.md CALLS OUT: this
    is registered in `main()` AFTER `install_strict_arguments`, so on
    `server.middleware` (outermost-first, per `MCPServer.middleware`'s own
    docstring) strict-arguments sits OUTER and this sits INNER. `reject_unknown_arguments`
    raises `MCPError` BEFORE ever calling ITS OWN `call_next` when an argument
    name is unknown — so this middleware's `__call__` is never invoked at all
    for such a call, and a refused-for-bad-arguments call is NOT counted here.
    That is a conscious choice: `tool_calls` aims to describe what a TOOL's own
    logic does (how often it runs, how often IT fails, how long it takes), and
    a client's argument-name typo never reaches the tool body — it is a
    protocol-level rejection identical in shape for every tool, and folding it
    into a specific tool's failure count would attribute a client mistake to
    that tool's own health. `tests/test_server.py`'s
    `TestUsageAndStrictArgumentsComposition` drives both middlewares together
    and pins this: an unknown-argument call raises MCPError as before AND
    leaves `tool_calls` untouched.

    RULE 1 — RECORDING NEVER FAILS THE CALL. The write happens through
    `_record`, below, which swallows every exception `conn_factory()` or
    `usage.record_call` can raise.
    RULE 2 — NO SILENT SWALLOW (CB-15's own rule, applied to this module's own
    failure mode). A recording failure prints one line to stderr — the one
    channel every MCP client logs (see `_preflight`'s docstring for the same
    reasoning) — naming the tool and the failure; it never appears in a tool's
    OWN response, because the caller's response is not this middleware's to
    rewrite.
    RULE 3 — THE CONNECTION COMES FROM THE SERVER'S OWN `conn_factory`, THE
    SAME ONE EVERY TOOL USES (`server.py`'s `_conn`), never a second,
    independently-resolved connection. Tracker-root resolution is a heuristic
    with several declaration channels (CLAUDE.md's Database section); a second
    resolution path could silently write this table to a DIFFERENT tracker than
    the one the counted tool call used — the CB-8 class of bug — and this
    project's own `.claude/plans/L3-BRIEF-DIR-1-release-b-tool-call-usage.md`
    §2(2.3) names that risk explicitly. The connection the tool itself opened
    is already closed by the time this middleware runs (each tool body does
    `with conn_factory() as conn: ...` and exits that block before returning),
    so this necessarily opens its OWN connection — but through the identical
    factory, which is what keeps it pointed at the same tracker.
    """

    async def record_tool_usage(ctx: Any, call_next: Any) -> Any:
        if ctx.method != "tools/call" or not isinstance(ctx.params, Mapping):
            return await call_next(ctx)
        name = ctx.params.get("name")
        if not isinstance(name, str):
            return await call_next(ctx)

        start = time.perf_counter()
        try:
            result = await call_next(ctx)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            _record(
                conn_factory,
                tool_name=name,
                success=False,
                error_type=type(exc).__name__,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000

        match result:
            case CallToolResult(is_error=True) | {"isError": True}:
                is_error = True
            case _:
                is_error = False
        _record(
            conn_factory,
            tool_name=name,
            success=not is_error,
            error_type="ToolError" if is_error else None,
            duration_ms=duration_ms,
        )
        return result

    server.middleware.append(record_tool_usage)


def _record(
    conn_factory: db.ConnFactory,
    *,
    tool_name: str,
    success: bool,
    error_type: str | None,
    duration_ms: float,
) -> None:
    """The one write this module performs — see rules 1-3 on `install_usage_tracking`.

    CB-192: `PRAGMA busy_timeout=50` is set on THIS connection only, right
    after it is opened and before the write, never touching `db.connect()` or
    any other connection's setting (`busy_timeout` is per-connection). After
    CB-195 removed `ensure_schema`'s own unconditional seed write,
    `usage.record_call`'s INSERT is the last write left on the tool-call path
    that can contend with a foreign writer — and it used to inherit the
    shared 5000ms `busy_timeout`, so a concurrent write anywhere in the
    tracker could delay every client's response by up to five seconds just to
    record telemetry about that same call.

    50ms is sized from a measurement, not a guess: an ordinary codebugs write
    holds the lock for 0.84-8.58ms (median 0.84ms on an empty tracker, 6.50ms
    median / 7.49ms p95 / 8.58ms max on a 3000-row one) — so 50ms is roughly a
    sixfold margin over the observed p95. Ordinary concurrency is therefore
    absorbed without losing a row; only pathological contention (a wedged
    writer, an abnormally long foreign transaction) trades the lost row for a
    bounded ~54ms ceiling instead of the old 5-second one. Today's behaviour
    under that same pathological case is worse on both axes: the client waits
    the full 5 seconds AND the row is still lost when the wait times out — so
    this is not a trade against a working case, it is a strict improvement on
    the one case that was already failing.

    Rule 2 (no silent swallow, below) is what keeps a shortened timeout
    honest: a row dropped by the 50ms budget still prints to stderr exactly
    like any other recording failure — nothing about the shorter timeout
    licenses a silent loss.
    """
    try:
        with conn_factory() as conn:
            conn.execute("PRAGMA busy_timeout=50")
            usage.record_call(
                conn,
                tool_name=tool_name,
                success=success,
                error_type=error_type,
                duration_ms=duration_ms,
            )
    except Exception as exc:
        print(
            f"codebugs-mcp: failed to record tool-call usage for {tool_name!r}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


def _preflight() -> None:
    """Say once, on stderr, when this server's tracker binding is broken or unusual.

    WHY THIS EXISTS (CB-11). `_conn` connects lazily per tool call, so a server
    started where no tracker is reachable looks perfectly healthy at startup and
    then fails every call forever — once per invocation, with no single moment
    that names the problem. stderr is the right channel because MCP clients log
    it, while tool responses are the one place the diagnostic cannot reach.

    WARN-ONLY, NEVER FATAL. Exiting here would break lazy-connect self-healing: a
    server whose project directory appears after startup must still work. So this
    reports and returns, always.

    Silent on the ordinary discovered path — one line per project per startup is
    noise. A DECLARED root is announced, because a non-default binding is exactly
    what someone reading the log later needs to see.
    """
    info = db.describe_root()
    if info["error"]:
        print(f"codebugs-mcp: {info['error']}", file=sys.stderr)
        print(
            "codebugs-mcp: serving anyway — tool calls will fail until a tracker is "
            "reachable; `codebugs where` shows the current binding",
            file=sys.stderr,
        )
        return
    # The two `exists` checks below are mutually exclusive with each other by
    # construction (a tri-state cannot be two values at once), and both are
    # mutually exclusive with the declared-root line for a reason that is not
    # visible from here: a resolved-but-absent — or unconfirmable — database can
    # only come from the walk, since the named and declared routes refuse it
    # outright, so `source` is always "discovery" when `exists` is not True.
    # Written as separate `if`s rather than an `elif` chain because they answer
    # different questions, not because several can fire.
    if info["exists"] is False:
        # Resolving is not the same as being there (CB-23). This binding does not
        # fail — the first tool call CREATES the tracker — so it is invisible in
        # exactly the way CB-11 exists to prevent, and is worth a line even though
        # nothing is broken yet.
        #
        # `is False` for CB-203's reason, and it matters MORE here than in the
        # CLI: this line promises a healthy future to a log a human reads hours
        # later, and under truthiness an unreachable tracker printed exactly that
        # promise. One resolver, two consumers — so the same truth reaches both,
        # through each one's own channel.
        print(
            f"codebugs-mcp: {info['path']} does not exist yet — the first write will "
            f"create a new, empty tracker there",
            file=sys.stderr,
        )
    if info["exists"] is None:
        # CB-203: could not tell whether a tracker is there. Warn-only like every
        # other line here — the server must still start, because the condition
        # can be repaired underneath a running server and lazy connect will pick
        # it up.
        print(
            f"codebugs-mcp: could not confirm a tracker at {info['path']} — "
            f"{info['exists_reason']}; tool calls may fail until it is fixed",
            file=sys.stderr,
        )
    if info["source"] != "discovery":
        print(
            f"codebugs-mcp: tracker root {info['root']} (from {info['source_label']})",
            file=sys.stderr,
        )
    if info["writable"] is False:
        # CB-100: this is the moment that matters most — before this, an
        # unwritable tracker looked healthy at startup and then failed every
        # tool call forever, with no single moment naming why (CB-11's failure
        # mode, arriving through a new door). Advisory (os.access is
        # check-then-act), so worded to investigate rather than declare, and
        # silent on True/None on purpose — see db.describe_root's docstring.
        print(
            f"codebugs-mcp: {info['path']} may not be writable — check "
            f"permissions on the file and its .codebugs/ directory; "
            f"`codebugs where` shows the current binding",
            file=sys.stderr,
        )


SERVER_NAMES = {
    "findings": "codebugs",
    "provenance": "codeprovenance",
    "reqs": "codereqs",
    "merge": "codemerge",
    "sweep": "codesweep",
    "bench": "codebench",
    "blockers": "codeblockers",
    "milestones": "codemilestones",
    "claims": "codeclaims",
    "similarity": "codesimilarity",
    "relations": "coderelations",
    "grouping": "codegrouping",
    "loc": "codeloc",
    "all": "codebugs",
}

#: Told to every connecting client via `MCPServer(instructions=...)` (T-75). The
#: 83-tool catalogue says nothing about ORDER; this is the one place that does.
#: Names the recommended loop and the 5-8 tool names it cannot be read without,
#: never a full tool listing (that already exists, and is longer than any text
#: written here) — see the unit brief for the content contract this text is
#: negotiated against.
INSTRUCTIONS = """Recommended loop for a finding:

1. File the observation with `add` (or `batch_add` for several at once).
2. Read what came back before doing anything else: `attention` is the server's
   own flag when your observation raised the card's severity or diverged from
   its stored category; `dedup_action` says whether this created a new card,
   bumped or reopened an existing one, or refiled one already dismissed.
3. The code location is anchored automatically at file time (git-derived), so
   the card survives later edits; `anchor_resolve` reports whether an anchor
   still points at live code.
4. Close the card with `update(status="fixed")` once it is actually fixed.

Deduplication is the point, not a side effect: filing the same finding twice
does not create two cards, it bumps or reopens the one that already exists.
Filing an observation again is normal and useful, not noise.

Working alongside other agents on this tracker? Claim a card with
`claims_claim` before starting on it and release it with `claims_release` when
done, or two agents can end up fixing the same thing.

Requirements (`reqs_add`, `reqs_query`, ...) are a separate, authored entity
next to findings: they have no deduplication. Do not file a requirement
through `add`, or a defect through `reqs_add`.
"""


def _build_server(mode: str, conn_factory=None) -> MCPServer:
    """Build and fully wire the MCP server for `mode` — the exact steps `main()` runs.

    Split out of `main()` (T-75) so a test can construct the real server object
    — the one carrying `instructions=INSTRUCTIONS` and every registered tool —
    without also entering `server.run()`'s blocking stdio loop. `conn_factory`
    defaults to the module's own `_conn`, which is what `main()` needs; a test
    passes its own tracker fixture instead.
    """
    if conn_factory is None:
        conn_factory = _conn

    # mcp 2.0 renamed FastMCP -> MCPServer and dropped the constructor's
    # json_response flag; it only ever applied to streamable-http, and we run stdio.
    server_obj = MCPServer(SERVER_NAMES[mode], instructions=INSTRUCTIONS)

    # Wrapped, so what clients receive does not depend on which interpreter
    # built the server (CB-73). The adapter is registration-time only; the real
    # server object is what runs and what install_strict_arguments inspects.
    registrar = _NormalizedDescriptions(server_obj)
    for provider in db.get_tool_providers(mode=mode):
        provider.register_fn(registrar, conn_factory)

    # After registration, so the middleware sees the full tool catalogue.
    install_strict_arguments(server_obj)
    # AFTER strict-arguments: on `server.middleware` (outermost-first) that
    # makes strict-arguments OUTER and usage tracking INNER, so an
    # unknown-argument refusal never reaches this middleware at all — a
    # deliberate choice, not an accident of call order. See
    # `install_usage_tracking`'s docstring for why.
    install_usage_tracking(server_obj, conn_factory)

    return server_obj


def main():
    """Run the MCP server with optional mode selection.

    DELIBERATELY NOT GIVEN CB-78's SIGPIPE TREATMENT, and this is the call site
    rather than a plan note because the next person auditing the two entry points
    for consistency would otherwise either "fix" the asymmetry or re-derive it.
    `cli.run` restores `SIG_DFL` so a dead reader kills the process at 141; here
    stdout is the stdio JSON-RPC **transport**, not a report stream. A write
    failure on it is a protocol event the SDK's error handling should observe,
    and nobody pipes an MCP server into `head`. Dying silently by signal when a
    client disconnects is a different question with a different owner.
    """
    parser = argparse.ArgumentParser(description="Codebugs MCP server")
    parser.add_argument(
        "--mode",
        choices=list(SERVER_NAMES),
        default="all",
        help="Which tools to expose (default: all)",
    )
    parser.add_argument(
        "--tracker-root",
        default=None,
        metavar="DIR",
        help=(
            f"Serve the tracker in DIR instead of deriving it from the working "
            f"directory (overrides ${db.ENV_ROOT})"
        ),
    )
    args = parser.parse_args()
    db.set_tracker_root(args.tracker_root)
    _preflight()

    server = _build_server(args.mode)
    server.run()


if __name__ == "__main__":
    main()
