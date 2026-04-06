# ARCH-002: Tool Provider Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move tool registration from server.py into domain modules so each module owns both its schema and its MCP tools.

**Architecture:** A `ToolProvider` registry in `db.py` (parallel to ARCH-001's `SchemaEntry`). Each domain module defines `register_tools(mcp, conn_factory)` and calls `register_tool_provider()` at module level. `server.py` discovers providers and calls them.

**Tech Stack:** Python 3.11+, FastMCP, SQLite

**Spec:** `docs/superpowers/specs/2026-04-06-tool-registry-design.md`

---

### Task 1: Add ToolProvider registry API to `db.py`

**Files:**
- Modify: `src/codebugs/db.py:15-42` (after SchemaEntry, before _resolve_order)
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write failing tests for the tool provider registry**

Add to `tests/test_registry.py`:

```python
from codebugs.db import (
    register_schema, _schema_registry, _resolve_order,
    ToolProvider, register_tool_provider, _tool_providers,
    ConnFactory,
)


class TestToolProviderRegistry:
    @pytest.fixture(autouse=True)
    def _clean_providers(self):
        original = _tool_providers.copy()
        _tool_providers.clear()
        yield
        _tool_providers.clear()
        _tool_providers.extend(original)

    def test_register_adds_provider(self):
        fn = MagicMock()
        register_tool_provider("test_domain", fn)
        assert len(_tool_providers) == 1
        assert _tool_providers[0].name == "test_domain"
        assert _tool_providers[0].register_fn is fn

    def test_duplicate_name_raises(self):
        fn = MagicMock()
        register_tool_provider("dup", fn)
        with pytest.raises(ValueError, match="already registered"):
            register_tool_provider("dup", fn)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_registry.py::TestToolProviderRegistry -v`
Expected: ImportError — `ToolProvider`, `register_tool_provider`, `_tool_providers` don't exist.

- [ ] **Step 3: Implement the tool provider registry in `db.py`**

Add after the `register_schema` function in `src/codebugs/db.py` (before `_resolve_order`):

```python
# Type alias for the connection factory passed to tool providers
ConnFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]


@dataclass
class ToolProvider:
    """A registered tool provider with domain metadata."""
    name: str
    register_fn: Callable  # Callable[[FastMCP, ConnFactory], None]
    depends_on: tuple[str, ...] = ()


_tool_providers: list[ToolProvider] = []


def register_tool_provider(
    name: str,
    register_fn: Callable,
    *,
    depends_on: tuple[str, ...] = (),
) -> None:
    """Register a tool provider. Called at module level by domain modules.

    Raises ValueError if name is already registered.
    """
    if any(p.name == name for p in _tool_providers):
        raise ValueError(f"Tool provider '{name}' is already registered")
    _tool_providers.append(ToolProvider(name, register_fn, depends_on))
```

Also add `AbstractContextManager` to the imports at the top of `db.py`:

```python
from contextlib import AbstractContextManager
```

- [ ] **Step 4: Update the existing import line in `tests/test_registry.py`**

Change the import at the top of the file from:

```python
from codebugs.db import register_schema, _schema_registry, _resolve_order
```

To:

```python
from codebugs.db import (
    register_schema, _schema_registry, _resolve_order,
    ToolProvider, register_tool_provider, _tool_providers,
    ConnFactory,
)
```

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/test_registry.py -v`
Expected: All PASS (existing + 2 new).

- [ ] **Step 6: Run full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All 305 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codebugs/db.py tests/test_registry.py
git commit -m "feat(db): add tool provider registry API"
```

---

### Task 2: Migrate bench tools (simplest domain — 4 tools, no cross-domain logic)

**Files:**
- Modify: `src/codebugs/bench.py` (append register_tools + register_tool_provider)
- Modify: `src/codebugs/server.py:847-967` (remove register_bench_tools)
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write smoke test**

Add to `tests/test_registry.py`:

```python
from contextlib import contextmanager


class TestBenchToolProvider:
    def test_bench_provider_registered(self):
        import codebugs.bench  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "bench" in names

    def test_bench_register_tools_callable(self):
        import codebugs.bench  # noqa: F401
        provider = next(p for p in _tool_providers if p.name == "bench")
        mock_mcp = MagicMock()

        @contextmanager
        def mock_conn():
            conn = sqlite3.connect(":memory:")
            from codebugs import bench as b
            b.ensure_schema(conn)
            try:
                yield conn
            finally:
                conn.close()

        # Should not raise — verifies imports and wiring
        provider.register_fn(mock_mcp, mock_conn)
        # Verify tools were registered via @mcp.tool()
        assert mock_mcp.tool.call_count == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_registry.py::TestBenchToolProvider -v`
Expected: FAIL — no "bench" tool provider registered.

- [ ] **Step 3: Add `register_tools` to `bench.py`**

Append to the end of `src/codebugs/bench.py` (after the existing `register_schema` call). Copy `register_bench_tools` from `server.py:847-967`, rename to `register_tools`, change signature to accept `conn_factory`, and replace all `_conn()` with `conn_factory()`:

```python
from codebugs.db import register_tool_provider  # noqa: E402


def register_tools(mcp, conn_factory) -> None:
    """Register benchmark MCP tools."""

    @mcp.tool()
    def codebench_import(
        benchmark: str,
        csv_data: str | None = None,
        json_data: str | list | None = None,
        date: str | None = None,
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Import benchmark results from CSV or JSON.

        CSV convention: first column is the row label, remaining columns are
        metric names with numeric values.

        JSON convention: array of objects, first key is the row label, rest
        are metric keys with numeric values.

        Args:
            benchmark: Benchmark name (e.g. "search-perf")
            csv_data: CSV string (header + data rows). Provide csv_data OR json_data.
            json_data: JSON array string. Provide csv_data OR json_data.
            date: Run date (default: today, ISO format YYYY-MM-DD)
            tags: Optional tags (e.g. ["nightly", "v2.1"])
            meta: Optional metadata (e.g. {"git_sha": "abc123", "ci_url": "..."})
        """
        if csv_data is None and json_data is None:
            raise ValueError("Provide either csv_data or json_data")
        if csv_data is not None and json_data is not None:
            raise ValueError("Provide csv_data or json_data, not both")
        with conn_factory() as conn:
            if csv_data:
                return import_csv(
                    conn, benchmark=benchmark, csv_data=csv_data,
                    date=date, tags=tags, meta=meta,
                )
            return import_json(
                conn, benchmark=benchmark, json_data=json_data,
                date=date, tags=tags, meta=meta,
            )

    @mcp.tool()
    def codebench_query(
        benchmark: str,
        runs: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        metrics: list[str] | None = None,
        rows: list[str] | None = None,
        group_by: str = "row",
        last_n: int | None = None,
        format: str = "json",
    ) -> dict[str, Any]:
        """Query and pivot benchmark results.

        group_by="row": original table shape (row_labels as rows, metrics as
        columns). Returns one table per run.

        group_by="run": trend view (runs as rows, metrics as columns).
        Returns one table per row_label.

        Args:
            benchmark: Benchmark name to query
            runs: Specific run IDs (default: all matching)
            date_from: Start date filter (inclusive, YYYY-MM-DD)
            date_to: End date filter (inclusive, YYYY-MM-DD)
            metrics: Which metrics to include (default: all)
            rows: Which row_labels to include (default: all)
            group_by: Pivot axis — "row" or "run"
            last_n: Limit to last N runs by date
            format: Output — "json" or "csv"
        """
        with conn_factory() as conn:
            return query(
                conn, benchmark=benchmark, runs=runs,
                date_from=date_from, date_to=date_to,
                metrics=metrics, rows=rows, group_by=group_by,
                last_n=last_n, format=format,
            )

    @mcp.tool()
    def codebench_list(
        benchmark: str | None = None,
        last_n: int | None = None,
    ) -> dict[str, Any]:
        """List benchmarks or runs.

        Without benchmark: lists all benchmark names with run counts.
        With benchmark: lists runs for that benchmark.

        Args:
            benchmark: If provided, list runs for this benchmark
            last_n: Limit to last N runs (only when benchmark is provided)
        """
        with conn_factory() as conn:
            if benchmark:
                return list_runs(conn, benchmark=benchmark, last_n=last_n)
            return list_benchmarks(conn)

    @mcp.tool()
    def codebench_delete(
        run_id: str | None = None,
        benchmark: str | None = None,
    ) -> dict[str, Any]:
        """Delete a single run or all runs for a benchmark.

        Args:
            run_id: Delete a specific run (e.g. "BE-1")
            benchmark: Delete all runs for a benchmark name
        """
        if not run_id and not benchmark:
            raise ValueError("Provide run_id or benchmark")
        if run_id and benchmark:
            raise ValueError("Provide run_id or benchmark, not both")
        with conn_factory() as conn:
            if run_id:
                return delete_run(conn, run_id)
            return delete_benchmark(conn, benchmark)


register_tool_provider("bench", register_tools)
```

Note: Inside `register_tools`, the bench functions are called without the `bench.` prefix since they're in the same module (e.g. `import_csv` not `bench.import_csv`).

- [ ] **Step 4: Remove `register_bench_tools` from `server.py`**

Delete lines 847-967 (`register_bench_tools` function) from `src/codebugs/server.py`.

Also remove `bench` from the import on line 13 (change `from codebugs import db, reqs, bench, blockers` to `from codebugs import db, reqs, blockers`).

Update the mode handling near line 1064 — change:
```python
    if args.mode in ("bench", "all"):
        register_bench_tools(server)
```
To:
```python
    if args.mode in ("bench", "all"):
        from codebugs.bench import register_tools as bench_register
        bench_register(server, _conn)
```

- [ ] **Step 5: Run smoke test**

Run: `uv run python -m pytest tests/test_registry.py::TestBenchToolProvider -v`
Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codebugs/bench.py src/codebugs/server.py tests/test_registry.py
git commit -m "refactor: migrate bench tools from server.py to bench.py"
```

---

### Task 3: Migrate remaining simple domains (sweep, merge, blockers)

These three domains have no cross-domain logic and follow the same pattern as bench. Each is a separate sub-step with its own commit.

**Files:**
- Modify: `src/codebugs/sweep.py`, `src/codebugs/merge.py`, `src/codebugs/blockers.py`
- Modify: `src/codebugs/server.py` (remove 3 register functions)
- Modify: `tests/test_registry.py`

**IMPORTANT:** For each domain, the implementer must:
1. Read the exact `register_*_tools()` function from `server.py`
2. Copy it into the domain module as `register_tools(mcp, conn_factory)`
3. Replace all `_conn()` with `conn_factory()`
4. For merge tools: move `_get_main_head()` helper into `merge.py` (it's merge-specific)
5. For merge and sweep: these use lazy imports (`from codebugs import merge/sweep`) inside tools — since tools move INTO the module, replace with direct function calls
6. Add `register_tool_provider("name", register_tools)` at module level
7. Remove the function from server.py and update mode handling
8. Run full test suite after each domain

- [ ] **Step 1: Write smoke tests for all 3 domains**

Add to `tests/test_registry.py`:

```python
class TestSweepToolProvider:
    def test_sweep_provider_registered(self):
        import codebugs.sweep  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "sweep" in names

    def test_sweep_register_tools_callable(self):
        import codebugs.sweep  # noqa: F401
        provider = next(p for p in _tool_providers if p.name == "sweep")
        mock_mcp = MagicMock()

        @contextmanager
        def mock_conn():
            conn = sqlite3.connect(":memory:")
            from codebugs import sweep as s
            s.ensure_schema(conn)
            try:
                yield conn
            finally:
                conn.close()

        provider.register_fn(mock_mcp, mock_conn)
        assert mock_mcp.tool.call_count == 7


class TestMergeToolProvider:
    def test_merge_provider_registered(self):
        import codebugs.merge  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "merge" in names

    def test_merge_register_tools_callable(self):
        import codebugs.merge  # noqa: F401
        provider = next(p for p in _tool_providers if p.name == "merge")
        mock_mcp = MagicMock()

        @contextmanager
        def mock_conn():
            conn = sqlite3.connect(":memory:")
            from codebugs import merge as m
            m.ensure_schema(conn)
            try:
                yield conn
            finally:
                conn.close()

        provider.register_fn(mock_mcp, mock_conn)
        assert mock_mcp.tool.call_count == 5


class TestBlockersToolProvider:
    def test_blockers_provider_registered(self):
        import codebugs.blockers  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "blockers" in names

    def test_blockers_register_tools_callable(self):
        import codebugs.blockers  # noqa: F401
        provider = next(p for p in _tool_providers if p.name == "blockers")
        mock_mcp = MagicMock()

        @contextmanager
        def mock_conn():
            conn = sqlite3.connect(":memory:")
            from codebugs import db as d, blockers as b
            from codebugs import reqs as r
            d._ensure_findings_schema(conn)
            r.ensure_schema(conn)
            b.ensure_schema(conn)
            try:
                yield conn
            finally:
                conn.close()

        provider.register_fn(mock_mcp, mock_conn)
        assert mock_mcp.tool.call_count == 4
```

- [ ] **Step 2: Migrate sweep**

Read `register_sweep_tools()` from `server.py` (lines 733-846). Copy into `sweep.py` as `register_tools(mcp, conn_factory)`. Replace `_conn()` → `conn_factory()`. Replace `sweep.function()` → `function()` (same module). Add `register_tool_provider("sweep", register_tools)`. Remove from server.py. Update mode handling. Run full tests.

Commit: `git commit -m "refactor: migrate sweep tools from server.py to sweep.py"`

- [ ] **Step 3: Migrate merge**

Read `register_merge_tools()` from `server.py` (lines 621-731). Copy into `merge.py`. Also move `_get_main_head()` from server.py into `merge.py` (it's merge-specific — uses `_git_rev_parse` which we'll need to import from server.py temporarily, or copy the helper). Replace `_conn()` → `conn_factory()`. Add `register_tool_provider("merge", register_tools)`. Remove from server.py. Run full tests.

Commit: `git commit -m "refactor: migrate merge tools from server.py to merge.py"`

- [ ] **Step 4: Migrate blockers**

Read `register_blockers_tools()` from `server.py` (lines 969-end). Copy into `blockers.py`. Replace `_conn()` → `conn_factory()`. Replace `blockers.function()` → `function()`. Add `register_tool_provider("blockers", register_tools)`. Remove from server.py. Update import line. Run full tests.

Commit: `git commit -m "refactor: migrate blockers tools from server.py to blockers.py"`

- [ ] **Step 5: Run full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All tests PASS.

---

### Task 4: Migrate reqs tools (has cross-domain blocker bridge)

**Files:**
- Modify: `src/codebugs/reqs.py`
- Modify: `src/codebugs/server.py` (remove register_reqs_tools)
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write smoke test**

Add to `tests/test_registry.py`:

```python
class TestReqsToolProvider:
    def test_reqs_provider_registered(self):
        import codebugs.reqs  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "reqs" in names

    def test_reqs_register_tools_callable(self):
        import codebugs.reqs  # noqa: F401
        provider = next(p for p in _tool_providers if p.name == "reqs")
        mock_mcp = MagicMock()

        @contextmanager
        def mock_conn():
            conn = sqlite3.connect(":memory:")
            from codebugs import reqs as r
            r.ensure_schema(conn)
            try:
                yield conn
            finally:
                conn.close()

        provider.register_fn(mock_mcp, mock_conn)
        assert mock_mcp.tool.call_count == 11
```

- [ ] **Step 2: Migrate reqs tools**

Read `register_reqs_tools()` from `server.py`. Copy into `reqs.py` as `register_tools(mcp, conn_factory)`. Key difference: the `reqs_update` tool has cross-domain logic calling `blockers.get_unblocked_by()` — keep this as a deferred import inside the tool function. Replace `_conn()` → `conn_factory()`. Add `register_tool_provider("reqs", register_tools)`. Remove from server.py.

Commit: `git commit -m "refactor: migrate reqs tools from server.py to reqs.py"`

- [ ] **Step 3: Run full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All tests PASS.

---

### Task 5: Migrate findings tools + git helpers (most complex)

**Files:**
- Modify: `src/codebugs/db.py` (add register_tools + git helpers)
- Modify: `src/codebugs/server.py` (remove register_findings_tools + git helpers)
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write smoke test**

Add to `tests/test_registry.py`:

```python
class TestFindingsToolProvider:
    def test_findings_provider_registered(self):
        names = {p.name for p in _tool_providers}
        assert "findings" in names

    def test_findings_register_tools_callable(self):
        provider = next(p for p in _tool_providers if p.name == "findings")
        mock_mcp = MagicMock()

        @contextmanager
        def mock_conn():
            conn = sqlite3.connect(":memory:")
            from codebugs.db import _ensure_findings_schema
            _ensure_findings_schema(conn)
            try:
                yield conn
            finally:
                conn.close()

        provider.register_fn(mock_mcp, mock_conn)
        assert mock_mcp.tool.call_count == 9
```

- [ ] **Step 2: Move git helpers to db.py**

Move these functions from `server.py` to `db.py` (they're used by findings and merge tools):
- `_git_rev_parse(ref, *, silent=False, cwd=None)` — shared utility
- `_get_head_sha()` — findings-specific but uses git
- `_check_file_staleness(file_path, reported_at_commit, project_dir=None)` — staleness logic
- `_staleness_check_impl(conn, project_dir, *, ...)` — staleness logic

Also update `merge.py` to import `_git_rev_parse` from `db` instead of server (for `_get_main_head`).

- [ ] **Step 3: Migrate findings tools**

Read `register_findings_tools()` from `server.py`. Copy into `db.py` as `register_tools(mcp, conn_factory)`. Replace `_conn()` → `conn_factory()`. The git helpers are now in the same file. Add `register_tool_provider("findings", register_tools)`.

- [ ] **Step 4: Remove from server.py**

Remove `register_findings_tools`, all git helpers, and the staleness functions from server.py. Update mode handling.

- [ ] **Step 5: Run full suite + staleness tests**

Run: `uv run python -m pytest tests/ -v`
Expected: All tests PASS (including test_staleness.py).

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/db.py src/codebugs/server.py src/codebugs/merge.py tests/test_registry.py
git commit -m "refactor: migrate findings tools and git helpers from server.py to db.py"
```

---

### Task 6: Switch server.py main() to use the registry

**Files:**
- Modify: `src/codebugs/server.py` (rewrite main)
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write test for provider filtering**

Add to `tests/test_registry.py`:

```python
class TestAllToolProvidersRegistered:
    @pytest.fixture(autouse=True)
    def _import_all(self):
        import codebugs.reqs  # noqa: F401
        import codebugs.merge  # noqa: F401
        import codebugs.sweep  # noqa: F401
        import codebugs.bench  # noqa: F401
        import codebugs.blockers  # noqa: F401

    def test_all_providers_registered(self):
        names = {p.name for p in _tool_providers}
        assert names >= {"findings", "reqs", "merge", "sweep", "bench", "blockers"}
```

- [ ] **Step 2: Rewrite server.py main()**

Replace the entire `main()` function and the mode-dispatch block in `server.py` with:

```python
SERVER_NAMES = {
    "findings": "codebugs",
    "reqs": "codereqs",
    "merge": "codemerge",
    "sweep": "codesweep",
    "bench": "codebench",
    "blockers": "codeblockers",
    "all": "codebugs",
}


def main():
    parser = argparse.ArgumentParser(description="Codebugs MCP server")
    parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "bench", "blockers", "all"],
        default="all",
        help="Which tools to expose (default: all)",
    )
    args = parser.parse_args()

    server = FastMCP(SERVER_NAMES[args.mode], json_response=True)

    db._ensure_modules_loaded()
    for provider in db._tool_providers:
        if args.mode == "all" or provider.name == args.mode:
            provider.register_fn(server, _conn)

    server.run()
```

Remove all `register_*_tools` imports and calls. The `_conn` context manager stays in server.py — it's the `conn_factory` passed to providers.

- [ ] **Step 3: Remove all leftover register function imports and individual mode handling**

Server.py should now only contain:
- Imports (argparse, contextmanager, FastMCP, db)
- `_conn()` context manager
- `SERVER_NAMES` dict
- `main()` function
- `if __name__ == "__main__": main()`

- [ ] **Step 4: Run full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check src/codebugs/server.py src/codebugs/db.py`
Expected: Clean.

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/server.py tests/test_registry.py
git commit -m "refactor(server): replace mode dispatch with tool provider registry"
```

---

### Task 7: Cleanup and documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Update CLAUDE.md**

Update the Architecture section to reflect that tools now live in domain modules. Update "Known architectural debt" — `db.connect()` import trigger note now covers both schemas and tools.

- [ ] **Step 2: Run full suite one final time**

Run: `uv run python -m pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 3: Run ruff on all changed files**

Run: `uv run ruff check src/ tests/`
Expected: No new errors from our changes.

- [ ] **Step 4: Update ARCH-002 requirement status**

Use MCP tool: `reqs_update(req_id="ARCH-002", status="Implemented", test_coverage="tests/test_registry.py")`

- [ ] **Step 5: Commit and push**

```bash
git add CLAUDE.md tests/test_registry.py
git commit -m "docs: update CLAUDE.md for ARCH-002 completion"
git push
```
