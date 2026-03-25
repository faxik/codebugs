"""Tests for the codemerge coordination module."""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import merge


@pytest.fixture
def conn():
    """In-memory database with merge schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    merge.ensure_schema(c)
    yield c
    c.close()


class TestSchema:
    def test_tables_created(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "codemerge_sessions" in tables
        assert "codemerge_claims" in tables
        assert "codemerge_locks" in tables

    def test_lock_singleton_initialized(self, conn):
        row = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert row is not None
        assert row["session_id"] is None

    def test_ensure_schema_idempotent(self, conn):
        merge.ensure_schema(conn)  # second call should not raise
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "codemerge_sessions" in tables
