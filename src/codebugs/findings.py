"""Findings domain — CRUD, query, stats, MCP tools, and CLI for code findings."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
from typing import Any

from codebugs import db, entities
from codebugs.types import (
    ENTITY_FINDING,
    FINDING_ID_PREFIX,
    SEVERITIES,
    is_text_filter_active,
    is_vocabulary_filter_active,
    rank_case_sql,
    resolve_finding_status,
    resolve_severity,
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
    """
    if not meta:
        return
    reserved = _RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
    if updating:
        reserved -= db.resolver_updatable_meta_keys()
    hit = reserved & set(meta)
    if hit:
        raise ValueError(
            f"meta keys {sorted(hit)} are reserved for the identity machinery "
            f"(they are its output, not input — strip them before re-submitting)"
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
    return "auto:v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _occurrence_entry(
    *,
    now: str,
    severity: str,
    file: str,
    description: str,
    source: str,
    tags: list[str] | None,
    meta: dict[str, Any] | None,
    reported_at_commit: str | None,
    reported_at_ref: str | None,
) -> dict[str, Any]:
    """One bounded record of a deduplicated observation.

    Carries enough of the discarded observation (severity, description, file, tags,
    meta, refs) that a false merge can be un-merged from the ring alone. `meta`
    matters most: volatile meta values are exactly what fingerprint normalization
    strips, so without them the ring cannot show WHICH observations a too-coarse
    fingerprint merged (Codex review of this range).
    """
    entry: dict[str, Any] = {
        "at": now,
        "severity": severity,
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


def _bump_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    now: str,
    entry: dict[str, Any],
    reopen: bool = False,
) -> sqlite3.Row:
    """Record an occurrence on an existing finding; optionally reopen it (regression).

    ONE UPDATE, `meta` composed fully in Python and assigned exactly once (CB-16), the
    count bumped SQL-side, the mutated row captured via RETURNING and read by fetching
    — never rowcount (the RETURNING rule). Runs inside the caller's open transaction;
    the meta read-modify-write is safe only because add's whole body sits in db.txn
    (CB-24). The raw row is returned for conversion AFTER the transaction closes.

    Raises json.JSONDecodeError on malformed stored meta BEFORE any write — the add
    fails cleanly with nothing landed, which is the honest half of the CB-16 rule.
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
    params.append(json.dumps(meta))
    params.append(row["id"])

    return conn.execute(
        f"UPDATE findings SET {sets}, meta = ? WHERE id = ? RETURNING *",  # noqa: S608
        params,
    ).fetchone()


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
) -> tuple[dict[str, Any] | None, sqlite3.Row | None, bool, str]:
    """One observation through the identity function, inside an OPEN transaction.

    Returns (inserted_dict, matched_raw_row, was_new, dedup_action) — exactly one of
    the first two is non-None. An inserted row is converted here (its meta is the JSON
    this call just serialized, so conversion cannot fail) because post-add hooks need
    the dict; a matched row is returned RAW for conversion after the caller's
    transaction closes, since its stored meta is legacy data (CB-24 consequence 2).

    `severity` and `fingerprint` arrive already validated — validation must run before
    the caller opens its transaction, so invalid input raises immediately instead of
    an OperationalError after a busy_timeout wait under contention.
    """
    now = utc_now()
    dedup_action = "created"
    recurrence_of: str | None = None

    if finding_id is None:
        if fingerprint is None:
            fingerprint = _derive_fingerprint(category, file, description, meta)
        live, closed = _match_fingerprint(conn, fingerprint)
        entry = _occurrence_entry(
            now=now,
            severity=severity,
            file=file,
            description=description,
            source=source,
            tags=tags,
            meta=meta,
            reported_at_commit=reported_at_commit,
            reported_at_ref=reported_at_ref,
        )
        if live is not None:
            return None, _bump_row(conn, live, now=now, entry=entry), False, "bumped"
        if closed is not None and closed["status"] in _REOPEN_STATUSES:
            raw = _bump_row(conn, closed, now=now, entry=entry, reopen=True)
            # Fire like update_finding does: the write changed the row, inside this
            # transaction, so claims/milestone reconciliation land atomically with it.
            db.run_status_change_hooks(conn, closed["id"], closed["status"], "open")
            return None, raw, False, "reopened"
        if closed is not None:
            # A wont_fix / not_a_bug closure is a DECISION, not a fix — it stays
            # closed, and the recurrence becomes a new row that keeps the link.
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

    fid = finding_id or _next_id(conn)
    meta_final = dict(meta or {})
    if recurrence_of is not None:
        meta_final["recurrence_of"] = recurrence_of
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
    return result, None, True, dedup_action


class PostCommitCorruptionError(Exception):
    """The dedup write COMMITTED, then the matched row's stored data failed to parse.

    Distinct from the pre-write ``json.JSONDecodeError`` that ``_bump_row``
    raises on malformed stored ``meta`` — there the transaction rolls back and
    nothing lands. A caller deciding "did the observation land?" must not have
    to guess between the two (Codex round-3 review). Raised ONLY when the add's
    own frame committed: under an ambient transaction nothing has committed yet
    and the raw ``JSONDecodeError`` propagates to the owning frame instead,
    which typically abandons the whole unit with it — same contract as
    ``update_finding``'s conversion (Codex round 4). The UPDATE path keeps
    raising raw ``JSONDecodeError`` on its own committed path too,
    deliberately: its CLI re-raise contract is pinned by
    ``TestRetriageCliContract``.
    """


def _finalize_add(
    inserted: dict[str, Any] | None,
    raw_row: sqlite3.Row | None,
    was_new: bool,
    dedup_action: str,
    *,
    committed: bool,
) -> dict[str, Any]:
    """Convert an _add_one outcome to the response dict, AFTER the transaction closed.

    `was_new` / `dedup_action` are response-only keys (sweep.py's was_new
    discriminator), not columns. ``committed`` is this frame's ``db.txn``
    ownership result: only a frame that actually committed may classify a
    conversion failure as post-commit — under an ambient transaction the owner
    will normally roll the unit back, so claiming "recorded" would mislead
    retry/accounting logic.
    """
    if inserted is not None:
        result = inserted
    else:
        try:
            result = db.row_to_dict(raw_row)
        except json.JSONDecodeError as e:
            if committed:
                raise PostCommitCorruptionError(
                    f"occurrence recorded on {raw_row['id']}, but its stored data "
                    f"could not be serialized: {e}"
                ) from e
            raise
    result["was_new"] = was_new
    result["dedup_action"] = dedup_action
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
) -> dict[str, Any]:
    """Record an observation. Returns the created OR matched finding as a dict.

    ``annotate=False`` skips the pre-add resolvers (CB-45) for this insert;
    used by CSV import — an import is not an observation.

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

    The whole body runs in ``db.txn`` — the fingerprint lookup plus conditional
    write is a read-modify-write (CB-24). Do not restore a ``conn.commit()`` here:
    ``db.txn`` yields ``False`` under an ambient transaction, and committing then
    would commit the *caller's* work.
    """
    severity = resolve_severity(severity)
    fingerprint = _validate_fingerprint(fingerprint)
    _validate_meta_keys(meta)

    with db.txn(conn) as owned:
        inserted, raw_row, was_new, dedup_action = _add_one(
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
        )
    return _finalize_add(inserted, raw_row, was_new, dedup_action, committed=owned)


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


def batch_add_findings(
    conn: sqlite3.Connection,
    findings: list[dict[str, Any]],
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
    """
    # Validate EVERY member before the transaction opens: invalid input raises
    # immediately, not after a busy_timeout wait, and never half-applies a batch.
    validated: list[tuple[str, str | None]] = []
    for i, f in enumerate(findings):
        unknown = set(f) - _BATCH_MEMBER_KEYS
        if unknown:
            raise ValueError(f"findings[{i}]: unknown keys {sorted(unknown)}")
        _validate_meta_keys(f.get("meta"))
        validated.append(
            (
                resolve_severity(f.get("severity", "medium")),
                _validate_fingerprint(f.get("fingerprint")),
            )
        )

    # Each member's result is the row AS OBSERVED when that member was processed: a
    # later member bumping an earlier one does not retroactively update the earlier
    # member's returned occurrence_count. Input order is preserved by construction.
    outcomes = []
    with db.txn(conn) as owned:
        for f, (severity, fingerprint) in zip(findings, validated, strict=True):
            outcomes.append(
                _add_one(
                    conn,
                    severity=severity,
                    category=f["category"],
                    file=f["file"],
                    description=f["description"],
                    source=f.get("source", "human"),
                    tags=f.get("tags"),
                    meta=f.get("meta"),
                    finding_id=f.get("id"),
                    reported_at_commit=f.get("reported_at_commit"),
                    reported_at_ref=f.get("reported_at_ref"),
                    fingerprint=fingerprint,
                )
            )
    return [_finalize_add(*outcome, committed=owned) for outcome in outcomes]


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
    excluded — it is immutable after insert.

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
        conditions.append("reported_at_commit LIKE ? || '%'")
        params.append(commit.lower())
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
    ) -> dict[str, Any]:
        """Record a code finding observation (deduplicated by fingerprint).

        If the fingerprint matches a live finding, that finding's occurrence count
        is bumped and IT is returned (`was_new: false`, `dedup_action: "bumped"`);
        a match on a `fixed` finding reopens it as a regression (`"reopened"`); a
        match on a `wont_fix`/`not_a_bug` finding creates a new row linked via
        `meta.recurrence_of`. Check `was_new` to tell create from match. Without a
        fingerprint a conservative server-side one is derived from category, file
        and the normalized description.

        Args:
            severity: critical, high, medium, or low (case-insensitive, no aliases)
            category: Finding category (e.g. tz_naive_datetime, n_plus_one, missing_validation).
                      Call `categories` first to reuse existing category names.
            file: File path relative to project root
            description: What's wrong
            source: Who created this finding (default: claude)
            tags: Optional tags for grouping
            meta: Optional JSON metadata (lines, module, rule_code, etc.)
            reported_at_commit: Git SHA when finding was created (auto-detected from HEAD if omitted)
            reported_at_ref: Version/tag label (e.g. "v2.1.0"), always caller-supplied
            fingerprint: Stable identity token for this defect, computed from the
                         INVARIANT part of the observation (normalized error
                         signature + failing test + anchor file — no timestamps,
                         SHAs, run ids). Same defect → same fingerprint. The
                         `auto:` prefix is reserved for server-derived values.
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
            )

    @mcp.tool()
    def batch_add(
        findings: list[dict[str, Any]],
        reported_at_commit: str | None = None,
        reported_at_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """Record multiple finding observations at once (deduplicated by fingerprint).

        Members are deduplicated exactly like `add` — including against each other,
        so two members sharing a fingerprint yield one insert plus one bump. One
        result per input, in input order; check each result's `was_new` /
        `dedup_action`. Unknown member keys are refused.

        Args:
            findings: List of finding objects, each with keys:
                severity, category, file, description, and optionally:
                source, tags, meta, reported_at_commit, reported_at_ref, fingerprint
            reported_at_commit: Default commit SHA for all findings (auto-detected if omitted).
                                Per-finding values override this.
            reported_at_ref: Default version label for all findings.
                             Per-finding values override this.
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
            return batch_add_findings(conn, enriched)

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
            reported_at_ref: Update version/tag label (e.g. "v2.1.0")
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
            source: Filter by source (claude, ruff, human, etc.)
            tag: Filter by tag (finds findings containing this tag)
            meta_key: Filter by metadata key existence
            meta_value: Filter by metadata value (requires meta_key)
            commit: Filter by reported_at_commit (prefix match, hex validated)
            ref: Filter by reported_at_ref (exact match)
            fingerprint: Filter by identity fingerprint (exact match)
            group_by: Group results by: file, category, severity, status, source
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
                deferred_ids = blockers.deferred_id_restriction(
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
                counts = blockers.blocker_counts_for(conn, ENTITY_FINDING, deferred_ids)
                for row in result["findings"]:
                    row["blocker_count"] = counts.get(row["id"], 0)
            return result

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


def register_cli(sub, commands) -> None:
    """Register findings CLI subcommands."""
    import argparse
    from codebugs.fmt import format_table

    def _cmd_add(args: argparse.Namespace) -> None:
        conn = db.connect()
        meta = {}
        if args.lines:
            meta["lines"] = args.lines
        if args.meta:
            meta.update(json.loads(args.meta))

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

    def _cmd_import_csv(args: argparse.Namespace) -> None:
        conn = db.connect()
        imported = 0
        skipped = 0
        merged = 0
        errors = 0
        corrupt = 0
        # Loop-invariant: resolver registration happens at module import, which
        # db.connect() above completed, so the union cannot change mid-import.
        dropped_keys = _RESERVED_META_KEYS | db.resolver_reserved_meta_keys()
        # The open is HOISTED out of the `with` so the guard covers it alone
        # (CB-71). `with open(...) as f:` owned the entire import loop, so the
        # obvious `try: with open(...): <loop>` would have put both the
        # already-committed rows and the loop's own stderr prints (:1866, :1877,
        # :1886) inside an OSError arm — reporting a partially-landed import as
        # bad input, the CB-15/CB-16 lie this guard exists to avoid. A read
        # failure mid-iteration therefore still crashes, deliberately: what to
        # report when rows are already committed is a semantics decision, CB-77.
        try:
            handle = open(args.file, newline="")
        except OSError as e:
            print(f"codebugs: {e}", file=sys.stderr)
            conn.close()
            sys.exit(1)
        with handle as f:
            reader = csv.DictReader(f)
            for row in reader:
                # No inline .strip().lower() — add_finding normalizes now (CB-19).
                severity = row.get("severity") or row.get("Severity") or "medium"
                category = (row.get("category") or row.get("Category") or "").strip()
                filepath = (row.get("file") or row.get("File") or "").strip()
                description = (row.get("description") or row.get("Description") or "").strip()
                source = (row.get("source") or row.get("Source") or "import").strip()

                if not filepath or not description or not category:
                    continue

                # Re-importing this tracker's own export must be a no-op, not a
                # mass-resurrection: identical descriptions derive identical
                # fingerprints, so without this guard every since-fixed card in
                # the CSV would REOPEN as a phantom regression.
                row_id = (row.get("id") or "").strip()
                if row_id and conn.execute(
                    "SELECT 1 FROM findings WHERE id = ?", (row_id,)
                ).fetchone():
                    skipped += 1
                    continue

                meta = {}
                # Full meta round-trips (export writes it as JSON): without it a
                # derived fingerprint re-derives DIFFERENTLY on import, because
                # the volatile tokens it stripped are no longer declared. Reserved
                # identity keys (an exported ring/regression history) are the
                # machinery's output, not input — dropped, not refused.
                meta_json = (row.get("meta") or "").strip()
                if meta_json:
                    try:
                        stored = json.loads(meta_json)
                    except json.JSONDecodeError:
                        stored = {}
                    if isinstance(stored, dict):
                        # Strip the DYNAMIC union, not just the static set: an
                        # exported similar_to/resolver_errors would otherwise be
                        # refused by _validate_meta_keys and kill the re-import
                        # mid-way with earlier rows already committed (CB-45
                        # review — the enumeration was the letter, "the
                        # machinery's output is not input" is the intent).
                        meta.update({k: v for k, v in stored.items() if k not in dropped_keys})
                lines = (row.get("lines") or row.get("Lines") or "").strip()
                if lines:
                    meta["lines"] = lines

                fingerprint = (row.get("fingerprint") or "").strip() or None
                # Exported auto-derived fingerprints carry the reserved prefix,
                # which add_finding refuses from callers; passing None re-derives
                # the same value server-side.
                if fingerprint and fingerprint.startswith(_AUTO_FP_PREFIX):
                    fingerprint = None

                # Per-row guard: a mid-file bad fingerprint (oversized, live
                # collision, reserved prefix survives the auto: strip) must not
                # abort the import with a raw traceback while earlier rows are
                # already committed — name the row, count it, keep going.
                try:
                    result = add_finding(
                        conn,
                        severity=severity,
                        category=category,
                        file=filepath,
                        description=description,
                        source=source,
                        meta=meta or None,
                        fingerprint=fingerprint,
                        annotate=False,  # an import is not an observation (CB-45)
                    )
                except PostCommitCorruptionError as e:
                    # The occurrence bump LANDED; only serializing the matched
                    # row failed. Reporting it as a failed CSV row would be a
                    # failure-shaped signal for a successful mutation.
                    corrupt += 1
                    print(f"Stored-data corruption: {e}", file=sys.stderr)
                    continue
                except json.JSONDecodeError as e:
                    # MUST stay ahead of the ValueError arm, which it
                    # subclasses. Pre-write corruption: _bump_row parses the
                    # matched row's stored meta BEFORE writing, so the
                    # transaction rolled back and NOTHING landed — unlike
                    # PostCommitCorruptionError above, and the message must
                    # not claim otherwise (Codex round-3 review).
                    errors += 1
                    label = row_id or f"row {imported + skipped + merged + errors + corrupt}"
                    print(
                        f"Error importing {label}: matched row has malformed stored "
                        f"meta; observation NOT recorded: {e}",
                        file=sys.stderr,
                    )
                    continue
                except ValueError as e:
                    errors += 1
                    label = row_id or f"row {imported + skipped + merged + errors + corrupt}"
                    print(f"Error importing {label}: {e}", file=sys.stderr)
                    continue
                # add_finding is an upsert (CB-43): a row whose fingerprint
                # matches an existing live card is MERGED into it, not created.
                # Counting that as "imported" would report full success while
                # fewer rows exist than the file held; a faithful cross-tracker
                # restore path is a separate card.
                if result["was_new"]:
                    imported += 1
                else:
                    merged += 1

        conn.close()
        parts = []
        if skipped:
            parts.append(f"{skipped} already present, skipped")
        if merged:
            parts.append(f"{merged} merged into existing findings by fingerprint")
        if errors:
            parts.append(f"{errors} failed (see stderr)")
        if corrupt:
            parts.append(f"{corrupt} recorded onto rows with corrupt stored data (see stderr)")
        suffix = f" ({'; '.join(parts)})" if parts else ""
        print(f"Imported {imported} findings.{suffix}")
        if errors or corrupt:
            sys.exit(1)

    def _cmd_export_csv(args: argparse.Namespace) -> None:
        conn = db.connect()
        result = query_findings(conn, limit=100000)
        conn.close()

        output = args.file or "codebugs_export.csv"
        with open(output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "severity",
                    "category",
                    "file",
                    "status",
                    "description",
                    "source",
                    "tags",
                    "meta",
                    "created_at",
                    "updated_at",
                    "fingerprint",
                    "occurrence_count",
                    "last_seen_at",
                ]
            )
            for finding in result["findings"]:
                writer.writerow(
                    [
                        finding["id"],
                        finding["severity"],
                        finding["category"],
                        finding["file"],
                        finding["status"],
                        finding["description"],
                        finding["source"],
                        json.dumps(finding["tags"]),
                        json.dumps(finding["meta"]),
                        finding["created_at"],
                        finding["updated_at"],
                        finding["fingerprint"],
                        finding["occurrence_count"],
                        finding["last_seen_at"],
                    ]
                )
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

    p = sub.add_parser("get", help="Fetch a single finding by ID")
    p.add_argument("id", help="Finding ID (e.g. CB-1383)")

    p = sub.add_parser("stats", help="Cross-tabulated summary")
    p.add_argument("--by", help="Group by: severity|category|status|file|source")

    sub.add_parser("summary", help="Dashboard overview")
    sub.add_parser("categories", help="List all categories with counts")

    p = sub.add_parser("import-csv", help="Import findings from CSV")
    p.add_argument("file", help="CSV file path")

    p = sub.add_parser("export-csv", help="Export findings to CSV")
    p.add_argument("file", nargs="?", help="Output file (default: codebugs_export.csv)")

    commands.update(
        {
            "add": _cmd_add,
            "update": _cmd_update,
            "query": _cmd_query,
            "get": _cmd_get,
            "stats": _cmd_stats,
            "summary": _cmd_summary,
            "categories": _cmd_categories,
            "import-csv": _cmd_import_csv,
            "export-csv": _cmd_export_csv,
        }
    )


db.register_schema("findings", ensure_schema)
db.register_tool_provider("findings", register_tools)
db.register_cli_provider("findings", register_cli)
