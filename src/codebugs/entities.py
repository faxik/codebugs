"""Polymorphic entity resolution — the single deep module that knows how to map an
opaque entity ID (CB-N, FR-N, NFR-N) to its kind, table, status, and terminal state.

Owns the one sanctioned cross-table read over `findings` / `requirements`. Depends only
on `types` (constants) and an injected sqlite3.Connection — it never imports the findings
or reqs domain modules, so there is no circular-import risk. Adding a new entity kind is a
single entry in ``ENTITY_KINDS``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from codebugs import types as t


@dataclass(frozen=True)
class EntityKind:
    """Declarative descriptor for one resolvable entity kind.

    Every field of this class that reaches SQL as an IDENTIFIER — ``table``,
    ``sort_col``, and each member of ``readable_cols`` — is validated in
    ``__post_init__``, so a malformed kind dies where it is declared instead of
    inside a query. That is what lets the ``# noqa: S608`` justifications on the
    f-string statements below be structural rather than a promise (CB-22).

    Every OTHER field is unvalidated because it never becomes SQL text: it is
    either bound as a parameter, compared in Python, or used outside SQL entirely.
    A new field needs validation here if and only if it is destined for an
    f-string. (Today: ``name`` binds in ``claims.py``, ``busy_status`` and
    ``sort_vocabulary`` bind, ``terminal`` compares, ``result_key`` is a JSON
    envelope key, ``id_pattern`` never touches the database.)
    """

    name: str  # == blockers item_type, e.g. "finding"
    table: str  # frozen-constant identifier, never caller input
    id_pattern: re.Pattern[str]
    terminal: frozenset[str]
    sort_col: str  # deferred-query ordering column
    # Declared precedence of sort_col, most important first, or None when sort_col
    # is not a vocabulary column (an id, an integer, a timestamp) and ordering by
    # it directly is already correct.
    #
    # REQUIRED, deliberately nullable: it has no default so a new kind cannot
    # inherit one silently. sort_col is TEXT for both real kinds, and ordering by
    # it directly sorts alphabetically (CB-20) — for requirements that puts
    # `could` FIRST and `must` LAST, inverting the ranking outright. Declaring
    # "this column has no precedence" must be a decision someone made, not a
    # default they never saw.
    sort_vocabulary: tuple[str, ...] | None
    result_key: str  # JSON envelope key
    readable_cols: frozenset[str]  # per-kind allowlist for field() reads
    # Status this kind moves to while claimed. None == this kind does not project.
    # A kind declaring it MUST satisfy P1-P4 (see EntityRef.set_status).
    busy_status: str | None = None

    def __post_init__(self) -> None:
        """Refuse any identifier that must not be interpolated (CB-22).

        Assigns nothing, so it is safe on a frozen dataclass, and it runs on every
        construction path this package uses — including ``dataclasses.replace()``,
        which builds a new instance through ``__init__``. It does NOT run for
        ``object.__new__``, unpickling, or a subclass that overrides it; none of
        those occur here, and none are worth designing against.

        ``readable_cols`` is validated MEMBER BY MEMBER, not merely as a set. Its
        membership check in ``_read`` guards the CALLER's argument against the
        allowlist; nothing there guards the allowlist's own contents, so an
        unvalidated member turns the fence into a subquery-shaped hole.
        """
        interpolated = [("table", self.table), ("sort_col", self.sort_col)]
        # sorted() only so the reported member is deterministic for a set.
        interpolated += [("readable_cols member", c) for c in sorted(self.readable_cols)]
        for label, value in interpolated:
            if not t.is_sql_identifier(value):
                raise ValueError(
                    f"EntityKind({self.name!r}).{label} is not a bare column "
                    f"identifier: {value!r}"
                )

    def order_by(self) -> tuple[str, list[str]]:
        """``(fragment, params)`` ordering this kind's rows by ``sort_col``.

        Ranked by declared precedence when ``sort_vocabulary`` is set, so callers
        cannot accidentally order a vocabulary column alphabetically (CB-20). The
        params must be spliced at the fragment's textual position in the final
        statement, not prepended.

        Neither branch re-checks ``sort_col``: ``__post_init__`` already refused a
        malformed one, and a second copy of that check is what CB-22 removed.
        """
        if self.sort_vocabulary is None:
            return self.sort_col, []
        return t.rank_case_sql(self.sort_col, self.sort_vocabulary)


ENTITY_KINDS: tuple[EntityKind, ...] = (
    EntityKind(
        name=t.ENTITY_FINDING,
        table="findings",
        id_pattern=re.compile(r"^CB-\d+"),
        terminal=t.FINDING_TERMINAL,
        sort_col="severity",
        sort_vocabulary=t.SEVERITIES,
        result_key="findings",
        readable_cols=frozenset({"id", "status", "description", "severity"}),
        busy_status="in_progress",
    ),
    EntityKind(
        name=t.ENTITY_REQUIREMENT,
        table="requirements",
        id_pattern=re.compile(r"^N?FR-\d+"),
        terminal=t.REQUIREMENT_TERMINAL,
        sort_col="priority",
        sort_vocabulary=t.PRIORITIES,
        result_key="requirements",
        readable_cols=frozenset({"id", "status", "description", "priority"}),
    ),
)

_BY_NAME: dict[str, EntityKind] = {k.name: k for k in ENTITY_KINDS}


def entity_kind(name: str) -> EntityKind:
    """Resolve a kind by name (type-driven entry point). Raises KeyError on unknown name."""
    return _BY_NAME[name]


@dataclass(frozen=True)
class EntityRef:
    """A typed handle to one entity. Connection is injected per read — the ref holds no state."""

    id: str
    kind: EntityKind

    @classmethod
    def of(cls, entity_id: str) -> EntityRef:
        """Detect kind from the ID prefix (id-driven entry point)."""
        for kind in ENTITY_KINDS:
            if kind.id_pattern.match(entity_id):
                return cls(entity_id, kind)
        raise ValueError(f"Unknown entity ID format: {entity_id}. Expected CB-N, FR-N, or NFR-N.")

    def _read(self, conn: sqlite3.Connection, col: str) -> Any | None:
        """Read one allowlisted column for this entity. Raises ValueError on a column
        not declared readable for this kind (guards both SQL injection and schema validity)."""
        if col not in self.kind.readable_cols:
            raise ValueError(f"Column {col!r} is not readable for kind {self.kind.name!r}")
        row = conn.execute(
            # noqa justified structurally: `col` is a member of readable_cols and
            # `table` is a field, both validated as bare identifiers in
            # EntityKind.__post_init__ (CB-22). The check above is the allowlist;
            # __post_init__ is what makes the allowlist itself trustworthy.
            f"SELECT {col} FROM {self.kind.table} WHERE id = ?",  # noqa: S608
            (self.id,),
        ).fetchone()
        return row[col] if row else None

    def exists(self, conn: sqlite3.Connection) -> bool:
        return self._read(conn, "id") is not None

    def status(self, conn: sqlite3.Connection) -> str | None:
        return self._read(conn, "status")

    def description(self, conn: sqlite3.Connection) -> str | None:
        return self._read(conn, "description")

    def is_resolved(self, conn: sqlite3.Connection) -> bool:
        """True iff the entity exists and its status is terminal. Missing ⇒ False."""
        status = self.status(conn)
        return status is not None and status in self.kind.terminal

    def require(self, conn: sqlite3.Connection) -> None:
        """Raise KeyError if the entity does not exist."""
        if not self.exists(conn):
            raise KeyError(f"Entity not found: {self.id}")

    def field(self, conn: sqlite3.Connection, *, name: str) -> Any | None:
        """Read an arbitrary allowlisted column (escape hatch). Raises ValueError if the
        column is not in this kind's ``readable_cols``."""
        return self._read(conn, name)

    def set_status(self, conn: sqlite3.Connection, *, new_status: str, expected: str) -> bool:
        """Guarded status write — THE single sanctioned cross-table status write.

        Runs inside the caller's transaction and MUST NOT commit: the caller
        composes it with other writes. Returns True iff the row moved.

        Deliberately does NOT use RETURNING — ``rowcount`` is the correct outcome
        idiom precisely when RETURNING is absent. Deliberately does NOT fire
        status-change hooks: it is only ever called with a non-terminal status, so
        there is nothing for the terminal hook to react to and no recursion.

        A kind that declares ``busy_status`` MUST satisfy:
          P1. its table has ``id`` (TEXT PK), ``status`` (TEXT), ``updated_at`` (TEXT);
          P2. the declared value passes any CHECK on ``status`` and is CANONICAL —
              no alias resolution happens here, the string is written verbatim;
          P3. ``busy_status not in kind.terminal``;
          P4. its domain module accepts that this write bypasses its ``update_*``
              function — no note appended, no meta touched, no hook fired. The
              audit trail for a projection lives in ``entity_claims`` instead.
        P1-P3 are enforced by a test; P4 is a review obligation.
        """
        cur = conn.execute(
            # noqa justified structurally: `table` is validated as a bare identifier
            # in EntityKind.__post_init__ (CB-22), not merely assumed frozen.
            f"UPDATE {self.kind.table} SET status = ?, updated_at = ? WHERE id = ? AND status = ?",  # noqa: S608
            (new_status, t.utc_now(), self.id, expected),
        )
        return cur.rowcount == 1
