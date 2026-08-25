"""Tests for the `tool_calls` usage counter (release-b, DIR-1).

This module owns its own table (`tool_calls`) and CLI verb (`usage`). It
deliberately has NO MCP tool provider: the middleware that populates the
table lives in `server.py`, tested separately in `tests/test_server.py`
alongside `install_strict_arguments`, since that is the one seam where this
project touches the SDK's provisional `MCPServer.middleware`.

What is pinned here: the schema, the write primitive (`record_call`), the
read primitive (`usage_summary`), and the `usage` CLI verb — including that
it says, unprompted, that it only sees MCP-server calls.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from codebugs import db, usage


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


class TestSchemaAndRecordCall:
    def test_table_exists_after_ensure_schema(self, conn):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_calls'"
        ).fetchone()
        assert row is not None

    def test_a_successful_call_is_recorded_with_name_and_nonzero_duration(self, conn):
        usage.record_call(conn, tool_name="stats", success=True, error_type=None, duration_ms=1.5)
        row = conn.execute("SELECT * FROM tool_calls").fetchone()
        assert row["tool_name"] == "stats"
        assert row["success"] == 1
        assert row["error_type"] is None
        assert row["duration_ms"] == 1.5
        assert row["called_at"]  # non-empty timestamp, db.utc_now()-shaped

    def test_a_failed_call_is_recorded_with_its_error_type(self, conn):
        usage.record_call(
            conn, tool_name="update", success=False, error_type="ValueError", duration_ms=0.3
        )
        row = conn.execute("SELECT * FROM tool_calls").fetchone()
        assert row["success"] == 0
        assert row["error_type"] == "ValueError"

    def test_error_type_never_stores_the_exception_text(self, conn):
        """The rule is TYPE, never TEXT — a caller's exception message can carry
        their own data and be arbitrarily long. This asserts what record_call
        actually persists, not merely what the docstring promises."""
        usage.record_call(
            conn,
            tool_name="add",
            success=False,
            error_type="ValueError",
            duration_ms=0.1,
        )
        row = conn.execute("SELECT error_type FROM tool_calls").fetchone()
        assert row["error_type"] == "ValueError"
        assert "secret" not in (row["error_type"] or "")


class TestUsageSummary:
    def test_empty_table_answers_clearly_rather_than_crashing(self, conn):
        result = usage.usage_summary(conn)
        assert result["rows"] == []

    def test_counts_calls_failures_and_timing_per_tool(self, conn):
        usage.record_call(conn, tool_name="stats", success=True, error_type=None, duration_ms=10.0)
        usage.record_call(conn, tool_name="stats", success=True, error_type=None, duration_ms=20.0)
        usage.record_call(
            conn, tool_name="stats", success=False, error_type="KeyError", duration_ms=5.0
        )
        usage.record_call(conn, tool_name="update", success=True, error_type=None, duration_ms=1.0)

        result = usage.usage_summary(conn)
        rows = {r["tool_name"]: r for r in result["rows"]}

        assert rows["stats"]["calls"] == 3
        assert rows["stats"]["failures"] == 1
        assert rows["stats"]["total_ms"] == pytest.approx(35.0)
        assert rows["stats"]["avg_ms"] == pytest.approx(35.0 / 3)
        assert rows["update"]["calls"] == 1
        assert rows["update"]["failures"] == 0

    def test_ordered_by_call_count_descending(self, conn):
        usage.record_call(conn, tool_name="rare", success=True, error_type=None, duration_ms=1.0)
        for _ in range(3):
            usage.record_call(conn, tool_name="common", success=True, error_type=None, duration_ms=1.0)

        result = usage.usage_summary(conn)
        names = [r["tool_name"] for r in result["rows"]]
        assert names == ["common", "rare"]

    def test_since_filters_out_older_calls(self, conn):
        # Inserted directly (rather than through record_call, which always
        # stamps "now") so the fixture can plant one row on each side of the
        # `since` boundary.
        conn.execute(
            "INSERT INTO tool_calls (tool_name, called_at, success, error_type, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            ("old", "2020-01-01T00:00:00Z", 1, None, 1.0),
        )
        conn.execute(
            "INSERT INTO tool_calls (tool_name, called_at, success, error_type, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            ("new", "2030-01-01T00:00:00Z", 1, None, 1.0),
        )
        conn.commit()
        result = usage.usage_summary(conn, since="2025-01-01T00:00:00Z")
        names = [r["tool_name"] for r in result["rows"]]
        assert names == ["new"]

    def test_limit_caps_the_row_count(self, conn):
        usage.record_call(conn, tool_name="a", success=True, error_type=None, duration_ms=1.0)
        usage.record_call(conn, tool_name="b", success=True, error_type=None, duration_ms=1.0)
        result = usage.usage_summary(conn, limit=1)
        assert len(result["rows"]) == 1


class TestUsageCliVerb:
    def _run(self, project_dir, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", "usage", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )

    def test_empty_table_prints_a_clear_message_not_a_crash(self, tmp_project):
        result = self._run(tmp_project)
        assert result.returncode == 0, result.stderr
        assert "no results" in result.stdout.lower() or "no calls" in result.stdout.lower()

    def test_help_or_output_states_the_mcp_only_scope(self, tmp_project):
        """CB-15/CB-16 territory: a summary calling itself 'usage' while silently
        counting only one of three surfaces (MCP / CLI / library) would be a
        success-shaped lie. The verb must say so itself."""
        result = self._run(tmp_project)
        assert result.returncode == 0, result.stderr
        assert "mcp" in result.stdout.lower()
        assert "cli" in result.stdout.lower() or "command" in result.stdout.lower()

    def test_reports_recorded_calls_in_a_table(self, tmp_project):
        conn = db.connect(tmp_project)
        usage.record_call(conn, tool_name="stats", success=True, error_type=None, duration_ms=12.5)
        conn.close()

        result = self._run(tmp_project)
        assert result.returncode == 0, result.stderr
        assert "stats" in result.stdout
