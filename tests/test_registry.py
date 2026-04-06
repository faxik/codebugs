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
