# ARCH-001: Schema Registry

**Date:** 2026-04-06
**Status:** Design approved
**Requirement:** ARCH-001

## Problem

`db.connect()` late-imports all 5 domain modules and calls their `ensure_schema()` functions in a hardcoded order. This couples `db.py` to every domain module — adding a new module means editing core infrastructure. The goal is to invert this: domain modules register themselves, and `db.connect()` initializes whatever has been registered.

## Design

### Registry API (`db.py`)

New public API — three additions to `db.py`:

```python
@dataclass
class SchemaEntry:
    name: str                              # unique identifier (e.g. "reqs", "blockers")
    ensure_fn: Callable[[sqlite3.Connection], None]
    depends_on: tuple[str, ...] = ()       # names of schemas that must init first

_schema_registry: list[SchemaEntry] = []

def register_schema(
    name: str,
    ensure_fn: Callable[[sqlite3.Connection], None],
    *,
    depends_on: tuple[str, ...] = (),
) -> None:
    """Register a schema initializer. Called at module level by domain modules.
    Raises ValueError if name is already registered."""
    if any(e.name == name for e in _schema_registry):
        raise ValueError(f"Schema '{name}' is already registered")
    _schema_registry.append(SchemaEntry(name, ensure_fn, depends_on))

def _resolve_order() -> list[SchemaEntry]:
    """Topological sort of registered schemas. Raises ValueError on cycles or missing deps."""
    ...
```

`SchemaEntry` and `register_schema` are public (importable by domain modules). `_resolve_order` is private to `db.py`.

### db.py self-registration

`db.py` wraps its own schema init (findings table + migrations) into a single function and registers it:

```python
def _ensure_findings_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_statuses(conn)
    _migrate_provenance(conn)
    conn.commit()

register_schema("db", _ensure_findings_schema)
```

This runs at module load time when `db.py` is first imported.

### Domain module self-registration

Each domain module adds a `register_schema()` call at the bottom of the file, after `ensure_schema` is defined:

```python
# reqs.py
from codebugs.db import register_schema
register_schema("reqs", ensure_schema)

# merge.py
from codebugs.db import register_schema
register_schema("merge", ensure_schema)

# sweep.py
from codebugs.db import register_schema
register_schema("sweep", ensure_schema)

# bench.py
from codebugs.db import register_schema
register_schema("bench", ensure_schema)

# blockers.py
from codebugs.db import register_schema
register_schema("blockers", ensure_schema, depends_on=("db", "reqs"))
```

Only `blockers` declares dependencies — it queries the `findings` and `requirements` tables.

### db.connect() changes

Replace the late imports and hardcoded `ensure_schema()` calls with:

```python
def connect(path=None):
    path = path or _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    _ensure_modules_loaded()       # trigger imports
    for entry in _resolve_order(): # topo-sorted
        entry.ensure_fn(conn)

    return conn
```

### Import trigger: `_ensure_modules_loaded()`

A function in `db.py` that imports all known domain modules once:

```python
_modules_loaded = False

def _ensure_modules_loaded() -> None:
    global _modules_loaded
    if _modules_loaded:
        return
    _modules_loaded = True
    from codebugs import reqs, merge, sweep, bench, blockers  # noqa: F401
```

**Why keep this?** Modules must be imported for their `register_schema()` calls to execute. This is the same set of imports as today, but the function only triggers imports — it has zero knowledge of schemas, ordering, or what each module does. When ARCH-002 (tool registry) lands, this becomes the single import trigger for both registries, and eventually gets replaced by entry-point discovery.

**Why not rely on server.py/cli.py imports?** Those entry points import different subsets depending on `--mode`. `db.connect()` must work regardless of which entry point was used.

### Topological sort: `_resolve_order()`

Standard Kahn's algorithm. Validates:
- No missing dependencies (every `depends_on` name has a registered entry)
- No cycles
- Raises `ValueError` with a clear message on either failure

### What does NOT change

- `ensure_schema()` function signatures — unchanged in all modules
- `SCHEMA` / `*_SCHEMA` constants — unchanged
- All existing public APIs — unchanged
- Database file format — unchanged (same tables, same columns)
- Test fixtures that call `ensure_schema()` directly — still work
- `server.py` `_conn()` context manager — still calls `db.connect()`
- `cli.py` connection handling — still calls `db.connect()`

## Dependency graph

```
db (no deps)
  <- reqs (no deps)
  <- merge (no deps)
  <- sweep (no deps)
  <- bench (no deps)
  <- blockers (depends_on: db, reqs)
```

## Testing strategy

### Existing tests (must all pass)

All 6 test files must pass unchanged:
- `test_db.py` — uses `db.connect(tmp_path)`, exercises full init chain
- `test_reqs.py` — in-memory, calls `reqs.ensure_schema()` directly
- `test_bench.py` — in-memory, calls `bench.ensure_schema()` directly
- `test_merge.py` — in-memory, calls `merge.ensure_schema()` directly
- `test_sweep.py` — in-memory, calls `sweep.ensure_schema()` directly
- `test_blockers.py` — uses `db.connect(tmp_path)`, full chain
- `test_staleness.py` — uses `db.connect(tmp_path)`, full chain

### New tests (`test_registry.py`)

1. **`test_register_and_resolve_order`** — register 3 entries with deps, verify topo order
2. **`test_cycle_detection`** — A depends on B, B depends on A -> ValueError
3. **`test_missing_dependency`** — depends_on names a schema not registered -> ValueError
4. **`test_duplicate_name`** — registering same name twice -> ValueError
5. **`test_idempotent_modules_loaded`** — calling `_ensure_modules_loaded()` twice is safe
6. **`test_connect_initializes_all_schemas`** — `db.connect()` with all modules creates all expected tables

### Safety constraints

- All existing tests run before and after every change
- No changes to database file format or table schemas
- No changes to any module's public API
- `ensure_schema()` functions remain callable independently (tests depend on this)

## Migration notes

- This is a pure refactor — no behavioral changes
- Existing databases are unaffected (same schemas, same migrations)
- The `_ensure_modules_loaded()` function is a temporary bridge, not a permanent design
