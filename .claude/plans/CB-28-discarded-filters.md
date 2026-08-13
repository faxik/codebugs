# CB-28 — a declared argument silently discarded by routing

## Reproducer

Against main `f22d9fb`. Two findings (one `critical`, one `low`), both blocked so both are
`deferred`; two requirements (`must`, `could`), both blocked:

```
query(status="deferred")                        -> 2 rows
query(status="deferred", severity="critical")   -> 2 rows   <-- should be 1
query(status="deferred", category="nonsense")   -> 2 rows   <-- should be 0
reqs_query(status="deferred", priority="must")  -> 2 rows   <-- should be 1
staleness_check(finding_id="CB-1", status="fixed") -> 1 row  <-- CB-1 is open
release_item(status="abandoned", commit="deadbeef") -> done_commit = None
```

## Root cause

Four branches route to a narrower implementation and forward only some of the arguments the
caller supplied. The dropped ones are **known, correctly spelled and correctly typed** — they
pass every validation the package has — and the call returns a success payload. CB-15 closed
exactly this for *unknown* argument names, on the stated grounds that a success payload with the
caller's data discarded is worse than an error because the caller cannot tell. Nothing closed it
for known ones.

## Evidence (each read directly this session)

| site | branch | silently dropped |
|---|---|---|
| `findings.py:769` | `if status == "deferred"` | `severity, category, file, source, tag, meta_key, meta_value, commit, ref, group_by, id, ids` |
| `reqs.py:758` | `if status == "deferred"` | `priority, section, search, source, tag, group_by, id, ids` |
| `provenance.py:122` | `if finding_id` | `status, category, file` |
| `milestones/capacity.py:260` | `if status == "abandoned"` | `commit` |

The last one was found by the sibling sweep, not named on the card.

## Sweep method (stated, because last iteration's sweep was incomplete)

Swept for the **shape**, in two AST passes, not for the names of known-bad arguments:

1. *early return* — a function with ≥4 params having a `return` that references ≥2 fewer params
   than its widest sibling return. Found `findings.py:769`, `reqs.py:758`, and two false
   positives in `claims.py` (both `except sqlite3.OperationalError → _undetermined(...)`, the
   documented contended outcome, not a dropped filter).
2. *if/else assignment* — both branches assign the same variable but one ignores ≥2 params the
   other consumes. This is the shape pass 1 structurally cannot see, and it is the shape
   `provenance.check_findings` has. Found `provenance.py:122` and `capacity.py:260`, plus three
   false positives: `update_finding` / `update_requirement`'s `if not updates:` no-op path (there
   is nothing to forward), and `add_blocker`'s duplicate check (it raises).

Two passes were needed because pass 1's notion of "branch" was `return`. Recording that so the
next sweep starts from "what shapes can a branch take", not from one of them.

## Plan — FORWARD. (This section replaces a rejected "just raise" plan.)

**My first plan said "raise at all four sites" and was wrong.** Its argument — that forwarding
would teach the generic `blockers` module about `severity` and `priority` — attacked a strawman
implementation. The cross-model review rejected it and produced the evidence:

- **The April design doc already specifies the correct shape**
  (`docs/2026-04-04-blockers-design.md:278-291`): detect `status="deferred"`, **strip** it, call
  `blockers.get_deferred_item_ids(conn, entity_type)`, pass the result as an `id IN (…)` filter to
  the ordinary domain query, and annotate each row with `blocker_count`. Dependency points the
  right way: blockers stays generic, each domain keeps its own filters.
- **`blockers.get_deferred_item_ids` already exists** (`blockers.py:462`) and returns exactly that
  set. The clean path was built and then not used; `query_deferred_entities` is the shortcut that
  shipped instead.
- **`provenance.check_findings`'s own docstring already promises it** — "Filters forward to
  findings.query_findings" (`provenance.py:117`) — while the `finding_id` branch forwards nothing.
  Code contradicting its own stated contract is the CB-23 shape, and CB-23 settled that the
  contract wins.
- **Ordering is not an obstacle**, which was my unstated feasibility worry: `query_findings` orders
  by `{rank_sql}, created_at DESC` (`findings.py:493`) and `query_deferred_entities` by
  `{rank_sql}, created_at DESC` via `kind.order_by()` (`blockers.py:445`). Identical. Forwarding
  preserves the CB-20 ranked order for free.

So "raise" was a cheaper substitute for a fix the repo had already designed. Forward instead:

1. **`findings.py` MCP `query`** and **`reqs.py` MCP `reqs_query`** — on `status="deferred"`:
   intersect `get_deferred_item_ids(...)` with any caller-supplied `id`/`ids`, drop the synthetic
   status, call the ordinary domain query with **every** other filter intact, and re-annotate
   `blocker_count`. `total` then reflects the filtered count, which is what the caller asked for.
2. **`provenance.check_findings`** — the `finding_id` branch forwards `status`/`category`/`file`
   through `query_findings(id=finding_id, …)`, honouring the docstring.

**The empty intersection must short-circuit, and this is the sharp edge.** `ids=[]` means "no
filter" to `query_findings` (pinned by a CB-25 test), so passing an empty intersection through
would return the **whole table** — the CB-28 defect reappearing inside CB-28's own fix, exactly as
the naive predicate did in CB-25. Callers return an empty page instead. For the same reason
`types.is_vocabulary_filter_active` is **not** used here: my first plan proposed it for this
judgement, and it is documented as wrong for list-valued arguments.

## Siblings — refuse, because here the argument is genuinely unhonourable

These are not forwarding failures; no path exists that could honour them.

3. **`capacity.release_item(status="abandoned", commit=…)`** — an abandoned item is reopened and
   re-pullable, so a commit is meaningless; only `done` records one (`capacity.py:245`, and the MCP
   doc says so at `milestones/__init__.py:313`). Incompatible-argument combination → raise.
4. **`foundation.set_item_status`** — returns early when the status already matches, silently
   dropping `commit` despite the docstring promising "Records done_commit + done_at when terminal".
   Raise and name `mark_integrated`, which is the documented way to record a commit
   (`README.md:286`), rather than inventing backfill semantics on the no-op path.
5. **`findings.query_findings(meta_value=… )` without `meta_key`** — `if meta_key and meta_value`
   / `elif meta_key` means a lone `meta_value` adds no condition at all. The MCP description
   already declares `meta_key` required (`findings.py:749`); enforce it.

## Risks & out of scope

- **Behaviour change, deliberately.** Callers passing a deferred+filter combination were getting
  wrong rows; they now get right ones. Callers passing an unhonourable argument now get an error
  instead of a success. Both match what CB-15 did for unknown names.
- **`ids=[]` keeps meaning "no filter"** on the ordinary query and is untouched. Only the
  *intersection result* short-circuits.
- **Golden schema**: no tool description changes are planned — behaviour is being brought into line
  with what the descriptions already claim — so `tests/golden/mcp_schema.json` should not move. If
  any description does change, regenerate it with `PYTHONPATH=src uv run python tests/dump_schema.py`,
  since `test_boundary.py:155` snapshot-tests descriptions.
- **`group_by` now works on the deferred path** as a consequence of forwarding. Note in passing that
  `group_by="status"` groups by persisted statuses, not one synthetic `deferred` bucket.
- Not in scope: CB-29 (free-text filter validation).

## Risks & out of scope

- **`limit`/`offset` are honoured by the deferred path and must stay allowed.** So must
  `status` itself — it is what selected the branch.
- **This narrows a public surface**: a caller today passing `status="deferred", severity=…` gets
  rows and will now get `ValueError`. That is the intended correction (they were getting wrong
  rows), and it matches what CB-15 did to unknown names. Called out because it is a behaviour
  change, not a pure addition.
- **`group_by` on the deferred path**: also unhonoured, so also refused. Worth noting separately
  because its absence changes the *shape* of the response, so a caller passing it is even more
  clearly misled.
- **Not in scope:** making the deferred path actually support filtering (that is a feature, and
  it is CB-6/CB-21's surface-parity question). Not in scope: free-text filter validation (CB-29).

## Verification

- Every reproducer row must flip to `ValueError` (or, for `release_item`, to a raise).
- New tests proven to fail against unfixed code by reverting `src/` and re-running.
- The *allowed* combinations must keep working: `query(status="deferred")`,
  `query(status="deferred", limit=5, offset=1)`, `release_item(status="done", commit=…)`.
- Full suite (866 on main) + `ruff check`.
