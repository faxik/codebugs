"""Codebugs CLI — thin wrapper over the database layer."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Any

from codebugs import db, reqs


def _format_table(rows: list[dict], columns: list[str], max_widths: dict | None = None) -> str:
    if not rows:
        return "(no results)"
    max_widths = max_widths or {}
    col_widths = {}
    for col in columns:
        header_w = len(col)
        data_w = max((len(str(row.get(col, ""))) for row in rows), default=0)
        w = max(header_w, data_w)
        if col in max_widths:
            w = min(w, max_widths[col])
        col_widths[col] = w

    fmt = "  ".join(f"{{:<{col_widths[c]}}}" for c in columns)
    lines = [fmt.format(*columns)]
    lines.append(fmt.format(*("-" * col_widths[c] for c in columns)))
    for row in rows:
        vals = []
        for c in columns:
            v = str(row.get(c, ""))
            w = col_widths[c]
            if len(v) > w:
                v = v[: w - 1] + "…"
            vals.append(v)
        lines.append(fmt.format(*vals))
    return "\n".join(lines)


def cmd_add(args: argparse.Namespace) -> None:
    conn = db.connect()
    meta = {}
    if args.lines:
        meta["lines"] = args.lines
    if args.meta:
        meta.update(json.loads(args.meta))

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

    result = db.add_finding(
        conn,
        severity=args.severity,
        category=args.category,
        file=args.file,
        description=args.description,
        source=args.source or "human",
        tags=tags,
        meta=meta or None,
    )
    conn.close()
    print(f"Added: {result['id']}")


def cmd_update(args: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        result = db.update_finding(
            conn,
            args.id,
            status=args.status,
            notes=args.notes,
        )
        print(f"Updated: {result['id']} (status={result['status']})")
    except KeyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_query(args: argparse.Namespace) -> None:
    conn = db.connect()
    result = db.query_findings(
        conn,
        status=args.status,
        severity=args.severity,
        category=args.category,
        file=args.file,
        source=args.source,
        group_by=args.group_by,
        limit=args.limit or 100,
    )
    conn.close()

    if result.get("grouped"):
        data = [{"group": r["group_key"], "count": str(r["count"])} for r in result["groups"]]
        print(_format_table(data, ["group", "count"]))
    else:
        findings = result["findings"]
        if not findings:
            print("(no findings match)")
            return
        data = [
            {
                "id": f["id"],
                "sev": f["severity"],
                "category": f["category"],
                "file": f["file"],
                "status": f["status"],
                "description": f["description"],
            }
            for f in findings
        ]
        print(
            _format_table(
                data,
                ["id", "sev", "category", "file", "status", "description"],
                max_widths={"description": 60, "file": 40, "category": 25},
            )
        )
        print(f"\n{result['total']} finding(s) total.")


def cmd_stats(args: argparse.Namespace) -> None:
    conn = db.connect()
    result = db.get_stats(conn, group_by=args.by or "severity")
    conn.close()

    groups = result["groups"]
    if not groups:
        print("(no findings)")
        return

    header = f"{'':30s} {'critical':>8s} {'high':>8s} {'medium':>8s} {'low':>8s} {'total':>8s}"
    print(header)
    print("-" * len(header))
    totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
    for grp in sorted(groups):
        d = groups[grp]
        print(
            f"{grp:30s} {d['critical']:>8d} {d['high']:>8d} {d['medium']:>8d} {d['low']:>8d} {d['total']:>8d}"
        )
        for k in totals:
            totals[k] += d[k]
    print("-" * len(header))
    print(
        f"{'TOTAL':30s} {totals['critical']:>8d} {totals['high']:>8d} {totals['medium']:>8d} {totals['low']:>8d} {totals['total']:>8d}"
    )


def cmd_summary(args: argparse.Namespace) -> None:
    conn = db.connect()
    s = db.get_summary(conn)
    conn.close()

    print("Codebugs Summary")
    print("=" * 50)
    print(f"Findings:  {s['open']} open / {s['resolved']} resolved / {s['total']} total")
    print()
    print("Open by severity:")
    for sev in ("critical", "high", "medium", "low"):
        c = s["open_by_severity"].get(sev, 0)
        bar = "#" * min(c, 40)
        print(f"  {sev:10s}  {c:>4d}  {bar}")
    if s["top_categories"]:
        print()
        print("Top categories:")
        for cat in s["top_categories"]:
            print(f"  {cat['category']:30s}  {cat['count']:>4d}")
    if s["hottest_files"]:
        print()
        print("Hottest files:")
        for f in s["hottest_files"]:
            print(f"  {f['file']:50s}  {f['critical_high']} crit/high, {f['open']} open")


def cmd_categories(args: argparse.Namespace) -> None:
    conn = db.connect()
    cats = db.get_categories(conn)
    conn.close()

    if not cats:
        print("(no categories yet)")
        return
    data = [
        {
            "category": c["category"],
            "total": str(c["total"]),
            "open": str(c["open_count"]),
            "fixed": str(c["fixed_count"]),
        }
        for c in cats
    ]
    print(_format_table(data, ["category", "total", "open", "fixed"]))


def cmd_import_csv(args: argparse.Namespace) -> None:
    conn = db.connect()
    imported = 0
    with open(args.file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            severity = (row.get("severity") or row.get("Severity") or "medium").strip().lower()
            category = (row.get("category") or row.get("Category") or "").strip()
            filepath = (row.get("file") or row.get("File") or "").strip()
            description = (row.get("description") or row.get("Description") or "").strip()
            source = (row.get("source") or row.get("Source") or "import").strip()

            if not filepath or not description or not category:
                continue

            meta = {}
            lines = (row.get("lines") or row.get("Lines") or "").strip()
            if lines:
                meta["lines"] = lines

            db.add_finding(
                conn,
                severity=severity,
                category=category,
                file=filepath,
                description=description,
                source=source,
                meta=meta or None,
            )
            imported += 1

    conn.close()
    print(f"Imported {imported} findings.")


def cmd_export_csv(args: argparse.Namespace) -> None:
    conn = db.connect()
    result = db.query_findings(conn, limit=100000)
    conn.close()

    output = args.file or "codebugs_export.csv"
    with open(output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "severity", "category", "file", "status", "description", "source", "tags", "meta", "created_at", "updated_at"])
        for finding in result["findings"]:
            writer.writerow([
                finding["id"],
                finding["severity"],
                finding["category"],
                finding["file"],
                finding["status"],
                finding["description"],
                finding["source"],
                json.dumps(finding["tags"]),
                json.dumps(finding["meta"]),
                finding["created_at"],
                finding["updated_at"],
            ])
    print(f"Exported {len(result['findings'])} findings to {output}")


# --- Requirements CLI commands ---


def cmd_reqs_add(args: argparse.Namespace) -> None:
    conn = db.connect()
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    result = reqs.add_requirement(
        conn, req_id=args.id, description=args.description,
        section=args.section or "", priority=args.priority or "Should",
        status=args.status or "Planned", source=args.source or "",
        test_coverage=args.test_coverage or "", tags=tags,
    )
    conn.close()
    print(f"Added: {result['id']}")


def cmd_reqs_update(args: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        result = reqs.update_requirement(
            conn, args.id, status=args.status,
            description=args.description, priority=args.priority,
            test_coverage=args.test_coverage, notes=args.notes,
        )
        print(f"Updated: {result['id']} (status={result['status']})")
    except KeyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_reqs_query(args: argparse.Namespace) -> None:
    conn = db.connect()
    result = reqs.query_requirements(
        conn, status=args.status, priority=args.priority,
        section=args.section, search=args.search,
        group_by=args.group_by, limit=args.limit or 100,
    )
    conn.close()

    if result.get("grouped"):
        data = [{"group": r["group_key"], "count": str(r["count"])} for r in result["groups"]]
        print(_format_table(data, ["group", "count"]))
    else:
        items = result["requirements"]
        if not items:
            print("(no requirements match)")
            return
        data = [
            {
                "id": r["id"], "priority": r["priority"],
                "status": r["status"], "section": r["section"],
                "description": r["description"],
            }
            for r in items
        ]
        print(_format_table(
            data, ["id", "priority", "status", "section", "description"],
            max_widths={"description": 60, "section": 30},
        ))
        print(f"\n{result['total']} requirement(s) total.")


def cmd_reqs_stats(args: argparse.Namespace) -> None:
    conn = db.connect()
    result = reqs.get_reqs_stats(conn, group_by=args.by or "status")
    conn.close()

    groups = result["groups"]
    if not groups:
        print("(no requirements)")
        return

    header = f"{'':30s} {'Must':>8s} {'Should':>8s} {'Could':>8s} {'total':>8s}"
    print(header)
    print("-" * len(header))
    totals = {"Must": 0, "Should": 0, "Could": 0, "total": 0}
    for grp in sorted(groups):
        d = groups[grp]
        print(f"{grp:30s} {d['Must']:>8d} {d['Should']:>8d} {d['Could']:>8d} {d['total']:>8d}")
        for k in totals:
            totals[k] += d[k]
    print("-" * len(header))
    print(f"{'TOTAL':30s} {totals['Must']:>8d} {totals['Should']:>8d} {totals['Could']:>8d} {totals['total']:>8d}")


def cmd_reqs_summary(args: argparse.Namespace) -> None:
    conn = db.connect()
    s = reqs.get_reqs_summary(conn)
    conn.close()

    print("Requirements Summary")
    print("=" * 50)
    print(f"Total: {s['total']}")
    print()
    print("By status:")
    for status in reqs.VALID_STATUSES:
        c = s["by_status"].get(status, 0)
        bar = "#" * min(c, 40)
        print(f"  {status:12s}  {c:>4d}  {bar}")
    print()
    print("By priority:")
    for p in reqs.VALID_PRIORITIES:
        print(f"  {p:12s}  {s['by_priority'].get(p, 0):>4d}")
    if s["implemented_without_tests"]:
        print(f"\nImplemented without tests: {s['implemented_without_tests']}")
    if s["sections"]:
        print(f"\nSection progress:")
        for sec in s["sections"]:
            pct = (sec["done"] / sec["total"] * 100) if sec["total"] else 0
            print(f"  {sec['section']:40s}  {sec['done']}/{sec['total']} ({pct:.0f}%)")


def cmd_reqs_verify(args: argparse.Namespace) -> None:
    conn = db.connect()
    checks = args.checks.split(",") if args.checks else None
    result = reqs.verify_requirements(conn, project_dir=args.project_dir, checks=checks)
    conn.close()

    print(f"Verified {result['total_requirements']} requirements.")
    if not result["issues"]:
        print("No issues found.")
        return

    print(f"\n{result['issues_found']} issue(s) found:\n")
    data = [
        {"check": i["check"], "sev": i["severity"], "id": i["id"], "message": i["message"]}
        for i in result["issues"]
    ]
    print(_format_table(data, ["check", "sev", "id", "message"], max_widths={"message": 70}))


def cmd_reqs_import(args: argparse.Namespace) -> None:
    conn = db.connect()
    result = reqs.import_markdown(conn, args.file)
    conn.close()
    print(f"Imported {result['imported']} requirements, skipped {result['skipped']}.")


def cmd_reqs_export(args: argparse.Namespace) -> None:
    conn = db.connect()
    md = reqs.export_markdown(conn)
    conn.close()

    if args.file:
        with open(args.file, "w") as f:
            f.write(md)
        print(f"Exported to {args.file}")
    else:
        print(md)


def cmd_merge_sessions(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    sessions = merge.get_sessions(conn, status=args.status)
    conn.close()
    if not sessions:
        print("(no sessions)")
        return
    data = [
        {
            "session_id": s["session_id"],
            "branch": s["branch"],
            "status": s["status"],
            "claims": str(s["claim_count"]),
            "description": s["description"],
        }
        for s in sessions
    ]
    print(_format_table(
        data, ["session_id", "branch", "status", "claims", "description"],
        max_widths={"description": 40, "branch": 30},
    ))


def cmd_merge_status(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    s = merge.get_status(conn)
    conn.close()
    print("Codemerge Status")
    print("=" * 40)
    print(f"Active sessions:    {s['active_sessions']}")
    print(f"Merging sessions:   {s['merging_sessions']}")
    print(f"Done sessions:      {s['done_sessions']}")
    print(f"Abandoned sessions: {s['abandoned_sessions']}")
    print(f"Total claims:       {s['total_claims']}")
    print(f"Lock holder:        {s['lock_holder'] or '(none)'}")


def cmd_merge_abandon(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    try:
        result = merge.abandon_session(conn, args.session_id)
        print(f"Abandoned: {result['session_id']}")
    except KeyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_merge_claims(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import merge
    claims = merge.get_claims(conn, args.session_id)
    conn.close()
    if not claims:
        print("(no claims)")
        return
    data = [{"file": c["file_path"], "claimed_at": c["claimed_at"]} for c in claims]
    print(_format_table(data, ["file", "claimed_at"]))


def _register_merge_subcommands(sub, commands):
    """Register merge CLI subcommands."""
    p = sub.add_parser("merge-sessions", help="List merge sessions")
    p.add_argument("--status", help="Filter: active|merging|done|abandoned")

    sub.add_parser("merge-status", help="Merge coordination dashboard")

    p = sub.add_parser("merge-abandon", help="Abandon a stale session")
    p.add_argument("session_id", help="Session ID to abandon")

    p = sub.add_parser("merge-claims", help="List claimed files for a session")
    p.add_argument("session_id", help="Session ID")

    commands.update({
        "merge-sessions": cmd_merge_sessions,
        "merge-status": cmd_merge_status,
        "merge-abandon": cmd_merge_abandon,
        "merge-claims": cmd_merge_claims,
    })


# --- Sweep CLI commands ---


def cmd_sweep_create(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    try:
        result = sweep.create_sweep(
            conn, name=args.name, description=args.description or "",
            default_batch_size=args.batch_size or 10,
        )
        print(f"Created: {result['sweep_id']}" + (f" ({result['name']})" if result["name"] else ""))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_sweep_add(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    try:
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
        result = sweep.add_items(conn, args.sweep, args.items, tags=tags)
        print(f"Added {result['added']} items, {result['duplicates_skipped']} duplicates skipped.")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_sweep_next(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    try:
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
        result = sweep.next_batch(conn, args.sweep, limit=args.limit, tags=tags)
        if not result["items"]:
            print("(no unprocessed items)")
            return
        data = [{"item": i["item"], "tags": ",".join(i["tags"])} for i in result["items"]]
        print(_format_table(data, ["item", "tags"], max_widths={"item": 60}))
        print(f"\n{result['remaining']} remaining.")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_sweep_mark(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    try:
        result = sweep.mark_items(conn, args.sweep, args.items, processed=not args.undo)
        action = "Unmarked" if args.undo else "Marked"
        print(f"{action} {result['updated']} items.")
    except (ValueError, KeyError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_sweep_status(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    try:
        s = sweep.get_status(conn, args.sweep)
        print(f"Sweep: {s['sweep_id']}" + (f" ({s['name']})" if s["name"] else ""))
        print(f"Status: {s['status']}")
        print(f"Items:  {s['processed']}/{s['total']} processed, {s['remaining']} remaining")
        if s["by_tag"]:
            print("\nBy tag:")
            for tag, counts in sorted(s["by_tag"].items()):
                print(f"  {tag:20s}  {counts['processed']}/{counts['total']}")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_sweep_archive(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    try:
        result = sweep.archive_sweep(conn, args.sweep)
        print(f"Archived: {result['sweep_id']}")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_sweep_list(args: argparse.Namespace) -> None:
    conn = db.connect()
    from codebugs import sweep
    try:
        result = sweep.list_sweeps(conn, include_archived=args.all)
        if not result["sweeps"]:
            print("(no sweeps)")
            return
        data = [
            {
                "sweep_id": s["sweep_id"],
                "name": s["name"] or "",
                "status": s["status"],
                "progress": f"{s['processed']}/{s['total']}",
                "remaining": str(s["remaining"]),
            }
            for s in result["sweeps"]
        ]
        print(_format_table(data, ["sweep_id", "name", "status", "progress", "remaining"]))
    finally:
        conn.close()


def _register_sweep_subcommands(sub, commands):
    """Register sweep CLI subcommands."""
    p = sub.add_parser("sweep-create", help="Create a new sweep")
    p.add_argument("--name", help="Optional sweep name")
    p.add_argument("--description", help="Sweep description")
    p.add_argument("--batch-size", type=int, help="Default batch size (default: 10)")

    p = sub.add_parser("sweep-add", help="Add items to a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to add")
    p.add_argument("--tags", help="Comma-separated tags")

    p = sub.add_parser("sweep-next", help="Get next batch of unprocessed items")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("--limit", type=int, help="Batch size override")
    p.add_argument("--tags", help="Filter by tags (comma-separated)")

    p = sub.add_parser("sweep-mark", help="Mark items as processed")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")
    p.add_argument("items", nargs="+", help="Items to mark")
    p.add_argument("--undo", action="store_true", help="Unmark items instead")

    p = sub.add_parser("sweep-status", help="Sweep progress overview")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-archive", help="Archive a sweep")
    p.add_argument("sweep", help="Sweep ID (SW-N) or name")

    p = sub.add_parser("sweep-list", help="List sweeps")
    p.add_argument("--all", action="store_true", help="Include archived sweeps")

    commands.update({
        "sweep-create": cmd_sweep_create,
        "sweep-add": cmd_sweep_add,
        "sweep-next": cmd_sweep_next,
        "sweep-mark": cmd_sweep_mark,
        "sweep-status": cmd_sweep_status,
        "sweep-archive": cmd_sweep_archive,
        "sweep-list": cmd_sweep_list,
    })


def _register_findings_subcommands(sub, commands):
    """Register findings CLI subcommands."""
    p = sub.add_parser("add", help="Add a finding")
    p.add_argument("-s", "--severity", required=True, help="critical|high|medium|low")
    p.add_argument("-c", "--category", required=True, help="Finding category")
    p.add_argument("-f", "--file", required=True, help="File path")
    p.add_argument("-d", "--description", required=True, help="Description")
    p.add_argument("-l", "--lines", help="Line range (stored in meta)")
    p.add_argument("--source", help="Source (default: human)")
    p.add_argument("--tags", help="Comma-separated tags")
    p.add_argument("--meta", help="JSON metadata string")

    p = sub.add_parser("update", help="Update a finding")
    p.add_argument("id", help="Finding ID")
    p.add_argument("--status", help="New status")
    p.add_argument("--notes", help="Notes")

    p = sub.add_parser("query", help="Search findings")
    p.add_argument("--status", help="Filter by status")
    p.add_argument("--severity", "-s", help="Filter by severity")
    p.add_argument("--category", "-c", help="Filter by category")
    p.add_argument("--file", "-f", help="Filter by file (substring)")
    p.add_argument("--source", help="Filter by source")
    p.add_argument("--group-by", help="Group by: file|category|severity|status|source")
    p.add_argument("--limit", type=int, help="Max results")

    p = sub.add_parser("stats", help="Cross-tabulated summary")
    p.add_argument("--by", help="Group by: severity|category|status|file|source")

    sub.add_parser("summary", help="Dashboard overview")
    sub.add_parser("categories", help="List all categories with counts")

    p = sub.add_parser("import-csv", help="Import findings from CSV")
    p.add_argument("file", help="CSV file path")

    p = sub.add_parser("export-csv", help="Export findings to CSV")
    p.add_argument("file", nargs="?", help="Output file (default: codebugs_export.csv)")

    commands.update({
        "add": cmd_add,
        "update": cmd_update,
        "query": cmd_query,
        "stats": cmd_stats,
        "summary": cmd_summary,
        "categories": cmd_categories,
        "import-csv": cmd_import_csv,
        "export-csv": cmd_export_csv,
    })


def _register_reqs_subcommands(sub, commands):
    """Register requirements CLI subcommands."""
    p = sub.add_parser("reqs-add", help="Add a requirement")
    p.add_argument("id", help="Requirement ID (e.g. FR-001)")
    p.add_argument("-d", "--description", required=True, help="Description")
    p.add_argument("--section", help="Section name")
    p.add_argument("--priority", help="Must|Should|Could")
    p.add_argument("--status", help="Planned|Partial|Implemented|Verified|Superseded|Obsolete")
    p.add_argument("--source", help="Source reference")
    p.add_argument("--test-coverage", help="Test file name(s)")
    p.add_argument("--tags", help="Comma-separated tags")

    p = sub.add_parser("reqs-update", help="Update a requirement")
    p.add_argument("id", help="Requirement ID")
    p.add_argument("--status", help="New status")
    p.add_argument("--description", help="Updated description")
    p.add_argument("--priority", help="Updated priority")
    p.add_argument("--test-coverage", help="Updated test coverage")
    p.add_argument("--notes", help="Notes")

    p = sub.add_parser("reqs-query", help="Search requirements")
    p.add_argument("--status", help="Filter by status")
    p.add_argument("--priority", help="Filter by priority")
    p.add_argument("--section", help="Filter by section (substring)")
    p.add_argument("--search", help="Search in description/ID")
    p.add_argument("--group-by", help="Group by: section|status|priority|source")
    p.add_argument("--limit", type=int, help="Max results")

    p = sub.add_parser("reqs-stats", help="Requirements cross-tab")
    p.add_argument("--by", help="Group by: status|priority|section|source")

    sub.add_parser("reqs-summary", help="Requirements dashboard")

    p = sub.add_parser("reqs-verify", help="Verify requirements for issues")
    p.add_argument("--checks", help="Comma-separated: tests,ids,status (default: all)")
    p.add_argument("--project-dir", help="Project root for test file checks")

    p = sub.add_parser("reqs-import", help="Import from REQUIREMENTS.md")
    p.add_argument("file", help="Markdown file path")

    p = sub.add_parser("reqs-export", help="Export as markdown")
    p.add_argument("file", nargs="?", help="Output file (default: stdout)")

    commands.update({
        "reqs-add": cmd_reqs_add,
        "reqs-update": cmd_reqs_update,
        "reqs-query": cmd_reqs_query,
        "reqs-stats": cmd_reqs_stats,
        "reqs-summary": cmd_reqs_summary,
        "reqs-verify": cmd_reqs_verify,
        "reqs-import": cmd_reqs_import,
        "reqs-export": cmd_reqs_export,
    })


def main() -> None:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "all"],
        default="all",
    )
    pre_args, _ = pre_parser.parse_known_args()

    parser = argparse.ArgumentParser(
        description="codebugs — AI-native code finding & requirements tracker",
        prog="codebugs",
        parents=[pre_parser],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    commands = {}
    if pre_args.mode in ("findings", "all"):
        _register_findings_subcommands(sub, commands)
    if pre_args.mode in ("reqs", "all"):
        _register_reqs_subcommands(sub, commands)
    if pre_args.mode in ("merge", "all"):
        _register_merge_subcommands(sub, commands)
    if pre_args.mode in ("sweep", "all"):
        _register_sweep_subcommands(sub, commands)

    args = parser.parse_args()
    commands[args.command](args)


if __name__ == "__main__":
    main()
