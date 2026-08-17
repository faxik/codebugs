# batch-codebugs — DRY RUN, codebugs tracker, 2026-08-17

**No tracker writes were made.** `--apply` was not given. Everything below is a proposal.

---

## Headline: this backlog should not be batched

24 open rows. The recommendation is **do not publish any owning meta-bug card.**

The three candidate owning groups found below each have exactly **two** members, and each
parent card would itself be an open row — so publishing all three moves the backlog from
24 open to 27 open (24 excluding `meta_batch`) while collapsing nothing. That is the
counting convention the skill warns about, arriving in its purest form.

The reasons this corpus resists batching are structural, not a failure of the sweep:

- **There are no duplicate rows.** `similarity_report` at `threshold=0.5` (well below the
  calibrated 0.7) over the live population: `rows_considered=24`, `collapse_count=0`,
  `families_total=0`. Every card is a distinctly-worded, hand-authored diagnosis.
- **The evidence graph is already explicit.** 20 of 24 rows name at least one other `CB-N`
  in prose, with the relationship stated ("carried forward from", "companion to",
  "deferred out of the CB-26 tree"). The edges this skill exists to reverse-engineer were
  written by hand at filing time.
- **No prior batching exists to extend.** `query(category="meta_batch")` → 0 rows;
  `query(meta_key="meta_bug")` → 0 rows.
- **Nearly every card is its own edit.** ~24 rows → ~24 independent landing edits. There
  is no sweep, no transformation, no generator.

This skill is built for a 1120-card auto-filed corpus. This is a 24-card curated one. The
honest output is not meta-bugs; it is the three items in **What is actually worth doing**.

---

## Which identity client — MIXED, and the boundary is visible in the data

| Rows | Fingerprint | Filed |
|---|---|---|
| CB-47, CB-57, CB-58, CB-59, CB-60, CB-61 | `auto:v1:…` present | 2026-08-16 09:03 and ≥17:44 |
| CB-51, CB-52, CB-53, CB-54, CB-55, CB-56 | NULL | 2026-08-16 17:11:33 (one `batch_add`) |
| CB-6, CB-21, CB-29, CB-31…CB-46 | NULL | 2026-04-06 … 2026-08-16 08:35 |

8 of 24 open rows carry a fingerprint; `max(occurrence_count) = 1` across the whole open
population. The client became fingerprinting **between 17:11 and 17:44 on 2026-08-16** —
consistent with an MCP server restart picking up the CB-43 build. Writes issued now do
fingerprint. Legacy rows stay NULL (backfill is blocked — CB-46).

**Consequence for this report:** `same-generator` and `same-regression` are both
unavailable here. `same-regression` is not "absent", it is **dormant** — a reopen fires
only on a fingerprint hit, and two thirds of the population has no fingerprint. An empty
regression result is not evidence that no regressions exist.

**The population moved during the run.** The census opened at 22 open rows; CB-60 and
CB-61 were visible by the time the category rollup was taken, giving 24. Everything below
is a snapshot at 2026-08-17.

---

## Phase 0 — standing diagnoses

**Found one, and it is already written into a card.** CB-21 carries the umbrella claim for
the parity-gate family verbatim: *"CB-6 … is the same enumeration problem on the surface
axis. CB-19 is the vocabulary-validation axis. This card is the mutability axis. All three
want one parity gate rather than three separate sweeps."* CB-19 is closed (`fixed`), so the
live pair is CB-21 + CB-6. This is handled as a **view** (V2) below, not an owning card —
see why in V2.

**One card's headline premise is factually false, verified directly by me today.** CB-6
reads *"CLI has zero blockers subcommands"*. `src/codebugs/blockers.py:649` defines
`register_cli`, registering four commands — `blockers-add` (`:651`), `blockers-query`
(`:659`), `blockers-check` (`:666`), `blockers-resolve` (`:672`) — and
`register_cli_provider("blockers", register_cli)` is called at `:722`. The card's own
2026-08-14 correction note says this, and cites `:633`/`:706`; the file has since moved by
~16 lines, so even the correction's citation is now stale. **CB-6 survives only as the
policy question "is the CLI a curated subset of MCP, or a peer surface?"** — which its
correction note also says is *partly already answered* in
`docs/superpowers/plans/2026-05-11-milestones-streams.md:98`. Disposition proposal: retitle
CB-6 to the policy question, or close it and refile the question. Not a batching action.

---

## Phase 1 — edge harvest, and which regions were swept by which edge type

| Edge type | Trust | Swept over | Yield |
|---|---|---|---|
| (1) identical failure signature | highest | all 24 live rows, `similarity_report` @ 0.5 | **0 families** |
| (2) explicit `CB-N` cross-reference | high | all 24 descriptions, regex `CB-[0-9]+` | 20 rows carry ≥1 edge |
| (3) shared filing event | high | `reported_at_commit` + `created_at` to the second | 4 clusters (see below) |
| (4) named shared target | medium | `file` column + anchors named in prose | 3 clusters |
| (5) named shared question | weakest | full-text read of all 24 | 3 candidates, all promoted or rejected below |

**Shared filing events (edge type 3), re-derived not taken on trust:**

- `fb03d8eb…` @ `2026-08-16T17:11:33Z` → CB-51, CB-52, CB-53, CB-54, CB-55, CB-56 (one
  `/code-review` of the CB-43/CB-45 landing)
- *(no commit)* @ `2026-08-16T17:44:10Z` → CB-57, CB-58, CB-59 (the CB-50 harness review)
- `004027ea…` @ `2026-08-14T07:16–07:17Z` → CB-31, CB-32, CB-33, CB-34, CB-35 (CB-26 tree)
- `764edcf7…` @ `2026-08-14T08:06Z` → CB-37, CB-38 (CB-27/CB-30 carry-forwards)

**A shared filing event is not a group.** Each of these four clusters was tested against the
resolution sentence and **three of the four failed it** — the members were filed together
because one review session produced them, not because one change resolves them. That is
the single most important negative result in this run.

---

## Candidate owning groups — 3 found, 0 recommended for publication

Each passes the resolution sentence and carries a negative control. All are 2 members.
**Recommendation for all three: do not mint a `meta_batch` parent.** The linkage is worth
recording; a parent row is the wrong instrument at this size. See the alternative under
each.

### G1 — the field-freshness contract of a deduplicated row

> **Deciding which columns of a deduplicated finding track the LATEST observation rather
> than the FIRST resolves every member.**

- **Relationship:** `same-decision` (owning-eligible) · **Operation:** `decide`
- **Members:** CB-52 (medium, `findings.py`), CB-53 (medium, `provenance.py`)
- **Decision anchor:** the contract of `findings._bump_row` + the `meta.occurrences` ring —
  an invariant, not merely a shared subsystem.
- **Option set, identical for both members:** (a) the bump writes the latest observation
  into the column; (b) the column is first-report-only and *readers* must consult
  `last_seen_at` / the occurrence ring.
- **Unblocks the whole card:** CB-52 — under (a) it is a `_bump_row` change plus a routing
  re-evaluation hook, under (b) it is a documented `first-assessment-wins` on `_bump_row`.
  CB-53 — under (a) the bump refreshes provenance, under (b) `staleness_check` /
  `check_findings` consult `last_seen_at`. Nothing else remains in either card.
- **Predicate (runnable):** *the card's entire content is a question about whether a
  specific column reflects the newest observation of an already-deduplicated row, and both
  its answers are expressible as either a `_bump_row` write or a reader-side lookup.*
- **Negative control (an outsider, not a member): CB-56.** Same filing event
  (`fb03d8eb`, 17:11:33), same `/code-review`, same `findings-identity` category, filed
  against the same file — and it **fails the predicate**: its question is what `add` does
  with machinery-written reserved meta keys at *ingestion*, decided at the validation seam
  before any dedup occurs. Answering the field-freshness contract leaves CB-56 exactly as
  blocked. Second control, also failing: **CB-54** (the `auto:v1` → `auto:v2` normalizer
  question — pre-fingerprint, not post-dedup).
- **Counts:** rows 2 · sites 2 (`findings._bump_row`; `provenance.check_findings` /
  `staleness_check`) · edits 2 · trees 1
- **Disposition spread (per member, expected):** CB-52 → `fixed` under (a), `wont_fix`+doc
  under (b). CB-53 → `fixed` under either.
- **Honest caveat, stated because it is the weakest of the three:** severity and
  `reported_at_commit` *could* legitimately be decided independently (escalate severity for
  safety, freeze provenance for audit). That answer is still one artifact — a
  per-column contract — but if you find yourself writing two rationales, this is two cards.
- **Alternative to a parent card:** none needed; add one sentence to each card naming the
  other as the co-decision.

### G2 — where enforcement for the main-branch invariant lives

> **Deciding whether the `main` invariants are enforced client-side, server-side, or both
> resolves every member.**

- **Relationship:** `same-decision` (owning-eligible) · **Operation:** `decide`
- **Members:** CB-57 (medium, `tools/pre-commit-hook.sh`), CB-59 (medium, `.github`)
- **Decision anchor:** the enforcement layer for "main only ever moves by a `--no-ff` merge
  of a typed branch" — a stated invariant with a stated gap.
- **Option set, identical for both:** (a) client-side hooks only; (b) protected
  `origin/main` + one CI assertion only; (c) both.
- **Unblocks the whole card:** CB-59 *is* the question. CB-57 — under (b) it becomes
  `wont_fix` (a local `pre-merge-commit` hook is redundant against a server-side gate that
  catches every bypass); under (a) or (c) it is a specified ~10-line hook plus dropping
  `--no-verify` from `worktree-finish.sh`.
- **Predicate (runnable):** *the card proposes a mechanism whose necessity is determined by
  the answer to "client-side or server-side", and whose disposition flips between fix and
  wont_fix depending on that answer.*
- **Negative control (an outsider): CB-58.** Same filing event (17:44:10), same `process`
  category, same parent incident (CB-50), same directory (`tools/`) — the strongest
  superficial match in the corpus — and it **fails the predicate**: the defect is that
  `worktree-setup.sh` writes `in_progress` instead of taking a real claim, with no holder
  identity and no release path. That hole is present and identical under (a), (b) and (c).
  Branch protection does not give a card a holder. This control is deliberately drawn
  *inside* the same filing cluster, which is where a false group would have swallowed it.
- **Counts:** rows 2 · sites 3 (`tools/pre-commit-hook.sh`, `tools/install-hooks.sh`,
  `.github/workflows/`) · edits 2 · trees 1 — but note CB-59 is **partly not a code edit**
  (GitHub branch-protection settings are configured, not committed).
- **Disposition spread:** CB-57 → `fixed` under (a)/(c), `wont_fix` under (b). CB-59 →
  `fixed` under (b)/(c), `wont_fix` under (a).
- **Alternative to a parent card:** this is the highest-value linkage in the run — the two
  cards are in direct tension and answering them separately produces incoherent
  enforcement. Record it as prose in both cards, or resolve it in one sitting.

### G3 — the uncurated category namespace

> **Curating the `category` namespace — normalize at write time, adopt-or-explicitly-mint on
> a near-hit, and fold the existing variants — resolves every member.**

- **Relationship:** `same-root-cause` (owning-eligible) · **Operation:** `fix-generator`
- **Members:** CB-60 (medium, `findings.py`), CB-61 (low, `cli.py`)
- **Root cause:** `category` is unvalidated free text (`add_finding`, `findings.py:664`;
  schema `category TEXT NOT NULL`, `:30`) while being a load-bearing identity input —
  it is hashed into the fingerprint (`findings.py:353`), `similarity_report` blocks strictly
  by it (`similarity.py:274`), and the annotator pool never crosses it
  (`similarity.py:170-175`). *(Line citations are the cards' own; I did not re-open
  `similarity.py` — treat them as the cards' claims, not as my verification.)*
- **Predicate (runnable):** *the card's fix is a component of one category-namespace
  curation policy — CB-60 applies it to new rows, CB-61 applies it retroactively.*
- **Negative control (an outsider): CB-54.** Also an identity-normalization card, also
  filed against `findings.py`, also changes a fingerprint input, and **cited by CB-60
  itself** ("NOTE per CB-54") — the ideal shape of a false positive. It **fails the
  predicate**: CB-54 is about `\b`-anchoring and case-consistency in
  `_normalize_for_fingerprint`'s handling of the *description*, requiring an `auto:v2`
  version bump. The category policy neither answers it nor is answered by it. A topical
  neighbour that the mechanism excludes.
- **Counts:** rows 2 · sites 2 · edits 2 · trees 1 (sequenced: CB-61 cannot be written
  before CB-60 fixes the canonical fold rule).
- **Disposition spread:** both → `fixed`.
- **This one already has the right instrument available and unused:** CB-61 depends on
  CB-60 in the `entity_resolved` sense. **`blockers_add(item_id="CB-61",
  blocked_by="CB-60")` is a better fit than a meta-bug** — it is one row in a table built
  for exactly this, it feeds `pull_next` eligibility, and it needs no parent card.

---

## Views — non-owning, write nothing to member cards

### V1 — dormancy census of the CB-26 / CB-30 carry-forwards (MEASURED TODAY)

`same-procedure`. Six cards were deferred out of the CB-26 and CB-27/CB-30 trees on
2026-08-14, each recording "currently latent/dormant, verified 2026-08-14". **That is three
days old and the skill forbids trusting a supplied count, so I re-ran it.**

**Decision procedure:** for each card, count the live rows the defect requires in order to
occur.

| Card | Requires | Measured 2026-08-17 | Verdict |
|---|---|---|---|
| CB-32 | ≥1 non-stream milestone item | `release/1.1`: `total_items = 0` | **latent** |
| CB-33 | an entity attached to ≥2 milestones | only `stream/triage` has items (55); security 0, maintenance 0 | **latent** |
| CB-35 | a finding mis-streamed by re-triage | `stream/security`: `total_items = 0` (never used) | **latent** |
| CB-38 | ≥1 assigned item or capacity row | `wip_status` → `[]`; `in_progress = 0` | **latent** |
| CB-34 | a terminal entity carrying an active blocker | 1 active blocker total, on CB-47, which is `open` | **latent** |
| CB-31 | a live read relying on the hand-applied filter | **7 of 30** stored-open triage rows are excluded by the read filter | **LIVE** |

**Falsifier (mandatory, and it fires): CB-31.** `stream/triage` reports `open = 30`;
`triage_inbox`, which applies `reconcile.source_is_terminal` per row, returns **23**. Seven
stored rows point at terminal findings *right now* and are invisible only because the
read-side filter catches them. That is the CB-26 shape recurring, measured — and it is
precisely CB-31's argument that the filter has no seam and a fourth call site could forget
it. The procedure discriminates; it is not vacuously answering "latent" for everything.

**What the view is for:** five of these six cards can be deprioritized on measured evidence
rather than on impression, and CB-31 is the one with a live symptom. **It owns nothing** —
"dormant today" is a scheduling fact, not a shared mechanism, and the six cards have six
different fixes. If these were given a parent, a single decision on the parent would be
read as deciding all six.

*(Not chased in this dry run: whether those 7 rows predate CB-26's hook or are fresh drift
from one of the five documented bypass writers. Identifying them needs a direct query
against `milestone_items`; either way the 7 is real and is CB-31's evidence.)*

### V2 — "a repo rule enforced by prose and an enumeration" (sequencing only)

`same-disposition` at best. Six cards — CB-6, CB-21, CB-29, CB-31, CB-37, CB-55 — each say
the same thing about a different axis, and four of them cross-reference CB-21 as the
umbrella. It is the largest cross-reference cluster in the corpus and it is **not a group**:

> Doing ___ resolves every member.

cannot be completed. CB-21 wants a parity test over `PRAGMA table_info` vs update
signatures; CB-37 wants a runtime connection wrapper (its own recommendation (b)) and
explicitly documents why the static predicate *certifies the bug it was built to catch*;
CB-31 wants a SQL view or query builder; CB-29 wants a shared `is_text_filter_active`
guard; CB-55 wants a `run_domain_command` decorator; CB-6 wants a registry-walking coverage
test. Six mechanisms, six files, six edits. Naming this a group would be naming it by area
— the skill's first red flag — and CB-21's own text explains why the tempting shared
primitive was considered and correctly rejected.

**Its only legitimate use is sequencing:** if a session is ever funded to build gate
infrastructure, these six are the candidate list, and CB-21 is the natural first because it
is the one that already declares the umbrella.

---

## Residue — singletons, named

Not in any group, and each is its own unit of work:

**CB-51** (the only `high`; CSV import restore path — four verified defects with a single
root *inside one card*, which is what a well-formed card looks like) · **CB-42** (fencing
token; spans codebugs *and* autosorter, filed rather than attempted, after four Codex
rounds each moving the window) · **CB-46** (backfill blocked on the merge policy; the
natural *dependency* of CB-61's collision case, which CB-61 already defers to it) ·
**CB-47** (skill maintenance — its date blocker fires **today**, 2026-08-17) · **CB-54**
(`auto:v2` normalizer) · **CB-55** · **CB-56** · **CB-58** · **CB-6** (see Phase 0 — headline
false) · **CB-21** · **CB-29** · **CB-31** · **CB-34** · **CB-37**.

---

## The five measures, kept separate

| Measure | Value |
|---|---|
| Observation compression (rows → distinct defects) | **24 → 24.** Zero. No duplicate rows exist. |
| Members individually verified | **24 of 24**, each read in full (description + meta + tags) |
| Shared decisions required | **3 proposed** (field-freshness; enforcement layer; category namespace) + **2 already standing** (merge policy CB-46; CLI coverage policy CB-6) |
| Independent landing edits required | **~24** — approximately one per card; no sweep, no shared transformation |
| Execution cost | ~25 tool calls, read-only, no writes |

**Both backlog numbers:** 24 open excluding `category=meta_batch`; 0 `meta_batch` rows. If
all three candidates were published: 27 open, 24 excluding `meta_batch`. **The count this
skill exists to shrink would grow by 3 and shrink by 0.**

---

## Limits of coverage — this is not completeness

- **Recall is unmeasured.** Phase 1 finds candidates among *harvested edges*. A genuine
  shared mechanism that no card references and that `similarity_report` cannot see was never
  proposed, and falsifying the three candidates above says nothing about what was missed.
- **Swept:** all 24 open findings, by edge types 1–5 as tabulated.
- **Not swept:** the 37 resolved rows (a group of already-closed cards is not actionable);
  the `requirements` entity entirely (this skill reads findings only); the 7 stale
  `milestone_items` rows, identified only by count.
- **`same-regression` could not run** — two thirds of the population has no fingerprint.
- **Line citations:** `blockers.py:649/651/659/666/672/722` I opened and read myself. The
  `findings.py` and `similarity.py` line numbers in G3 are the cards' own claims, repeated
  as claims.

---

## What is actually worth doing

1. **Answer G2 (CB-57 vs CB-59) as one decision.** The two cards are in direct tension —
   CB-59 argues the local harness "overclaims" without server-side enforcement, CB-57
   proposes more local enforcement. Deciding them separately produces an incoherent gate.
   Highest-value output of this run.
2. **`blockers_add(item_id="CB-61", blocked_by="CB-60")`** — a real dependency, currently
   recorded only as the word "Companion" in prose. One row, right instrument.
3. **Act on V1:** CB-32, CB-33, CB-34, CB-35, CB-38 are latent on evidence measured today;
   CB-31 has a live symptom (7 of 30). That is a priority ordering nobody had before.
4. **Fix CB-6's title** — it asserts something false about the current tree.
5. **Do not publish meta-bug cards in this tracker.** Revisit if it passes ~200 open rows
   or if an automated filer starts writing to it; neither is true today.

---

## If `--apply` had been given

It would have created three `meta_batch` parents (G1/G2/G3) via the two-phase protocol
(`batch_state: building` → per-member pre-write check → bidirectional audit → `active`),
checking `was_new` on each `add` and stopping without writing a single pointer if the parent
deduplicated onto an incumbent. **That is exactly what the recommendation above says not to
do.** Re-run with `--apply` only if you disagree with the headline.
