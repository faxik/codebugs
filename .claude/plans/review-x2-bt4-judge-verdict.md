# JUDGE — FINAL VERDICT on BT-4 (adversarial-review-x2, 2026-08-20)

Verbatim return of the Judge agent (session tier). The judge spot-checked every load-bearing claim personally against the landed tree (query_findings :1856-1887; _occurrence_entry :470-502; _bump_row :576-591; _REOPEN_STATUSES :61 vs import docstring :1049-1051; update_finding :1578-1580, :1609-1611; CB-60 gate :900-905 and insert-only stamp :734-738; provenance._effective_commit one call site; provenance-design.md:71; SCHEMA index list :50-53) and re-ran FATAL-2's ring-aware SQL.

## Verdict Summary (condensed — full table in the review conversation)

- FATAL-1 (+C-DS-4): "tags is the only column with a real cost" refuted by six column-reading filters. FATAL.
- FATAL-2 (Opus-only): option (б) eliminated by a FALSE impossibility claim; ring-reader SQL works in one statement; honest residual costs are bounded-ring semantics + untouched tag_report/row body. FATAL.
- FATAL-3 (≙C-MR-3/4, C-DS-5, corroborated): population is 9 fields, not 7; meta and category omitted; SET-clause cite incomplete. FATAL.
- SERIOUS-4 ≙ C-MR-2 (corroborated): query(ref=) is a live consumer. SERIOUS.
- SERIOUS-5 ≙ C-DS-3 (corroborated): CB-53 landed for one reader of two; "inherit" propagates a partial contract. SERIOUS.
- SERIOUS-6 ≙ C-MR-5 (corroborated): CB-60 category-loss paths — code defects, routed to cards. SERIOUS (doc) + code cards.
- SERIOUS-7 ≙ C-MI-2 (corroborated): import opt-out = separate flag + own AST ratchet; never overload escalate. SERIOUS (obligation).
- SERIOUS-8 (Opus-only): union unbounded + un-removable (update full-replace). SERIOUS.
- C-MR-1 (Codex-only): reported_at_ref is MUTABLE BY DESIGN (release tagging after filing) — "frozen like CB-53" repeals a landed contract. SERIOUS. Most consequential single-model catch.
- WEAKNESS: branch totality (W-9), corruption-line shift strict-vs-tolerant (W-10 ≙ C-MR-6), landing obligations incl. CB-63's unmet CLAUDE.md exit (W-11), ring-source contamination via import (W-12), premise verification abdication (W-14, root cause), "всем очередям" overstatement (C-DS-2), derived-fingerprint nuance (C-DS-9), identifiers unexpanded (N-17 elevated to WEAKNESS).
- DISMISSED: NITPICK-18 (BT disproportionate) — the review's own output (9-field population, reproduced identity fork) retroactively justifies the BT.

## Rebuild vs patch — ruling: REBUILD

The premise block, the population, and the cost column of all three rows are each independently broken — they ARE the document. The skeleton survives (one-decision framing, per-column ratification table, frozen-context section, process). The rebuilt table must be GENERATED — three-way diff SCHEMA × _occurrence_entry × _bump_row sets builder, consumers column from one grep of query_findings — not hand-enumerated a second time.

## Mandatory Fixes 1-13 (all applied in the rebuilt BT-4)

1 generated premise block, 9-field population, meta/category/fingerprint classified; 2 re-cost every row against six filters; 3 option (б) with true cost; 4 category row as highest-severity (reproduced CB-60 interactions; C-ALT-5 option); 5 meta row with explicit policy choice; 6 split "frozen" into observation-frozen vs immutable, reported_at_ref = observation-frozen but manually mutable, define query(ref=) semantics; 7 source = declared first-reporter semantics across all three readers + CB-21 link + peer-source ring caveat; 8 concrete import opt-out (separate flag, single call site, own ratchet) + fix "без изменения сигнатур" self-contradiction; 9 honest union price + sub-decision (cap / tombstones / finding_tags relation); 10 totality over three dedup branches per column; 11 strict-vs-tolerant stored-tags parsing choice; 12 landing obligations (CHANGELOG, CLAUDE.md dedup section, wire golden, structural-test extension); 13 expand every internal identifier.

## Code findings routed to tracker cards (independent of the document's fate)

1. CB-60 dedup-path category loss — HIGH (identity fork on pre-CB-60 spellings; occurrence lost on gated typo with supplied fingerprint; authorized mint evaporates on bump path; no category cross-check in _live_row_by_fingerprint).
2. import_findings docstring contradicts _REOPEN_STATUSES — LOW (says "fixed/stale would reopen"; stale is live and merely bumps).
3. Fresh DBs never receive idx_findings_reported_at_ref — MEDIUM (SCHEMA declares column, not index; both creation sites live in migration paths fresh DBs never take).
4. (optional, curator's call) query(commit=) as CB-53's second ring-blind reader — belongs in the rebuilt BT-4's semantics cell first.

## Cross-model scorecard

Codex-only: C-MR-1 (mutable-by-design ref contract), C-DS-7 (import docstring lie), C-DS-8 (fresh-DB index), C-DS-9, the C-MI/C-ALT catalogue (finding_tags relation; query(ref=) semantics menu).
Opus-only: FATAL-2 (false impossibility with working counter-query — "the owner is being handed a rigged menu"), FATAL-1 in full generality, SERIOUS-8, W-9, W-11, W-12, process findings.
Both independently: eight corroborated pairs, all reproduced, zero defended.

## Design Health Score: 4/10

Back to the drawing board for the premise block and all three table rows; the skeleton survives. Do not ratify; rebuild from generated evidence, then a light verification pass (a full second x2 is not required if the table is generated rather than hand-enumerated — the failure mode guarded against is enumeration, and generation removes it). The three code cards route independently and do not wait for the document.
