"""The generated sweep surface, exercised through the REAL server and CLI paths.

WHY THIS FILE EXISTS, AND WHY IT IS LONGER THAN ITS BENCH TWIN. `sweep`'s CLI
surface is covered by NOTHING today — CB-146 measured it: 83 tests in
`tests/test_sweep.py`, zero of them reaching a verb, and no test anywhere on
`--mode`. A lost verb, an eaten `help=` string or a changed `dest` would leave
that suite green. So the CLI contract below is written out as a LITERAL table
rather than derived from the declarations: a table derived from the source of
truth cannot notice the source of truth changing.

The second thing this file has to catch is specific to how `sweep` was written.
Its nine CLI handlers were CLOSURES inside `register_cli` and are module-level
functions now, so every name they used to capture from that frame — `db`,
`sys`, `argparse`, `format_table`, and the two comma-splitters — is a
module-level lookup. A name lost in that move raises only when the body RUNS, so
`TestHandlerBodiesRunWithTheirNamesResolved` runs all nine end to end.

WHAT IS DELIBERATELY NOT HERE. No residency rule and no acceptance bar: BT-6's
seven behavioural bars were each shown takeable without doing the work, and the
owner ratified (2026-08-22, Э-15) that an eighth is not written and that the
honesty of the pilot's number is judged by a reader, not by a predicate. These
tests pin BEHAVIOUR the pilot must preserve; none of them is offered as proof
that the surface's content migrated. The generator's own refusal contract is
already pinned once, in `tests/test_bench_surface.py`, and is not repeated here
— a second copy would be one drift from disagreeing with the first.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import subprocess
import sys
import types as pytypes
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError

from codebugs import db, server, surfacegen, sweep
from codebugs.server import dedent_docstring
from codebugs.sweep_surface import SURFACE

TOOL_NAMES = [
    "codesweep_add",
    "codesweep_archive",
    "codesweep_archive_items",
    "codesweep_create",
    "codesweep_list",
    "codesweep_list_items",
    "codesweep_mark",
    "codesweep_next",
    "codesweep_status",
]
VERB_NAMES = [
    "sweep-add",
    "sweep-archive",
    "sweep-archive-items",
    "sweep-create",
    "sweep-list",
    "sweep-list-items",
    "sweep-mark",
    "sweep-next",
    "sweep-status",
]

#: The order verbs are REGISTERED in, which is the order `codebugs --help`
#: prints them. Sorted views elsewhere in this file cannot see a reordering.
REGISTRATION_ORDER = [
    "sweep-create",
    "sweep-add",
    "sweep-next",
    "sweep-mark",
    "sweep-status",
    "sweep-archive",
    "sweep-archive-items",
    "sweep-list-items",
    "sweep-list",
]


@pytest.fixture
def tracker(tmp_path):
    """A real tracker, so a tool call reaches real storage."""
    db.init_project(str(tmp_path))
    root = str(tmp_path)

    def factory():
        return _Closing(db.connect(project_dir=root))

    return factory


class _Closing:
    """`server._conn` in miniature: a connection usable as a context manager."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        return False


def build_server(conn_factory, declarations=None):
    """A server built through the path `server.py` actually uses."""
    raw = MCPServer("codebugs")
    adapter = server._NormalizedDescriptions(raw)
    if declarations is None:
        sweep.register_tools(adapter, conn_factory)
    else:
        surfacegen.emit_tools(adapter, conn_factory, declarations)
    server.install_strict_arguments(raw)
    return raw


def build_parser(declarations=None):
    parser = argparse.ArgumentParser(prog="codebugs")
    sub = parser.add_subparsers(dest="command")
    commands: dict = {}
    if declarations is None:
        sweep.register_cli(sub, commands)
    else:
        surfacegen.emit_cli(sub, commands, declarations)
    return parser, sub, commands


def listed(server_obj):
    return asyncio.run(server_obj.list_tools())


def called(server_obj, name, arguments):
    """The structured payload of a real `tools/call`, not the raw result object."""
    result = asyncio.run(server_obj.call_tool(name, arguments))
    assert not result.is_error, result.content
    return result.structured_content


#: What argparse's `action=` strings name. Written out because `action=` selects
#: a CONSTRUCTOR and leaves no attribute of that name behind, so a declaration
#: carrying it cannot be checked by `getattr(action, key)` like every other
#: keyword.
_ACTION_CLASSES = {"store_true": argparse._StoreTrueAction}


def action_by_dest(parser, dest):
    for action in parser._actions:
        if action.dest == dest:
            return action
    raise AssertionError(f"no action with dest {dest!r}")


class TestGeneratedMcpSurface:
    def test_exactly_nine_tools_and_no_duplicates(self, tracker):
        """The late-binding / double-registration trap, asserted as a count.

        A loop registering inside a closure over the loop variable, or one
        registering a declaration twice, shows up here and nowhere else — and
        nine declarations make both likelier than four did.
        """
        tools = listed(build_server(tracker))
        assert sorted(t.name for t in tools) == TOOL_NAMES
        assert len(tools) == len(TOOL_NAMES)

    def test_every_description_is_the_declared_prose_dedented(self, tracker):
        """`__doc__`, not `description=` — see the generator's module docstring.

        If the emitter ever passes `description=`, `_NormalizedDescriptions`
        stops dedenting and this comparison is what notices, because the wire
        golden registers on a raw server and dedents by itself.
        """
        by_name = {t.name: t for t in listed(build_server(tracker))}
        for decl in SURFACE:
            facet = decl["mcp"]
            assert by_name[facet["name"]].description == dedent_docstring(facet["doc"])

    def test_input_schema_matches_the_declared_parameters(self, tracker):
        by_name = {t.name: t for t in listed(build_server(tracker))}
        for decl in SURFACE:
            facet = decl["mcp"]
            schema = by_name[facet["name"]].input_schema
            declared = [p["name"] for p in facet["params"]]
            assert list(schema["properties"]) == declared
            required = [p["name"] for p in facet["params"] if "default" not in p]
            assert sorted(schema.get("required", [])) == sorted(required)
            for param in facet["params"]:
                if "default" in param:
                    assert schema["properties"][param["name"]]["default"] == param["default"]

    def test_schema_title_comes_from_the_declared_name(self, tracker):
        """The golden reads `inputSchema.title`, which the SDK derives from
        `__name__` — so the emitted function must carry the declared name."""
        for tool in listed(build_server(tracker)):
            assert tool.input_schema["title"] == f"{tool.name}Arguments"

    def test_an_indented_declared_doc_is_dedented_by_the_server_path(self, tracker):
        """Pins the registration FORM, which is otherwise invisible.

        Passing `description=` instead of setting `__doc__` bypasses
        `server._NormalizedDescriptions` (CB-73) — and against this file's own
        declarations that substitution is indistinguishable, because their prose
        constants sit at column 0 and are already dedented. The discriminator
        has to be a declaration whose prose is indented.
        """
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["mcp"]["name"] == "codesweep_list":
                decl["mcp"]["doc"] = "Summary line.\n\n        Indented body.\n        "
        by_name = {t.name: t.description for t in listed(build_server(tracker, clone))}
        assert by_name["codesweep_list"] == "Summary line.\n\nIndented body.\n"

    def test_a_valid_call_reaches_the_domain(self, tracker):
        srv = build_server(tracker)
        made = called(srv, "codesweep_create", {"name": "t1"})
        assert made["sweep_id"] == "SW-1"
        added = called(srv, "codesweep_add", {"sweep_ref": "t1", "items": ["a", "b"]})
        assert added["added"] == 2
        status = called(srv, "codesweep_status", {"sweep_ref": "t1"})
        assert status["total"] == 2 and status["remaining"] == 2

    def test_EVERY_tool_reaches_ITS_OWN_domain_function(self, tracker):
        """All nine, through a real `tools/call`, discriminated by result shape.

        `calls=` is the one declared field that is BEHAVIOUR rather than
        surface, so no surface comparison can see it wired to the wrong domain
        function — the wire golden is byte-identical either way, and this is a
        typo away in a file of nine near-identical blocks. Measured: with
        `codesweep_list_items` rewired to `list_sweeps`, the whole suite stayed
        green until this test existed. Each response's top-level key SET is
        unique across the nine, so the key set is the discriminator.
        """
        srv = build_server(tracker)
        expected = {
            "codesweep_create": ["created_at", "default_batch_size", "description",
                                 "lifecycle", "name", "status", "sweep_id",
                                 "terminal_states", "transitions", "updated_at"],
            "codesweep_add": ["added", "duplicates_skipped", "recurrence_bumped", "sweep_id"],
            "codesweep_next": ["items", "remaining", "sweep_id"],
            "codesweep_mark": ["state", "sweep_id", "updated"],
            "codesweep_status": ["archived", "by_state", "by_tag", "default_batch_size",
                                 "lifecycle", "name", "processed", "remaining", "status",
                                 "sweep_id", "terminal_states", "total"],
            "codesweep_list_items": ["items", "sweep_id"],
            "codesweep_archive_items": ["archived", "sweep_id"],
            "codesweep_list": ["sweeps"],
            "codesweep_archive": ["status", "sweep_id"],
        }
        assert sorted(expected) == TOOL_NAMES
        calls = [
            ("codesweep_create", {"name": "t1"}),
            ("codesweep_add", {"sweep_ref": "t1", "items": ["a", "b"]}),
            ("codesweep_next", {"sweep_ref": "t1"}),
            ("codesweep_mark", {"sweep_ref": "t1", "items": ["a"]}),
            ("codesweep_status", {"sweep_ref": "t1"}),
            ("codesweep_list_items", {"sweep_ref": "t1"}),
            ("codesweep_archive_items", {"sweep_ref": "t1", "items": ["b"]}),
            ("codesweep_list", {}),
            ("codesweep_archive", {"sweep_ref": "t1"}),
        ]
        for name, arguments in calls:
            assert sorted(called(srv, name, arguments)) == expected[name], name

    def test_EVERY_generated_body_forwards_EVERY_declared_parameter(self, tracker):
        """All nine tools, every parameter, by name, with declared defaults.

        Every one of `sweep`'s tools is generated from `calls=` — there is no
        handwritten MCP body left — so a dropped parameter anywhere is a silently
        wrong call rather than a crash. Asserted with a spy per tool rather than
        by observing results: a call that exercises two arguments cannot see an
        emitter that drops the rest.
        """
        for decl in SURFACE:
            facet = decl["mcp"]
            seen: dict = {}

            def spy(conn, **kwargs):
                seen.update(kwargs)
                return {"ok": True}

            clone = copy.deepcopy(SURFACE)
            for candidate in clone:
                if candidate["mcp"]["name"] == facet["name"]:
                    candidate["mcp"]["calls"] = spy
            supplied = {
                p["name"]: "supplied" for p in facet["params"] if "default" not in p
            }
            for param in facet["params"]:
                if "default" not in param and param["name"] == "items":
                    supplied["items"] = ["x"]
            srv = build_server(tracker, clone)
            called(srv, facet["name"], supplied)

            assert sorted(seen) == sorted(p["name"] for p in facet["params"]), facet["name"]
            for param in facet["params"]:
                if "default" in param:
                    assert seen[param["name"]] == param["default"], (facet["name"], param["name"])
            for name, value in supplied.items():
                assert seen[name] == value, (facet["name"], name)

    def test_an_unknown_argument_is_refused(self, tracker):
        """CB-15 over a GENERATED tool: the emitted signature is what the
        strict-argument middleware reads its allowlist from."""
        srv = build_server(tracker)
        middleware = srv.middleware[-1]

        async def call_next(ctx):
            return {"ok": True}

        ctx = pytypes.SimpleNamespace(
            method="tools/call",
            params={
                "name": "codesweep_list_items",
                "arguments": {"sweep_ref": "t1", "tags": "x"},
            },
        )
        with pytest.raises(MCPError) as excinfo:
            asyncio.run(middleware(ctx, call_next))
        assert "tags" in excinfo.value.message
        assert "tag" in excinfo.value.message


#: THE CLI CONTRACT, WRITTEN OUT. Not derived from `SURFACE`: a table derived
#: from the source of truth cannot notice the source of truth changing, and
#: CB-146 measured that nothing else in this repository is watching these verbs.
#: Per verb: the `help=` the parent parser lists it under, then one row per
#: argument — (dest, option_strings, nargs, action class, default, help).
CLI_CONTRACT = {
    "sweep-create": (
        "Create a new sweep",
        [
            ("name", ["--name"], None, "_StoreAction", None, "Optional sweep name"),
            ("description", ["--description"], None, "_StoreAction", None, "Sweep description"),
            (
                "batch_size",
                ["--batch-size"],
                None,
                "_StoreAction",
                None,
                "Default batch size (default: 10)",
            ),
            (
                "lifecycle",
                ["--lifecycle"],
                None,
                "_StoreAction",
                None,
                "Comma-separated lifecycle states (default: pending,done)",
            ),
            (
                "terminal_states",
                ["--terminal-states"],
                None,
                "_StoreAction",
                None,
                "Comma-separated terminal states (default: done)",
            ),
        ],
    ),
    "sweep-add": (
        "Add items to a sweep",
        [
            ("sweep", [], None, "_StoreAction", None, "Sweep ID (SW-N) or name"),
            ("items", [], "+", "_StoreAction", None, "Items to add"),
            ("tags", ["--tags"], None, "_StoreAction", None, "Comma-separated tags"),
        ],
    ),
    "sweep-next": (
        "Get next batch of unprocessed items",
        [
            ("sweep", [], None, "_StoreAction", None, "Sweep ID (SW-N) or name"),
            ("limit", ["--limit"], None, "_StoreAction", None, "Batch size override"),
            (
                "tags",
                ["--tags"],
                None,
                "_StoreAction",
                None,
                "Filter by tags (comma-separated)",
            ),
        ],
    ),
    "sweep-mark": (
        "Mark items as processed or transition state",
        [
            ("sweep", [], None, "_StoreAction", None, "Sweep ID (SW-N) or name"),
            ("items", [], "+", "_StoreAction", None, "Items to mark"),
            (
                "undo",
                ["--undo"],
                0,
                "_StoreTrueAction",
                False,
                "Map to first non-terminal state",
            ),
            (
                "state",
                ["--state"],
                None,
                "_StoreAction",
                None,
                "Explicit target state (validated against lifecycle)",
            ),
        ],
    ),
    "sweep-status": (
        "Sweep progress overview",
        [("sweep", [], None, "_StoreAction", None, "Sweep ID (SW-N) or name")],
    ),
    "sweep-archive": (
        "Archive an entire sweep",
        [("sweep", [], None, "_StoreAction", None, "Sweep ID (SW-N) or name")],
    ),
    "sweep-archive-items": (
        "Selectively archive entries (soft-delete)",
        [
            ("sweep", [], None, "_StoreAction", None, "Sweep ID (SW-N) or name"),
            ("items", [], "*", "_StoreAction", None, "Specific items to archive (optional)"),
            ("state", ["--state"], None, "_StoreAction", None, "Archive entries in this state"),
            (
                "older_than",
                ["--older-than"],
                None,
                "_StoreAction",
                None,
                "Archive entries older than (e.g. 30d, 6m)",
            ),
            (
                "reason",
                ["--reason"],
                None,
                "_StoreAction",
                None,
                "Free-form reason recorded on archived entries",
            ),
        ],
    ),
    "sweep-list-items": (
        "List entries in a sweep",
        [
            ("sweep", [], None, "_StoreAction", None, "Sweep ID (SW-N) or name"),
            ("state", ["--state"], None, "_StoreAction", None, "Filter by state"),
            ("tag", ["--tag"], None, "_StoreAction", None, "Filter by tag"),
            ("all", ["--all"], 0, "_StoreTrueAction", False, "Include archived entries"),
            (
                "archived_only",
                ["--archived-only"],
                0,
                "_StoreTrueAction",
                False,
                "Show only archived entries",
            ),
            ("limit", ["--limit"], None, "_StoreAction", None, "Max entries to return"),
        ],
    ),
    "sweep-list": (
        "List sweeps",
        [("all", ["--all"], 0, "_StoreTrueAction", False, "Include archived sweeps")],
    ),
}

#: The arguments argparse coerces with `type=int`, kept beside the table rather
#: than in it: `type` is a callable and a tuple of callables reads worse than a
#: set of the two facts that matter.
INT_TYPED = {
    ("sweep-create", "batch_size"),
    ("sweep-next", "limit"),
    ("sweep-list-items", "limit"),
}


class TestGeneratedCliSurface:
    def test_exactly_nine_verbs(self):
        _parser, sub, commands = build_parser()
        assert sorted(sub.choices) == VERB_NAMES
        assert sorted(commands) == VERB_NAMES

    def test_registration_order_is_preserved(self):
        """Sorted views above cannot see a reordering, and `codebugs --help`
        prints the registration order to a user."""
        _parser, _sub, commands = build_parser()
        assert list(commands) == REGISTRATION_ORDER

    def test_the_cli_contract_matches_the_built_parser(self):
        """The literal contract, argument by argument. CB-146's only catcher."""
        _parser, sub, _commands = build_parser()
        listed_help = {p.dest: p.help for p in sub._choices_actions}
        assert sorted(listed_help) == VERB_NAMES
        # The table must cover every verb. Without this, deleting a verb's
        # entry silently stops checking its arguments while
        # `test_exactly_nine_verbs` stays green off the separate literal.
        assert sorted(CLI_CONTRACT) == VERB_NAMES
        for verb, (verb_help, rows) in CLI_CONTRACT.items():
            child = sub.choices[verb]
            assert listed_help[verb] == verb_help, verb
            actual = [
                (
                    a.dest,
                    list(a.option_strings),
                    a.nargs,
                    type(a).__name__,
                    a.default,
                    a.help,
                )
                for a in child._actions
                if a.dest != "help"
            ]
            assert actual == [
                (dest, opts, nargs, cls, default, text)
                for dest, opts, nargs, cls, default, text in rows
            ], verb
            for dest, *_rest in rows:
                action = action_by_dest(child, dest)
                assert action.type is (int if (verb, dest) in INT_TYPED else None), (verb, dest)

    def test_no_verb_declares_a_keyword_the_contract_table_cannot_see(self):
        """The table above enumerates six attributes; argparse has more, and
        `emit_cli` passes every declared keyword through UNTRANSLATED.

        So this is the composition check the table cannot be: it states the
        PROPERTY that holds across the whole of `sweep`'s CLI — no option is
        required, nothing restricts `choices`, nothing overrides `metavar`, and
        `const` is whatever the action class implies — rather than adding four
        more columns to 38 rows. Measured: without it, adding `required=True` to
        one declared flag left the whole suite green while
        `codebugs sweep-next SW-1` became unusable (exit 2), because the
        keyword-forwarding test one method up CONFIRMS the mutation instead of
        refusing it. If a future verb legitimately needs one of these, the
        refusal is loud and the exception is written here, in the open.
        """
        _parser, sub, _commands = build_parser()
        for verb in VERB_NAMES:
            for action in sub.choices[verb]._actions:
                if action.dest == "help":
                    continue
                where = (verb, action.dest)
                if action.option_strings:
                    # `required=` is declarable only on an OPTION; on a
                    # positional argparse DERIVES it from `nargs` and refuses
                    # the keyword outright, so asserting it there would pin
                    # argparse's derivation rather than the declaration.
                    assert action.required is False, where
                assert action.choices is None, where
                assert action.metavar is None, where
                expected_const = True if type(action).__name__ == "_StoreTrueAction" else None
                assert action.const is expected_const, where

    def test_every_declared_argparse_keyword_reaches_the_parser(self):
        _parser, sub, _commands = build_parser()
        for decl in SURFACE:
            facet = decl["cli"]
            child = sub.choices[facet["name"]]
            for arg in facet["args"]:
                flags = arg["flags"]
                dest = flags[-1].lstrip("-").replace("-", "_")
                action = action_by_dest(child, dest)
                if flags[0].startswith("-"):
                    assert action.option_strings == flags
                for key, value in arg.items():
                    if key == "flags":
                        continue
                    if key == "action":
                        # `action=` is argparse's CONSTRUCTOR selector, not an
                        # attribute of what it constructs, so it is checked
                        # through the class argparse actually built.
                        assert type(action) is _ACTION_CLASSES[value], (facet["name"], dest)
                        continue
                    assert getattr(action, key) == value, (facet["name"], dest, key)

    def test_the_declared_handler_is_the_wired_handler(self):
        _parser, _sub, commands = build_parser()
        for decl in SURFACE:
            facet = decl["cli"]
            assert commands[facet["name"]] is facet["manual_handler"]

    def test_parsing_produces_the_documented_dests(self):
        parser, _sub, _commands = build_parser()
        args = parser.parse_args(
            ["sweep-list-items", "SW-1", "--tag", "x", "--archived-only", "--limit", "3"]
        )
        assert args.sweep == "SW-1"
        assert args.tag == "x"
        assert args.archived_only is True
        assert args.all is False
        assert args.limit == 3
        assert args.state is None


class TestHandlerBodiesRunWithTheirNamesResolved:
    """Every one of the nine verbs, end to end, against a real tracker.

    The nine bodies were closures inside `register_cli` and captured `db`,
    `sys`, `argparse`, `format_table`, `_parse_csv` and `_parse_tags` from that
    frame; they are module-level lookups now. A name lost in that move raises
    `NameError` only when the body RUNS, and before this test nothing in the
    repository ran any of them (CB-146).
    """

    def _run(self, monkeypatch, capsys, tmp_path, argv):
        parser, _sub, commands = build_parser()
        args = parser.parse_args(argv)
        monkeypatch.chdir(tmp_path)
        commands[argv[0]](args)
        return capsys.readouterr().out

    def test_all_nine_verbs_execute(self, monkeypatch, capsys, tmp_path):
        db.init_project(str(tmp_path))
        run = lambda argv: self._run(monkeypatch, capsys, tmp_path, argv)  # noqa: E731

        assert "Created: SW-1 (t1)" in run(["sweep-create", "--name", "t1"])
        # A SPACE after the comma, so `_parse_csv`'s `.strip()` is load-bearing:
        # without it the second tag is " y" and the `--tags y` filter below
        # returns nothing.
        assert "Added 2 new items" in run(["sweep-add", "t1", "a", "b", "--tags", "x, y"])

        # `--limit 1` on a two-item sweep: the assertion is the COUNT, because
        # "some item appeared" cannot see `limit=args.limit` replaced by None.
        listing = run(["sweep-next", "t1", "--limit", "1", "--tags", "y"])
        assert "1 remaining" in listing
        assert "a" in listing and "b" not in listing

        assert "state=done" in run(["sweep-mark", "t1", "a"])
        assert "1/2 processed" in run(["sweep-status", "t1"])
        assert "b" in run(["sweep-list-items", "t1"])
        assert "Archived 1 entries" in run(
            ["sweep-archive-items", "t1", "b", "--reason", "stale"]
        )
        # AFTER the archive, so `--all` discriminates: without it the archived
        # entry is hidden, and asserting `--all` on a sweep with nothing archived
        # passes whether the flag is forwarded or not.
        assert "b" not in run(["sweep-list-items", "t1"])
        assert "b" in run(["sweep-list-items", "t1", "--all"])
        assert "b" in run(["sweep-list-items", "t1", "--archived-only"])

        assert "SW-1" in run(["sweep-list", "--all"])
        assert "Archived: SW-1" in run(["sweep-archive", "t1"])

    def test_a_lifecycle_sweep_round_trips_through_the_verbs(
        self, monkeypatch, capsys, tmp_path
    ):
        """`_cmd_sweep_create` reaches `_parse_csv` for two of its arguments,
        and `sweep-mark --state` reaches the transition validator."""
        db.init_project(str(tmp_path))
        run = lambda argv: self._run(monkeypatch, capsys, tmp_path, argv)  # noqa: E731

        out = run(
            [
                "sweep-create",
                "--name",
                "retro",
                "--lifecycle",
                "DETECTED,CONFIRMED,RESOLVED",
                "--terminal-states",
                "RESOLVED",
                "--batch-size",
                "3",
            ]
        )
        assert "Lifecycle: DETECTED -> CONFIRMED -> RESOLVED" in out
        run(["sweep-add", "retro", "i1"])
        assert "state=CONFIRMED" in run(["sweep-mark", "retro", "i1", "--state", "CONFIRMED"])

    def test_a_domain_refusal_still_exits_one_through_the_handler(
        self, monkeypatch, capsys, tmp_path
    ):
        """The `except ValueError -> stderr -> sys.exit(1)` arm, which needs the
        module-level `sys` the closure used to capture."""
        db.init_project(str(tmp_path))
        with pytest.raises(SystemExit) as excinfo:
            self._run(monkeypatch, capsys, tmp_path, ["sweep-status", "NOPE"])
        assert excinfo.value.code == 1
        assert "NOPE" in capsys.readouterr().err


class TestModeSweep:
    """`sweep` was already in both allowlists, so the pilot needed no edit to
    `server.py` or `cli.py` — but nothing proved it, which is how a provider-name
    change would go unnoticed until a client failed."""

    def test_the_registry_yields_the_sweep_provider_under_mode_sweep(self):
        assert [p.name for p in db.get_cli_providers(mode="sweep")] == ["sweep"]
        assert [p.name for p in db.get_tool_providers(mode="sweep")] == ["sweep"]

    def test_the_cli_in_mode_sweep_offers_the_sweep_verbs_and_not_others(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "--mode", "sweep", "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root / "src"), "COLUMNS": "200"},
        )
        assert result.returncode == 0, result.stderr
        for verb in VERB_NAMES:
            assert verb in result.stdout
        assert "reqs-add" not in result.stdout


class TestMetamorphicOnDeclaredFields:
    """Change ONE declared field; exactly that field changes in the surface.

    A functional-dependence check on the CONTENT of the surface, not on its
    presence — a declaration whose fields were inert would pass a presence check
    and fail here. It is NOT an acceptance bar (see the module docstring).
    """

    def _mutated(self, verb, dest, key, value):
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["cli"]["name"] != verb:
                continue
            for arg in decl["cli"]["args"]:
                if arg["flags"][-1].lstrip("-").replace("-", "_") == dest:
                    arg[key] = value
        return clone

    def test_a_changed_help_string_changes_only_that_help(self):
        _p, sub, _c = build_parser()
        before = {a.dest: a.help for a in sub.choices["sweep-list-items"]._actions}
        clone = self._mutated("sweep-list-items", "tag", "help", "CHANGED HELP")
        _p2, sub2, _c2 = build_parser(clone)
        after = {a.dest: a.help for a in sub2.choices["sweep-list-items"]._actions}
        assert after["tag"] == "CHANGED HELP"
        assert {k: v for k, v in after.items() if k != "tag"} == {
            k: v for k, v in before.items() if k != "tag"
        }

    def test_a_changed_nargs_changes_only_that_nargs(self):
        clone = self._mutated("sweep-add", "items", "nargs", "*")
        _p, sub, _c = build_parser(clone)
        assert action_by_dest(sub.choices["sweep-add"], "items").nargs == "*"
        _p2, sub2, _c2 = build_parser()
        assert action_by_dest(sub2.choices["sweep-add"], "items").nargs == "+"

    def test_a_changed_verb_help_changes_only_that_listing(self):
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["cli"]["name"] == "sweep-archive":
                decl["cli"]["help"] = "CHANGED VERB HELP"
        _p, sub, _c = build_parser(clone)
        listing = {p.dest: p.help for p in sub._choices_actions}
        assert listing["sweep-archive"] == "CHANGED VERB HELP"
        assert listing["sweep-list"] == "List sweeps"

    def test_a_changed_declared_default_reaches_a_parsed_namespace(self):
        clone = self._mutated("sweep-list-items", "all", "default", True)
        parser, _sub, _c = build_parser(clone)
        assert parser.parse_args(["sweep-list-items", "SW-1"]).all is True
        parser2, _sub2, _c2 = build_parser()
        assert parser2.parse_args(["sweep-list-items", "SW-1"]).all is False

    def test_a_changed_tool_docstring_changes_only_that_description(self, tracker):
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["mcp"]["name"] == "codesweep_status":
                decl["mcp"]["doc"] = "CHANGED DESCRIPTION.\n"
        by_name = {t.name: t.description for t in listed(build_server(tracker, clone))}
        assert by_name["codesweep_status"] == "CHANGED DESCRIPTION.\n"
        base = {t.name: t.description for t in listed(build_server(tracker))}
        assert {k: v for k, v in by_name.items() if k != "codesweep_status"} == {
            k: v for k, v in base.items() if k != "codesweep_status"
        }

    def test_a_changed_declared_parameter_default_reaches_the_schema(self, tracker):
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["mcp"]["name"] == "codesweep_create":
                for param in decl["mcp"]["params"]:
                    if param["name"] == "default_batch_size":
                        param["default"] = 25
        by_name = {t.name: t for t in listed(build_server(tracker, clone))}
        props = by_name["codesweep_create"].input_schema["properties"]
        assert props["default_batch_size"]["default"] == 25

    def test_removing_one_declaration_removes_exactly_its_surface(self, tracker):
        """One-sided declarations exist, so the arithmetic is per side:
        MCP: N -> N - has_mcp(decl); CLI: K -> K - has_cli(decl)."""
        clone = [d for d in copy.deepcopy(SURFACE) if d["mcp"]["name"] != "codesweep_archive"]
        assert sorted(t.name for t in listed(build_server(tracker, clone))) == [
            n for n in TOOL_NAMES if n != "codesweep_archive"
        ]
        _p, sub, commands = build_parser(clone)
        assert sorted(sub.choices) == [n for n in VERB_NAMES if n != "sweep-archive"]
        assert sorted(commands) == [n for n in VERB_NAMES if n != "sweep-archive"]


class TestRegistrationComesFromTheEmitter:
    """Stub the emitter BEFORE anything registers; the surface must vanish.

    Both directions, because "zero" is worth nothing if the same construction
    yields zero for an unrelated reason. Stated honestly: this proves where
    REGISTRATION comes from and NOT where the surface's CONTENT comes from — a
    three-line emitter dispatching over a table of handler names passes it with
    zero migrated content, which BT-6's seventh review pass reproduced against a
    real module.
    """

    def test_without_the_stub_the_surface_is_there(self, tracker):
        assert len(listed(build_server(tracker))) == 9
        _p, sub, commands = build_parser()
        assert len(sub.choices) == 9 and len(commands) == 9

    def test_with_the_emitter_stubbed_the_surface_is_gone(self, tracker, monkeypatch):
        monkeypatch.setattr(surfacegen, "emit_tools", lambda *a, **k: [])
        monkeypatch.setattr(surfacegen, "emit_cli", lambda *a, **k: [])
        assert listed(build_server(tracker)) == []
        _p, sub, commands = build_parser()
        assert len(sub.choices) == 0
        assert commands == {}
