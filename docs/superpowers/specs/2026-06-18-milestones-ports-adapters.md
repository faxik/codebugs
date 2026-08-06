# Milestones refactor — Candidate A: Ports & Adapters

Internal refactor of `src/codebugs/milestones.py` (1718 lines). MCP tool +
CLI command names FROZEN. One interrelated 4-table schema. No new circular
imports — kill the existing late-import-of-blockers dodge.

The thesis: the lifecycle/capacity/triage/close-gate LOGIC is a deep module
that owns its 4 tables. Everything it currently reaches across a domain
boundary for — *does this bug/req exist*, *does this ref have an active
blocker*, *what time is it*, *propagate dismissal to the entity* — is a
**transport** concern and gets **injected** through narrow ports. Production
wires real adapters that call `findings.*` / `reqs.* `/ `blockers.*`. Tests
inject in-memory fakes and exercise the whole engine with **no findings /
reqs / blockers schema present at all**.

---

## 1. Interface signature

### File layout

```
src/codebugs/milestones/
    __init__.py        # re-exports public fns + register_* ; module-level registrations
    ports.py           # Protocols: EntityPort, BlockerPort, Clock  (zero codebugs imports)
    core.py            # MilestoneEngine — all lifecycle logic, ports injected
    schema.py          # MILESTONES_SCHEMA, ensure_schema, SEED_MILESTONES, _row_to_*
    adapters.py        # real adapters wiring findings/reqs/blockers + system clock
    server.py          # register_tools(mcp, conn_factory)  — builds engine, binds tools
    cli.py             # register_cli(sub, commands)
```

(Single-file variant works too — the seam that matters is the *port boundary*,
not the file count. Splitting just makes the dependency arrows visible.)

### ports.py — the three ports (zero-dependency, like `types.py`)

```python
from __future__ import annotations
import sqlite3
from typing import Protocol

class Clock(Protocol):
    def now(self) -> str: ...          # ISO-8601 UTC, replaces direct utc_now()

class EntityPort(Protocol):
    """Read existence + write status-propagation for the *other* domains
    (findings, requirements). Milestones never touches their tables directly."""
    def exists(self, conn: sqlite3.Connection, *, kind: str, ref: str) -> bool: ...
        # kind in ("bug","requirement"); 'external' never reaches the port
    def mark_dismissed(self, conn: sqlite3.Connection, *, kind: str, ref: str) -> None: ...
        # bug -> finding status='not_a_bug'; requirement -> req status='obsolete';
        # missing entity is swallowed (matches today's `except KeyError: pass`)

class BlockerPort(Protocol):
    def has_active_blocker(self, conn: sqlite3.Connection, *, ref: str) -> bool: ...
        # returns False on any failure (matches today's `except Exception: return False`)
```

Note all ports take `conn` — they share the milestones transaction (the
existing code already calls `update_finding(conn, ...)` and
`query_blockers(conn, ...)` on the *same* connection, including inside
`pull_next`'s `BEGIN IMMEDIATE`). Passing `conn` per-call keeps that single-
connection / single-transaction invariant; the port is a *strategy*, not a
separate datastore.

### core.py — the engine that consumes them

```python
class MilestoneEngine:
    def __init__(self, *, entity: EntityPort, blocker: BlockerPort, clock: Clock):
        self._entity = entity
        self._blocker = blocker
        self._clock = clock

    # Foundation
    def create_milestone(self, conn, *, id, kind, description, target_date=None, actor="user") -> dict: ...
    def update_milestone(self, conn, *, id, ...) -> dict: ...
    def list_milestones(self, conn, *, kind=None, state=None) -> list[dict]: ...
    def get_milestone_status(self, conn, *, id) -> dict: ...
    def add_milestone_item(self, conn, *, milestone_id, item_kind, item_ref, ...) -> dict: ...
    def move_milestone_item(self, conn, *, item_ref, to_milestone, ...) -> dict: ...
    def set_item_status(self, conn, *, item_ref, status, commit=None, ...) -> dict: ...

    # Triage
    def triage_inbox(self, conn, *, limit=50) -> list[dict]: ...
    def triage_dismiss(self, conn, *, bug_id, reason, actor="user") -> dict: ...
    def triage_promote(self, conn, *, bug_id, to_milestone, ...) -> dict: ...
    def auto_route_finding(self, conn, finding: dict) -> None:     # the post-add hook

    # Capacity / pull
    def pull_next(self, conn, *, agent_id, capacity, actor=None) -> dict | None: ...
    def release_item(self, conn, *, item_ref, status="done", commit=None, actor=None) -> dict: ...
    def get_wip_status(self, conn, *, agent_id=None) -> list[dict]: ...

    # Close-gate / branch
    def mark_branch_only(self, conn, *, item_ref, branch_name, actor="user") -> dict: ...
    def mark_integrated(self, conn, *, item_ref, commit, actor="user") -> dict: ...
    def milestone_defer(self, conn, *, item_ref, ...) -> dict: ...
    def milestone_close(self, conn, *, id, force=False, ...) -> dict: ...
    def query_audit(self, conn, *, ...) -> list[dict]: ...
```

The shared spine (`_audit`, `_validate_item_ref`, `_get_item_by_ref`,
`_milestone_exists`, `_get_milestone`, `_row_to_*`) becomes private methods /
helpers on the engine. The three port calls replace the four seam sites:

| Today (line)                                                       | Becomes |
|--------------------------------------------------------------------|---------|
| `_validate_item_ref` raw `SELECT … FROM findings/requirements` (L158-164) | `self._entity.exists(conn, kind=..., ref=...)` |
| `_eligibility_failure` raw `SELECT 1 FROM requirements` for linked_frs (L754) | `self._entity.exists(conn, kind="requirement", ref=fr)` |
| `triage_dismiss` late `import update_finding/update_requirement` (L584,L590) | `self._entity.mark_dismissed(conn, kind=..., ref=...)` |
| `_has_active_blocker` late `from codebugs import blockers` (L721) | `self._blocker.has_active_blocker(conn, ref=...)` |
| `_items_with_active_blockers` late `from codebugs import blockers` (L365) | loop over `self._blocker.has_active_blocker(...)` |
| every `utc_now()` call (~20 sites)                                  | `self._clock.now()` |

### adapters.py — real production adapters

```python
from codebugs.types import utc_now

class SystemClock:
    def now(self) -> str: return utc_now()

class CodebugsEntityAdapter:
    def exists(self, conn, *, kind, ref) -> bool:
        table = {"bug": "findings", "requirement": "requirements"}[kind]
        return conn.execute(
            f"SELECT 1 FROM {table} WHERE id = ?", (ref,)  # table from fixed allowlist, not user input
        ).fetchone() is not None
    def mark_dismissed(self, conn, *, kind, ref) -> None:
        if kind == "bug":
            from codebugs.findings import update_finding
            try: update_finding(conn, ref, status="not_a_bug")
            except KeyError: pass
        elif kind == "requirement":
            from codebugs.reqs import update_requirement
            try: update_requirement(conn, ref, status="obsolete")
            except KeyError: pass

class BlockersAdapter:
    def has_active_blocker(self, conn, *, ref) -> bool:
        from codebugs import blockers
        try:
            return bool(blockers.query_blockers(conn, item_id=ref, active_only=True).get("blockers"))
        except Exception:
            return False
```

The late imports **do not vanish** — but they collapse from 4 scattered
in-function dodges to 2 lines inside one adapter file whose entire job is "I
am allowed to import sibling domains." `core.py` imports nothing from
findings/reqs/blockers, so the cycle is structurally impossible to
reintroduce there. (`adapters.py` importing `blockers` at call-time is still
late, because `blockers` imports `db` which triggers `_ensure_modules_loaded`;
that's a `db`-bootstrap cycle, not a milestones↔blockers one, and is unchanged.)

---

## 2. Usage example

### Production wiring — `server.py`, tool names identical

```python
from codebugs.milestones.core import MilestoneEngine
from codebugs.milestones.adapters import SystemClock, CodebugsEntityAdapter, BlockersAdapter

def _build_engine() -> MilestoneEngine:
    return MilestoneEngine(
        entity=CodebugsEntityAdapter(),
        blocker=BlockersAdapter(),
        clock=SystemClock(),
    )

def register_tools(mcp, conn_factory) -> None:
    engine = _build_engine()                       # one engine, stateless across calls

    @mcp.tool(name="pull_next")                    # FROZEN name — unchanged
    def _pull_next(agent_id: str, capacity: dict[str, int] | None = None):
        cap = capacity or {"large": 1, "small": 2, "triage": 5}
        with conn_factory() as conn:
            return engine.pull_next(conn, agent_id=agent_id, capacity=cap)

    @mcp.tool(name="triage_dismiss")               # FROZEN name — unchanged
    def _triage_dismiss(bug_id: str, reason: str):
        with conn_factory() as conn:
            return engine.triage_dismiss(conn, bug_id=bug_id, reason=reason)
    # ... milestone_create, milestone_close, mark_integrated, etc. — all names verbatim
```

The post-add hook registration (was L1718) becomes:

```python
register_post_add_hook("milestones.auto_route", _build_engine().auto_route_finding)
```

Same hook name, same callback signature `(conn, finding)`.

### Test wiring — fakes, NO findings/reqs/blockers tables

```python
class FakeEntity:
    def __init__(self, known): self.known = set(known); self.dismissed = []
    def exists(self, conn, *, kind, ref): return (kind, ref) in self.known
    def mark_dismissed(self, conn, *, kind, ref): self.dismissed.append((kind, ref))

class FakeBlocker:
    def __init__(self, blocked=()): self.blocked = set(blocked)
    def has_active_blocker(self, conn, *, ref): return ref in self.blocked

class FrozenClock:
    def __init__(self, t="2026-06-18T00:00:00Z"): self.t = t
    def now(self): return self.t

def test_pull_next_skips_blocked_without_blockers_schema():
    conn = sqlite3.connect(":memory:")            # ONLY milestones tables
    conn.row_factory = sqlite3.Row
    schema.ensure_schema(conn)
    engine = MilestoneEngine(
        entity=FakeEntity({("bug", "CB-1"), ("bug", "CB-2")}),
        blocker=FakeBlocker(blocked={"CB-1"}),     # CB-1 blocked, CB-2 clear
        clock=FrozenClock(),
    )
    engine.add_milestone_item(conn, milestone_id="stream/triage", item_kind="bug", item_ref="CB-1")
    engine.add_milestone_item(conn, milestone_id="stream/triage", item_kind="bug", item_ref="CB-2")
    got = engine.pull_next(conn, agent_id="a1", capacity={"triage": 5})
    assert got["item_ref"] == "CB-2"               # blocked CB-1 skipped, NO blockers table existed
```

Today this test is impossible: `add_milestone_item` → `_validate_item_ref`
hits `SELECT … FROM findings` (no such table), and `pull_next` →
`_has_active_blocker` imports `blockers` whose `query_blockers` needs the
`blockers` table. Both are now fakes; the eligibility ladder
(`_eligibility_failure`, L731-764) is tested as pure logic.

---

## 3. What complexity it hides internally

- **The 4-bucket priority walk** (`_candidates` / `_bucket_query`, L767-797):
  security → open releases by target_date → triage → maintenance, with the
  `NULLS LAST, priority ASC, created_at ASC` ordering. Callers just say
  `pull_next`.
- **`BEGIN IMMEDIATE` atomicity** (L812-851): isolation save/restore, the
  `WHERE id=? AND status='open'` claim guard, rollback-on-empty. The port
  calls (`has_active_blocker`, `exists` for linked_frs) run *inside* this
  transaction on the same `conn` — the engine guarantees that; ports don't
  know they're in a transaction.
- **Capacity bookkeeping**: `_capacity_for` / `_upsert_capacity_increment` /
  `_decrement_capacity` (L665-716), the per-`(agent,size)` columns,
  `MAX(col-1,0)` floor, increment-on-pull / decrement-on-release pairing.
- **The close-gate predicate** (`milestone_close`, L1037-1113): three
  independent refusal reasons (unfinished / branch-only / blocked) accumulated
  into one actionable message, `force` bypass, stream-never-closes rule.
- **Auto-route schema-probe** (L198-200): the `sqlite_master` guard so raw
  `sqlite3.connect()` callers that never ran milestones' `ensure_schema`
  don't blow up inside `add_finding`. Stays in `auto_route_finding`.
- **Dismissal fan-out**: item_kind → entity-status mapping, now behind
  `EntityPort.mark_dismissed` so the *policy* (bug→not_a_bug,
  req→obsolete) lives in the adapter, the *trigger* in the engine.

---

## 4. Dependency strategy

| Dependency | Classification | Mechanism |
|---|---|---|
| `sqlite3` connection | in-process | passed as `conn` arg (unchanged) |
| `codebugs.db` registries (`register_schema`/`_tool_provider`/`_cli_provider`/`_post_add_hook`) | in-process | called at `__init__.py` module level (unchanged, required by CLAUDE.md) |
| `codebugs.types.utc_now` | local-substitutable → **port** | becomes `Clock`; real adapter delegates to `utc_now` |
| findings/reqs existence + status-propagation | cross-domain → **port** | `EntityPort`, real adapter does the SELECT + late import of `update_finding`/`update_requirement` |
| blockers active-check | cross-domain → **port** | `BlockerPort`, real adapter late-imports `blockers.query_blockers` |
| git/commit SHA | **already injected** — not a port | `commit` is a *parameter* to `mark_integrated`/`release_item`/`set_item_status` (caller = worktree-finish.sh supplies it). No git access in this module; nothing to abstract. Leave as-is. |
| `codebugs.fmt.format_table` | in-process (CLI only) | unchanged local import |
| FastMCP `mcp` | in-process | passed to `register_tools` |

**Which become ports: exactly three — `EntityPort`, `BlockerPort`, `Clock`.**
Not git (it's already a plain parameter — abstracting it would be ceremony for
zero seam). Not the `db` registries (framework wiring, not a domain boundary).

**Schema ownership:** unchanged. The 4 tables (`milestones`,
`milestone_items`, `milestone_audit`, `agent_capacity`) and their FKs/indexes
stay one interrelated schema in `schema.py`, owned by milestones via
`ensure_schema`. `register_schema("milestones", ensure_schema,
depends_on=("findings","reqs","blockers"))` is **kept** — the FK
`milestone_items.milestone_id REFERENCES milestones(id)` is internal, but the
`depends_on` documents creation order so production DBs have findings/reqs
tables present when the real `EntityPort` runs. (Tests bypass this by not
registering at all and calling `schema.ensure_schema` directly.)

**Shared spine placement:** `_audit`, `_get_item_by_ref`, `_milestone_exists`,
`_get_milestone`, `_validate_item_ref` → private methods on `MilestoneEngine`
(they need `self._clock` / `self._entity`). `_row_to_milestone/_item/_audit`
→ free functions in `schema.py` (pure row→dict, no ports). This keeps the
mechanical converters reusable and stateless while the policy-bearing helpers
sit on the engine.

---

## 5. Trade-offs

**Honest costs**

- **Port ceremony for a 3-method surface.** Three Protocols + three real
  adapters + one `_build_engine` is ~60 lines of new wiring to abstract what
  is today ~4 inline SQL statements and 4 late imports. For a module touched
  mostly by its own team, that's real overhead.
- **`engine.method(conn, ...)` everywhere.** Every public fn signature gains
  an implicit `self`. The 18 tool closures and 7 CLI handlers all route
  through `engine.` instead of calling free functions. Churn touches every
  call site, even ones with no cross-boundary concern (`list_milestones`,
  `query_audit`).
- **The late import isn't *eliminated*, it's *relocated*.** `adapters.py`
  still late-imports `blockers`/`findings`/`reqs` inside methods (the
  `db`-bootstrap cycle is real and untouched). The win is *containment* — one
  file is allowed to do it, `core.py` is provably clean — not deletion.
- **Two engine constructions** (`register_tools` + the post-add hook) unless
  you cache a module-level singleton; minor.

**Does the current pain justify it?**

The pain is genuine but *small in surface*: 4 late-import sites, all
swallowing exceptions (`except Exception: return False`, `except KeyError:
pass`). The sharp edge is **testability** — right now you cannot unit-test
`pull_next` eligibility or `milestone_close`'s gate without standing up
findings + reqs + blockers schemas, because `_validate_item_ref` and
`_has_active_blocker` reach into them unconditionally. The eligibility ladder
(`_eligibility_failure`) is the most logic-dense, highest-value-to-test code
in the file and it's currently the *hardest* to isolate.

So: **the payoff is concentrated in exactly the riskiest code path
(pull_next/close-gate), and ports&adapters buys a clean fake-driven test of it
with no sibling schema.** That's the strongest argument for this candidate.

The weakest spot: if you only cared about killing the circular import, you
could do that with a single thin `_blockers()` accessor and never introduce
Protocols at all — ports&adapters is overkill *for the import problem alone*.
It earns its keep only if you value the engine-as-deep-testable-module
property. If the team won't write the fake-driven tests, this is ceremony.

**Net:** adopt if testability of the lifecycle engine is a goal; the FROZEN
tool/CLI names are fully preserved (they're just closures over `engine.*`), the
schema stays one unit, and the cross-domain reach becomes three named,
fakeable seams instead of scattered inline SQL + swallowed late imports.
