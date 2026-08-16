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


LONG_DESC = "a genuinely long description of a defect that clears the guard " * 2


class TestFindingsIntegration:
    def test_annotation_lands_in_inserted_row(self, conn):
        db.register_pre_add_resolver(
            "t.a", lambda c, o: {"ka": o["category"]}, meta_keys=("ka",)
        )
        result = findings.add_finding(
            conn, severity="low", category="cat-x", file="f", description=LONG_DESC
        )
        assert result["meta"]["ka"] == "cat-x"
        stored = findings.get_finding(conn, result["id"])
        assert stored["meta"]["ka"] == "cat-x"  # in the INSERT, not a later UPDATE

    def test_bump_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver("t.a", lambda c, o: calls.append(1), meta_keys=("ka",))
        kw = dict(severity="low", category="c", file="f", description=LONG_DESC)
        findings.add_finding(conn, **kw)
        second = findings.add_finding(conn, **kw)
        assert second["dedup_action"] == "bumped" and len(calls) == 1

    def test_reopen_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver(
            "t.a", lambda c, o: calls.append(1) or None, meta_keys=("ka",)
        )
        kw = dict(severity="low", category="c", file="f", description=LONG_DESC)
        first = findings.add_finding(conn, **kw)
        findings.update_finding(conn, first["id"], status="fixed")
        again = findings.add_finding(conn, **kw)
        assert again["dedup_action"] == "reopened" and len(calls) == 1

    def test_recurrence_insert_fires_resolvers(self, conn):
        seen = []
        db.register_pre_add_resolver(
            "t.a", lambda c, o: seen.append(o["dedup_action"]) or None, meta_keys=("ka",)
        )
        kw = dict(severity="low", category="c", file="f", description=LONG_DESC)
        first = findings.add_finding(conn, **kw)
        findings.update_finding(conn, first["id"], status="wont_fix")
        again = findings.add_finding(conn, **kw)
        assert again["dedup_action"] == "recurrence_of_closed"
        assert seen == ["created", "recurrence_of_closed"]

    def test_explicit_id_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver("t.a", lambda c, o: calls.append(1), meta_keys=("ka",))
        findings.add_finding(
            conn,
            severity="low",
            category="c",
            file="f",
            description=LONG_DESC,
            finding_id="CB-500",
        )
        assert calls == []

    def test_annotate_false_does_not_fire_resolvers(self, conn):
        calls = []
        db.register_pre_add_resolver("t.a", lambda c, o: calls.append(1), meta_keys=("ka",))
        findings.add_finding(
            conn,
            severity="low",
            category="c",
            file="f",
            description=LONG_DESC,
            annotate=False,
        )
        assert calls == []

    def test_batch_members_see_earlier_members(self, conn):
        # Pinned semantics (review, mandatory fix 10): inside batch_add's single
        # transaction, member k's resolver sees members 1..k-1 — input-order-
        # dependent and asymmetric, matching identity's own intra-batch behaviour.
        seen_counts = []

        def counting(c, o):
            n = c.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
            seen_counts.append(n)
            return None

        db.register_pre_add_resolver("t.count", counting, meta_keys=("ka",))
        findings.batch_add_findings(
            conn,
            [
                {
                    "severity": "low",
                    "category": "c",
                    "file": "f",
                    "description": LONG_DESC + "1",
                },
                {
                    "severity": "low",
                    "category": "c",
                    "file": "f",
                    "description": LONG_DESC + "2",
                },
            ],
        )
        assert seen_counts == [0, 1]

    def test_caller_meta_with_resolver_key_refused(self, conn):
        db.register_pre_add_resolver("t.a", lambda c, o: None, meta_keys=("ka",))
        with pytest.raises(ValueError, match="ka"):
            findings.add_finding(
                conn,
                severity="low",
                category="c",
                file="f",
                description="d",
                meta={"ka": "spoof"},
            )

    def test_resolver_errors_refused_on_update(self, conn):
        r = findings.add_finding(
            conn,
            severity="low",
            category="c",
            file="f",
            description="d",
            finding_id="CB-501",
        )
        with pytest.raises(ValueError, match="resolver_errors"):
            findings.update_finding(conn, r["id"], meta_update={"resolver_errors": []})

    def test_failed_resolver_finding_still_lands_with_error_stamp(self, conn):
        db.register_pre_add_resolver("t.bad", lambda c, o: 1 / 0, meta_keys=("ka",))
        result = findings.add_finding(
            conn, severity="low", category="c", file="f", description=LONG_DESC
        )
        assert result["was_new"] is True
        assert result["meta"]["resolver_errors"][0]["resolver"] == "t.bad"

    def test_normalized_identity_text_delegates(self):
        # Pins delegation only — the wrapper exists so similarity never imports
        # the private, versioned normalization.
        assert findings.normalized_identity_text(
            "A  B"
        ) == findings._normalize_for_fingerprint("A  B", None)

    def test_live_statuses_public_alias(self):
        assert findings.LIVE_STATUSES == ("open", "in_progress", "stale")


class TestSimilarityCandidates:
    def _seed(self, conn):
        for i, (cat, status) in enumerate(
            [("a", None), ("a", "fixed"), ("b", None), ("a", "wont_fix")]
        ):
            findings.add_finding(
                conn,
                severity="low",
                category=cat,
                file="f",
                description=f"row {i} " * 20,
                finding_id=f"CB-{i + 1}",
            )
            if status:
                findings.update_finding(conn, f"CB-{i + 1}", status=status)

    def test_raw_meta_and_ordering(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn)
        assert [r["id"] for r in rows] == ["CB-1", "CB-2", "CB-3", "CB-4"]  # created_at, id
        assert isinstance(rows[0]["meta_json"], str)  # RAW string, never parsed

    def test_category_and_statuses_filters(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(
            conn, category="a", statuses=findings.LIVE_STATUSES + ("wont_fix",)
        )
        assert [r["id"] for r in rows] == ["CB-1", "CB-4"]

    def test_status_vocabulary_resolved(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn, status="FIXED")  # CB-19 forgiveness
        assert [r["id"] for r in rows] == ["CB-2"]
        with pytest.raises(ValueError):
            findings.similarity_candidates(conn, status="nonsense")

    def test_limit_newest_first(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn, limit=2, order="newest")
        assert [r["id"] for r in rows] == ["CB-4", "CB-3"]

    def test_exclude_id(self, conn):
        self._seed(conn)
        rows = findings.similarity_candidates(conn, category="b", exclude_id="CB-3")
        assert rows == []
