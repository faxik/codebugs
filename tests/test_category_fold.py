"""Tests for the retro-fold of stored categories (CB-61), in BOTH of its modes.

The file used to say "spellings" here and mean it: every class above the CB-222
banner near the bottom works on ``VARIANT``/``CANON``, two spellings of one
word. That was the same half-truth the three surfaces told (CB-222) — the code
has always been able to fold one category NAME into a DIFFERENT one, which is
the mode a tracker's rare names are collapsed with, and nothing tested it. The
classes below the banner cover that mode, the CB-223 gate on its target, and the
strictness of the two booleans that decide whether a run writes.

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
import re
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


# ===========================================================================
# CB-222 + CB-223 — MERGING DIFFERENT CATEGORY NAMES
#
# Everything above this line works on the pair VARIANT="Process Improvement" /
# CANON="process_improvement": two SPELLINGS of one word. The mode the owner
# actually intends to use — folding one category NAME into a different one —
# had no test at all, collision class included, which is why the surfaces could
# promise only spellings for months without anything going red (CB-222).
#
# `MERGE_FROM`/`MERGE_TO` are two canonical names, neither a spelling of the
# other: `normalize_category` maps each to itself and the Levenshtein distance
# between them is far past any near-miss threshold, so nothing here can pass by
# accidentally exercising the spelling path.
# ===========================================================================

MERGE_FROM = "data_loss"
MERGE_TO = "correctness"
ABSENT_TARGET = "totaly_new_typo_name"


def _categories(conn):
    return sorted(
        r["category"] for r in conn.execute("SELECT DISTINCT category FROM findings")
    )


class TestMergingDifferentNames:
    """Oracle rows 1-3: the merge mode itself, which nothing pinned before."""

    def test_a_row_moves_to_the_other_name_and_its_auto_v1_is_rederived(self, conn):
        """Row 1. Two assertions, because two different mutants live here: one that
        writes the category and keeps the old hash (identity silently desyncs from
        the stored inputs), and one that re-keys without moving the category."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)
        old_fp = _snapshot(conn)[0]["fingerprint"]

        report = findings.normalize_categories(
            conn, fold_map={MERGE_FROM: MERGE_TO}, apply=True
        )

        assert report["applied"] is True and report["stopped"] is False
        assert report["fold_map"] == {MERGE_FROM: MERGE_TO}
        rows = {r["id"]: r for r in _snapshot(conn)}
        assert rows["CB-1"]["category"] == MERGE_TO
        assert rows["CB-1"]["fingerprint"] == findings._derive_fingerprint(
            MERGE_TO, "a.py", DESC, {}
        )
        assert rows["CB-1"]["fingerprint"] != old_fp
        # The whole point of the operation, stated as the user states it.
        assert _categories(conn) == [MERGE_TO]

    def test_re_running_the_same_map_renames_nothing(self, conn):
        """Row 2. Idempotent by RESULT: after the fold the key matches no stored
        category, so the second run has nothing to do. Pins that a second `--apply`
        is harmless, which is what an operator retrying actually does."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        # CB-2 is what makes MERGE_TO an EXISTING target, and it must survive the
        # fold: without it the second run would be refused by the CB-223 gate
        # rather than reporting nothing to do, which is a different property.
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)
        findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO}, apply=True)

        again = findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO})

        assert again["renames"] == []
        assert again["fold_map"] == {}
        assert again["stopped"] is False

    def test_a_merge_that_would_collide_stops_and_writes_nothing(self, conn):
        """Row 3. The collision class, on DIFFERENT names rather than spellings.

        Same file and description under two names, so folding one into the other
        lands both live rows on one fingerprint — the state the partial unique
        index forbids and the one an automatic merge would have to invent a winner
        for.
        """
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO)
        before = _snapshot(conn)

        report = findings.normalize_categories(
            conn, fold_map={MERGE_FROM: MERGE_TO}, apply=True
        )

        assert report["stopped"] is True and report["applied"] is False
        assert _snapshot(conn) == before
        assert len(report["collisions"]) == 1
        collision = report["collisions"][0]
        assert collision["fingerprint"] == findings._derive_fingerprint(
            MERGE_TO, "a.py", DESC, {}
        )
        assert {r["id"] for r in collision["rows"]} == {"CB-1", "CB-2"}


class TestFoldTargetMustExist:
    """Oracle rows 4, 5, 7 (CB-223): a target this tracker does not hold is a MINT."""

    def test_an_unknown_target_is_refused_and_nothing_is_written(self, conn):
        """Row 4. Before this, the call returned 0, moved every matching row and
        left the tracker holding one MORE category name than it started with —
        in the operation whose only purpose is to hold fewer."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        before = _snapshot(conn)

        with pytest.raises(ValueError) as exc:
            findings.normalize_categories(
                conn, fold_map={MERGE_FROM: ABSENT_TARGET}, apply=True
            )

        assert ABSENT_TARGET in str(exc.value)
        assert _snapshot(conn) == before
        assert _categories(conn) == [MERGE_FROM]

    def test_a_near_miss_target_names_the_canonical_spelling(self, conn):
        """The other half of `_gate_category`'s vocabulary, reached through the
        fold. Read on its merits rather than assumed: the message was written for
        `add`, and this asserts it still identifies the RIGHT existing name when
        the caller is a fold target rather than an observation."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)

        with pytest.raises(ValueError) as exc:
            findings.normalize_categories(conn, fold_map={MERGE_FROM: "corectness"})

        assert "near-miss" in str(exc.value)
        assert repr(MERGE_TO) in str(exc.value)

    def test_every_name_the_refusal_prints_is_a_value_this_command_accepts(self, conn):
        """The COMPOSITION of the two refusals, which neither one can establish.

        `_gate_category` was written for `add`, where the caller's category is
        normalized at the boundary, so naming the STORED spelling is helpful there.
        A fold target gets no such boundary — `_validate_fold_map` refuses a target
        that is not already canonical — so naming a stored spelling here advised a
        value the very next run rejects. Reproduced on a pre-CB-60 corpus, which is
        exactly the corpus this command exists for; a fixture whose stored spellings
        are already canonical cannot exhibit it, which is why the fixture below
        stores a VARIANT.

        The assertion is deliberately not "the message contains X": it takes the
        names out of both refusal branches and FEEDS THEM BACK to the command, which
        is the only form that cannot drift from what the command accepts.
        """
        _insert_raw(conn, fid="CB-1", category=VARIANT)
        _insert_raw(conn, fid="CB-2", category=MERGE_FROM, description=DESC_B)

        # (a) the near-miss branch: one letter short of the canonical form.
        with pytest.raises(ValueError) as near:
            findings.normalize_categories(conn, fold_map={MERGE_FROM: CANON[:-1]})
        advised = re.findall(r"'([^']*)'", str(near.value))
        # (b) the wholly-new branch: its "nearest existing" list.
        with pytest.raises(ValueError) as new:
            findings.normalize_categories(conn, fold_map={MERGE_FROM: "zz_unrelated_name"})
        listed = re.findall(r"'([^']*)'", str(new.value))

        candidates = [n for n in advised + listed if n not in (CANON[:-1], "zz_unrelated_name")]
        assert candidates, "the refusals named nothing at all — the check would be vacuous"
        for name in candidates:
            # Accepted means: not refused as non-canonical, and not refused as an
            # unknown target. Any name a refusal offers must clear BOTH.
            findings.normalize_categories(conn, fold_map={MERGE_FROM: name})

    def test_the_flag_permits_the_mint_rather_than_walling_it_off(self, conn):
        """Row 5. A gate with no way out is a wall, not a diagnostic — so the same
        call with permission must actually land the new name."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)

        report = findings.normalize_categories(
            conn,
            fold_map={MERGE_FROM: ABSENT_TARGET},
            apply=True,
            new_category=True,
        )

        assert report["applied"] is True
        assert _categories(conn) == [ABSENT_TARGET]

    def test_the_dry_run_refuses_exactly_as_the_apply_does(self, conn):
        """Row 7, and it is the row a `if apply:` mutant survives every other test
        on. A dry run's job is to say what WOULD happen, and "it would refuse" is a
        legitimate answer; a run that passes dry and refuses on apply is worse than
        either behaviour on its own. Both modes are asserted here rather than in
        two places, because the property is that they AGREE."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        before = _snapshot(conn)

        with pytest.raises(ValueError):
            findings.normalize_categories(conn, fold_map={MERGE_FROM: ABSENT_TARGET})
        with pytest.raises(ValueError):
            findings.normalize_categories(
                conn, fold_map={MERGE_FROM: ABSENT_TARGET}, apply=True
            )
        assert _snapshot(conn) == before

    def test_a_target_is_judged_even_when_its_key_matches_no_row(self, conn):
        """The gate validates the ARGUMENT, like `_validate_fold_map` above it, so
        it does not wait to see whether the key matches anything. Stated as its own
        test because it is a real behaviour change on a tracker with no findings at
        all, where the call used to be a silent 0-rename no-op."""
        with pytest.raises(ValueError) as exc:
            findings.normalize_categories(conn, fold_map={"nothing_stored": ABSENT_TARGET})
        assert "no categories yet" in str(exc.value)

    def test_folding_into_the_empty_category_stays_ungated(self, conn):
        """PIN OF PRESERVED BEHAVIOUR — green before this unit too, and said out loud
        so a reader does not take it for a regression test on a defect that shipped.

        `""` is a legal, deliberately ungated category everywhere else (CB-60),
        and the new gate must not have quietly made it a mint. A tracker whose
        rows all carry `""` is a legitimate state to fold INTO."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)

        report = findings.normalize_categories(
            conn, fold_map={MERGE_FROM: ""}, apply=True
        )

        assert report["applied"] is True
        assert _categories(conn) == [""]

    def test_a_name_held_only_by_a_terminal_row_is_still_an_existing_target(self, conn):
        """PIN OF PRESERVED BEHAVIOUR, like the empty-category test above: green on
        the parent commit as well, because there was no gate there to refuse it.

        `_existing_categories` reads the whole table, terminal rows included — a
        category used only by fixed cards still EXISTS. Pinned because the new gate
        is the first caller for which "exists" decides a refusal rather than a
        warning, so a future status filter there would become a false refusal."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(
            conn, fid="CB-2", category=MERGE_TO, description=DESC_B, status="fixed"
        )

        report = findings.normalize_categories(
            conn, fold_map={MERGE_FROM: MERGE_TO}, apply=True
        )

        assert report["applied"] is True


class TestDerivedFoldIsNotGated:
    """Oracle row 6. PIN OF PRESERVED BEHAVIOUR, not a guard against a mutant that
    once shipped — say so, or a reader takes it for a regression test.

    The mechanical fold (`fold_map=None`) must keep working untouched, and the
    reason it is exempt is a PROOF rather than a carve-out: its targets are
    `normalize_category(stored)`, and `_existing_categories` keys on exactly that
    normalized form, so every derived target is already present. The danger the
    row guards is the plausible wrong fix — gating the derived mode against the
    RAW stored spellings — which would make the migration refuse itself on the
    very corpus it exists to clean.
    """

    def test_the_mechanical_fold_still_runs_where_the_canon_never_appeared(self, conn):
        """The behavioural half: the ONLY stored spelling is the variant, so
        `process_improvement` has never existed as a stored value."""
        _insert_raw(conn, fid="CB-1", category=VARIANT)
        assert _categories(conn) == [VARIANT]

        report = findings.normalize_categories(conn, apply=True)

        assert report["applied"] is True
        assert _categories(conn) == [CANON]

    def test_premise_existing_categories_is_keyed_by_the_normalized_form(self, conn):
        """The PREMISE the exemption rests on, pinned so the proof cannot rot
        silently. Reaches into two private helpers deliberately: the claim being
        pinned is about their contract with each other, which no public call can
        express, and a premise test is this repository's sanctioned shape for
        exactly that (see the git/argparse premise tests).

        The third assertion IS the mutant, written out rather than described: a
        gate keyed on the raw stored spellings refuses the derived target, so if
        anyone ever moves the gate above the `fold_map is not None` guard, this
        line says what breaks and why.
        """
        _insert_raw(conn, fid="CB-1", category=VARIANT)

        existing = findings._existing_categories(conn)
        assert normalize_category(VARIANT) in existing
        assert findings._gate_category(existing, CANON, new_category=False) is False

        raw_keyed = {VARIANT: VARIANT}
        with pytest.raises(ValueError):
            findings._gate_category(raw_keyed, CANON, new_category=False)


class TestCliMergeSurface:
    """Oracle row 8: the flag has to travel from argv to the domain call."""

    def test_an_unknown_target_prints_one_line_and_exits_1(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        conn.commit()
        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "codebugs", "--tracker-root", tmp_project, "categories-normalize",
                "--fold-map", json.dumps({MERGE_FROM: ABSENT_TARGET}),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()

        assert exc.value.code == 1
        captured = capsys.readouterr()
        # `domain_errors()` already owns this shape — one line on stderr, nothing
        # on stdout, no traceback. Asserted rather than assumed, per the brief.
        assert captured.out == ""
        assert len(captured.err.strip().splitlines()) == 1
        assert ABSENT_TARGET in captured.err

    def test_the_new_category_flag_reaches_the_domain(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        """The mutant this exists for: `--new-category` declared on the parser and
        never passed to `normalize_categories`. The golden cannot see that — it
        records the parser, not the call — and the refusal above stays green."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        conn.commit()
        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "codebugs", "--tracker-root", tmp_project, "categories-normalize",
                "--fold-map", json.dumps({MERGE_FROM: ABSENT_TARGET}),
                "--new-category",
            ],
        )
        cli.main()  # no SystemExit

        out = capsys.readouterr().out
        assert ABSENT_TARGET in out and MERGE_FROM in out


def _tool_error():
    """The SDK's wrapper type. Named rather than `Exception`: a bare `Exception`
    would pass on an import error or a typo in the tool name, so the trap would
    stop discriminating exactly when it matters. Measured on this SDK — a domain
    `ValueError` and a strict-bool refusal both arrive as `ToolError`."""
    from mcp.server.mcpserver.exceptions import ToolError

    return ToolError


class TestMcpMergeSurface:
    """Oracle rows 9 and 10, through the REAL `MCPServer` + `call_tool` pipeline.

    A hand-rolled stub cannot stand in here: row 10 is a claim about where
    pydantic validates, and row 9 is a claim about what the tool BODY forwards —
    the wire golden records the declared schema and is structurally blind to a
    parameter that is declared and then dropped on the floor (the CB-157 shape).
    """

    @staticmethod
    def _mcp(tmp_project):
        """A per-call connection, exactly as `server.py`'s own `_conn` does it.

        Not the test's own connection: `call_tool` runs the handler OFF the
        calling thread and a `sqlite3` connection is bound to the thread that
        created it, so handing the fixture's object over raises before any of
        this file's assertions are reached.
        """
        from contextlib import contextmanager

        from mcp.server.mcpserver import MCPServer

        @contextmanager
        def factory():
            c = db.connect(tmp_project)
            try:
                yield c
            finally:
                c.close()

        mcp = MCPServer("cb222-cb223-fold-test")
        findings.register_tools(mcp, factory)
        return mcp

    @staticmethod
    def _call(mcp, name, **arguments):
        import asyncio

        return asyncio.run(mcp.call_tool(name, arguments))

    def test_an_unknown_target_propagates_as_a_tool_error(self, conn, tmp_project):
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        mcp = self._mcp(tmp_project)

        with pytest.raises(_tool_error()) as exc:
            self._call(
                mcp,
                "categories_normalize",
                fold_map={MERGE_FROM: ABSENT_TARGET},
                apply=True,
            )

        assert ABSENT_TARGET in str(exc.value)
        assert _categories(conn) == [MERGE_FROM]

    def test_the_new_category_argument_reaches_the_body(self, conn, tmp_project):
        """CB-157's rule applied here: DECLARING the parameter is not the same as
        FORWARDING it. A wrapper that accepts `new_category` and calls
        `normalize_categories` without it keeps a green golden and a green schema,
        and refuses this call."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        mcp = self._mcp(tmp_project)

        result = self._call(
            mcp,
            "categories_normalize",
            fold_map={MERGE_FROM: ABSENT_TARGET},
            apply=True,
            new_category=True,
        )

        assert result.structured_content["applied"] is True
        assert _categories(conn) == [ABSENT_TARGET]

    def test_a_float_apply_is_refused_before_any_row_moves(self, conn, tmp_project):
        """Row 10, and the reason the `("categories_normalize", "apply")` row left
        `DECLARED_EXCEPTIONS`. Lax pydantic coerces `1.0 -> True`, which turns the
        dry-run DEFAULT — the only brake on a corpus-wide re-key — into a mass
        rewrite for the literal that most looks like a client's mistake. The
        assertion that matters is the second one: nothing moved."""
        _insert_raw(conn, fid="CB-1", category=VARIANT)
        mcp = self._mcp(tmp_project)

        with pytest.raises(_tool_error()):
            self._call(mcp, "categories_normalize", apply=1.0)

        assert _categories(conn) == [VARIANT]

    def test_a_float_new_category_is_refused_too(self, conn, tmp_project):
        """The new parameter is born strict; it never had a row in the exceptions
        table to remove. Pinned behaviourally beside `apply` so the two cannot
        drift apart."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        mcp = self._mcp(tmp_project)

        with pytest.raises(_tool_error()):
            self._call(
                mcp,
                "categories_normalize",
                fold_map={MERGE_FROM: ABSENT_TARGET},
                new_category=1.0,
            )

        assert _categories(conn) == [MERGE_FROM]


# ===========================================================================
# CB-207 + CB-209 — THE DRY RUN ANSWERS ITS OWN QUESTION
#
# A dry run exists for one question: "will this do what I meant?" Two things it
# did silently, both measured before this change:
#
#   CB-207  a fold_map KEY matching no stored category renamed nothing and
#           appeared in no part of the report — not `renames`, not a counter —
#           so a typo on the left-hand side was visible only to someone
#           diffing the `from -> to` table against their own map by eye.
#   CB-209  the collision stop-rule reads LIVE rows only, because the partial
#           unique index it defends is live-only. Every other identity merge —
#           two closed cards onto one hash, or a closed card onto a live one —
#           applied at exit 0 with an empty `collisions`.
#
# Both are answered by REPORTING, never by refusing: `unmatched_fold_keys` and
# `merged_identities` are unconditional lists on the `attention` discipline,
# and neither touches `stopped` or the exit code. The states they describe are
# legal — an inert key writes nothing, and CB-43's `recurrence_of` contract
# reaches a shared terminal hash with no fold at all. Creating them in SILENCE
# is what was wrong.
# ===========================================================================


def _live_recurrence_twins(conn):
    """Two TERMINAL rows sharing one fingerprint, reached with NO fold at all.

    TWO STEPS, and the second is the whole point. The obvious construction —
    file, dismiss, re-observe — stops at `(wont_fix, open)`, which is a
    (terminal, LIVE) pair and legal for a different reason: the partial unique
    index forbids two LIVE rows and says nothing about a terminal beside a live
    one. Closing the recurrence row as well is what produces the pair this
    fixture is named for, and a test built on one step would conclude the whole
    premise of CB-209 was false.
    """
    first = findings.add_finding(
        conn, severity="high", category=MERGE_TO, file="a.py",
        description=DESC, new_category=True,
    )
    findings.update_finding(conn, finding_id=first["id"], status="wont_fix")
    second = findings.add_finding(
        conn, severity="high", category=MERGE_TO, file="a.py", description=DESC
    )
    assert second["id"] != first["id"], "premise: a dismissed card refiles a recurrence"
    findings.update_finding(conn, finding_id=second["id"], status="fixed")
    rows = conn.execute("SELECT status, fingerprint FROM findings").fetchall()
    assert len(rows) == 2 and len({r["fingerprint"] for r in rows}) == 1, (
        "premise: the two rows share one fingerprint"
    )
    assert all(r["status"] in ("wont_fix", "fixed") for r in rows), (
        "premise: BOTH rows are terminal — a (terminal, live) pair is a different state"
    )
    return first["id"], second["id"]


class TestUnmatchedFoldKeys:
    """Oracle rows 1-4 (CB-207)."""

    def test_a_key_matching_no_stored_category_is_named(self, conn):
        """Row 1. The motivating shape: one pair right, one letter wrong."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        # The target has to be a category this tracker holds, or CB-223's gate
        # refuses the whole call before any of this is reached.
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)

        report = findings.normalize_categories(
            conn, fold_map={MERGE_FROM: MERGE_TO, "data_looss": MERGE_TO}
        )

        assert report["unmatched_fold_keys"] == ["data_looss"]
        # The typo really was inert: the good pair still did its work.
        assert [r["id"] for r in report["renames"]] == ["CB-1"]

    def test_a_key_whose_target_equals_the_stored_value_is_not_called_unmatched(self, conn):
        """Row 2, and the reason this list CANNOT be derived from `renames`.

        Such a key names its category perfectly and asks for no change, so
        `_fold_row_decision` returns `unchanged` and the row reaches neither
        `renames` nor any counter. A report computed from the RESULT would
        therefore accuse a correct key of matching nothing — measured against
        exactly that mutant. The set is built from the stored categories the
        traversal SAW instead.
        """
        _insert_raw(conn, fid="CB-1", category=MERGE_TO)

        report = findings.normalize_categories(conn, fold_map={MERGE_TO: MERGE_TO})

        assert report["renames"] == [], "premise: an identity pair renames nothing"
        assert report["unmatched_fold_keys"] == []

    def test_a_non_string_stored_category_is_not_something_a_key_can_match(self, conn):
        """A BLOB category is skipped by the fold, so it is not in the set a key
        is judged against either — one predicate, read from the row's own kind
        rather than re-spelled here."""
        _insert_raw(conn, fid="CB-1", category=MERGE_TO)
        _insert_raw(conn, fid="CB-2", category=MERGE_FROM, description=DESC_B)
        conn.execute("UPDATE findings SET category = CAST(7 AS BLOB) WHERE id = 'CB-2'")
        conn.commit()

        report = findings.normalize_categories(conn, fold_map={"7": MERGE_TO})

        assert report["counts"]["skipped_non_string"] == 1
        assert report["unmatched_fold_keys"] == ["7"]

    def test_a_precanonical_stored_spelling_is_matched_exactly_and_not_normalized(self, conn):
        """The match is EXACT against the stored string, because that is what the
        fold does (`fold_map.get(stored)`). A key typed in canonical form does
        not reach a stored "Process Improvement", and saying so is the truth
        about that run — this pins the report agreeing with the fold rather than
        judging by a second, kinder rule."""
        _insert_raw(conn, fid="CB-1", category=VARIANT)

        report = findings.normalize_categories(conn, fold_map={CANON: MERGE_TO},
                                               new_category=True)

        assert report["renames"] == [], "premise: the canonical key does not match"
        assert report["unmatched_fold_keys"] == [CANON]

    def test_a_fully_matched_map_leaves_the_key_present_and_empty(self, conn):
        """Row 3. `[]` means "looked, there are none" and must never collapse into
        an absent key — the `attention`/`stripped_meta_keys` discipline."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)

        report = findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO})

        assert "unmatched_fold_keys" in report
        assert report["unmatched_fold_keys"] == []

    def test_the_derived_mode_reports_nothing_and_still_carries_the_key(self, conn):
        """Row 4 — A PIN OF PRESERVED BEHAVIOUR, not a guard against a mutant.

        With no `fold_map` there are no keys to miss: the targets ARE the stored
        spellings. `[]` is the honest answer there, and the key is still present
        for the same reason it is everywhere else.
        """
        _insert_raw(conn, fid="CB-1", category=VARIANT)

        report = findings.normalize_categories(conn)

        assert "unmatched_fold_keys" in report
        assert report["unmatched_fold_keys"] == []
        assert report["renames"] != [], "premise: the derived fold still had work to do"


class TestMergedIdentities:
    """Oracle rows 5-7 (CB-209)."""

    def test_a_fold_that_fuses_two_terminal_cards_names_the_pair(self, conn):
        """Row 5, the card's own scenario. Two closed cards with the same file and
        description under different category names; the fold makes their hashes
        equal, and the stop-rule cannot see it because neither row is live."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="fixed")
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="fixed")

        report = findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO})

        assert report["collisions"] == [], "premise: the live stop-rule stays blind here"
        assert len(report["merged_identities"]) == 1
        group = report["merged_identities"][0]
        assert sorted(r["id"] for r in group["rows"]) == ["CB-1", "CB-2"]
        assert {r["status"] for r in group["rows"]} == {"fixed"}
        assert group["fingerprint"] == report["renames"][0]["new_fingerprint"]

    def test_a_fold_that_fuses_a_closed_card_onto_a_live_one_is_named_too(self, conn):
        """MEASURED SCOPE, WIDER THAN THE CARD'S WORDING AND DELIBERATELY SO.

        CB-209 speaks of terminal pairs. The same silence covers a closed card
        folded onto a LIVE card's hash: exactly one live member, so the stop-rule
        does not fire, and before this change that applied at exit 0 with an empty
        `collisions` — measured. Scoping the report to all-terminal groups would
        have left an identity merge of the same class reported by nothing at all.
        """
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="fixed")
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="open")

        report = findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO})

        assert report["stopped"] is False
        assert len(report["merged_identities"]) == 1
        assert sorted(
            (r["id"], r["status"]) for r in report["merged_identities"][0]["rows"]
        ) == [("CB-1", "fixed"), ("CB-2", "open")]

    def test_a_legal_recurrence_pair_that_predates_the_run_is_not_reported(self, conn):
        """ROW 6, AND THE WHOLE DIFFICULTY OF CB-209.

        Two terminal rows on one fingerprint are a legal, ordinary state reached
        with no fold at all. Without this row, "widen the stop-rule to every
        status" passes every other row in this class and starts crying wolf on
        `recurrence_of` twins the migration never touched.
        """
        _live_recurrence_twins(conn)

        derived = findings.normalize_categories(conn)
        explicit = findings.normalize_categories(conn, fold_map={MERGE_TO: MERGE_TO})

        assert derived["merged_identities"] == []
        assert explicit["merged_identities"] == []

    def test_a_legal_pair_renamed_wholesale_is_not_reported(self, conn):
        """The pair moves TOGETHER to a new hash. Its membership at that
        fingerprint goes from nobody to both, so a rule phrased as "the
        membership changed" flags it — and nothing was merged. Counting the
        DISTINCT PRE-FOLD fingerprints the members arrived carrying answers
        correctly: one value, one identity, already together.
        """
        _live_recurrence_twins(conn)

        report = findings.normalize_categories(
            conn, fold_map={MERGE_TO: MERGE_FROM}, new_category=True
        )

        assert len(report["renames"]) == 2, "premise: both rows really were renamed"
        assert report["merged_identities"] == []

    def test_a_group_that_only_loses_members_is_not_reported(self, conn):
        """THE MIRROR CASE, AND IT IS REACHABLE — the first version of this test
        asserted it was not, and adversarial review built it.

        Three rows share one hash. Two carry the category the map names and are
        re-keyed; the third carries that hash under a category the map leaves
        alone, so it stays put. Folding renames those two OUT from under it: the
        old group shrinks to one, and the pair lands together on a new hash.
        Nothing was merged, and nothing is reported.

        What makes the rule immune is not that the case cannot happen but its
        shape: every member still sitting on a fingerprint it did not move to
        carries that same value as its pre-fold one, so a shrinking group holds
        exactly one distinct value and can never clear the authorship test.
        """
        shared = findings._derive_fingerprint(MERGE_TO, "a.py", DESC, {})
        _insert_raw(conn, fid="CB-1", category=MERGE_TO, status="fixed", fingerprint=shared)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="fixed", fingerprint=shared)
        _insert_raw(conn, fid="CB-3", category=MERGE_FROM, status="fixed", fingerprint=shared)

        assert len({r["fingerprint"] for r in _snapshot(conn)}) == 1, (
            "premise: all three really do start on one hash"
        )

        report = findings.normalize_categories(conn, fold_map={MERGE_TO: MERGE_FROM})

        assert sorted(r["id"] for r in report["renames"]) == ["CB-1", "CB-2"], (
            "premise: exactly the two movable rows depart, leaving the third alone"
        )
        assert report["renames"][0]["new_fingerprint"] != shared
        assert report["merged_identities"] == []

    def test_a_terminal_row_with_a_non_string_fingerprint_does_not_break_the_run(self, conn):
        """A REGRESSION THIS UNIT INTRODUCED AND THEN CLOSED, kept as its pin.

        SQLite's dynamic typing permits a BLOB fingerprint exactly as it permits
        a BLOB category. The new map holds every row with a post-fold identity,
        where the stop-rule's map held only live ones — so a CLOSED row carrying
        such a token entered a `sorted()` for the first time, and `sorted()`
        cannot order bytes against str. Measured against this branch's parent:
        the terminal case ran fine there and raised `TypeError` here, until such
        tokens were excluded from that map. The exclusion loses nothing, because
        a re-derived fingerprint is always a `str`, so no row can ever arrive at
        a BLOB value and the row itself is never re-keyed.

        The LIVE case is untouched and still raises — it did so before this unit
        as well, and it is the stop-rule's map, which this change must not move.
        """
        _insert_raw(conn, fid="CB-1", category=MERGE_TO, status="fixed")
        _insert_raw(conn, fid="CB-2", category=MERGE_FROM, description=DESC_B, status="fixed")
        conn.execute(
            "UPDATE findings SET fingerprint = CAST(x'DEADBEEF' AS BLOB) WHERE id = 'CB-2'"
        )
        conn.commit()
        stored = conn.execute(
            "SELECT fingerprint FROM findings WHERE id = 'CB-2'"
        ).fetchone()["fingerprint"]
        assert not isinstance(stored, str), "premise: the token really is not a string"

        report = findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO})

        assert report["counts"]["supplied_untouched"] == 1
        assert report["merged_identities"] == []

    def test_a_row_with_a_null_fingerprint_never_joins_a_group(self, conn):
        """A NULL hash is not an identity — it matches nothing — so such a row is
        outside both the stop-rule's map and this one, exactly as before."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="fixed", fingerprint=None)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="fixed", fingerprint=None)

        report = findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO})

        assert report["counts"]["null_untouched"] == 1
        assert report["merged_identities"] == []

    def test_the_live_stop_rule_is_unchanged(self, conn):
        """Row 7 — A PIN OF PRESERVED BEHAVIOUR. Two LIVE rows fused by the fold
        still stop the run, still write nothing, and are NOT listed a second time
        under the new key: that run's refusal is the operator's whole answer."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="open")
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="open")
        before = _snapshot(conn)

        report = findings.normalize_categories(
            conn, fold_map={MERGE_FROM: MERGE_TO}, apply=True
        )

        assert report["stopped"] is True
        assert report["applied"] is False
        assert len(report["collisions"]) == 1
        assert report["merged_identities"] == []
        assert _snapshot(conn) == before

    def test_the_key_is_present_and_empty_on_an_ordinary_fold(self, conn):
        """`[]` means "looked, none", never "no such channel"."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)

        report = findings.normalize_categories(conn, fold_map={MERGE_FROM: MERGE_TO})

        assert "merged_identities" in report
        assert report["merged_identities"] == []


class TestNeitherKeyRefuses:
    """Oracle row 8. Both are information; `stopped` stays the stop-rule's alone."""

    def test_a_run_carrying_both_signals_still_applies_and_exits_0(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="fixed")
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="fixed")
        conn.commit()

        report = findings.normalize_categories(
            conn,
            fold_map={MERGE_FROM: MERGE_TO, "data_looss": MERGE_TO},
            apply=True,
        )

        assert report["unmatched_fold_keys"] == ["data_looss"]
        assert len(report["merged_identities"]) == 1
        assert report["stopped"] is False
        assert report["applied"] is True

        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            ["codebugs", "--tracker-root", tmp_project, "categories-normalize",
             "--fold-map", json.dumps({MERGE_TO: MERGE_TO, "data_looss": MERGE_TO}),
             "--apply"],
        )
        cli.main()  # no SystemExit: neither key is a refusal
        assert "data_looss" in capsys.readouterr().out


class TestFoldReportCliAndJson:
    """Oracle row 9. Human output prints a section only when it has something to
    say; `--json` carries both keys always, because there "checked, none" is the
    answer a machine reader needs."""

    def _run(self, tmp_project, monkeypatch, capsys, *args):
        from codebugs import cli

        monkeypatch.setattr(
            sys,
            "argv",
            ["codebugs", "--tracker-root", tmp_project, "categories-normalize", *args],
        )
        cli.main()
        return capsys.readouterr().out

    def test_both_sections_print_when_they_have_content(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="fixed")
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="fixed")
        conn.commit()

        out = self._run(
            tmp_project, monkeypatch, capsys,
            "--fold-map", json.dumps({MERGE_FROM: MERGE_TO, "data_looss": MERGE_TO}),
        )

        assert "matched NO stored category" in out
        assert "'data_looss'" in out
        assert "would be SHARED" in out
        assert "CB-1" in out and "CB-2" in out

    def test_neither_section_prints_when_empty(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)
        conn.commit()

        out = self._run(
            tmp_project, monkeypatch, capsys,
            "--fold-map", json.dumps({MERGE_FROM: MERGE_TO}),
        )

        assert "matched NO stored category" not in out
        assert "would be SHARED" not in out
        assert "unmatched_fold_keys" not in out
        assert "merged_identities" not in out

    def test_a_stopped_run_does_not_claim_the_merges_happen(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        """Found by adversarial review. With a live collision AND a terminal merge
        in one run, the unconditional wording put two sentences in one report
        asserting opposite things: the collision block says NOTHING was written,
        while the merge block said the run is not refused."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="open")
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="open")
        _insert_raw(conn, fid="CB-3", category=MERGE_FROM, status="fixed", description=DESC_B)
        _insert_raw(conn, fid="CB-4", category=MERGE_TO, status="fixed", description=DESC_B)
        conn.commit()

        out = self._run(
            tmp_project, monkeypatch, capsys,
            "--fold-map", json.dumps({MERGE_FROM: MERGE_TO}),
        )

        assert "STOPPED — nothing written" in out
        assert "WOULD be shared" in out
        assert "none of this happens either" in out
        assert "the run is not refused" not in out

    def test_the_consequence_named_for_a_live_member_is_not_the_closed_twin_one(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        """Found by adversarial review, and it is the shape this feature meets
        most often: closing a CB-113(a) spelling fork puts a CLOSED card beside a
        LIVE one. There nothing is revived and nothing is abandoned — the live
        card keeps taking the observations. The first draft of this line stated
        the closed-twins consequence for every group, which is false here."""
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM, status="fixed")
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, status="open")
        conn.commit()

        out = self._run(
            tmp_project, monkeypatch, capsys,
            "--fold-map", json.dumps({MERGE_FROM: MERGE_TO}),
        )

        assert "would be SHARED" in out
        assert "Where BOTH" in out and "still open" in out

    def test_the_human_list_is_capped_and_says_how_many_it_did_not_show(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        """Found by adversarial review, which measured this section growing to
        4513 lines on a corpus forked at scale — past the pipe buffer, so
        `… | head` began exiting 141. Capping the HUMAN list hides nothing,
        because the count is stated and `--json` is never capped."""
        wanted = findings._FOLD_MERGED_GROUPS_SHOWN + 3
        for i in range(wanted):
            _insert_raw(conn, fid=f"CB-{2 * i + 1}", category=MERGE_FROM,
                        status="fixed", description=f"{DESC} number {i}")
            _insert_raw(conn, fid=f"CB-{2 * i + 2}", category=MERGE_TO,
                        status="fixed", description=f"{DESC} number {i}")
        conn.commit()

        out = self._run(
            tmp_project, monkeypatch, capsys,
            "--fold-map", json.dumps({MERGE_FROM: MERGE_TO}),
        )

        assert f"!! {wanted} fingerprints would be SHARED" in out
        assert "… and 3 more not shown; --json carries the whole list." in out

        payload = json.loads(
            self._run(tmp_project, monkeypatch, capsys,
                      "--fold-map", json.dumps({MERGE_FROM: MERGE_TO}), "--json")
        )
        assert len(payload["merged_identities"]) == wanted, "--json is never capped"

    def test_json_carries_both_keys_even_when_empty(
        self, conn, tmp_project, monkeypatch, capsys
    ):
        _insert_raw(conn, fid="CB-1", category=MERGE_FROM)
        _insert_raw(conn, fid="CB-2", category=MERGE_TO, description=DESC_B)
        conn.commit()

        out = self._run(
            tmp_project, monkeypatch, capsys,
            "--fold-map", json.dumps({MERGE_FROM: MERGE_TO}), "--json",
        )
        payload = json.loads(out)

        assert payload["unmatched_fold_keys"] == []
        assert payload["merged_identities"] == []


class TestFoldReportDocumentsItsOwnKeys:
    """The three surfaces used to promise the opposite of what the code now does
    — "accepted in silence and appears nowhere in the report" was in the domain
    docstring, the MCP tool description and the CHANGELOG at once. A contract
    described in three places and fixed in one is this repository's most-repeated
    defect, so the prose is pinned against the code."""

    def test_the_domain_docstring_names_both_new_keys(self):
        doc = findings.normalize_categories.__doc__ or ""
        assert "unmatched_fold_keys" in doc
        assert "merged_identities" in doc
        assert "appears NOWHERE in the report" not in doc

    def test_the_changelog_no_longer_says_an_unmatched_key_is_invisible(self):
        text = (pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text()
        assert "appears nowhere in the report" not in text
