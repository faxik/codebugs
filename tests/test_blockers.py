"""Tests for codebugs blockers (dependency tracking) layer."""

import os
import sqlite3
import threading

import pytest

from codebugs import blockers, db, findings, reqs


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _add_finding(conn, fid="CB-1", description="test finding", **kw):
    defaults = dict(severity="medium", category="bug", file="src/x.py")
    defaults.update(kw)
    return findings.add_finding(conn, finding_id=fid, description=description, **defaults)


def _add_req(conn, rid="FR-001", description="test requirement", **kw):
    defaults = dict(section="core", priority="should", status="planned")
    defaults.update(kw)
    return reqs.add_requirement(conn, req_id=rid, description=description, **defaults)


# ---------------------------------------------------------------------------
# entity-id detection now lives in entities.py (see tests/test_entities.py)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _normalize_trigger_at
# ---------------------------------------------------------------------------

class TestNormalizeTriggerAt:
    def test_date_only(self):
        result = blockers._normalize_trigger_at("2026-04-10")
        assert result == "2026-04-10T00:00:00Z"

    def test_datetime_utc(self):
        result = blockers._normalize_trigger_at("2026-04-10T14:30:00Z")
        assert result == "2026-04-10T14:30:00Z"

    def test_datetime_with_offset(self):
        result = blockers._normalize_trigger_at("2026-04-10T14:30:00+02:00")
        assert result == "2026-04-10T12:30:00Z"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid date format"):
            blockers._normalize_trigger_at("not-a-date")


# ---------------------------------------------------------------------------
# add_blocker
# ---------------------------------------------------------------------------

class TestAddBlocker:
    def test_entity_resolved(self, conn):
        _add_finding(conn, "CB-1", "blocker bug")
        _add_finding(conn, "CB-2", "blocked bug")
        result = blockers.add_blocker(
            conn, item_id="CB-2", reason="needs CB-1 first", blocked_by="CB-1",
        )
        assert result["item_id"] == "CB-2"
        assert result["blocked_by"] == "CB-1"
        assert result["trigger_type"] == "entity_resolved"
        assert result["is_active"] is True
        assert result["item_description"] == "blocked bug"

    def test_date_trigger(self, conn):
        _add_finding(conn, "CB-1")
        result = blockers.add_blocker(
            conn, item_id="CB-1", reason="wait a week",
            trigger_type="date", trigger_at="2026-04-10",
        )
        assert result["trigger_type"] == "date"
        assert result["trigger_at"] == "2026-04-10T00:00:00Z"

    def test_manual_trigger(self, conn):
        _add_finding(conn, "CB-1")
        result = blockers.add_blocker(
            conn, item_id="CB-1", reason="manual hold",
        )
        assert result["trigger_type"] == "manual"
        assert result["blocked_by"] is None

    def test_cross_entity(self, conn):
        _add_finding(conn, "CB-1", "auth fix")
        _add_req(conn, "FR-001", "auth feature")
        result = blockers.add_blocker(
            conn, item_id="FR-001", reason="needs auth fix", blocked_by="CB-1",
        )
        assert result["item_type"] == "requirement"
        assert result["blocked_by_type"] == "finding"

    def test_validates_item_exists(self, conn):
        with pytest.raises(KeyError, match="Entity not found: CB-99"):
            blockers.add_blocker(conn, item_id="CB-99", reason="nope")

    def test_validates_blocked_by_exists(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(KeyError, match="Blocking entity not found: CB-99"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="nope", blocked_by="CB-99",
            )

    def test_rejects_self_block(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="cannot block itself"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="loop", blocked_by="CB-1",
            )

    def test_rejects_duplicate_entity(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r1", blocked_by="CB-1")
        with pytest.raises(ValueError, match="Duplicate blocker"):
            blockers.add_blocker(conn, item_id="CB-2", reason="r2", blocked_by="CB-1")

    def test_rejects_duplicate_date(self, conn):
        _add_finding(conn, "CB-1")
        blockers.add_blocker(
            conn, item_id="CB-1", reason="r1", trigger_type="date", trigger_at="2026-04-10",
        )
        with pytest.raises(ValueError, match="Duplicate blocker"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="r2", trigger_type="date", trigger_at="2026-04-10",
            )

    def test_entity_resolved_requires_blocked_by(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="blocked_by is required"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="r", trigger_type="entity_resolved",
            )

    def test_date_requires_trigger_at(self, conn):
        _add_finding(conn, "CB-1")
        with pytest.raises(ValueError, match="trigger_at is required"):
            blockers.add_blocker(
                conn, item_id="CB-1", reason="r", trigger_type="date",
            )


# ---------------------------------------------------------------------------
# Dynamic evaluation — is_blocker_satisfied
# ---------------------------------------------------------------------------

class TestDynamicEvaluation:
    def test_entity_resolved_satisfied_when_fixed(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        b = blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        assert b["is_satisfied"] is False

        findings.update_finding(conn, "CB-1", status="fixed")
        result = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True
        assert result["blockers"][0]["is_active"] is False

    def test_entity_resolved_reverts_on_reopen(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        findings.update_finding(conn, "CB-1", status="fixed")
        result = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True

        findings.update_finding(conn, "CB-1", status="open")
        result = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is False
        assert result["blockers"][0]["is_active"] is True

    def test_date_satisfied_when_past(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(
            conn, item_id="CB-1", reason="r",
            trigger_type="date", trigger_at="2020-01-01",
        )
        assert b["is_satisfied"] is True
        assert b["is_active"] is False

    def test_date_not_satisfied_when_future(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(
            conn, item_id="CB-1", reason="r",
            trigger_type="date", trigger_at="2099-12-31",
        )
        assert b["is_satisfied"] is False
        assert b["is_active"] is True

    def test_manual_satisfied_when_resolved(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(conn, item_id="CB-1", reason="manual hold")
        assert b["is_satisfied"] is False

        blockers.resolve_blocker(conn, blocker_id=b["id"], action="resolve")
        result = blockers.query_blockers(conn, item_id="CB-1", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True

    def test_requirement_terminal_statuses(self, conn):
        _add_req(conn, "FR-001", "req1")
        _add_req(conn, "FR-002", "req2")
        blockers.add_blocker(conn, item_id="FR-002", reason="r", blocked_by="FR-001")

        reqs.update_requirement(conn, "FR-001", status="implemented")
        result = blockers.query_blockers(conn, item_id="FR-002", active_only=False)
        assert result["blockers"][0]["is_satisfied"] is True


# ---------------------------------------------------------------------------
# query_blockers
# ---------------------------------------------------------------------------

class TestQueryBlockers:
    def test_active_only_default(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        findings.update_finding(conn, "CB-1", status="fixed")

        active = blockers.query_blockers(conn, item_id="CB-2")
        assert active["total"] == 0

        all_ = blockers.query_blockers(conn, item_id="CB-2", active_only=False)
        assert all_["total"] == 1

    def test_reverse_lookup(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-2", reason="r1", blocked_by="CB-1")
        blockers.add_blocker(conn, item_id="CB-3", reason="r2", blocked_by="CB-1")

        result = blockers.query_blockers(conn, blocked_by="CB-1")
        assert result["total"] == 2
        ids = {b["item_id"] for b in result["blockers"]}
        assert ids == {"CB-2", "CB-3"}

    def test_filter_by_trigger_type(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-1", reason="wait", trigger_type="date", trigger_at="2099-12-31")
        blockers.add_blocker(conn, item_id="CB-2", reason="hold")

        result = blockers.query_blockers(conn, trigger_type="manual")
        assert result["total"] == 1
        assert result["blockers"][0]["item_id"] == "CB-2"


# ---------------------------------------------------------------------------
# check_blockers
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def test_actionable(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        findings.update_finding(conn, "CB-1", status="fixed")

        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 1
        assert result["actionable"][0]["item_id"] == "CB-2"

    def test_partially_unblocked(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-3", reason="r1", blocked_by="CB-1")
        blockers.add_blocker(conn, item_id="CB-3", reason="r2", blocked_by="CB-2")
        findings.update_finding(conn, "CB-1", status="fixed")

        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 0
        assert len(result["partially_unblocked"]) == 1
        assert result["partially_unblocked"][0]["item_id"] == "CB-3"
        assert result["partially_unblocked"][0]["remaining"] == 1

    def test_overdue_date_triggers(self, conn):
        _add_finding(conn, "CB-1")
        blockers.add_blocker(
            conn, item_id="CB-1", reason="old",
            trigger_type="date", trigger_at="2020-01-01",
        )
        result = blockers.check_blockers(conn)
        assert len(result["overdue_date_triggers"]) == 1
        assert result["overdue_date_triggers"][0]["item_id"] == "CB-1"


# ---------------------------------------------------------------------------
# resolve_blocker
# ---------------------------------------------------------------------------

class TestResolveBlocker:
    def test_cancel(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        b = blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        result = blockers.resolve_blocker(conn, blocker_id=b["id"], action="cancel")
        assert result["blocker"]["is_cancelled"] is True
        assert result["remaining_count"] == 0

    def test_resolve_manual(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(conn, item_id="CB-1", reason="hold")

        result = blockers.resolve_blocker(conn, blocker_id=b["id"], action="resolve")
        assert result["blocker"]["is_satisfied"] is True
        assert result["blocker"]["resolved_at"] is not None

    def test_resolve_non_manual_raises(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        b = blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        with pytest.raises(ValueError, match="only valid for manual"):
            blockers.resolve_blocker(conn, blocker_id=b["id"], action="resolve")

    def test_cancel_already_cancelled_raises(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(conn, item_id="CB-1", reason="hold")
        blockers.resolve_blocker(conn, blocker_id=b["id"], action="cancel")

        with pytest.raises(ValueError, match="already cancelled"):
            blockers.resolve_blocker(conn, blocker_id=b["id"], action="cancel")

    def test_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="Blocker not found"):
            blockers.resolve_blocker(conn, blocker_id=9999, action="cancel")


# ---------------------------------------------------------------------------
# Integration: get_unblocked_by
# ---------------------------------------------------------------------------

class TestGetUnblockedBy:
    def test_single_blocker_unblocked(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        findings.update_finding(conn, "CB-1", status="fixed")

        result = blockers.get_unblocked_by(conn, "CB-1", "finding")
        assert len(result) == 1
        assert result[0]["item_id"] == "CB-2"
        assert result[0]["all_blockers_satisfied"] is True

    def test_partial_unblock(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-3", reason="r1", blocked_by="CB-1")
        blockers.add_blocker(conn, item_id="CB-3", reason="r2", blocked_by="CB-2")
        findings.update_finding(conn, "CB-1", status="fixed")

        result = blockers.get_unblocked_by(conn, "CB-1", "finding")
        assert len(result) == 1
        assert result[0]["all_blockers_satisfied"] is False
        assert result[0]["remaining_blockers"] == 1


# ---------------------------------------------------------------------------
# Integration: get_deferred_item_ids / get_deferred_counts
# ---------------------------------------------------------------------------

class TestDeferredHelpers:
    def test_deferred_item_ids(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        _add_finding(conn, "CB-3")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        ids = blockers.get_deferred_item_ids(conn, "finding")
        assert ids == {"CB-2"}

    def test_deferred_counts(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")

        counts = blockers.get_deferred_counts(conn, "finding")
        assert counts["deferred_count"] == 1
        assert counts["overdue_count"] == 0
        assert counts["currently_unblocked_count"] == 0

    def test_deferred_counts_with_resolved(self, conn):
        _add_finding(conn, "CB-1")
        _add_finding(conn, "CB-2")
        blockers.add_blocker(conn, item_id="CB-2", reason="r", blocked_by="CB-1")
        findings.update_finding(conn, "CB-1", status="fixed")

        counts = blockers.get_deferred_counts(conn, "finding")
        assert counts["deferred_count"] == 0
        assert counts["currently_unblocked_count"] == 1


# ---------------------------------------------------------------------------
# Full workflow
# ---------------------------------------------------------------------------

class TestFullWorkflow:
    def test_add_defer_fix_check_reopen(self, conn):
        _add_finding(conn, "CB-1", "auth bug")
        _add_finding(conn, "CB-2", "depends on auth fix")

        # Defer CB-2 on CB-1
        b = blockers.add_blocker(
            conn, item_id="CB-2", reason="depends on auth fix", blocked_by="CB-1",
        )
        assert b["is_active"] is True

        # CB-2 is deferred
        deferred = blockers.get_deferred_item_ids(conn, "finding")
        assert "CB-2" in deferred

        # Fix CB-1
        findings.update_finding(conn, "CB-1", status="fixed")

        # CB-2 is now actionable
        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 1
        assert result["actionable"][0]["item_id"] == "CB-2"

        # CB-2 no longer deferred
        deferred = blockers.get_deferred_item_ids(conn, "finding")
        assert "CB-2" not in deferred

        # Reopen CB-1 — CB-2 is deferred again
        findings.update_finding(conn, "CB-1", status="open")
        deferred = blockers.get_deferred_item_ids(conn, "finding")
        assert "CB-2" in deferred

        result = blockers.check_blockers(conn)
        assert len(result["actionable"]) == 0


class TestDeferredOrdering:
    """CB-20 sibling: the deferred query orders by `kind.sort_col`, a TEXT column.

    Ordering by it directly sorts alphabetically. For findings that put `low`
    above `medium`; for requirements it was a total inversion — `could` first and
    `must` LAST — so the deferred queue recommended the least important work.
    """

    def test_findings_use_declared_severity_precedence(self, conn):
        for i, sev in enumerate(("low", "critical", "medium", "high"), start=1):
            _add_finding(conn, fid=f"CB-{i}", severity=sev)
            blockers.add_blocker(conn, item_id=f"CB-{i}", reason="waiting", trigger_type="manual")

        res = blockers.query_deferred_entities(conn, entity_type="finding", limit=50)
        assert [f["severity"] for f in res["findings"]] == ["critical", "high", "medium", "low"]

    def test_requirements_use_declared_priority_precedence(self, conn):
        """The inverted case: lexically `could` < `must` < `should`."""
        for i, pri in enumerate(("could", "must", "should"), start=1):
            _add_req(conn, rid=f"FR-{i}", priority=pri)
            blockers.add_blocker(conn, item_id=f"FR-{i}", reason="waiting", trigger_type="manual")

        res = blockers.query_deferred_entities(conn, entity_type="requirement", limit=50)
        assert [r["priority"] for r in res["requirements"]] == ["must", "should", "could"]

    def test_limit_keeps_the_most_important_rows(self, conn):
        """The harm: under LIMIT, wrong ordering truncates the rows that matter."""
        for i, pri in enumerate(("could", "could", "must"), start=1):
            _add_req(conn, rid=f"FR-{i}", priority=pri)
            blockers.add_blocker(conn, item_id=f"FR-{i}", reason="waiting", trigger_type="manual")

        res = blockers.query_deferred_entities(conn, entity_type="requirement", limit=1)
        assert [r["priority"] for r in res["requirements"]] == ["must"]

    def test_the_id_filter_still_applies(self, conn):
        """Guards the parameter-splice position: ids, then rank, then limit/offset.

        The rank placeholders sit between the `id IN (...)` list and LIMIT/OFFSET.
        Binding them anywhere else corrupts the id filter itself, not just the order.
        """
        for i, sev in enumerate(("low", "medium"), start=1):
            _add_finding(conn, fid=f"CB-{i}", severity=sev)
            blockers.add_blocker(conn, item_id=f"CB-{i}", reason="waiting", trigger_type="manual")
        _add_finding(conn, fid="CB-9", severity="critical")  # no blocker: must not appear

        res = blockers.query_deferred_entities(conn, entity_type="finding", limit=50)
        assert [f["id"] for f in res["findings"]] == ["CB-2", "CB-1"]
        assert res["total"] == 2


class TestFalseyTriggerTypeDoesNotDisableTheFilter:
    """CB-25, at the site the first sibling sweep missed.

    `query_blockers` already validated `trigger_type` against `TRIGGER_TYPES` — but
    the check sat INSIDE `if trigger_type:`, so a falsey value skipped the guard and
    the condition alike and every blocker came back. The validation being present in
    the body is exactly what made it look safe.

    Found only by sweeping for the SHAPE of the guard rather than for the filter
    names already known to be affected."""

    def _one(self, conn):
        a = _add_finding(conn, fid="CB-1")
        b = _add_finding(conn, fid="CB-2", description="dep")
        blockers.add_blocker(
            conn, item_id=a["id"], blocked_by=b["id"], trigger_type="manual", reason="r"
        )

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_trigger_type_raises_instead_of_returning_everything(self, conn, falsey):
        self._one(conn)
        with pytest.raises(ValueError, match="Invalid trigger_type"):
            blockers.query_blockers(conn, trigger_type=falsey)

    @pytest.mark.parametrize("empty", [None, ""])
    def test_none_and_empty_string_still_mean_no_filter(self, conn, empty):
        self._one(conn)
        assert blockers.query_blockers(conn, trigger_type=empty)["total"] == 1

    def test_valid_trigger_type_still_filters(self, conn):
        self._one(conn)
        assert blockers.query_blockers(conn, trigger_type="manual")["total"] == 1
        assert blockers.query_blockers(conn, trigger_type="date")["total"] == 0


class TestDeferredQueryForwardsDomainFilters:
    """CB-28: the MCP `deferred` path discarded every filter but limit/offset.

    `query(status="deferred", severity="critical")` returned EVERY deferred finding
    and the caller read that as the critical ones — a success payload with the
    caller's arguments discarded, which is CB-15's failure mode reached through
    routing rather than through validation.

    The fix is the shape the 2026-04-04 blockers design already specified: resolve
    the pseudo-status to an id restriction, then let the OWNING DOMAIN apply its own
    filters. Blockers never learns what `severity` or `priority` mean."""

    def _mcp(self, conn, module):
        from contextlib import contextmanager

        @contextmanager
        def factory():
            yield conn

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, *a, **k):
                def deco(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return deco

        m = FakeMCP()
        module.register_tools(m, factory)
        return m.tools

    def _two_deferred_findings(self, conn):
        crit = _add_finding(conn, fid="CB-90", description="crit", severity="critical")
        low = _add_finding(conn, fid="CB-91", description="low", severity="low")
        dep = _add_finding(conn, fid="CB-99", description="dep")
        for x in (crit, low):
            blockers.add_blocker(
                conn, item_id=x["id"], blocked_by=dep["id"],
                trigger_type="entity_resolved", reason="r",
            )
        return crit, low

    def test_severity_filter_applies_to_deferred_findings(self, conn):
        crit, _ = self._two_deferred_findings(conn)
        q = self._mcp(conn, findings)["query"]
        assert q(status="deferred")["total"] == 2
        got = q(status="deferred", severity="critical")
        assert got["total"] == 1
        assert [f["id"] for f in got["findings"]] == [crit["id"]]

    def test_non_matching_filter_yields_nothing_not_everything(self, conn):
        self._two_deferred_findings(conn)
        q = self._mcp(conn, findings)["query"]
        assert q(status="deferred", category="no-such-category")["total"] == 0

    def test_blocker_count_is_still_annotated(self, conn):
        """The design doc requires it and the old path provided it."""
        self._two_deferred_findings(conn)
        q = self._mcp(conn, findings)["query"]
        got = q(status="deferred")
        assert all(f["blocker_count"] == 1 for f in got["findings"])

    def test_declared_severity_precedence_survives_forwarding(self, conn):
        """CB-20's ranked order must not regress: critical before low."""
        self._two_deferred_findings(conn)
        q = self._mcp(conn, findings)["query"]
        assert [f["severity"] for f in q(status="deferred")["findings"]] == ["critical", "low"]

    def test_caller_supplied_ids_intersect_rather_than_being_overwritten(self, conn):
        crit, low = self._two_deferred_findings(conn)
        q = self._mcp(conn, findings)["query"]
        got = q(status="deferred", ids=[low["id"]])
        assert [f["id"] for f in got["findings"]] == [low["id"]]

    def test_priority_filter_applies_to_deferred_requirements(self, conn):
        must = _add_req(conn, rid="FR-90", priority="must")
        _add_req(conn, rid="FR-91", priority="could")
        dep = _add_finding(conn, fid="CB-98", description="dep")
        for r in ("FR-90", "FR-91"):
            blockers.add_blocker(
                conn, item_id=r, blocked_by=dep["id"],
                trigger_type="entity_resolved", reason="r",
            )
        rq = self._mcp(conn, reqs)["reqs_query"]
        assert rq(status="deferred")["total"] == 2
        got = rq(status="deferred", priority="must")
        assert got["total"] == 1
        assert [r["id"] for r in got["requirements"]] == [must["id"]]


class TestDeferredEmptyIntersection:
    """The sharp edge of the CB-28 fix, and the reason it is pinned separately.

    `ids=[]` means "no filter" to every domain query (a CB-25 test pins that). So an
    empty deferred intersection must NOT be forwarded as `ids=[]` — doing so returns
    the WHOLE TABLE, which is CB-28's own defect reappearing inside CB-28's fix,
    exactly as the naive predicate reintroduced CB-25 inside its fix."""

    def _q(self, conn):
        from contextlib import contextmanager

        @contextmanager
        def factory():
            yield conn

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, *a, **k):
                def deco(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return deco

        m = FakeMCP()
        findings.register_tools(m, factory)
        return m.tools["query"]

    def test_no_deferred_entities_at_all_returns_empty_not_everything(self, conn):
        _add_finding(conn, fid="CB-80", description="unblocked")
        _add_finding(conn, fid="CB-81", description="also unblocked")
        got = self._q(conn)(status="deferred")
        assert got["total"] == 0 and got["findings"] == []

    def test_ids_disjoint_from_deferred_set_returns_empty_not_everything(self, conn):
        blocked = _add_finding(conn, fid="CB-82", description="blocked")
        other = _add_finding(conn, fid="CB-83", description="not blocked")
        dep = _add_finding(conn, fid="CB-84", description="dep")
        blockers.add_blocker(
            conn, item_id=blocked["id"], blocked_by=dep["id"],
            trigger_type="entity_resolved", reason="r",
        )
        got = self._q(conn)(status="deferred", ids=[other["id"]])
        assert got["total"] == 0 and got["findings"] == []


class PausingConnection(sqlite3.Connection):
    """Fires a one-shot hook right after ``resolve_blocker``'s row read.

    The findings, reqs, milestones, sweep and merge suites carry twins of this; each
    keys on its own read, and the project deliberately has no ``conftest.py``.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.after_select = None

    def execute(self, sql, *args, **kwargs):
        cur = super().execute(sql, *args, **kwargs)
        if self.after_select and sql.lstrip().startswith("SELECT * FROM blockers WHERE id"):
            hook, self.after_select = self.after_select, None
            hook()
        return cur


class TestResolveBlockerIsOneTransaction:
    """CB-36 batch 2: every guard in ``resolve_blocker`` is decided from a stale read.

    It reads the blocker row, guards on ``cancelled_at``, then branches on
    ``trigger_type`` and ``resolved_at`` to choose which column to write. Unfixed, a
    concurrent ``cancel`` and ``resolve`` both observe ``cancelled_at IS NULL``, both
    pass their guard, and the row ends up **simultaneously cancelled and resolved** —
    a state no serial ordering can produce, with both callers reporting success.
    """

    def _open(self, tmp_project):
        path = os.path.join(tmp_project, ".codebugs", "findings.db")
        c = sqlite3.connect(path, factory=PausingConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def test_a_blocker_cannot_be_cancelled_and_resolved_at_once(self, tmp_project):
        """Here the final STATE does discriminate, unlike most races in this repo.

        Cancel and resolve write different columns, so the illegal interleaving leaves
        a row carrying both timestamps — something neither serial order can produce.
        That makes the assertion a plain state check rather than a "who was refused"
        check, and it still fails against unfixed code.
        """
        seed = db.connect(tmp_project)
        try:
            _add_finding(seed, "CB-1")
            b = blockers.add_blocker(seed, item_id="CB-1", reason="hold")  # manual trigger
            blocker_id = b["id"]
        finally:
            seed.close()

        a = self._open(tmp_project)
        a_read, b_started, b_read = (threading.Event() for _ in range(3))

        def competing_resolver():
            a_read.wait(timeout=10)
            other = self._open(tmp_project)
            other.after_select = b_read.set
            b_started.set()
            try:
                blockers.resolve_blocker(other, blocker_id=blocker_id, action="resolve")
            except Exception:  # noqa: BLE001 — being refused is the fixed behaviour
                pass
            finally:
                b_read.set()
                other.close()

        a.after_select = lambda: (
            a_read.set(),
            b_started.wait(timeout=10),
            b_read.wait(timeout=1.0),
        )

        t = threading.Thread(target=competing_resolver)
        t.start()
        try:
            blockers.resolve_blocker(a, blocker_id=blocker_id, action="cancel")
        finally:
            t.join(timeout=30)
            a.close()
        assert not t.is_alive()

        check = db.connect(tmp_project)
        try:
            row = check.execute(
                "SELECT cancelled_at, resolved_at FROM blockers WHERE id = ?", (blocker_id,)
            ).fetchone()
        finally:
            check.close()

        assert not (row["cancelled_at"] and row["resolved_at"]), (
            "a blocker cannot be both cancelled and resolved — both writers passed a "
            "guard decided from the same stale read"
        )
        assert row["cancelled_at"], "the cancel that won the lock should have landed"

    def test_an_ambient_transaction_is_not_committed_by_resolve_blocker(self, conn):
        _add_finding(conn, "CB-1")
        b = blockers.add_blocker(conn, item_id="CB-1", reason="hold")

        with pytest.raises(RuntimeError, match="caller aborts"):
            with db.txn(conn) as opened:
                assert opened, "the caller owns this transaction"
                blockers.resolve_blocker(conn, blocker_id=b["id"], action="cancel")
                raise RuntimeError("caller aborts after the nested call")

        row = conn.execute(
            "SELECT cancelled_at FROM blockers WHERE id = ?", (b["id"],)
        ).fetchone()
        assert row["cancelled_at"] is None, "the nested call must roll back with its caller"
