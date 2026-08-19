"""CB-76 reproducer: a failed export must not destroy the export it replaces.

Committed rather than left in a scratchpad because a plan's central evidence
has to be re-runnable by a reviewer (the CB-76 round-1 review could not).

    python tests/manual/repro_cb76_truncation.py

Against the UNFIXED tree it prints `previous export LOST : True` and exits 1;
against the fixed tree the previous export survives byte-for-byte and it exits
0. The write failure is SIMULATED at the csv writer — `ENOSPC` is what a full
disk or a quota raises at exactly that point, and the query has already
completed by then, so nothing else in the process is perturbed.

NEGATIVE RESULT, recorded so it is not re-derived: injecting the failure with
RLIMIT_FSIZE instead does NOT work. The limit hits sqlite's WAL first and the
run dies with `sqlite3.OperationalError: disk I/O error` before the export is
ever reached. Inject at the write, not at the process.
"""

from __future__ import annotations

import errno
import os
import sys
import tempfile

# $CB76_SRC points this at ANOTHER checkout's src/, which is how the
# non-vacuity check is run: against the pre-fix tree it must report
# `previous export LOST : True` and exit 1. Without an override it uses its own
# checkout. Note `pythonpath = ["src"]` in pyproject prepends the local src for
# pytest, which is why this script does its own path insert rather than relying
# on an editable install (that would resolve `codebugs` to the main checkout).
_REPO_SRC = os.environ.get("CB76_SRC") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"
)
sys.path.insert(0, _REPO_SRC)

PREVIOUS = "PREVIOUS GOOD EXPORT - 3 findings\n"


class _FullDisk:
    """csv.writer stand-in that raises ENOSPC on the first row."""

    def __init__(self, *_a, **_k) -> None:
        pass

    def writerow(self, _row) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")


def main() -> int:
    from codebugs import cli, db, findings

    with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as dest:
        db.init_project(project)
        conn = db.connect(project)
        findings.add_finding(
            conn, severity="high", category="bug", file="a.py", description="something broke", new_category=True
        )
        conn.close()

        target = os.path.join(dest, "export.csv")
        with open(target, "w") as f:
            f.write(PREVIOUS)

        findings.csv.writer = _FullDisk
        sys.argv = ["codebugs", "--tracker-root", project, "export-csv", target]
        try:
            cli.main()
            outcome = "returned normally"
        except SystemExit as e:
            outcome = f"SystemExit({e.code})"
        except OSError as e:
            outcome = f"{type(e).__name__}: {e}  <-- escaped as a raw traceback"

        with open(target) as f:
            after = f.read()
        lost = after != PREVIOUS

        print(f"outcome              : {outcome}")
        print(f"previous file bytes  : {len(PREVIOUS)}")
        print(f"file bytes afterwards: {len(after)}")
        print(f"previous export LOST : {lost}")
        leftovers = [n for n in os.listdir(dest) if n.startswith(".codebugs-export-")]
        print(f"temp files left      : {leftovers}")
        return 1 if (lost or leftovers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
