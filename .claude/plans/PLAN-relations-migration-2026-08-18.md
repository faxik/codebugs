# PLAN — migrating legacy relation meta keys into `finding_relations`

**Status: NOT IMPLEMENTABLE YET.** This document carries the open defects from two adversarial
review rounds. It is split out of `PLAN-finding-relations-2026-08-18.md` (the core table + tools),
which is sound and proceeds independently.

**Why split** (owner decision, 2026-08-18): across two cross-model review rounds, every FATAL
finding landed in the migration, and none in the core. The core is ~200 lines of well-idiomed code
that mutates nothing; the migration is ~100 individual judgements of the form *"this key name means
this relation, in this direction"*, each of which can be wrong. Holding the core hostage to the
migration's governance was costing a working ledger for no safety gain.

**Do not implement from this document as it stands.** Close the §3 defects first, then give it its
own review round.

---

## 1. The problem, restated

3,176 cards carry relation facts in ad-hoc JSON `meta` keys: **164 distinct key names** for roughly
five concepts, **837 edges** over 76 yielding keys (reproducible baseline from
`~/.cache/codebugs-identity/relation_demand.py`). None of it is queryable, and `meta` keys cannot be
deleted (`findings.py:923-924` is `dict.update`, merge-only).

Owner rulings that stand:
- Migrate **meta keys only**; prose (4,132 pairs) is out of scope — its `CB-\d+` mentions are
  unsigned, and loading them would rebuild the trap that once joined a flake card to a feature card.
- A cheap LLM may classify, **batched**, but its output is a proposal, never a write.
- Cure the stock, verify it, then mark legacy keys obsolete with a warning, then consider enforcing.

## 2. The design as it currently stands

**Manifest-first.** Stage 1 generates `relations_bootstrap_manifest.jsonl`, one row per candidate
edge (source card, key, raw value snapshot, extracted target, target status, proposed relation,
direction, polarity, classifier, confidence, disposition). Stage 2 is human review. Stage 3 is a
dumb importer that reads only the committed manifest and inserts via the core's `relate()`.

**Vocabulary** (validated by measuring real values, not by reading key names):

| relation | kind | notes |
|---|---|---|
| `related_to` | symmetric | absorbs ~40 names incl. `anchor`, `duplicate_cluster` |
| `split_from` | directed, child → parent | `parent` maps here — **`grouping.py:85` already defines it so**, pinned by `tests/test_grouping.py:306`. Reversed keys: `split_children`, `members`, `owns_cards`, `spawned_cards` |
| `found_during` | directed | `found_during`, `discovered_during`, `surfaced_by` |
| `distinct_from` | symmetric | `distinct_from`, `adjacent_not_duplicate`, `distinct_from_parent`, `related_distinct` |
| `duplicate_of` | **directed, loser → survivor** | only **2** real edges; canonicalising it inverts the survivor |
| `follow_up_of` | directed | `followups` is reversed |

Excluded: `recurrence*` (core-owned), `blocked*` and `blocks` (blockers module owns finding→finding
blocking), `similar_to` (annotator-owned).

## 3. OPEN DEFECTS — each blocks implementation

Numbered for tracking. Round 1 = the first review of the combined plan; round 2 = the review of the
manifest-first redesign.

### 3.1 The withdrawn duplicate would be re-asserted (round 2, both models)

`CB-2251` carries `duplicate_of='CB-2227'`, `residual_merged_into='CB-2227'` **and**
`retracted_by='author, same session'`. `CB-2227` carries `duplicate_filed_and_retracted='CB-2251'`.
Excluding the *marker key* does not suppress the two *positive* rows — they contain no negation, no
work slice, fewer than three ids, and would import as a live duplicate. That is 1 of only 2
`duplicate_of` edges, i.e. half the migrated content of the relation that most needs to be correct.

Worse: even importing the marker as a pre-retracted tombstone does not help. The marker sits on
`CB-2227` naming CB-2251, so the tombstone is `(CB-2227, duplicate_of, CB-2251)` while the assertion
is `(CB-2251, duplicate_of, CB-2227)` — and `duplicate_of` is deliberately **not** canonicalised, so
those are two distinct index keys that never match.

**Needs:** orient the tombstone loser→survivor before insert; add `retracted_by`-on-source-card to
the polarity screen. Note the retraction signal sits one key away from the value being migrated.

### 3.2 The success criterion cannot be met, and measures the wrong universe (round 2, both models)

`ledger_share = ledger_edges / (ledger_edges + residual_meta_edges)` with a ≥0.9 gate.

- Meta keys are immortal, so a migrated edge counts in **both** numerator and denominator. Ceiling
  is ~0.53. A perfect migration reports failure and the obsolete-warning phase never unblocks.
- The denominator's script (`relation_demand.py:117-119`) uses a 13-token word list containing
  **none** of `found_during`, `distinct`, `anchor`, `split`, `follow`, `spawned`, `owns`,
  `discovered`, `surfaced` — so ~229 of the mapped edges enter the numerator and never the
  denominator. Two errors in opposite directions, neither bounded.

**Needs:** `residual = accepted manifest edges MINUS live ledger edges`, both sides computed from
the allowlist, reporting rejected/unreviewed rows separately.

### 3.3 The hand-review rule selects for volume and misses every high-consequence class (round 2, Opus)

"Confirm every key group at ≥10 edges" spends the review budget on `related` (459 edges, where error
is nearly impossible) and confirms **0 of 4 reversed keys** and **0 of the `duplicate_of` family**.
Direction inversion and false duplicate-ness are the only two errors a human cannot later detect
from the ledger alone.

Worked example: `CB-3042.members` = 7 edges under one key, below threshold. If the classifier
proposes default direction, the importer writes "parent split from its own child" seven times and no
human is required to look.

**Needs:** replace the volume threshold with a **consequence predicate** — hand-confirm every key
whose direction is `reversed`, every key mapping to `duplicate_of`, plus the ≥10 band.

### 3.4 The negation screen does no work (round 2, Opus)

Measured over the allowlist's positive-mapped keys: **1 flag in 481 rows, and it is a false
positive** (`CB-2183 related` = *"RELATED, does NOT own this"*). All four of its stated fixtures
(`introduced_by`, `not_introduced_by`, `do_not_absorb`, `duplicate_filing_gap_from_cb2596`) are keys
**outside** the allowlist, so what actually stops them is fail-closed, not the screen.

**Needs:** keep the screen, but state honestly that fail-closed handles today's corpus; the screen
exists for the future case where a *listed* key acquires a negated value.

### 3.5 The allowlist is built from a stale export and already misses live keys (round 2, Codex)

The live target tracker contains `merged_into` (14 rows), `merged_from` (5) and `followup_card` (1)
that the frozen export does not — **because they were created by this very arc's own duplicate
merges**. `merged_from` is survivor→loser, the inverse of `merged_into`, so default-direction
mapping would invert it.

**Needs:** generation **aborts**, never merely prints, when the live tracker holds an edge-yielding
key absent from the allowlist. Any relation-writing workflow is itself a producer.

### 3.6 The importer is insert-only, so a corrected disposition can never be applied (round 2, Opus)

The manifest is declared the authority, but after run 1 the ledger is a one-way projection. Flipping
a row from `accept` to `reject` and re-running is a no-op; the wrong edges stay live and manifest and
ledger diverge permanently.

**Needs:** on re-run, retract live edges whose manifest row is no longer `accept`, reason
`manifest:<hash>`.

### 3.7 Range expansion over-extracts a suppression relation (round 2, Opus)

The corpus contains exactly one range: `CB-1899.distinct_from = ['CB-52..CB-63', …]`. Expanding
yields 12 existing ids. The plan justified expansion as *"under-extraction causes a future false
merge — the dangerous direction"*, but **that reasoning inverts for `distinct_from`**:
over-extraction suppresses 10 pairs no human ever suppressed, and a suppression that should not
exist is invisible by construction — nothing reports "these two were never compared". Inclusivity is
also undefined (11 or 12 ids?), and with n=1 there is no second sample to disambiguate.

**Needs:** state inclusivity; emit each expanded id as its own manifest row so stage 2 sees all
twelve individually; record over-expansion as the cost side.

### 3.8 Rules 6 and 7 fire on zero reachable rows (round 2, Opus)

The work-slice rule's only evidence (`CB-3017.blocks='CB-2942-W1'`) sits under an **excluded** key;
0 occurrences on mapped keys. The star-graph guard is scoped to the `duplicate_of` family, whose
measured max ids-per-value is **1**; the two real star values (`duplicate_pairs`,
`downstream_symptom_cards`) are unlisted keys. Both guards are redundant where they fire and absent
where the hazard lives — and they give false assurance to whoever later widens the allowlist.

**Needs:** scope the star guard to *any* directed key, and the work-slice rule to *any* key.

### 3.9 Duplicate manifest rows collapse provenance (round 2, Codex)

73 `(src,dst)` pairs carry ≥2 relation-shaped keys. Where both map to the same relation
(`CB-2251→CB-2227` under `duplicate_of` and `residual_merged_into`), the unique index plus idempotent
`relate()` collapses them: whichever imports first supplies `source` and `note` permanently, so a
hand-confirmed and an LLM-proposed row are no longer distinguishable — defeating the
`bootstrap:hand` / `bootstrap:llm` split that justified deferring a proposals table.

**Needs:** dedup on `(src, rel, dst)` at generation, carrying both source keys in `note`.

### 3.10 Manifest attestation is undefined (round 2, Codex)

"Reads only the committed manifest" is not enforced: no tracked-path check, no clean-worktree/HEAD
blob verification, no reviewer identity, no rejection of null or machine-auto-filled dispositions.
Generation emits `disposition:null` and the plan never says who dispositions the rows outside the
hand-confirm set — leaving them null loses them, auto-accepting makes the manifest a rubber stamp.

### 3.11 Four fixtures cannot fail (round 2, Opus)

Tests named for the four headline corrections all pass with the behaviour deleted: the polarity and
shape fixtures use unlisted keys (fail-closed produces the expected zero), the foreign-id fixture is
vacuous by the plan's own admission, and the liveness fixture exercises `relate()` while the risk
lives in the generator.

**Needs:** move fixtures onto *listed* keys, and assert generator output rather than `relate()`.

### 3.12 The obsolete-warning phase has no channel that reaches its audience (round 2, both models)

`grep -rn "warnings\.warn|DeprecationWarning" src/codebugs/` returns **zero hits** — no warning
machinery exists. `update_finding` returns the row (`findings.py:966`); there is no diagnostics slot.
Every stderr precedent sits in CLI handlers, not in the library functions the MCP tools call. The
producers of these keys are agents writing through `mcp__codebugs__add` / `update`, and
`server.py`'s own channel model says tool responses are where a diagnostic cannot reach — inverted
for an in-call warning: stderr goes to a log the agent never reads.

**Needs:** a `warnings: [...]` key on the `add`/`update` return payload, covering `batch_add`, with
the golden-schema regen budgeted for those two mutated tools.

## 4. Standing traps (carried from round 1, already folded into the design above)

- Endpoint filter is **existence**, not liveness — 62.8% of edges target closed cards, and a live
  card citing a closed one is the case the ledger exists for.
- **Never** a raw `BEGIN IMMEDIATE`; use `db.txn` (CB-40).
- Key name predicts neither value **type** nor **polarity**: `duplicate_of` holds file paths,
  `related_bugs` holds foreign hex ids, `owns_cards` mixes a hex id into a CB list.
- The target tracker must be named explicitly — there are 13 on this machine, and the one the code
  lands in has 89 rows while the edge corpus has 3,211.
- Measure values, do not read key names. That is what caught `anchor` (16 edges, 12 pointing at one
  card — a cluster anchor, not a duplicate) before any reviewer did.

## 5. Sequencing

1. Core table + tools land first (`PLAN-finding-relations-2026-08-18.md`).
2. Seed by hand: 50 human-labelled edges already exist in
   `~/.cache/codebugs-identity/goldset-2026-08-17.json`.
3. Close §3.1–3.12 in this document; re-review; then implement.
