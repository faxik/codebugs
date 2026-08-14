# CB-40 + CB-36 (final site) — absorb the two raw `BEGIN IMMEDIATE` sites into `db.txn`

Branch: `fix/cb-40-cb-36-raw-txn` · Base: `4bc34ef` · 2026-08-14
Remedy **(b)** of CB-40, chosen by the user: shrink the `BEGIN IMMEDIATE` allowlist to zero rather
than guard the assignment in place.

Closes **CB-40** and the last site of **CB-36** (`merge.merge`), which share one transaction
boundary. Also closes **CB-39** if the `pull_next` return is fixed in the same pass — see edit 3.

---

## The blocker remedy (b) has to solve

Both raw sites exist for ONE reason, and it is not style: **they roll back and return a value.**

| site | abort path | returns |
|---|---|---|
| `merge.merge` | lock held and unexpired | `{"proceed": False, "reason": "lock_held", …}` |
| `merge.merge` | main moved | `{"proceed": False, "reason": "main_moved", …}` |
| `capacity.pull_next` | no eligible candidate | `None` |

A plain `return` inside `with db.txn(conn)` **commits** — the context manager's `__exit__` runs
normally. So `db.txn` as it stands cannot express these three paths. That is the whole reason the
allowlist has two entries.

`merge.merge`'s `main_moved` abort is not hypothetical bookkeeping: the expired-lock branch above it
has already UPDATEd the previous holder's session to `abandoned`, and that write **must** be
discarded when the head check then fails.

## Design: `db.TxnAbort`, and NO change to `db.txn`

```python
class TxnAbort(Exception):  # db.py
    """Raise inside ``db.txn`` to roll back and return a value instead of raising out.

    ``db.txn``'s ``except BaseException`` already rolls back and re-raises, so this
    needs no special handling there. The CALLER catches it outside the block and
    returns ``.result``.
    """
    def __init__(self, result=None):
        super().__init__("transaction aborted by caller")
        self.result = result
```

Call shape:

```python
try:
    with db.txn(conn):
        ...
        raise db.TxnAbort({"proceed": False, "reason": "lock_held", ...})
        ...
except db.TxnAbort as abort:
    return abort.result
```

**Why this and not a `db.txn` signature change.** `db.txn` yields a `bool` and seven call sites rely
on it. Yielding a handle object instead would change every one of them and the CLAUDE.md contract
that documents the bool. A sentinel exception rides the rollback path that already exists.

**It lives in `db.py`, not privately in each module** — two private copies is the exact drift the
`_SAFE_IDENT` / `_IDENT` bullet warns about.

### The one honest gap, stated up front

Under an **ambient** transaction `db.txn` yields `False` and does not roll back, so a `TxnAbort`
raised there unwinds to the caller's `except` **with the block's partial writes still live in the
caller's transaction**. For `merge.merge` that means the expired-lock `abandoned` UPDATE survives an
abort it was supposed to discard.

This is a *smaller* hazard than CB-40's current one (today those writes are committed outright,
along with the caller's), but it is not zero, and remedy (b) does not by itself make these functions
ambient-safe. Two candidate answers:

* **(i) Document + assert.** Both functions declare "must be called with no open transaction" and
  assert `not conn.in_transaction`. Precedent: the claims module already declares exactly this
  invariant in CLAUDE.md, for the same reason, and no in-repo caller violates it.
* **(ii) SAVEPOINT.** Wrap the abortable region in a SAVEPOINT so the abort rolls back to it even
  when ambient. Precedent: `reconcile._reconcile_on_terminal`. Strictly better, strictly more code.

**Plan takes (i)**, because it matches the existing claims precedent and keeps this tree at four
edits; (ii) is recorded as a follow-up rather than silently skipped. **If review judges (i)
insufficient for a mutual-exclusion gate, take (ii) instead — say so and I will.**

---

## Independent edits

| # | Change | Locations | Cards |
|---|---|---|---|
| 1 | Add `TxnAbort` + docstring | `db.py` | CB-40 |
| 2 | `merge.merge` → `db.txn` + `TxnAbort`; **move the status guard inside the lock** | `merge.py:~248-337` | CB-40, CB-36 |
| 3 | `pull_next` → `db.txn` + `TxnAbort`; capture result by numeric id | `capacity.py:200-255` | CB-40, CB-39 |
| 4 | Shrink the ratchet allowlist to `{("db.py", "BEGIN IMMEDIATE")}` | `tests/test_claims.py:350-358` | CB-40 |

Four rows, at the ceiling.

### Edit 2 detail — the CB-36 half

`merge.merge` reads the session and decides `row["status"] != "active"` at `:262-268`, **before**
`BEGIN IMMEDIATE` at `:288`. A concurrent `abandon_session` committing in that window lets `merge()`
flip an abandoned session back to `merging` and hand it the singleton lock. The guard moves inside.

The idempotent "already merging" short-circuit (`:264-267`) also reads `codemerge_locks` outside the
lock. It moves inside too and becomes a `TxnAbort({"proceed": True, ...})` — it must not commit,
because it writes nothing.

### Edit 3 detail — CB-39 rides along

`pull_next` returns `_get_item_by_ref(conn, chosen["item_ref"])` **after** its COMMIT (`:255`), which
re-resolves `ORDER BY id DESC LIMIT 1` and can return a different attachment. That is CB-39
verbatim. Since this edit already restructures the function's transaction boundary, capturing the
row by numeric `id` inside the block is free here and CB-39 closes with it.

## Verification

1. **Ratchet shrinks to one entry and still passes** — `tests/test_claims.py::test_24`. If it fails,
   a raw `BEGIN` survived.
2. **`merge.merge` guard race** — a session abandoned between the old read point and lock
   acquisition must NOT be revived. Discriminator is who is refused, not final state.
3. **Abort paths still roll back** — `main_moved` must discard the expired-lock `abandoned` UPDATE.
   This test fails on any implementation that returns instead of aborting.
4. **`pull_next` returns the row it claimed**, not the newest attachment (CB-39).
5. **Ambient-transaction behaviour is asserted, whichever of (i)/(ii) is chosen** — under (i) the
   assertion raises; under (ii) the abort rolls back to the savepoint.
6. Full suite (934 baseline) + `ruff check`.

## Risks

* This is the mutual-exclusion primitive for parallel agents. A wrong rollback here means two agents
  merging at once — worse than the defect being fixed.
* `pull_next`'s `_candidates` does per-row blocker/requirement reads inside the lock (CB-31). This
  change neither improves nor worsens that; it must not silently widen the window further.
* Out of scope: CB-31, CB-38, CB-37.
