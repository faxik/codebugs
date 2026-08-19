# CB-31 — a declared seam for "which milestone items are live"

Branch `fix/cb-31-live-items-seam`, base `93ec01f`. One card, one tree.
**Revision 2**, after `adversarial-review-x2` (Opus adversary + Codex/GPT-5.6-Sol, both read-only
against this worktree). Both returned *proceed with fixes*; neither could break the core predicate.
Corrections appendix at the bottom.

## Why this is one card and not the batch that was asked for

The session focus was "stabilization batch — items that touch related bugs or the same files". The
one legitimate cluster in the open queue is CB-69 + CB-84 (predicate 1: the blockers layer has no
single-pass active-blocker summary). It is NOT this card: CB-31's mechanism is the **terminal-source
status join**, CB-69/84's is **blocker discovery**. Different tables, different predicates, no shared
causal change. Codex reviewed the shortlist independently and called the union "theme-clustering
wearing a mechanism costume". Split.

## Why the seam is worth a new API — the justification is the WRITE LOCK, not anti-drift

Revision 1 led with "a fourth call site becomes structurally unable to forget". **Both reviewers
attacked that, and they are right**: an AST ratchet over `source_is_terminal`'s existing call sites
delivers the same "record the decision" property today, with no new SQL, no NULL-safety surface and
no drift twin. Anti-drift alone does not buy a new API.

What does: `capacity._candidates` runs `source_is_terminal` **per candidate row inside
`pull_next`'s `BEGIN IMMEDIATE` window** (`capacity.py:179-187`, lock taken at `capacity.py:243-246`),
and each call is a `sqlite_master` probe plus a status `SELECT`. Every concurrent agent waits behind
that. Folding the predicate into the bucket query removes per-row I/O from an exclusive-lock hold.
That is the card's own stated priority — *"the lock-hold extension in a multi-agent tracker is the
part worth fixing"* — and it is the only justification this plan now rests on. The seam and the
ratchet are complementary: the seam removes the cost, the ratchet records the population.

## Reproducer

Measured on the live tracker, 2026-08-19, at `93ec01f`; **independently reproduced by the Opus
adversary to the row**:

```
milestone_status("stream/triage")  -> open_items: 44
triage_inbox(limit=200)            -> 37 rows

total stored-open: 44
stale (source finding terminal): 7 -> CB-26 CB-27 CB-30 CB-36 CB-39 CB-40 CB-41 (all `fixed`)
orphan (no source row): 0 | external: 0 | in_progress: 0 | all item_kind = 'bug'
44 - 7 = 37
```

**The card's open question, answered.** It asked whether the 7 predate CB-26's hook or are fresh
drift. All seven were closed in the 2026-08-14 iterations (`004027e`, `ae77cba`, `19e4947`) and
**nothing filed since 2026-08-16 is stale**. The eager hook works; these are a backlog the read
filter silently absorbs — CB-26's rule exactly: *eager reconciliation keeps stored state honest;
only a read-side filter can make a guarantee.*

## Root cause

`reconcile.source_is_terminal` (`reconcile.py:102-118`) is a per-row Python predicate: `_table_exists`
(a `sqlite_master` probe, `reconcile.py:77-80`) plus `entities.EntityRef.is_resolved`
(`entities.py:147-160, :171-174`). It is applied **by hand** at every read that must not return
finished work, so the rule lives in prose.

Precise cost, corrected per Codex: **two queries per row only for a recognised kind whose table
exists.** An `external` or unmapped kind costs zero; a recognised kind with an absent table costs one.

## Evidence — the population is THREE call sites, not the two the card names

| # | Site | Shape | In the card? |
|---|---|---|---|
| 1 | `triage.triage_inbox` — `triage.py:78-84` | list comprehension, 1 call/row | yes |
| 2 | `capacity._candidates` — `capacity.py:179-187` | nested per-row loop, **inside `BEGIN IMMEDIATE`** | yes |
| 3 | `foundation.list_milestone_items(live_only=True)` — `foundation.py:161-177` | comprehension after `_row_to_item` | **no — added later, for codashboard** |

Site 3 is not a defect: it remembered. It is the card's own prediction coming true in the benign
direction, one iteration after filing.

**Deliberately NOT filtered — both sites named, because an exclusion list that omits a site is the
same defect as a call-site list that omits one:**

- `foundation.get_milestone_status` (`foundation.py:186-188`) — a rollup reports the table as stored
  (`reconcile.py:25-33`).
- `closegate`'s unfinished gate (`closegate.py:212-219`) — a stored-`open` item over a terminal
  source yields a **false refusal** of `milestone_close`, and that is correct: `done_commit` is never
  a gate, so hiding such rows would let a release close over a missed integration (CB-32,
  `reconcile.py:59-73`).

## Plan

### 1. The seam — `reconcile.live_source_clause(conn, *, alias)`

Returns `(sql_fragment, params)`. The fragment is **always a valid SQL boolean expression**,
defaulting to the literal `1`, so no call site needs an `if frag:` branch — point-of-use discipline
is the wrong enforcement layer (CB-41), including for the seam's own adoption.

For each `EntityKind` in `entities.ENTITY_KINDS` whose table exists on this connection:

```sql
NOT EXISTS (
  SELECT 1 FROM <kind.table> _src
  WHERE <alias>.item_kind = ? AND _src.id = <alias>.item_ref AND _src.status IN (?, ?, ?)
)
```

- **`NOT EXISTS`, never `status NOT IN (...)` over a `LEFT JOIN` or a scalar subquery.** A missing
  source row gives NULL, `NULL NOT IN (...)` is NULL, and `WHERE NULL` **excludes** — inverting
  fail-open into a queue that hides work. `NOT EXISTS` is never NULL: a row is hidden only on
  affirmative proof (recognised kind, existing table, matching row, terminal status).
- **`alias` is REQUIRED and validated.** This is the review's sharpest finding and it is worse than
  either reviewer stated. With an empty/unqualified alias, `item_kind` and `item_ref` resolve against
  the **subquery's** table first. Today that is harmless only because `findings`/`requirements`
  happen to lack those columns. Measured, adding an `item_kind` column to `findings`:

  ```
  alias=''  , findings has no item_kind (today) -> kept [2]   OK
  alias=''  , findings GAINS item_kind column   -> kept []    *** FILTER DISABLED ***
  alias='mi.', findings GAINS item_kind column  -> kept [2]   OK
  ```

  The unqualified form did not merely stop hiding terminal rows — it **hid the `external` row that
  must stay live**, because the subquery stops referencing `mi.item_kind` at all. Fail-CLOSED,
  hiding live work. So: `alias` is a **required** keyword argument, a **bare identifier** validated
  with `types.is_sql_identifier` (the builder appends the `.` itself, so a caller cannot pass a raw
  fragment), and **all three queries gain an explicit `mi` alias** so every site is uniform.
- **Computed ONCE per traversal.** `live_source_clause` probes `sqlite_master` per kind. Codex caught
  that putting it inside `_bucket_query` would repeat two probes for each of four buckets — eight
  extra reads inside `BEGIN IMMEDIATE`, making the lock hold *worse*, which is the opposite of this
  card's justification. `_candidates` computes the clause once and passes it to `_bucket_query`.
- **A missing source table omits its fragment from BOTH the SQL and the params** — fail-open, and it
  keeps raw-connection callers working.
- **Terminal statuses are BOUND and derived from `kind.terminal`**, never re-spelled, and **sorted**
  (`frozenset` iteration order is unstable, which would make a template-asserting test flaky).
- **`kind.table` is interpolated** with `# noqa: S608`; honest because `EntityKind.__post_init__`
  validates it at construction (CB-22).
- **Kinds absent from `ENTITY_KIND_TO_ITEM_KIND`** (`_schema.py:68-71`, not total over `ENTITY_KINDS`
  by contract) are **skipped**, matching `source_is_terminal`'s own fail-open on the same lookup
  (`reconcile.py:96-99`). Never `[...]`, which would raise `KeyError`.

Exact bound-parameter vector with both tables present, per Codex:
`bug, fixed, not_a_bug, wont_fix, requirement, implemented, obsolete, superseded, verified`.

### 2. Adopt at all three sites

- `triage_inbox`: alias the table `mi`, splice the clause into the WHERE, params `[seam...]`.
  **The LIMIT stays in Python** — see §3.
- `_candidates` / `_bucket_query`: clause computed once in `_candidates`, `_bucket_query(pattern,
  clause)` returns `(sql, params)`; params splice at the fragment's textual position, before
  `ORDER BY` (CB-20 — a wrong splice corrupts only the parameterised cases while unfiltered tests
  pass). Each bucket binds `[seam...]`.
- `list_milestone_items`: splice when `live_only`; binds `[milestone_id, statuses..., seam...]`.

### 3. The LIMIT move is DROPPED from this card, and a tiebreaker is added

Revision 1 proposed folding `triage_inbox`'s LIMIT into SQL and claimed "behaviour is preserved".
**Both reviewers measured that false, independently:**

- **Negative limit.** `triage.py:84` is `live[:limit]`; SQL `LIMIT -1` means *unlimited*.
  `LIMIT -1` over 4 rows returns 4; `live[:-1]` returns 3. `milestones/__init__.py:250-258` declares
  `limit: int = 50` with **no validation**, and the CLI passes an unvalidated `type=int`, so
  `--limit -1` reaches the domain function today.
- **Tie ordering.** `ORDER BY created_at ASC` has no tiebreaker and `utc_now()` is whole-second. The
  real tracker has ties among stored-open triage rows: `('2026-08-14T07:16:27Z', 5)`,
  `('2026-08-16T17:11:33Z', 4)`, `('2026-08-16T17:44:10Z', 2)`.

So the LIMIT stays where it is. **But `, id ASC` is added to the ORDER BY**, because changing the
WHERE can itself change the query plan among tied `created_at` values, and the Python slice would
then pick different rows. `id` is the insertion order the existing tests already assume
(`tests/test_milestones.py:478-483`), so this makes them stronger, not different. Validating or
rejecting a negative `limit` is a real but separate contract question — **filed, not fixed here.**

### 4. Anti-drift: a DIFFERENTIAL test with NAMED discriminating rows

`source_is_terminal` stays (canonical single-row predicate, re-exported by
`milestones/__init__.py:67`). A test asserts the SQL clause and the Python predicate agree **row for
row**. Non-vacuity is specified, not hoped for — the review measured which rows are load-bearing:

Named mutant it must kill: the scalar-subquery form
`(SELECT status FROM findings WHERE id = mi.item_ref) NOT IN (...)`, which is expressible as a WHERE
fragment and is the realistic NULL-unsafe mistake. Measured: canonical keeps `[2,3,4]`, mutant keeps
`[2]`. It is caught **only** by these two rows, so both are mandatory:

- an **`external`** row whose `item_ref` points at a **terminal** finding (an external pointing at a
  live finding makes the case vacuous);
- a **`bug`** row whose `item_ref` resolves to **no** finding.

Plus: assert fixture cardinality; at least one expected-hidden row **per entity kind**; at least one
expected-live row per fail-open class; and that **both verdict classes are non-empty** (comparing two
empty result sets would pass).

**Cases the normal schema cannot express**, per both reviewers — built on a deliberately minimal raw
schema, never via `db.connect()`, and said so in the test:
- unmapped `item_kind` — forbidden by `CHECK(item_kind IN ('bug','requirement','external'))`
  (`_schema.py:109-110`);
- source `status IS NULL` — `status TEXT NOT NULL` on both tables (`findings.py:34`, `reqs.py:28`).

### 5. Ratchet the population

An AST test pinning the exact set of functions calling `live_source_clause`. Per Codex, the asserted
set is the **callers** — `triage_inbox`, `_candidates`, `list_milestone_items` — which is stated
identically here and in the test so the two cannot disagree. It cannot *prevent* a fourth read from
forgetting; it makes adding one a decision someone records, the same shape as
`TestOpenCallSitesRatchet`. Said plainly rather than dressed as structural impossibility.

### 6. Documentation edits are deliverables, not follow-ups

Both reviewers noted these go false on landing:
- `reconcile.py:25-33` — says the filter is applied at **two** sites; it is three, and now one seam.
- `triage.py:65-76` — says pushing LIMIT into SQL is unsafe. Still true, now for the *measured*
  reasons in §3, not the one it gives.
- `foundation.py:136-145` — names `source_is_terminal` as the implementation.
- `tests/test_milestones_reconcile.py:250-256` — `test_limit_counts_live_rows_only`'s docstring keeps
  passing but its stated reason changes.
- **`reconcile.py:87-90`'s `_milestones_ready` docstring cites `tests/test_sweep.py` as the
  raw-connection caller. Verified false for these reads** — that file initialises only sweep schema
  and never calls a milestone read. The real precedents are `tests/test_milestones_reconcile.py:333-348`
  and `tests/test_milestones.py:360-375` (milestone hooks on findings-only databases). The docstring
  is right about the *hook* path and my revision-1 plan misapplied it to *queue reads*.

## Risks & out-of-scope

- **Fail-open is the whole safety property.** Verified by §4 plus explicit cases for external,
  missing source row, absent table, and the degenerate both-absent `AND 1`.
- **Out of scope, named:** `get_milestone_status` and the `closegate` gate (§Evidence); the blockers
  N+1 (CB-69/CB-84, different mechanism); backfilling the 7 stale stored rows (a write with its own
  audit questions — the read filter already hides them); negative-`limit` validation (§3);
  `list_milestone_items` missing from `REEXPORTED_NAMES` (`tests/test_milestones_surface.py:61-90`) —
  a **pre-existing** surface gap Codex found, filed rather than fixed here.
- **`live_source_clause` is NOT added to the facade** — it stays in `reconcile`, so no
  public-surface decision rides along.

## Verification

1. Differential test (§4) — proven to fail against the **named** scalar-subquery mutant.
2. Fail-open cases: external / missing row / absent table / both tables absent.
3. Table-availability matrix: both present, findings-only, requirements-only, both absent —
   asserting selected rows **and** exact placeholder/parameter counts.
4. Query-count test via a locally-defined `RecordingConnection` (it lives in `test_bench.py:919`,
   `test_reqs.py:20`, `test_findings.py`, `test_merge.py:882` — **not** in any `test_milestones*.py`,
   so it must be copied per the no-conftest convention). **Reset the recorder after schema setup and
   assert the TOTAL statement population, including `sqlite_master` probes** — old and new both issue
   one statement *against `milestone_items`*, so that qualifier alone cannot discriminate. Run at two
   different row counts to prove constancy: 3 total after, vs `1 + 2N` before.
5. `pull_next` refuses a terminal source in a **RELEASE** milestone — the only fixture proving the SQL
   predicate rather than the hook protects it, since the reconciler deliberately leaves release items
   alone (`reconcile.py:59-73`).
6. `list_milestone_items` pagination: stale rows placed **before the offset boundary**, verifying the
   filter precedes both offset and limit (existing tests cover `live_only + statuses + limit`, not
   `offset`).
7. `triage_inbox` returns the same 37 rows on the real tracker.
8. Full suite in the worktree + `ruff check`.

---

## Adversarial Review x2 Corrections

Opus adversary and Codex/GPT-5.6-Sol, in parallel, read-only. Both verdicts: **proceed with fixes**.
Codex confidence 0.95. Neither could break the `NOT EXISTS` predicate — Opus ran it against
externals, missing rows, wrong-kind matches, absent tables and the degenerate `1` and found it
row-for-row identical to `source_is_terminal`.

**Corroborated by both models** (treat as high-confidence):
1. Folding LIMIT into SQL is **not** behaviour-preserving — negative limit and tie ordering. Dropped.
2. The `alias` parameter is unsafe/unvalidated. Now required, validated, and uniformly qualified.
3. The differential test was under-specified and could pass vacuously. Discriminating rows named.
4. Fixture cases the schema forbids (unmapped `item_kind`; NULL status) need a raw minimal schema.
5. Existing docstrings go false and must be edited in-tree.
6. The view rejection's stated reason was wrong.

**Opus-only:** the `closegate` exclusion was unnamed (W2); and the decisive argument against a view —
measured, `CREATE VIEW` over a missing table succeeds but `SELECT` from it raises
`OperationalError`, so a view **fails closed with a crash** for exactly the raw-connection callers
the design must keep working. Revision 1's stated reason (DDL would hardcode terminal sets) was soft,
and Codex called it "categorically false" — a view *can* be regenerated from constants. Conclusion
unchanged, reasoning replaced.

**Codex-only:** the four-buckets-times-two-probes repetition inside `BEGIN IMMEDIATE` (the finding
that most directly threatened this card's own justification); the exact bound-parameter vector; the
false `tests/test_sweep.py` citation; `list_milestone_items` missing from the frozen public surface;
the query-count test being vacuous unless it counts *all* templates; the untested `offset` path; and
that "two queries per row" holds only for recognised kinds with an existing table.

**Where my own reproduction corrected a reviewer:** Opus characterised the unqualified-`alias` defect
as failing *open*. Measured, it fails **closed** — it hid the `external` row that must stay live,
because the subquery stops referencing the outer `item_kind` altogether. Same mechanism, worse
direction, and it moves this from a hardening note to the reason `alias` is a required argument.
