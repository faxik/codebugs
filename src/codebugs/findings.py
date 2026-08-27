"""Findings domain — CRUD, query, stats, MCP tools, and CLI for code findings."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Annotated, Any, Literal, NamedTuple, TypedDict

from pydantic import Field

from codebugs import db, entities
from codebugs.types import (
    is_sql_identifier,
    ENTITY_FINDING,
    FINDING_ID_PREFIX,
    SEVERITIES,
    is_text_filter_active,
    is_vocabulary_filter_active,
    normalize_category,
    rank_case_sql,
    require_row_limit,
    resolve_finding_status,
    resolve_severity,
    severity_rank,
    utc_now,
    validate_batch_payload,
)

SCHEMA = """\
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    category TEXT NOT NULL,
    file TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
    description TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'human',
    tags TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    reported_at_commit TEXT,
    reported_at_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fingerprint TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
"""

# The identity branch classes (CB-43). Defined here, above the index DDL that is
# derived from the live set; the branch-table narrative lives at the Identity
# section below. The table is TOTAL over types.FINDING_STATUSES —
# tests/test_dedup.py::TestBranchTotality pins it.
LIVE_STATUSES = ("open", "in_progress", "stale")  # same fingerprint -> bump this row
_REOPEN_STATUSES = ("fixed",)  # same fingerprint -> regression: reopen this row
RECURRENCE_STATUSES = ("wont_fix", "not_a_bug")  # decision stays closed -> new linked row

# Applied AFTER every migration, never inside SCHEMA (sweep.py's pattern). SCHEMA runs
# first in ensure_schema, so an index here that references a migrated-in column would
# raise on any pre-existing table — and `_migrate_statuses` rebuilds the table from a
# hardcoded DDL and recreates only its own hardcoded index list, so an index created
# earlier would silently vanish on the rebuild path.
#
# The partial UNIQUE index is the identity guarantee (CB-43): at most one LIVE card per
# fingerprint is a database fact, not transaction discipline — the claims.py shape. The
# WHERE is DERIVED from LIVE_STATUSES (the rank_case_sql doctrine: SQL built from the
# tuple cannot drift from it); the values are repo-owned literals, which is why
# interpolating them into DDL — where parameters cannot bind — is sanctioned.
_POST_MIGRATION_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_fingerprint_live ON findings(fingerprint) "
    "WHERE fingerprint IS NOT NULL AND status IN ("
    + ", ".join(f"'{s}'" for s in LIVE_STATUSES)
    + ")",
    # CB-115: both historical creators of this index live on migration paths a FRESH
    # database never takes (_migrate_statuses early-returns because SCHEMA already
    # spells 'in_progress'; the provenance ALTER is guarded by a column check that is
    # false because SCHEMA carries the column), so fresh databases had no
    # reported_at_ref index at all. Declared here — not in SCHEMA — for the reasons
    # in the comment above; on migrated databases IF NOT EXISTS makes it a no-op.
    "CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Initialize the findings schema (tables, indexes, migrations)."""
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    _migrate_statuses(conn)
    _migrate_findings_add_provenance_columns(conn)
    _migrate_findings_add_identity_columns(conn)
    for stmt in _POST_MIGRATION_INDEXES:
        conn.execute(stmt)
    conn.commit()


def _migrate_statuses(conn: sqlite3.Connection) -> None:
    """Add 'in_progress' to the status CHECK constraint on existing databases."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='findings'"
    ).fetchone()
    if row is None:
        return
    ddl = row[0] or ""
    if "in_progress" in ddl:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """CREATE TABLE findings_new (
            id TEXT PRIMARY KEY,
            severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
            category TEXT NOT NULL,
            file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
            description TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'human',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            reported_at_commit TEXT,
            reported_at_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """INSERT INTO findings_new
           (id, severity, category, file, status, description, source, tags, meta, created_at, updated_at)
           SELECT id, severity, category, file, status, description, source, tags, meta, created_at, updated_at
           FROM findings"""
    )
    conn.execute("DROP TABLE findings")
    conn.execute("ALTER TABLE findings_new RENAME TO findings")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _migrate_findings_add_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add provenance columns to existing databases that already passed status migration.

    Schema ownership follows table ownership: these columns live on the findings table,
    so the migration lives here even though the columns are used by provenance.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
    if "reported_at_commit" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_commit TEXT")
    if "reported_at_ref" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN reported_at_ref TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_findings_reported_at_ref ON findings(reported_at_ref)"
        )
    conn.commit()


def _migrate_findings_add_identity_columns(conn: sqlite3.Connection) -> None:
    """Add the CB-43 identity columns to existing databases.

    Columns only — the partial unique index lives in _POST_MIGRATION_INDEXES,
    because it must be created after BOTH this migration (the column must exist)
    and _migrate_statuses (whose table rebuild would drop it).

    NULL fingerprints are pre-migration rows (or explicit-id rows); NULL never
    matches anything, so legacy rows are inert to dedup until re-observed.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
    if "fingerprint" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN fingerprint TEXT")
    if "occurrence_count" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1")
    if "last_seen_at" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN last_seen_at TEXT")
    conn.commit()


def _next_id(conn: sqlite3.Connection) -> str:
    """Generate next CB-N id."""
    prefix_len = len(FINDING_ID_PREFIX) + 1  # 1-based SUBSTR offset past the prefix
    row = conn.execute(
        f"SELECT id FROM findings WHERE id LIKE ? "
        f"ORDER BY CAST(SUBSTR(id, {prefix_len}) AS INTEGER) DESC LIMIT 1",
        (f"{FINDING_ID_PREFIX}%",),
    ).fetchone()
    if row:
        match = re.search(rf"{re.escape(FINDING_ID_PREFIX)}(\d+)", row["id"])
        n = int(match.group(1)) + 1 if match else 1
    else:
        n = 1
    return f"{FINDING_ID_PREFIX}{n}"


# --- Identity (CB-43) ---------------------------------------------------------------
#
# A FINDING is a defect; an OCCURRENCE is one observation of it. The identity function
# maps an observation to the finding it belongs to via `fingerprint`, so one defect
# observed N times is one row with occurrence_count=N instead of N rows.
#
# The status branch table (LIVE_STATUSES / _REOPEN_STATUSES / RECURRENCE_STATUSES,
# defined above the index DDL they feed) is TOTAL over types.FINDING_STATUSES, and
# tests/test_dedup.py::TestBranchTotality pins that: a new status added to the
# vocabulary must be classified there or the test fails — the alternative is that it
# silently falls through to "no match" and the duplicate explosion resumes for exactly
# that status (the review's judge found `stale` doing this in an earlier draft).
# LIVE_STATUSES and RECURRENCE_STATUSES are public so consumers (similarity.py's
# annotation pool) derive from the classified sets instead of re-spelling them.

_AUTO_FP_PREFIX = "auto:"  # reserved: derived fingerprints only, callers may not supply it
# The CURRENT derivation version. One constant, two readers — `_derive_fingerprint`
# writes it and `normalize_categories` re-derives only values carrying it: a second
# literal is one drift away from a migration re-keying a version it cannot reproduce.
_AUTO_V1_PREFIX = _AUTO_FP_PREFIX + "v1:"
_FP_MAX_LEN = 256

# Meta keys the identity machinery itself writes. Caller-supplied values under these
# names would be read back as trusted internal structures — meta={"occurrences": 1}
# makes the NEXT observation's ring append raise TypeError, and a spoofed
# "recurrence_of" fabricates a link. Refused at add and at update(meta_update).
_RESERVED_META_KEYS = frozenset({"occurrences", "occurrences_dropped", "regressed", "recurrence_of"})

# Fallback normalization strips a meta value from the description only when its KEY
# looks run-scoped. Stripping every string value merges defects whose discriminator
# the filer echoes through meta — meta={"rule_code": "E501"} vs "F401" normalized
# identically and the second defect silently vanished (caught in diff review). A
# key-name allowlist errs toward KEEPING a token, i.e. toward a false split — the
# conservative direction, since a false merge is invisible and a split is merely
# today's status quo. The measured 71/115 family collapse came entirely from sha /
# slug / log values, all covered here.
_VOLATILE_KEY_TOKENS = ("sha", "commit", "slug", "branch", "log", "run", "time", "duration")


def _is_volatile_meta_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _VOLATILE_KEY_TOKENS)


# Hex runs need >= 1 digit: `\b[0-9a-fA-F]{7,}\b` alone eats all-letter words
# ("defaced", "effaced") and merges two genuinely different descriptions.
_FP_HEX_RUN = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{7,}\b")
_FP_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_FP_WS = re.compile(r"\s+")


# The one reserved key that stays REFUSED on the ADD path rather than being
# stripped-with-visibility (CB-56). Every other reserved key is machinery
# OUTPUT that a caller legitimately re-submits by copying a fetched card
# forward (get -> modify -> add); `resolver_errors` is different in kind — it
# is a reported FAILURE state (a resolver's annotation attempt did not land),
# and CLAUDE.md's post-add-resolver seam already documents it as "refused on
# both paths". Silently stripping it on add would make a caller's belief "my
# last observation's resolver failed" disappear with no comment, which is the
# CB-15 shape stripping-with-visibility exists to avoid for everything else.
# A literal here (not a symbol imported from db) because the runner's own key
# is not one any extension declares — `db.resolver_reserved_meta_keys()`
# always includes it unconditionally (`db.py`'s `_RESOLVER_ERRORS_KEY`) — and
# the name is already a public part of this tracker's query surface
# (`query(meta_key="resolver_errors")` is documented in CLAUDE.md), so it is
# not at risk of drifting the way an extension's own key name would.
_ADD_ALWAYS_REFUSED_META_KEYS = frozenset({"resolver_errors"})


#: The one structurally stable marker of a leaked tool-call tail (CB-90).
#: Measured on the autosorter corpus, 2026-08-25, 3373 rows: 80 descriptions
#: carry it, each EXACTLY ONCE, and the tail is always TERMINAL. The 61 rows the
#: card names by `<parameter name=` are a strict subset of those 80.
_TOOL_CALL_TAIL_MARKER = "</description>"

#: The opening twin. A leaked tail is by construction a MID-CALL slice, so it
#: carries the CLOSING tag without the opening one that would match it — 0 of
#: those 80 rows contain this string anywhere. A legitimate XML/MCP snippet
#: inside a card is balanced, and that is what tells the two apart.
_TOOL_CALL_TAIL_OPENER = "<description>"


def _strip_tool_call_tail(description: str) -> tuple[str, bool]:
    """Cut a leaked tool-call tail off *description*. Returns ``(text, was_cut)``.

    A calling agent sometimes includes a slice of its own XML-like tool call in
    the value it passes as `description`. The authored prose is COMPLETE — the
    tail is EXTRA, not a truncation (verified on three samples by reading the
    160 characters before the join, all of which end on a finished sentence), so
    there is nothing to recover and the cut is cheap.

    **The marker ALONE is NOT the predicate, and that correction is the whole of
    this function.** The unit brief prescribed "cut from `</description>` to the
    end of the string", naming this repo's own CB-90 card as the fixture that
    must survive. Measured, the card's own description contains the marker THREE
    times legitimately — inside a quoted SQL `LIKE` pattern, inside a quoted
    example of this very contamination, and inside a prose bullet naming the two
    substrings — so the prescribed predicate would have destroyed roughly 80% of
    the text of the card it was told to preserve, cutting at the FIRST occurrence.
    Widening to `<parameter name=` instead is worse for the same reason and the
    brief says so.

    The predicate is therefore COMPOUND, and each of its three conjuncts refuses
    a different legitimate shape:

    1. **The closing tag must be UNMATCHED** — no `<description>` before it. A
       leaked tail is a mid-call slice, so it carries a closing tag whose opening
       was never in the value; a card that quotes a whole XML or MCP tool
       definition is BALANCED. Measured: 0 of the 80 contaminated rows contain
       the opening tag anywhere.
    2. **Everything after the marker must be envelope** — at least one non-blank
       line. This is what refuses a QUOTATION, which is followed by more prose.
    3. **Every one of those lines starts with `<` at COLUMN ZERO.** This is what
       refuses an indented code block, which is how prose quotes markup — CB-90's
       own card indents its example four spaces. Measured: all 114 envelope lines
       across the 80 tails are unindented.

    Conjunct 1 was NOT in the brief and was added after constructing a false
    positive the designated fixture does not cover: a card about MCP surfaces
    ending in an unindented `<tool>…<description>x</description><parameter …>`
    block is cut by conjuncts 2 and 3 alone, losing 70 bytes of legitimate text.
    It costs nothing on the corpus and closes that class.

    Fail-open on anything else, deliberately (brief §4): an unrecognised shape
    stays VISIBLE in the stored text rather than being cut on a guess. The
    measured cost is one row of the 80 — a bare newline after the marker, with no
    envelope at all — which this leaves alone rather than reach a verdict from a
    vacuously-true "every line" over an empty set, the "guard reporting clean
    because it could not look" shape.

    **The residual that CANNOT be closed here, named rather than claimed away.**
    A card that ENDS on a verbatim, unindented, unfenced quotation of the leak is
    cut, and the bytes lost are the evidence the card exists to record. Adversarial
    review constructed two (76 and 38 bytes). This is not a hole to be patched: at
    that point the quotation is BYTE-IDENTICAL to the thing it quotes — unmatched
    closing tag, terminal, envelope at column zero — so no predicate reading only
    the text can separate them, and any conjunct that refused it would refuse the
    real leak too. Both ordinary ways of quoting markup already escape: a fenced
    block ends on a ``` line and an indented block is not at column zero, and each
    is verified by a test. The CLI note and the response key are what make the
    cut recoverable when it does misfire — the caller is told, and still holds
    the text it sent.

    Non-text input is returned unchanged. `description` has no type validation on
    this path today, and adding one here would be an unrequested behaviour change
    riding along inside a contamination fix (CB-82's lesson).
    """
    if not isinstance(description, str):
        return description, False
    at = description.find(_TOOL_CALL_TAIL_MARKER)
    while at >= 0:
        # Conjunct 1: this closing tag must be unmatched. Checked per candidate
        # rather than once for the whole string, because "is THIS tag matched"
        # is the question — an opening tag sitting inside the envelope after the
        # cut point would not match a marker before it.
        if _TOOL_CALL_TAIL_OPENER not in description[:at]:
            rest = description[at + len(_TOOL_CALL_TAIL_MARKER) :]
            # Conjuncts 2 and 3.
            lines = [line for line in rest.splitlines() if line.strip()]
            if lines and all(line.startswith("<") for line in lines):
                return description[:at], True
        at = description.find(_TOOL_CALL_TAIL_MARKER, at + 1)
    return description, False


def _validate_meta_keys(
    meta: dict[str, Any] | None, *, updating: bool = False
) -> tuple[dict[str, Any] | None, frozenset[str]]:
    """Validate caller meta against identity-machinery/resolver keys.

    Returns ``(meta, stripped_keys)``. On UPDATE (unchanged behavior) that is
    always ``(meta, frozenset())`` — a reserved key still raises immediately,
    except keys a resolver declared UPDATABLE at registration (similarity's
    `similar_to`): the add-side reservation stops spoofing, but a permanently
    unrepairable annotation is the CB-26 shape, so a re-scrub must be able to
    rewrite or clear it. The updatable set comes from the registry, never from
    a literal here: core findings must not know any one extension's key names.

    On ADD (CB-56), a reserved key is no longer an outright refusal. The
    get -> modify -> add round trip is a real, common caller shape — an MCP
    client that reads a card back and re-files a variant naturally carries
    whatever the machinery had stamped onto it (`similar_to`,
    `category_minted`, `occurrences`, ...) — and CSV import already handles
    this identical situation by stripping the same dynamic reserved union
    rather than refusing it (`_import_meta`), so add refusing it was one
    ingestion surface disagreeing with another over the identical input. What
    add strips is now returned to the caller, never silently: a caller must
    never be left believing its meta landed unmodified when part of it was
    machinery output that got removed before this observation reached
    identity — the CB-15 "silently dropping caller data" shape applies to a
    silent strip exactly as it applies to a silent refusal. `resolver_errors`
    is the one exception, kept as an outright refusal (see
    `_ADD_ALWAYS_REFUSED_META_KEYS`).

    `category_minted` (CB-60) is among the stripped keys on ADD: it is the
    mint gate's own output and a caller supplying it would spoof the mint
    count. It is refused on update instead, because a permanently
    unrepairable stamp is the CB-26 shape and `update(meta_update=)` must be
    able to rewrite a false one.
    """
    if not meta:
        return meta, frozenset()
    reserved = _RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
    if updating:
        reserved -= db.resolver_updatable_meta_keys()
        hit = reserved & set(meta)
        if hit:
            raise ValueError(
                f"meta keys {sorted(hit)} are reserved for the identity machinery "
                f"(they are its output, not input — strip them before re-submitting)"
            )
        return meta, frozenset()

    reserved |= _ADD_ONLY_RESERVED_META_KEYS
    hit = _ADD_ALWAYS_REFUSED_META_KEYS & set(meta)
    if hit:
        raise ValueError(
            f"meta keys {sorted(hit)} are reserved for the identity machinery "
            f"and report a FAILURE state, not input — omit them rather than "
            f"re-submitting a fetched card's failure report"
        )
    strippable = (reserved - _ADD_ALWAYS_REFUSED_META_KEYS) & set(meta)
    if not strippable:
        return meta, frozenset()
    cleaned = {k: v for k, v in meta.items() if k not in strippable}
    return cleaned, frozenset(strippable)


# --- Category canon (CB-60) -----------------------------------------------------------
#
# Category is an identity input (the auto:v1 fingerprint hashes it; similarity groups and
# annotates strictly inside it), so minting a NEW category must be a visible decision, not
# a typo: twin spellings fork identity forever, silently. The gate below lives on the
# OBSERVATION path only — add_finding / batch_add_findings with no explicit finding_id.
# An explicit id asserts identity and bypasses it (the same predicate as dedup and the
# pre-add resolvers); import_findings calls _add_one directly and stores categories
# verbatim (CB-51: an import is not an observation, and a backup with old spellings must
# restore); restore_findings is a raw INSERT and never reaches this code at all.

# Written by the gate when an observation mints a new category, so
# query(meta_key="category_minted") counts minting events. Reserved on ADD only:
# caller-supplied it would spoof that count, but an unrepairable stamp is the CB-26
# shape, so update(meta_update=) may rewrite a false one — unlike _RESERVED_META_KEYS,
# which are refused on both paths.
#
# BT-8 joins it: `fingerprint_refusals` is likewise the machinery's own output —
# a caller supplying it at filing time would spoof a number that goes to the
# owner — and likewise repairable, because an unrepairable stamp is the CB-26
# shape. Being ADD-only is also what makes `import_findings` drop it (the import
# strips this whole union): a peer tracker's refusal count is not this tracker's
# demand signal.
_ADD_ONLY_RESERVED_META_KEYS = frozenset({"category_minted", "fingerprint_refusals"})

# BT-8 (ratified 2026-08-22, seventh ratification): the dedup fork is LEFT AS IT
# IS and its refusals are COUNTED — policy by measured demand, the same move the
# `moved_file` counter makes. This unit builds EXACTLY that: no merge policy, no
# `merged` status, no fingerprint backfill. CB-46 stays open, waiting on data
# rather than on code.
#
# PUBLIC because the key IS the read surface. The number is read with
# `query(meta_key="fingerprint_refusals")` on both the MCP and the CLI side —
# no new tool and no new verb — so the name has to be something a caller can
# spell, and something a test can pin without re-typing a literal.
#
# WHAT THE NUMBER MEANS, and it must travel with the number (K-5b): it counts
# REFUSAL EVENTS, not distinct people and not distinct intentions. One caller
# retrying in a loop inflates it by one per attempt. It is a demand signal for
# "somebody wanted THIS decided card back in play while a live recurrence held
# its fingerprint", and it is a lower bound on nothing and an upper bound on
# nothing — read it as evidence that the fork is being hit, not as a headcount.
REFUSAL_COUNT_META_KEY = "fingerprint_refusals"

# Bound as a PARAMETER, never interpolated: `json_set`/`json_extract` take the
# path as a value, so this needs neither identifier validation nor an S608
# suppression -- there is no interpolated SQL here to suppress.
_REFUSAL_COUNT_JSON_PATH = f"$.{REFUSAL_COUNT_META_KEY}"

# Every `codebugs add` FLAG that writes into `meta`, paired with the meta key it
# writes and the spelling a caller types. `_cmd_add` reads this tuple twice — once
# to seed `meta` and once to refuse a conflict with the same key arriving through
# `--meta` — so the two can never drift apart, and a second such flag is covered the
# day it is declared rather than the day someone remembers the check (CB-129).
# (dest, meta key, spelling)
_ADD_META_FLAGS: tuple[tuple[str, str, str], ...] = (("lines", "lines", "-l/--lines"),)


def _levenshtein(a: str, b: str) -> int:
    """Plain edit distance. Category names are short and few — no cap needed."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def _near_hit_threshold(name: str) -> int:
    """Max edit distance at which a proposed name counts as a NEAR-MISS of an
    existing one. Conservative and length-scaled — a false "did you mean" on a
    genuinely distinct short name is worse than a missed hint, and the flag
    escapes either refusal anyway, so this only shapes the message."""
    n = len(name)
    if n < 5:
        return 0
    if n < 12:
        return 1
    return 2


def _existing_categories(conn: sqlite3.Connection) -> dict[str, str]:
    """Normalized name -> a stored spelling, for every category the table holds.

    The canon is what the TABLE holds (terminal rows included — a category used
    only by fixed cards still exists). Keyed by normalized form so a stored
    pre-CB-60 spelling ("process-improvement") still legitimizes its normalized
    twin; ORDER BY makes "which stored spelling represents it" deterministic.
    """
    out: dict[str, str] = {}
    for row in conn.execute("SELECT DISTINCT category FROM findings ORDER BY category"):
        # SQLite's dynamic typing lets an explicit-id add store a non-string
        # category. Skip it rather than raise: one legacy weird row must not
        # brick every future observation add, and a non-string name cannot
        # legitimize any observation's spelling anyway (observations are
        # normalized, and normalize_category refuses non-strings at input).
        if isinstance(row["category"], str):
            out.setdefault(normalize_category(row["category"]), row["category"])
    return out


def _gate_category(existing: dict[str, str], norm: str, *, new_category: bool) -> bool:
    """Decide whether an already-normalized category may be filed. True = it MINTS.

    An existing name (normalized-equal to any stored spelling) passes with no
    flag and mints nothing — so does ``""``, the legal uncategorized value.
    Anything else needs ``new_category=True``; without it a near-miss is refused
    naming the canonical spelling, and a genuinely new name is refused listing
    the nearest existing ones. The flag is PERMISSION, not assertion: passing it
    for an existing category is a harmless no-op.
    """
    if norm == "" or norm in existing:
        return False
    if new_category:
        return True
    ranked = sorted((_levenshtein(norm, known), known) for known in existing)
    threshold = _near_hit_threshold(norm)
    if ranked and ranked[0][0] <= threshold:
        canonical = existing[ranked[0][1]]
        raise ValueError(
            f"category {norm!r} looks like a near-miss of existing category "
            f"{canonical!r} — use the existing spelling, or pass new_category=True "
            f"to deliberately mint a new category"
        )
    if ranked:
        nearest = ", ".join(repr(existing[known]) for _, known in ranked[:3])
        hint = f"nearest existing: {nearest}"
    else:
        hint = "this tracker has no categories yet"
    raise ValueError(
        f"category {norm!r} does not exist in this tracker ({hint}); "
        f"pass new_category=True to mint it deliberately"
    )


# Occurrence ring: keep-first + keep-last, NOT drop-oldest — the earliest observations
# carry the evidence an un-merge needs, so pure drop-oldest would discard exactly what
# the too-coarse-fingerprint mitigation depends on.
_OCC_KEEP_FIRST = 10
_OCC_KEEP_LAST = 10
_OCC_DESC_CAP = 2000


def _validate_fingerprint(fingerprint: object) -> str | None:
    """Validate a caller-supplied fingerprint. None passes through (means: derive/skip).

    Rejects non-strings, empty/whitespace tokens (an empty string would become one
    global indexed identity that the ''-means-no-filter convention makes unqueryable),
    oversized values, and the reserved `auto:` prefix (otherwise a caller could collide
    with the derived namespace and the supplied/derived partition guarantee is false).
    """
    if fingerprint is None:
        return None
    if not isinstance(fingerprint, str):
        raise ValueError(f"fingerprint must be a string, got {type(fingerprint).__name__}")
    fp = fingerprint.strip()
    if not fp:
        raise ValueError("fingerprint must be non-empty")
    if len(fp) > _FP_MAX_LEN:
        raise ValueError(f"fingerprint exceeds {_FP_MAX_LEN} chars")
    if fp.startswith(_AUTO_FP_PREFIX):
        raise ValueError(f"fingerprint prefix {_AUTO_FP_PREFIX!r} is reserved for derived values")
    return fp


def _normalize_for_fingerprint(description: str, meta: dict[str, Any] | None) -> str:
    """Normalize a description to its invariant part for fallback fingerprinting.

    The load-bearing step is stripping the observation's OWN declared volatile meta
    values (sha, branch slug, log path...): measured against the real corpus,
    hex/timestamp stripping alone collapsed 0 of the 115-row family CB-43 cites —
    the blocker was the branch slug, which only the filer's meta knows. Only values
    under volatile-looking KEY names are stripped (see _VOLATILE_KEY_TOKENS for
    why), and general numbers are KEPT (rc=124 vs rc=1 is a real family split that
    must stay distinct).
    """
    text = description
    if meta:
        tokens = sorted(
            (
                v
                for k, v in meta.items()
                if isinstance(v, str) and len(v) >= 3 and _is_volatile_meta_key(k)
            ),
            key=len,
            reverse=True,
        )
        for token in tokens:
            text = text.replace(token, " ")
    # ISO strip runs BEFORE lowercasing: the pattern anchors on the uppercase
    # T/Z separators, and lowercased timestamps would survive to split the hash
    # (caught by test_hex_and_timestamp_variance_collapses failing first try).
    text = _FP_ISO_TS.sub(" ", text)
    text = text.lower()
    text = _FP_HEX_RUN.sub(" ", text)
    return _FP_WS.sub(" ", text).strip()


def normalized_identity_text(description: str, meta: dict[str, Any] | None = None) -> str:
    """Public wrapper over the fallback-fingerprint normalization (CB-43).

    The similarity extension scores over THIS text so grouping and identity
    agree on what is invariant; the algorithm itself stays private and
    versioned (`auto:v1`).
    """
    return _normalize_for_fingerprint(description, meta)


def _derive_fingerprint(
    category: str, file: str, description: str, meta: dict[str, Any] | None
) -> str:
    """Server-side fallback fingerprint: `auto:v1:` + sha256 of a canonical JSON array.

    A JSON array, not a joined string — category and file are arbitrary text, so any
    separator is ambiguous (("a|b","c") vs ("a","b|c")). `v1` versions the
    normalization algorithm so a future correction changes the prefix, not silently
    every derived hash. The `auto:` namespace cannot collide with supplied
    fingerprints because _validate_fingerprint refuses the prefix.
    """
    canonical = json.dumps(
        [category, file, _normalize_for_fingerprint(description, meta)],
        ensure_ascii=False,
    )
    return _AUTO_V1_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _occurrence_entry(
    *,
    now: str,
    severity: str,
    category: str,
    file: str,
    description: str,
    source: str,
    tags: list[str] | None,
    meta: dict[str, Any] | None,
    reported_at_commit: str | None,
    reported_at_ref: str | None,
) -> dict[str, Any]:
    """One bounded record of a deduplicated observation.

    Carries enough of the discarded observation (severity, category, description,
    file, tags, meta, refs) that a false merge can be un-merged from the ring alone.
    `meta` matters most: volatile meta values are exactly what fingerprint
    normalization strips, so without them the ring cannot show WHICH observations a
    too-coarse fingerprint merged (Codex review of this range).

    `category` is REQUIRED and written unconditionally — never `if category:` —
    because `""` is a legal category, and its absence in a ring entry would be
    indistinguishable from a pre-CB-113 entry written before the key existed.
    Category is an identity input, so a supplied-fingerprint merge across
    category spellings is reconstructable only if each entry records what was
    observed: the observation path passes the normalized spelling, import passes
    the peer's verbatim one (CB-51).
    """
    entry: dict[str, Any] = {
        "at": now,
        "severity": severity,
        "category": category,
        "file": file,
        "description": description[:_OCC_DESC_CAP],
        "source": source,
        "tags": tags or [],
        "reported_at_commit": reported_at_commit,
        "reported_at_ref": reported_at_ref,
    }
    if meta:
        entry["meta"] = meta
    return entry


def _escalated_severity(stored: object, observed: str) -> str | None:
    """The new severity when ``observed`` is MORE severe than ``stored``, else None.

    Severity is monotonic under observation (CB-52): a re-observation may raise a
    finding's severity, never lower it. `SEVERITIES` runs most-severe-first, so the
    worse of two is the one with the SMALLER rank — `max()`, over either the ranks
    or the strings, is the backwards implementation this helper exists to foreclose.

    Returns None when nothing should be written, which is what keeps the assignment
    off the SET clause entirely on the common no-change path.
    """
    if severity_rank(observed) < severity_rank(stored):
        return observed
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class BumpOutcome:
    """What a bump did, beyond the row it left behind (BT-5).

    `escalated_from` / `escalated_to` are `_bump_row`'s OWN decision, lifted out
    of the local it used to die in: `RETURNING *` hands back the POST-update row,
    so the pre-escalation severity exists nowhere else in the response. There is
    deliberately no second severity comparison anywhere in this module — the
    predicate is `_escalated_severity`, called once, and point-of-use discipline
    is the wrong enforcement layer (CB-41/CB-52).

    INVARIANT: both fields are None, or both are canonical severity strings.
    `_bump_row` is the only producer, and on the `escalate=False` path the
    comparison does not run at all, so both stay None.
    """

    row: sqlite3.Row
    escalated_from: str | None
    escalated_to: str | None


def _bump_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    now: str,
    entry: dict[str, Any],
    reopen: bool = False,
    observed_severity: str,
    escalate: bool = True,
    promote_tags: bool = True,
) -> BumpOutcome:
    """Record an occurrence on an existing finding; optionally reopen it (regression).

    ONE UPDATE, `meta` composed fully in Python and assigned exactly once (CB-16), the
    count bumped SQL-side, the mutated row captured via RETURNING and read by fetching
    — never rowcount (the RETURNING rule). Runs inside the caller's open transaction;
    the meta read-modify-write is safe only because add's whole body sits in db.txn
    (CB-24). The raw row is returned for conversion AFTER the transaction closes.

    SEVERITY IS MONOTONIC UNDER OBSERVATION (CB-52): a bump writes the more severe
    of (stored, observed) and never lowers it. `escalate=False` opts a caller out —
    exactly one does, `import_findings`, because an import is not an observation of
    this repository (CB-51) and a peer's CSV must not re-rate a local card on
    foreign evidence.

    TAGS UNION UNDER OBSERVATION (BT-4): a bump merges the observation's tags
    (`entry["tags"]`) into the `tags` column — exact string equality (no
    casefold), first-encountered order (stored before observed), deduplicated,
    the merged container serialized exactly once (CB-82). `promote_tags=False`
    opts a caller out; exactly one does, `import_findings` again, and on that
    path the column is neither read nor written — the ring still carries the
    observation's tags. Removal is deliberately NOT built here:
    `update_finding(tags=)` stays a full replace, so a hand-removed tag returns
    with the next observation carrying it (sub-decision open with the owner).

    EVERY fragment is appended to `sets` together with its parameter, and nothing is
    spliced outside the builder — that pairing is what keeps placeholder order and
    parameter order the same thing rather than two things a reader must keep in
    step. `meta = ?` used to sit in the statement template with its parameter added
    after the builder had finished, which was harmless only while the sole extension
    of `sets` was the literal `status = 'open'` (no parameter). CB-52 added the first
    parameter-consuming extension, so the hazard became live: a value appended on the
    wrong side of `meta` binds the meta JSON into `severity`, which the CHECK
    constraint rejects as an `IntegrityError` — outside this module's documented
    `ValueError`/`KeyError` contract. Making the pairing structural costs three lines
    and replaces four separate prose warnings; point-of-use discipline is the wrong
    enforcement layer (CB-41).

    Raises json.JSONDecodeError on malformed stored meta — and, on the
    `promote_tags` path, on malformed stored tags — BEFORE any write: the add
    fails cleanly with nothing landed, which is the honest half of the CB-16
    rule. The tags strict parse MOVED the malformed-stored-tags corruption
    class from post-commit (`PostCommitCorruptionError`) to pre-write (BT-4).

    Returns a `BumpOutcome`, not the bare row (BT-5): the escalation decision
    below is the only place the PRE-update severity is in hand, and the
    attention block in the response is built from it — inside the transaction,
    so the post-commit conversion path acquires no new reason to fail.
    """
    meta = json.loads(row["meta"])
    # Defensive re-typing for rows written before the reserved-key guard existed
    # (or by hand): a non-list "occurrences" must not turn the NEXT observation
    # into a TypeError. The bad value is displaced, not merged — the guard is what
    # keeps this path from being reachable for new writes.
    prior = meta.get("occurrences")
    ring = list(prior) if isinstance(prior, list) else []
    ring.append(entry)
    overflow = len(ring) - (_OCC_KEEP_FIRST + _OCC_KEEP_LAST)
    if overflow > 0:
        dropped = meta.get("occurrences_dropped")
        meta["occurrences_dropped"] = (dropped if isinstance(dropped, int) else 0) + overflow
        ring = ring[:_OCC_KEEP_FIRST] + ring[-_OCC_KEEP_LAST:]
    meta["occurrences"] = ring

    sets = "occurrence_count = occurrence_count + 1, last_seen_at = ?, updated_at = ?"
    params: list[Any] = [now, now]
    if reopen:
        prior_reg = meta.get("regressed")
        regressed = list(prior_reg) if isinstance(prior_reg, list) else []
        regressed.append({"at": now, "from_status": row["status"]})
        meta["regressed"] = regressed
        sets += ", status = 'open'"
    # Assigned at most once, from one computed value (CB-16).
    escalated_from: str | None = None
    escalated_to: str | None = None
    if escalate:
        escalated = _escalated_severity(row["severity"], observed_severity)
        if escalated is not None:
            sets += ", severity = ?"
            params.append(escalated)
            # THE single severity comparison. `row["severity"]` is still the
            # pre-UPDATE value here, which is exactly what makes this the only
            # moment `from` exists; both fields are set together (BT-5).
            escalated_from = row["severity"]
            escalated_to = escalated
    if promote_tags:
        # Strict parse of the stored column, PRE-write (symmetric with `meta`
        # at the top): the union cannot be computed from a value that does not
        # parse, so malformed stored tags fail the add with nothing landed. A
        # valid but non-list value is DISPLACED, not merged — the ring guard's
        # convention above, never a TypeError.
        stored_tags = json.loads(row["tags"])
        merged: list[Any] = []
        base = stored_tags if isinstance(stored_tags, list) else []
        for tag in (*base, *entry["tags"]):
            # `not in` is exact equality over a tiny list — no casefold, no
            # hashing (a foreign-written unhashable member must displace-safely,
            # not raise), first-encountered wins.
            if tag not in merged:
                merged.append(tag)
        if merged != stored_tags:
            # Serialized ONCE; this exact string is the bound parameter (CB-82).
            # Appended INSIDE the builder, paired with its parameter (CB-16).
            sets += ", tags = ?"
            params.append(json.dumps(merged))
    sets += ", meta = ?"
    params.append(json.dumps(meta))
    params.append(row["id"])

    return BumpOutcome(
        row=conn.execute(
            f"UPDATE findings SET {sets} WHERE id = ? RETURNING *",  # noqa: S608
            params,
        ).fetchone(),
        escalated_from=escalated_from,
        escalated_to=escalated_to,
    )


def _live_row_by_fingerprint(
    conn: sqlite3.Connection, fingerprint: str, *, exclude_id: str | None = None
) -> sqlite3.Row | None:
    """The ONE copy of the live-row-by-fingerprint predicate.

    Its status set must stay in lockstep with the partial unique index's WHERE
    (both derive from LIVE_STATUSES); three call sites hand-rolling it was three
    places for that to drift.
    """
    sql = (
        f"SELECT * FROM findings WHERE fingerprint = ? "
        f"AND status IN ({','.join('?' for _ in LIVE_STATUSES)})"
    )
    params: list[Any] = [fingerprint, *LIVE_STATUSES]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone()


def _match_fingerprint(
    conn: sqlite3.Connection, fingerprint: str
) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    """(live_row, newest_closed_row) for a fingerprint, either possibly None.

    The closed lookup is one query over all terminal statuses ordered
    `updated_at DESC, rowid DESC` — the newest row's own status class decides
    reopen-vs-recurrence, and rowid breaks the whole-second updated_at tie
    deterministically.
    """
    live = _live_row_by_fingerprint(conn, fingerprint)
    terminal = _REOPEN_STATUSES + RECURRENCE_STATUSES
    closed = conn.execute(
        f"SELECT * FROM findings WHERE fingerprint = ? "
        f"AND status IN ({','.join('?' for _ in terminal)}) "
        f"ORDER BY updated_at DESC, rowid DESC LIMIT 1",
        (fingerprint, *terminal),
    ).fetchone()
    return live, closed


#: The first attention signal (BT-5): THIS observation raised the finding's
#: severity. Severity is monotonic under observation (CB-52), so the record can
#: only ever describe an escalation — there is no de-escalation to report.
SEVERITY_ESCALATED = "severity_escalated"

#: The second attention signal (BT-5, T-15): this observation does not NAME the
#: matched row's category. It creates no fact — since T-10 the ring carries each
#: observation's category, so the divergence is derivable from two fields of one
#: response (the `category` column vs `meta.occurrences[-1].category`). What it
#: buys is top-level NAMING of a fact buried three levels deep in a ring that
#: filers demonstrably do not read; that rationale is deliberately weaker than
#: the one above, where the pre-escalation severity exists nowhere else at all.
CATEGORY_DIVERGENCE = "category_divergence"

#: The two record forms. Functional `TypedDict` syntax is MANDATORY for the first:
#: `from` is a Python keyword, so the class form cannot spell the field at all.
#: Each `Literal` must repeat its constant's text — `Literal[SEVERITY_ESCALATED]`
#: is not a legal type — so the totality test asserts they agree rather than
#: leaving a second spelling free to drift.
#:
#: A UNION, never one dict with `total=False`: optional fields would admit a
#: record carrying neither form's required keys, and collapsing the two
#: `Literal`s would destroy the pin on the signal names themselves. The name
#: `AttentionRecord` survives as the alias of the union, so `AddOutcome` and
#: `_attention_records` keep their annotations.
SeverityEscalatedRecord = TypedDict(
    "SeverityEscalatedRecord",
    {"signal": Literal["severity_escalated"], "from": str, "to": str},
)
CategoryDivergenceRecord = TypedDict(
    "CategoryDivergenceRecord",
    {"signal": Literal["category_divergence"], "observed": str, "stored": str},
)
AttentionRecord = SeverityEscalatedRecord | CategoryDivergenceRecord

#: Which signals each dedup branch may emit. LIVE, not documentation: the builder
#: below reads it, so a wrong cell changes the response rather than only a test.
#:
#: The two insert branches no longer share one reason, and this paragraph used to
#: say they did. `created` is empty STRUCTURALLY: nothing matched, so neither
#: signal has a second side — no stored severity to raise and no stored category
#: to disagree with. `recurrence_of_closed` is empty of the ESCALATION for the
#: other half of that reason (it is an insert path, `_bump_row` never runs, which
#: is also why it reports `was_new: True`) — but a matched row DOES exist there:
#: the dismissed twin the branch deliberately leaves closed (a decision stays
#: decided). So the category comparison is well-defined against the twin, and the
#: cell is not empty. A new dedup branch must be classified here; the builder
#: raises `KeyError` on an unknown action rather than returning an empty list,
#: because "evaluated, nothing fired" is the one meaning an unclassified branch
#: must not be able to borrow. `tests/test_dedup.py::TestAttentionSignalMatrix`
#: derives the key set from `_add_one`'s own AST, so the table cannot fall behind.
_ATTENTION_SIGNALS_BY_ACTION: dict[str, frozenset[str]] = {
    "created": frozenset(),
    "bumped": frozenset({SEVERITY_ESCALATED, CATEGORY_DIVERGENCE}),
    "reopened": frozenset({SEVERITY_ESCALATED, CATEGORY_DIVERGENCE}),
    "recurrence_of_closed": frozenset({CATEGORY_DIVERGENCE}),
}


def _attention_records(
    dedup_action: str,
    bump: BumpOutcome | None,
    *,
    observed_category: str,
    stored_category: object,
) -> tuple[AttentionRecord, ...]:
    """The attention block for one observation — built INSIDE the transaction.

    Two things must both hold for a record to appear: the branch admits the
    signal (`_ATTENTION_SIGNALS_BY_ACTION`), and the observation itself reported
    it. The severity comparison is NOT repeated here — `_bump_row` already made
    it, and one predicate is the whole point (CB-41/CB-52). On the insert
    branches `bump` is None because `_bump_row` did not run; `stored_category` is
    None there too, EXCEPT on the recurrence branch, where the matched row is the
    dismissed twin the branch leaves closed.

    CATEGORY: both sides are normalized, so a difference of SPELLING
    (`Process Improvement` / `process-improvement`) is not a signal and a
    difference of NAME is. A non-string `stored_category` is SKIPPED, not raised
    on — the same policy as `_existing_categories`, for the same reason stated
    there: SQLite's dynamic typing lets a legacy or explicit-id row hold one, and
    one such row must not brick every future observation. The observed side needs
    no guard: `add_finding`/`batch_add_findings` have already run
    `normalize_category` on it (which refuses a non-string) and `import_findings`
    passes a string built from the CSV row, so it is a `str` on all three
    branches that can emit this signal. It is normalized here ANYWAY, and the
    honest reason is not that some caller needs it: NO caller can observe that
    half today — the observation path normalizes before `_add_one`, and the
    import path, the only one carrying a verbatim spelling (CB-51), throws the
    record away. It is normalized because "both sides" is THIS FUNCTION's
    contract rather than a coincidence of who happens to call it. No behavioural
    test can discriminate it while that holds, so it is pinned by a direct call
    in `TestAttentionSignalMatrix` and SAID here rather than claimed as covered
    — the same rule CB-82 states for a guard its callers make unreachable.

    Ordering is deterministic (this body appends in one fixed order) and each
    signal type appends at most once, so the list is a stable contract rather
    than a set that happens to serialize.
    """
    allowed = _ATTENTION_SIGNALS_BY_ACTION[dedup_action]  # fail-closed on a new branch
    records: list[AttentionRecord] = []
    if SEVERITY_ESCALATED in allowed and bump is not None and bump.escalated_from is not None:
        records.append(
            {
                "signal": SEVERITY_ESCALATED,
                "from": bump.escalated_from,
                "to": bump.escalated_to,
            }
        )
    if CATEGORY_DIVERGENCE in allowed and isinstance(stored_category, str):
        stored_norm = normalize_category(stored_category)
        observed_norm = normalize_category(observed_category)
        if observed_norm != stored_norm:
            records.append(
                {
                    "signal": CATEGORY_DIVERGENCE,
                    "observed": observed_norm,
                    "stored": stored_norm,
                }
            )
    return tuple(records)


@dataclasses.dataclass(frozen=True, slots=True)
class AddOutcome:
    """What one observation did — `_add_one`'s typed return (BT-5).

    Replaces a positional 4-tuple whose splat in `batch_add_findings` made a
    fifth member unaddable without touching every call site. Exactly one of
    `inserted` / `raw_row` is non-None, the same contract the tuple carried.

    `attention` is a TUPLE: the outcome is frozen, and every response gets its
    own `list()` of it, so no two members of a batch can share one mutable list.
    """

    inserted: dict[str, Any] | None
    raw_row: sqlite3.Row | None
    was_new: bool
    dedup_action: str
    attention: tuple[AttentionRecord, ...]


def _add_one(
    conn: sqlite3.Connection,
    *,
    severity: str,
    category: str,
    file: str,
    description: str,
    source: str,
    tags: list[str] | None,
    meta: dict[str, Any] | None,
    finding_id: str | None,
    reported_at_commit: str | None,
    reported_at_ref: str | None,
    fingerprint: str | None,
    annotate: bool = True,
    escalate: bool = True,
    promote_tags: bool = True,
    new_category: bool = False,
    gate_category: bool = True,
    project_dir: str | None = None,
) -> AddOutcome:
    """One observation through the identity function, inside an OPEN transaction.

    Returns an `AddOutcome` — exactly one of `inserted` / `raw_row` is non-None. An
    inserted row is converted here (its meta is the JSON this call just serialized,
    so conversion cannot fail) because post-add hooks need the dict; a matched row is
    returned RAW for conversion after the caller's transaction closes, since its
    stored meta is legacy data (CB-24 consequence 2).

    The `attention` block is assembled HERE, not in `_finalize_add` (BT-5): this is
    the frame that holds the `BumpOutcome`, and it is inside the transaction, so the
    post-commit conversion path stays mechanical and gains no new way to fail.

    `severity` and `fingerprint` arrive already validated — validation must run before
    the caller opens its transaction, so invalid input raises immediately instead of
    an OperationalError after a busy_timeout wait under contention. `category`
    likewise arrives already normalized on the observation path (verbatim on the
    import path, CB-51) — but the CB-60 mint GATE runs HERE, on the insert
    continuation, AFTER the bump/reopen branches have returned (CB-113(b)): an
    observation of a known live or reopenable card creates nothing, so refusing it
    over a category spelling would lose the occurrence — count, ring evidence,
    regression reopen — about a row that already exists. Only an observation that
    would CREATE a row (`finding_id is None and gate_category`, plain insert and
    recurrence_of_closed alike) is gated; a permitted mint stamps
    `meta.category_minted`. `gate_category=False` opts a caller out — exactly one
    does, `import_findings` (an import is not an observation), pinned by
    `tests/test_category_gate.py::TestGateCategoryOptOutRatchet`.
    """
    now = utc_now()
    dedup_action = "created"
    recurrence_of: str | None = None
    # The matched row's stored category, assigned at EVERY match site below and
    # nowhere else (BT-5 T-15). One variable initialized before the branching,
    # rather than carrying `closed` into the insert continuation: `closed` exists
    # only inside the `finding_id is None` branch, and on the explicit-id path
    # there is no matched row at all. Deliberately typed `object` — SQLite's
    # dynamic typing means a stored category need not be a `str`, and the
    # skip-don't-raise policy lives in `_attention_records`.
    matched_category: object = None

    if finding_id is None:
        if fingerprint is None:
            fingerprint = _derive_fingerprint(category, file, description, meta)
        live, closed = _match_fingerprint(conn, fingerprint)
        entry = _occurrence_entry(
            now=now,
            severity=severity,
            category=category,
            file=file,
            description=description,
            source=source,
            tags=tags,
            meta=meta,
            reported_at_commit=reported_at_commit,
            reported_at_ref=reported_at_ref,
        )
        if live is not None:
            matched_category = live["category"]
            bump = _bump_row(
                conn,
                live,
                now=now,
                entry=entry,
                observed_severity=severity,
                escalate=escalate,
                promote_tags=promote_tags,
            )
            return AddOutcome(
                inserted=None,
                raw_row=bump.row,
                was_new=False,
                dedup_action="bumped",
                attention=_attention_records(
                    "bumped",
                    bump,
                    observed_category=category,
                    stored_category=matched_category,
                ),
            )
        if closed is not None and closed["status"] in _REOPEN_STATUSES:
            matched_category = closed["category"]
            bump = _bump_row(
                conn,
                closed,
                now=now,
                entry=entry,
                reopen=True,
                observed_severity=severity,
                escalate=escalate,
                promote_tags=promote_tags,
            )
            # Fire like update_finding does: the write changed the row, inside this
            # transaction, so claims/milestone reconciliation land atomically with it.
            db.run_status_change_hooks(conn, closed["id"], closed["status"], "open")
            return AddOutcome(
                inserted=None,
                raw_row=bump.row,
                was_new=False,
                dedup_action="reopened",
                attention=_attention_records(
                    "reopened",
                    bump,
                    observed_category=category,
                    stored_category=matched_category,
                ),
            )
        if closed is not None:
            # A wont_fix / not_a_bug closure is a DECISION, not a fix — it stays
            # closed, and the recurrence becomes a new row that keeps the link.
            # The twin is the MATCHED row and it survives to the insert
            # continuation exactly as `recurrence_of` does, which is what makes
            # the category comparison there a comparison against the twin.
            matched_category = closed["category"]
            recurrence_of = closed["id"]
            dedup_action = "recurrence_of_closed"
    elif fingerprint is not None:
        # An explicit id asserts identity, so no dedup matching — but a supplied
        # fingerprint colliding with a live row would otherwise surface as a raw
        # IntegrityError from the partial unique index at INSERT time.
        live = _live_row_by_fingerprint(conn, fingerprint)
        if live is not None:
            raise ValueError(
                f"fingerprint already held by live finding {live['id']}; "
                f"omit finding_id to record an occurrence on it instead"
            )

    # The CB-60 mint gate, on the insert continuation ONLY (CB-113(b)): the
    # bump/reopen branches returned above, so an observation of a known card was
    # recorded regardless of its category spelling — the ring carries the observed
    # form as evidence. From here on this observation CREATES a row (plain insert
    # or recurrence_of_closed), which is where an ungated spelling forks identity.
    # The explicit-id predicate is preserved: an asserted identity stores its
    # category verbatim, ungated, as before.
    mint_category = False
    if finding_id is None and gate_category:
        mint_category = _gate_category(
            _existing_categories(conn), category, new_category=new_category
        )

    fid = finding_id or _next_id(conn)
    meta_final = dict(meta or {})
    if recurrence_of is not None:
        meta_final["recurrence_of"] = recurrence_of
    if mint_category:
        # The gate decided this observation MINTS its category; the stamp makes
        # minting countable (query(meta_key="category_minted")). Lands only on
        # the insert path — a dedup bump returned above records no minting.
        meta_final["category_minted"] = True
    if finding_id is None and annotate:
        # Pre-add resolvers (CB-45): annotate-only. THE predicate is
        # `finding_id is None` — an explicit id asserts identity and bypasses
        # the observation machinery (dedup, hooks, resolvers alike). NOT a
        # dedup_action test: explicit-id inserts also carry "created".
        meta_final.update(
            db.run_pre_add_resolvers(
                conn,
                {
                    "finding_id": fid,
                    "severity": severity,
                    "category": category,
                    "file": file,
                    "description": description,
                    "source": source,
                    "tags": list(tags or []),
                    "meta": dict(meta or {}),
                    "fingerprint": fingerprint,
                    "dedup_action": dedup_action,
                    "recurrence_of": recurrence_of,
                    "at": now,
                    # The two keys BT-7 Р3 adds, and they travel together on
                    # purpose. The observation carried neither the revision the
                    # finding is reported against nor any statement of WHICH
                    # WORKING TREE it is about, so a resolver that wants to read
                    # code as of that revision had no way to ask. `project_dir`
                    # is the caller's assertion about the tree; it is passed
                    # through verbatim and NEVER defaulted to the process cwd
                    # here, because a resolver that silently reads whatever tree
                    # the process happens to stand in is the confidently-wrong
                    # answer BT-7 spent a review round closing. A resolver with
                    # no root fails closed instead.
                    "reported_at_commit": reported_at_commit,
                    "project_dir": project_dir,
                },
                forbidden=_RESERVED_META_KEYS,
            )
        )
    conn.execute(
        """INSERT INTO findings (id, severity, category, file, status, description,
           source, tags, meta, reported_at_commit, reported_at_ref, created_at, updated_at,
           fingerprint, last_seen_at)
           VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fid,
            severity,
            category,
            file,
            description,
            source,
            json.dumps(tags or []),
            json.dumps(meta_final),
            reported_at_commit,
            reported_at_ref,
            now,
            now,
            fingerprint,
            now,
        ),
    )
    result = db.row_to_dict(conn.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone())
    db.run_post_add_hooks(conn, result)
    # The insert path: `_bump_row` never ran, so there is no escalation to report
    # and `_attention_records` is handed no bump. It still runs, because the
    # branch must be CLASSIFIED — an action the matrix does not know raises here.
    # `matched_category` is None on a plain `created`, and the dismissed twin's
    # on `recurrence_of_closed` — the one insert branch that HAS a matched row.
    return AddOutcome(
        inserted=result,
        raw_row=None,
        was_new=True,
        dedup_action=dedup_action,
        attention=_attention_records(
            dedup_action,
            None,
            observed_category=category,
            stored_category=matched_category,
        ),
    )


class PostCommitCorruptionError(Exception):
    """The dedup write COMMITTED, then the matched row's stored data failed to parse.

    Distinct from the pre-write ``json.JSONDecodeError`` that ``_bump_row``
    raises on malformed stored ``meta`` — and, since BT-4, on malformed stored
    ``tags`` on the ``promote_tags`` path — there the transaction rolls back and
    nothing lands. A caller deciding "did the observation land?" must not have
    to guess between the two (Codex round-3 review). Raised ONLY when the add's
    own frame committed: under an ambient transaction nothing has committed yet
    and the raw ``JSONDecodeError`` propagates to the owning frame instead,
    which typically abandons the whole unit with it — same contract as
    ``update_finding``'s conversion (Codex round 4). The UPDATE path keeps
    raising raw ``JSONDecodeError`` on its own committed path too,
    deliberately: its CLI re-raise contract is pinned by
    ``TestRetriageCliContract``.

    HONEST REACHABILITY NOTE (BT-4): for ``tags`` this class is no longer
    reachable through the bump paths that call ``_finalize_add`` — with
    ``promote_tags=True`` the strict parse raises pre-write, and the one
    ``promote_tags=False`` caller (``import_findings``) never calls
    ``_finalize_add`` at all. The class is NOT deleted: it is the defensive
    classifier for any frame that committed and only then failed to serialize
    (``_finalize_add`` still parses the whole returned row), and the
    minimal-form decision is to keep that guard rather than reason it away.
    """


#: Keys `_finalize_add` writes onto the response that are NOT columns of
#: `findings`. Declared rather than left implicit so a ratchet can hold two
#: things at once (judge W-4): that this really is what the constructor writes
#: (AST), and that none of them can ever shadow a column — a future `attention`
#: column would otherwise be silently overwritten in every response, the CB-16
#: shape one layer up. Pinned by `tests/test_dedup.py::TestResponseOnlyKeysRatchet`.
_RESPONSE_ONLY_KEYS = frozenset(
    {
        "was_new",
        "dedup_action",
        "attention",
        "stripped_meta_keys",
        "stripped_description_tail",
    }
)


def _finalize_add(
    outcome: AddOutcome,
    *,
    committed: bool,
    stripped_meta_keys: frozenset[str] = frozenset(),
    stripped_description_tail: bool = False,
) -> dict[str, Any]:
    """Convert an _add_one outcome to the response dict, AFTER the transaction closed.

    MECHANICAL BY CONTRACT (BT-5): conversion plus key writes, no computation and
    no new failure mode. Every signal was decided inside the transaction, so the
    post-commit path — the one that can already only report `PostCommitCorruptionError`
    — never acquires a second reason to fail. `stripped_meta_keys` is likewise
    handed in already decided: it was computed by `_validate_meta_keys` before the
    transaction even opened (CB-56), so writing it here is the same "conversion
    plus key writes" as `attention`, not a second computation site.

    `was_new` / `dedup_action` / `attention` / `stripped_meta_keys` are
    response-only keys (see `_RESPONSE_ONLY_KEYS`), not columns.
    `stripped_meta_keys` follows the `attention` discipline (BT-5) exactly:
    present UNCONDITIONALLY, on every branch, and an empty list is a normal
    answer meaning "checked, nothing to strip" — never "no such channel". A
    caller that copies a fetched card's `meta` forward (get -> modify -> add)
    must be able to tell, from this response alone, which of its own keys
    silently did not land (CB-56); the alternative — the key only appearing
    when something WAS stripped — collapses exactly the distinction BT-5 was
    built to keep. ``committed`` is this frame's ``db.txn``
    ownership result: only a frame that actually committed may classify a
    conversion failure as post-commit — under an ambient transaction the owner
    will normally roll the unit back, so claiming "recorded" would mislead
    retry/accounting logic.

    The insert path builds the response as a SHALLOW COPY of the dict the post-add
    hooks were handed (CB-119). Copying HERE and not at the hook seam is the whole
    point: hooks run inside `_add_one`, this frame runs after the transaction
    closed, so a hook's mutations still reach the response — only the aliasing
    dies. A hook that RETAINS the dict used to watch response-only keys appear on
    it later. Shallow is deliberate and sufficient: every response-only key is
    top-level, and the nested `meta` object is shared on purpose — this is not a
    defensive deep copy, it is an alias cut at exactly one level.
    """
    if outcome.inserted is not None:
        result = dict(outcome.inserted)
    else:
        try:
            result = db.row_to_dict(outcome.raw_row)
        except json.JSONDecodeError as e:
            if committed:
                raise PostCommitCorruptionError(
                    f"occurrence recorded on {outcome.raw_row['id']}, but its stored data "
                    f"could not be serialized: {e}"
                ) from e
            raise
    result["was_new"] = outcome.was_new
    result["dedup_action"] = outcome.dedup_action
    # A FRESH list per response: the outcome holds a tuple precisely so no two
    # members of one batch can be handed the same mutable object.
    result["attention"] = list(outcome.attention)
    # UNCONDITIONAL, like `attention` (CB-56/BT-5 discipline): `[]` means
    # "checked, nothing to strip", never "no such channel". A fresh sorted
    # list per response for the same reason `attention` gets a fresh list.
    result["stripped_meta_keys"] = sorted(stripped_meta_keys)
    # UNCONDITIONAL for the same reason (CB-90): `False` means "checked, nothing
    # to cut", never "no such channel". A caller must be able to tell from this
    # response ALONE that the text it passed is not the text that was stored.
    result["stripped_description_tail"] = stripped_description_tail
    return result


def add_finding(
    conn: sqlite3.Connection,
    *,
    severity: str,
    category: str,
    file: str,
    description: str,
    source: str = "human",
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    finding_id: str | None = None,
    reported_at_commit: str | None = None,
    reported_at_ref: str | None = None,
    fingerprint: str | None = None,
    annotate: bool = True,
    new_category: bool = False,
    project_dir: str | None = None,
) -> dict[str, Any]:
    """Record an observation. Returns the created OR matched finding as a dict.

    ``annotate=False`` skips the pre-add resolvers (CB-45) for this insert;
    used by CSV import — an import is not an observation.

    Category canon (CB-60), on the OBSERVATION path only (``finding_id is
    None`` — the same predicate as dedup and the resolvers; an explicit id
    asserts identity and stores the category verbatim): the spelling is
    normalized via ``types.normalize_category`` BEFORE fingerprint derivation,
    so twin spellings hash and store one canonical name. The mint GATE is
    decided AFTER the dedup branch is known (inside ``_add_one``, CB-113(b)):
    an observation matching a known live or reopenable card is recorded
    ALWAYS — bump and reopen never raise over a category spelling, the ring
    carries the observed form as evidence. Only an observation that would
    CREATE a row (plain insert or recurrence-of-closed) is gated: a category
    the tracker does not already hold requires ``new_category=True`` — without
    it a near-miss of an existing name raises ``ValueError`` naming the
    canonical spelling, and a genuinely new name raises listing the nearest
    existing ones. A permitted mint stamps ``meta.category_minted: true`` so
    ``query(meta_key="category_minted")`` counts minting events. ``""`` stays
    a legal, ungated category.

    Identity (CB-43): an observation whose ``fingerprint`` matches a live finding
    bumps that finding's ``occurrence_count`` and returns it (``was_new: False,
    dedup_action: "bumped"``); a match on a ``fixed`` finding REOPENS it as a
    regression (``"reopened"``); a match on a ``wont_fix``/``not_a_bug`` finding
    creates a new row linked via ``meta.recurrence_of`` (``"recurrence_of_closed"``).
    No fingerprint supplied → a conservative ``auto:v1:`` fallback is derived from
    (category, file, normalized description). An explicit ``finding_id`` asserts
    identity and bypasses both derivation and matching.

    ``fingerprint`` is frozen at insert and is NOT settable via ``update_finding`` —
    deliberately immutable for now (re-keying a live card must renegotiate the
    partial unique index; that is a future card, not an accident to enable here).

    ``severity`` is normalized, not exact-matched: case and surrounding whitespace
    are forgiven, aliases are not (CB-19). The stored value is always canonical.

    ``source``, top-level ``meta`` and ``reported_at_ref`` are observation-frozen
    (BT-4): a dedup bump never updates those columns — the occurrence ring
    (``meta.occurrences``) carries each later observation's values as evidence.

    ``project_dir`` names the WORKING TREE this observation is about, for the
    pre-add resolvers — nothing on this call path reads it otherwise, and it is
    never stored. It exists because the observation literal could not say which
    tree a finding's ``file`` and ``reported_at_commit`` are coordinates IN, so
    a resolver wanting to read the code as of that revision (BT-7 Р3) had no way
    to ask. It is deliberately NOT defaulted to the process cwd: a long-lived
    server's cwd has nothing to do with the tracker a call writes to, so a
    resolver reading whatever tree the process stands in would produce a
    confidently wrong answer rather than an absent one. Omitted, resolvers that
    need a tree fail closed and record why.

    The whole body runs in ``db.txn`` — the fingerprint lookup plus conditional
    write is a read-modify-write (CB-24). Do not restore a ``conn.commit()`` here:
    ``db.txn`` yields ``False`` under an ambient transaction, and committing then
    would commit the *caller's* work.
    """
    severity = resolve_severity(severity)
    fingerprint = _validate_fingerprint(fingerprint)
    meta, stripped_meta_keys = _validate_meta_keys(meta)
    stripped_description_tail = False
    if finding_id is None:
        # Pure normalization stays pre-txn (it is validation, and it is the
        # fingerprint-derivation input); the mint GATE runs inside _add_one on
        # the insert continuation, after the dedup branch is known (CB-113(b)).
        category = normalize_category(category)
        # Same predicate, same phase, and the phase is load-bearing (CB-90):
        # `description` is an `auto:v1` input, so a tail cut AFTER derivation
        # would leave a tailed and a clean report of one defect on two hashes,
        # i.e. two cards. Cutting here makes them collapse correctly.
        description, stripped_description_tail = _strip_tool_call_tail(description)

    with db.txn(conn) as owned:
        outcome = _add_one(
            conn,
            severity=severity,
            category=category,
            file=file,
            description=description,
            source=source,
            tags=tags,
            meta=meta,
            finding_id=finding_id,
            reported_at_commit=reported_at_commit,
            reported_at_ref=reported_at_ref,
            fingerprint=fingerprint,
            annotate=annotate,
            new_category=new_category,
            project_dir=project_dir,
        )
    return _finalize_add(
        outcome,
        committed=owned,
        stripped_meta_keys=stripped_meta_keys,
        stripped_description_tail=stripped_description_tail,
    )


# Member keys accepted by batch_add_findings. The strict-argument middleware guards
# top-level MCP tool arguments only; per-member dicts pass through it freely, so a
# typo'd key here ("fingerprit") would otherwise be silently dropped and the fallback
# fingerprint would engage — a success payload with the caller's identity key
# discarded, CB-15's failure mode inside CB-43's fix.
_BATCH_MEMBER_KEYS = frozenset(
    {
        "id",
        "severity",
        "category",
        "file",
        "description",
        "source",
        "tags",
        "meta",
        "reported_at_commit",
        "reported_at_ref",
        "fingerprint",
    }
)


class _ValidatedMember(NamedTuple):
    """One `batch_add_findings` member after the pre-transaction cleaning pass.

    Exists so the second loop cannot reach past it to the RAW member dict. Four
    of these seven fields are values the cleaning pass CHANGED — a category
    normalized, a fingerprint validated, a meta stripped of reserved keys, a
    description stripped of a tool-call tail — and each is reported to the caller
    as having been changed. Reading the raw dict again for any of them would
    store the uncleaned value behind a response that says otherwise. Named
    fields make that a compile-time impossibility instead of a comment.
    """

    severity: str
    fingerprint: str | None
    category: str
    description: str
    meta: dict[str, Any] | None
    stripped_meta_keys: frozenset[str]
    stripped_description_tail: bool


class ImportRowError(NamedTuple):
    """One row the import could not use. `label` names it for the operator."""

    label: str
    message: str


class ImportReport(NamedTuple):
    """What an import did. Counts are per CSV row, not per database write.

    `skipped_present` and `skipped_decided` are separate because they are
    different facts and the operator reads them: a row skipped because this
    tracker already holds it is "already present", while a row skipped because
    recording it would have REOPENED a decided card is not present at all.
    Collapsing them printed the second as the first — a success-shaped mislabel
    in the one line a human reads (CB-15/CB-16 family).
    """

    imported: int
    merged: int
    skipped_present: int
    skipped_decided: int
    errors: list[ImportRowError]


def _import_meta(row: dict[str, Any], dropped_keys: frozenset[str] | set[str]) -> dict[str, Any]:
    """The meta an imported row may carry: JSON or dict in, reserved keys out.

    Stripping lives HERE, not in the CLI handler, and that is the point of
    CB-51: the four identity rules had accumulated in a presentation layer, so
    the domain validated meta that only one caller had sanitized. The reserved
    union is DYNAMIC (`db.resolver_reserved_meta_keys()`), so a resolver added
    later cannot slip a key through. The machinery's OUTPUT is not its input:
    an exported `similar_to` / `resolver_errors` / `recurrence_of` is dropped,
    not refused, or a tracker's own export would be unimportable.
    """
    raw = row.get("meta")
    if isinstance(raw, str):
        raw = raw.strip()
        try:
            raw = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raw = {}
    meta = {k: v for k, v in raw.items() if k not in dropped_keys} if isinstance(raw, dict) else {}

    lines = (row.get("lines") or row.get("Lines") or "").strip()
    if lines:
        meta["lines"] = lines
    return meta


def _import_tags(row: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    """`(tags, error)` — a list in, a list out; JSON in, a list out.

    Returns an error rather than dropping a malformed value silently: the row
    has an error channel, and "your tags vanished" with no message is the kind
    of quiet loss this card exists to remove.
    """
    raw = row.get("tags")
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None, "tags column is not valid JSON"
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        return None, "tags must be a JSON array of strings"
    return raw, None


def import_findings(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> ImportReport:
    """Fold observations from another source into this tracker, in ONE transaction.

    ROW CONTRACT: each row is a mapping in the shape `export-csv` writes, i.e.
    what `csv.DictReader` yields — every value may be a string. `meta` and `tags`
    may arrive as JSON text OR as an already-decoded dict/list, so the CLI and a
    library caller hand over the same thing. Only `category`, `file` and
    `description` are required; a row missing one is not a finding and is passed
    over silently.

    An import is NOT an observation of this repository (CB-45): it carries no
    provenance of its own, so `annotate=False` — the pre-add resolvers never run.
    Routing this through `batch_add_findings` was designed and REJECTED in review:
    that function has no `annotate` parameter, so every imported row would have
    run the similarity resolver (trigram work over up to 500 candidates) inside
    the held write lock, silently; and it validates every member before opening
    its transaction, which forbids the per-row error partitioning below.

    THE WHOLE LOOP IS ONE TRANSACTION (CB-77). A read failure part-way through a
    large CSV used to leave N rows committed behind it and escape as a traceback;
    the caller's decision, ratified 2026-08-18, is all-or-nothing. Consequence the
    caller must honour: on an exception NOTHING landed, so a count must not be
    printed.

    TWO IMPORT-SPECIFIC RULES, each closing a reproduced defect:

    1. **An import never reopens a decided card.** A fingerprint hit on a
       `fixed` row — the whole of `_REOPEN_STATUSES` — would normally REOPEN it
       as a regression — correct for an observation, wrong for an import, which
       is a statement about someone else's tracker. (`stale` is a LIVE status:
       a hit on it bumps, it never reopens.) Measured before the fix: a foreign
       row whose id did not even exist locally flipped a local `fixed` card to
       `open`. A hit on a LIVE row still bumps (another sighting of a card you
       already have is a real occurrence), and a `wont_fix` hit still files a
       linked recurrence row — neither is a filed defect and neither is changed
       here.

    2. **An id identifies a row only together with its content.** The guard this
       replaces asked `SELECT 1 FROM findings WHERE id = ?`, which is bare-id
       existence, not identity. Every tracker numbers CB-1, CB-2, …, so a foreign
       export lost every row whose NUMBER was taken locally — measured, all three
       peer rows dropped into a 3-row tracker, reported as "3 already present".
       Worse, ids MINTED BY THIS IMPORT collide with later rows of the same file:
       because `export-csv` orders by severity rather than id, restoring a backup
       into an EMPTY tracker silently dropped rows (measured: 3 out, 2 back,
       exit 0). Comparing content as well as id fixes both while keeping what the
       old guard was really for — re-importing your own export is still a no-op.

    Why the id check is not simply deleted: rows written with an explicit id store
    `fingerprint = NULL` (measured), and NULL matches nothing, so a fingerprint-only
    skip cannot see them and they duplicate on every re-import. That population is
    every pre-CB-43 row and every explicit-id row.
    """
    imported = merged = skipped_present = skipped_decided = 0
    errors: list[ImportRowError] = []
    # The add-only reservation (category_minted, CB-60) is stripped here too, or an
    # export carrying a mint stamp would be refused row-by-row on re-import — and a
    # peer's minting decision is theirs, not this tracker's.
    dropped_keys = (
        _RESERVED_META_KEYS | _ADD_ONLY_RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
    )

    with db.txn(conn):
        for index, row in enumerate(rows, start=1):
            row_id = (row.get("id") or "").strip()
            label = row_id or f"row {index}"

            category = (row.get("category") or row.get("Category") or "").strip()
            file = (row.get("file") or row.get("File") or "").strip()
            description = (row.get("description") or row.get("Description") or "").strip()
            if not category or not file or not description:
                continue  # not an error: an incomplete row is not a finding

            if row_id and _same_finding_exists(conn, row_id, category, file, description):
                skipped_present += 1
                continue

            meta = _import_meta(row, dropped_keys)
            if row_id:
                # The origin survives renumbering. Without this the row lands with
                # a local id and no record that it came from someone else's CB-N.
                meta["imported_id"] = row_id

            # Empty is NOT supplied — a CSV reader yields "" for every absent
            # column. The `auto:` strip is one of the four identity rules CB-51
            # was filed about: an exported derived fingerprint carries the
            # reserved prefix, which callers may not supply, and passing None
            # re-derives the identical value server-side.
            fingerprint = (row.get("fingerprint") or "").strip() or None
            if fingerprint and fingerprint.startswith(_AUTO_FP_PREFIX):
                fingerprint = None

            try:
                tags, tags_error = _import_tags(row)
                if tags_error is not None:
                    raise ValueError(tags_error)
                severity = resolve_severity(row.get("severity") or row.get("Severity") or "medium")
                fingerprint = _validate_fingerprint(fingerprint)
                _validate_meta_keys(meta)

                would_reopen, fingerprint = _import_would_reopen(
                    conn, fingerprint, category, file, description, meta
                )
                if would_reopen:
                    skipped_decided += 1
                    continue

                outcome = _add_one(
                    conn,
                    severity=severity,
                    category=category,
                    file=file,
                    description=description,
                    source=(row.get("source") or row.get("Source") or "import").strip(),
                    tags=tags,
                    meta=meta or None,
                    finding_id=None,
                    reported_at_commit=None,
                    reported_at_ref=None,
                    fingerprint=fingerprint,
                    annotate=False,
                    # An import is not an observation (CB-51), so it records the
                    # occurrence but does not RE-RATE the local card: a peer's
                    # tracker calling their copy `critical` is their assessment of
                    # their repository, not evidence about this one. Same reason
                    # `annotate=False` sits one line above.
                    escalate=False,
                    # Same class of reason (BT-4): an import is not an observation,
                    # so a peer's tags are not promoted into the local column —
                    # the ring records them, the column stays this tracker's own.
                    promote_tags=False,
                    # Same class of reason again (CB-60/CB-113): an import is not
                    # an observation, so a peer's category lands verbatim, ungated
                    # and unstamped — a backup with old or foreign spellings must
                    # restore. The single sanctioned opt-out; pinned by
                    # TestGateCategoryOptOutRatchet.
                    gate_category=False,
                )
            except ValueError as e:
                errors.append(ImportRowError(label, str(e)))
                continue

            # An import reads the outcome's counters and nothing else — it never
            # calls `_finalize_add`, so the attention block has no path to a
            # caller here and needs no opt-out flag (BT-5 section D; a flag would
            # be dead code). Pinned by TestImportCarriesNoAttention.
            if outcome.was_new:
                imported += 1
            else:
                merged += 1

    return ImportReport(imported, merged, skipped_present, skipped_decided, errors)


class RestoreReport(NamedTuple):
    """What a restore did. `restored` is rows written; there is no partial state."""

    restored: int


#: Every column a faithful restore writes. Declared once, used to build the INSERT and
#: checked against the live schema by `TestRestoreWritesEveryColumn` — a column added to
#: `findings` and forgotten here would be silently dropped by every future restore, which
#: is precisely the class of quiet loss this card exists to remove.
#: Export page size. A module constant so a test can shrink it and actually exercise the
#: loop — with the page hardcoded, "it pages" is an unfalsifiable claim short of building a
#: 100k-row fixture.
_EXPORT_PAGE = 5000

#: Ids per collision-check statement. SQLite's `SQLITE_LIMIT_VARIABLE_NUMBER` is 32766 on
#: this build (999 on older ones), and one placeholder per row meant a large enough backup
#: raised `too many SQL variables` and could not be restored at all — measured: 40000
#: placeholders raise, 32766 do not. Chunking well under the OLD limit keeps it working on
#: any SQLite this package might meet, and costs nothing: a 20000-row restore runs in 0.3s.
_RESTORE_ID_CHUNK = 500

_RESTORE_COLUMNS = (
    "id", "severity", "category", "file", "status", "description", "source",
    "tags", "meta", "reported_at_commit", "reported_at_ref", "created_at",
    "updated_at", "fingerprint", "occurrence_count", "last_seen_at",
)

if not all(is_sql_identifier(_c) for _c in _RESTORE_COLUMNS):  # pragma: no cover - import-time
    raise ValueError(
        "_RESTORE_COLUMNS carries a value that is not a safe SQL identifier: "
        f"{[c for c in _RESTORE_COLUMNS if not is_sql_identifier(c)]}"
    )


def _restore_json(value: Any, *, field: str, label: str) -> str:
    """A `tags`/`meta` column for a restore: JSON text or a container, stored EXACTLY once.

    Serialized once and that exact string is stored (CB-74/CB-82): validating with one
    `json.dumps` and storing with a second leaves a window where a mutable or
    `__iter__`-overriding value shows different data to each.

    SHAPE IS CHECKED, because `json.dumps` complains about neither shape nor member types
    (CB-82) — it will happily write `{"a": 1}` into `tags`, which then crashes the display
    layer's `",".join(...)` long after the restore reported success. `tags` is a list of
    strings; `meta` is an object. Reserved meta keys are deliberately ALLOWED here: an
    exported `recurrence_of` is the evidence a restore exists to preserve.
    """
    default = "[]" if field == "tags" else "{}"
    if value is None or value == "":
        return default
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{label}: {field} is not valid JSON: {e}") from e
        serialized = value
    else:
        parsed = value
        serialized = json.dumps(value)

    if field == "tags":
        if not isinstance(parsed, list) or not all(isinstance(t, str) for t in parsed):
            raise ValueError(f"{label}: tags must be a JSON array of strings")
    elif not isinstance(parsed, dict) or not all(isinstance(k, str) for k in parsed):
        raise ValueError(f"{label}: meta must be a JSON object with string keys")
    return serialized


def restore_findings(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> RestoreReport:
    """Write an export back VERBATIM — id, status, occurrence_count, timestamps and all.

    This is not an import and deliberately shares no code with one. An import is an
    observation and must go through the identity function; a restore is a statement that
    these rows ARE the tracker, so it bypasses dedup, the pre-add resolvers and the
    post-add hooks and writes the stored columns directly.

    THE ADD PATH CANNOT DO THIS, and each refusal was measured before this was written:
    `_validate_fingerprint` rejects the `auto:` prefix every derived fingerprint carries;
    `_validate_meta_keys` rejects `recurrence_of` / `occurrences`, which is the evidence a
    restore exists to preserve; and `status` / `occurrence_count` / `created_at` are not
    insertable at all. Worse, the obvious two-step — insert as `open`, then UPDATE —
    REFUSES A LEGITIMATE EXPORT: a `wont_fix` card and its `recurrence_of` twin share a
    fingerprint by design, so whichever lands second collides on
    `ux_findings_fingerprint_live`. Writing the FINAL statuses satisfies that partial index
    by construction.

    ALL-OR-NOTHING, and it REFUSES rather than merges. Every check runs inside the
    transaction, so `BEGIN IMMEDIATE` holds the write lock and there is no check-then-act
    window: every row must carry an id, no id may already exist locally, and no id may
    repeat within the file. A collision raises `ValueError` naming the ids — without it a
    duplicate leaks `sqlite3.IntegrityError`, which is outside this module's contract and
    unclassifiable by `db.is_contention` (code 19, not 5/6).

    WHAT A RESTORE CANNOT BRING BACK, stated rather than discovered: milestone items and
    their audit rows. They are a PROJECTION built by post-add hooks, they are not exported,
    and firing the hooks here would fabricate one triage item and two audit rows per card
    asserting a history that never happened. A restored tracker therefore has no milestone
    projections; the CLI says so.
    """
    if not rows:
        return RestoreReport(0)

    with db.txn(conn):
        seen: dict[str, int] = {}
        prepared: list[tuple[Any, ...]] = []

        for index, row in enumerate(rows, start=1):
            row_id = (row.get("id") or "").strip()
            label = row_id  # every path below this raise has a non-empty id
            if not row_id:
                raise ValueError(
                    f"restore requires an id on every row; row {index} has none. "
                    f"Use `import-csv` to fold in rows that carry no identity."
                )
            if row_id in seen:
                raise ValueError(
                    f"{row_id} appears twice in the file (rows {seen[row_id]} and {index})"
                )
            seen[row_id] = index

            category = (row.get("category") or "").strip()
            file = (row.get("file") or "").strip()
            description = (row.get("description") or "").strip()
            if not category or not file or not description:
                raise ValueError(
                    f"{label}: category, file and description are required for a restore"
                )

            try:
                occurrence_count = int(row.get("occurrence_count") or 1)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{label}: occurrence_count is not an integer") from e
            if occurrence_count < 1:
                raise ValueError(f"{label}: occurrence_count must be >= 1")

            now = utc_now()
            created_at = (row.get("created_at") or "").strip() or now
            # A DICT with named placeholders, not a positional tuple. The exporter had
            # exactly this coupling — a header list and a row list, hand-aligned — and
            # adding two columns shifted every value after `meta`, turning the exported
            # `fingerprint` into a timestamp. Rebuilding it here would have re-created the
            # defect one function away, and the schema ratchet is set-based so a REORDER
            # of `_RESTORE_COLUMNS` would misalign every row while the suite stayed green.
            prepared.append(
                {
                    "id": row_id,
                    # Spelling is resolved, meaning is not (CB-19): a legacy spelling
                    # normalises instead of leaking an IntegrityError from the CHECK.
                    "severity": resolve_severity(row.get("severity") or "medium"),
                    "category": category,
                    "file": file,
                    "status": resolve_finding_status(row.get("status") or "open"),
                    "description": description,
                    "source": (row.get("source") or "restore").strip(),
                    "tags": _restore_json(row.get("tags"), field="tags", label=label),
                    "meta": _restore_json(row.get("meta"), field="meta", label=label),
                    "reported_at_commit": (row.get("reported_at_commit") or "").strip() or None,
                    "reported_at_ref": (row.get("reported_at_ref") or "").strip() or None,
                    "created_at": created_at,
                    "updated_at": (row.get("updated_at") or "").strip() or created_at,
                    # NOT stripped of the `auto:` prefix — that strip is an IMPORT rule,
                    # and applying it here is what would leave the restored tracker with
                    # no identity function at all.
                    "fingerprint": (row.get("fingerprint") or "").strip() or None,
                    "occurrence_count": occurrence_count,
                    "last_seen_at": (row.get("last_seen_at") or "").strip() or None,
                }
            )

        columns = ", ".join(_RESTORE_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _RESTORE_COLUMNS)

        # ONE bound parameter, so there is no row ceiling. The obvious
        # `IN (?,?,?...)` uses a placeholder per row and SQLite caps
        # `SQLITE_LIMIT_VARIABLE_NUMBER` at 32766 on this build (999 on older ones) —
        # measured: 40000 placeholders raise `too many SQL variables`, so a large enough
        # tracker could export a backup it was unable to restore. Chunking also works and
        # was rejected: it reintroduces a magic number to get wrong. JSON1 is already a
        # dependency (`json_extract` in `query_findings`).
        existing = {
            r["id"]
            for r in conn.execute(
                "SELECT id FROM findings WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(list(seen)),),
            )
        }
        if existing:
            raise ValueError(
                f"refusing to restore: {len(existing)} id(s) already exist here "
                f"({', '.join(sorted(existing)[:5])}"
                f"{', ...' if len(existing) > 5 else ''}). "
                f"A restore writes ids verbatim, so it will not merge into a populated "
                f"tracker; use `import-csv` to fold these rows in with fresh ids."
            )

        conn.executemany(
            f"INSERT INTO findings ({columns}) VALUES ({placeholders})",  # noqa: S608
            prepared,
        )

    return RestoreReport(len(prepared))


def _same_finding_exists(
    conn: sqlite3.Connection, row_id: str, category: str, file: str, description: str
) -> bool:
    """Is `row_id` already held locally BY THE SAME FINDING?

    Content, not just the id — see `import_findings` rule 2. `description` is
    compared verbatim rather than through the fingerprint normalization on
    purpose: this guard's job is "is this literally the row I already have",
    and the normalized form deliberately collapses distinct rows.
    """
    hit = conn.execute(
        "SELECT category, file, description FROM findings WHERE id = ?", (row_id,)
    ).fetchone()
    return hit is not None and (
        hit["category"] == category and hit["file"] == file and hit["description"] == description
    )


def _import_would_reopen(
    conn: sqlite3.Connection,
    fingerprint: str | None,
    category: str,
    file: str,
    description: str,
    meta: dict[str, Any] | None,
) -> tuple[bool, str]:
    """`(would_reopen, fingerprint)` — see `import_findings` rule 1.

    RETURNS the resolved fingerprint so the caller can hand it to `_add_one`,
    which would otherwise derive it and re-run both match queries for the same
    row — four SELECTs per row instead of two, inside the held write lock. That
    COST is now the whole reason this helper exists; the other reason has been
    withdrawn, and this paragraph is the record of it.

    This docstring used to say the question was asked here rather than by giving
    `_add_one` a new outcome, because "`_add_one`'s return shape stays the one
    every other caller and the MCP wrappers are written against". BT-5 REVERSES
    that (ratified by the owner 2026-08-20; judge's mandatory fix #3):
    `_add_one` now returns a typed `AddOutcome`. The reversal is narrower than it
    looks, and that is why it was taken — the real content of the old decision
    was "do not change the shape of the RESPONSE", and it is honoured. The tuple
    that changed was module-private (three in-module callers plus one test
    monkeypatch); the MCP response shape gains one key, additively and
    deliberately, and the wrappers document it.
    """
    fp = fingerprint or _derive_fingerprint(category, file, description, meta)
    _live, closed = _match_fingerprint(conn, fp)
    return (closed is not None and closed["status"] in _REOPEN_STATUSES), fp


def batch_add_findings(
    conn: sqlite3.Connection,
    findings: list[dict[str, Any]],
    *,
    new_category: bool = False,
    project_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Record multiple observations at once. Returns one result per input, in input
    order.

    Contract: ONE logical transaction, one result per input. (The old contract —
    "N INSERTs, one bulk SELECT, N hook fires, exactly ONE commit" — is repealed:
    a deduplicated member does not insert and does not fire hooks, and results are
    built from the per-member loop rather than a bulk SELECT, which returned
    B-tree order and silently shrank when two members shared an id.)

    Members go through the same identity function as ``add_finding``, within the
    single transaction — so two members of one batch sharing a fingerprint yield
    one insert plus one bump. MUST NOT delegate to add_finding() in a loop (that
    produces N commits).

    Category canon (CB-60): ``new_category`` is batch-wide permission, applied
    per member on the observation path (no ``id`` key — an explicit id stores
    its category verbatim, same as ``add_finding``). The mint GATE is decided
    inside ``_add_one``, per member, AFTER that member's dedup branch is known
    (CB-113(b)): a member whose fingerprint matches a known live or reopenable
    card is recorded regardless of its category spelling. Insert-path members
    are judged in input order against the table PLUS categories earlier members
    of this batch minted — an earlier mint is an INSERT inside the open
    transaction, visible to the gate's canon read on the same connection — and
    only the first member introducing a category carries the
    ``category_minted`` stamp: one minting event, not one per row. A gate
    refusal mid-batch propagates and rolls back the whole batch, so nothing
    lands, exactly as when the gate ran up front.
    """
    # CONTAINER shape first (CB-80): `findings` itself must be a list of
    # objects before anything below indexes into a member. A `str` argument is
    # iterable, so a check that only tests each iterated element (never the
    # container) would silently walk CHARACTERS instead of refusing here — see
    # `types.validate_batch_payload`.
    findings = validate_batch_payload(findings, label="findings")

    # Validate every ARGUMENT before the transaction opens: invalid input raises
    # immediately, not after a busy_timeout wait, and never half-applies a batch.
    # The category mint gate is the one deliberate exception — it depends on the
    # member's dedup branch, so it runs inside the transaction (CB-113(b)); its
    # refusal rolls back the whole batch, preserving the nothing-lands property.
    #
    # `meta`/`stripped_meta_keys` come from `_validate_meta_keys` per member
    # (CB-56) and `description`/`stripped_description_tail` from
    # `_strip_tool_call_tail` (CB-90). Both are the SAME hazard and it is why
    # this pass carries a record instead of a tuple: what gets passed to
    # `_add_one` below must be the CLEANED value, and re-reading `f["meta"]` or
    # `f["description"]` in the second loop would silently reintroduce exactly
    # what this pass removed — while the response still reported it as cut,
    # which is a success-shaped lie about the caller's own data. A positional
    # tuple made that a one-slot misread away (CB-52's lesson: close the
    # ordering hazard structurally rather than warn about it in a comment).
    validated: list[_ValidatedMember] = []
    for i, f in enumerate(findings):
        unknown = set(f) - _BATCH_MEMBER_KEYS
        if unknown:
            raise ValueError(f"findings[{i}]: unknown keys {sorted(unknown)}")
        meta, stripped = _validate_meta_keys(f.get("meta"))
        category = f["category"]
        description = f["description"]
        tail_cut = False
        if f.get("id") is None:
            category = normalize_category(category)
            description, tail_cut = _strip_tool_call_tail(description)
        validated.append(
            _ValidatedMember(
                severity=resolve_severity(f.get("severity", "medium")),
                fingerprint=_validate_fingerprint(f.get("fingerprint")),
                category=category,
                description=description,
                meta=meta,
                stripped_meta_keys=stripped,
                stripped_description_tail=tail_cut,
            )
        )

    # Each member's result is the row AS OBSERVED when that member was processed: a
    # later member bumping an earlier one does not retroactively update the earlier
    # member's returned occurrence_count. Input order is preserved by construction.
    outcomes: list[AddOutcome] = []
    with db.txn(conn) as owned:
        for f, member in zip(findings, validated, strict=True):
            outcomes.append(
                _add_one(
                    conn,
                    severity=member.severity,
                    category=member.category,
                    file=f["file"],
                    description=member.description,
                    source=f.get("source", "human"),
                    tags=f.get("tags"),
                    meta=member.meta,
                    finding_id=f.get("id"),
                    reported_at_commit=f.get("reported_at_commit"),
                    reported_at_ref=f.get("reported_at_ref"),
                    fingerprint=member.fingerprint,
                    new_category=new_category,
                    project_dir=project_dir,
                )
            )
    return [
        _finalize_add(
            outcome,
            committed=owned,
            stripped_meta_keys=member.stripped_meta_keys,
            stripped_description_tail=member.stripped_description_tail,
        )
        for outcome, member in zip(outcomes, validated, strict=True)
    ]


def _bump_refusal_count(conn: sqlite3.Connection, finding_id: str) -> None:
    """`meta.fingerprint_refusals += 1` on *finding_id*, in its own transaction.

    ONE STATEMENT, so this is not an instance of the CB-24 read-modify-write
    hazard: there is no Python-side read to go stale between the read and the
    write, exactly as `SET n = n + 1` is already exempt.

    `updated_at` is deliberately NOT touched. CB-123 ratified `recent` as a
    reader over that column with the stated caveat that a last TOUCH is not a
    closure; a touch that changed nothing a reader of the card can see would
    make that reader LIER rather than more accurate. A refusal changes no
    content of the card — it changes only how often the card was wanted back.

    A stored value of the wrong TYPE (a hand-repaired string, say) is coerced by
    SQLite's arithmetic and the count restarts from 1. That is the accepted cost
    of the key staying repairable; the alternative is an unrepairable stamp,
    which is the CB-26 shape this repository has twice chosen against.
    """
    with db.txn(conn):
        conn.execute(
            "UPDATE findings SET meta = json_set(meta, ?, "
            "COALESCE(json_extract(meta, ?), 0) + 1) WHERE id = ?",
            (_REFUSAL_COUNT_JSON_PATH, _REFUSAL_COUNT_JSON_PATH, finding_id),
        )


def _count_fingerprint_refusal(conn: sqlite3.Connection, finding_id: str) -> None:
    """Record one refusal event. Fail-OPEN, and never in the caller's transaction.

    TWO RULES, and the direction of each is the whole design.

    **The counter is fail-open; the refusal is fail-closed.** Losing one unit of
    statistics is acceptable, a caller not receiving its `ValueError` never is.
    So every failure here is swallowed and logged — the `run_status_change_hooks`
    precedent — and NEVER the other way round. The split into two functions is
    what makes that testable: this one owns the swallow, `_bump_refusal_count`
    owns the write, so a test can break the write and watch the refusal still
    arrive intact.

    **Under a caller's open transaction the counter is SKIPPED**, and the reason
    is one step narrower than the obvious one — measured, because the first draft
    of this docstring overstated it. `update_finding` may run inside someone
    else's transaction. A naive "write it in its own COMMITTED transaction" there
    would commit the CALLER'S unrelated work, verbatim CB-40, where assigning
    `isolation_level` silently committed an ambient transaction — but
    `_bump_refusal_count` goes through `db.txn`, which is reentrant and commits
    nothing under an ambient transaction, so that specific defect is already
    unreachable. What actually happens without this guard is that the count JOINS
    the stranger's transaction and shares its fate: measured, a caller that
    swallows the `ValueError` and commits carries the count out with it, while a
    caller that aborts destroys it. Neither is this function's decision to make.
    A statistic must not be a write inside a unit of work nobody asked to extend,
    and it must not have a different value depending on what an unrelated caller
    did afterwards. So it is skipped, and the unit is lost — fail-open.

    HONEST SCOPE, enumerated rather than assumed — and the enumeration is PINNED
    BY A TEST, because the previous one rotted silently. `update_finding` has
    FIVE call sites in `src/` today, and
    `tests/test_findings.py::TestUpdateFindingCallSitesRatchet` counts them by
    AST: a sixth turns the suite RED instead of quietly invalidating this
    paragraph. That failure is not hypothetical. This text used to open "all
    four callers of `update_finding` were read", and the fifth —
    `loc._apply_recapture` — arrived by FORWARD MERGE from another branch after
    that enumeration had been made, leaving a true conclusion standing on a
    false reason. A premise established by ENUMERATION must be replayed after a
    forward merge; a ratchet is what makes the merge do the replaying.

    That path has NO PRODUCER today. The refusal predicate fires only on a
    nonlive→live status change, so producing one needs a `status` argument
    naming a LIVE status, issued under a transaction this function did not open.
    Exactly TWO of the five run under an ambient transaction, and they miss for
    two DIFFERENT reasons — do not collapse them into one clause, which is how
    the last version of this paragraph became wrong:

      * `milestones.triage_dismiss` passes the literal `status="not_a_bug"`, a
        TERMINAL status. The predicate reads a status and finds the wrong
        direction.
      * `loc._apply_recapture` passes `meta_update={"loc": …}` and NO `status`
        at all. The predicate has nothing to read.

    The other THREE open their own connection with no transaction in progress,
    so `conn.in_transaction` is False and this guard never skips anything: the
    MCP `update` wrapper, the CLI `update` handler, and
    `provenance.resolve_trailers`. Note that third one specifically, because the
    previous version of this paragraph filed it under "ambient" and it is not —
    `provenance.py` contains no `db.txn` ANYWHERE, so nothing in that module can
    be the frame that opens one; an ambient caller would have to arrive from
    outside it, and the only caller today is its own CLI handler, which opens a
    fresh connection. State it in that order deliberately: the durable half is
    the module property, the caller list is an enumeration, and an enumeration
    is exactly what went stale here. So the correction is not only arithmetic —
    the CLASSIFICATION was wrong too. Both facts were MEASURED, not reasoned:
    `conn.in_transaction` was sampled at every one of these sites by driving
    each caller.

    So this is a guard for the day a producer appears, not a live route, and
    `tests/test_findings.py` builds that caller by hand to hold the line.
    """
    if conn.in_transaction:
        return
    try:
        _bump_refusal_count(conn, finding_id)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[fingerprint-refusal counter failed for {finding_id}] {e}\n")


def update_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    *,
    status: str | None = None,
    severity: str | None = None,
    notes: str | None = None,
    append_note: str | None = None,
    tags: list[str] | None = None,
    meta_update: dict[str, Any] | None = None,
    reported_at_ref: str | None = None,
) -> dict[str, Any]:
    """Update a finding. Returns updated finding.

    ``notes`` replaces the notes wholesale; ``append_note`` adds a newline-joined
    line, preserving prior history. Note: reported_at_commit is intentionally
    excluded — it is immutable after insert. ``reported_at_ref`` here is the
    SANCTIONED manual mutation of an observation-frozen column (BT-4): a dedup
    bump never moves it, this call does — a release is tagged after filing. Do
    not confuse the pair: the commit is immutable, the ref is mutable by design.

    ``severity`` re-triages in place, mirroring ``priority`` on
    ``reqs.update_requirement`` (CB-17). It is validated here with the same
    strictness ``add_finding`` applies at insert — the column's CHECK constraint
    is a backstop, not the validator, and reaching it would surface an
    ``IntegrityError`` where the contract promises ``ValueError``.

    The three meta-writing arguments compose over a single dict, applied in this
    order: ``notes`` replaces, ``append_note`` then extends *that replacement*,
    and ``meta_update`` merges last — so an explicit ``meta_update["notes"]``
    still wins. They must never build separate dicts from the pre-update row: a
    second ``meta = ?`` in one UPDATE silently discards the first (CB-16).

    That composition is only safe if the read and the write are ONE transaction,
    which is why the whole body sits in ``db.txn`` (CB-24). ``meta`` is merged in
    Python from the row read at the top, so two writers that each read before
    either writes would both report success while the later erased the earlier's
    merge. ``busy_timeout`` serializes the writes and does nothing about the read
    that preceded them; ``BEGIN IMMEDIATE`` takes the write lock up front instead.
    Do not restore a ``conn.commit()`` here: ``db.txn`` yields ``False`` under an
    ambient transaction, and committing then would commit the *caller's* work
    (``milestones.triage_dismiss`` is such a caller).

    **What this function deliberately CANNOT reach, and why (CB-21).**
    ``description``, ``category`` and ``file`` are the three INPUTS to the
    derived ``auto:v1`` fingerprint (CB-43), and ``category`` is since CB-60 a
    normalized and mint-gated input besides. An argument for any of them would
    make this function a RE-KEY of identity — and re-keying is a separate
    negotiated contract, not something a caller acquires by argument: CB-61
    negotiated exactly one such operation, ``normalize_categories``, which
    issues its own UPDATE for precisely that reason. ``source`` is
    first-reporter provenance, frozen by design (BT-4; ratified by the owner
    2026-08-20) — later observations' sources live only in the occurrence ring.
    ``reported_at_commit`` stays immutable after insert, as above.

    **The fingerprint refusal is COUNTED (BT-8, ratified 2026-08-22).** Re-triaging
    a decided card back to a live status while a live recurrence holds its
    fingerprint still raises `ValueError` naming the blocking row — unchanged, and
    that is constraint one. What is new is that each such event increments
    `meta.fingerprint_refusals` ON THE REFUSED CARD, readable through the existing
    surface as `query(meta_key="fingerprint_refusals")`. No new tool, no new verb.

    The number is a DEMAND SIGNAL for the merge policy CB-46 is blocked on — the
    ratified decision was to leave the fork as it is and count, exactly as
    `moved_file` is counted. Read it with its predicate attached: it counts
    REFUSAL EVENTS, not distinct people and not distinct intentions, so one
    caller retrying in a loop inflates it by one per attempt.

    Three properties, each of which the implementation is shaped by. The count is
    written AFTER this frame's transaction, because a write inside it would be
    rolled back by the very `raise` it is recording. It is fail-OPEN — a counter
    that cannot be written is swallowed and logged, and the caller still receives
    its `ValueError` unchanged. And it is SKIPPED under an ambient transaction,
    where committing it would commit the caller's unrelated work (CB-40); that
    path has no producer today, and `_count_fingerprint_refusal` enumerates why.

    `updated_at` deliberately does not move: a refusal changes no content of the
    card, and CB-123's `recent` reader is already carrying the caveat that a last
    touch is not a closure.

    Read those as the CURRENT contract with its reason, not as a verdict that
    they can never become mutable: CB-21 asks whether ``description`` and
    ``file`` should be, and that question is open. What is closed is leaving
    the answer unsaid. ``tests/test_update_parity.py`` is the gate — every
    column of both entities must be declared mutable (naming the parameters
    that write it) or immutable (with a reason), so a new column, a new
    parameter, or a new surface argument fails a test instead of being
    rediscovered by a fourth inspection of this function.
    """
    # Argument-only validation runs BEFORE the transaction. These resolvers are pure
    # functions of their input and need no row, and `BEGIN IMMEDIATE` first would mean
    # an invalid status raises OperationalError after a five-second wait under write
    # contention, instead of the ValueError the contract promises immediately.
    if status is not None:
        status = resolve_finding_status(status)
    if severity is not None:
        severity = resolve_severity(severity)
    # Same reservation as on the add path: a meta_update planting "occurrences"
    # or "recurrence_of" would be read back as the identity machinery's own state.
    _validate_meta_keys(meta_update, updating=True)

    with db.txn(conn):
        row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        if not row:
            raise KeyError(f"Finding not found: {finding_id}")

        # Re-triaging a closed card back to a live status is VALID input, but if a
        # live recurrence already carries this row's fingerprint the write would hit
        # ux_findings_fingerprint_live and surface as a raw IntegrityError — outside
        # the ValueError/KeyError contract, unclassifiable by db.is_contention (code
        # 19, not 5/6), and uncaught by every CLI handler. Pre-check inside this
        # transaction and name the blocking row instead.
        refusal: str | None = None
        if (
            status is not None
            and status in LIVE_STATUSES
            and row["status"] not in LIVE_STATUSES
            and row["fingerprint"] is not None
        ):
            live = _live_row_by_fingerprint(conn, row["fingerprint"], exclude_id=finding_id)
            if live is not None:
                # BT-8. The refusal is now COUNTED, and the counting is what forces
                # this shape. A write placed here would be invisible BY
                # CONSTRUCTION: raising inside `db.txn` rolls the transaction back
                # and takes the count with it. So the refusal MESSAGE is built here,
                # inside the write lock where the check-then-act window is closed,
                # and it is raised AFTER the block. This path writes nothing, so it
                # has nothing to undo and simply commits an empty transaction —
                # exactly the shape CB-40 ratified when it deleted the last two raw
                # `BEGIN IMMEDIATE` sites, and the reason the `TxnAbort` sentinel it
                # rejected is not needed here either.
                refusal = (
                    f"cannot set {finding_id} to {status}: its fingerprint is held by "
                    f"live finding {live['id']} (resolve or close that one first)"
                )

        if refusal is None:
            updates = []
            params: list[Any] = []

            if status is not None:
                updates.append("status = ?")
                params.append(status)

            # A plain column, appended exactly once to the shared `updates` list — it must
            # never grow a second `severity = ?` the way `meta` once did (CB-16).
            if severity is not None:
                updates.append("severity = ?")
                params.append(severity)

            if tags is not None:
                updates.append("tags = ?")
                params.append(json.dumps(tags))

            # One dict, one `meta = ?`. See the docstring for the ordering contract.
            #
            # Parsed lazily so that an update touching no meta argument still reaches its
            # own SQL: the column carries no json_valid constraint, and building the dict
            # unconditionally would abort a plain status write on a malformed legacy row.
            # This does NOT make such a call succeed — the row_to_dict conversion at the
            # return still raises. It only keeps the write itself from being skipped.
            #
            # The condition must list every meta-writing argument below it. A new argument
            # added to the block but not to this guard becomes a silent no-op on its own,
            # while working whenever some other meta argument happens to be present.
            if notes is not None or append_note is not None or meta_update is not None:
                new_meta = json.loads(row["meta"])

                if notes is not None:
                    new_meta["notes"] = notes

                if append_note is not None:
                    prior = new_meta.get("notes")
                    new_meta["notes"] = f"{prior}\n{append_note}" if prior else append_note

                if meta_update is not None:
                    new_meta.update(meta_update)

                updates.append("meta = ?")
                params.append(json.dumps(new_meta))

            if reported_at_ref is not None:
                updates.append("reported_at_ref = ?")
                params.append(reported_at_ref)

            # The no-op path still holds the write lock for the length of one SELECT.
            # Deriving "will this write?" from the arguments beforehand would duplicate
            # the argument list — the same fragility the lazy meta guard above warns
            # about — so correctness wins over the microseconds.
            if not updates:
                final_row = row
            else:
                updates.append("updated_at = ?")
                params.append(utc_now())
                params.append(finding_id)

                old_status = row["status"]
                cur = conn.execute(f"UPDATE findings SET {', '.join(updates)} WHERE id = ?", params)
                # Fire iff the write actually changed the row. `status` is already canonical
                # via resolve_finding_status above, so an alias does not read as a change.
                # Hooks run inside this transaction, before it commits, so a status change
                # and its side-effects (e.g. auto-releasing a claim) land atomically.
                if status is not None and cur.rowcount == 1 and status != old_status:
                    db.run_status_change_hooks(conn, finding_id, old_status, status)
                final_row = conn.execute(
                    "SELECT * FROM findings WHERE id = ?", (finding_id,)
                ).fetchone()

    # BT-8. Counted, then raised — in that order and OUTSIDE the transaction above,
    # which is the only place the count can outlive the refusal. `_count_fingerprint_refusal`
    # is fail-open and skips itself under an ambient transaction; the refusal that
    # follows it is fail-closed and unconditional, and a counter that threw has
    # already been swallowed by the time this line runs.
    if refusal is not None:
        _count_fingerprint_refusal(conn, finding_id)
        raise ValueError(refusal)

    # Converted OUTSIDE the block, and that placement is load-bearing. `row_to_dict`
    # raises json.JSONDecodeError on a row whose stored meta is malformed; raised
    # inside, it would roll back a write the contract promises has landed, reporting
    # failure for a mutation that succeeded (CB-16). The SELECT stays inside so the
    # returned row is the transaction's own view.
    #
    # The guarantee is "after THIS frame's transaction", not "after a commit". Under
    # an ambient transaction db.txn yields False and nothing has committed yet, so
    # the raise propagates to the owning frame and abandons the whole unit with it —
    # deliberate: a compound caller such as milestones.triage_dismiss should not keep
    # half its work because the row it touched had unreadable meta.
    return db.row_to_dict(final_row)


def parse_meta(meta_json: str | None) -> dict[str, Any]:
    """Tolerant parse over a raw stored meta blob — the ONE place.

    Lives here because findings owns the `meta` column. Invalid JSON and
    valid-but-non-dict JSON ("[1,2]", "3") both degrade to {}: legacy rows must
    degrade the CONSUMER's answer, never fail the caller (CB-24 consequence 4).
    The accessors below hand out `meta_json` as the stored STRING precisely so
    that this decision belongs to the reader, not to the SELECT.
    """
    try:
        meta = json.loads(meta_json) if meta_json else {}
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def parse_tags(tags_json: str | None) -> list[str]:
    """Tolerant parse over a raw stored tags blob; non-list / non-str degrade away.

    Same contract as parse_meta: the tags column is `NOT NULL DEFAULT '[]'` on
    the write path, but a foreign or hand-edited row is not bound by that, and a
    grouping pass over 3000 cards must not die on one of them.
    """
    try:
        tags = json.loads(tags_json) if tags_json else []
    except (TypeError, ValueError):
        return []
    if not isinstance(tags, list):
        return []
    return [t for t in tags if isinstance(t, str)]


def similarity_candidates(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    categories: tuple[str, ...] | None = None,
    status: str | None = None,
    statuses: tuple[str, ...] | None = None,
    limit: int | None = None,
    order: str = "oldest",
) -> list[dict[str, Any]]:
    """Candidate records for an out-of-domain grouping pass (CB-45).

    The sanctioned read surface for similarity.py — no other module may SELECT
    from findings (module-ownership rule). Returns raw rows: ``meta_json`` is
    the STORED STRING, never parsed here — parsing would raise on legacy rows
    and make the caller's tolerate-and-degrade policy unimplementable (CB-24
    consequence 4). Ordered ``created_at, id`` (``order="newest"`` reverses) so
    any grouping over the result is deterministic despite whole-second
    timestamps. ``status`` is a vocabulary filter (resolved, CB-19/CB-25);
    ``statuses`` is an explicit tuple for callers that know their population.
    ``categories`` is the same explicit-tuple twin for category: findings
    permit ``category=""``, which the ``category=`` FILTER convention must read
    as "no filter" — a caller whose category is a VALUE (the resolver matching
    an observation's own category, "" included) passes ``categories=("",)`` and
    gets an exact match instead of the whole table (Codex diff review).
    """
    conditions: list[str] = []
    params: list[Any] = []
    if is_text_filter_active(category):
        conditions.append("category = ?")
        params.append(category)
    if categories:
        conditions.append(f"category IN ({','.join('?' for _ in categories)})")
        params.extend(categories)
    if is_vocabulary_filter_active(status):
        conditions.append("status = ?")
        params.append(resolve_finding_status(status))
    if statuses:
        conditions.append(f"status IN ({','.join('?' for _ in statuses)})")
        params.extend(statuses)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    direction = "DESC" if order == "newest" else "ASC"
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        f"SELECT id, category, file, status, severity, occurrence_count, created_at, "
        f"description, meta AS meta_json FROM findings {where} "
        f"ORDER BY created_at {direction}, id {direction} {limit_sql}",  # noqa: S608
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def grouping_candidates(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    categories: tuple[str, ...] | None = None,
    status: str | None = None,
    statuses: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Candidate records for a non-similarity grouping pass (grouping.py).

    The sanctioned read surface for grouping.py, and a sibling of
    similarity_candidates rather than a widening of it: the two feed different
    algorithms and must be free to drift. This one carries the two columns the
    similarity pass has no use for and the citation/tag/lineage passes cannot
    work without — ``tags_json`` and, like its sibling, ``meta_json`` as the
    STORED STRING (see parse_meta / parse_tags for why parsing is the reader's
    job). Ordering is ``created_at, id`` for the same reason: whole-second
    timestamps make any grouping over the result non-deterministic otherwise.

    Filter conventions are its sibling's exactly — ``category``/``status`` are
    FILTERS (blank means "no filter"), ``categories``/``statuses`` are explicit
    tuples for callers that know their population.
    """
    conditions: list[str] = []
    params: list[Any] = []
    if is_text_filter_active(category):
        conditions.append("category = ?")
        params.append(category)
    if categories:
        conditions.append(f"category IN ({','.join('?' for _ in categories)})")
        params.extend(categories)
    if is_vocabulary_filter_active(status):
        conditions.append("status = ?")
        params.append(resolve_finding_status(status))
    if statuses:
        conditions.append(f"status IN ({','.join('?' for _ in statuses)})")
        params.extend(statuses)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        f"SELECT id, category, file, status, severity, occurrence_count, created_at, "
        f"description, tags AS tags_json, meta AS meta_json FROM findings {where} "
        f"ORDER BY created_at ASC, id ASC {limit_sql}",  # noqa: S608
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def anchor_candidates(
    conn: sqlite3.Connection,
    *,
    finding_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    file: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Candidate records for a location-anchor pass (loc.py, BT-7 Т-b).

    The sanctioned read surface for ``loc.py``, and a third sibling of
    ``similarity_candidates``/``grouping_candidates`` rather than a widening of
    either: ``loc`` needs neither ``description`` nor ``tags`` and DOES need
    ``reported_at_commit``, which neither sibling carries. The module-ownership
    rule is the reason all three exist — no module outside this one may SELECT
    from ``findings``, and ``loc.py`` is a zero-SQL extension whose licence to
    exist is exactly that it never does.

    ``meta_json`` is the STORED STRING and is never parsed here (CB-24
    consequence 4). That is load-bearing for this caller specifically: the whole
    read side of BT-7 is built to degrade rather than raise on a stored object
    it does not like, and a row whose ``meta`` does not even parse must reach
    the caller as a row it can report on, not as a ``JSONDecodeError`` that
    aborts a batch of ten thousand.

    Ordered ``created_at, id`` so a pass over the result is deterministic
    despite whole-second timestamps. ``status``/``category``/``file`` are
    FILTERS in this package's convention (``None`` and ``""`` mean "no filter");
    the DEFAULT population — findings carry no anchor until one is captured, so
    "no filter" is rarely what a caller wants — is chosen by the caller, not
    here. ``finding_id`` restricts to one row and does NOT raise for an unknown
    id: an accessor reports what is there, and the KeyError contract belongs to
    ``get_finding``.
    """
    conditions: list[str] = []
    params: list[Any] = []
    if is_text_filter_active(finding_id):
        conditions.append("id = ?")
        params.append(finding_id)
    if is_text_filter_active(category):
        conditions.append("category = ?")
        params.append(category)
    if is_text_filter_active(file):
        conditions.append("file = ?")
        params.append(file)
    if is_vocabulary_filter_active(status):
        conditions.append("status = ?")
        params.append(resolve_finding_status(status))
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        f"SELECT id, category, file, status, created_at, reported_at_commit, "
        f"meta AS meta_json FROM findings {where} "
        f"ORDER BY created_at ASC, id ASC {limit_sql}",  # noqa: S608
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    *,
    resolve_anchors: bool = True,
    project_dir: str | None = None,
) -> dict[str, Any]:
    """Fetch a single finding by ID. Raises KeyError if not found.

    The row carries an ``anchor`` summary whenever a read enricher is registered
    (``loc``, today), and by DEFAULT that summary is RESOLVED against the
    repository: an ordinary read of a card whose code has since moved says where
    it went, which is the point of the whole seam and what the owner's
    acceptance names. The cost is bounded by one row, which is why the default
    here is greedy and ``query``'s is not.

    ``resolve_anchors=False`` is the opt-out, and it exists because a read path
    that cannot spawn a process must remain available: a script, a test, a
    machine with no git, a tracker read from outside the tree it describes. It
    does not remove the summary — the card still says whether it carries an
    anchor — it removes only the part that costs.

    ``project_dir`` names the repository to resolve against; omitted, it is the
    tracker's OWN directory (``db.connection_root``), never the process cwd,
    which BT-7 Р3 refuses. A root that cannot be determined is reported as
    ``unknown(no_root)`` inside the summary rather than raised.
    """
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        raise KeyError(f"Finding not found: {finding_id}")
    finding = db.row_to_dict(row)
    _enrich_read([finding], conn, resolve=resolve_anchors, project_dir=project_dir)
    return finding


def _enrich_read(
    rows: list[dict[str, Any]],
    conn: sqlite3.Connection,
    *,
    resolve: bool,
    project_dir: str | None,
) -> None:
    """Run the read-enricher seam over rows this module is about to return.

    The root is resolved ONLY when something might actually use it, so a read
    with resolution off does not even ask the connection where it lives.
    """
    root = project_dir
    if root is None and resolve:
        root = db.connection_root(conn)
    db.run_read_enrichers(conn, rows, resolve=resolve, project_dir=root)


# --- Grouping axes (CB-62) ----------------------------------------------------
#
# ONE definition, read by `query_findings` AND `get_stats`. Before this the two
# functions carried hand-written twins of the same set in DIFFERENT orders
# (`file, category, severity, status, source` against `severity, category,
# status, file, source`) — one decision written down twice, which is the shape
# this work exists to remove. The order below is now the single one, and it is
# what both the MCP descriptions and the CLI `help=` enumerate.
#
# TWO of the seven axes are NOT columns, and that is the whole design problem.
# The five column axes PARTITION the population: every row lands in exactly one
# group and the counts sum to the total. `tag` and `meta:<key>` do neither — a
# row with two tags is in two groups, and a row carrying no value on the axis is
# in none and would simply VANISH from the answer. Both facts are therefore
# reported as numbers beside the groups rather than left for a reader to infer;
# see `_axis_counts`.
GROUP_COLUMNS: tuple[str, ...] = ("severity", "category", "status", "file", "source")
GROUP_TAG = "tag"
GROUP_META_PREFIX = "meta:"

# The axis list as one string. DERIVED, so the REFUSAL message can never name a
# set the resolver does not implement — but state the scope precisely: the four
# surface texts (two MCP docstrings, two CLI `help=`) are hand-written prose and
# are NOT generated from this, because a docstring cannot be an f-string and
# generating half of the four would be worse than generating none. What holds
# those four is a test, `test_every_surface_enumerates_every_axis`, which is the
# same prose-against-code shape `TestBt4FreshnessDeclarations` uses.
GROUP_AXES_HELP = f"{', '.join(GROUP_COLUMNS)}, {GROUP_TAG}, {GROUP_META_PREFIX}<key>"

# Characters SQLite's JSON-path grammar spends on structure. A key containing
# one cannot be named by the naive `"$." + key` this package builds, and the
# failure is SILENT: measured on SQLite 3.46, `json_extract('{"a.b":1,"a":{"b":2}}',
# '$.a.b')` answers 2 — the NESTED value — while `'$."a.b"'` answers 1. So the
# path a caller gets is not the key a caller asked for.
#
# `.` and `[` are measured broken (wrong value, or None). `"` measured WORKS
# under naive concatenation on this version and is refused anyway, because it is
# the grammar's own quoting character and NO escape form addresses it (`$."q\"k"`
# and `$."q"k"` both answer None), so a key holding one could not be expressed by
# the quoted spelling any eventual fix must use — refusing it now keeps that
# decision free rather than shipping a behaviour that would have to break. `]` is
# inert today and is refused with its partner so the rule is one sentence.
#
# THE COST IS REAL AND WAS MEASURED, not assumed away: of the 313 distinct
# top-level meta keys on this tracker (measured 2026-08-25 — a moving corpus, so
# read it as a measurement rather than an invariant), exactly TWO carry a dot,
# `misassigned_to_1.81` and `misassigned_to_1.98`, and none carry the other
# three. Those two keys cannot be grouped by until CB-167 decides the grammar.
_META_PATH_METACHARS = '.["]'

# What counts as a value this axis can rank. Positive enumeration on purpose:
# `json_type` answers NULL for an absent key, and `NULL NOT IN (...)` is NULL,
# which `WHERE` excludes — so a negative list would depend on NULL semantics to
# get the common case right. A row is grouped only on affirmative proof, the same
# shape `reconcile.live_source_clause` is built on.
_META_SCALAR_TEST = "json_type(meta, ?) IN ('text','integer','real','true','false')"

# THE GUARD MUST NOT BE STRICTER THAN THE ENGINE IT GUARDS, and the first draft
# of it was — found by adversarial review, and it defeated this work's central
# claim rather than some edge case.
#
# `json_valid(X)` with no flags means canonical RFC-8259, which REJECTS `NaN`,
# `Infinity` and `-Infinity`. Python's `json.loads` ACCEPTS them and — this is
# the part that makes it live rather than theoretical — `json.dumps` WRITES them
# by default, so this package's own write path puts them in the column: measured,
# `add_finding(meta={"x": float("nan")})` stores `{"x": NaN}`. CLAUDE.md's CB-82
# entry ratifies exactly that value as supported ("meta={"x": nan} … stores and
# round-trips fine today").
#
# So an unguarded `json_valid` excluded such a row from EVERY meta axis — even
# for a key sitting right beside the NaN and holding a perfectly good string —
# while `grouping.tag_report` counted it, because `parse_tags` goes through
# `json.loads`. Two shipped tools, one corpus, different answers: the divergence
# this whole axis exists to prevent, reintroduced by its own safety check.
#
# Flag 6 is JSON5 (2) | JSONB (4). Measured on SQLite 3.46.1: `json_valid` says 0
# for `{"k": NaN}` at flags 0/1/4/8 and 1 at flags 2/6, while `json_type`,
# `json_extract` and `json_each` all read that same document happily. The guard
# now matches what they accept instead of a stricter standard they do not apply.
_JSON5 = 6


class _GroupAxis(NamedTuple):
    kind: str  # "column" | "tag" | "meta"
    column: str | None
    meta_path: str | None


def _resolve_group_axis(group_by: str) -> _GroupAxis:
    """The one place a `group_by` value becomes an axis. Raises on anything else.

    The refusal for a dotted key NAMES CB-167 deliberately. The two `meta_key`
    FILTERS in `query_findings` build their path the same naive way and are left
    untouched by this change: their consumer population — callers who may be
    relying on the accidental nesting traversal — is NOT measured, and changing a
    shipped surface on an unmeasured population is a worse trade than declaring
    the asymmetry. So the divergence is stated at the one place a user meets it.
    """
    if group_by in GROUP_COLUMNS:
        return _GroupAxis("column", group_by, None)
    if group_by == GROUP_TAG:
        return _GroupAxis("tag", None, None)
    if group_by.startswith(GROUP_META_PREFIX):
        key = group_by[len(GROUP_META_PREFIX) :]
        if not key:
            # `json_extract(doc, '$.')` raises OperationalError — an
            # environmental exception class escaping a domain function, which no
            # CLI arm classifies. Refuse as input, which is what it is.
            raise ValueError(
                f"Invalid group_by: {group_by!r}. '{GROUP_META_PREFIX}' needs a key, "
                f"e.g. '{GROUP_META_PREFIX}found_by'"
            )
        # A CONTROL character is refused for the SAME reason a dot is, and NUL is
        # the one that proves it (adversarial review). SQLite's path is a C
        # string, so it TRUNCATES at a NUL: measured, `json_extract('{"a":"WRONG_A"}',
        # '$.a' + chr(0) + 'b')` answers `'WRONG_A'` — the key `a\0b` silently
        # reads the neighbouring key `a`, which is the dotted-key failure exactly.
        # And a LEADING NUL leaves the bare path `'$.'`, which raises
        # `sqlite3.OperationalError` — the environmental exception class the empty
        # key is refused to avoid, escaping a domain function that promises
        # `ValueError` and reaching the CLI as a raw traceback, since
        # `domain_errors()` classifies neither.
        bad = sorted({c for c in key if c in _META_PATH_METACHARS or ord(c) < 0x20})
        if bad:
            raise ValueError(
                f"Invalid group_by: {group_by!r}. A meta key containing "
                f"{', '.join(repr(c) for c in bad)} cannot be told apart from a JSON "
                "path here — SQLite reads '$.a.b' as a nested lookup, not as the "
                "top-level key 'a.b' — and the grammar for naming such a key has not "
                "been decided (CB-167). Every other key is groupable."
            )
        return _GroupAxis("meta", None, f"$.{key}")
    raise ValueError(f"Invalid group_by: {group_by}. Must be one of ({GROUP_AXES_HELP})")


def _membership_sql(
    axis: _GroupAxis, *, conditions: list[str], params: list[Any]
) -> tuple[str, list[Any]]:
    """The relation `(finding, group_key)` — ONE row per row-in-a-group, per axis.

    This is the single place an axis becomes SQL. Both readers aggregate over it
    and neither knows how any axis works, so the two of them cannot disagree
    about ordering, about NULL, or about what a duplicate means — which is the
    failure that made two hand-written axis lists drift apart in the first place.
    Reducing every axis to this one shape is also what lets `get_stats` keep its
    severity cross-tab with no second implementation of anything.

    THE TAG BRANCH IS A ROW-WISE TRANSLATION OF ``parse_tags`` and must stay one
    — that function is what `grouping.tag_report` counts through, and two shipped
    tools reporting different totals for one corpus is precisely the divergence
    this axis was built to avoid. Line for line: `json_valid` for its
    `except (TypeError, ValueError)`, `json_type(...) = 'array'` for its
    `isinstance(tags, list)`, `je.type = 'text'` for its `isinstance(t, str)`,
    and `SELECT DISTINCT` for the `set()` that deduplicates within a row.

    That DISTINCT is load-bearing twice over. Without it `["a","a"]` would be
    counted twice, disagreeing with `grouping-tags`; and the occurrence SUM in
    `get_stats` would double for the same row while its finding count did not,
    so one group's two numbers would describe different populations.
    """
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    more = "AND" if conditions else "WHERE"
    cols = "id, severity, occurrence_count"

    if axis.kind == "column":
        return (
            f"SELECT {cols}, {axis.column} AS group_key FROM findings {where}",
            list(params),
        )

    if axis.kind == "tag":
        # The guard has to sit INSIDE the subquery: `json_each` RAISES on
        # malformed JSON, aborting the whole report over one hand-edited row, and
        # a WHERE clause in the outer query would be applied to rows the
        # table-valued function has already been handed. Measured on SQLite 3.46
        # that this survives the flattening the planner does anyway, and
        # `test_a_row_whose_tags_do_not_parse_is_ungrouped_not_fatal` pins it.
        #
        # CASE, not `AND`: this file already records that SQLite documents CASE
        # branches as lazy and does NOT promise `AND` short-circuits, which is
        # why the `commit` filter above is written the same way.
        #
        # The subquery is also what keeps the caller's WHERE unambiguous —
        # `json_each` exposes a column named `id`, so an unqualified `id = ?`
        # beside it fails outright with "ambiguous column name" (measured).
        #
        # HONEST SCOPE: this guards the GROUPING, not the whole query. The
        # pre-existing `tag=` FILTER is a bare `EXISTS (SELECT 1 FROM
        # json_each(tags) ...)` with no `json_valid` of its own, so combining
        # `tag=` with any axis still dies on a malformed row — measured, and
        # pinned as a known limit rather than repaired, because this unit may not
        # change a shipped filter's behaviour on an unmeasured population.
        inner = (
            f"SELECT {cols}, tags FROM findings {where} {more} "
            f"CASE WHEN json_valid(tags, {_JSON5}) THEN json_type(tags) = 'array' ELSE 0 END"
        )
        return (
            "SELECT DISTINCT f.id AS id, f.severity AS severity, "
            "f.occurrence_count AS occurrence_count, je.value AS group_key "
            f"FROM ({inner}) f JOIN json_each(f.tags) je WHERE je.type = 'text'",
            list(params),
        )

    # axis.kind == "meta". The path is BOUND, never interpolated, so a key
    # holding a space cannot reach the SQL text as syntax — and no S608
    # suppression is owed for it either, unlike the interpolated-identifier
    # sites elsewhere in this file. (Spelled as the bare rule code on purpose:
    # ruff parses a suppression directive out of ANY comment, prose included,
    # and warns that this one is malformed.)
    #
    # A GROUP KEY IS ALWAYS TEXT, and this is the only axis that could have made
    # it otherwise — a column key is text and a tag is filtered to `type='text'`.
    # `json_extract` would hand back a real integer for a numeric value, and the
    # two readers would then disagree about it: `query` returns a LIST of groups
    # and would keep 1 and "1" apart, while `get_stats` returns a dict KEYED by
    # the group and JSON coerces an integer key to a string on the way out,
    # merging them. One tool, two answers to one question, decided by which verb
    # you called — so the key is cast once, here, and both readers see the same
    # groups. The cost is the other collision, named rather than hidden: a
    # numeric 1 and the string "1" share a group.
    #
    # A JSON boolean is spelled as the WORD for the same reason and it is NOT the
    # cast doing it: `json_extract` answers 1/0 for true/false, so casting alone
    # would merge `true` into the number 1 — a likelier neighbour than the string
    # "true", which is what it collides with instead.
    #
    # PARAMETER ORDER IS TEXTUAL, as everywhere else in this file: two path
    # placeholders in the SELECT, then the caller's WHERE values, then one more
    # for the scalar test. Prepending or appending as a block would bind the
    # path to a filter's placeholder and corrupt exactly the FILTERED queries.
    path = axis.meta_path
    return (
        "SELECT id, severity, occurrence_count, "
        f"CASE WHEN json_valid(meta, {_JSON5}) THEN (CASE json_type(meta, ?) "
        "WHEN 'true' THEN 'true' WHEN 'false' THEN 'false' "
        "ELSE CAST(json_extract(meta, ?) AS TEXT) END) END AS group_key "
        f"FROM findings {where} {more} "
        f"CASE WHEN json_valid(meta, {_JSON5}) THEN {_META_SCALAR_TEST} ELSE 0 END",
        [path, path, *params, path],
    )


def _axis_counts(
    conn: sqlite3.Connection,
    *,
    axis: _GroupAxis,
    conditions: list[str],
    params: list[Any],
    member_sql: str,
    member_params: list[Any],
) -> dict[str, int]:
    """The four numbers that keep a set of group counts honest.

    ``population`` — rows matching the filters.
    ``ungrouped_rows`` — rows this axis put in NO group. Never derivable from the
        groups themselves, and "none" and "forty" are different facts about a
        corpus, so it is reported even when it is zero.
    ``multi_group_rows`` — rows this axis put in more than one group. Non-zero
        means the counts DOUBLE-COUNT and do not sum to the population.
    ``nonscalar_value_rows`` — a SUBSET of ``ungrouped_rows``: the key is present
        but holds an object or an array, so there is no single value to group by.
        It is named apart from the rest because on this tracker the key `loc` is
        a container on 169 of 172 rows (measured 2026-08-25), and reporting those
        as "carries no value" would be a different, wrong statement about it.

    All four are present on every axis. `[]`-discipline, as `attention` and
    `stripped_meta_keys` already do it here: a key that appears only sometimes
    teaches a reader to test for presence, at which point presence encodes a
    second fact.

    The numbers come from the membership relation, never from a second reading of
    the axis — so a change to how an axis groups moves the groups and the numbers
    describing them together, and cannot leave one telling the truth about a
    population the other no longer has.
    """
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    population = conn.execute(f"SELECT COUNT(*) AS c FROM findings {where}", params).fetchone()[
        "c"
    ]
    spread = conn.execute(
        "SELECT COUNT(*) AS grouped, "
        "COALESCE(SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END), 0) AS multi FROM "
        f"(SELECT id, COUNT(*) AS n FROM ({member_sql}) GROUP BY id)",
        member_params,
    ).fetchone()

    counts = {
        "population": population,
        # Absent, JSON null, malformed JSON and — for the meta axis — a container
        # value all land here: one answer to one question, the row carries no
        # value on this axis. Derived by subtraction from the SAME relation the
        # groups came from, so no row can be counted in both or in neither.
        "ungrouped_rows": population - spread["grouped"],
        "multi_group_rows": spread["multi"],
        "nonscalar_value_rows": 0,
    }
    if axis.kind == "meta":
        counts["nonscalar_value_rows"] = conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN json_valid(meta, {_JSON5}) THEN "
            "(CASE WHEN json_type(meta, ?) IN ('object','array') THEN 1 ELSE 0 END) "
            f"ELSE 0 END), 0) AS c FROM findings {where}",
            [axis.meta_path, *params],
        ).fetchone()["c"]
    return counts


def _group_cell(value: Any) -> str:
    """A group key as TEXT, for display only.

    INSURANCE, and a mutant that deletes it SURVIVES — said that way round
    because the first draft of this claimed it was load-bearing and no test can
    discriminate it. Every group key IS already a string today, by three separate
    mechanisms: the five column axes are `NOT NULL` TEXT columns, the tag axis
    filters `je.type = 'text'`, and the meta axis CASTs. So `sorted()` and
    `f"{grp:30s}"` would both be safe without this.

    It stays because that invariant lives in three places and a sixth axis added
    without a CAST would crash a shipped verb rather than misprint one row —
    `sorted()` refuses to order an int against a str, and `f"{grp:30s}"` refuses
    an int outright. One line at the presentation edge makes that unrepresentable
    instead of requiring the next author to rediscover it.
    """
    return str(value)


def _group_disclosure(result: dict[str, Any]) -> str:
    """The line that stops right numbers from being read with the wrong meaning.

    A reader who has only ever grouped by a column has learned, correctly and
    silently, that the counts sum to the population. `tag` breaks that in both
    directions at once — a two-tag card is counted twice and an untagged one not
    at all — and the table alone cannot show either. Printed on EVERY grouped
    result, including the column axes where all three numbers are zero: "no rows
    without tags" and "forty rows without tags" are different facts about a
    corpus, and a line that appears only when it is non-zero cannot report the
    first one.
    """
    return (
        f"population {result['population']} row(s) — "
        f"{result['ungrouped_rows']} in no group, "
        f"{result['multi_group_rows']} in more than one group, "
        f"{result['nonscalar_value_rows']} with a non-scalar value; "
        "the counts partition the population only while those three are 0."
    )


def query_findings(
    conn: sqlite3.Connection,
    *,
    id: str | None = None,
    ids: list[str] | None = None,
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    file: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    meta_key: str | None = None,
    meta_value: str | None = None,
    commit: str | None = None,
    ref: str | None = None,
    fingerprint: str | None = None,
    group_by: str | None = None,
    limit: int = 100,
    offset: int = 0,
    resolve_anchors: bool = False,
    project_dir: str | None = None,
) -> dict[str, Any]:
    """Query findings with filters. Returns results or grouped counts.

    `id` / `ids` are AND-combined with other filters; missing IDs are silently absent.
    `commit` matches the frozen first-report column OR any occurrence-ring entry
    (CB-128) — any observation, not the newest (that is `provenance._effective_commit`).

    Every returned finding carries an ``anchor`` summary while a read enricher is
    registered, and ``resolve_anchors`` decides only whether that summary COSTS
    anything. The default is False, and the asymmetry with ``get_finding`` is the
    design rather than an oversight: resolving one anchor is two to four git
    calls, and this is the primary read path with a page of up to a hundred rows.
    Presence of an anchor is reported either way, out of the ``meta`` the row had
    already read.

    With the flag on, the page is resolved in ONE pass — the per-tree context is
    built once for the whole population, never per row.
    """
    # CB-196. Validated HERE, at the top, and specifically ABOVE the `ids`
    # widening below: that widening runs only inside `if ids:` and rewrites a
    # caller's `-1` to `len(ids)`, so a check placed after it would be a gate
    # that cannot fire for exactly the calls that carry an id list, while still
    # firing for the bare call. One argument, two verdicts, decided by an
    # unrelated parameter. Validating the ARGUMENT rather than the derived value
    # is also this package's standing rule (CB-82): a refusal must cost no
    # partial work.
    limit = require_row_limit("limit", limit)

    conditions: list[str] = []
    params: list[Any] = []

    # Free-text filter: exact match on an opaque token, no resolver — so the
    # None/'' convention comes from is_text_filter_active, which itself refuses
    # non-strings (there is no downstream resolver to do it). Stripped to match
    # the write side (_validate_fingerprint stores stripped tokens; an untrimmed
    # query for the token you just added must not return zero rows), and a
    # whitespace-only active filter is wrong input, same as on write.
    if is_text_filter_active(fingerprint):
        fingerprint = fingerprint.strip()
        if not fingerprint:
            raise ValueError("fingerprint filter must be non-empty")
        conditions.append("fingerprint = ?")
        params.append(fingerprint)

    if id:
        conditions.append("id = ?")
        params.append(id)
    if ids:
        conditions.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
        if limit < len(ids):
            limit = len(ids)
    if is_vocabulary_filter_active(status):
        conditions.append("status = ?")
        params.append(resolve_finding_status(status))
    if is_vocabulary_filter_active(severity):
        # Resolved, like `status` two lines up. Left raw, this filter compared the
        # caller's spelling against a canonical column: `severity="HIGH"` silently
        # returned ZERO rows rather than raising (CB-19). Once the write paths
        # normalize, leaving this raw would be worse still — the write would land
        # as `high` and the read-back by the same spelling would find nothing.
        conditions.append("severity = ?")
        params.append(resolve_severity(severity))
    if category:
        conditions.append("category = ?")
        params.append(category)
    if file:
        conditions.append("file LIKE ?")
        params.append(f"%{file}%")
    if source:
        conditions.append("source = ?")
        params.append(source)
    if tag:
        conditions.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)")
        params.append(tag)
    if meta_value and not meta_key:
        # A lone `meta_value` matched neither arm below, so it added no condition and
        # the caller got the unfiltered queue back (CB-28). The MCP description already
        # declares meta_key required; this enforces it instead of discarding the value.
        raise ValueError("meta_value requires meta_key")
    if meta_key and meta_value:
        conditions.append("json_extract(meta, ?) = ?")
        params.append(f"$.{meta_key}")
        params.append(meta_value)
    elif meta_key:
        conditions.append("json_extract(meta, ?) IS NOT NULL")
        params.append(f"$.{meta_key}")
    if commit:
        if not re.fullmatch(r"[0-9a-fA-F]+", commit):
            raise ValueError(f"commit filter must be hex, got: {commit!r}")
        # Column OR occurrence ring (CB-128). The column is frozen at first report
        # (CB-53); re-observations live in meta.occurrences[*].reported_at_commit.
        # Semantics: ANY observation on the commit matches — deliberately unlike
        # provenance._effective_commit, which wants the NEWEST observation
        # because it answers a different question (how stale is the card).
        #
        # Two guards, both measured (SQLite 3.47): `json_valid(meta)` inside a
        # CASE — CASE branches are documented-lazy, AND short-circuit is not —
        # keeps a malformed meta on an UNRELATED row from aborting the whole
        # query; `json_each.type = 'object'` keeps a non-object ring element
        # (a bare string) from doing the same via json_extract on the value,
        # which json_valid(meta) cannot see. `json_type = 'array'` mirrors the
        # reader's `isinstance(ring, list)`: a dict ring must not match by value.
        # Both placeholders are bound here, paired with the condition.
        conditions.append(
            "(reported_at_commit LIKE ? || '%'"
            " OR CASE WHEN json_valid(meta) THEN"
            "      json_type(meta, '$.occurrences') = 'array'"
            "      AND EXISTS (SELECT 1 FROM json_each(meta, '$.occurrences')"
            "                  WHERE json_each.type = 'object'"
            "                    AND json_extract(json_each.value, '$.reported_at_commit')"
            "                        LIKE ? || '%')"
            "    END)"
        )
        params.extend([commit.lower(), commit.lower()])
    if ref:
        conditions.append("reported_at_ref = ?")
        params.append(ref)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if group_by:
        axis = _resolve_group_axis(group_by)
        if resolve_anchors:
            # CB-28: forward where a path exists, refuse only where none could.
            # A grouped result has no rows to annotate, so nothing here can
            # honour the argument, and ignoring it would return a success
            # payload with the caller's request discarded.
            raise ValueError(
                "resolve_anchors is not available with group_by: a grouped result "
                "carries counts, not findings, so there is nothing to annotate"
            )
        member_sql, member_params = _membership_sql(axis, conditions=conditions, params=params)
        rows = conn.execute(
            f"SELECT group_key, COUNT(*) AS count FROM ({member_sql}) "
            "GROUP BY group_key ORDER BY count DESC, group_key ASC",
            member_params,
        ).fetchall()
        counts = _axis_counts(
            conn,
            axis=axis,
            conditions=conditions,
            params=params,
            member_sql=member_sql,
            member_params=member_params,
        )
        return {
            "grouped": True,
            "group_by": group_by,
            "groups": [dict(r) for r in rows],
            **counts,
        }

    count = conn.execute(f"SELECT COUNT(*) as c FROM findings {where}", params).fetchone()["c"]

    # Order by DECLARED severity precedence, not alphabetically (CB-20). A bare
    # `ORDER BY severity` ranks `low` above `medium`, which under a LIMIT
    # truncates the more important rows rather than merely displaying them oddly.
    #
    # PARAMETER ORDER IS LOAD-BEARING. The CASE placeholders sit textually after
    # the WHERE fragment and before LIMIT/OFFSET, so its values must be spliced
    # in exactly that position. Prepending them instead would bind severities to
    # the WHERE placeholders — corrupting every *filtered* query while unfiltered
    # ones kept passing. The count query above must also run before this extend.
    rank_sql, rank_params = rank_case_sql("severity", SEVERITIES)
    params.extend(rank_params)
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM findings {where} ORDER BY {rank_sql}, created_at DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    found = [db.row_to_dict(r) for r in rows]
    _enrich_read(found, conn, resolve=resolve_anchors, project_dir=project_dir)
    return {
        "grouped": False,
        "total": count,
        "limit": limit,
        "offset": offset,
        "findings": found,
    }


# --- The `recent` window (CB-123) ---------------------------------------------
#
# A SECOND reading path over `findings`, deliberately, rather than a `since`
# parameter on `query_findings`. The cost is named rather than hidden: two SELECT
# builders now read this table. What it buys is that neither of the two hazards
# `query_findings` carries is entered at all. Its comment above warns that the
# severity-rank CASE placeholders sit textually between the WHERE fragment and
# LIMIT/OFFSET, so a new condition spliced in there corrupts *filtered* queries
# only — this query has no CASE, so the hazard does not exist here rather than
# being covered by a test. And with no `order_by` argument, no caller-supplied
# column ever reaches `ORDER BY`, so CB-20 (a vocabulary column sorted
# alphabetically) and CB-22 (an interpolated identifier) cannot be reopened by an
# argument that does not exist. Module ownership is unaffected: `findings.py`
# owns this table.
_SINCE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SINCE_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?")


def _validate_since(since: object) -> str:
    """Refuse anything that is not a whole date or a whole ISO second.

    ``since`` is a MANDATORY filter, so the query-side convention — ``None`` and
    ``""`` mean "no filter" (CB-25) — is exactly wrong here: there is no honest
    "everything" to fall back to, and a silently widened window answers a
    question nobody asked. This is the WRITE-side rule (CB-82) applied to a
    required argument: validate before anything is parsed or queried, so a
    refusal costs no work, and raise the ``ValueError`` the module contract
    promises rather than leaking whatever SQLite would have done.

    Decided by TYPE first, for ``is_vocabulary_filter_active``'s reason: never
    run ``==`` or ``len()`` on the value, because ``unittest.mock.ANY`` compares
    equal to any string and a ``str`` subclass can override either. Truthiness is
    the specific trap — ``since=0`` would bind into ``updated_at >= 0``, which
    matches every row, handing the caller the whole table dressed as a window.
    """
    if not isinstance(since, str):
        raise ValueError(
            "since must be a date 'YYYY-MM-DD' or an ISO timestamp "
            f"'YYYY-MM-DDTHH:MM:SSZ', got: {since!r}"
        )
    # Stripped for the same reason the fingerprint filter is: the write side
    # stores trimmed timestamps, so an untrimmed bound must not read as garbage.
    text = str.strip(since)
    if _SINCE_DATE_RE.fullmatch(text):
        fmt = "%Y-%m-%d"
    elif _SINCE_TIMESTAMP_RE.fullmatch(text):
        fmt = "%Y-%m-%dT%H:%M:%SZ" if text.endswith("Z") else "%Y-%m-%dT%H:%M:%S"
    else:
        raise ValueError(
            "since must be a date 'YYYY-MM-DD' or an ISO timestamp "
            f"'YYYY-MM-DDTHH:MM:SSZ', got: {since!r}"
        )
    # Shape is not meaning: `2026-02-31` and `2026-13-01` both match the pattern
    # and are not dates. A bound that is not a real instant would order
    # lexicographically against the column anyway and quietly return the wrong set.
    try:
        datetime.strptime(text, fmt)
    except ValueError as exc:
        raise ValueError(f"since is not a real date or time: {since!r} ({exc})") from exc
    return text


def recent_findings(
    conn: sqlite3.Connection,
    *,
    since: str,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Findings whose LAST TOUCH (``updated_at``) is at or after ``since``.

    WHAT THIS MEASURES. ``updated_at`` is the time of the last WRITE to the row,
    not the moment the finding was closed — there is no close timestamp anywhere
    in the schema. A status change moves it, and so do a re-tag, a meta patch, a
    severity re-triage, an ``append_note``, and a DEDUPLICATED OBSERVATION (a
    repeat report bumps the occurrence count and stamps ``updated_at`` while the
    status stays exactly where it was).

    So ``recent_findings(since=…, status="fixed")`` means *cards that are fixed
    NOW and were touched since that date*, not *cards closed since that date*.
    The error is ONE-SIDED: false positives are possible, misses are not, because
    closing a card always writes ``updated_at``.

    ``since`` is inclusive (``>=``). With a date-granular bound the exclusive
    form would silently drop the whole first day, and a net-delta count built on
    it would be quietly wrong.

    Rows come back newest touch first. ``rowid DESC`` is not decoration:
    ``utc_now()`` is whole-second, so ties are the ordinary case, and without a
    tiebreaker the page boundary under ``LIMIT`` is arbitrary. Same form as
    ``_match_fingerprint``'s closed-row lookup, deliberately — no new precedent.

    Raises ``ValueError`` on a ``since`` that is not a date, and on a ``status``
    outside the finding vocabulary. ``status=None``/``""`` means every status.
    """
    since_value = _validate_since(since)

    conditions = ["updated_at >= ?"]
    params: list[Any] = [since_value]
    status_value: str | None = None
    if is_vocabulary_filter_active(status):
        # Resolved on the query side as well as the write side (CB-19): a filter
        # spelled `done` must find the row stored as `fixed`, or the caller
        # writes a value and cannot read it back by the same spelling.
        status_value = resolve_finding_status(status)
        conditions.append("status = ?")
        params.append(status_value)

    where = f"WHERE {' AND '.join(conditions)}"
    count = conn.execute(f"SELECT COUNT(*) AS c FROM findings {where}", params).fetchone()["c"]

    # Every fragment is appended with its own parameter, in textual order, and the
    # page bounds are spliced at the end where their placeholders sit. Written as
    # one expression rather than a sequence of `extend`s so that no statement can
    # ever be inserted between a fragment and its value (CB-41's lesson:
    # point-of-use discipline is the wrong enforcement layer).
    rows = conn.execute(
        f"SELECT * FROM findings {where} ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    found = [db.row_to_dict(r) for r in rows]
    # The anchor summary is carried here too, and CHEAPLY — never resolved, and
    # with no flag to turn resolution on. Two reasons, and the first is this
    # package's own doctrine: `recent` returns the same rows from the same table
    # through the same surface as `query`, so a key present on one and absent on
    # the other teaches a reader to test for presence, at which point presence
    # encodes a second fact (review found the asymmetry). The second is that
    # `recent` answers "what changed", not "where is it" — a caller who wants
    # coordinates has `get` for one card and `query(resolve_anchors=True)` for a
    # page, and a third resolving surface would be a third place to keep honest.
    _enrich_read(found, conn, resolve=False, project_dir=None)
    return {
        "total": count,
        "limit": limit,
        "offset": offset,
        "since": since_value,
        "status": status_value,
        "findings": found,
    }


def get_stats(
    conn: sqlite3.Connection,
    *,
    group_by: str = "severity",
) -> dict[str, Any]:
    """Aggregated counts. Returns cross-tabulated stats.

    The axis vocabulary is `_resolve_group_axis`'s, shared byte for byte with
    `query_findings` — the two used to carry hand-written twins of one set in
    different orders, with nothing holding them together.

    The three disclosure numbers ride along for the same reason they do there:
    with `tag` on the axis the printed TOTAL exceeds the number of findings,
    because a two-tag card is counted under both of its tags. That is not an
    error, and a reader has no way to know it from the table alone.
    """
    axis = _resolve_group_axis(group_by)
    member_sql, member_params = _membership_sql(axis, conditions=[], params=[])

    rows = conn.execute(
        f"""SELECT group_key as grp, severity, COUNT(*) as cnt,
                   SUM(occurrence_count) as occ
            FROM ({member_sql})
            GROUP BY grp, severity
            ORDER BY grp, severity""",
        member_params,
    ).fetchall()
    counts = _axis_counts(
        conn,
        axis=axis,
        conditions=[],
        params=[],
        member_sql=member_sql,
        member_params=member_params,
    )

    groups: dict[str, dict[str, int]] = {}
    for r in rows:
        grp = r["grp"]
        if grp not in groups:
            groups[grp] = {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "total": 0,
                # Row counts under-report once dedup collapses a family into one
                # row; the occurrence sum is the observation count.
                "occurrences": 0,
            }
        groups[grp][r["severity"]] = r["cnt"]
        groups[grp]["total"] += r["cnt"]
        groups[grp]["occurrences"] += r["occ"]

    return {"group_by": group_by, "groups": groups, **counts}


def get_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Dashboard-style overview."""
    total = conn.execute("SELECT COUNT(*) as c FROM findings").fetchone()["c"]
    occ = conn.execute(
        "SELECT COALESCE(SUM(occurrence_count), 0) as t, "
        "COALESCE(SUM(CASE WHEN status = 'open' THEN occurrence_count ELSE 0 END), 0) as o "
        "FROM findings"
    ).fetchone()
    by_status = {}
    for r in conn.execute("SELECT status, COUNT(*) as c FROM findings GROUP BY status"):
        by_status[r["status"]] = r["c"]

    # Declared precedence, not alphabetical (CB-20) — this dict's key order IS the
    # dashboard's row order, and nothing downstream re-sorts it. Unlike `get_stats`,
    # which pre-seeds its dict with the vocabulary and so is immune to row order,
    # this one is built straight from the rows.
    rank_sql, rank_params = rank_case_sql("severity", SEVERITIES)
    by_severity = {}
    for r in conn.execute(
        "SELECT severity, COUNT(*) as c FROM findings WHERE status = 'open' "
        f"GROUP BY severity ORDER BY {rank_sql}",
        rank_params,
    ):
        by_severity[r["severity"]] = r["c"]

    open_count = by_status.get("open", 0)

    top_categories = []
    for r in conn.execute(
        "SELECT category, COUNT(*) as c FROM findings WHERE status = 'open' GROUP BY category ORDER BY c DESC LIMIT 5"
    ):
        top_categories.append({"category": r["category"], "count": r["c"]})

    hottest_files = []
    for r in conn.execute(
        """SELECT file, COUNT(*) as total_open,
                  SUM(CASE WHEN severity IN ('critical', 'high') THEN 1 ELSE 0 END) as crit_high
           FROM findings WHERE status = 'open'
           GROUP BY file ORDER BY crit_high DESC, total_open DESC LIMIT 5"""
    ):
        hottest_files.append(
            {
                "file": r["file"],
                "open": r["total_open"],
                "critical_high": r["crit_high"],
            }
        )

    return {
        "total": total,
        "open": open_count,
        "resolved": total - open_count,
        # Observation counts — row counts under-report exactly the families dedup
        # collapses (a 40-occurrence defect is one row).
        "total_occurrences": occ["t"],
        "open_occurrences": occ["o"],
        "by_status": by_status,
        "open_by_severity": by_severity,
        "top_categories": top_categories,
        "hottest_files": hottest_files,
    }


def get_categories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List all categories with counts, for consistency checking."""
    rows = conn.execute(
        """SELECT category, COUNT(*) as total,
                  SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_count,
                  SUM(CASE WHEN status = 'fixed' THEN 1 ELSE 0 END) as fixed_count
           FROM findings GROUP BY category ORDER BY total DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


# --- Category retro-fold (CB-61) ---------------------------------------------
#
# CB-60 canonicalizes the OBSERVATION write path; the stored corpus keeps whatever
# spellings it was filed with, and `fingerprint` is STORED, never recomputed. Since
# category is an input of the `auto:v1` hash, a pre-gate row re-observed with its OWN
# spelling derives a hash the row does not carry and FORKS identity instead of bumping
# (CB-113(a)) — and the mint gate does not refuse it, because `_existing_categories`
# normalizes stored spellings for its membership test. This is the one-shot migration
# that folds the corpus to canon and re-keys the hashes so the fork closes.

_FOLD_SELECT = (
    "SELECT id, category, file, description, meta, fingerprint, status FROM findings ORDER BY id"
)

# Report keys for the per-row outcome. `category_only` is the SUM of the two
# untouched-hash kinds, not a kind of its own.
_FOLD_KIND_ACTION = {
    "refingerprinted": "rederived",
    "supplied_untouched": "untouched_supplied",
    "null_untouched": "untouched_null",
}

# How many `merged_identities` groups the HUMAN report prints before it stops and
# says how many it did not. `--json` is never capped. See `_print_fold_report`.
_FOLD_MERGED_GROUPS_SHOWN = 20


def _validate_fold_map(fold_map: object) -> dict[str, str] | None:
    """Validate a caller-supplied fold map. ``None`` means "derive the map".

    ``None`` and ``{}`` are DIFFERENT (the "no filter is None and '', never
    truthiness" doctrine applied to a write argument): ``None`` asks for the
    mechanical fold, ``{}`` is an explicit no-op that renames nothing.

    Every target must already be canonical. A fold whose target is not what
    ``normalize_category`` returns would produce a spelling the very next
    observation folds again — a migration that leaves its own defect behind.
    """
    if fold_map is None:
        return None
    if not isinstance(fold_map, dict):
        raise ValueError(f"fold_map must be a mapping, got {type(fold_map).__name__}")
    for variant, target in fold_map.items():
        if not isinstance(variant, str) or not isinstance(target, str):
            raise ValueError(
                f"fold_map entries must be strings, got {variant!r} -> {target!r}"
            )
        canonical = normalize_category(target)
        if canonical != target:
            raise ValueError(
                f"fold_map target {target!r} (for {variant!r}) is not canonical — "
                f"normalize_category gives {canonical!r}; folding to a non-canonical "
                f"spelling only defers the fork"
            )
    return dict(fold_map)


def _fold_row_decision(
    row: sqlite3.Row, fold_map: dict[str, str] | None
) -> tuple[str, str | None, str | None]:
    """Classify one stored row: ``(kind, target_category, new_fingerprint)``.

    Kinds: ``unchanged``, ``skipped_non_string``, ``unverifiable``,
    ``null_untouched``, ``supplied_untouched``, ``refingerprinted``.

    The ROUND-TRIP is the honesty of this migration. A stored `auto:v1` hash is
    re-keyed only after the stored inputs are shown to reproduce the stored value
    — note the stored ``meta`` is NOT the meta the hash was derived from (the
    occurrence ring, `category_minted` and `similar_to` are all added later), so
    "these keys do not move the hash" is something to VERIFY per row, not assume.
    When the round trip fails the row is skipped WHOLE: rewriting the category
    without re-keying is exactly the permanent identity desync this card closes,
    and fabricating a hash from inputs that provably do not reproduce the stored
    one is worse than either.
    """
    stored = row["category"]
    if not isinstance(stored, str):
        # SQLite's dynamic typing lets an explicit-id add store a non-string
        # category; `_existing_categories` skips one for the same reason. One
        # legacy row must not abort a corpus-wide migration.
        return ("skipped_non_string", None, None)
    target = normalize_category(stored) if fold_map is None else fold_map.get(stored)
    if target is None or target == stored:
        return ("unchanged", None, None)
    fingerprint = row["fingerprint"]
    if fingerprint is None:
        return ("null_untouched", target, None)
    if not isinstance(fingerprint, str) or not fingerprint.startswith(_AUTO_V1_PREFIX):
        # Supplied (or a future derivation version): the caller owns that token's
        # meaning, so the migration renames the category and does not touch it.
        return ("supplied_untouched", target, None)
    try:
        meta = json.loads(row["meta"])
        if not isinstance(meta, dict):
            raise ValueError("stored meta is not an object")
        if _derive_fingerprint(stored, row["file"], row["description"], meta) != fingerprint:
            raise ValueError("stored inputs do not reproduce the stored fingerprint")
        new_fingerprint = _derive_fingerprint(target, row["file"], row["description"], meta)
    except (ValueError, TypeError, AttributeError):
        # ValueError covers json.JSONDecodeError; the other two cover a row whose
        # description/file/meta hold a non-string SQLite value.
        return ("unverifiable", target, None)
    return ("refingerprinted", target, new_fingerprint)


def _plan_category_fold(
    conn: sqlite3.Connection,
    fold_map: dict[str, str] | None,
    *,
    new_category: bool = False,
) -> dict[str, Any]:
    """Read the whole population and decide. Writes nothing; the caller applies."""
    if fold_map is not None:
        # CB-223. An EXPLICIT target this tracker does not hold is a MINT, and it
        # goes through the very gate an observation's category goes through. Without
        # it a typo in a target silently created a new category name — in the one
        # operation whose entire purpose is to REDUCE the number of names, and with
        # nothing in the report saying so. The target is already normalized by
        # construction (`_validate_fold_map` refuses a non-canonical one), which is
        # exactly the input `_gate_category` documents itself as taking.
        #
        # The gate sits HERE, before the population scan, so it fires identically on
        # the dry run and on `apply=True`: `_plan_category_fold` is what both modes
        # call. A dry run whose whole job is to say what would happen must say
        # "it would refuse". Note this validates the ARGUMENT, exactly as
        # `_validate_fold_map` above it does, so a bad target is refused whether or
        # not any stored row happens to match its source.
        #
        # THE DERIVED MODE (`fold_map is None`) IS NOT GATED, and that is a PROOF,
        # not an exemption. Its targets are `normalize_category(stored)` over the
        # stored spellings, and `_existing_categories` KEYS on exactly that
        # normalized form — so every derived target is already a key of `existing`
        # and the gate could only ever pass. Gating it against the RAW spellings
        # instead would make the mechanical fold refuse itself. The proof rests on
        # `_existing_categories`' keying, so it breaks if that keying ever changes;
        # `tests/test_category_fold.py::TestDerivedFoldIsNotGated` pins it.
        # The gate is handed the NORMALIZED form as the display value for every
        # category, and that is not tidiness — without it the two refusals of this
        # one command CONTRADICT each other. `_gate_category`'s messages name
        # `existing[key]`, the STORED spelling, which is right for `add`, where the
        # caller's category is normalized at the boundary: told "use the existing
        # spelling 'Process-Improvement'", an observer types it and it works. A fold
        # target has no such boundary — `_validate_fold_map` REFUSES a target that is
        # not already canonical — so on a pre-CB-60 corpus (exactly the corpus this
        # command exists for) the near-miss branch advised 'Process-Improvement' and
        # the next run refused that very value as non-canonical, and the "nearest
        # existing" list could name three values of which none was a legal target.
        # Measured, and found by adversarial review rather than by the measurement
        # that was supposed to find it: a fixture whose stored spellings are already
        # canonical cannot exhibit it. Swapping the VALUES changes no decision — the
        # accept/reject test and the distance ranking both read the KEYS, which are
        # already the normalized forms — so this only makes every name the refusal
        # prints a value the command will actually accept.
        existing = {norm: norm for norm in _existing_categories(conn)}
        # First bad target wins: `_gate_category` raises, and it is used verbatim
        # rather than re-implemented as a collector ("a check duplicated rather than
        # shared is one edit from disagreeing with itself"). The cost is real and
        # named on `normalize_categories`: a ten-pair map with two typos takes two
        # runs to clear, not one.
        for target in dict.fromkeys(fold_map.values()):
            _gate_category(existing, target, new_category=new_category)

    renames: list[dict[str, Any]] = []
    unverifiable: list[dict[str, Any]] = []
    counts = {
        "category_only": 0,
        "refingerprinted": 0,
        "supplied_untouched": 0,
        "null_untouched": 0,
        "unverifiable": 0,
        "skipped_non_string": 0,
    }
    by_fingerprint: dict[str, list[dict[str, Any]]] = {}
    # Every row that will hold an identity after the fold, live or not. The map
    # above is the LIVE half of this one and stays exactly what it was: it feeds
    # the stop-rule, whose whole argument rests on the partial unique index being
    # live-only. This one feeds `merged_identities`, which reports and never stops.
    by_fingerprint_all: dict[str, list[dict[str, Any]]] = {}
    # The categories the corpus actually holds, as STORED. This is what a fold_map
    # key is matched against, so it is what decides whether a key matched at all.
    stored_categories: set[str] = set()
    rows_scanned = 0

    for row in conn.execute(_FOLD_SELECT):
        rows_scanned += 1
        kind, target, new_fingerprint = _fold_row_decision(row, fold_map)
        # CB-207, and the trap is that this set CANNOT be derived from `renames`:
        # a key whose target EQUALS the stored value decides `unchanged`, so it
        # reaches neither `renames` nor any counter, and a report built from the
        # RESULT would call a key that named its category perfectly a miss
        # (measured). The test is the row's own KIND rather than a second
        # `isinstance` — `skipped_non_string` IS "the stored category is not a
        # str" — so the report judges by the same predicate the fold acts on
        # instead of by a copy of it. It sits beside the counting chain rather
        # than inside it because `unchanged` rows take no branch there at all.
        if kind != "skipped_non_string":
            stored_categories.add(row["category"])

        if kind == "skipped_non_string":
            counts["skipped_non_string"] += 1
        elif kind == "unverifiable":
            counts["unverifiable"] += 1
            unverifiable.append(
                {
                    "id": row["id"],
                    "category": row["category"],
                    "to": target,
                    "fingerprint": row["fingerprint"],
                }
            )
        elif kind in _FOLD_KIND_ACTION:
            counts[kind] += 1
            if kind != "refingerprinted":
                counts["category_only"] += 1
            renames.append(
                {
                    "id": row["id"],
                    "from": row["category"],
                    "to": target,
                    "fingerprint_action": _FOLD_KIND_ACTION[kind],
                    "old_fingerprint": row["fingerprint"],
                    # For an untouched hash the POST-fold value is the stored one.
                    "new_fingerprint": (
                        new_fingerprint if new_fingerprint is not None else row["fingerprint"]
                    ),
                }
            )

        # Post-fold occupancy of the partial unique index
        # (`ux_findings_fingerprint_live`): live rows, non-NULL hash. A row this
        # run does NOT re-key still occupies its stored value.
        effective = new_fingerprint if kind == "refingerprinted" else row["fingerprint"]
        if effective is not None:
            member = {
                "id": row["id"],
                "status": row["status"],
                "from": row["category"],
                "to": target if kind in _FOLD_KIND_ACTION else None,
                # The PRE-fold identity, and the whole discriminator for CB-209
                # below: members that already carried this value were not brought
                # together by this run. A row in this map always has one — a NULL
                # hash is `null_untouched`, whose effective value is NULL too, so
                # it never reaches here.
                "old_fingerprint": row["fingerprint"],
            }
            # `from` is the RAW column value, as it has always been in the
            # stop-rule's row. A BLOB category therefore makes the whole report
            # unserializable, on `--json` and over MCP — pre-existing for a LIVE
            # collision, and reaching a CLOSED row through this map. Not repaired
            # here: the only lossless repair decides how the report REPRESENTS a
            # non-string category and must apply to both lists at once, or one
            # field means two things. CB-229 carries it.
            # A NON-STRING token is left out of THIS map, and the exclusion is
            # PROVABLY LOSSLESS rather than defensive. SQLite's dynamic typing
            # permits one exactly as it permits a non-string category, and such a
            # row is `supplied_untouched`: the fold never re-keys it, and nothing
            # can arrive at its value either, because a re-derived fingerprint is
            # always a `str` beginning with `auto:v1:`. So it can never take part
            # in a merge this run creates, and keeping it would buy nothing while
            # WIDENING a pre-existing crash — `sorted()` cannot order bytes
            # against str, and until this map existed only the live one, which is
            # left exactly as it was, could meet that. Measured on both sides.
            if isinstance(effective, str):
                by_fingerprint_all.setdefault(effective, []).append(member)
            if row["status"] in LIVE_STATUSES:
                # The stop-rule's row shape is UNCHANGED, and it is PROJECTED from
                # the record above rather than spelled a second time — two literals
                # of one row shape are one edit from disagreeing.
                by_fingerprint.setdefault(effective, []).append(
                    {k: member[k] for k in ("id", "status", "from", "to")}
                )

    # Two live rows cannot already share a hash — the partial unique index forbids
    # it — so any collision here was CREATED by this fold. Ratified: name it and
    # stop; the pairs are material for the merge policy (CB-46), not for an
    # automatic merge this migration would have to invent a winner for.
    collisions = [
        {"fingerprint": fp, "rows": members}
        for fp, members in sorted(by_fingerprint.items())
        if len(members) > 1
    ]

    # CB-209. The stop-rule above can only see a group that is ENTIRELY live,
    # because that is the only group the partial unique index forbids. Every other
    # identity merge this fold performs was therefore silent — measured on both
    # shapes: two `fixed` cards folded onto one hash, and a `fixed` card folded
    # onto a LIVE card's hash, each applying at exit 0 with an empty `collisions`.
    # Both are REPORTED here and neither is refused, because the resulting state
    # is LEGAL: the index permits it, and CB-43's `recurrence_of` contract reaches
    # it with no fold at all. What is not legal is creating it in silence.
    #
    # THE DISCRIMINATOR IS AUTHORSHIP, AND IT IS SPELLED AS "MORE THAN ONE PRE-FOLD
    # IDENTITY", not as "the membership changed". Those are not the same rule, and
    # the second cries wolf on a REACHABLE case: a legal pair renamed WHOLESALE
    # lands on a new fingerprint whose membership went from nobody to both, while
    # remaining the one identity it always was. Asking how many distinct values the
    # members arrived carrying answers it — one value means they were already one
    # identity, two or more means this run fused them.
    #
    # THE MIRROR CASE — a group that LOSES a member — IS REACHABLE, and the first
    # draft of this comment called it impossible. Adversarial review built it: a
    # row can sit in the group carrying a hash derived from a category the map
    # does not name, so it stays put while the rows that CAN move are renamed out
    # from under it. What makes the rule immune is not that the case cannot happen
    # but its shape: every member still sitting on a fingerprint it did not move
    # to carries that same value as its PRE-fold one, so a group that only ever
    # shrinks holds exactly ONE distinct value and can never be reported. Immune
    # by construction, not by luck.
    #
    # Groups the stop-rule already refuses are excluded rather than listed twice.
    # KNOWN LIMIT, named rather than claimed away (adversarial review): the
    # stop-rule's own row list has always been LIVE-ONLY, so a CLOSED row that the
    # same fingerprint also collects appears in neither list. It costs one cycle —
    # the closed member surfaces here on the re-run, once the live collision that
    # stopped this one has been resolved — and the alternative, listing a refused
    # fingerprint in both, reports one group twice on a run that writes nothing.
    refused = {fp for fp, members in by_fingerprint.items() if len(members) > 1}
    merged_identities = [
        {"fingerprint": fp, "rows": members}
        for fp, members in sorted(by_fingerprint_all.items())
        if len(members) > 1
        and fp not in refused
        and len({m["old_fingerprint"] for m in members}) > 1
    ]

    # CB-207. Derived mode has no keys to miss — its targets ARE the stored
    # spellings — so `[]` there is the honest answer and not a channel that went
    # missing. Sorted so two runs over one tracker cannot disagree about order.
    unmatched_fold_keys = (
        []
        if fold_map is None
        else sorted(key for key in fold_map if key not in stored_categories)
    )

    return {
        "applied": False,
        # NEITHER new key touches this. `stopped` stays the live stop-rule's alone:
        # both are information, and turning a report into a refusal would deny the
        # operator the picture over a state the database itself permits.
        "stopped": bool(collisions),
        "fold_map": {r["from"]: r["to"] for r in renames},
        "unmatched_fold_keys": unmatched_fold_keys,
        "rows_scanned": rows_scanned,
        "renames": renames,
        "counts": counts,
        "collisions": collisions,
        "merged_identities": merged_identities,
        "unverifiable": unverifiable,
    }


def normalize_categories(
    conn: sqlite3.Connection,
    *,
    fold_map: dict[str, str] | None = None,
    apply: bool = False,
    new_category: bool = False,
) -> dict[str, Any]:
    """Rename stored categories and re-key their `auto:v1` hashes (CB-61).

    THIS DOES TWO THINGS, and the second is a working mode rather than a side
    effect (CB-222 — the contract used to promise only the first, while the code
    had always done both). **(1)** With no ``fold_map`` it folds every stored
    SPELLING to its own canonical form, so pre-CB-60 rows stop forking identity
    against the normalized spelling a new observation writes. **(2)** With a
    ``fold_map`` it MERGES CATEGORY NAMES: any stored name may be renamed to any
    canonical target, and the two names need not be spellings of each other. That
    is how a tracker's rare category names are collapsed into its common ones.

    DRY RUN BY DEFAULT: with ``apply=False`` nothing is written and no write
    transaction is opened at all. ``apply=True`` performs the whole migration
    inside ONE ``db.txn`` — the population is read under the write lock (CB-24),
    the collision decision is made from that read, and either every rename lands
    or none does.

    ``fold_map`` maps a STORED CATEGORY NAME to the TARGET NAME it becomes. The
    key is matched against the stored value exactly and may be any name the table
    holds; the value must already satisfy ``normalize_category(t) == t``, because
    folding to a non-canonical spelling only defers the fork. ``None`` (the
    default) derives the map mechanically — each stored spelling folds to its own
    normalized form. ``{}`` is an explicit no-op, not the default.

    A KEY THAT MATCHES NO STORED CATEGORY IS ACCEPTED AND NAMED (CB-207). It
    renames nothing — it is inert — but it no longer passes in silence: every such
    key is listed in ``unmatched_fold_keys``. That it is a REPORT while a bad
    TARGET is a REFUSAL (``new_category`` below) is deliberate, and the line runs
    between creating state and creating nothing: an unknown target would MINT a
    category name, which is the one thing an operation that exists to reduce their
    number must not do by typo, while an unmatched key leaves the tracker exactly
    as it was. Refusing it would also cost a real case — one map is reasonably
    kept across several trackers, and a key some of them do not hold is normal.

    The match is EXACT against the stored string, because that is how the fold
    itself matches (``fold_map.get(stored)``). So on a pre-CB-60 corpus a key
    typed in canonical form does not reach a stored ``"Process Improvement"`` and
    is reported as unmatched — which is the truth about that run, not a defect to
    file. Rows whose stored category is not a string are not part of the set a key
    is judged against, for ``_existing_categories``' reason.

    ``new_category`` is PERMISSION TO MINT — the same flag name and the same
    meaning as on ``add_finding`` (CB-60), and it applies to the whole map at once.
    It is NOT the same in its after-effect, and the difference is stated because the
    name invites the assumption: an observation that mints stamps
    ``meta.category_minted`` on the row it files, and a fold stamps nothing, so
    ``query(meta_key="category_minted")`` does not count a name minted this way. A
    report key for it was considered and refused by the direction — an unknown target
    is refused without the flag, so nothing is minted by accident, and with the flag
    the intent has been stated. A target this tracker does not
    already hold is refused without it (CB-223): the operation exists to REDUCE
    the number of category names, so a typo that quietly invents one more is the
    failure it must not have. The refusal names the nearest existing categories,
    or the canonical spelling when the target is a near-miss of one. It stops at
    the FIRST bad target rather than collecting them all, so a ten-pair map with
    two typos takes two runs to clear — the deliberate price of using the same
    gate the observation path uses instead of writing a second one. The DERIVED
    mode is never gated: its targets are normalized stored spellings, which the
    existing-category index is keyed by, so the gate could only ever pass there.

    BEFORE ``apply=True``, TAKE A BACKUP: ``export-csv`` writes the findings, and
    ``restore-csv`` puts them back VERBATIM (ids, statuses, occurrence counts,
    fingerprints) into an EMPTY tracker. Its boundary is real — milestone items
    and the audit history are not part of a CSV export and are not restored.

    Fingerprint policy, per renamed row: a ``NULL`` hash and a SUPPLIED hash are
    left byte-identical (only the category is rewritten); an ``auto:v1`` hash is
    round-tripped against the stored inputs and re-derived with the new category
    and the SAME normalizer version — only the category input moves (CB-54). A row
    whose stored inputs do not reproduce its stored hash is skipped WHOLE (its
    category is not rewritten either) and reported under ``unverifiable``; that
    does not stop the run, but a non-zero count is a decision for the operator.

    THE OCCURRENCE RING IS NOT REWRITTEN. ``meta`` never appears in an UPDATE here:
    occurrence records are verbatim evidence of what was observed, including the
    variant spelling this fold removes from the column.

    THIS IS THE ONE SANCTIONED RE-KEY. ``update_finding`` documents ``fingerprint``
    as immutable and stays that way — the relaxation is declared here and does not
    generalize; this function issues its own UPDATE rather than routing through the
    updater, so no caller acquires a re-key by argument.

    Collisions are REPORTED AND STOP, never auto-merged (ratified 2026-08-20): if
    the post-fold state would put two LIVE rows on one fingerprint, the whole run
    writes nothing and the report names the fingerprint, the row ids and their
    from/to categories. Known limit, fail-closed: a crafted ``fold_map`` can also
    make an INTERMEDIATE state (mid-loop) violate the partial unique index without
    the final state doing so — that raises ``sqlite3.IntegrityError`` and rolls the
    whole transaction back, so nothing lands either way.

    EVERY OTHER IDENTITY MERGE IS REPORTED, NOT REFUSED (CB-209), under
    ``merged_identities``. That rule reaches only groups that are entirely live,
    because that is all the index forbids; a fold that puts two CLOSED cards — or a
    closed card and a live one — on one fingerprint applied at exit 0 with an empty
    ``collisions`` (measured, both shapes). The state is legal, and CB-43's
    ``recurrence_of`` contract reaches it with no fold at all, so refusing would
    fire on states this migration never touched. Creating it SILENTLY is the
    defect: the lookup that reopens a closed card takes one row by
    ``updated_at DESC``, so of two closed twins on one hash a later observation
    revives whichever that order picks and the other is never reopened again. A
    group is reported when its members arrived carrying MORE THAN ONE pre-fold
    fingerprint — the test is authorship, not membership, because a group that
    merely LOSES a member to a rename, and one renamed wholesale onto a new hash,
    both have a changed membership and merge nothing. The CONSEQUENCE differs by
    the members' statuses and is worth stating precisely: two CLOSED twins on one
    hash mean a later observation revives one and abandons the other, while a
    closed card beside a LIVE one simply leaves the live card taking the
    observations, which is the ordinary shape closing a CB-113(a) fork produces.

    Returns the same report shape in both modes: ``applied``, ``stopped``,
    ``fold_map`` (only the pairs that actually matched rows), ``unmatched_fold_keys``,
    ``rows_scanned``, ``renames``, ``counts``, ``collisions``, ``merged_identities``,
    ``unverifiable``. The two lists named for CB-207 and CB-209 are UNCONDITIONAL,
    on the ``attention``/``stripped_meta_keys`` discipline: ``[]`` means "looked,
    there are none" and never "no such channel", and neither moves ``stopped`` or
    the exit code.
    """
    validated = _validate_fold_map(fold_map)
    if not apply:
        return _plan_category_fold(conn, validated, new_category=new_category)

    with db.txn(conn):
        report = _plan_category_fold(conn, validated, new_category=new_category)
        if report["stopped"]:
            # Nothing was written, so there is nothing to roll back — the empty
            # transaction commits and the refusal is a plain return (the
            # `TxnAbort`-sentinel design this repo already rejected).
            return report
        for rename in report["renames"]:
            if rename["fingerprint_action"] == "rederived":
                cur = conn.execute(
                    "UPDATE findings SET category = ?, fingerprint = ? WHERE id = ?",
                    (rename["to"], rename["new_fingerprint"], rename["id"]),
                )
            else:
                # `updated_at` is deliberately not bumped: it records an AUTHORED
                # change, and a spelling migration authors nothing.
                cur = conn.execute(
                    "UPDATE findings SET category = ? WHERE id = ?",
                    (rename["to"], rename["id"]),
                )
            # No RETURNING here, so rowcount is the outcome channel (the RETURNING
            # rule). A row that vanished between the planning read and this write
            # cannot happen under the held write lock — which is why anything but
            # exactly one row means the plan is not describing this database.
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"category fold: UPDATE for {rename['id']} touched {cur.rowcount} rows, "
                    f"expected 1 — the whole fold is rolled back"
                )
        report["applied"] = True
    return report


def _ambient_project_dir() -> str | None:
    """This process's working directory — the tree an MCP `add` is about.

    The MCP surface has no `project_dir` argument and deliberately does not gain
    one here: adding it would move the wire golden, and this unit adds no tools.
    So the server states the tree it is standing in, which is the SAME source it
    has always used for `reported_at_commit` (`git rev-parse HEAD` with an
    inherited cwd). Naming it once and passing it to both is what satisfies
    BT-7 Р3's actual requirement — that the revision and the root come from ONE
    source — and it is what makes the value an assertion by the caller rather
    than something a resolver reached for on its own.

    None when the directory has been deleted out from under a long-lived server
    (CB-79); every consumer treats that as "no tree", never as "the tree here".

    A near-twin of `provenance._ambient_cwd`, and NOT shared with it: provenance
    imports findings, so the dependency cannot run the other way.
    """
    try:
        return os.getcwd()
    except OSError:
        return None


def _ambient_head(project_dir: str | None) -> str | None:
    """The revision the tree at *project_dir* is standing on, or None (CB-144).

    THE ONE PLACE this capture is spelled. It used to be spelled twice, both
    times inside `register_tools` — once in `add`, once in `batch_add` — and the
    CLI handler, which is the other filing surface, simply did not spell it at
    all. A card filed by `codebugs add` therefore carried `reported_at_commit =
    NULL` forever, and BT-7's ratified location anchor is keyed on that frozen
    commit: reverse blame starts there, and the content channel reads the anchor
    text out of the object store AT that revision. So the anchor's coverage
    ceiling was set by the FILING SURFACE rather than by the mechanism.

    Collapsing the two copies into one function rather than adding a third is
    the whole shape of the fix. A rule expressed as an enumeration gets fixed at
    the sites someone enumerated, and this repository's recurring lesson is that
    the population is always larger than the list — the CLI handler WAS the item
    nobody had enumerated.

    WHAT THIS DELIBERATELY IS NOT: a default inside `add_finding`. The obvious
    "make the state unrepresentable" move is a REGRESSION here, and both paths
    that prove it are ratified. `import_findings` folds in another tracker's
    rows — an import is not an observation (CB-51), which is exactly why it
    already passes `annotate=False`, `escalate=False` and `promote_tags=False`
    — so stamping the LOCAL HEAD onto a foreign row would be a confidently wrong
    answer rather than a missing one. `restore_findings` (CB-97) is a raw INSERT
    whose contract is to return a row BYTE FOR BYTE, and `None` is one of the
    values it must be able to restore. Neither is reachable from here BY
    CONSTRUCTION: this is called only by the two MCP wrappers and the CLI
    handler, never by the domain function they all call.

    `silent=True`, so a tree git cannot answer for leaves the column NULL. An
    invented revision would be worse than the absence it replaces — the anchor
    would then resolve against a commit the card was never filed at.
    """
    return db.git_rev_parse("HEAD", silent=True, cwd=project_dir)


def register_tools(mcp, conn_factory) -> None:
    """Register finding-tracker tools on the given MCP server."""
    from codebugs import blockers

    @mcp.tool()
    def add(
        severity: str,
        category: str,
        file: str,
        description: str,
        source: str = "claude",
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        reported_at_commit: str | None = None,
        reported_at_ref: str | None = None,
        fingerprint: str | None = None,
        new_category: bool = False,
    ) -> dict[str, Any]:
        """Record a code finding observation (deduplicated by fingerprint).

        If the fingerprint matches a live finding, that finding's occurrence count
        is bumped and IT is returned (`was_new: false`, `dedup_action: "bumped"`);
        a match on a `fixed` finding reopens it as a regression (`"reopened"`); a
        match on a `wont_fix`/`not_a_bug` finding creates a new row linked via
        `meta.recurrence_of`. Without a fingerprint a conservative server-side one
        is derived from category, file and the normalized description.

        `dedup_action` has exactly four values — "created", "bumped", "reopened"
        and "recurrence_of_closed" — and the fourth is the one to read carefully:
        a recurrence of a DISMISSED twin files a NEW row and therefore reports
        `was_new: true`, so a client that tells create from match by gating on
        `was_new == false` misses the event entirely. The twin's id is always in
        `meta.recurrence_of`, and `meta.similar_to` usually carries its status
        alongside; on the paths where it does not — a caller-supplied fingerprint
        whose text does not resemble the twin, or a normalized description under
        the similarity minimum — the twin's status is NOT in this response at
        all, and costs one `get`.

        `attention` is a top-level list, ALWAYS present and often empty: an empty
        list means "evaluated, nothing serious fired", which is a different fact
        from an absent channel. Two record forms exist, and a list may carry both
        (severity first, category second; each form at most once).

        `{signal: severity_escalated, from, to}` says THIS observation raised the
        finding's stored severity. It appears only where a stored severity was
        raised — the `bumped` and `reopened` branches — and severity is monotonic
        under observation, so there is no de-escalation record to expect.

        `{signal: category_divergence, observed, stored}` says this observation
        does not NAME the matched finding's category. It appears on every branch
        that HAS a matched row: `bumped`, `reopened`, and the recurrence branch,
        where the comparison is against the DISMISSED TWIN rather than the new
        row. Both sides are normalized, so a difference of spelling
        (`Process Improvement` vs `process-improvement`) is deliberately not a
        signal while a difference of name is; a stored category that is not text
        is skipped rather than raising. A newly created finding matched nothing,
        so it emits neither record.

        `stripped_meta_keys` is a top-level list, ALWAYS present and often
        empty, following the same discipline as `attention`: `[]` means
        "checked, nothing to strip", never "no such channel". A `meta` key
        that is identity machinery OUTPUT (e.g. `occurrences`, `recurrence_of`,
        `category_minted`) is stripped from what gets stored rather than
        refused, so a caller that copies a fetched card's `meta` forward
        (`get` -> modify -> `add`) can tell, from this response alone, which
        of its own keys silently did not land. `resolver_errors` is the one
        exception: it reports a FAILURE state, not machinery input, so it is
        REFUSED outright rather than stripped, on this path exactly as on
        `update`'s `meta_update`. This is the ADD-side contract only — CSV
        import strips the same dynamic reserved union but silently, with no
        equivalent response key (a decided, separate contract, CB-51), and
        `update`'s `meta_update` still refuses every reserved key rather than
        stripping any of them.

        `stripped_description_tail` is a top-level boolean, ALWAYS present and
        usually `False`, following that same discipline: `False` means "checked,
        nothing to cut", never "no such channel". Some filing agents leak a
        slice of their own tool call into the end of `description`; when the
        text after a `</description>` marker is nothing but envelope lines, that
        tail is CUT rather than refused — the finding is real and only its tail
        is junk — and cut BEFORE the fingerprint is derived, so a tailed and a
        clean report of one defect collapse onto one card instead of two. Prose
        that merely quotes the marker is not cut. `True` means the text stored
        is not byte-for-byte the text you passed.

        Args:
            severity: critical, high, medium, or low (case-insensitive, no aliases)
            category: Finding category (e.g. tz_naive_datetime, n_plus_one, missing_validation).
                      Call `categories` first to reuse existing category names.
                      Spelling is normalized (casefold, hyphen/whitespace -> "_");
                      a category this tracker does not already hold is REFUSED
                      with a hint unless new_category=true — but only when the
                      observation would CREATE a row: a fingerprint match on a
                      known live or fixed finding is recorded regardless, with
                      the observed category kept in the occurrence ring.
            file: File path relative to project root
            description: What's wrong
            source: First reporter of this defect (default: claude). Frozen at
                    first report by design (BT-4): a re-observation keeps the
                    original; newest sources live in the occurrence ring
                    (meta.occurrences[*].source) — and an imported observation's
                    ring source can be a peer tracker's.
            tags: Optional tags for grouping
            meta: Optional JSON metadata (lines, module, rule_code, etc.).
                  Top-level meta is the row's AUTHORED state, observation-frozen
                  (BT-4): a re-observation's meta lands only as per-occurrence
                  evidence in meta.occurrences[*].meta. Promoting specific keys
                  into the row is a future allowlist by measured demand, not a
                  general merge.
            reported_at_commit: Git SHA when finding was created (auto-detected from HEAD if omitted)
            reported_at_ref: Version/tag label (e.g. "v2.1.0"), always caller-supplied.
                             Observation-frozen: a bump never updates it
                             (per-occurrence refs stay in the ring as evidence) —
                             but manually mutable BY DESIGN via
                             update(reported_at_ref=), since a release is tagged
                             after filing.
            fingerprint: Stable identity token for this defect, computed from the
                         INVARIANT part of the observation (normalized error
                         signature + failing test + anchor file — no timestamps,
                         SHAs, run ids). Same defect → same fingerprint. The
                         `auto:` prefix is reserved for server-derived values.
            new_category: Explicit permission to MINT a category the tracker does
                          not hold yet (CB-60). Minting is stamped as
                          meta.category_minted for later counting. Existing
                          categories never need this.
        """
        project_dir = _ambient_project_dir()
        if reported_at_commit is None:
            reported_at_commit = _ambient_head(project_dir)
        with conn_factory() as conn:
            return add_finding(
                conn,
                severity=severity,
                category=category,
                file=file,
                description=description,
                source=source,
                tags=tags,
                meta=meta,
                reported_at_commit=reported_at_commit,
                reported_at_ref=reported_at_ref,
                fingerprint=fingerprint,
                new_category=new_category,
                project_dir=project_dir,
            )

    @mcp.tool()
    def batch_add(
        findings: list[dict[str, Any]],
        reported_at_commit: str | None = None,
        reported_at_ref: str | None = None,
        new_category: bool = False,
    ) -> list[dict[str, Any]]:
        """Record multiple finding observations at once (deduplicated by fingerprint).

        Members are deduplicated exactly like `add` — including against each other,
        so two members sharing a fingerprint yield one insert plus one bump. One
        result per input, in input order. Unknown member keys are refused.

        Each result carries the same discriminators `add` returns, and the same
        four `dedup_action` values: "created", "bumped", "reopened" and
        "recurrence_of_closed", the last of which reports `was_new: true` because
        it files a NEW row linked to a dismissed twin via `meta.recurrence_of`.

        Each result also carries its OWN `attention` list — always present, often
        empty, never shared between members. Two record forms exist, in this
        order and at most once each:
        `{signal: severity_escalated, from, to}` on the `bumped` and `reopened`
        branches, meaning that member's observation raised the stored finding's
        severity; and `{signal: category_divergence, observed, stored}` on every
        branch with a matched row (`bumped`, `reopened`, and the recurrence
        branch, where the comparison is against the dismissed twin), meaning that
        member does not NAME the matched finding's category. Both category sides
        are normalized, so a difference of spelling is not a signal; a stored
        category that is not text is skipped rather than raising.

        Each result also carries its OWN `stripped_meta_keys` list — always
        present, often empty, never shared between members — following the
        same discipline: a `meta` key that is identity machinery OUTPUT (e.g.
        `occurrences`, `recurrence_of`, `category_minted`) is stripped from
        what gets stored rather than refused, and reported here so a caller
        forwarding a fetched card's `meta` can tell which of its own keys
        silently did not land. `resolver_errors` is refused outright instead
        (a FAILURE state, not machinery input), on this path exactly as on
        `add`.

        Each result likewise carries its OWN `stripped_description_tail`
        boolean — always present, usually `False`, meaning "checked, nothing to
        cut" rather than "no such channel". A leaked tool-call tail on that
        member's `description` (envelope lines and nothing else after a
        `</description>` marker) is CUT rather than refused, before the
        fingerprint is derived so a tailed and a clean report of one defect
        collapse onto one card; prose merely quoting the marker is left alone.
        `True` means that member's stored text is not the text you passed.

        Args:
            findings: List of finding objects, each with keys:
                severity, category, file, description, and optionally:
                source, tags, meta, reported_at_commit, reported_at_ref, fingerprint
            reported_at_commit: Default commit SHA for all findings (auto-detected if omitted).
                                Per-finding values override this.
            reported_at_ref: Default version label for all findings.
                             Per-finding values override this.
            new_category: Batch-wide permission to MINT categories the tracker
                          does not hold yet (CB-60); the first member introducing
                          a category is stamped meta.category_minted.
        """
        project_dir = _ambient_project_dir()
        default_commit = (
            reported_at_commit
            if reported_at_commit is not None
            else _ambient_head(project_dir)
        )
        enriched = []
        for f in findings:
            f = {**f}
            if "reported_at_commit" not in f:
                f["reported_at_commit"] = default_commit
            if "reported_at_ref" not in f and reported_at_ref is not None:
                f["reported_at_ref"] = reported_at_ref
            enriched.append(f)
        with conn_factory() as conn:
            return batch_add_findings(
                conn, enriched, new_category=new_category, project_dir=project_dir
            )

    @mcp.tool()
    def update(
        finding_id: str,
        status: str | None = None,
        severity: str | None = None,
        notes: str | None = None,
        append_note: str | None = None,
        tags: list[str] | None = None,
        meta_update: dict[str, Any] | None = None,
        reported_at_ref: str | None = None,
    ) -> dict[str, Any]:
        """Update a finding's status, severity, notes, tags, or metadata.

        Args:
            finding_id: The finding ID (e.g. CB-1)
            status: New status: open, in_progress, fixed, not_a_bug, wont_fix, stale.
                    Aliases accepted: done/resolved/implemented/closed → fixed,
                    wontfix → wont_fix, invalid → not_a_bug,
                    active/working/in-progress → in_progress
            severity: Re-triage the finding: critical, high, medium, or low.
                      Case-insensitive, but no aliases — unlike status, "crit" and
                      "P0" are refused.
            notes: REPLACES the notes wholesale, discarding whatever was there.
                   To add to an existing record without destroying it, use
                   append_note instead. If meta_update also carries a "notes"
                   key, the meta_update value is the one that lands — see
                   meta_update for why that is deliberate.
            append_note: Appends a newline-joined line, preserving the prior notes.
                         This is the safe way to add evidence to a long-lived card.
            tags: Replace tags list
            meta_update: Merge additional metadata keys. The three meta-writing
                         arguments compose over ONE dict, in this order: notes
                         replaces, append_note then extends that replacement,
                         and meta_update merges LAST. So passing both notes=
                         and meta_update={"notes": ...} in a single call is
                         neither an error nor a refusal — meta_update wins the
                         collision, on every key it names. That precedence is
                         deliberate rather than incidental: meta_update names
                         the storage key directly, which makes it the repair
                         path for keys no other argument can reach
                         (similar_to, category_minted, fingerprint_refusals),
                         and a stamp no argument could overwrite would be an
                         unrepairable one.
            reported_at_ref: Update version/tag label (e.g. "v2.1.0"). This is
                             the SANCTIONED manual mutation of an
                             observation-frozen column (BT-4): observations
                             never move it, this call does — a release is
                             tagged after filing.
        """
        with conn_factory() as conn:
            result = update_finding(
                conn,
                finding_id,
                status=status,
                severity=severity,
                notes=notes,
                append_note=append_note,
                tags=tags,
                meta_update=meta_update,
                reported_at_ref=reported_at_ref,
            )
            if status and entities.EntityRef.of(finding_id).is_resolved(conn):
                unblocked = blockers.get_unblocked_by(conn, finding_id, ENTITY_FINDING)
                if unblocked:
                    result["unblocked_items"] = unblocked
            return result

    @mcp.tool()
    def query(
        id: str | None = None,
        ids: list[str] | None = None,
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        file: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        meta_key: str | None = None,
        meta_value: str | None = None,
        commit: str | None = None,
        ref: str | None = None,
        fingerprint: str | None = None,
        group_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
        resolve_anchors: Annotated[bool, Field(strict=True)] = False,
    ) -> dict[str, Any]:
        """Search and filter findings. Returns structured results.

        Supports lookup by ID via `id=` (single) or `ids=` (batch). Missing IDs
        are silently absent from the result so the caller can diff. For a strict
        single-ID fetch that errors on miss, use `get` instead.

        Args:
            id: Fetch a single finding by exact ID (e.g. CB-1383)
            ids: Fetch multiple findings by ID list; missing IDs are skipped
            status: Filter by status (open, in_progress, fixed, not_a_bug, wont_fix, stale, deferred). Aliases accepted.
                    Use 'deferred' to find items with active blockers.
            severity: Filter by severity (critical, high, medium, low)
            category: Filter by exact category
            file: Filter by file path (substring match)
            source: Filter by source (claude, ruff, human, etc.). Compares the
                    FIRST reporter — the column is frozen at first report
                    (BT-4); later observations' sources live only in the
                    occurrence ring (meta.occurrences[*].source), and an
                    imported observation's ring source can be a peer tracker's.
            tag: Filter by tag (finds findings containing this tag)
            meta_key: Filter by metadata key existence. Reads the row's AUTHORED
                      top-level meta (the column), never the occurrence ring.
            meta_value: Filter by metadata value (requires meta_key; same
                        authored top-level meta as meta_key — ring meta is not
                        consulted)
            commit: Matches the first-report column OR any occurrence in the
                    ring (prefix match, hex validated) — "what was observed on
                    this commit" (CB-128). `staleness_check` uses the NEWEST
                    ring entry instead: a different question.
            ref: Filter by reported_at_ref (exact match, never prefix) — matches
                 the first-observed or manually assigned release ref (BT-4);
                 per-occurrence refs in the ring are not consulted.
            fingerprint: Filter by identity fingerprint (exact match)
            group_by: Group results by: severity, category, status, file, source,
                      tag, meta:<key> (source groups count FIRST reporters — the
                      column is frozen at first report).
                      `tag` and `meta:<key>` do NOT partition the population: a
                      card with two tags is counted under both, and a card
                      carrying no value on the axis is in no group at all. The
                      response therefore always carries `population`,
                      `ungrouped_rows`, `multi_group_rows` and
                      `nonscalar_value_rows` beside `groups`; the counts sum to
                      the population only while the last three are 0.
                      `meta:<key>` reads the AUTHORED top-level meta, like
                      `meta_key` does, and a key holding `.`, `[`, `]` or `"` is
                      REFUSED — SQLite cannot tell such a name from a path
                      (CB-167). A key that is absent, JSON null, or holds an
                      object/array is ungrouped rather than invented.
                      OVERLAPS `grouping_tags` DELIBERATELY and differently:
                      that tool is a tag census with pair co-occurrence over
                      `status`/`category` only; this is a distribution that
                      composes with every filter on this tool.
            limit: Max results (default 100). 0 means NO results, EXCEPT when
                      `id`/`ids` is given, where the id list sets a floor and a
                      smaller limit is raised to fit it. A negative value is an
                      error (it used to mean "no limit").
            offset: Pagination offset
            resolve_anchors: Resolve each result's location anchor against the
                      repository HEAD, so a card whose code moved reports its
                      new path. OFF by default because it costs 2-4 git calls
                      per ANCHORED row and this is the primary read path; the
                      cheap half — whether a card carries an anchor at all, and
                      the refusal token when capture found nothing to grab — is
                      in every result either way. `get` resolves one card by
                      default.
        """
        with conn_factory() as conn:
            # CB-196. The `deferred` branch below RETURNS without ever reaching
            # `query_findings`, so the domain guard cannot see that call: with no
            # deferred rows in the tracker, `limit=-1` used to come back at exit 0
            # echoing `"limit": -1`, while the identical call on a tracker that
            # HAS one refused. One argument, two verdicts, decided by whether the
            # tracker happens to hold a deferred row — the same shape this
            # function's own comment condemns for the `ids` widening. This is the
            # SHARED predicate called at one more site, not a second predicate.
            limit = require_row_limit("limit", limit)

            deferred_ids: list[str] | None = None
            if status == "deferred":
                # `deferred` is a PSEUDO-status: resolve it to an id restriction and
                # let the ordinary query apply every other filter, exactly as the
                # 2026-04-04 blockers design specified. This branch used to forward
                # only limit/offset, so `query(status="deferred", severity="critical")`
                # returned every deferred finding and the caller read that as the
                # critical ones — a success payload with the arguments discarded,
                # which is CB-15's failure mode reached through routing (CB-28).
                # ONE evaluation for both halves (CB-69): pairing
                # `deferred_id_restriction` with `blocker_counts_for` scanned the
                # blocker table twice and re-resolved every entity dependency.
                deferred_ids, deferred_counts = blockers.deferred_ids_and_counts(
                    conn, ENTITY_FINDING, id=id, ids=ids
                )
                if not deferred_ids:
                    # MUST NOT fall through as `ids=[]` — that reads as "no filter".
                    #
                    # The SHAPE has to follow `group_by`, and it did not until
                    # adversarial review: this arm returned an ungrouped empty
                    # page even when the caller asked for groups, so the four
                    # disclosure keys this tool's own description promises on
                    # every grouped response were simply absent. A promise with
                    # the word "always" in it has to survive its own short
                    # circuits (CB-62). The counts are all zero here honestly —
                    # the population really is empty — which is a different
                    # statement from "no such channel", exactly as `attention: []`
                    # is a different statement from a missing `attention`.
                    if group_by:
                        _resolve_group_axis(group_by)  # refuse a bad axis first
                        return {
                            "grouped": True,
                            "group_by": group_by,
                            "groups": [],
                            "population": 0,
                            "ungrouped_rows": 0,
                            "multi_group_rows": 0,
                            "nonscalar_value_rows": 0,
                        }
                    return {
                        "grouped": False,
                        "total": 0,
                        "limit": limit,
                        "offset": offset,
                        "findings": [],
                    }
                id, ids, status = None, deferred_ids, None
            result = query_findings(
                conn,
                id=id,
                ids=ids,
                status=status,
                severity=severity,
                category=category,
                file=file,
                source=source,
                tag=tag,
                meta_key=meta_key,
                meta_value=meta_value,
                commit=commit,
                ref=ref,
                fingerprint=fingerprint,
                group_by=group_by,
                limit=limit,
                offset=offset,
                resolve_anchors=resolve_anchors,
            )
            if deferred_ids is not None and not result.get("grouped"):
                for row in result["findings"]:
                    row["blocker_count"] = deferred_counts.get(row["id"], 0)
            return result

    @mcp.tool()
    def recent(
        since: str,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Findings TOUCHED at or after a date — the one call for "what closed since".

        WHAT THIS MEASURES: `updated_at`, the time of the LAST WRITE to the row,
        and not the moment the finding was closed. There is no close timestamp
        anywhere in the schema. A status change moves `updated_at`, and so do a
        re-tag, a meta patch, a severity re-triage, an `append_note`, and a
        DEDUPLICATED OBSERVATION — a repeat report bumps the occurrence count and
        stamps `updated_at` while the status stays exactly where it was.

        So `recent(since=..., status="fixed")` means "cards that are fixed NOW and
        were touched since that date", NOT "cards closed since that date". The
        error is ONE-SIDED: false positives are possible, misses are not, because
        closing a card always writes `updated_at`.

        Rows come back newest touch first, with `rowid` breaking the whole-second
        ties `updated_at` produces, so a paged walk is stable.

        Args:
            since: Lower bound on `updated_at`, INCLUSIVE. 'YYYY-MM-DD' or
                   'YYYY-MM-DDTHH:MM:SSZ'. REQUIRED — an unparseable value is
                   refused rather than defaulted, because a silently widened
                   window answers a question nobody asked.
            status: Filter by status (open, in_progress, fixed, not_a_bug,
                    wont_fix, stale). Aliases accepted. Omit for every status.
                    The `deferred` pseudo-status of `query` is NOT accepted here
                    and is refused rather than ignored — use `query` for it.
            limit: Max results (default 100)
            offset: Pagination offset
        """
        with conn_factory() as conn:
            return recent_findings(
                conn, since=since, status=status, limit=limit, offset=offset
            )

    @mcp.tool()
    def get(
        finding_id: str,
        resolve_anchors: Annotated[bool, Field(strict=True)] = True,
    ) -> dict[str, Any]:
        """Fetch a single finding by ID with full body (description, severity,
        status, tags, meta, timestamps, commit refs).

        The result carries an `anchor` summary saying where this card's code is
        NOW: `state` tells a card with no anchor apart from one whose anchor was
        retracted by hand and from one where capture looked and had nothing to
        grab, and `loc_status`/`moved_file`/`path` report the resolution against
        HEAD when it ran.

        Raises a not-found error if the ID does not exist. For lenient batch
        lookup that silently drops missing IDs, use `query(ids=[...])`.

        Args:
            finding_id: The finding ID (e.g. CB-1383)
            resolve_anchors: Resolve the anchor against the repository (default
                    ON — the cost is bounded by one card). Pass False for a read
                    that must not spawn a process: no git available, no
                    repository, or a caller that only wants to know whether an
                    anchor exists at all.
        """
        with conn_factory() as conn:
            return get_finding(conn, finding_id, resolve_anchors=resolve_anchors)

    @mcp.tool()
    def stats(group_by: str = "severity") -> dict[str, Any]:
        """Aggregated cross-tabulated counts.

        Args:
            group_by: Group by: severity, category, status, file, source, tag,
                      meta:<key> (source buckets count FIRST reporters — the
                      column is frozen at first report, BT-4).
                      With `tag` or `meta:<key>` the rows do NOT partition: a
                      card with two tags is cross-tabulated under both of them,
                      so the totals exceed the number of findings. `population`,
                      `ungrouped_rows`, `multi_group_rows` and
                      `nonscalar_value_rows` ride beside `groups` on every axis
                      and are what make that readable. A meta key holding `.`,
                      `[`, `]` or `"` is refused (CB-167).
        """
        with conn_factory() as conn:
            return get_stats(conn, group_by=group_by)

    @mcp.tool()
    def summary() -> dict[str, Any]:
        """Dashboard overview — open/resolved counts, severity breakdown,
        top categories, hottest files, deferred counts. Start here for orientation."""
        with conn_factory() as conn:
            result = get_summary(conn)
            result.update(blockers.get_deferred_counts(conn, ENTITY_FINDING))
            return result

    @mcp.tool()
    def categories() -> list[dict[str, Any]]:
        """List all existing categories with counts.
        Call this before adding findings to reuse consistent category names."""
        with conn_factory() as conn:
            return get_categories(conn)

    @mcp.tool()
    def categories_normalize(
        fold_map: dict | str | None = None,
        apply: Annotated[bool, Field(strict=True)] = False,
        new_category: Annotated[bool, Field(strict=True)] = False,
    ) -> dict[str, Any]:
        """Rename stored categories and re-key their fingerprints (CB-61).

        TWO MODES, and the second is a working mode rather than a side effect.
        Without `fold_map` this folds every stored SPELLING to its canonical
        form, for rows filed before write-time canonicalization existed, whose
        stored `auto:v1` fingerprint still carries the old spelling and therefore
        forks identity when the same defect is reported again. With a `fold_map`
        it MERGES CATEGORY NAMES: any stored name may be renamed to any canonical
        target, and the two need not be spellings of each other. That second mode
        is how a tracker's rare category names are collapsed into its common ones.

        DRY RUN BY DEFAULT — without `apply=true` nothing is written and the
        report tells you exactly what would change. A key that matches no stored
        category is accepted and renames nothing, and `unmatched_fold_keys` names
        every one of them, so a typo on the left-hand side is stated rather than
        left to be spotted as a pair missing from the `from -> to` table. A typo
        in a TARGET is refused instead — see `new_category`. Matching is exact
        against the stored spelling, so a canonical key does not reach a stored
        `Process Improvement` and is reported unmatched. Take an `export-csv`
        backup before applying; `restore-csv` puts findings back verbatim into an
        EMPTY tracker, but milestone items and audit history are not in a CSV
        export and are not restored.

        Each renamed row's fingerprint is handled by kind: a `NULL` or a
        caller-SUPPLIED fingerprint is left byte-identical, an `auto:v1` one is
        re-derived with the new category after its stored inputs are verified to
        reproduce the stored hash. A row that fails that round trip is skipped
        WHOLE and reported under `unverifiable`. The occurrence ring
        (`meta.occurrences`) is never rewritten.

        If the fold would put two LIVE findings on one fingerprint, the run
        writes NOTHING and reports the colliding pair by id — merging two cards
        is a decision, not a migration step. Any OTHER identity merge — two closed
        cards, or a closed card and a live one — is legal, so it is reported under
        `merged_identities` rather than refused: the run proceeds, and you are told
        which cards this fold fused. Both `unmatched_fold_keys` and
        `merged_identities` are always present; `[]` means "checked, none".

        Args:
            fold_map: Optional {stored category name: canonical target name} map,
                      as an object or a JSON string. The key is matched exactly
                      against the stored value and may be any name the table
                      holds. Every target must already be canonical (casefold,
                      hyphen/whitespace -> "_"). Omit it to fold every stored
                      spelling to its own normalized form; `{}` is an explicit
                      no-op.
            apply: Write the changes. Default false (report only).
            new_category: Permission to fold INTO a category this tracker does
                          not hold yet, for the whole map at once. Without it
                          such a target is refused, naming the nearest existing
                          categories — an operation meant to REDUCE the number of
                          category names must not invent one by typo. The refusal
                          stops at the first bad target.
        """
        parsed = json.loads(fold_map) if isinstance(fold_map, str) else fold_map
        with conn_factory() as conn:
            return normalize_categories(
                conn, fold_map=parsed, apply=apply, new_category=new_category
            )


def register_cli(sub, commands) -> None:
    """Register findings CLI subcommands."""
    import argparse
    from codebugs.fmt import format_table
    from codebugs.fsio import atomic_write, diagnostic_stream

    def _cmd_add(args: argparse.Namespace) -> None:
        # CB-129. Two spellings can name one `meta` field: a dedicated flag and a key
        # inside `--meta`. `meta.update(json.loads(args.meta))` over a flag-seeded dict
        # let the JSON win SILENTLY, so `-l "10-20" --meta '{"lines": [10, 20]}'` stored
        # only the list and reported success — an explicitly typed argument discarded by
        # a success-shaped call, the CB-15 class reached through composition rather than
        # through validation. There is no "honour both" path (one key, one value), so
        # CB-28's rule leaves refusal as the only honest answer; applying the flag LAST
        # was rejected because it would silently invert the stored type for every caller
        # that already passes both. Equal values are not a conflict and still pass.
        #
        # This runs BEFORE db.connect(): a refusal must cost no partial work and no open
        # connection (CB-82). It exits directly rather than raising ValueError, so it
        # cannot disturb the json.JSONDecodeError-before-ValueError arm ordering below.
        # CB-133. A CLI flag is a WRITE path, so `None` is the ONLY "not supplied"
        # (CB-82); the query-side rule that `""` ALSO means absent (CB-25) is
        # deliberately the opposite one and must not be borrowed here. Under the
        # previous `if value:` an explicitly typed `-l ""` landed nowhere, counted as
        # no conflict, and reported success — CB-129's own success-shaped discard,
        # surviving on the empty string. Refusing rather than storing `""` is what
        # `bench._require_text` already does for exactly this state: a stored value
        # that is absent means *invent one*, and nobody meant an empty line range.
        flag_meta = {}
        for dest, key, spelling in _ADD_META_FLAGS:
            value = getattr(args, dest, None)
            if value is None:
                continue
            if not value:
                print(
                    f"codebugs add: {spelling} was given an empty value, which is not "
                    f"a {key} anyone could have meant. An empty string is not the same "
                    f"as leaving the flag out.\n"
                    f"Omit {spelling}, or give it a value.",
                    file=sys.stderr,
                )
                sys.exit(1)
            flag_meta[key] = value

        # CB-132. `json.loads` had NO arm over it, so `--meta 'not json'` left the CLI
        # as a raw `json.JSONDecodeError` traceback and `--meta '[1,2]'` as
        # `TypeError: cannot convert dictionary update sequence element #0` out of the
        # `meta.update` below. A handler that catches NOTHING breaks the Error-handling
        # rule exactly as surely as one catching in the wrong order (the CB-19/CB-79
        # lesson). `--meta ""` is CB-133's shape on the same argument: a SUPPLIED empty
        # document, not an absent one — the split `bench.py` draws between
        # `csv_data=None` and `csv_data=""`.
        #
        # The arm wraps THIS PARSE ALONE, deliberately. The `json.JSONDecodeError`
        # raised further down by `add_finding` is `_bump_row`'s pre-write parse of a
        # MATCHED row's stored meta — corruption, not bad input — and it must keep
        # escaping loudly; one broad `try` would print a tidy usage error for a state
        # no caller typed, which is the CB-15/CB-16 lie. Both arms exit directly
        # rather than raising, so neither can disturb the
        # json.JSONDecodeError-before-ValueError ordering contract below.
        json_meta: Any = {}
        if args.meta is not None:
            if not args.meta:
                print(
                    "codebugs add: --meta was given an empty value. Omit it, or pass a "
                    "JSON object.",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                json_meta = json.loads(args.meta)
            except json.JSONDecodeError as e:
                print(
                    f"codebugs add: --meta must be a JSON object, and this is not valid "
                    f"JSON: {args.meta!r}\n  {e}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not isinstance(json_meta, dict):
                # Well-formed JSON of the wrong SHAPE — the case the decode arm cannot
                # see, and the one that used to die inside `dict.update`. Refusing also
                # keeps the conflict check below honest: testing `key in json_meta` on a
                # str would be a SUBSTRING test, a wrong answer rather than an error.
                print(
                    f"codebugs add: --meta must be a JSON object (a mapping), not "
                    f"{type(json_meta).__name__}: {args.meta!r}",
                    file=sys.stderr,
                )
                sys.exit(1)

        for _dest, key, spelling in _ADD_META_FLAGS:
            if key in flag_meta and key in json_meta and json_meta[key] != flag_meta[key]:
                print(
                    f"codebugs add: {spelling} and the {key!r} key in --meta name the "
                    f"same field with different values, and only one of them can be "
                    f"stored.\n"
                    f"  {spelling} gives {flag_meta[key]!r}\n"
                    f"  --meta gives {json_meta[key]!r}"
                    f"  <- this one would have won, silently discarding {spelling}.\n"
                    f"Pass only one of the two spellings, or make them equal.",
                    file=sys.stderr,
                )
                sys.exit(1)

        meta = dict(flag_meta)
        meta.update(json_meta)

        conn = db.connect()
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

        # CB-144. ONE resolution of the tree, feeding both the root and the
        # revision, because BT-7 Р3 requires those two to come from a single
        # source — a card anchored against a revision from one tree and a path
        # from another is anchored against nothing.
        project_dir = _ambient_project_dir()

        from codebugs.cli import domain_errors

        try:
            # json.JSONDecodeError re-raises rather than printing as bad input
            # (domain_errors, cli.py): this is _bump_row's PRE-write parse of
            # the matched row's stored meta — corruption, not bad input. (The
            # post-commit twin — stored tags failing AFTER the bump landed —
            # arrives as PostCommitCorruptionError, which nothing here catches
            # and which therefore also propagates loudly.)
            with domain_errors():
                result = add_finding(
                    conn,
                    severity=args.severity,
                    category=args.category,
                    file=args.file,
                    description=args.description,
                    source=args.source or "human",
                    tags=tags,
                    meta=meta or None,
                    fingerprint=args.fingerprint,
                    new_category=args.new_category,
                    # `db.connect()` above walks up from the cwd, so the cwd is
                    # the tree this invocation is about — stated rather than
                    # reached for.
                    project_dir=project_dir,
                    # CB-144. The CLI is a FILING SURFACE like the two MCP
                    # wrappers, and it captured nothing, so every CLI-filed
                    # card was structurally unanchorable. Unconditional because
                    # there is no argument to omit: `--commit` is NOT added
                    # here on purpose — that is CB-6's already-named surface
                    # gap, and adding it would mix two axes and disturb the
                    # update-parity gate.
                    reported_at_commit=_ambient_head(project_dir),
                )
        finally:
            conn.close()
        # A bump is not a creation — saying "Added" for a non-write is the CB-15
        # class of success-shaped lie, on the human surface.
        action = result["dedup_action"]
        if action == "bumped":
            print(f"Bumped: {result['id']} (occurrence {result['occurrence_count']})")
        elif action == "reopened":
            print(f"Reopened as regression: {result['id']} (occurrence {result['occurrence_count']})")
        elif action == "recurrence_of_closed":
            print(f"Added: {result['id']} (recurrence of closed {result['meta']['recurrence_of']})")
        else:
            print(f"Added: {result['id']}")
        # CB-56: the CLI prints fixed lines rather than serializing the whole
        # response (same audience split as `attention`, CLAUDE.md's BT-5 entry),
        # but a silent strip is forbidden regardless of surface — the visibility
        # requirement is about the ACT of stripping, not about which caller asked.
        stripped = result.get("stripped_meta_keys") or []
        if stripped:
            print(
                f"Note: meta keys {stripped} were stripped before filing — they "
                f"are identity-machinery output, not caller input.",
                file=sys.stderr,
            )
        # CB-90, and it is the SAME rule as the note above rather than a second
        # one: the description reaching the database is not the description that
        # was typed, so the human surface has to say so or the strip is silent.
        # This costs the caller MORE than the meta note does — meta keys are
        # machinery output the caller never authored, whereas a cut tail takes
        # away bytes the caller DID type — so if either note were optional it
        # would be the other one. Adversarial review of this unit found it
        # missing while the comment above already stated the rule verbatim.
        if result.get("stripped_description_tail"):
            print(
                "Note: a trailing tool-call fragment was cut from the description "
                "before filing — the stored text ends at the last line of your prose.",
                file=sys.stderr,
            )

    def _cmd_update(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        try:
            # json.JSONDecodeError re-raises rather than printing as bad input
            # (domain_errors, cli.py): this is a corrupted stored row, not bad
            # user input, and the write has ALREADY been committed by the time
            # result serialization raises. Reporting it as a clean input error
            # would exit 1 on a successful mutation — a failure-shaped signal
            # for a write that landed.
            with domain_errors():
                result = update_finding(
                    conn,
                    args.id,
                    status=args.status,
                    severity=args.severity,
                    notes=args.notes,
                    append_note=args.append_note,
                )
                print(
                    f"Updated: {result['id']} "
                    f"(status={result['status']}, severity={result['severity']})"
                )
        finally:
            conn.close()

    def _anchor_cell(summary: Any) -> str:
        """One table cell for an anchor summary. Never raises, never lies.

        A RESOLVED row shows what the resolution said; anything else shows the
        stored state, because "moved_file" and "this card has no anchor" are
        answers to different questions and a column that printed an empty
        string for both would recreate the conflation the summary exists to
        end.
        """
        if not isinstance(summary, dict):
            return ""
        if summary.get("resolved"):
            return str(summary.get("loc_status") or "")
        return str(summary.get("state") or "")

    def _cmd_query(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        ids = [s.strip() for s in args.id.split(",") if s.strip()] if args.id else None
        try:
            # json.JSONDecodeError re-raises (domain_errors, cli.py) rather
            # than printing as bad input: a corrupt stored row is not bad user
            # input, and flattening it into a one-line "bad input" message
            # would hide a data-integrity problem behind a usage error.
            # `--severity`/`--status` are free text; an unknown value names
            # itself and exits 1 instead of printing a traceback (CB-19).
            with domain_errors():
                result = query_findings(
                    conn,
                    ids=ids,
                    status=args.status,
                    severity=args.severity,
                    category=args.category,
                    file=args.file,
                    source=args.source,
                    fingerprint=args.fingerprint,
                    group_by=args.group_by,
                    # CB-124: `or 100` silently turned `--limit 0` into 100
                    # rows, the truthiness shape CB-25/CB-82 condemn.
                    # `argparse` gives None only when the flag is absent.
                    limit=args.limit if args.limit is not None else 100,
                    resolve_anchors=args.resolve_anchors,
                    project_dir=args.repo,
                )
        finally:
            conn.close()

        if result.get("grouped"):
            data = [
                {"group": _group_cell(r["group_key"]), "count": str(r["count"])}
                for r in result["groups"]
            ]
            print(format_table(data, ["group", "count"]))
            print(_group_disclosure(result))
        else:
            findings = result["findings"]
            if not findings:
                print("(no findings match)")
                return
            data = [
                {
                    "id": f["id"],
                    "sev": f["severity"],
                    "category": f["category"],
                    "file": f["file"],
                    "status": f["status"],
                    "description": f["description"],
                }
                for f in findings
            ]
            columns = ["id", "sev", "category", "file", "status", "description"]
            if args.resolve_anchors:
                # The ONE place this module names a read enricher's key, and it
                # is PRESENTATION rather than data flow: the domain path hands
                # rows to `db.run_read_enrichers` and never learns what any
                # extension called its summary. A table needs a column header,
                # and a flag whose effect is invisible would be worse than the
                # coupling. `.get` throughout, so an unregistered extension
                # prints a blank cell instead of raising.
                for row_data, f in zip(data, findings):
                    row_data["loc"] = _anchor_cell(f.get("anchor"))
                columns.insert(4, "loc")
            print(
                format_table(
                    data,
                    columns,
                    # The narrower widths are the COST of the extra column and
                    # must not be paid by a caller who did not ask for it —
                    # review caught this applying to every `codebugs query`.
                    max_widths=(
                        {"description": 50, "file": 40, "category": 20}
                        if args.resolve_anchors
                        else {"description": 60, "file": 40, "category": 25}
                    ),
                )
            )
            print(f"\n{result['total']} finding(s) total.")

    def _cmd_recent(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        try:
            # json.JSONDecodeError re-raises (domain_errors, cli.py): this one
            # reaches here through `db.row_to_dict` on a row with corrupt
            # stored meta/tags — a data-integrity problem, not bad user input,
            # and flattening it into a tidy one-line usage error would hide
            # it. A `--since` that is not a date, or an unknown `--status`,
            # names itself and exits 1 instead of printing a traceback.
            with domain_errors():
                result = recent_findings(
                    conn,
                    since=args.since,
                    status=args.status,
                    # CB-124: `or 100` silently turned `--limit 0` into 100
                    # rows. `argparse` gives None only when the flag is absent.
                    limit=args.limit if args.limit is not None else 100,
                )
        finally:
            conn.close()

        rows = result["findings"]
        if not rows:
            print(f"(no findings touched since {result['since']})")
            return
        data = [
            {
                "id": f["id"],
                "sev": f["severity"],
                "status": f["status"],
                "touched": f["updated_at"],
                "category": f["category"],
                "description": f["description"],
            }
            for f in rows
        ]
        print(
            format_table(
                data,
                ["id", "sev", "status", "touched", "category", "description"],
                max_widths={"description": 50, "category": 22},
            )
        )
        print(f"\n{result['total']} finding(s) touched since {result['since']}.")

    def _cmd_get(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        try:
            with domain_errors():
                result = get_finding(
                    conn,
                    args.id,
                    resolve_anchors=args.resolve_anchors,
                    project_dir=args.repo,
                )
        finally:
            conn.close()
        print(json.dumps(result, indent=2, sort_keys=True))

    def _cmd_stats(args: argparse.Namespace) -> None:
        from codebugs.cli import domain_errors

        conn = db.connect()
        try:
            # CB-170. This handler used to call `get_stats` bare and close the
            # connection on the line after, outside any `finally` — so an
            # unknown `--by` printed a raw traceback AND leaked the connection,
            # while the sibling `_cmd_query` in this same file had closed both
            # halves back at CB-19. Fixed here rather than left for the sweep
            # because this change is what makes an unknown axis MORE likely: the
            # vocabulary just grew a `meta:<key>` form that refuses on a key
            # a caller can perfectly reasonably type.
            #
            # Ordering is `domain_errors()`': json.JSONDecodeError re-raises
            # first, and only then does bad input print one line and exit 1.
            with domain_errors():
                result = get_stats(conn, group_by=args.by or "severity")
        finally:
            conn.close()

        groups = result["groups"]
        if not groups:
            # "(no findings)" was a FALSE statement about the corpus the moment a
            # non-partitioning axis existed, and the disclosure line was exactly
            # what it skipped past (adversarial review). Measured on the live
            # tracker: `stats --by meta:loc` printed "(no findings)" at exit 0
            # over 172 cards, 169 of which carry that key — as a container, so
            # none of them could be grouped by it. The sibling `query` verb told
            # the truth on the same question, which is this unit's own subject:
            # one decision, two hand-written readers, drifted.
            print("(no groups)")
            print(_group_disclosure(result))
            return

        header = f"{'':30s} {'critical':>8s} {'high':>8s} {'medium':>8s} {'low':>8s} {'total':>8s}"
        print(header)
        print("-" * len(header))
        totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        # Keyed by TEXT. Insurance rather than a fix — see `_group_cell`, which
        # explains why no test can discriminate it today and why it stays.
        for grp in sorted(groups, key=_group_cell):
            d = groups[grp]
            print(
                f"{_group_cell(grp):30s} {d['critical']:>8d} {d['high']:>8d} {d['medium']:>8d} {d['low']:>8d} {d['total']:>8d}"
            )
            for k in totals:
                totals[k] += d[k]
        print("-" * len(header))
        print(
            f"{'TOTAL':30s} {totals['critical']:>8d} {totals['high']:>8d} {totals['medium']:>8d} {totals['low']:>8d} {totals['total']:>8d}"
        )
        print(_group_disclosure(result))

    def _cmd_summary(args: argparse.Namespace) -> None:
        conn = db.connect()
        s = get_summary(conn)
        conn.close()

        print("Codebugs Summary")
        print("=" * 50)
        print(f"Findings:  {s['open']} open / {s['resolved']} resolved / {s['total']} total")
        print()
        print("Open by severity:")
        for sev in ("critical", "high", "medium", "low"):
            c = s["open_by_severity"].get(sev, 0)
            bar = "#" * min(c, 40)
            print(f"  {sev:10s}  {c:>4d}  {bar}")
        if s["top_categories"]:
            print()
            print("Top categories:")
            for cat in s["top_categories"]:
                print(f"  {cat['category']:30s}  {cat['count']:>4d}")
        if s["hottest_files"]:
            print()
            print("Hottest files:")
            for f in s["hottest_files"]:
                print(f"  {f['file']:50s}  {f['critical_high']} crit/high, {f['open']} open")

    def _cmd_categories(args: argparse.Namespace) -> None:
        conn = db.connect()
        cats = get_categories(conn)
        conn.close()

        if not cats:
            print("(no categories yet)")
            return
        data = [
            {
                "category": c["category"],
                "total": str(c["total"]),
                "open": str(c["open_count"]),
                "fixed": str(c["fixed_count"]),
            }
            for c in cats
        ]
        print(format_table(data, ["category", "total", "open", "fixed"]))

    def _print_fold_report(report: dict[str, Any]) -> None:
        counts = report["counts"]
        if report["applied"]:
            mode = "applied"
        elif report["stopped"]:
            mode = "STOPPED — nothing written"
        else:
            mode = "dry run — nothing written"
        print(f"Category fold ({mode})")
        print(f"  rows scanned:        {report['rows_scanned']}")
        print(f"  rows to rename:      {len(report['renames'])}")
        print(f"    re-fingerprinted:  {counts['refingerprinted']}")
        print(
            f"    category only:     {counts['category_only']}"
            f"  (supplied {counts['supplied_untouched']}, null {counts['null_untouched']})"
        )
        print(f"  unverifiable:        {counts['unverifiable']}")
        print(f"  non-string category: {counts['skipped_non_string']}")

        if report["fold_map"]:
            per_pair: dict[str, int] = {}
            for rename in report["renames"]:
                per_pair[rename["from"]] = per_pair.get(rename["from"], 0) + 1
            rows = [
                {"from": variant, "to": target, "rows": str(per_pair.get(variant, 0))}
                for variant, target in sorted(report["fold_map"].items())
            ]
            print()
            print(format_table(rows, ["from", "to", "rows"]))

        # Both CB-207/CB-209 sections print ONLY when non-empty, exactly like the
        # two blocks below them. The empty list keeps its meaning in `--json`,
        # where a machine reader needs "checked, none"; on a terminal the same
        # line would be noise on every healthy run.
        if report["unmatched_fold_keys"]:
            print()
            print(
                f"!! {len(report['unmatched_fold_keys'])} fold_map keys matched NO stored "
                f"category — they rename nothing. The match is exact against the stored "
                f"spelling, so a pre-canonical name must be typed as it is stored:"
            )
            for key in report["unmatched_fold_keys"]:
                print(f"   {key!r}")

        if report["merged_identities"]:
            merged_rows = report["merged_identities"]
            print()
            if report["stopped"]:
                # A STOPPED run writes nothing, so this section describes something
                # that does not happen. The unconditional wording put two sentences
                # in one report asserting opposite things about whether the run
                # proceeds — found by adversarial review on a fixture carrying a
                # live collision and a terminal merge at once.
                print(
                    f"!! {len(merged_rows)} more fingerprints WOULD be shared by cards this "
                    f"fold brings together — but the collisions below stop the run, so none "
                    f"of this happens either. It is what the map would do once they are "
                    f"resolved:"
                )
            else:
                # The consequence differs by the members' STATUSES, and the first
                # draft of this line stated the closed-twins one for both. That is
                # false on the shape this feature most often meets: closing the
                # CB-113(a) fork puts a closed card beside a LIVE one, and there
                # the live card simply goes on taking the observations — measured,
                # `dedup_action: "bumped"`. Nothing is revived and nothing is lost.
                print(
                    f"!! {len(merged_rows)} fingerprints would be SHARED by cards this fold "
                    f"brings together. This is allowed and the run is not refused. Where BOTH "
                    f"cards are closed, a later report of the defect can revive only ONE of "
                    f"them and the other stays behind; where one is still open, that open card "
                    f"goes on taking the observations:"
                )
            # CAPPED, unlike the blocks around it, because this list is O(the merges
            # the fold performs) rather than O(a rare accident): on a corpus forked
            # by CB-113(a) at scale, adversarial review measured the human output
            # growing from 11 lines to 4513, past the pipe buffer, so that
            # `… | head` began exiting 141. The count is always stated, so nothing
            # is hidden, and the full list is one `--json` away. `unmatched_fold_keys`
            # needs no cap: it is bounded by the map the operator typed.
            for merged in merged_rows[:_FOLD_MERGED_GROUPS_SHOWN]:
                print(f"   {merged['fingerprint']}")
                for member in merged["rows"]:
                    target = "(not renamed)" if member["to"] is None else repr(member["to"])
                    print(
                        f"     {member['id']} [{member['status']}]  "
                        f"{member['from']!r} -> {target}"
                    )
            if len(merged_rows) > _FOLD_MERGED_GROUPS_SHOWN:
                print(
                    f"   … and {len(merged_rows) - _FOLD_MERGED_GROUPS_SHOWN} more not shown; "
                    f"--json carries the whole list."
                )

        if report["unverifiable"]:
            print()
            print(
                f"!! {len(report['unverifiable'])} rows SKIPPED WHOLE (category left "
                f"unchanged): the stored inputs do not reproduce the stored auto:v1 "
                f"fingerprint, so re-keying them would be a fabrication:"
            )
            for entry in report["unverifiable"]:
                print(f"   {entry['id']}  {entry['category']!r} -> would be {entry['to']!r}")

        if report["collisions"]:
            print()
            print(
                f"!! {len(report['collisions'])} fingerprint collisions after the fold — "
                f"NOTHING was written. Two live findings would share one identity; "
                f"merging them is a decision, not a migration step:"
            )
            for collision in report["collisions"]:
                print(f"   {collision['fingerprint']}")
                for member in collision["rows"]:
                    target = "(not renamed)" if member["to"] is None else repr(member["to"])
                    print(
                        f"     {member['id']} [{member['status']}]  "
                        f"{member['from']!r} -> {target}"
                    )

    def _cmd_categories_normalize(args: argparse.Namespace) -> None:
        # The flag is decoded BEFORE the connection opens: bad JSON on the command
        # line is plain bad input, and refusing it costs no partial work.
        if args.fold_map is None:
            fold_map = None
        else:
            try:
                fold_map = json.loads(args.fold_map)
            except json.JSONDecodeError as e:
                print(f"codebugs: --fold-map is not valid JSON: {e}", file=sys.stderr)
                sys.exit(1)

        from codebugs.cli import domain_errors

        conn = db.connect()
        try:
            # json.JSONDecodeError re-raises (domain_errors, cli.py): a
            # corrupted stored row is not bad user input.
            with domain_errors():
                report = normalize_categories(
                    conn,
                    fold_map=fold_map,
                    apply=args.apply,
                    new_category=args.new_category,
                )
        finally:
            conn.close()

        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_fold_report(report)
        # A COLLISION exits 0 on a dry run — it is REPORTED, not refused, because a
        # collision is a fact about the DATA and the report is the channel for it.
        # This says nothing about the run's other outcomes: a bad target (CB-223) and
        # a non-canonical one are facts about the INPUT, and both refuse by exception
        # on the dry run just as they do under `--apply`, so a dry run does not
        # universally exit 0. (Note "dry run vs --apply" here is a different axis from
        # the "two modes" the docstrings name, which is derived-vs-explicit-map.)
        # `--apply` that stopped wrote nothing, so it must not look like success.
        if args.apply and report["stopped"]:
            sys.exit(1)

    def _cmd_import_csv(args: argparse.Namespace) -> None:
        """Read the file, hand rows to the domain, print. No import semantics here.

        Every rule that decides what an import MEANS lives in
        `findings.import_findings` (CB-51) — including the reserved-meta strip and
        the `auto:` fingerprint strip, which an earlier draft of this handler kept
        and whose docstring then claimed otherwise. The domain takes rows in the
        shape `csv.DictReader` yields, so this function decodes nothing.
        """
        conn = db.connect()
        # Reading the WHOLE file before the transaction opens is what makes
        # CB-77's all-or-nothing contract honest: a read failure now happens with
        # nothing written, rather than interleaved with per-row commits. It costs
        # memory proportional to the file — the accepted trade for a
        # tracker-sized CSV. The open is guarded alone (CB-71).
        try:
            with open(args.file, newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as e:
            print(f"codebugs: {e}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        try:
            report = import_findings(conn, rows)
        except (ValueError, sqlite3.Error) as e:
            # ONE arm, deliberately. The repo's JSONDecodeError-before-ValueError
            # ordering (`_cmd_update`) exists because those arms BEHAVE
            # differently — re-raise versus a tidy line. Here they do not: the
            # import is one transaction, so every failure means nothing landed
            # and every failure says exactly that. Printing a count on this path
            # would be a success-shaped signal for a rollback (CB-15/CB-16).
            print(f"codebugs: import aborted, no rows imported: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

        for err in report.errors:
            print(f"Error importing {err.label}: {err.message}", file=sys.stderr)

        parts = []
        if report.skipped_present:
            parts.append(f"{report.skipped_present} already present, skipped")
        if report.skipped_decided:
            parts.append(
                f"{report.skipped_decided} skipped, would have reopened a decided finding"
            )
        if report.merged:
            parts.append(f"{report.merged} merged into existing findings by fingerprint")
        if report.errors:
            parts.append(f"{len(report.errors)} failed (see stderr)")
        suffix = f" ({'; '.join(parts)})" if parts else ""
        print(f"Imported {report.imported} findings.{suffix}")
        if report.errors:
            sys.exit(1)

    def _cmd_restore_csv(args: argparse.Namespace) -> None:
        """Write an export back verbatim. Refuses rather than merges."""
        conn = db.connect()
        # Read before the transaction opens, same reason as import: an all-or-nothing
        # contract is only honest if a read failure happens with nothing written.
        try:
            with open(args.file, newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as e:
            print(f"codebugs: {e}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        try:
            report = restore_findings(conn, rows)
        except (ValueError, sqlite3.Error) as e:
            # One arm: the restore is one transaction, so every failure means the same
            # thing — nothing landed — and the message says so instead of printing a
            # count over a rollback (CB-15/CB-16).
            print(f"codebugs: restore aborted, nothing written: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

        print(f"Restored {report.restored} findings.")
        if report.restored:
            # Said out loud rather than left for a reader to discover: milestone items
            # and their audit rows are a projection built by post-add hooks, they are
            # not in the CSV, and firing those hooks would fabricate a triage history
            # that never happened. So they are absent, not wrong.
            print(
                "Note: milestone items and audit history are not part of a CSV export "
                "and were not restored.",
                file=sys.stderr,
            )

    def _cmd_export_csv(args: argparse.Namespace) -> None:
        conn = db.connect()
        # ONE query, not OFFSET pagination, and not the old `limit=100000` cap.
        #
        # The cap silently truncated a larger tracker on the one path where losing rows
        # costs most — an export is the input to a restore, so a quiet ceiling there is a
        # quiet backup failure. But paging it was WORSE, and review caught that before it
        # shipped: `query_findings` orders by `{severity rank}, created_at DESC` with no
        # unique tiebreaker, and `created_at` is whole-second, so ties are ordinary. OFFSET
        # paging over a non-total order is not a stable partition of the table — a tie
        # group straddling a page boundary can be emitted twice or skipped. Duplicated or
        # missing rows in a backup is strictly worse than a documented cap.
        #
        # Asking for the count first and then fetching that many keeps the single-query
        # stability AND removes the ceiling. The window between the two reads only ever
        # makes the second short (a concurrent delete) or drops a newer row — never
        # corrupts one — and an export is a snapshot, not a lock.
        total = query_findings(conn, limit=1)["total"]
        result = query_findings(conn, limit=max(total, 1))
        conn.close()

        output = args.file or "codebugs_export.csv"
        # CB-76: `open(output, "w")` truncated the destination before the first
        # byte, so any write failure destroyed the previous export, and no arm
        # caught the OSError either. The success print below stays OUTSIDE the
        # guard — a post-write failure reported as bad input is the CB-15/CB-16
        # lie CB-71 measured live.
        #
        # CB-78 NARROWED what this arm can still see, and the cost was accepted
        # deliberately rather than discovered later. `export-csv /dev/stdout`
        # writes IN PLACE (fsio.atomic_write's held-open-inode branch), so a dead
        # reader used to raise BrokenPipeError here and produce a real diagnostic
        # — measured, `export-csv /dev/stdout | head -1` gave exit 1 and
        # `codebugs: [Errno 32] Broken pipe`. With SIG_DFL installed by `cli.run`
        # the process dies at 141 with empty stderr before that exception exists.
        # This is the ONE command where CB-78 removes a working message rather
        # than replacing a traceback, which is why it was re-ratified knowing it.
        # The arm still guards every non-pipe destination (a full disk, an
        # unwritable directory, a read-only file), which is what CB-76 filed it
        # for.
        try:
            with atomic_write(output, newline="") as (f, dest):
                # ONE declaration for the header AND the rows (CB-97). They used to be
                # two parallel positional lists, and adding the provenance columns to the
                # header alone shifted every value after `meta` — the exported
                # `fingerprint` became a timestamp, and a cross-tracker round-trip test
                # caught it. `_RESTORE_COLUMNS` is the same tuple `restore_findings`
                # writes, which is the point: an export is the input to a restore, so the
                # two cannot be allowed to disagree about the column set.
                writer = csv.DictWriter(f, fieldnames=list(_RESTORE_COLUMNS))
                writer.writeheader()
                for finding in result["findings"]:
                    row = {c: finding.get(c) for c in _RESTORE_COLUMNS}
                    # `row_to_dict` parses these back into containers; the file carries JSON.
                    row["tags"] = json.dumps(finding["tags"])
                    row["meta"] = json.dumps(finding["meta"])
                    writer.writerow(row)
        except OSError as e:
            print(f"codebugs: {e}", file=sys.stderr)
            sys.exit(1)
        # CB-143/CB-194: when `output` is an alias of this process's own
        # stdout AND/OR stderr, `atomic_write` wrote the CSV through a FRESH
        # open of the same inode (its in-place branch) — a second,
        # independent file offset on the same underlying file. Printing this
        # line to whichever inherited descriptor is ALSO that inode would
        # land at the start (or, for the true dup'd-fd case, inside the
        # middle) of the file we just wrote. `diagnostic_stream` is the
        # single place that turns `dest` into a channel choice — never
        # recompute that choice here, see its docstring — and it can answer
        # "print nothing" when both stdout and stderr are the destination.
        msg = f"Exported {len(result['findings'])} findings to {output}"
        stream = diagnostic_stream(dest)
        if stream is not None:
            print(msg, file=stream)

    p = sub.add_parser("add", help="Add a finding")
    p.add_argument("-s", "--severity", required=True, help="critical|high|medium|low")
    p.add_argument("-c", "--category", required=True, help="Finding category")
    p.add_argument("-f", "--file", required=True, help="File path")
    p.add_argument("-d", "--description", required=True, help="Description")
    p.add_argument("-l", "--lines", help="Line range (stored in meta)")
    p.add_argument("--source", help="Source (default: human)")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--meta", help="JSON metadata string")
    p.add_argument(
        "--fingerprint",
        help="Stable identity token; same defect -> same fingerprint (dedup key)",
    )
    p.add_argument(
        "--new-category",
        action="store_true",
        help="Permit minting a category this tracker does not hold yet (CB-60)",
    )

    p = sub.add_parser("update", help="Update a finding")
    p.add_argument("id", help="Finding ID")
    p.add_argument("--status", help="New status")
    p.add_argument("--severity", "-s", help="Re-triage: critical|high|medium|low")
    p.add_argument("--notes", help="Notes (REPLACES the existing notes wholesale)")
    p.add_argument("--append-note", help="Append a line, preserving the existing notes")

    p = sub.add_parser("query", help="Search findings")
    p.add_argument("--id", help="Filter by ID (single CB-N or comma-separated list)")
    p.add_argument("--status", help="Filter by status")
    p.add_argument("--severity", "-s", help="Filter by severity")
    p.add_argument("--category", "-c", help="Filter by category")
    p.add_argument("--file", "-f", help="Filter by file (substring)")
    p.add_argument("--source", help="Filter by source")
    p.add_argument("--fingerprint", help="Filter by identity fingerprint (exact)")
    p.add_argument(
        "--group-by",
        help=(
            "Group by: severity|category|status|file|source|tag|meta:<key>. "
            "tag and meta:<key> do not partition — a card with two tags is in "
            "both groups and one with none is in no group; the footer reports both"
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        help="Max results (0 for none unless --id/--ids is given; negative is an error)",
    )
    p.add_argument(
        "--resolve-anchors",
        dest="resolve_anchors",
        action="store_true",
        help=(
            "Resolve each result's location anchor against the repository, adding a 'loc' "
            "column with where the code is now. OFF by default: 2-4 git calls per ANCHORED "
            "row on a page of up to 100"
        ),
    )
    p.add_argument(
        "--anchor-repo",
        dest="repo",
        help=(
            "Repository to resolve location anchors against (default: the tracker's own "
            "directory). See `get --anchor-repo` for why this is not spelled --repo"
        ),
    )

    p = sub.add_parser(
        "recent",
        help="Findings TOUCHED (updated_at) at or after a date — not a close-time query",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Findings touched at or after a date — the one call for \"what closed since\".\n"
            "\n"
            "WHAT THIS MEASURES: updated_at, the time of the LAST WRITE to the row, and\n"
            "not the moment the finding was closed. There is no close timestamp anywhere\n"
            "in the schema. A status change moves updated_at, and so do a re-tag, a meta\n"
            "patch, a severity re-triage, an append_note, and a DEDUPLICATED OBSERVATION\n"
            "— a repeat report bumps the occurrence count and stamps updated_at while the\n"
            "status stays exactly where it was.\n"
            "\n"
            "So `recent --since DATE --status fixed` means \"cards that are fixed NOW and\n"
            "were touched since that date\", NOT \"cards closed since that date\". The error\n"
            "is ONE-SIDED: false positives are possible, misses are not, because closing a\n"
            "card always writes updated_at.\n"
            "\n"
            "Rows print newest touch first; whole-second ties break by rowid, so a paged\n"
            "walk is stable."
        ),
    )
    p.add_argument(
        "--since",
        required=True,
        help="Lower bound on updated_at, INCLUSIVE: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ",
    )
    p.add_argument("--status", help="Filter by status (aliases accepted); omit for every status")
    p.add_argument("--limit", type=int, help="Max results (default 100)")

    p = sub.add_parser("get", help="Fetch a single finding by ID")
    p.add_argument("id", help="Finding ID (e.g. CB-1383)")
    p.add_argument(
        "--no-resolve-anchor",
        dest="resolve_anchors",
        action="store_false",
        help=(
            "Do not resolve the location anchor against the repository. The card still "
            "reports whether it carries one; only the git work is skipped. For a read "
            "that must not spawn a process."
        ),
    )
    p.add_argument(
        "--anchor-repo",
        dest="repo",
        help=(
            "Repository to resolve the location anchor against (default: the tracker's own "
            "directory). NOT --repo: that name already means 'and also locate .codebugs/' "
            "on this CLI's provenance verbs, and one flag with two meanings is a trap"
        ),
    )

    p = sub.add_parser("stats", help="Cross-tabulated summary")
    p.add_argument(
        "--by",
        help=(
            "Group by: severity|category|status|file|source|tag|meta:<key>. "
            "tag and meta:<key> do not partition — a card with two tags is "
            "cross-tabulated under both, so TOTAL exceeds the finding count"
        ),
    )

    sub.add_parser("summary", help="Dashboard overview")
    sub.add_parser("categories", help="List all categories with counts")

    p = sub.add_parser(
        "categories-normalize",
        help="Rename stored categories and re-key auto:v1 fingerprints: fold spellings "
        "to canon, or MERGE category names into one another with --fold-map (CB-61)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes (without this the command only reports)",
    )
    p.add_argument(
        "--fold-map",
        help='JSON {"stored category name": "canonical_target_name"} — the key may be '
        "ANY name the table holds, not just a spelling of the target, so this is how "
        "category names are merged; omit it to fold every stored spelling to its own "
        "normalized form. Check the printed from->to table against your map before "
        "--apply: a key matching no stored category is silently absent from it. Back up "
        "with export-csv first (restore-csv reloads findings verbatim into an empty "
        "tracker; milestone items and audit history are not in a CSV export)",
    )
    p.add_argument(
        "--new-category",
        action="store_true",
        help="Permit folding INTO a category this tracker does not hold yet; without it "
        "such a target is refused, naming the nearest existing ones (CB-223)",
    )
    p.add_argument("--json", action="store_true", help="Print the raw report as JSON")

    p = sub.add_parser("import-csv", help="Import findings from CSV")
    p.add_argument("file", help="CSV file path")

    p = sub.add_parser(
        "restore-csv",
        help="Restore an export VERBATIM (ids, statuses, counts) into an empty tracker",
    )
    p.add_argument("file", help="CSV file path")

    p = sub.add_parser("export-csv", help="Export findings to CSV")
    p.add_argument("file", nargs="?", help="Output file (default: codebugs_export.csv)")

    commands.update(
        {
            "add": _cmd_add,
            "update": _cmd_update,
            "query": _cmd_query,
            "recent": _cmd_recent,
            "get": _cmd_get,
            "stats": _cmd_stats,
            "summary": _cmd_summary,
            "categories": _cmd_categories,
            "categories-normalize": _cmd_categories_normalize,
            "import-csv": _cmd_import_csv,
            "restore-csv": _cmd_restore_csv,
            "export-csv": _cmd_export_csv,
        }
    )


db.register_schema("findings", ensure_schema)
db.register_tool_provider("findings", register_tools)
db.register_cli_provider("findings", register_cli)
