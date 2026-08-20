"""Tests for the one-shot retro-fold of stored category spellings (CB-61).

CB-60 canonicalized the OBSERVATION write path; the stored corpus kept its
variant spellings, and `fingerprint` is STORED, not recomputed. Category is an
input of the `auto:v1` hash, so a pre-gate row re-observed with its OWN spelling
derives a different hash than the one on disk and FORKS identity instead of
bumping (CB-113(a)). `findings.normalize_categories` folds the corpus to canon
and re-derives the stored `auto:v1` hashes, which closes the fork.

Coverage: the CB-113(a) fork and its closure, dry-run writing nothing at all,
report-and-stop on a re-derivation collision (with the pair NAMED), supplied and
NULL fingerprints left byte-identical, the round-trip guard that skips a row
whose stored inputs do not reproduce its stored hash, the occurrence ring left
untouched, terminal rows folded (they legitimize spellings through
`_existing_categories`), and fold_map validation.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

from codebugs import db, findings
from codebugs.types import normalize_category, utc_now

VARIANT = "Process Improvement"
CANON = "process_improvement"
DESC = "the worker crashed while flushing the queue after the retry budget was exhausted"
DESC_B = "the scheduler dropped the callback because the shutdown hook ran before drain"


@pytest.fixture
def tmp_project(tmp_path):
    db.init_project(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def conn(tmp_project):
    c = db.connect(tmp_project)
    yield c
    c.close()


def _insert_raw(
    conn,
    *,
    fid,
    category,
    file="a.py",
    description=DESC,
    meta=None,
    fingerprint="derive",
    status="open",
    severity="high",
    tags=None,
):
    """A pre-gate row, written the way the corpus really holds one.

    Deliberately a raw INSERT: `add_finding` would normalize the category and
    derive the hash from the NORMALIZED name, which is precisely the state this
    fixture must NOT reproduce.
    """
    meta = {} if meta is None else meta
    if fingerprint == "derive":
        fingerprint = findings._derive_fingerprint(category, file, description, meta)
    now = utc_now()
    conn.execute(
        "INSERT INTO findings (id, severity, category, file, status, description, source,"
        " tags, meta, created_at, updated_at, fingerprint, occurrence_count)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (
            fid,
            severity,
            category,
            file,
            status,
            description,
            "human",
            json.dumps(tags or []),
            json.dumps(meta),
            now,
            now,
            fingerprint,
        ),
    )
    conn.commit()
    return fid


def _snapshot(conn):
    return conn.execute(
        "SELECT id, category, fingerprint, meta, status, occurrence_count, updated_at"
        " FROM findings ORDER BY id"
    ).fetchall()


def _db_bytes(tmp_project):
    """Hash of the persistent database files. `-shm` is excluded on purpose: it is
    shared-memory coordination state that a pure READ legitimately touches, so
    including it would make this assertion fail for a reason that is not a write."""
    root = pathlib.Path(tmp_project) / ".codebugs"
    h = hashlib.sha256()
    for name in ("findings.db", "findings.db-wal"):
        p = root / name
        h.update(name.encode())
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()


class TestCb113aFork:
    """The defect, and its closure. Two databases, not one: folding a tracker that
    ALREADY forked converges the two rows onto one hash, which is exactly the
    collision the stop-rule refuses — see TestCollisionStops."""

    def test_pre_gate_row_forks_when_re_observed_with_its_own_spelling(self, conn):
        original = _insert_raw(conn, fid="CB-1", category=VARIANT)

        result = findings.add_finding(
            conn, severity="high", category=VARIANT, file="a.py", description=DESC
        )

        assert result["was_new"] is True, "expected the pre-gate fork to reproduce"
        assert result["id"] != original
        assert result["category"] == CANON

    def test_after_the_fold_the_same_observation_bumps_instead(self, conn):
        original = _insert_raw(conn, fid="CB-1", category=VARIANT)

        report = findings.normalize_categories(conn, apply=True)
        assert report["applied"] is True
        assert report["stopped"] is False

        row = conn.execute("SELECT category, fingerprint FROM findings WHERE id = ?", (original,)).fetchone()
        assert row["category"] == CANON
        assert row["fingerprint"] == findings._derive_fingerprint(CANON, "a.py", DESC, {})

        result = findings.add_finding(
            conn, severity="high", category=VARIANT, file="a.py", description=DESC
        )
        assert result["was_new"] is False
        assert result["id"] == original
        assert result["occurrence_count"] == 2


class TestDryRunWritesNothing:
    """Dry run is the DEFAULT, and it must not open a write transaction at all."""

    def test_dry_run_leaves_rows_and_bytes_identical(self, tmp_project):
        conn = db.connect(tmp_project)
        try:
            _insert_raw(conn, fid="CB-1", category=VARIANT)
            _insert_raw(conn, fid="CB-2", category="Some Other", description=DESC_B)
            before_rows = _snapshot(conn)
            before_bytes = _db_bytes(tmp_project)

            report = findings.normalize_categories(conn)

            assert report["applied"] is False
            assert len(report["renames"]) == 2
            assert _snapshot(conn) == before_rows
            assert _db_bytes(tmp_project) == before_bytes
            assert conn.in_transaction is False
        finally:
            conn.close()


class TestCollisionStops:
    """Ratified: report-and-stop, never auto-merge. And the stop is WHOLE-RUN —
    a row that would have folded cleanly must not land either."""

    def _seed(self, conn):
        # Same file+description, spellings that converge under the fold: after the
        # fold both derive auto:v1(process_improvement, a.py, DESC).
        _insert_raw(conn, fid="CB-1", category=VARIANT)
        _insert_raw(conn, fid="CB-2", category=CANON)
        # An innocent bystander that WOULD fold cleanly.
        _insert_raw(conn, fid="CB-3", category="Other Thing", description=DESC_B)

    def test_apply_stops_names_the_pair_and_writes_nothing(self, conn):
        self._seed(conn)
        before = _snapshot(conn)

        report = findings.normalize_categories(conn, apply=True)

        assert report["stopped"] is True
        assert report["applied"] is False
        assert _snapshot(conn) == before

        assert len(report["collisions"]) == 1
        collision = report["collisions"][0]
        assert collision["fingerprint"] == findings._derive_fingerprint(CANON, "a.py", DESC, {})
        ids = {r["id"] for r in collision["rows"]}
        assert ids == {"CB-1", "CB-2"}
        by_id = {r["id"]: r for r in collision["rows"]}
        assert by_id["CB-1"]["from"] == VARIANT
        assert by_id["CB-1"]["to"] == CANON
        assert by_id["CB-2"]["to"] is None  # already canonical, not renamed

    def test_dry_run_reports_the_same_stop_without_writing(self, conn):
        self._seed(conn)
        report = findings.normalize_categories(conn)
        assert report["stopped"] is True
        assert len(report["collisions"]) == 1

    def test_cli_apply_exits_1_and_names_the_pair(self, conn, tmp_project, monkeypatch, capsys):
        self._seed(conn)
        conn.commit()
        from codebugs import cli

        monkeypatch.setattr(
            sys, "argv", ["codebugs", "--tracker-root", tmp_project, "categories-normalize", "--apply"]
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "CB-1" in out and "CB-2" in out

    def test_cli_dry_run_exits_0_even_with_a_collision(self, conn, tmp_project, monkeypatch, capsys):
        self._seed(conn)
        conn.commit()
        from codebugs import cli

        monkeypatch.setattr(
            sys, "argv", ["codebugs", "--tracker-root", tmp_project, "categories-normalize"]
        )
        cli.main()  # no SystemExit: a dry run reports, it does not refuse
        assert "CB-1" in capsys.readouterr().out


class TestFingerprintPolicy:
    def test_supplied_and_null_fingerprints_keep_their_bytes(self, conn):
        _insert_raw(conn, fid="CB-1", category=VARIANT, fingerprint="team:widget-7")
        _insert_raw(conn, fid="CB-2", category=VARIANT, description=DESC_B, fingerprint=None)

        report = findings.normalize_categories(conn, apply=True)
        assert report["stopped"] is False

        rows = {r["id"]: r for r in _snapshot(conn)}
        assert rows["CB-1"]["category"] == CANON
        assert rows["CB-1"]["fingerprint"] == "team:widget-7"
        assert rows["CB-2"]["category"] == CANON
        assert rows["CB-2"]["fingerprint"] is None

        assert report["counts"]["supplied_untouched"] == 1
        assert report["counts"]["null_untouched"] == 1
        assert report["counts"]["refingerprinted"] == 0
        assert report["counts"]["category_only"] == 2
        actions = {r["id"]: r["fingerprint_action"] for r in report["renames"]}
        assert actions == {"CB-1": "untouched_supplied", "CB-2": "untouched_null"}

    def test_a_row_whose_inputs_do_not_reproduce_its_hash_is_skipped_whole(self, conn):
        bogus = "auto:v1:" + "0" * 32
        _insert_raw(conn, fid="CB-1", category=VARIANT, fingerprint=bogus)
        _insert_raw(conn, fid="CB-2", category=VARIANT, description=DESC_B)

        report = findings.normalize_categories(conn, apply=True)

        rows = {r["id"]: r for r in _snapshot(conn)}
        # skipped ENTIRELY — the category is not rewritten either
        assert rows["CB-1"]["category"] == VARIANT
        assert rows["CB-1"]["fingerprint"] == bogus
        # ...and the run continued
        assert rows["CB-2"]["category"] == CANON
        assert report["applied"] is True
        assert report["counts"]["unverifiable"] == 1
        assert [u["id"] for u in report["unverifiable"]] == ["CB-1"]
        assert [r["id"] for r in report["renames"]] == ["CB-2"]

    def test_unparseable_stored_meta_is_unverifiable_not_an_exception(self, conn):
        _insert_raw(conn, fid="CB-1", category=VARIANT)
        conn.execute("UPDATE findings SET meta = ? WHERE id = ?", ("{not json", "CB-1"))
        conn.commit()

        report = findings.normalize_categories(conn, apply=True)
        assert report["counts"]["unverifiable"] == 1
        assert _snapshot(conn)[0]["category"] == VARIANT


class TestRingIsNotRewritten:
    def test_meta_occurrences_survive_the_fold_byte_for_byte(self, conn):
        meta = {
            "occurrences": [{"at": "2026-01-01T00:00:00Z", "category": VARIANT, "severity": "low"}],
            "category_minted": True,
        }
        _insert_raw(conn, fid="CB-1", category=VARIANT, meta=meta)
        before = conn.execute("SELECT meta FROM findings WHERE id = 'CB-1'").fetchone()["meta"]

        report = findings.normalize_categories(conn, apply=True)
        assert report["counts"]["refingerprinted"] == 1

        after = conn.execute("SELECT meta FROM findings WHERE id = 'CB-1'").fetchone()["meta"]
        assert after == before
        assert json.loads(after)["occurrences"] == meta["occurrences"]


class TestTerminalRowsFold:
    """`_existing_categories` reads DISTINCT category over ALL rows, terminal
    included — so a `fixed` row left at its variant spelling keeps legitimizing
    that spelling for every future observation."""

    def test_a_fixed_row_is_folded_too(self, conn):
        _insert_raw(conn, fid="CB-1", category=VARIANT, status="fixed")

        findings.normalize_categories(conn, apply=True)

        assert _snapshot(conn)[0]["category"] == CANON
        assert normalize_category(VARIANT) not in {
            k for k in findings._existing_categories(conn) if k != CANON
        }
        assert set(findings._existing_categories(conn).values()) == {CANON}


class TestFoldMapValidation:
    def test_a_non_canonical_target_is_refused_naming_the_pair(self, conn):
        with pytest.raises(ValueError) as exc:
            findings.normalize_categories(conn, fold_map={VARIANT: "Process Improvement"})
        assert VARIANT in str(exc.value)
        assert "process_improvement" in str(exc.value)

    def test_empty_map_is_a_legal_no_op_not_the_default(self, conn):
        _insert_raw(conn, fid="CB-1", category=VARIANT)

        report = findings.normalize_categories(conn, fold_map={}, apply=True)

        assert report["renames"] == []
        assert report["rows_scanned"] == 1
        assert _snapshot(conn)[0]["category"] == VARIANT

    def test_non_string_key_or_value_is_refused(self, conn):
        with pytest.raises(ValueError):
            findings.normalize_categories(conn, fold_map={1: CANON})
        with pytest.raises(ValueError):
            findings.normalize_categories(conn, fold_map={VARIANT: 1})

    def test_a_non_mapping_is_refused(self, conn):
        with pytest.raises(ValueError):
            findings.normalize_categories(conn, fold_map=[VARIANT, CANON])

    def test_an_explicit_map_touches_only_the_spellings_it_names(self, conn):
        _insert_raw(conn, fid="CB-1", category=VARIANT)
        _insert_raw(conn, fid="CB-2", category="Other Thing", description=DESC_B)

        report = findings.normalize_categories(
            conn, fold_map={VARIANT: CANON}, apply=True
        )

        rows = {r["id"]: r for r in _snapshot(conn)}
        assert rows["CB-1"]["category"] == CANON
        assert rows["CB-2"]["category"] == "Other Thing"
        assert report["fold_map"] == {VARIANT: CANON}


class TestNonStringCategoryIsSkipped:
    def test_a_legacy_non_string_category_is_counted_not_raised(self, conn):
        _insert_raw(conn, fid="CB-1", category=VARIANT)
        # A BLOB, not an integer: the column has TEXT affinity, so SQLite converts
        # an inserted number to text and the "non-string" case never materializes
        # that way (measured — the first draft of this test asserted against a `7`
        # that arrived back as `'7'`). BLOB is the storage class TEXT affinity
        # leaves alone, and it is what `sqlite3` hands back as non-`str`.
        conn.execute("UPDATE findings SET category = CAST(7 AS BLOB) WHERE id = 'CB-1'")
        conn.commit()
        assert not isinstance(_snapshot(conn)[0]["category"], str)

        report = findings.normalize_categories(conn, apply=True)

        assert report["counts"]["skipped_non_string"] == 1
        assert report["renames"] == []


class TestSurfacesAreRegistered:
    def test_the_cli_verb_exists(self):
        commands = {}

        class _Sub:
            def add_parser(self, name, **kw):
                import argparse

                return argparse.ArgumentParser(add_help=False)

        findings.register_cli(_Sub(), commands)
        assert "categories-normalize" in commands

    def test_the_mcp_tool_exists(self):
        registered = []

        class _Mcp:
            def tool(self, *a, **kw):
                def deco(fn):
                    registered.append(fn.__name__)
                    return fn

                return deco

        findings.register_tools(_Mcp(), lambda: None)
        assert "categories_normalize" in registered
