"""Surface contract for grouping.py: three reports reachable from MCP and CLI (CB-127).

The domain logic is covered by ``tests/test_grouping.py``; this file pins the
WRAPPERS. The load-bearing property is that each wrapper is a THIN forward —
every parameter a caller names arrives in the domain function's kwargs with
exactly that value (CB-28's "a declared argument must reach its query"), on
both surfaces, with no logic in between. That is tested behaviourally, with a
recording stub, because a structural read cannot see a dropped kwarg.

Async work runs through ``asyncio.run`` inside sync tests, as in
``test_server.py`` — the project has no pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from contextlib import contextmanager

import pytest
from mcp.shared.exceptions import MCPError

from codebugs import cli, db, findings, grouping, server
from tests.test_server import _Recorder, _ctx, _server_with_middleware

TOOLS = {"grouping_citations", "grouping_tags", "grouping_filing"}

# (tool name, CLI verb, domain function name, one NON-DEFAULT value per parameter)
CASES = [
    (
        "grouping_citations",
        "grouping-citations",
        "citation_report",
        {
            "status": "open",
            "category": "correctness",
            "hub_degree": 7,
            "component_limit": 4,
            "member_limit": 3,
            "anchor_limit": 2,
            "orphan_limit": 1,
        },
    ),
    (
        "grouping_tags",
        "grouping-tags",
        "tag_report",
        {
            "status": "open",
            "category": "correctness",
            "min_pair_count": 5,
            "tag_limit": 6,
            "pair_limit": 7,
        },
    ),
    (
        "grouping_filing",
        "grouping-filing",
        "filing_report",
        {
            "status": "open",
            "category": "correctness",
            "lineage_limit": 2,
            "event_limit": 3,
        },
    ),
]


@pytest.fixture
def tracker(tmp_path):
    project = str(tmp_path)
    db.init_project(project)

    @contextmanager
    def _conn():
        conn = db.connect(project)
        try:
            yield conn
        finally:
            conn.close()

    _conn.project = project
    return _conn


class _Capture:
    """A stand-in registrar: collects the wrapper functions register_tools defines."""

    def __init__(self):
        self.fns = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.fns[fn.__name__] = fn
            return fn

        return deco


def _wrappers(tracker):
    cap = _Capture()
    grouping.register_tools(cap, tracker)
    return cap.fns


def _stub(monkeypatch, fn_name, result=None):
    calls = []

    def stub(conn, **kw):
        calls.append(kw)
        return result if result is not None else {"populations": ["stub"]}

    monkeypatch.setattr(grouping, fn_name, stub)
    return calls


def _call(mcp, name, arguments):
    async def go():
        return await mcp.call_tool(name, arguments)

    return asyncio.run(go())


def _cli(monkeypatch, tracker, argv):
    monkeypatch.setattr(sys, "argv", ["codebugs", "--tracker-root", tracker.project, *argv])
    cli.main()


def _cli_argv(values):
    out = []
    for k, v in values.items():
        out += [f"--{k.replace('_', '-')}", str(v)]
    return out


def add(conn, description, **kw):
    kw.setdefault("severity", "medium")
    kw.setdefault("category", "correctness")
    kw.setdefault("file", "a.py")
    return findings.add_finding(conn, description=description, **kw, new_category=True)["id"]


# --- 1. Thin forwarding -------------------------------------------------------


class TestThinForwarding:
    @pytest.mark.parametrize("tool, verb, fn_name, values", CASES, ids=[c[0] for c in CASES])
    def test_mcp_delivers_every_parameter_verbatim(self, tracker, monkeypatch, tool, verb,
                                                   fn_name, values):
        calls = _stub(monkeypatch, fn_name)
        mcp, _ = _server_with_middleware(tracker, mode="grouping")
        _call(mcp, tool, values)
        assert calls == [values]

    @pytest.mark.parametrize("tool, verb, fn_name, values", CASES, ids=[c[1] for c in CASES])
    def test_cli_delivers_every_parameter_verbatim(self, tracker, monkeypatch, capsys, tool,
                                                   verb, fn_name, values):
        calls = _stub(monkeypatch, fn_name)
        _cli(monkeypatch, tracker, [verb, *_cli_argv(values), "--json"])
        assert calls == [values]
        assert json.loads(capsys.readouterr().out) == {"populations": ["stub"]}

    @pytest.mark.parametrize("tool, verb, fn_name, values", CASES, ids=[c[0] for c in CASES])
    def test_wrapper_signature_equals_domain_keyword_signature(self, tracker, tool, verb,
                                                               fn_name, values):
        domain = inspect.signature(getattr(grouping, fn_name))
        expected = {
            p.name: p.default
            for p in domain.parameters.values()
            if p.kind is inspect.Parameter.KEYWORD_ONLY
        }
        wrapper = inspect.signature(_wrappers(tracker)[tool])
        got = {p.name: p.default for p in wrapper.parameters.values()}
        assert got == expected
        # and the case table above names every parameter, so a new one cannot
        # slip past the forwarding test unexercised
        assert set(values) == set(expected)

    def test_wrappers_and_cli_issue_no_sql(self, tracker):
        for name, fn in _wrappers(tracker).items():
            assert ".execute(" not in inspect.getsource(fn), name
        assert ".execute(" not in inspect.getsource(grouping.register_cli)


# --- 2. Three hardcoded lists + mode isolation ---------------------------------


def test_grouping_is_registered_in_all_three_hardcoded_lists():
    """There is no discovery. Omitting any one of these ships a silently absent feature."""
    assert "grouping" in inspect.getsource(db._ensure_modules_loaded)
    assert "grouping" in server.SERVER_NAMES
    assert "grouping" in inspect.getsource(cli.main)


def test_mode_isolation(tracker):
    providers = db.get_tool_providers(mode="grouping")
    assert [p.name for p in providers] == ["grouping"]
    mcp, _ = _server_with_middleware(tracker, mode="grouping")
    assert {t.name for t in asyncio.run(mcp.list_tools())} == TOOLS
    mcp_all, _ = _server_with_middleware(tracker, mode="all")
    assert TOOLS <= {t.name for t in asyncio.run(mcp_all.list_tools())}


# --- 3. Zero SQL in the module --------------------------------------------------


def test_module_issues_no_sql():
    assert "execute(" not in inspect.getsource(grouping)


# --- 4. Strict arguments --------------------------------------------------------


def test_typo_argument_is_refused(tracker):
    _, mw = _server_with_middleware(tracker, mode="grouping")
    with pytest.raises(MCPError):
        asyncio.run(mw(_ctx("grouping_citations", {"hub_degre": 3}), _Recorder()))


# --- 5. Empty population --------------------------------------------------------


class TestEmptyPopulation:
    def test_mcp_reports_zeros(self, tracker):
        mcp, _ = _server_with_middleware(tracker, mode="grouping")
        c = _call(mcp, "grouping_citations", {}).structured_content
        assert c["populations"] and c["components"] == [] and c["components_total"] == 0
        t = _call(mcp, "grouping_tags", {}).structured_content
        assert t["populations"] and t["tags"] == [] and t["tags_total"] == 0
        f = _call(mcp, "grouping_filing", {}).structured_content
        assert f["populations"] and f["lineages"] == [] and f["lineages_total"] == 0

    @pytest.mark.parametrize(
        "verb, marker",
        [
            ("grouping-citations", "No citation components."),
            ("grouping-tags", "No tags."),
            ("grouping-filing", "No lineages."),
        ],
    )
    def test_cli_says_so_explicitly(self, tracker, monkeypatch, capsys, verb, marker):
        _cli(monkeypatch, tracker, [verb])
        out = capsys.readouterr().out
        assert marker in out
        assert "populations=" in out


# --- 6. Errors --------------------------------------------------------------------


class TestErrors:
    @pytest.mark.parametrize(
        "argv",
        [
            ["grouping-citations", "--hub-degree", "-1"],
            ["grouping-tags", "--min-pair-count", "-1"],
            ["grouping-filing", "--event-limit", "-1"],
        ],
    )
    def test_cli_negative_limit_is_one_line_exit_1(self, tracker, monkeypatch, capsys, argv):
        with pytest.raises(SystemExit) as excinfo:
            _cli(monkeypatch, tracker, argv)
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert err.startswith("Error: ") and err.count("\n") == 1

    def test_mcp_negative_limit_propagates(self, tracker):
        mcp, _ = _server_with_middleware(tracker, mode="grouping")
        with pytest.raises(Exception, match="must be >= 0"):
            _call(mcp, "grouping_tags", {"min_pair_count": -1})


# --- 7. hub_degree=None is reachable from the CLI ---------------------------------


def test_cli_can_disable_hubs(tracker, monkeypatch, capsys):
    calls = _stub(monkeypatch, "citation_report")
    _cli(monkeypatch, tracker, ["grouping-citations", "--hub-degree", "none", "--json"])
    assert calls[0]["hub_degree"] is None


# --- 8. A real run through MCP ----------------------------------------------------


def test_two_cards_citing_each_other_form_one_component(tracker):
    with tracker() as conn:
        a = add(conn, "first card, see the second one")
        b = add(conn, f"second card, duplicates {a}")
        findings.update_finding(conn, a, append_note=f"mirror of {b}")
    mcp, _ = _server_with_middleware(tracker, mode="grouping")
    res = _call(mcp, "grouping_citations", {}).structured_content
    assert res["components_total"] == 1
    assert {m["id"] for m in res["components"][0]["members"]} == {a, b}
