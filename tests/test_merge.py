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


class TestClaims:
    def test_add_claim(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.add_claim(conn, "s1", "src/foo.py")
        assert result["file_path"] == "src/foo.py"
        assert result["session_id"] == "s1"

    def test_add_claim_idempotent(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.add_claim(conn, "s1", "src/foo.py")  # no error
        claims = merge.get_claims(conn, "s1")
        assert len(claims) == 1

    def test_add_claim_unknown_session_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.add_claim(conn, "nonexistent", "src/foo.py")

    def test_add_claim_done_session_raises(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        _force_merging(conn, "s1")
        merge.finish(conn, "s1", success=True)
        with pytest.raises(ValueError, match="not active"):
            merge.add_claim(conn, "s1", "src/foo.py")

    def test_get_claims_empty(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        assert merge.get_claims(conn, "s1") == []

    def test_get_claims_multiple(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.add_claim(conn, "s1", "src/bar.py")
        claims = merge.get_claims(conn, "s1")
        paths = {c["file_path"] for c in claims}
        assert paths == {"src/foo.py", "src/bar.py"}

    def test_claims_updates_last_activity(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        before = conn.execute(
            "SELECT last_activity FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()[0]
        merge.add_claim(conn, "s1", "src/foo.py")
        after = conn.execute(
            "SELECT last_activity FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()[0]
        assert after >= before


class TestMerge:
    def _head_fn(self, sha="abc123"):
        """Return a callable that returns a fixed main HEAD."""
        return lambda: sha

    def test_merge_clean(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is True
        row = conn.execute(
            "SELECT status FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()
        assert row["status"] == "merging"
        lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()
        assert lock["session_id"] == "s1"

    def test_merge_cas_rejects_stale_head(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("def456"),
        )
        assert result["proceed"] is False
        assert result["reason"] == "main_moved"
        assert result["current_head"] == "def456"
        row = conn.execute(
            "SELECT status FROM codemerge_sessions WHERE session_id='s1'"
        ).fetchone()
        assert row["status"] == "active"

    def test_merge_lock_held_rejects(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        result = merge.merge(
            conn, "s2", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is False
        assert result["reason"] == "lock_held"
        assert result["holder"] == "s1"

    def test_merge_expired_lock_reclaimed(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        conn.execute(
            "UPDATE codemerge_locks SET expires_at='2000-01-01T00:00:00Z' WHERE id=1"
        )
        conn.commit()
        result = merge.merge(
            conn, "s2", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is True

    def test_merge_idempotent_if_already_merging(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        result = merge.merge(
            conn, "s1", expected_main_head="abc123",
            current_main_head_fn=self._head_fn("abc123"),
        )
        assert result["proceed"] is True

    def test_merge_unknown_session_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.merge(
                conn, "nope", expected_main_head="abc",
                current_main_head_fn=self._head_fn("abc"),
            )

    def test_merge_done_session_rejects(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.merge(
            conn, "s1", expected_main_head="abc",
            current_main_head_fn=self._head_fn("abc"),
        )
        merge.finish(conn, "s1", success=True)
        with pytest.raises(ValueError, match="not in 'active' state"):
            merge.merge(
                conn, "s1", expected_main_head="abc",
                current_main_head_fn=self._head_fn("abc"),
            )


class TestCheckOverlaps:
    def test_no_overlaps(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/foo.py")
        merge.add_claim(conn, "s2", "src/bar.py")
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is True
        assert result["conflicts"] == []

    def test_parallel_session_overlap(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/shared.py")
        merge.add_claim(conn, "s2", "src/shared.py")
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is False
        assert len(result["conflicts"]) == 1
        conflict = result["conflicts"][0]
        assert conflict["file"] == "src/shared.py"
        assert conflict["blocking_session"] == "s2"
        assert conflict["type"] == "parallel_session"

    def test_main_diverged_overlap(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.add_claim(conn, "s1", "src/foo.py")
        result = merge.check_overlaps(
            conn, "s1", main_changed_files=["src/foo.py", "src/other.py"],
        )
        assert result["clean"] is False
        conflict = result["conflicts"][0]
        assert conflict["file"] == "src/foo.py"
        assert conflict["type"] == "main_diverged"

    def test_ignores_done_sessions(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/shared.py")
        merge.add_claim(conn, "s2", "src/shared.py")
        merge.merge(conn, "s2", expected_main_head="abc", current_main_head_fn=lambda: "abc")
        merge.finish(conn, "s2", success=True)
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is True

    def test_ignores_abandoned_sessions(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        merge.start_session(conn, session_id="s2", branch="b2", description="d2")
        merge.add_claim(conn, "s1", "src/shared.py")
        merge.add_claim(conn, "s2", "src/shared.py")
        merge.abandon_session(conn, "s2")
        result = merge.check_overlaps(conn, "s1")
        assert result["clean"] is True

    def test_returns_main_head(self, conn):
        merge.start_session(conn, session_id="s1", branch="b1", description="d1")
        result = merge.check_overlaps(
            conn, "s1", current_main_head_fn=lambda: "abc123",
        )
        assert result["main_head"] == "abc123"

    def test_unknown_session_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            merge.check_overlaps(conn, "nope")
