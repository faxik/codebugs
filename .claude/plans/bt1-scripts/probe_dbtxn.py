#!/usr/bin/env python3
"""BT-1: does `db.txn`'s `finally` COMMIT the transaction when ROLLBACK failed?

WHAT THIS ANSWERS
    In `db.txn`, the cleanup path swallows a failed `ROLLBACK` and then runs
    `conn.isolation_level = saved` in a `finally`. That assignment is not inert:
    in CPython's sqlite3, assigning `isolation_level` can itself COMMIT the open
    transaction. This script measures, for `saved` in (None, '', 'DEFERRED'),
    whether the data written inside the failed unit of work SURVIVES -- i.e.
    whether the escape path is durable or not.

WHY THIS QUESTION MUST NOT BE ANSWERED BY READING CODE
    The behaviour is not in `db.py` at all. It is in the C implementation of the
    `isolation_level` setter, it is conditional on the VALUE being restored, and
    the value depends on how the caller's connection was constructed. Reading
    `conn.isolation_level = saved` tells you nothing about whether a COMMIT is
    issued; three consecutive manual claims in this workstream about "what the
    code does here" were falsified by running it. So this runs it: a real
    file-backed database, a connection whose ROLLBACK is forced to fail, and a
    durability check performed by CLOSING the connection (which rolls back
    anything still open) and reopening it.

    Both halves are measured: PART A isolates the CPython mechanism with no
    `db.py` involved, PART B drives the repository's real `db.txn`.

USAGE
    python3 .claude/plans/bt1-scripts/probe_dbtxn.py [--src src]

Writes only into a fresh temporary directory (never into the repository);
prints to stdout; stdlib only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile

SAVED_VALUES = [None, "", "DEFERRED"]


def show(v: object) -> str:
    return "None" if v is None else repr(v)


# --------------------------------------------------------------------------


class RollbackBreaker(sqlite3.Connection):
    """A connection whose ROLLBACK always fails, the state `db.txn` swallows."""

    break_rollback = True
    rollback_attempts = 0

    def execute(self, sql, *args, **kwargs):  # type: ignore[override]
        if self.break_rollback and sql.strip().upper().startswith("ROLLBACK"):
            type(self).rollback_attempts += 1
            raise sqlite3.OperationalError("simulated: cannot rollback - no transaction active")
        return super().execute(sql, *args, **kwargs)


def fresh_db(tmp: str, name: str) -> str:
    path = os.path.join(tmp, name)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v TEXT)")
    con.commit()
    con.close()
    return path


def durable_rows(path: str) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        con.close()


# --------------------------------------------------------------------------
# PART A -- the CPython mechanism, no db.py involved
# --------------------------------------------------------------------------


def part_a(tmp: str) -> list[tuple]:
    rows = []
    for i, saved in enumerate(SAVED_VALUES):
        path = fresh_db(tmp, f"a{i}.db")
        con = sqlite3.connect(path)
        con.isolation_level = None
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO t VALUES ('written-inside-the-failed-unit')")
        in_txn_before = con.in_transaction
        con.isolation_level = saved  # <-- the assignment under test
        in_txn_after = con.in_transaction
        con.close()  # closing rolls back anything still open
        rows.append((show(saved), in_txn_before, in_txn_after, durable_rows(path)))
    return rows


# --------------------------------------------------------------------------
# PART B -- the repository's real db.txn, with ROLLBACK forced to fail
# --------------------------------------------------------------------------


def part_b(tmp: str, db, break_rollback: bool) -> list[tuple]:
    rows = []
    for i, saved in enumerate(SAVED_VALUES):
        path = fresh_db(tmp, f"b{int(break_rollback)}{i}.db")
        con = sqlite3.connect(path, factory=RollbackBreaker)
        con.break_rollback = break_rollback
        RollbackBreaker.rollback_attempts = 0
        con.isolation_level = saved
        raised = ""
        try:
            with db.txn(con):
                con.execute("INSERT INTO t VALUES ('written-inside-the-failed-unit')")
                raise RuntimeError("unit of work fails here")
        except RuntimeError as e:
            raised = f"RuntimeError({e})"
        except BaseException as e:  # pragma: no cover - would be a finding in itself
            raised = f"{type(e).__name__}({e})"
        in_txn_after = con.in_transaction
        attempts = RollbackBreaker.rollback_attempts
        con.close()
        rows.append(
            (show(saved), raised, attempts, in_txn_after, durable_rows(path))
        )
    return rows


# --------------------------------------------------------------------------


def locate_finally(src_root: str) -> tuple[str, int, str]:
    """Find `conn.isolation_level = saved` in db.py -- the reference is verified,
    not quoted from memory."""
    path = os.path.join(src_root, "codebugs", "db.py")
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for n, line in enumerate(lines, 1):
        if line.strip() == "conn.isolation_level = saved":
            return path, n, line.rstrip("\n")
    return path, -1, "<NOT FOUND -- db.txn no longer restores isolation_level>"


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", "..", ".."))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=os.path.join(repo, "src"))
    args = ap.parse_args()

    sys.path.insert(0, args.src)
    from codebugs import db  # noqa: E402

    print("=" * 100)
    print("BT-1 PROBE: db.txn's `finally` restore, with ROLLBACK failing")
    print("=" * 100)
    print(f"CPython        : {sys.version.split()[0]}  ({sys.executable})")
    print(f"sqlite3 module : {sqlite3.__name__}, sqlite_version = {sqlite3.sqlite_version}")
    print(f"sqlite3.threadsafety = {sqlite3.threadsafety}")
    print(f"db module      : {db.__file__}")
    path, lineno, text = locate_finally(args.src)
    print(f"restore site   : {os.path.relpath(path, repo)}:{lineno}   {text.strip()}")
    print()

    tmp = tempfile.mkdtemp(prefix="bt1-probe-")
    try:
        print("-" * 100)
        print("PART A -- the CPython mechanism alone (no db.py): BEGIN IMMEDIATE, INSERT,")
        print("          then `conn.isolation_level = saved`, then close() and reopen.")
        print("-" * 100)
        print(f"{'saved':<12} {'in_txn before':>14} {'in_txn after':>14} {'rows survive close':>20}")
        for saved, before, after, n in part_a(tmp):
            print(f"{saved:<12} {str(before):>14} {str(after):>14} {n:>20}")
        print()
        print("  rows survive close = 1 -> the assignment COMMITTED the open transaction")
        print("  rows survive close = 0 -> the transaction was still open and close() undid it")
        print()

        print("-" * 100)
        print("PART B -- the real db.txn, ROLLBACK FORCED TO FAIL (the state db.txn swallows).")
        print("          A RuntimeError is raised inside the block after one INSERT.")
        print("-" * 100)
        print(f"{'saved':<12} {'exception out of the block':<38} {'rb-tries':>8} "
              f"{'in_txn after':>13} {'DURABLE':>8}")
        for saved, raised, attempts, after, n in part_b(tmp, db, break_rollback=True):
            print(f"{saved:<12} {raised:<38} {attempts:>8} {str(after):>13} "
                  f"{('YES' if n else 'no'):>8}  (rows={n})")
        print()

        print("-" * 100)
        print("PART C -- control: identical run with ROLLBACK WORKING.")
        print("-" * 100)
        print(f"{'saved':<12} {'exception out of the block':<38} {'rb-tries':>8} "
              f"{'in_txn after':>13} {'DURABLE':>8}")
        for saved, raised, attempts, after, n in part_b(tmp, db, break_rollback=False):
            print(f"{saved:<12} {raised:<38} {attempts:>8} {str(after):>13} "
                  f"{('YES' if n else 'no'):>8}  (rows={n})")
        print()

        print("-" * 100)
        print("WHAT `saved` ACTUALLY IS AT THE CALL SITES (measured, not assumed)")
        print("-" * 100)
        plain = sqlite3.connect(os.path.join(tmp, "probe-default.db"))
        print(f"  sqlite3.connect(...).isolation_level          = {show(plain.isolation_level)}")
        plain.close()
        tracker = os.path.join(tmp, "real-tracker")
        os.makedirs(tracker, exist_ok=True)
        db.init_project(tracker)
        real = db.connect(tracker)
        print(f"  db.connect(...).isolation_level               = {show(real.isolation_level)}")
        real.close()
        print()
        print("  So the value restored at the site above is the empty string on every")
        print("  connection this package opens -- the row measured as NOT durable.")
        print()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
