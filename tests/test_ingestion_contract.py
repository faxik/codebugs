"""Ingestion contract: CB-56 (strip-with-visibility) + CB-80 (batch payload shape).

One unit, one seam (owner brief, DIR-1 T-59): `batch_add_findings` routes through
`_add_one` exactly like `add_finding`, so CB-56's behavior change on the ADD path
and CB-80's container validation both land on the identical entry points and must
report the identical response shape. Kept in one file for the same reason the
brief keeps it one unit rather than two cards worked separately.
"""

from __future__ import annotations

import sqlite3

import pytest

from codebugs import findings, reqs

LONG_DESC = "worker crashed while flushing the queue after the retry budget was exhausted"
LONG_DESC_2 = "worker crashed while flushing the queue after the retry budget was exceeded"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    findings.ensure_schema(c)
    yield c
    c.close()


@pytest.fixture
def reqs_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    reqs.ensure_schema(c)
    yield c
    c.close()


class TestCB56RoundTrip:
    """The get -> modify -> add round trip that CB-56 was filed against.

    Before the fix: an MCP client that reads a card back (whose meta the
    identity machinery had stamped, e.g. `category_minted`) and re-files a
    variant carrying that SAME meta hits `ValueError: meta keys [...] are
    reserved`, though the identical call succeeded before CB-45 introduced the
    reservation. After the fix: the call succeeds, and the response NAMES what
    it stripped rather than silently discarding it (CB-15's shape applies to a
    silent strip exactly as it applies to a silent refusal).
    """

    def test_round_trip_no_longer_refused_and_names_the_strip(self, conn):
        first = findings.add_finding(
            conn,
            severity="low",
            category="round_trip_cat",
            file="f.py",
            description=LONG_DESC,
            new_category=True,
        )
        assert first["meta"]["category_minted"] is True  # a genuine mint

        fetched = findings.get_finding(conn, first["id"])
        # The client edits the description and re-files, passing the meta
        # exactly AS FETCHED — the shape CB-56's card reproduces.
        result = findings.add_finding(
            conn,
            severity="low",
            category="round_trip_cat",
            file="f.py",
            description=LONG_DESC_2,
            meta=dict(fetched["meta"]),
        )
        # No ValueError reached this line at all — the call succeeded.
        assert result["dedup_action"] == "created"
        # `loc` is ALSO reserved — `loc.py` registers its own pre-add resolver
        # with `meta_keys=("loc",)`, and every add gets stamped with it, so a
        # fetched card carries it forward exactly like `category_minted`. Both
        # are stripped, and both are named — proof the union really is DYNAMIC
        # (`db.resolver_reserved_meta_keys()`) rather than a hand-picked list.
        assert result["stripped_meta_keys"] == ["category_minted", "loc"]
        # The category already existed by the second call, so nothing here
        # re-mints it — a survived spoof is the only way this key could
        # reappear, and it did not.
        assert "category_minted" not in result["meta"]

    def test_ordinary_add_reports_an_empty_strip_list(self, conn):
        # BT-5 discipline extended to this key (CLAUDE.md CB-43(11)): the key
        # is UNCONDITIONAL and `[]` is a normal answer ("checked, nothing to
        # strip"), never an absent channel.
        result = findings.add_finding(
            conn,
            severity="low",
            category="plain",
            file="f.py",
            description=LONG_DESC,
            new_category=True,
            meta={"rule_code": "E501"},
        )
        assert result["stripped_meta_keys"] == []
        assert result["meta"]["rule_code"] == "E501"

    def test_resolver_errors_still_refused_on_add(self, conn):
        # The one named exception (CB-56 §2): `resolver_errors` reports a
        # FAILURE state, not machinery output a caller would innocently carry
        # forward, and CLAUDE.md documents it as refused on both paths.
        with pytest.raises(ValueError, match="resolver_errors"):
            findings.add_finding(
                conn,
                severity="low",
                category="plain",
                file="f.py",
                description=LONG_DESC,
                new_category=True,
                meta={"resolver_errors": [{"resolver": "spoofed", "error": "x"}]},
            )


class TestCB56Batch:
    """`batch_add_findings` must report the identical shape, per member."""

    def test_batch_member_strips_and_reports_like_add(self, conn):
        first = findings.add_finding(
            conn,
            severity="low",
            category="batch_cat",
            file="f.py",
            description=LONG_DESC,
            new_category=True,
        )
        fetched_meta = dict(first["meta"])
        results = findings.batch_add_findings(
            conn,
            [
                {
                    "severity": "low",
                    "category": "batch_cat",
                    "file": "f.py",
                    "description": LONG_DESC_2,
                    "meta": fetched_meta,
                }
            ],
        )
        [result] = results
        assert result["dedup_action"] == "created"
        # `loc` is also reserved (see TestCB56RoundTrip's note); the batch
        # entry point must report the identical union, per member.
        assert result["stripped_meta_keys"] == ["category_minted", "loc"]
        assert "category_minted" not in result["meta"]

    def test_batch_member_without_reserved_keys_reports_empty(self, conn):
        [result] = findings.batch_add_findings(
            conn,
            [
                {
                    "severity": "low",
                    "category": "batch_plain",
                    "file": "f.py",
                    "description": LONG_DESC,
                }
            ],
            new_category=True,
        )
        assert result["stripped_meta_keys"] == []

    def test_empty_batch_still_returns_empty_list(self, conn):
        # Must behave exactly as before this unit's change (brief §6).
        assert findings.batch_add_findings(conn, []) == []


class TestCB80BatchPayloadShape:
    """The batch entry points must refuse a malformed CONTAINER with ValueError,
    never leak a raw TypeError (CLAUDE.md's Error-handling contract).
    """

    def test_findings_int_container_refused(self, conn):
        with pytest.raises(ValueError):
            findings.batch_add_findings(conn, 5)

    def test_findings_list_of_non_mapping_refused(self, conn):
        with pytest.raises(ValueError):
            findings.batch_add_findings(conn, [5])

    def test_requirements_string_container_refused(self, reqs_conn):
        # THE interesting case (CB-80): a str IS iterable, so a check that only
        # tests each ITERATED element — never the container itself — silently
        # walks CHARACTERS instead of refusing here, and dies two frames deeper
        # with `TypeError: string indices must be integers`.
        with pytest.raises(ValueError):
            reqs.batch_add_requirements(reqs_conn, "ab")

    def test_requirements_int_container_refused(self, reqs_conn):
        with pytest.raises(ValueError):
            reqs.batch_add_requirements(reqs_conn, 5)

    def test_requirements_list_of_non_mapping_refused(self, reqs_conn):
        with pytest.raises(ValueError):
            reqs.batch_add_requirements(reqs_conn, [5])

    def test_findings_empty_batch_unaffected(self, conn):
        assert findings.batch_add_findings(conn, []) == []

    def test_requirements_empty_batch_unaffected(self, reqs_conn):
        assert reqs.batch_add_requirements(reqs_conn, []) == []


class TestImportVsAddDivergence:
    """CB-56 §4 asks whether CSV import's strip and the new add-path strip
    coincide, and to report a divergence rather than silently reconcile it.

    They do NOT fully coincide: `import_findings` strips the identical dynamic
    reserved union `_validate_meta_keys` computes, but it does not carve out
    `resolver_errors` as a standing exception the way the add path now does —
    `_import_meta`'s `dropped_keys` is built from
    `db.resolver_reserved_meta_keys()`, which unconditionally INCLUDES the
    runner's own error key. So a CSV row carrying `resolver_errors` in its
    meta column imports silently (stripped, no refusal), while the identical
    dict passed to `add_finding` raises. This predates this unit's change on
    every OTHER reserved key (import already stripped where add refused); this
    unit narrows the gap to exactly this one key rather than closing it,
    because import's contract (CB-51: "an import is not an observation") is a
    separate negotiated decision from add's, and reopening it is out of this
    unit's scope. Pinned here as the finding, not silently reconciled.
    """

    def test_import_silently_strips_resolver_errors(self, conn):
        row = {
            "category": "import_div",
            "file": "f.py",
            "description": LONG_DESC,
            "meta": '{"resolver_errors": [{"resolver": "peer", "error": "x"}]}',
        }
        report = findings.import_findings(conn, [row])
        assert report.imported == 1
        assert report.errors == []
        [row_out] = findings.query_findings(conn, category="import_div")["findings"]
        assert "resolver_errors" not in row_out["meta"]
