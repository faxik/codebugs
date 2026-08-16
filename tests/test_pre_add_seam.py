"""Pre-add resolver seam (CB-45): registration, SAVEPOINT isolation, the enforced
never-commit contract, hostile resolvers, and failure surfacing."""

import sqlite3

import pytest

from codebugs import db, findings


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Snapshot the registry EXCLUDING similarity.annotate: pytest collection imports
    test_similarity.py first, which registers the real resolver process-wide; excluding
    it by name makes these tests order-independent by construction (review W-8)."""
    snapshot = [r for r in db._pre_add_resolvers if r.name != "similarity.annotate"]
    monkeypatch.setattr(db, "_pre_add_resolvers", snapshot)


def _obs(**over):
    base = {
        "finding_id": "CB-999",
        "severity": "low",
        "category": "c",
        "file": "f",
        "description": "d",
        "source": "test",
        "tags": [],
        "meta": {},
        "fingerprint": "fp-1",
        "dedup_action": "created",
        "recurrence_of": None,
        "at": "2026-08-16T00:00:00Z",
    }
    base.update(over)
    return base


INSERT_ROW = (
    "INSERT INTO findings (id, severity, category, file, status, description,"
    " source, tags, meta, created_at, updated_at) VALUES (?,'low','x','x','open',"
    "'x','t','[]','{}','2026-01-01','2026-01-01')"
)


class _Abort(Exception):
    pass


class TestRegistration:
    def test_identical_reregistration_is_noop(self):
        n = len(db._pre_add_resolvers)

        def fn(c, o):
            return None

        db.register_pre_add_resolver("t.a", fn, meta_keys=("ka",))
        db.register_pre_add_resolver("t.a", fn, meta_keys=("ka",))
        assert len(db._pre_add_resolvers) == n + 1

    def test_same_name_different_meta_keys_raises(self):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with pytest.raises(ValueError, match="meta_keys"):
            db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("kz",))

    def test_overlapping_meta_keys_refused(self):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with pytest.raises(ValueError, match="ka"):
            db.register_pre_add_resolver("t.b", lambda c, o: None, meta_keys=("ka", "kb"))

    def test_resolver_errors_key_refused(self):
        with pytest.raises(ValueError, match="resolver_errors"):
            db.register_pre_add_resolver(
                "t.a", lambda c, o: None, meta_keys=("resolver_errors",)
            )

    def test_reserved_union(self):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka", "kb"))
        assert {"ka", "kb", "resolver_errors"} <= db.resolver_reserved_meta_keys()


class TestTransactionContract:
    """The card's strongest ratified requirement: a resolver must never commit."""

    def test_runner_outside_transaction_raises(self, conn):
        # Outside a txn, SAVEPOINT starts one and RELEASE COMMITS it — the runner
        # must refuse (raise, never assert) rather than silently commit.
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": 1}, meta_keys=("ka",))
        assert not conn.in_transaction
        with pytest.raises(RuntimeError, match="OPEN transaction"):
            db.run_pre_add_resolvers(conn, _obs())

    def test_runner_leaves_caller_transaction_open(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": 1}, meta_keys=("ka",))
        with pytest.raises(_Abort):
            with db.txn(conn):
                conn.execute(INSERT_ROW, ("CB-777",))
                db.run_pre_add_resolvers(conn, _obs())
                assert conn.in_transaction  # still inside the caller's txn
                raise _Abort()  # db.txn rolls back
        assert conn.execute("SELECT id FROM findings WHERE id='CB-777'").fetchone() is None

    def test_resolver_that_commits_is_unrecoverable_and_loud(self, conn):
        def hostile(c, o):
            c.commit()
            return {"ka": 1}

        db.register_pre_add_resolver("t.hostile", hostile, meta_keys=("ka",))
        with db.txn(conn):
            with pytest.raises(RuntimeError, match="closed the caller's transaction"):
                db.run_pre_add_resolvers(conn, _obs())
            # re-open so db.txn's closing COMMIT has a transaction to commit
            conn.execute("BEGIN IMMEDIATE")

    def test_resolver_that_rolls_back_is_unrecoverable_and_loud(self, conn):
        def hostile(c, o):
            c.execute("ROLLBACK")
            return {"ka": 1}

        db.register_pre_add_resolver("t.hostile", hostile, meta_keys=("ka",))
        with db.txn(conn):
            with pytest.raises(RuntimeError):
                db.run_pre_add_resolvers(conn, _obs())
            conn.execute("BEGIN IMMEDIATE")

    def test_failing_resolver_that_destroyed_savepoint_raises_named_error(self, conn):
        def hostile(c, o):
            c.execute("ROLLBACK")  # destroys txn AND savepoint
            raise RuntimeError("boom")

        db.register_pre_add_resolver("t.hostile", hostile, meta_keys=("ka",))
        with db.txn(conn):
            with pytest.raises(RuntimeError, match="savepoint stack"):
                db.run_pre_add_resolvers(conn, _obs())
            conn.execute("BEGIN IMMEDIATE")


class TestRunner:
    def test_annotation_merged(self, conn):
        db.register_pre_add_resolver(
            "t.a", lambda c, o: {"ka": [o["finding_id"]]}, meta_keys=("ka",)
        )
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert patch == {"ka": ["CB-999"]}

    def test_none_means_no_opinion(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with db.txn(conn):
            assert db.run_pre_add_resolvers(conn, _obs()) == {}

    def test_failure_rolls_back_resolver_writes_only(self, conn):
        def bad(c, o):
            c.execute(INSERT_ROW, ("CB-666",))
            raise RuntimeError("boom")

        db.register_pre_add_resolver("t.bad", bad, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
            assert conn.in_transaction
            row = conn.execute("SELECT id FROM findings WHERE id='CB-666'").fetchone()
        assert row is None
        assert patch["resolver_errors"][0]["resolver"] == "t.bad"
        assert "boom" in patch["resolver_errors"][0]["error"]
        assert patch["resolver_errors"][0]["at"] == "2026-08-16T00:00:00Z"

    def test_failure_does_not_stop_later_resolvers(self, conn):
        db.register_pre_add_resolver("t.bad", lambda c, o: 1 / 0, meta_keys=("ka",))
        db.register_pre_add_resolver("t.ok", lambda c, o: {"kb": 1}, meta_keys=("kb",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert patch["kb"] == 1 and len(patch["resolver_errors"]) == 1

    def test_undeclared_key_is_failure(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"other": 1}, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert "other" not in patch and patch["resolver_errors"][0]["resolver"] == "t.a"

    def test_forbidden_key_is_failure(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": 1}, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs(), forbidden=frozenset({"ka"}))
        assert "ka" not in patch and patch["resolver_errors"]

    def test_non_dict_outcome_is_failure(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: ["ka"], meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert patch["resolver_errors"][0]["resolver"] == "t.a"

    def test_non_serializable_outcome_is_failure_not_crash(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: {"ka": {1, 2}}, meta_keys=("ka",))
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert "ka" not in patch and patch["resolver_errors"]

    def test_nan_outcome_is_failure(self, conn):
        db.register_pre_add_resolver(
            "t.a", lambda c, o: {"ka": float("nan")}, meta_keys=("ka",)
        )
        with db.txn(conn):
            patch = db.run_pre_add_resolvers(conn, _obs())
        assert "ka" not in patch and patch["resolver_errors"]
