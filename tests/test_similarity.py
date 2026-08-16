"""Similarity extension (CB-45): lexical detector, auditable grouping report,
file-time annotation."""

import sqlite3

import pytest

from codebugs import db, findings, similarity


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    yield c
    c.close()


LONG_A = (
    "post-merge gate failed on main. log tail: wal checkpoint failed during close:"
    " unable to open database file, worker shutdown aborted"
)
LONG_A2 = (
    "post-merge gate failed on main. log tail: wal checkpoint failed during close:"
    " unable to open database file, worker shutdown was aborted"
)
LONG_B = (
    "post-merge gate inconclusive on main. log tail: ack = backend.get_ack(timeout="
    "min(liveness_poll_interval, remaining)) timed out waiting for worker"
)


class TestDetector:
    def test_normalize_strips_ansi_remnants(self):
        assert "[32m" not in similarity.normalize_text("ok [0m[32m.[0m done", None)
        assert "[32m" not in similarity.normalize_text("ok \x1b[32mgreen\x1b[0m done", None)

    def test_jaccard_identity_and_bounds(self):
        t = similarity.trigram_set("some normalized description text")
        assert similarity.jaccard(t, t) == 1.0
        assert similarity.jaccard(t, frozenset()) == 0.0

    def test_near_duplicates_score_high(self):
        a = similarity.trigram_set(similarity.normalize_text(LONG_A, None))
        b = similarity.trigram_set(similarity.normalize_text(LONG_A2, None))
        assert similarity.jaccard(a, b) == pytest.approx(0.96, abs=0.03)

    def test_distinct_defects_score_low(self):
        a = similarity.trigram_set(similarity.normalize_text(LONG_A, None))
        b = similarity.trigram_set(similarity.normalize_text(LONG_B, None))
        assert similarity.jaccard(a, b) == pytest.approx(0.215, abs=0.06)
        assert similarity.jaccard(a, b) < similarity.DEFAULT_THRESHOLD

    def test_cosine(self):
        assert similarity.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert similarity.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert similarity.cosine([0.0], [0.0]) == 0.0

    def test_cosine_dimension_mismatch_raises(self):
        with pytest.raises(ValueError, match="dimension"):
            similarity.cosine([1.0, 0.0], [1.0])

    def test_threshold_and_limit_validated(self, conn):
        with pytest.raises(ValueError):
            similarity.find_similar(conn, description=LONG_A, category="g", threshold=1.5)
        with pytest.raises(ValueError):
            similarity.find_similar(conn, description=LONG_A, category="g", limit=-1)
        with pytest.raises(ValueError):
            similarity.group_report(conn, threshold=-0.1)


class TestFindSimilar:
    def _add(self, conn, fid, desc, category="gate", status=None):
        findings.add_finding(
            conn, severity="low", category=category, file="f", description=desc,
            finding_id=fid,
        )
        if status:
            findings.update_finding(conn, fid, status=status)

    def test_finds_live_same_category_candidates(self, conn):
        self._add(conn, "CB-1", LONG_A)
        self._add(conn, "CB-2", LONG_B)
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"]
        assert out[0]["status"] == "open"
        assert 0.0 < out[0]["score"] <= 1.0

    def test_other_category_excluded(self, conn):
        self._add(conn, "CB-1", LONG_A, category="other")
        assert similarity.find_similar(conn, description=LONG_A2, category="gate") == []

    def test_decided_rows_included_with_status(self, conn):
        # Review CX-smell-2: "resembles CB-N, already dismissed" is the most
        # valuable annotation; wont_fix/not_a_bug are IN the pool, fixed is not.
        self._add(conn, "CB-1", LONG_A, status="wont_fix")
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"] and out[0]["status"] == "wont_fix"

    def test_fixed_rows_excluded(self, conn):
        self._add(conn, "CB-1", LONG_A, status="fixed")
        assert similarity.find_similar(conn, description=LONG_A2, category="gate") == []

    def test_short_query_returns_nothing(self, conn):
        self._add(conn, "CB-1", "Bug 1x")
        assert similarity.find_similar(conn, description="Bug 2x", category="gate") == []

    def test_short_candidates_excluded(self, conn):
        self._add(conn, "CB-1", "Bug 1x")
        assert similarity.find_similar(conn, description=LONG_A, category="gate") == []

    def test_malformed_candidate_meta_tolerated(self, conn):
        self._add(conn, "CB-1", LONG_A)
        conn.execute("UPDATE findings SET meta='{not json' WHERE id='CB-1'")
        conn.commit()
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"]

    def test_valid_non_dict_meta_tolerated(self, conn):
        # Review CX-13: valid JSON that is not an object must degrade, not crash.
        self._add(conn, "CB-1", LONG_A)
        conn.execute("UPDATE findings SET meta='[1,2]' WHERE id='CB-1'")
        conn.commit()
        out = similarity.find_similar(conn, description=LONG_A2, category="gate")
        assert [m["id"] for m in out] == ["CB-1"]


class TestGroupReport:
    def _seed3(self, conn):
        for i, d in enumerate([LONG_A, LONG_A2, LONG_B]):
            findings.add_finding(
                conn, severity="low", category="gate", file="f", description=d,
                finding_id=f"CB-{i + 1}",
            )

    def test_groups_family_with_audit_evidence(self, conn):
        self._seed3(conn)
        report = similarity.group_report(conn)
        assert report["rows_considered"] == 3
        assert report["rows_skipped_short"] == 0
        assert report["collapse_count"] == 1
        assert report["populations"] == list(findings.LIVE_STATUSES)
        [fam] = report["families"]
        assert {m["id"] for m in fam["members"]} == {"CB-1", "CB-2"}
        assert fam["min_pair_score"] == pytest.approx(0.96, abs=0.03)
        [edge] = fam["edges"]
        assert {edge["a"], edge["b"]} == {"CB-1", "CB-2"}
        assert edge["score"] == pytest.approx(0.96, abs=0.03)
        assert "wal checkpoint" in fam["members"][0]["description_excerpt"]

    def test_threshold_respected(self, conn):
        self._seed3(conn)
        strict = similarity.group_report(conn, threshold=0.999)
        assert strict["families"] == [] and strict["collapse_count"] == 0

    def test_default_population_is_live_only(self, conn):
        self._seed3(conn)
        findings.update_finding(conn, "CB-1", status="fixed")
        report = similarity.group_report(conn)
        assert report["rows_considered"] == 2 and report["families"] == []
        widened = similarity.group_report(conn, status="fixed")
        assert widened["rows_considered"] == 1
        assert widened["populations"] == ["fixed"]

    def test_all_statuses_sentinel(self, conn):
        self._seed3(conn)
        findings.update_finding(conn, "CB-1", status="fixed")
        report = similarity.group_report(conn, status="all")
        assert report["rows_considered"] == 3
        assert report["collapse_count"] == 1
        assert report["populations"] == ["all"]

    def test_short_rows_skipped_and_counted(self, conn):
        for i, d in enumerate(["Bug 1x", "Bug 2x"]):
            findings.add_finding(
                conn, severity="low", category="gate", file="f", description=d,
                finding_id=f"CB-{i + 1}",
            )
        report = similarity.group_report(conn)
        assert report["families"] == [] and report["rows_skipped_short"] == 2

    def test_category_blocking(self, conn):
        findings.add_finding(
            conn, severity="low", category="a", file="f", description=LONG_A,
            finding_id="CB-1",
        )
        findings.add_finding(
            conn, severity="low", category="b", file="f", description=LONG_A,
            finding_id="CB-2",
        )
        assert similarity.group_report(conn)["families"] == []

    def test_family_limit_with_visible_totals(self, conn):
        self._seed3(conn)
        report = similarity.group_report(conn, family_limit=0)
        assert report["families"] == [] and report["families_total"] == 1
        # collapse_count is a statistic over ALL families, not the page
        assert report["collapse_count"] == 1

    def test_vectors_override_pairs_that_have_them(self, conn):
        findings.add_finding(
            conn, severity="low", category="a", file="f", description=LONG_A,
            finding_id="CB-1",
        )
        findings.add_finding(
            conn, severity="low", category="a", file="f", description=LONG_B,
            finding_id="CB-2",
        )
        vecs = {"CB-1": [1.0, 0.0], "CB-2": [0.96, 0.28]}  # cosine ~ 0.96
        report = similarity.group_report(conn, vectors=vecs, threshold=0.9)
        assert report["collapse_count"] == 1
