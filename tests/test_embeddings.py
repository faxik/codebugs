"""Tests for the embeddings module."""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import reqs
from codebugs import embeddings


@pytest.fixture
def conn():
    """In-memory database with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    reqs.ensure_schema(c)
    yield c
    c.close()


class TestEmbeddings:
    def test_store_and_retrieve(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="Ingest documents")
        vec = [0.1, 0.2, 0.3, 0.4, 0.5]
        result = embeddings.store_embedding(conn, "FR-001", vec)
        assert result["stored"] is True
        assert result["dimensions"] == 5

    def test_store_not_found(self, conn):
        with pytest.raises(KeyError):
            embeddings.store_embedding(conn, "FR-999", [0.1, 0.2])

    def test_batch_store(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-002", description="b")
        result = embeddings.batch_store_embeddings(conn, {
            "FR-001": [0.1, 0.2, 0.3],
            "FR-002": [0.4, 0.5, 0.6],
        })
        assert result["stored"] == 2

    def test_search_similar(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="Ingest documents")
        reqs.add_requirement(conn, req_id="FR-002", description="Delete documents")
        reqs.add_requirement(conn, req_id="FR-003", description="Search entities")

        embeddings.store_embedding(conn, "FR-001", [1.0, 0.0, 0.0])
        embeddings.store_embedding(conn, "FR-002", [0.9, 0.1, 0.0])
        embeddings.store_embedding(conn, "FR-003", [0.0, 0.0, 1.0])

        results = embeddings.search_similar(conn, [1.0, 0.0, 0.0], limit=2)
        assert len(results) == 2
        assert results[0]["id"] == "FR-001"
        assert results[0]["similarity"] == 1.0
        assert results[1]["id"] == "FR-002"
        assert results[1]["similarity"] > 0.9

    def test_search_with_status_filter(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a", status="implemented")
        reqs.add_requirement(conn, req_id="FR-002", description="b", status="planned")
        embeddings.store_embedding(conn, "FR-001", [1.0, 0.0])
        embeddings.store_embedding(conn, "FR-002", [0.9, 0.1])

        results = embeddings.search_similar(conn, [1.0, 0.0], status="planned")
        assert len(results) == 1
        assert results[0]["id"] == "FR-002"

    def test_search_min_similarity(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-002", description="b")
        embeddings.store_embedding(conn, "FR-001", [1.0, 0.0])
        embeddings.store_embedding(conn, "FR-002", [0.0, 1.0])  # orthogonal

        results = embeddings.search_similar(conn, [1.0, 0.0], min_similarity=0.5)
        assert len(results) == 1
        assert results[0]["id"] == "FR-001"

    def test_embedding_stats(self, conn):
        reqs.add_requirement(conn, req_id="FR-001", description="a")
        reqs.add_requirement(conn, req_id="FR-002", description="b")
        embeddings.store_embedding(conn, "FR-001", [0.1, 0.2])

        stats = embeddings.embedding_stats(conn)
        assert stats["total"] == 2
        assert stats["embedded"] == 1
        assert stats["missing"] == 1

    def test_pack_unpack_roundtrip(self):
        vec = [0.123, 0.456, 0.789, -1.0, 0.0]
        packed = embeddings._pack_vector(vec)
        unpacked = embeddings._unpack_vector(packed)
        for a, b in zip(vec, unpacked):
            assert abs(a - b) < 1e-6

    def test_cosine_similarity_identical(self):
        assert embeddings._cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0

    def test_cosine_similarity_orthogonal(self):
        assert embeddings._cosine_similarity([1, 0], [0, 1]) == 0.0

    def test_cosine_similarity_zero_vector(self):
        assert embeddings._cosine_similarity([0, 0], [1, 1]) == 0.0
