"""Shared entity type constants, aliases, and resolvers.

This module has zero dependencies on other codebugs modules — safe to import
from anywhere without circular import risk.
"""

from __future__ import annotations

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

def resolve_finding_status(status: str) -> str:
    """Normalize a finding status input to canonical lowercase form."""
    s = status.lower().strip()
    s = FINDING_STATUS_ALIASES.get(s, s)
    if s not in FINDING_STATUSES:
        raise ValueError(f"Invalid finding status: {status!r}")
    return s


def resolve_requirement_status(status: str) -> str:
    """Normalize a requirement status input to canonical lowercase form."""
    s = status.lower().strip()
    if s not in REQUIREMENT_STATUSES:
        raise ValueError(f"Invalid requirement status: {status!r}")
    return s


def resolve_priority(priority: str) -> str:
    """Normalize a priority input to canonical lowercase form."""
    p = priority.lower().strip()
    if p not in PRIORITIES:
        raise ValueError(f"Invalid priority: {priority!r}")
    return p
