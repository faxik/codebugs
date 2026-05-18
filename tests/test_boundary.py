"""Boundary tests for the findings/provenance/db.py split.

Verifies:
1. Post-add hook fires inside the same transaction as findings.add_finding (one commit).
2. findings.batch_add_findings fires hooks per row, then exactly one commit.
3. MCP wire-schema is byte-identical to the golden snapshot.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from contextlib import contextmanager

import pytest

from codebugs import db, findings


class CountingConn:
    """sqlite3.Connection proxy that counts commits. (sqlite3.Connection.commit
    is C-implemented and read-only, so we can't monkeypatch it directly.)"""

    def __init__(self, conn):
        self._conn = conn
        self.commit_count = 0

    def commit(self):
        self.commit_count += 1
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def conn(tmp_path):
    """Fresh DB on disk so connect()'s schema-init path runs end-to-end."""
    c = db.connect(str(tmp_path))
    yield c
    c.close()


class TestPostAddHookAtomicity:
    """Hard constraint #1: hooks fire inside the same transaction as the INSERT."""

    def test_add_finding_runs_hook_before_commit(self, conn):
        """The hook must observe the inserted row but run BEFORE commit returns."""
        seen = []

        def hook(c, finding):
            row = c.execute("SELECT id FROM findings WHERE id = ?", (finding["id"],)).fetchone()
            seen.append((finding["id"], row is not None))

        db.register_post_add_hook("test.atomicity_hook", hook)
        try:
            result = findings.add_finding(
                conn,
                severity="high",
                category="bug",
                file="a.py",
                description="d",
            )
        finally:
            db._post_add_hooks[:] = [
                h for h in db._post_add_hooks if h.name != "test.atomicity_hook"
            ]

        assert seen == [(result["id"], True)]

    def test_add_finding_commits_exactly_once(self, conn):
        """add_finding should call conn.commit() exactly once per finding."""
        proxy = CountingConn(conn)
        findings.add_finding(
            proxy,
            severity="low",
            category="x",
            file="a.py",
            description="d",
        )
        assert proxy.commit_count == 1

    def test_batch_add_findings_fires_hook_per_row_then_one_commit(self, conn):
        """Hard constraint: N inserts → bulk SELECT → N hook fires → ONE commit."""
        hook_calls: list[str] = []

        def hook(c, finding):
            hook_calls.append(finding["id"])

        db.register_post_add_hook("test.batch_hook", hook)
        proxy = CountingConn(conn)
        try:
            results = findings.batch_add_findings(
                proxy,
                [
                    {"severity": "high", "category": "bug", "file": "a.py", "description": "d1"},
                    {
                        "severity": "medium",
                        "category": "style",
                        "file": "b.py",
                        "description": "d2",
                    },
                    {"severity": "low", "category": "perf", "file": "c.py", "description": "d3"},
                ],
            )
        finally:
            db._post_add_hooks[:] = [h for h in db._post_add_hooks if h.name != "test.batch_hook"]

        ids = [r["id"] for r in results]
        assert hook_calls == ids, "hook should fire once per row, in insertion order"
        assert proxy.commit_count == 1, (
            "batch_add_findings must commit exactly ONCE for the whole batch"
        )


class TestMcpWireSchema:
    """Regression gate: the MCP tool schemas clients see must not drift unintentionally."""

    GOLDEN = pathlib.Path(__file__).parent / "golden" / "mcp_schema.json"

    @staticmethod
    def _dump_current_schema() -> list[dict]:
        """Dump the current MCP tool schemas as a flat sorted list."""
        from mcp.server.fastmcp import FastMCP

        @contextmanager
        def _conn():
            c = db.connect()
            try:
                yield c
            finally:
                c.close()

        async def collect():
            all_tools = []
            for provider in db.get_tool_providers(mode="all"):
                server = FastMCP(provider.name, json_response=True)
                provider.register_fn(server, _conn)
                tools = await server.list_tools()
                for t in tools:
                    all_tools.append(
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.inputSchema,
                        }
                    )
            all_tools.sort(key=lambda x: x["name"])
            return all_tools

        return asyncio.run(collect())

    def test_schema_matches_golden(self):
        """Tool surface (names + inputSchema + descriptions) must match the golden.

        If this fails: either (a) you intentionally changed a tool — regenerate the golden
        with `uv run python tests/dump_schema.py > tests/golden/mcp_schema.json`,
        or (b) you accidentally drifted — fix the offending change.
        """
        assert self.GOLDEN.exists(), (
            f"Golden file missing at {self.GOLDEN}. Regenerate with the dump script."
        )
        expected = json.loads(self.GOLDEN.read_text())
        current = self._dump_current_schema()

        if current != expected:
            cur_names = {t["name"] for t in current}
            exp_names = {t["name"] for t in expected}
            added = sorted(cur_names - exp_names)
            removed = sorted(exp_names - cur_names)
            drifted = sorted(
                t["name"]
                for t in current
                if t["name"] in exp_names
                and t != next(e for e in expected if e["name"] == t["name"])
            )
            pytest.fail(
                f"MCP schema drift detected.\n"
                f"  Added tools: {added}\n"
                f"  Removed tools: {removed}\n"
                f"  Drifted tools: {drifted}\n"
                f"Regenerate golden if intentional."
            )
