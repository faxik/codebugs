"""The generated bench surface, exercised through the REAL server and CLI paths.

WHY THIS FILE EXISTS SEPARATELY FROM `tests/test_bench.py`. That file is the
FLOOR and stays untouched: it reaches the tools as raw functions through a
`FakeMCP` that discards its kwargs, and only two of the four ever get called. It
proves the domain behaviour and it proves nothing about what a client sees. Here
the server is built the way `server.py` builds it — `_NormalizedDescriptions`
around the registrar, `install_strict_arguments` after registration — and the
parser is built the way `cli.py` builds it, so what is asserted is the surface
itself.

WHAT IS DELIBERATELY NOT HERE. No residency rule and no acceptance bar: BT-6's
seven behavioural bars were each shown takeable without doing the work, and the
owner ratified (2026-08-22, Э-15) that an eighth is not written and that the
honesty of the pilot's number is judged by a reader, not by a predicate. These
tests pin BEHAVIOUR the pilot must preserve and BEHAVIOUR the generator must
have; none of them is offered as proof that the surface's content migrated.
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

from codebugs import bench, db, server, surfacegen
from codebugs.bench_surface import SURFACE
from codebugs.server import dedent_docstring

TOOL_NAMES = ["codebench_delete", "codebench_import", "codebench_list", "codebench_query"]
VERB_NAMES = ["bench-delete", "bench-import", "bench-list", "bench-query"]


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
    """A server built through the path `server.py` actually uses.

    `declarations` is injectable so a metamorphic test can register a MODIFIED
    copy of the surface without touching the module-level one.
    """
    raw = MCPServer("codebugs")
    adapter = server._NormalizedDescriptions(raw)
    if declarations is None:
        bench.register_tools(adapter, conn_factory)
    else:
        surfacegen.emit_tools(adapter, conn_factory, declarations)
    server.install_strict_arguments(raw)
    return raw


def build_parser(declarations=None):
    parser = argparse.ArgumentParser(prog="codebugs")
    sub = parser.add_subparsers(dest="command")
    commands: dict = {}
    if declarations is None:
        bench.register_cli(sub, commands)
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


def action_by_dest(parser, dest):
    for action in parser._actions:
        if action.dest == dest:
            return action
    raise AssertionError(f"no action with dest {dest!r}")


class TestGeneratedMcpSurface:
    def test_exactly_four_tools_and_no_duplicates(self, tracker):
        """The late-binding / double-registration trap, asserted as a count.

        A loop that registers inside a closure over the loop variable, or one
        that registers a declaration twice, both show up here and nowhere else.
        """
        tools = listed(build_server(tracker))
        assert sorted(t.name for t in tools) == TOOL_NAMES
        assert len(tools) == len(TOOL_NAMES)

    def test_every_description_is_the_declared_prose_dedented(self, tracker):
        """`__doc__`, not `description=` — see the generator's module docstring.

        If the emitter ever passes `description=`, `_NormalizedDescriptions`
        stops dedenting and this comparison is the thing that notices, because
        the wire golden registers on a raw server and dedents by itself.
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
        `server._NormalizedDescriptions` (CB-73) — and against THIS file's own
        declarations that substitution is indistinguishable, because their prose
        constants sit at column 0 and are already dedented. So the discriminator
        has to be a declaration whose prose is indented: only the `__doc__` form
        reaches the adapter and comes back stripped.
        """
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["mcp"]["name"] == "codebench_list":
                decl["mcp"]["doc"] = "Summary line.\n\n        Indented body.\n        "
        by_name = {t.name: t.description for t in listed(build_server(tracker, clone))}
        assert by_name["codebench_list"] == "Summary line.\n\nIndented body.\n"

    def test_a_valid_call_reaches_the_domain(self, tracker):
        srv = build_server(tracker)
        payload = called(
            srv,
            "codebench_import",
            {"benchmark": "perf", "csv_data": "row,ms\na,1.5\n", "date": "2026-01-01"},
        )
        assert payload["rows"] == 1
        listing = called(srv, "codebench_list", {})
        assert [b["benchmark"] for b in listing["benchmarks"]] == ["perf"]

    def test_a_generated_body_reaches_the_domain(self, tracker):
        """`codebench_query` is the one tool with no handwritten body at all."""
        srv = build_server(tracker)
        called(
            srv,
            "codebench_import",
            {"benchmark": "perf", "csv_data": "row,ms\na,1.5\nb,2.5\n", "date": "2026-01-01"},
        )
        out = called(srv, "codebench_query", {"benchmark": "perf", "rows": ["a"]})
        assert out["runs_matched"] == 1
        assert [r["row_label"] for t in out["data"] for r in t["rows"]] == ["a"]

    def test_a_generated_body_forwards_EVERY_declared_parameter(self, tracker):
        """All nine, by name, with the declared defaults filled in.

        Asserted with a spy rather than by observing a query result: a call that
        exercises two arguments cannot see an emitter that silently drops the
        other seven, and cross-model review named exactly that gap in the
        earlier version of this test.
        """
        seen: dict = {}

        def spy(conn, **kwargs):
            seen.update(kwargs)
            return {"ok": True}

        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["mcp"]["name"] == "codebench_query":
                decl["mcp"]["calls"] = spy
        srv = build_server(tracker, clone)
        called(srv, "codebench_query", {"benchmark": "perf"})

        facet = next(d["mcp"] for d in SURFACE if d["mcp"]["name"] == "codebench_query")
        assert sorted(seen) == sorted(p["name"] for p in facet["params"])
        for param in facet["params"]:
            if "default" in param:
                assert seen[param["name"]] == param["default"], param["name"]
        assert seen["benchmark"] == "perf"

    def test_an_unknown_argument_is_refused(self, tracker):
        """CB-15 over a GENERATED tool: the emitted signature is what the
        strict-argument middleware reads its allowlist from."""
        srv = build_server(tracker)
        middleware = srv.middleware[-1]

        async def call_next(ctx):
            return {"ok": True}

        ctx = pytypes.SimpleNamespace(
            method="tools/call",
            params={"name": "codebench_query", "arguments": {"benchmark": "p", "metric": "ms"}},
        )
        with pytest.raises(MCPError) as excinfo:
            asyncio.run(middleware(ctx, call_next))
        assert "metric" in excinfo.value.message
        assert "metrics" in excinfo.value.message


class TestGeneratedCliSurface:
    def test_exactly_four_verbs(self):
        _parser, sub, commands = build_parser()
        assert sorted(sub.choices) == VERB_NAMES
        assert sorted(commands) == VERB_NAMES

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
                    assert getattr(action, key) == value, (facet["name"], dest, key)

    def test_the_declared_handler_is_the_wired_handler(self):
        _parser, _sub, commands = build_parser()
        for decl in SURFACE:
            facet = decl["cli"]
            assert commands[facet["name"]] is facet["manual_handler"]

    def test_parsing_produces_the_documented_dests(self):
        parser, _sub, _commands = build_parser()
        args = parser.parse_args(["bench-query", "perf", "--date-from", "2026-01-01", "--last-n", "3"])
        assert args.benchmark == "perf"
        assert args.date_from == "2026-01-01"
        assert args.last_n == 3
        assert args.group_by == "row"
        assert args.format == "json"


class TestModeBench:
    """The first test in this repository on `--mode`, for any module.

    `bench` was already in both allowlists, so the pilot needed no edit to
    `server.py` or `cli.py` — but nothing anywhere proved that, which is how a
    provider-name change would have gone unnoticed until a client failed.
    """

    def test_the_registry_yields_the_bench_provider_under_mode_bench(self):
        assert [p.name for p in db.get_cli_providers(mode="bench")] == ["bench"]
        assert [p.name for p in db.get_tool_providers(mode="bench")] == ["bench"]

    def test_the_cli_in_mode_bench_offers_the_bench_verbs_and_not_others(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "--mode", "bench", "--help"],
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

    This is a functional-dependence check on the CONTENT of the surface, not on
    its presence — a declaration whose fields are inert would pass a
    presence check and fail here. It is NOT an acceptance bar (see the module
    docstring): a name table plus a handwritten installer would pass the
    presence half of it too, which is precisely why the owner ratified that no
    eighth bar is written.
    """

    def _mutated(self, verb, dest, key, value):
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            for arg in decl["cli"]["args"]:
                if decl["cli"]["name"] == verb and arg["flags"][-1].lstrip("-").replace("-", "_") == dest:
                    arg[key] = value
        return clone

    def test_a_changed_help_string_changes_only_that_help(self):
        _p, sub, _c = build_parser()
        before = {a.dest: a.help for a in sub.choices["bench-list"]._actions}
        clone = self._mutated("bench-list", "last_n", "help", "CHANGED HELP")
        _p2, sub2, _c2 = build_parser(clone)
        after = {a.dest: a.help for a in sub2.choices["bench-list"]._actions}
        assert after["last_n"] == "CHANGED HELP"
        assert {k: v for k, v in after.items() if k != "last_n"} == {
            k: v for k, v in before.items() if k != "last_n"
        }

    def test_a_changed_choices_list_changes_only_that_choices(self):
        clone = self._mutated("bench-query", "group_by", "choices", ["row", "run", "metric"])
        _p, sub, _c = build_parser(clone)
        assert action_by_dest(sub.choices["bench-query"], "group_by").choices == [
            "row",
            "run",
            "metric",
        ]
        assert action_by_dest(sub.choices["bench-query"], "format").choices == ["json", "csv"]

    def test_a_changed_default_changes_only_that_default(self):
        clone = self._mutated("bench-query", "group_by", "default", "run")
        parser, _sub, _c = build_parser(clone)
        args = parser.parse_args(["bench-query", "perf"])
        assert args.group_by == "run"
        assert args.format == "json"

    def test_a_changed_tool_docstring_changes_only_that_description(self, tracker):
        clone = copy.deepcopy(SURFACE)
        for decl in clone:
            if decl["mcp"]["name"] == "codebench_list":
                decl["mcp"]["doc"] = "CHANGED DESCRIPTION.\n"
        by_name = {t.name: t.description for t in listed(build_server(tracker, clone))}
        assert by_name["codebench_list"] == "CHANGED DESCRIPTION.\n"
        base = {t.name: t.description for t in listed(build_server(tracker))}
        assert {k: v for k, v in by_name.items() if k != "codebench_list"} == {
            k: v for k, v in base.items() if k != "codebench_list"
        }

    def test_removing_one_declaration_removes_exactly_its_surface(self, tracker):
        """One-sided declarations exist, so the arithmetic is per side:
        MCP: N -> N - has_mcp(decl); CLI: K -> K - has_cli(decl)."""
        clone = [d for d in copy.deepcopy(SURFACE) if d["mcp"]["name"] != "codebench_delete"]
        assert sorted(t.name for t in listed(build_server(tracker, clone))) == [
            "codebench_import",
            "codebench_list",
            "codebench_query",
        ]
        _p, sub, commands = build_parser(clone)
        assert sorted(sub.choices) == ["bench-import", "bench-list", "bench-query"]
        assert sorted(commands) == ["bench-import", "bench-list", "bench-query"]


class TestRegistrationComesFromTheEmitter:
    """Stub the emitter BEFORE anything registers; the surface must vanish.

    Both directions, because "zero" is worth nothing if the same construction
    yields zero for an unrelated reason. Stated honestly: this proves where
    REGISTRATION comes from and NOT where the surface's CONTENT comes from — a
    three-line emitter dispatching over a table of handler names passes it with
    zero migrated content, which was reproduced against the real `bench.py` in
    BT-6's seventh review pass.
    """

    def test_without_the_stub_the_surface_is_there(self, tracker):
        assert len(listed(build_server(tracker))) == 4
        _p, sub, commands = build_parser()
        assert len(sub.choices) == 4 and len(commands) == 4

    def test_with_the_emitter_stubbed_the_surface_is_gone(self, tracker, monkeypatch):
        monkeypatch.setattr(surfacegen, "emit_tools", lambda *a, **k: [])
        monkeypatch.setattr(surfacegen, "emit_cli", lambda *a, **k: [])
        assert listed(build_server(tracker)) == []
        _p, sub, commands = build_parser()
        assert len(sub.choices) == 0
        assert commands == {}


class TestGeneratorRefusesMalformedDeclarations:
    def test_a_tool_declaring_neither_body_form_is_refused(self, tracker):
        clone = copy.deepcopy(SURFACE)
        del clone[0]["mcp"]["manual_handler"]
        with pytest.raises(surfacegen.DeclarationError):
            build_server(tracker, clone)

    def test_a_tool_declaring_both_body_forms_is_refused(self, tracker):
        clone = copy.deepcopy(SURFACE)
        clone[0]["mcp"]["calls"] = bench.import_csv
        with pytest.raises(surfacegen.DeclarationError):
            build_server(tracker, clone)

    def test_a_verb_with_no_handler_is_refused(self):
        clone = copy.deepcopy(SURFACE)
        del clone[0]["cli"]["manual_handler"]
        with pytest.raises(surfacegen.DeclarationError):
            build_parser(clone)

    def test_a_duplicate_tool_name_is_refused_before_anything_registers(self, tracker):
        """The refusal must arrive with the server still EMPTY.

        Validating after the emission loop leaves a half-built server behind the
        exception — and for the CLI, argparse raises its own conflict error
        first, so `DeclarationError` never gets to speak. Cross-model review
        found both; this asserts the refusal AND the untouched state.
        """
        clone = copy.deepcopy(SURFACE)
        clone[1]["mcp"]["name"] = clone[0]["mcp"]["name"]
        raw = MCPServer("codebugs")
        with pytest.raises(surfacegen.DeclarationError):
            surfacegen.emit_tools(server._NormalizedDescriptions(raw), tracker, clone)
        assert asyncio.run(raw.list_tools()) == []

    def test_a_duplicate_verb_name_is_refused_before_anything_registers(self):
        clone = copy.deepcopy(SURFACE)
        clone[1]["cli"]["name"] = clone[0]["cli"]["name"]
        parser = argparse.ArgumentParser(prog="codebugs")
        sub = parser.add_subparsers(dest="command")
        commands: dict = {}
        with pytest.raises(surfacegen.DeclarationError):
            surfacegen.emit_cli(sub, commands, clone)
        assert commands == {}
        assert not sub.choices

    def test_a_misspelled_side_key_is_refused_rather_than_dropping_a_surface(self):
        """The fail-open cross-model review reproduced: `cl` for `cli` used to
        emit three verbs at exit 0, and no later check could tell that from a
        legitimately one-sided declaration."""
        clone = copy.deepcopy(SURFACE)
        clone[0]["cl"] = clone[0].pop("cli")
        with pytest.raises(surfacegen.DeclarationError) as excinfo:
            build_parser(clone)
        assert "cl" in str(excinfo.value)

    def test_a_declaration_naming_no_side_is_refused(self):
        clone = copy.deepcopy(SURFACE)
        clone[0].pop("mcp")
        clone[0].pop("cli")
        with pytest.raises(surfacegen.DeclarationError):
            build_parser(clone)

    def test_a_one_sided_declaration_is_still_legal(self, tracker):
        """The refusal above must not cost the asymmetric shape the package has:
        `merge` exposes 5 MCP-only and 3 CLI-only capabilities."""
        clone = copy.deepcopy(SURFACE)
        clone[0].pop("cli")
        assert len(listed(build_server(tracker, clone))) == 4
        _p, sub, commands = build_parser(clone)
        assert sorted(sub.choices) == ["bench-delete", "bench-list", "bench-query"]
        assert sorted(commands) == ["bench-delete", "bench-list", "bench-query"]

    def test_a_misspelled_facet_key_is_refused(self):
        """A misspelled `manual_handler` would otherwise leave a verb bodyless,
        and a misspelled `default` would make a parameter required."""
        clone = copy.deepcopy(SURFACE)
        clone[0]["cli"]["manual_hander"] = clone[0]["cli"].pop("manual_handler")
        with pytest.raises(surfacegen.DeclarationError) as excinfo:
            build_parser(clone)
        assert "manual_hander" in str(excinfo.value)

    def test_a_facet_missing_a_required_key_raises_the_declared_type(self, tracker):
        """`DeclarationError`, not `KeyError` — the contract is what the module
        promises, and half the malformations arriving as `KeyError` makes that
        contract half true."""
        clone = copy.deepcopy(SURFACE)
        del clone[0]["mcp"]["doc"]
        with pytest.raises(surfacegen.DeclarationError):
            build_server(tracker, clone)

    def test_a_verb_missing_its_handler_registers_no_parser_either(self):
        """The handler check is a whole-set precondition too: a later verb's
        missing handler must not leave the earlier verbs' parsers built."""
        clone = copy.deepcopy(SURFACE)
        del clone[-1]["cli"]["manual_handler"]
        parser = argparse.ArgumentParser(prog="codebugs")
        sub = parser.add_subparsers(dest="command")
        commands: dict = {}
        with pytest.raises(surfacegen.DeclarationError):
            surfacegen.emit_cli(sub, commands, clone)
        assert commands == {}
        assert not sub.choices


class TestGeneratorIssuesNoSql:
    def test_the_generator_contains_no_sql(self):
        """The generator is not a domain module: it owns no table and may not
        learn one. A grep, deliberately, because that is the check BT-6 named."""
        source = (Path(surfacegen.__file__)).read_text(encoding="utf-8")
        for word in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert word not in source
