# BT-4 Defender report (2026-08-20)

Verbatim return of the Defender agent (Opus). Verification method: direct reads of findings.py ranges plus five executable probes against an in-memory tracker reproducing the disputed behaviours. sqlite 3.47.1.

Positions: Opus adversary (18 findings) — 14 CONCEDE, 4 PARTIAL (FATAL-2, SERIOUS-5, SERIOUS-7, NITPICK-18), 0 DEFEND. Codex/Sol (24 findings) — 23 CONCEDE, 1 PARTIAL (C-DS-1), 0 DEFEND.

## Key reproduced probes

- FATAL-1: card filed source="human", re-observed source="claude", ref="v2.1.0", tags=["release-blocker"] → dedup bumped; `query tag=release-blocker: 0`, `query source=claude: 0`, `query ref=v2.1.0: 0`. Three filters, three zeroes.
- FATAL-2 (concede half): the attacker's ring-aware SQL works — `ring-aware tag query: ['CB-1']` (row whose tag exists only in meta.occurrences), one statement. DEFENSE half: option (б) still fails honestly — ring bounded first-10+last-10 (`findings.py:379-384`, `:569-573`) so it answers "recently observed" not "ever observed"; `tag_report` (grouping.py:422) and the row body untouched.
- FATAL-3: `meta` — after re-observation with meta={"rc":"124"}: top-level keys ['category_minted','occurrences'], `query meta_key=rc: 0`, ring last entry carries 'meta'. `category` — absent from `_occurrence_entry` (:470-502) and SET clause; ring entry has category? False; IS a fingerprint input (`_derive_fingerprint(category, file, description, meta)`, :452/:675).
- SERIOUS-6 (most consequential): (1) pre-CB-60 row re-observed with its OWN verbatim spelling → `created` (fork), not bumped — `normalize_category` folds the observation before `_derive_fingerprint` while the stored fingerprint hashes the old spelling; the gate does not refuse because `_existing_categories` normalizes stored spellings for membership. (2) near-miss typo + supplied fingerprint → ValueError BEFORE db.txn, occurrence lost (occ stays 1). (3) mint uncounted on bump path (findings.py:734-737 says so itself).
- C-MR-5 sharper case: `new_category=True` + supplied fingerprint hitting a live row → bumped; the authorized new category evaporates — not stored, not in ring, not counted.
- C-DS-7: bug in the SOURCE, not the document — `import_findings` docstring (:1049-1051) claims "fixed/stale would reopen" but `_REOPEN_STATUSES = ("fixed",)` (:61); `stale` is live and merely bumps. File as its own trivial card.
- C-DS-8: fresh DB lacks `idx_findings_reported_at_ref` — SCHEMA declares the column but not the index; only migrated DBs have it. Third independent disproof of "no ref consumer". Separate card.
- SERIOUS-8: `update_finding` full-replaces tags (:1578-1580) — a human deleting `release-blocker` gets it back on the filer's next observation, forever; union-merged column has no bound (ring is bounded, the column would not be).
- WEAKNESS-10 ≙ C-MR-6: `TestStoredCorruptionClassification` (test_dedup.py:582) pins malformed stored meta = pre-write rollback vs malformed stored tags = PostCommitCorruptionError after commit; a tags union moves that class across the pinned line — strict-parse vs tolerant `parse_tags` must be chosen explicitly.
- C-MR-1: `reported_at_ref` explicitly mutable by design — provenance-design.md:67-71 ("you might tag a release after findings were filed"), `update_finding`:1609, `tests/test_findings.py:1408`. "Frozen like CB-53" as written repeals a landed public contract.
- SERIOUS-5 ≙ C-DS-3: `_effective_commit` has ONE call site (provenance.py:655); `query(commit=)` (findings.py:1880-1884) still reads the frozen column — "readers consult the ring" is true of one reader of two.
- SERIOUS-7 (partial): the import opt-out is a landing obligation, not a refutation — per-flag AST ratchet is the repo's normal pattern (`TestEscalateOptOutRatchet`); ship `promote_tags=False` at the single import call site + its own ratchet + surface-absence test.

## Independently corroborated across both model families (none defended, all reproduced)

missing `meta` member (FATAL-3 ≙ C-MR-3); missing `category` member (FATAL-3 ≙ C-MR-4); false "no ref consumer" premise (SERIOUS-4 ≙ C-MR-2); CB-53 one-reader-of-two partiality (SERIOUS-5 ≙ C-DS-3); CB-60 category-loss path (SERIOUS-6 ≙ C-MR-5); corruption-contract shift under tags union (WEAKNESS-10 ≙ C-MR-6); incomplete SET-clause cite (FATAL-3 ≙ C-DS-5); import opt-out obligation (SERIOUS-7 ≙ C-MI-2).

## Verdict: do not ratify. Rebuild, do not patch.

### Five concessions that must drive the rebuild

1. **Population is nine fields, not three, and must be GENERATED** — mechanical three-way diff SCHEMA × `_occurrence_entry` × `_bump_row` sets builder, plus a query-consumers column. `meta` and `category` get their own rows.
2. **`category` is the highest-severity row and is in nobody's table**: reproduced silent identity fork on pre-CB-60 rows; occurrence destroyed on near-miss typo with supplied fingerprint; mint uncounted/evaporated on bump path. Landed four days ago.
3. **Re-cost every row from the six column-reading filters** (`query_findings:1856-1887`): tags is not unique; ref and meta share the shape; `source` freeze = declared first-reporter semantics + a CB-21 cell, not a free docstring.
4. **Split "frozen" into observation-frozen vs immutable, re-decide `reported_at_ref`**: explicitly mutable by design; "inherit CB-53" repeals a landed contract; CB-53's landed scope covers one of two readers; `query(ref=)` must be given one meaning (first / manual release / latest usable / any-retained — the bounded ring cannot support "ever observed").
5. **Replace the false impossibility with the true cost; price the union honestly**: option (б) works in four lines of SQL but answers "recently observed" only and leaves `tag_report`/row body untouched; option (а) is unbounded and un-removable (full-replace `update`). Sub-decision required: cap / tombstone set / `finding_tags(finding_id, tag, origin)` relation (the only shape that makes removal and import-origin expressible).

Full defender text lives in the conversation transcript of the review session; this file preserves the probes, positions and rebuild drivers.
