# BT-7 «Локационный якорь» — JUDGE VERDICT (adversarial-review-x2)

Target: `.claude/plans/BT-7-location-anchor.md` (v1, DIR-2, 2026-08-22)
Inputs: `review-x2-bt7-opus-attack.md` (34 findings), `review-x2-bt7-codex-attack.md` (35 findings,
numbered C-R1..12 / C-S1..10 / C-M1..9 / C-A1..4 as the defender did),
`review-x2-bt7-defender.md` (55 CONCEDE / 13 PARTIAL / 1 DEFEND).

**Judge's method.** I read all four documents end to end and re-verified the load-bearing claims
against the tree myself, read-only, rather than trusting either attacker's or the defender's
`file:line`. Everything below marked *verified* I ran or read.

Spot-checks performed (all hold):

| # | Claim | Where | Result |
|---|---|---|---|
| 1 | Registering a resolver reserves its `meta_keys` against caller-supplied meta | `db.py:280-300` docstring ("findings reserves the union"), `db.py:333` `resolver_reserved_meta_keys`, `findings.py:278-282` `reserved = _RESERVED_META_KEYS \| db.resolver_reserved_meta_keys()` | **verified** |
| 2 | Resolver patch is the right-hand side of `dict.update` (resolver wins, not caller) | `findings.py:1049-1050` `meta_final.update(db.run_pre_add_resolvers(...))`; `_derive_fingerprint` at `:938` runs BEFORE resolvers at `:1050` | **verified** — and the ordering also proves Р1 holds structurally |
| 3 | `_validate_resolver_outcome` refuses undeclared keys | `db.py:373` `bad = (set(outcome) - resolver.meta_keys) \| (set(outcome) & forbidden)` | **verified** |
| 4 | `batch_add_findings` has no `annotate` parameter; one `db.txn`, `_add_one` per member | `annotate` at `findings.py:891, 1044, 1206, 1211, 1279, 1392-1394, 1501-1506`; none in `batch_add_findings` (`:1792`); `with db.txn(conn)` then `_add_one(` inside the loop | **verified** |
| 5 | `file_status` containment: `commonpath` → `unknown(out_of_repo)` | `provenance.py:338`, `:345` | **verified** |
| 6 | `_verdict` returns exactly `{file_status, reason}`; rename destination only in prose | `provenance.py:149`, `:550` `_verdict("renamed", f"{file_path} renamed to {new_path}")` | **verified** |
| 7 | MCP `staleness_check` has no `project_dir`; cache keyed `(file, effective)` | `provenance.py:801-806`, `:659` | **verified** |
| 8 | libcheck writes `{"lines": [line_numbers]}` = individual lines, via `batch_add` | `~/.claude/skills/libcheck/SKILL.md:132-135` | **verified** |
| 9 | `relations.py` opening paragraph is the table-over-meta precedent ("merge-only… cannot forget") | `relations.py:1-9` | **verified** |
| 10 | `sites` key exists in this tracker and §1а missed it | `codebugs export-csv` (read-only, 129 rows today): `lines` 44, **`sites` 18**, `site`/`line`/`anchor`/`function` 0 | **verified** |
| 11 | Defender's counter on SERIOUS-10: `[0, 0]` is a real writer shape | `~/.claude/skills/arch-health/state/pending-2026-08-17-arch-health.py:195,224,245,265,287,302` | **verified** — the defender's PARTIAL is earned |
| 12 | `_normalize_for_fingerprint` reads top-level `str` values only | `findings.py:437-440` | **verified** (W-1 / C-S9) |
| 13 | `get_stats` groups only by persisted columns | `findings.py:2325-2333` | **verified** (C-R6) |
| 14 | `restore_findings` bypasses resolvers and keeps reserved meta verbatim | `findings.py:1612` | **verified** (C-M6) |
| 15 | RFC D5 is "out of scope", not an immutability boundary | `RFC-identity-graph-2026-08-17.md:280` | **verified** (C-R7) |
| 16 | 23.7 ms is CB-45's number; 34.5 ms is the RFC's | `CB-45-similarity-seam.md` has 23.7 ×3 and 34.5 ×0; RFC `:213`, `:349` have 34.5 | **verified** (W-3) |
| 17 | `similarity.py` declares `updatable_keys=("similar_to",)`, `MIN_TEXT_LEN = 40` | `similarity.py:380`, `:58` | **verified** |
| 18 | Occurrence ring is bounded first-10 + last-10, `description` capped at 2000 | `findings.py:395-397`, `:631-636` | **verified** — relevant to the C-A4 spine (see Judge's own finding below) |

**Judge's own finding, not raised by either attacker and overclaimed by the defender (J-1).**
The defender's recommended spine is "C-A2 + C-A4: capture outside the lock, into the occurrence
ring — zero new seams, zero core knowledge of the extension's key". That last clause does not
survive the code. On a dedup hit the bump path (`_bump_row`, `findings.py:~600-640`) builds the
occurrence entry and runs **no resolver** — verified, and the defender says so itself (C-R4,
C-R11). So a precomputed anchor from the pre-lock phase can only reach `meta.occurrences[*]` if
the CORE carries it there, which means either the core knows the key `anchor` (what CB-45 forbids)
or a NEW generic seam exists: a pre-lock "observation enricher" registry whose output is carried
into the occurrence entry under a namespaced slot, on insert and bump alike. That seam is far
cheaper than a bump-side resolver (pure data, no SQL, no SAVEPOINT discipline) — but it is a seam,
and the redraft must name it and price it rather than inherit "zero seams" from the defender.
Two further costs of C-A4 the defender did not price: (a) ring entries carry `text` + two hashes +
context, ×20 retained entries — a `_OCC_TEXT_CAP`-style bound is needed, in the same spirit as
`_OCC_DESC_CAP = 2000`; (b) `restore_findings` and `import_findings` both handle `occurrences`
verbatim/stripped respectively, so a ring-resident anchor inherits those contracts automatically —
which is actually an argument FOR the ring (C-M6's asymmetry is already decided there) and should
be said.

---

## Verdict Summary

Severity scale: FATAL = the document cannot go to the owner with this in it; SERIOUS = must be
fixed before ratification; WEAKNESS = fix in the redraft, cheap; NITPICK = cosmetic; DISMISSED =
no action. "Corroborated" = both models raised it independently.

| ID | Adversary Claim | Defender Response | Your Ruling | Severity |
|---|---|---|---|---|
| **FATAL-1** / C-R1 | Registering the capture as a pre-add resolver reserves `anchor`; caller cannot supply it; and even if it could, resolver overwrites caller (`dict.update` RHS). Р3(iii) also breaks without `updatable_keys`. | CONCEDE, proved by execution (add refused; with `updatable_keys` update works, add still refused). Rewrite Р4; name `updatable_keys=("anchor",)` in Т-a. | Evidence holds (spot-checks 1-3). Codex's refinement — `updatable_keys` buys Р3(iii) but can never buy "caller wins on add" — is the precise statement. **Corroborated by both models.** | **FATAL** |
| **FATAL-2** / C-R8 | §4 reads a length-2 int list as `[min,max]`; libcheck's contract is individual lines; 76 rows misread as ranges, 130 collapsed to first element. | CONCEDE. Every element is a separate line; take first, count the rest as `anchor_sites_dropped`. | Holds (spot-check 8). Confident-wrong-answer class. **Corroborated.** | **FATAL** |
| **FATAL-3** | §4's `file:N-M` branch gated on exact `file`-column match captures 2/31 (codebugs), 0/38 (autosorter); the dialect is basenames. | CONCEDE. Either delete or gate on basename AND refuse multi-filename rows; cite the matching count, not the token count. | Holds. Individually this is a dead branch with a false justification; it is FATAL only as part of the §4 cluster. **Opus only** — Codex missed it. | **SERIOUS** (FATAL as part of the §4 cluster with FATAL-2/4) |
| **FATAL-4** | §1а's key census missed `sites`/`site` in both corpora (18 + 43 rows); `anchor` in autosorter holds card ids, not prose. | CONCEDE; re-sweep by SHAPE, not name list; grammar must read or count-refuse `sites`/`site`. | Holds — I reproduced `sites` = 18 myself (spot-check 10). The document committed the exact enumeration error CLAUDE.md names and §4 quotes at itself. **Opus only** — Codex missed it, which is itself signal that a name-list sweep is easy to miss even for a reviewer. | **FATAL** |
| **FATAL-5** / C-R2 / C-M3 | Capture on absolute paths + storing `text` turns MCP `add` into an arbitrary-file-read that lands in the DB and every CSV export; no size/kind/encoding policy. | PARTIAL: the consequence is real; "deliberately deleting the containment check" overstates (BT-7 opens a NEW reader, `file_status` is untouched). Add §7а; route out-of-repo reading to the owner as question 4 (CB-91 is open). | Defender's reframing is correct and better than the attack's: the right fix is a declared boundary, not a restored check. Severity stays FATAL — it is the only finding that would ship a capability hazard rather than a document defect. **Corroborated** (Codex's CB-91 trust-boundary framing outranks Opus's). | **FATAL** |
| **FATAL-6** / C-R4 | П3 says anchor MUST follow newest observation; Р3(ii)★ says don't build it; §8 q1 asks the owner to ratify "обновляется новейшим наблюдением"; q2 asks to defer it. | CONCEDE; three edits together (П3, q1, q2), q2 becomes three-way once C-A4 is on the table. | Holds on a plain reading. The owner's standing rule (question carries cost of each option) cannot be satisfied by q1 as written. **Corroborated.** | **FATAL** |
| **FATAL-7** / C-R5 | A's "survives rename" cell is not implementable: destination exists only in `_verdict`'s prose `reason`, `_displayable` mangles non-UTF-8, `file` is IMMUTABLE. | CONCEDE; either drop the cell or add structured `new_path` + public path API as Т-0. | Holds (spot-check 6). I downgrade one notch: the DIRECTION does not depend on rename survival, and the fix is a named unit (Т-0) or an honest cell. It is an overclaim in the options table, not an unratifiable contradiction. **Corroborated.** | **SERIOUS** |
| **FATAL-8** / C-R3 / C-M8 | Batch path never mentioned; no `annotate` on `batch_add_findings`; N file reads inside one `BEGIN IMMEDIATE`; CB-45 measured 2.4 s/100-batch for a CPU-only resolver. | CONCEDE; adopt C-A2 two-phase capture before `db.txn`; per-batch path cache (C-M8). | Holds (spot-check 4). libcheck — the dominant producer of list-typed `lines` — files via `batch_add`, so this is the primary path, not a corner. Dissolved structurally by C-A2. **Corroborated.** | **FATAL** (dissolved by the mandatory C-A2 restructure) |
| SERIOUS-1 / C-S10 | `_resolve_candidate`/`_repo_root` are private; `similarity.py` precedent is the opposite ownership pattern. | CONCEDE; Т-0 public path-resolution API in provenance. | Holds. **Corroborated.** | **SERIOUS** |
| SERIOUS-2 | §2's four rows are CB-95's own bullets; "порождена из П1–П9" is false. | PARTIAL: false about rows, true about columns; downgrade the claim, add rows E/F. | Defender is right on both halves. The sentence is a methodology overclaim; the missed options are adjudicated separately. | **WEAKNESS** |
| SERIOUS-3 | A separate anchor TABLE was never considered; `relations.py` is the in-repo precedent; dissolves multi-site and non-removability. | PARTIAL: a table was not evaluated, but `meta.anchor` is not the 164-key substrate relations replaced; Р2's version argument survives; add row E and compare on queryability/retractability. | Holds as a partition error in the storage decision (spot-check 9). The storage model is now a THREE-way choice — top-level `meta.anchor` / occurrence ring (C-A4) / table — and the document evaluated one against a strawman ("колонка"). This is structural. **Opus only.** | **SERIOUS** (structural: storage model must be re-decided in v2) |
| SERIOUS-4 / C-S1 / C-A1 | Option C dismissed on unmeasured O(history); the cheap single `git diff -U0 --find-renames` hunk-remap variant exists and its parser is already in provenance. | CONCEDE; split C into C1 (reject, cost asserted) and C2 (evaluate; compose C2 → A → unknown). | Holds. The composition C2-first/A-fallback is the right cascade and should be in D. **Corroborated.** | **SERIOUS** |
| SERIOUS-5 / C-M6 | `import_findings` strips the resolver-key union; `anchor` already holds card ids on 16 autosorter rows → silently dropped on import; name is taken. | CONCEDE; rename the key (`loc_anchor`/`location`), say so in q1's package. | Holds. Renaming before anything ships costs nothing. **Opus raised the collision; Codex raised the import/restore asymmetry** — together they form one contract the document never declared. | **SERIOUS** |
| SERIOUS-6 / C-M4 | "silent" and "queryable `resolver_errors`" are opposites; 381-385 corpus rows would stamp `resolver_errors`; three incompatible failure classifications across Р3(i)/§4/§7. | CONCEDE; capture never raises; one closed `anchor_capture_skipped(reason)` vocabulary. | Holds (db.py:457-458 does both). **Corroborated.** | **SERIOUS** |
| SERIOUS-7 / C-S8 | No `MIN_TEXT_LEN` analogue; whitespace collapse erases indentation (semantic in Python), making a reindent invisible to `hash` and `context_hash` together. | CONCEDE; `MIN_ANCHOR_CHARS` calibrated, `MAX_ANCHOR_LINES`, leading whitespace → depth token. | Holds (spot-check 17). **Corroborated.** | **SERIOUS** |
| SERIOUS-8 | `check_findings` cache key `(file, effective)` is wrong for a working-tree probe; MCP `staleness_check` has no `project_dir` and binds to `_ambient_cwd()` which can be `None`. | CONCEDE; anchor probe not cached by that key; `anchor_resolve` takes `project_dir`, defaults via `describe_root()`. | Holds (spot-check 7). This is CB-11/CB-49's "binding you cannot see" lesson and Р5 would have created a third answer to "which directory". **Opus only.** | **SERIOUS** |
| SERIOUS-9 | `meta` cannot delete a key; a `lost` anchor is permanent. | PARTIAL: rewritable but not removable; read side reports `lost` honestly; define a tombstone. | Defender is right: on a read-side design a stale stored anchor yields `lost`, not a wrong answer. A one-sentence tombstone closes it; a table or ring storage dissolves it. Downgraded. **Opus only.** | **WEAKNESS** |
| SERIOUS-10 | `[0, 0]` "measured shape" occurs zero times in both corpora. | PARTIAL: absent from the snapshot (2026-08-17 09:57), present in a live writer's source at six call sites; the claim's PROVENANCE is wrong, the shape is real; under the corrected list rule `line >= 1` catches it. | Defender's counter verified (spot-check 11). The attacker's method — corpus-only — could not see it; a writer-source sweep could. Downgraded to a provenance-of-claim error. **Opus only.** | **WEAKNESS** |
| SERIOUS-11 | §5's backfill "checkout'ом того коммита" is destructive in a live worktree. | CONCEDE; `git show <commit>:<path>`. | Holds. One word-class, but a deferred bullet is how a mechanism ships as written. **Opus only.** | **SERIOUS** |
| SERIOUS-12 / C-M7 | §9 omits `_ensure_modules_loaded`/`SERVER_NAMES`/`--mode`; Т-a alone changes `add`/`update`/`import` behaviour before any reader exists. | CONCEDE; §9 becomes Т-0/Т-a/Т-b/Т-c with registration and the behaviour change stated. | Holds. The second half is the sharp one. **Corroborated.** | **SERIOUS** |
| W-1 / C-S9 | П8's "normalization trap" is inert (top-level `str` only); the real trap is the inverse (flattening); Р1 holds structurally because fingerprint derives before resolvers run. | CONCEDE; rewrite П8 around the inverse trap; add the structural reason to Р1. | Holds (spot-checks 2, 12). The attacker's addendum gives Р1 a reason the document lacked — keep it. **Corroborated.** | **WEAKNESS** |
| W-2 | A's "works outside git" cell vs Р3(i)'s "from worktree root" gate contradict. | PARTIAL: algorithm vs deployment choice; make the gate say which, tied to §7а/q4. | Defender is right that these are inconsistent rather than both false. Fix lands with §7а. | **WEAKNESS** |
| W-3 | 34.5 ms misattributed to CB-45 (it is the RFC's; CB-45 says 23.7). | CONCEDE. | Holds (spot-check 16). | **NITPICK** |
| W-4 | §6 cites D1 as undecided; S1 (relations) has shipped. | CONCEDE. | Holds (`relations.py` exists, registered as `coderelations`). | **WEAKNESS** |
| W-5 / C-M9 | `confidence` in Р5's shape, defined nowhere; precedent has `reason` not confidence. | CONCEDE; delete. | Holds. **Corroborated.** | **WEAKNESS** |
| W-6 | "построчный seek" does not exist; a size cap does not bound a high-line read (corpus max 5708). | CONCEDE; `max_bytes_read`. | Holds. | **WEAKNESS** |
| W-7 | No encoding/binary/node-kind policy; `fsio.py` carries the taxonomy for the write direction. | CONCEDE; folded into §7а. | Holds; part of FATAL-5's fix. | **WEAKNESS** (folded into FATAL-5) |
| W-8 | Owner asked for "auto-resolution of the new line"; after BT-7 it is reachable only from a verb an agent must know to call. | CONCEDE; `get` carries the STORED anchor by default, resolved only on `resolve_anchor=True`. | Holds, and the defender's split (stored by default, resolved on request) preserves Р5's purity rule correctly. | **WEAKNESS** |
| W-9 / C-S4 | Tail counts: 206 not ≥210, 15 not 19; `file:line`/prose split differs. | PARTIAL: head errors real; the split is classifier-dependent and no party reproduces another's (doc 41/6, Opus 40/7, defender 38/9; invariant total 47). | Defender is right: publish the regex, not the split. **Corroborated** on the two head numbers; the tail split is unreproducible by all three parties. | **WEAKNESS** |
| W-10 / C-S5 / C-S6 | "One primary anchor" answers ~⅔ of located cards partially; multi-site is the dogfood norm. | PARTIAL: attacker's 21/32 reproduces on neither axis; doc's 33 multi-FILE is wrong (12/32); the real number is 30/32 multi-SITE — conclusion stronger than the number. | The conclusion is what matters and it is structural: a single primary anchor answers 2 of 32 dogfood cards completely, plus 18 `sites` rows are plural by construction. This feeds the storage-model decision (SERIOUS-3). Upgraded. **Corroborated** (from opposite directions). | **SERIOUS** |
| N-1 | П3 misstates `file_status`' shape (`file_status` key; `renamed`/`deleted` reasons are prose). | CONCEDE. | Holds. | **NITPICK** |
| N-2 | "ровно два писателя" is true of `src/`, not of the system (MCP docstring invites `lines`). | CONCEDE. | Holds; explains the 9 grammars. | **NITPICK** |
| N-3 | "second zero-SQL extension" transfers the letter of the similarity precedent, not the reason (external I/O in a foreign transaction). | CONCEDE. | Holds; C-A2 is what makes the precedent transferable. | **NITPICK** |
| N-4 | Confirmations: П1, П6, П7, П8 tuple, `--meta`-beats-`-l`, resolvers under lock, no bump resolvers, no `ast`, `f0b4010`, CB-95 quote. | DEFEND: re-verified, no action. | Agreed. These premises survived and the redraft must NOT re-derive them. | **DISMISSED** (no action — confirmation list) |
| C-R6 | `anchor_capture_skipped` "метрика в `stats`" cannot be computed: nothing is persisted and `get_stats` groups only persisted columns. | CONCEDE; recommend (c): drop the counter, gate §5 on `anchor_resolve`'s `lost` rate instead. | Holds (spot-check 13). The demand trigger behind three deferrals was uncomputable as specified. **Codex only** — Opus missed it. | **SERIOUS** |
| C-R7 | Р1/П4/§6 mischaracterize RFC D5 as an immutability boundary; it is "out of scope"; "не в `auto:v2`" pre-decides it. | CONCEDE; scope Р1 to `auto:v1`. | Holds (spot-check 15). **Codex only.** | **WEAKNESS** |
| C-R9 | Р5 defines no compatible API contract with `check_findings`' batch shape. | CONCEDE; nest `anchor: {...} \| null` per record; `anchor_resolve` mirrors `check_findings`' batch contract. | Holds; the nest-don't-merge rule (two producers of `reason`) is correct. **Codex only.** | **SERIOUS** |
| C-R10 | "Manual re-anchoring" via `meta_update` has no capture/validate path; users must reimplement the normalizer and can persist malformed anchors. | PARTIAL: Р3(iii) promises permission, not a helper; add `anchor_recapture` verb + validator. | Defender's narrowing is fair, but Р3(iii) is the ONLY repair path in the ★ design, so a missing verb is a design gap, not a nicety. | **SERIOUS** |
| C-R11 | Existing cards cannot acquire anchors (backfill deferred; bumps return before resolvers), so §5's `lost`/`moved` triggers are unmeasurable on the live corpus. | CONCEDE; "strongest structural finding in either report"; recommend C-A4. | I agree with the defender's assessment and upgrade it: this invalidates the document's own decision PROCEDURE (three deferrals gated on a measurement that cannot be taken), not one clause. **Codex only** — Opus missed it entirely. | **FATAL** |
| C-R12 | The anchor does not stabilize dedup across file moves (`file` is an `auto:v1` input). | PARTIAL: Р1 argues against ADDING instability, not that identity is stable; state the limit in §0. | Defender is right; a scope sentence closes it. | **WEAKNESS** |
| C-S2 | "A works on the entire population" is false; 381 rows return `unknown`. | PARTIAL: the document says so two clauses later; the headline word is wrong. | Agreed. | **WEAKNESS** |
| C-S3 | Cost table has unmeasured numbers/rankings stated as fact. | CONCEDE; mark `(оценка)`. | Holds. | **WEAKNESS** |
| C-S7 | `context_hash` is `str\|null` in Р2 and "обязателен" in §7. | CONCEDE; required at capture; `null` replaced by recorded widths. | Holds — a genuine internal contradiction in the stored contract. **Codex only.** | **SERIOUS** |
| C-M1 | No invariants on `line`/`end`/`v`/`text`/bool-as-int/EOF. | CONCEDE; INVARIANTS block + shared validator. | Holds; this repo has been bitten by bool-as-int twice. | **SERIOUS** |
| C-M2 | Hash unspecified: algorithm, digest, encoding, EOL, multi-line separator, context width, inclusion. | CONCEDE; sha256 over JSON array, 32 hex, EOL-normalized, context excludes anchor lines, N stated. | Holds; the JSON-array-not-joined-string lesson is already in `_derive_fingerprint`'s docstring. | **SERIOUS** |
| C-M5 | No `resolved_against` snapshot identity on the verdict. | CONCEDE. | Holds; `check_findings` already sets the standard with `checked_commit`/`current_head`. | **WEAKNESS** |
| C-A1 | Diff-hunk mapping before content fallback. | ADOPT. | Correct; same as SERIOUS-4's C2. | **Recommended** |
| C-A2 | Precompute capture outside `db.txn` (RFC S2's ratified two-phase). | ADOPT; "single highest-value change". | Correct and mandatory — it dissolves FATAL-8/C-R3/C-M8 and most of §7. Note J-1: it also requires a carrier into the occurrence entry if C-A4 is taken. | **Mandatory** |
| C-A3 | Structured `line=`/`end=` on `add` as primary input. | PARTIAL: §4 did consider and defer it; re-argue against the corrected grammar. | Defender is right that it was considered; the deferral reason ("free for clients") is now false after FATAL-2. Recommended, not mandatory. | **Recommended** |
| C-A4 | Occurrence-level capture + derived latest anchor. | PARTIAL: §5 bullet 4 names it but evaluates it as a historical record only; promote to a real Р3 variant; q2 becomes three-way. | Correct direction. But see **J-1**: "zero new seams" is an overclaim — the bump path runs nothing, so a generic pre-lock enricher seam is still required. Must be priced honestly in v2. | **Mandatory** (as an evaluated option, with J-1's cost) |

**Cross-model disagreement signal.**
- *Opus found, Codex missed:* the `sites`/`site` census (FATAL-4), the `file:N` gate death (FATAL-3), the key-name collision (SERIOUS-5), the cache-key/ambient-cwd binding (SERIOUS-8), the destructive checkout (SERIOUS-11), the table option (SERIOUS-3). Pattern: Opus was stronger on *measurement against the corpus and on this repo's own history*.
- *Codex found, Opus missed:* C-R11 (the deferral procedure is unmeasurable — the single strongest structural finding), C-R6 (the metric cannot be computed), C-S7 (`context_hash` contradiction), C-R7 (D5 mischaracterized), C-R9/C-M1/C-M2/C-M5 (contract under-specification), and all four alternatives including the two that become the redraft's spine. Pattern: Codex was stronger on *contract completeness and on alternatives*.
- Sixteen findings were corroborated, and every one of them sits in §1а, §4, Р3 or Р4 — the four sections the document claims are measured rather than reasoned. That concentration is the most important single fact in this review.

---

## Mandatory Fixes (before the document goes to the owner)

**Ruling: REBUILD as v2, do not patch.** The reasons: (1) the storage model, the capture
phase, the capture population and the grammar are all load-bearing and all change; (2) the
corroborated findings cluster in exactly the four sections that would have to be rewritten, so a
patch would touch more text than it keeps; (3) three of the ★-recommendations (Р2, Р3(i)+Р4, §4)
and both owner questions are wrong *together*, and section-local fixes are how an artifact acquires
new fatal defects while closing old ones (the owner's standing rule). Keep from v1 verbatim: §0's
scoping, П1/П6/П7/П9 (N-4 confirmed), §2's A/B rows minus the rename cell, Р1 (rescoped to
`auto:v1`), Р5's read-only principle, Р6's versioning principle, §6 with W-4's correction.

Structural changes for v2, in order:

1. **Storage model: decide among THREE options on one comparison, not one against "колонка".**
   (SERIOUS-3, W-10, SERIOUS-9, SERIOUS-5, C-M6.) Options: (a) top-level `meta.<key>` (v1's
   Р2); (b) per-occurrence in the ring with newest-usable read (C-A4); (c) a table
   `finding_anchors(finding_id, idx, …)` (relations.py precedent). Compare on: N anchors per card
   (30/32 dogfood rows are multi-site), retractability, queryability (`WHERE hash = ?`),
   import/restore contract (the ring already decides it; a table needs new export/import/restore
   round-trips), and ring-size cost (J-1). Whatever is chosen, **rename the key** — `anchor` is
   taken in a live peer tracker for card ids.
2. **Capture phase: outside the lock, two-phase, per RFC S2.** (FATAL-8, C-R3, C-M8, C-A2.)
   `add`/`batch_add` read and hash files BEFORE `db.txn`; each distinct resolved path read once
   per batch; the in-lock step is validation + stamp only. State the TOCTOU residual. **Name the
   carrier seam (J-1)**: how the precomputed object reaches the insert path (resolver patch) AND
   the bump path (occurrence entry), without the core learning the key name — a generic pre-lock
   "observation enricher" slot is the cheapest honest answer, and it is a new seam; price it.
3. **Capture population: every observation, not insert-only.** (C-R11, FATAL-6, C-A4.) Without
   this §5's triggers cannot be measured and the deferral method collapses. If the owner rejects
   per-occurrence capture, §5 must say plainly that its triggers are unmeasurable in v1 and Т-c
   (backfill via `git show`, never checkout — SERIOUS-11) moves from optional to required.
4. **Containment boundary is an owner decision, not inherited scope.** (FATAL-5, C-R2, W-2,
   W-7, C-M3.) New §7а: default `commonpath` containment identical to `file_status`;
   `S_ISREG` only (mirror `fsio.py:178-196`); numeric caps (`max_bytes_read`, `max_span_lines`,
   `max_text_bytes`); `errors="replace"`; NUL sniff; capture never raises (SERIOUS-6). Out-of-repo
   reading = owner question 4, with CB-89 (refused) and CB-91 (open) cited. §2's "works outside
   git" cell becomes conditional on that answer.
5. **Grammar re-derived from a SHAPE sweep over every meta key, including `sites`/`site`.**
   (FATAL-2, FATAL-3, FATAL-4, SERIOUS-10, C-S6, W-9.) Predicate: a value containing
   `NAME.ext:DIGITS` or an int-like line reference, applied to all keys; republish the seven-key
   census; `list[int]` = individual lines (libcheck contract), no range inference; `file:N` branch
   either deleted or basename-gated with multi-filename refusal, justified by the MATCHING count;
   `[0,0]` cited to its writer source, not to the corpus; "first range is primary" stated as a
   convention with its measured cost (2 of 32 answered completely); the irregular-residue split
   published as a regex, not a number.
6. **Р3(i)+Р4 rewritten together against the seam's real contract.** (FATAL-1, C-R1.)
   Registration IS reservation; caller cannot supply the key on add and that is correct
   (spoof-proof coordinate); `updatable_keys=(<key>,)` gives Р3(iii) and nothing more; delete the
   `fingerprint` analogy. If option 1(b) or 1(c) is chosen, say which parts of this paragraph
   still apply.
7. **Owner questions rewritten so q1 and q2 do not contradict.** (FATAL-6, C-R4.) q1 = exactly
   the ★ package, with "обновляется новейшим наблюдением" struck; q2 = a THREE-way choice
   (insert-only + manual / bump-seam / per-occurrence) with the cost of each, J-1's seam included
   in the third; q3 = symbolic anchor by demand (unchanged); **q4 = out-of-repo read boundary**
   (new). Each question reconstructed from zero per the owner's standing rule.
8. **Failure semantics: one closed classification at all three sites.** (SERIOUS-6, C-M4,
   C-R6.) Capture never raises; `resolver_errors` stays the broken-resolver signal; the demand
   metric is either stamped on the row or dropped in favour of `anchor_resolve`'s `lost` rate
   (the defender's (c) is the cheapest honest choice).
9. **Contract completeness for the stored object and the verdict.** (C-M1, C-M2, C-S7, C-R9,
   C-M5, W-5, SERIOUS-7.) INVARIANTS block (bool-as-int refused, `line >= 1`, `end >= line`,
   caps, unknown `v` → `unknown(unsupported_anchor_version)`); hash fully specified (sha256 over
   JSON array, 32 hex, EOL-normalized, context excludes anchor, N numeric); `context_hash`
   required with recorded widths; leading whitespace → depth token, `MIN_ANCHOR_CHARS`
   calibrated; verdict shape nests under `anchor:` with `resolved_against`; `confidence` deleted.
10. **Resolution cascade and the C row.** (SERIOUS-4, C-A1, FATAL-7.) Split C into C1 (rejected,
    cost marked as asserted) and C2 (single rename-aware `git diff -U0` + hunk remap); D's cascade
    becomes C2 → A → `unknown(reason)`; A's rename cell either dropped or backed by Т-0
    (`new_path` structured in the `renamed` verdict + public path-resolution API in provenance —
    SERIOUS-1/C-S10).
11. **Binding and caching for the read surface.** (SERIOUS-8.) `anchor_resolve` takes
    `project_dir`, defaults via `db.describe_root()`, never `_ambient_cwd()`; not cached under
    `check_findings`' `(file, effective)` key.
12. **§9 re-sliced**: Т-0 (provenance public API + `new_path`), Т-a (module + grammar +
    normalizer + three registrations + `updatable_keys` + tests for the behaviour change it
    causes in `add`/`update`/`import`), Т-b (read surface + wire golden), Т-c (backfill, status
    per item 3). (SERIOUS-12, C-M7.)

---

## Recommended Fixes

- Structured `line=`/`end=` arguments on `add` (C-A3): re-argue the deferral against the corrected
  grammar; the "free for clients" reason is gone after FATAL-2. File the CB-15-class card for
  `--meta` silently beating `-l` now, since it becomes a correctness issue the moment a structured
  argument exists.
- `get` returns the STORED anchor by default and the resolved one only on `resolve_anchor=True`
  (W-8) — meets the owner's "auto-resolution" request in practice without breaking Р5's no-I/O
  rule for `get`/`query`.
- Tombstone value for a retracted anchor (SERIOUS-9) if option 1(a) is chosen; moot under 1(b)/(c).
- Add the one-line scope sentence to §0 that the anchor stabilizes lookup inside a card, not dedup
  across file moves (C-R12).
- Rescope Р1 to `auto:v1` and correct П4/§6 on D5 (C-R7); add the structural reason Р1 holds
  (fingerprint derives at `:938`, resolvers run at `:1050`) and rewrite П8 around the INVERSE
  trap — flattening (W-1/C-S9).
- Mark every unmeasured cost cell `(оценка)` (C-S3); correct 23.7/34.5 attribution (W-3); fix
  W-4 (S1 shipped, D1 decided); N-1/N-2/N-3 wording.
- Keep the defender's note that the occurrence ring already carries import-stripped /
  restore-verbatim semantics (C-M6) — under option 1(b) that asymmetry is inherited, not invented,
  which is an argument worth stating in the comparison.

---

## Dismissed Findings

- **N-4** — a confirmation list, not a defect. The defender re-verified each item and so did I
  where it mattered (П6 reserved sets, П7 auto-capture, П8 tuple, no bump resolvers, no `ast`).
  Action: none, except that v2 must NOT re-derive these premises.
- **The "deliberately deleting the containment check" clause of FATAL-5** — dismissed as framing
  only; BT-7 does not touch `file_status`. The finding itself stands at FATAL under C-R2's framing.
- **The attacker's "21 of 32" number in W-10** — reproduces on neither axis per the defender; the
  conclusion survives on the defender's 30/32 multi-SITE count, which I did not independently
  re-classify (classifier-dependent, as W-9 shows for every party). The direction of the finding is
  not in doubt given `sites` = 18 alone.
- **SERIOUS-10 as a "fabricated shape"** — dismissed; the shape is real at six call sites in a
  live writer. What remains is a provenance-of-claim error (WEAKNESS).

---

## Design Health Score

**4 / 10** — back to the drawing board for the DOCUMENT, with the DIRECTION retained.

Justification. The scale says 1–4 is "back to the drawing board" and 5–6 is "significant rework".
This sits at the top of the lower band, not the bottom of the upper one, because the things that
are wrong are the things the document was FOR: the storage model was decided against a strawman,
the capture mechanism is refuted by execution, the capture population makes the document's own
deferral method unmeasurable, the grammar misreads its dominant writer and missed a locational key
in both corpora, the read boundary quietly takes a decision two cards left open, and both owner
questions are unanswerable as posed. Sixteen findings corroborated across two model families, all in
the four sections that claim to be measured. A patch would not be smaller than a rewrite. What earns
the 4 rather than lower: §0's scoping is right; eight premises survived verification verbatim; the
options table's A/B rows and cost COLUMNS are genuine work; and both attackers plus the defender
agree on the same spine for v2, so the redraft has an unusually clear brief.

**Is the DIRECTION sound even though the DOCUMENT failed? Yes, on all three legs, and no attack
touched any of them.**
- *Content anchor as core (A):* no attacker proposed replacing it; Codex's C2 (diff-hunk remap)
  COMPOSES with it as a first stage, it does not supplant it. A stays the universal fallback.
- *Resolve-on-read, never write the resolved line back:* attacked nowhere; it is what makes a
  stale stored anchor a `lost` verdict rather than a wrong answer (SERIOUS-9's defence rests on it)
  and what makes the two-phase TOCTOU residual acceptable (C-A2).
- *Anchor is not an identity input:* holds, and holds STRUCTURALLY for a reason the document
  never gave — `_derive_fingerprint` runs before the resolvers (`findings.py:938` vs `:1050`), so
  a captured anchor physically cannot affect its own row's fingerprint. Rescope the claim to
  `auto:v1` (C-R7) and it is unassailable.

**Which decisions are genuinely the OWNER's, and which are engineering.**

Owner's (they set policy, trade cost against value, or touch a boundary two cards already sent
upward):
1. **Out-of-repo reading** (q4, new): whether the tracker may read and store bytes from a path
   named by card data outside the worktree. CB-89 refused it, CB-91 left it open as a trust
   decision "not blocked on code". Nothing in engineering can decide this.
2. **Capture population / freshness** (q2, three-way): insert-only + manual re-anchoring vs a
   bump-side seam vs per-occurrence capture. This is the first exception to BT-4's "top-level meta
   is authored state" if (a) or (b) is chosen, and BT-4 was the owner's ratification; per-occurrence
   (c) avoids the exception but costs ring space and a new carrier seam (J-1). Policy, with costs.
3. **Symbolic anchor now vs by demand** (q3): whether to take a per-language parser dependency
   before a measurement exists. Cost-vs-value.
4. **Storage model** (new, folded into q1): meta vs ring vs table is partly engineering, but the
   table option changes the export/import/restore surface the owner uses daily and the ring option
   changes what a card's `meta` shows him. He should see the three-way comparison, not be handed
   a result.

Engineering (decide in v2, state the reasoning, do not ask):
- Every contract-completeness item: invariants, hash spec, `context_hash` widths, verdict nesting,
  `resolved_against`, caps as numbers, failure vocabulary, depth-token normalization.
- Two-phase capture outside the lock — it is the ratified RFC S2 pattern; there is no owner
  question in it.
- Grammar re-derivation from a shape sweep, key rename away from `anchor`, the C2 → A cascade,
  `project_dir` binding via `describe_root()`, the Т-0 provenance API, §9's slicing.
- Rescoping Р1 to `auto:v1` — engineering declines to pre-decide D5; that is the opposite of
  asking.

**One sentence for the curator.** The idea survived its first contact with the code; the document
did not, and the reason is the one this repository keeps writing down — it asserted its measurements
instead of holding them. Rebuild it as v2 on the spine all three reviewers converged on (content
anchor, captured per observation outside the lock, resolved on read, outside identity, inside a
declared boundary), with the storage model decided on a three-way comparison and the four owner
questions reconstructed from zero.
