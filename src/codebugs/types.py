"""Shared entity type constants, aliases, and resolvers.

This module has zero dependencies on other codebugs modules — safe to import
from anywhere without circular import risk.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


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


def is_vocabulary_filter_active(value: object) -> bool:
    """Is this query-filter argument a real filter, or does it mean "no filter"?

    Only ``None`` (not supplied) and ``""`` (the documented empty-filter convention)
    mean "no filter". Everything else is an active filter and must reach its resolver,
    which is what refuses it. Guarding with plain truthiness instead conflates those two
    with *wrong input*: ``query_findings(severity=0)`` short-circuited past the CB-19
    guard and returned the whole table, and an unfiltered queue is indistinguishable
    from a correctly filtered one (CB-25).

    **Never decide this with ``value != ""``.** That runs arbitrary user code:
    ``unittest.mock.ANY`` is truthy yet compares equal to ``""``, so it would flip from
    raising to silently disabling the filter, and a ``str`` subclass overriding
    ``__ne__`` would do the same to a perfectly valid ``"open"`` — the CB-25 defect
    reintroduced by its own fix. ``str.__len__`` for the same reason: a subclass cannot
    reach it.

    Scoped to *vocabulary* filters on purpose. It is wrong for list-valued filters such
    as ``ids`` / ``tags``, where an empty list legitimately means "no filter" and where
    an active empty filter would silently return nothing instead of everything.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return str.__len__(value) != 0
    return True


def is_text_filter_active(value: object) -> bool:
    """Free-text twin of ``is_vocabulary_filter_active`` — for filters with no resolver.

    Same convention: ``None`` (not supplied) and ``""`` (the documented empty filter)
    mean "no filter". The difference is what refuses wrong input: a vocabulary filter
    forwards to its resolver, which raises on a non-string; a free-text filter has no
    resolver, so *this predicate* is the only gate and must raise the ``ValueError``
    itself. Returning ``True`` for a non-string instead would bind it into SQL, where
    ``fingerprint = 0`` silently matches nothing — an empty page indistinguishable
    from a real one (the CB-25 shape, on the free-text side CB-29 tracks).

    Type-based for the same reason as its twin: never run ``==``/``len()`` on the
    value (``unittest.mock.ANY`` compares equal to ``""``; a ``str`` subclass can
    override ``__ne__``).
    """
    if value is None:
        return False
    if isinstance(value, str):
        return str.__len__(value) != 0
    raise ValueError(f"text filter must be a string, got {type(value).__name__}")


def _resolve(
    value: str,
    valid: tuple[str, ...],
    aliases: dict[str, str] | None,
    label: str,
) -> str:
    """Normalize a value to canonical lowercase form with optional alias lookup.

    A non-string is refused as ``ValueError`` here rather than escaping as the
    ``AttributeError`` that ``.lower()`` would raise: domain functions promise
    ``ValueError`` for invalid input, and a caller passing ``None`` is giving
    invalid input, not tripping an internal error. This guard belongs at the shared
    layer — every resolver had the same hole, so fixing it per-resolver would leave
    the next one to re-acquire it.
    """
    if not isinstance(value, str):
        raise ValueError(f"Invalid {label}: {value!r}")
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


def resolve_severity(severity: str) -> str:
    """Normalize a severity input to canonical lowercase form.

    Case and surrounding whitespace only — severity still has NO aliases, unlike
    ``status``. ``"High"`` and ``" HIGH "`` resolve to ``"high"``; ``"crit"``,
    ``"P0"`` and ``"sev1"`` still raise. Add aliases only on evidence of callers
    using them (CB-19).

    Severity was the one vocabulary in this module without a resolver, so
    ``findings.py`` open-coded ``if severity not in SEVERITIES`` at three sites
    and the CSV import open-coded ``.strip().lower()`` at a fourth, while the
    sibling ``priority`` had been lenient all along. Every severity input in the
    package goes through here — including ``query_findings``, whose filter was
    raw while ``status`` two lines above it already resolved.
    """
    return _resolve(severity, SEVERITIES, None, "severity")


# Category spelling separators: hyphen and whitespace runs (in any mix) collapse
# to one underscore. Hyphen and space are the two separators the measured twin
# pairs actually used (CB-60: `process-improvement` vs `process_improvement`);
# underscores already in the input are left alone — this normalizes SPELLING,
# it does not re-tokenize the name.
_CATEGORY_SEPARATORS = re.compile(r"[-\s]+")


def normalize_category(category: str) -> str:
    """Normalize a category spelling to its canonical stored form (CB-60).

    Casefold, strip, and collapse hyphen/whitespace runs to a single ``_``.
    Category is an IDENTITY input — the ``auto:v1`` fingerprint hashes it and
    similarity groups strictly inside it — so twin spellings
    (``process-improvement`` / ``Process Improvement`` / ``process_improvement``)
    must map to one canonical name or they fork identity forever.

    Spelling, never meaning — the same contract as the vocabulary resolvers
    above: no aliases, no synonym folding. ``""`` passes through unchanged
    (the legal uncategorized value; similarity's annotation pool matches it
    exactly). Applied by ``findings`` on the OBSERVATION write path only,
    before fingerprint derivation; an explicit ``finding_id`` add, CSV import
    and restore store the category verbatim (CB-51: an import is not an
    observation).
    """
    if not isinstance(category, str):
        raise ValueError(f"Invalid category: {category!r}")
    return _CATEGORY_SEPARATORS.sub("_", category.casefold().strip())


# --- SQL identifiers ---

# Unanchored on purpose — `is_sql_identifier` applies it with `fullmatch`. The
# anchored `^...$` form this replaced was NOT equivalent: `$` also matches just
# before a trailing newline, so it accepted "findings\n" as an identifier.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def is_sql_identifier(name: str) -> bool:
    """True iff ``name`` is identifier-SHAPED and therefore safe to interpolate.

    THE single definition of that pattern for the whole package (CB-22). It lives
    here because ``types`` is the zero-dependency module every other one may import,
    and because the pattern is security-relevant: a second copy is how one of them
    drifts. ``entities`` once carried a byte-identical ``_SAFE_IDENT``; that the two
    compiled to the same object was an artifact of ``re``'s pattern cache, not a
    guarantee.

    Deliberately a SAFE SUBSET, not a validator of "is a real column". It accepts
    identifier-shaped strings that SQLite would read as something else — bare
    keywords like ``NULL`` are accepted here and would become an expression in an
    ``ORDER BY``. That is fine for its actual job, which is to make interpolation
    injection-safe; whether the identifier names a real column is the caller's
    business, and a wrong-but-shaped name fails loudly as ``no such column``.

    Callers must interpolate an identifier ONLY after this returns True. Values are
    never interpolated — bind them.
    """
    return bool(_IDENT.fullmatch(name))


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


def severity_rank(severity: object) -> int:
    """Position of ``severity`` in the declared precedence. LOWER is MORE severe.

    The Python-side twin of ``rank_case_sql``, and derived from the same tuple for
    the same reason: a second hand-written precedence table is one drift from
    disagreeing with the first (CB-22). Reorder ``SEVERITIES`` and both follow.

    **Direction is the trap this function exists to remove.** ``SEVERITIES`` runs
    most-severe-first, so escalating to "the worse of two" is ``min`` over ranks,
    never ``max`` — over either the ranks or the strings. Callers should read
    ``SEVERITIES[min(severity_rank(a), severity_rank(b))]`` and nothing else.

    Deliberately NOT a resolver: callers pass values ``resolve_severity`` has
    already normalized, and accepting ``"HIGH"`` here would put a second
    normalization policy in a second place.

    Anything unrecognised — a legacy value, a corrupt one, a non-string from a row
    written before the CHECK constraint — ranks LAST (``len(SEVERITIES)``), the same
    convention as ``rank_case_sql``'s ``ELSE``. That keeps the comparison total and
    one-directional: an unknown stored value can never outrank a real observation,
    and ranking it cannot raise inside an open transaction.
    """
    try:
        return SEVERITIES.index(severity)  # type: ignore[arg-type]
    except ValueError:
        return len(SEVERITIES)


# --- Batch payload shape (CB-80) ---


def validate_batch_payload(value: object, *, label: str) -> list[Any]:
    """Refuse a batch argument that is not a list of mappings, with a ``ValueError``.

    ``findings.batch_add_findings`` / ``reqs.batch_add_requirements`` are typed
    ``list[dict[str, Any]]``, but that annotation is not enforced against a raw
    Python caller (an MCP client is stopped earlier, by pydantic's own argument
    model) — neither domain function validated the CONTAINER before indexing
    into it. Reproduced against 41058c5 (CB-80): ``batch_add_findings(conn, 5)``
    raised ``TypeError: 'int' object is not iterable``; ``batch_add_findings(conn,
    [5])`` raised a different ``TypeError`` from the first per-member ``dict``
    access; and ``batch_add_requirements(conn, "ab")`` is the interesting one —
    a ``str`` IS iterable, so a check that establishes "is this a list" only by
    iterating it (``for x in value: ...``, with no test of ``value`` itself)
    does not stop at the boundary: it silently iterates CHARACTERS, and the
    failure surfaces two frames deeper as ``TypeError: string indices must be
    integers``, not here. That is CB-74's "validating one view while consuming
    another" shape wearing a new outfit, so the container check below is a
    strict ``isinstance(value, list)`` — never a duck-typed "is iterable" test,
    and never skipped in favor of trusting the per-element loop alone to catch
    everything.

    This package's contract is that domain functions raise ``ValueError`` for
    invalid input; a raw ``TypeError`` leaking out of a validator whose only
    job is to keep other code from raising one is precisely the failure this
    function exists to close.

    ``label`` names the caller's own argument in the raised message
    (``"findings"``, ``"requirements"``) — this module knows nothing about
    either domain, only the FORM ("a list of objects"), which is what makes
    the check shareable rather than a third or fourth copy of
    ``bench.import_json``'s landed list-of-mappings validator (that one also
    accepts a JSON string and decodes it first; batch payloads here never do,
    so this function does not carry that half).

    Every element is checked, not just the first: a well-formed member
    followed by a malformed one would otherwise clear a first-element-only
    check and die later at the identical unchecked site CB-80 was filed
    against.
    """
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of objects, not {type(value).__name__}")
    for index, element in enumerate(value):
        if not isinstance(element, Mapping):
            raise ValueError(f"{label}[{index}] must be an object, not {type(element).__name__}")
    return value
