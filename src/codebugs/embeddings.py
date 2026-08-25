"""Embedding storage and similarity search for requirements.

SAFETY PRECONDITIONS — WHAT THE CALLER HANDS US AND WHERE IT GOES (CB-174).

There is no embedding provider inside this package. The CALLER computes the
vector, in its own process, and passes the finished numbers as the
``embedding`` argument; ``codebugs`` stores them in its own SQLite file and
sends them nowhere. The tools here never receive the requirement's TEXT at all
— ``reqs_embed``/``reqs_batch_embed`` take an id and a list of floats.

The claim is bounded to THIS PACKAGE'S OWN CODE and to the vector's route, and
that bound is deliberate rather than modest: the ``mcp`` dependency does carry
a network transport (``server.py`` says so — an HTTP mode exists, and this
project runs over stdio). What is checkable, and what is checked, is that no
module of ``codebugs`` imports a network capability at all;
``tests/test_no_network_capability.py`` is the gate, because a safety claim
with no gate behind it is a "gate that cannot fire" written as prose.

WHAT THIS MODULE REFUSES, AND WHY IT HAS TO. Nothing here knows the "right"
dimensionality, so before CB-174 a vector of any width landed beside vectors of
any other, and ``search_similar`` then died on the first pair
``cosine_similarity`` could not compare — the whole search, not one row. A
``NaN`` was quieter and worse: it stored fine, scored ``nan``, and
``nan >= min_similarity`` is ``False``, so the row simply VANISHED from the
results and "nothing similar" became indistinguishable from an empty tracker.
Both are refused at the write now, and search folds the width test into SQL so
a foreign row never reaches the comparison.

RESIDUAL, NAMED RATHER THAN CLOSED. Once a tracker holds vectors of one width,
there is no sanctioned way to switch embedding model: this package has no
clear-and-re-embed operation, and building one with no caller asking for it was
deliberately refused (the CB-44/CB-45 precedent — a seam is built with its first
consumer). ``embedding_stats`` reports the widths actually present so the state
is at least nameable.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from typing import Any

from codebugs import db
from codebugs.types import is_vocabulary_filter_active, resolve_requirement_status, utc_now

# ``struct.pack("<f")`` is little-endian float32, so one component is 4 bytes.
# PREMISE: ``length()`` on a BLOB in SQLite counts BYTES (a 3-component vector
# measures 12), which is what lets the width test live in SQL rather than in a
# per-row Python predicate. Pinned by
# ``tests/test_embeddings.py::TestSqliteBlobLengthPremise`` so a SQLite that
# changed the behaviour would turn the suite red instead of quietly disarming
# the read-side guard.
_BYTES_PER_COMPONENT = 4


def _reject_unusable_vector(vec: list[float], *, what: str) -> None:
    """Refuse a vector that would make similarity search wrong or silent.

    Decidable FROM THE ARGUMENT ALONE, so it runs BEFORE any transaction is
    opened: a bad input must not take the write lock (the same reason
    ``store_embedding`` already packed its vector before ``db.txn``). The
    tracker-agreement test is the opposite case and lives inside the
    transaction — see ``_reject_width_the_tracker_disagrees_with``.

    Three refusals, each measured on the pre-CB-174 code:

    * EMPTY — ``_pack_vector([])`` returns ``b""`` and a zero-width row stored
      perfectly happily, matching nothing and scoring against nothing.
    * NON-NUMERIC — reached ``struct.pack`` and surfaced as ``struct.error``,
      which is not the ``ValueError`` this package's modules promise for bad
      input.
    * ``NaN`` / ``inf`` — ``struct.pack`` accepts both silently. A stored
      ``NaN`` disappears from every result set without a word, and a ``NaN`` in
      a QUERY vector removes every row, so "nothing is similar" is returned for
      a tracker full of vectors. That is the silent-empty-queue shape (CB-19,
      CB-25), which this repository treats as worse than a loud failure.

    ``bool`` passes, because it is an ``int`` and packed fine before this
    change; narrowing that would be an unrequested behaviour change riding
    along inside a validation fix (CB-82).
    """
    if len(vec) == 0:
        raise ValueError(
            f"{what} is empty: a zero-dimensional vector can never match anything, "
            "and it would be stored as an empty blob indistinguishable from a "
            "corrupt one"
        )
    for i, component in enumerate(vec):
        if not isinstance(component, (int, float)):
            raise ValueError(
                f"{what}[{i}] is {type(component).__name__}, not a number: "
                "an embedding is a list of floats"
            )
        if not math.isfinite(component):
            raise ValueError(
                f"{what}[{i}] is {component!r}: NaN and infinity store without "
                "complaint and then make the row score as NaN, which compares "
                "False against every threshold — the row would vanish from search "
                "results with no error anywhere"
            )


def _describe_width(byte_width: int) -> str:
    """A byte width, said the way a caller thinks about it.

    Falls back to naming the bytes when the length is not a whole number of
    components, rather than rounding and reporting a dimension count that is
    not what is stored.
    """
    if byte_width % _BYTES_PER_COMPONENT:
        return f"{byte_width}-byte (not a whole number of components)"
    return f"{byte_width // _BYTES_PER_COMPONENT}-dimensional"


def _stored_byte_widths(conn: sqlite3.Connection) -> list[int]:
    """Distinct stored vector widths, in BYTES — the same quantity SQL compares.

    Bytes rather than components deliberately, so the write guard and
    ``search_similar``'s ``WHERE`` decide on ONE number. Dividing by four here
    would make them two rules a rounding apart: a blob whose length is not a
    multiple of four (reachable only by writing the column directly, but
    reachable) divides to the same component count as a well-formed neighbour,
    so a component-wise write guard would accept a vector beside a row the
    byte-wise read guard then excludes — the tracker would look uniform to the
    writer and mixed to the reader. That is the "two copies of one precedence
    table" drift this repository keeps relearning (CB-22, CB-52).
    """
    rows = conn.execute(
        "SELECT DISTINCT length(embedding) FROM requirements WHERE embedding IS NOT NULL"
    ).fetchall()
    return sorted({row[0] for row in rows})


def _reject_width_the_tracker_disagrees_with(
    conn: sqlite3.Connection, dimensions: int
) -> None:
    """Refuse a vector whose width disagrees with what the tracker already holds.

    THIS IS A CHECK-THEN-ACT AND IT MUST RUN INSIDE THE WRITING TRANSACTION.
    Outside one, two concurrent writers carrying different widths both read an
    empty table, both pass, and both write — the caller would have built the
    exact mixed state this guard exists to prevent. That is CB-24's shape
    verbatim: a value computed in Python from a row just read has to be written
    back inside ONE transaction with that read, and ``busy_timeout`` cannot
    help because it serializes the writes and never touches the read before
    them. ``db.txn`` issues ``BEGIN IMMEDIATE``, taking the write lock BEFORE
    the read, which is exactly why it is the right wrapper here.
    """
    wanted = dimensions * _BYTES_PER_COMPONENT
    conflicting = [n for n in _stored_byte_widths(conn) if n != wanted]
    if conflicting:
        widths = ", ".join(_describe_width(n) for n in conflicting)
        raise ValueError(
            f"embedding dimension mismatch: this tracker already stores "
            f"{widths} vectors and this write carries {dimensions}-dimensional. "
            "Vectors of mixed width cannot be compared, so one foreign row is "
            "enough to make reqs_search_similar unable to score the rest. NOTE "
            "the residual this refusal exposes: there is no sanctioned way to "
            "change embedding model here — no clear-and-re-embed operation "
            "exists in this package, and one was deliberately not built ahead of "
            "a caller asking for it (CB-174)."
        )


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
    that is wrong in BOTH ways — unknown ``req_id`` AND an unusable vector —
    is decided by the VECTOR. That was true of CB-125's ``struct.error`` from
    the pack and it stays true of CB-174's ``ValueError`` from the pre-flight
    validation, which now runs first and gives the module's documented
    exception type instead of ``struct``'s. Either argument alone is
    unaffected. Restoring the old order would mean re-adding the very read
    CB-125 removed.

    Raises:
        ValueError: the vector itself is unusable (empty, non-numeric, ``NaN``
            or ``inf``), or its width disagrees with what this tracker already
            stores. See CB-174 and the module docstring.
        KeyError: no requirement with this id. CHANGED by CB-125: a requirement
            deleted concurrently now reaches this arm instead of being reported
            as a successful store.
    """
    _reject_unusable_vector(embedding, what="embedding")
    blob = _pack_vector(embedding)
    with db.txn(conn):
        # INSIDE the transaction on purpose: this one reads the table, so it is
        # a check-then-act. See _reject_width_the_tracker_disagrees_with.
        _reject_width_the_tracker_disagrees_with(conn, len(embedding))
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

    THE BATCH MUST BE HOMOGENEOUS WITH ITSELF, and that is a separate rule from
    agreeing with the tracker rather than a special case of it (CB-174): in an
    EMPTY tracker there is nothing to compare against, so a single call
    carrying two different widths would pass a tracker-agreement test and
    create, in one operation, exactly the mixed state the test exists to
    prevent. Self-consistency is decidable from the argument, so it is checked
    before the transaction opens; agreement with the tracker needs a read and
    therefore runs inside it.

    Args:
        embeddings: Dict mapping req_id -> vector

    Raises:
        ValueError: some vector is unusable (empty, non-numeric, ``NaN`` or
            ``inf``), the batch carries more than one width, or that width
            disagrees with what this tracker already stores.
    """
    for req_id, vec in embeddings.items():
        _reject_unusable_vector(vec, what=f"embedding for {req_id}")
    widths = sorted({len(vec) for vec in embeddings.values()})
    if len(widths) > 1:
        raise ValueError(
            "embedding dimension mismatch inside one batch: this call carries "
            f"{', '.join(str(n) for n in widths)}-dimensional vectors. Vectors "
            "of mixed width cannot be compared, and accepting this batch would "
            "put the tracker into a state where reqs_search_similar can no "
            "longer score every row (CB-174)."
        )
    now = utc_now()
    stored = 0
    # ``db.txn`` rather than a bare loop plus ``conn.commit()``, because the
    # width test below READS the table and then writes to it: outside a
    # transaction that is an unsynchronized check-then-act (measured: a plain
    # SELECT does not open one — ``conn.in_transaction`` stays False until the
    # first UPDATE), so two concurrent batches of different widths would both
    # pass. This is the transaction ``batch_store_embeddings`` was already
    # missing; CB-184 owns the REST of that gap — a requirement that does not
    # exist is still silently counted as not-stored here rather than raising
    # ``KeyError`` the way its twin above does — and that contract change is
    # deliberately NOT made here.
    with db.txn(conn):
        if widths:
            _reject_width_the_tracker_disagrees_with(conn, widths[0])
        for req_id, vec in embeddings.items():
            blob = _pack_vector(vec)
            cursor = conn.execute(
                "UPDATE requirements SET embedding = ?, updated_at = ? WHERE id = ?",
                (blob, now, req_id),
            )
            if cursor.rowcount > 0:
                stored += 1
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

    A ROW OF ANOTHER WIDTH IS EXCLUDED IN SQL, NOT SKIPPED IN PYTHON (CB-174).
    ``cosine_similarity`` raises on mismatched widths, and that ``raise`` is a
    ratified decision this does not touch: ``zip()`` would truncate the dot
    product while the norms stayed full, returning a plausible wrong number
    instead of an error. The defect was never that refusal, it was the
    COMPOSITION — one foreign row aborted the whole loop, discarding the rows
    already scored, in an order nothing controls. Folding the width test into
    the ``WHERE`` makes that ``raise`` UNREACHABLE from this path instead of
    removing it; the precedent for the form is
    ``milestones.reconcile.live_source_clause``, where an exclusion is
    likewise SQL rather than a per-row Python predicate. The width is BOUND,
    never interpolated.

    The cost is named rather than hidden: excluded rows become INVISIBLE, and
    this function returns a LIST, so it has nowhere to report a count the way
    ``add``'s ``stripped_meta_keys`` does. ``embedding_stats`` is the channel
    instead — it reports which widths the tracker actually holds, so a mixed
    table is nameable rather than silently half-searched.

    Args:
        query_embedding: The query vector
        limit: Max results
        min_similarity: Minimum cosine similarity threshold (0.0-1.0)
        status: Optional status filter

    Raises:
        ValueError: the query vector is unusable, or ``status`` is not a
            requirement status. A ``NaN`` in the QUERY is refused for the same
            reason as one on the write path, and here the argument is stronger:
            every row scores ``nan``, every comparison against the threshold is
            ``False``, and the caller is handed an empty list for a tracker
            that is full.
    """
    _reject_unusable_vector(query_embedding, what="query_embedding")
    conditions = ["embedding IS NOT NULL"]
    params: list[Any] = []
    conditions.append("length(embedding) = ?")
    params.append(len(query_embedding) * _BYTES_PER_COMPONENT)
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
    """Report on embedding coverage, INCLUDING which vector widths are present.

    The width breakdown is not decoration — it is the visibility channel for
    CB-174's read-side guard. ``search_similar`` excludes a row of another width
    in SQL and returns a plain list, which cannot carry a note about what it
    dropped, so without this a tracker that quietly went mixed would answer
    "nothing is similar" forever with nothing anywhere naming the cause.

    ``dimensions`` and ``mixed`` are UNCONDITIONAL keys, following the
    ``attention`` / ``stripped_meta_keys`` discipline: an empty list means
    *looked, no vectors stored*, never *no such channel*.
    """
    total = conn.execute("SELECT COUNT(*) as c FROM requirements").fetchone()["c"]
    embedded = conn.execute(
        "SELECT COUNT(*) as c FROM requirements WHERE embedding IS NOT NULL"
    ).fetchone()["c"]
    missing = conn.execute(
        "SELECT id, section FROM requirements WHERE embedding IS NULL ORDER BY id"
    ).fetchall()
    widths = conn.execute(
        "SELECT length(embedding) AS n, COUNT(*) AS c FROM requirements "
        "WHERE embedding IS NOT NULL GROUP BY length(embedding) ORDER BY n"
    ).fetchall()
    # Grouped by BYTE length, the quantity every guard here decides on, and the
    # byte count is reported beside the component count rather than folded into
    # it: two blobs can divide to the same dimension and still be different
    # widths, and a report that hid that would be the "clean because it could
    # not look" shape.
    by_width = [
        {
            "dimensions": r["n"] // _BYTES_PER_COMPONENT,
            "bytes": r["n"],
            "count": r["c"],
        }
        for r in widths
    ]
    return {
        "total": total,
        "embedded": embedded,
        "missing": total - embedded,
        "missing_ids": [{"id": r["id"], "section": r["section"]} for r in missing[:20]],
        "dimensions": by_width,
        "mixed": len(by_width) > 1,
    }


def register_tools(mcp, conn_factory):
    """Register embedding MCP tools on the given MCP server."""

    @mcp.tool()
    def reqs_embed(
        req_id: str,
        embedding: list[float],
    ) -> dict[str, Any]:
        """Store an embedding vector for a requirement.

        YOU compute the embedding, in your own process, and pass the finished
        numbers here. This tool never receives the requirement's text. codebugs
        stores the vector in its own local SQLite file and sends it nowhere —
        no module of this package imports a network capability at all, which is
        enforced by a test rather than merely asserted here. (Scope, stated
        precisely: that is a claim about this package's own code and about the
        route your vector takes. The MCP transport itself is a separate layer.)

        Because there is no embedding provider inside codebugs, nothing here
        knows the "right" dimensionality — it is whatever the first stored
        vector had. So the vector is refused if it is empty, contains a
        non-number, contains NaN or infinity, or has a different number of
        components than the vectors already stored in this tracker. Each of
        those would otherwise break reqs_search_similar: a mismatched width
        makes it unable to score the other rows, and a NaN makes a row drop out
        of every result with no error at all.

        Once a tracker holds vectors of one width you cannot switch embedding
        model: there is no clear-and-re-embed operation in this package.
        reqs_embedding_stats reports which widths are actually present.

        Args:
            req_id: Requirement ID
            embedding: Float vector. Any dimensionality, but the SAME one for
                every requirement in a given tracker.
        """
        with conn_factory() as conn:
            return store_embedding(conn, req_id, embedding)

    @mcp.tool()
    def reqs_batch_embed(
        embeddings: dict[str, list[float]],
    ) -> dict[str, Any]:
        """Store embeddings for multiple requirements at once.

        Same preconditions as reqs_embed: you compute the vectors yourself and
        pass finished numbers, the requirement text never reaches this tool,
        and codebugs stores them locally and sends them nowhere.

        Every vector in one call must have the same number of components as
        every other vector in the call AND as the vectors already stored in
        this tracker; empty vectors, non-numbers, NaN and infinity are refused.
        The self-consistency rule is a separate one: in an empty tracker there
        is nothing to compare against, so without it a single call could create
        the mixed state the rules exist to prevent.

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
        You compute it yourself; no text is sent anywhere by this tool, and the
        query vector is not stored.

        Requirements whose stored vector has a different number of components
        than your query are EXCLUDED from the search rather than compared, so
        one foreign vector can no longer make the whole search fail. That also
        means they are invisible here: if you get fewer results than you
        expect, call reqs_embedding_stats, which reports which widths this
        tracker holds. A query vector that is empty or contains NaN or infinity
        is refused, because it would match nothing and return an empty list
        indistinguishable from an empty tracker.

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
        """Report on embedding coverage --- how many requirements have embeddings.

        This tool takes no input at all, so it is not a privacy surface and
        carries no precondition block of its own; that is said explicitly
        rather than left as an omission a reader has to interpret.

        Beyond coverage it reports `dimensions` --- which vector widths this
        tracker actually holds, and how many rows each --- plus `mixed`, true
        when there is more than one. That is the channel for noticing a tracker
        that received vectors from two different embedding models:
        reqs_search_similar silently excludes rows of a width other than your
        query's, and being able to see the split here is what keeps that from
        looking like "nothing is similar". Both keys are always present; an
        empty `dimensions` list means no vectors are stored, never that the
        check did not run.
        """
        with conn_factory() as conn:
            return embedding_stats(conn)
