# Opus adversary — BT-4 attack (2026-08-20)

Verbatim return of the Opus adversary agent (lines re-verified by the agent against the landed tree; a subagent's numbering drifted ~4 lines on `_occurrence_entry` and was re-read directly).

## FATAL

**FATAL-1: The document's central claim — `tags` is "единственная колонка с реальной ценой бездействия" — is false, and it is refuted inside the very function the document cites as its evidence.** (`src/codebugs/findings.py:1856-1887`)

The whole per-column table is built on one criterion: "does a query filter read the COLUMN rather than the ring". `query_findings` has **six** such filters, and five of them read columns the document declares costless or does not mention at all:

```
1856    if category:      conditions.append("category = ?")
1859    if file:          conditions.append("file LIKE ?")
1862    if source:        conditions.append("source = ?")
1865    if tag:           conditions.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)")
1873    if meta_key and meta_value: conditions.append("json_extract(meta, ?) = ?")
1877    elif meta_key:    conditions.append("json_extract(meta, ?) IS NOT NULL")
1880    if commit:        conditions.append("reported_at_commit LIKE ? || '%'")
1885    if ref:           conditions.append("reported_at_ref = ?")
```

A card first filed by `human` and re-observed fifty times by `claude` still answers `query(source="human")` and is invisible to `query(source="claude")` — byte-identical shape to the `tags` argument, on the row the document prices at "цена ≈0". Same for `ref` (see SERIOUS-4). Two of the three recommendations in the table rest on a cost figure the code contradicts. The document never ran the grep; it says so itself ("при x2 — перепроверить file:line фильтра").

**FATAL-2: Option (б) for `tags` is eliminated by a false impossibility claim — the reader-side half is four lines of SQL, and the current filter is ALREADY the thing the document says is unaffordable.** (`findings.py:1866`)

The document: *"(б) заморозка + «читательская половина» — структурно не работает: фильтр — SQL по колонке, читателю пришлось бы сканировать JSON-ring в каждом query."*

The existing tag filter at `:1866` is `EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)` — a JSON scan over a text column, in SQL, per query. The ring version is the same construct one level deeper. Run against this repo's sqlite (3.46.1):

```sql
SELECT id FROM findings f WHERE EXISTS (
  SELECT 1 FROM json_each(f.tags) WHERE value = ?
  UNION ALL
  SELECT 1 FROM json_each(f.meta,'$.occurrences') occ,
                json_each(occ.value,'$.tags') t WHERE t.value = ?)
```
→ returns the row whose tag exists only in the ring. No Python scan, no per-row loop, one statement.

This matters more than an ordinary error: the document exists to hand the owner three costed options for ratification. One option is killed by a claim of structural impossibility that is false, and the surviving recommendation is the irreversible, unbounded one. Note also that `provenance._effective_commit` (`provenance.py:555`, sole call site `:655`) is exactly this ring-reader pattern — already ratified and landed one column over as CB-53. The document calls the ratified pattern impossible while citing CB-53 as an immutable precedent two sections earlier.

**FATAL-3: Population totality fails — the document commits the exact defect it convicts CB-63 of, in the same document.** (`findings.py:490-502` vs `:576-591` vs `:29-48`)

Its closing section says CB-63 "объявил тотальность над «контрактом свежести» и перечислил 2 колонки из 5". BT-4 then enumerates 7 of **9**. Two observation-supplied, bump-discarded fields are named nowhere in the document:

- **`meta`.** `_add_one` accepts it (`:650`), passes it to the ring (`:686`), and the ring stores it *conditionally* (`:500-501`, `if meta: entry["meta"] = meta`). The bump writes `meta = ?` at `:590-591` — but only `occurrences` / `occurrences_dropped` / `regressed`. The observation's own keys never reach the column. `query(meta_key=…)` reads the column (`:1873-1879`). Identical shape to `tags`, same primary-filter argument, unmentioned. The document's premise line even parenthesises it — "`(+meta)`" — which is precisely how a fifth member gets lost.
- **`category`.** Accepted at `:649`, **absent from `_occurrence_entry` entirely** (`:490-502` — no `category` key) and absent from the SET clause (`:576-591`). `query(category=)` at `:1856` and `group_by="category"` at `:1892` both read the column. Unlike `file`/`description`, it is not even recoverable from the ring, so the document's own escape hatch for the frozen group ("fingerprint inputs, un-mergeable from the ring") does not apply — and it *is* a fingerprint input (`:677`, `_derive_fingerprint(category, file, description, meta)`), which the document also fails to say.

Additionally, the SET-clause premise itself is wrong: `findings.py:576-577` is only the *first* fragment. The clause continues at `:583` (`status`), `:588` (`severity`) and `:590` (`meta = ?`). The document enumerates status and severity and drops `meta` — the omission and the missing member are the same mistake.

## SERIOUS

**SERIOUS-4: The `reported_at_ref` recommendation rests on "нет инструмента-потребителя по ref" — there is one, and it reads the column.** (`findings.py:1885-1887`, MCP `query` docstring `:2240`)

```
1885    if ref:
1886        conditions.append("reported_at_ref = ?")
1887        params.append(ref)
```
Exposed on the MCP `query` tool as `ref: Filter by reported_at_ref (exact match)`. So the recommendation "отдельного читателя не строить, пока нет инструмента-потребителя" is built on an absence that does not exist, and by the document's own tags criterion this column has a live cost: a card re-observed at `v2.1.0` is unfindable by `query(ref="v2.1.0")`.

The "same axis, different granularity" framing is also wrong. `reported_at_commit` is **auto-captured** by the tracker (`findings.py:2094`, `:2140`, `db.git_rev_parse("HEAD", silent=True)`); `reported_at_ref` is, per the MCP docstring at `:2082`, "always caller-supplied". Different capture channels, different null-density (CB-103 and CB-63 both carry `reported_at_ref: null`), so the inheritance argument does not transfer mechanically.

**SERIOUS-5: "Inherit CB-53" inherits a contract that is itself incomplete, inside a block the document declares non-revisable.** (`provenance.py:555`, `:655`; `findings.py:1880-1884`)

CB-53's landed reader-half is one function with **one** call site — `_effective_commit` used by `check_findings` at `provenance.py:655`. But `reported_at_commit` has a second reader: `query(commit=)` at `findings.py:1880-1884`, a prefix `LIKE` against the frozen column, untouched by CB-53. So "readers consult the ring" is currently true of one reader out of two.

The document freezes this under "Контекст уже решённого (неизменяемые факты, не пересматриваются здесь)" and then recommends propagating it to a third column. It is propagating a known-partial contract while forbidding the review from examining it — and the partiality is the *same* defect (column-reading query filter) that the document uses to justify acting on `tags`.

**SERIOUS-6: The just-landed CB-60 category gate changed the observation-vs-row contract, and BT-4 — the document that owns that contract — does not mention it.** (`findings.py:280-287`, `:902-905`, `:734-738`)

`add_finding` now does, *before* `db.txn` and therefore before any bump:
```
902        category = normalize_category(category)
903        mint_category = _gate_category(
904            _existing_categories(conn), category, new_category=new_category
905        )
```
Three consequences BT-4 must own and does not:

1. `category` is normalized on the write path but stored rows are not retro-folded (CB-61 blocked). Since category is a fingerprint input (`:677`), an observation of a pre-CB-60 card with the old spelling now derives a **different** fingerprint — it forks identity instead of bumping. For this column the freshness question is not "frozen vs refreshed", it is "silent identity fork", which is a strictly worse failure than the tags one being escalated to a BT.
2. The gate sits *upstream* of the bump, so with a caller-supplied fingerprint a category typo now raises `ValueError` and the occurrence of a known live card is **lost entirely** rather than recorded.
3. CB-60's own countability mechanism is holed on the dedup path, and the code says so at `:736-737`: *"Lands only on the insert path — a dedup bump returned above records no minting."* `query(meta_key="category_minted")` undercounts by exactly the bumped observations.

And `_live_row_by_fingerprint` (`:600-617`) matches on fingerprint + status only — no category cross-check — so a supplied fingerprint can bind two observations with different categories and the divergence leaves no trace anywhere, not even the ring.

**SERIOUS-7: The proposed import opt-out is the FOURTH co-varying "an import is not an observation" switch at one call site — an enumeration, in a repo with an AST ratchet already pinning one of them.** (`findings.py:1121-1126`, `:1141`, `:1147`; `tests/test_dedup.py:912`)

`import_findings` already carries, at one call site:
```
1121        would_reopen, fingerprint = _import_would_reopen(...)   # switch 1: no reopen
1124        if would_reopen: skipped_decided += 1; continue
1141            annotate=False,                                     # switch 2: no resolvers
1147            escalate=False,                                     # switch 3: no re-rating
```
BT-4 proposes switch 4 (`merge_tags=False` or equivalent) without noticing that all four express one predicate. `tests/test_dedup.py:912::TestEscalateOptOutRatchet` exists specifically to pin switch 3 to exactly one call site by AST; a fourth boolean means a second ratchet, or a ratchet that now has to enumerate. This is the repo's documented recurring failure ("a rule expressed as an enumeration is the letter") reproduced inside a document whose closing section cites that exact lesson.

**SERIOUS-8: Union-merge is proposed as unbounded and irreversible, and the document prices neither.** (`findings.py:569-573`, `:1578-1580`)

- **No cap.** The ring is bounded — `overflow = len(ring) - (_OCC_KEEP_FIRST + _OCC_KEEP_LAST)` at `:569-573`, with a counted-drop record. A union-merged `tags` column has no such bound. On corpus-scale cards (the similarity calibration used a 3162-row corpus with a 115-row family) the tag set grows monotonically forever in a `TEXT` column that every `query(tag=)` json_each-scans.
- **No working removal.** The document says removal is "снятие только руками через `update(tags=)`". `update_finding` does a full replace:
```
1578        if tags is not None:
1579            updates.append("tags = ?")
1580            params.append(json.dumps(tags))
```
So a human who removes `release-blocker` loses it back on the filer's next observation, forever, with no precedence record. CB-52's escalation has the same property but is bounded by a four-value lattice with a documented direction; an unbounded free-text set has neither. "Цена — рост/спам тегов" understates this: the real cost is an un-removable label and a column with no upper bound.

## WEAKNESS

**WEAKNESS-9: The contract must be total over the CB-43 branch table, and the document's table has one cell per column where it needs three.** (`findings.py:690-718`)

`_add_one` has three dedup branches — live bump (`:690-699`), reopen bump (`:700-713`), recurrence-new-row (`:714-718`) — and `tests/test_dedup.py::TestBranchTotality` pins the branch table as total. A tags contract has to answer all three: does a *reopen* union-merge tags across the card's previous lifetime (so `fixed-in-1.2` survives the regression)? The document's per-column table cannot express that, and `escalate` already demonstrates the pattern of a per-branch parameter threading through both `_bump_row` calls.

**WEAKNESS-10: `_bump_row`'s documented failure contract has to widen, and the document does not say so.** (`findings.py:545-559`, `:561`, `:791+`)

The docstring promises *"Raises json.JSONDecodeError on malformed stored meta BEFORE any write — the add fails cleanly with nothing landed"*. A tags union needs `json.loads(row["tags"])` pre-write, adding a second source of that exception and moving a class of failure that today surfaces post-commit as `PostCommitCorruptionError` (`:791`) into the pre-write path. That is an observable behaviour change for existing corrupt rows. Also `:545-556` mandates that every fragment be appended to `sets` *with its parameter* — satisfiable, but the document should cite the rule it must obey rather than leave the unit to rediscover it.

**WEAKNESS-11: The unit plan contradicts its own recommendation and omits three landing obligations.** (BT-4 §Процесс п.3)

"один юнит на `tags` (запись + tests + MCP/CLI **без изменения сигнатур**)" — but the same document mandates an import opt-out, which is a signature change to `_bump_row` and `_add_one`. Missing entirely:
- **CHANGELOG.** CB-52, the cited precedent, landed a user-visible entry (`CHANGELOG.md:10-33`, including "Escalation is one-way" and "Importing does not re-rate"). Tags union-merge is at least as user-visible.
- **CLAUDE.md dedup section.** CB-63's stated exit condition was "the contract is ratified **and written into CLAUDE.md's dedup section**"; the ledger records CB-63 closed `wont_fix` partly because that exit was unmet.
- **Wire golden.** `tests/golden/mcp_schema.json` pins the `add` / `batch_add` / `query` / `update` tool descriptions verbatim (all four present with full prose bodies). Any docstring line for `source` / `reported_at_ref` in those tools requires regeneration — and the DIR-2 brief names the wire golden as a DIR-1/DIR-2 serialization point.

**WEAKNESS-12: "Новейшие источники и так в ring" is contaminated by the very path the document flags elsewhere.** (`findings.py:1134`)

`import_findings` writes `source=(row.get("source") or "import").strip()` into the observation, and that reaches the ring entry on a live hit. So the ring's newest `source` is frequently a *peer tracker's* source — foreign evidence the document itself argues must not touch the local row. Recommending "freeze the column, the ring has the fresh values" for `source` hands readers a field whose newest value can be a peer's, on the same page that demands an import opt-out for `tags` for exactly that reason.

**WEAKNESS-13: Bare line-number citations, in a document whose own evidence base already rotted that way.** CB-103 cites `findings.py:380-392` and `:463-477`; both are now wrong (the card was filed in the CB-52 worktree — the real ranges are `:470-502` and `:576-591`). BT-4 repeats the form with `:490-501` and `:576-577`, and `:576-577` is already an incomplete cite of a clause that continues at `:583`, `:588`, `:590`. Cite symbols (`_occurrence_entry`, `_bump_row`'s `sets` builder), not offsets.

**WEAKNESS-14: The document defers verification of its single load-bearing premise to its own reviewer.** "`query(tag=)` читает колонку … (утверждение CB-103; при x2 — перепроверить file:line фильтра)". The DIR-2 brief assigns fact-gathering to the direction holder with provenance. The premise turned out true — but the same one-line grep that would have confirmed it also surfaces the five other filters that falsify FATAL-1 and SERIOUS-4. The abdication is not cosmetic; it is why two of three recommendations are wrong.

## NITPICK

**NITPICK-15:** The premise line writes the ring's fields as "`at, severity, file, description, source, tags, reported_at_commit, reported_at_ref` (+`meta`)". The parenthesis is the whole bug in FATAL-3 in miniature — and the code makes `meta` conditional (`:500-501`, `if meta:`), which is itself a fact worth stating rather than bracketing.

**NITPICK-16:** The tags filter is guarded by plain truthiness — `if tag:` at `:1865` — the CB-25 shape (CB-29 territory for free-text filters). The tags unit lands on this exact line; note the adjacency so it is serialized rather than "fixed while in there".

**NITPICK-17:** Internal identifiers are unexpanded for the owner who has to ratify this — `Т-3`, `Т-5`, `В-3`, `§8.3б`, `§13`, and "нарезка юнитов (3)" (which reads as "three units" but means cascade level 3). The owner's standing rule is that a ratification question reconstructs itself from zero.

**NITPICK-18:** CB-103 is `severity: low`, `status: open`, and closes with "NOT REPRODUCED as a user-visible failure". Elevating it to a BT with a mandatory cross-model pass and an owner ratification round, to land what is in the end one union-merge plus two docstrings, is a real process cost — defensible only if the population is actually decided, which per FATAL-3 it is not.

## Summary Scorecard

| Category | Count |
|---|---|
| FATAL | 3 |
| SERIOUS | 5 |
| WEAKNESS | 6 |
| NITPICK | 4 |

**Verdict: do not ratify.** Three of the document's load-bearing statements are refuted by the file it cites — the "only column with a real cost" claim (five other column-reading filters at `findings.py:1856-1887`), the "structurally impossible" dismissal of option (б) (refuted by a working four-line query), and the `reported_at_ref` "no consumer" premise (`query(ref=)` at `:1885-1887`). And the population is short by two members, `meta` and `category`, in the document whose closing paragraph convicts CB-63 of exactly that. Rebuild the premise block from a single grep of `query_findings`' filter list plus a field-by-field diff of `_occurrence_entry` (`:490-502`) against the SET builder (`:576-591`) against `SCHEMA` (`:29-48`), then re-cost every row of the table.
