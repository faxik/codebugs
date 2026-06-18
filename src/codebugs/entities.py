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

# Every interpolated SQL identifier (table / sort_col / readable column) must match this.
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EntityKind:
    """Declarative descriptor for one resolvable entity kind."""

    name: str  # == blockers item_type, e.g. "finding"
    table: str  # frozen-constant identifier, never caller input
    id_pattern: re.Pattern[str]
    terminal: frozenset[str]
    sort_col: str  # deferred-query ordering column
    result_key: str  # JSON envelope key
    readable_cols: frozenset[str]  # per-kind allowlist for field() reads


ENTITY_KINDS: tuple[EntityKind, ...] = (
    EntityKind(
        name=t.ENTITY_FINDING,
        table="findings",
        id_pattern=re.compile(r"^CB-\d+"),
        terminal=t.FINDING_TERMINAL,
        sort_col="severity",
        result_key="findings",
        readable_cols=frozenset({"id", "status", "description", "severity"}),
    ),
    EntityKind(
        name=t.ENTITY_REQUIREMENT,
        table="requirements",
        id_pattern=re.compile(r"^N?FR-\d+"),
        terminal=t.REQUIREMENT_TERMINAL,
        sort_col="priority",
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
            f"SELECT {col} FROM {self.kind.table} WHERE id = ?",  # noqa: S608 (identifiers allowlisted)
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
