# CB-40 + CB-41 + CB-36 (final site) + CB-39 — `merge.py` and `pull_next` transaction boundaries

Branch: `fix/cb-40-cb-36-raw-txn` · Base: `4bc34ef` · 2026-08-14
**Revision 2** — rewritten after a Codex/gpt-5.6-sol **NO-GO** on revision 1. Corrections appendix at
the end.

Remedy **(b)** of CB-40 (user-chosen): absorb both raw sites into `db.txn`, allowlist to zero.
CB-41 semantics: **renew the lease atomically** (user-chosen).

Closes **CB-40**, **CB-41**, **CB-39**, and CB-36's last site. All four live in one transaction
boundary; that is why they are one tree and not four.

---

## What revision 1 got wrong

Revision 1 invented a `db.TxnAbort` sentinel so a block could roll back and return a value, because
three paths do that today. **It is not needed, and it was dangerous.**

* Not needed: `lock_held`, idempotent-success and no-candidate **write nothing**, so they can simply
  `return` and let `db.txn` commit an empty transaction. And `main_moved` only needed a rollback
  because the expired-holder `abandoned` UPDATE happened *before* the head check — **reorder those
  two and there is nothing to discard.**
* Dangerous: `db.txn` swallows a failed `ROLLBACK` (`db.py:297-300`, verified). That is correct for a
  real exception — cleanup must not mask the original — but for a control-flow sentinel deliberately
  converted into a normal return, it means a refusal-shaped result handed back with the transaction
  still live and the write still present.

So: **no change to `db.py` at all.** The allowlist shrinks because the raw sites become `db.txn`
users, not because `db.txn` grew a feature.

## Ambient transactions: an unconditional check, not `assert`

Revision 1 proposed `assert not conn.in_transaction`. **Python removes `assert` under `-O`**, so
that is not a barrier. Nor is documentation alone.

Both functions get an unconditional runtime refusal. The reason is specific to what they are: under
an ambient transaction `db.txn` yields `False` and the caller owns the commit, so `merge()` would
return `proceed: True` **before the lock row is committed** — invisible to every other connection.
For a mutual-exclusion gate, returning "you hold the lock" while the lock is uncommitted is worse
than any defect being fixed here. Same for `pull_next` returning a claim nobody else can see.

The claims module's documented invariant is the precedent for the *rule*, not for enforcing it with
prose — and Codex is right that its "precedent" asserts nothing.

---

## Independent edits

| # | Change | Locations | Cards |
|---|---|---|---|
| 1 | `merge.merge` → `db.txn`; guard moved inside; head check reordered before the expired-holder write; expired self-retry renews | `merge.py:~246-337` | CB-40, CB-41, CB-36 |
| 2 | `pull_next` → `db.txn`; claim captured via `UPDATE … RETURNING *` | `capacity.py:200-255` | CB-40, CB-39 |
| 3 | Ratchet: allowlist → `db.py` only, and count occurrences rather than dedupe by filename | `tests/test_claims.py:350-365` | CB-40 |
| 4 | Correct the stale transaction docs | `CLAUDE.md` | CB-40 |

Four rows, at the ceiling. `merge.py` and `capacity.py` already import `db` (batches 1–2).

### Edit 1 — the new `merge.merge` order

Everything below is inside one `with db.txn(conn):`, after the ambient check:

1. `_get_session` — the read.
2. **Idempotent / self-owned branch.** If this session already holds the lock: **renew** —
   `UPDATE codemerge_locks SET expires_at=<now+TTL> WHERE id=1 AND session_id=?` — and return
   `proceed: True`. This branch now WRITES, so it must commit; returning normally does that.
   Renewal is unconditional on expiry: a live lease is extended, an expired one is reclaimed by its
   own owner. That is CB-41 option (b), and it removes the disagreement between the two branches
   because expiry no longer decides anything on the self-owned path.
3. **Status guard**, now inside the lock (CB-36): refuse unless `active`.
4. Read the lock row. If held by *someone else* and unexpired → `return` `lock_held`. No writes.
5. **Head check** — `current_main_head_fn()` — moved to HERE, *before* any write. Mismatch →
   `return` `main_moved`. No writes.
6. Only now: if the lock is held by someone else and expired, mark that holder abandoned; then take
   the lock and set this session `merging`.

Step 5 moving above step 6 is what makes the sentinel unnecessary, and it is also simply more
correct: today a `main_moved` refusal has already abandoned another session before deciding not to
proceed, and only the rollback hides it.

**Accepted consequence (Codex, and it is a real contract change):** requests that previously refused
instantly — nonexistent, `done`, `abandoned` sessions — now may wait up to `busy_timeout` and can
surface `database is locked` instead of `KeyError`/`ValueError` under contention. That is inherent
to making the guard authoritative; a guard evaluated outside the lock is the defect. Documented in
the docstring. The external head callback already ran under the lock, so that exposure is unchanged.

### Edit 2 — `pull_next`

Same absorb. The claim UPDATE gains `RETURNING *`; the raw row is fetched **inside** the block and
converted with `_row_to_item` **after** it — converting inside would let a malformed `meta_json`
roll back an otherwise successful claim (CB-24 consequence 2). That closes **CB-39**'s post-commit
re-read in the same pass, and per CLAUDE.md's RETURNING rule that statement's `rowcount` must never
be read afterwards.

### Edit 3 — the ratchet

Allowlist becomes `{("db.py", "BEGIN IMMEDIATE")}`. Codex is right that the current check is a
*filename* allowlist — `found` is a set and the assertion is `found <= allowed`, so any number of
occurrences in an allowed file passes, and zero would pass too. Since this tree's claim is "exactly
one executable site", the check counts occurrences and asserts the count.

## Verification

1. **CB-41, the test that does not exist today:** an expired same-session retry followed by a
   competing acquisition must not yield `proceed: True` twice. Drive the clock by writing
   `expires_at` in the past rather than sleeping.
2. **CB-41 renewal:** an expired self-retry extends `expires_at`; a competing session then gets
   `lock_held`, not the lock.
3. **CB-36 guard race — NOT DELIVERED; the promise is withdrawn rather than left standing.**
   A discriminating reproducer needs the same commit-seam machinery CB-39 needs. The honest
   position is that the guard-move is verified by line-by-line review — Codex traced it and
   confirmed that with every authoritative read and write under `BEGIN IMMEDIATE`, a
   `merging` session without its lock raises and cannot renew another owner's lock — plus the
   lease tests below. Recorded on CB-36 rather than approximated by a test that cannot fail.
4. **`main_moved` performs no writes** — assert the previous holder is NOT abandoned after a
   `main_moved` refusal. **This PASSES against old code**, which wrote and then rolled back;
   no assertion over these tables separates "never wrote" from "wrote and undid it". It is a
   regression pin for the new ordering, which reaches the same outcome without depending on a
   rollback. An earlier draft of this line claimed it fails old code — that was wrong.
4b. **CB-41 round two** — a slow acquisition must not be GRANTED an already-expired lease.
   Needs an **injected clock**: a first draft expired the pre-existing lock row inside the
   HEAD callback and passed against the defect, because the hoisted stamp was still only
   microseconds old. Monkeypatch `merge.datetime` and burn the TTL inside the callback.
   Verified to fail against a hoisted-stamp mutation.
5. **Ambient refusal:** both functions raise under `db.txn`, unconditionally (not via `assert`).
6. **CB-39:** `pull_next` returns the row it claimed, not the newest attachment.
7. **Ratchet** passes with one entry and a counted assertion.
8. Full suite (934 baseline) + `ruff check`.

## Risks / out of scope

* This is the mutual-exclusion primitive for parallel agents. A wrong rollback means two agents
  merging at once.
* CB-41 (b) means the TTL no longer bounds a *retrying* holder — a hung-but-retrying session can
  hold the gate indefinitely. Accepted deliberately by the user; recorded here because it is the
  cost side of the choice, and it wants a follow-up (stale-holder detection that is not TTL-based).
* Out of scope: CB-31, CB-37, CB-38.

---

## Adversarial Review Corrections (revision 2)

Codex/gpt-5.6-sol, confidence 0.95, verdict **NO-GO** on revision 1. Findings, all accepted:

1. **CB-41 discovered** — the idempotent path ignores lease expiry, so two sessions can both be told
   to proceed. Filed as its own `high` card; verified by the author by reading both branches. This
   was the single most valuable output of the review.
2. **`TxnAbort` could return a refusal with the transaction still live**, because `db.txn` swallows a
   failed `ROLLBACK`. Removed the sentinel entirely — its own design-smell section had the better
   answer.
3. **`assert` is stripped under `-O`** — replaced with an unconditional runtime check, and the
   claims "precedent" was misread: it documents the invariant without enforcing it.
4. **CB-39 should use `UPDATE … RETURNING`**, not a second SELECT under the lock.
5. **The ratchet is a filename allowlist**, not a one-site ratchet.
6. **Stale docs** — CLAUDE.md still declares two raw sites and says `pull_next` follows merge.py's
   raw pattern.

Codex also judged the guard-move's latency/error-precedence change to be a real but acceptable
contract change, and confirmed `pull_next`'s fresh-connection concurrency is otherwise preserved.

---

## Adversarial Review Corrections (round 2 — on the implemented diff)

Codex/gpt-5.6-sol reviewed the DIFF after revision 2 was implemented, and returned a second
**NO-GO** at 0.98 confidence with one fatal finding **introduced by this fix**:

**The lease deadline was computed before the write lock and before the HEAD callback.** Both can
consume real time — the lock wait is bounded by `busy_timeout`, and `current_main_head_fn` shells
out to git. If either burned the TTL, the lease was written ALREADY EXPIRED, the call returned
`proceed: True`, and a competitor immediately saw the lock reclaimable and also got `proceed: True`.
Codex reproduced it deterministically by advancing the clock 301 seconds inside the callback.

That is **CB-41 reappearing inside its own fix** — the defect class this repo has now filed
repeatedly (the naive predicate reintroducing CB-25; the empty-intersection trap in CB-28). Both
timestamps are now sampled at their point of use: the expiry comparison inside the lock, the granted
deadline immediately before each write, after the callback returns.

Also fixed from the same review:

* **The ratchet over-claimed.** A line scan misses multiline calls, `executescript`, lowercase
  spelling, and two calls on one line — so "exactly one executable site" was enforcing less than it
  said. Now an AST literal-call census; dynamic SQL remains out of reach of any static check and the
  docstring says so.
* **A third test passes on both sides** (`test_no_candidate_returns_none_and_writes_nothing`) and was
  not labelled. Labelled.
* **This plan contradicted its own test file** about `main_moved`. Corrected above.
* **The promised CB-36 race discriminator was never delivered.** Withdrawn explicitly above rather
  than left as an unmet promise.

What Codex confirmed as sound: dropping `TxnAbort` removed the hazard rather than relocating it (no
refusal path writes; the empty commit creates no durable state); the ordinary races serialize
correctly and a `merging` session cannot renew another owner's lock; `pull_next`'s `RETURNING`
capture and lock timing; and the CLAUDE.md edits apart from the lease claim, which the stale-deadline
fix now makes true.

One judgement recorded rather than actioned: Codex notes there is no fencing token, so an external
`abandon_session` can revoke a holder after it was told to proceed. That is a real property of this
design, pre-existing and unchanged here; it wants a fencing token or an owner acknowledgement, which
is a larger design question than this tree.

---

## Adversarial Review Corrections (round 3 — third NO-GO)

Codex, 0.99 confidence, reproduced the **same fatal defect one step further along**: round 2 moved
the lease stamp below the lock and the HEAD callback, but the stale-holder `abandoned` UPDATE sits
between the stamp and the lease write. Burning the TTL there produced B and C both `proceed: True`.

**Three rounds on one shape is the signal that the fix was at the wrong layer.** Point-of-use
sampling has to be re-established every time a statement is inserted, and twice it silently wasn't.
So the deadline is now computed by SQLite inside the UPDATE
(`strftime('%Y-%m-%dT%H:%M:%SZ','now',?)`) — sampling and writing are the same operation, and a
stale deadline is unrepresentable rather than merely avoided. `merge.py` imports no clock at all now.

Consequences worth recording:

* **The round-2 regression test became vacuous** and was replaced. It monkeypatched
  `merge.datetime`, which the SQL version does not consult, so it passed trivially. The replacement
  asserts the **SQL template** (the repo's rule for guards of this kind) plus a live-lease check, and
  a second test covers the stale-holder takeover path specifically. Both verified to fail against a
  Python-sampled-deadline mutation.
* **The AST ratchet had three more gaps**, all closed: `executescript` bodies hold many statements
  so only checking the literal's prefix missed `"SELECT 1; BEGIN IMMEDIATE; …"`; a leading `--`
  comment hid the statement after it; and a bare `startswith("BEGIN")` over-counted `BEGINNING`. It
  is now statement-split, comment-stripped and token-aware. Its docstring now states what it still
  cannot see — runtime-built SQL, and the unresolved receiver — instead of claiming a complete
  executable-site census.

Recorded but not actioned, same as round 2: there is no fencing token, so an external
`abandon_session` can revoke a holder after it was told to proceed. Pre-existing, unchanged here,
and a larger design question than this tree.
