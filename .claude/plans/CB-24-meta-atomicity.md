# CB-24 — meta updates are a non-atomic read-modify-write

## Reproducer

`tests/test_findings.py::TestConcurrentMetaUpdatesDoNotLoseEachOther`, two tests, both
deterministic against the unfixed tree:

```
FAILED test_a_competing_append_note_is_not_erased
FAILED test_a_competing_meta_update_key_is_not_erased
  AssertionError: {'from_a': True}   # from_b written by a call that returned success
```

A `PausingConnection` subclass fires a one-shot hook immediately after the opening
`SELECT * FROM findings WHERE id = ?`. Writer A pauses there until writer B (its own
connection, its own thread) has also read. Both then merge from the same stale row; A
writes last and B's merge is gone, with no error on either side.

## Root cause

`findings.update_finding` (`src/codebugs/findings.py:274-346`) and
`reqs.update_requirement` (`src/codebugs/reqs.py:184-242`) both:

1. `SELECT` the row — **outside any transaction**;
2. `json.loads(row["meta"])` and merge `notes` / `append_note` / `meta_update` in Python;
3. `UPDATE ... SET meta = ?` with the serialized result;
4. `conn.commit()`.

Steps 1 and 3 are two independent transactions. `busy_timeout=5000` serializes the
*writes* and does nothing about the read that preceded them, so two writers that both
reach step 1 before either reaches step 3 both succeed and the later erases the earlier.

Pre-existing, not a regression: `git show main:src/codebugs/findings.py` already reads the
row before the merge. CB-16 fixed the duplicate `SET` assignment (one `meta = ?` per
statement); that is orthogonal to whether the read and the write are one unit.

## Plan

Wrap each function's whole body in `db.txn(conn)` — which issues `BEGIN IMMEDIATE`, so the
write lock is taken *before* the SELECT — and delete the unconditional `conn.commit()`.

Commit ownership is the subtle part, and it is why `conn.commit()` must go rather than
stay: `db.txn` yields `False` when a transaction is already open, in which case this frame
must do nothing at all and leave the commit to the owning frame. A surviving
`conn.commit()` would commit *the caller's* transaction from inside a nested call.

Callers, both verified by reading:

- `milestones/triage.py:115,121` — `triage_dismiss` has already written `milestone_items`
  and its audit row, so an implicit write transaction is open and `db.txn` yields `False`.
  Its own `conn.commit()` at `:125` now commits all three writes as one unit, where before
  the nested `update_finding` committed the dismissal early. A strict improvement, and the
  reason the ambient case must be handled rather than forbidden.
- `provenance.py:265` — `resolve_trailers` only `SELECT`s beforehand, and a SELECT does not
  open a transaction under Python's default isolation level, so `db.txn` yields `True` and
  the per-trailer commit behaviour is unchanged.

The MCP wrappers (`findings.py:673,828`) and CLI handlers (`reqs.py:678,837`) each use a
fresh connection with no ambient transaction.

## Risks & out of scope

- **A no-op update now takes the write lock briefly.** `update_finding(conn, id)` with no
  arguments reaches `if not updates: return` inside the transaction. Deriving "will this
  write?" from the arguments before opening the transaction was rejected: it would
  duplicate the argument list, which is the exact fragility the existing lazy-meta guard
  comment warns about — a new argument added without updating the pre-check becomes a
  silent no-op. Correctness over a lock held for microseconds on a path nothing hot uses.
- **Ambient DEFERRED transactions do not gain `BEGIN IMMEDIATE` semantics.** When `db.txn`
  yields `False` the read and write are still one unit, which is what this card is about,
  but the lock was taken by the caller on its own terms. `triage_dismiss` opens its
  implicit transaction with a write, so there is no read-snapshot upgrade to fail on.
- **Out of scope:** compare-and-swap with retry, and moving the merge into SQL via
  `json_patch`. Both are heavier than the defect warrants and neither matches the project's
  existing transaction discipline.
- The requirements twin has no `append_note` (deliberate, documented); its exposure is
  `notes` and `meta_update` only.

## Verification

- The two findings tests plus their requirements twins fail before and pass after.
- Mutation: revert each `db.txn` wrapper independently and confirm the matching tests fail.
- Full suite, plus `ruff check`.
