"""CB-52 gate: a dedup bump must escalate a finding's severity.

Run:  uv run --extra dev python tests/manual/repro_cb52_severity_freeze.py
Exit: 0 when the contract holds, 1 when it does not.

Files a `low` observation, re-observes the SAME defect (same fingerprint) at
`critical`, and asserts BOTH halves of the fix: the stored column moved, and the
row is reachable by `query(severity="critical")` — the tracker's primary read path,
which is the user-visible half. A print-only version of this script reported
success while either half was still broken, so the checks are assertions and the
exit code carries the verdict.

Recorded run against the unfixed tree (commit 64d6f63):

    STORED severity        : 'low'
    ring severities        : ['critical']
    query(severity=critical) hits: []

Milestone routing is deliberately NOT checked here: it is out of this card's scope
and belongs to CB-35. The earlier draft's projection read also lacked an
`item_kind` filter, which is CB-26's trap 1.
"""

import tempfile

from codebugs import db, findings

OBS = {
    "category": "security:authz",
    "file": "src/app/routes.py",
    "description": "Admin route skips the token check when the header is absent",
}


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as root:
        db.init_project(root)
        conn = db.connect(project_dir=root)

        first = findings.add_finding(conn, severity="low", source="repro", **OBS)
        fid = first["id"]
        print(f"1st observation: {fid} severity={first['severity']!r}")

        second = findings.add_finding(conn, severity="critical", source="repro", **OBS)
        print(
            f"2nd observation: {second['id']} severity={second['severity']!r} "
            f"(was_new={second.get('was_new')}, action={second.get('dedup_action')})"
        )

        if second["id"] != fid:
            failures.append("PRECONDITION: the second observation did not dedup onto the first")

        row = findings.get_finding(conn, fid)
        ring = row["meta"].get("occurrences", [])
        hits = [f["id"] for f in findings.query_findings(conn, severity="critical")["findings"]]

        print()
        print(f"STORED severity        : {row['severity']!r}")
        print(f"occurrence_count       : {row['occurrence_count']}")
        print(f"ring severities        : {[e.get('severity') for e in ring]}")
        print(f"query(severity=critical) hits: {hits}")

        if row["severity"] != "critical":
            failures.append(f"stored severity is {row['severity']!r}, expected 'critical'")
        if hits != [fid]:
            failures.append(f"query(severity='critical') returned {hits}, expected [{fid!r}]")
        if row["occurrence_count"] != 2:
            failures.append(f"occurrence_count is {row['occurrence_count']}, expected 2")
        conn.close()

    print()
    if failures:
        print("CB-52 REPRODUCED — the contract does NOT hold:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("CB-52 contract holds: the bump escalated severity and the row is queryable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
