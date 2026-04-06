# ARCH-001: Schema Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded late-import schema initialization in `db.connect()` with a self-registration pattern where domain modules register their own schemas.

**Architecture:** A `SchemaEntry` dataclass + `register_schema()` function in `db.py`. Each domain module calls `register_schema()` at module level. `db.connect()` topologically sorts registered entries by declared dependencies and runs them in order.

**Tech Stack:** Python 3.11+, SQLite, dataclasses

**Spec:** `docs/superpowers/specs/2026-04-06-schema-registry-design.md`

---

### Task 1: Add registry infrastructure to `db.py`

**Files:**
- Modify: `src/codebugs/db.py:1-11` (imports/top of file)
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write failing tests for the registry API**

Create `tests/test_registry.py`:

```python
"""Tests for the schema registry (ARCH-001)."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from codebugs.db import SchemaEntry, register_schema, _schema_registry, _resolve_order


class TestRegisterSchema:
    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        """Save and restore the global registry around each test."""
        original = _schema_registry.copy()
        _schema_registry.clear()
        yield
        _schema_registry.clear()
        _schema_registry.extend(original)

    def test_register_adds_entry(self):
        fn = MagicMock()
        register_schema("test_mod", fn)
        assert len(_schema_registry) == 1
        assert _schema_registry[0].name == "test_mod"
        assert _schema_registry[0].ensure_fn is fn
        assert _schema_registry[0].depends_on == ()

    def test_register_with_dependencies(self):
        fn = MagicMock()
        register_schema("child", fn, depends_on=("parent",))
        assert _schema_registry[0].depends_on == ("parent",)

    def test_duplicate_name_raises(self):
        fn = MagicMock()
        register_schema("dup", fn)
        with pytest.raises(ValueError, match="already registered"):
            register_schema("dup", fn)


class TestResolveOrder:
    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        original = _schema_registry.copy()
        _schema_registry.clear()
        yield
        _schema_registry.clear()
        _schema_registry.extend(original)

    def test_no_deps_preserves_registration_order(self):
        for name in ("a", "b", "c"):
            register_schema(name, MagicMock())
        order = [e.name for e in _resolve_order()]
        assert order == ["a", "b", "c"]

    def test_dependency_ordering(self):
        register_schema("child", MagicMock(), depends_on=("parent",))
        register_schema("parent", MagicMock())
        order = [e.name for e in _resolve_order()]
        assert order.index("parent") < order.index("child")

    def test_diamond_dependency(self):
        register_schema("base", MagicMock())
        register_schema("left", MagicMock(), depends_on=("base",))
        register_schema("right", MagicMock(), depends_on=("base",))
        register_schema("top", MagicMock(), depends_on=("left", "right"))
        order = [e.name for e in _resolve_order()]
        assert order.index("base") < order.index("left")
        assert order.index("base") < order.index("right")
        assert order.index("left") < order.index("top")
        assert order.index("right") < order.index("top")

    def test_cycle_raises(self):
        register_schema("a", MagicMock(), depends_on=("b",))
        register_schema("b", MagicMock(), depends_on=("a",))
        with pytest.raises(ValueError, match="[Cc]ycl"):
            _resolve_order()

    def test_missing_dependency_raises(self):
        register_schema("orphan", MagicMock(), depends_on=("nonexistent",))
        with pytest.raises(ValueError, match="nonexistent"):
            _resolve_order()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_registry.py -v`
Expected: ImportError — `SchemaEntry`, `register_schema`, `_resolve_order` don't exist yet.

- [ ] **Step 3: Implement the registry API in `db.py`**

Add after the existing imports (line 10) in `src/codebugs/db.py`:

```python
from dataclasses import dataclass
from collections.abc import Callable


@dataclass
class SchemaEntry:
    """A registered schema initializer with dependency metadata."""
    name: str
    ensure_fn: Callable[[sqlite3.Connection], None]
    depends_on: tuple[str, ...] = ()


_schema_registry: list[SchemaEntry] = []


def register_schema(
    name: str,
    ensure_fn: Callable[[sqlite3.Connection], None],
    *,
    depends_on: tuple[str, ...] = (),
) -> None:
    """Register a schema initializer. Called at module level by domain modules.

    Raises ValueError if name is already registered.
    """
    if any(e.name == name for e in _schema_registry):
        raise ValueError(f"Schema '{name}' is already registered")
    _schema_registry.append(SchemaEntry(name, ensure_fn, depends_on))


def _resolve_order() -> list[SchemaEntry]:
    """Topological sort of registered schemas using Kahn's algorithm.

    Raises ValueError on cycles or missing dependencies.
    """
    entries = {e.name: e for e in _schema_registry}
    # Validate all dependencies exist
    for e in _schema_registry:
        for dep in e.depends_on:
            if dep not in entries:
                raise ValueError(
                    f"Schema '{e.name}' depends on '{dep}' which is not registered"
                )

    # Kahn's algorithm
    in_degree: dict[str, int] = {name: 0 for name in entries}
    for e in _schema_registry:
        for dep in e.depends_on:
            in_degree[e.name] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    result: list[SchemaEntry] = []

    while queue:
        name = queue.pop(0)
        result.append(entries[name])
        for e in _schema_registry:
            if name in e.depends_on:
                in_degree[e.name] -= 1
                if in_degree[e.name] == 0:
                    queue.append(e.name)

    if len(result) != len(entries):
        remaining = set(entries) - {e.name for e in result}
        raise ValueError(f"Cycle detected among schemas: {remaining}")

    return result
```

- [ ] **Step 4: Run registry tests to verify they pass**

Run: `uv run python -m pytest tests/test_registry.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run python -m pytest tests/ -v`
Expected: 289 tests PASS (adding registry code doesn't change any behavior yet).

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/db.py tests/test_registry.py
git commit -m "feat(db): add schema registry API with topological sort"
```

---

### Task 2: Register `db` findings schema

**Files:**
- Modify: `src/codebugs/db.py:84-109` (connect function and surrounding area)

- [ ] **Step 1: Write a test that the "db" schema is self-registered**

Add to `tests/test_registry.py`:

```python
class TestDbSelfRegistration:
    """Verify db.py registers its own findings schema."""

    def test_db_schema_in_registry(self):
        # db.py registers "db" at module load time — it should already be there
        names = [e.name for e in _schema_registry]
        assert "db" in names

    def test_db_schema_creates_findings_table(self):
        conn = sqlite3.connect(":memory:")
        entry = next(e for e in _schema_registry if e.name == "db")
        entry.ensure_fn(conn)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "findings" in tables
        conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_registry.py::TestDbSelfRegistration -v`
Expected: FAIL — no "db" entry in registry yet.

- [ ] **Step 3: Extract findings schema init and self-register in `db.py`**

Add after `_resolve_order()` in `src/codebugs/db.py`:

```python
def _ensure_findings_schema(conn: sqlite3.Connection) -> None:
    """Initialize the findings table and run migrations."""
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    _migrate_statuses(conn)
    _migrate_provenance(conn)


register_schema("db", _ensure_findings_schema)
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `uv run python -m pytest tests/test_registry.py::TestDbSelfRegistration -v`
Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: 289+ tests PASS. The self-registration runs at import time but `connect()` still uses the old code path — both coexist safely.

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/db.py tests/test_registry.py
git commit -m "feat(db): self-register findings schema in registry"
```

---

### Task 3: Register all domain modules

**Files:**
- Modify: `src/codebugs/reqs.py:670` (append)
- Modify: `src/codebugs/merge.py:429` (append)
- Modify: `src/codebugs/sweep.py:345` (append)
- Modify: `src/codebugs/bench.py:517` (append)
- Modify: `src/codebugs/blockers.py:539` (append)
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write a test that all modules are registered**

Add to `tests/test_registry.py`:

```python
class TestAllModulesRegistered:
    """All domain modules must be registered after import."""

    @pytest.fixture(autouse=True)
    def _import_all(self):
        """Ensure all domain modules are imported."""
        import codebugs.reqs  # noqa: F401
        import codebugs.merge  # noqa: F401
        import codebugs.sweep  # noqa: F401
        import codebugs.bench  # noqa: F401
        import codebugs.blockers  # noqa: F401

    def test_all_modules_registered(self):
        names = {e.name for e in _schema_registry}
        assert names >= {"db", "reqs", "merge", "sweep", "bench", "blockers"}

    def test_blockers_depends_on_db_and_reqs(self):
        entry = next(e for e in _schema_registry if e.name == "blockers")
        assert "db" in entry.depends_on
        assert "reqs" in entry.depends_on

    def test_resolve_order_puts_blockers_after_deps(self):
        order = [e.name for e in _resolve_order()]
        assert order.index("db") < order.index("blockers")
        assert order.index("reqs") < order.index("blockers")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_registry.py::TestAllModulesRegistered -v`
Expected: FAIL — domain modules don't call `register_schema` yet.

- [ ] **Step 3: Add `register_schema` calls to each domain module**

Append to end of `src/codebugs/reqs.py` (after line 670):

```python


# --- Schema registry (ARCH-001) ---
from codebugs.db import register_schema  # noqa: E402

register_schema("reqs", ensure_schema)
```

Append to end of `src/codebugs/merge.py` (after line 429):

```python


# --- Schema registry (ARCH-001) ---
from codebugs.db import register_schema  # noqa: E402

register_schema("merge", ensure_schema)
```

Append to end of `src/codebugs/sweep.py` (after line 345):

```python


# --- Schema registry (ARCH-001) ---
from codebugs.db import register_schema  # noqa: E402

register_schema("sweep", ensure_schema)
```

Append to end of `src/codebugs/bench.py` (after line 517):

```python


# --- Schema registry (ARCH-001) ---
from codebugs.db import register_schema  # noqa: E402

register_schema("bench", ensure_schema)
```

Append to end of `src/codebugs/blockers.py` (after line 539):

```python


# --- Schema registry (ARCH-001) ---
from codebugs.db import register_schema  # noqa: E402

register_schema("blockers", ensure_schema, depends_on=("db", "reqs"))
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `uv run python -m pytest tests/test_registry.py::TestAllModulesRegistered -v`
Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: 289+ tests PASS. Both old and new code paths coexist — `connect()` still uses late imports, and `register_schema` calls also execute. No conflict because `ensure_schema` functions are idempotent (`CREATE TABLE IF NOT EXISTS`).

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/reqs.py src/codebugs/merge.py src/codebugs/sweep.py src/codebugs/bench.py src/codebugs/blockers.py tests/test_registry.py
git commit -m "feat: register all domain module schemas in registry"
```

---

### Task 4: Switch `db.connect()` to use the registry

**Files:**
- Modify: `src/codebugs/db.py:84-109` (connect function)
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write integration test for registry-driven connect**

Add to `tests/test_registry.py`:

```python
import os
from codebugs import db


class TestConnectUsesRegistry:
    """db.connect() initializes all schemas via the registry."""

    def test_connect_creates_all_tables(self, tmp_path):
        path = tmp_path / ".codebugs"
        conn = db.connect(str(tmp_path))
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            assert "findings" in tables
            assert "requirements" in tables
            assert "codemerge_sessions" in tables
            assert "codesweep_sweeps" in tables
            assert "codebench_runs" in tables
            assert "blockers" in tables
        finally:
            conn.close()

    def test_connect_idempotent(self, tmp_path):
        """Calling connect twice on same DB doesn't crash."""
        conn1 = db.connect(str(tmp_path))
        conn1.close()
        conn2 = db.connect(str(tmp_path))
        conn2.close()
```

- [ ] **Step 2: Run to verify it passes (existing connect still works)**

Run: `uv run python -m pytest tests/test_registry.py::TestConnectUsesRegistry -v`
Expected: PASS — current `connect()` already creates these tables.

- [ ] **Step 3: Replace the late imports in `connect()` with registry-driven init**

Replace `src/codebugs/db.py` `connect()` function (lines 84-109) with:

```python
_modules_loaded = False


def _ensure_modules_loaded() -> None:
    """Import all domain modules so their register_schema() calls execute."""
    global _modules_loaded
    if _modules_loaded:
        return
    _modules_loaded = True
    from codebugs import reqs, merge, sweep, bench, blockers  # noqa: F401


def connect(project_dir: str | None = None) -> sqlite3.Connection:
    """Open (and initialize) the codebugs database."""
    path = _db_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    _ensure_modules_loaded()
    for entry in _resolve_order():
        entry.ensure_fn(conn)

    return conn
```

- [ ] **Step 4: Run registry tests**

Run: `uv run python -m pytest tests/test_registry.py -v`
Expected: All PASS.

- [ ] **Step 5: Run FULL test suite — this is the critical step**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL 289+ tests PASS. This verifies that every existing test — including `test_db.py`, `test_blockers.py`, and `test_staleness.py` which use `db.connect()` — works with the new registry-driven initialization.

- [ ] **Step 6: Commit**

```bash
git add src/codebugs/db.py
git commit -m "refactor(db): switch connect() to registry-driven schema init"
```

---

### Task 5: Cleanup and documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Add test for `_ensure_modules_loaded` idempotency**

Add to `tests/test_registry.py`:

```python
class TestEnsureModulesLoaded:
    def test_idempotent(self):
        """Calling _ensure_modules_loaded() twice doesn't re-import or crash."""
        from codebugs.db import _ensure_modules_loaded
        _ensure_modules_loaded()
        _ensure_modules_loaded()
        # No error = success. Registry should not have duplicates.
        names = [e.name for e in _schema_registry]
        assert len(names) == len(set(names))
```

- [ ] **Step 2: Run it**

Run: `uv run python -m pytest tests/test_registry.py::TestEnsureModulesLoaded -v`
Expected: PASS.

- [ ] **Step 3: Update CLAUDE.md architecture section**

In `CLAUDE.md`, update the "Known architectural debt" section to mark the `db.connect()` god function as resolved:

Replace:
```
- **`db.connect()` god function**: initializes all domain schemas via deferred imports (`from codebugs import reqs, merge, ...`). No top-level imports, but still couples `db.py` to every domain module. ARCH-001 replaces this with a schema registry.
```

With:
```
- **`db.connect()` import trigger**: `_ensure_modules_loaded()` still imports all known domain modules so their `register_schema()` calls execute. Schema ordering and initialization is now handled by the registry with topological sort. This trigger will be replaced by auto-discovery in ARCH-002.
```

- [ ] **Step 4: Run full test suite one final time**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_registry.py CLAUDE.md
git commit -m "docs: update CLAUDE.md for ARCH-001 completion, add registry tests"
```

---

### Task 6: Final verification and push

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL tests PASS.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check src/ tests/`
Expected: No errors.

- [ ] **Step 3: Update ARCH-001 requirement status**

Use MCP tool: `reqs_update(req_id="ARCH-001", status="Implemented", test_coverage="tests/test_registry.py")`

- [ ] **Step 4: Push**

```bash
git push
```
