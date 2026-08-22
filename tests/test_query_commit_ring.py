"""`query(commit=)` sees the occurrence ring (CB-128).

The `reported_at_commit` COLUMN is frozen at first report (CB-53 (b), ratified via
CB-63); re-observations land in `meta.occurrences[*].reported_at_commit` (CB-43).
`provenance._effective_commit` already reads the ring (newest entry wins — the
staleness question); `query_findings(commit=)` read only the column, so "what was
observed on commit X" missed every re-observation. The semantics here are ANY
observation, not the newest: all observations are equal for that question.

Every ring fixture goes through the REAL write path — `findings.add_finding` twice
with the same identity and different `reported_at_commit` — and is ASSERTED
(`dedup_action == "bumped"`, and the ring read back through `get_finding` carries
the re-observation's commit), so a fixture that silently stopped building a ring
cannot leave a vacuous pass behind. The only direct `UPDATE findings SET meta`
writes are the garbage probes in `TestMalformedNeighbours`, which are not ring
fixtures at all (see their docstrings).
"""

from __future__ import annotations

import json

import pytest

from codebugs import db, findings

FIRST = "a" * 40
SECOND = "b1b2b3b4b5b6b7b8b9b0b1b2b3b4b5b6b7b8b9b0"
THIRD = "c" * 40


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _observe(conn, *, desc, commit, severity="high", cat="bug", file="a.py"):
    """One observation on the REAL path; identity = (cat, file, desc)."""
    return findings.add_finding(
        conn,
        severity=severity,
        category=cat,
        file=file,
        description=desc,
        reported_at_commit=commit,
        new_category=True,
    )


def _ring_card(conn, *, desc, first=FIRST, second=SECOND, **kw):
    """A card first reported on `first` and re-observed on `second`, ASSERTED.

    Returns the finding id. The assertion is the point (CLAUDE.md, harness tests:
    "a test that sets up its own fixture must assert the fixture exists").
    """
    a = _observe(conn, desc=desc, commit=first, **kw)
    b = _observe(conn, desc=desc, commit=second, **kw)
    assert a["dedup_action"] == "created", a
    assert b["dedup_action"] == "bumped" and b["id"] == a["id"], b
    stored = findings.get_finding(conn, a["id"])
    assert stored["reported_at_commit"] == first, "column must stay frozen (CB-53)"
    ring = stored["meta"]["occurrences"]
    assert isinstance(ring, list) and ring, stored["meta"]
    assert [e["reported_at_commit"] for e in ring] == [second], ring
    return a["id"]


def _ids(result):
    assert result["grouped"] is False
    return [f["id"] for f in result["findings"]]


class TestRingIsVisible:
    def test_1_re_observation_commit_finds_card_first_reported_elsewhere(self, conn):
        fid = _ring_card(conn, desc="ring-visible failure text")
        res = findings.query_findings(conn, commit=SECOND)
        assert _ids(res) == [fid]
        assert res["total"] == 1

    def test_2_first_report_commit_still_finds_card_and_single_add_unchanged(self, conn):
        fid = _ring_card(conn, desc="first-report still matches")
        lone = _observe(conn, desc="lone observation, no ring", commit=THIRD)
        assert lone["dedup_action"] == "created"
        assert "occurrences" not in (findings.get_finding(conn, lone["id"])["meta"] or {})

        assert _ids(findings.query_findings(conn, commit=FIRST)) == [fid]
        assert _ids(findings.query_findings(conn, commit=THIRD)) == [lone["id"]]
        # A commit nobody observed matches nothing on either branch.
        assert findings.query_findings(conn, commit="d" * 40)["total"] == 0

    def test_3_prefix_match_on_both_branches(self, conn):
        fid = _ring_card(conn, desc="prefix on both branches")
        assert _ids(findings.query_findings(conn, commit=FIRST[:7])) == [fid]
        assert _ids(findings.query_findings(conn, commit=SECOND[:7])) == [fid]

    def test_4_hex_validation_not_relaxed(self, conn):
        _ring_card(conn, desc="validation stays")
        with pytest.raises(ValueError, match="hex"):
            findings.query_findings(conn, commit="zz")

    def test_5_card_matching_column_and_ring_returned_exactly_once(self, conn):
        """Green on both sides of CB-128 by design: the column alone already
        matched before the fix; what this pins is that adding the ring branch
        (OR + EXISTS) does not turn a double match into a duplicated row."""
        # First report AND re-observation on the same commit: both branches match.
        fid = _ring_card(conn, desc="double match", first=FIRST, second=FIRST)
        res = findings.query_findings(conn, commit=FIRST)
        assert res["total"] == 1
        assert len(res["findings"]) == 1 and res["findings"][0]["id"] == fid

    def test_6_limit_offset_group_by_survive_commit_plus_other_filter(self, conn):
        """Pins the load-bearing parameter order: WHERE params (now two for the
        commit branch), then the severity-rank CASE params, then LIMIT/OFFSET."""
        hi = _ring_card(conn, desc="ordering high", severity="high")
        lo = _ring_card(conn, desc="ordering low", severity="low")
        _ring_card(conn, desc="ordering medium", severity="medium")
        # Bystander on another commit must not be pulled in by a shifted binding.
        _observe(conn, desc="bystander", commit=THIRD, severity="high")

        page1 = findings.query_findings(conn, commit=SECOND, limit=2, offset=0)
        page2 = findings.query_findings(conn, commit=SECOND, limit=2, offset=2)
        assert page1["total"] == 3 and page2["total"] == 3
        assert _ids(page1)[0] == hi  # severity precedence, not alphabetical (CB-20)
        assert _ids(page2) == [lo]

        only_low = findings.query_findings(conn, commit=SECOND, severity="low")
        assert _ids(only_low) == [lo]

        grouped = findings.query_findings(conn, commit=SECOND, group_by="severity")
        assert grouped["grouped"] is True
        assert {g["group_key"]: g["count"] for g in grouped["groups"]} == {
            "high": 1,
            "low": 1,
            "medium": 1,
        }

    def test_7_null_ring_commit_matches_only_the_column(self, conn):
        """Domain `add_finding` does NOT auto-capture HEAD — that lives in the MCP
        wrapper — so `reported_at_commit=None` lands as JSON `null` in the ring."""
        a = _observe(conn, desc="null ring entry", commit=FIRST)
        b = _observe(conn, desc="null ring entry", commit=None)
        assert b["dedup_action"] == "bumped" and b["id"] == a["id"]
        stored = findings.get_finding(conn, a["id"])
        assert stored["reported_at_commit"] == FIRST
        assert [e["reported_at_commit"] for e in stored["meta"]["occurrences"]] == [None]

        assert _ids(findings.query_findings(conn, commit=FIRST)) == [a["id"]]
        assert findings.query_findings(conn, commit=SECOND)["total"] == 0
        # Every prefix of every commit must still miss the null entry.
        assert findings.query_findings(conn, commit="0")["total"] == 0

    def test_9_upper_case_in_ring_and_in_filter(self, conn):
        upper = "ABCDEF" + "0" * 34
        fid = _ring_card(conn, desc="upper case commits", second=upper)
        assert _ids(findings.query_findings(conn, commit="abcdef")) == [fid]
        assert _ids(findings.query_findings(conn, commit="ABCDEF")) == [fid]
        assert _ids(findings.query_findings(conn, commit=upper)) == [fid]


class TestMalformedNeighbours:
    """Garbage on a DIFFERENT row must not take down `query(commit=)`.

    These rows are written with a direct `UPDATE findings SET meta = ?`. That is
    deliberate and is NOT a ring fixture: no write path in the package produces
    malformed meta or a non-object ring element, so the only way to probe the
    reader's robustness is to plant the garbage by hand. The live card beside it
    is still built on the real path and asserted.
    """

    @staticmethod
    def _plant(conn, desc, meta_text):
        victim = _observe(conn, desc=desc, commit=THIRD)
        conn.execute("UPDATE findings SET meta = ? WHERE id = ?", (meta_text, victim["id"]))
        conn.commit()
        stored = conn.execute(
            "SELECT meta FROM findings WHERE id = ?", (victim["id"],)
        ).fetchone()["meta"]
        assert stored == meta_text, "garbage must actually be on disk"
        return victim["id"]

    def test_8a_malformed_meta_on_another_row_does_not_break_commit_filter(self, conn):
        fid = _ring_card(conn, desc="live card next to garbage")
        self._plant(conn, "malformed meta victim", "{not json")
        assert _ids(findings.query_findings(conn, commit=SECOND)) == [fid]
        # The column branch keeps working for the garbage row itself.
        assert findings.query_findings(conn, commit=THIRD)["total"] == 1

    def test_8b_string_ring_element_on_another_row_does_not_break_commit_filter(self, conn):
        fid = _ring_card(conn, desc="live card next to string ring")
        self._plant(conn, "string ring victim", json.dumps({"occurrences": [SECOND]}))
        # A bare string in the ring is not an observation record: it must neither
        # crash the query nor match.
        assert _ids(findings.query_findings(conn, commit=SECOND)) == [fid]

    def test_8c_dict_ring_and_scalar_ring_do_not_match_by_value(self, conn):
        fid = _ring_card(conn, desc="live card next to dict ring")
        self._plant(conn, "dict ring victim", json.dumps({"occurrences": {"x": SECOND}}))
        self._plant(conn, "scalar ring victim", json.dumps({"occurrences": SECOND}))
        assert _ids(findings.query_findings(conn, commit=SECOND)) == [fid]
