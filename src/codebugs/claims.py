"""Entity claims — who currently holds a finding or a requirement.

One table, ``entity_claims``. Mutual exclusion is a partial unique index
(``entity_id`` WHERE ``released_at IS NULL``), so at most one LIVE claim per
entity is guaranteed by the database rather than by transaction discipline.
Release is a soft delete, so history and audit facts have a home from day one.

Two layers, and the split is load-bearing:

* **core** (``_claim_core`` / ``_release_core``) emits statements only. It NEVER
  opens a transaction and NEVER commits. It is what the terminal hook calls,
  because that hook runs inside ``update_finding``'s already-open transaction.
* **public** (``claim`` / ``release``) wraps the core in ``db.txn`` and classifies
  SQLite contention into the ``undetermined`` outcome.

**Ambient-transaction invariant (normative):** every caller of the public layer
MUST hold a connection with no open transaction. On a connection whose
transaction was opened implicitly by an earlier statement, ``db.txn`` yields
False, the core writes, nothing commits, and ``claim`` still reports success.
This is unreachable today because ``server.py``'s ``_conn`` and every CLI handler
open a fresh connection per call. A future ambient consumer must own its own
commit and call the CORE layer, never ``claim`` / ``release``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import Field

from codebugs import db, entities
from codebugs import types as t

CLAIMS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS entity_claims (
    claim_id       TEXT PRIMARY KEY,
    entity_id      TEXT NOT NULL,
    kind           TEXT NOT NULL,

    holder         TEXT NOT NULL,
    holder_kind    TEXT NOT NULL DEFAULT 'agent'
                     CHECK(holder_kind IN ('branch', 'agent', 'human')),
    holder_repo    TEXT,

    claimed_at     TEXT NOT NULL,
    renewed_at     TEXT NOT NULL,
    touch_count    INTEGER NOT NULL DEFAULT 1,
    note           TEXT NOT NULL DEFAULT '',

    prev_status    TEXT,
    projected_to   TEXT,

    released_at    TEXT,
    released_by    TEXT,
    release_reason TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_live
    ON entity_claims(entity_id) WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_claims_holder_live
    ON entity_claims(holder) WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_claims_entity ON entity_claims(entity_id);
"""

CLAIM_ID_PREFIX = "CLM-"

_ROW_COLS = (
    "claim_id, entity_id, kind, holder, holder_kind, holder_repo, "
    "claimed_at, renewed_at, touch_count, note, prev_status, projected_to"
)

#: Every claim/release response carries every one of these keys, None where the
#: outcome does not populate it. There is no other response shape.
_COMMON_KEYS = (
    "outcome",
    "entity_id",
    "kind",
    "holder",
    "holder_kind",
    "holder_repo",
    "claim_id",
    "claimed_at",
    "renewed_at",
    "touch_count",
    "held_seconds",
    "idle_seconds",
    "projected",
    "projected_to",
    "prev_status",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Initialize the claims schema (table + indexes)."""
    for stmt in CLAIMS_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


# --- helpers -----------------------------------------------------------------


def _next_claim_id(conn: sqlite3.Connection) -> str:
    """Generate the next CLM-N id. Called inside the claim transaction, so the
    read-then-insert is not a race."""
    prefix_len = len(CLAIM_ID_PREFIX) + 1  # 1-based SUBSTR offset past the prefix
    row = conn.execute(
        f"SELECT claim_id FROM entity_claims WHERE claim_id LIKE ? "  # noqa: S608 (constant)
        f"ORDER BY CAST(SUBSTR(claim_id, {prefix_len}) AS INTEGER) DESC LIMIT 1",
        (f"{CLAIM_ID_PREFIX}%",),
    ).fetchone()
    n = int(row["claim_id"][len(CLAIM_ID_PREFIX) :]) + 1 if row else 1
    return f"{CLAIM_ID_PREFIX}{n}"


def _live_claim(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    """The live claim row for an entity, or None."""
    row = conn.execute(
        f"SELECT {_ROW_COLS} FROM entity_claims WHERE entity_id = ? AND released_at IS NULL",  # noqa: S608 (constant column list)
        (entity_id,),
    ).fetchone()
    return dict(row) if row else None


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _elapsed(then: str | None, now: str) -> int | None:
    """Whole seconds between two utc_now() strings. None if either is unparseable."""
    a, b = _parse(then), _parse(now)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds())


def _decorate(row: dict[str, Any], now: str) -> dict[str, Any]:
    """Add the only two derived fields a returned row ever carries.

    No `stale`, no `orphaned`, no `divergent` — those are audit decorations and
    audit tooling is deferred. The reader picks its own threshold on idle_seconds.
    """
    out = dict(row)
    out["held_seconds"] = _elapsed(row.get("claimed_at"), now)
    out["idle_seconds"] = _elapsed(row.get("renewed_at"), now)
    return out


def _response(
    outcome: str,
    *,
    entity_id: str,
    kind: str | None = None,
    row: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """THE single constructor for every claim/release response.

    Every key in _COMMON_KEYS is present on every outcome, None where it does not
    apply. `row` is a full entity_claims row — the live one, or the row just
    written. holder/holder_kind/holder_repo are always the LIVE claim's triple, so
    on `held_by_other` and `not_yours` they name the incumbent, not the caller.
    """
    r = dict(row) if row is not None else {}
    now = t.utc_now()
    out: dict[str, Any] = {
        "outcome": outcome,
        "entity_id": entity_id,
        "kind": kind if kind is not None else r.get("kind"),
        "holder": r.get("holder"),
        "holder_kind": r.get("holder_kind"),
        "holder_repo": r.get("holder_repo"),
        "claim_id": r.get("claim_id"),
        "claimed_at": r.get("claimed_at"),
        "renewed_at": r.get("renewed_at"),
        "touch_count": r.get("touch_count"),
        "held_seconds": _elapsed(r.get("claimed_at"), now),
        "idle_seconds": _elapsed(r.get("renewed_at"), now),
        "projected": r.get("projected_to") is not None,
        "projected_to": r.get("projected_to"),
        "prev_status": r.get("prev_status"),
    }
    out.update(extra)
    return out


#: Contention classification lives in db — connect() itself can meet it, so it is
#: not a claims-specific concern. Kept as a module-local name for readability.
_is_contention = db.is_contention


def _undetermined(exc: sqlite3.OperationalError, *, entity_id: str) -> dict[str, Any]:
    if not _is_contention(exc):
        raise exc  # a real error is NEVER masked as contention
    try:
        kind = entities.EntityRef.of(entity_id).kind.name
    except ValueError:
        kind = None
    return _response(
        "undetermined",
        entity_id=entity_id,
        kind=kind,
        row=None,
        reason="database_busy",
        retry_after_ms=250,
        detail=str(exc),
    )


# --- core layer: emits statements, NEVER commits, NEVER opens a transaction ---


def _claim_core(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    holder: str,
    holder_kind: str = "agent",
    holder_repo: str | None = None,
    note: str = "",
    project: bool = True,
    allow_terminal: bool = False,
) -> dict[str, Any]:
    """Claim an entity. Emits statements into the caller's transaction."""
    ref = entities.EntityRef.of(entity_id)  # ValueError on a bad format
    ref.require(conn)  # KeyError if the entity does not exist

    # The terminal guard is unconditional — gated only on allow_terminal, never on
    # projection. Requirements never project but their terminal set is populated,
    # so the guard is live for them from day one.
    current = ref.status(conn)
    if current in ref.kind.terminal and not allow_terminal:
        return _response(
            "entity_terminal",
            entity_id=entity_id,
            kind=ref.kind.name,
            row=_live_claim(conn, entity_id),
            current_status=current,
        )

    busy = ref.kind.busy_status  # None == this kind does not project
    do_project = project and busy is not None

    now = t.utc_now()
    claim_id = _next_claim_id(conn)
    cur = conn.execute(
        f"""INSERT INTO entity_claims
                (claim_id, entity_id, kind, holder, holder_kind, holder_repo,
                 claimed_at, renewed_at, touch_count, note, prev_status, projected_to,
                 released_at, released_by, release_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL, NULL, NULL, NULL)
            ON CONFLICT(entity_id) WHERE released_at IS NULL DO UPDATE SET
                   renewed_at  = excluded.renewed_at,
                   touch_count = entity_claims.touch_count + 1,
                   note        = CASE WHEN excluded.note <> '' THEN excluded.note
                                      ELSE entity_claims.note END
             WHERE entity_claims.holder      =  excluded.holder
               AND entity_claims.holder_kind =  excluded.holder_kind
               AND entity_claims.holder_repo IS excluded.holder_repo
            RETURNING {_ROW_COLS}, (touch_count = 1) AS was_new""",  # noqa: S608 (constant column list)
        (claim_id, entity_id, ref.kind.name, holder, holder_kind, holder_repo, now, now, note),
    )
    written = cur.fetchone()  # NEVER rowcount — this statement carries RETURNING

    if written is None:
        # The upsert's WHERE refused: a live claim exists under a different triple.
        return _response(
            "held_by_other",
            entity_id=entity_id,
            kind=ref.kind.name,
            row=_live_claim(conn, entity_id),
        )

    row = dict(written)
    was_new = bool(row.pop("was_new"))
    if not was_new:
        return _response("already_mine", entity_id=entity_id, kind=ref.kind.name, row=row)

    extra: dict[str, Any] = {}
    if do_project:
        moved = ref.set_status(conn, new_status=busy, expected=current)
        conn.execute(
            "UPDATE entity_claims SET prev_status = ?, projected_to = ? WHERE claim_id = ?",
            (current, busy if moved else None, row["claim_id"]),
        )
        row["prev_status"] = current
        row["projected_to"] = busy if moved else None
        if not moved:
            # The status moved between the guard read and the projection. No known
            # execution path holds the write lock from BEGIN IMMEDIATE, but leaving
            # prev_status set would make release restore a value that was never
            # current, so it is checked rather than assumed.
            extra["projection"] = "raced"

    return _response("claimed", entity_id=entity_id, kind=ref.kind.name, row=row, **extra)


def _release_core(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    holder: str,
    holder_kind: str = "agent",
    holder_repo: str | None = None,
    restore_status: bool = True,
    reason: str = "explicit",
    released_by: str | None = None,
) -> dict[str, Any]:
    """Release an entity. Emits statements into the caller's transaction.

    Authorization is the FULL NULL-safe holder triple, matching what claim
    compares. Matching on `holder` alone let a same-text holder of another kind or
    repo release someone else's claim.
    """
    now = t.utc_now()
    cur = conn.execute(
        f"""UPDATE entity_claims
               SET released_at = ?, released_by = ?, release_reason = ?
             WHERE entity_id   =  ?
               AND holder      =  ?
               AND holder_kind =  ?
               AND holder_repo IS ?
               AND released_at IS NULL
            RETURNING {_ROW_COLS}, released_at, released_by, release_reason""",  # noqa: S608 (constant column list)
        (now, released_by or holder, reason, entity_id, holder, holder_kind, holder_repo),
    )
    released = cur.fetchone()  # NEVER rowcount — this statement carries RETURNING

    if released is None:
        live = _live_claim(conn, entity_id)
        if live is not None:
            return _response("not_yours", entity_id=entity_id, row=live)
        return _response("not_claimed", entity_id=entity_id)

    row = dict(released)
    restored = False
    current = None
    if row.get("projected_to") is not None and restore_status:
        ref = entities.EntityRef.of(entity_id)
        restored = ref.set_status(conn, new_status=row["prev_status"], expected=row["projected_to"])
        current = ref.status(conn)

    return _response(
        "released",
        entity_id=entity_id,
        row=row,
        status_restored=restored,
        current_status=current,
    )


# --- public layer: transaction-managing, contention-classifying ---------------


def claim(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    holder: str,
    holder_kind: str = "agent",
    holder_repo: str | None = None,
    note: str = "",
    project: bool = True,
    allow_terminal: bool = False,
) -> dict[str, Any]:
    """Claim an entity for a holder. Idempotent for the same holder triple.

    Outcomes: claimed | already_mine | held_by_other | entity_terminal | undetermined.
    `undetermined` means the database was too contended to tell whether the claim
    was made — re-issue the identical call, which converges on already_mine.

    Requires a connection with no open transaction (see the module docstring).
    """
    try:
        with db.txn(conn):
            return _claim_core(
                conn,
                entity_id=entity_id,
                holder=holder,
                holder_kind=holder_kind,
                holder_repo=holder_repo,
                note=note,
                project=project,
                allow_terminal=allow_terminal,
            )
    except sqlite3.OperationalError as exc:
        return _undetermined(exc, entity_id=entity_id)


def release(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    holder: str,
    holder_kind: str = "agent",
    holder_repo: str | None = None,
    restore_status: bool = True,
    reason: str = "explicit",
) -> dict[str, Any]:
    """Release a claim. Authorized on the full holder triple.

    Outcomes: released | not_yours | not_claimed | undetermined.
    Never resurrects finished work: the status is restored only if it still holds
    the value the claim projected.

    Requires a connection with no open transaction (see the module docstring).
    """
    try:
        with db.txn(conn):
            return _release_core(
                conn,
                entity_id=entity_id,
                holder=holder,
                holder_kind=holder_kind,
                holder_repo=holder_repo,
                restore_status=restore_status,
                reason=reason,
            )
    except sqlite3.OperationalError as exc:
        return _undetermined(exc, entity_id=entity_id)


# --- read layer: no transaction, no writes. LIVE CLAIMS ONLY. ----------------


def who_holds(conn: sqlite3.Connection, *, entity_id: str) -> dict[str, Any] | None:
    """The live claim on an entity, decorated, or None."""
    row = _live_claim(conn, entity_id)
    return _decorate(row, t.utc_now()) if row else None


def held_by(conn: sqlite3.Connection, *, holder: str) -> dict[str, Any]:
    """Everything a holder currently holds. Indexed point query, not a fold."""
    rows = conn.execute(
        f"SELECT {_ROW_COLS} FROM entity_claims "  # noqa: S608 (constant column list)
        "WHERE holder = ? AND released_at IS NULL ORDER BY claimed_at",
        (holder,),
    ).fetchall()
    now = t.utc_now()
    claims = [_decorate(dict(r), now) for r in rows]
    return {"holder": holder, "count": len(claims), "claims": claims}


def list_claims(
    conn: sqlite3.Connection,
    *,
    kind: str | None = None,
    holder: str | None = None,
    holder_kind: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Live claims, filtered. Released rows are never returned — history querying
    is deferred; the rows are retained, only the surface is absent."""
    rows = conn.execute(
        f"SELECT {_ROW_COLS} FROM entity_claims "  # noqa: S608 (constant column list)
        "WHERE (:kind IS NULL OR kind = :kind) "
        "  AND (:holder IS NULL OR holder = :holder) "
        "  AND (:holder_kind IS NULL OR holder_kind = :holder_kind) "
        "  AND released_at IS NULL "
        "ORDER BY renewed_at DESC LIMIT :limit",
        {"kind": kind, "holder": holder, "holder_kind": holder_kind, "limit": limit},
    ).fetchall()
    now = t.utc_now()
    claims = [_decorate(dict(r), now) for r in rows]
    return {"count": len(claims), "claims": claims}


# --- the terminal hook -------------------------------------------------------


def _auto_release_on_terminal(
    conn: sqlite3.Connection, entity_id: str, old_status: str | None, new_status: str
) -> None:
    """Close the live claim when an entity reaches a terminal status.

    Runs inside the domain update's open transaction, so it calls the CORE layer:
    the status change and the release commit together, or neither lands.
    """
    ref = entities.EntityRef.of(entity_id)
    if new_status not in ref.kind.terminal:
        return
    live = _live_claim(conn, entity_id)
    if live is None:
        return
    _release_core(
        conn,
        entity_id=entity_id,
        holder=live["holder"],  # the live row's OWN triple, so authorization
        holder_kind=live["holder_kind"],  # can never mismatch
        holder_repo=live["holder_repo"],
        restore_status=False,  # the entity is FINISHED; never restore
        reason=f"terminal:{new_status}",
        released_by="hook:status_change",
    )


# --- MCP tools ---------------------------------------------------------------


def register_tools(mcp, conn_factory) -> None:
    """Register claim tools on the given MCP server."""

    @mcp.tool()
    def claims_claim(
        entity_id: str,
        holder: str,
        holder_kind: str = "agent",
        holder_repo: str | None = None,
        note: str = "",
        project: Annotated[bool, Field(strict=True)] = True,
        allow_terminal: Annotated[bool, Field(strict=True)] = False,
    ) -> dict[str, Any]:
        """Claim a finding or requirement so parallel agents do not collide.

        Args:
            entity_id: CB-N, FR-N or NFR-N
            holder: who is claiming — a branch name, agent id, or person
            holder_kind: branch | agent | human
            holder_repo: absolute path of the repo owning the branch, if any
            note: free-text reason, kept on renewal unless replaced
            project: also move a finding to in_progress (requirements never project)
            allow_terminal: claim even if the entity is already resolved

        Returns:
            outcome: claimed | already_mine | held_by_other | entity_terminal | undetermined.
            On held_by_other the holder fields name the INCUMBENT.
            On undetermined, re-issue the identical call — it converges on already_mine.
        """
        with conn_factory() as conn:
            return claim(
                conn,
                entity_id=entity_id,
                holder=holder,
                holder_kind=holder_kind,
                holder_repo=holder_repo,
                note=note,
                project=project,
                allow_terminal=allow_terminal,
            )

    @mcp.tool()
    def claims_release(
        entity_id: str,
        holder: str,
        holder_kind: str = "agent",
        holder_repo: str | None = None,
        restore_status: Annotated[bool, Field(strict=True)] = True,
        reason: str = "explicit",
    ) -> dict[str, Any]:
        """Release a claim. Authorized on the full (holder, holder_kind, holder_repo)
        triple — pass exactly what you claimed with.

        Returns:
            outcome: released | not_yours | not_claimed | undetermined.
            A projected status is restored only if it still holds the projected
            value, so finished work is never resurrected.
        """
        with conn_factory() as conn:
            return release(
                conn,
                entity_id=entity_id,
                holder=holder,
                holder_kind=holder_kind,
                holder_repo=holder_repo,
                restore_status=restore_status,
                reason=reason,
            )

    @mcp.tool()
    def claims_who_holds(entity_id: str) -> dict[str, Any]:
        """Who currently holds this entity, if anyone."""
        with conn_factory() as conn:
            row = who_holds(conn, entity_id=entity_id)
        return {"held": row is not None, "entity_id": entity_id, "claim": row}

    @mcp.tool()
    def claims_held_by(holder: str) -> dict[str, Any]:
        """Everything a given holder currently holds."""
        with conn_factory() as conn:
            return held_by(conn, holder=holder)

    @mcp.tool()
    def claims_list(
        kind: str | None = None,
        holder: str | None = None,
        holder_kind: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List live claims, optionally filtered by kind, holder or holder kind."""
        with conn_factory() as conn:
            return list_claims(conn, kind=kind, holder=holder, holder_kind=holder_kind, limit=limit)


# --- CLI ---------------------------------------------------------------------
#
# Exit codes are the API for shell callers, so a `set -euo pipefail` script can
# branch without parsing output:
#   0  claimed / already_mine / released / not_claimed / held (who-holds) / success
#   1  error (bad id, no such entity, no tracker)
#   3  held_by_other / not_yours / not held
#   4  entity_terminal
#   5  undetermined — retry

_EXIT = {
    "claimed": 0,
    "already_mine": 0,
    "released": 0,
    "not_claimed": 0,
    "held_by_other": 3,
    "not_yours": 3,
    "entity_terminal": 4,
    "undetermined": 5,
}


def _holder_desc(r: dict[str, Any]) -> str:
    repo = r.get("holder_repo")
    return f"{r['holder']} ({r['holder_kind']}{', ' + repo if repo else ''})"


def _emit(result: dict[str, Any], line: str, as_json: bool) -> None:
    print(json.dumps(result, indent=2) if as_json else line)
    sys.exit(_EXIT.get(result["outcome"], 1))


def _connect_or_undetermined(entity_id: str, as_json: bool) -> sqlite3.Connection:
    """Open the tracker, reporting contention as `undetermined` rather than a
    traceback.

    db.connect() WRITES during schema initialization — merge.ensure_schema does an
    `INSERT OR IGNORE` — so a database held by another writer for longer than
    busy_timeout raises before any claim code runs. The shell contract promises
    exit 5 for contention wherever it arises, so it is classified here too.
    """
    try:
        return db.connect()
    except sqlite3.OperationalError as exc:
        if not _is_contention(exc):
            raise
        _emit(
            _undetermined(exc, entity_id=entity_id),
            f"UNDETERMINED {entity_id}: database busy, retry in 250ms",
            as_json,
        )


def _cmd_claims_claim(args):
    conn = _connect_or_undetermined(args.id, args.json)
    try:
        result = claim(
            conn,
            entity_id=args.id,
            holder=args.holder,
            holder_kind=args.holder_kind,
            holder_repo=args.repo,
            note=args.note or "",
            project=not args.no_project,
            allow_terminal=args.allow_terminal,
        )
    except (ValueError, KeyError) as e:
        print(f"codebugs: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    o = result["outcome"]
    if o == "claimed":
        line = f"claimed {args.id} as {_holder_desc(result)} touch={result['touch_count']}"
    elif o == "already_mine":
        line = (
            f"already yours: {args.id} held by {_holder_desc(result)} touch={result['touch_count']}"
        )
    elif o == "held_by_other":
        line = f"REFUSED {args.id}: held by {_holder_desc(result)} since {result['claimed_at']}"
    elif o == "entity_terminal":
        line = (
            f"REFUSED {args.id}: already {result['current_status']} "
            "(use --allow-terminal to claim anyway)"
        )
    else:
        line = f"UNDETERMINED {args.id}: database busy, retry in {result['retry_after_ms']}ms"
    _emit(result, line, args.json)


def _cmd_claims_release(args):
    conn = _connect_or_undetermined(args.id, args.json)
    try:
        result = release(
            conn,
            entity_id=args.id,
            holder=args.holder,
            holder_kind=args.holder_kind,
            holder_repo=args.repo,
            restore_status=not args.no_restore,
            reason=args.reason,
        )
    except (ValueError, KeyError) as e:
        print(f"codebugs: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    o = result["outcome"]
    if o == "released":
        if result["prev_status"] and result["status_restored"]:
            line = (
                f"released {args.id} (was {result['projected_to']}, "
                f"restored to {result['prev_status']})"
            )
        else:
            line = f"released {args.id}"
    elif o == "not_claimed":
        line = f"nothing to release for {args.id}"
    elif o == "not_yours":
        line = f"REFUSED {args.id}: held by {_holder_desc(result)}"
    else:
        line = f"UNDETERMINED {args.id}: database busy, retry in {result['retry_after_ms']}ms"
    _emit(result, line, args.json)


def _cmd_claims_who_holds(args):
    conn = db.connect()
    try:
        row = who_holds(conn, entity_id=args.id)
    except ValueError as e:
        print(f"codebugs: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    if args.json:
        print(json.dumps({"held": row is not None, "entity_id": args.id, "claim": row}, indent=2))
    elif row:
        print(
            f"{args.id} held by {_holder_desc(row)} since {row['claimed_at']} "
            f"idle {row['idle_seconds']}s"
        )
    else:
        print(f"{args.id} not held")
    sys.exit(0 if row else 3)


def _cmd_claims_list(args):
    conn = db.connect()
    try:
        result = list_claims(conn, kind=args.kind, holder=args.holder, holder_kind=args.holder_kind)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.format == "ids":
        # Bare ids, one per line, nothing else — this is what lets a shell loop
        # drive `release` without parsing. Empty output and rc 0 when none match.
        for c in result["claims"]:
            print(c["entity_id"])
    else:
        from codebugs import fmt

        rows = [
            {
                "ENTITY": c["entity_id"],
                "HOLDER": c["holder"],
                "KIND": c["holder_kind"],
                "REPO": c["holder_repo"] or "",
                "IDLE_S": str(c["idle_seconds"]),
            }
            for c in result["claims"]
        ]
        print(fmt.format_table(rows, ["ENTITY", "HOLDER", "KIND", "REPO", "IDLE_S"]))
    sys.exit(0)


def register_cli(sub, commands) -> None:
    """Register claim CLI subcommands."""
    p = sub.add_parser("claim", help="Claim a finding or requirement")
    p.add_argument("id", help="Entity ID (CB-N, FR-N, NFR-N)")
    p.add_argument("--holder", required=True, help="Branch name, agent id, or person")
    p.add_argument("--holder-kind", choices=["branch", "agent", "human"], default="agent")
    p.add_argument("--repo", help="Absolute path of the repo owning the branch")
    p.add_argument("--note", help="Why it is claimed")
    p.add_argument("--no-project", action="store_true", help="Do not move the entity's status")
    p.add_argument("--allow-terminal", action="store_true", help="Claim even if resolved")
    p.add_argument("--json", action="store_true")
    commands["claim"] = _cmd_claims_claim

    p = sub.add_parser("release", help="Release a claim")
    p.add_argument("id", help="Entity ID")
    p.add_argument("--holder", required=True, help="Must match the claiming holder")
    p.add_argument("--holder-kind", choices=["branch", "agent", "human"], default="agent")
    p.add_argument("--repo", help="Must match the claiming repo")
    p.add_argument("--no-restore", action="store_true", help="Leave the status as it is")
    p.add_argument("--reason", default="explicit")
    p.add_argument("--json", action="store_true")
    commands["release"] = _cmd_claims_release

    p = sub.add_parser("who-holds", help="Who holds this entity")
    p.add_argument("id", help="Entity ID")
    p.add_argument("--json", action="store_true")
    commands["who-holds"] = _cmd_claims_who_holds

    p = sub.add_parser("claims", help="List live claims")
    p.add_argument("--holder")
    p.add_argument("--kind", help="Entity kind: finding | requirement")
    p.add_argument("--holder-kind", choices=["branch", "agent", "human"])
    p.add_argument("--format", choices=["ids", "table"], default="table")
    p.add_argument("--json", action="store_true")
    commands["claims"] = _cmd_claims_list


db.register_schema("claims", ensure_schema)
db.register_tool_provider("claims", register_tools)
db.register_cli_provider("claims", register_cli)
db.register_status_change_hook("claims_auto_release", _auto_release_on_terminal)
