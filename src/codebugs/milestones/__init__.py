"""Milestones & streams — release containers + standing buckets for codebugs.

Spec: ~/w/autosorter/.claude/plans/codebugs-milestones-streams-v1.md
Plan: docs/superpowers/plans/2026-05-11-milestones-streams.md

This package is one domain split across files for navigability + testability.
It has a single schema owner (_schema) and a shared spine (_spine); the four
context modules (foundation, triage, capacity, closegate) hold the domain
logic. This __init__ is the facade: it re-exports the public + test-facing
surface and owns MCP/CLI registration so the wire names stay frozen.
"""

from __future__ import annotations

from typing import Any

from codebugs.milestones._schema import (  # noqa: F401
    AUTO_ROUTER_ACTOR,
    ITEM_KINDS,
    ITEM_SIZES,
    ITEM_STATUSES,
    MILESTONE_ITEM_TERMINAL,
    MILESTONE_KINDS,
    MILESTONE_STATES,
    MILESTONES_SCHEMA,
    SEED_MILESTONES,
    ensure_schema,
)
from codebugs.milestones._spine import (  # noqa: F401
    _audit,
    _get_item_by_ref,
    _get_milestone,
    _items_with_active_blockers,
    _milestone_exists,
    _row_to_audit,
    _row_to_item,
    _row_to_milestone,
    _validate_item_ref,
)
from codebugs.milestones.foundation import (  # noqa: F401
    add_milestone_item,
    create_milestone,
    get_milestone_status,
    list_milestones,
    move_milestone_item,
    query_audit,
    set_item_status,
    update_milestone,
)
from codebugs.milestones.triage import (  # noqa: F401
    _auto_route_finding,
    triage_dismiss,
    triage_inbox,
    triage_promote,
)
from codebugs.milestones.capacity import (  # noqa: F401
    _eligibility_failure,
    get_wip_status,
    pull_next,
    release_item,
)
from codebugs.milestones.reconcile import (  # noqa: F401
    _reconcile_on_reopen,
    _reconcile_on_terminal,
    reconcile_all,
    source_is_terminal,
)
from codebugs.milestones.closegate import (  # noqa: F401
    mark_branch_only,
    mark_integrated,
    milestone_close,
    milestone_defer,
)


def register_tools(mcp, conn_factory) -> None:
    """Register milestones MCP tools."""

    @mcp.tool()
    def milestone_create(
        id: str,
        kind: str,
        description: str,
        target_date: str | None = None,
    ) -> dict[str, Any]:
        """Create a new milestone.

        Args:
            id: Slug identifier, e.g. 'release/1.2' or 'stream/security'.
            kind: 'release' or 'stream'. Streams never close.
            description: Short charter for the milestone.
            target_date: ISO date (e.g. '2026-06-30'). Optional, releases only.
        """
        with conn_factory() as conn:
            return create_milestone(
                conn, id=id, kind=kind, description=description,
                target_date=target_date,
            )

    @mcp.tool()
    def milestone_update(
        id: str,
        description: str | None = None,
        target_date: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Update mutable fields of a milestone. id and kind are immutable.

        Args:
            id: Milestone slug.
            description: New description (or None to skip).
            target_date: New ISO target date (or None to skip).
            state: New state (open / closing / shipped / archived).
        """
        with conn_factory() as conn:
            return update_milestone(
                conn, id=id, description=description,
                target_date=target_date, state=state,
            )

    @mcp.tool()
    def milestone_list(
        kind: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """List milestones with optional filters.

        Args:
            kind: 'release' or 'stream'.
            state: 'open' / 'closing' / 'shipped' / 'archived'.
        """
        with conn_factory() as conn:
            return list_milestones(conn, kind=kind, state=state)

    @mcp.tool()
    def milestone_status(id: str) -> dict[str, Any]:
        """Detailed rollup for one milestone: item counts by status / size,
        blockers, branch-only items, days to target.

        Args:
            id: Milestone slug.
        """
        with conn_factory() as conn:
            return get_milestone_status(conn, id=id)

    @mcp.tool()
    def milestone_add_item(
        milestone_id: str,
        item_kind: str,
        item_ref: str,
        size: str = "small",
        priority: int = 100,
        acceptance: str = "",
        linked_frs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Attach an item (bug / requirement / external) to a milestone.

        Args:
            milestone_id: Target milestone slug.
            item_kind: 'bug' (CB-N), 'requirement' (FR-N), or 'external'.
            item_ref: The id of the underlying entity (must exist for bug/req).
            size: 'large' (worktree+sprint), 'small' (1-2h), 'triage' (minutes).
            priority: Lower = higher priority. Default 100.
            acceptance: Markdown acceptance criteria. Required for size='large'.
            linked_frs: Optional list of FR ids to link (used by pull_next eligibility).
        """
        meta: dict[str, Any] = {}
        if linked_frs:
            meta["linked_frs"] = linked_frs
        with conn_factory() as conn:
            return add_milestone_item(
                conn,
                milestone_id=milestone_id,
                item_kind=item_kind,
                item_ref=item_ref,
                size=size,
                priority=priority,
                acceptance=acceptance,
                meta=meta or None,
            )

    @mcp.tool()
    def milestone_move_item(
        item_ref: str,
        to_milestone: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Move an item to a different milestone.

        Args:
            item_ref: The item to move (e.g. CB-5).
            to_milestone: Destination milestone slug.
            reason: One-line audit reason.
        """
        with conn_factory() as conn:
            return move_milestone_item(
                conn, item_ref=item_ref, to_milestone=to_milestone, reason=reason,
            )

    @mcp.tool()
    def milestone_set_status(
        item_ref: str,
        status: str,
        commit: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Set an item's status. Records done_commit if status is terminal.

        Args:
            item_ref: The item id (e.g. CB-5).
            status: open / in_progress / done / deferred / dismissed.
            commit: SHA where the work landed on main (recorded for terminal status).
            reason: Optional audit reason.
        """
        with conn_factory() as conn:
            return set_item_status(
                conn, item_ref=item_ref, status=status,
                commit=commit, reason=reason,
            )

    @mcp.tool()
    def milestone_audit_query(
        milestone_id: str | None = None,
        item_ref: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Audit log query with filters. Returns most-recent rows first.

        Args:
            milestone_id: Filter by milestone slug.
            item_ref: Filter by item id.
            actor: Filter by actor.
            since: ISO datetime — only rows at or after this time.
            limit: Max rows (default 200).
        """
        with conn_factory() as conn:
            return query_audit(
                conn, milestone_id=milestone_id, item_ref=item_ref,
                actor=actor, since=since, limit=limit,
            )

    # --- Phase 2: triage + pull_next + WIP ---
    # Tools are bound with explicit names to expose spec-mandated identifiers
    # (`triage_inbox`, `pull_next`, etc.) without shadowing module-level
    # functions of the same name.

    @mcp.tool(name="triage_inbox")
    def _triage_inbox(limit: int = 50) -> list[dict[str, Any]]:
        """List open items in stream/triage, oldest first.

        Args:
            limit: Max rows (default 50).
        """
        with conn_factory() as conn:
            return triage_inbox(conn, limit=limit)

    @mcp.tool(name="triage_dismiss")
    def _triage_dismiss(bug_id: str, reason: str) -> dict[str, Any]:
        """Mark a triage item as dismissed. Propagates to the underlying entity:
        bug → finding 'not_a_bug'; requirement → requirement 'obsolete';
        external → no propagation.

        Args:
            bug_id: The item id (e.g. CB-5).
            reason: Required one-line audit reason.
        """
        with conn_factory() as conn:
            return triage_dismiss(conn, bug_id=bug_id, reason=reason)

    @mcp.tool(name="triage_promote")
    def _triage_promote(
        bug_id: str,
        to_milestone: str,
        size: str = "small",
        acceptance: str = "",
        priority: int = 100,
        linked_frs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Move a triage item to a target milestone.

        Args:
            bug_id: The item id (e.g. CB-5).
            to_milestone: Destination milestone slug.
            size: 'large' / 'small' / 'triage'. Default 'small'.
            acceptance: Required for size='large'.
            priority: Lower = higher priority. Default 100.
            linked_frs: FR ids linked to this item (required for size='large' bugs
                in release milestones to be pull-eligible).
        """
        with conn_factory() as conn:
            return triage_promote(
                conn, bug_id=bug_id, to_milestone=to_milestone,
                size=size, acceptance=acceptance, priority=priority,
                linked_frs=linked_frs,
            )

    @mcp.tool(name="pull_next")
    def _pull_next(
        agent_id: str,
        capacity: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        """Claim the next eligible item for the calling agent. Returns the
        item dict or None if nothing eligible.

        Priority: stream/security > release/* (earliest target_date) >
                  stream/triage > stream/maintenance.

        Args:
            agent_id: Stable id for the calling agent. Used as actor in audit.
            capacity: Dict like {'large':1,'small':2,'triage':5}. Defaults
                to those values if not provided.
        """
        cap = capacity or {"large": 1, "small": 2, "triage": 5}
        with conn_factory() as conn:
            return pull_next(conn, agent_id=agent_id, capacity=cap)

    @mcp.tool(name="release_item")
    def _release_item(
        item_ref: str,
        status: str = "done",
        commit: str | None = None,
    ) -> dict[str, Any]:
        """Free agent capacity for an item.

        Args:
            item_ref: The item id (e.g. CB-5).
            status: 'done' (terminal) or 'abandoned' (returns item to 'open').
            commit: SHA where the work landed (recorded if status='done').
        """
        with conn_factory() as conn:
            return release_item(
                conn, item_ref=item_ref, status=status, commit=commit,
            )

    @mcp.tool(name="wip_status")
    def _wip_status(agent_id: str | None = None) -> list[dict[str, Any]]:
        """Snapshot of agent_capacity. agent_id=None returns all agents.

        Args:
            agent_id: Filter to one agent (None = all).
        """
        with conn_factory() as conn:
            return get_wip_status(conn, agent_id=agent_id)

    # --- Phase 3: branch tracking + close gate + defer ---

    @mcp.tool(name="mark_branch_only")
    def _mark_branch_only(item_ref: str, branch_name: str) -> dict[str, Any]:
        """Flag an item as living on a feature branch (not yet integrated).
        Called by worktree-setup.sh when a branch is created.

        Args:
            item_ref: The item id (e.g. CB-5).
            branch_name: Git branch holding the work.
        """
        with conn_factory() as conn:
            return mark_branch_only(
                conn, item_ref=item_ref, branch_name=branch_name,
            )

    @mcp.tool(name="mark_integrated")
    def _mark_integrated(item_ref: str, commit: str) -> dict[str, Any]:
        """Mark an item as merged to main. Sets done_commit, status='done',
        clears branch_only. Called by worktree-finish.sh.

        Args:
            item_ref: The item id (e.g. CB-5).
            commit: Commit SHA where the work landed on main.
        """
        with conn_factory() as conn:
            return mark_integrated(conn, item_ref=item_ref, commit=commit)

    @mcp.tool(name="milestone_close")
    def _milestone_close(
        id: str,
        force: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        """Close a release milestone. Refuses if items are unfinished, on a
        branch, or have unresolved blockers. Streams cannot be closed.

        Args:
            id: Milestone slug (must be kind='release').
            force: Override the close-gate (still won't close streams). Audit-logged.
            reason: Audit reason for the close.
        """
        with conn_factory() as conn:
            return milestone_close(conn, id=id, force=force, reason=reason)

    @mcp.tool(name="milestone_defer")
    def _milestone_defer(
        item_ref: str,
        to_milestone: str = "stream/maintenance",
        reason: str = "",
    ) -> dict[str, Any]:
        """Move an item to stream/maintenance (or another milestone) and
        mark it deferred.

        Args:
            item_ref: The item to defer.
            to_milestone: Destination (default 'stream/maintenance').
            reason: Optional audit reason.
        """
        with conn_factory() as conn:
            return milestone_defer(
                conn, item_ref=item_ref, to_milestone=to_milestone,
                reason=reason,
            )


def register_cli(sub, commands) -> None:
    """Register milestones CLI subcommands (flat domain-action pattern)."""
    import argparse
    import sys

    from codebugs.fmt import format_table

    def _cmd_milestone_list(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = list_milestones(conn, kind=args.kind, state=args.state)
        finally:
            conn.close()
        if not rows:
            print("(no milestones)")
            return
        data = [
            {
                "id": r["id"],
                "kind": r["kind"],
                "state": r["state"],
                "target": r.get("target_date") or "-",
                "description": r["description"][:60],
            }
            for r in rows
        ]
        print(format_table(data, ["id", "kind", "state", "target", "description"]))

    def _cmd_milestone_status(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            status = get_milestone_status(conn, id=args.id)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        m = status["milestone"]
        print(f"{m['id']}  ({m['kind']}, state={m['state']})")
        if m.get("target_date"):
            countdown = status.get("days_to_target")
            cs = f" ({countdown} days)" if countdown is not None else ""
            print(f"  target: {m['target_date']}{cs}")
        if m["description"]:
            print(f"  {m['description']}")
        print()
        print(f"Items: {status['total_items']} total "
              f"({status['open_items']} open/in_progress, "
              f"{status['done_items']} done)")
        print()
        print("  By status:")
        for k, v in status["by_status"].items():
            if v:
                print(f"    {k:14s} {v:>4d}")
        print("  By size:")
        for k, v in status["by_size"].items():
            if v:
                print(f"    {k:14s} {v:>4d}")
        if status["branch_only_items"]:
            print()
            print(f"  Branch-only: {', '.join(status['branch_only_items'])}")
        if status["blocked_items"]:
            print(f"  Blocked: {', '.join(status['blocked_items'])}")

    def _cmd_milestone_audit(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = query_audit(
                conn,
                milestone_id=args.milestone or None,
                item_ref=args.item or None,
                actor=args.actor or None,
                limit=args.limit or 200,
            )
        finally:
            conn.close()
        if not rows:
            print("(no audit entries)")
            return
        data = [
            {
                "at": r["at"],
                "actor": r["actor"],
                "action": r["action"],
                "milestone": r["milestone_id"],
                "item": r.get("item_ref") or "-",
                "from": r.get("from_state") or "-",
                "to": r.get("to_state") or "-",
                "reason": (r.get("reason") or "")[:30],
            }
            for r in rows
        ]
        print(format_table(
            data,
            ["at", "actor", "action", "milestone", "item", "from", "to", "reason"],
        ))

    p = sub.add_parser("milestone-list", help="List milestones")
    p.add_argument("--kind", help="Filter by kind (release|stream)")
    p.add_argument("--state", help="Filter by state")

    p = sub.add_parser("milestone-status", help="Show milestone rollup")
    p.add_argument("id", help="Milestone slug (e.g. release/1.1)")

    p = sub.add_parser("milestone-audit", help="Show audit log")
    p.add_argument("--milestone", help="Filter by milestone slug")
    p.add_argument("--item", help="Filter by item ref")
    p.add_argument("--actor", help="Filter by actor")
    p.add_argument("--limit", type=int, help="Row limit (default 200)")

    def _cmd_triage_inbox(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = triage_inbox(conn, limit=args.limit or 50)
        finally:
            conn.close()
        if not rows:
            print("(triage inbox empty)")
            return
        data = [
            {
                "ref": r["item_ref"],
                "kind": r["item_kind"],
                "size": r["size"],
                "priority": str(r["priority"]),
                "created": r["created_at"],
            }
            for r in rows
        ]
        print(format_table(data, ["ref", "kind", "size", "priority", "created"]))

    def _cmd_wip_status(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            rows = get_wip_status(conn, agent_id=args.agent)
        finally:
            conn.close()
        if not rows:
            print("(no agent capacity records)")
            return
        data = [
            {
                "agent": r["agent_id"],
                "large": str(r["large_held"]),
                "small": str(r["small_held"]),
                "triage": str(r["triage_held"]),
                "last_pull": r.get("last_pull_at") or "-",
            }
            for r in rows
        ]
        print(format_table(data, ["agent", "large", "small", "triage", "last_pull"]))

    p = sub.add_parser("triage-inbox", help="List items in stream/triage")
    p.add_argument("--limit", type=int, help="Row limit (default 50)")

    p = sub.add_parser("wip-status", help="Show agent capacity snapshot")
    p.add_argument("--agent", help="Filter by agent id (default: all)")

    def _cmd_milestone_mark_branch(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            item = mark_branch_only(
                conn, item_ref=args.item_ref, branch_name=args.branch,
            )
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        print(f"branch-only: {item['item_ref']} @ {args.branch}")

    def _cmd_milestone_mark_integrated(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            item = mark_integrated(
                conn, item_ref=args.item_ref, commit=args.commit,
            )
        except KeyError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        finally:
            conn.close()
        print(f"integrated: {item['item_ref']} @ {item['done_commit']}")

    p = sub.add_parser(
        "milestone-mark-branch",
        help="Flag an item as living on a feature branch (not yet integrated)",
    )
    p.add_argument("item_ref", help="Item id (e.g. CB-1234)")
    p.add_argument("branch", help="Git branch name holding the work")

    p = sub.add_parser(
        "milestone-mark-integrated",
        help="Mark an item as merged to main; clears branch-only, records done_commit",
    )
    p.add_argument("item_ref", help="Item id (e.g. CB-1234)")
    p.add_argument("commit", help="Commit SHA where the work landed on main")

    def _cmd_milestone_reconcile(args: argparse.Namespace) -> None:
        from codebugs.db import connect
        conn = connect()
        try:
            result = reconcile_all(conn, apply=args.apply)
        finally:
            conn.close()
        if not result["candidates"]:
            print("(nothing to reconcile)")
            return
        print(format_table(
            [
                {
                    "item": r["item_ref"],
                    "milestone": r["milestone_id"],
                    "transition": f"{r['from_status']} -> {r['to_status']}",
                    "source": r["source_status"],
                }
                for r in result["items"]
            ],
            ["item", "milestone", "transition", "source"],
        ))
        verb = "reconciled" if result["applied"] else "would reconcile (dry run)"
        print(f"\n{verb}: {result['candidates']} item(s)")
        if not result["applied"]:
            print("re-run with --apply to write")

    p = sub.add_parser(
        "milestone-reconcile",
        help="Close stream items whose source finding/requirement is already terminal",
    )
    # Dry run by DEFAULT. A bulk repair that mutates unless told otherwise is how a
    # repair tool becomes an accident.
    p.add_argument(
        "--apply", action="store_true",
        help="Actually write the changes (default: dry run)",
    )

    commands.update({
        "milestone-list": _cmd_milestone_list,
        "milestone-status": _cmd_milestone_status,
        "milestone-audit": _cmd_milestone_audit,
        "triage-inbox": _cmd_triage_inbox,
        "wip-status": _cmd_wip_status,
        "milestone-mark-branch": _cmd_milestone_mark_branch,
        "milestone-mark-integrated": _cmd_milestone_mark_integrated,
        "milestone-reconcile": _cmd_milestone_reconcile,
    })


# --- Module-level registrations --------------------------------------------

from codebugs.db import (  # noqa: E402
    register_cli_provider,
    register_post_add_hook,
    register_schema,
    register_status_change_hook,
    register_tool_provider,
)

register_schema("milestones", ensure_schema, depends_on=("findings", "reqs", "blockers"))
register_tool_provider("milestones", register_tools)
register_cli_provider("milestones", register_cli)
register_post_add_hook("milestones.auto_route", _auto_route_finding)
# The update-side twin of the router above. Without it, routing happened once at
# add time and a resolved finding stayed live in its stream forever (CB-26).
register_status_change_hook("milestones.reconcile_terminal", _reconcile_on_terminal)
# The inverse: a terminal source reopening (a dedup-detected regression, CB-43)
# must reopen its stream items, or the reopened card is invisible to every queue.
register_status_change_hook("milestones.reconcile_reopen", _reconcile_on_reopen)
