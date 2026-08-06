# Adversarial Review: CLAUDE.md vs. Actual Codebase

**Date:** 2026-04-06
**Verdict:** CLAUDE.md contains multiple factual errors, aspirational claims presented as current state, and critical omissions that will actively mislead contributors.

---

## FATAL

**FATAL-1: Module names in Architecture section are WRONG**

CLAUDE.md says:
> Domain modules (`src/codebugs/`): `db.py`, `reqs.py`, `bench.py`, `blockers.py`, `merge.py`, `sweep.py`

This is correct for the actual file names. But the Architecture section omits `server.py` and `cli.py` from the `src/codebugs/` listing while placing them in separate bullets as if they live elsewhere. They are in the same package directory. More critically: **there is no `provenance.py` module** despite the MCP server exposing a `staleness_check` tool and `db.py` containing `_migrate_provenance()`. The provenance/staleness logic lives as bare functions in `server.py` (`_check_file_staleness`, `_staleness_check_impl`), violating the very architecture CLAUDE.md describes where "each domain module owns its schema." This is undocumented architectural debt that CLAUDE.md pretends doesn't exist.

**FATAL-2: SCHEMA variable naming rule is false**

CLAUDE.md states:
> Each module defines its schema as a module-level `SCHEMA` string

Actual variable names:
- `db.py`: `SCHEMA`
- `reqs.py`: `REQS_SCHEMA`
- `merge.py`: `MERGE_SCHEMA`
- `sweep.py`: `SCHEMA`
- `bench.py`: `SCHEMA`
- `blockers.py`: `BLOCKERS_SCHEMA`

Three out of six modules use a prefixed name, not `SCHEMA`. A developer following CLAUDE.md's rule will create `SCHEMA` in a new module, then discover half the codebase disagrees. This is not a nitpick — it causes confusion about which convention to follow and makes automated refactoring (ARCH-001) harder.

**FATAL-3: "New domain modules must NOT be added to db.connect()" — already violated, no enforcement**

CLAUDE.md's migration rule says new modules must not be added to `db.connect()`. But `db.connect()` currently calls `ensure_schema()` for ALL five domain modules (reqs, merge, sweep, bench, blockers). There is no mechanism to prevent a developer from adding a sixth. The rule is aspirational but presented as current policy. Worse: the ARCH-001 migration that would create the schema registry **does not exist anywhere in the codebase** — grep for `ARCH-00` returns zero hits in `src/` and `tests/`. The migration plan references requirements "in codebugs" but they don't exist.

**FATAL-4: Keyword-only args rule is fiction**

CLAUDE.md states:
> Public functions use keyword-only args after `conn` (`def f(conn, *, name, ...)`)

Grep for `def.*conn.*\*` across all modules returns **zero matches**. Actual public function signatures use positional args:
- `merge.abandon_session(conn, session_id)` — positional
- `blockers.is_blocker_satisfied(conn, blocker)` — positional
- `blockers.add_blocker(conn, *, item_id, ...)` — keyword-only but no positional after conn
- `db.get_summary(conn)` — no additional args at all

The `*` keyword-only pattern is used in some functions (like `add_blocker`) but is far from universal. This rule describes a desired future state, not current reality.

---

## SERIOUS

**SERIOUS-1: CLI handler naming convention is inconsistent**

CLAUDE.md says handlers are named `cmd_<domain>_<action>()`. Actual names:
- Findings: `cmd_add`, `cmd_update`, `cmd_query`, `cmd_stats`, `cmd_summary`, `cmd_categories`, `cmd_import_csv`, `cmd_export_csv` — **NO domain prefix**
- Reqs: `cmd_reqs_add`, `cmd_reqs_update` — follows convention
- Merge: `cmd_merge_sessions`, `cmd_merge_status`, `cmd_merge_abandon` — follows convention
- Sweep: `cmd_sweep_create`, `cmd_sweep_add` — follows convention
- Bench: `cmd_bench_import`, `cmd_bench_query` — follows convention

Findings handlers predate the convention and were never migrated. CLAUDE.md presents this as a rule but doesn't acknowledge the existing violations.

**SERIOUS-2: MCP tool naming convention is inconsistent**

CLAUDE.md says:
> MCP tool functions match their module's public API names prefixed with the domain (`codebench_import`, `reqs_add`, `blockers_check`)

But findings tools have NO prefix: `add`, `batch_add`, `update`, `query`, `stats`, `summary`, `categories`, `staleness_check`. Every other domain uses a prefix (`reqs_add`, `codemerge_start`, `codesweep_create`, `codebench_import`, `blockers_add`). The findings domain is the exception, and CLAUDE.md doesn't mention it.

**SERIOUS-3: Testing convention claim is half-true**

CLAUDE.md says:
> Each test class gets a fresh in-memory DB via a `conn` fixture

But `test_db.py` uses `tmp_project` (a real temp directory with `db.connect()`) — NOT an in-memory DB. This is because `db.connect()` creates a file-based DB. Meanwhile `test_reqs.py`, `test_merge.py`, `test_sweep.py`, `test_bench.py`, and `test_blockers.py` use `sqlite3.connect(":memory:")` with module-specific `ensure_schema()`.

There is no shared `conftest.py` — each test file duplicates the fixture. CLAUDE.md implies a uniform pattern that doesn't exist.

**SERIOUS-4: `db.py` violates its own import rule**

CLAUDE.md says:
> `db.py` is infrastructure... It must NOT import domain modules at the top level.

`db.py` technically uses deferred imports inside `connect()`:
```python
from codebugs import reqs
from codebugs import merge
# ...etc
```

This is the exact coupling ARCH-001 is supposed to fix. But the rule as stated ("must NOT import at the top level") is satisfied by the deferred import trick while the spirit of the rule (no coupling) is completely violated. `db.connect()` is a god function that knows about every domain module. CLAUDE.md should be honest about this.

**SERIOUS-5: `blockers.py` violates the cross-module import rule**

CLAUDE.md says:
> Domain modules... must NOT import each other except through well-defined interfaces.

`blockers.py` imports both `db` and `reqs` (lines 491, 494) to call their private `_row_to_dict()` functions. Calling private functions from another module is the opposite of a "well-defined interface." This is structural coupling hidden behind deferred imports.

**SERIOUS-6: CLI missing `blockers` mode**

`server.py` supports `--mode blockers` but `cli.py`'s main function has mode choices: `["findings", "reqs", "merge", "sweep", "bench", "all"]` — no `blockers`. There is no `_register_blockers_subcommands()` in `cli.py`. The CLI silently omits an entire domain module. CLAUDE.md doesn't mention this asymmetry.

**SERIOUS-7: `staleness_check` tool has no domain module — business logic lives in server.py**

The `_check_file_staleness()` and `_staleness_check_impl()` functions (50+ lines of business logic including git subprocess calls, caching, and staleness classification) live directly in `server.py`. This violates CLAUDE.md's architecture: "each domain module owns its schema, constants, and public functions." The staleness logic is not testable without importing the server module. `test_staleness.py` exists but must reach into server internals.

---

## WEAKNESS

**WEAKNESS-1: No error handling conventions documented**

CLAUDE.md says nothing about:
- What exceptions domain modules should raise (ValueError? KeyError? Custom?)
- How MCP tools should handle errors (return error dict? raise and let FastMCP handle?)
- Whether CLI handlers should catch and format errors or propagate

The actual codebase uses a mix: `KeyError` for not-found entities, `ValueError` for invalid input, bare `raise`. No consistency is documented or enforced.

**WEAKNESS-2: No logging conventions**

Zero mentions of logging in CLAUDE.md. The codebase appears to have no logging at all — no `import logging` in any domain module. For an "industrial-grade" tool, silent failures and no observability is a problem.

**WEAKNESS-3: No documentation standards for public functions**

CLAUDE.md requires type hints but says nothing about docstrings. Module-level docstrings exist (good), but there's no rule about function-level docstrings. MCP tool functions have excellent docstrings (they're user-facing), but domain module functions are inconsistent.

**WEAKNESS-4: No concurrency documentation**

The codebase uses `PRAGMA journal_mode=WAL` (good), but CLAUDE.md says nothing about:
- Connection lifecycle (should connections be short-lived?)
- Thread safety of SQLite connections
- What happens when multiple MCP clients hit the server simultaneously
- The merge module's locking mechanism and its limitations

**WEAKNESS-5: No versioning or backward compatibility rules**

CLAUDE.md says schema changes must be additive, which is good. But there are no rules about:
- MCP tool interface stability (can you rename a tool? Remove a parameter?)
- CLI command stability
- Database schema versioning (how do you know which migrations have run?)

**WEAKNESS-6: ARCH-001 through ARCH-005 are phantom requirements**

The migration plan says "See requirements ARCH-001 through ARCH-005 in codebugs." These requirements exist nowhere in the codebase (zero grep hits). They might be in the codebugs DB itself, but that's not inspectable from CLAUDE.md. A developer reading this has no way to find the actual migration plan, making this section useless.

**WEAKNESS-7: No MCP protocol constraints documented**

CLAUDE.md mentions `str | list | None` for JSON params but says nothing about:
- Maximum response sizes
- Tool description length limits
- How to handle long-running operations (no streaming support in current MCP)
- The `json_response=True` flag on FastMCP and what it implies

---

## NITPICK

**NITPICK-1: Python version claim says 3.11+ but runtime is 3.13**

The `.venv` directory shows Python 3.13. `pyproject.toml` has `target-version = "py311"` for ruff, but there's no `requires-python` constraint visible. Minor mismatch.

**NITPICK-2: Run command could mention ruff**

Testing section says `uv run python -m pytest tests/ -v` but doesn't mention how to run linting: `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/`.

**NITPICK-3: "AI-native" is marketing, not architecture**

The tagline "AI-native code finding & requirements tracker" appears in CLAUDE.md and server.py. This is meaningless to a developer. What makes it "AI-native"? The MCP interface? The embedding support? Say what you mean.

---

## Summary Scorecard

| Category | Count |
|----------|-------|
| FATAL    | 4     |
| SERIOUS  | 7     |
| WEAKNESS | 7     |
| NITPICK  | 3     |

## Recommended Actions (priority order)

1. **Fix the lies first.** FATAL-1 through FATAL-4 are factual errors. Every claim in CLAUDE.md must reflect actual code, not aspirations. If a rule is aspirational, label it as such: "TARGET CONVENTION (not yet enforced):"
2. **Extract staleness logic** from `server.py` into a proper domain module (SERIOUS-7). This is the most obvious architectural violation.
3. **Standardize SCHEMA variable naming** — pick one convention and apply it everywhere. Either always `SCHEMA` or always `<MODULE>_SCHEMA`.
4. **Create the ARCH requirements** as actual trackable items, or remove the phantom reference.
5. **Add missing conventions** for error handling, logging, and concurrency.
6. **Acknowledge the findings-domain naming exception** in both CLI and MCP tool naming rules.
7. **Add blockers support to CLI** or document why it's MCP-only.
