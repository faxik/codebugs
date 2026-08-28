"""CB-243 probe: reproduce the trade-off band `_record`'s docstring now names.

`_record` (`src/codebugs/server.py`) sets `PRAGMA busy_timeout=50` on its own
recording connection before the usage-tracking INSERT (CB-192). Before that
change the connection inherited the tracker's shared 5000ms budget. This
script measures, on a REAL sqlite file and a REAL foreign write-lock holder,
what each budget does at three points of foreign-lock hold time: 200ms,
700ms (the point CB-195/CB-192's own measurements were built on) and 2000ms.

It does not import `_record` itself — that function always uses 50ms, by
design, so there is nothing left to parameterize on the current code. It
reproduces the mechanism `_record` relies on (`PRAGMA busy_timeout=N`) against
the same schema (`usage.USAGE_SCHEMA`) and reports, for a recording attempt
started while a foreign writer holds `BEGIN IMMEDIATE`, whether the write
LANDED (the busy timeout waited out the foreign hold) or was LOST (sqlite
gave up and raised `OperationalError: database is locked`).

Run:  ./.venv/bin/python tests/manual/probe_cb243_busy_timeout_band.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from codebugs import usage  # noqa: E402

HOLD_POINTS_MS = (200, 700, 2000)
BUDGETS_MS = {"prior (5000ms)": 5000, "current (50ms)": 50}


def _make_db(path: str) -> None:
    conn = sqlite3.connect(path)
    for stmt in usage.USAGE_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    conn.close()


def _foreign_holder(path: str, hold_ms: int, ready: threading.Event) -> None:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO tool_calls (tool_name, called_at, success, error_type, "
                 "duration_ms) VALUES ('foreign', 'x', 1, NULL, 0)")
    ready.set()
    time.sleep(hold_ms / 1000.0)
    conn.commit()
    conn.close()


def _attempt_record(path: str, budget_ms: int) -> tuple[bool, float]:
    """Mirror `_record`'s own sequence: open, set the budget, write, commit."""
    start = time.perf_counter()
    try:
        conn = sqlite3.connect(path)
        conn.execute(f"PRAGMA busy_timeout={budget_ms}")
        usage.record_call(
            conn, tool_name="probe", success=True, error_type=None, duration_ms=0.0
        )
        conn.close()
        return True, (time.perf_counter() - start) * 1000
    except sqlite3.OperationalError:
        return False, (time.perf_counter() - start) * 1000


def run_one(hold_ms: int, budget_ms: int) -> tuple[bool, float]:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "probe.db")
        _make_db(path)
        ready = threading.Event()
        t = threading.Thread(target=_foreign_holder, args=(path, hold_ms, ready))
        t.start()
        ready.wait(timeout=5)
        landed, elapsed_ms = _attempt_record(path, budget_ms)
        t.join()
        return landed, elapsed_ms


def main() -> None:
    print(f"{'hold (ms)':>10}  " + "  ".join(f"{b:>18}" for b in BUDGETS_MS))
    for hold_ms in HOLD_POINTS_MS:
        cells = []
        for budget_ms in BUDGETS_MS.values():
            landed, elapsed_ms = run_one(hold_ms, budget_ms)
            cells.append(f"{'landed' if landed else 'LOST':>10} {elapsed_ms:6.0f}ms")
        print(f"{hold_ms:>10}  " + "  ".join(f"{c:>18}" for c in cells))


if __name__ == "__main__":
    main()
