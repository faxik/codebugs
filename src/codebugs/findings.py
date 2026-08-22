"""Findings domain — CRUD, query, stats, MCP tools, and CLI for code findings."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any, Literal, NamedTuple, TypedDict

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
    resolve_finding_status,
    resolve_severity,
    severity_rank,
    utc_now,
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


def _validate_meta_keys(meta: dict[str, Any] | None, *, updating: bool = False) -> None:
    """Refuse caller meta colliding with identity-machinery or resolver keys.

    On UPDATE, keys a resolver declared UPDATABLE at registration (similarity's
    `similar_to`) are deliberately writable: the add-side reservation stops
    spoofing, but a permanently unrepairable annotation is the CB-26 shape — a
    re-scrub must be able to rewrite or clear it. The updatable set comes from
    the registry, never from a literal here: core findings must not know any
    one extension's key names (the seam exists so extensions declare their meta
    contract at registration). `resolver_errors` stays refused on both paths.

    `category_minted` (CB-60) is refused on ADD only: it is the gate's own
    output and a caller supplying it would spoof the mint count, but a
    permanently unrepairable stamp is the CB-26 shape, so update(meta_update=)
    may rewrite a false one.
    """
    if not meta:
        return
    reserved = _RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
    if updating:
        reserved -= db.resolver_updatable_meta_keys()
    else:
        reserved |= _ADD_ONLY_RESERVED_META_KEYS
    hit = reserved & set(meta)
    if hit:
        raise ValueError(
            f"meta keys {sorted(hit)} are reserved for the identity machinery "
            f"(they are its output, not input — strip them before re-submitting)"
        )


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
_ADD_ONLY_RESERVED_META_KEYS = frozenset({"category_minted"})

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
_RESPONSE_ONLY_KEYS = frozenset({"was_new", "dedup_action", "attention"})


def _finalize_add(outcome: AddOutcome, *, committed: bool) -> dict[str, Any]:
    """Convert an _add_one outcome to the response dict, AFTER the transaction closed.

    MECHANICAL BY CONTRACT (BT-5): conversion plus key writes, no computation and
    no new failure mode. Every signal was decided inside the transaction, so the
    post-commit path — the one that can already only report `PostCommitCorruptionError`
    — never acquires a second reason to fail.

    `was_new` / `dedup_action` / `attention` are response-only keys (see
    `_RESPONSE_ONLY_KEYS`), not columns. ``committed`` is this frame's ``db.txn``
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

    The whole body runs in ``db.txn`` — the fingerprint lookup plus conditional
    write is a read-modify-write (CB-24). Do not restore a ``conn.commit()`` here:
    ``db.txn`` yields ``False`` under an ambient transaction, and committing then
    would commit the *caller's* work.
    """
    severity = resolve_severity(severity)
    fingerprint = _validate_fingerprint(fingerprint)
    _validate_meta_keys(meta)
    if finding_id is None:
        # Pure normalization stays pre-txn (it is validation, and it is the
        # fingerprint-derivation input); the mint GATE runs inside _add_one on
        # the insert continuation, after the dedup branch is known (CB-113(b)).
        category = normalize_category(category)

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
        )
    return _finalize_add(outcome, committed=owned)


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
    # Validate every ARGUMENT before the transaction opens: invalid input raises
    # immediately, not after a busy_timeout wait, and never half-applies a batch.
    # The category mint gate is the one deliberate exception — it depends on the
    # member's dedup branch, so it runs inside the transaction (CB-113(b)); its
    # refusal rolls back the whole batch, preserving the nothing-lands property.
    validated: list[tuple[str, str | None, str]] = []
    for i, f in enumerate(findings):
        unknown = set(f) - _BATCH_MEMBER_KEYS
        if unknown:
            raise ValueError(f"findings[{i}]: unknown keys {sorted(unknown)}")
        _validate_meta_keys(f.get("meta"))
        category = f["category"]
        if f.get("id") is None:
            category = normalize_category(category)
        validated.append(
            (
                resolve_severity(f.get("severity", "medium")),
                _validate_fingerprint(f.get("fingerprint")),
                category,
            )
        )

    # Each member's result is the row AS OBSERVED when that member was processed: a
    # later member bumping an earlier one does not retroactively update the earlier
    # member's returned occurrence_count. Input order is preserved by construction.
    outcomes: list[AddOutcome] = []
    with db.txn(conn) as owned:
        for f, (severity, fingerprint, category) in zip(findings, validated, strict=True):
            outcomes.append(
                _add_one(
                    conn,
                    severity=severity,
                    category=category,
                    file=f["file"],
                    description=f["description"],
                    source=f.get("source", "human"),
                    tags=f.get("tags"),
                    meta=f.get("meta"),
                    finding_id=f.get("id"),
                    reported_at_commit=f.get("reported_at_commit"),
                    reported_at_ref=f.get("reported_at_ref"),
                    fingerprint=fingerprint,
                    new_category=new_category,
                )
            )
    return [_finalize_add(outcome, committed=owned) for outcome in outcomes]


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
        if (
            status is not None
            and status in LIVE_STATUSES
            and row["status"] not in LIVE_STATUSES
            and row["fingerprint"] is not None
        ):
            live = _live_row_by_fingerprint(conn, row["fingerprint"], exclude_id=finding_id)
            if live is not None:
                raise ValueError(
                    f"cannot set {finding_id} to {status}: its fingerprint is held by "
                    f"live finding {live['id']} (resolve or close that one first)"
                )

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


def get_finding(conn: sqlite3.Connection, finding_id: str) -> dict[str, Any]:
    """Fetch a single finding by ID. Raises KeyError if not found."""
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        raise KeyError(f"Finding not found: {finding_id}")
    return db.row_to_dict(row)


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
) -> dict[str, Any]:
    """Query findings with filters. Returns results or grouped counts.

    `id` / `ids` are AND-combined with other filters; missing IDs are silently absent.
    `commit` matches the frozen first-report column OR any occurrence-ring entry
    (CB-128) — any observation, not the newest (that is `provenance._effective_commit`).
    """
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
        valid_groups = ("file", "category", "severity", "status", "source")
        if group_by not in valid_groups:
            raise ValueError(f"Invalid group_by: {group_by}. Must be one of {valid_groups}")
        rows = conn.execute(
            f"SELECT {group_by} as group_key, COUNT(*) as count FROM findings {where} GROUP BY {group_by} ORDER BY count DESC",
            params,
        ).fetchall()
        return {"grouped": True, "group_by": group_by, "groups": [dict(r) for r in rows]}

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
    return {
        "grouped": False,
        "total": count,
        "limit": limit,
        "offset": offset,
        "findings": [db.row_to_dict(r) for r in rows],
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
    return {
        "total": count,
        "limit": limit,
        "offset": offset,
        "since": since_value,
        "status": status_value,
        "findings": [db.row_to_dict(r) for r in rows],
    }


def get_stats(
    conn: sqlite3.Connection,
    *,
    group_by: str = "severity",
) -> dict[str, Any]:
    """Aggregated counts. Returns cross-tabulated stats."""
    valid_groups = ("severity", "category", "status", "file", "source")
    if group_by not in valid_groups:
        raise ValueError(f"Invalid group_by: {group_by}. Must be one of {valid_groups}")

    rows = conn.execute(
        f"""SELECT {group_by} as grp, severity, COUNT(*) as cnt,
                   SUM(occurrence_count) as occ
            FROM findings
            GROUP BY grp, severity
            ORDER BY grp, severity"""
    ).fetchall()

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

    return {"group_by": group_by, "groups": groups}


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
    conn: sqlite3.Connection, fold_map: dict[str, str] | None
) -> dict[str, Any]:
    """Read the whole population and decide. Writes nothing; the caller applies."""
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
    rows_scanned = 0

    for row in conn.execute(_FOLD_SELECT):
        rows_scanned += 1
        kind, target, new_fingerprint = _fold_row_decision(row, fold_map)
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
        if effective is not None and row["status"] in LIVE_STATUSES:
            by_fingerprint.setdefault(effective, []).append(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "from": row["category"],
                    "to": target if kind in _FOLD_KIND_ACTION else None,
                }
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

    return {
        "applied": False,
        "stopped": bool(collisions),
        "fold_map": {r["from"]: r["to"] for r in renames},
        "rows_scanned": rows_scanned,
        "renames": renames,
        "counts": counts,
        "collisions": collisions,
        "unverifiable": unverifiable,
    }


def normalize_categories(
    conn: sqlite3.Connection,
    *,
    fold_map: dict[str, str] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Fold stored category spellings to canon and re-key their `auto:v1` hashes (CB-61).

    DRY RUN BY DEFAULT: with ``apply=False`` nothing is written and no write
    transaction is opened at all. ``apply=True`` performs the whole migration
    inside ONE ``db.txn`` — the population is read under the write lock (CB-24),
    the collision decision is made from that read, and either every rename lands
    or none does.

    ``fold_map`` maps a STORED spelling to its canonical target; every target must
    already satisfy ``normalize_category(t) == t``. ``None`` (the default) derives
    the map mechanically — each stored spelling folds to its own normalized form.
    ``{}`` is an explicit no-op, not the default.

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

    Returns the same report shape in both modes: ``applied``, ``stopped``,
    ``fold_map`` (only the pairs that actually matched rows), ``rows_scanned``,
    ``renames``, ``counts``, ``collisions``, ``unverifiable``.
    """
    validated = _validate_fold_map(fold_map)
    if not apply:
        return _plan_category_fold(conn, validated)

    with db.txn(conn):
        report = _plan_category_fold(conn, validated)
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
        if reported_at_commit is None:
            reported_at_commit = db.git_rev_parse("HEAD", silent=True)
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
        default_commit = (
            reported_at_commit
            if reported_at_commit is not None
            else db.git_rev_parse("HEAD", silent=True)
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
            return batch_add_findings(conn, enriched, new_category=new_category)

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
                   append_note instead.
            append_note: Appends a newline-joined line, preserving the prior notes.
                         This is the safe way to add evidence to a long-lived card.
            tags: Replace tags list
            meta_update: Merge additional metadata keys
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
            group_by: Group results by: file, category, severity, status, source
                      (source groups count FIRST reporters — the column is
                      frozen at first report)
            limit: Max results (default 100)
            offset: Pagination offset
        """
        with conn_factory() as conn:
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
    def get(finding_id: str) -> dict[str, Any]:
        """Fetch a single finding by ID with full body (description, severity,
        status, tags, meta, timestamps, commit refs).

        Raises a not-found error if the ID does not exist. For lenient batch
        lookup that silently drops missing IDs, use `query(ids=[...])`.

        Args:
            finding_id: The finding ID (e.g. CB-1383)
        """
        with conn_factory() as conn:
            return get_finding(conn, finding_id)

    @mcp.tool()
    def stats(group_by: str = "severity") -> dict[str, Any]:
        """Aggregated cross-tabulated counts.

        Args:
            group_by: Group by: severity, category, status, file, source
                      (source buckets count FIRST reporters — the column is
                      frozen at first report, BT-4)
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
        fold_map: dict | str | None = None, apply: bool = False
    ) -> dict[str, Any]:
        """One-shot migration: fold stored category spellings to canon (CB-61).

        DRY RUN BY DEFAULT — without `apply=true` nothing is written and the
        report tells you exactly what would change. New findings are already
        canonicalized at write time; this exists for rows filed before that,
        whose stored `auto:v1` fingerprint still carries the old spelling and
        therefore forks identity when the same defect is reported again.

        Each renamed row's fingerprint is handled by kind: a `NULL` or a
        caller-SUPPLIED fingerprint is left byte-identical, an `auto:v1` one is
        re-derived with the new category after its stored inputs are verified to
        reproduce the stored hash. A row that fails that round trip is skipped
        WHOLE and reported under `unverifiable`. The occurrence ring
        (`meta.occurrences`) is never rewritten.

        If the fold would put two LIVE findings on one fingerprint, the run
        writes NOTHING and reports the colliding pair by id — merging two cards
        is a decision, not a migration step.

        Args:
            fold_map: Optional {stored spelling: canonical target} map, as an
                      object or a JSON string. Every target must already be
                      canonical (casefold, hyphen/whitespace -> "_"). Omit it to
                      fold every stored spelling to its own normalized form; `{}`
                      is an explicit no-op.
            apply: Write the changes. Default false (report only).
        """
        parsed = json.loads(fold_map) if isinstance(fold_map, str) else fold_map
        with conn_factory() as conn:
            return normalize_categories(conn, fold_map=parsed, apply=apply)


def register_cli(sub, commands) -> None:
    """Register findings CLI subcommands."""
    import argparse
    from codebugs.fmt import format_table
    from codebugs.fsio import atomic_write

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
        flag_meta = {}
        for dest, key, _spelling in _ADD_META_FLAGS:
            value = getattr(args, dest, None)
            if value:
                flag_meta[key] = value

        json_meta = json.loads(args.meta) if args.meta else {}
        # A non-dict payload is left exactly as it behaved before — `dict.update` below
        # raises on it. Testing `key in json_meta` on a str would be a SUBSTRING test,
        # which is a wrong answer rather than an error.
        if isinstance(json_meta, dict):
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

        try:
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
            )
        except json.JSONDecodeError:
            # MUST stay ahead of the ValueError arm, which it subclasses — the
            # _cmd_update ordering contract. This is _bump_row's PRE-write
            # parse of the matched row's stored meta: corruption, not bad
            # input, so it must not print as a tidy usage error. (The
            # post-commit twin — stored tags failing AFTER the bump landed —
            # arrives as PostCommitCorruptionError, which no arm here catches
            # and which therefore also propagates loudly.)
            raise
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
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

    def _cmd_update(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
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
        except json.JSONDecodeError:
            # MUST stay ahead of the ValueError arm below, which it subclasses.
            # This is a corrupted stored row, not bad user input, and the write
            # has ALREADY been committed by the time result serialization raises.
            # Reporting it as a clean input error would exit 1 on a successful
            # mutation — a failure-shaped signal for a write that landed.
            raise
        except (KeyError, ValueError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

    def _cmd_query(args: argparse.Namespace) -> None:
        conn = db.connect()
        ids = [s.strip() for s in args.id.split(",") if s.strip()] if args.id else None
        try:
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
                limit=args.limit or 100,
            )
        except json.JSONDecodeError:
            # MUST stay ahead of the ValueError arm, which it subclasses — same
            # ordering contract as `_cmd_update`. A corrupt stored row is not bad
            # user input, and flattening it into a one-line "bad input" message
            # would hide a data-integrity problem behind a usage error.
            raise
        except ValueError as e:
            # `--severity`/`--status` are free text; an unknown value now names
            # itself and exits 1 instead of printing a traceback. Vocabulary
            # filters have raised since `status` began resolving — this handler
            # simply never caught it (CB-19).
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

        if result.get("grouped"):
            data = [{"group": r["group_key"], "count": str(r["count"])} for r in result["groups"]]
            print(format_table(data, ["group", "count"]))
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
            print(
                format_table(
                    data,
                    ["id", "sev", "category", "file", "status", "description"],
                    max_widths={"description": 60, "file": 40, "category": 25},
                )
            )
            print(f"\n{result['total']} finding(s) total.")

    def _cmd_recent(args: argparse.Namespace) -> None:
        conn = db.connect()
        try:
            result = recent_findings(
                conn,
                since=args.since,
                status=args.status,
                limit=args.limit or 100,
            )
        except json.JSONDecodeError:
            # MUST stay ahead of the ValueError arm, which it subclasses — the
            # `_cmd_update` ordering contract. This one reaches here through
            # `db.row_to_dict` on a row with corrupt stored meta/tags: a
            # data-integrity problem, not bad user input, and flattening it into
            # a tidy one-line usage error would hide it.
            raise
        except ValueError as e:
            # A `--since` that is not a date, or an unknown `--status`, names
            # itself and exits 1 instead of printing a traceback.
            print(str(e), file=sys.stderr)
            sys.exit(1)
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
        conn = db.connect()
        try:
            result = get_finding(conn, args.id)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        print(json.dumps(result, indent=2, sort_keys=True))

    def _cmd_stats(args: argparse.Namespace) -> None:
        conn = db.connect()
        result = get_stats(conn, group_by=args.by or "severity")
        conn.close()

        groups = result["groups"]
        if not groups:
            print("(no findings)")
            return

        header = f"{'':30s} {'critical':>8s} {'high':>8s} {'medium':>8s} {'low':>8s} {'total':>8s}"
        print(header)
        print("-" * len(header))
        totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        for grp in sorted(groups):
            d = groups[grp]
            print(
                f"{grp:30s} {d['critical']:>8d} {d['high']:>8d} {d['medium']:>8d} {d['low']:>8d} {d['total']:>8d}"
            )
            for k in totals:
                totals[k] += d[k]
        print("-" * len(header))
        print(
            f"{'TOTAL':30s} {totals['critical']:>8d} {totals['high']:>8d} {totals['medium']:>8d} {totals['low']:>8d} {totals['total']:>8d}"
        )

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

        conn = db.connect()
        try:
            report = normalize_categories(conn, fold_map=fold_map, apply=args.apply)
        except json.JSONDecodeError:
            # MUST stay ahead of the ValueError arm it subclasses (the _cmd_update
            # ordering contract): a corrupted stored row is not bad user input.
            raise
        except (KeyError, ValueError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()

        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_fold_report(report)
        # A dry run always exits 0 — it REPORTS a collision, it does not refuse.
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
            with atomic_write(output, newline="") as f:
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
        print(f"Exported {len(result['findings'])} findings to {output}")

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
    p.add_argument("--group-by", help="Group by: file|category|severity|status|source")
    p.add_argument("--limit", type=int, help="Max results")

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

    p = sub.add_parser("stats", help="Cross-tabulated summary")
    p.add_argument("--by", help="Group by: severity|category|status|file|source")

    sub.add_parser("summary", help="Dashboard overview")
    sub.add_parser("categories", help="List all categories with counts")

    p = sub.add_parser(
        "categories-normalize",
        help="Fold stored category spellings to canon and re-key auto:v1 fingerprints (CB-61)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes (without this the command only reports)",
    )
    p.add_argument(
        "--fold-map",
        help='JSON {"stored spelling": "canonical_target"}; omit to fold every '
        "spelling to its own normalized form",
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
