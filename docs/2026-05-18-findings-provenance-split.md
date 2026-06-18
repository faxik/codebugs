# Findings + Provenance Module Split — Minimal-Interface Design

## Goal

Extract from `db.py` into `findings.py` (~700 LOC) and `provenance.py` (~270 LOC), keeping `db.py` as pure infra (registries, `connect()`, ID gen, hooks). Collapse the 8-call findings API into 3 entry points.

---

## 1. Interfaces

### `findings.py` — 3 public callables

```python
# findings.py
from typing import Any, Literal, TypedDict
import sqlite3

class FindingSpec(TypedDict, total=False):
    severity: str; category: str; file: str; description: str
    source: str; tags: list[str]; meta: dict[str, Any]
    id: str; reported_at_commit: str; reported_at_ref: str
    status: str  # update-only

def add(
    conn: sqlite3.Connection,
    specs: FindingSpec | list[FindingSpec],
    *,
    fire_hooks: bool = True,
) -> dict | list[dict]:
    """Insert one or many findings inside a single transaction.
    Runs db.post_add_hooks for each, then commits once. Mirrors the
    scalar/list shape of `specs` in the return value."""

def patch(
    conn: sqlite3.Connection,
    finding_id: str,
    **fields,  # status, notes, tags, meta_update, reported_at_ref
) -> dict:
    """Partial update. reported_at_commit is rejected (immutable)."""

class Query:
    """Fluent + introspectable. Terminal verbs: .one() .all() .stats() .summary() .categories()"""
    def __init__(self, conn): ...
    def where(self, **filters) -> "Query": ...  # status, severity, category, file, source, tag, meta_key/value, commit, ref, id, ids
    def page(self, *, limit=100, offset=0) -> "Query": ...
    def group_by(self, field: str) -> "Query": ...
    def one(self, finding_id: str) -> dict: ...       # KeyError on miss
    def all(self) -> dict: ...                         # {findings, total, ...}
    def stats(self) -> dict: ...                       # honors .where()
    def summary(self) -> dict: ...
    def categories(self) -> list[dict]: ...

# Free-floating convenience: findings.query(conn).where(status='open').all()
def query(conn) -> Query: return Query(conn)
```

That's **3 names**: `add`, `patch`, `query`. `get` collapses into `query(conn).one(id)`. `stats`/`summary`/`categories` are terminals on the same builder so filters compose.

### `provenance.py` — 1 public callable

```python
# provenance.py
def staleness(
    conn,
    *,
    finding_id: str | None = None,
    project_dir: str | None = None,
    **filters,  # status, category, file — forwarded to findings.query
) -> dict:
    """Single entry point. If finding_id given, checks one; otherwise
    queries findings via findings.query(...).where(**filters).all() and
    batches per (file, reported_at_commit). Caches git work in-process."""
```

`_check_file_staleness` and `_git_rev_parse` become module-private helpers — never re-exported. One door in.

---

## 2. Adapting to the frozen MCP surface

`register_tools` in `findings.py` is the *only* place the 7 legacy names live:

```python
def register_tools(mcp, conn_factory):
    @mcp.tool()
    def add(severity, category, file, description, **kw):       # frozen name
        return findings.add(conn_factory(), {"severity":severity, "category":category,
                                             "file":file, "description":description, **kw})

    @mcp.tool()
    def query(**kw):
        q = findings.query(conn_factory())
        gb = kw.pop("group_by", None); lim = kw.pop("limit",100); off = kw.pop("offset",0)
        q = q.where(**kw).page(limit=lim, offset=off)
        return (q.group_by(gb) if gb else q).all()

    @mcp.tool()
    def get(finding_id):       return findings.query(conn_factory()).one(finding_id)
    @mcp.tool()
    def update(finding_id, **kw): return findings.patch(conn_factory(), finding_id, **kw)
    @mcp.tool()
    def stats(**kw):           return findings.query(conn_factory()).where(**kw).stats()
    @mcp.tool()
    def summary():             return findings.query(conn_factory()).summary()
    @mcp.tool()
    def categories():          return findings.query(conn_factory()).categories()

register_tool_provider("findings", register_tools)
register_schema("findings", ensure_schema)
register_cli_provider("findings", register_cli)
```

**Atomic hooks**: `findings.add` does `INSERT → db.run_post_add_hooks(conn, row) → conn.commit()` per row (or per-batch loop, same one transaction). `milestones.auto_route_finding` still runs before commit. `db.py` exposes `run_post_add_hooks` as the only non-underscore name in this area; the registry decorator (`db.post_add_hook("milestones")`) stays.

---

## 3. Hidden complexity

Callers never touch: `_next_id` generation, JSON-serialize of `tags`/`meta`, status resolver normalization, batch IN-clause assembly, `meta_update` merge-vs-replace semantics, the `ids` → `limit` auto-bump (commit `f9ce7d3`), post-add hook firing, group-by SQL synthesis, git subprocess plumbing, rename detection via `git diff --diff-filter=R`, commit-reachability via `git cat-file -t`, per-file staleness caching keyed on `(file, reported_at_commit)`.

---

## 4. Dependency strategy

| Dep | Category | Treatment |
|---|---|---|
| `sqlite3.Connection` (findings → db) | **in-process** | Passed in; no port. `db.connect()` is the factory. |
| Registries (`register_schema`, `register_tool_provider`, `register_cli_provider`, `post_add_hook`) | **in-process** | Plain function calls into `db`. No abstraction. `db` exports them; domain modules import. |
| Post-add hooks fired inside `findings.add` | **in-process callback** | `db.run_post_add_hooks(conn, row)` — a published seam, not private. |
| `git` subprocess (in `provenance.py`) | **local-substitutable** | Direct `subprocess.check_output`. No port. Tests use real `tmp_path` git repos (already the pattern at `tests/test_staleness.py`). A `GitPort` ABC would be ceremony for one consumer. |
| `os.path` / `os.getcwd` | **local-sub** | Direct. `project_dir` arg already injects the root. |
| `findings` ← `provenance` | **in-process import**, one-way | Enforced by CLAUDE.md rule; no cycle. |

---

## 5. Trade-offs (honest)

**Wins**: 8 functions → 3. `get` and `stats` no longer drift apart — same `Query` builder applies filters identically. Provenance shrinks to one verb. Module top-level reads as a contract, not a buffet.

**Costs**:
- `findings.add(conn, spec_or_list)` is *polymorphic*. Static type checkers grumble; callers must remember the return shape mirrors the input. The old `add_finding`/`batch_add_findings` split was uglier but unambiguous.
- `Query` builder adds 1 indirection vs. `query_findings(conn, status='open')`. Stack traces grow a frame. For one-shot calls it reads slightly worse than a kwarg-only function.
- `patch(**fields)` loses the explicit signature — IDE autocomplete suffers. Mitigated by a `PatchFields` TypedDict, but that drifts.
- `Query.where(**filters)` accepts arbitrary kwargs; typos become silent no-ops unless we validate against a known-filters set (recommended: raise `ValueError` on unknown keys).
- The MCP shim in `register_tools` is **more code** than before, because it bridges flat tool-name signatures to the builder. Cost is paid once, in one place — acceptable.
- `provenance.staleness(**filters)` re-introduces the same typo risk; same mitigation.

**Where I'd cave**: if the team finds `Query` overkill, split into `query(conn, **filters) -> dict` + `stats(conn, **filters)` + `summary(conn)` + `categories(conn)`. Still 5 vs. 8; loses filter-composition uniformity but gains explicit signatures. The `add`+`patch` collapse is the load-bearing win and stays either way.
