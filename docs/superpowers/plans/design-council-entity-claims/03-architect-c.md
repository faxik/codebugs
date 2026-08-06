# Architect C — Derived State / Process First

**Lever:** current ownership may not be stored as directly-mutable state. It must be derived from
an append-only record. A derived cache is permitted only if it is explicitly a rebuildable
projection with a stated rebuild procedure.

**Position in one line:** the repo already writes an append-only ownership-shaped log
(`milestone_audit`) and already has a declarative transition validator (`sweep._validate_transition`)
— it just never joined them up. The append-only substrate is not exotic here; it is the shape this
codebase keeps re-deriving by hand.

---

## 0. Two results established this run, before any proposal

These are load-bearing for everything below. Both were produced this run.

### 0.1 C9 is refuted as stated. It measures a query plan, not a substrate.

The brief hands me C9 as my hardest problem: *"what does agent-7 hold" is a full window fold that
no index helps — 82 ms at 40k rows, 752 ms at 500k.* I reproduced the research fixture and raced
three formulations of the identical question. Probe:
`/tmp/claude-1000/-home-faxik-w-codebugs/349b1511-db4b-4ac3-a327-b85cb19885ba/scratchpad/probeC9.py`,
executed with the repo's own `uv run python`.

```
=== 40k: 40,000 log rows / 10,000 entities / 8 actors ===
  -- NO index --
     who-holds-X             :     2.824 ms   (1 rows)
     agent-holds WINDOW fold :    54.456 ms   (1250 rows)
     agent-holds MAX(seq)    :  4634.041 ms   (1250 rows)
     agent-holds ANTI-JOIN   :  1563.582 ms   (1250 rows)
  -- WITH indexes --
     who-holds-X             :     0.003 ms   (1 rows)
     agent-holds WINDOW fold :    55.788 ms   (1250 rows)
     agent-holds MAX(seq)    :     4.897 ms   (1250 rows)
     agent-holds ANTI-JOIN   :     4.428 ms   (1250 rows)

=== 500k: 500,000 log rows / 50,000 entities / 8 actors ===
  -- WITH indexes --
     who-holds-X             :     0.003 ms   (1 rows)
     agent-holds WINDOW fold :   699.712 ms   (6250 rows)
     agent-holds MAX(seq)    :    63.238 ms   (6250 rows)
     agent-holds ANTI-JOIN   :    58.524 ms   (6250 rows)

=== 500k_realistic: 500,000 log rows / 50,000 entities / 200 actors ===
  -- WITH indexes --
     who-holds-X             :     0.003 ms   (1 rows)
     agent-holds WINDOW fold :   689.626 ms   (250 rows)
     agent-holds MAX(seq)    :     3.221 ms   (250 rows)
     agent-holds ANTI-JOIN   :     2.970 ms   (250 rows)
```

`EXPLAIN QUERY PLAN` says exactly why, and the two plans are structurally different:

```
-- ANTI-JOIN, indexed
SEARCH e USING INDEX idx_ee_actor_seq (actor=?)
CORRELATED SCALAR SUBQUERY 1
SEARCH l USING COVERING INDEX idx_ee_entity_seq (entity_id=? AND seq>?)

-- WINDOW fold, indexed
CO-ROUTINE (subquery-1)
CO-ROUTINE (subquery-3)
SCAN entity_events USING INDEX idx_ee_entity_seq      <-- SCAN, not SEARCH
SCAN (subquery-3)
SCAN (subquery-1)
```

The window formulation is `O(n)` **by construction** — it computes the head event for *every*
entity in the table and then discards all but one actor's. No index can help, because the query
asked for the whole fold. The anti-join asks a different question: *of the events this actor
emitted, which are still the head?* That is a `SEARCH` on `(actor, seq DESC)` producing `k`
candidates, each verified by one covering-index probe on `(entity_id, seq DESC)`.

**Cost is `O(k · log n)` where `k` is the number of claim/renew events that actor has ever emitted
— not `O(n)`.** At 500k rows: 699.7 ms → 58.5 ms (12×) with 8 actors; 689.6 ms → **2.970 ms
(232×)** with 200 actors, which is the shape a bug tracker actually has (many agents, each holding
a handful).

Correctness of the anti-join was verified by construction, not eyeballed: the fixture places a live
`claim` at the head of every even-numbered entity with actor `agent-((e+k) % n_actors)`. The
predicted row counts are 10000/8 = 1250 and 50000/8 = 6250, and the query returned exactly 1250 and
6250. Renew events are handled correctly for free — a `claim` followed by the same actor's `renew`
has a later event, so the claim is excluded and the renew is returned: one row per held entity, not
two.

**What I concede, honestly, and it is not nothing:**

- **The index is mandatory, not an optimization.** Unindexed, the anti-join is 1563 ms at 40k rows
  (quadratic) and the correlated `MAX(seq)` variant is 4634 ms. A design that ships the log without
  both indexes is broken. This is a real fragility that a mutable owner column does not have.
- **Cost scales with `k`, an actor's lifetime claim count, and `k` only ever grows.** An agent
  identity that claims 50,000 things over two years pays 50,000 index probes. This is bounded in
  practice by three things, in increasing order of effort: agent ids in this system are ephemeral
  per-worktree strings, not stable identities; the query accepts a `seq > :watermark` floor; and
  Proposal 1 exists precisely to buy `O(1)` if it ever bites. **It is a real unbounded-growth term
  and I am not going to pretend otherwise.**
- **`who holds X` is 0.003 ms**, confirming the research number. That direction was never in doubt.

So success criterion 3 is met at 2.97 ms on a 500k-row / 50k-entity log — roughly 100× larger than
this repo will plausibly ever be — and I no longer need to argue "the audit value is worth 752 ms."
I would have argued that; I no longer have to.

### 0.2 A verified correction to the brief: `_SAFE_IDENT` is dead code.

Brief fact 4 states that `entities._read()` "enforces a per-kind `readable_cols` allowlist and a
`_SAFE_IDENT` regex on interpolated identifiers." The first half is true; **the second half is
false.** `_SAFE_IDENT` is defined at `entities.py:20` with a comment asserting the guarantee —

```python
19  # Every interpolated SQL identifier (table / sort_col / readable column) must match this.
20  _SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
```

— and `_read()` at `entities.py:80-89` never references it:

```python
80      def _read(self, conn: sqlite3.Connection, col: str) -> Any | None:
83          if col not in self.kind.readable_cols:
84              raise ValueError(f"Column {col!r} is not readable for kind {self.kind.name!r}")
85          row = conn.execute(
86              f"SELECT {col} FROM {self.kind.table} WHERE id = ?",  # noqa: S608 (identifiers allowlisted)
87              (self.id,),
88          ).fetchone()
```

The `readable_cols` frozenset carries the whole guarantee; `self.kind.table` is interpolated with no
check at all (safe today only because it is a frozen constant). A delegated grep found zero other
occurrences in the file. This matters to me because all three of my proposals interpolate a table
name to write the status projection, and I intend to make that constant earn its comment rather
than add a second unchecked interpolation site. Downstream reviewers should not repeat the brief's
claim as fact.

### 0.3 A second, minor correction: `REQUIREMENT_STATUSES` is at `types.py:39`, not `:40`.

Brief fact 5 cites `src/codebugs/types.py:40`. Read this run, line 40 is blank and the tuple is on
line 39:

```python
38  # --- Requirement statuses ---
39  REQUIREMENT_STATUSES = ("planned", "partial", "implemented", "verified", "superseded", "obsolete")
40
41  REQUIREMENT_TERMINAL = frozenset({"implemented", "verified", "superseded", "obsolete"})
```

The *substance* of fact 5 is entirely correct — requirements have no in-progress status. Only the
line number is off by one. Flagged so the adversary and judge cite the right line.

---

## 1. The declaration, shared by all three proposals

All three proposals extend the existing descriptor at `entities.py:23-33` rather than inventing a
parallel registry. Current definition, read this run:

```python
23  @dataclass(frozen=True)
24  class EntityKind:
27      name: str  # == blockers item_type, e.g. "finding"
28      table: str  # frozen-constant identifier, never caller input
29      id_pattern: re.Pattern[str]
30      terminal: frozenset[str]
31      sort_col: str  # deferred-query ordering column
32      result_key: str  # JSON envelope key
33      readable_cols: frozenset[str]  # per-kind allowlist for field() reads
```

Two fields are added, both with defaults so no existing construction site breaks:

```python
    busy_status: str | None = None   # status to project a live claim into; None = no projection
    claimable: bool = True           # may this kind be claimed at all
```

- `finding` (`entities.py:37-45`) declares `busy_status="in_progress"`.
- `requirement` (`entities.py:46-54`) declares `busy_status=None`.

**This is the whole answer to success criterion 5, and it is a two-word answer: projection is
optional per kind, because the ledger — not the status column — is the ownership record.**
Requirements get full ownership, full `who holds` / `what does agent-7 hold`, full staleness
reporting, and zero schema change to `requirements`. `REQUIREMENT_STATUSES` (`types.py:39`) is not
touched, and the `_migrate_to_lowercase` table-rebuild machinery (`reqs.py:53-95`) is not disturbed.

Compare the alternative — adding `in_progress` to `REQUIREMENT_STATUSES` — which would require a
second full `requirements` table rebuild, would put a status in the enum that no `reqs_query`
consumer expects, and would make kind #3 face the same question again. Declaring the projection
optional makes kind #3 free.

**Success criterion 4** is then literally satisfied: a third kind is one `EntityKind(...)` entry in
the `ENTITY_KINDS` tuple (`entities.py:36-55`) declaring `busy_status` and `claimable`. Zero
ownership code. The ledger is keyed on an opaque `entity_id` and a `kind` string; it has no
per-kind branches.

**Q2 — does the projection make `entities.py` read/write?** No. `entities.py` stays the declaration
+ sanctioned-read module (113 lines, zero `INSERT`/`UPDATE`/`DELETE`, zero `register_*` calls —
verified). The write lives in a new sibling `claims.py` that imports `entities` publicly, exactly as
`findings.py:12` and `reqs.py:11` already do (`from codebugs import db, entities`). To avoid a
second unchecked interpolation site, `_SAFE_IDENT` is promoted to public `entities.SAFE_IDENT` and
actually used — by `claims.py` on `kind.table` / `kind.busy_status` before interpolation, and (a
one-line boy-scout fix) by `_read()` itself. Dead constant becomes load-bearing.

---

## 2. Outcome vocabulary, shared by all three

Five outcomes. Three from the user's constraint, the fourth mandated by C4, the fifth falling out of
the declaration.

| outcome | meaning | payload |
|---|---|---|
| `claimed` | you now hold it; head was free or released | `holder`, `claimed_at`, `seq`, `projected` (bool) |
| `already_mine` | you already held it; a `renew` was appended | `holder`, `claimed_at`, `renewed_at`, `seq` |
| `held_by_other` | nothing appended; someone else is at the head | `holder`, `claimed_at`, `age_seconds`, `stale` (bool, reader threshold) |
| `undetermined` | **C4.** `sqlite3.OperationalError` matching `database is locked` / `database is busy`. State unknown. | `retry_after_ms` (jittered), `attempts` |
| `refused` | the declaration forbids it: `claimable=False`, or `EntityRef.is_resolved(conn)` is true (`entities.py:100-103`) | `reason`, `status` |

**On C4 specifically.** The research established that the clean `rowcount == 0` loss is a courtesy
of `busy_timeout=5000`, which `db.connect()` (`db.py:492-503`) inherits by accident from
`sqlite3.connect(timeout=5.0)` and never mentions. All three proposals therefore:

1. Set `PRAGMA busy_timeout` **explicitly** in `claims.ensure_schema()` rather than inheriting it,
   so a reader of the code can see the contract. (`ensure_schema` runs on every `connect()` via
   `db._resolved_order()` at `db.py:499-500`, so this reaches every connection without touching
   `db.connect()` itself.)
2. Catch `sqlite3.OperationalError`, match the message, and return `undetermined` rather than
   letting it propagate. This is deliberate divergence from the CLAUDE.md rule that MCP tools let
   exceptions propagate to FastMCP: a lock contention is a *retryable outcome of the protocol*, not
   an error, and rendering it as an exception would push retry logic into every agent prompt.
   `refused` and genuinely-invalid input still raise.
3. Never auto-retry `undetermined` internally more than a bounded 2 attempts. Beyond that the caller
   decides — an agent that has been waiting 10 s should reconsider, not spin.

**On C2, explicitly.** I am *not* claiming my substrate is safer. The research settled it: all four
substrates produced exactly one winner, 200/200 trials, 4 OS processes. I claim three other things,
and the council should hold me to these and not to safety:

- **Expressiveness.** My claim path decides its outcome from the *content* of the head event, not
  from a `rowcount`. That is what makes `claimed` vs `already_mine` a natural distinction rather
  than a second query, and it is what lets `held_by_other` name the holder and their age in the same
  breath.
- **Audit value at zero marginal cost.** The claim *is* the audit record. Every competing substrate
  needs a second write to get history, and the repo already pays exactly that second write in 12
  places (`_spine.py:_audit`, called from `foundation.py`, `closegate.py`, `capacity.py`,
  `triage.py`).
- **The C1 answer** (below), which no mutable substrate can reproduce.

---

## 3. Q6 — is a declared transition layer justified by present demand? *(I own this question.)*

I looked for real demand rather than reasoning from the user's phrase "seed of a process". Both
sides have genuine evidence.

### The case FOR (stronger than I expected)

**There are 14 hand-rolled "you may not do X because state is Y" rules across four modules**, each
independently invented:

| file:line | rule |
|---|---|
| `merge.py:146-147` | `finish()` requires status `merging` |
| `merge.py:176-177` | `add_claim()` requires an active session |
| `merge.py:223-226` | `merge()` requires status `active` |
| `blockers.py:308-309` | refuse cancel of an already-cancelled blocker |
| `blockers.py:319-322` | `resolve` only valid for `manual` triggers |
| `blockers.py:323-324` | refuse resolve of an already-resolved blocker |
| `sweep.py:286-287` | refuse `add_items` on an archived sweep |
| `sweep.py:460-463` | refuse `mark_items` on an archived item |
| `sweep.py:395-407` | **generic declared-DAG transition guard** |
| `closegate.py:145-146` | streams cannot be closed (absolute, `force` cannot bypass) |
| `closegate.py:155-171` | refuse close on unfinished items unless `force` |
| `closegate.py:164-171` | refuse close on branch-only items unless `force` |
| `closegate.py:173-177` | refuse close on blocker-gated items unless `force` |
| `capacity.py:110-130` | `_eligibility_failure` — five bundled sub-checks |

**And one of them is already the abstraction.** `sweep._validate_transition`, read this run at
`sweep.py:395-407`:

```python
395  def _validate_transition(
396      transitions: dict[str, list[str]] | None, src: str, dst: str
397  ) -> None:
398      if transitions is None:
399          return
400      if src == dst:
401          return  # idempotent
402      allowed = transitions.get(src, [])
403      if dst not in allowed:
404          raise ValueError(
405              f"Transition not allowed: {src!r} -> {dst!r}. "
406              f"Allowed from {src!r}: {allowed}"
407          )
```

It is driven by a declared JSON DAG stored in `codesweep_sweeps.transitions` (`sweep.py:34`, a
nullable `TEXT` column added by migration via `_SWEEP_NEW_COLS` at `sweep.py:72-76`), validated at
declaration time in `create_sweep` (`sweep.py:178-200`), loaded by `_load_sweep_lifecycle`
(`sweep.py:143-153`), and enforced at exactly one call site, `sweep.py:464`. **Someone in this repo
already built the declared-transition layer, proved it works, and it stayed local to codesweep while
three other modules hand-rolled their own gates.** That is the strongest possible present-demand
evidence: not a hypothetical need, an abstraction that exists and failed to spread.

Supporting: `EntityKind.terminal` (`entities.py:30`, populated at `:41` and `:50`) is already a
per-kind declared state predicate, consumed cross-module — `is_resolved` (`entities.py:100-103`) is
what `blockers` ultimately calls for `entity_resolved` triggers. And `milestone_audit` already has
`from_state` / `to_state` columns (`_schema.py:85-86`): the *schema* of a transition log exists,
written from 12 sites, and is never read for enforcement.

### The case AGAINST (which I find decisive)

**The 14 rules are not the same shape, and the majority are not expressible as a state DAG.**
`_eligibility_failure` (`capacity.py:86-131`, read this run) is the clearest counter-example — of
its five checks, exactly one is a status check:

```python
110      if item["status"] != "open":
111          return f"not open (status={item['status']})"
112      if item["item_kind"] != "external" and has_active_blocker(item["item_ref"]):
113          return "has active blocker"
114      if item["size"] == "large" and not (item.get("acceptance") or "").strip():
115          return "size=large requires acceptance"
116      if (item["size"] == "large"
117              and item["item_kind"] == "bug"
118              and milestone["kind"] == "release"):
...
126      size = item["size"]
127      cap = capacity.get(size, 0)
128      used = held.get(size, 0)
129      if used >= cap:
130          return f"agent capacity for {size} full ({used}/{cap})"
```

The other four are: a cross-domain blocker query, a non-empty-text check on a free-text field, a
referential-existence check against `requirements`, and an arithmetic capacity comparison. A
transition DAG expresses none of them. Encoding them would require a predicate language — and a
predicate language is where "declarative workflow engine" projects go to die.

Three further disqualifiers:

- **The rules disagree about what a violation *means*.** `closegate` raises. `capacity` returns a
  reason string and skips to the next candidate. `provenance.py:261` **silently skips** a terminal
  finding without raising. `foundation.py:270-271` and `triage.py:91-92` early-return idempotently.
  A shared layer would need to support refuse / skip / report / no-op as configurable policy, which
  is more configuration surface than the 14 call sites contain code.
- **`force` + logged reason has no home in a DAG.** `milestone_close` (`closegate.py:144-202`) lets
  every soft rule be overridden with an audited reason (`closegate.py:191-202` writes
  `f"force:{reason}"`), while `kind == 'stream'` (`closegate.py:145-146`) is absolute. A declared
  DAG has no natural expression for "this edge is forbidden but overridable with a note, that one
  is not."
- **C7 is the governing constraint.** The harm is duplicated agent work, not corruption, and the
  race has never been observed firing. A general workflow engine is an enormous multiplier on a
  problem whose measured cost is "two agents eventually noticed and picked the better fix."
  `pull_next` is the local precedent for what happens to correct machinery nobody adopts.

### My position

**A general guarded-transition engine is speculative generality and should not be built now. The
narrowest declaration — per-kind `busy_status` and `claimable` — is justified now, because it is
load-bearing for success criteria 4 and 5 and costs two dataclass fields with defaults.**

The 14 rules are real demand for *something*, but they are demand for a **shared-gates refactor**
with its own value case, its own scope, and its own reviewer — not for a subsystem smuggled in on
the back of a claim primitive. Coupling them doubles the blast radius of a feature whose measured
harm is duplicated work.

What I *do* recommend, at near-zero cost: state in `CLAUDE.md` that `sweep._validate_transition`
(`sweep.py:395-407`) is the reference implementation for declared transitions and the seam any
future unification should lift, rather than a fifth hand-rolled gate. Proposal 3 below exists to
price the alternative honestly, not because I recommend it. **I am arguing against my own most
ambitious proposal, and the council should read that as a real finding rather than modesty.**

---

## 4. C1 — what happens when `provenance.py` fires on a claimed entity

This is where the append-only substrate has an answer nothing else has, and it is the strongest
technical argument in this document.

The writer, read this run — `provenance.resolve_trailers`, `provenance.py:222-272`:

```python
261          if status_input is not None and current["status"] in types.FINDING_TERMINAL:
262              report["skipped"].append(t.cb_id)
263              continue
264          if not dry_run:
265              findings.update_finding(
266                  conn,
267                  t.cb_id,
268                  status=status_input,
269                  append_note=f"{label} by commit {t.sha[:12]} ({t.subject}).",
270              )
```

The damaging sequence is concrete: agent-7 claims CB-1234 (status → `in_progress`), does the work,
commits `Resolves: CB-1234`, and `resolve_trailers` flips the status to `fixed` knowing nothing
about claims. The guard at line 261 does not help — `in_progress` is not in `FINDING_TERMINAL`
(`types.py:36`), so the write proceeds.

**Under a mutable-owner design this is a desync that must be reconciled.** The owner column still
says agent-7 while the status says `fixed`; somebody has to notice and clear it. Reconciliation code
is where the bugs live, and the fix requires `provenance.py` to reach into the ownership store to
clear a field — a cross-module write that CLAUDE.md's module rules are specifically written to
prevent.

**Under an append-only ledger the answer is one line and no reconciliation exists.** Because current
ownership is *derived as "the head event wins"*, a second writer does not have to clear anything — it
only has to **append**. Add to `provenance.py`, inside the existing `if not dry_run:` block after the
`update_finding` call at `:265-270`:

```python
claims.record(
    conn,
    entity_id=t.cb_id,
    verb="resolve",
    actor="provenance",
    from_status=current["status"],
    to_status=status_input,
    ref=t.sha,
    note=f"{label} by commit {t.sha[:12]} ({t.subject}).",
)
```

Consequences, each falling out of the fold rather than being coded:

1. **The claim closes itself.** The `resolve` event is now the head, so `holder(CB-1234)` returns
   free and `holdings(agent-7)` no longer lists it. The anti-join excludes it automatically — the
   agent-7 claim event now has a later event. No sync code, no reconciliation pass, no scheduled job.
2. **The two writers stop racing and start collaborating.** Appends never conflict with each other;
   only the head *interpretation* changes. `provenance` becomes a first-class participant in the
   same log rather than a hazard to it.
3. **Attribution survives.** The ledger records that `provenance` closed agent-7's claim, citing the
   commit SHA. `history(CB-1234)` reads: claimed by agent-7 → resolved by provenance @ abc123. That
   is exactly the provenance story this repo already invests in — and it is strictly more information
   than a cleared owner column can carry.
4. **A subsequent `claim("CB-1234")` returns `refused`, not `claimed`.** The status is now `fixed`,
   which is in `FINDING_TERMINAL` (`types.py:36`), so `EntityRef.is_resolved` (`entities.py:100-103`)
   is true and the declaration refuses. The loop closes without a special case.
5. **`import claims` in `provenance.py` is legal.** It already imports `findings` and calls
   `findings.update_finding` / `findings.get_finding` (`provenance.py:257, 265`); `claims.record` is
   a public function on a domain module. No private reach.

**Honest caveat:** `resolve_trailers` calls `findings.update_finding`, which commits internally
(`findings.py:299`). So the status flip and the ledger append are two transactions, not one, and a
crash between them leaves a `fixed` finding with a stale claim head. That window is real. It is
*self-healing* rather than corrupting — the next `holder()` read sees a claim on a terminal entity
and reports it stale via `is_resolved`, which is exactly criterion 7's mechanism — but a reviewer
should know the atomicity is not free here, and closing it properly means giving `update_finding` a
no-commit path, which is a separate change I am not bundling.

**A second, cheaper option exists and I reject it:** derive "the claim is over" purely at read time
from `is_resolved`, appending nothing. That works for the *display* of staleness but loses the
attribution and leaves the head event lying about the current state, which defeats the point of the
ledger being the record. Append.

---

## 5. Common schema

All three proposals share this table. Column names deliberately echo `milestone_audit`
(`_schema.py:79-92`) — `actor` / `action`→`verb` / `from_state`→`from_status` /
`to_state`→`to_status` / `reason`→`note` / `at` — so that a later convergence is a rename, not a
remodel.

```sql
CREATE TABLE IF NOT EXISTS entity_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    verb        TEXT NOT NULL
                CHECK(verb IN ('claim','renew','release','resolve','steal')),
    actor       TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    ref         TEXT,
    note        TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);

-- BOTH indexes are mandatory, not optimizations. See §0.1: without them the
-- reverse query is quadratic (1563 ms at 40k rows).
CREATE INDEX IF NOT EXISTS idx_ee_entity_seq ON entity_events(entity_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_ee_actor_seq  ON entity_events(actor, seq DESC);
```

No `REFERENCES findings(id)`. **C5 is respected**: `PRAGMA foreign_keys` is 0, so an FK would be
decorative. Orphaned events after a finding is deleted are handled in code — `holder()` returns the
event and marks `entity_missing: true` via `EntityRef.exists` (`entities.py:91-92`), the same
defensive posture `triage.py:116` already takes.

`seq INTEGER PRIMARY KEY AUTOINCREMENT` is the monotone ordering. Note this is deliberately **not**
`UNIQUE(entity_id, seq)` optimistic-append: the research measured that variant losing with an
`IntegrityError` rather than a clean signal, a different exception shape from every other substrate.
I avoid that entire class by making exclusion a `WHERE NOT EXISTS` guard evaluated at write time.

---

## Proposal 1 — **Ledger and Lens**

### Core idea

An append-only `entity_events` log is the sole source of truth, and beside it lives
`claims_current`, a **materialized projection** holding one row per currently-held entity. The
projection is written only inside the same transaction as the append, is never a caller-writable
surface, and can be dropped and rebuilt from the log at any moment by a single fold. This is
CQRS-lite, deliberately at its smallest: one read model, one rebuild function, no eventual
consistency, no projection lag, no snapshotting. It buys `O(1)` on both read paths in exchange for
a cache that can, in principle, drift.

### How it works

**Schema:** the common `entity_events` above, plus

```sql
CREATE TABLE IF NOT EXISTS claims_current (
    entity_id   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    holder      TEXT NOT NULL,
    claimed_at  TEXT NOT NULL,
    renewed_at  TEXT NOT NULL,
    from_status TEXT,
    head_seq    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cc_holder ON claims_current(holder);
```

**This table is a derived cache, not state.** Contract, to be stated in the module docstring and
enforced by a test: `claims_current` is exactly the output of `rebuild_projection()`. It is never
written outside `claims.py`, never read as authority for anything the log disagrees with, and is
`DROP`-able without data loss.

```python
def rebuild_projection(conn: sqlite3.Connection) -> dict[str, Any]:
    """Truncate and re-fold claims_current from entity_events. Pure function of the log.
    Exposed as MCP tool `claims_rebuild` and CLI `codebugs claims rebuild`."""
```

whose body is one statement:

```sql
DELETE FROM claims_current;
INSERT INTO claims_current (entity_id, kind, holder, claimed_at, renewed_at, from_status, head_seq)
SELECT e.entity_id, e.kind, e.actor, e.at, e.at, e.from_status, e.seq
FROM entity_events e
WHERE e.verb IN ('claim','renew')
  AND NOT EXISTS (SELECT 1 FROM entity_events l
                  WHERE l.entity_id = e.entity_id AND l.seq > e.seq);
```

Note the rebuild is the §0.1 anti-join. Measured at 58.5 ms for a whole 500k-row log — a rebuild is
cheaper than a single window-fold read.

**Claim path.** Three statements (append + projection upsert + status projection), therefore
`BEGIN IMMEDIATE`, following the `capacity.py:167-215` save/restore pattern the repo already tests
at `tests/test_milestones.py:801`.

```python
def claim(conn, *, entity_id: str, actor: str, note: str = "",
          steal: bool = False) -> dict[str, Any]:
```

```
saved = conn.isolation_level; conn.isolation_level = None
try:
    conn.execute("BEGIN IMMEDIATE")
    # 1. read head from the projection (O(1), PK lookup)
    # 2. decide outcome: refused / already_mine / held_by_other / claimed
    # 3. append event; upsert claims_current; project status
    conn.commit()
except sqlite3.OperationalError as e:  # C4
    conn.rollback(); return {"outcome": "undetermined", ...}
finally:
    conn.isolation_level = saved
```

Because `BEGIN IMMEDIATE` takes the write lock up front, the read at step 1 is already exclusive —
no guard subquery is needed and no `SQLITE_BUSY_SNAPSHOT` is possible (research CASE 3's trap is
avoided precisely because we never write a plain `BEGIN`).

Claim SQL, step 3:

```sql
INSERT INTO entity_events (entity_id, kind, verb, actor, from_status, to_status, ref, note, at)
VALUES (:eid, :kind, :verb, :actor, :from_status, :to_status, :ref, :note, :now);

INSERT INTO claims_current (entity_id, kind, holder, claimed_at, renewed_at, from_status, head_seq)
VALUES (:eid, :kind, :actor, :now, :now, :from_status, last_insert_rowid())
ON CONFLICT(entity_id) DO UPDATE SET
    holder = excluded.holder, renewed_at = excluded.renewed_at, head_seq = excluded.head_seq;
```

Status projection (findings only; skipped when `busy_status is None`), a compare-and-swap:

```sql
UPDATE findings SET status = :busy, updated_at = :now
WHERE id = :eid AND status = :from_status;
```

`rowcount == 0` means somebody moved the status between our read and our write — we do not retry, we
record `projected: false` in the returned dict and in the event note. The claim still stands; only
the convenience projection was skipped.

**Read path A — who holds CB-1234:**

```sql
SELECT entity_id, kind, holder, claimed_at, renewed_at, head_seq
FROM claims_current WHERE entity_id = ?;
```

O(1) PK lookup.

**Read path B — what does agent-7 hold:**

```sql
SELECT entity_id, kind, claimed_at, renewed_at
FROM claims_current WHERE holder = ? ORDER BY claimed_at DESC;
```

O(rows-held) index seek on `idx_cc_holder`. **Genuinely O(1)-per-result — no `k` term, no growth
with log size.**

**Release:**

```sql
INSERT INTO entity_events (...) VALUES (..., 'release', ...);
DELETE FROM claims_current WHERE entity_id = :eid AND holder = :actor;
UPDATE findings SET status = :restore, updated_at = :now WHERE id = :eid AND status = :busy;
```

The third statement answers **Q5**: `from_status` was captured in the claim event, and the restore
only fires if the status is *still* `busy_status`. If the holder already set `fixed`, the guard fails
with `rowcount == 0` and the entity is left alone. No resurrection. This mechanism was proven in the
research probe (`prev_status` capture, Q3g).

**Outcome vocabulary:** the five in §2.

### Pros

- **Both read paths are O(1).** Success criterion 3 is satisfied with no `k` term and no growth
  argument to defend. This is the only proposal here that is immune to the §0.1 caveat.
- Full audit history retained; `history(entity_id)` is a straight index scan.
- The projection is trivially verifiable: a test asserts `claims_current` equals
  `rebuild_projection()` output after every operation in the suite. Drift is detectable, not
  theoretical.
- `rebuild_projection` doubles as the migration path for Proposal 3 or for a future
  `milestone_items` convergence — the fold already exists.
- Degrades gracefully: if the projection is ever suspect, drop it and rebuild in 58 ms.

### Cons

- **Two tables and a coherence invariant to maintain.** This is the honest cost. Every future writer
  of an event must remember the projection, or add it to a shared `record()` seam and trust it.
- **`claims_current` is a mutable one-row-per-entity table, which is exactly the thing my lever
  forbids as *state*.** It is legal only because it is declared derived and is rebuildable — but a
  skeptical reviewer is entitled to say this is Architect B's design wearing a log as a hat. **I
  think that criticism is largely correct**, and it is the main reason this is not my recommendation.
- More code than Proposal 2 for a performance win that §0.1 shows is not currently needed.
- The `DELETE FROM claims_current` in `rebuild_projection` is a footgun if ever called concurrently
  with a claim; it needs `BEGIN IMMEDIATE` too, and that must be remembered.

### Effort: **L**

New module (~450 lines), two tables, five MCP tools, five CLI handlers, projection-coherence test
matrix, concurrency test, rebuild test, `provenance.py` edit, wiring.

### Risk profile

- **Medium.** The dominant risk is projection drift via a future code path that appends an event
  without updating the cache. Mitigated structurally by funnelling all appends through a single
  private `_append()` and by a coherence test — but mitigation depends on discipline, and this repo
  already has a documented instance of discipline failing (`blockers.py` reaching into private
  cross-module functions).
- Low correctness risk on the claim itself (`BEGIN IMMEDIATE` + write lock, the tested
  `capacity.py:167` pattern).
- Low performance risk.

---

## Proposal 2 — **Fold on Read**

### Core idea

One table. `entity_events`, append-only, two indexes, no cache, no projection table, no rebuild
procedure, no coherence invariant. Current ownership is a *query*, not a stored thing — and §0.1
establishes by measurement that both read paths are fast enough that storing it would be premature.
The claim path is a single guarded `INSERT ... SELECT ... WHERE NOT EXISTS ... RETURNING`, which per
the research needs no transaction ceremony at all. This is the smallest possible honest expression
of "ownership is derived from a record of what happened."

### How it works

**Schema:** exactly the common `entity_events` in §5. Nothing else. No second table.

**Public API** (`src/codebugs/claims.py`; keyword-only after `conn` per CLAUDE.md):

```python
def claim(conn: sqlite3.Connection, *, entity_id: str, actor: str,
          note: str = "", steal: bool = False) -> dict[str, Any]: ...
def release(conn: sqlite3.Connection, *, entity_id: str, actor: str,
            note: str = "", restore_status: bool = True) -> dict[str, Any]: ...
def holder(conn: sqlite3.Connection, *, entity_id: str) -> dict[str, Any] | None: ...
def holdings(conn: sqlite3.Connection, *, actor: str,
             stale_after_hours: float | None = None) -> list[dict[str, Any]]: ...
def stale(conn: sqlite3.Connection, *, older_than_hours: float = 24.0) -> list[dict[str, Any]]: ...
def history(conn: sqlite3.Connection, *, entity_id: str, limit: int = 50) -> list[dict[str, Any]]: ...
def record(conn: sqlite3.Connection, *, entity_id: str, verb: str, actor: str,
           from_status: str | None = None, to_status: str | None = None,
           ref: str | None = None, note: str = "") -> dict[str, Any]: ...
```

`record()` is the raw append seam — the public entry point `provenance.py` and (later)
`capacity.py` call. It is deliberately *not* the claim path: it appends unconditionally, because a
second writer reporting what it already did must never be refused.

**Transaction shape.** Two cases, and the distinction is the design's main subtlety:

- **`busy_status is None`** (requirements, and any future kind that opts out): the claim is **one
  statement**. Per research Q3d, a guarded single write needs no explicit transaction and is
  provably correct for exclusion. No ceremony.
- **`busy_status is not None`** (findings): claim is two writes (event + status projection), so it
  takes `BEGIN IMMEDIATE` via a small shared helper, following `capacity.py:167-215`. **Never a
  plain `BEGIN`** — research CASE 3 shows that pins a read snapshot and produces an unrescuable
  `SQLITE_BUSY_SNAPSHOT`.

**Claim path, exact SQL.**

Step 1 — read the head. No transaction, deliberately. Per research CASE 2, a bare `SELECT` under
`isolation_level=''` opens no transaction, so there is no snapshot to go stale. This read is allowed
to be wrong; the guard in step 2 re-evaluates at write time.

```sql
SELECT seq, verb, actor, at, from_status
FROM entity_events
WHERE entity_id = :eid
ORDER BY seq DESC
LIMIT 1;
```

Measured **0.003 ms** (§0.1, 500k rows).

Step 2 — decide `verb`: head absent or head verb in `('release','resolve')` → `claim`; head verb in
`('claim','renew')` and `head.actor == actor` → `renew`; otherwise we expect to lose. Then check the
declaration: `refused` if `not kind.claimable` or `EntityRef.of(entity_id).is_resolved(conn)`.

Step 3 — the guarded append. **This single statement is the entire mutual-exclusion mechanism.**

```sql
INSERT INTO entity_events
    (entity_id, kind, verb, actor, from_status, to_status, ref, note, at)
SELECT :eid, :kind, :verb, :actor, :from_status, :to_status, :ref, :note, :now
WHERE NOT EXISTS (
    SELECT 1 FROM entity_events h
    WHERE h.entity_id = :eid
      AND h.verb IN ('claim','renew')
      AND h.actor <> :actor
      AND h.seq = (SELECT MAX(seq) FROM entity_events WHERE entity_id = :eid)
)
RETURNING seq, at;
```

- A row comes back ⇒ the append happened ⇒ outcome is `claimed` or `already_mine` per the verb
  chosen in step 2.
- Empty result ⇒ the head belongs to another actor ⇒ `held_by_other`; re-run step 1 to name them and
  compute `age_seconds` / `stale`.
- `RETURNING` is used rather than `rowcount` because it returns content, not a count — sqlite 3.47.1
  is well above the 3.35 floor (research Q3a).
- `steal=True` drops the `AND h.actor <> :actor` clause and sets `verb='steal'`. Explicit opt-in
  only, per the user's "reported, never auto-stolen" constraint.

The inner `MAX(seq)` subquery is an index seek on `idx_ee_entity_seq`, not a scan.

Step 4 — status projection, findings only, CAS-guarded, inside the same `BEGIN IMMEDIATE`:

```sql
UPDATE findings SET status = :busy, updated_at = :now
WHERE id = :eid AND status = :from_status;
```

`rowcount == 0` ⇒ someone moved it concurrently ⇒ return `projected: false`, do not retry, do not
fail the claim. Table and column identifiers come from the frozen `EntityKind` and are validated
against `entities.SAFE_IDENT` before interpolation (§0.2).

**Read path A — "who holds CB-1234":** identical to step 1 above, wrapped by `holder()`, which
interprets the head verb and adds `age_seconds`, `stale`, and `entity_missing`. **0.003 ms measured
at 500k rows.**

**Read path B — "what does agent-7 hold":** the anti-join from §0.1.

```sql
SELECT e.entity_id, e.kind, e.at AS claimed_at, e.seq, e.from_status
FROM entity_events e
WHERE e.actor = :actor
  AND e.verb IN ('claim','renew','steal')
  AND NOT EXISTS (
      SELECT 1 FROM entity_events l
      WHERE l.entity_id = e.entity_id AND l.seq > e.seq
  )
ORDER BY e.seq DESC;
```

**Measured 2.970 ms at 500k rows / 50k entities / 200 actors; 58.5 ms in the pathological 8-actor
case.** `EXPLAIN QUERY PLAN` confirms `SEARCH ... USING INDEX idx_ee_actor_seq` plus a covering-index
probe — not a scan.

**Staleness (criterion 7):** the same anti-join with `AND e.at < :cutoff`, threshold chosen by the
reader at call time, not baked into a row. Per **C8** there is no TTL, no lease, no expiry column and
no sweeper. `renewed_at` semantics fall out for free: a repeat claim appends a `renew` whose `at` is
fresh, so "stale" means "hasn't checked in", which is more useful than "claimed long ago" — and it
cost zero schema.

**Q5 / release:** identical to Proposal 1's release, minus the `claims_current` delete.

**Outcome vocabulary:** the five in §2.

### Pros

- **One table, one concept, nothing to keep in sync.** No projection, no cache, no rebuild
  procedure, no coherence invariant, no drift class of bug. The lever is satisfied in its purest
  form and the design has no "but actually there's a mutable table over here" caveat.
- **The claim is one guarded statement** for non-projecting kinds — provably correct for exclusion
  with zero transaction ceremony (research Q3d), and it never produces an `IntegrityError`, so the
  loser's signal has the same shape as every other substrate.
- Both read paths measured fast at 100× this repo's plausible scale (§0.1).
- Audit trail is free and complete — the claim *is* the record. This is the axis C2 tells me to
  argue on, and here it costs literally nothing extra.
- **C1 is answered by an append**, and appends never conflict (§4).
- Smallest diff of the three; matches the C7 bar (an expressiveness problem deserves a proportionate
  fix).
- The `stale` reader-threshold design means criterion 7 needs no background process at all.

### Cons

- **Read path B is `O(k)` in an actor's lifetime claim count, and `k` only grows.** §0.1 measures it
  fast today and bounds it in practice, but it is genuinely unbounded in principle. Proposal 1 is
  the escape hatch and the migration is additive — but "we'll fix it later if it bites" is a real
  concession and I am making it explicitly.
- **Both indexes are mandatory.** Drop `idx_ee_entity_seq` and the design goes quadratic (1563 ms at
  40k rows). A mutable owner column has no such cliff. This must be a comment in the schema, which
  it is (§5).
- Log grows monotonically with no compaction story. At this repo's scale that is a non-problem
  (research Q7: 200k rows worst case ≈ 20 MB), but there is no answer if it is ever wrong.
- The claim path has two code shapes (one-statement vs `BEGIN IMMEDIATE`) depending on whether the
  kind projects. That is a real conditional complexity, and a reviewer could reasonably ask for
  `BEGIN IMMEDIATE` unconditionally for uniformity at a small cost.
- `history()` is a genuinely new capability nobody asked for. I claim it as a benefit; it is also
  scope.

### Effort: **M**

~300 lines in one new module, one table, five MCP tools, five CLI handlers, one `provenance.py`
edit, one SKILL.md edit, wiring, and a test file modelled on `tests/test_milestones.py:801`.

### Risk profile

- **Low correctness risk.** The exclusion is a single guarded write, the substrate class the research
  proved at 200/200 trials across 4 OS processes. No `IntegrityError` path, no snapshot hazard, no
  plain `BEGIN` anywhere.
- **Low-medium performance risk**, quantified rather than asserted: fine to ~500k events with the
  indexes present, and the `k` term is the thing to watch. Ship a test that asserts both indexes
  exist.
- **Medium adoption risk** — shared by all three, and mitigated in §8.
- **Low blast radius.** One new module, two edits to existing files, three lines of wiring. Nothing
  existing changes behaviour unless it opts in.

---

## Proposal 3 — **Declared Passage**

### Core idea

Take the user's "seed of a process" literally. `entity_events` becomes the transition log for a
declared lifecycle: each `EntityKind` carries not just `busy_status` but a full state set,
terminal set, and a `transitions` DAG — generalizing `sweep._validate_transition` (`sweep.py:395-407`)
from a codesweep-local helper into an entity-layer capability. Claiming is then not a special
primitive at all; it is the transition `open → in_progress` performed by an actor, and mutual
exclusion is the DAG's idempotency rule plus the actor guard. The 14 hand-rolled gates in §3 get a
home to migrate into, one at a time.

### How it works

**Declaration** — `EntityKind` gains three more fields beyond §1:

```python
    states: tuple[str, ...] = ()
    transitions: dict[str, tuple[str, ...]] | None = None   # None = unconstrained
    actor_rule: str = "holder"   # "holder" | "any" | "none"
```

populated from the existing constants — `finding` gets `states=t.FINDING_STATUSES` (`types.py:21`),
`terminal=t.FINDING_TERMINAL` (already present at `entities.py:41`), and a DAG such as
`{"open": ("in_progress","not_a_bug","wont_fix","stale"), "in_progress": ("open","fixed",...)}`.
`requirement` gets `states=t.REQUIREMENT_STATUSES` (`types.py:39`) and `transitions=None`
(unconstrained) — because the requirement lifecycle is genuinely not a DAG anybody has written down.

**Validation** is lifted verbatim from `sweep.py:395-407` into `claims.py` (or a new
`transitions.py`), preserving its two important behaviours: `transitions is None` means
unconstrained, and `src == dst` is idempotent rather than an error. **Per the global CLAUDE.md rule,
this lift is done with `git-split2` marker mode, not copy-paste**, so `git blame` on the moved lines
still traces to the original author; `sweep.py` then calls the shared function.

**Schema:** the common `entity_events`, plus `verb` widened to include `'transition'`, and a new
column:

```sql
ALTER TABLE entity_events ADD COLUMN actor_rule_applied TEXT;
```

**API** — claim/release become thin wrappers over one primitive:

```python
def transition(conn, *, entity_id: str, actor: str, to_status: str,
               note: str = "", force: bool = False,
               reason: str = "") -> dict[str, Any]: ...

def claim(conn, *, entity_id, actor, **kw):
    return transition(conn, entity_id=entity_id, actor=actor,
                      to_status=entity_kind_of(entity_id).busy_status, **kw)
```

**Claim path.** `BEGIN IMMEDIATE` (always — this is unavoidably multi-statement), then: read head
event + current status; validate `_validate_transition(kind.transitions, cur, to_status)`; validate
`actor_rule` against the head event's actor; append; project. Same guarded `INSERT ... SELECT ...
RETURNING` as Proposal 2 for the append itself. The `force=True` + `reason` escape hatch is modelled
directly on `milestone_close` (`closegate.py:191-202`), including writing `f"force:{reason}"` into
the event's `note`.

**Read paths:** identical to Proposal 2 (§0.1 anti-join and point lookup), or Proposal 1's
projection if paired.

**Outcome vocabulary:** the five in §2, plus `refused` gaining a structured `violated` payload
naming the rejected edge (`{"from": "fixed", "to": "in_progress", "allowed": [...]}`).

### Pros

- It is the thing the user actually sensed. Claiming stops being a bolted-on primitive and becomes
  one verb in a declared process.
- **The 14 hand-rolled gates get a target.** `merge.py`'s three status-equality checks and
  `sweep.py`'s DAG are directly expressible; migrating them would delete real duplication.
- `sweep._validate_transition` stops being an orphaned abstraction and becomes the shared one it
  should have been.
- Illegal status transitions become impossible for *every* kind at once, which is a genuine
  correctness improvement `findings.update_finding` (`findings.py:235-303`) does not have today —
  it accepts any valid enum value from any state.
- `entity_events` becomes a true transition log, giving `from_status`/`to_status` the meaning
  `milestone_audit`'s identical columns (`_schema.py:85-86`) never got.

### Cons

- **Most of the 14 gates do not fit** (§3). `capacity.py:110-130`'s five checks are capacity
  arithmetic, free-text emptiness, cross-domain blocker lookup, and referential existence — a DAG
  expresses none of them. The claimed consolidation benefit is roughly 4 of 14, and I would be
  overselling it to say otherwise.
- **It changes the behaviour of an existing, widely-called function.** Making
  `findings.update_finding` reject transitions would break `provenance.resolve_trailers` the moment
  a trailer implies an edge nobody declared — and provenance's current posture is to *skip*
  silently (`provenance.py:261`), not raise. Reconciling refuse-vs-skip-vs-report policy across
  existing callers is most of the work and all of the risk.
- Requires writing down a DAG for findings that nobody has agreed on. Every disagreement about an
  edge becomes a blocking design argument on what was supposed to be a claim primitive.
- **Directly contradicts C7.** The measured harm is duplicated agent work. This is the heaviest
  response on the table to the lightest problem, and the brief explicitly demands I justify that
  weight or concede it. **I concede it.**
- Highest chance of the `pull_next` failure mode: a large, correct, general mechanism nobody wires
  up.

### Effort: **XL**

New declaration surface, a transition engine, a history-preserving lift of `sweep._validate_transition`
with `sweep.py` retargeted, a findings DAG agreed by the user, migration of ≥4 existing gates,
policy reconciliation for provenance, plus everything Proposal 2 needs.

### Risk profile

- **High.** Behaviour change to existing call paths; design risk in the DAG itself; scope risk from
  the gate migration; and a real chance of an argument about edges stalling the claim feature that
  motivated all of this.
- Correctness of the *claim* remains low-risk (same guarded append), but the surrounding engine is
  where the risk concentrates.

---

## 6. My recommendation: **Proposal 2 — Fold on Read**

**Why, in order of weight.**

1. **§0.1 removed the reason to prefer Proposal 1.** I expected to be defending a 752 ms reverse
   query and arguing that audit value justified it. Measured, the anti-join is **2.970 ms at 500k
   rows** in the realistic actor distribution and 58.5 ms in the pathological one. Proposal 1's
   materialized cache buys `O(1)` over an already-fast `O(k)`, and pays for it with a second table
   and a coherence invariant. That is a bad trade at this repo's scale, and Proposal 1 is honestly
   just Architect B's mutable lock row with a log attached — I would rather say that plainly than
   ship it.
2. **C7 sets the bar and Proposal 2 is the only one of mine that clears it.** The harm is duplicated
   work, not corruption, and the race has never been observed firing. One table, one guarded
   statement, two indexes is a proportionate response. Proposal 3 is not, and I say so in §3 and in
   its own Cons.
3. **The audit substrate is not speculative here — it is the shape this repo keeps reaching for.**
   `milestone_audit` (`_schema.py:79-92`) is already an append-only log with `actor` / `from_state` /
   `to_state` / `reason` / `at`, written from 12 sites via `_spine.py:64-80`. My `entity_events` is
   that table generalized to every entity kind, with column names chosen to make a later merge a
   rename. This is convergence with an existing pattern, not an import of foreign machinery — which
   is the specific charge (Microsoft's own "for most systems, traditional data management is
   sufficient"; Kiehl's post-mortem) that research Q7 correctly aimed at me.
4. **C1 is the decisive technical argument and only the log has it.** `provenance.py:264-269` fires
   independently of claims. A mutable owner needs reconciliation code and a cross-module write. The
   log needs an append, appends never conflict, the claim closes itself by the fold, and attribution
   survives with the commit SHA. That is a structural advantage, not a preference.
5. **It leaves the door open.** If `k` ever bites, `rebuild_projection()` from Proposal 1 is a purely
   additive change over the same log — the fold already exists as read path B. If the process layer
   is ever wanted, `entity_events` is already the transition log Proposal 3 needs. Choosing 2 does
   not foreclose 1 or 3; choosing 1 or 3 forecloses the simplicity of 2.

**What I would be wrong about, if I am wrong.** The `k` term. If agent identities in this system
turn out to be stable and long-lived rather than ephemeral per-worktree strings, `k` grows without
bound and read path B degrades on a timescale of years. The mitigation is real and additive, but the
council should treat "are agent ids ephemeral?" as a question worth answering before merge rather
than after — I did not verify it, and it is the single assumption my recommendation rests on that I
could not measure.

---

## 7. Remaining criteria, answered explicitly

**Criterion 1 — exactly one winner, proven by executed test.** Follow the precedent at
`tests/test_milestones.py:801-846` (`test_two_threads_two_connections_no_double_claim`): two real
threads, each with its own `db.connect(tmp_project)` on a file DB, `threading.Barrier(2)`. Assert one
`claimed` and one `held_by_other`, and that the loser names the winner. Also assert
`SELECT COUNT(*) FROM entity_events WHERE entity_id=? AND verb='claim'` equals 1. Add a
`busy_timeout=0` variant asserting `undetermined` is returned rather than raised (C4), since
`tests/test_sweep.py:754` shows the repo already varies `timeout` deliberately.

**Criterion 2 — retrying holder is never told it lost.** The guard is
`AND h.actor <> :actor`: the head being your own claim can never block your append. Verified shape
matches the research Q3f probe and k8s `tryAcquireOrRenew`.

**Criterion 6 — existing `query(status="in_progress")` keeps working.** Findings still get
`in_progress` written by the projection in step 4. No consumer changes. Requirements never had the
status, so nothing to preserve.

**Criterion 8 — `milestone_items` convergence.** Honest scope statement:
`milestone_items.assigned_agent` (`_schema.py:64`, partial index at `:77`) is not merely ownership —
it is coupled to `agent_capacity` (`_schema.py:94-99`) counters decremented by `release_item`
(`capacity.py:264-273`) and to size-gated eligibility (`capacity.py:110-130`). Subsuming it means
absorbing capacity accounting, which is a separate feature.

*Phase 1 (in scope, ~3 lines):* `pull_next` additionally calls `claims.record(verb="claim", ...)`
next to its existing `_audit(...)` call at `capacity.py:203-212`. `milestone_items.assigned_agent`
stays authoritative for capacity; the ledger becomes the **union view** of all ownership, so
`holdings(agent-7)` answers correctly across both systems. Bonus: this gives the unwired `pull_next`
a second consumer.
*Phase 2 (explicitly out of scope):* `assigned_agent` becomes a derived read and the column is
dropped, once capacity accounting has its own home.

---

## 8. Q7 / C11 — the exact file and change that makes this get called

**Primary, and it is the strongest lever available (C11).**
`~/.claude/skills/fix-latest-codebugs/SKILL.md:92`, verified by direct read this run:

```
92	1. `mcp__codebugs__update(id="CB-1234", status="in_progress", assignee="claude")` — claims the bug.
```

Neither `id=` nor `assignee=` exists in the MCP `update` signature (`findings.py:574-581`, which
takes `finding_id, status, notes, tags, meta_update, reported_at_ref`). **This documented claim call
cannot succeed today and must be edited regardless of which design wins.** Replace with:

```
1. `mcp__codebugs__claims_acquire(entity_id="CB-1234", actor="<agent-id>")` — claims the bug.
   - `claimed` → proceed.
   - `already_mine` → you are resuming; proceed.
   - `held_by_other` → STOP. Report the holder and `age_seconds`; offer the next candidate.
   - `undetermined` → retry once after `retry_after_ms`; then treat as `held_by_other`.
   - `refused` → the entity is already resolved; report and offer the next candidate.
```

Replacing a broken instruction is a far stronger adoption story than adding an optional tool, and it
is the specific failure mode `pull_next` demonstrates.

**Secondary, independent of the ledger, and worth doing even if a sibling proposal wins.**
`findings.update_finding` (`findings.py:235-303`) is the single choke point every status write in the
codebase passes through — the `UPDATE` is `findings.py:298`, with no rowcount check. Add an optional
keyword-only `expected_status: str | None = None`; when supplied, append `AND status = ?` to the
`WHERE` and return `changed: bool` from `cursor.rowcount`. Surface `changed` in the MCP `update`
tool. **This is the minimal honest fix for the C7 expressiveness problem and it is orthogonal to the
ledger** — the council should consider landing it regardless.

**Module wiring** (per CLAUDE.md; all verified this run):

| change | location |
|---|---|
| `db.register_schema("claims", ensure_schema)` at module level | new `src/codebugs/claims.py`; registry at `db.py:49-64` |
| `db.register_tool_provider("claims", register_tools)` | `db.py:114-124` |
| `db.register_cli_provider("claims", register_cli)` | `db.py:149-153` |
| add `claims` to the import list | `db.py:487` (inside `_ensure_modules_loaded`, `db.py:478-489`) |
| add `"claims": "codeclaims"` | `SERVER_NAMES`, `server.py:22-32` |
| add `"claims"` to `choices` | `cli.py:49` |
| `provenance.py` appends a `resolve` event (§4) | after `provenance.py:270` |
| `capacity.py` appends a `claim` event (criterion 8 phase 1) | next to `capacity.py:203-212` |
| tests | new `tests/test_claims.py`, no shared `conftest.py` |

MCP tools carry the domain prefix per CLAUDE.md: `claims_acquire`, `claims_release`, `claims_who`,
`claims_holdings`, `claims_history`. CLI handlers `cmd_claims_<action>()`. The `mode` slug is the
string passed to `register_tool_provider` and is what `--mode claims` matches
(`db.get_tool_providers`, `db.py:127-132`).

---

## 9. Where I would accept being overruled

- If the council decides the reverse query must be `O(1)` on principle rather than measurement,
  Proposal 1 is the right answer and I would not fight it — but I would insist the `claims_current`
  table be documented as derived, with `rebuild_projection` shipped and tested, or the lever is
  being evaded rather than satisfied.
- If the user genuinely wants the process layer now, Proposal 3 is buildable and §3 lists the four
  gates that would actually consolidate. I recommend against it on C7 grounds and on the evidence
  that most gates do not fit, but the disagreement is about proportion, not feasibility.
- **If the council concludes an event log is disproportionate to an API-expressiveness problem, the
  honest fallback is the `expected_status` + `changed` patch in §8 alone, and I would rather see
  that ship than see any of my three proposals ship unadopted.** A correct primitive nobody calls is
  the failure this council was convened to avoid repeating.
