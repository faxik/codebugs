# Cluster: CB-27 + CB-30 — CB-24 conformance for two live read-modify-write sites

Branch: `fix/cb-27-cb-30-txn-conformance`
Base: `764edcf` (main)
Iteration: bugfix-loop, focus `codebugs`, 2026-08-14

Both cards were **re-scoped before this tree was created** so that each is fully closable here:

* **CB-27** → the live `sweep.mark_items` site only. Its enforcement-mechanism half is **CB-37**.
* **CB-30** → fault (1), `release_item` atomicity only. Its fault (2) is **CB-38** (reframed).

A third card, **CB-36**, records the ten *other* unwrapped sites this iteration's sibling sweep
found. It is deliberately not in this tree — see "Why only two sites".

---

## Why one tree

**Predicate: sibling-sweep hit** (`bugfix-loop` Phase 7 mandates a repo-wide sibling sweep; the
shared clustering reference admits such a hit "only under the clustering ceilings").
`capacity.release_item` is a hit of the sweep run for `sweep.mark_items`, and it is an
already-filed card (CB-30).

**One transformation rule, stated once, applying to both sites:**

> Take `db.txn(conn)` **before** the first read; keep the read, the Python decision derived from
> it, and every dependent write inside that one block; delete the function's own `conn.commit()`;
> convert any returned row **outside** the block.

**Falsifiable check, and an honest caveat.** Clause 3 is vacuous at `mark_items` — it builds its
return dict locally (`sweep.py:484-488`) and has no row to convert. A Codex/gpt-5.6-sol review of
the shortlist argued this makes the two edits "directionally honest, not literally identical" and
recommended naming the tree *CB-24 conformance for two live sites* rather than *identical edit
seam*. That naming is adopted. The rule itself is identical at both sites; only its third clause
has nothing to do at one of them. **If the adversarial review reads that as two rules rather than
one, split the tree** — `mark_items` is the higher-ranked member and goes alone.

**Not the reason for clustering:** same category tag, same severity band, both filed as
CB-24 followups, both cheap. None of those would justify one tree.

## Why only two sites, when fifteen exist

The sibling sweep found ~15 instances of this shape package-wide (43 executable `conn.commit()`
sites against 7 `db.txn` users). Each wrap is *independently landable*, so taking all of them
would be ~15 independent edits against a hard ceiling of 4. The remaining thirteen are filed with
their evidence as **CB-36** (`high`) rather than silently deferred, and the loop takes them in
later iterations. This is sequencing, not a cheaper substitute: no site is being fixed in a
weaker way than the correct way, there are simply fewer of them in this tree.

## Independent edits

| # | Change shape | Locations | Cards | Pre-finish verification |
|---|---|---|---|---|
| 1 | Wrap body in `db.txn`; delete own `conn.commit()`. Return dict is local, no conversion move needed. | `src/codebugs/sweep.py:429-488` (`mark_items`) | CB-27 | `pytest tests/test_sweep.py -q` + new `test_sweep_txn.py` race + rollback tests |
| 2 | Wrap body in `db.txn` starting **before** `_get_item_by_ref`; delete own `conn.commit()`; capture updated row by numeric `id` inside the block and convert outside. | `src/codebugs/milestones/capacity.py:263-314` (`release_item`) | CB-30 | `pytest tests/test_milestones*.py -q` + new race test |

Two rows, both independently landable and verifiable synchronously. Ceiling (4) respected.

---

## CB-27 — `sweep.mark_items` admits transitions no serial order permits

### Reproducer
Sweep with `lifecycle = [a, b, c]`, `transitions = {a: [b, c], b: [], c: []}`.
Two writers concurrently call `mark_items(state="b")` and `mark_items(state="c")` on the same
item. Both `SELECT` state `a`, both validate their own transition against that read, both
`UPDATE`. Final state is whichever committed last, and the DAG says only one of the two
transitions was ever legal from a serialized standpoint — after `a → b`, `b → c` must be
**rejected** because `b` has no outgoing edges.

Deterministic construction (no timing luck): drive writer A to the point after its `SELECT`,
then let writer B run to completion, then let A finish. In-process this is done by opening two
connections to one file-backed DB and interleaving explicitly.

### Root cause — verified by reading
`src/codebugs/sweep.py`:
* `429` `_resolve_sweep`, `430` `_load_sweep_lifecycle` — reads, outside any transaction.
* `453-457` per-item `SELECT state, archived_at FROM codesweep_items`.
* `464` `_validate_transition(transitions, cur_row["state"], target_state)` — **the decision, made
  in Python from the row just read**.
* `467-477` the dependent `UPDATE`.
* `483` `conn.commit()` — the function's own commit, no `db.txn` anywhere in the body.

`busy_timeout` serializes the *writes*; it never touches the read that preceded them. This is
verbatim the CB-24 mechanism.

### Evidence it is reachable
MCP tool `codesweep_mark` (`sweep.py:~845`) and CLI `_cmd_sweep_mark` (`sweep.py:1047`), both of
which open a fresh connection — so neither holds an ambient transaction that `db.txn` would defer
to.

### Plan
Wrap from `_resolve_sweep` (429) through the sweep-timestamp `UPDATE` (478-481) in one
`with db.txn(conn):`. **One transaction for the whole batch, not one per item** — the batch must
roll back as a unit, and per-item transactions would reintroduce the same race between items.
Delete `483`. `_load_sweep_lifecycle`'s `json.loads` stays *inside*: it is input to the decision,
not output conversion.

### Verification
1. The race test above fails on `git show main:src/codebugs/sweep.py` and passes after.
2. Partial-batch rollback: `mark_items` over `[valid, invalid]` leaves *neither* applied.
3. Ambient-transaction test: on a connection with an open transaction, `db.txn` yields `False`,
   nothing commits, and a caller rollback discards the work.
4. `pytest tests/test_sweep.py -q` unchanged (the DAG test at `tests/test_sweep.py:527` must
   still pass).

---

## CB-30 — `release_item` decrements capacity from a pre-lock read

### Reproducer
Agent A holds two `small` items; `agent_capacity.small_held = 2`. Writer 1 calls
`release_item(item_1)` and reads `assigned_agent = A`. Before it writes, CB-26's reconciliation
hook (`reconcile._apply_row`, `reconcile.py:155-184`) closes item 1 and decrements `2 → 1`.
Writer 1 resumes and decrements `1 → 0`. Item 2 is still assigned to A, but capacity reports zero
held — so A can be handed more work than its declared capacity.

### Root cause — verified by reading
`src/codebugs/milestones/capacity.py`:
* `263` `item = _get_item_by_ref(conn, item_ref)` — read, no lock held.
* `264` `agent = item.get("assigned_agent")` — **the value the later decrement depends on**.
* `278-295` the item `UPDATE` (unconditional).
* `301-302` `_decrement_capacity(conn, agent, item["size"])` using the stale `agent`.
* `303-312` `_audit(...)` using stale `item["status"]` as `from_state`.
* `313` `conn.commit()`, no `db.txn`.

### Plan
Open `with db.txn(conn):` **before** line 263 and close it after the audit. Delete `313`.

Three constraints, each of which is a known trap in this repo:

1. **Return conversion moves outside the block.** `_get_item_by_ref` → `_row_to_item`
   (`_spine.py:21-25`) calls `json.loads` on `meta_json`; a malformed value raises
   `json.JSONDecodeError` *inside* a block that would then roll back a write the contract
   promises has landed. That is CB-24 consequence (2) and CB-16's lie in a new place.
2. **Re-fetch by numeric `id`, not by `item_ref`.** A post-block `_get_item_by_ref(conn, item_ref)`
   re-resolves via `ORDER BY id DESC LIMIT 1` (`_spine.py:83-90`) and can return a *different,
   newer* attachment — the CB-33 defect. Capture the raw row by `item["id"]` inside the block;
   convert after.
3. **Argument validation ordering is preserved deliberately.** Today the `_get_item_by_ref` lookup
   precedes both the `abandoned`+`commit` refusal (266-274) and the invalid-status refusal (299).
   Hoisting validation before `db.txn` would avoid taking the write lock for a doomed call, but it
   **changes which exception wins** when the item is missing *and* the status is invalid
   (`KeyError` today, `ValueError` after). Out of scope; the no-op path holding the lock for one
   read is the same cost CB-24 already accepted in `update_finding`.

### Explicitly out of scope, and why
* `_decrement_capacity` (`capacity.py:79-88`) silently accepting a missing row or a zero counter
  (`MAX(col-1,0)`, no `rowcount` check). Wrapping does not fix it; it is hardening beyond the
  CB-24 edit and belongs with **CB-38**'s idempotency question.
* Everything in **CB-38** (the other doors that leak a slot).

### Verification
1. Race test: two connections, interleaved so the reconciliation hook lands between
   `release_item`'s read and its write. Must fail on `main` and pass after.
2. Return-value test: item with malformed `meta_json` — the write lands *and* the error surfaces,
   rather than the write being rolled back.
3. Multi-attachment test: same `item_ref` attached to two milestones; `release_item` must return
   the attachment it actually updated, not the newest.
4. Ambient-transaction test as above.

---

## Shared risks

* **Lock-hold duration grows.** `mark_items` holds the writer lock for a whole batch, and
  `release_item` for one read plus three writes. Both are required for correctness; at this
  tracker's scale (tens to low hundreds of rows) the cost is negligible. Recorded because CB-31
  makes the opposite complaint about `pull_next`'s lock window.
* **Behaviour change for ambient-transaction callers.** After the change, a caller that already
  holds a transaction no longer has its work committed as a side effect. No in-repo caller does
  this — MCP wrappers (`sweep.py:~845`, `milestones/__init__.py:~318`) and CLI handlers each open
  a fresh connection, and `server.py`'s `_conn` only connects/yields/closes. Verified.
* **`.worktrees/` is untracked in main.** Explained, not dirt to act on. Never `git add -A`.

## Out of scope
CB-36's other thirteen sites; CB-37's enforcement mechanism; CB-38's capacity policy; CB-31,
CB-33, CB-29, CB-21, CB-6, CB-32, CB-34, CB-35.
