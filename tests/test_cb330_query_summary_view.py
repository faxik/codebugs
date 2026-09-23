"""CB-330: the `view` parameter of the MCP `query` and `recent` tools.

`view="summary"` returns short listing rows — `id`, `severity`, `category`,
`file`, `status`, the first 200 CHARACTERS of `description` and a
`description_truncated` flag, plus `blocker_count` on the `deferred` path and
`loc` under `resolve_anchors=True` — so an agent looking through a list no longer
has to dump a full response to a file and rebuild that table by hand. `"full"`
stays the default and must be the response of today, field for field.

Every test goes through the tool as a server built by `server.build_registrar`
returns it (the production registration stack, CB-310), not through the
projection function alone: the property is what a client receives.

Each property below was checked by a mutation probe when the unit landed — the
code was broken in exactly that property and the named test went red.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from codebugs import blockers, db, findings, server

SUMMARY_KEYS = {
    "id",
    "severity",
    "category",
    "file",
    "status",
    "description",
    "description_truncated",
}

LONG_ASCII = "x" * 300
# Cyrillic on purpose: 200 characters are 400 UTF-8 bytes, so a truncation FLAG
# counted in bytes would read True here while the characters fit (acceptance
# review found that mutant surviving while this string was ASCII).
EXACT_200 = "ж" * 200
# Cyrillic: 2 bytes per character in UTF-8, so a byte cut at 200 would keep
# 100 characters — the mutation this string exists to catch.
LONG_CYRILLIC = "я" * 250
SHORT = "a short description of a small defect"


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _rev(root):
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True
    )
    return out.stdout.decode().strip()


@pytest.fixture()
def tracker(tmp_path):
    """A real repository with the tracker inside it, so anchors can resolve."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.py").write_text(
        "".join(f"VALUE_{i:02d} = compute_the_thing({i})\n" for i in range(1, 13))
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    db.init_project(str(root))
    return root


def _factory(root):
    @contextlib.contextmanager
    def factory():
        conn = db.connect(str(root))
        try:
            yield conn
        finally:
            conn.close()

    return factory


def _mcp(root):
    # Production registration stack, not the bare server (CB-310).
    mcp = MCPServer("cb330-view")
    findings.register_tools(server.build_registrar(mcp), _factory(root))
    return mcp


def _call(mcp, name, **arguments):
    return asyncio.run(mcp.call_tool(name, arguments)).structured_content


def _file(root, description, *, meta=None, severity="low"):
    conn = db.connect(str(root))
    try:
        return findings.add_finding(
            conn,
            severity=severity,
            category="cb330_view",
            file="f.py",
            description=description,
            meta=meta if meta is not None else {"note": "carried in meta"},
            reported_at_commit=_rev(root),
            project_dir=str(root),
            new_category=True,
        )["id"]
    finally:
        conn.close()


def _populate(root):
    """Four cards with the description lengths the truncation rule turns on."""
    return {
        "short": _file(root, SHORT),
        "exact": _file(root, EXACT_200),
        "long": _file(root, LONG_ASCII),
        "cyrillic": _file(root, LONG_CYRILLIC),
    }


def _defer(root, finding_id):
    conn = db.connect(str(root))
    try:
        blockers.add_blocker(conn, item_id=finding_id, reason="waiting on something")
        conn.commit()
    finally:
        conn.close()


def _as_wire(value):
    """What a value looks like after the JSON trip a structured result takes."""
    return json.loads(json.dumps(value))


def _by_id(result):
    return {row["id"]: row for row in result["findings"]}


TOOLS = [
    pytest.param("query", {}, id="query"),
    pytest.param("recent", {"since": "2000-01-01"}, id="recent"),
]


# --- property 1: a closed shape, no `meta` -------------------------------------


class TestTheSummaryRowIsClosed:
    @pytest.mark.parametrize(("tool", "base"), TOOLS)
    def test_a_summary_row_carries_exactly_the_closed_set(self, tracker, tool, base):
        _populate(tracker)
        rows = _call(_mcp(tracker), tool, view="summary", **base)["findings"]
        assert len(rows) == 4
        for row in rows:
            assert "meta" not in row
            assert set(row) == SUMMARY_KEYS, row


# --- property 2: characters, not bytes; the flag makes the cut visible ---------


class TestTheDescriptionIsCutByCharacters:
    @pytest.mark.parametrize(("tool", "base"), TOOLS)
    def test_cut_and_flag(self, tracker, tool, base):
        ids = _populate(tracker)
        mcp = _mcp(tracker)
        summary = _by_id(_call(mcp, tool, view="summary", **base))

        assert summary[ids["short"]]["description"] == SHORT
        assert summary[ids["short"]]["description_truncated"] is False

        # The boundary: exactly 200 characters is not truncated.
        assert summary[ids["exact"]]["description"] == EXACT_200
        assert summary[ids["exact"]]["description_truncated"] is False

        assert summary[ids["long"]]["description"] == LONG_ASCII[:200]
        assert summary[ids["long"]]["description_truncated"] is True

        cyr = summary[ids["cyrillic"]]
        assert len(cyr["description"]) == 200
        assert cyr["description"] == LONG_CYRILLIC[:200]
        assert cyr["description_truncated"] is True


# --- property 3: the response's own keys survive in both views -----------------


class TestTheResponseKeysAreKept:
    @pytest.mark.parametrize(
        ("tool", "base", "keys"),
        [
            pytest.param(
                "query", {}, {"grouped", "total", "limit", "offset", "findings"}, id="query"
            ),
            pytest.param(
                "recent",
                {"since": "2000-01-01"},
                {"total", "limit", "offset", "since", "status", "findings"},
                id="recent",
            ),
        ],
    )
    def test_same_keys_and_same_values_outside_the_rows(self, tracker, tool, base, keys):
        _populate(tracker)
        mcp = _mcp(tracker)
        full = _call(mcp, tool, view="full", limit=3, offset=1, **base)
        summary = _call(mcp, tool, view="summary", limit=3, offset=1, **base)
        assert set(full) == keys
        assert set(summary) == keys
        for key in keys - {"findings"}:
            assert summary[key] == full[key], key
        assert [r["id"] for r in summary["findings"]] == [r["id"] for r in full["findings"]]

    def test_a_grouped_response_is_not_touched_by_the_view(self, tracker):
        _populate(tracker)
        mcp = _mcp(tracker)
        full = _call(mcp, "query", group_by="severity", view="full")
        summary = _call(mcp, "query", group_by="severity", view="summary")
        assert full["grouped"] is True
        assert summary == full


# --- property 4: the `deferred` branch, both of its exits ----------------------


class TestTheDeferredBranch:
    def test_the_ordinary_path_keeps_blocker_count(self, tracker):
        ids = _populate(tracker)
        _defer(tracker, ids["long"])
        mcp = _mcp(tracker)
        full = _call(mcp, "query", status="deferred", view="full")
        summary = _call(mcp, "query", status="deferred", view="summary")
        assert [r["id"] for r in summary["findings"]] == [ids["long"]]
        row = summary["findings"][0]
        assert row["blocker_count"] == full["findings"][0]["blocker_count"] == 1
        assert set(row) == SUMMARY_KEYS | {"blocker_count"}

    def test_the_early_empty_return_has_the_same_shape_in_both_views(self, tracker):
        # No card is deferred, so the branch returns before `query_findings`.
        _populate(tracker)
        mcp = _mcp(tracker)
        full = _call(mcp, "query", status="deferred", view="full")
        summary = _call(mcp, "query", status="deferred", view="summary")
        assert full["findings"] == []
        assert summary == full


# --- property 5: an unknown view is refused, in this package's words -----------


class TestAnUnknownViewIsRefused:
    @pytest.mark.parametrize(
        ("tool", "base"),
        [
            *TOOLS,
            # The short circuit, on a tracker with NO deferred card (CB-196):
            # the refusal must not depend on what the tracker holds.
            pytest.param("query", {"status": "deferred"}, id="query-deferred-empty"),
            # The GROUPED short circuit of the same branch returns directly and
            # never reaches the projection, so only the top-of-body guard can
            # refuse there (Codex review, round 1).
            pytest.param(
                "query",
                {"status": "deferred", "group_by": "severity"},
                id="query-deferred-empty-grouped",
            ),
        ],
    )
    def test_refused_naming_the_accepted_values(self, tracker, tool, base):
        _populate(tracker)
        with pytest.raises(ToolError) as caught:
            _call(_mcp(tracker), tool, view="compact", **base)
        text = str(caught.value)
        assert "Unknown view 'compact'" in text
        assert "'full'" in text and "'summary'" in text

    def test_a_wrong_type_stays_with_the_validation_library(self, tracker):
        """CB-326's rule, unchanged: a wrong TYPE alone is the library's to refuse."""
        with pytest.raises(ToolError) as caught:
            _call(_mcp(tracker), "query", view=1)
        assert "Unknown view" not in str(caught.value)

    def test_the_published_schema_names_the_values(self, tracker):
        tools = {t.name: t for t in asyncio.run(_mcp(tracker).list_tools())}
        for name in ("query", "recent"):
            prop = tools[name].input_schema["properties"]["view"]
            assert prop["enum"] == ["full", "summary"]
            assert prop["default"] == "full"


# --- property 6: `full` is today's response, field for field --------------------


class TestFullIsTodaysResponse:
    def test_query_full_and_no_view_equal_the_domain_result(self, tracker):
        _populate(tracker)
        mcp = _mcp(tracker)
        conn = db.connect(str(tracker))
        try:
            expected = _as_wire(findings.query_findings(conn, limit=None))
        finally:
            conn.close()
        assert _call(mcp, "query") == expected
        assert _call(mcp, "query", view="full") == expected
        assert all("meta" in row for row in expected["findings"])

    def test_query_deferred_full_equals_the_domain_result_plus_blocker_count(self, tracker):
        ids = _populate(tracker)
        _defer(tracker, ids["short"])
        _defer(tracker, ids["cyrillic"])
        mcp = _mcp(tracker)
        conn = db.connect(str(tracker))
        try:
            deferred_ids, counts = blockers.deferred_ids_and_counts(conn, "finding")
            expected = findings.query_findings(conn, ids=deferred_ids, limit=None)
        finally:
            conn.close()
        for row in expected["findings"]:
            row["blocker_count"] = counts.get(row["id"], 0)
        expected = _as_wire(expected)
        assert len(expected["findings"]) == 2
        assert _call(mcp, "query", status="deferred") == expected
        assert _call(mcp, "query", status="deferred", view="full") == expected

    def test_recent_full_and_no_view_equal_the_domain_result(self, tracker):
        _populate(tracker)
        mcp = _mcp(tracker)
        conn = db.connect(str(tracker))
        try:
            expected = _as_wire(findings.recent_findings(conn, since="2000-01-01"))
        finally:
            conn.close()
        assert _call(mcp, "recent", since="2000-01-01") == expected
        assert _call(mcp, "recent", since="2000-01-01", view="full") == expected


# --- property 8: `loc` is `_anchor_cell`'s answer, and only when asked ---------


class TestLocFollowsResolveAnchors:
    def test_loc_is_the_anchor_cell_of_the_same_row(self, tracker):
        moved = _file(
            tracker,
            "line four of f.py is wrong, described at length to avoid dedup",
            meta={"line": 4},
        )
        unanchored = _file(tracker, "a description naming no site at all")
        _git(tracker, "mv", "f.py", "moved.py")
        _git(tracker, "commit", "-qm", "move it")

        mcp = _mcp(tracker)
        full = _by_id(_call(mcp, "query", resolve_anchors=True, view="full"))
        summary = _by_id(_call(mcp, "query", resolve_anchors=True, view="summary"))

        # Two different answers, so a projection that ignored the resolution
        # (or the stored state) could not pass both.
        assert summary[moved]["loc"] == "moved_file"
        assert summary[unanchored]["loc"] == full[unanchored]["anchor"]["state"]
        assert summary[moved]["loc"] != summary[unanchored]["loc"]
        for fid, row in summary.items():
            assert row["loc"] == findings._anchor_cell(full[fid]["anchor"])
            assert set(row) == SUMMARY_KEYS | {"loc"}

    def test_no_loc_key_without_resolve_anchors(self, tracker):
        _file(tracker, "line four again, a different card", meta={"line": 4})
        rows = _call(_mcp(tracker), "query", view="summary")["findings"]
        assert rows and all("loc" not in row for row in rows)
