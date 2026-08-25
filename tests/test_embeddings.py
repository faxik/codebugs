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


# ---------------------------------------------------------------------------
# CB-174 --- the write validates the vector, and search survives a foreign one.
# ---------------------------------------------------------------------------


def _store_raw(conn, req_id: str, blob: bytes) -> None:
    """Put a blob into the embedding column WITHOUT going through validation.

    This is how the mixed-width and NaN states are reached in these fixtures,
    and reaching them that way is honest: before CB-174 the ordinary tools
    produced exactly these states, so every tracker written by an older version
    can still hold one. A fixture that could only be built through the new
    guards would be proving the guards against themselves.
    """
    conn.execute("UPDATE requirements SET embedding = ? WHERE id = ?", (blob, req_id))
    conn.commit()


class TestSqliteBlobLengthPremise:
    """PREMISE, not behaviour: ``length()`` on a BLOB in SQLite counts BYTES.

    The read-side guard in ``search_similar`` and the width breakdown in
    ``embedding_stats`` both divide that byte count by four to recover a
    component count. If a SQLite release ever made ``length()`` mean something
    else on a blob, both would go quietly wrong — the guard would exclude every
    row, which reads as "nothing is similar". Pinning it means that day turns
    the suite red instead of disarming the guard in silence, the same reason
    this tree pins git and argparse behaviours it depends on.
    """

    def test_length_of_a_blob_is_its_byte_count(self, conn):
        packed = embeddings._pack_vector([1.0, 2.0, 3.0])
        assert len(packed) == 3 * embeddings._BYTES_PER_COMPONENT
        conn.execute("CREATE TABLE _probe (b BLOB)")
        conn.execute("INSERT INTO _probe VALUES (?)", (packed,))
        row = conn.execute("SELECT length(b), typeof(b) FROM _probe").fetchone()
        assert row[0] == 12
        assert row[1] == "blob"


class TestTheWriteRefusesAnUnusableVector:
    """The vector's own unfitness is decidable from the argument alone."""

    @pytest.mark.parametrize(
        "bad, expected",
        [
            ([], "is empty"),
            ([0.1, float("nan"), 0.3], "nan"),
            ([0.1, float("inf")], "inf"),
            ([0.1, float("-inf")], "inf"),
            ([0.1, "0.2"], "not a number"),
            ([0.1, None], "not a number"),
        ],
        ids=["empty", "nan", "inf", "-inf", "string", "none"],
    )
    def test_store_embedding_refuses(self, conn, bad, expected):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        with pytest.raises(ValueError) as excinfo:
            embeddings.store_embedding(conn, "FR-1", bad)
        assert expected in str(excinfo.value).lower()
        row = conn.execute("SELECT embedding FROM requirements WHERE id = 'FR-1'").fetchone()
        assert row["embedding"] is None, "a refused vector must land nothing"

    @pytest.mark.parametrize(
        "bad, expected",
        [
            ([], "is empty"),
            ([0.1, float("nan"), 0.3], "nan"),
            ([0.1, float("inf")], "inf"),
            ([0.1, "0.2"], "not a number"),
        ],
        ids=["empty", "nan", "inf", "string"],
    )
    def test_batch_store_embeddings_refuses(self, conn, bad, expected):
        """The SAME refusals on the batch path.

        MUTANT: drop the per-vector validation from ``batch_store_embeddings``
        and leave it in ``store_embedding`` --- this class goes red while the
        single-write class above stays green, which is what "a rule expressed
        as one call site is the wrong rule" looks like when it is measured.
        """
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        with pytest.raises(ValueError) as excinfo:
            embeddings.batch_store_embeddings(conn, {"FR-1": bad})
        assert expected in str(excinfo.value).lower()
        row = conn.execute("SELECT embedding FROM requirements WHERE id = 'FR-1'").fetchone()
        assert row["embedding"] is None, "a refused batch must land nothing"

    def test_a_bool_still_packs(self, conn):
        """Declared, not incidental: ``bool`` is an ``int`` and stored fine before
        CB-174, so refusing it would be an unrequested behaviour change riding
        along inside a validation fix (CB-82's rule)."""
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        assert embeddings.store_embedding(conn, "FR-1", [True, False])["stored"] is True


class TestTheWriteRefusesAWidthTheTrackerDisagreesWith:
    """Oracle 1: rejected on BOTH write paths, not just the one someone listed."""

    def test_store_embedding_refuses_a_foreign_width(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        with pytest.raises(ValueError) as excinfo:
            embeddings.store_embedding(conn, "FR-2", [1.0, 0.0])
        message = str(excinfo.value)
        assert "3" in message and "2" in message, "the refusal must name BOTH widths"
        row = conn.execute("SELECT embedding FROM requirements WHERE id = 'FR-2'").fetchone()
        assert row["embedding"] is None

    def test_batch_store_embeddings_refuses_a_foreign_width(self, conn):
        """MUTANT: remove the tracker-agreement check from
        ``batch_store_embeddings`` only --- this test goes red and its
        single-write sibling stays green."""
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        with pytest.raises(ValueError) as excinfo:
            embeddings.batch_store_embeddings(conn, {"FR-2": [1.0, 0.0]})
        assert "3" in str(excinfo.value) and "2" in str(excinfo.value)
        row = conn.execute("SELECT embedding FROM requirements WHERE id = 'FR-2'").fetchone()
        assert row["embedding"] is None

    def test_the_matching_width_is_still_accepted(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        assert embeddings.batch_store_embeddings(
            conn, {"FR-2": [0.0, 1.0, 0.0]}
        ) == {"stored": 1, "total": 1}

    def test_the_refusal_names_the_missing_way_out(self, conn):
        """The residual is carried BY the refusal, not only by a design note.

        There is no clear-and-re-embed operation in this package, so a caller
        who changes embedding model meets this wall with no sanctioned path
        through it. A gate with no way out is a wall rather than a diagnostic
        unless it says so, and this one says so.
        """
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        with pytest.raises(ValueError) as excinfo:
            embeddings.store_embedding(conn, "FR-2", [1.0, 0.0])
        assert "re-embed" in str(excinfo.value)


class TestABatchMustBeHomogeneousWithItself:
    """Oracle 1a --- a SEPARATE rule, not a special case of tracker agreement.

    In an EMPTY tracker there is nothing to compare against, so a check that
    only asks "does this agree with what is stored" waves the whole batch
    through and the single operation creates the mixed state it exists to
    prevent.
    """

    def test_two_widths_in_one_call_are_refused_in_an_empty_tracker(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        assert embeddings.embedding_stats(conn)["embedded"] == 0, (
            "the premise of this test is an EMPTY tracker"
        )
        with pytest.raises(ValueError) as excinfo:
            embeddings.batch_store_embeddings(
                conn, {"FR-1": [1.0, 0.0, 0.0], "FR-2": [1.0, 0.0]}
            )
        assert "inside one batch" in str(excinfo.value)
        assert embeddings.embedding_stats(conn)["embedded"] == 0, (
            "a refused batch must land nothing"
        )

    def test_an_empty_batch_is_still_a_no_op(self, conn):
        assert embeddings.batch_store_embeddings(conn, {}) == {"stored": 0, "total": 0}


class TestTheWidthCheckLivesInsideTheWritingTransaction:
    """Oracle 1b --- STRUCTURAL, and structural on purpose.

    A behavioural test cannot discriminate this defect, for the same reason
    CB-41's SQL-template test could not be behavioural: a comparison made
    before the write lock is taken looks perfectly correct until two writers
    happen to overlap. What is checkable is placement. The width test READS the
    table and then the function writes to it, so outside a transaction it is an
    unsynchronized check-then-act: two concurrent writers of different widths
    both read an empty table, both pass, both write.

    MUTANT: move the ``_reject_width_the_tracker_disagrees_with`` call above the
    ``with db.txn(conn):`` line in either function --- this class goes red.

    **"INSIDE A TRANSACTION" IS NOT THE PROPERTY; "INSIDE THE SAME ONE AS THE
    WRITE" IS.** The first version of this class asked only whether the guard
    sat inside *some* ``db.txn`` block, and adversarial review broke it with a
    mutant that splits one transaction into two consecutive ones --- check in
    the first, which commits and releases the lock, write in the second.
    Measured against that mutant: ``BEGIN IMMEDIATE`` fired twice, a competing
    writer landed in the gap, the tracker ended mixed
    (``[('FR-1', 12), ('FR-2', 8)]``), and the whole file stayed green at
    53 passed. That is verbatim the check-then-act this class exists to
    forbid, so the assertion is now about ONE block holding BOTH.

    A test that validates elements cannot validate their composition --- this
    repository's own rule, turned on a test written to enforce it.
    """

    GUARD = "_reject_width_the_tracker_disagrees_with"

    def _function(self, name):
        import ast
        import pathlib

        src = pathlib.Path(embeddings.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found in embeddings.py")

    @staticmethod
    def _txn_blocks(func):
        import ast

        blocks = []
        for node in ast.walk(func):
            if isinstance(node, ast.With):
                for item in node.items:
                    call = item.context_expr
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "txn"
                    ):
                        blocks.append(node)
        return blocks

    @staticmethod
    def _guard_calls(node, guard):
        import ast

        return [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == guard
        ]

    @staticmethod
    def _writes(node):
        """UPDATE statements on ``requirements`` reachable from ``node``."""
        import ast

        found = []
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if n.value.strip().upper().startswith("UPDATE REQUIREMENTS"):
                    found.append(n)
        return found

    @pytest.mark.parametrize("name", ["store_embedding", "batch_store_embeddings"])
    def test_the_guard_and_the_write_share_ONE_db_txn_block(self, name):
        func = self._function(name)
        in_function = self._guard_calls(func, self.GUARD)
        assert len(in_function) == 1, (
            f"{name} must call {self.GUARD} exactly once; found {len(in_function)}"
        )
        assert self._writes(func), f"{name} must issue an UPDATE -- fixture check"

        blocks = self._txn_blocks(func)
        assert blocks, f"{name} must open a db.txn block"
        assert len(blocks) == 1, (
            f"{name} opens {len(blocks)} db.txn blocks. Two consecutive "
            "transactions put the check in one and the write in another, which "
            "releases the write lock between them -- exactly the "
            "unsynchronized check-then-act this is here to forbid."
        )

        block = blocks[0]
        guarded = [c for stmt in block.body for c in self._guard_calls(stmt, self.GUARD)]
        written = [w for stmt in block.body for w in self._writes(stmt)]
        assert len(guarded) == 1, (
            f"{name} calls {self.GUARD} outside its db.txn block -- that is an "
            "unsynchronized check-then-act (CB-24's shape)"
        )
        assert written, (
            f"{name} writes outside the db.txn block that carries the width "
            "check, so the check decides under a lock the write does not hold"
        )

    def test_the_argument_only_checks_run_before_the_transaction(self):
        """The mirror rule: an unusable vector must NOT take the write lock.

        This is the pattern ``store_embedding`` already followed by packing
        before ``db.txn``, and it is why the two kinds of check are in two
        places rather than folded into one.
        """
        import ast

        for name in ("store_embedding", "batch_store_embeddings"):
            func = self._function(name)
            blocks = self._txn_blocks(func)
            inside = {
                id(n)
                for block in blocks
                for stmt in block.body
                for n in ast.walk(stmt)
            }
            calls = [
                n
                for n in ast.walk(func)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_reject_unusable_vector"
            ]
            assert calls, f"{name} must validate the vector itself"
            assert not [c for c in calls if id(c) in inside], (
                f"{name} validates the vector inside the write lock -- a refusal "
                "should cost no lock at all"
            )


class TestSearchSurvivesAForeignVector:
    """Oracle 4 --- one foreign row used to abort the whole search."""

    def _mixed_tracker(self, conn):
        for i, description in enumerate(["a", "b", "c"], start=1):
            reqs.add_requirement(conn, req_id=f"FR-{i}", description=description)
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        embeddings.store_embedding(conn, "FR-2", [0.9, 0.1, 0.0])
        # Reachable state: this is what any tracker written before CB-174 can hold.
        _store_raw(conn, "FR-3", embeddings._pack_vector([1.0, 0.0]))

    def test_the_matching_rows_come_back_and_nothing_raises(self, conn):
        """MUTANT: delete the ``length(embedding) = ?`` condition from
        ``search_similar`` --- this test goes red with
        ``ValueError: vector dimension mismatch``."""
        self._mixed_tracker(conn)
        results = embeddings.search_similar(conn, [1.0, 0.0, 0.0], min_similarity=0.0)
        assert [r["id"] for r in results] == ["FR-1", "FR-2"]

    def test_the_loud_pairwise_raise_is_preserved_not_removed(self):
        """The fix makes ``cosine_similarity``'s refusal UNREACHABLE from the
        search path; removing it would be a different and worse change, because
        ``zip()`` would truncate the dot product while the norms stayed full."""
        with pytest.raises(ValueError, match="vector dimension mismatch"):
            embeddings.cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0])

    def test_a_query_of_the_other_width_finds_its_own_rows(self, conn):
        self._mixed_tracker(conn)
        results = embeddings.search_similar(conn, [1.0, 0.0], min_similarity=0.0)
        assert [r["id"] for r in results] == ["FR-3"]

    @pytest.mark.parametrize("bad", [[], [1.0, float("nan"), 0.0]], ids=["empty", "nan"])
    def test_an_unusable_query_vector_is_refused_rather_than_returning_nothing(
        self, conn, bad
    ):
        self._mixed_tracker(conn)
        with pytest.raises(ValueError):
            embeddings.search_similar(conn, bad)


class TestTheReadGuardDoesNotIntroduceANewSilence:
    """The SQL width filter creates its own silent-empty-queue, and this is where
    it is paid back. Found by adversarial review, not by the design.

    On a UNIFORM tracker --- the ordinary case, and the one the write guard now
    guarantees --- a query of the wrong width used to raise loudly from
    ``cosine_similarity`` and, with the filter in place, became an empty list:
    "nothing is similar" about a tracker full of vectors. ``embedding_stats``
    cannot rescue that one, because a uniform tracker reports ``mixed: False``,
    i.e. everything is fine. Reintroducing the very defect class this unit
    removes, inside its own fix.

    MUTANT: delete the refusal in ``search_similar``'s empty-result branch ---
    the first test here goes red.
    """

    def test_a_foreign_width_query_on_a_uniform_tracker_is_refused_not_emptied(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        embeddings.store_embedding(conn, "FR-2", [0.0, 1.0, 0.0])
        assert embeddings.embedding_stats(conn)["mixed"] is False

        with pytest.raises(ValueError) as excinfo:
            embeddings.search_similar(conn, [1.0, 0.0, 0.0, 0.0, 0.0])
        message = str(excinfo.value)
        assert "5-dimensional" in message and "3-dimensional" in message

    def test_an_empty_tracker_still_answers_empty_rather_than_refusing(self, conn):
        """Affirmative proof only. With nothing stored, an empty answer is TRUE,
        and refusing would be a false alarm on a perfectly ordinary state."""
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        assert embeddings.search_similar(conn, [1.0, 0.0]) == []

    def test_a_mixed_tracker_where_some_rows_match_never_reaches_the_refusal(self, conn):
        """The CB-174 behaviour is preserved, not undone: as long as the query
        width matches SOMETHING, the search degrades instead of failing."""
        for i in (1, 2):
            reqs.add_requirement(conn, req_id=f"FR-{i}", description="x")
        _store_raw(conn, "FR-1", embeddings._pack_vector([1.0, 0.0]))
        _store_raw(conn, "FR-2", embeddings._pack_vector([1.0, 0.0, 0.0]))
        assert [r["id"] for r in embeddings.search_similar(conn, [1.0, 0.0])] == ["FR-1"]

    def test_a_status_filter_that_empties_the_page_is_not_mistaken_for_a_width_error(
        self, conn
    ):
        """The refusal keys on the WIDTH, never on the emptiness. A right-width
        query whose status filter matched nothing is an honest empty page."""
        reqs.add_requirement(conn, req_id="FR-1", description="a", status="implemented")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0])
        assert embeddings.search_similar(conn, [1.0, 0.0], status="planned") == []


class TestTheSilentNanDropIsWhatTheWriteGuardPrevents:
    """The behaviour CB-174 removes, pinned as the thing that was fixed.

    A stored ``NaN`` scores ``nan``; ``nan >= min_similarity`` is ``False``; the
    row leaves the result set with no error anywhere. The read-side width guard
    cannot help --- the row has the right width --- which is precisely why the
    fix had to be on the WRITE path.
    """

    def test_a_nan_row_still_vanishes_silently_when_it_bypasses_the_guard(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0])
        _store_raw(conn, "FR-2", embeddings._pack_vector([float("nan"), 0.0]))

        results = embeddings.search_similar(conn, [1.0, 0.0], min_similarity=0.0)
        assert [r["id"] for r in results] == ["FR-1"], (
            "the premise of the fix: the NaN row is dropped with no signal"
        )

    def test_and_the_write_path_no_longer_lets_that_row_exist(self, conn):
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        with pytest.raises(ValueError):
            embeddings.store_embedding(conn, "FR-2", [float("nan"), 0.0])


class TestEmbeddingStatsNamesAMixedTracker:
    """Oracle 5 --- without this, the read-side guard is a silent hider of rows.

    MUTANT: drop the ``dimensions``/``mixed`` keys --- this class goes red.
    """

    def test_an_empty_tracker_reports_an_empty_breakdown_not_a_missing_key(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        stats = embeddings.embedding_stats(conn)
        assert stats["dimensions"] == []
        assert stats["mixed"] is False

    def test_a_uniform_tracker_reports_one_width(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        embeddings.store_embedding(conn, "FR-2", [0.0, 1.0, 0.0])
        stats = embeddings.embedding_stats(conn)
        assert stats["dimensions"] == [{"dimensions": 3, "bytes": 12, "count": 2}]
        assert stats["mixed"] is False

    def test_a_mixed_tracker_is_named(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0, 0.0])
        _store_raw(conn, "FR-2", embeddings._pack_vector([1.0, 0.0]))
        stats = embeddings.embedding_stats(conn)
        assert stats["mixed"] is True
        assert stats["dimensions"] == [
            {"dimensions": 2, "bytes": 8, "count": 1},
            {"dimensions": 3, "bytes": 12, "count": 1},
        ]

    def test_a_ragged_blob_is_not_folded_into_its_neighbour(self, conn):
        """Two blobs can divide to the same component count and still be
        different widths. Reporting only the quotient would say ``mixed: False``
        over a table SQL treats as two populations."""
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0])  # 8 bytes
        _store_raw(conn, "FR-2", b"\x00" * 10)  # divides to 2 as well
        stats = embeddings.embedding_stats(conn)
        assert stats["mixed"] is True
        assert [d["bytes"] for d in stats["dimensions"]] == [8, 10]


class TestOneQuantityDecidesOnBothSides:
    """The write guard and the read guard must compare the SAME number.

    A component-wise write guard divides by four, so a blob whose byte length is
    not a whole number of components folds onto a well-formed neighbour: the
    write would be accepted while ``search_similar``'s byte-wise ``WHERE``
    excludes the row it was accepted beside. Two rules a rounding apart, which
    is the drift CB-22/CB-52 exist to foreclose.

    MUTANT: make ``_stored_byte_widths`` divide by ``_BYTES_PER_COMPONENT`` and
    compare component counts --- the first test here goes red.
    """

    def test_a_ragged_blob_still_refuses_a_write_that_divides_to_the_same_count(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        _store_raw(conn, "FR-1", b"\x00" * 10)  # 10 // 4 == 2
        with pytest.raises(ValueError) as excinfo:
            embeddings.store_embedding(conn, "FR-2", [1.0, 0.0])  # 8 bytes
        assert "10-byte" in str(excinfo.value), str(excinfo.value)

    def test_the_search_guard_excludes_that_row_too(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a")
        reqs.add_requirement(conn, req_id="FR-2", description="b")
        _store_raw(conn, "FR-1", b"\x00" * 10)
        _store_raw(conn, "FR-2", embeddings._pack_vector([1.0, 0.0]))
        results = embeddings.search_similar(conn, [1.0, 0.0], min_similarity=0.0)
        assert [r["id"] for r in results] == ["FR-2"]


class TestTheWidthConditionComposesWithTheStatusFilter:
    """Composition, not elements --- the trap CLAUDE.md records for ``rank_case_sql``.

    Both conditions bind a parameter, and the parameters are positional, so a
    fragment appended at one textual position with its parameter appended at
    another silently swaps the two values. Every test that exercises ONE filter
    passes either way; only a fixture with BOTH plus rows that discriminate can
    see it.
    """

    def test_both_filters_apply_and_neither_takes_the_others_value(self, conn):
        reqs.add_requirement(conn, req_id="FR-1", description="a", status="planned")
        reqs.add_requirement(conn, req_id="FR-2", description="b", status="implemented")
        reqs.add_requirement(conn, req_id="FR-3", description="c", status="planned")
        embeddings.store_embedding(conn, "FR-1", [1.0, 0.0])
        embeddings.store_embedding(conn, "FR-2", [1.0, 0.0])
        # A foreign width AND the wanted status: excluded by width alone.
        _store_raw(conn, "FR-3", embeddings._pack_vector([1.0, 0.0, 0.0]))

        results = embeddings.search_similar(
            conn, [1.0, 0.0], status="planned", min_similarity=0.0
        )
        assert [r["id"] for r in results] == ["FR-1"], (
            "FR-2 is the right width but the wrong status; FR-3 is the right "
            "status but the wrong width -- either parameter landing in the "
            "other's slot changes this answer"
        )
