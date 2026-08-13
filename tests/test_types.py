"""Tests for shared entity type constants and resolvers."""

from __future__ import annotations

import pytest

from codebugs.types import (
    FINDING_STATUSES,
    REQUIREMENT_STATUSES,
    FINDING_TERMINAL,
    REQUIREMENT_TERMINAL,
    TERMINAL_STATUSES,
    ENTITY_FINDING,
    ENTITY_REQUIREMENT,
    PRIORITIES,
    SEVERITIES,
    rank_case_sql,
    resolve_finding_status,
    resolve_requirement_status,
    resolve_priority,
)


class TestConstants:
    def test_finding_terminal_subset_of_statuses(self):
        assert FINDING_TERMINAL <= set(FINDING_STATUSES)

    def test_requirement_terminal_subset_of_statuses(self):
        assert REQUIREMENT_TERMINAL <= set(REQUIREMENT_STATUSES)

    def test_terminal_statuses_keys(self):
        assert set(TERMINAL_STATUSES) == {ENTITY_FINDING, ENTITY_REQUIREMENT}

    def test_stale_not_in_finding_terminal(self):
        assert "stale" not in FINDING_TERMINAL


class TestResolveFindingStatus:
    def test_canonical_passthrough(self):
        assert resolve_finding_status("open") == "open"
        assert resolve_finding_status("fixed") == "fixed"

    def test_case_insensitive(self):
        assert resolve_finding_status("OPEN") == "open"
        assert resolve_finding_status("Fixed") == "fixed"

    def test_aliases(self):
        assert resolve_finding_status("done") == "fixed"
        assert resolve_finding_status("resolved") == "fixed"
        assert resolve_finding_status("wontfix") == "wont_fix"
        assert resolve_finding_status("in-progress") == "in_progress"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid finding status"):
            resolve_finding_status("bogus")

    def test_strips_whitespace(self):
        assert resolve_finding_status("  open  ") == "open"


class TestResolveRequirementStatus:
    def test_canonical_passthrough(self):
        assert resolve_requirement_status("planned") == "planned"
        assert resolve_requirement_status("implemented") == "implemented"

    def test_titlecase_accepted(self):
        assert resolve_requirement_status("Planned") == "planned"
        assert resolve_requirement_status("Implemented") == "implemented"
        assert resolve_requirement_status("Obsolete") == "obsolete"

    def test_uppercase_accepted(self):
        assert resolve_requirement_status("PLANNED") == "planned"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid requirement status"):
            resolve_requirement_status("bogus")


class TestResolvePriority:
    def test_canonical_passthrough(self):
        assert resolve_priority("must") == "must"
        assert resolve_priority("should") == "should"
        assert resolve_priority("could") == "could"

    def test_titlecase_accepted(self):
        assert resolve_priority("Must") == "must"
        assert resolve_priority("Should") == "should"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid priority"):
            resolve_priority("bogus")


class TestRankCaseSql:
    """CB-20: vocabulary columns are TEXT, so a bare ORDER BY sorts alphabetically."""

    def test_values_are_bound_not_interpolated(self):
        sql, params = rank_case_sql("severity", SEVERITIES)
        assert params == list(SEVERITIES)
        for value in SEVERITIES:
            assert value not in sql, "vocabulary values must be placeholders, never inlined"
        assert sql.count("?") == len(SEVERITIES)

    def test_ranks_follow_declaration_order(self):
        sql, _ = rank_case_sql("severity", SEVERITIES)
        assert (
            sql
            == "CASE severity WHEN ? THEN 0 WHEN ? THEN 1 WHEN ? THEN 2 WHEN ? THEN 3 ELSE 4 END"
        )

    def test_unknown_values_sort_last_not_first(self):
        """ELSE must exceed every real rank — an unrecognised value must not outrank a real one."""
        sql, _ = rank_case_sql("severity", SEVERITIES)
        assert f"ELSE {len(SEVERITIES)} END" in sql

    def test_works_for_the_other_vocabulary(self):
        sql, params = rank_case_sql("priority", PRIORITIES)
        assert params == list(PRIORITIES)
        assert sql.startswith("CASE priority ")

    def test_reordering_the_vocabulary_reorders_the_sql(self):
        """The rank is derived, so the SQL cannot drift from the vocabulary."""
        sql, params = rank_case_sql("severity", ("low", "high"))
        assert params == ["low", "high"]
        assert sql == "CASE severity WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END"

    @pytest.mark.parametrize(
        "bad", ["severity; DROP TABLE findings", "a b", "", "1col", "tbl.col", "col--"]
    )
    def test_non_identifier_column_is_refused(self, bad):
        with pytest.raises(ValueError, match="bare column identifier"):
            rank_case_sql(bad, SEVERITIES)

    def test_empty_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="must not be empty"):
            rank_case_sql("severity", ())

    def test_the_fragment_actually_orders_in_sqlite(self):
        """Executed, not just string-compared — the point is the ORDER BY behaviour."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (severity TEXT)")
        conn.executemany(
            "INSERT INTO t VALUES (?)", [(s,) for s in ("low", "critical", "medium", "high")]
        )

        assert [r[0] for r in conn.execute("SELECT severity FROM t ORDER BY severity")] == [
            "critical",
            "high",
            "low",
            "medium",
        ], "baseline: plain lexical ordering is what CB-20 is about"

        sql, params = rank_case_sql("severity", SEVERITIES)
        got = [r[0] for r in conn.execute(f"SELECT severity FROM t ORDER BY {sql}", params)]
        assert got == list(SEVERITIES)
        conn.close()

    def test_unknown_value_sorts_last_in_sqlite(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (severity TEXT)")
        conn.executemany(
            "INSERT INTO t VALUES (?)", [(s,) for s in ("aaa_legacy", "low", "critical")]
        )
        sql, params = rank_case_sql("severity", SEVERITIES)
        got = [r[0] for r in conn.execute(f"SELECT severity FROM t ORDER BY {sql}", params)]
        assert got == ["critical", "low", "aaa_legacy"], (
            "a legacy value must not outrank a real one"
        )
        conn.close()
