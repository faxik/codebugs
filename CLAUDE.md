# Codebugs

AI-native code finding & requirements tracker. SQLite-backed, exposed via MCP server + CLI.

## Architecture

- **Domain modules** (`src/codebugs/`): `db.py` (findings + shared infra), `reqs.py`, `bench.py`, `blockers.py`, `merge.py`, `sweep.py`
- **Shared types** (`types.py`): Entity constants (statuses, priorities, severities), resolver functions, terminal states. Zero-dependency — safe to import from anywhere
- **MCP server** (`server.py`): Thin FastMCP orchestrator (~48 lines). Discovers tool providers via registry, filters by `--mode` flag
- **CLI** (`cli.py`): Thin argparse orchestrator (~40 lines). Discovers CLI providers via registry, filters by `--mode` flag
- **Formatting** (`fmt.py`): Shared CLI output utilities (ASCII table formatting)
- **Storage**: Single SQLite DB at `.codebugs/findings.db`; each domain module owns its schema via `ensure_schema(conn)`

### Known architectural debt

- **Staleness/provenance logic** (~130 lines) now lives in `db.py` alongside findings. Extraction to a dedicated `provenance.py` is planned.
- **`db.connect()` import trigger**: `_ensure_modules_loaded()` still imports all known domain modules so their `register_schema()`, `register_tool_provider()`, and `register_cli_provider()` calls execute. All three registries are complete (ARCH-001 + ARCH-002 + ARCH-004). This trigger will be replaced by auto-discovery.
- **`blockers.py` cross-module reach**: calls `db._row_to_dict()` and `reqs._row_to_dict()` — private functions across module boundaries. These should be made public or replaced with a shared utility.
- **Findings naming exception**: The findings domain predates the naming conventions. Its MCP tools (`add`, `query`, `stats`, etc.) lack the domain prefix that all other modules use (`reqs_add`, `codebench_import`). Renaming MCP tools is a breaking change for clients.

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
- Each domain module defines `register_tools(mcp, conn_factory)` and calls `register_tool_provider()` at module level.
- `server.py` discovers providers via the registry and passes `_conn` as the `conn_factory`.
- Tool parameters that accept JSON should use `str | list | None` (not just `str`) so MCP clients can pass native types.
- New modules: define `register_tools(mcp, conn_factory)`, call `register_tool_provider("name", register_tools)` at module level.

### CLI
- Each domain module defines `register_cli(sub, commands)` and calls `register_cli_provider()` at module level.
- `cli.py` discovers providers via the registry and filters by `--mode` flag.
- New modules: define `register_cli(sub, commands)`, call `register_cli_provider("name", register_cli)` at module level.

## Architecture migration (in progress)

We are migrating toward a plugin architecture in phases. Query with `reqs_query --section "Architecture Migration"` or MCP tool `reqs_query(section="Architecture Migration")` for the full plan (ARCH-001 through ARCH-005).

**Phase order**: schema registry (ARCH-001 done) -> tool registration (ARCH-002 done) -> entity types (ARCH-003 done) -> CLI unification (ARCH-004 done) -> embedding separation (ARCH-005).

**Current rules for new code:**
- New domain modules must call `register_schema()`, `register_tool_provider()`, and `register_cli_provider()` at module level — do NOT edit `db.connect()`, `server.py`, or `cli.py`.
- Add the new module import to `_ensure_modules_loaded()` in `db.py` (temporary, until auto-discovery).
- Prefer self-contained modules that register themselves over central wiring.
