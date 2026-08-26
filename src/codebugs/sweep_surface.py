"""The sweep module's exposed surface, declared once for both sides.

Every entry names ONE capability and carries the whole of what a client or a
shell sees of it: the MCP tool name, its complete description, and every
parameter with its annotation and default; the CLI verb name, its one-line help,
and every argparse keyword. `codebugs.surfacegen` turns this data into the
registered tool and the built parser — nothing here registers anything, and
nothing in `sweep.py` describes anything.

`manual_handler=` names a handwritten BODY, never an installer: the body decides
what the capability DOES, this file decides what it LOOKS LIKE. Every one of the
nine CLI verbs needs one, because each renders its own result to a terminal;
none of the nine MCP tools does, because each only forwards its declared
parameters into a domain function, so all nine are generated from `calls=`.

GRAMMAR. This file is held to a restricted grammar and checked by
`module_surface.py --lint-declarations`: no f-strings, no implicit
concatenation, no escapes in non-raw literals, and every literal reached through
a container, a keyword argument or an assignment. The grammar is what makes the
BT-6 prose column mean anything, and it is a constraint on THIS FILE ONLY — it
is an instrument of the pilot, not a proposal for the repository.

Its measured cost, worth naming because the pilot exists to price things: the
grammar admits data only, so `str | None` (an operator) and `list[str]` (a
subscript) cannot be written here at all. The optional and parameterised
annotations therefore arrive as the named vocabulary imported below.

DECLARED, because the repository rule says modules must not import each other's
private functions: the handler names below ARE private and this file imports
them. It is not a second module reaching into `sweep` — it is `sweep`'s own
declaration half, split out only because the measurement needed the two halves
countable apart, and `sweep.py` imports it straight back at registration time.
"""

from codebugs.sweep import (
    _cmd_sweep_add,
    _cmd_sweep_archive,
    _cmd_sweep_archive_items,
    _cmd_sweep_create,
    _cmd_sweep_list,
    _cmd_sweep_list_items,
    _cmd_sweep_mark,
    _cmd_sweep_next,
    _cmd_sweep_status,
    add_items,
    archive_items,
    archive_sweep,
    create_sweep,
    get_status,
    list_items,
    list_sweeps,
    mark_items,
    next_batch,
)
from codebugs.surfacegen import (
    OPT_BOOL,
    OPT_INT,
    OPT_TEXT,
    OPT_TEXT_LIST,
    OPT_TEXT_LIST_MAP,
    TEXT_LIST,
)

CODESWEEP_CREATE_DOC = """Create a new sweep for batch iteration over items.

Args:
    name: Optional human-readable name (must be unique)
    description: What this sweep is for
    default_batch_size: Default items per batch (default: 10)
    lifecycle: Ordered list of allowed states (default ["pending","done"]).
        For retro-style workflows: ["DETECTED","CONFIRMED","ESCALATED",
        "POSTPONED","RESOLVED","DROPPED"].
    terminal_states: States that count as "processed" (default ["done"]).
    transitions: Optional dict[state, list[allowed_next_state]] for
        DAG-constrained lifecycles. None = unconstrained transitions.
"""

CODESWEEP_ADD_DOC = """Add items to a sweep. Atomic upsert: existing items have their
`recurrence_count` bumped instead of being silently skipped, their
`last_seen` updated, and their archive flag cleared (R5: re-detected
archived items un-archive automatically).

Args:
    sweep_ref: Sweep ID (SW-N) or name
    items: Item identifiers to add
    tags: Optional tags applied to this batch (overwrite on bump)

Returns:
    {sweep_id, added, recurrence_bumped, duplicates_skipped (alias)}
"""

CODESWEEP_NEXT_DOC = """Get next batch of unprocessed (non-terminal, non-archived) items in
insertion order.

Args:
    sweep_ref: Sweep ID (SW-N) or name
    limit: Batch size (overrides sweep default). 0 means NO items; omit it to
        use the sweep's own default batch size. A negative value is an error
        (it used to mean "no limit").
    tags: Filter to items matching any of these tags
"""

CODESWEEP_MARK_DOC = """Mark items by state transition.

Args:
    sweep_ref: Sweep ID (SW-N) or name
    items: Item identifiers to mark
    processed: Legacy mode — True maps to first terminal state, False
        to first non-terminal state. Omit it entirely (the default) to get
        the same effect as True. MUTUALLY EXCLUSIVE with `state`: sending
        both is an error, including when the two happen to agree, because
        `state` names one state and `processed` names a class of them.
    state: Explicit target state. Validated against the sweep's
        `lifecycle` and `transitions` DAG (if declared). Mutually exclusive
        with `processed` — send one or the other, never both.
"""

CODESWEEP_STATUS_DOC = """Sweep overview — total/processed/remaining/archived counts, per-tag and
per-state breakdowns. Archived entries are excluded from total/processed/
remaining and reported separately as `archived`.

Args:
    sweep_ref: Sweep ID (SW-N) or name
"""

CODESWEEP_ARCHIVE_DOC = """Archive a sweep. Archived sweeps are excluded from codesweep_list by default.

For entry-level archive, use `codesweep_archive_items`.

Args:
    sweep_ref: Sweep ID (SW-N) or name
"""

CODESWEEP_ARCHIVE_ITEMS_DOC = """Selectively archive entries within a sweep (soft-delete).

Archived entries are excluded from `codesweep_next`, `codesweep_status`
totals, and default `codesweep_list_items`. They remain matchable by
`codesweep_add` for recurrence detection — re-adding un-archives them
with `recurrence_count` carried forward (R5 invariant).

At least one filter is required.

Args:
    sweep_ref: Sweep ID (SW-N) or name
    items: Specific item identifiers to archive. An EXPLICITLY EMPTY list
        selects nothing; passing it together with where_status or older_than
        is an ERROR, because those filters would then be silently ignored.
        Omit items entirely to archive by filter.
    where_status: Archive entries currently in this state
    older_than: Duration spec — '30d', '2w', '6m', '1y'. Compares against
        the entry's last activity timestamp.
    reason: Free-form reason recorded on each archived entry
"""

CODESWEEP_LIST_ITEMS_DOC = """List items in a sweep with optional filters.

Args:
    sweep_ref: Sweep ID (SW-N) or name
    state: Filter to a specific state
    tag: Filter to items having this tag
    include_archived: Include archived entries alongside live ones
    archived_only: Show only archived entries (overrides include_archived)
    limit: Max number of entries to return. 0 means NO entries; omit it for
        no limit. A negative value is an error (it used to mean "no limit").
"""

CODESWEEP_LIST_DOC = """List all sweeps with summary counts.

Args:
    include_archived: Include archived sweeps (default: false)
"""
SURFACE = [
    dict(
        mcp=dict(
            name="codesweep_create",
            doc=CODESWEEP_CREATE_DOC,
            params=[
                dict(name="name", type=OPT_TEXT, default=None),
                dict(name="description", type=str, default=""),
                dict(name="default_batch_size", type=int, default=10),
                dict(name="lifecycle", type=OPT_TEXT_LIST, default=None),
                dict(name="terminal_states", type=OPT_TEXT_LIST, default=None),
                dict(name="transitions", type=OPT_TEXT_LIST_MAP, default=None),
            ],
            calls=create_sweep,
        ),
        cli=dict(
            name="sweep-create",
            help="Create a new sweep",
            args=[
                dict(flags=["--name"], help="Optional sweep name"),
                dict(flags=["--description"], help="Sweep description"),
                dict(flags=["--batch-size"], type=int, help="Default batch size (default: 10)"),
                dict(
                    flags=["--lifecycle"],
                    help="Comma-separated lifecycle states (default: pending,done)",
                ),
                dict(
                    flags=["--terminal-states"],
                    help="Comma-separated terminal states (default: done)",
                ),
            ],
            manual_handler=_cmd_sweep_create,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_add",
            doc=CODESWEEP_ADD_DOC,
            params=[
                dict(name="sweep_ref", type=str),
                dict(name="items", type=TEXT_LIST),
                dict(name="tags", type=OPT_TEXT_LIST, default=None),
            ],
            calls=add_items,
        ),
        cli=dict(
            name="sweep-add",
            help="Add items to a sweep",
            args=[
                dict(flags=["sweep"], help="Sweep ID (SW-N) or name"),
                dict(flags=["items"], nargs="+", help="Items to add"),
                dict(flags=["--tags"], help="Comma-separated tags"),
            ],
            manual_handler=_cmd_sweep_add,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_next",
            doc=CODESWEEP_NEXT_DOC,
            params=[
                dict(name="sweep_ref", type=str),
                dict(name="limit", type=OPT_INT, default=None),
                dict(name="tags", type=OPT_TEXT_LIST, default=None),
            ],
            calls=next_batch,
        ),
        cli=dict(
            name="sweep-next",
            help="Get next batch of unprocessed items",
            args=[
                dict(flags=["sweep"], help="Sweep ID (SW-N) or name"),
                dict(
                    flags=["--limit"],
                    type=int,
                    help="Batch size override (0 for none; negative is an error)",
                ),
                dict(flags=["--tags"], help="Filter by tags (comma-separated)"),
            ],
            manual_handler=_cmd_sweep_next,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_mark",
            doc=CODESWEEP_MARK_DOC,
            params=[
                dict(name="sweep_ref", type=str),
                dict(name="items", type=TEXT_LIST),
                dict(name="processed", type=OPT_BOOL, default=None),
                dict(name="state", type=OPT_TEXT, default=None),
            ],
            calls=mark_items,
        ),
        cli=dict(
            name="sweep-mark",
            help="Mark items as processed or transition state",
            args=[
                dict(flags=["sweep"], help="Sweep ID (SW-N) or name"),
                dict(flags=["items"], nargs="+", help="Items to mark"),
                dict(
                    flags=["--undo"],
                    action="store_true",
                    help="Map to first non-terminal state (not with --state)",
                ),
                dict(
                    flags=["--state"],
                    help="Explicit target state (validated against lifecycle; not with --undo)",
                ),
            ],
            manual_handler=_cmd_sweep_mark,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_status",
            doc=CODESWEEP_STATUS_DOC,
            params=[
                dict(name="sweep_ref", type=str),
            ],
            calls=get_status,
        ),
        cli=dict(
            name="sweep-status",
            help="Sweep progress overview",
            args=[
                dict(flags=["sweep"], help="Sweep ID (SW-N) or name"),
            ],
            manual_handler=_cmd_sweep_status,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_archive",
            doc=CODESWEEP_ARCHIVE_DOC,
            params=[
                dict(name="sweep_ref", type=str),
            ],
            calls=archive_sweep,
        ),
        cli=dict(
            name="sweep-archive",
            help="Archive an entire sweep",
            args=[
                dict(flags=["sweep"], help="Sweep ID (SW-N) or name"),
            ],
            manual_handler=_cmd_sweep_archive,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_archive_items",
            doc=CODESWEEP_ARCHIVE_ITEMS_DOC,
            params=[
                dict(name="sweep_ref", type=str),
                dict(name="items", type=OPT_TEXT_LIST, default=None),
                dict(name="where_status", type=OPT_TEXT, default=None),
                dict(name="older_than", type=OPT_TEXT, default=None),
                dict(name="reason", type=OPT_TEXT, default=None),
            ],
            calls=archive_items,
        ),
        cli=dict(
            name="sweep-archive-items",
            help="Selectively archive entries (soft-delete)",
            args=[
                dict(flags=["sweep"], help="Sweep ID (SW-N) or name"),
                dict(
                    flags=["items"],
                    nargs="*",
                    help="Specific items to archive (optional)",
                ),
                dict(flags=["--state"], help="Archive entries in this state"),
                dict(
                    flags=["--older-than"],
                    help="Archive entries older than (e.g. 30d, 6m)",
                ),
                dict(
                    flags=["--reason"],
                    help="Free-form reason recorded on archived entries",
                ),
            ],
            manual_handler=_cmd_sweep_archive_items,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_list_items",
            doc=CODESWEEP_LIST_ITEMS_DOC,
            params=[
                dict(name="sweep_ref", type=str),
                dict(name="state", type=OPT_TEXT, default=None),
                dict(name="tag", type=OPT_TEXT, default=None),
                dict(name="include_archived", type=bool, default=False),
                dict(name="archived_only", type=bool, default=False),
                dict(name="limit", type=OPT_INT, default=None),
            ],
            calls=list_items,
        ),
        cli=dict(
            name="sweep-list-items",
            help="List entries in a sweep",
            args=[
                dict(flags=["sweep"], help="Sweep ID (SW-N) or name"),
                dict(flags=["--state"], help="Filter by state"),
                dict(flags=["--tag"], help="Filter by tag"),
                dict(
                    flags=["--all"],
                    action="store_true",
                    help="Include archived entries",
                ),
                dict(
                    flags=["--archived-only"],
                    action="store_true",
                    help="Show only archived entries",
                ),
                dict(flags=["--limit"], type=int, help="Max entries to return"),
            ],
            manual_handler=_cmd_sweep_list_items,
        ),
    ),
    dict(
        mcp=dict(
            name="codesweep_list",
            doc=CODESWEEP_LIST_DOC,
            params=[
                dict(name="include_archived", type=bool, default=False),
            ],
            calls=list_sweeps,
        ),
        cli=dict(
            name="sweep-list",
            help="List sweeps",
            args=[
                dict(
                    flags=["--all"],
                    action="store_true",
                    help="Include archived sweeps",
                ),
            ],
            manual_handler=_cmd_sweep_list,
        ),
    ),
]
