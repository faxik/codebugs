"""Shared entity type constants, aliases, and resolvers.

This module has zero dependencies on other codebugs modules — safe to import
from anywhere without circular import risk.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
