"""Shared entity type constants, aliases, and resolvers.

This module has zero dependencies on other codebugs modules — safe to import
from anywhere without circular import risk.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


def utc_now() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Finding ids ---
FINDING_ID_PREFIX = "CB-"

# --- Finding statuses ---
FINDING_STATUSES = ("open", "in_progress", "fixed", "not_a_bug", "wont_fix", "stale")

FINDING_STATUS_ALIASES: dict[str, str] = {
    "done": "fixed",
    "resolved": "fixed",
    "implemented": "fixed",
    "closed": "fixed",
    "wontfix": "wont_fix",
    "won't_fix": "wont_fix",
    "invalid": "not_a_bug",
    "in-progress": "in_progress",
    "active": "in_progress",
    "working": "in_progress",
}

FINDING_TERMINAL = frozenset({"fixed", "not_a_bug", "wont_fix"})

# --- Requirement statuses ---
REQUIREMENT_STATUSES = ("planned", "partial", "implemented", "verified", "superseded", "obsolete")

REQUIREMENT_TERMINAL = frozenset({"implemented", "verified", "superseded", "obsolete"})

# --- Merge session statuses ---
MERGE_STATUSES = ("active", "merging", "done", "abandoned")

# --- Severities (findings) ---
SEVERITIES = ("critical", "high", "medium", "low")

# --- Priorities (requirements) ---
PRIORITIES = ("must", "should", "could")

# --- Entity types (used by blockers) ---
ENTITY_FINDING = "finding"
ENTITY_REQUIREMENT = "requirement"

ENTITY_TABLES: dict[str, str] = {
    ENTITY_FINDING: "findings",
    ENTITY_REQUIREMENT: "requirements",
}

TERMINAL_STATUSES: dict[str, frozenset[str]] = {
    ENTITY_FINDING: FINDING_TERMINAL,
    ENTITY_REQUIREMENT: REQUIREMENT_TERMINAL,
}

# --- Blocker trigger types ---
TRIGGER_TYPES = ("entity_resolved", "date", "manual")


# --- Resolvers ---


def _resolve(
    value: str,
    valid: tuple[str, ...],
    aliases: dict[str, str] | None,
    label: str,
) -> str:
    """Normalize a value to canonical lowercase form with optional alias lookup."""
    v = value.lower().strip()
    if aliases:
        v = aliases.get(v, v)
    if v not in valid:
        raise ValueError(f"Invalid {label}: {value!r}")
    return v


def resolve_finding_status(status: str) -> str:
    """Normalize a finding status input to canonical lowercase form."""
    return _resolve(status, FINDING_STATUSES, FINDING_STATUS_ALIASES, "finding status")


def resolve_requirement_status(status: str) -> str:
    """Normalize a requirement status input to canonical lowercase form."""
    return _resolve(status, REQUIREMENT_STATUSES, None, "requirement status")


def resolve_priority(priority: str) -> str:
    """Normalize a priority input to canonical lowercase form."""
    return _resolve(priority, PRIORITIES, None, "priority")


# --- SQL identifiers ---

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_sql_identifier(name: str) -> bool:
    """True iff ``name`` is a bare SQL identifier safe to interpolate into a statement.

    THE single definition of that pattern for the whole package (CB-22). It lives
    here because ``types`` is the zero-dependency module every other one may import,
    and because the pattern is security-relevant: a second copy is how one of them
    drifts. ``entities`` once carried a byte-identical ``_SAFE_IDENT``; that the two
    compiled to the same object was an artifact of ``re``'s pattern cache, not a
    guarantee.

    Callers must interpolate an identifier ONLY after this returns True. Values are
    never interpolated — bind them.
    """
    return bool(_IDENT.match(name))


# --- Ordering ---


def rank_case_sql(column: str, vocabulary: tuple[str, ...]) -> tuple[str, list[str]]:
    """Build an ORDER BY fragment sorting ``column`` by DECLARED precedence.

    Vocabulary columns are TEXT, so a bare ``ORDER BY severity`` sorts
    alphabetically — ``critical, high, low, medium`` — silently ranking ``low``
    above ``medium`` (CB-20). Under a ``LIMIT`` that does not merely look wrong,
    it truncates the more important rows.

    The rank is derived from the tuple rather than hardcoded, so the SQL cannot
    drift from the vocabulary: reorder ``SEVERITIES`` and the ordering follows.
    ``ELSE len(vocabulary)`` puts any legacy or corrupt value LAST rather than
    first, which is the safe direction — an unrecognised value must not outrank
    a real one.

    Returns ``(fragment, params)``. The values are BOUND, not interpolated. The
    caller must splice ``params`` at the position the fragment occupies in the
    final statement — see the warning in ``findings.query_findings``.

    ``column`` is re-checked here rather than trusted: this is a public function
    with callers that are not ``EntityKind`` (``findings.query_findings`` passes a
    literal), so it owns its own boundary (CB-22).
    """
    if not is_sql_identifier(column):
        raise ValueError(f"Not a bare column identifier: {column!r}")
    if not vocabulary:
        raise ValueError("vocabulary must not be empty")

    whens = " ".join(f"WHEN ? THEN {i}" for i in range(len(vocabulary)))
    return f"CASE {column} {whens} ELSE {len(vocabulary)} END", list(vocabulary)
