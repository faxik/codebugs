"""Tests for shared entity type constants and resolvers."""

from __future__ import annotations

import pytest

from codebugs import types
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
    severity_rank,
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


class TestSeverityRank:
    """`severity_rank` is the Python-side twin of `rank_case_sql` (CB-52).

    It exists so the escalation in `findings._bump_row` can compare two severities
    without a second, hand-written precedence table — the duplicated-rather-than-
    shared hazard CB-22 records. Reorder SEVERITIES and both sides follow.
    """

    def test_rank_follows_the_declared_vocabulary_order(self):
        assert [severity_rank(s) for s in SEVERITIES] == list(range(len(SEVERITIES)))

    def test_lower_rank_means_more_severe(self):
        # The direction that matters: index 0 is MOST severe, so escalation takes
        # the MINIMUM rank. A `max()` over ranks would select `low`.
        assert severity_rank("critical") < severity_rank("high")
        assert severity_rank("high") < severity_rank("medium")
        assert severity_rank("medium") < severity_rank("low")

    def test_unknown_value_sorts_last_and_can_never_outrank_a_real_one(self):
        # Same convention as rank_case_sql's `ELSE len(vocabulary)`: a legacy or
        # corrupt stored value must never win an escalation comparison.
        assert severity_rank("sev1") == len(SEVERITIES)
        assert severity_rank("") == len(SEVERITIES)
        for real in SEVERITIES:
            assert severity_rank(real) < severity_rank("not-a-severity")

    def test_it_does_not_resolve_spelling(self):
        # Deliberately NOT a resolver: callers pass canonical values that
        # `resolve_severity` has already normalized. Treating "HIGH" as known here
        # would put a second normalization policy in a second place.
        assert severity_rank("HIGH") == len(SEVERITIES)

    @pytest.mark.parametrize("value", [None, 0, [], {}])
    def test_non_string_input_is_unknown_rather_than_a_crash(self, value):
        # The escalation compares a STORED value against an OBSERVED one; a row
        # written before the CHECK constraint could hold anything. Ranking it last
        # keeps the comparison total instead of raising inside an open transaction.
        assert severity_rank(value) == len(SEVERITIES)


class TestRequireRowLimit:
    """CB-161 — the ONE definition of "a row limit", shared by three call sites.

    It lives here rather than three times over because a predicate that is
    duplicated rather than shared is one drift away from disagreeing with
    itself — the same reason `is_sql_identifier` is the only copy of its
    pattern.
    """

    def test_none_is_no_limit(self):
        assert types.require_row_limit("last_n", None) is None

    def test_zero_is_a_real_limit_meaning_zero_rows(self):
        """Not "> 0": `sweep.list_items` already gave zero rows on a zero, and a
        strictly-positive rule would have broken the one correct site of three."""
        assert types.require_row_limit("last_n", 0) == 0

    def test_a_positive_integer_passes_through(self):
        assert types.require_row_limit("last_n", 7) == 7

    def test_negative_is_refused(self):
        with pytest.raises(ValueError, match="must not be negative"):
            types.require_row_limit("last_n", -1)

    @pytest.mark.parametrize("bad", ["5", 2.7, [], {}, object()])
    def test_a_non_integer_is_refused(self, bad):
        with pytest.raises(ValueError, match="must be an integer"):
            types.require_row_limit("last_n", bad)

    @pytest.mark.parametrize("bad", [True, False])
    def test_bool_is_refused_although_it_subclasses_int(self, bad):
        """`isinstance(True, int)` is True, so the naive check accepts `True` and
        quietly means `LIMIT 1`. `last_n=False` is the worse half: it would mean
        zero rows to a caller who almost certainly meant "no limit" — this card's
        own defect, one falsey value further along.
        """
        with pytest.raises(ValueError, match="must be an integer"):
            types.require_row_limit("last_n", bad)

    def test_the_label_names_the_callers_own_argument(self):
        with pytest.raises(ValueError, match="^limit must be an integer"):
            types.require_row_limit("limit", "5")

    def test_the_canonical_integer_is_returned_not_the_object_handed_in(self):
        """Validating one view while consuming another is not a guard (CB-74).

        An `int` subclass may answer this function's comparison differently from
        the value SQLite would bind, so what comes back — and what the caller
        binds — is a plain `int`.
        """

        class Sneaky(int):
            def __lt__(self, other):
                return False  # claims never to be negative

        returned = types.require_row_limit("last_n", Sneaky(3))
        assert type(returned) is int
        assert returned == 3

    def test_a_lying_subclass_cannot_smuggle_a_negative_past_the_check(self):
        class Sneaky(int):
            def __lt__(self, other):
                return False

        with pytest.raises(ValueError, match="must not be negative"):
            types.require_row_limit("last_n", Sneaky(-4))
