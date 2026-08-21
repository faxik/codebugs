"""Embedding storage and similarity search for requirements."""

from __future__ import annotations

import math
import sqlite3
import struct
from typing import Any

from codebugs import db
from codebugs.types import is_vocabulary_filter_active, resolve_requirement_status, utc_now


def _pack_vector(vec: list[float]) -> bytes:
    """Pack a float vector into bytes (little-endian float32)."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vector(blob: bytes) -> list[float]:
    """Unpack bytes into a float vector."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. The package's ONE copy.

    Mismatched dimensions raise: zip() would silently truncate the dot product
    while the norms use all components — a plausible-looking wrong number
    instead of an error. (Stored vectors of differing width can only come from
    corrupt or mixed-model data, which must stay loud.)
    """
    if len(a) != len(b):
        raise ValueError(f"vector dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def store_embedding(
    conn: sqlite3.Connection,
    req_id: str,
    embedding: list[float],
) -> dict[str, Any]:
    """Store an embedding vector for a requirement.

    The caller is responsible for generating the embedding (e.g. via an
    embedding API). This function just stores and retrieves.

    ``"stored": True`` is the WRITE'S RECEIPT, not an assertion (CB-24, CB-125).
    The existence SELECT that used to precede the UPDATE decided nothing the
    UPDATE cannot decide itself, and the two were separate statements with no
    transaction spanning them: a requirement deleted in that window left the
    UPDATE matching zero rows while the caller was told the vector had been
    stored. The UPDATE now carries ``RETURNING`` and a row that is not there
    raises the same ``KeyError`` — so the response cannot outrun the write. Never
    read ``rowcount`` on a ``RETURNING`` statement: it is 0 until the cursor is
    exhausted, which would report nothing-happened over a landed write.

    ``db.txn`` earns its place even on a single-statement write: it owns the
    commit, so this can be called inside a caller's transaction without
    committing the caller's unrelated work (CB-24 consequence 1). The vector is
    packed BEFORE the block, so a malformed one is refused without taking the
    write lock.

    One more precedence shift falls out of that and is declared rather than
    papered over (Codex diff review): with existence now decided BY the UPDATE
    there is no earlier point at which a missing id can be detected, so a call
    that is wrong in BOTH ways — unknown ``req_id`` AND an unpackable vector —
    now raises ``struct.error`` from the pack where it used to raise ``KeyError``
    from the existence read. Either argument alone is unaffected. Restoring the
    old order would mean re-adding the very read this removed.

    Raises:
        KeyError: no requirement with this id. CHANGED by CB-125: a requirement
            deleted concurrently now reaches this arm instead of being reported
            as a successful store.
    """
    blob = _pack_vector(embedding)
    with db.txn(conn):
        row = conn.execute(
            "UPDATE requirements SET embedding = ?, updated_at = ? WHERE id = ? RETURNING id",
            (blob, utc_now(), req_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Requirement not found: {req_id}")
    return {"id": req_id, "dimensions": len(embedding), "stored": True}


def batch_store_embeddings(
    conn: sqlite3.Connection,
    embeddings: dict[str, list[float]],
) -> dict[str, Any]:
    """Store embeddings for multiple requirements at once.

    Args:
        embeddings: Dict mapping req_id -> vector
    """
    now = utc_now()
    stored = 0
    for req_id, vec in embeddings.items():
        blob = _pack_vector(vec)
        cursor = conn.execute(
            "UPDATE requirements SET embedding = ?, updated_at = ? WHERE id = ?",
            (blob, now, req_id),
        )
        if cursor.rowcount > 0:
            stored += 1
    conn.commit()
    return {"stored": stored, "total": len(embeddings)}


def search_similar(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    *,
    limit: int = 10,
    min_similarity: float = 0.0,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Find requirements most similar to a query embedding.

    Uses brute-force cosine similarity (fine for <10K requirements).

    Args:
        query_embedding: The query vector
        limit: Max results
        min_similarity: Minimum cosine similarity threshold (0.0-1.0)
        status: Optional status filter
    """
    conditions = ["embedding IS NOT NULL"]
    params: list[Any] = []
    if is_vocabulary_filter_active(status):
        conditions.append("status = ?")
        # Resolved like every other status filter (CB-19 sibling sweep): raw, this
        # silently returned no similar requirements for a correctly-spelled-but-
        # differently-cased status, which reads as "nothing is similar".
        params.append(resolve_requirement_status(status))

    where = f"WHERE {' AND '.join(conditions)}"
    rows = conn.execute(
        f"SELECT * FROM requirements {where}", params,
    ).fetchall()

    scored = []
    for row in rows:
        vec = _unpack_vector(row["embedding"])
        sim = cosine_similarity(query_embedding, vec)
        if sim >= min_similarity:
            d = db.row_to_dict(row)
            d.pop("embedding", None)  # Don't return the blob
            d["similarity"] = round(sim, 4)
            scored.append(d)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:limit]


def embedding_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Report on embedding coverage."""
    total = conn.execute("SELECT COUNT(*) as c FROM requirements").fetchone()["c"]
    embedded = conn.execute(
        "SELECT COUNT(*) as c FROM requirements WHERE embedding IS NOT NULL"
    ).fetchone()["c"]
    missing = conn.execute(
        "SELECT id, section FROM requirements WHERE embedding IS NULL ORDER BY id"
    ).fetchall()
    return {
        "total": total,
        "embedded": embedded,
        "missing": total - embedded,
        "missing_ids": [{"id": r["id"], "section": r["section"]} for r in missing[:20]],
    }


def register_tools(mcp, conn_factory):
    """Register embedding MCP tools on the given MCP server."""

    @mcp.tool()
    def reqs_embed(
        req_id: str,
        embedding: list[float],
    ) -> dict[str, Any]:
        """Store an embedding vector for a requirement.

        The caller generates the embedding (e.g. via an embedding API).
        Enables semantic search across requirements via reqs_search_similar.

        Args:
            req_id: Requirement ID
            embedding: Float vector (any dimensionality)
        """
        with conn_factory() as conn:
            return store_embedding(conn, req_id, embedding)

    @mcp.tool()
    def reqs_batch_embed(
        embeddings: dict[str, list[float]],
    ) -> dict[str, Any]:
        """Store embeddings for multiple requirements at once.

        Args:
            embeddings: Dict mapping requirement ID to float vector
        """
        with conn_factory() as conn:
            return batch_store_embeddings(conn, embeddings)

    @mcp.tool()
    def reqs_search_similar(
        query_embedding: list[float],
        limit: int = 10,
        min_similarity: float = 0.3,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find requirements semantically similar to a query.

        Pass a query embedding (from the same model used to embed requirements).
        Returns requirements ranked by cosine similarity.

        Args:
            query_embedding: Query vector
            limit: Max results (default 10)
            min_similarity: Minimum cosine similarity (default 0.3)
            status: Optional status filter
        """
        with conn_factory() as conn:
            return search_similar(
                conn, query_embedding, limit=limit,
                min_similarity=min_similarity, status=status,
            )

    @mcp.tool()
    def reqs_embedding_stats() -> dict[str, Any]:
        """Report on embedding coverage --- how many requirements have embeddings."""
        with conn_factory() as conn:
            return embedding_stats(conn)
