# Codebugs

AI-native code finding & requirements tracker. SQLite-backed, exposed via MCP server + CLI.

## Architecture

- **Domain modules** (`src/codebugs/`): `db.py` (findings + shared infra), `reqs.py`, `bench.py`, `blockers.py`, `merge.py`, `sweep.py`
- **MCP server** (`server.py`): FastMCP tool registration, one `register_*_tools()` per domain
- **CLI** (`cli.py`): argparse-based, calls domain modules directly
- **Storage**: Single SQLite DB at `.codebugs/findings.db`; each domain module owns its schema via `ensure_schema(conn)`

### Known architectural debt

- **Staleness/provenance logic** (~130 lines of business logic including git subprocess calls) lives in `server.py` instead of a dedicated domain module. Extraction to `provenance.py` is planned.
- **`db.connect()` import trigger**: `_ensure_modules_loaded()` still imports all known domain modules so their `register_schema()` calls execute. Schema ordering and initialization is now handled by the registry with topological sort (ARCH-001 complete). This trigger will be replaced by auto-discovery in ARCH-002.
- **`blockers.py` cross-module reach**: calls `db._row_to_dict()` and `reqs._row_to_dict()` — private functions across module boundaries. These should be made public or replaced with a shared utility.
- **Findings naming exception**: The findings domain predates the naming conventions. Its CLI handlers (`cmd_add`, `cmd_query`, etc.) and MCP tools (`add`, `query`, `stats`, etc.) lack the domain prefix that all other modules use (`cmd_reqs_add`, `reqs_add`). Renaming MCP tools is a breaking change for clients.
- **No blockers CLI**: `blockers` has MCP tools but no CLI subcommands. All other domains have both.

## Code rules

### Module structure
- Each domain module owns its schema, constants, and public functions. No module should reach into another module's tables directly.
- `db.py` is infrastructure — it provides `connect()`, ID generation, and findings CRUD. It must NOT import domain modules at the top level.
- Domain modules may import `db` for connection/ID utilities. They must NOT import each other's private functions — only public interfaces.

### Naming and style
- Python 3.11+. Type hints on all public function signatures.
- `ruff` for linting/formatting, line length 100.
- Public functions use keyword-only args after `conn`: `def f(conn, *, name, ...)`.
- MCP tool functions are prefixed with the domain: `codebench_import`, `reqs_add`, `blockers_check`. (Exception: findings tools lack prefix — see known debt above.)
- CLI handlers are named `cmd_<domain>_<action>()`. (Exception: findings handlers lack domain prefix — see known debt above.)

### Database
- Each module defines its schema as a module-level string (`SCHEMA` or `<DOMAIN>_SCHEMA`) and provides `ensure_schema(conn)`.
- All schema changes must be additive (new tables, new columns with defaults) or use explicit migration functions.
- Use parameterized queries exclusively. Never interpolate values into SQL.
- SQLite WAL mode is enabled. No concurrent-write coordination beyond SQLite's built-in locking.

### Error handling
- Domain functions raise `ValueError` for invalid input and `KeyError` for missing entities.
- MCP tools let exceptions propagate to FastMCP's built-in error handling.
- CLI handlers catch domain exceptions and print to stderr with `sys.exit(1)`.
- All MCP tools return `dict[str, Any]`.

### Testing
- Tests live in `tests/test_<module>.py`. Most test classes use a fresh in-memory DB via a `conn` fixture.
- Tests requiring `db.connect()`, cross-module schemas, or git operations use `tmp_path` file-based DBs.
- No shared `conftest.py` — each test file defines its own fixtures.
- Test the domain module's public API, not internal helpers.
- Run tests: `uv run python -m pytest tests/ -v`
- Run lint: `uv run ruff check src/ tests/`
- Run format: `uv run ruff format src/ tests/`

### MCP tool registration
- Tools are registered in `server.py` via `register_*_tools(mcp)` functions.
- Each tool manages its own connection via the `_conn()` context manager.
- Tool parameters that accept JSON should use `str | list | None` (not just `str`) so MCP clients can pass native types.

### CLI
- CLI commands are registered in `_register_*_subcommands()` functions in `cli.py`.
- Handlers are named `cmd_<domain>_<action>()`.

## Architecture migration (in progress)

We are migrating toward a plugin architecture in phases. Query with `reqs_query --section "Architecture Migration"` or MCP tool `reqs_query(section="Architecture Migration")` for the full plan (ARCH-001 through ARCH-005).

**Phase order**: schema registry (ARCH-001) -> tool registration (ARCH-002) -> entity types (ARCH-003) -> CLI unification (ARCH-004) -> embedding separation (ARCH-005).

**Current rules for new code:**
- New domain modules must NOT be added to `db.connect()` — use the upcoming schema registry pattern instead.
- Prefer self-contained modules that register themselves over central wiring.
