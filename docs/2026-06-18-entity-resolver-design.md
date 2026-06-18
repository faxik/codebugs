# Entity Resolver — Deep Module Design (RFC)

**Status:** adversarially reviewed (8/10 post-fix); ready to implement
**Date:** 2026-06-18
**Candidate:** #1 from improve-codebase-architecture exploration

> Line citations below are directional (verified against HEAD 2026-06-18 ±a few lines);
> re-anchor before implementation.

## Problem

Polymorphic entity resolution — "given an opaque entity ID, what kind is it, does it
exist, what is its status/description, is it terminal, and which table/columns back it" —
is smeared across the codebase:

- `types.py:56-64` — `ENTITY_TABLES` map and `TERMINAL_STATUSES` map (data only).
- `blockers.py:49-80` — `_detect_entity_type` (regex `CB-`→finding, `N?FR-`→requirement),
  `_get_entity_field` (raw `SELECT {field} FROM {table} WHERE id=?`), `_entity_exists`,
  `_get_entity_status`, `_get_entity_description`.
- `blockers.py:102-106` — terminal check `status in TERMINAL_STATUSES[type]`, inside the
  `trigger_type == "entity_resolved"` branch.
- `blockers.py:459-464` — deferred-query routing: per-kind `(table, sort_col, result_key)`.
- `milestones.py:154-168` — `_validate_item_ref`, a 2-arm dispatch (`bug`→findings,
  `requirement`→requirements) that does its own `SELECT 1 FROM ...` existence checks;
  `milestones.py:753-758` — a `linked_frs` loop validator returning a reason string.
- `findings.py:605-606` & `reqs.py:657` — read terminal sets via the **`blockers.` namespace**
  (`blockers.TERMINAL_STATUSES` / `blockers.ENTITY_FINDING`) to decide cascade-unblock.

**The actual coupling (corrected — see Appendix):** `blockers.TERMINAL_STATUSES` and
`blockers.ENTITY_*` are *re-exports* of `types` constants (`blockers.py:11`). So findings/reqs
reading them through the `blockers.` namespace is name-laundering a `types` constant — a cosmetic
smell, repaired by a one-line repoint, **not** the justification for this module. The real,
load-bearing motivation is: (1) the ID→kind / table / column / terminal knowledge is duplicated
across the five sites above, so a 3rd entity kind needs edits in ≥4 files; and (2) `blockers`
cannot be unit-tested without live findings+requirements rows. (findings/reqs do genuinely import
`blockers` — for `get_unblocked_by` / `query_deferred_entities` / `get_deferred_counts` — but that
behavioral dependency is out of scope here.)

## Proposed interface (Design C, in a new `entities.py`)

A frozen value-object `EntityRef` plus a static registry, in a **new `entities.py`** module
(importing `types` for the constants). The connection is injected per call.

**Why `entities.py` and not `types.py`:** `types.py` is contractually zero-coupling
(`types.py:1-5` docstring; CLAUDE.md "no module reaches into another module's tables"). It owns
*name maps*, not *query logic*. Putting `SELECT ... FROM findings` into `types.py` would preserve
zero-*import* but destroy zero-*coupling* — it would hardcode two domains' table/column shapes
into the leaf everyone imports. `entities.py` is the honest home: it depends on `types`, holds the
one sanctioned polymorphic cross-table read (relocated from `blockers._get_entity_field`,
`blockers.py:60-68`), and keeps `types.py` pristine.

```python
# entities.py
import re, sqlite3
from dataclasses import dataclass
from typing import Any
from codebugs import types as t

@dataclass(frozen=True)
class EntityKind:
    name: str                      # "finding" | "requirement"   (== blockers item_type)
    table: str                     # "findings" | "requirements" (frozen-const identifier)
    id_pattern: re.Pattern
    terminal: frozenset[str]
    sort_col: str                  # "severity" | "priority"     (deferred-query ordering)
    result_key: str                # "findings" | "requirements"
    readable_cols: frozenset[str]  # per-kind: {"id","status","description","severity"|"priority"}

# single source of truth; +1 kind = +1 entry. terminal sets come from types (not redefined).
ENTITY_KINDS: tuple[EntityKind, ...] = (
    EntityKind("finding", "findings", re.compile(r"^CB-\d+"), t.FINDING_TERMINAL,
               "severity", "findings", frozenset({"id","status","description","severity"})),
    EntityKind("requirement", "requirements", re.compile(r"^N?FR-\d+"), t.REQUIREMENT_TERMINAL,
               "priority", "requirements", frozenset({"id","status","description","priority"})),
)
_BY_NAME = {k.name: k for k in ENTITY_KINDS}

# Identifier-shape guard for every interpolated identifier (table / sort_col / readable cols).
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def entity_kind(name: str) -> EntityKind: ...   # _BY_NAME[name]; KeyError on miss (type-driven entry)

@dataclass(frozen=True)
class EntityRef:
    id: str
    kind: EntityKind

    @classmethod
    def of(cls, entity_id: str) -> "EntityRef": ...   # detect kind from ID prefix; ValueError on unknown
    # NOTE: no `of_kind` public classmethod. Type-driven callers use entity_kind(name) and build
    # EntityRef(id, kind) explicitly, or use the type-driven free helpers below. This avoids the
    # silent-wrong-ref footgun of constructing a ref whose declared kind contradicts its id.

    def exists(self, conn) -> bool: ...
    def status(self, conn) -> str | None: ...
    def description(self, conn) -> str | None: ...
    def is_resolved(self, conn) -> bool: ...          # status in kind.terminal (False if missing)
    def require(self, conn) -> None: ...              # KeyError if absent (no chaining return)
    def field(self, conn, *, name: str) -> Any | None: ...  # per-kind allowlisted single-column read
```

### Usage (real call sites rewritten)

```python
# blockers.add_blocker (was ~138-140) — id-driven
EntityRef.of(item_id).require(conn)

# blockers.is_blocker_satisfied (was 102-106) — REWRITE STAYS INSIDE THE EXISTING GUARD.
# blocked_by_type is NULL for date/manual triggers (schema blockers.py:21-22), so the
# entity lookup must never run outside this branch.
if blocker["trigger_type"] == "entity_resolved":
    kind = entity_kind(blocker["blocked_by_type"])
    return EntityRef(blocker["blocked_by"], kind).is_resolved(conn)

# blockers deferred-query routing (was 459-464) — type-driven; sort_col/table are frozen consts
k = entity_kind(entity_type)
rows = conn.execute(
    f"SELECT * FROM {k.table} WHERE id IN ({ph}) ORDER BY {k.sort_col}, created_at DESC LIMIT ? OFFSET ?",
    [...]).fetchall()
return {..., k.result_key: [db.row_to_dict(r) for r in rows]}

# findings.py cascade-unblock (was 605-608) — drop the blockers.-namespaced constants
if status and EntityRef.of(finding_id).is_resolved(conn):
    unblocked = blockers.get_unblocked_by(conn, finding_id, ENTITY_FINDING)
```

**Milestones is intentionally out of scope.** Its `item_kind` vocabulary is
`("bug", "requirement", "external")` (`milestones.py:21`), which is *not* the `EntityKind`
vocabulary (`finding`/`requirement`), and `external` has no backing table. `_validate_item_ref`
could adopt the resolver later via an explicit `{"bug": "finding", "requirement": "requirement"}`
alias map at its boundary (leaving `external` on its current skip path), but this RFC does not
migrate it — the fit is poor and the payoff small.

### What it hides
ID→kind regex detection; table/column name resolution; terminal-status semantics
("missing ⇒ not resolved"); deferred-query routing triple; SQL-injection allowlisting.

## Dependency strategy

**Category: in-process (local SQLite).** Fully deepenable — merge the lookup into one object,
test against a real in-memory DB seeded with findings + requirements rows.

- **New `entities.py`, no circular imports.** `entities.py` imports `types` (zero-dep leaf) and
  reads findings/requirements rows via raw SQL on the *injected* connection — it never imports
  those domain modules. `blockers`/`findings`/`reqs` import `entities`; nothing points back. The
  polymorphic cross-table read is not new — it relocates `blockers._get_entity_field`
  (`blockers.py:60-68`) into one dedicated module. `types.py` stays untouched and zero-coupling;
  `ENTITY_TABLES`/`TERMINAL_STATUSES` remain its canonical hand-written constants (consumed, not
  redefined, by `entities.py`).
- `findings`/`reqs` stop reading terminal sets through the `blockers.` namespace.
- **SQL-injection safety — three interpolation surfaces, two guards:**
  1. *`field()` read column* — caller-supplied `name` must be in the kind's `readable_cols`
     (per-kind, so it also guarantees schema-validity: `field(name="priority")` is rejected on a
     finding rather than producing `SELECT priority FROM findings`).
  2. *`table`* and 3. *`sort_col`* — attributes of frozen `EntityKind` constants, never caller
     input. A construction-time test asserts every `table` / `sort_col` / `readable_cols` member
     matches `_SAFE_IDENT`.
  Row **values** (ids, limit, offset) are always bound via `?`.

### Rejected alternative: Ports & Adapters (Design D)
D inverts the dependency — each domain supplies an `EntitySource` adapter, resolver holds no SQL,
`blockers` tests use a fake adapter with no schema. It uniquely retires the "no module reaches
into another's tables" debt and gives the best test story. **Rejected** for now (confirmed by the
adversarial review): ~40 lines of port+registry+adapter machinery and lifecycle coupling is
over-built for 2 SQL-backed entity kinds with no non-SQL storage on the horizon. The honest middle
— a SQL-bearing resolver in its own `entities.py` — preserves the zero-coupling invariant D was
defending without D's indirection. Revisit D only if a future kind needs non-SQL backing.

## Testing strategy

- **New boundary tests** (`tests/test_entities.py`, in-memory DB):
  - `EntityRef.of` detects finding/requirement/unknown-format (ValueError).
  - `exists`/`status`/`description`/`is_resolved`/`require` against seeded rows + missing rows.
  - terminal semantics per kind; `is_resolved` False when entity missing.
  - **NULL-type blocker path**: a `date`/`manual` blocker with `blocked_by_type IS NULL` flows
    through `is_blocker_satisfied` without ever constructing an `EntityRef` (regression guard for
    the original whole-function-rewrite bug).
  - deferred-query routing returns correct `result_key`/ordering per kind.
  - SQL-injection / schema-validity guard: `field(name=...)` rejects any column not in the kind's
    `readable_cols` (so `field(name="priority")` raises on a finding, never hits SQL); every
    registry `table` / `sort_col` / `readable_cols` member matches `_SAFE_IDENT`.
  - mutual-exclusivity: no two `id_pattern`s match the same ID (cheap insurance for future kinds).
- **Old tests to delete/replace:** the `_get_entity_*` / `_detect_entity_type` branch tests in
  `test_blockers.py` that exercise the soon-deleted private helpers.
- **Test env:** in-memory SQLite with findings + requirements schemas (no git, no `db.connect()`).

## Implementation recommendations (durable)

- The resolver **owns**: ID→kind inference, the per-kind descriptor `(table, terminal, sort_col,
  result_key, readable_cols)`, the single per-kind-allowlisted dynamic-SQL read, and the
  "missing entity ⇒ not resolved" rule. It does **not** own trigger-type / lifecycle dispatch —
  that stays with `blockers`.
- It **hides**: every table/column literal and the regex prefixes from all callers.
- It **exposes**: `EntityRef.of(id)` (id-driven) + `entity_kind(name)` (type-driven, for callers
  holding a DB-authoritative type and for the deferred-query router that has no row to detect
  from). No `of_kind` — type-driven callers build `EntityRef(id, entity_kind(name))` explicitly.
- **Migration:** add `entities.py` to `_ensure_modules_loaded()` only if it ends up registering
  anything (it owns no schema, so likely not). Delete `blockers._detect_entity_type` /
  `_get_entity_*`; repoint `blockers` call sites to `entities`; repoint `findings`/`reqs` terminal
  reads from `blockers.TERMINAL_STATUSES` → `types.TERMINAL_STATUSES` (one-liner, independent of
  the rest). Leave `types.ENTITY_TABLES`/`TERMINAL_STATUSES` as canonical hand-written constants.
  Milestones is **not** migrated by this RFC.

---

## Appendix: Adversarial Review Corrections (2026-06-18)

A 3-agent adversarial review (adversary → defender → judge) stress-tested the v1 draft against
the codebase. Judge score: **6/10 draft → 8/10 after the fixes below.** Central architecture
ruling: **stay Design C but relocate to a new `entities.py`** — neither pure-C-in-`types.py`
(violates the zero-coupling invariant) nor full Design D (over-built for 2 SQL kinds).

| # | Finding (judge severity) | Correction applied |
|---|--------------------------|--------------------|
| 1 | `is_blocker_satisfied` rewrite dropped the `trigger_type=="entity_resolved"` guard → crash on NULL `blocked_by_type` for date/manual blockers (SERIOUS) | Rewrite now lives *inside* the guard; added NULL-type regression test |
| 2 | Raw SQL placed in `types.py` destroys its zero-coupling invariant (SERIOUS) | `EntityRef` moved to new `entities.py`; `types.py` stays data-only |
| 3 | Milestones example broken: `item_kind` is `bug`/`requirement`/`external`, not `finding`; `external` is tableless (SERIOUS) | Milestones removed from scope; documented as a poor fit with an optional future alias map |
| 4 | Three identifier-interpolation surfaces (`field`/`table`/`sort_col`) collapsed under one ambiguous `_ALLOWED_COLS`; `_SAFE_IDENT` referenced but undefined (WEAKNESS) | Stated all three surfaces + two guards; `_SAFE_IDENT` now defined |
| 5 | Stated motivation "findings/reqs depend on blockers for constants" was false — they're `types` re-exports (SERIOUS→reframed) | Problem statement corrected; real motivation (duplication + testability) foregrounded |
| 6 | Global `_ALLOWED_COLS` couldn't express per-kind schema (severity≠priority) (WEAKNESS) | `readable_cols` is now per-`EntityKind`; doubles as schema-validity guard |
| 7 | `of_kind` redundant + silent-wrong-ref footgun; `require()→self` chaining is stateless ceremony (WEAKNESS/NITPICK) | Dropped `of_kind` (use `entity_kind(name)` + explicit ctor); `require()` returns `None` |
| 8 | "milestones 158/162/755 duplicate" mischaracterized; line citations off-by-a-few (NITPICK) | Problem statement reworded; citation caveat added at top |

**Dismissed:** the regex `^N?FR-\d+` is correct (matches `FR-`/`NFR-`); the "`import re` causes
import-order bugs" concern (stdlib, no cycle — and moot once `re` lives in `entities.py`). All
three of the adversary's "FATAL" labels were downgraded to SERIOUS by the judge: each is a
localized, in-place fix, not an architectural dead end.
