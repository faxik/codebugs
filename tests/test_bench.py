"""Tests for the codebench benchmark results module."""

from __future__ import annotations

import json
import sqlite3

import pytest

from codebugs import bench


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
        result = bench.import_csv(
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
