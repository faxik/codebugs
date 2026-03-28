"""Tests for the sweep batch-iteration module."""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import sweep


@pytest.fixture
def conn():
    """In-memory database with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    sweep.ensure_schema(c)
    yield c
    c.close()


class TestCreateSweep:
    def test_basic_create(self, conn):
        result = sweep.create_sweep(conn)
        assert result["sweep_id"] == "SW-1"
        assert result["status"] == "active"
        assert result["default_batch_size"] == 10

    def test_create_with_name(self, conn):
        result = sweep.create_sweep(conn, name="lint-pass")
        assert result["name"] == "lint-pass"
        assert result["sweep_id"] == "SW-1"

    def test_create_with_description(self, conn):
        result = sweep.create_sweep(conn, description="Review all controllers")
        assert result["description"] == "Review all controllers"

    def test_create_with_batch_size(self, conn):
        result = sweep.create_sweep(conn, default_batch_size=5)
        assert result["default_batch_size"] == 5

    def test_auto_increment_id(self, conn):
        r1 = sweep.create_sweep(conn)
        r2 = sweep.create_sweep(conn)
        assert r1["sweep_id"] == "SW-1"
        assert r2["sweep_id"] == "SW-2"

    def test_duplicate_name_raises(self, conn):
        sweep.create_sweep(conn, name="lint")
        with pytest.raises(ValueError, match="already exists"):
            sweep.create_sweep(conn, name="lint")

    def test_batch_size_zero_raises(self, conn):
        with pytest.raises(ValueError, match="at least 1"):
            sweep.create_sweep(conn, default_batch_size=0)
