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
    is_vocabulary_filter_active,
    rank_case_sql,
    resolve_finding_status,
    resolve_requirement_status,
    resolve_priority,
    resolve_severity,
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


class TestResolveSeverity:
    """CB-19: severity was the one vocabulary in this module with no resolver."""

    def test_canonical_passthrough(self):
        for sev in SEVERITIES:
            assert resolve_severity(sev) == sev

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("High", "high"),
            ("HIGH", "high"),
            ("  critical  ", "critical"),
            ("MeDiUm", "medium"),
            ("\tlow\n", "low"),
        ],
    )
    def test_case_and_whitespace_are_forgiven(self, given, expected):
        assert resolve_severity(given) == expected

    @pytest.mark.parametrize("bad", ["crit", "P0", "sev1", "blocker", "urgent", "", "  "])
    def test_meaning_is_never_guessed(self, bad):
        """Severity normalizes case, not meaning — it has NO aliases, deliberately.
        Adding them is a separate decision requiring evidence of real callers."""
        with pytest.raises(ValueError, match="Invalid severity"):
            resolve_severity(bad)

    def test_it_matches_its_sibling_priority(self):
        """The card's whole point: two vocabularies in one module answered the same
        question differently."""
        assert resolve_severity("High") == "high"
        assert resolve_priority("Must") == "must"


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


class TestResolversRefuseNonStrings:
    """CB-19 review finding: `_resolve` called `.lower()` before checking anything,
    so a `None` escaped as `AttributeError` — violating the package contract that
    domain functions raise `ValueError` for invalid input.

    Guarded at the shared `_resolve`, not per resolver: every one of them had the
    same hole, so a per-resolver fix would leave the next one to re-acquire it."""

    @pytest.mark.parametrize(
        "resolver", [resolve_severity, resolve_priority, resolve_finding_status,
                     resolve_requirement_status]
    )
    @pytest.mark.parametrize("bad", [None, 3, ["high"], object()])
    def test_non_string_raises_value_error_not_attribute_error(self, resolver, bad):
        with pytest.raises(ValueError, match="Invalid"):
            resolver(bad)


class TestIsVocabularyFilterActive:
    """CB-25: the guard in front of every vocabulary filter.

    `_resolve` above refuses non-strings, but a *falsey* one never reached it — the
    call sites guarded with plain truthiness, so `query_findings(severity=0)` skipped
    the condition entirely and returned the whole table."""

    def test_none_and_empty_string_mean_no_filter(self):
        assert is_vocabulary_filter_active(None) is False
        assert is_vocabulary_filter_active("") is False

    def test_ordinary_value_is_active(self):
        assert is_vocabulary_filter_active("open") is True
        assert is_vocabulary_filter_active("  ") is True  # resolver strips, then refuses

    @pytest.mark.parametrize("falsey", [0, False, [], {}, set(), 0.0, b""])
    def test_falsey_non_strings_are_active_so_the_resolver_can_refuse_them(self, falsey):
        """The defect itself: these are wrong input, not "no filter"."""
        assert is_vocabulary_filter_active(falsey) is True

    def test_mock_any_is_active(self):
        """`ANY` is truthy but compares EQUAL to "" — so the obvious
        `value is not None and value != ""` would silently disable the filter for it,
        reintroducing CB-25 inside CB-25's own fix. Pins the type-based predicate."""
        from unittest.mock import ANY

        assert bool(ANY) is True
        assert (ANY != "") is False  # this is what the naive predicate would trust
        assert is_vocabulary_filter_active(ANY) is True

    def test_str_subclass_overriding_ne_is_still_active(self):
        """Same trap from the other side: a perfectly valid status that lies about `!=`.
        Under the naive predicate this resolvable value became "no filter"."""

        class Contrarian(str):
            def __ne__(self, other):
                return False

        value = Contrarian("open")
        assert (value != "") is False  # the naive predicate would call this "no filter"
        assert is_vocabulary_filter_active(value) is True
        assert resolve_finding_status(value) == "open"

    def test_str_subclass_overriding_len_cannot_fake_emptiness(self):
        """`str.__len__` rather than `len()`, for the same do-not-run-user-code reason."""

        class Liar(str):
            def __len__(self):
                return 0

        assert is_vocabulary_filter_active(Liar("open")) is True
