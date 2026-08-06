# Milestones refactor — Candidate B: Maximal-Flexibility Split

Internal refactor of `src/codebugs/milestones.py` (1718 lines) into N independent,
self-registering domain modules. Tool/CLI names FROZEN (autosorter calls by name).

---

## 1. Interface signature

### Module layout

A new package replaces the single file:

```
src/codebugs/milestones/
  __init__.py            # re-export shim: `from codebugs.milestones import create_milestone` still works
  _core.py               # SHARED SPINE — schema + helpers, NO tool/CLI registration of behavior
  foundation.py          # milestone+item+audit CRUD context
  triage.py              # triage_inbox/dismiss/promote + _auto_route_finding hook
  pull.py                # capacity tracking + pull_next/release_item/get_wip_status
  closegate.py           # mark_branch_only/mark_integrated/milestone_defer/milestone_close
```

`_core.py` (the support module — owns the schema + spine, registers nothing
behavioral):

```python
MILESTONES_SCHEMA = "...4 tables..."   # one interrelated DDL, unchanged
SEED_MILESTONES = [...]
# constants: MILESTONE_KINDS, ITEM_KINDS, ITEM_SIZES, ITEM_STATUSES, MILESTONE_ITEM_TERMINAL, AUTO_ROUTER_ACTOR
def ensure_schema(conn) -> None: ...          # builds all 4 tables + seeds
# spine helpers, all public-but-underscored, imported by the 4 contexts:
def row_to_milestone(row) -> dict: ...
def row_to_item(row) -> dict: ...
def row_to_audit(row) -> dict: ...
def milestone_exists(conn, mid) -> bool: ...
def get_milestone(conn, mid) -> dict: ...
def get_item_by_ref(conn, ref) -> dict: ...
def validate_item_ref(conn, kind, ref) -> None: ...
def audit(conn, *, milestone_id, item_ref, actor, action, ...) -> None: ...
def items_with_active_blockers(conn, items) -> list[str]: ...   # reaches blockers
def has_active_blocker(conn, item_ref) -> bool: ...             # reaches blockers

register_schema("milestones", ensure_schema, depends_on=("findings", "reqs", "blockers"))
```

The four context modules each import the spine and register ONLY their own
behavior (tools + CLI + hook). Schema is registered ONCE, by `_core`.

```python
# foundation.py
from codebugs.milestones import _core
def create_milestone(...): ...   # uses _core.audit, _core.get_milestone, ...
def update_milestone(...): ...
def list_milestones(...): ...
def get_milestone_status(...): ...   # uses _core.items_with_active_blockers
def add_milestone_item(...): ...
def move_milestone_item(...): ...
def set_item_status(...): ...
def query_audit(...): ...
def register_tools(mcp, conn_factory): ...   # milestone_create/_update/_list/_status/
                                             # _add_item/_move_item/_set_status/_audit_query
def register_cli(sub, commands): ...         # milestone-list / -status / -audit
register_tool_provider("milestones.foundation", register_tools)
register_cli_provider("milestones.foundation", register_cli)
```

```python
# triage.py
from codebugs.milestones import _core
def triage_inbox(...): ...
def triage_dismiss(...): ...    # reaches findings.update_finding / reqs.update_requirement
def triage_promote(...): ...
def _auto_route_finding(conn, finding): ...
def register_tools(mcp, conn_factory): ...   # triage_inbox / triage_dismiss / triage_promote
def register_cli(sub, commands): ...         # triage-inbox
register_tool_provider("milestones.triage", register_tools)
register_cli_provider("milestones.triage", register_cli)
register_post_add_hook("milestones.auto_route", _auto_route_finding)   # hook OWNED here
```

```python
# pull.py
from codebugs.milestones import _core
def pull_next(...): ...           # uses _core.has_active_blocker, _core.get_item_by_ref
def release_item(...): ...
def get_wip_status(...): ...
# capacity helpers _capacity_for / _upsert_capacity_increment / _decrement_capacity
# eligibility helpers _eligibility_failure / _bucket_query / _candidates   (pull-private)
def register_tools(mcp, conn_factory): ...   # pull_next / release_item / wip_status
def register_cli(sub, commands): ...         # wip-status
register_tool_provider("milestones.pull", register_tools)
register_cli_provider("milestones.pull", register_cli)
```

```python
# closegate.py
from codebugs.milestones import _core
def mark_branch_only(...): ...
def mark_integrated(...): ...
def milestone_defer(...): ...
def milestone_close(...): ...     # uses _core.items_with_active_blockers
def register_tools(mcp, conn_factory): ...   # mark_branch_only / mark_integrated /
                                             # milestone_close / milestone_defer
def register_cli(sub, commands): ...         # milestone-mark-branch / milestone-mark-integrated
register_tool_provider("milestones.closegate", register_tools)
register_cli_provider("milestones.closegate", register_cli)
```

### Schema ownership decision

**One owner: `_core`.** The 4 tables are a single interrelated schema with FKs
(`milestone_items.milestone_id REFERENCES milestones(id)`) and seed rows the
other contexts depend on (`stream/triage`, `stream/security`). Splitting DDL
across 4 `ensure_schema` functions would force ordering and partial-table
states. `_core.ensure_schema` builds all 4 tables atomically and calls
`register_schema("milestones", ...)` once. The context modules NEVER call
`register_schema`. This is the deliberate exception to "each first-class domain
module owns its schema" — these are sub-contexts of ONE bounded schema, not
4 independent domains.

### FROZEN names preserved

Splitting `register_tool_provider` into 4 providers does NOT change emitted tool
names. FastMCP tool identity is the `name=` kwarg / function name inside
`@mcp.tool()`, independent of which provider function registered it. `pull.py`'s
`@mcp.tool(name="pull_next")` emits exactly `pull_next`. CLI command identity is
the `commands` dict key + `sub.add_parser("...")` string, also unchanged.
Provider *registry* names (`"milestones.pull"`) are internal mode slugs only.

---

## 2. Usage example

### `_ensure_modules_loaded()` in `db.py`

```python
# before:  from codebugs import milestones  # noqa
# after:
from codebugs.milestones import _core, foundation, triage, pull, closegate  # noqa: F401
```

Importing the 4 contexts fires their `register_tool_provider` /
`register_cli_provider` / `register_post_add_hook`; importing `_core` fires the
single `register_schema`. (Alternatively `__init__.py` imports all five so a bare
`from codebugs import milestones` still pulls everything — keeps the trigger
one line.)

### `SERVER_NAMES` (server.py) + `--mode` allowlist (cli.py)

Per-context mode slugs become loadable in isolation:

```python
SERVER_NAMES = {..., "milestones",                       # umbrella: all 4 + core
                     "milestones.foundation",
                     "milestones.triage",
                     "milestones.pull",
                     "milestones.closegate"}
```

`--mode milestones.pull` loads only `_core` (schema) + `pull` (tools). Because
schema lives in `_core` and every context imports `_core`, loading ANY single
context transitively registers the full schema — no partial-table risk. The
umbrella slug `milestones` is satisfied by prefix-match if the registry filter
supports `name.startswith(mode)`; otherwise list all four explicitly.

### Test loading just one context

```python
def test_pull_in_isolation(tmp_path):
    # importing pull pulls _core (schema) transitively; no foundation/triage/closegate
    from codebugs.milestones import _core, pull
    conn = sqlite3.connect(tmp_path / "t.db"); conn.row_factory = sqlite3.Row
    _core.ensure_schema(conn)
    # seed an item directly via _core helpers, then:
    got = pull.pull_next(conn, agent_id="a1", capacity={"large":1,"small":2,"triage":5})
    assert got is None  # empty buckets
```

### Future extension (the flexibility payoff)

A `burndown.py` context: `from codebugs.milestones import _core`, read-only
queries over the 4 tables, its own `register_tool_provider("milestones.burndown")`
+ one import line in `_ensure_modules_loaded`. **Zero edits to foundation/triage/
pull/closegate.** Open–closed at the context boundary.

---

## 3. Complexity hidden internally

- **Schema atomicity + seed dependency** — hidden in `_core.ensure_schema`. Callers
  never see that triage depends on the `stream/triage` seed row existing.
- **Cross-table reads in rollups** — `get_milestone_status` (foundation) and
  `milestone_close` (closegate) JOIN-read `milestone_items` + call
  `_core.items_with_active_blockers`. The blocker reach is hidden in `_core`.
- **`pull_next` concurrency** — `BEGIN IMMEDIATE` save/restore of `isolation_level`,
  the 4-bucket priority `_candidates` generator, and `_eligibility_failure`
  stay entirely inside `pull.py`. No other context sees the transaction dance.
- **Dismissal propagation** — triage hides that dismissing a `bug` flips the
  underlying finding to `not_a_bug` and a `requirement` to `obsolete`.
- **Auto-route schema-probe** — `_auto_route_finding` probes `sqlite_master` for
  `milestone_items` before inserting (raw-connection callers in tests). Hidden in triage.

---

## 4. Dependency strategy (Ousterhout)

- **Shared spine placement** — `_core.py`, a leaf support module. Depended on by
  all 4 contexts; depends on nothing within the package. Acyclic by construction:
  contexts → `_core` → (`codebugs.db`, `codebugs.types`). No context imports another
  context. **Eliminated dependency** between contexts (they were one file; now
  each only knows `_core`).
- **Schema ownership across N modules** — centralized in `_core` (one
  `register_schema`, `depends_on=("findings","reqs","blockers")` unchanged). Avoids
  the **artificial dependency** of N modules racing to create interrelated tables.
- **Cross-boundary reach (findings/reqs/blockers)** —
  - blockers reach (`query_blockers`) is consolidated into `_core.has_active_blocker`
    + `_core.items_with_active_blockers`; pull + foundation + closegate all call the
    spine instead of each importing `blockers`. Turns 3 leak points into 1.
  - findings/reqs reach (`update_finding`, `update_requirement`) stays local to
    `triage.triage_dismiss` — only triage propagates dismissals, so it's a
    **true dependency** kept at its single natural site (lazy import, no cycle:
    findings/reqs don't import milestones).
- **Post-add hook ownership** — `register_post_add_hook("milestones.auto_route",
  _auto_route_finding)` moves to `triage.py`, the only context that defines routing.
  `db.add_finding` calls the hook by registry; no import-direction change
  (db → hook fn, never milestones → db at top level except the registration line).
- **Dependency category** — the spine is an **information-hiding deep-ish module**
  (small interface: ~9 helpers; hides schema + blocker reach). The split trades
  one fat module for a star topology centered on `_core`.

---

## 5. Trade-offs (honest costs)

**Costs**
- **More files (1 → 6)** and registry sprawl: 4 tool providers + 4 CLI providers +
  1 schema + 1 hook = 10 registration calls vs. today's 4. `_ensure_modules_loaded`,
  `SERVER_NAMES`, and the `--mode` allowlist each grow by ~4 entries.
- **Spine privacy erosion** — `_audit`, `_get_item_by_ref`, etc. must become
  package-public (`_core.audit`, `_core.get_item_by_ref`). The leading-underscore
  signal that "this is internal" weakens; nothing stops a future context from
  abusing them. (Mitigation: docstring "spine — milestones-internal only".)
- **Schema/behavior ownership asymmetry** — violates the codebase's "each domain
  module owns its schema" rule (CLAUDE.md). `_core` owns schema; contexts own
  behavior. A reader must learn this exception. Documented as known debt.
- **`--mode` granularity is partly illusory** — loading `milestones.pull` alone
  still registers the full 4-table schema (via `_core`) and the contexts share
  every table, so isolation is at the *tool-surface* level, not the *data* level.
  Real value: smaller tool list per mode, faster targeted tests — not a smaller DB.
- **Umbrella-slug matching** — needs prefix-match (`startswith`) in the mode filter,
  or 5 explicit slugs maintained by hand.
- **Cross-context invariants get harder to see** — e.g. `pull_next` (pull) sets
  `in_progress`; `milestone_close` (closegate) refuses on `in_progress`. The
  status-machine contract now spans files; a change to `ITEM_STATUSES` semantics
  must be checked across 4 modules. (Constants live in `_core`, which helps.)

**Benefits bought**
- True open–closed for new contexts (burndown/dependencies) with zero edits to
  existing context files.
- Blocker reach consolidated 3→1.
- Per-context tests + per-context `--mode` tool surfaces.
- Each file is now ~150–400 lines, navigable; `pull.py`'s concurrency code is
  isolated from CRUD.

**When NOT to pick this**: if no future contexts are anticipated and `--mode`
sub-granularity is unused, the split is pure overhead — Candidate A (single
module, internal section discipline) is cheaper. This candidate bets on extension.
