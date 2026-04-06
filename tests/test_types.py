"""Tests for shared entity type constants and resolvers."""

from __future__ import annotations

import pytest

from codebugs.types import (
    FINDING_STATUSES, REQUIREMENT_STATUSES, MERGE_STATUSES,
    SEVERITIES, PRIORITIES,
    FINDING_TERMINAL, REQUIREMENT_TERMINAL, TERMINAL_STATUSES,
    ENTITY_FINDING, ENTITY_REQUIREMENT,
    TRIGGER_TYPES,
    resolve_finding_status, resolve_requirement_status, resolve_priority,
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
