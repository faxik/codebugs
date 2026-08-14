# Cluster: CB-27 + CB-30 — CB-24 conformance for two live read-modify-write sites

Branch: `fix/cb-27-cb-30-txn-conformance`
Base: `764edcf` (main)
Iteration: bugfix-loop, focus `codebugs`, 2026-08-14
**Revision 2** — rewritten after `adversarial-review-x2`. See the corrections appendix at the end.

Both cards were **re-scoped before this tree was created** so that each is fully closable here:

* **CB-27** → the live `sweep.mark_items` site only. Its enforcement-mechanism half is **CB-37**.
* **CB-30** → fault (1), `release_item` atomicity only. Its fault (2) is **CB-38** (reframed).

**CB-36** records every other unwrapped site found by this iteration's sibling sweep.

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

**The split-trigger is resolved and closed.** Revision 1 left a live conditional — "if the review
reads that as two rules rather than one, split the tree". Cross-model review (Codex/Sol) read the
vacuous third clause as **one conditional rule, not two**, and recommended against splitting; the
Opus defender concurred. The tree stands at two sites. `release_item`'s half nevertheless required
a redesign of its central mechanism before implementation — see its section.

**Not the reason for clustering:** same category tag, same severity band, both filed as
CB-24 followups, both cheap. None of those would justify one tree.

## Why only two sites

The sibling sweep found **19 instances** of this shape in the package: 4 already fixed by CB-24,
2 in this tree, **13 outstanding**. All 13 are enumerated with `file:line` on card **CB-36**
(`high`), which owns them. Each wrap is *independently landable*, so taking all of them would be
~13 independent edits against a hard ceiling of 4.

The supporting counts, re-verified by the defender using an AST walk rather than grep: **43**
`conn.commit()` calls with `conn` as receiver, against **7** `db.txn` call sites. (A line-based
grep reports 47 because four hits are inside docstrings.) Those numbers are the *search space*, not
the result — the instance count comes from reading every committing function, which is CB-36's
recorded method.

This is sequencing, not a cheaper substitute: no site is being fixed in a weaker way than the
correct way, there are simply fewer of them in this tree.

## Independent edits

| # | Change shape | Locations | Cards | Pre-finish verification |
|---|---|---|---|---|
| 1 | Wrap body in `db.txn`; delete own `conn.commit()`; document batch atomicity in the docstring. Return dict is built locally, so no conversion move. | `src/codebugs/sweep.py:410` (`mark_items`); body to wrap `:429-482` | CB-27 | `pytest tests/test_sweep.py -q` + new race and rollback tests |
| 2 | Wrap body in `db.txn` opened **before** the lookup; swap the lookup to a raw-row helper; `UPDATE … RETURNING *` to capture the mutated row; convert outside the block; delete own `conn.commit()`. | `src/codebugs/milestones/capacity.py:252-314` (`release_item`); new helper in `milestones/_spine.py` | CB-30 | `pytest tests/test_milestones*.py -q` + new race, commit-seam and precedence tests |

Two rows, both independently landable and verifiable synchronously. Ceiling (4) respected.

## Imports and ratchet

Neither module can reach `db.txn` today; this must be part of the diff, not discovered during it.

* `src/codebugs/sweep.py` has **no module-level `db`**. `:766` imports *names*
  (`register_schema, register_tool_provider, register_cli_provider`), and `:976` is a
  *function-local* `from codebugs import db` inside `register_cli` — which is why the CLI handler
  at `:1048` can call `db.connect()` while `mark_items` at `:410` cannot. Add a module-level
  `from codebugs import db` and drop the redundant local. Import-safe: `db.py` imports domain
  modules only inside `_ensure_modules_loaded()`, never at module level.
* `src/codebugs/milestones/capacity.py` has **zero** `db` references. Add `from codebugs import db`,
  and extend the `_spine` import (`:12`) with `_row_to_item` and the new `_get_item_row_by_ref`.

**The `tests/test_claims.py` `BEGIN IMMEDIATE` allowlist does NOT change.** `capacity.py:211`
(`pull_next`) is one of its two grandfathered raw sites, alongside `merge.py:256`. This change adds
`db.txn`, not a raw `BEGIN`, so the allowlist neither grows nor shrinks. Do not "helpfully" widen it.

---

## CB-27 — `sweep.mark_items` admits transitions no serial order permits

### Root cause — verified by reading
`src/codebugs/sweep.py`:
* `429` `_resolve_sweep`, `430` `_load_sweep_lifecycle` — reads, outside any transaction.
* `453-457` per-item `SELECT state, archived_at FROM codesweep_items`.
* `464` `_validate_transition(transitions, cur_row["state"], target_state)` — **the decision, made
  in Python from the row just read**.
* `467-477` the dependent `UPDATE`.
* `479-482` the sweep-timestamp `UPDATE`.
* `483` `conn.commit()` — the function's own commit, no `db.txn` anywhere in the body.

`busy_timeout` serializes the *writes*; it never touches the read that preceded them.

### Reproducer
Sweep with `lifecycle = [a, b, c]`, `transitions = {a: [b, c], b: [], c: []}`. Two writers
concurrently mark the same item — A to `b`, B to `c`. Both read state `a`, both validate against
that stale read, both write.

### Plan
Wrap `429`–`482` in one `with db.txn(conn):`. Delete `483`. `_load_sweep_lifecycle`'s `json.loads`
stays **inside**: it is input to the decision, not output conversion.

**One transaction for the whole batch, not one per item.** Two reasons, and neither is "a race
between items" — there is no cross-item state, and a per-item `BEGIN IMMEDIATE` would in fact close
each item's own stale-read window. The real reasons are:
(a) the batch must roll back as a unit;
(b) `_resolve_sweep` and `_load_sweep_lifecycle` (`:429-430`) read the lifecycle and `transitions`
DAG **once, outside the loop** — per-item transactions would let a concurrent lifecycle rewrite
invalidate the DAG that the remaining items are still being validated against.

**Add the contract to the docstring** (`:418-428`) *before* writing the rollback test, so the test
pins a documented promise rather than inventing one:

> The whole batch is one transaction: if any item is missing, archived, or makes an illegal
> transition, **no** item in the call is applied.

### Verification

1. **Race test — and the assertion is the hard part.** The final state is `b` **both** before and
   after the fix, so asserting on state is vacuous in both directions. The discriminator is *who
   gets refused*:
   * Pre-fix: B commits `c`, A then commits `b`. **Neither call raises.**
   * Post-fix: B blocks at `BEGIN IMMEDIATE`, A's bounded wait expires, A commits `b`; B unblocks,
     re-reads `b`, and `_validate_transition` (`:402-407`) finds `transitions["b"] == []` →
     **`ValueError`**.

   So: capture B's exception in the worker thread; assert A succeeded and B raised `ValueError`
   matching `Transition not allowed`.

   Use the **bounded three-event interleave**, not "let B run to completion" — after the fix B can
   never complete while A holds the lock, so an unbounded wait burns the 5s `busy_timeout`. Copy
   the established shape from `tests/test_findings.py:504-547`, whose own docstring explains that
   the bounded `b_read.wait(timeout=1.0)` guarded by `b_started` is what prevents a false pass.
   Needs a new `PausingConnection` twin in `tests/test_sweep.py` keying on
   `"SELECT state, archived_at FROM codesweep_items"` (`:453-455`); the one-shot reset matters,
   since `mark_items` issues that SELECT once per item.
2. **Partial-batch rollback:** `mark_items` over `[valid, invalid]` leaves *neither* applied.
   Nothing depends on today's behaviour — after a mid-loop raise the earlier UPDATE is *pending,
   not committed*, and every caller discards it (MCP `:862` under `with conn_factory()`, CLI
   `:1050` under `finally: conn.close()`). No test asserts partial application; the only failure
   test (`tests/test_sweep.py:206-207`) uses a single nonexistent item and raises before any write.
3. **Ambient-transaction test:** on a connection with an open transaction, `db.txn` yields `False`,
   nothing commits, and a caller rollback discards the work.
4. `pytest tests/test_sweep.py -q` unchanged — the DAG test at `:527` must still pass.

---

## CB-30 — `release_item` decrements capacity from a pre-lock read

### Root cause — verified by reading
`src/codebugs/milestones/capacity.py`:
* `263` `item = _get_item_by_ref(conn, item_ref)` — read, no lock held.
* `264` `agent = item.get("assigned_agent")` — **the value the later decrement depends on**.
* `278-295` the item `UPDATE` (unconditional; both branches discard the cursor).
* `301-302` `_decrement_capacity(conn, agent, item["size"])` using the stale `agent`.
* `303-312` `_audit(...)` using stale `item["status"]` as `from_state`.
* `313` `conn.commit()`, no `db.txn`. `314` a post-commit re-read.

### Reproducer
Agent A holds two `small` items; `small_held = 2`. `release_item(item_1)` reads
`assigned_agent = A`. Before it writes, CB-26's hook (`reconcile._apply_row`, `reconcile.py:155-184`)
closes item 1 and decrements `2 → 1`. `release_item` resumes and decrements `1 → 0`. Item 2 is
still assigned but capacity reports zero.

### Plan — redesigned in revision 2

Open `with db.txn(conn):` **before** line 263; close it after the audit; delete `313`.

**1. The opening lookup must return a RAW row.** `_get_item_by_ref` (`_spine.py:83-90`) returns
`_row_to_item(row)`, and `_row_to_item` (`:21-25`) calls `json.loads` on `meta_json`. Parsing
therefore happens *before any write*, which is why revision 1's malformed-`meta` verification was
unreachable. `release_item` consumes only plain columns from that row — `id` (282, 291), `size`
(302), `milestone_id` (305), `status` (310), `assigned_agent` (264) — and never `meta`. So add to
`_spine.py`:

```python
def _get_item_row_by_ref(conn: sqlite3.Connection, item_ref: str) -> sqlite3.Row:
    """Raw attachment row — no JSON parsing. A caller that parses `meta_json`
    inside a write transaction turns a malformed value into a rollback of a write
    the contract promises has landed (CB-24 consequence 2)."""
    row = conn.execute(
        "SELECT * FROM milestone_items WHERE item_ref = ? ORDER BY id DESC LIMIT 1",
        (item_ref,),
    ).fetchone()
    if not row:
        raise KeyError(f"Item not found: {item_ref}")
    return row
```

and refactor `_get_item_by_ref` to `return _row_to_item(_get_item_row_by_ref(conn, item_ref))`.
**Do not inline a second copy of the query in `capacity.py`** — `_spine.py:85` must stay the only
place the `ORDER BY id DESC LIMIT 1` selection rule is written, or CB-33's eventual fix has two
sites to find. `pull_next` (`capacity.py:249`) keeps the converting variant.

**2. Capture the mutated row with `RETURNING *`; do not re-query for it.** The closing
`_get_item_by_ref(conn, item_ref)` at `:314` runs *after* the commit at `:313`, so a concurrently
inserted newer attachment is returned instead of the row just written — the function reports
`status='open'` for an item it just marked `done`. Append `RETURNING *` to both UPDATEs
(`278-282`, `292-295`), keep that row, convert with `_row_to_item` after the block. Guard
`updated_row is None` — unreachable under the write lock, but a `None` would otherwise surface as
an opaque `TypeError` outside the block.

This is compatible with CLAUDE.md's `RETURNING` rule: both UPDATEs discard their cursors entirely
today, so no `rowcount` read is being broken. Idiom precedent: `claims.py:271`/`:274` and
`:335`/`:338`, carrying the literal comment `# NEVER rowcount — this statement carries RETURNING`.

**3. What the return value now promises — and what it does not.**

> `release_item` returns **the attachment it actually mutated**. It does **not** promise that
> attachment is the one the caller meant.

Revision 1 credited the by-id capture with fixing CB-33. **It does not.** The opening lookup is
still `ORDER BY id DESC LIMIT 1` and the signature (`:252-259`) accepts neither an attachment id
nor a `milestone_id`, so the *selection* remains arbitrary — and the wrong capacity slot can still
be decremented, since `agent` and `size` both come off that arbitrarily-chosen row. What this fix
closes is the **post-commit re-read window**, nothing more. CB-33 stays open.

**4. Exception precedence — "preserved" is true only uncontended.**

| Case | Today | After | Note |
|---|---|---|---|
| missing item + invalid status | `KeyError` | `KeyError` | preserved |
| malformed `meta_json` + invalid status | `JSONDecodeError` | `ValueError` | **deliberate change** — parsing moves after the block |
| malformed `meta_json` + valid status | `JSONDecodeError`, no write | write lands, then `JSONDecodeError` | **deliberate** — this is CB-24 consequence 2 working |
| write lock held by another writer | domain exception (WAL readers never block) | `sqlite3.OperationalError` from `db.py:291` after `busy_timeout` | **new failure mode**, the cost of locking first |

`JSONDecodeError` subclasses `ValueError` — the exact trap CLAUDE.md's error-handling rule names.
Harmless here only because `release_item` has **no CLI handler** (verified: `milestones/__init__.py`
registers only `milestone-list`, `milestone-status`, `milestone-audit`, `triage-inbox`,
`wip-status`, `milestone-mark-branch`, `milestone-mark-integrated`, `milestone-reconcile`), so no
catch-ordering arm exists to invert. Note this in the docstring so a future CLI handler does not
reproduce CB-15/CB-16.

Argument validation stays **inside** the block, preserving the uncontended order. Hoisting it would
avoid taking the lock for a doomed call but would flip missing-item-plus-invalid-status from
`KeyError` to `ValueError`. Out of scope.

### Verification

1. **Race test** — two connections, bounded three-event interleave, reusing
   `tests/test_milestones.py:899-915`'s existing `PausingConnection` **as-is**: it already keys on
   `"SELECT * FROM milestone_items"`, which is `_get_item_by_ref`'s statement (`_spine.py:84-87`)
   and stays so under the raw-row refactor. Assertion discriminates on state: pre-fix
   `small_held == 0` while item 2 is still assigned; post-fix `small_held == 1`, because once A
   commits, `_live_rows` (`reconcile.py:140-152`) filters `status != target` and the hook no-ops.
2. **Commit-seam test — single-threaded, and it fails on `main`.** Revision 1's multi-attachment
   test could not fail: with a static set of attachments both lookups pick the same row, so `main`
   already returns the row it mutated. The discriminator must be injected *between the commit and
   the re-read*, which no existing helper reaches (`PausingConnection` is one-shot and fires at
   `:263`). Add a `CommitPausingConnection` hooking **both** seams — `commit()` for `main`'s
   `:313` and an `execute()` prefix check for `COMMIT`, which is how `db.txn` closes (`db.py:294`)
   — because keying on only one gives a vacuous pass on the other. The hook inserts a *newer*
   attachment for the same `item_ref` on a second connection (`AUTOINCREMENT`, `_schema.py:107`,
   guarantees it wins `ORDER BY id DESC`). Assert `result["id"] == mutated_id` and
   `result["status"] == "done"`. Pre-fix the re-read returns the new attachment → `status == "open"`.
3. **Precedence tests** — the four rows of the table above.
4. **Ambient-transaction test** as for CB-27.

### Explicitly out of scope
* `_decrement_capacity` (`:79-88`) silently accepting a missing row or zero counter
  (`MAX(col-1,0)`, no `rowcount` check) — hardening beyond the CB-24 edit; belongs with **CB-38**.
* Everything in **CB-38** (the other doors that leak a slot).
* **CB-33** — which attachment `release_item` *should* act on.
* `pull_next` (`:249`) has the **same post-commit re-read window** as `release_item:314` — it
  returns `_get_item_by_ref(...)` after its `COMMIT`. Found while verifying this plan; not fixed
  here because `pull_next` is the other grandfathered raw-`BEGIN IMMEDIATE` site and restructuring
  it is a separate edit. To be filed.

---

## Shared risks

* **Lock-hold duration grows.** `mark_items` issues exactly `2N+3` statements under one writer lock
  (2 sweep reads, SELECT+UPDATE per item, 1 sweep UPDATE). The batch size is **caller-supplied and
  unbounded** — `codesweep_mark` (`sweep.py:845`) declares no ceiling. At observed usage (tens of
  items) the cost is negligible, and no ceiling is added here. Recorded because CB-31 makes the
  opposite complaint about `pull_next`'s lock window.
* **Behaviour change for ambient-transaction callers.** After the change, a caller already holding a
  transaction no longer has its work committed as a side effect. No in-repo caller does this —
  independently re-verified: `mark_items` is reached only from `sweep.py:862` and `:1050`,
  `release_item` only from `milestones/__init__.py:332`, all fresh-connection; `server._conn`
  (`server.py:18-24`) only connects, yields and closes; and the hooks bypass both targets entirely
  (`reconcile._apply_row` calls `_decrement_capacity` directly, `claims._auto_release_on_terminal`
  calls `_release_core`, `triage._auto_route_finding` emits its own `INSERT OR IGNORE`).
* **`.worktrees/` is untracked in main.** Explained, not dirt to act on. Never `git add -A`.

## Out of scope
CB-36's other thirteen sites; CB-37's enforcement mechanism; CB-38's capacity policy; CB-31, CB-33,
CB-29, CB-21, CB-6, CB-32, CB-34, CB-35. Also:

* `mark_items` does not de-duplicate `items`, and `updated` reports `len(items)` rather than
  distinct rows changed (`sweep.py:452`, `:486`). Benign — a repeat re-reads the updated state and
  `_validate_transition` short-circuits on `src == dst` (`:400-401`) — but it is a small instance of
  this repo's success-shaped-return family. Pre-existing; to be filed, not folded in.

---

## Adversarial Review x2 Corrections (revision 2)

Attackers: **Codex/gpt-5.6-sol** (completed, confidence 0.97) and an **Opus adversary** which was
still running after 53 minutes and is treated as FAILED; if it returns, its findings get a second
defender round. Defender and judge: Opus. **Single-attacker review — recorded as a limitation.**

Defender tally: **19 CONCEDE, 4 PARTIAL, 0 clean DEFEND.** Verdict: CB-27's half survived with
wording and test-construction repairs; **CB-30's half needed its central mechanism redesigned**,
because two of its three constraints rested on premises that do not hold.

What the review caught that revision 1 got wrong:

1. **The malformed-`meta` verification was unreachable.** `_get_item_by_ref` parses JSON at the top,
   before any write, so "the write lands and the error surfaces" could not be produced by moving
   only the closing conversion. Fixed by the raw-row helper.
2. **Returning by numeric `id` does not fix CB-33** — it buys self-consistency only. Revision 1
   claimed otherwise.
3. **Three proposed tests could not fail against `main`**, the failure mode this repo has a
   documented history of shipping. The multi-attachment test was vacuous; both race tests would
   have deadlocked on `busy_timeout`; and CB-27's race assertion did not discriminate at all, since
   the final state is identical before and after.
4. **The batch-transaction rationale was false as written** — per-item transactions *would* close
   the per-item race. The real justification is batch atomicity plus pinning the once-only
   lifecycle/DAG read.
5. **Accounting contradicted itself** — "ten other sites" and "thirteen remain" in one document.
   Reconciled to 19 instances / 4 fixed / 2 here / 13 on CB-36.
6. **Citations** — `mark_items` is at `:410` (not `:429`); the sweep-timestamp UPDATE is `:479-482`
   (not `:478-481`).
7. **Missing from the change list entirely** — neither module can even reach `db.txn` today.

One attacker finding was **rejected**: that the plan claimed a CLI path for `release_item`. Both
cited paths sat inside a parenthetical qualifying "MCP wrappers"; the attacker misparsed the
sentence. The underlying fact — `release_item` has no CLI handler — is true and is now stated
explicitly rather than left to parsing.

Found by the author while verifying the defender's citations, and missed by both reviewers:
`pull_next` (`capacity.py:249`) carries the same post-commit re-read window as `release_item:314`.
