# ARCH-002: Tool Provider Registry

**Date:** 2026-04-06
**Status:** Design approved
**Requirement:** ARCH-002
**Depends on:** ARCH-001 (schema registry)

## Problem

`server.py` contains 6 `register_*_tools()` functions totaling ~900 lines, defining 39 MCP tools. Each domain's tools are defined inside server.py rather than owned by the domain module. Adding a new module means editing server.py — the same central-wiring problem ARCH-001 solved for schemas.

## Design

### Tool Provider Registry API (`db.py`)

Extends the ARCH-001 registry with a parallel tool provider registry:

```python
@dataclass
class ToolProvider:
    name: str                                          # domain name (e.g. "reqs")
    register_fn: Callable[[FastMCP, ConnFactory], None] # registers tools on mcp
    depends_on: tuple[str, ...] = ()                   # other providers needed first

ConnFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]

_tool_providers: list[ToolProvider] = []

def register_tool_provider(
    name: str,
    register_fn: Callable[[FastMCP, ConnFactory], None],
    *,
    depends_on: tuple[str, ...] = (),
) -> None:
    """Register a tool provider. Called at module level by domain modules."""
    ...
```

`ConnFactory` is a type alias for a callable returning a context manager yielding a connection. This lets modules use `with conn_factory() as conn:` without importing server internals.

### Domain module convention

Each domain module defines a `register_tools(mcp, conn_factory)` function containing the `@mcp.tool()` decorated functions that currently live in server.py. It then calls `register_tool_provider()` at module level:

```python
# reqs.py
def register_tools(mcp: FastMCP, conn_factory: ConnFactory) -> None:
    @mcp.tool()
    def reqs_add(...) -> dict[str, Any]:
        with conn_factory() as conn:
            return add_requirement(conn, ...)
    # ... more tools

register_tool_provider("reqs", register_tools)
```

### What moves where

| Current location (server.py) | New location | Reason |
|------------------------------|-------------|--------|
| `register_findings_tools()` | `db.py` (findings are in db.py) | Domain ownership |
| `register_reqs_tools()` | `reqs.py` | Domain ownership |
| `register_merge_tools()` | `merge.py` | Domain ownership |
| `register_sweep_tools()` | `sweep.py` | Domain ownership |
| `register_bench_tools()` | `bench.py` | Domain ownership |
| `register_blockers_tools()` | `blockers.py` | Domain ownership |
| `_conn()` | `server.py` (stays) | Passed as `conn_factory` param |
| `_git_rev_parse()` | `db.py` (shared git utility) | Used by findings + merge |
| `_get_head_sha()` | `db.py` (findings tool helper) | Findings-specific but uses git |
| `_get_main_head()` | `merge.py` | Merge-specific |
| `_check_file_staleness()` | `db.py` (with findings tools) | Provenance logic, findings-specific |
| `_staleness_check_impl()` | `db.py` (with findings tools) | Provenance logic, findings-specific |

Note: `_git_rev_parse`, `_get_head_sha`, staleness functions move to `db.py` alongside the findings tools because `db.py` owns the findings domain. When provenance is later extracted to `provenance.py` (a separate task), the staleness functions will move there.

### Cross-domain bridge: `reqs_update`

The `reqs_update` tool calls both `reqs.update_requirement()` and `blockers.get_unblocked_by()`. This cross-domain logic stays inside `reqs.py`'s `register_tools()` function as a deferred import:

```python
def register_tools(mcp, conn_factory):
    @mcp.tool()
    def reqs_update(...):
        with conn_factory() as conn:
            result = update_requirement(conn, ...)
            if status:
                from codebugs import blockers
                # ... blocker integration
```

### Mode support

`ToolProvider` includes the `name` field which matches the `--mode` flag values. `server.py` filters providers:

```python
def _get_providers(mode: str) -> list[ToolProvider]:
    if mode == "all":
        return db._resolved_tool_providers()
    return [p for p in db._resolved_tool_providers() if p.name == mode]
```

### What server.py becomes

```python
def main():
    args = parse_args()
    mcp = FastMCP(name=SERVER_NAMES.get(args.mode, "codebugs"))

    @contextmanager
    def conn_factory():
        conn = db.connect()
        try:
            yield conn
        finally:
            conn.close()

    db._ensure_modules_loaded()
    providers = _get_providers(args.mode)
    for provider in providers:
        provider.register_fn(mcp, conn_factory)

    mcp.run()
```

Plus `parse_args()` and the `SERVER_NAMES` dict. Under 50 lines total.

### What does NOT change

- All 39 MCP tool function signatures — unchanged (same params, same return types)
- MCP tool names — unchanged
- Tool docstrings — unchanged (they're user-facing for AI clients)
- `--mode` CLI behavior — unchanged
- Database operations — unchanged
- `ConnFactory` type is just the existing `_conn()` pattern, formalized

## Dependency graph (tool providers)

```
db/findings (no deps)
  <- reqs (no deps, but has cross-domain blocker bridge)
  <- merge (no deps)
  <- sweep (no deps)
  <- bench (no deps)
  <- blockers (no deps for tool registration)
```

No tool provider has dependencies — they all register independently. The schema dependency (blockers depends on db+reqs) is handled by the schema registry, not the tool registry.

## Testing strategy

### Existing tests (must all pass)

All 305 tests must pass throughout. Tool behavior is unchanged — only the location of tool definitions changes.

### New tests

1. **`test_registry.py` additions**: `TestToolProviderRegistration` — verify all 6 providers are registered, verify mode filtering
2. **Per-domain tool tests**: Not strictly needed since tools are functionally identical, but a smoke test verifying each moved `register_tools()` can be called with a mock MCP would catch import errors

### Migration safety

Each domain can be migrated independently:
1. Move `register_X_tools()` from server.py to domain module
2. Add `register_tool_provider()` call
3. Remove the function from server.py
4. Run full test suite

If any step breaks, only that domain's tools are affected — easy to revert.

## Execution order

1. Add `ToolProvider` registry API to `db.py` (parallel to SchemaEntry)
2. Move `_git_rev_parse` and git helpers to `db.py`
3. Migrate one domain at a time (start with bench — simplest, 4 tools, no cross-domain logic)
4. Update `server.py` main() to use the registry
5. Clean up: remove empty register functions from server.py
