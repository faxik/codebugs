"""Tests for the codebench benchmark results module."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from codebugs import bench, db


@pytest.fixture
def conn():
    """In-memory database with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    bench.ensure_schema(c)
    yield c
    c.close()


SAMPLE_CSV = """\
method,P@5,MRR,recall
bm25,0.72,0.65,0.58
dense,0.81,0.71,0.64
hybrid,0.85,0.74,0.67
"""

SAMPLE_JSON = json.dumps([
    {"method": "bm25", "P@5": 0.72, "MRR": 0.65},
    {"method": "dense", "P@5": 0.81, "MRR": 0.71},
])

SCALAR_CSV = """\
test,duration_s
build,12.4
"""


class TestImportCsv:
    def test_basic_import(self, conn):
        result = bench.import_csv(conn, benchmark="search-perf", csv_data=SAMPLE_CSV)
        assert result["run_id"] == "BE-1"
        assert result["benchmark"] == "search-perf"
        assert result["rows"] == 3
        assert result["results_stored"] == 9  # 3 rows * 3 metrics
        assert result["metrics"] == ["P@5", "MRR", "recall"]

    def test_auto_increment_id(self, conn):
        r1 = bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV)
        r2 = bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV)
        assert r1["run_id"] == "BE-1"
        assert r2["run_id"] == "BE-2"

    def test_explicit_run_id(self, conn):
        result = bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, run_id="BE-99")
        assert result["run_id"] == "BE-99"

    def test_date_defaults_to_today(self, conn):
        result = bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV)
        assert len(result["date"]) == 10  # YYYY-MM-DD

    def test_explicit_date(self, conn):
        result = bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, date="2026-01-15")
        assert result["date"] == "2026-01-15"

    def test_tags_and_meta(self, conn):
        bench.import_csv(
            conn, benchmark="a", csv_data=SAMPLE_CSV,
            tags=["nightly"], meta={"git_sha": "abc123"},
        )
        runs = bench.list_runs(conn, benchmark="a")
        run = runs["runs"][0]
        assert run["tags"] == ["nightly"]
        assert run["meta"]["git_sha"] == "abc123"

    def test_scalar_csv(self, conn):
        result = bench.import_csv(conn, benchmark="build", csv_data=SCALAR_CSV)
        assert result["rows"] == 1
        assert result["results_stored"] == 1

    def test_empty_csv_raises(self, conn):
        with pytest.raises(ValueError, match="no data rows"):
            bench.import_csv(conn, benchmark="a", csv_data="method,score\n")

    def test_single_column_raises(self, conn):
        with pytest.raises(ValueError, match="at least 2 columns"):
            bench.import_csv(conn, benchmark="a", csv_data="method\nbm25\n")

    def test_non_numeric_raises(self, conn):
        with pytest.raises(ValueError, match="Non-numeric"):
            bench.import_csv(conn, benchmark="a", csv_data="method,score\nbm25,bad\n")

    def test_empty_label_raises(self, conn):
        with pytest.raises(ValueError, match="Row label"):
            bench.import_csv(conn, benchmark="a", csv_data="method,score\n,0.5\n")

    def test_blank_metric_skipped(self, conn):
        csv_data = "method,P@5,MRR\nbm25,0.72,\n"
        result = bench.import_csv(conn, benchmark="a", csv_data=csv_data)
        assert result["results_stored"] == 1  # MRR skipped


class TestImportCsvTypeGuard:
    """CB-75. `import_csv` fed csv_data straight to `io.StringIO`, so a non-str
    leaked "TypeError: initial_value must be str or None, not int" instead of the
    ValueError the module contract promises ("Domain functions raise ValueError
    for invalid input"). The CSV twin of CB-72.

    Scope, stated precisely because the first draft of this docstring overclaimed
    and a cross-model review caught it: this closes the WRONG-TYPE door only. An
    ordinary str payload can still raise sqlite3.IntegrityError (duplicate row
    labels, duplicate headers, a `nan` metric) from inside the insert loop — a
    different defect, filed separately, and NOT fixed here.

    NON-VACUITY, measured rather than asserted: every payload below except None
    was run against main's bench.py on 2026-08-17 (commit 41058c5) and raised a
    stdlib TypeError. `None` is the exception and is called out in its own test —
    it already raised a ValueError, just the wrong one.

    ASSERTIONS ARE EXACT EQUALITY, not `match=`. `pytest.raises(match=...)` runs
    re.SEARCH, and `re.escape` only escapes metacharacters — it adds no anchors —
    so a `match=` test also passes for a message with junk prefixed or appended,
    including a contradictory one. (The import_json class below still says
    `re.escape` "anchors the WHOLE message"; that claim is wrong for the same
    reason and is corrected there.) Exactness is what makes these tests
    distinguish this positive up-front guard from a blanket
    `except TypeError: raise ValueError(<same text>)` around the body — a rewrap
    that would also convert a post-commit failure into bad input.

    Reachability is IN-PROCESS ONLY — the MCP wire type is `csv_data: str | None`
    (a pydantic refusal precedes the wrapper body), which is why this is `low`.
    """

    _MSG = "csv_data must be CSV text as str, not "

    def _refusal(self, conn, payload) -> str:
        with pytest.raises(ValueError) as excinfo:
            bench.import_csv(conn, benchmark="a", csv_data=payload)
        return str(excinfo.value)

    @pytest.mark.parametrize(
        ("payload", "type_name"),
        [
            pytest.param(5, "int", id="int"),
            pytest.param([], "list", id="empty list"),
            pytest.param(["a,b", "1,2"], "list", id="list of lines"),
            pytest.param(b"x,y\n1,2\n", "bytes", id="bytes"),
            pytest.param(bytearray(b"x,y\n1,2\n"), "bytearray", id="bytearray"),
            pytest.param(None, "NoneType", id="None"),
            pytest.param({"a": 1}, "dict", id="dict"),
        ],
    )
    def test_non_str_payload_raises_value_error_naming_the_type(
        self, conn, payload, type_name
    ):
        assert self._refusal(conn, payload) == self._MSG + type_name

    def test_bytes_are_refused_rather_than_decoded(self, conn):
        """Deliberate divergence from import_json, which WIDENED its annotation to
        accept bytes because json.loads already took them. io.StringIO never
        accepted bytes, so no caller can be importing that way today — refusing
        them is a contract fix, whereas decoding them would be a new feature
        wearing a bugfix costume. Pinned so a future "consistency" change has to
        argue with this test.
        """
        assert self._refusal(conn, b"method,score\nbm25,0.7\n") == self._MSG + "bytes"

    def test_none_is_refused_instead_of_reporting_the_wrong_fault(self, conn):
        """`None` did NOT leak a TypeError — io.StringIO(None) is legal and yields
        an empty stream, so this already raised ValueError, just the wrong one:
        "CSV must have at least 2 columns" describes a malformed header, not a
        missing payload. So this test passes on both trees by exception TYPE and
        discriminates only on the MESSAGE. Said plainly because a reader cannot
        otherwise tell it from a broken test.
        """
        assert self._refusal(conn, None) == self._MSG + "NoneType"

    def test_a_class_spoofing_str_is_refused_not_left_to_leak_typeerror(self, conn):
        """The guard reads `type(x)`, never `isinstance`, and this is the test that
        makes the difference visible.

        CPython's isinstance honours a `__class__` property, so an object
        declaring `__class__ -> str` satisfies `isinstance(x, str)` and then hits
        io.StringIO's own TypeError anyway — CB-75's exact leak surviving its own
        fix. Not a contrived threat: `unittest.mock.MagicMock(spec=str)` is
        precisely such an object, and this repo already pins a mock-shaped trap
        for the same reason (CB-25's mock.ANY case in tests/test_types.py).

        The general rule this pins: the guard's predicate must be IDENTICAL to
        the consumer's requirement. StringIO checks the real type, so the guard
        must too. Found by a Codex diff review; verified by running both spellings.
        """
        class SpoofsStr:
            @property
            def __class__(self):
                return str

        payload = SpoofsStr()
        assert isinstance(payload, str), "premise: this payload defeats an isinstance guard"
        assert self._refusal(conn, payload) == self._MSG + "SpoofsStr"

    def test_a_mock_specced_as_str_is_refused(self, conn):
        """The realistic form of the case above — a test double, not an attacker."""
        payload = MagicMock(spec=str)
        assert isinstance(payload, str), "premise: spec=str defeats an isinstance guard"
        assert self._refusal(conn, payload).startswith(self._MSG)

    def test_a_str_subclass_still_imports(self, conn):
        """`issubclass(type(x), str)`, not `type(x) is str`: a str subclass is CSV
        text and must keep working. There is no split-view hazard to guard against
        here — the reason import_json needs `list(json_data)` and this does not
        (CB-74). Verified: StringIO reads the real content, not __str__.
        """
        class Csv(str):
            pass

        result = bench.import_csv(conn, benchmark="a", csv_data=Csv(SCALAR_CSV))
        assert result["rows"] == 1

    def test_empty_string_still_reaches_the_parser(self, conn):
        """CB-67's ratified rule: an empty DATA PAYLOAD is supplied content, so it
        must reach the parser and raise ITS ValueError. A truthiness guard
        (`if not csv_data`) would refuse it with the type message instead, which
        is why the check tests the type and nothing else.
        """
        with pytest.raises(ValueError, match="at least 2 columns"):
            bench.import_csv(conn, benchmark="a", csv_data="")


class TestImportJson:
    def test_basic_import(self, conn):
        result = bench.import_json(conn, benchmark="search-perf", json_data=SAMPLE_JSON)
        assert result["run_id"] == "BE-1"
        assert result["rows"] == 2
        assert result["results_stored"] == 4

    def test_non_array_raises(self, conn):
        with pytest.raises(ValueError, match="non-empty array"):
            bench.import_json(conn, benchmark="a", json_data='{"x": 1}')

    def test_empty_array_raises(self, conn):
        with pytest.raises(ValueError, match="non-empty array"):
            bench.import_json(conn, benchmark="a", json_data="[]")

    def test_import_list_directly(self, conn):
        data = [{"method": "bm25", "P@5": 0.72, "MRR": 0.55},
                {"method": "dense", "P@5": 0.81, "MRR": 0.63}]
        result = bench.import_json(conn, benchmark="search-perf", json_data=data)
        assert result["rows"] == 2
        assert result["results_stored"] == 4

    def test_empty_list_raises(self, conn):
        with pytest.raises(ValueError, match="non-empty array"):
            bench.import_json(conn, benchmark="a", json_data=[])


class TestImportJsonShapeGuard:
    """CB-72 + CB-74. import_json accepted its argument without checking its
    shape, so two inputs walked past the module's own contract ("domain
    functions raise ValueError for invalid input") and out as stdlib
    exceptions: a payload outside `str | bytes | bytearray | list` as
    TypeError from json.loads (CB-72, in-process only), and an array whose
    ELEMENTS are not objects as AttributeError from `data[0].keys()` (CB-74,
    reachable from an MCP client because the wire type is `str | list | None`).

    NON-VACUITY, measured rather than asserted: every refusal case below was
    run against main's bench.py on 2026-08-17 (commit 3cd23d0) and raised the
    stdlib exception named in its row — never a ValueError. The `object then
    int` row is the one that matters most: unfixed it does not die at
    data[0].keys() at all, but later inside csv.DictWriter, which is exactly
    the door a data[0]-only guard would leave open.

    `match=` is load-bearing, not decoration: it pins the whole message TEXT via
    re.escape, so a message naming the accepted type wrongly does not pass.
    (Correction, 2026-08-17: this used to claim re.escape "anchors" the message.
    It does not — `match=` runs re.SEARCH and re.escape adds no anchors, so a
    message with text prefixed or appended still passes. The claim was wrong, not
    the tests; TestImportCsvTypeGuard above asserts exact equality instead, which
    is the stronger form to copy.) Without a message assertion the class passes
    against a blanket
    `except (TypeError, AttributeError): raise ValueError(...)` around the
    function body — a shape that would ALSO convert a post-commit failure
    inside import_csv into a ValueError, which _cmd_bench_import's arm then
    reports as bad input for a write that already landed (the CB-15/CB-16
    lie). A loose fragment regex is nearly as bad: "element 0 .*not int" also
    matches a message that got the accepted set wrong.
    """

    _TYPES = "json_data must be a JSON array as str/bytes/bytearray, or a list of objects, not "

    # (case id, payload, exception raised by the UNFIXED tree, exact refusal message)
    REFUSED = [
        ("dict", {}, TypeError, _TYPES + "dict"),
        ("int", 5, TypeError, _TYPES + "int"),
        ("list of ints", [1, 2], AttributeError, "JSON array element 0 must be an object, not int"),
        ("string of ints", "[1, 2]", AttributeError,
         "JSON array element 0 must be an object, not int"),
        ("string of null", "[null]", AttributeError,
         "JSON array element 0 must be an object, not NoneType"),
        ("object then int", [{"a": 1, "b": 2}, 5], AttributeError,
         "JSON array element 1 must be an object, not int"),
    ]

    @pytest.mark.parametrize(
        ("payload", "message"),
        [pytest.param(p, m, id=i) for i, p, _, m in REFUSED],
    )
    def test_bad_shape_raises_value_error_naming_what_was_wrong(self, conn, payload, message):
        with pytest.raises(ValueError, match=re.escape(message)):
            bench.import_json(conn, benchmark="a", json_data=payload)

    def test_a_list_subclass_cannot_show_the_guard_one_view_and_the_consumer_another(self, conn):
        """The guard iterates; the code after it INDEXES (data[0]) and iterates
        again (writerows). A list subclass whose __iter__ disagrees with
        __getitem__ therefore passed a check on mappings and then handed a
        non-mapping to data[0] — CB-74's exact AttributeError, surviving inside
        its own fix. Found by Codex diff review against the FIRST
        implementation of this guard, not against main.

        The discriminator is "no AttributeError", NOT a refusal. Once the list
        is materialized once, the two views cannot diverge — the single
        snapshot is both validated and consumed — so this imports the iterated
        row instead of raising anything. Asserting a ValueError here was the
        first draft of this test and was simply wrong: it demanded the guard
        reject a payload the fix makes coherent.
        """

        class SplitList(list):
            def __iter__(self):
                return iter([{"method": "bm25", "score": 0.5}])

        result = bench.import_json(conn, benchmark="a", json_data=SplitList([1]))
        assert result["rows"] == 1
        assert result["results_stored"] == 1

    def test_a_duck_typed_row_is_refused_and_that_is_a_deliberate_narrowing(self, conn):
        """Guarding on Mapping refuses an object that merely implements
        .keys()/.get() without registering as one — and such an object DOES
        import on main. Pinned because it is a real behaviour change, not
        because it is desirable: "an array of objects" is the documented
        contract, the refusal is loud and at the boundary, and no caller sends
        such a row. If someone ever does, this test is where the decision is
        recorded and can be revisited.
        """

        class DuckRow:
            def keys(self):
                return ["method", "score"]

            def get(self, key, default=None):
                return {"method": "bm25", "score": 0.5}.get(key, default)

        with pytest.raises(ValueError, match=re.escape("element 0 must be an object, not DuckRow")):
            bench.import_json(conn, benchmark="a", json_data=[DuckRow()])

    # --- accepted shapes: these worked before the guard and must still work ---

    def test_bytes_and_bytearray_still_import(self, conn):
        """The guard's accepted set is exactly what json.loads already takes.
        Both of these import successfully on the unfixed tree, so refusing them
        would have been a behaviour change smuggled in as a bugfix — this test
        is the pin for that decision, and it passes on BOTH trees deliberately.
        """
        payload = b'[{"method": "bm25", "score": 0.5}]'
        assert bench.import_json(conn, benchmark="a", json_data=payload)["rows"] == 1
        assert bench.import_json(conn, benchmark="a", json_data=bytearray(payload))["rows"] == 1

    def test_mappings_that_are_not_dicts_still_import(self, conn):
        """Guard on collections.abc.Mapping, not dict. `isinstance(el, dict)`
        would newly refuse MappingProxyType, which imports fine today —
        csv.DictWriter needs only .keys()/.get(), both guaranteed by Mapping.
        Passes on both trees by design, for the same reason as above.
        """
        from types import MappingProxyType

        data = [MappingProxyType({"method": "bm25", "score": 0.5})]
        assert bench.import_json(conn, benchmark="a", json_data=data)["rows"] == 1


class TestQuery:
    @pytest.fixture(autouse=True)
    def _seed(self, conn):
        bench.import_csv(conn, benchmark="sp", csv_data=SAMPLE_CSV, date="2026-03-28", run_id="BE-1")
        bench.import_csv(conn, benchmark="sp", csv_data=SAMPLE_CSV, date="2026-03-29", run_id="BE-2")

    def test_query_by_row(self, conn):
        result = bench.query(conn, benchmark="sp", group_by="row")
        assert result["runs_matched"] == 2
        assert result["group_by"] == "row"
        # One table per run
        data = result["data"]
        assert len(data) == 2
        # Each table has 3 rows (bm25, dense, hybrid)
        assert len(data[0]["rows"]) == 3
        assert "P@5" in data[0]["rows"][0]

    def test_query_by_run(self, conn):
        result = bench.query(conn, benchmark="sp", group_by="run")
        data = result["data"]
        # One table per row_label
        assert len(data) == 3  # bm25, dense, hybrid
        # Each table has 2 rows (one per run)
        assert len(data[0]["rows"]) == 2
        assert "run_id" in data[0]["rows"][0]

    def test_filter_by_metrics(self, conn):
        result = bench.query(conn, benchmark="sp", metrics=["P@5"])
        for table in result["data"]:
            for row in table["rows"]:
                assert "P@5" in row
                assert "MRR" not in row

    def test_filter_by_rows(self, conn):
        result = bench.query(conn, benchmark="sp", rows=["bm25"])
        for table in result["data"]:
            for row in table["rows"]:
                assert row["row_label"] == "bm25"

    def test_filter_by_date_range(self, conn):
        result = bench.query(conn, benchmark="sp", date_from="2026-03-29")
        assert result["runs_matched"] == 1
        assert result["run_ids"] == ["BE-2"]

    def test_filter_by_runs(self, conn):
        result = bench.query(conn, benchmark="sp", runs=["BE-1"])
        assert result["runs_matched"] == 1

    def test_last_n(self, conn):
        result = bench.query(conn, benchmark="sp", last_n=1)
        assert result["runs_matched"] == 1
        assert result["run_ids"] == ["BE-2"]  # most recent

    def test_csv_format(self, conn):
        result = bench.query(conn, benchmark="sp", format="csv", last_n=1)
        assert "csv" in result
        assert "bm25" in result["csv"]
        assert "P@5" in result["csv"]

    def test_no_matches(self, conn):
        result = bench.query(conn, benchmark="nonexistent")
        assert result["runs_matched"] == 0
        assert result["data"] == []

    def test_invalid_group_by(self, conn):
        with pytest.raises(ValueError, match="group_by"):
            bench.query(conn, benchmark="sp", group_by="bad")

    def test_invalid_format(self, conn):
        with pytest.raises(ValueError, match="format"):
            bench.query(conn, benchmark="sp", format="xml")


class TestList:
    def test_list_benchmarks(self, conn):
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, date="2026-03-28")
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, date="2026-03-29")
        bench.import_csv(conn, benchmark="b", csv_data=SCALAR_CSV, date="2026-03-30")

        result = bench.list_benchmarks(conn)
        assert len(result["benchmarks"]) == 2
        bm_a = next(b for b in result["benchmarks"] if b["benchmark"] == "a")
        assert bm_a["run_count"] == 2
        assert bm_a["first_date"] == "2026-03-28"
        assert bm_a["last_date"] == "2026-03-29"

    def test_list_runs(self, conn):
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, date="2026-03-28")
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, date="2026-03-29")

        result = bench.list_runs(conn, benchmark="a")
        assert len(result["runs"]) == 2

    def test_list_runs_last_n(self, conn):
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, date="2026-03-28")
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV, date="2026-03-29")

        result = bench.list_runs(conn, benchmark="a", last_n=1)
        assert len(result["runs"]) == 1
        assert result["runs"][0]["date"] == "2026-03-29"

    def test_list_empty(self, conn):
        result = bench.list_benchmarks(conn)
        assert result["benchmarks"] == []


class TestDelete:
    def test_delete_run(self, conn):
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV)
        result = bench.delete_run(conn, "BE-1")
        assert result["deleted"] == "BE-1"
        assert result["results_removed"] == 9

        # Verify gone
        assert bench.list_runs(conn, benchmark="a")["runs"] == []

    def test_delete_benchmark(self, conn):
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV)
        bench.import_csv(conn, benchmark="a", csv_data=SAMPLE_CSV)
        result = bench.delete_benchmark(conn, "a")
        assert result["runs_removed"] == 2
        assert result["results_removed"] == 18

    def test_delete_nonexistent_run(self, conn):
        with pytest.raises(KeyError, match="not found"):
            bench.delete_run(conn, "BE-999")

    def test_delete_nonexistent_benchmark(self, conn):
        with pytest.raises(KeyError, match="not found"):
            bench.delete_benchmark(conn, "nonexistent")


class TestExclusiveArguments:
    """CB-67. bench-import and bench-delete each take exactly one of two mutually
    exclusive arguments. That contract was written down only inside the MCP
    wrappers, so both CLI handlers PICKED A WINNER where the contract says refuse
    — and codebench_import validated with one predicate (`is not None`) while
    dispatching with another (`if csv_data:`), so an empty payload passed
    validation as supplied and then failed dispatch as absent.

    Each test below fails against the unfixed tree. The vacuity trap that would
    have made it pass on BOTH trees is named in its docstring, because all three
    naive forms were vacuous.
    """

    CSV = "method,score\nbm25,0.72\n"
    JSON = '[{"method": "dense", "score": 0.99}]'

    @staticmethod
    def _tools(conn):
        from contextlib import contextmanager

        @contextmanager
        def factory():
            yield conn

        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, *a, **k):
                def deco(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return deco

        m = FakeMCP()
        bench.register_tools(m, factory)
        return m.tools

    @staticmethod
    def _cli(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            cwd=project,
            capture_output=True,
            text=True,
        )

    # --- D3: validation and dispatch disagreed inside one function -----------

    def test_mcp_import_empty_csv_reaches_the_csv_parser(self, conn):
        """An empty csv_data is SUPPLIED CONTENT, so it must reach import_csv and
        raise that parser's contracted ValueError.

        Vacuity trap: `pytest.raises(Exception)` or `raises((ValueError, TypeError))`
        passes on both trees, because the unfixed tree raises TypeError from
        json.loads(None). Only matching the CSV parser's own message discriminates.
        A second trap: calling the DOMAIN import_csv("") proves nothing — it already
        raises correctly on both trees — so this must go through the MCP closure.
        """
        with pytest.raises(ValueError, match="at least 2 columns"):
            self._tools(conn)["codebench_import"](benchmark="b", csv_data="")

    # --- the MCP contract these CLI handlers were missing --------------------

    def test_mcp_refuses_both_payloads(self, conn):
        with pytest.raises(ValueError, match="not both"):
            self._tools(conn)["codebench_import"](
                benchmark="b", csv_data=self.CSV, json_data=self.JSON
            )

    def test_mcp_refuses_both_delete_targets(self, conn):
        with pytest.raises(ValueError, match="not both"):
            self._tools(conn)["codebench_delete"](run_id="BE-1", benchmark="a")

    # --- D1: the CLI import picked a winner ----------------------------------

    def test_cli_import_refuses_two_sources_instead_of_discarding_one(self, tmp_path):
        """Unfixed: `bench-import data.csv --json-file other.json` reports success
        and imports the JSON, silently discarding the CSV.

        Vacuity trap: asserting "one run was imported" passes on both trees — both
        import exactly one run, only the CONTENT differs. So the fixtures carry
        disjoint row labels and the assertion is that NOTHING landed.
        The positional fixture must not be named *.json, or the extension sniff
        confounds the result.
        """
        db.init_project(str(tmp_path))
        (tmp_path / "data.csv").write_text(self.CSV)
        (tmp_path / "other.json").write_text(self.JSON)

        out = self._cli(tmp_path, "bench-import", "data.csv", "--json-file", "other.json", "-b", "X")
        assert out.returncode == 1, out.stdout + out.stderr
        assert "not both" in (out.stdout + out.stderr), out.stdout + out.stderr

        c = db.connect(str(tmp_path))
        try:
            assert bench.list_runs(c, benchmark="X")["runs"] == []
        finally:
            c.close()

    def test_cli_import_still_infers_json_from_a_bare_positional(self, tmp_path):
        """The refusal must not cost the extension sniff: `bench-import foo.json`
        with no flag has always imported as JSON, and still must. Without this,
        a label-driven fix routes positional JSON into the CSV parser."""
        db.init_project(str(tmp_path))
        (tmp_path / "results.json").write_text(self.JSON)

        out = self._cli(tmp_path, "bench-import", "results.json", "-b", "X")
        assert out.returncode == 0, out.stdout + out.stderr

        c = db.connect(str(tmp_path))
        try:
            runs = bench.list_runs(c, benchmark="X")["runs"]
            assert len(runs) == 1, runs
        finally:
            c.close()

    # --- D2: the CLI delete picked a winner ----------------------------------

    def test_cli_delete_refuses_two_targets_instead_of_ignoring_one(self, tmp_path):
        """Unfixed: `bench-delete --run-id BE-1 --benchmark X` deletes BE-1, exits 0,
        and silently ignores --benchmark.

        Vacuity trap: seeding BE-1 INTO X makes "X is gone" true on both trees, since
        deleting the only run empties it. So BE-1 lives under A and X is a separate
        benchmark, and all three of (refusal, BE-1 survives, X survives) are asserted.
        Also asserts stderr is a message rather than a traceback — that is what
        catches a handler whose except clause does not cover the new ValueError.
        """
        db.init_project(str(tmp_path))
        c = db.connect(str(tmp_path))
        try:
            run = bench.import_csv(c, benchmark="A", csv_data=self.CSV)
            bench.import_csv(c, benchmark="X", csv_data=self.CSV)
        finally:
            c.close()

        out = self._cli(tmp_path, "bench-delete", "--run-id", run["run_id"], "--benchmark", "X")
        assert out.returncode == 1, out.stdout + out.stderr
        assert "not both" in (out.stdout + out.stderr), out.stdout + out.stderr
        assert "Traceback" not in out.stderr, out.stderr

        c = db.connect(str(tmp_path))
        try:
            assert [r["run_id"] for r in bench.list_runs(c, benchmark="A")["runs"]] == [
                run["run_id"]
            ]
            assert bench.list_runs(c, benchmark="X")["runs"] != []
        finally:
            c.close()

    # --- what must NOT change ------------------------------------------------

    def test_supplied_means_what_each_argument_kind_says_it_means(self, conn, tmp_path):
        """Revision 1 of this fix unified "supplied" as `is not None` everywhere and
        would have broken all four of these. An empty DATA PAYLOAD is supplied
        content; an empty PATH or ENTITY ID is not a value at all. Pinned so the
        next revision cannot quietly re-unify the axis.
        """
        tools = self._tools(conn)

        # An empty entity id still reads as "not supplied" on the delete wrapper.
        with pytest.raises(ValueError, match="Provide run_id or benchmark"):
            tools["codebench_delete"](benchmark="")

        # An empty JSON array is supplied content and reaches the JSON parser.
        with pytest.raises(ValueError, match="non-empty array"):
            tools["codebench_import"](benchmark="b", json_data=[])

        # An empty path still reads as "not supplied" on the CLI.
        db.init_project(str(tmp_path))
        out = self._cli(tmp_path, "bench-import", "", "-b", "Z")
        assert out.returncode == 1
        assert "Provide" in (out.stdout + out.stderr), out.stdout + out.stderr
        assert "Traceback" not in out.stderr, out.stderr


class TestFileIOErrorContract:
    """CB-71. `bench-import` had TWO defects in the same five lines.

    (1) The file read sat inside a `try` whose only arm was `except (ValueError,
    json.JSONDecodeError)`, and a read raises neither — so an unreadable path
    escaped as a raw traceback.

    (2) That same `try` also spanned the SUCCESS print, which runs after
    import_csv/import_json commit. So a post-commit failure from that statement
    was caught by the input-validation arm and reported as one tidy line at exit
    1 for a run that had already landed — the CB-15/CB-16 success-shaped lie,
    live, in the code CB-71 was filed against.

    NON-VACUITY, which is the whole risk here. Exit code 1 and "the path appears
    on stderr" are BOTH satisfied by the unfixed tree (an uncaught exception
    already exits 1 and its traceback already contains the path), so neither
    discriminates and both are kept only to pin the contract jointly. The real
    discriminators are opposite in sign: `"Traceback" not in stderr` for the read
    guard, and `"Traceback" IN stderr` for the post-commit case — the latter
    mirroring TestRetriageCliContract's stored-corruption test in
    tests/test_findings.py, this repo's template for "a failure raised after the
    write must not be disguised as an input error".
    """

    @staticmethod
    def _cli(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            capture_output=True, text=True, cwd=str(project),
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )

    def test_missing_path_is_a_clean_error_not_a_traceback(self, tmp_path):
        db.init_project(str(tmp_path))
        r = self._cli(tmp_path, "bench-import", "missing.csv", "-b", "Q")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert "missing.csv" in r.stderr, r.stderr

    def test_a_directory_path_is_a_clean_error_not_a_traceback(self, tmp_path):
        """IsADirectoryError, covered by the same guard and otherwise unpinned."""
        db.init_project(str(tmp_path))
        (tmp_path / "adir").mkdir()
        r = self._cli(tmp_path, "bench-import", "adir", "-b", "Q")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert "adir" in r.stderr, r.stderr

    def test_a_post_commit_output_failure_is_not_laundered_into_an_input_error(self, tmp_path):
        """The load-bearing half: every test above passes IDENTICALLY for the
        localized guard this fix uses and for the handler-wide `except OSError`
        it rejects, so none of them can fail when the rule is broken. This one
        can.

        Mechanism, measured rather than assumed: closing the `sys.stdout` OBJECT
        makes `print` raise `ValueError: I/O operation on closed file.`, which is
        exactly what the pre-existing arm catches. (Closing fd 1 instead raises
        OSError, which that arm never caught, and under block buffering the
        failure surfaces at interpreter shutdown outside every handler — that
        wider family is CB-78.)

        Against the unfixed tree this run printed the single line "I/O operation
        on closed file." with exit 1 while the BE run was committed and visible
        in bench-list. Verified red on bc3f67e before the fix landed.
        """
        db.init_project(str(tmp_path))
        (tmp_path / "good.csv").write_text("label,metric\na,1\n")
        script = (
            "import sys\n"
            "sys.argv = ['codebugs', 'bench-import', 'good.csv', '-b', 'LANDED']\n"
            "from codebugs.cli import main\n"
            "sys.stdout.close()\n"
            "main()\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=str(tmp_path),
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )

        # The premise: the import really did commit before the output failed.
        c = db.connect(str(tmp_path))
        try:
            landed = c.execute(
                "SELECT COUNT(*) AS n FROM codebench_runs WHERE benchmark = 'LANDED'"
            ).fetchone()["n"]
        finally:
            c.close()
        assert landed == 1, "premise broken: nothing committed, so there is no lie to detect"

        # The discriminator: a committed write whose output failed must CRASH,
        # not come back as a tidy input error.
        assert "Traceback" in r.stderr, (
            "a post-commit output failure must not be disguised as an input error; "
            f"stderr was {r.stderr!r}"
        )
        assert r.stderr.strip() != "I/O operation on closed file.", r.stderr


class TestImportArgumentValidation:
    """CB-82 — a write path must not invent a value from a falsey wrong type.

    `TestImportArgumentValidation` MUST fail against 2cb5dc2; the pre-fix
    behaviour for the falsey cases is a SUCCESSFUL call that stores a default,
    so `pytest.raises` is itself the discriminator. `TestImportArgumentCompat`
    below passes on both sides by design.
    """

    CSV = "metric,value\nrow-a,1\n"

    def _import(self, conn, **kw):
        args = {"benchmark": "b", "csv_data": self.CSV}
        args.update(kw)
        return bench.import_csv(conn, **args)

    @pytest.mark.parametrize("falsey", [[], {}, "", 0, set()])
    @pytest.mark.parametrize("arg", ["date", "run_id"])
    def test_a_falsey_wrong_type_is_refused_not_silently_defaulted(self, conn, arg, falsey):
        """Recorded at 2cb5dc2: `date=[]` STORED '2026-08-17' and `run_id=[]`
        stored 'BE-1' — `x or default` cannot tell a falsey wrong type from
        "not supplied".

        NOTE the card itself was wrong here: it claimed `date={}` / `run_id={}`
        "reach the INSERT and raise". They do not — `{}` is falsey, so they took
        the same silent-default path. Measured.
        """
        with pytest.raises(ValueError):
            self._import(conn, **{arg: falsey})

    def test_nothing_is_written_when_an_argument_is_refused(self, conn):
        """The guard runs BEFORE any parse or INSERT, so a refusal costs no
        partial work. Uses an otherwise-valid payload, so the only reason for an
        empty table is the refusal itself."""
        with pytest.raises(ValueError):
            self._import(conn, date=[])
        assert conn.execute("SELECT COUNT(*) c FROM codebench_runs").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM codebench_results").fetchone()["c"] == 0

    @pytest.mark.parametrize(
        ("kw", "match"),
        [
            ({"benchmark": []}, "benchmark must be a string"),
            ({"benchmark": ""}, "benchmark must not be empty"),
            ({"date": ["x"]}, "date must be a string"),
            ({"tags": {1, 2}}, "tags must be a list"),
            ({"tags": [1]}, r"tags\[0\] must be a string"),
            ({"meta": []}, "meta must be a dict"),
            ({"meta": {1: "x"}}, "meta keys must be strings"),
            ({"meta": {"a": object()}}, "meta must be JSON-serializable"),
        ],
    )
    def test_each_argument_reports_which_one_and_why(self, conn, kw, match):
        """Pre-fix these raised ProgrammingError / InterfaceError / TypeError from
        sqlite3 or json — violating "domain functions raise ValueError for invalid
        input" — or, for the truthy-wrong-type cases, stored garbage."""
        with pytest.raises(ValueError, match=match):
            self._import(conn, **kw)

    def test_import_json_inherits_the_guard(self, conn):
        """Codex's correction to my first draft: an EMPTY/malformed json_data
        already raises on the pre-fix tree, so such a test cannot discriminate.
        This passes VALID json_data and an invalid SHARED argument, so the only
        thing under test is whether the delegation carries the guard."""
        with pytest.raises(ValueError, match="date must be a string"):
            bench.import_json(
                conn,
                benchmark="b",
                json_data='[{"metric": "row-a", "value": 1}]',
                date=[],
            )

    def test_the_checked_bytes_are_the_stored_bytes(self, conn):
        """CB-74's lesson applied here: the guard serializes ONCE and the INSERT
        binds that exact string, so a container that mutates between a check and
        a consume cannot store something the guard never saw.

        A list whose contents change on each iteration would, with two separate
        `json.dumps` calls, be validated in one state and stored in another.
        """

        class Shifting(list):
            def __init__(self):
                super().__init__(["first"])
                self._n = 0

            def __iter__(self):
                self._n += 1
                return iter(["first"] if self._n <= 2 else ["MUTATED"])

        self._import(conn, tags=Shifting())
        stored = conn.execute("SELECT tags FROM codebench_runs").fetchone()["tags"]
        assert "MUTATED" not in stored, f"stored a view the guard never validated: {stored}"


class TestImportArgumentCompat:
    """Passes on BOTH sides — pins behaviour the change preserves."""

    CSV = "metric,value\nrow-a,1\n"

    def test_omitting_date_and_run_id_still_defaults(self, conn):
        r = bench.import_csv(conn, benchmark="b", csv_data=self.CSV)
        assert r["run_id"] == "BE-1"
        assert len(r["date"]) == 10

    def test_empty_tags_and_meta_are_valid_supplied_values(self, conn):
        r = bench.import_csv(conn, benchmark="b", csv_data=self.CSV, tags=[], meta={})
        row = conn.execute("SELECT tags, meta FROM codebench_runs").fetchone()
        assert (row["tags"], row["meta"]) == ("[]", "{}")
        assert r["run_id"]

    def test_nan_in_meta_is_still_accepted(self, conn):
        """The NaN policy is deliberately UNCHANGED. Refusing it would be a
        narrowing this card did not ask for; `json.loads` accepts it back, so
        the round trip is intact."""
        bench.import_csv(
            conn, benchmark="b", csv_data=self.CSV, meta={"x": float("nan")}
        )
        stored = conn.execute("SELECT meta FROM codebench_runs").fetchone()["meta"]
        assert "NaN" in stored
        assert json.loads(stored)["x"] != json.loads(stored)["x"]  # NaN != NaN


class RecordingConnection(sqlite3.Connection):
    """Captures SQL TEMPLATES, not executed statements.

    `set_trace_callback` reports parameters already expanded, so a guard reading
    it cannot tell a real statement from the same text inside a value. Same
    reasoning as the RecordingConnection in tests/test_findings.py.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sql_log: list[str] = []

    def execute(self, sql, *a, **kw):
        self.sql_log.append(sql)
        return super().execute(sql, *a, **kw)

    def executemany(self, sql, *a, **kw):
        self.sql_log.append(sql)
        return super().executemany(sql, *a, **kw)


# (label, csv_data, kwargs, expected message fragment). Every one of these
# raised sqlite3.IntegrityError, csv.Error or a MID-WRITE ValueError before
# CB-81; each is now a clean pre-write ValueError.
PAYLOAD_FAULTS = [
    ("duplicate row labels", "m,v\na,1\na,2\n", {}, "Duplicate metric 'v' for row 'a'"),
    ("duplicate headers", "m,v,v\na,1,2\n", {}, "Duplicate metric 'v' for row 'a'"),
    ("nan", "m,v\na,nan\n", {}, "Non-finite value 'nan'"),
    ("inf", "m,v\na,inf\n", {}, "Non-finite value 'inf'"),
    ("-inf", "m,v\na,-inf\n", {}, "Non-finite value '-inf'"),
    ("Infinity", "m,v\na,Infinity\n", {}, "Non-finite value 'Infinity'"),
    ("overflowing literal", "m,v\na,1e400\n", {}, "Non-finite value '1e400'"),
    ("empty label after a valid row", "m,v\na,1\n,2\n", {}, "Row label"),
    ("non-numeric after a valid row", "m,v\na,1\nb,x\n", {}, "Non-numeric value 'x'"),
    ("unparseable csv", 'm,v\n"' + "x" * 200_000 + "\n", {}, "CSV could not be parsed"),
]


class TestImportPreWriteValidation:
    """CB-81 — every payload fault is decided BEFORE the first INSERT.

    Before this, validation was interleaved with the writes: the run row went in,
    then each cell was checked inside the loop that inserted it, so the SCHEMA
    was the only thing checking the payload. A plain CSV therefore killed
    `bench-import` with a raw `sqlite3.IntegrityError` traceback — a class no arm
    in cli.py handles.

    On the shipping surfaces nothing LANDED (the CLI closes the connection in a
    `finally` and the MCP wrapper uses `with conn_factory()`, so the implicit
    transaction was discarded), which is why the discriminating assertion here is
    the exception TYPE AND MESSAGE, never the row counts: `runs=0 results=0` is
    already the outcome today and would pass against the unfixed tree.
    """

    @pytest.mark.parametrize(
        "csv_data,kwargs,fragment",
        [pytest.param(c, k, f, id=label) for label, c, k, f in PAYLOAD_FAULTS],
    )
    def test_payload_fault_is_a_pre_write_value_error(self, conn, csv_data, kwargs, fragment):
        with pytest.raises(ValueError) as excinfo:
            bench.import_csv(conn, benchmark="b", csv_data=csv_data, **kwargs)
        assert fragment in str(excinfo.value), str(excinfo.value)

    @pytest.mark.parametrize(
        "csv_data,kwargs",
        [pytest.param(c, k, id=label) for label, c, k, _ in PAYLOAD_FAULTS],
    )
    def test_a_refused_payload_writes_nothing(self, conn, csv_data, kwargs):
        """Snapshot-and-compare on a CLEAN connection, never "the tables are
        empty": the duplicate-run-id case below legitimately requires a first
        successful import, and an assertion that cannot hold for one member of
        the family is an assertion nobody can extend.

        Scope: `db.txn` yields False under an AMBIENT transaction and starts
        nothing, so this guarantee is about a connection with none open.
        """
        before = self._counts(conn)
        with pytest.raises(ValueError):
            bench.import_csv(conn, benchmark="b", csv_data=csv_data, **kwargs)
        assert self._counts(conn) == before
        assert not conn.in_transaction

    def test_an_existing_run_id_is_refused_before_the_write(self, conn):
        """Library-reachable only — no CLI flag and no MCP parameter exposes
        run_id — so this pins the domain contract, not a user-facing door."""
        bench.import_csv(conn, benchmark="b", csv_data="m,v\na,1\n", run_id="BE-9")
        before = self._counts(conn)
        with pytest.raises(ValueError, match="already exists"):
            bench.import_csv(conn, benchmark="b", csv_data="m,v\nz,1\n", run_id="BE-9")
        assert self._counts(conn) == before == (1, 1)

    def test_import_json_inherits_every_guard(self, conn):
        """It converts to CSV and delegates, so the guards must reach it. A
        JSON document cannot express a duplicate key, so this uses the
        non-finite path, which it can."""
        with pytest.raises(ValueError, match="Non-finite value"):
            bench.import_json(
                conn, benchmark="b", json_data=json.dumps([{"m": "a", "v": float("inf")}])
            )
        assert self._counts(conn) == (0, 0)

    @staticmethod
    def _counts(conn):
        return (
            conn.execute("SELECT count(*) FROM codebench_runs").fetchone()[0],
            conn.execute("SELECT count(*) FROM codebench_results").fetchone()[0],
        )


class TestImportPairCheckNarrowsNothing:
    """CB-81 — the duplicate check is on (row_label, metric) PAIRS, which is
    exactly UNIQUE(run_id,row_label,metric) evaluated earlier.

    The card's wording said to refuse duplicate row labels and duplicate headers.
    Refusing either BY NAME would reject payloads that import cleanly today, and
    these two are those payloads — measured on main before the change. They are
    the reason the implementation does not follow the card's letter.

    Passes on BOTH sides by design: they pin behaviour the change preserves.
    """

    def test_a_repeated_label_with_disjoint_cells_still_imports(self, conn):
        r = bench.import_csv(conn, benchmark="b", csv_data="m,v,w\na,1,\na,,2\n")
        assert r["results_stored"] == 2
        rows = conn.execute(
            "SELECT row_label, metric, value FROM codebench_results ORDER BY metric"
        ).fetchall()
        assert [tuple(x) for x in rows] == [("a", "v", 1.0), ("a", "w", 2.0)]

    def test_a_duplicate_header_with_blank_cells_still_imports(self, conn):
        r = bench.import_csv(conn, benchmark="b", csv_data="m,v,v\na,,\n")
        assert r["results_stored"] == 0

    def test_a_short_row_is_still_skipped_not_refused(self, conn):
        """DictReader fills a missing field with restval=None, which the blank
        skip has always swallowed."""
        r = bench.import_csv(conn, benchmark="b", csv_data="m,v,w\na,1\n")
        assert r["results_stored"] == 1


class TestImportAtomicity:
    """CB-81 — the writes and the read that feeds them are ONE transaction.

    `_next_run_id` reads the highest BE-n and the INSERT writes BE-n+1: the CB-24
    read-modify-write shape, which CB-36's sweep of that population did not
    reach.
    """

    CSV = "m,v\na,1\nb,2\n"

    def test_begin_immediate_precedes_the_run_id_read(self, tmp_path):
        """The ORDER is the point, and only a template log can see it: moving
        just the INSERTs inside the transaction would pass every functional test
        in this file while leaving the race exactly where it was.

        Against main this fails on the first assertion — there is no
        BEGIN IMMEDIATE anywhere in the call.
        """
        conn = sqlite3.connect(
            str(tmp_path / "t.db"), factory=RecordingConnection
        )
        conn.row_factory = sqlite3.Row
        bench.ensure_schema(conn)
        conn.sql_log.clear()
        bench.import_csv(conn, benchmark="b", csv_data=self.CSV)

        begins = [i for i, s in enumerate(conn.sql_log) if s.startswith("BEGIN IMMEDIATE")]
        reads = [i for i, s in enumerate(conn.sql_log) if "SELECT run_id FROM codebench_runs" in s]
        assert begins, f"no BEGIN IMMEDIATE was issued: {conn.sql_log}"
        assert reads, f"the run-id read did not happen: {conn.sql_log}"
        assert begins[0] < reads[0], conn.sql_log
        conn.close()

    def test_a_failure_mid_insert_rolls_back_the_run_row(self, conn):
        """An ENVIRONMENTAL failure, not a payload one — the pre-pass cannot see
        this, which is why the transaction is the other half of the fix and not
        belt-and-braces.

        Injected with a trigger so the failure arrives from SQLite during the
        result inserts, exactly where a disk error would. Against main the run
        row is still visible on this connection afterwards (uncommitted, but the
        caller holds it); after the fix both tables are empty.
        """
        conn.execute(
            "CREATE TRIGGER boom BEFORE INSERT ON codebench_results "
            "WHEN (SELECT count(*) FROM codebench_results) >= 1 "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            bench.import_csv(conn, benchmark="b", csv_data=self.CSV)
        assert conn.execute("SELECT count(*) FROM codebench_runs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM codebench_results").fetchone()[0] == 0
        assert not conn.in_transaction

    def test_an_ambient_transaction_is_not_committed(self, tmp_path):
        """`db.txn` yields False under an ambient transaction and starts
        nothing, so the CALLER keeps the commit decision. Before this,
        `import_csv` ended in a bare `conn.commit()`, which committed the
        caller's unrelated work too (CB-24 consequence 1) — that is what the
        unrelated row below detects.
        """
        path = str(tmp_path / "t.db")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        bench.ensure_schema(conn)
        conn.execute("CREATE TABLE unrelated (x TEXT)")
        conn.commit()

        conn.execute("INSERT INTO unrelated VALUES ('caller-owned')")
        assert conn.in_transaction
        bench.import_csv(conn, benchmark="b", csv_data=self.CSV)
        assert conn.in_transaction, "import_csv committed the caller's transaction"
        conn.rollback()

        assert conn.execute("SELECT count(*) FROM unrelated").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM codebench_runs").fetchone()[0] == 0
        conn.close()

    def test_a_standalone_import_is_committed(self, tmp_path):
        """Passes on BOTH sides — pins behaviour the change preserves. Read back
        through a SECOND connection, because querying through the connection
        that wrote does not distinguish a commit from an open transaction.
        """
        path = str(tmp_path / "t.db")
        writer = sqlite3.connect(path)
        writer.row_factory = sqlite3.Row
        bench.ensure_schema(writer)
        bench.import_csv(writer, benchmark="b", csv_data=self.CSV)

        reader = sqlite3.connect(path)
        assert reader.execute("SELECT count(*) FROM codebench_results").fetchone()[0] == 2
        writer.close()
        reader.close()


class TestImportCliContract:
    """CB-81's reported symptom is specifically the CLI traceback."""

    @staticmethod
    def _cli(project, *args):
        return subprocess.run(
            [sys.executable, "-m", "codebugs.cli", *args],
            capture_output=True, text=True, cwd=str(project),
            env={**os.environ, "PYTHONPATH": os.path.join(os.getcwd(), "src")},
        )

    def test_a_duplicate_pair_is_a_clean_error_not_a_traceback(self, tmp_path):
        """Against main this prints a full `sqlite3.IntegrityError` traceback."""
        db.init_project(str(tmp_path))
        (tmp_path / "dup.csv").write_text("m,v\na,1\na,2\n")
        r = self._cli(tmp_path, "bench-import", "dup.csv", "-b", "Q")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert "Duplicate metric" in r.stderr, r.stderr

    def test_an_unparseable_csv_is_a_clean_error_not_a_traceback(self, tmp_path):
        """`_csv.Error` is NOT a ValueError, so against main it escaped both the
        handler's arm and cli.main as a raw traceback."""
        db.init_project(str(tmp_path))
        (tmp_path / "bad.csv").write_text('m,v\n"' + "x" * 200_000 + "\n")
        r = self._cli(tmp_path, "bench-import", "bad.csv", "-b", "Q")
        assert "Traceback" not in r.stderr, r.stderr
        assert r.returncode == 1, r.stdout + r.stderr
        assert "CSV could not be parsed" in r.stderr, r.stderr

    def test_a_refused_import_leaves_the_tracker_untouched(self, tmp_path):
        db.init_project(str(tmp_path))
        (tmp_path / "dup.csv").write_text("m,v\na,1\na,2\n")
        self._cli(tmp_path, "bench-import", "dup.csv", "-b", "Q")
        c = sqlite3.connect(str(tmp_path / ".codebugs" / "findings.db"))
        assert c.execute("SELECT count(*) FROM codebench_runs").fetchone()[0] == 0
        c.close()


class InjectingConnection(sqlite3.Connection):
    """Runs a competing writer on ANOTHER connection just before a chosen statement.

    Single-threaded on purpose: the hook fires inside the call under test, before
    the statement it names is handed to SQLite, so the competing write lands in
    exactly the window between the read that decides and the write that acts. No
    thread scheduling, so nothing here can pass by timing luck.

    ``outcome`` records which writer won that window, and that is the
    discriminator the tests assert on (CLAUDE.md, Testing (a)): after the fix the
    final state is identical to the state the unfixed code reaches on a quiet
    database, so *which writer is refused* is the only thing that differs. The
    competing connection carries a short ``busy_timeout`` because after the fix it
    can never acquire the lock — waiting for it unboundedly is Testing (b).

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


def _bench_conn(path, *, factory=sqlite3.Connection, busy_ms=5000):
    c = sqlite3.connect(path, factory=factory)
    c.row_factory = sqlite3.Row
    c.execute(f"PRAGMA busy_timeout={busy_ms}")
    return c


ONE_RESULT_CSV = "method,P@5\nbm25,0.72\n"


class TestDeleteIsOneTransaction:
    """CB-87 / CB-24: the reads that decide and the DELETEs that act are one unit.

    Both functions read first and write after, with no transaction spanning the
    pair, so a competing writer fits in the window. ``busy_timeout`` cannot help:
    it serializes the writes and never touches the read that preceded them.
    """

    def test_a_result_inserted_in_the_window_cannot_falsify_results_removed(self, tmp_path):
        """``results_removed`` must be what the DELETE removed, not what a COUNT predicted.

        Unfixed, the COUNT runs before the DELETE outside any transaction: a result
        row inserted between them is deleted and never counted, so the call reports
        having removed fewer rows than it destroyed — a success-shaped report about
        a write the caller cannot audit.
        """
        path = str(tmp_path / "bench.db")
        main = _bench_conn(path, factory=InjectingConnection)
        bench.ensure_schema(main)
        bench.import_csv(main, benchmark="a", csv_data=ONE_RESULT_CSV, run_id="BE-1")
        assert main.execute("SELECT COUNT(*) AS c FROM codebench_results").fetchone()["c"] == 1

        other = _bench_conn(path, busy_ms=150)

        def competing_result():
            other.execute(
                "INSERT INTO codebench_results (run_id, row_label, metric, value) "
                "VALUES ('BE-1', 'dense', 'P@5', 0.9)"
            )
            other.commit()

        main.injection = competing_result
        main.inject_before = "DELETE FROM codebench_results WHERE run_id"
        try:
            result = bench.delete_run(main, "BE-1")
        finally:
            other.close()

        assert main.outcome is not None, "the injection point was never reached"
        removed_for_real = 2 if main.outcome == "landed" else 1
        assert result["results_removed"] == removed_for_real
        assert main.outcome.startswith("refused"), main.outcome
        main.close()

    def test_a_run_inserted_in_the_window_does_not_leave_orphaned_results(self, tmp_path):
        """Data corruption, not a stale report: results go by a SNAPSHOT of run_ids
        while the runs themselves go unconditionally by ``benchmark``.

        A run imported into that window is absent from the snapshot, so its results
        survive the first DELETE — and then the second DELETE removes its
        ``codebench_runs`` row anyway, leaving results referencing a run that no
        longer exists.
        """
        path = str(tmp_path / "bench.db")
        main = _bench_conn(path, factory=InjectingConnection)
        bench.ensure_schema(main)
        bench.import_csv(main, benchmark="a", csv_data=ONE_RESULT_CSV, run_id="BE-1")

        other = _bench_conn(path, busy_ms=150)

        def competing_run():
            other.execute(
                "INSERT INTO codebench_runs (run_id, benchmark, date, created_at) "
                "VALUES ('BE-2', 'a', '2026-08-21', '2026-08-21T00:00:00Z')"
            )
            other.execute(
                "INSERT INTO codebench_results (run_id, row_label, metric, value) "
                "VALUES ('BE-2', 'dense', 'P@5', 0.9)"
            )
            other.commit()

        main.injection = competing_run
        main.inject_before = "DELETE FROM codebench_results WHERE run_id IN"
        try:
            bench.delete_benchmark(main, "a")
        finally:
            other.close()

        assert main.outcome is not None, "the injection point was never reached"
        orphans = main.execute(
            "SELECT COUNT(*) AS c FROM codebench_results "
            "WHERE run_id NOT IN (SELECT run_id FROM codebench_runs)"
        ).fetchone()["c"]
        assert orphans == 0, "results left behind by a run deleted out from under them"
        assert main.outcome.startswith("refused"), main.outcome
        main.close()
