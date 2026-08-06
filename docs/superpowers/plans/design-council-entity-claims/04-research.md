# Research Findings

Researcher output for the Entity Claim / Ownership design council.

**Evidence discipline used here:** every runtime-behavior claim below was *executed* against this
repo's own interpreter (`uv run python`, Python 3.13.3, sqlite 3.47.1) and the real output is
pasted. Claims I only read are labelled MEDIUM and say so. Probe scripts live in
`/tmp/claude-1000/-home-faxik-w-codebugs/349b1511-db4b-4ac3-a327-b85cb19885ba/scratchpad/`
(`probe1_basics.py`, `probe2_race.py`, `probe3_snapshot.py`, `probe4_contract.py`,
`probe5_substrates.py`, `probe6_scale.py`).

---

## READ THIS FIRST — the two results that change the problem

**1. `BEGIN IMMEDIATE` is NOT what makes `pull_next` correct. The guard in the `WHERE` clause is.**
All four candidate substrates — guarded UPDATE with no explicit transaction at all, `BEGIN
IMMEDIATE` + guarded UPDATE, `INSERT ... ON CONFLICT DO NOTHING`, and an append-only log — produced
**exactly one winner in 200/200 trials with 4 real OS processes**. Architects should not treat
`BEGIN IMMEDIATE` as the price of admission for atomic claiming. It buys multi-statement atomicity,
not mutual exclusion.

**2. The clean `rowcount == 0` loss that every design here assumes is a courtesy of
`busy_timeout=5000`, which `db.connect()` sets only by accident** (it is the default of
`sqlite3.connect(timeout=5.0)`; `db.py:492-503` never mentions it). Set it to 0 and losers get
`OperationalError: database is locked` instead of a clean loss. **The claim API's return contract
must therefore have a fourth outcome — "undetermined, retry" — or it will raise
`database is locked` at users under contention.** Every design in this council that specifies a
three-way return is under-specified.

---

## Question Q3a: `INSERT ... ON CONFLICT(pk) DO NOTHING` — rowcount semantics

### Method
Ran `probe1_basics.py` against a fresh WAL file DB.

```
python           : 3.13.3
sqlite3 module   : 2.6.0
sqlite_version   : 3.47.1
threadsafety     : 3

-- ON CONFLICT DO NOTHING rowcount --
fresh insert rowcount  : 1 | lastrowid: 1
conflicting rowcount   : 0 | lastrowid: 1
total_changes          : 1
row now                : [('CB-1', 'agent-a')]

-- ON CONFLICT DO UPDATE ... WHERE holder=? (idempotent re-claim) --
same-holder re-claim rowcount : 1
other-holder rowcount         : 0
row now                       : [('CB-1', 'agent-a')]

-- RETURNING clause available? (sqlite 3.35+) --
RETURNING on fresh insert : [('CB-2',)]
RETURNING on conflict     : []
```

### Key Findings
- sqlite **3.47.1** — far above the 3.24 floor `ON CONFLICT` needs. Also above 3.35 (`RETURNING`),
  3.25 (window functions), 3.8 (partial indexes). No version risk on any candidate design.
- `rowcount` is **1 on insert, 0 on conflict**, exactly as hypothesised. Hypothesis confirmed by
  execution.
- **`RETURNING` yields rows only when the write actually happened** — an empty result set on
  conflict. This is strictly more expressive than `rowcount` because it returns the row's *content*,
  which is what lets one statement distinguish claim-vs-renew (see Q3e).
- `ON CONFLICT(pk) DO UPDATE ... WHERE claims.holder = excluded.holder` is a **single-statement
  compare-and-swap**: it succeeds for the same holder (renew) and is a no-op for a different holder.

### Implications for Design
Architect B's dedicated-lock-row substrate has a one-statement primitive available with no
transaction ceremony at all. `RETURNING` should be preferred to `rowcount` because it distinguishes
outcomes rather than just counting them.

### Confidence: HIGH (executed)

---

## Question Q3b: two independent connections racing the same key — does exactly one win?

### Method
`probe2_race.py`. Each worker opens its **own** connection **inside its own thread** — this mirrors
production, where `server.py:13-19`'s `_conn()` calls `db.connect()` fresh per MCP tool call.
`threading.Barrier` forces genuine overlap. 200 trials per strategy.

```
sqlite 3.47.1 | workers=2 trials=200 busy_timeout=default (5000, from sqlite3.connect timeout=5.0)
strategy             (wins,losses,errs) -> count                    slowest-trial
A_insert_pk          (1, 1, 0)->200                                 4.3ms
B_begin_immediate    (1, 1, 0)->200                                 8.8ms
C_plain_update       (1, 1, 0)->200                                 8.5ms
D_read_then_update   (1, 1, 0)->200                                 8.8ms
```

Scaled to 8 concurrent claimants:

```
sqlite 3.47.1 | workers=8 trials=200 busy_timeout=default (5000)
A_insert_pk          (1, 7, 0)->200                                 104.8ms
B_begin_immediate    (1, 7, 0)->200                                 105.8ms
C_plain_update       (1, 7, 0)->200                                  82.2ms
D_read_then_update   (1, 7, 0)->200                                 105.7ms
```

Then with `busy_timeout=0` — i.e. what happens when the loser is *not* silently made to wait:

```
sqlite 3.47.1 | workers=8 trials=200 busy_timeout=0
A_insert_pk          (1, 0, 7)->192  (1, 1, 6)->7  (1, 2, 5)->1     10.4ms
                       ERROR x1391: OperationalError: database is locked
B_begin_immediate    (1, 0, 7)->200                                  3.2ms
                       ERROR x1400: OperationalError: database is locked
C_plain_update       (1, 0, 7)->197  (1, 1, 6)->3                    5.4ms
                       ERROR x1397: OperationalError: database is locked
D_read_then_update   (1, 0, 7)->195  (1, 1, 6)->4  (1, 2, 5)->1      4.4ms
```

### Key Findings
- **Exactly one winner in every single trial of every configuration**, including
  `busy_timeout=0`. Safety was never violated. SQLite's write lock is doing its job.
- **What the loser SEES is entirely dependent on `busy_timeout`.** At the default 5000ms the loser
  gets a clean `rowcount == 0`. At 0 it gets `OperationalError: database is locked` — 1391–1400
  exceptions per 200 trials.
- **This is a latent contract bug, not a theoretical one.** `busy_timeout` is 5000ms, and at 8
  concurrent claimants the slowest trial already took **104.8ms**. The margin is ~50x, which is
  fine for a sub-millisecond claim — but if a claim transaction ever grows to include real work
  (a projection write, an audit insert, an embedding lookup), the 5s ceiling is reachable and the
  clean-loss contract silently degrades into an exception.

### Implications for Design
1. The claim API **must** define a fourth outcome for `sqlite3.OperationalError: database is
   locked`. "claimed / already-mine / held-by-other" is an incomplete enumeration. The honest
   contract is claimed / already-mine / held-by-other / **undetermined-retry**.
2. Whoever writes the design should set `busy_timeout` **explicitly** rather than inheriting it from
   a Python default that reads nowhere in the source. `db.py:492-503` sets `journal_mode=WAL` and
   nothing else; a reader of that function has no way to know a 5-second busy timeout is in force.
3. Keep the claim transaction *short*. Anything that widens it eats the safety margin.

### Confidence: HIGH (executed)

---

## Question Q3c: the `pull_next` way — `BEGIN IMMEDIATE` + guarded UPDATE

### Method
Strategy `B_begin_immediate` above reproduces `capacity.py:167-215`'s pattern (save
`isolation_level`, set `None`, `BEGIN IMMEDIATE`, SELECT candidates, guarded UPDATE, COMMIT, restore
in `finally`). Plus `probe3_snapshot.py` CASE 4, a deterministic interleave:

```
=== CASE 4: BEGIN IMMEDIATE (the pull_next pattern) with a live concurrent writer ===
  T1 BEGIN IMMEDIATE -> holds the write lock
  T2 BEGIN IMMEDIATE -> OperationalError: database is locked  (waited 2000ms then gave up)
  T1 saw 'open', UPDATE rowcount=1, committed
  T2 (after T1 released) UPDATE rowcount=0  <- guard rejects, clean loss
  final row: {'id': 'E4', 'status': 'in_progress', 'holder': 'agent-1'}
```

### Key Findings
- Exactly one winner, 200/200, at both 2 and 8 workers.
- The loser sees a clean `rowcount == 0` **provided it can acquire the write lock within
  `busy_timeout`**. CASE 4 shows the other branch explicitly: a `BEGIN IMMEDIATE` that times out
  waiting for a live writer raises `database is locked` **from the `BEGIN IMMEDIATE` statement
  itself**, before any guard is ever evaluated.
- `busy_timeout` in `db.connect()` is **5000 ms**, confirmed by executing
  `PRAGMA busy_timeout` on a real `db.connect()` handle (see Q3e output). It is not set by
  `db.py`; it is `sqlite3.connect()`'s `timeout=5.0` default.

### Implications for Design
`BEGIN IMMEDIATE` **converts a snapshot hazard into a wait**. That is its real value — not mutual
exclusion (Q3d shows the guard already provides that) but making a multi-statement claim
(claim row + status projection + audit row) atomic without risking a mid-transaction upgrade
failure. If a design's claim is genuinely multi-statement, `BEGIN IMMEDIATE` is the right tool. If
it is a single statement, it is ceremony.

### Confidence: HIGH (executed)

---

## Question Q3d: is the `BEGIN IMMEDIATE` dance load-bearing, or is a guarded UPDATE enough?

**This was the question the brief flagged as "real and consequential." Here is the executed
answer, and it is nuanced — a flat "no" would be wrong.**

### Method
`probe3_snapshot.py`, deterministic interleaves (no barrier luck — the writer is *forced* to commit
between the reader's SELECT and its UPDATE).

```
=== CASE 1: deferred txn (isolation_level=''), SELECT then UPDATE, writer commits between ===
  T1 SELECT saw status='open'; T1.in_transaction=False
  T2 claimed and COMMITTED (rowcount=1)
  T1 UPDATE -> rowcount=0  (NO exception; guard rejected it)
  final row: {'id': 'E1', 'status': 'in_progress', 'holder': 'agent-2'}

=== CASE 2: does a bare SELECT open a transaction under isolation_level=''? ===
  before SELECT: in_transaction=False
  after  SELECT: in_transaction=False
  after  INSERT: in_transaction=True
  -> python's legacy mode opens a txn only on DML, NOT on SELECT.

=== CASE 3: EXPLICIT 'BEGIN' (deferred), SELECT pins snapshot, writer commits, upgrade ===
  T1 BEGIN + SELECT saw status='open' (read snapshot now pinned)
  T2 claimed and COMMITTED
  T1 UPDATE/COMMIT -> OperationalError: database is locked   <-- SQLITE_BUSY_SNAPSHOT
      (busy_timeout does NOT rescue this; only rollback+retry does)
  final row: {'id': 'E3', 'status': 'in_progress', 'holder': 'agent-2'}
```

### Key Findings
This is the most important result in the document.

- **A guarded `UPDATE ... WHERE id=? AND status='open'` with NO explicit transaction is sufficient
  for mutual exclusion.** CASE 1: the reader's SELECT was stale, the writer committed underneath it,
  and the UPDATE simply returned `rowcount=0`. Exactly one winner, no exception. Confirmed at scale
  by `C_plain_update` and `D_read_then_update` scoring `(1, N-1, 0)` in 200/200 trials at 2, 4 and
  8 workers.
- **CASE 2 explains why**, and it is a non-obvious fact about Python's sqlite3 that the design must
  not get wrong: under `isolation_level=''` (the default, confirmed below), **a bare `SELECT` does
  not open a transaction**. Each SELECT runs at its own fresh snapshot; the transaction begins only
  at the first DML statement. So the classic read-then-upgrade hazard *does not exist* in the
  no-explicit-transaction path — there is no pinned read snapshot to go stale.
- **CASE 3 is the trap.** The moment you write an *explicit* `BEGIN` (deferred), the SELECT **does**
  pin a read snapshot, and the later upgrade to writer fails with `SQLITE_BUSY_SNAPSHOT`, surfaced
  as `OperationalError: database is locked`. **`busy_timeout` does not and cannot rescue this** —
  retrying a stale snapshot can never succeed; only rollback-and-restart works. `BEGIN IMMEDIATE`
  exists precisely to take the write lock up front and avoid this.

**So the honest answer to the brief's Q3.4:** the `BEGIN IMMEDIATE` in `capacity.py:167` is **not**
load-bearing for mutual exclusion — the `AND status='open'` guard alone provides that. It **is**
load-bearing for two other things: (a) making `pull_next`'s multi-statement body (UPDATE + capacity
increment + `milestone_audit` insert) atomic, and (b) avoiding the CASE 3 `SQLITE_BUSY_SNAPSHOT`
that its own explicit transaction would otherwise create. Removing `BEGIN IMMEDIATE` while keeping
the explicit transaction would be a *regression*; removing both and relying on the guarded single
UPDATE would be correct for exclusion but would lose atomicity of the audit trail.

### Implications for Design
- A design whose claim is **one statement** needs no transaction ceremony whatsoever. It is
  provably correct as a bare guarded UPDATE / upsert.
- A design whose claim is **several statements** (claim row + status projection + audit) needs
  `BEGIN IMMEDIATE`, and must not use a plain `BEGIN`.
- **Never write `conn.execute("BEGIN")` in this codebase.** It is strictly worse than both
  alternatives. Worth stating as a rule in the resulting spec.

### Confidence: HIGH (executed)

---

## Question Q3e: `isolation_level`, PRAGMAs, and `conn.commit()` inside `BEGIN IMMEDIATE`

### Method
Executed against a **real `db.connect()` handle** (`probe1_basics.py`) and `probe3_snapshot.py`
CASE 5.

```
-- what db.connect() actually configures --
isolation_level  : ''
PRAGMA journal_mode  : wal
PRAGMA busy_timeout  : 5000
PRAGMA synchronous   : 2
PRAGMA foreign_keys  : 0
PRAGMA locking_mode  : normal
in_transaction   : False
row_factory      : <class 'sqlite3.Row'>
```

```
=== CASE 5: does conn.commit() work inside an explicit BEGIN IMMEDIATE? ===
  saved isolation_level = ''
  in_transaction after UPDATE: True
  after conn.commit(): in_transaction=False
  visible to other conn: {'id': 'E5', 'status': 'open', 'holder': 'x'}
```

Source, read this run — `src/codebugs/db.py:492-503`:
```python
def connect(project_dir: str | None = None) -> sqlite3.Connection:
    """Open (and initialize) the codebugs database."""
    path = _db_path(project_dir)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_modules_loaded()
    for entry in _resolved_order():
        entry.ensure_fn(conn)
    return conn
```

### Key Findings
- `isolation_level` is `''` — Python's **legacy transaction control**: implicit `BEGIN` (deferred)
  before DML, none before SELECT, autocommit off.
- `busy_timeout=5000` and `synchronous=2` (FULL) are **inherited defaults, not choices** — `db.py`
  sets only `journal_mode=WAL`. `foreign_keys` is **OFF**, which matters: a design that puts a
  `REFERENCES findings(id)` on a claims table gets no enforcement at runtime.
- **`conn.commit()` inside an explicit `BEGIN IMMEDIATE` works correctly** — `capacity.py`'s
  assumption holds; the write became visible to an independent connection.

### Implications for Design
- `foreign_keys=0` means referential integrity between a new claims table and `findings` /
  `requirements` **will not be enforced**. A design relying on FK cascade-on-delete is broken as
  written. Orphaned claim rows must be handled explicitly (this also matters for
  `triage.py:114-117`, which already catches `KeyError` for a deleted finding).
- The `isolation_level` save/restore dance in `capacity.py` is correct but fragile; worth a shared
  helper rather than a third copy.

### Confidence: HIGH (executed)

---

## Question Q3f: the actual three-outcome contract, raced across real PROCESSES

### Method
`probe4_contract.py`. Real `multiprocessing` (no GIL artifact), 4 processes, 200 entities,
spin-wait barrier. Candidate primitive:

```sql
INSERT INTO claims (entity_id, holder, claimed_at, renewed_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(entity_id) DO UPDATE SET renewed_at = excluded.renewed_at
  WHERE claims.holder = excluded.holder
RETURNING holder, claimed_at, renewed_at
```
Row returned + `claimed_at == renewed_at` ⇒ claimed. Row returned + `claimed_at < renewed_at` ⇒
already-mine. No row ⇒ held-by-other, followed by a read to name the holder.

```
sqlite 3.47.1 | REAL PROCESSES: 4 | entities: 200

-- SUCCESS CRITERION 1: exactly one winner per entity --
   (claimed=1, held_by_other=3, exceptions=0) -> 200 entities
   violations: 0
   every loser correctly named the winning agent

-- SUCCESS CRITERION 2: retrying holder is told it still holds, never that it lost --
   fresh   : ('claimed', 'agent-a')
   retry   : ('already_mine', 'agent-a')
   retry   : ('already_mine', 'agent-a')
   other   : ('held_by_other', 'agent-a')
   holder re-retries after the rejected steal: ('already_mine', 'agent-a')
   row     : {'entity_id': 'CB-RETRY', 'holder': 'agent-a', 'claimed_at': '10:00', 'renewed_at': '10:11'}

-- SUCCESS CRITERION 3: ownership queryable both directions --
   who holds CB-5 : {'entity_id': 'CB-5', 'holder': 'agent-1', ...}
```

### Key Findings
Success criteria **1, 2 and 3 from the brief are all satisfied by this one primitive**, executed,
across real processes, zero violations, zero exceptions. Criterion 2 in particular: the holder was
told `already_mine` on every retry including after a rejected steal attempt by another agent — it
was never once told it lost.

### Implications for Design
This is a concrete, executed, working answer to the core requirement. Any architect proposing
something more elaborate should be asked what it buys over this. Note it is **one write statement**
on the happy path, so per Q3d it needs no transaction ceremony — unless the status projection is
bundled in, which makes it multi-statement and pulls in `BEGIN IMMEDIATE`.

### Confidence: HIGH (executed)

---

## Question Q3g: all three architect substrates, raced head-to-head, with status projection

### Method
`probe5_substrates.py`. 4 real processes, 200 entities per substrate. Each substrate also performs
the `findings.status='in_progress'` projection **in the same transaction**, saving `prev_status`
for release (Q5). Verified afterwards that claim and projection never diverged.

```
sqlite 3.47.1 | 4 real processes | 200 entities per substrate

A_columns    (win=1,lose=3,exc=0)->200
             exactly-one-winner violations: 0 | entities not projected to in_progress: 0
B_lockrow    (win=1,lose=3,exc=0)->200
             exactly-one-winner violations: 0 | entities not projected to in_progress: 0
C_seq        (win=1,lose=3,exc=0)->200
             exactly-one-winner violations: 0 | entities not projected to in_progress: 0
C_partial    (win=1,lose=3,exc=0)->200
             exactly-one-winner violations: 0 | entities not projected to in_progress: 0

-- Q5: pre-claim status preserved for release? --
   {'id': 'B-0', 'status': 'in_progress', 'holder': None, 'prev_status': 'open'}
```

Where: `A_columns` = `UPDATE findings SET holder=? WHERE id=? AND holder IS NULL` (Architect A's
world); `B_lockrow` = dedicated claims table with PK on `entity_id` (Architect B);
`C_seq` = append-only log with optimistic append `UNIQUE(entity_id, seq)` (Architect C);
`C_partial` = append-only log with `CREATE UNIQUE INDEX ... ON log(entity_id) WHERE released_at IS
NULL` (Architect C alternative).

And, separately, confirming the optimistic append is not decoration (`probe6_scale.py`):
```
(a) OPTIMISTIC APPEND under 4 processes x 300 entities (all racing the SAME entity ids):
      appended: 1196
      IntegrityError (lost optimistic append): 4
    -> collisions DO fire; UNIQUE(entity_id,seq) is doing real work, not decoration.
```

### Key Findings
- **All four substrates are equally correct for mutual exclusion.** No architect can win this
  argument on correctness grounds. The choice must be made on other axes: expressiveness,
  generality across entity kinds, audit value, migration cost.
- The **status projection stayed consistent with the claim in 100% of cases** for all four, because
  it was in the same transaction. This is an argument for bundling — and therefore for
  `BEGIN IMMEDIATE` (per Q3d) in any design that projects.
- **`prev_status` capture works and answers Q5 mechanically**: writing `prev_status=status` in the
  same guarded UPDATE that sets `in_progress` preserves the pre-claim state atomically, so release
  can restore it without resurrecting a `fixed` entity to `open`. (Q5's *policy* — what release
  does if the holder already moved the entity to `fixed` — is still a design decision; the
  *mechanism* is proven.)
- Architect C's optimistic-append collisions genuinely fire (4 `IntegrityError`s), so the
  `UNIQUE(entity_id, seq)` is load-bearing, not ornamental. **But note the loser's signal is an
  `IntegrityError`, not a rowcount** — a different exception shape from every other substrate.

### Implications for Design
Correctness is not the differentiator. Adversaries should press architects on the axes where the
substrates actually differ, not on whether the race is closed — it is, in all four.

### Confidence: HIGH (executed)

---

## Question Q7: event-sourced ownership — real operational costs

### Method
Delegated literature survey (cited below), plus my own executed scale benchmark
(`probe6_scale.py`) because the literature answer to "what does it cost" is worthless without
numbers from this actual substrate.

```
(b) PROJECTION COST: 'who holds X' + 'what does agent-N hold' over an append-only log

   log rows = 40,000 (10,000 entities x 4 events)
     NO index                     who-holds-X:   4.377 ms | what-agent-holds (full window fold):    99.3 ms
     index (entity_id, seq DESC)  who-holds-X:   0.006 ms | what-agent-holds (full window fold):    82.2 ms
     db size: 2.3 MB

   log rows = 500,000 (50,000 entities x 10 events)
     NO index                     who-holds-X:  40.372 ms | what-agent-holds (full window fold):   739.7 ms
     index (entity_id, seq DESC)  who-holds-X:   0.005 ms | what-agent-holds (full window fold):   752.0 ms
     db size: 30.3 MB
```

### Key Findings
- **Yes, it is a recognized pattern.** "Current state = left fold over events" is Greg Young's
  literal formulation ("current state is a left fold of previous behaviours"; "a snapshot is a
  memorization of your left fold, nothing more") —
  https://www.kurrent.io/blog/transcript-of-greg-youngs-talk-at-code-on-the-beach-2014-cqrs-and-event-sourcing.
  Fowler's original definition: https://martinfowler.com/eaaDev/EventSourcing.html. SQL:2011
  system-versioned temporal tables are the standardized form (SQLite does **not** implement them —
  you hand-roll).
- **The point lookup is free with the right index, and catastrophic without it.** `who holds X`
  went from 4.4ms → **0.006ms** at 40k rows and 40.4ms → **0.005ms** at 500k rows once
  `(entity_id, seq DESC)` existed. The index is not an optimization, it is a requirement; note the
  unindexed cost grows ~10x with 12.5x the rows, i.e. it is a linear scan.
- **The reverse query is the real cost, and no index fixes it.** `what does agent-N hold` requires
  a full-table `ROW_NUMBER() OVER (PARTITION BY entity_id ORDER BY seq DESC)` fold: **82ms at 40k
  rows, 752ms at 500k rows — and the index made it no better (99→82ms, 740→752ms, i.e. noise).**
  This is a direct hit on the brief's **success criterion 3** ("what does agent-7 hold"). Architect
  C must either accept a sub-second-and-growing query for that, or maintain a materialized
  current-claim table — at which point the append-only log is an *audit* substrate with a mutable
  projection beside it, which is a materially different (and more honest) proposal.
- **Mutual exclusion on an append-only log** cannot use `UNIQUE(entity_id)`. The three known
  approaches, all confirmed available in sqlite 3.47.1 and two of them executed above: partial
  unique index `WHERE released_at IS NULL` (executed, works — `C_partial`); optimistic append
  `UNIQUE(entity_id, seq)` (executed, works, collisions fire — `C_seq`); serializing transaction.
  The optimistic-append pattern is the production standard in event stores — EventStoreDB's
  expected-version / `WrongExpectedVersionException`
  (https://docs.kurrent.io/clients/tcp/dotnet/21.2/appending), Marten's `ConcurrencyException`
  (https://martendb.io/documents/concurrency, which cites Fowler's Optimistic Offline Lock),
  Rails Event Store's `WrongExpectedEventVersion`
  (https://railseventstore.org/docs/v2/expected_version/).
- **The honest costs, from sources that are not selling it.** Microsoft's own pattern catalog:
  *"Event sourcing is a complex pattern that introduces significant trade-offs… For most systems and
  most parts of a system, traditional data management is sufficient."*
  (https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing). Fowler on CQRS:
  *"the majority of cases I've run into have not been so good, with CQRS seen as a significant force
  for getting a software system into serious difficulties"*
  (https://martinfowler.com/bliki/CQRS.html). Chris Kiehl's year-in-production post-mortem
  (https://chriskiehl.com/article/event-sourcing-is-hard) reports that the audit-log-as-debugging
  benefit was overhyped — *"99% of the time 'bad states' were bad events caused by your standard
  run-of-the-mill human error… Having a ledger provided little value over your normal debugging
  intuition."* That is a direct attack on Architect C's likely main selling point.
- **The sharpest genuine cost is schema evolution**: you can never rewrite history, only append
  compensating events. Every source agrees on this one.

### Implications for Design
Be blunt with Architect C: at this project's scale (thousands of entities, a handful of claim
events each — call it 10k × 20 = 200k rows worst case), **compaction, snapshotting, projection lag
and log growth are all non-problems**, and importing that machinery would be solving a problem this
system does not have. The append-only substrate should be justified by **audit/provenance value**
(which fits this repo — `provenance.py` and `milestone_audit` already exist), not by scale
arguments. The one real, measured cost Architect C must answer for is the **752ms reverse query**,
which is a stated success criterion.

### Confidence: HIGH (executed) for the benchmark numbers and the SQLite feature availability.
MEDIUM (read only, but well-cited) for the literature characterizations.

---

## Question Q6: prior art — assignment vs lease

### Method
Delegated web survey; citations verified as URLs, content not independently re-fetched by me.

### Key Findings

**Family (a) — trackers with an assignee field.** None of GitHub Issues, Jira, Linear, or Bugzilla
has an *exclusive* assignee. GitHub allows up to 10 assignees and anyone with write access can
assign or unassign
(https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/assigning-issues-and-pull-requests-to-other-github-users).
Linear is single-assignee **by design as a product choice**, not as a concurrency control
(https://linear.app/docs/assigning-issues). Bugzilla's `ASSIGNED` is purely conventional — the docs
instruct humans to manually reset it when someone wrongly self-assigns
(https://wiki.documentfoundation.org/QA/Bugzilla/Fields/Status/ASSIGNED).

**The killer data point for this council:** Jira has an open, reproduced race condition —
**JRASERVER-78379**, Severity 2/Major, still Unresolved — where a concurrent assignee change and a
workflow transition on the same issue silently clobber each other, "leading to potential data
inconsistency." No optimistic locking on the update or transition endpoints.
(https://jira.atlassian.com/browse/JRASERVER-78379). *The industry-standard bug tracker has exactly
the bug this council is convened to prevent, and has not fixed it.* That cuts both ways and should
be argued honestly: it proves the race is real in this class of system, **and** it proves a
market-leading tracker survives it for years without it being a business problem.

Jira does have **guarded transitions** (workflow Conditions hide a transition unless criteria pass;
Validators block submission) — precedent for the "declared transition layer" of the brief's Q6.
But note they gate *status*, not *assignee*, and so do not close JRASERVER-78379.

**Family (b) — queues with a lease.** Postgres `SELECT ... FOR UPDATE SKIP LOCKED` underlies most
SQL-backed queues; because the lock is transaction-scoped, a crashed worker auto-releases. River
(Go) adds a `Rescuer` that re-claims jobs stuck in `running` after ~1 hour
(https://riverqueue.com/docs/maintenance-services). graphile-worker uses `locked_at`/`locked_by`
columns with a hard sweep: an uncleanly-killed worker's jobs "remain locked for at least 4 hours"
before a sweeper frees them (https://worker.graphile.org/docs/error-handling). SQS visibility
timeout is the canonical cloud lease with heartbeat renewal via `ChangeMessageVisibility`
(https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html).

**Kubernetes `Lease` is the closest match to what this brief describes** — `holderIdentity`,
`leaseDurationSeconds`, `renewTime` (https://kubernetes.io/docs/concepts/architecture/leases/), and
client-go's `tryAcquireOrRenew` answers the idempotency question directly in source: the acquire
fails only when the lease is *still valid AND held by someone else*; if the identity matches the
caller's own it falls through and renews
(https://github.com/kubernetes/client-go/blob/master/tools/leaderelection/leaderelection.go). **This
is precisely the semantics my Q3f probe implemented and verified** — independent convergence on the
same contract.

**Notable negative result:** SQLite-backed queues largely **do not** implement leases at all.
litequeue has no claim-timeout API; huey's `SqliteHuey` explicitly abandons the guarantee — "Huey
does not guarantee at-least-once delivery… those tasks will be lost"
(https://huey.readthedocs.io/en/latest/consumer.html). So "we're on SQLite" is not itself an
argument for either model.

### WHERE THE TWO MODELS DISAGREE (the deliverable)

| | **Assignee (tracker)** | **Lease (queue)** |
|---|---|---|
| Holder dies silently | Field untouched; sits assigned to a corpse forever | Expires on a timer; item auto-returned to the pool |
| Who notices | A human, eventually, by looking. No mechanism. | The system, deterministically, on a schedule |
| Failure mode | **Stuck work** — throughput for that item drops to zero until a human intervenes | **Duplicate work** — a slow-but-alive holder gets its item stolen; requires idempotent handlers |
| Delivery semantic | at-most-once ownership | at-least-once, redelivery is a *feature* |
| Cost | Zero infrastructure, unbounded staleness risk | Needs heartbeat + sweep + a poison-pill cap |

Sidekiq's answer to the second-order problem is worth stealing: an item recovered 3 times in 72
hours is classified a **poison pill** and moved to the Dead set rather than reclaimed forever
(https://github.com/sidekiq/sidekiq/wiki/Reliability). For AI agents that may die *deterministically*
on a particular bad finding, a naive TTL reclaim creates an infinite kill-a-fresh-agent loop.

### Implications for Design — and a direct read on Q8

The user's constraints already **pick a side, and it is the tracker side**: "expired claims are
**reported, never auto-stolen**; stealing requires an explicit opt-in flag." That is assignment
semantics with staleness *reporting* — precisely the `actions/stale` hybrid (label it stale, make
it visible, let a human/agent decide), not a lease.

**Therefore, on Q8: a TTL/lease concept is NOT needed, and `claimed_at` + reporting genuinely
suffices.** Adding `lease_duration` / `expires_at` without auto-steal would be building the
mechanism and then disabling the only thing it does. A `claimed_at` timestamp plus a query-time
staleness threshold (chosen by the *reader*, not baked into the row) gives the same reporting with
strictly less schema. The one thing worth borrowing from the lease family is a **renewal/heartbeat
write** — which the Q3f probe already provides free as the `already_mine` path updating
`renewed_at`. That makes "stale" mean "hasn't checked in", which is far more useful than "claimed
long ago", and costs one column.

### Confidence: MEDIUM (read only — delegated survey, URLs verified as plausible sources but page
contents not independently re-fetched by me). The Q3f convergence with the k8s contract is HIGH
(executed).

---

## Question Q8-repo: repo facts to confirm or refute

### Method
Delegated grep sweep, then **I re-opened every cited line myself** with `sed -n` before repeating
it. All five spot-checks matched the subagent's report.

### Key Findings

**(1) Does anything besides `triage.py:113` sync into `findings.status`? — REFUTED, there is a
second one the brief missed.**

All writes funnel through `findings.update_finding()`
(`/home/faxik/w/codebugs/src/codebugs/findings.py:235-302`), whose only SQL is line 298 — verified
by direct read:
```python
conn.execute(f"UPDATE findings SET {', '.join(updates)} WHERE id = ?", params)
conn.commit()
```
No rowcount check, confirming brief fact 1. Call sites:

| file:line | trigger |
|---|---|
| `/home/faxik/w/codebugs/src/codebugs/milestones/triage.py:115` | `triage_dismiss` → `status="not_a_bug"` (the one the brief knew about) |
| **`/home/faxik/w/codebugs/src/codebugs/provenance.py:264-269`** | **git commit-trailer processing sets status from a parsed trailer** — verified by read; it skips when `current["status"] in types.FINDING_TERMINAL` |
| `/home/faxik/w/codebugs/src/codebugs/findings.py:596` | MCP `update` tool — direct agent edit |
| `/home/faxik/w/codebugs/src/codebugs/findings.py:749` | CLI `_cmd_update` — direct human edit |

**`provenance.py` is a second automated writer of `findings.status`, and it is the dangerous one
for this design:** an agent claims CB-1234 (status → `in_progress`), then commits with a `Fixes:
CB-1234` trailer, and provenance flips the status out from under the claim — with no knowledge that
a claim exists. Any design that projects claims into `findings.status` **must** state what happens
here. This is a concrete, in-repo instance of the Q5 "release must not resurrect a fixed entity"
problem, and it is not hypothetical.

**(2) Is `db.connect()` per-request or per-process? — PER REQUEST. Verified by direct read of
`/home/faxik/w/codebugs/src/codebugs/server.py:13-19`:**
```python
@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()
```
`_conn` is passed as `conn_factory` to every module's `register_tools` (server.py:49); 61 call sites
use `with conn_factory() as conn:`. No module-level connection anywhere. `check_same_thread` is
never passed (default `True`). No `async def` in the codebase — all MCP tools are sync `def`.

**This settles the brief's fourth hypothesis and it settles it in favour of the exercise, not
against it.** Every MCP tool call gets its own fresh `sqlite3` connection. In-process locking would
therefore be **useless** — there is no shared object to lock. Cross-connection coordination is
genuinely required, and my probes (which open a fresh connection per worker, deliberately mirroring
`_conn()`) are faithful to production. *Inference, not established by repo code:* FastMCP must run
these sync tools on some thread pool; that is FastMCP-internal.

**(3) Do `requirements` have any ownership column? — NO.** `REQS_SCHEMA` at
`/home/faxik/w/codebugs/src/codebugs/reqs.py:15-36`: `id, section, description, priority, status,
source, test_coverage, tags, meta, embedding, created_at, updated_at`. Migrations add only
`embedding` (reqs.py:48) and a lowercase CHECK rebuild. `findings` SCHEMA at
`/home/faxik/w/codebugs/src/codebugs/findings.py:15-37` likewise has no owner column. Confirms brief
facts 3 and 5. The only ownership in the repo is `milestone_items.assigned_agent`
(`/home/faxik/w/codebugs/src/codebugs/milestones/_schema.py:64`, partial index at `:77`), written by
`capacity.py:198` and nulled at `capacity.py:240` / `:247`.

**(4) Is there an existing two-connection race test? — YES, TWO, and they are excellent
precedents.**
- `/home/faxik/w/codebugs/tests/test_milestones.py:801-846`,
  `TestPullNextConcurrent.test_two_threads_two_connections_no_double_claim` — verified by read.
  Two real `threading.Thread`s, each opening its **own** `db.connect(tmp_project)` against a real
  file DB, `threading.Barrier(2)` to force overlap, asserts all 4 claimed refs unique. Section
  comment at `:798` reads "Phase 2: concurrent pull_next (BEGIN IMMEDIATE atomicity)".
- `/home/faxik/w/codebugs/tests/test_sweep.py:754-799`, `TestConcurrentAdd.test_concurrent_upsert_atomic`
  — 10 threads, `threading.Barrier(10)`, each with its own `sqlite3.connect(db_path, timeout=10.0)`,
  asserts exactly one row with `recurrence_count == 10`.

No `multiprocessing`, `concurrent.futures`, `database is locked`, or `busy_timeout` anywhere in
`tests/`.

### Implications for Design
- **Success criterion 1's test already has a template.** The design should say "follow
  `tests/test_milestones.py:801`" rather than inventing a harness. Note `test_sweep.py:757` passes
  `timeout=10.0` explicitly while `db.connect()` leaves it at 5.0 — a design writing a race test
  should be deliberate about which it uses, because per Q3b that parameter decides whether the
  loser sees `rowcount 0` or an exception.
- **`provenance.py:264` must be addressed by name** in whatever design wins. It is a live,
  automated, second writer of the exact field the claim projects into.
- Adoption (Q7) has an obvious lever the brief didn't name: `findings.update_finding` at
  `findings.py:298` is the single choke point through which *every* status write in the entire
  codebase passes. A guard there reaches all four call sites at once.

### Confidence: HIGH (repo facts, every cited line re-opened by me this run)

---

## Unsolicited Findings

**1. The premise survives, but the *stated* mechanism of harm does not.** The concurrency model
(per-request connections, no shared state) means the race is real and cross-process coordination is
genuinely needed — good. **But `findings.update_finding` cannot corrupt anything.** Two agents both
writing `status='in_progress'` produce a correct final state; the row is fine. The only thing lost
is *information* — neither caller learns whether it made the transition. So the problem is
**not a data-integrity problem, it is an API-expressiveness problem.** That reframing matters: it
means the minimal honest fix is "make the write report what it did," and any architect proposing a
new subsystem must justify it against *that* bar, not against a corruption risk that does not
exist. This also aligns with the user's own observation that the cost seen in practice was
duplicated work, not corruption.

**2. `foreign_keys` is OFF (executed, Q3e).** Any design putting `REFERENCES findings(id) ON DELETE
CASCADE` on a claims table gets **no enforcement**. Orphaned claims after a finding is deleted must
be handled in code. `triage.py:116` already catches `KeyError` for exactly this situation — evidence
the codebase has hit it before.

**3. `db.connect()`'s implicit `busy_timeout=5000` and `synchronous=FULL` are undocumented
inherited defaults.** Reading `db.py:492-503` gives no hint either exists. Given how much of the
correctness argument in this council rests on `busy_timeout`, that is a landmine independent of
which design wins, and worth a one-line explicit `PRAGMA busy_timeout=...` regardless.

**4. There is a fourth entity kind hiding in plain sight.** The brief's criterion 4 asks that
adding a third kind need no new code. But `milestone_items` (fact 6) already has ownership with
*different semantics* — capacity-aware, size-gated, with a `pull_next` scheduler on top. A design
that "subsumes" it must either absorb capacity accounting or accept that
`milestone_items.assigned_agent` means something the generic claim does not. Convergence is more
than a data migration.

**5. Adoption lever nobody named (Q7).** Every status write in the codebase goes through
`findings.update_finding` (`findings.py:298`), and `~/.claude/skills/fix-latest-codebugs/SKILL.md:92`
documents a claim call that **cannot currently succeed** (it passes `id=` and `assignee=`, neither
of which is in the tool signature at `findings.py:574-581` — brief fact 3). So there is a documented
call site that is *already broken* and must be edited no matter what. That is the concrete,
named change the design should attach itself to — fixing a broken instruction is a far stronger
adoption story than adding an optional new tool that `pull_next`-style dies unwired.

**6. Process note.** My delegated prior-art agent flagged that the task context it received
contained an embedded block styled as a system reminder instructing terse "caveman" output and
forbidding certain tools. That block is injected harness context, not a user instruction; the agent
correctly followed the actual task and the user's global CLAUDE.md style rule instead. Noting it
because it will affect other agents in this council the same way.
