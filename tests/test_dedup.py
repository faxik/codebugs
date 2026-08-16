"""Tests for the findings identity function (CB-43).

One defect observed N times must be ONE row with occurrence_count=N. Coverage:
the status branch table's totality, fingerprint validation/derivation, the
live/reopen/recurrence branches, the reopen milestone projection (the acceptance
is that triage_inbox RETURNS the card, not that a hook fired), batch semantics,
the partial unique index, the public-update collision guard, the legacy
migration through full ensure_schema, and concurrency.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from codebugs import db, findings
from codebugs.types import FINDING_STATUSES


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _add(conn, *, desc="the failure text", cat="bug", file="a.py", **kw):
    return findings.add_finding(
        conn, severity="high", category=cat, file=file, description=desc, **kw
    )


class TestBranchTotality:
    """Every status in the vocabulary maps to exactly ONE dedup branch.

    The judge's own finding: with stale-reopen dropped, `stale` matched NO branch
    in an earlier draft and fell through to plain insert — the duplicate explosion
    silently continuing for 37% of the measured family. A new vocabulary status
    must fail here, not fall through.
    """

    def test_branches_partition_the_vocabulary(self):
        branches = (
            set(findings.LIVE_STATUSES),
            set(findings._REOPEN_STATUSES),
            set(findings.RECURRENCE_STATUSES),
        )
        union = set().union(*branches)
        assert union == set(FINDING_STATUSES), (
            f"unclassified statuses: {set(FINDING_STATUSES) - union} — "
            "an unclassified status silently resumes duplicate-explosion"
        )
        total = sum(len(b) for b in branches)
        assert total == len(union), "a status must belong to exactly one branch"

    def test_partial_index_covers_exactly_the_live_set(self, conn):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_findings_fingerprint_live'"
        ).fetchone()
        assert row is not None, "the identity guarantee index is missing"
        for status in findings.LIVE_STATUSES:
            assert f"'{status}'" in row["sql"]


class TestFingerprintValidation:
    @pytest.mark.parametrize(
        "bad",
        [0, 1.5, [], {}, b"x"],
        ids=["int", "float", "list", "dict", "bytes"],
    )
    def test_non_string_refused(self, conn, bad):
        with pytest.raises(ValueError, match="fingerprint"):
            _add(conn, fingerprint=bad)

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
    def test_empty_refused(self, conn, bad):
        # An empty supplied fingerprint would become one global indexed identity
        # that the ''-means-no-filter convention makes unqueryable.
        with pytest.raises(ValueError, match="non-empty"):
            _add(conn, fingerprint=bad)

    def test_reserved_auto_prefix_refused(self, conn):
        # Otherwise a caller can collide with the derived namespace and the
        # supplied/derived partition guarantee is false (review: Codex major#8).
        with pytest.raises(ValueError, match="reserved"):
            _add(conn, fingerprint="auto:v1:deadbeef01")

    def test_oversized_refused(self, conn):
        with pytest.raises(ValueError, match="exceeds"):
            _add(conn, fingerprint="x" * 257)


class TestFallbackDerivation:
    """The server-side fallback: conservative, meta-aware, versioned."""

    def test_identical_observations_collapse(self, conn):
        first = _add(conn)
        second = _add(conn)
        assert first["was_new"] is True and first["dedup_action"] == "created"
        assert second["was_new"] is False and second["dedup_action"] == "bumped"
        assert second["id"] == first["id"]
        assert second["occurrence_count"] == 2
        assert conn.execute("SELECT COUNT(*) c FROM findings").fetchone()["c"] == 1

    def test_derived_fingerprint_is_versioned(self, conn):
        row = _add(conn)
        assert row["fingerprint"].startswith("auto:v1:")

    def test_hex_and_timestamp_variance_collapses(self, conn):
        a = _add(conn, desc="gate failed at 2026-08-16T06:57:34Z commit deadbeef123 rc=1")
        b = _add(conn, desc="gate failed at 2026-08-17T09:12:00Z commit 0badc0ffee1 rc=1")
        assert b["id"] == a["id"] and b["dedup_action"] == "bumped"

    def test_declared_meta_tokens_are_stripped(self, conn):
        """The load-bearing normalization step: the measured family's blocker was
        the branch slug, which only the filer's own meta declares."""
        a = _add(
            conn,
            desc="gate died (slug: fix-cb-1-organize) log /tmp/gate-fix-cb-1-organize.log",
            meta={"slug": "fix-cb-1-organize"},
        )
        b = _add(
            conn,
            desc="gate died (slug: fix-cb-2-other) log /tmp/gate-fix-cb-2-other.log",
            meta={"slug": "fix-cb-2-other"},
        )
        assert b["id"] == a["id"] and b["dedup_action"] == "bumped"

    def test_return_codes_stay_distinct(self, conn):
        # rc=124 vs rc=1 is the measured 40/9 family split; general numbers are
        # deliberately NOT normalized away.
        a = _add(conn, desc="gate timeout rc=124")
        b = _add(conn, desc="gate timeout rc=1")
        assert b["id"] != a["id"] and b["was_new"] is True

    def test_all_letter_hex_words_survive(self, conn):
        # \b[0-9a-fA-F]{7,}\b without a digit requirement eats English words and
        # merges genuinely different descriptions (review W-4).
        a = _add(conn, desc="the banner was defaced by the module")
        b = _add(conn, desc="the banner was effaced by the module")
        assert b["id"] != a["id"]

    def test_category_and_file_split_identity(self, conn):
        a = _add(conn)
        assert _add(conn, cat="perf")["id"] != a["id"]
        assert _add(conn, file="b.py")["id"] != a["id"]


class TestSuppliedFingerprint:
    def test_supplied_key_collapses_different_texts(self, conn):
        a = _add(conn, desc="wal checkpoint failed, run 12", fingerprint="wal-close")
        b = _add(conn, desc="wal checkpoint failed, run 99", fingerprint="wal-close")
        assert b["id"] == a["id"] and b["dedup_action"] == "bumped"

    def test_supplied_and_derived_never_collide(self, conn):
        a = _add(conn, desc="identical text")
        b = _add(conn, desc="identical text", fingerprint="my-own-key")
        assert b["id"] != a["id"], "a supplied key must not fall into the auto namespace"


class TestExplicitIdBypass:
    """An explicit finding_id asserts identity: no derivation, no matching.

    158 of 173 existing call sites create fixtures from identical helper tuples
    (CB-1 and CB-2 with the same category/file/description) — fallback dedup
    engaging there would make the second entity silently not exist (review:
    Codex major#2, the largest compatibility population).
    """

    def test_identical_fixtures_do_not_collapse(self, conn):
        a = _add(conn, finding_id="CB-901")
        b = _add(conn, finding_id="CB-902")
        assert a["id"] == "CB-901" and b["id"] == "CB-902"
        assert b["was_new"] is True and b["dedup_action"] == "created"
        assert a["fingerprint"] is None and b["fingerprint"] is None

    def test_explicit_id_with_colliding_live_fingerprint_refused(self, conn):
        _add(conn, fingerprint="key-1")
        # Without the pre-check this surfaces as a raw IntegrityError from the
        # partial unique index — outside the ValueError/KeyError contract.
        with pytest.raises(ValueError, match="key-1|live finding"):
            _add(conn, finding_id="CB-903", fingerprint="key-1")


class TestReopenRegression:
    """A fingerprint hit on a `fixed` card is a regression: reopen THAT card."""

    def test_reopen_from_fixed(self, conn):
        first = _add(conn)
        findings.update_finding(conn, first["id"], status="fixed")
        again = _add(conn)
        assert again["id"] == first["id"]
        assert again["dedup_action"] == "reopened" and again["was_new"] is False
        assert again["status"] == "open"
        assert again["occurrence_count"] == 2
        assert again["meta"]["regressed"][0]["from_status"] == "fixed"

    def test_reopen_fires_status_change_hook_once(self, conn):
        first = _add(conn)
        findings.update_finding(conn, first["id"], status="fixed")
        fired: list[tuple] = []
        db.register_status_change_hook("test.reopen_spy", lambda c, e, o, n: fired.append((e, o, n)))
        try:
            _add(conn)
        finally:
            db._status_change_hooks[:] = [
                h for h in db._status_change_hooks if h.name != "test.reopen_spy"
            ]
        assert fired == [(first["id"], "fixed", "open")]

    def test_reopened_card_returns_to_triage_inbox(self, conn):
        """THE acceptance for the reopen projection (review FATAL-1, corroborated):
        the finding reopens AND its stream item reopens, so triage_inbox returns
        the card. Asserting only that a hook fired would pass while the card stayed
        invisible to every queue — reconcile's terminal hook returns early on
        nonterminal, and re-running the INSERT OR IGNORE router is a proven no-op.
        """
        from codebugs import milestones

        first = _add(conn)
        assert any(
            i["item_ref"] == first["id"] for i in milestones.triage_inbox(conn)
        ), "auto-route should have filed the new card into stream/triage"

        findings.update_finding(conn, first["id"], status="fixed")
        assert not any(
            i["item_ref"] == first["id"] for i in milestones.triage_inbox(conn)
        ), "terminal reconciliation should have closed the item"

        again = _add(conn)
        assert again["id"] == first["id"] and again["dedup_action"] == "reopened"
        inbox = milestones.triage_inbox(conn)
        assert any(i["item_ref"] == first["id"] for i in inbox), (
            "reopened card is INVISIBLE to triage — the regression the reopen "
            "projection exists to prevent"
        )

    def test_reopen_stale_is_a_bump_not_a_reopen(self, conn):
        # `stale` is NOT terminal (types.FINDING_TERMINAL) and stale items remain
        # queue-visible, so there is nothing to reopen — it bumps in place.
        first = _add(conn)
        findings.update_finding(conn, first["id"], status="stale")
        again = _add(conn)
        assert again["id"] == first["id"]
        assert again["dedup_action"] == "bumped"
        assert again["status"] == "stale"


class TestRecurrenceOfDecision:
    """wont_fix / not_a_bug are DECISIONS — they stay closed; the recurrence is a
    new row that keeps the link."""

    @pytest.mark.parametrize("closed_status", ["wont_fix", "not_a_bug"])
    def test_new_linked_row(self, conn, closed_status):
        first = _add(conn)
        findings.update_finding(conn, first["id"], status=closed_status)
        again = _add(conn)
        assert again["id"] != first["id"] and again["was_new"] is True
        assert again["dedup_action"] == "recurrence_of_closed"
        assert again["meta"]["recurrence_of"] == first["id"]
        assert again["fingerprint"] == first["fingerprint"]
        old = findings.get_finding(conn, first["id"])
        assert old["status"] == closed_status, "a decision must stay decided"

    def test_newest_terminal_row_decides_the_branch(self, conn):
        # fixed history + newer wont_fix decision -> the decision wins.
        first = _add(conn)
        findings.update_finding(conn, first["id"], status="wont_fix")
        second = _add(conn)  # recurrence row, live
        findings.update_finding(conn, second["id"], status="fixed")
        third = _add(conn)
        # second is the newest terminal row and is `fixed` -> reopen it.
        assert third["id"] == second["id"] and third["dedup_action"] == "reopened"


class TestOccurrenceRing:
    def test_ring_keeps_first_and_last(self, conn):
        first = _add(conn, desc="ring test failure")
        for _ in range(24):
            last = _add(conn, desc="ring test failure")
        assert last["occurrence_count"] == 25
        ring = last["meta"]["occurrences"]
        assert len(ring) == findings._OCC_KEEP_FIRST + findings._OCC_KEEP_LAST
        assert last["meta"]["occurrences_dropped"] == 25 - 1 - len(ring)
        assert first["id"] == last["id"]

    def test_entry_carries_unmerge_evidence(self, conn):
        _add(conn, desc="same signature rc=7")
        bumped = _add(
            conn,
            desc="same signature rc=7",
            tags=["gate"],
            reported_at_ref="v2.0",
        )
        entry = bumped["meta"]["occurrences"][-1]
        # Enough of the discarded observation to un-merge a false merge (Codex#7).
        assert entry["severity"] == "high"
        assert entry["description"] == "same signature rc=7"
        assert entry["tags"] == ["gate"]
        assert entry["reported_at_ref"] == "v2.0"


class TestBatchIdentity:
    def test_results_are_input_ordered(self, conn):
        # Ids CB-1, CB-10, CB-2 come back in B-tree order from a bulk SELECT..IN;
        # results must be built from the member loop instead (review SERIOUS-1).
        # Input order deliberately differs from lexical/B-tree order ("CB-1" <
        # "CB-10" < "CB-2"): with ids in that order the old bulk-SELECT bug
        # passes by coincidence — the first draft of this test did exactly that.
        members = [
            {"severity": "low", "category": "c", "file": "f.py", "description": f"d{i}", "id": fid}
            for i, fid in enumerate(["CB-10", "CB-2", "CB-1"])
        ]
        out = findings.batch_add_findings(conn, members)
        assert [r["id"] for r in out] == ["CB-10", "CB-2", "CB-1"]

    def test_in_batch_self_dedup(self, conn):
        out = findings.batch_add_findings(
            conn,
            [
                {"severity": "high", "category": "c", "file": "f.py", "description": "same"},
                {"severity": "high", "category": "c", "file": "f.py", "description": "same"},
            ],
        )
        assert len(out) == 2, "one result per input, even when deduplicated"
        assert out[0]["dedup_action"] == "created"
        assert out[1]["dedup_action"] == "bumped"
        assert out[1]["id"] == out[0]["id"]
        assert conn.execute("SELECT COUNT(*) c FROM findings").fetchone()["c"] == 1

    def test_unknown_member_key_refused_atomically(self, conn):
        # The strict-argument middleware guards top-level names only; a per-member
        # typo would silently engage the fallback fingerprint (review SERIOUS-7).
        with pytest.raises(ValueError, match="fingerprit"):
            findings.batch_add_findings(
                conn,
                [
                    {"severity": "high", "category": "c", "file": "f.py", "description": "a"},
                    {
                        "severity": "high",
                        "category": "c",
                        "file": "f.py",
                        "description": "b",
                        "fingerprit": "typo",
                    },
                ],
            )
        assert conn.execute("SELECT COUNT(*) c FROM findings").fetchone()["c"] == 0, (
            "validation failures must not half-apply a batch"
        )


class TestPartialUniqueIndex:
    def test_two_live_rows_same_fingerprint_impossible(self, conn):
        """The DB-level guarantee itself, bypassing the domain layer entirely."""
        _add(conn, fingerprint="unique-key")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings (id, severity, category, file, status, description,"
                " source, tags, meta, created_at, updated_at, fingerprint)"
                " VALUES ('CB-999','high','c','f.py','open','d','t','[]','{}','x','x',"
                " 'unique-key')"
            )

    def test_closed_row_may_share_fingerprint(self, conn):
        first = _add(conn)
        findings.update_finding(conn, first["id"], status="wont_fix")
        again = _add(conn)  # recurrence row, same fingerprint, both rows exist
        rows = conn.execute(
            "SELECT id FROM findings WHERE fingerprint = ?", (first["fingerprint"],)
        ).fetchall()
        assert {r["id"] for r in rows} == {first["id"], again["id"]}


class TestPublicUpdateCollisionGuard:
    """Re-triaging a closed card back to open is VALID input; when its fingerprint
    is held by a live recurrence it must raise a domain ValueError naming the
    blocking row — never a raw IntegrityError (review FATAL-2, corroborated)."""

    def test_reopen_blocked_by_live_recurrence(self, conn):
        first = _add(conn)
        findings.update_finding(conn, first["id"], status="wont_fix")
        blocker = _add(conn)  # live recurrence sharing the fingerprint
        with pytest.raises(ValueError, match=blocker["id"]):
            findings.update_finding(conn, first["id"], status="open")
        assert findings.get_finding(conn, first["id"])["status"] == "wont_fix"

    def test_reopen_allowed_once_recurrence_closes(self, conn):
        first = _add(conn)
        findings.update_finding(conn, first["id"], status="wont_fix")
        blocker = _add(conn)
        findings.update_finding(conn, blocker["id"], status="fixed")
        out = findings.update_finding(conn, first["id"], status="open")
        assert out["status"] == "open"


class TestQueryFingerprintFilter:
    def test_exact_match(self, conn):
        a = _add(conn, fingerprint="find-me")
        _add(conn, desc="other")
        res = findings.query_findings(conn, fingerprint="find-me")
        assert [r["id"] for r in res["findings"]] == [a["id"]]

    def test_none_and_empty_mean_no_filter(self, conn):
        _add(conn)
        assert findings.query_findings(conn, fingerprint=None)["total"] == 1
        assert findings.query_findings(conn, fingerprint="")["total"] == 1

    def test_non_string_raises(self, conn):
        # A free-text filter has no resolver, so the predicate itself refuses:
        # binding 0 into SQL would return a silent empty page (CB-25's shape).
        _add(conn)
        with pytest.raises(ValueError, match="string"):
            findings.query_findings(conn, fingerprint=0)


class TestStatsOccurrences:
    def test_summary_and_stats_carry_observation_counts(self, conn):
        _add(conn)
        _add(conn)  # bump -> 1 row, 2 observations
        s = findings.get_summary(conn)
        assert s["total"] == 1
        assert s["total_occurrences"] == 2
        assert s["open_occurrences"] == 2
        stats = findings.get_stats(conn, group_by="severity")
        assert stats["groups"]["high"]["occurrences"] == 2


class TestLegacyMigration:
    def test_full_ensure_schema_from_legacy_table(self, tmp_path):
        """From a REAL legacy table through the COMPLETE ensure_schema — not the
        new migration helper alone. The _migrate_statuses rebuild recreates only
        its own hardcoded index list, and SCHEMA runs before every migration, so
        index placement is exactly what this test exists to catch (SERIOUS-6)."""
        dbfile = tmp_path / "legacy.db"
        raw = sqlite3.connect(dbfile)
        # Pre-in_progress DDL: forces the _migrate_statuses table REBUILD path.
        raw.executescript(
            """CREATE TABLE findings (
                   id TEXT PRIMARY KEY,
                   severity TEXT NOT NULL CHECK(severity IN ('critical','high','medium','low')),
                   category TEXT NOT NULL,
                   file TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'open'
                       CHECK(status IN ('open','fixed','not_a_bug','wont_fix','stale')),
                   description TEXT NOT NULL,
                   source TEXT NOT NULL DEFAULT 'human',
                   tags TEXT NOT NULL DEFAULT '[]',
                   meta TEXT NOT NULL DEFAULT '{}',
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               );
               INSERT INTO findings (id, severity, category, file, status, description,
                                     created_at, updated_at)
               VALUES ('CB-1', 'high', 'bug', 'a.py', 'open', 'legacy row', 'x', 'x');"""
        )
        raw.commit()
        raw.close()

        conn = sqlite3.connect(dbfile)
        conn.row_factory = sqlite3.Row
        findings.ensure_schema(conn)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(findings)").fetchall()}
        assert {"fingerprint", "occurrence_count", "last_seen_at"} <= cols
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'ux_findings_fingerprint_live'"
        ).fetchone(), "the identity index must survive the status-migration rebuild"
        row = conn.execute("SELECT * FROM findings WHERE id = 'CB-1'").fetchone()
        assert row["description"] == "legacy row"
        assert row["fingerprint"] is None and row["occurrence_count"] == 1
        conn.close()

    def test_null_fingerprint_rows_never_match(self, conn):
        conn.execute(
            "INSERT INTO findings (id, severity, category, file, status, description,"
            " source, tags, meta, created_at, updated_at)"
            " VALUES ('CB-800','high','bug','a.py','open','the failure text','h','[]','{}','x','x')"
        )
        conn.commit()
        added = _add(conn)  # identical (category, file, description) as CB-800
        assert added["id"] != "CB-800"
        assert added["was_new"] is True


class TestTransactionDiscipline:
    def test_ambient_transaction_is_not_committed(self, conn):
        """add_finding under a caller's open transaction must not commit the
        caller's work — db.txn yields False and the deleted conn.commit() must
        never come back (CB-24 consequence 1). Pins deliberately-preserved
        behavior on the fixed side; fails on main only via the marker surviving."""
        with pytest.raises(RuntimeError, match="abort the unit"):
            with db.txn(conn):
                conn.execute(
                    "INSERT INTO findings (id, severity, category, file, status,"
                    " description, source, tags, meta, created_at, updated_at)"
                    " VALUES ('CB-700','low','c','m.py','open','marker','h','[]','{}','x','x')"
                )
                _add(conn)
                raise RuntimeError("abort the unit")
        assert conn.execute("SELECT COUNT(*) c FROM findings").fetchone()["c"] == 0, (
            "a commit inside add_finding would have landed the caller's marker row"
        )

    def test_concurrent_same_observation_yields_one_row(self, tmp_project):
        """Two connections racing the same observation: BEGIN IMMEDIATE serializes
        them, the loser's fingerprint SELECT runs after the winner's commit, so it
        BUMPS instead of duplicating. The partial index is the backstop that would
        turn a broken interleave into IntegrityError rather than silent duplication."""
        errors: list[Exception] = []

        def worker():
            c = db.connect(tmp_project)
            try:
                _add(c, desc="race observation rc=9")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                c.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors, f"racing adds must serialize cleanly, got {errors}"
        check = db.connect(tmp_project)
        rows = check.execute(
            "SELECT occurrence_count FROM findings WHERE description LIKE 'race %'"
        ).fetchall()
        check.close()
        assert len(rows) == 1, "the race produced duplicate rows"
        assert rows[0]["occurrence_count"] == 2


class TestResponseShape:
    def test_created_response_carries_discriminators(self, conn):
        row = _add(conn)
        assert row["was_new"] is True
        assert row["dedup_action"] == "created"
        assert row["occurrence_count"] == 1
        assert row["last_seen_at"] is not None

    def test_stored_row_has_no_response_keys(self, conn):
        _add(conn)
        raw = conn.execute("SELECT meta FROM findings").fetchone()
        assert "was_new" not in raw["meta"]
        assert "dedup_action" not in json.loads(raw["meta"])


class TestDiffReviewRegressions:
    """Regression pins for the five findings of the cross-model diff review."""

    def test_reopen_releases_ownership_and_capacity(self, conn):
        """An item closed via set_item_status (NOT the reconciler) still carries
        assigned_agent/pulled_at/done_commit. Reopening without releasing leaves
        the old agent charged for an item a new agent can pull — two agents
        charged for one item."""
        from codebugs import milestones

        first = _add(conn)
        item = milestones.pull_next(conn, agent_id="agent-A", capacity={"triage": 1})
        assert item is not None and item["item_ref"] == first["id"]
        milestones.set_item_status(
            conn, item_ref=first["id"], status="done", commit="abc123def", actor="agent-A"
        )
        findings.update_finding(conn, first["id"], status="fixed")

        again = _add(conn)
        assert again["dedup_action"] == "reopened"
        row = conn.execute(
            "SELECT * FROM milestone_items WHERE item_ref = ? AND item_kind = 'bug'",
            (first["id"],),
        ).fetchone()
        assert row["status"] == "open"
        assert row["assigned_agent"] is None, "old owner must be released on reopen"
        assert row["pulled_at"] is None
        assert row["done_commit"] is None, "a regressed item is no longer integrated"
        held = conn.execute(
            "SELECT triage_held FROM agent_capacity WHERE agent_id = 'agent-A'"
        ).fetchone()
        assert held is None or held["triage_held"] == 0, "capacity slot leaked on reopen"

    @pytest.mark.parametrize("key", ["occurrences", "occurrences_dropped", "regressed", "recurrence_of"])
    def test_reserved_meta_keys_refused_on_add(self, conn, key):
        with pytest.raises(ValueError, match="reserved"):
            _add(conn, meta={key: 1})

    def test_reserved_meta_keys_refused_on_meta_update(self, conn):
        first = _add(conn)
        with pytest.raises(ValueError, match="reserved"):
            findings.update_finding(conn, first["id"], meta_update={"occurrences": 1})

    def test_legacy_poisoned_ring_does_not_crash_the_next_observation(self, conn):
        first = _add(conn)
        conn.execute(
            "UPDATE findings SET meta = ? WHERE id = ?",
            (json.dumps({"occurrences": 1, "occurrences_dropped": "x"}), first["id"]),
        )
        conn.commit()
        again = _add(conn)  # must not raise TypeError from the poisoned structures
        assert again["id"] == first["id"] and again["dedup_action"] == "bumped"
        assert isinstance(again["meta"]["occurrences"], list)

    def test_stable_meta_values_are_not_stripped(self, conn):
        """Stripping EVERY meta string value merges defects whose discriminator the
        filer echoes through meta: rule E501 vs rule F401 normalized identically
        and the second defect silently vanished. Only volatile-looking KEYS strip."""
        a = _add(conn, desc="rule E501 failed", meta={"rule_code": "E501"})
        b = _add(conn, desc="rule F401 failed", meta={"rule_code": "F401"})
        assert b["id"] != a["id"], "distinct rule_codes must stay distinct defects"

    def test_query_matches_the_stripped_stored_token(self, conn):
        a = _add(conn, fingerprint="  padded-key  ")
        assert a["fingerprint"] == "padded-key"
        for spelling in ("padded-key", "  padded-key  "):
            res = findings.query_findings(conn, fingerprint=spelling)
            assert [r["id"] for r in res["findings"]] == [a["id"]], spelling

    def test_whitespace_only_fingerprint_filter_raises(self, conn):
        with pytest.raises(ValueError, match="non-empty"):
            findings.query_findings(conn, fingerprint="   ")


class TestCsvIdentityRoundTrip:
    def _run(self, cwd, *args):
        import os as _os
        import subprocess
        import sys as _sys

        return subprocess.run(
            [_sys.executable, "-m", "codebugs.cli", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            env={**_os.environ, "PYTHONPATH": _os.path.join(_os.getcwd(), "src")},
        )

    def test_auto_fingerprint_rederives_identically_across_trackers(self, tmp_path):
        """Export from tracker A, import into tracker B: a derived fingerprint must
        re-derive to the SAME value, which requires the meta column to round-trip
        (the volatile tokens it strips are declared there). And re-importing into
        tracker A itself must skip, not resurrect."""
        a_dir, b_dir = str(tmp_path / "a"), str(tmp_path / "b")
        for d in (a_dir, b_dir):
            (tmp_path / d.rsplit("/", 1)[-1]).mkdir()
            db.init_project(d)
        ca = db.connect(a_dir)
        orig = findings.add_finding(
            ca,
            severity="high",
            category="gate",
            file="tools/gate.sh",
            description="gate died (slug: fix-cb-9-x) log /tmp/gate-fix-cb-9-x.log",
            meta={"slug": "fix-cb-9-x", "log": "/tmp/gate-fix-cb-9-x.log"},
        )
        ca.close()
        csv_path = str(tmp_path / "out.csv")

        r = self._run(a_dir, "export-csv", csv_path)
        assert r.returncode == 0, r.stderr

        r = self._run(b_dir, "import-csv", csv_path)
        assert r.returncode == 0, r.stderr
        cb = db.connect(b_dir)
        imported = cb.execute("SELECT fingerprint FROM findings").fetchone()
        cb.close()
        assert imported["fingerprint"] == orig["fingerprint"], (
            "derived identity must survive a cross-tracker CSV round-trip"
        )

        r = self._run(a_dir, "import-csv", csv_path)
        assert r.returncode == 0, r.stderr
        assert "skipped" in r.stdout
        ca = db.connect(a_dir)
        count = ca.execute("SELECT COUNT(*) c FROM findings").fetchone()["c"]
        ca.close()
        assert count == 1, "same-tracker re-import must not duplicate or resurrect"
