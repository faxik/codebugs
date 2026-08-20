"""Tests for category canon on the observation write path (CB-60).

Category is an identity input (the auto:v1 fingerprint hashes it; similarity
annotates and groups strictly inside it), not free text. Coverage: spelling
normalization (twins collapse at write time), the minting gate (a genuinely
new name needs `new_category=True`, a near-miss is refused naming the
canonical spelling), the `category_minted` stamp and its add-side
reservation, and the paths the gate deliberately does NOT touch: explicit
finding_id, empty category, and CSV import (an import is not an observation,
CB-51).
"""

from __future__ import annotations

import pytest

from codebugs import db, findings
from codebugs.types import normalize_category


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


LONG_A = "worker crashed while flushing the queue after the retry budget was exhausted"
LONG_B = "scheduler dropped the callback because the shutdown hook ran before drain"


def _add(conn, *, desc=LONG_A, cat, **kw):
    return findings.add_finding(
        conn, severity="medium", category=cat, file="a.py", description=desc, **kw
    )


class TestNormalizeCategory:
    def test_twin_spellings_normalize_identically(self):
        assert (
            normalize_category("process-improvement")
            == normalize_category("process_improvement")
            == normalize_category("Process Improvement")
            == "process_improvement"
        )

    def test_casefold_strip_and_whitespace_runs(self):
        assert normalize_category("  N-Plus  One ") == "n_plus_one"

    def test_empty_passes_through(self):
        # "" is a legal category (similarity's annotation pool matches it
        # exactly, categories=("",)) — the normalizer must not invent a value.
        assert normalize_category("") == ""

    def test_non_string_is_refused_as_value_error(self):
        with pytest.raises(ValueError):
            normalize_category(None)  # type: ignore[arg-type]


class TestTwinCollapse:
    def test_hyphen_underscore_twins_collapse_to_one_row(self, conn):
        """The motivating pair: two observations of one defect, twin spellings.

        Normalization runs BEFORE fingerprint derivation, so both hash the
        canonical spelling and the second observation BUMPS the first row
        instead of forking identity forever. Mutation probe: with the
        normalization call removed this yields two rows and fails.
        """
        first = _add(conn, cat="process-improvement", new_category=True)
        second = _add(conn, cat="process_improvement")
        assert second["id"] == first["id"]
        assert second["was_new"] is False
        assert second["dedup_action"] == "bumped"
        assert second["occurrence_count"] == 2
        assert second["category"] == "process_improvement"  # canonical spelling stored
        n = conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
        assert n == 1


class TestCategoryGate:
    def test_new_category_without_flag_is_refused_with_hint(self, conn):
        _add(conn, cat="process_improvement", new_category=True)
        with pytest.raises(ValueError, match="new_category"):
            _add(conn, desc=LONG_B, cat="memory_leak")

    def test_refusal_lists_nearest_existing(self, conn):
        _add(conn, cat="process_improvement", new_category=True)
        _add(conn, desc=LONG_B, cat="dropped_callback", new_category=True)
        with pytest.raises(ValueError, match="process_improvement"):
            _add(conn, desc="another defect entirely, long enough to matter", cat="memory_leak")

    def test_empty_tracker_first_add_needs_the_flag(self, conn):
        with pytest.raises(ValueError, match="new_category"):
            _add(conn, cat="brand_new")

    def test_new_category_with_flag_mints_and_stamps(self, conn):
        result = _add(conn, cat="memory_leak", new_category=True)
        assert result["meta"]["category_minted"] is True
        got = findings.query_findings(conn, meta_key="category_minted")
        assert [r["id"] for r in got["findings"]] == [result["id"]]

    def test_near_hit_is_refused_naming_the_canonical_spelling(self, conn):
        _add(conn, cat="process_improvement", new_category=True)
        with pytest.raises(ValueError, match="process_improvement"):
            _add(conn, desc=LONG_B, cat="process_improvemnt")

    def test_near_hit_escapes_with_the_flag(self, conn):
        _add(conn, cat="process_improvement", new_category=True)
        result = _add(conn, desc=LONG_B, cat="process_improvemnt", new_category=True)
        assert result["category"] == "process_improvemnt"
        assert result["meta"]["category_minted"] is True

    def test_exact_normalized_match_passes_without_flag(self, conn):
        _add(conn, cat="process_improvement", new_category=True)
        result = _add(conn, desc=LONG_B, cat="Process-Improvement")
        assert result["was_new"] is True
        assert result["category"] == "process_improvement"
        assert "category_minted" not in result["meta"]

    def test_flag_on_existing_category_is_permission_not_assertion(self, conn):
        _add(conn, cat="process_improvement", new_category=True)
        result = _add(conn, desc=LONG_B, cat="process_improvement", new_category=True)
        assert result["was_new"] is True
        assert "category_minted" not in result["meta"]  # nothing was minted

    def test_empty_category_is_never_gated_and_never_stamped(self, conn):
        result = _add(conn, cat="")
        assert result["category"] == ""
        assert "category_minted" not in result["meta"]

    def test_short_names_do_not_near_hit(self, conn):
        # Conservative threshold: a false refusal is worse than a miss, so
        # names under 5 chars only ever exact-match ("db" vs "de" is not a
        # near-miss). Still refused as genuinely new without the flag.
        _add(conn, cat="db", new_category=True)
        result = _add(conn, desc=LONG_B, cat="de", new_category=True)
        assert result["category"] == "de"

    def test_explicit_finding_id_bypasses_gate_and_normalization(self, conn):
        """An explicit id asserts identity and bypasses the observation
        machinery (dedup, resolvers, hooks) — the gate and the normalizer live
        on the observation path, so fixtures keep filing verbatim."""
        result = findings.add_finding(
            conn,
            severity="low",
            category="Weird-Case Name",
            file="f.py",
            description="fixture row",
            finding_id="CB-900",
        )
        assert result["category"] == "Weird-Case Name"

    def test_refusal_lands_nothing(self, conn):
        with pytest.raises(ValueError):
            _add(conn, cat="brand_new")
        n = conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
        assert n == 0


class TestStoredRowsUntouched:
    def test_gate_never_rewrites_stored_categories_or_fingerprints(self, conn):
        """CB-61 (retro-fold) stays blocked: the gate reads existing rows, it
        never rewrites them — an old-spelling stored row keeps its category
        and its auto:v1 fingerprint verbatim."""
        # A pre-CB-60-shaped row: explicit id path stores the category verbatim.
        findings.add_finding(
            conn,
            severity="low",
            category="process-improvement",
            file="old.py",
            description="legacy spelling row",
            finding_id="CB-800",
            fingerprint="legacy-token",
        )
        _add(conn, cat="io_error", new_category=True)
        before = {
            r["id"]: (r["category"], r["fingerprint"])
            for r in conn.execute("SELECT id, category, fingerprint FROM findings")
        }
        with pytest.raises(ValueError):
            _add(conn, desc=LONG_B, cat="genuinely_new_thing")
        _add(conn, desc=LONG_B, cat="another_new_thing", new_category=True)
        after = {
            r["id"]: (r["category"], r["fingerprint"])
            for r in conn.execute("SELECT id, category, fingerprint FROM findings")
        }
        for fid, snapshot in before.items():
            assert after[fid] == snapshot


class TestLegacyWeirdRows:
    def test_a_non_string_stored_category_does_not_brick_the_gate(self, conn):
        """SQLite's dynamic typing lets a raw or explicit-id write store a
        non-string category; the canon read must skip it, not raise on every
        future observation add."""
        conn.execute(
            "INSERT INTO findings (id, severity, category, file, description,"
            " created_at, updated_at) VALUES ('CB-666', 'low', 5, 'f.py', 'weird',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        result = _add(conn, cat="normal_cat", new_category=True)
        assert result["category"] == "normal_cat"


class TestBatchGate:
    def test_batch_without_flag_refuses_before_anything_lands(self, conn):
        with pytest.raises(ValueError, match="new_category"):
            findings.batch_add_findings(
                conn,
                [
                    {"severity": "low", "category": "new_cat", "file": "f", "description": LONG_A},
                ],
            )
        n = conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
        assert n == 0

    def test_batch_flag_mints_once_per_new_category(self, conn):
        results = findings.batch_add_findings(
            conn,
            [
                {"severity": "low", "category": "new_cat", "file": "f", "description": LONG_A},
                {"severity": "low", "category": "new_cat", "file": "g", "description": LONG_B},
            ],
            new_category=True,
        )
        assert [r["was_new"] for r in results] == [True, True]
        stamps = [r["meta"].get("category_minted") for r in results]
        assert stamps == [True, None]  # one minting event, not one per row

    def test_batch_member_normalizes_before_dedup(self, conn):
        results = findings.batch_add_findings(
            conn,
            [
                {
                    "severity": "low",
                    "category": "proc-fail",
                    "file": "f",
                    "description": LONG_A,
                },
                {
                    "severity": "low",
                    "category": "proc_fail",
                    "file": "f",
                    "description": LONG_A,
                },
            ],
            new_category=True,
        )
        assert results[0]["id"] == results[1]["id"]
        assert results[1]["dedup_action"] == "bumped"

    def test_batch_explicit_id_member_bypasses(self, conn):
        results = findings.batch_add_findings(
            conn,
            [
                {
                    "id": "CB-901",
                    "severity": "low",
                    "category": "Verbatim-Cat",
                    "file": "f",
                    "description": LONG_A,
                },
            ],
        )
        assert results[0]["category"] == "Verbatim-Cat"


class TestStampReservation:
    def test_caller_cannot_spoof_the_stamp_on_add(self, conn):
        with pytest.raises(ValueError, match="category_minted"):
            _add(conn, cat="spoof_cat", new_category=True, meta={"category_minted": True})

    def test_stamp_is_repairable_on_update(self, conn):
        # Add-side reservation stops spoofing the mint count; a permanently
        # unrepairable stamp would be the CB-26 shape, so update may rewrite it.
        row = _add(conn, cat="real_cat", new_category=True)
        fixed = findings.update_finding(conn, row["id"], meta_update={"category_minted": False})
        assert fixed["meta"]["category_minted"] is False


class TestImportIsNotGated:
    def test_import_takes_new_categories_verbatim_without_flag(self, conn):
        """CB-51 verbatim contract: import is not an observation — no
        normalization, no gate, no stamp. A backup with old spellings restores."""
        report = findings.import_findings(
            conn,
            [
                {
                    "id": "CB-7001",
                    "severity": "low",
                    "category": "Foreign-Spelling",
                    "file": "x.py",
                    "description": "peer row with a category this tracker has never seen",
                }
            ],
        )
        assert report.imported == 1 and not report.errors
        rows = conn.execute("SELECT category FROM findings").fetchall()
        assert rows[0]["category"] == "Foreign-Spelling"

    def test_import_strips_a_foreign_mint_stamp(self, conn):
        report = findings.import_findings(
            conn,
            [
                {
                    "id": "CB-7002",
                    "severity": "low",
                    "category": "peer_cat",
                    "file": "x.py",
                    "description": "peer row whose export carries their mint stamp",
                    "meta": {"category_minted": True},
                }
            ],
        )
        assert report.imported == 1 and not report.errors
        got = findings.query_findings(conn, meta_key="category_minted")
        assert got["findings"] == []
