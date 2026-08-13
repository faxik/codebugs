# CB-26 (re-scoped) — a terminal source entity must not stay live in a derived queue

**Scope ratified by the user 2026-08-14:** build the *reconciliation* half — eager hook **plus**
one-time backfill. The card's original subject (should a **severity** re-triage re-route between
`stream/security` and `stream/triage`?) stays **open** and is explicitly out of scope.

## Reproducer — measured on the live `.codebugs/findings.db`, 2026-08-14

```sql
SELECT mi.item_ref, mi.status, f.status FROM milestone_items mi
LEFT JOIN findings f ON f.id = mi.item_ref
WHERE mi.milestone_id='stream/triage' AND mi.status='open';
```

- 23 rows open in `stream/triage`
- **19 point at a terminal finding** (18 `fixed`, 1 `wont_fix`):
  CB-7, CB-8, CB-9, CB-10, CB-11, CB-12, CB-13, CB-14, CB-15, CB-16, CB-17, CB-18,
  CB-19, CB-20, CB-22, CB-23, CB-24, CB-25, CB-28
- only 4 point at genuinely open findings

`triage_inbox` is ~83% stale and the oldest "eligible" row is fixed CB-7.

## Root cause — verified by direct read, not inferred

- `milestone_items.status` is the **only** thing milestone queries consult:
  `triage.py:69` (`WHERE milestone_id='stream/triage' AND status='open'`) and
  `capacity.py:_candidates` → `pull_next`.
- `findings.update_finding` **does** fire status-change hooks (`findings.py:358`), and the
  infrastructure is real (`db.py:217` `register_status_change_hook`, `db.py:236`
  `run_status_change_hooks`, which runs hooks **inside the caller's transaction**).
- But `grep -rn register_status_change_hook src/codebugs/` returns **exactly one**
  registration package-wide: `claims.py:825`.
- Milestones registers **only** the add-time router:
  `register_post_add_hook("milestones.auto_route", _auto_route_finding)`
  (`milestones/__init__.py:638`, hook body `triage.py:21`).

So a finding is routed into a stream when it is **filed**, and nothing ever moves the item when it
**resolves**. `pull_next` can therefore hand an agent work that is already done.

## The correct fix, stated before any feasibility filter

Register the **update-side twin** of the existing add-side router. `db.register_status_change_hook`
was built for exactly this and its docstring says so ("The update-side twin of
register_post_add_hook: the create side already existed, the update side did not"). `claims.py:482`
`_auto_release_on_terminal` is the worked example of the shape, including the crucial constraint:
it calls the **core, non-committing** layer because it runs inside the domain update's open
transaction.

This is the CB-28 lesson applied: the seam already exists and was simply never used.

## Design

New module `src/codebugs/milestones/reconcile.py` (keeps `triage.py` focused; may import
`capacity.py`, which imports neither — verified no cycle).

### 1. The outcome map is DECLARED, and fails closed

A terminal entity status maps to a terminal *item* status. Declared in `_schema.py`, never derived
by heuristic:

| entity terminal status | item status |
|---|---|
| finding `fixed` | `done` |
| finding `not_a_bug`, `wont_fix` | `dismissed` |
| requirement `implemented`, `verified` | `done` |
| requirement `superseded`, `obsolete` | `dismissed` |

Source vocabularies: `types.FINDING_TERMINAL` (`types.py:37`), `types.REQUIREMENT_TERMINAL`
(`types.py:42`). Item vocabulary: `_schema.ITEM_STATUSES` / `MILESTONE_ITEM_TERMINAL`
(`_schema.py:22,25`).

**A status absent from the map raises** rather than defaulting — the hook wrapper logs it and the
item stays open, which is visible, instead of guessing. A **completeness test** asserts the map's
keys equal `FINDING_TERMINAL | REQUIREMENT_TERMINAL`, so adding a terminal status to `types.py`
without deciding its projection fails CI. This is the standing "fail closed on the unknown, count
it, and gate new values in CI" instruction in its concrete form.

### 2. The hook

```
_reconcile_on_terminal(conn, entity_id, old_status, new_status)
```

1. **Schema-probe** `milestone_items` first and return if absent — same guard and same reason as
   `_auto_route_finding` (`triage.py:27`): raw `sqlite3.connect()` callers (e.g. `tests/test_sweep.py`)
   invoke `add_finding`/`update_finding` on connections with no milestones schema.
2. `entities.EntityRef.of(entity_id)`; return unless `new_status in ref.kind.terminal`.
   (`EntityRef.of` raises `ValueError` on an unknown id shape — `entities.py:145`.)
3. Select **all** non-terminal items for that `item_ref`
   (`status NOT IN ('done','dismissed')`). Multiple attachments are legal — the
   `UNIQUE(milestone_id, item_kind, item_ref)` constraint (`_schema.py:72`) is per-milestone, not global.
4. Per row: set the mapped status, `done_at`, `updated_at`, clear `assigned_agent`; write an audit
   row with a new `RECONCILER_ACTOR` constant.
5. **Release held capacity.** If the row had an `assigned_agent`, call
   `capacity._decrement_capacity(conn, agent, size)` (`capacity.py:78` — verified **non-committing**).

**Step 5 is the fix's own edge case, and it is the trap this repo has hit three iterations running.**
`pull_next` increments capacity (`capacity.py:224`) and only `release_item` decrements it
(`capacity.py:295`). Closing a pulled item without decrementing would leak an agent's slot
**permanently** — a new defect shipped inside this defect's fix.

### 3. No commit, ever

The hook runs inside `update_finding`'s open transaction. It therefore uses bare `conn.execute`
statements and **must not** call `conn.commit()` and **must not** call
`foundation.move_item`/`capacity.release_item`, both of which commit internally
(`capacity.py:306`). Under `db.txn`'s reentrancy an inner commit would commit the **caller's**
work — the documented CB-24 trap.

### 4. Backfill

`reconcile_all(conn, *, actor, dry_run=False) -> dict` — sweeps every milestone item whose source
entity is already terminal, applying the same mapping and writing the same audit rows. Exposed as
MCP `milestone_reconcile` and CLI `milestone-reconcile` (both carry the domain prefix per the
naming rule; the milestones spec-canonical exception covers only the five named tools). Wrapped in
`db.txn` at the public layer, mirroring `claim`/`_claim_core`'s two-layer split.

Not run from `ensure_schema`: a silent bulk mutation on every `connect()` is exactly the kind of
invisible write this repo files cards about.

### 5. Idempotence and no recursion

`triage_dismiss` sets the item to `dismissed` (`triage.py:96`) **before** propagating to
`update_finding` (`triage.py:116`), so the hook sees no non-terminal item and no-ops. Filtering on
`status NOT IN ('done','dismissed')` makes re-entry a no-op generally, so no audit duplicates.

## Risks & out of scope

- **Out of scope:** the severity/category **re-routing** question (CB-26's original title) — stays open.
- **Out of scope:** CB-27's live `sweep.mark_items` race and CB-29's filter contract — separate trees,
  no clustering predicate passes.
- **Behaviour change, intended and ratified:** `triage_inbox` drops from 23 rows to 4;
  `pull_next` stops offering resolved work.
- **Requirements are affected too**, not just findings — `reqs.update_requirement` fires the same
  hook and `item_kind='requirement'` rows exist. Deliberate.
- `item_kind='external'` rows have no source entity; the hook is keyed on `item_ref` from an entity
  update, so they are never touched.

## Verification

Every test must be proven to fail against the unfixed code (revert the registration line / the
`_decrement_capacity` call, confirm the diffstat is non-empty first, then re-run).

1. finding → `fixed` ⇒ its item becomes `done` + one audit row.
2. finding → `not_a_bug` / `wont_fix` ⇒ item `dismissed`.
3. requirement → `implemented` ⇒ item `done`; → `obsolete` ⇒ `dismissed`.
4. **capacity**: pull an item (capacity 1) → mark the finding fixed ⇒ `assigned_agent` cleared **and**
   `<size>_held` back to 0.
5. **atomicity**: the item change rolls back with the finding update when the transaction aborts.
6. **idempotence**: second terminal write adds no second audit row; `triage_dismiss` unchanged.
7. **schema probe**: `update_finding` on a raw connection without milestones tables does not raise.
8. **completeness**: map keys == `FINDING_TERMINAL | REQUIREMENT_TERMINAL`.
9. **multi-attachment**: a finding on two milestones closes both.
10. **`pull_next` no longer returns a terminal-source item.**
11. backfill: `dry_run=True` mutates nothing; real run closes exactly the stale rows and is a no-op
    on a second run.
12. Full suite (886 baseline) + `ruff check`.
