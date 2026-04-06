"""Tests for the schema registry (ARCH-001)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from codebugs import db
from codebugs.db import (
    register_schema, _schema_registry, _resolve_order,
    ToolProvider, register_tool_provider, _tool_providers,  # noqa: F401
    ConnFactory,  # noqa: F401
)


@pytest.fixture()
def clean_registry():
    """Save and restore the global registry around a test."""
    original = _schema_registry.copy()
    _schema_registry.clear()
    yield
    _schema_registry.clear()
    _schema_registry.extend(original)


class TestRegisterSchema:
    @pytest.fixture(autouse=True)
    def _clean(self, clean_registry):
        pass

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
    def _clean(self, clean_registry):
        pass

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


class TestConnectUsesRegistry:
    """db.connect() initializes all schemas via the registry."""

    def test_connect_creates_all_tables(self, tmp_path):
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


class TestFindingsToolProvider:
    def test_findings_provider_registered(self):
        names = {p.name for p in _tool_providers}
        assert "findings" in names


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

        provider.register_fn(mock_mcp, mock_conn)
        assert mock_mcp.tool.call_count == 4


class TestSweepToolProvider:
    def test_sweep_provider_registered(self):
        import codebugs.sweep  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "sweep" in names


class TestMergeToolProvider:
    def test_merge_provider_registered(self):
        import codebugs.merge  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "merge" in names


class TestReqsToolProvider:
    def test_reqs_provider_registered(self):
        import codebugs.reqs  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "reqs" in names


class TestBlockersToolProvider:
    def test_blockers_provider_registered(self):
        import codebugs.blockers  # noqa: F401
        names = {p.name for p in _tool_providers}
        assert "blockers" in names


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


class TestEnsureModulesLoaded:
    def test_idempotent(self):
        """Calling _ensure_modules_loaded() twice doesn't re-import or crash."""
        from codebugs.db import _ensure_modules_loaded
        _ensure_modules_loaded()
        _ensure_modules_loaded()
        # No error = success. Registry should not have duplicates.
        names = [e.name for e in _schema_registry]
        assert len(names) == len(set(names))
