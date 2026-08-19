"""CB-26: a terminal source entity must not stay live in a derived queue.

Routing was a one-time act at add time, so a resolved finding kept its `open`
milestone item forever: 19 of 23 live `stream/triage` rows pointed at already-fixed
findings and `pull_next` could hand an agent finished work.

Two mechanisms are under test, and they fail differently on purpose — the hook
keeps the STORED row honest, the defensive filter makes the guarantee hold even
when a writer bypasses the hook.
"""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import db, entities, findings, milestones, reqs, types
from codebugs.milestones._schema import TERMINAL_ITEM_OUTCOME


@pytest.fixture
def conn(tmp_path):
    db.init_project(str(tmp_path))
    c = db.connect(str(tmp_path))
    yield c
    c.close()


def _add_finding(conn, fid="CB-1", **kw):
    defaults = dict(severity="medium", category="bug", file="src/x.py")
    defaults.update(kw)
    return findings.add_finding(conn, finding_id=fid, description="bug", **defaults)


def _item(conn, ref):
    row = conn.execute(
        "SELECT * FROM milestone_items WHERE item_ref = ? ORDER BY id DESC LIMIT 1", (ref,)
    ).fetchone()
    return dict(row) if row else None


def _stale_row(conn, ref):
    """Reproduce a hook BYPASS: resolve the finding, then force the item back open
    the way `set_item_status` / `release_item(abandoned)` / the importers do."""
    _add_finding(conn, ref)
    findings.update_finding(conn, ref, status="fixed")
    conn.execute(
        "UPDATE milestone_items SET status='open', done_at=NULL WHERE item_ref=?", (ref,)
    )
    conn.commit()


def _audit_rows(conn, ref, action="reconcile"):
    return conn.execute(
        "SELECT * FROM milestone_audit WHERE item_ref = ? AND action = ?", (ref, action)
    ).fetchall()


# ---------------------------------------------------------------------------
# The eager hook
# ---------------------------------------------------------------------------

class TestTerminalProjection:
    def test_fixed_finding_closes_its_triage_item(self, conn):
        _add_finding(conn, "CB-1")
        assert _item(conn, "CB-1")["status"] == "open"

        findings.update_finding(conn, "CB-1", status="fixed")

        item = _item(conn, "CB-1")
        assert item["status"] == "done"
        assert item["done_at"] is not None
        assert len(_audit_rows(conn, "CB-1")) == 1

    @pytest.mark.parametrize("status", ["not_a_bug", "wont_fix"])
    def test_discarded_finding_dismisses_its_item(self, conn, status):
        _add_finding(conn, "CB-2")
        findings.update_finding(conn, "CB-2", status=status)

        item = _item(conn, "CB-2")
        assert item["status"] == "dismissed"
        # `done_at` records completion, not mere closure.
        assert item["done_at"] is None

    def test_non_terminal_status_leaves_the_item_alone(self, conn):
        _add_finding(conn, "CB-3")
        findings.update_finding(conn, "CB-3", status="in_progress")
        assert _item(conn, "CB-3")["status"] == "open"

    def test_terminal_to_terminal_remaps(self, conn):
        """`fixed -> wont_fix` must move `done -> dismissed`.

        A "skip rows already terminal" filter passes every other test here and
        silently fails this one, which is why the predicate is `status != target`.
        """
        _add_finding(conn, "CB-4")
        findings.update_finding(conn, "CB-4", status="fixed")
        assert _item(conn, "CB-4")["status"] == "done"

        findings.update_finding(conn, "CB-4", status="wont_fix")
        assert _item(conn, "CB-4")["status"] == "dismissed"

    def test_requirements_project_too(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="r", section="core")
        milestones.add_milestone_item(
            conn, milestone_id="stream/triage", item_kind="requirement", item_ref="FR-001",
        )
        reqs.update_requirement(conn, "FR-001", status="implemented")
        assert _item(conn, "FR-001")["status"] == "done"

    def test_obsolete_requirement_is_dismissed(self, conn):
        reqs.add_requirement(conn, req_id="FR-002", description="r", section="core")
        milestones.add_milestone_item(
            conn, milestone_id="stream/triage", item_kind="requirement", item_ref="FR-002",
        )
        reqs.update_requirement(conn, "FR-002", status="obsolete")
        assert _item(conn, "FR-002")["status"] == "dismissed"


class TestScopeAndIsolation:
    def test_external_row_sharing_the_ref_is_untouched(self, conn):
        """`(bug, CB-5)` and `(external, CB-5)` are both legal rows.

        `_validate_item_ref` skips validation for externals and UNIQUE includes
        `item_kind`, so matching on `item_ref` alone would close a row that is not
        a projection of the finding at all.
        """
        _add_finding(conn, "CB-5")
        milestones.add_milestone_item(
            conn, milestone_id="stream/maintenance", item_kind="external", item_ref="CB-5",
        )
        findings.update_finding(conn, "CB-5", status="fixed")

        ext = conn.execute(
            "SELECT status FROM milestone_items WHERE item_kind='external' AND item_ref='CB-5'"
        ).fetchone()
        assert ext["status"] == "open"

    def test_deferred_items_are_not_closed(self, conn):
        """Deferral is a record, and no queue returns a deferred row anyway."""
        _add_finding(conn, "CB-6")
        conn.execute("UPDATE milestone_items SET status='deferred' WHERE item_ref='CB-6'")
        conn.commit()

        findings.update_finding(conn, "CB-6", status="fixed")
        assert _item(conn, "CB-6")["status"] == "deferred"

    def test_release_milestone_items_are_left_to_the_close_gate(self, conn):
        """The hook is stream-scoped so it cannot weaken `milestone_close`.

        `milestone_close`'s unfinished check reads only the item status, and
        `done_commit` is never a gate, so auto-marking a release item `done` would
        let a release close over a missed integration step.
        """
        _add_finding(conn, "CB-7")
        milestones.add_milestone_item(
            conn, milestone_id="release/1.1", item_kind="bug", item_ref="CB-7",
        )
        findings.update_finding(conn, "CB-7", status="fixed")

        rel = conn.execute(
            "SELECT status FROM milestone_items "
            "WHERE milestone_id='release/1.1' AND item_ref='CB-7'"
        ).fetchone()
        assert rel["status"] == "open"
        with pytest.raises(ValueError, match="unfinished"):
            milestones.milestone_close(conn, id="release/1.1")


class TestCapacityIsReleased:
    def test_pulled_item_releases_its_slot(self, conn):
        """Closing a pulled item without decrementing would leak the agent's slot
        permanently — the fix's own edge case."""
        _add_finding(conn, "CB-8")
        pulled = milestones.pull_next(
            conn, agent_id="agent-a", capacity={"triage": 1, "small": 1, "large": 1},
        )
        assert pulled is not None and pulled["item_ref"] == "CB-8"
        held = conn.execute(
            "SELECT triage_held FROM agent_capacity WHERE agent_id='agent-a'"
        ).fetchone()
        assert held["triage_held"] == 1

        findings.update_finding(conn, "CB-8", status="fixed")

        item = _item(conn, "CB-8")
        assert item["status"] == "done"
        assert item["assigned_agent"] is None
        held = conn.execute(
            "SELECT triage_held FROM agent_capacity WHERE agent_id='agent-a'"
        ).fetchone()
        assert held["triage_held"] == 0


class TestIdempotenceAndAtomicity:
    def test_second_terminal_write_adds_no_second_audit_row(self, conn):
        _add_finding(conn, "CB-9")
        findings.update_finding(conn, "CB-9", status="fixed")
        findings.update_finding(conn, "CB-9", status="fixed")
        assert len(_audit_rows(conn, "CB-9")) == 1

    def test_item_change_rolls_back_with_the_caller(self, conn):
        """Atomicity must be induced in the CALLER's frame.

        Raising inside the hook proves nothing: `run_status_change_hooks` swallows,
        so the transaction would commit regardless.
        """
        _add_finding(conn, "CB-10")
        with pytest.raises(RuntimeError):
            with db.txn(conn):
                findings.update_finding(conn, "CB-10", status="fixed")
                raise RuntimeError("caller aborts")

        assert _item(conn, "CB-10")["status"] == "open"
        assert conn.execute(
            "SELECT status FROM findings WHERE id='CB-10'"
        ).fetchone()["status"] == "open"

    def test_triage_dismiss_still_works_and_does_not_double_write(self, conn):
        """Non-regression: `triage_dismiss` closes the item, then propagates to the
        finding, which now fires the hook."""
        _add_finding(conn, "CB-11")
        milestones.triage_dismiss(conn, bug_id="CB-11", reason="not real")

        assert _item(conn, "CB-11")["status"] == "dismissed"
        assert conn.execute(
            "SELECT status FROM findings WHERE id='CB-11'"
        ).fetchone()["status"] == "not_a_bug"
        assert len(_audit_rows(conn, "CB-11")) == 0  # dismissal audited as `dismiss`


# ---------------------------------------------------------------------------
# The defensive filter — what makes the invariant actually hold
# ---------------------------------------------------------------------------

class TestDefensiveFiltering:
    def test_triage_inbox_hides_a_terminal_source(self, conn):
        _stale_row(conn, "CB-12")
        assert conn.execute(
            "SELECT status FROM milestone_items WHERE item_ref='CB-12'"
        ).fetchone()["status"] == "open"
        assert [i["item_ref"] for i in milestones.triage_inbox(conn)] == []

    def test_pull_next_refuses_a_terminal_source(self, conn):
        _stale_row(conn, "CB-13")
        assert milestones.pull_next(
            conn, agent_id="a", capacity={"triage": 5, "small": 5, "large": 5},
        ) is None

    def test_limit_counts_live_rows_only(self, conn):
        """The LIMIT is applied after filtering. Pushing it into SQL would return
        fewer than `limit` live rows whenever stale ones sort ahead."""
        _stale_row(conn, "CB-14")
        _add_finding(conn, "CB-15")
        assert [i["item_ref"] for i in milestones.triage_inbox(conn, limit=1)] == ["CB-15"]

    def test_live_items_are_still_returned(self, conn):
        _add_finding(conn, "CB-16")
        assert [i["item_ref"] for i in milestones.triage_inbox(conn)] == ["CB-16"]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

class TestBackfill:
    def test_dry_run_reports_without_writing(self, conn):
        _stale_row(conn, "CB-17")
        result = milestones.reconcile_all(conn)
        assert result["applied"] is False
        assert result["candidates"] == 1
        assert _item(conn, "CB-17")["status"] == "open"

    def test_apply_closes_exactly_the_stale_rows(self, conn):
        _stale_row(conn, "CB-18")
        _add_finding(conn, "CB-19")  # live, must survive

        result = milestones.reconcile_all(conn, apply=True)
        assert result["applied"] is True and result["candidates"] == 1
        assert _item(conn, "CB-18")["status"] == "done"
        assert _item(conn, "CB-19")["status"] == "open"

    def test_second_run_is_a_no_op(self, conn):
        _stale_row(conn, "CB-20")
        milestones.reconcile_all(conn, apply=True)
        assert milestones.reconcile_all(conn, apply=True)["candidates"] == 0


# ---------------------------------------------------------------------------
# Ratchets — these are CI drift gates, NOT regression tests. Neither can fail
# by reverting the fix; both fail when someone extends a vocabulary without
# deciding its projection.
# ---------------------------------------------------------------------------

class TestTerminalOutcomeMapIsComplete:
    def test_every_terminal_status_has_a_declared_projection(self):
        assert set(TERMINAL_ITEM_OUTCOME[types.ENTITY_FINDING]) == types.FINDING_TERMINAL
        assert (
            set(TERMINAL_ITEM_OUTCOME[types.ENTITY_REQUIREMENT])
            == types.REQUIREMENT_TERMINAL
        )

    def test_every_entity_kind_is_covered(self):
        assert {k.name for k in entities.ENTITY_KINDS} == set(TERMINAL_ITEM_OUTCOME)

    def test_item_kind_map_covers_every_entity_kind_too(self):
        """The sibling enumeration gets the same gate.

        `TERMINAL_ITEM_OUTCOME` fails closed and is pinned; `ENTITY_KIND_TO_ITEM_KIND`
        fails OPEN — an unmapped kind makes the hook and the backfill silently skip
        it. Applying the ratchet to one enumeration and not its neighbour in the same
        file is how the discipline drifts.
        """
        from codebugs.milestones._schema import ENTITY_KIND_TO_ITEM_KIND
        assert set(ENTITY_KIND_TO_ITEM_KIND) == {k.name for k in entities.ENTITY_KINDS}

    def test_item_kind_map_targets_are_declared_item_kinds(self):
        from codebugs.milestones._schema import ENTITY_KIND_TO_ITEM_KIND, ITEM_KINDS
        assert set(ENTITY_KIND_TO_ITEM_KIND.values()) <= set(ITEM_KINDS)

    def test_targets_are_declared_item_statuses(self):
        from codebugs.milestones._schema import MILESTONE_ITEM_TERMINAL
        for per_kind in TERMINAL_ITEM_OUTCOME.values():
            assert set(per_kind.values()) <= MILESTONE_ITEM_TERMINAL

    def test_unknown_pair_fails_closed(self):
        from codebugs.milestones._schema import outcome_for
        with pytest.raises(ValueError, match="No declared milestone-item projection"):
            outcome_for(types.ENTITY_FINDING, "stale")


class TestSchemaProbe:
    def test_update_finding_survives_a_milestone_less_connection(self, tmp_path):
        """Non-regression for raw `sqlite3.connect()` callers. Its real revert is
        removing the probe, not the registration."""
        path = str(tmp_path / "raw.db")
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        findings.ensure_schema(c)
        findings.add_finding(
            c, finding_id="CB-99", description="x", severity="low",
            category="bug", file="a.py",
        )
        findings.update_finding(c, "CB-99", status="fixed")
        assert c.execute(
            "SELECT status FROM findings WHERE id='CB-99'"
        ).fetchone()["status"] == "fixed"
        c.close()


# ---------------------------------------------------------------------------
# CB-31 — the composed twin: one SQL clause instead of a per-row predicate.
# ---------------------------------------------------------------------------


class _RecordingConnection(sqlite3.Connection):
    """Counts every executed statement TEMPLATE.

    A LOCAL copy on purpose: the house `RecordingConnection` lives in
    `test_bench.py`, `test_reqs.py`, `test_findings.py` and `test_merge.py`, and
    `conftest.py` is reserved for the one tracker-root guard, so each file carries
    its own (CLAUDE.md, Testing).
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.recorded_sql: list[str] = []

    def execute(self, sql, *a, **kw):
        self.recorded_sql.append(sql)
        return super().execute(sql, *a, **kw)


def _raw_item(conn, item_kind, item_ref, milestone_id="stream/triage"):
    """Insert a milestone_items row directly, bypassing every writer."""
    conn.execute(
        "INSERT INTO milestone_items (milestone_id, item_kind, item_ref, size, "
        "priority, status, acceptance, meta_json, created_at, updated_at) "
        "VALUES (?, ?, ?, 'triage', 100, 'open', '', '{}', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (milestone_id, item_kind, item_ref),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM milestone_items WHERE item_ref = ? AND item_kind = ? "
        "ORDER BY id DESC LIMIT 1", (item_ref, item_kind)
    ).fetchone()["id"]


def _differential_fixture(conn):
    """Rows spanning every classification, INCLUDING the two that alone discriminate
    the realistic NULL-unsafe mutant. Returns {label: milestone_items.id}.

    Measured in review: a scalar-subquery mutant keeps only `bug_live`. It is caught
    by `external_over_terminal` and `bug_missing_source` and by nothing else, so an
    "externals are covered" fixture whose external points at a LIVE finding is
    vacuous against the very mistake this test exists to kill.
    """
    conn.execute("DELETE FROM milestone_items")
    conn.commit()
    _add_finding(conn, "CB-T")
    findings.update_finding(conn, "CB-T", status="fixed")
    _add_finding(conn, "CB-L")
    reqs.add_requirement(
        conn, req_id="FR-T", description="r", section="S", priority="must"
    )
    reqs.update_requirement(conn, "FR-T", status="implemented")
    reqs.add_requirement(
        conn, req_id="FR-L", description="r", section="S", priority="must"
    )
    conn.execute("DELETE FROM milestone_items")
    conn.commit()
    return {
        "bug_terminal": _raw_item(conn, "bug", "CB-T"),
        "bug_live": _raw_item(conn, "bug", "CB-L"),
        "req_terminal": _raw_item(conn, "requirement", "FR-T"),
        "req_live": _raw_item(conn, "requirement", "FR-L"),
        # DISCRIMINATOR 1: an external whose ref matches a TERMINAL finding.
        "external_over_terminal": _raw_item(conn, "external", "CB-T"),
        # DISCRIMINATOR 2: a bug whose source row does not exist.
        "bug_missing_source": _raw_item(conn, "bug", "CB-GONE"),
    }


def _sql_live_ids(conn, clause, params):
    return {
        r[0] for r in conn.execute(
            f"SELECT mi.id FROM milestone_items mi WHERE ({clause})", params  # noqa: S608
        ).fetchall()
    }


def _python_live_ids(conn):
    from codebugs.milestones import reconcile
    rows = conn.execute("SELECT id, item_kind, item_ref FROM milestone_items").fetchall()
    return {
        r["id"] for r in rows
        if not reconcile.source_is_terminal(conn, r["item_kind"], r["item_ref"])
    }


class TestLiveSourceClauseDifferential:
    """The SQL clause and `source_is_terminal` must never disagree.

    A second copy of "is this entity terminal" is the drift this repo keeps filing
    cards about, so the guarantee is a differential assertion rather than two
    enumerations that happen to match today.
    """

    def test_agrees_with_source_is_terminal_row_for_row(self, conn):
        from codebugs.milestones import reconcile
        ids = _differential_fixture(conn)
        clause, params = reconcile.live_source_clause(conn, alias="mi")
        sql_live = _sql_live_ids(conn, clause, params)

        assert sql_live == _python_live_ids(conn)

        # Non-vacuity: both verdict classes non-empty, and one hidden row PER KIND.
        # Comparing two empty sets would otherwise pass.
        assert len(ids) == 6
        hidden = set(ids.values()) - sql_live
        assert ids["bug_terminal"] in hidden
        assert ids["req_terminal"] in hidden
        assert sql_live == {
            ids["bug_live"], ids["req_live"],
            ids["external_over_terminal"], ids["bug_missing_source"],
        }

    def test_kills_the_scalar_subquery_mutant(self, conn):
        """The NAMED mutant: `(SELECT status ...) NOT IN (...)`.

        This is the realistic NULL-unsafe mistake — unlike a LEFT JOIN, it IS
        expressible as a WHERE fragment, so the seam's `(sql, params)` shape does
        not foreclose it. NULL NOT IN (...) is NULL and `WHERE NULL` excludes, so
        it silently HIDES live work.
        """
        from codebugs.milestones import reconcile
        ids = _differential_fixture(conn)
        mutant = (
            "(SELECT _s.status FROM findings _s WHERE _s.id = mi.item_ref) "
            "NOT IN ('fixed', 'not_a_bug', 'wont_fix')"
        )
        mutant_live = _sql_live_ids(conn, mutant, [])
        canonical, params = reconcile.live_source_clause(conn, alias="mi")
        canonical_live = _sql_live_ids(conn, canonical, params)

        assert mutant_live != canonical_live
        # And precisely WHICH rows it loses — the two discriminators.
        assert ids["external_over_terminal"] in canonical_live - mutant_live
        assert ids["bug_missing_source"] in canonical_live - mutant_live


class TestLiveSourceClauseAlias:
    def test_alias_must_be_a_bare_identifier(self, conn):
        from codebugs.milestones import reconcile
        for bad in ("mi.", "mi x", "", "mi;--", "mi\n"):
            with pytest.raises(ValueError, match="plain SQL identifier"):
                reconcile.live_source_clause(conn, alias=bad)

    def test_correlated_columns_are_qualified(self, conn):
        from codebugs.milestones import reconcile
        clause, _ = reconcile.live_source_clause(conn, alias="mi")
        assert "mi.item_kind = ?" in clause
        assert "_src.id = mi.item_ref" in clause

    def test_a_shadowing_source_column_cannot_disable_the_filter(self, tmp_path):
        """Measured regression. With the correlated columns unqualified, a source
        table that gains an `item_kind` column silently captures the reference: the
        subquery stops mentioning the OUTER item_kind and hides an `external` row
        that must stay live. Fail-CLOSED, hiding live work.

        Raw minimal schema, because the real `findings` table has no such column.
        """
        from codebugs.milestones import reconcile
        c = sqlite3.connect(str(tmp_path / "shadow.db"))
        c.row_factory = sqlite3.Row
        c.execute(
            "CREATE TABLE findings (id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "item_kind TEXT, item_ref TEXT)"
        )
        c.execute(
            "CREATE TABLE milestone_items (id INTEGER PRIMARY KEY, "
            "item_kind TEXT, item_ref TEXT)"
        )
        c.execute("INSERT INTO findings VALUES ('CB-1', 'fixed', 'bug', 'CB-1')")
        c.execute("INSERT INTO milestone_items VALUES (1, 'bug', 'CB-1')")
        c.execute("INSERT INTO milestone_items VALUES (2, 'external', 'CB-1')")
        c.commit()
        clause, params = reconcile.live_source_clause(c, alias="mi")
        live = {
            r[0] for r in c.execute(
                f"SELECT mi.id FROM milestone_items mi WHERE ({clause})", params  # noqa: S608
            ).fetchall()
        }
        assert live == {2}, "the external row must stay live despite the shadow column"
        c.close()


class TestLiveSourceClauseTableAvailability:
    """Every unknown fails OPEN. An absent source table drops out of BOTH the SQL
    and the parameter list."""

    def _bare(self, tmp_path, name, tables):
        c = sqlite3.connect(str(tmp_path / name))
        c.row_factory = sqlite3.Row
        for t in tables:
            c.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY, status TEXT NOT NULL)")  # noqa: S608
        c.execute(
            "CREATE TABLE milestone_items (id INTEGER PRIMARY KEY, "
            "item_kind TEXT, item_ref TEXT)"
        )
        c.execute("INSERT INTO milestone_items VALUES (1, 'bug', 'CB-1')")
        c.commit()
        return c

    @pytest.mark.parametrize(
        "tables,fragments,nparams",
        [
            (("findings", "requirements"), 2, 3 + 1 + 4 + 1),
            (("findings",), 1, 3 + 1),
            (("requirements",), 1, 4 + 1),
            ((), 0, 0),
        ],
    )
    def test_matrix(self, tmp_path, tables, fragments, nparams):
        from codebugs.milestones import reconcile
        c = self._bare(tmp_path, f"m{len(tables)}{fragments}.db", tables)
        clause, params = reconcile.live_source_clause(c, alias="mi")
        assert clause.count("NOT EXISTS") == fragments
        assert len(params) == nparams
        assert clause.count("?") == nparams
        if fragments == 0:
            assert clause == "1"
        # Fail-open in every shape: with no findings row present nothing is hidden.
        live = {
            r[0] for r in c.execute(
                f"SELECT mi.id FROM milestone_items mi WHERE ({clause})", params  # noqa: S608
            ).fetchall()
        }
        assert live == {1}
        c.close()

    def test_a_terminal_source_is_hidden_only_on_affirmative_proof(self, tmp_path):
        from codebugs.milestones import reconcile
        c = self._bare(tmp_path, "proof.db", ("findings", "requirements"))
        clause, params = reconcile.live_source_clause(c, alias="mi")
        assert _sql_live_ids(c, clause, params) == {1}  # no source row -> live
        c.execute("INSERT INTO findings VALUES ('CB-1', 'fixed')")
        c.commit()
        assert _sql_live_ids(c, clause, params) == set()  # now proven terminal
        c.close()


class TestLiveSourceClauseCost:
    """The card's second, independent reason: per-row I/O, some of it inside
    `pull_next`'s BEGIN IMMEDIATE window."""

    def _recording(self, tmp_path):
        db.init_project(str(tmp_path))
        c = sqlite3.connect(
            str(tmp_path / ".codebugs" / "findings.db"), factory=_RecordingConnection
        )
        c.row_factory = sqlite3.Row
        return c

    @pytest.mark.parametrize("n", [2, 5])
    def test_triage_inbox_statement_count_is_constant_in_row_count(self, tmp_path, n):
        """Counts the TOTAL statement population, not just the ones naming
        `milestone_items` — the old 1+2N implementation also issued exactly ONE
        statement against that table, so the qualifier alone cannot discriminate.
        The extra two are `live_source_clause`'s per-kind sqlite_master probes.
        """
        c = self._recording(tmp_path)
        for i in range(n):
            _add_finding(c, f"CB-{i + 1}")
        c.recorded_sql.clear()
        rows = milestones.triage_inbox(c, limit=50)
        assert len(rows) == n
        assert len(c.recorded_sql) == 3, c.recorded_sql
        assert sum("milestone_items" in s for s in c.recorded_sql) == 1
        assert sum("sqlite_master" in s for s in c.recorded_sql) == 2
        c.close()

    def test_candidates_builds_the_clause_once_for_all_four_buckets(self, tmp_path):
        """Rebuilding it per bucket would add eight sqlite_master reads inside the
        exclusive-lock hold — worse than the defect being fixed."""
        c = self._recording(tmp_path)
        _add_finding(c, "CB-1")
        c.recorded_sql.clear()
        milestones.pull_next(
            c, agent_id="a", capacity={"triage": 5, "small": 5, "large": 5}
        )
        assert sum("sqlite_master" in s for s in c.recorded_sql) == 2, c.recorded_sql
        c.close()


class TestLiveSourceClauseAdoption:
    def test_pull_next_refuses_a_terminal_source_in_a_release_milestone(self, conn):
        """The only fixture proving the SQL predicate — not the hook — protects
        `pull_next`: the reconciler is scoped to streams on purpose (CB-32), so a
        release item over a resolved finding stays stored-`open`."""
        milestones.create_milestone(
            conn, id="release/9.9", kind="release", description="r"
        )
        _add_finding(conn, "CB-50")
        milestones.add_milestone_item(
            conn, milestone_id="release/9.9", item_kind="bug", item_ref="CB-50"
        )
        findings.update_finding(conn, "CB-50", status="fixed")
        row = conn.execute(
            "SELECT status FROM milestone_items WHERE milestone_id='release/9.9'"
        ).fetchone()
        assert row["status"] == "open", "premise: the hook leaves release items alone"

        conn.execute("DELETE FROM milestone_items WHERE milestone_id='stream/triage'")
        conn.commit()
        assert milestones.pull_next(
            conn, agent_id="a", capacity={"triage": 5, "small": 5, "large": 5}
        ) is None

    def test_list_milestone_items_filters_before_offset(self, conn):
        """Stale rows placed BEFORE the offset boundary. If the filter ran after
        the slice, the offset would consume them and the page would be short."""
        milestones.create_milestone(
            conn, id="release/8.8", kind="release", description="r"
        )
        conn.execute("DELETE FROM milestone_items")
        conn.commit()
        for i in range(2):
            _add_finding(conn, f"CB-S{i}")
            findings.update_finding(conn, f"CB-S{i}", status="fixed")
            _raw_item(conn, "bug", f"CB-S{i}", milestone_id="release/8.8")
        for i in range(3):
            _add_finding(conn, f"CB-Q{i}")
            _raw_item(conn, "bug", f"CB-Q{i}", milestone_id="release/8.8")

        page = milestones.list_milestone_items(
            conn, milestone_id="release/8.8", live_only=True, offset=1, limit=2
        )
        assert [i["item_ref"] for i in page] == ["CB-Q1", "CB-Q2"]


class TestLiveSourceClauseCallSites:
    """Pins the population. It cannot PREVENT a fourth queue read from forgetting
    the filter — nothing local can — but it makes adding one a decision someone
    records, the same shape as TestOpenCallSitesRatchet."""

    def test_call_sites_are_pinned(self):
        import ast
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "src" / "codebugs" / "milestones"
        found = set()
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for sub in ast.walk(node):
                    if not isinstance(sub, ast.Call):
                        continue
                    fn = sub.func
                    name = (
                        fn.attr if isinstance(fn, ast.Attribute)
                        else fn.id if isinstance(fn, ast.Name) else None
                    )
                    if name == "live_source_clause":
                        found.add((path.name, node.name))
        assert found == {
            ("triage.py", "triage_inbox"),
            ("capacity.py", "_candidates"),
            ("foundation.py", "list_milestone_items"),
        }, found
