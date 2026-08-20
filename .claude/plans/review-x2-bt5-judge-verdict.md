# BT-5 Judge verdict (2026-08-20)

Judge: Fable 5 (cross-model vs the Opus adversary/defender and Codex/Sol attacker).
Spot-checks run personally against main `fdf4376` — after T-9 landed (`promote_tags` now
real in `_bump_row`), before T-10 (verified: `_occurrence_entry` :477-509 has NO
category field). Every claim below marked "verified" was read from the tree by the
judge, not inherited from any agent.

## What the judge verified personally (symbols, current main)

- Four `dedup_action` values in `_add_one`: `"created"` :714, `"bumped"` :742,
  `"reopened"` :757, `"recurrence_of_closed"` :762.
- Recurrence branch falls through to the insert path and returns
  `(result, None, True, dedup_action)` :832 — `was_new=True`, and
  `meta_final["recurrence_of"]` written at :777 (sole site).
- `_bump_row` computes `escalated = _escalated_severity(...)` :607 as a local and
  returns only the post-UPDATE `RETURNING *` row :635-636 — the pre-value dies there.
- `_finalize_add` :862-893 is the sole response constructor; `batch_add_findings`
  splats a positional 4-tuple: `[_finalize_add(*outcome, committed=owned) ...]` :1546.
- `_import_would_reopen` docstring :1460-1463 carries the "return shape stays the one
  every other caller and the MCP wrappers are written against" contract, verbatim.
- Similarity resolver: `_ANNOTATE_STATUSES = LIVE_STATUSES + RECURRENCE_STATUSES`
  (`similarity.py:78`), match payload `{"id", "score", "status"}` (:184), fires on the
  recurrence-insert path via the resolver seam (`finding_id is None and annotate`).
- Wire (executed, not read): `add`'s output schema is
  `{additionalProperties: True, type: object}`; `batch_add`'s wraps the list in
  `{result: [...]}` with `additionalProperties: True` items. Golden: 0 occurrences of
  `outputSchema`. So even a golden extension would gate nothing — only a behavioural
  result test can.
- MCP `add` docstring :2119-2127 names `"bumped"`/`"reopened"`, describes the
  recurrence behaviour, and omits the literal string `recurrence_of_closed`; the
  `batch_add` docstring names no action values at all.
- `findings` table: 16 columns, `attention` not among them.
- Post-add hooks (`db.run_post_add_hooks(conn, result)` :831) receive the very dict
  `_finalize_add` later mutates with response-only keys.

## Verdict Summary

Corroborated pairs share a row. F-* = Opus FATAL, S-* = Opus SERIOUS, W-* = Opus
WEAKNESS, N-* = Opus NITPICK; C-* = Codex.

| ID | Claim | Defender position | Ruling | Severity |
|---|---|---|---|---|
| F-1 ≙ C-MR-3 | `dedup_action` vocabulary is 4 values, not 3; the sketch's own exclusion rule kills signal #3 | Concede | **SUSTAINED** (verified :714/:742/:757/:762). Premise block sourced from observed samples instead of the branch table — the repo's standing enumeration lesson inside an identity design | FATAL |
| F-2 ≙ C-MR-2 | Signal #1's `from` value dies in `_bump_row`; response needs two internal contract changes | Concede | **SUSTAINED**, framing softened: the 4-tuple is module-private (3 in-module callers + 1 test monkeypatch), and the :1460-1463 docstring's real content is the *response* shape — AddOutcome/BumpOutcome satisfies it. The reversal of the once-declined decision must be cited and that docstring updated in the same unit | FATAL |
| F-3 ≙ C-MR-4 | Recurrence path: `was_new=True`, attention record about row B keyed flat on row A; `prior_status` transient | Concede | **SUSTAINED** (verified) — resolved by deleting signal #3 | FATAL |
| S-1 ≙ C-smell | Absent-key argument inverts CB-25; repo precedent is unconditional keys | Ruling #1 | **UPHELD**: `attention: []` always present. CB-25 is a filter-INPUT rule (Codex right on citation); `claims._response` and `_finalize_add`'s own two unconditional response-only keys are the precedent (Opus right on contract) | SERIOUS |
| S-2 ≙ C-MR-5 ≙ C-MR-7 | "Wire golden" cost misattributed; nothing gates the response shape; closed vocabulary exists only in prose | Concede | **SUSTAINED** — verified live (`additionalProperties: True`). Behavioural MCP result test is the only real gate; golden `outputSchema` extension DISMISSED as a gate (cannot fire against an open schema) | SERIOUS |
| S-3 ≙ C-smell | Supplied-vs-derived fingerprint provenance blind on import/restore rows | Partial | **PARTIAL SUSTAINED**: import rows are self-protected (verbatim-category derivation means a differently-spelled observation derives a different fingerprint — defender probe, plausible from :1177/:1464); live exposure is `restore_findings` and explicit-id rows. Mandate: snapshot a provenance boolean at the observation entry point (after fallback derivation, `fingerprint is not None` cannot classify) | SERIOUS |
| S-4 | `_gate_category` shrinks signal #2's population to near zero | Partial | **PARTIAL SUSTAINED**: narrowed, not emptied — divergence between two EXISTING categories bumps unflagged (defender probe). Sketch must state the honest population | SERIOUS |
| S-5 ≙ C-smell | Import opt-out flag: dead code; `promote_tags` doesn't exist | Concede | **SUSTAINED on the core** (import never calls `_finalize_add` — no channel, no flag, pin with a test). The "parameter does not exist" half is now STALE: T-9 landed `promote_tags` (:537, :1207) between attack and judgment | SERIOUS |
| S-6 ≙ C-missing | No totality guard for the composition; matrix unspecified | Ruling #3 | **UPHELD**: matrix is live (escalation fires on both `_bump_row` call sites). Denominator 6, not 12 — but the exact count belongs in the derived totality test (TestBranchTotality shape), never in prose; this repo has been wrong both times it left a count in prose | SERIOUS |
| S-7 | `meta.similar_to` already carries signal #3's payload on its dominant path | Concede | **SUSTAINED** (verified pool + payload) — third structural carrier; feeds the #3 deletion | SERIOUS |
| W-1 | Audience is silently MCP-only (CLI prints fixed one-liners; no batch-add verb) | Concede | **SUSTAINED** — verified :2436-2447; state it in the sketch | WEAKNESS |
| W-2 ≙ C-MR-1 | Signal #2: hard T-10 dependency under the frame; justification shifts post-T-10 | Ruling #4 | **UPHELD** (verified no category in ring on main). Pre-T-10 the divergence is recorded NOWHERE — an attention record would be the fact's only record, violating "из УЖЕ ЗАПИСАННЫХ фактов". Codex right on the hard dependency; Opus right that post-T-10 the honest rationale is top-level NAMING, weaker than #1's | SERIOUS |
| W-3 | `prior_status` named as durable | Concede | **MOOT** after #3 deletion | — |
| W-4 | Flat namespace: future `attention` column silently overwritten (CB-16 shape) | Concede (rename/envelope) | **SUSTAINED, remedy MODIFIED**: keep the name `attention`; add a ratchet test asserting response-only keys ∩ findings columns = ∅ (16 columns today, verified). Cheaper than renaming, matches repo practice, and covers `was_new`/`dedup_action` retroactively — the envelope would break those two for no gain | WEAKNESS |
| W-5 ≙ C-MR-6 | Post-commit failure path drops the block exactly when needed | Concede | **SUSTAINED**: signals captured inside the transaction (BumpOutcome/AddOutcome), `_finalize_add` stays mechanical — kills the class | SERIOUS |
| N-1 | Unpriced option (г): one docstring line naming `recurrence_of_closed` | Adopted | **SUSTAINED, ELEVATED** — becomes signal #3's replacement; verified the omission at :2119-2127 | Elevated |
| N-2 | `dismissed_twin` misnames `not_a_bug` | — | **MOOT** after deletion | — |
| N-3 ≙ C-smell | `batch_add` returns a list; wire wraps it in `{result: [...]}` — two consumption forms | Concede | **SUSTAINED** as a documentation/test obligation (verified both forms) | WEAKNESS |
| C-smell (legacy) | Non-string/legacy stored categories: `normalize_category` raises; comparison underspecified | Concede | **SUSTAINED**, conditional on signal #2: compare `normalize(observed) != normalize(stored)`, skip-don't-raise on non-string stored (mirror `_existing_categories`' ratified policy) | SERIOUS (post-T-10) |
| C-missing (rest) | Ordering/uniqueness; canonical emitted values; single-source `escalated`; response-only persistence guards + hook-dict aliasing; negative & stacking tests; real MCP result tests; exact doc obligations; import pin; batch validation invariant | Concede | **SUSTAINED** wholesale as unit requirements. Hook-aliasing verified: hooks at :831 receive the dict `_finalize_add` later mutates — pin the ordering | SERIOUS (aggregate) |
| C-alt | Records themselves not overengineered; no `details` envelope | Accepted | **ACCEPTED** — guard against over-correction | Credit |
| C-smell | Reopen exclusion weakly justified | Concede-lite | **DISMISSED as attack**: the threshold is owner-fixed; the frame is not on trial | Dismissed |

## Ruling on the defender's four disagreement rulings

All four **UPHELD**, one remedy modified:

1. **Always-present `attention: []`** — upheld exactly as ruled (Codex right on the
   CB-25 citation, Opus right on the contract).
2. **Signal #3 collapses; option (г) replaces it; letter-fix to the owner** — upheld,
   with required content (below).
3. **Matrix: 6 cells, derived not enumerated** — upheld; the count itself goes in the
   test, not the sketch prose.
4. **Signal #2: hard T-10 dependency + new (naming) rationale** — upheld; verified the
   dependency personally on main.

## Ruling on the surviving SHAPE

**One-signal core NOW; two-signal as a priced conditional the owner decides.**

- The unit that lands first carries `severity_escalated` only — the one genuinely
  dissolved fact (the pre-escalation severity exists nowhere in the response: the
  insert path writes no ring entry, the column holds the max). Transport:
  `BumpOutcome` returning `_bump_row`'s own `escalated` decision (single predicate —
  discharges the drift risk) + `AddOutcome` replacing the positional 4-tuple.
- `category_divergence` goes to the owner in the same presentation as a separately
  priced option: hard dependency "T-10 lands first", honest rationale re-stated as
  top-level naming of a fact buried three levels deep in a ring filers don't read
  (weaker than #1's), gate-bounded population stated, normalize-both-sides +
  skip-non-string policy, provenance boolean at entry. It must NOT be silently
  bundled into the first unit.

**Signal #3 → owner: the letter-fix route is HONEST, upheld — with two conditions.**
The ratified intent (the filer learns a decision was already taken) is carried
top-level by `dedup_action: "recurrence_of_closed"` — structural, named, in the very
field the frame's own exclusion rule treats as sufficient for reopen — plus
`meta.recurrence_of` (always) and `meta.similar_to` `{id, score, status}` (dominant
path). Deleting the attention record does not narrow the meaning; it recognizes the
intent is already implemented, and FORM is exactly what this sketch was licensed to
decide. It would narrow the meaning only if the ratified intent were "all three
signals appear in the attention block specifically" — but the owner ratified the
threshold (which signals are serious), not the carrier. Conditions:
1. The notification line must state the residue plainly: on non-similarity recurrence
   paths (caller-supplied fingerprint with dissimilar text, or normalized text
   < 40 chars) the twin's `prior_status` is NOT in the response — one `get` away.
2. It lands together with option (г) (docstring naming the fourth action string), so
   the structural carrier is discoverable, not just present. An undocumented carrier
   would make the letter-fix a quiet regression of the ratified intent.
One clear line covering both, delivered inside the same owner presentation that
carries the revised form — not a separate question.

## Mandatory Fixes (before the sketch goes to the owner)

1. **Rewrite the premise block from the branch table**, not from observed MCP calls:
   four `dedup_action` values; `was_new: True` on the recurrence branch; "оба значения
   в руках `_bump_row`" corrected to "the pre-value dies inside `_bump_row`".
2. **Delete signal #3; adopt option (г)**: one docstring line in the MCP `add` (and
   `batch_add`) docstring naming `recurrence_of_closed`; golden regenerates because
   the description moved. Owner notification per the ruling above (residue + carriers
   named, one line, same presentation).
3. **One-signal core with the outcome transport**: `AddOutcome` (frozen; row,
   `was_new`, action literal, typed attention) replacing the 4-tuple and the
   `batch_add` splat; `BumpOutcome` carrying `_bump_row`'s own `escalated` decision —
   no second severity comparison anywhere. Signals captured inside the transaction;
   `_finalize_add` stays mechanical (closes W-5 ≙ C-MR-6). Cite the reversal of the
   `_import_would_reopen` decision and update that docstring (:1460-1463) in the same
   unit. Admits exactly `bumped`/`reopened`.
4. **`attention: []` always present**, `add`/`batch_add` only; audience stated
   MCP-only (CLI prints fixed lines; no batch CLI verb).
5. **Signal #2 as priced conditional** (see SHAPE ruling): T-10-first dependency
   stated as HARD under the frame; naming rationale; gate-bounded population;
   normalize(observed) vs normalize(stored) with skip-non-string; supplied-vs-derived
   boolean snapshotted at the observation entry point.
6. **Composition matrix derived from the branch table** with a TestBranchTotality-shape
   totality test; deterministic ordering and at-most-once per signal type in the list
   contract.
7. **Wire obligations corrected**: the response key moves NO golden — the docstring
   does; author a behavioural MCP result test (structured + unstructured forms of
   `batch_add` both covered); CHANGELOG entry.
8. **Import needs no flag, by construction** — no path to `_finalize_add`; pin with a
   test that import returns counts only and an imported live hit surfaces no
   attention.
9. **Namespace + aliasing guards**: ratchet test response-only keys ∩ findings
   columns = ∅; pin that post-add hooks run before `_finalize_add`'s mutation (hooks
   must never observe `attention`/`was_new`/`dedup_action`).

## Recommended Fixes

- Negative/stacking test list from Codex (equal/lower severity, derived-fp normalized
  twins, supplied-fp divergence, legacy spelling, both dismissed statuses, reopen
  escalation, multi-signal, empty) — carry into the unit brief verbatim.
- Consider `TypedDict`/`Literal` for the attention records internally (validation
  before commit); do NOT extend the golden with `outputSchema` — it cannot gate an
  `additionalProperties: True` schema and would be a gate that cannot fire.
- State in the sketch that `batch_add` already violates the "all MCP tools return
  dict" rule (wire wraps in `{result: [...]}`) so the per-row attention cost is priced
  against reality.

## Code findings to route (new tracker cards, not document fixes)

1. **CARD (docs, medium-low)**: MCP `add` docstring omits the fourth action string
   `recurrence_of_closed` (:2119-2127 names only `bumped`/`reopened`); `batch_add`'s
   docstring names no action values at all. A client gating on the documented
   vocabulary misclassifies the exact event BT-5 targets. This card is option (г)'s
   vehicle and stands even if BT-5 stalls.
2. **CARD (low, latent)**: post-add hooks receive the same dict object
   `_finalize_add` later mutates with response-only keys (:831 vs :891-892). No live
   consumer holds the reference across the boundary today; a hook that stores the
   dict would later observe response-only keys appear. Pin ordering or hand hooks a
   copy — decide there, not here.
3. Judged NOT card-worthy: `PostCommitCorruptionError` raising instead of returning
   (documented, deliberate classifier); similarity annotating recurrence rows
   (working as designed, and now load-bearing as signal #3's carrier).

## Dismissed Findings

- Codex "reopen exclusion weakly justified" — the threshold is owner-ratified; the
  frame is not on trial.
- Codex alternative "include `outputSchema` in the golden collector" — verified it
  cannot gate anything against an open schema; a gate that cannot fire is this
  repo's named failure shape.
- Opus S-5's second half ("`promote_tags` does not exist") — overtaken by events:
  T-9 landed it. The finding's core (no flag, by construction) survives.
- W-3, N-2 — moot with signal #3's deletion.

## Cross-model scorecard

- **Opus adversary**: strongest structural attack — the vocabulary premise falsity,
  the collapse of signal #3 under the sketch's own exclusion rule, the `similar_to`
  duplication, and the response-seam frame analysis were all his. One staleness
  (promote_tags). Every FATAL verified true by the judge.
- **Codex (Sol)**: caught what Opus under-weighted — the HARD T-10 dependency under
  the ratified frame (Opus filed it as a weakness), the post-commit capture rule, and
  supplied the adopted transport (AddOutcome/BumpOutcome) plus the legacy-category
  policy. Under-attacked signal #3 (priced a transport for cargo already at the
  destination — the defender's phrase, endorsed) and offered one gate that cannot
  fire (golden outputSchema).
- **Defender**: 0 defends across 49 positions is itself a verdict on the sketch;
  every probe the judge re-checked held (recurrence carriers, wire schema, ring
  contents, column count, docstring omission). All four disagreement rulings upheld;
  one remedy modified (W-4: ratchet test, not rename).
- The cross-model pairing earned its keep: neither model alone produced the full fix
  list — the collapse argument is Opus-only, the frame-violation argument and the
  transport are Codex-only.

## Design Health Score

**4/10** for the sketch as submitted: two of five premises factually wrong (both
errors flattering the design), one of three signals dead under the sketch's own
rule, a second conditional on an unlanded unit, transport cost understated by two
contract changes, and the wire-cost gate misattributed. Held above 3 by: the ratified
frame correctly restated and respected, options (б)/(в) rejected for the right
reasons, and the core signal (`severity_escalated`) being real, valuable, and
genuinely dissolved — the surviving one-signal design is sound. With the mandatory
fixes applied, the revised sketch projects to ~8.
