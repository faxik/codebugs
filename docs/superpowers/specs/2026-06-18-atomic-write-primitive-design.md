# Design: `atomic_write` — shared BEGIN IMMEDIATE transaction primitive

Date: 2026-06-18
Status: REVIEWED (adversarial review passed 8/10; mandatory fixes applied — see appendix)

## Problem

The "atomic write under `BEGIN IMMEDIATE`" pattern is copy-pasted in two places:

- `merge.py:239-289` — the lock-acquire critical section inside `merge.merge()` (merge.py:193). NOTE: this is `merge.merge()`, NOT `start_session` (merge.py:73), which is a different function with no BEGIN IMMEDIATE.
- `milestones.py:812-851` — `pull_next`.

Both repeat verbatim:

```python
saved_isolation = conn.isolation_level
conn.isolation_level = None
try:
    conn.execute("BEGIN IMMEDIATE")
    try:
        ... critical section ...
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
finally:
    conn.isolation_level = saved_isolation
```

CLAUDE.md documents the coupling: milestones "reuses the `merge.py:239-289` save/restore pattern."
Coupling-by-convention — a bug fixed in one site won't reach the other. This is in-process,
pure-SQLite, trivially deepenable.

## Exit-path constraint (the crux)

The critical section has THREE exit paths, not two:

1. **Completion** → COMMIT.
2. **Exception** → ROLLBACK, re-raise.
3. **Voluntary abort returning a value WITHOUT raising** —
   `merge.py:251` (`lock_held`), `merge.py:268` (`main_moved`) both do
   `conn.execute("ROLLBACK"); return {...}`; `milestones.py:825` does
   `conn.execute("ROLLBACK"); return None` when no candidate is eligible.

Path 3 is why a naive "commit on clean exit, rollback on exception" CM is wrong:
the body must be able to roll back AND hand a value back to the caller.

The read-decide-write sequence must be atomic under the IMMEDIATE lock; the abort is the
no-write branch of that sequence. For these call sites the reads (the lock row and
`current_main_head_fn()` in merge, candidate selection in `pull_next`) are the
lock-protected state — hoisting them before `BEGIN` would reintroduce the TOCTOU race the
lock exists to close.

## Proposed interface (Design C — optimize for the common caller)

Lives in `db.py` (infrastructure layer). New imports required in db.py:
`from contextlib import contextmanager`, `from collections.abc import Iterator`,
`from typing import NoReturn` (db.py already has `dataclass`, `Any`, `Callable`,
`AbstractContextManager`).

```python
# in db.py (infrastructure layer)

class _Abort(Exception):
    """Internal sentinel — carries an abort return value out of the with-block.
    Private to db.py; only ever raised via Tx.abort()."""
    def __init__(self, value: Any):
        self.value = value

@dataclass
class Tx:
    aborted: bool = False
    abort_value: Any = None
    def abort(self, value: Any = None) -> NoReturn:
        self.abort_value = value
        self.aborted = True
        raise _Abort(value)

def _safe_rollback(conn: sqlite3.Connection) -> None:
    """ROLLBACK that never masks an in-flight exception. After a COMMIT that
    failed by ending the transaction, an explicit ROLLBACK raises
    'cannot rollback - no transaction is active'; swallow only that."""
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        pass

@contextmanager
def atomic_write(conn: sqlite3.Connection) -> Iterator[Tx]:
    """Run a critical section under BEGIN IMMEDIATE.

    - block completes      -> COMMIT
    - tx.abort(value)       -> ROLLBACK; tx.aborted=True, tx.abort_value=value
    - any other exception   -> ROLLBACK, re-raise
    - COMMIT itself fails    -> best-effort ROLLBACK, re-raise the COMMIT error
    isolation_level is always restored.

    Precondition: conn must NOT already be in a transaction (no nested BEGIN).
    """
    saved = conn.isolation_level
    conn.isolation_level = None
    tx = Tx()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield tx
        except _Abort:
            _safe_rollback(conn)            # voluntary abort: swallow, no re-raise
        except BaseException:
            _safe_rollback(conn)            # real error in body: rollback + propagate
            raise
        else:
            conn.execute("COMMIT")          # COMMIT outside the body's try; if it
                                            # raises, propagate the real error unmasked
    finally:
        conn.isolation_level = saved
```

Why `COMMIT` sits in `else:` and rollback goes through `_safe_rollback`: if `COMMIT`
were inside the body `try`, a failing COMMIT would fall into `except BaseException`,
run a second ROLLBACK, and that ROLLBACK could itself raise "no transaction active",
masking the original COMMIT error. The `else:` branch + swallow-on-rollback guarantees
the caller always sees the true failure.

### Usage — trivial happy path

```python
with atomic_write(conn):
    conn.execute(...)
    conn.execute(...)
# committed; isolation restored. zero ceremony.
```

### Usage — merge.py `merge()` (two abort-with-value exits)

```python
with atomic_write(conn) as tx:
    lock = conn.execute("SELECT * FROM codemerge_locks WHERE id=1").fetchone()
    if lock["session_id"] is not None:
        if lock["expires_at"] and lock["expires_at"] > now:
            tx.abort({"proceed": False, "reason": "lock_held",
                      "holder": lock["session_id"], "held_since": lock["acquired_at"],
                      "expires_at": lock["expires_at"]})
        conn.execute("UPDATE codemerge_sessions SET status='abandoned', last_activity=? "
                     "WHERE session_id=? AND status='merging'", (now, lock["session_id"]))
    actual_head = current_main_head_fn()
    if actual_head != expected_main_head:
        tx.abort({"proceed": False, "reason": "main_moved",
                  "expected_head": expected_main_head, "current_head": actual_head})
    conn.execute("UPDATE codemerge_locks SET session_id=?, acquired_at=?, expires_at=? WHERE id=1",
                 (session_id, now, expires))
    conn.execute("UPDATE codemerge_sessions SET status='merging', last_activity=? WHERE session_id=?",
                 (now, session_id))

if tx.aborted:
    return tx.abort_value
return {"proceed": True, "session_id": session_id}
```

### Usage — milestones.py pull_next (single abort-with-None)

```python
with atomic_write(conn) as tx:
    held = _capacity_for(conn, agent_id)
    chosen = None
    for item, milestone in _candidates(conn):
        if _eligibility_failure(conn, item, milestone, capacity, held) is None:
            chosen = item
            break
    if chosen is None:
        tx.abort(None)
    now = utc_now()
    conn.execute("""UPDATE milestone_items SET status='in_progress', assigned_agent=?,
                    pulled_at=?, updated_at=? WHERE id=? AND status='open'""",
                 (agent_id, now, now, chosen["id"]))
    _upsert_capacity_increment(conn, agent_id, chosen["size"])
    _audit(conn, milestone_id=chosen["milestone_id"], item_ref=chosen["item_ref"],
           actor=actor, action="pull", from_state="open", to_state="in_progress",
           reason=f"agent={agent_id} capacity={capacity}")

if tx.aborted:
    return None
return _get_item_by_ref(conn, chosen["item_ref"])
```

## What it hides

- The `isolation_level` save/`None`/restore dance in a `finally` (the easy-to-forget part).
- Issuing `BEGIN IMMEDIATE` and the SQLite lock semantics.
- The three-way exit fork: COMMIT on fall-through, ROLLBACK+swallow on `_Abort`,
  ROLLBACK+re-raise on any other exception. Catching `BaseException` ensures
  `KeyboardInterrupt`/`SystemExit` still roll back before propagating.
- The class of bug where a newly-added early return forgets its `ROLLBACK`.

## Dependency strategy

In-process. Lives in `db.py` (infrastructure; exports `connect()`, ID gen). No new
third-party deps — `contextlib`, `dataclasses`, `sqlite3` only. Both `merge.py` and
`milestones.py` already symbol-import from `codebugs.db` at module level
(`merge.py:429`, `milestones.py:1713`); the refactor extends those existing import
lines with `atomic_write, Tx`. No import cycle: `db.py` imports domain modules only
lazily inside `_ensure_modules_loaded()` (db.py:304), so the dependency direction
(domain → db) is unchanged. Concurrency remains SQLite file-lock (WAL + `BEGIN
IMMEDIATE`), unchanged.

### busy_timeout / SQLITE_BUSY

`db.connect()` should set `PRAGMA busy_timeout=5000` explicitly (after the WAL pragma,
db.py:296). This is behavior-preserving — Python's `sqlite3.connect()` already defaults
its `timeout` arg to 5.0s (mapped to `busy_timeout=5000`) — but makes the serialization
window explicit instead of resting on an implicit default. Today's cross-connection
correctness (the passing two-thread test at `tests/test_milestones.py:801`) depends on
this timeout: a second `BEGIN IMMEDIATE` *blocks* up to the timeout rather than failing
immediately.

## Testing strategy

New boundary tests. BEGIN IMMEDIATE works on `:memory:` (existing `test_merge.py` runs the
real critical section in-memory), so most tests use the in-memory `conn` fixture; only the
cross-connection contention test needs a file-based `tmp_path` conn for a real OS lock:
- (`:memory:`) happy path commits and persists rows; isolation_level restored afterward.
- (`:memory:`) `tx.abort(v)` rolls back (rows NOT persisted), returns `v`, sets `tx.aborted`,
  restores isolation_level.
- (`:memory:`) body raising a real exception rolls back and re-raises; isolation_level restored.
- (`:memory:`) COMMIT-failure path (e.g. deferred constraint) re-raises the real error,
  not a "no transaction active" rollback error.
- (`tmp_path`, two `db.connect()` connections) second `atomic_write` blocks/serializes
  against the first up to `busy_timeout`, then would raise — verify serialization, not a crash.

Old tests to keep: the existing behavioral tests of `merge.start` and `milestones.pull_next`
already assert the observable outcomes — they should pass UNCHANGED after the refactor
(that is the regression guarantee). No shallow-helper tests to delete (none exist).

## Known footguns to verify (adversarial targets)

1. **Bare `return` inside the block COMMITS.** A `return X` inside `with atomic_write(conn):`
   triggers normal `__exit__`, which runs `COMMIT`. If a caller *meant* to abort but wrote
   `return` instead of `tx.abort()`, it silently commits. Document: "to abort, call `tx.abort()`,
   never bare `return`."
2. **`except _Abort` swallow scope.** If body code calls a helper that raises `_Abort` for an
   unrelated reason, it'd be swallowed. Mitigated by keeping `_Abort` private to db.py.
3. **Nested `atomic_write` / already-in-transaction.** SQLite has no nested BEGIN; calling
   `atomic_write` while a transaction is open errors. Neither call site nests today; document
   the precondition (conn not already in a transaction).
4. **`COMMIT`/`ROLLBACK` failure modes — RESOLVED.** `COMMIT` is in the `else:` branch and
   all rollbacks go through `_safe_rollback`, so a failing COMMIT propagates its real error
   rather than being masked by a "no transaction active" rollback. `finally` still restores
   isolation_level on every path. See the interface section for the rationale.
5. **SQLITE_BUSY under contention.** A second `BEGIN IMMEDIATE` while another connection holds
   the write lock blocks up to `busy_timeout` (5000 ms — Python's `sqlite3.connect` default,
   made explicit in `db.connect`), then raises `sqlite3.OperationalError: database is locked`.
   `atomic_write` does NOT catch `OperationalError`: a genuine lock-timeout hits
   `except BaseException` → rollback → re-raise (correct — it's a real error). Serialization
   correctness depends on the timeout being set on the connection.
```

## Adversarial Review Corrections (2026-06-18)

3-agent adversarial review (adversary / defender / judge, all opus). Verdict: **8/10 —
fix mandatory items, then ship.** 0 FATAL; the core three-way-exit-fork design and all
Python `@contextmanager`/`_Abort`/`BaseException` control-flow semantics verified correct.
Baseline: 132 tests green.

Mandatory fixes applied to this spec:

1. **SERIOUS-5 (real latent bug):** `COMMIT` moved into an `else:` branch; added
   `_safe_rollback()` that swallows `OperationalError`. The original "COMMIT inside the
   body try" would let a failed COMMIT trigger a second ROLLBACK that raises "no
   transaction active", masking the true error. Judge built+ran the fix — all four exit
   paths correct, COMMIT-failure surfaces unmasked.
2. **SERIOUS-2 (doc error):** the merge critical section is in `merge.merge()` (merge.py:193),
   NOT `start`/`start_session` (merge.py:73). Corrected throughout.
3. **SERIOUS-1 (doc error):** "both modules already import db" was loose — they
   symbol-import from `codebugs.db` (merge.py:429, milestones.py:1713); refactor extends
   those lines with `atomic_write, Tx`. No cycle (db.py imports domain modules only lazily).
4. **SERIOUS-4 (doc omission):** adversary's "BEGIN IMMEDIATE will raise not block" was
   REFUTED — Python's default `busy_timeout=5000` serializes contenders, proven by the
   passing two-thread test at tests/test_milestones.py:801. Conceded the documentation gap:
   added explicit `PRAGMA busy_timeout=5000` to `db.connect()` and footgun #5.
5. **WEAKNESS-2 (mechanical):** added `contextmanager`/`Iterator`/`NoReturn` imports to db.py.

Recommended fixes also applied: SERIOUS-3 (tests run on `:memory:` except the contention
test), WEAKNESS-3 (softened the TOCTOU "MUST" wording).

Dismissed: WEAKNESS-1 (the two-field `Tx` is the deliberate minimal way to surface the abort
value without leaking the private `_Abort` sentinel to callers — kept as designed).
