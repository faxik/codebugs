# PLAN — `finding_relations`: the ratified half of the identity ledger

**Status:** scope approved by the owner 2026-08-17/18. **Revision 2**, after adversarial review x2
(Opus adversary + Codex/GPT-5.6-Sol attacker, Opus defender/judge). Implementation not started.
**Supersedes in scope:** RFC §S1a's two-table build — `relation_proposals` is DEFERRED.
**Evidence base:** `../autosorter/.claude/plans/RESULT-relation-ledger-demand-2026-08-17.md`.
**Review verdict on revision 1:** 5/10 — schema and scope sound, migration engine required redesign.
See the appendix for what changed and why.

---

## 1. What was decided

Measurement falsified the RFC's premise that the semantic scorer is the primary producer:

| producer | volume |
|---|---:|
| humans, in `meta` keys | **837 edges over 76 keys** (reproducible baseline, §2) |
| humans, in prose | 4,132 distinct pairs — **not migrated** (§6) |
| the semantic scorer | 172 pairs at cosine ≥ 0.78 |

Owner rulings:

1. **Build `finding_relations`; migrate the meta keys only; leave prose alone.** A cheap LLM is
   acceptable for classification, but it must be batched (2026-08-18).
2. **Cure the stock first, verify it, then tighten the flow.** Once the migration has landed and
   been shown to work, mark the legacy relation keys **`obsolete` with a warning** on `add`/`update`
   — warn, never refuse — and only later consider hard enforcement (2026-08-18). §11.

`relation_proposals` stays deferred: it has no producer once §7 stops classifying at migration time
(see §5, manifest-first). There is no semantic scoring inside codebugs and will not be until the
embedding-provider decision (RFC §S2).

## 2. The measurement, and its honest limits

Script: `~/.cache/codebugs-identity/relation_demand.py`, over the 3,176-row export.

**Reproducible baseline — these are the numbers this plan uses:**

- **164 distinct relation-shaped `meta` key names**; **76** yield ≥1 edge; **837 edges** over
  **399 cards**; 425 card-key pairs yield edges, 141 yield nothing.
- **0 extracted ids are absent** from the tracker.
- **612 of 974 edges (62.8%) target NON-LIVE cards** — overwhelmingly `fixed`. This ratio holds in
  every population computed (62.7–62.8%), and it is why §5 filters on **existence, not liveness**.
- Share of new cards carrying such a key: 0.2% (Mar) → **~25% and holding**.

> **Revision-1 correction.** R1 claimed "185 keys / 81 yielding / 974 edges / 546 cards". Those came
> from an ad-hoc widened word list, not from the checked-in script, and no filter configuration
> reproduces the tuple. The figures above are what `relation_demand.py` actually prints. The exact
> script that generates the candidate set is checked in as part of this work (§4), so the migration
> universe is reconstructible from the repository rather than from a cache directory.

**The single most important property of this data: the key NAME does not predict the value TYPE,
and it does not predict the value's POLARITY either.**

| key | what it actually holds |
|---|---|
| `duplicate_of` | **file paths** (`src/autosorter/core/ocr_quality.py`) — 2 of 4 |
| `related_bugs` | **autosorter hex ids** from a *different tracker* |
| `owns_cards` | mixes a CB id with an autosorter hex (`CB-2920`, `CB-1513`, `358a773e-c95`) |
| `related_plan` / `related_memory` | file paths, memory slugs |
| `followup_id` | sprint ids (`FU-W1-4`) |
| `found_during` | prose; 72 values, **16 carry no id, 8 are non-leading, 7 carry several** |
| `adjacent_not_duplicate` | semantically `distinct_from` |
| `related_distinct` | **negative**, despite matching the "mechanical" `related*` pattern |
| `introduced_by` | two positive values and one **negated** (`CB-3115`: *"not introduced — …"*) |
| `distinct_from` | a **dict** (`CB-2540`) and a **range** (`CB-1899`: `'CB-52..CB-63'`) |
| `CB-3017 blocks` | `'CB-2942-W1'` — a work-slice, matched by `\bCB-2942\b` |

`CB-3115` is the case that decides the architecture: **a key-level classification cannot express
value-level polarity.** Sample two positive `introduced_by` values, classify the key positive, and
the negated third migrates as a positive fact. No amount of hand-confirmation *at the key level*
fixes that. Hence §5.

## 3. Schema — one table

Module `src/codebugs/relations.py`, following the `blockers.py` idiom: schema constant at
`blockers.py:14`, indexes `:32-34`, `ensure_schema` `:40`, `register_schema` `:568-570`, tool
provider `:646`, CLI provider `:722`.

```sql
CREATE TABLE IF NOT EXISTS finding_relations (
  id INTEGER PRIMARY KEY,
  src_id TEXT NOT NULL,
  rel TEXT NOT NULL CHECK (rel IN
    ('duplicate_of','split_from','follow_up_of','found_during','distinct_from','related_to')),
  dst_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source TEXT NOT NULL,
  note TEXT,
  retracted_at TEXT, retracted_by TEXT, retracted_reason TEXT,
  CHECK (src_id != dst_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_live
  ON finding_relations(src_id, rel, dst_id) WHERE retracted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_relations_src ON finding_relations(src_id);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON finding_relations(dst_id);
```

`register_schema("relations", ensure_schema, depends_on=("findings",))` — signature confirmed at
`db.py:52-67`.

**Registration is THREE hardcoded lists, not one.** Omitting any of them ships a silently absent
feature, and `CREATE TABLE IF NOT EXISTS` raises no error to catch:

- `db.py:1029-1051` `_ensure_modules_loaded` — the domain import list;
- `server.py:201` `SERVER_NAMES` — MCP server naming;
- `cli.py:104` `--mode` choices.

A registry test asserts all three (`tests/test_registry.py` enumerates domains already).

**Invariants:**

- **Endpoint EXISTENCE validated in `relate()`** at the application layer via `EntityRef.exists()`
  (`entities.py:162`). `db._open` (`db.py:1054-1105`) sets only WAL and `busy_timeout` — but note
  `findings.py:105`/`:139` runs `PRAGMA foreign_keys=OFF/ON` during the legacy status migration, so
  FK enforcement is **nondeterministic per connection**. That is an argument *for* application-layer
  validation, not against it, and it is why no FK is declared.
- **Symmetric set is `{distinct_from, related_to}` ONLY**, stored in canonical orientation
  (lexicographic min as `src`) so the unique index enforces one edge per pair.
- **`duplicate_of` is DIRECTED: loser → survivor.** It is not symmetric in the sense that matters —
  it names which card dies. Canonicalising it inverts the survivor in **3 of 3 real cases**
  (`CB-878→CB-877`, `CB-2946→CB-2935`, `CB-2251→CB-2227`; in each the stored `src` would become the
  survivor). The RFC grouped it with `distinct_from` in error and revision 1 inherited that.
- **Retraction is a tombstone, never a DELETE.** `unrelate()` sets `retracted_at/by/reason`; readers
  filter `retracted_at IS NULL`.
- **Contradiction guard:** reject a live `duplicate_of` where a live `distinct_from` exists on the
  same unordered pair, and vice versa.
- **`relate()` runs under `with db.txn(conn)`** — the reentrant abstraction at `db.py:484-515`.
  **Never a raw `BEGIN IMMEDIATE`**: `db.py:486` says so outright, and `merge.py:257` /
  `capacity.py:214` carry explicit "no raw BEGIN IMMEDIATE here now (CB-40)" notes, because
  assigning `isolation_level` commits the caller's open transaction. Revision 1 carried the raw form
  verbatim from the RFC; at 837 inserts that is CB-40 at scale.
- Re-relating a live edge is an idempotent no-op returning the existing row.

## 4. Vocabulary — the RFC's five, plus exactly one

RFC vocabulary: `{duplicate_of, split_from, follow_up_of, found_during, distinct_from}`.
**One term is added: `related_to` (symmetric).**

| relation | kind | absorbs | edges |
|---|---|---|---:|
| `related_to` | symmetric | `related`, `related_codebugs`, `related_cb`, `related_to`, `sibling*`, `siblings`, `closest_sibling(s)`, `cross_links`, **`anchor`**, `duplicate_cluster` | ~720 |
| `split_from` | directed, child → parent | `split_from`, `parent`, `parents`, `parent_*`, and **reversed**: `split_children`, `members`, `owns_cards`, `spawned_cards` | ~90 |
| `found_during` | directed | `found_during`, `discovered_during`, `surfaced_by` | ~90 |
| `distinct_from` | symmetric | `distinct_from`, `adjacent_not_duplicate`, `distinct_from_parent`, `related_distinct` | ~70 |
| `duplicate_of` | directed, loser → survivor | `duplicate_of`, `residual_merged_into` | **2** |
| `follow_up_of` | directed | `follow_up_of`, `follow_up_to`, `followups` (reversed) | ~9 |

> **Revision-2 corrections, found by measuring the values rather than reading the key names**
> (`~/.cache/codebugs-identity/validate_mapping.py`, run before the second review round):
>
> - **`anchor` is NOT `duplicate_of` — it is a cluster anchor.** 16 cards carry it; **12 point at
>   the single card `CB-1566`**, and it forms chains (`CB-1889 → CB-1878 → CB-1566`,
>   `CB-1906 → CB-1895 → CB-1566`). Twelve cards cannot all be duplicates of one card, and
>   `duplicate_of` is transitive in a way a hub label is not. R2 had it as `duplicate_of`, which
>   would have manufactured **12 false merge candidates** in the one relation that must be
>   trustworthy. Reclassified to `related_to`. This is exactly the failure §9 predicted.
> - **`duplicate_filed_and_retracted` is a RETRACTION marker, not an assertion.** `CB-2227` carries
>   it with value `CB-2251`, while `CB-2251` carries `duplicate_of = CB-2227`. The pair holds a
>   claim *and its withdrawal*. Migrating the marker as a positive edge would re-assert something a
>   human explicitly retracted. **Excluded**; optionally imported as a pre-retracted tombstone.
> - **Real `duplicate_of` volume is 2 edges, not ~20.** R2's figure counted `anchor`'s 16.
>
> **Revision-1 correction — `part_of` is DROPPED.** R1 added it for `parent*`. But
> `grouping.py:85` already declares `LINEAGE_PARENT_KEYS = ("split_from", "parent")`, with a comment
> stating `parent` is included *"on purpose… the same relation with 20x the rows"*, and
> `tests/test_grouping.py:306` pins it. Introducing `part_of` would make one legacy fact mean
> `split_from` to the shipped reader and `part_of` to the new ledger — **the exact two-authority
> divergence this build exists to cure.** Mapping `parent → split_from` matches the shipped reader,
> removes a vocabulary term, and removes the acyclicity question with it.

**Exclusions**, enforced in `relate()` for hand-callers **and** in the mapping table for the
migration path — the latter matters because `relate()` receives a *term* and can never see which
meta key it came from:

- `recurrence_of` — core-owned (`_RESERVED_META_KEYS`, `findings.py:219`). **That frozenset is an
  exact set of four names, NOT a `recurrence_*` family** — R1 claimed otherwise; the family exists
  in the corpus precisely because core does not guard it. The `recurrence_*` family is excluded **by
  this plan's own prefix rule**.
- `blocked_by`, `blocked_on`, `blocked_behind`, `blocked_merge`, `blocked`, `ocr_rerun_blocked_by`,
  and **`blocks`** (18 edges — the *inverse* of `blocked_by`, which no literal enumeration caught).
  Prefix-matched on `block`, never enumerated. Owned by the blockers module.
- `similar_to` — annotator-owned.

**`blocked*`, `recurrence*` and `blocks` are hand-reviewed regardless of edge count.**

Provenance: every migrated edge carries `note = "bootstrap: meta.<original_key>"`. This preserves
the key name only — not the value, its qualifier, or the classification rationale — so it supports
auditing, not full reconstruction. The manifest (§5) is what preserves the rest.

## 5. Migration — a reviewed manifest, then a dumb importer

**The bootstrap does NOT classify. It imports a checked-in, human-reviewed artifact.** This is the
review's structural recommendation, and it collapses six separate defects into one artifact:
unreviewed model judgment reaching an authoritative table, re-run resurrection, a spoofable stamp,
an undefined candidate universe, unreproducible numbers, and a missing bootstrap CLI.

### Stage 1 — `relations-manifest` (generate)

Emits `relations_bootstrap_manifest.jsonl`, one line per candidate edge:

```json
{"src":"CB-3105","key":"spawned_cards","raw":"['CB-3112','CB-3113']","dst":"CB-3112",
 "dst_status":"open","rel":"split_from","direction":"reversed","polarity":"positive",
 "classifier":"llm|mechanical|hand","confidence":0.91,"disposition":null}
```

Extraction rules — **each exists because a real row breaks the naive version**:

1. **Candidate keys come from a checked-in enumerated allowlist**, `relations_bootstrap_map.py`,
   holding every key name with its relation, direction and edge count. **Unlisted keys fail closed,
   are counted, and are printed.** Revision 1 left this universe undefined while claiming to be
   "value-shape-driven"; the truth is *name-selected candidates with a value-shape emit gate*, and
   the plan now says so.
2. **Extract ids from every shape: bare string, prose, list, `dict` (keys AND values), and range
   notation.** `CB-2540 distinct_from` is a dict of 3; `CB-1899` is `['CB-52..CB-63', …]` where a
   plain regex takes 2 ids and drops the 10 between. Both are **suppression** relations, where
   under-extraction causes a future false merge — the dangerous direction.
3. **Drop ids ABSENT from the target tracker** (existence, not liveness) and drop self-edges.
   Revision 1 said "not a live row", which would have discarded **62.8%** of the corpus — precisely
   the live→closed class the ledger exists to resolve.
4. **Polarity is decided per VALUE, never inherited from the key.** Any value matching a negation
   pattern (`not `, `no longer`, `do not`, `distinct`, `NOT a`, `adjacent`) is marked
   `polarity:"negative"`, excluded from positive relations, and routed to human review. Fixtures:
   `CB-3115 introduced_by`, `CB-2310 related_distinct`, `CB-3085 not_introduced_by`,
   `CB-2266 do_not_absorb`, `CB-2537 duplicate_filing_gap_from_cb2596`.

   **The screen runs ONLY on keys mapping to a POSITIVE relation.** Measured: it flags 40 of 585
   edge-yielding card-key pairs, but a large share of those are legitimate `distinct_from` rows
   whose values *properly* contain "distinct" (`CB-1615`, `CB-1629`, `CB-1857`, …). Screening a
   negative relation for negation words routes correct suppression edges to review for nothing —
   and under-migrating `distinct_from` is the dangerous direction (rule 2).
5. **Direction is a REQUIRED per-key field that fails closed** — never a default with named
   exceptions. Reversed (parent names its children): `split_children`, `members`, `owns_cards`,
   `spawned_cards`, `followups`. Note `parents` is **default** direction, despite R1 listing it as
   reversed.
6. **Work-slice suffixes are flagged, not matched.** `CB-3017 blocks='CB-2942-W1'` — `-` is a word
   boundary, so `\bCB-2942\b` turns a slice reference into a parent-card edge.
7. **Star-graph guard.** `CB-2537`'s value names four cards that are duplicates *of each other*, not
   of `CB-2537`. Any value yielding ≥3 ids under a `duplicate_of`-family key is routed to review.

The LLM's role is confined to **Stage 1, batched, one call over the ~92 key groups** (not 974
edges), each item carrying the key name, its edge count and two real value samples. The prompt
**states there is no target distribution** — the 2026-08-17 adjudication prompt said "most true
positives land in `related`" and got zero `unrelated` back. Its output is a *proposal in the
manifest*, never a write.

### Stage 2 — human review

Review by key group, ~92 groups. Every group at ≥10 edges is confirmed by hand (14 keys = 870 edges
= 83% of volume), **plus** every `blocked*` / `recurrence*` / `blocks` group and every row marked
`polarity:"negative"` or flagged by rules 6–7, regardless of count. Dispositions are written into
the manifest, which is then committed.

### Stage 3 — `relations-import` (dumb importer)

Reads **only the committed manifest**. For each row with `disposition:"accept"`: validate endpoint
existence, reject self-edges, insert via the same `relate()` hand-callers use — no privileged path,
so canonicalization, the contradiction guard and idempotency apply identically. `source` records the
judgment, not just the mechanism: **`bootstrap:hand` vs `bootstrap:llm`**, so an unreviewed edge is
distinguishable forever.

The whole import runs inside **one** `db.txn(conn)` — read, insert, and run-record — so a failure
leaves no partial ledger.

### Re-run semantics — stated, not assumed

**A retracted edge stays retracted.** `relate()`'s idempotency covers only *live* edges; because the
unique index is `WHERE retracted_at IS NULL`, a naive second run would resurrect everything
deliberately tombstoned. The importer therefore **skips any (src, rel, dst) carrying a retraction
tombstone**, unless `--resurrect` is passed explicitly.

**There is no per-card stamp.** Revision 1 stamped `meta.relations_migrated_at` on every processed
card. That was wrong three ways: any meta write rewrites `updated_at` (`findings.py:940`), which
feeds `_match_fingerprint`'s reopen-vs-recurrence tiebreaker (`findings.py:465-483`); the key is
**spoofable**, since `_validate_meta_keys` (`:244-265`) protects only the reserved set; and it
**survives CSV import while the relation rows do not** (`findings.py:1874`, `:1929`), so an
export→import cycle would yield every card stamped and zero edges, permanently disabling the
migration. It is replaced by a **bootstrap-run record** carrying manifest hash, counts and
timestamps.

*(Measured, for honesty: the `updated_at` exposure is currently **zero** — no fingerprint in the
target tracker has two terminal-status rows, so there is nothing to reorder. Both attackers rated
this SERIOUS without measuring it. It is latent, not live — but the run-record is cheaper than the
stamp anyway, so the fix costs nothing.)*

### The target tracker, named

**`/home/faxik/w/autosorter/.codebugs/findings.db`** (3,211 rows) — where the edges live. The code
lands in `/home/faxik/w/codebugs`, whose own tracker holds **89 rows**. `db.connect()` resolves from
cwd, and there are **13 trackers on this machine**; the migration names its target explicitly and
refuses to run against any other without `--tracker-root`. Each other tracker has its own
vocabulary that `relations_bootstrap_map.py` has never seen — hence rule 1's fail-closed.

## 6. Explicitly out of scope

- **Prose migration.** 4,132 pairs, and `CB-\d+` counts any mention — including negative ones.
  Loading them unsigned rebuilds the trap that once joined a flake card to a feature card.
- **`relation_proposals`** — deferred; the manifest performs its staging role for a one-shot.
- **`backlinks()` / `group_by="tag"` / CSV round-trip** (RFC §S1b) — separate stage. **Known
  consequence, stated:** a tracker restored from its own CSV export currently loses the ledger
  entirely. That is acceptable only because the manifest is committed and the import is re-runnable.
- **Acting on any edge.** `duplicate_of` asserts; it merges nothing. Merge policy is CB-46, by hand.
- **`distinct_from` suppression at read time.** The RFC required it; `similarity.py` groups via DSU
  over category blocks and reads no relations table, so **`distinct_from` is inert until that
  integration ships.** R1 claimed a wrong `distinct_from` "suppresses every discovery path" — that
  is false today, and the claim is withdrawn rather than quietly relied upon.

## 7. Surface

| tool | purpose |
|---|---|
| `relations_relate(src, rel, dst, source, note?)` | assert; validates, canonicalizes, guards |
| `relations_unrelate(src, rel, dst, retracted_by, reason)` | tombstone it |
| `relations_query(id?, rel?, include_retracted?)` | edges touching a card, both directions |
| `relations_manifest(out)` / `relations_import(manifest, --dry-run)` | the two migration stages |

`source` and `retracted_by` are **caller-supplied parameters**, not placeholders — an audited ledger
whose actor is hardcoded is not audited. `active_suppressions()` lists live `distinct_from` edges.

## 8. Tests (TDD — red first)

1. `CHECK(src_id != dst_id)` rejects a self-edge.
2. `CHECK(rel IN …)` rejects an unknown relation.
3. Canonical orientation: `relate(B, related_to, A)` then `relate(A, related_to, B)` → **one** row.
4. **`duplicate_of` is NOT canonicalized**: pin `(CB-2251, duplicate_of, CB-2227)` in loser→survivor
   orientation and assert the reverse is a distinct row.
5. Endpoint validation rejects an absent `dst_id`.
6. **A `fixed` target migrates** — the 62.8% regression test.
7. `unrelate` tombstones; the unique index then permits re-relating.
8. **Retraction survives re-import**: relate → unrelate → re-run import → edge stays retracted.
9. Contradiction guard: `duplicate_of` refused where live `distinct_from` exists.
10. `recurrence_of`, `blocked_by` and **`blocks`** produce zero edges from the importer.
11. Direction fixtures: `spawned_cards`, `owns_cards`, `members` (dict), `split_children` all yield
    child→parent; `parents` yields default direction.
12. Shape fixtures: dict (`CB-2540`), range (`CB-1899`), multi-id, non-CB (`related_plan` → zero).
13. Polarity fixture: `CB-3115 introduced_by` (negated) yields zero positive edges.
14. Foreign-id fixture: `origin_id='CB-1'` from a worktree tracker does not become an edge.
15. Unlisted key fails closed and is counted.
16. `relate()` under an ambient transaction does not commit it (CB-40 regression).
17. Registry: `relations` is discoverable in `db.py`, `server.py` and `cli.py`.
18. `--dry-run` writes nothing.

The real MCP wire gate is `tests/test_boundary.py::TestMcpWireSchema` (regen instructions at `:162`)
plus `tests/test_server.py:162`; the golden-schema regen is one reviewed commit, not a test.

## 9. Risks

- **`related_to` is a junk drawer** — it absorbs ~72% of edges. This remains the most likely thing
  to be wrong here, and the mitigation is weaker than R1 claimed: `note` preserves the key *name*
  only, and the legacy value it points back to is itself mutable via `update(meta_update)`. The
  **manifest** is the real mitigation — it snapshots the raw value, so a later pass can split
  `related_to` on evidence rather than on a name.
- **`anchor` is classified `duplicate_of`** (a 12–16 card cluster chain pointing at `CB-1566`). It is
  in the ≥10 hand-confirm band; confirm the chain is duplicate-ness and not merely clustering.
- **The manifest costs 1–2 hours of human review**, up from R1's estimated ~10 minutes. That is the
  price of not letting a cheap model write facts.
- **This cures the stock, not the flow** — see §11.

## 10. Verification that the stock is actually cured

The owner's phase 2 requires a measurable criterion, not an impression. After import:

    ledger_share = edges_in_finding_relations / (edges_in_finding_relations + residual_meta_edges)

computed by re-running the §2 script against the post-migration tracker. Phase 3 does not start
until this is reported. Expected ≥ 0.9 for the mapped vocabulary, with the residual being the 141
non-yielding card-key pairs and the deliberately excluded blockers/recurrence families.

## 11. Phase 3 — `obsolete` with a warning (owner ruling, 2026-08-18)

Once §10 reports success:

- `add()` and `update()` emit a **deprecation warning** when a supplied meta key matches the
  relation-key pattern, naming `relations_relate` as the replacement. **The write still succeeds.**
- Only later, and as a separate decision, is hard refusal considered.

Order matters and is deliberate: tightening `add` before the stock is cured would refuse keys for
which no working alternative yet exists, and would block unrelated sessions mid-flight. This
directly answers the review's finding that the build otherwise cures the stock while leaving the
producer untouched.

---

## Appendix — Adversarial Review x2 corrections (2026-08-18)

Attackers: Opus adversary + Codex/GPT-5.6-Sol, in parallel; Opus defender/judge over the union.
Revision 1 scored **5/10**. Every claim below was re-verified by the orchestrator by direct Read.

**Corroborated by both models** (highest confidence): machine classification writing to an
authoritative table unreviewed; the `updated_at` stamp; three-place registration; CSV losing
relations; §2's numbers not reproducing; stale `BEGIN IMMEDIATE`; `related_to` as a junk drawer.

**Caught by Codex, missed by Opus:**
- **Value-level polarity inside one key** (`CB-3115 introduced_by`) — the deepest finding in either
  file. It invalidates key-level classification as a *method*, and is why §5 is manifest-first.
- **`grouping.py:85` already defines `parent` as `split_from` lineage**, pinned by
  `tests/test_grouping.py:306` — which killed `part_of` outright.
- **`distinct_from` is inert**: `similarity.py` reads no relations table.
- **Re-run resurrection** of retracted edges.
- `rel TEXT` vs the house `CHECK` idiom; the missing actor contract; `PRAGMA foreign_keys=ON` left
  by the legacy migration; `owns_cards` as a reversed key.

**Caught by Opus, missed by Codex:**
- **`duplicate_of` canonicalization inverts the survivor** — 3 of 3 real cases. Highest severity in
  either file.
- **Liveness vs existence** across three sections — 62.8% of the migration silently dropped.
- The value-shape/name-driven self-contradiction and the undefined candidate universe.
- Negative-polarity *key names* (the mirror of Codex's value-level finding).
- `members` dict shape; name-filter blind spots (`anchor`, `discovered_during`, `surfaced_by`,
  `cross_links`); the multi-tracker ambiguity; `CB-2942-W1` sub-slice matching.

**Dismissed by the judge, and why:**
- *Foreign-ID collision as a blocker* — real rows (`CB-2754`/`CB-2755` → `CB-1`/`CB-2`), but
  `origin_id` matches no candidate key. Retained as test 14.
- *"A value-shape sweep pulls in `meta.notes`"* — arithmetic correct (1,234 edges), but the
  algorithm is name-selected and never schedules that sweep. The defect was the undefined universe.
- *The `updated_at` finding, as SERIOUS* — measured exposure is **zero**; no fingerprint has two
  terminal-status rows. Both models rated it on mechanism without measuring. Downgraded to a note;
  the run-record replaces the stamp anyway.
- *`part_of` "explicitly permits cycles"* — overread of test 3. Moot: `part_of` is gone.

**Process note.** The raw `BEGIN IMMEDIATE` and the `duplicate_of` symmetry both came **verbatim
from the RFC, which had itself passed an adversarial review x2**. A review that passes an artifact
does not immunize the claims that artifact carries into its successors.
