"""Tests for the requirements tracking module."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from codebugs import db, reqs


@pytest.fixture
def conn():
    """In-memory database with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    reqs.ensure_schema(c)
    yield c
    c.close()


def _import_md(conn, md_text: str) -> dict:
    """Write markdown to a temp file, import it, and clean up."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(md_text)
        f.flush()
        path = f.name
    try:
        return reqs.import_markdown(conn, path)
    finally:
        os.unlink(path)


@pytest.fixture
def populated(conn):
    """Database with sample requirements."""
    now = reqs._now()
    for i, (status, priority, section, tc) in enumerate([
        ("Planned", "Must", "1.1 Ingestion", ""),
        ("Implemented", "Must", "1.1 Ingestion", "test_core.py"),
        ("Implemented", "Should", "1.2 Duplicate Detection", "test_dedup.py"),
        ("Superseded", "Could", "1.3 Sorting", ""),
        ("Partial", "Must", "1.2 Duplicate Detection", ""),
        ("Implemented", "Must", "1.4 Classification", ""),  # no test but Must
    ], start=1):
        conn.execute(
            """INSERT INTO requirements (id, section, description, priority, status,
               source, test_coverage, tags, meta, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, '', ?, '[]', '{}', ?, ?)""",
            (f"FR-{i:03d}", section, f"Requirement {i} description", priority, status,
             tc, now, now),
        )
    conn.commit()
    return conn


class TestAddRequirement:
    def test_basic_add(self, conn):
        result = reqs.add_requirement(
            conn, req_id="FR-001", description="System shall ingest documents",
            section="1.1 Ingestion", priority="Must", status="Planned",
        )
        assert result["id"] == "FR-001"
        assert result["status"] == "Planned"
        assert result["priority"] == "Must"
        assert result["section"] == "1.1 Ingestion"

    def test_invalid_priority_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid priority"):
            reqs.add_requirement(conn, req_id="FR-001", description="test", priority="High")

    def test_invalid_status_raises(self, conn):
        with pytest.raises(ValueError, match="Invalid status"):
            reqs.add_requirement(conn, req_id="FR-001", description="test", status="done")

    def test_duplicate_id_raises(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="first")
        with pytest.raises(sqlite3.IntegrityError):
            reqs.add_requirement(conn, req_id="FR-001", description="second")

    def test_tags_and_meta(self, conn):
        result = reqs.add_requirement(
            conn, req_id="FR-001", description="test",
            tags=["v2", "sweep"], meta={"author": "claude"},
        )
        assert result["tags"] == ["v2", "sweep"]
        assert result["meta"]["author"] == "claude"


class TestBatchAdd:
    def test_batch_insert(self, conn):
        results = reqs.batch_add_requirements(conn, [
            {"id": "FR-001", "description": "First", "priority": "Must"},
            {"id": "FR-002", "description": "Second"},
        ])
        assert len(results) == 2
        assert {r["id"] for r in results} == {"FR-001", "FR-002"}

    def test_batch_replace(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="original")
        results = reqs.batch_add_requirements(conn, [
            {"id": "FR-001", "description": "updated"},
        ])
        assert results[0]["description"] == "updated"


class TestUpdateRequirement:
    def test_update_status(self, populated):
        result = reqs.update_requirement(populated, "FR-001", status="Implemented")
        assert result["status"] == "Implemented"

    def test_update_not_found(self, conn):
        with pytest.raises(KeyError, match="not found"):
            reqs.update_requirement(conn, "FR-999", status="Implemented")

    def test_update_notes(self, populated):
        result = reqs.update_requirement(populated, "FR-001", notes="Needs review")
        assert result["meta"]["notes"] == "Needs review"

    def test_update_test_coverage(self, populated):
        result = reqs.update_requirement(populated, "FR-001", test_coverage="test_new.py")
        assert result["test_coverage"] == "test_new.py"

    def test_noop_update(self, populated):
        result = reqs.update_requirement(populated, "FR-001")
        assert result["id"] == "FR-001"  # Returns unchanged


class TestQueryRequirements:
    def test_query_all(self, populated):
        result = reqs.query_requirements(populated)
        assert result["total"] == 6

    def test_filter_by_status(self, populated):
        result = reqs.query_requirements(populated, status="Implemented")
        assert result["total"] == 3

    def test_filter_by_priority(self, populated):
        result = reqs.query_requirements(populated, priority="Must")
        assert result["total"] == 4

    def test_filter_by_section(self, populated):
        result = reqs.query_requirements(populated, section="Duplicate")
        assert result["total"] == 2

    def test_search(self, populated):
        result = reqs.query_requirements(populated, search="FR-003")
        assert result["total"] == 1
        assert result["requirements"][0]["id"] == "FR-003"

    def test_group_by(self, populated):
        result = reqs.query_requirements(populated, group_by="status")
        assert result["grouped"] is True
        groups = {g["group_key"]: g["count"] for g in result["groups"]}
        assert groups["Implemented"] == 3

    def test_pagination(self, populated):
        result = reqs.query_requirements(populated, limit=2, offset=0)
        assert len(result["requirements"]) == 2
        assert result["total"] == 6


class TestStats:
    def test_stats_by_status(self, populated):
        result = reqs.get_reqs_stats(populated, group_by="status")
        groups = result["groups"]
        assert groups["Implemented"]["total"] == 3
        assert groups["Planned"]["Must"] == 1

    def test_stats_by_priority(self, populated):
        result = reqs.get_reqs_stats(populated, group_by="priority")
        assert "Must" in result["groups"]

    def test_invalid_group_by(self, populated):
        with pytest.raises(ValueError, match="Invalid group_by"):
            reqs.get_reqs_stats(populated, group_by="file")


class TestSummary:
    def test_summary(self, populated):
        result = reqs.get_reqs_summary(populated)
        assert result["total"] == 6
        assert result["by_status"]["Implemented"] == 3
        assert result["implemented_without_tests"] == 1  # FR-006: Must, Implemented, no test
        assert len(result["sections"]) > 0


class TestVerify:
    def test_verify_duplicate_ids(self, conn):
        # Manually insert duplicate (bypass PK by using different tables — simulate import)
        # Since PK prevents actual duplicates, test the gap detection instead
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-010", description="b")
        result = reqs.verify_requirements(conn, checks=["ids"])
        gap_issues = [i for i in result["issues"] if "gap" in i["message"].lower()]
        assert len(gap_issues) == 1  # FR-002..FR-009 gap (8 items, >=5)

    def test_verify_status_contradiction(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001",
            description="Sorting (superseded by vault architecture)",
            status="Planned",
        )
        result = reqs.verify_requirements(conn, checks=["status"])
        status_issues = [i for i in result["issues"] if i["check"] == "status"]
        assert len(status_issues) >= 1
        assert "superseded" in status_issues[0]["message"].lower()

    def test_verify_missing_test_file(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001", description="test",
            status="Implemented", test_coverage="test_nonexistent.py",
        )
        result = reqs.verify_requirements(conn, checks=["tests"], project_dir="/tmp")
        test_issues = [i for i in result["issues"] if i["check"] == "tests"]
        assert len(test_issues) == 1
        assert "not found" in test_issues[0]["message"]

    def test_verify_must_without_test(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001", description="Critical feature",
            status="Implemented", priority="Must",
        )
        result = reqs.verify_requirements(conn, checks=["status"])
        issues = [i for i in result["issues"] if "without test" in i["message"]]
        assert len(issues) == 1

    def test_verify_all_clean(self, conn):
        reqs.add_requirement(
            conn, req_id="FR-001", description="Good requirement",
            status="Planned", priority="Should",
        )
        result = reqs.verify_requirements(conn, checks=["status"])
        assert result["issues_found"] == 0


class TestMarkdownImportExport:
    def test_import_basic(self, conn):
        md = """# Requirements

### 1.1 Ingestion (FR-001 -- FR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | System shall ingest PDFs | Must | Implemented | R&A | test_core.py |
| FR-002 | System shall track duplicates | Should | Planned | R&A | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'FR-001'").fetchone()
        assert row["priority"] == "Must"
        assert row["status"] == "Implemented"
        assert row["section"] == "1.1 Ingestion"
        assert row["test_coverage"] == "test_core.py"

    def test_export_roundtrip(self, populated):
        md = reqs.export_markdown(populated)
        assert "### 1.1 Ingestion" in md
        assert "FR-001" in md
        assert "| ID |" in md

    def test_import_status_normalization(self, conn):
        md = """### 1.1 Test (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Test | should | implemented | -- | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT * FROM requirements WHERE id = 'FR-001'").fetchone()
        assert row["status"] == "Implemented"
        assert row["priority"] == "Should"


class TestImportNFRRows:
    """CB-2: NFR-xxx IDs should be imported, not silently dropped."""

    def test_import_nfr_rows(self, conn):
        md = """# Requirements

### 1.1 Non-Functional (NFR-001 -- NFR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | System shall respond within 200ms | Must | Planned | Arch | -- |
| NFR-002 | System shall handle 1000 concurrent users | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row is not None
        assert row["priority"] == "Must"
        assert row["description"] == "System shall respond within 200ms"

    def test_import_mixed_fr_and_nfr(self, conn):
        md = """# Requirements

### 1.1 Mixed (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Functional req | Must | Planned | R&A | -- |
| NFR-001 | Non-functional req | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        assert conn.execute("SELECT COUNT(*) as c FROM requirements").fetchone()["c"] == 2


class TestImportUnnumberedSections:
    """CB-3: Unnumbered ### headings should create their own sections."""

    def test_unnumbered_section_heading(self, conn):
        md = """# Requirements

### Plugin Architecture (FR-101 -- FR-102)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-101 | Plugins shall load dynamically | Must | Planned | Arch | -- |
| FR-102 | Plugins shall be sandboxed | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'FR-101'").fetchone()
        assert row is not None
        assert row["section"] == "Plugin Architecture"

    def test_unnumbered_does_not_merge_into_previous(self, conn):
        md = """# Requirements

### 1.81 Archive Extract-and-Ingest (FR-001 -- FR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Extract archives | Must | Planned | R&A | -- |
| FR-002 | Detect format | Should | Planned | R&A | -- |

### Plugin Architecture (FR-003 -- FR-004)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-003 | Load plugins | Must | Planned | Arch | -- |
| FR-004 | Sandbox plugins | Should | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 4
        row_001 = conn.execute("SELECT section FROM requirements WHERE id = 'FR-001'").fetchone()
        row_003 = conn.execute("SELECT section FROM requirements WHERE id = 'FR-003'").fetchone()
        assert row_001["section"] == "1.81 Archive Extract-and-Ingest"
        assert row_003["section"] == "Plugin Architecture"
        assert row_001["section"] != row_003["section"]

    def test_unnumbered_section_with_nfr(self, conn):
        md = """# Requirements

### Performance Targets (NFR-001 -- NFR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Response time < 200ms | Must | Planned | Arch | -- |
| NFR-002 | Uptime 99.9% | Must | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 2
        row = conn.execute("SELECT * FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row is not None
        assert row["section"] == "Performance Targets"


class TestImportLevel2SectionHeadings:
    """CB-808: ## level-2 headings should be recognized as section boundaries."""

    def test_l2_heading_resets_section(self, conn):
        md = """# Requirements

### 1.98 Search Quality Benchmark (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Search benchmark | Must | Planned | Arch | -- |

## 2. Non-Functional Requirements

### Performance Targets (NFR-001 -- NFR-002)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Response time < 200ms | Must | Planned | Arch | -- |
| NFR-002 | Uptime 99.9% | Must | Planned | Arch | -- |
"""
        result = _import_md(conn, md)

        assert result["imported"] == 3
        row_fr = conn.execute("SELECT section FROM requirements WHERE id = 'FR-001'").fetchone()
        row_nfr = conn.execute("SELECT section FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row_fr["section"] == "1.98 Search Quality Benchmark"
        assert row_nfr["section"] == "Performance Targets"

    def test_l2_heading_without_number(self, conn):
        md = """## Non-Functional Requirements

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Latency < 200ms | Must | Planned | Arch | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT section FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row["section"] == "Non-Functional Requirements"

    def test_l2_heading_with_number(self, conn):
        md = """## 2. Non-Functional Requirements

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| NFR-001 | Latency < 200ms | Must | Planned | Arch | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT section FROM requirements WHERE id = 'NFR-001'").fetchone()
        assert row["section"] == "2. Non-Functional Requirements"

    def test_l2_does_not_capture_l3(self, conn):
        """Ensure ### headings still take priority over ## for their own rows."""
        md = """## 1. Functional Requirements

### 1.1 Ingestion (FR-001 -- FR-001)

| ID | Requirement | Priority | Status | Source | Test Coverage |
|----|-------------|----------|--------|--------|---------------|
| FR-001 | Ingest PDFs | Must | Planned | R&A | -- |
"""
        _import_md(conn, md)

        row = conn.execute("SELECT section FROM requirements WHERE id = 'FR-001'").fetchone()
        assert row["section"] == "1.1 Ingestion"


class TestUpdateSection:
    """CB-808: reqs_update should support the section field."""

    def test_update_section(self, populated):
        result = reqs.update_requirement(populated, "FR-001", section="2. Non-Functional Requirements")
        assert result["section"] == "2. Non-Functional Requirements"

    def test_update_section_to_empty(self, populated):
        result = reqs.update_requirement(populated, "FR-001", section="")
        assert result["section"] == ""


class TestEmbeddings:
    def test_store_and_retrieve(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="Ingest documents")
        vec = [0.1, 0.2, 0.3, 0.4, 0.5]
        result = reqs.store_embedding(conn, "FR-001", vec)
        assert result["stored"] is True
        assert result["dimensions"] == 5

    def test_store_not_found(self, conn):
        with pytest.raises(KeyError):
            reqs.store_embedding(conn, "FR-999", [0.1, 0.2])

    def test_batch_store(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-002", description="b")
        result = reqs.batch_store_embeddings(conn, {
            "FR-001": [0.1, 0.2, 0.3],
            "FR-002": [0.4, 0.5, 0.6],
        })
        assert result["stored"] == 2

    def test_search_similar(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="Ingest documents")
        reqs.add_requirement(conn, req_id="FR-002", description="Delete documents")
        reqs.add_requirement(conn, req_id="FR-003", description="Search entities")

        reqs.store_embedding(conn, "FR-001", [1.0, 0.0, 0.0])
        reqs.store_embedding(conn, "FR-002", [0.9, 0.1, 0.0])
        reqs.store_embedding(conn, "FR-003", [0.0, 0.0, 1.0])

        results = reqs.search_similar(conn, [1.0, 0.0, 0.0], limit=2)
        assert len(results) == 2
        assert results[0]["id"] == "FR-001"
        assert results[0]["similarity"] == 1.0
        assert results[1]["id"] == "FR-002"
        assert results[1]["similarity"] > 0.9

    def test_search_with_status_filter(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a", status="Implemented")
        reqs.add_requirement(conn, req_id="FR-002", description="b", status="Planned")
        reqs.store_embedding(conn, "FR-001", [1.0, 0.0])
        reqs.store_embedding(conn, "FR-002", [0.9, 0.1])

        results = reqs.search_similar(conn, [1.0, 0.0], status="Planned")
        assert len(results) == 1
        assert results[0]["id"] == "FR-002"

    def test_search_min_similarity(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-002", description="b")
        reqs.store_embedding(conn, "FR-001", [1.0, 0.0])
        reqs.store_embedding(conn, "FR-002", [0.0, 1.0])  # orthogonal

        results = reqs.search_similar(conn, [1.0, 0.0], min_similarity=0.5)
        assert len(results) == 1
        assert results[0]["id"] == "FR-001"

    def test_embedding_stats(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-002", description="b")
        reqs.store_embedding(conn, "FR-001", [0.1, 0.2])

        stats = reqs.embedding_stats(conn)
        assert stats["total"] == 2
        assert stats["embedded"] == 1
        assert stats["missing"] == 1

    def test_pack_unpack_roundtrip(self):
        vec = [0.123, 0.456, 0.789, -1.0, 0.0]
        packed = reqs._pack_vector(vec)
        unpacked = reqs._unpack_vector(packed)
        for a, b in zip(vec, unpacked):
            assert abs(a - b) < 1e-6

    def test_cosine_similarity_identical(self):
        assert reqs._cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0

    def test_cosine_similarity_orthogonal(self):
        assert reqs._cosine_similarity([1, 0], [0, 1]) == 0.0

    def test_cosine_similarity_zero_vector(self):
        assert reqs._cosine_similarity([0, 0], [1, 1]) == 0.0
