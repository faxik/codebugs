"""Tests for the codebench benchmark results module."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

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
