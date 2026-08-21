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
        assert embeddings.cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0

    def test_cosine_similarity_orthogonal(self):
        assert embeddings.cosine_similarity([1, 0], [0, 1]) == 0.0

    def test_cosine_similarity_zero_vector(self):
        assert embeddings.cosine_similarity([0, 0], [1, 1]) == 0.0


class TestFalseyStatusFilterDoesNotDisableTheFilter:
    """CB-25, at the similarity-search site. Same defect as the findings and
    requirements query filters; fixed in the same sweep so the seam cannot move."""

    @pytest.mark.parametrize("falsey", [0, False, [], {}])
    def test_falsey_status_raises(self, conn, falsey):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        embeddings.store_embedding(conn, "FR-1", [0.1, 0.2, 0.3])
        with pytest.raises(ValueError, match="Invalid requirement status"):
            embeddings.search_similar(conn, query_embedding=[0.1, 0.2, 0.3], status=falsey)

    @pytest.mark.parametrize("empty", [None, ""])
    def test_none_and_empty_string_still_mean_no_filter(self, conn, empty):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        embeddings.store_embedding(conn, "FR-1", [0.1, 0.2, 0.3])
        got = embeddings.search_similar(conn, query_embedding=[0.1, 0.2, 0.3], status=empty)
        assert len(got) == 1


class InjectingConnection(sqlite3.Connection):
    """Runs a competing writer on ANOTHER connection just before a chosen statement.

    Single-threaded on purpose: the hook fires inside the call under test, before
    the statement it names is handed to SQLite, so the competing write lands in
    exactly the window between the read that decides and the write that acts.

    ``outcome`` records which writer won that window — the discriminator required
    by CLAUDE.md, Testing (a), because after the fix the stored state is the same
    state the unfixed code reaches on a quiet database. The competing connection
    carries a short ``busy_timeout`` because after the fix it can never acquire the
    lock, and waiting for it unboundedly is Testing (b).

    This project deliberately has no shared ``conftest.py`` for fixtures, so this
    class is duplicated in the test files that need it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inject_before: str | None = None
        self.injection = None
        self.outcome: str | None = None

    def execute(self, sql, *args, **kwargs):
        if self.inject_before is not None and self.inject_before in sql:
            self.inject_before = None  # one-shot
            try:
                self.injection()
                self.outcome = "landed"
            except sqlite3.OperationalError as e:
                self.outcome = f"refused: {e}"
        return super().execute(sql, *args, **kwargs)


def _file_conn(path, *, factory=sqlite3.Connection, busy_ms=5000):
    c = sqlite3.connect(path, factory=factory)
    c.row_factory = sqlite3.Row
    c.execute(f"PRAGMA busy_timeout={busy_ms}")
    return c


class TestStoreEmbeddingIsOneTransaction:
    """CB-125 / CB-24: ``"stored": True`` must be the write's receipt, not an assertion.

    The existence read and the UPDATE were two steps with no transaction spanning
    them, and the response said ``stored`` unconditionally. A requirement deleted
    in that window left the UPDATE matching zero rows while the caller was told the
    vector had been stored — a success-shaped lie about a write that never happened.
    """

    def test_a_requirement_deleted_in_the_window_cannot_be_reported_as_stored(self, tmp_path):
        path = str(tmp_path / "reqs.db")
        main = _file_conn(path, factory=InjectingConnection)
        reqs.ensure_schema(main)
        reqs.add_requirement(main, req_id="FR-1", description="a")

        other = _file_conn(path, busy_ms=150)

        def competing_delete():
            other.execute("DELETE FROM requirements WHERE id = 'FR-1'")
            other.commit()

        main.injection = competing_delete
        main.inject_before = "UPDATE requirements SET embedding"
        try:
            result = embeddings.store_embedding(main, "FR-1", [0.1, 0.2, 0.3])
        finally:
            other.close()

        assert main.outcome is not None, "the injection point was never reached"
        assert main.outcome.startswith("refused"), main.outcome
        assert result["stored"] is True
        row = main.execute("SELECT embedding FROM requirements WHERE id = 'FR-1'").fetchone()
        assert row is not None and row["embedding"] is not None, (
            "reported stored over a row that carries no vector"
        )
        main.close()
