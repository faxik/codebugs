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


def _force_merging(conn, session_id):
    """Test helper: set session to 'merging' state and hold lock via direct SQL.
    This avoids depending on merge() which is implemented in a later task."""
    now = merge._now()
    conn.execute(
        "UPDATE codemerge_sessions SET status='merging', last_activity=? WHERE session_id=?",
        (now, session_id),
    )
    conn.execute(
        "UPDATE codemerge_locks SET session_id=?, acquired_at=?, expires_at=? WHERE id=1",
        (session_id, now, "2099-01-01T00:00:00Z"),
    )
    conn.commit()


class TestStartSession:
    def test_start_basic(self, conn):
        result = merge.start_session(
            conn, session_id="feat-sidebar",
            branch="feature/sidebar", description="Add sidebar nav",
        )
        assert result["session_id"] == "feat-sidebar"
        assert result["branch"] == "feature/sidebar"
        assert result["status"] == "active"
        assert "started_at" in result

    def test_start_with_base_commit(self, conn):
        result = merge.start_session(
            conn, session_id="feat-x", branch="feature/x",
            description="desc", base_commit="abc123", repo_root="/repo",
        )
        assert result["base_commit"] == "abc123"
        assert result["repo_root"] == "/repo"

    def test_start_duplicate_raises(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        with pytest.raises(sqlite3.IntegrityError):
            merge.start_session(conn, session_id="s1", branch="b2", description="d2")

    def test_start_reactivate_abandoned(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.abandon_session(conn, "s1")
        result = merge.start_session(
            conn, session_id="s1", branch="b1", description="d1 retry",
            allow_restart=True,
        )
        assert result["status"] == "active"
        assert result["description"] == "d1 retry"


class TestFinishSession:
    def test_finish_success(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        result = merge.finish(conn, "s1", success=True)
        assert result["status"] == "done"
        assert result["finished_at"] is not None

    def test_finish_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.finish(conn, "nonexistent", success=True)

    def test_finish_not_merging_raises(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        with pytest.raises(ValueError, match="not in 'merging' state"):
            merge.finish(conn, "s1", success=True)

    def test_finish_failure_reverts_to_active(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        result = merge.finish(conn, "s1", success=False)
        assert result["status"] == "active"
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert lock["session_id"] is None

    def test_finish_releases_lock(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        merge.finish(conn, "s1", success=True)
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert lock["session_id"] is None
        assert lock["acquired_at"] is None


class TestAbandonSession:
    def test_abandon_active(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.abandon_session(conn, "s1")
        assert result["status"] == "abandoned"
        assert result["finished_at"] is not None

    def test_abandon_merging_releases_lock(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        result = merge.abandon_session(conn, "s1")
        assert result["status"] == "abandoned"
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id = 1").fetchone()
        assert lock["session_id"] is None

    def test_abandon_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.abandon_session(conn, "nonexistent")

    def test_abandon_already_abandoned_is_idempotent(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.abandon_session(conn, "s1")
        result = merge.abandon_session(conn, "s1")
        assert result["status"] == "abandoned"
