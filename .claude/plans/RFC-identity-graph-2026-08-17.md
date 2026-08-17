# RFC: The identity graph — typed relations, seam-time resolution, and a measured consolidation loop

**Date:** 2026-08-17 · **Status:** REVISED after adversarial-review-x2 (verdict FAIL-REVISE,
health 5/10 — "rework of the document, not of the direction"; all 12 mandatory fixes encoded
below; re-enters review at the appendix's estimate of 8) · **Category:** findings-identity /
architecture

**Lineage.** Third iteration of the identity line: CB-43/44 shipped exact identity
(fingerprints, occurrence bumps, reopen-on-fixed; `.claude/plans/CB-44-CB-43-identity-dedup.md`),
CB-45 shipped the similarity seam (file-time annotator + offline report;
`.claude/plans/CB-45-similarity-seam.md`), CB-46 holds the merge policy (proposed D6, NOT
ratified). Anchor card: CB-65. Absorbs the asks accumulated in CB-62; CB-60/61 are siblings.

---

## 1. Problem, with measured base rates

Measured 2026-08-16/17 on the autosorter corpus. **Population definition, stated once and never
mixed** (mandatory fix 12): all figures below cover `open + in_progress` = **1,137 rows** of
3,176 total; the tool's own `LIVE_STATUSES` is wider — `("open","in_progress","stale")`,
1,207 rows — and 70 stale rows were never embedded. S0 and S2 must each state which population
they run on; the prior instrument defect recorded at `PROPOSAL-batch-heuristics-2026-08-17.md`
(autosorter repo) §4b was exactly this mixture. Numbers below are from that document,
cross-model reviewed.

**a. True prose duplicates are rare but expensive — and the count is an interval, not a
point.** Semantic scoring (bge-m3, pure cosine, calibrated against a 20k random-pair baseline:
median 0.532, p99 0.690, p99.9 0.750) found 14 pairs / 10 families at cosine ≥ 0.84.
**Adjudicated: 2** (the duplicate pair CB-2743/CB-2882 — same file, same mechanism, same fix
set, filed 2 days apart by two sessions via two different prod bugs — and the 4-card
same-root-cause hook family). **Unadjudicated: 8.** Honest base rate: confirmed 2, upper bound
10, per 1,137 rows. Cost of the confirmed miss: two sessions independently root-caused the same
defect, assigned divergent severities, and the merge now needs CB-46 policy plus human time.

**b. Batchable relational structure is abundant — and is prose, of which most is untyped.**
784 of 1,137 rows cite another card (2,415 citations). Classified by phrasing, **93.1% are bare
references** carrying no relation type; the typed tail is ~145 windows ("distinct from" 74,
follow-up/blocked-on/split-from 71), from a classifier its own author disclaims. So prose
mining is a low-yield bootstrap, not the value story. The durable structure is elsewhere:
*already-typed* meta conventions that are merely untraversable — `meta.split_children`,
`meta.sprint` (17 live rows across 13 categories), `meta.plan`, core-written
`meta.recurrence_of` — plus every future verdict recorded at filing time. The LIKE campaign
(CB-3123 → 3143/3144/3145, four categories, a real landing order) was *discovered through the
very `split_children` key humans had already written* — circular as evidence for a discovery
heuristic, and exactly the point as evidence for S1: the knowledge exists and the tracker
cannot query it.

**c. Every instrument that failed, failed the same way.** Lexical trigram Jaccard has
near-zero recall on verified positives (max in-family scores 0.237–0.393, all below the 0.5
floor). The category block made 8 of 14 semantic pairs structurally unreachable. Unsigned
citation mining manufactured false components. Composition: a hard block on an uncurated field
plus a weak measure produced a confident "no duplicate families here" **twice in two days**.

**d. The mechanism that demonstrably works, works at write time.** The fingerprint substrate
collapses 71/115 of the machine-emitted family at file time with zero human effort. The
annotator seam (`similarity.py:368-400`) fires on every genuine no-explicit-id insert, without
filer opt-in (explicit-id inserts, bumps, reopens and CSV import bypass it — `findings.py:561`,
`:534`, `:540`, `:1859`). Post-hoc scrubbing is archaeology; the leverage point is the moment
of filing.

## 2. Design principles

- **P1 — Filer-in-the-loop.** Identity is resolved best at filing, by the filer. The system
  hands the filer the right candidates and a one-call way to record the verdict. It never
  decides for them.
- **P2 — Relations are typed, signed data.** `distinct_from` is a first-class edge that
  *suppresses* grouping. Prose mining is bootstrap only.
- **P3 — No scorer ships without its calibration.** Every threshold is published with a
  random-pair baseline and recall against an adjudicated gold set. The 0.84 figure used in §1
  is a noise-percentile cut, **not** a calibrated operating point; nothing may treat it as one.
- **P4 — Pairs are the unit of judgment.** Components are candidate generators and display
  artifacts only, always carrying their diameter (`min_pair_score` in the shipped sense:
  minimum over *all* member pairs, `similarity.py:303-317`).
- **P5 — Machine proposes, owner ratifies.** Machine writes go to a proposal ledger, never to
  the facts table. No autonomous merge, ever.
- **P6 — Yield honesty.** The loop meters its own yield, has hard cost bounds, and says when
  it is not worth running.

## 3. Architecture — re-staged after review

Order: **S0 → S1a → S1b → S2 → (S3 deferred).** Each stage independently valuable; S2 is
gated on S0's adjudication output.

### S0 — Adjudication first, harness second (gates every scorer)

**First deliverable is human labelling, not code** (mandatory fix 2): adjudicate the 14
cosine-≥0.84 pairs and the 21 two-card citation components, each labelled
`duplicate` / `related` / `unrelated` by reading both cards. This is what collapses §1a's
[2, 10] interval and produces the first legitimate gold set.

Then `tools/eval_identity.py` + a per-tracker gold file:

- Every gold entry carries `label`, `labelled_by`, `labelled_at`, and **`discovered_by`** —
  which instrument surfaced the pair. A scorer's recall is reported **twice**: against all
  positives, and against positives with `discovered_by != <this scorer>`. The second number is
  the only honest one; without it the harness rubber-stamps the instrument that generated its
  own test set.
- **Two tasks, two metrics, never summed:** duplicate-recall and relation-recall. The 74
  "distinct from" prose windows are adjudication *seeds*: humans wrote them because the pair
  was worth distinguishing, so they land as **relation-task positives** and (post-adjudication)
  duplicate-task negatives. Note most are live→`fixed` pairs the annotator never scores
  (`_ANNOTATE_STATUSES`, `similarity.py:71`), so they cannot calibrate S2's live pool alone.
- **Hard negatives for the duplicate task are scorer-selected adjudication rejects** — the
  only kind that measures precision. Random pairs supply the baseline percentiles, not the
  negatives.
- Eval artifacts are reproducible: frozen text snapshots (the gold references card ids in a
  *different repo's* tracker — the eval must not silently re-read mutated descriptions),
  content hashes, a missing-id policy, a CI entry point in this repo, and confidence intervals
  honest about N (with ~20 pairs, recall moves in 5-point steps; say so rather than reporting
  false precision).
- Acceptance runs go **through the exact bounded retrieval path S2 will execute** (pool bound
  included), never over an offline all-pairs matrix — otherwise S0 measures a scorer the
  product never runs.

### S1a — Typed relations: an audited ledger, not a bare edge table

Two tables (mandatory fixes 4, 5), in the shape `blockers.py` / `claims.py` already establish:

```
finding_relations(
  id INTEGER PRIMARY KEY,
  src_id TEXT NOT NULL, rel TEXT NOT NULL, dst_id TEXT NOT NULL,
  created_at TEXT NOT NULL, source TEXT NOT NULL, note TEXT,
  retracted_at TEXT, retracted_by TEXT, retracted_reason TEXT,
  CHECK (src_id != dst_id)
)
UNIQUE (src_id, rel, dst_id) WHERE retracted_at IS NULL   -- findings.py:72-77 idiom

relation_proposals(
  id INTEGER PRIMARY KEY,
  src_id TEXT NOT NULL, rel TEXT NOT NULL, dst_id TEXT NOT NULL,
  score REAL, scorer TEXT, model TEXT, model_version TEXT,
  judge TEXT, rationale TEXT, run_id TEXT,
  state TEXT NOT NULL CHECK (state IN ('pending','accepted','rejected')),
  created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT
)
```

- **Vocabulary** (mandatory fix 3): `rel ∈ {duplicate_of, split_from, follow_up_of,
  found_during, distinct_from}`. **`recurrence_of` is excluded** — it is core-owned, guarded by
  `_RESERVED_META_KEYS` (`findings.py:215-219`, whose comment names the spoofing attack
  verbatim); `relate()` rejects it with the same error text, and only the bootstrap may copy
  core's own values in with `source='core'`. **`blocked_by` is excluded** — finding→finding
  blocking already ships in the blockers module with trigger/satisfaction/lifecycle semantics
  (`blockers.py:17-25`, `:69-82`); `relate()` errors pointing at `blockers-add`. `similar_to`
  (annotator-owned) never enters the enum.
- **Integrity** (mandatory fix 5): endpoint existence validated in `relate()` at the
  application layer via `EntityRef` — declared FKs would be decorative because `db._open` never
  enables `PRAGMA foreign_keys`. Symmetric relations (`duplicate_of`, `distinct_from`) are
  stored in **canonical orientation** (lexicographic min as `src`), so the UNIQUE index
  enforces one edge per pair and no reader ever searches both directions. A contradiction
  guard rejects a live `duplicate_of` where a live `distinct_from` exists on the same
  canonical pair, and vice versa. `relate()` runs under `BEGIN IMMEDIATE`; re-relating an
  existing live edge is an idempotent no-op returning the existing row (`ON CONFLICT` inert by
  construction); the original `source`/`note` win — a second opinion is a note append, not an
  overwrite.
- **Retraction is a tombstone, never a DELETE** (mandatory fix 5): `unrelate` sets
  `retracted_at/by/reason`. Readers filter `retracted_at IS NULL`. No durable domain table in
  this package hard-deletes, and the most dangerous write in this design — a wrong
  `distinct_from`, which suppresses every discovery path for the pair — must stay auditable.
  An `active_suppressions` query lists all live `distinct_from` edges for review.
- **Machine writes go only to `relation_proposals`** — including machine `distinct_from`.
  Acceptance atomically inserts the relation and marks the proposal; **rejections are durable**
  and excluded from future candidate generation, so no loop ever re-judges (and re-pays for)
  the same pair. `duplicate_of` acceptance still merges nothing: it queues the pair for CB-46's
  policy, which remains proposed-not-ratified. Proposals are their own table, not milestone
  `external` items — externals never self-reconcile out of a queue
  (`milestones/reconcile.py:102-116`).
- **Semantics note:** `duplicate_of` is the *assertion*; CB-45/D6's `meta.merged_into` (not
  yet in code) would be the *execution record*; whether both exist is CB-46's call. Severity
  reconciliation at merge (CB-2743 high vs CB-2882 medium) is likewise CB-46 input, noted
  under D3 — this RFC does not decide it.
- **Bootstrap replaces "lazy migration"** (mandatory fix 7): a **one-shot pass** reads the
  existing conventions (measured live shapes: `distinct_from` 20 rows, `found_during` 69,
  `blocked_by` 5, `split_from` 3, plus `split_children` arrays and prose phrasing), emits
  **proposals**, and stamps `relations_migrated_at`. Direction mapping is explicit
  (`split_children[i]` → `(child, split_from, parent)`). Afterwards the meta keys are
  **read-only legacy**: never deleted (meta updates are merge-only, `findings.py:923-924`, so
  deletion is impossible anyway), never re-read as authority. Core continues writing
  `meta.recurrence_of`; that stays core's.
- When a `distinct_from` lands, existing `meta.similar_to` annotations on the pair are
  **retained and suppressed at read time** — the annotation is an advisory snapshot
  (`similarity.py:14-17`), and history stays honest.

### S1b — Query and export conveniences (independent of S1a; own cost)

Split from S1a (review: bundling obscured both costs):

- `backlinks(id)`: typed edges from `finding_relations` **unioned** with blockers rows, each
  edge labelled with origin and lifecycle fields.
- `group_by="tag"`: a real line item, not a bullet — `group_by` is interpolated as a column
  name today (`findings.py:1127-1135`) and `tags` is a JSON array, so this is a `json_each`
  rewrite whose response carries `rows_total` and `multi_valued: true`, because per-tag counts
  are non-additive and a caller must not silently sum them. Meta-key aggregation ships the
  same way.
- `export-csv` gains `reported_at_commit` / `reported_at_ref` (stored columns the export
  currently drops, `findings.py:1913` region). Relations get a CSV round-trip design (separate
  artifact + old→new id map, since import re-mints ids) only when they exist to export.
- Golden-schema budget: each stage lands as **one** reviewed regen commit of
  `tests/golden/mcp_schema.json`, serialized with any concurrent exposure-layer work.

### S2 — Semantic candidates at the seam (prevention; gated on S0)

- **The provider call happens OUTSIDE the write lock** (mandatory fix 1 — corroborated FATAL).
  Two-phase: at `add` / `batch_add` entry, **before** `db.txn` opens, embed the observation
  text(s) and populate the vector cache in a short separate transaction; the pre-add resolver
  receives the precomputed query vector in the observation dict and performs pure arithmetic
  plus one read under the lock. Provider unreachable ⇒ no vector ⇒ the resolver degrades to
  today's lexical scoring. A batch embeds all members in **one** provider call, then opens its
  one DB transaction. (The resolver cannot run outside a transaction — `db.py:417-423` raises —
  which is exactly why the I/O must complete before the transaction begins.)
- **Scoring cost is measured and fine:** 500 pre-normalized 1024-dim dot products ≈ 10.9 ms
  (naive three-pass ≈ 27.9 ms), versus ≈ 34.5 ms for the *shipped* lexical trigram pass under
  the same lock. Recorded here so it is not re-litigated. The cache stores L2-normalized
  float32; scoring is a single dot product.
- **Vector cache with a real identity** (mandatory fix 6 — Codex-only catch): new table, e.g.
  `finding_vectors(finding_id, cache_key, dims, vec BLOB, created_at)`, keyed on
  **(provider, model, revision, normalization_version, content_hash)**. Content hash alone is
  not an identity: a same-dimension model swap would silently score vectors from incompatible
  spaces against a calibrated threshold — the confidently-wrong-instrument class this line has
  already produced twice. Mixed keys are never scored together; switching models requires an
  atomic full rebuild before activation. Access goes through a new sanctioned accessor on the
  findings read surface (preserving `similarity.py`'s zero-SQL invariant,
  `findings.py:982-983`) — reuse `embeddings.py`'s pack/cosine math, **not** its storage
  design, which is precisely what lacks model identity.
- **The candidate pool bound is a measured decision** (mandatory fix 8): dropping the category
  block while inheriting `CANDIDATE_POOL_LIMIT=500` `ORDER BY created_at DESC` would turn a
  full-category scan into a newest-500-of-1,207 recency window — evicting exactly the age band
  the motivating pair sits in. Options S0 must decide between, by measurement through the real
  path: full live population with pre-normalized dot (~26 ms measured for 1,207 rows), or a
  recall-shaped bound.
- **Honest response loop** (mandatory fix 9): MCP callers **already receive**
  `meta.similar_to` — `add` returns the full row and `row_to_dict` parses meta
  (`findings.py:1324-1336`, `db.py:549-550`). The deliverables are: (a) a structured `similar`
  field in the response, populated from *this call's* resolver output and **absent** on
  bump/reopen (resolvers never ran there — `findings.py:534`, `:540` — and echoing the matched
  row's stored annotation would surface a stale snapshot from a different observation); batch
  candidate sets are input-order-dependent by design and documented as such; (b) a CLI echo
  that **preserves the first-line grammar** (`Added: CB-N` stays byte-stable — verified
  consumers echo stdout verbatim), candidates on a second line or behind `--json`; (c) the
  plain statement that filers which discard the response gain nothing until changed — that is
  a per-caller adoption cost, not a free win — with one worked auto-filer example. The earlier
  "every verdict feeds the S0 gold set" sentence is **cut**: runtime `relate()` cannot edit a
  repo-owned gold file; verdicts flow to the gold set only through the S0 labelling protocol.
- **Provider opt-in preconditions, not follow-ups** (mandatory fix 10): D2's off-by-default
  stance stands, and opting in makes codebugs a network *client* for the first time — in a
  tool whose selling point is local-and-model-free, a configurable endpoint receiving full
  finding bodies is an exfiltration surface. Preconditions: TLS/auth handling, endpoint
  allowlist, connect/read timeouts, retry ceiling, payload cap, a circuit breaker after k
  consecutive failures, typed `resolver_errors` stamping (`provider_unreachable` /
  `provider_timeout` / `dimension_mismatch` — the per-row plumbing already exists and is
  queryable, `db.py:449-460`, `findings.py:1113-1115`; what is missing is the aggregate view),
  the error rate as a first-class report line, and a privacy warning that finding text leaves
  the machine.
- **Known boundary, kept:** the annotator pool excludes `fixed` rows; semantic
  reopen-by-similarity stays out. Any `similar-to-fixed` reporting is display-only, capped
  (top-N, higher threshold), and never feeds a judge.

### S3 — Consolidation loop: DEFERRED

Deferred outright (review C-A5, adopted). Revisit only if S0's adjudicated base rate ever
justifies a periodic loop, and then only with: the proposal ledger (durable rejections) already
in place; **brute-force top-k** over the cached vectors — no ANN at ~3k rows, per
`embeddings.py:99`'s standing "fine for <10K" judgment and the package's no-runtime-dependency
promise; and hard cost bounds up front (top-k, max pairs/run, token budget, timeout, cadence,
overflow behavior). Its batch-digest output, if built, names a concrete artifact (path +
schema) — "feeds the batch-codebugs skill" is not an interface.

## 4. Decision points (owner)

- **D1** Relations as a table vs meta convention. The review settled the technical half: meta
  cannot delete keys, cannot be indexed, and a retractable audited ledger needs the table.
  Remaining decision is only whether to pay the schema cost now.
- **D2** Embedding provider default stays OFF; per-tracker opt-in gated on the §S2
  preconditions list.
- **D3** CB-46 merge policy ratification is a prerequisite for *executing* `duplicate_of`
  proposals; until then they queue. Inputs CB-46 should absorb from this line: severity
  reconciliation on merge (recommend survivor takes max, else a merge can silently lower
  urgency) and the `duplicate_of`-assertion vs `merged_into`-execution split.
- **D5** Fingerprint immutability vs category renames (CB-61's conflict with
  `findings.py:694`): out of scope; S1/S2 do not touch stored fingerprints.

(D4 deleted — it re-litigated a CB-46 question this RFC declares out of scope.)

## 5. Non-goals

- Mass historical backfill/merge — owned by CB-46.
- Auto-merge in any form; machine writes reach facts only through accepted proposals.
- Grouping by category, file, or tag as a *predicate* — discovery axes only.
- ANN indexing at current corpus scale.
- Replacing batch-codebugs / bug-clustering judgment.
- Cross-tracker federation.

## 6. Honest value framing

The adjudicated base rate says a periodic dedup loop is not the value — confirmed 2 clusters
per 1,137 rows (upper bound 10, to be collapsed by S0's adjudication) — which is why S3 is
deferred. Of the citation graph, 93.1% is bare references; prose mining types only the ~7%
tail. The case for this RFC is therefore narrower and stronger than "mine the backlog": **(i)**
the structure humans already recorded in typed meta (`split_children`, `sprint`, `plan`) and in
~145 explicit prose relations becomes queryable, retractable data — recovering existing
knowledge, not manufacturing it — and every *future* verdict is captured at filing for the
cost of one `relate()` call; **(ii)** the next CB-2882 gets caught at the seam instead of
costing a duplicated root-cause analysis, a divergent severity, and a merge decision two weeks
later; **(iii)** S0 keeps all of it measured — this line produced two confidently-wrong
instruments in two days, and the only reason they were caught is that someone re-measured with
a different instrument.

## 7. Staging & fit

- **S0 → S1a → S1b → S2 → (S3 deferred).** S0's adjudication pass gates S2's threshold; S1a
  and S1b are model-free and can land while S0 runs.
- **Exposure-layer decision (was §7's contradiction):** S1's three surfaces ship
  **hand-written under today's pattern now**, and are listed in
  `RFC-exposure-layer-2026-08-17.md`'s migration scope as three more (thin) ops to convert.
  Waiting on that RFC's ratification plus its migration order would block S1 behind unrelated
  work; a three-op hand-written debt is smaller than the coupling. New tools are a deliberate
  wire change: **one** reviewed golden regen per stage, serialized with exposure-layer
  branches.

---

## Appendix — Adversarial Review x2 Corrections (2026-08-17)

Run per protocol: Opus adversary + Codex/GPT-5.6-Sol attacker in parallel (both read-only,
both against the real code), Opus defender, judge on the session model. Verdict:
**FAIL-REVISE, health 5/10**, 12 mandatory fixes — all encoded above.

**Corroborated by both models (high-confidence; all conceded):** provider I/O inside the
write-locked transaction; gold-set circularity + duplicate/relation conflation; no proposal
state in the schema (P5 unimplementable as drafted); cross-category pool with the inherited
newest-500 bound destroying recall; `blocked_by` both allowed and forbidden; integrity guards
regressed vs `blockers.py`; hard-delete retraction; "nobody sees the annotation" false on MCP;
lazy migration → permanent dual source of truth; symmetric/directed representation
underspecified; "never fed" wording false.

**Single-model catches — the reason x2 exists.** Opus-only: **`recurrence_of` in the enum
forges the exact link `_RESERVED_META_KEYS` refuses** (the sharpest finding of the review;
`findings.py:215-219` names the attack verbatim); the zero-SQL invariant; the 74 windows being
mostly live→fixed pairs outside the annotator's population; the base rate doing two
contradictory jobs. Codex-only: **content-hash-only vector-cache identity** (a same-dimension
model swap silently scores incompatible spaces against a calibrated threshold — the single
best finding); the inert `PRAGMA foreign_keys`; durable-rejection ledger (S3 otherwise re-pays
for the same verdicts forever); the exfiltration framing of the provider endpoint; the
cross-repo gold-set write hole; the verified auto-filer that discards stdout. Each of the two
best findings would have shipped under a monoculture review.

**Dismissed after defense (recorded so they are not re-raised):** per-add cosine cost as a
standalone FATAL (measured: 10.9–27.9 ms vs 34.5 ms for the shipped lexical pass under the
same lock; the real hazard was the batch multiplier, folded into the transaction fix);
"resolver failures are stderr-only" (they are stamped queryably per row under
`resolver_errors`; only the aggregate-signal gap survived); "two meanings of
`min_pair_score`" (the shipped meaning is unambiguously the diameter, `similarity.py:303-317`);
"S1 violates the exposure RFC's golden gate" (that gate scopes migration branches; deliberate
new wire surface is its own reviewed regen — only the sequencing concern survived, resolved in
§7); "S2 reverses the model-free stance" (default stays off; the honest framing — opting in
makes codebugs a network client — is now in §S2).

**Process note, kept deliberately:** the defender supported one ruling (the golden-gate
defense) with a "verbatim quote" that was in fact a paraphrase, and misstated `findings`'
position in the exposure migration order (ninth, not seventh). The judge caught it by
re-reading the source and the ruling survived on the text itself — but a defense quote is a
hypothesis until opened, same as an attack citation. Every other load-bearing citation in the
review was verified by at least two of the four voices; the RFC's own citations were verified
accurate by all of them (the review's one unambiguous compliment).
