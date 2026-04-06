"""Tests for codebugs database layer."""

import json
import os
import sqlite3

import pytest

from codebugs import db


@pytest.fixture
def tmp_project(tmp_path):
    """Provide a temporary project directory with a fresh DB."""
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    """Provide a connected database."""
    c = db.connect(tmp_project)
    yield c
    c.close()


class TestAddFinding:
    def test_add_basic(self, conn):
        result = db.add_finding(
            conn, severity="high", category="n_plus_one", file="src/api.py",
            description="Query in loop",
        )
        assert result["id"] == "CB-1"
        assert result["severity"] == "high"
        assert result["category"] == "n_plus_one"
        assert result["file"] == "src/api.py"
        assert result["status"] == "open"
        assert result["source"] == "human"
        assert result["tags"] == []
        assert result["meta"] == {}

    def test_add_with_meta_and_tags(self, conn):
        result = db.add_finding(
            conn, severity="medium", category="complexity", file="src/foo.py",
            description="CC too high", source="ruff",
            tags=["tech-debt", "refactor"],
            meta={"lines": "10-50", "rule_code": "C901"},
        )
        assert result["source"] == "ruff"
        assert result["tags"] == ["tech-debt", "refactor"]
        assert result["meta"]["lines"] == "10-50"
        assert result["meta"]["rule_code"] == "C901"

    def test_add_auto_increments_id(self, conn):
        f1 = db.add_finding(conn, severity="low", category="style", file="a.py", description="d1")
        f2 = db.add_finding(conn, severity="low", category="style", file="b.py", description="d2")
        f3 = db.add_finding(conn, severity="low", category="style", file="c.py", description="d3")
        assert f1["id"] == "CB-1"
        assert f2["id"] == "CB-2"
        assert f3["id"] == "CB-3"

    def test_add_custom_id(self, conn):
        result = db.add_finding(
            conn, severity="high", category="bug", file="x.py",
            description="desc", finding_id="CUSTOM-42",
        )
        assert result["id"] == "CUSTOM-42"

    def test_add_invalid_severity_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid severity"):
            db.add_finding(
                conn, severity="extreme", category="bug", file="x.py", description="d",
            )

    def test_add_sets_timestamps(self, conn):
        result = db.add_finding(
            conn, severity="low", category="style", file="a.py", description="d",
        )
        assert result["created_at"].endswith("Z")
        assert result["updated_at"] == result["created_at"]


class TestBatchAdd:
    def test_batch_add_multiple(self, conn):
        findings = [
            {"severity": "high", "category": "bug", "file": "a.py", "description": "d1"},
            {"severity": "medium", "category": "style", "file": "b.py", "description": "d2"},
            {"severity": "low", "category": "perf", "file": "c.py", "description": "d3"},
        ]
        results = db.batch_add_findings(conn, findings)
        assert len(results) == 3
        ids = {r["id"] for r in results}
        assert ids == {"CB-1", "CB-2", "CB-3"}

    def test_batch_add_invalid_severity_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid severity"):
            db.batch_add_findings(conn, [
                {"severity": "ultra", "category": "bug", "file": "a.py", "description": "d"},
            ])

    def test_batch_add_with_source_and_meta(self, conn):
        findings = [
            {
                "severity": "high", "category": "sec", "file": "auth.py",
                "description": "SQL injection", "source": "semgrep",
                "meta": {"cwe": "CWE-89"},
            },
        ]
        results = db.batch_add_findings(conn, findings)
        assert results[0]["source"] == "semgrep"
        assert results[0]["meta"]["cwe"] == "CWE-89"


class TestUpdateFinding:
    def test_update_status(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="fixed")
        assert result["status"] == "fixed"
        assert result["updated_at"] >= result["created_at"]

    def test_update_notes(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", notes="Fixed in PR #42")
        assert result["meta"]["notes"] == "Fixed in PR #42"

    def test_update_tags(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", tags=["urgent", "sprint-5"])
        assert result["tags"] == ["urgent", "sprint-5"]

    def test_update_meta(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d",
                       meta={"lines": "10-20"})
        result = db.update_finding(conn, "CB-1", meta_update={"fix_commit": "abc123"})
        assert result["meta"]["lines"] == "10-20"
        assert result["meta"]["fix_commit"] == "abc123"

    def test_update_not_found_raises(self, conn):
        with pytest.raises(KeyError, match="not found"):
            db.update_finding(conn, "CB-999", status="fixed")

    def test_update_status_in_progress(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="in_progress")
        assert result["status"] == "in_progress"

    def test_update_status_alias_done(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="done")
        assert result["status"] == "fixed"

    def test_update_status_alias_resolved(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="resolved")
        assert result["status"] == "fixed"

    def test_update_status_alias_implemented(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="implemented")
        assert result["status"] == "fixed"

    def test_update_status_alias_wontfix(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="wontfix")
        assert result["status"] == "wont_fix"

    def test_update_status_alias_invalid(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="invalid")
        assert result["status"] == "not_a_bug"

    def test_update_status_alias_active(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1", status="active")
        assert result["status"] == "in_progress"

    def test_update_invalid_status_raises(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        with pytest.raises(ValueError, match="Invalid finding status"):
            db.update_finding(conn, "CB-1", status="deleted")

    def test_update_noop(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d")
        result = db.update_finding(conn, "CB-1")
        assert result["status"] == "open"


class TestResolveStatus:
    def test_canonical_passthrough(self):
        for s in db.FINDING_STATUSES:
            assert db.resolve_finding_status(s) == s

    def test_all_aliases_resolve(self):
        for alias, canonical in db.FINDING_STATUS_ALIASES.items():
            assert db.resolve_finding_status(alias) == canonical

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Invalid finding status"):
            db.resolve_finding_status("banana")


class TestQueryFindings:
    @pytest.fixture(autouse=True)
    def seed_data(self, conn):
        db.add_finding(conn, severity="critical", category="security", file="auth.py",
                       description="SQL injection", source="semgrep", tags=["urgent"])
        db.add_finding(conn, severity="high", category="n_plus_one", file="api.py",
                       description="Query in loop", source="claude")
        db.add_finding(conn, severity="medium", category="n_plus_one", file="views.py",
                       description="Another N+1", source="claude")
        db.add_finding(conn, severity="low", category="style", file="utils.py",
                       description="Long line", source="ruff")
        db.update_finding(conn, "CB-4", status="fixed")

    def test_query_all(self, conn):
        result = db.query_findings(conn)
        assert result["total"] == 4
        assert len(result["findings"]) == 4

    def test_query_by_status(self, conn):
        result = db.query_findings(conn, status="open")
        assert result["total"] == 3
        assert all(f["status"] == "open" for f in result["findings"])

    def test_query_by_severity(self, conn):
        result = db.query_findings(conn, severity="critical")
        assert result["total"] == 1
        assert result["findings"][0]["category"] == "security"

    def test_query_by_category(self, conn):
        result = db.query_findings(conn, category="n_plus_one")
        assert result["total"] == 2

    def test_query_by_file_substring(self, conn):
        result = db.query_findings(conn, file="api")
        assert result["total"] == 1

    def test_query_by_source(self, conn):
        result = db.query_findings(conn, source="claude")
        assert result["total"] == 2

    def test_query_by_tag(self, conn):
        result = db.query_findings(conn, tag="urgent")
        assert result["total"] == 1
        assert result["findings"][0]["id"] == "CB-1"

    def test_query_group_by_category(self, conn):
        result = db.query_findings(conn, group_by="category")
        assert result["grouped"] is True
        groups = {g["group_key"]: g["count"] for g in result["groups"]}
        assert groups["n_plus_one"] == 2

    def test_query_group_by_file(self, conn):
        result = db.query_findings(conn, group_by="file")
        assert result["grouped"] is True
        assert len(result["groups"]) == 4

    def test_query_with_limit(self, conn):
        result = db.query_findings(conn, limit=2)
        assert len(result["findings"]) == 2
        assert result["total"] == 4

    def test_query_with_offset(self, conn):
        r1 = db.query_findings(conn, limit=2, offset=0)
        r2 = db.query_findings(conn, limit=2, offset=2)
        ids1 = {f["id"] for f in r1["findings"]}
        ids2 = {f["id"] for f in r2["findings"]}
        assert ids1.isdisjoint(ids2)

    def test_query_by_status_alias(self, conn):
        result = db.query_findings(conn, status="done")
        assert result["total"] == 1
        assert result["findings"][0]["status"] == "fixed"

    def test_query_combined_filters(self, conn):
        result = db.query_findings(conn, status="open", source="claude")
        assert result["total"] == 2
        assert all(f["source"] == "claude" for f in result["findings"])

    def test_query_invalid_group_by_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid group_by"):
            db.query_findings(conn, group_by="invalid")


class TestQueryMeta:
    def test_query_by_meta_key(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d",
                       meta={"rule_code": "C901"})
        db.add_finding(conn, severity="low", category="style", file="b.py", description="d2")
        result = db.query_findings(conn, meta_key="rule_code")
        assert result["total"] == 1

    def test_query_by_meta_key_value(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d",
                       meta={"rule_code": "C901"})
        db.add_finding(conn, severity="high", category="bug", file="b.py", description="d2",
                       meta={"rule_code": "E501"})
        result = db.query_findings(conn, meta_key="rule_code", meta_value="C901")
        assert result["total"] == 1
        assert result["findings"][0]["file"] == "a.py"


class TestStats:
    @pytest.fixture(autouse=True)
    def seed_data(self, conn):
        db.add_finding(conn, severity="critical", category="security", file="a.py", description="d1")
        db.add_finding(conn, severity="high", category="security", file="b.py", description="d2")
        db.add_finding(conn, severity="high", category="perf", file="c.py", description="d3")
        db.add_finding(conn, severity="medium", category="style", file="d.py", description="d4")

    def test_stats_by_severity(self, conn):
        result = db.get_stats(conn, group_by="severity")
        groups = result["groups"]
        # When group_by=severity, each group key is a severity level
        assert groups["critical"]["total"] == 1
        assert groups["high"]["total"] == 2
        assert groups["medium"]["total"] == 1

    def test_stats_by_category(self, conn):
        result = db.get_stats(conn, group_by="category")
        groups = result["groups"]
        assert groups["security"]["total"] == 2
        assert groups["security"]["critical"] == 1
        assert groups["security"]["high"] == 1

    def test_stats_invalid_group_by(self, conn):
        with pytest.raises(ValueError, match="Invalid group_by"):
            db.get_stats(conn, group_by="invalid")


class TestSummary:
    def test_summary_empty(self, conn):
        s = db.get_summary(conn)
        assert s["total"] == 0
        assert s["open"] == 0

    def test_summary_with_data(self, conn):
        db.add_finding(conn, severity="critical", category="sec", file="a.py", description="d1")
        db.add_finding(conn, severity="high", category="perf", file="b.py", description="d2")
        db.add_finding(conn, severity="medium", category="perf", file="c.py", description="d3")
        db.update_finding(conn, "CB-3", status="fixed")

        s = db.get_summary(conn)
        assert s["total"] == 3
        assert s["open"] == 2
        assert s["resolved"] == 1
        assert s["open_by_severity"]["critical"] == 1
        assert s["open_by_severity"]["high"] == 1
        assert len(s["top_categories"]) == 2
        assert len(s["hottest_files"]) == 2

    def test_summary_hottest_files_ranked_by_crit_high(self, conn):
        db.add_finding(conn, severity="critical", category="sec", file="danger.py", description="d1")
        db.add_finding(conn, severity="high", category="sec", file="danger.py", description="d2")
        db.add_finding(conn, severity="low", category="style", file="safe.py", description="d3")
        db.add_finding(conn, severity="low", category="style", file="safe.py", description="d4")
        db.add_finding(conn, severity="low", category="style", file="safe.py", description="d5")

        s = db.get_summary(conn)
        assert s["hottest_files"][0]["file"] == "danger.py"
        assert s["hottest_files"][0]["critical_high"] == 2


class TestCategories:
    def test_categories_empty(self, conn):
        assert db.get_categories(conn) == []

    def test_categories_with_data(self, conn):
        db.add_finding(conn, severity="high", category="bug", file="a.py", description="d1")
        db.add_finding(conn, severity="high", category="bug", file="b.py", description="d2")
        db.add_finding(conn, severity="medium", category="style", file="c.py", description="d3")
        db.update_finding(conn, "CB-1", status="fixed")

        cats = db.get_categories(conn)
        assert len(cats) == 2
        bug = next(c for c in cats if c["category"] == "bug")
        assert bug["total"] == 2
        assert bug["open_count"] == 1
        assert bug["fixed_count"] == 1


class TestConnect:
    def test_creates_db_directory(self, tmp_path):
        project = str(tmp_path)
        conn = db.connect(project)
        assert os.path.exists(os.path.join(project, ".codebugs", "findings.db"))
        conn.close()

    def test_idempotent_connect(self, tmp_path):
        project = str(tmp_path)
        c1 = db.connect(project)
        db.add_finding(c1, severity="low", category="x", file="a.py", description="d")
        c1.close()

        c2 = db.connect(project)
        result = db.query_findings(c2)
        assert result["total"] == 1
        c2.close()


class TestProvenance:
    def test_fresh_db_has_provenance_columns(self, conn):
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(findings)").fetchall()
        }
        assert "reported_at_commit" in cols
        assert "reported_at_ref" in cols

    def test_provenance_columns_nullable(self, conn):
        result = db.add_finding(
            conn, severity="high", category="test", file="a.py",
            description="no provenance",
        )
        assert result.get("reported_at_commit") is None
        assert result.get("reported_at_ref") is None

    def test_migrate_adds_provenance_to_existing_db(self, tmp_project):
        """Simulate a DB created before provenance columns existed."""
        path = os.path.join(tmp_project, db.DB_DIR, db.DB_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old_conn = sqlite3.connect(path)
        old_conn.execute("""CREATE TABLE findings (
            id TEXT PRIMARY KEY,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'in_progress', 'fixed', 'not_a_bug', 'wont_fix', 'stale')),
            description TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'human',
            tags TEXT NOT NULL DEFAULT '[]',
            meta TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        old_conn.execute(
            "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("CB-1", "high", "bug", "x.py", "open", "old bug", "human", "[]", "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        old_conn.commit()
        old_conn.close()

        # Re-open via connect() which triggers migration
        conn = db.connect(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(findings)").fetchall()}
        assert "reported_at_commit" in cols
        assert "reported_at_ref" in cols

        # Old data survives
        row = conn.execute("SELECT * FROM findings WHERE id = 'CB-1'").fetchone()
        assert row is not None
        assert row["reported_at_commit"] is None
        assert row["reported_at_ref"] is None
        conn.close()

    def test_migrate_provenance_idempotent(self, tmp_project):
        """Calling connect() twice on the same DB should not error."""
        conn1 = db.connect(tmp_project)
        db.add_finding(conn1, severity="low", category="test", file="a.py", description="d")
        conn1.close()

        conn2 = db.connect(tmp_project)
        row = conn2.execute("SELECT * FROM findings WHERE id = 'CB-1'").fetchone()
        assert row is not None
        conn2.close()

    def test_add_with_explicit_provenance(self, conn):
        result = db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="test",
            reported_at_commit="a" * 40,
            reported_at_ref="v2.1.0",
        )
        assert result["reported_at_commit"] == "a" * 40
        assert result["reported_at_ref"] == "v2.1.0"

    def test_add_without_provenance_defaults_none(self, conn):
        result = db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="test",
        )
        assert result["reported_at_commit"] is None
        assert result["reported_at_ref"] is None

    def test_batch_add_with_provenance(self, conn):
        results = db.batch_add_findings(conn, [
            {
                "severity": "high", "category": "bug", "file": "a.py",
                "description": "d1",
                "reported_at_commit": "b" * 40,
                "reported_at_ref": "v1.0",
            },
            {
                "severity": "low", "category": "style", "file": "b.py",
                "description": "d2",
            },
        ])
        assert results[0]["reported_at_commit"] == "b" * 40
        assert results[0]["reported_at_ref"] == "v1.0"
        assert results[1]["reported_at_commit"] is None
        assert results[1]["reported_at_ref"] is None

    def test_query_by_commit_prefix(self, conn):
        sha = "a1b2c3d4e5" + "0" * 30
        db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="d", reported_at_commit=sha,
        )
        db.add_finding(
            conn, severity="low", category="style", file="b.py",
            description="d2",
        )
        result = db.query_findings(conn, commit="a1b2c3d4e5")
        assert result["total"] == 1
        assert result["findings"][0]["reported_at_commit"] == sha

    def test_query_by_commit_rejects_non_hex(self, conn):
        with pytest.raises(ValueError, match="hex"):
            db.query_findings(conn, commit="not-hex!")

    def test_query_by_ref(self, conn):
        db.add_finding(
            conn, severity="high", category="bug", file="a.py",
            description="d", reported_at_ref="v2.1.0",
        )
        db.add_finding(
            conn, severity="low", category="style", file="b.py",
            description="d2", reported_at_ref="v3.0.0",
        )
        result = db.query_findings(conn, ref="v2.1.0")
        assert result["total"] == 1
        assert result["findings"][0]["reported_at_ref"] == "v2.1.0"

    def test_update_reported_at_ref(self, conn):
        f = db.add_finding(
            conn, severity="high", category="bug", file="a.py", description="d",
        )
        updated = db.update_finding(conn, f["id"], reported_at_ref="v2.0")
        assert updated["reported_at_ref"] == "v2.0"

    def test_update_does_not_accept_reported_at_commit(self, conn):
        """reported_at_commit is immutable — not a parameter of update_finding."""
        f = db.add_finding(
            conn, severity="high", category="bug", file="a.py", description="d",
            reported_at_commit="a" * 40,
        )
        with pytest.raises(TypeError):
            db.update_finding(conn, f["id"], reported_at_commit="b" * 40)
