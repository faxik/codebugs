# Opus adversary — BT-5 attack (2026-08-20)

Verbatim return of the Opus adversary agent (symbol-anchored evidence, verified against the landed tree mid-flight of T-8/T-9).

## FATAL

**FATAL-1: The response-shape premise is false, and the fourth `dedup_action` value already names signal #3 — killing it by the sketch's own exclusion rule** (`findings.py`, `_add_one`)

Sketch line 26 states the `add` response is row + `was_new` + `dedup_action` (`created|bumped|reopened`). There are **four** values, not three: `_add_one` sets `"created"` (:672), `"bumped"` (:699), `"reopened"` (:713) and `"recurrence_of_closed"` (:718). The sketch's own threshold rule excludes reopen *precisely because* `dedup_action` already names it; applied consistently, that rule deletes signal #3 — `recurrence_of_closed` names the dismissed-twin event in the same field, and the twin's id is already in the returned row (`meta_final["recurrence_of"]`, :733, sole write site) and printed by the CLI (`_cmd_add`, :2387-2388). Signal #3's entire novel content is one scalar, `prior_status`. Either the exclusion rule is wrong (reopen belongs back), or signal #3 is a one-field docstring fix, not a record type.

Methodology note: the premise is sourced from "живых вызовах MCP этой сессии" — an enumeration from observed samples rather than the branch table; the repo's standing lesson verbatim, committed in the premise block of an identity design.

**FATAL-2: Signal #1's fact is NOT "in hand at the moment of the response" — it dies three frames below it, and reviving it breaks a contract this module ratified in writing** (`_bump_row` / `_add_one` / `_finalize_add`)

In hand *inside* `_bump_row` — true (`row["severity"]`, :586). Where the response is assembled — false: `_bump_row` returns the **post**-UPDATE row (`RETURNING *`, :594-597), the pre-value is a local that dies at return; `_add_one` returns a 4-tuple (:658); `_finalize_add` (:808-839) is the sole response constructor and only sees that tuple. So signal #1 changes two return types — a decision this module already took in the opposite direction and wrote down: `_import_would_reopen`'s docstring (:1400-1402) exists precisely so `_add_one`'s return shape "stays the one every other caller and the MCP wrappers are written against". The widening also lands on a positional splat (`batch_add_findings`: `_finalize_add(*outcome, ...)`, :1486) — the parameter-ordering hazard CB-52 closed structurally in `_bump_row`, reintroduced in the response layer. The sketch's feasibility claim is true only for a "в руках кода" spanning three call frames and two contract changes.

**FATAL-3: One third of the closed vocabulary does not satisfy the block's own definition — signal #3 attaches attention to a row nothing happened to, about a different row** (`findings.py:788`)

On the recurrence path `_add_one` returns `was_new: True` — the returned card was created microseconds earlier; nothing was changed, nothing should have been; there is no stored card to diverge from. The payload points at the **twin** row while every other key in the flat dict is a column of the returned row (`_finalize_add` writes response-only keys into the row dict itself, :837-838) — an attention record about row B keyed at the top level of row A. The frame ratified *that dismissed-twin is a signal*, not *this form*; the form does not hold.

## SERIOUS

**SERIOUS-1: The absent-key argument inverts CB-25 rather than applying it.** CB-25's fix made three states distinguishable by type; deleting one of the three states removes the evidence. Absent key collapses "evaluated, none fired" with "no attention channel at all" (explicit-id add, older server, non-add path). The repo already ratified the answer the other way (Claims `_response()` carries all fifteen keys, unconditionally); within `_finalize_add`, `was_new`/`dedup_action` are unconditional — `attention` would be the sole conditional key.

**SERIOUS-2: "Wire golden" cost is misattributed — nothing gates the response shape at all.** No `outputSchema` in the golden (grep → 0); `install_strict_arguments` inspects only request arguments (server.py:107-150); no test asserts an add/batch_add response key set. The golden moves only if the docstring is edited; the response key ships with zero coverage. A ratchet must be authored from scratch.

**SERIOUS-3: Signal #2's precondition is right for the wrong reason, with two live exceptions.** Hash equality is a property of *which writer created the matched row*, not of the hash: `import_findings` stores categories verbatim (CB-51) and lets `_add_one` re-derive fingerprints (:1109-1111, :1121-1123); `restore_findings` is a raw INSERT (both documented at :285-287). A guard coded as "only when fingerprint was caller-supplied" is structurally blind on import/restore-written rows — the cross-check cannot detect the bug class it is named for (CB-113).

**SERIOUS-4: `_gate_category` shrinks signal #2's population to near zero.** The gate (:901-905) runs on every observation-path add and refuses unknown categories without `new_category=True`; signal #2 can only fire between two categories that both already exist. The motivating case (invented divergent name) is refused at the gate, never signalled.

**SERIOUS-5: The proposed import opt-out flag has no reachable caller and cites a parameter that does not exist.** `promote_tags` — zero occurrences in src/ (T-9 proposal in plans only). Import structurally cannot emit attention: `import_findings` calls `_add_one` directly (:1128), uses only `was_new` for counting, returns `ImportReport` counts (:1158) and never calls `_finalize_add`. Correct answer: "by construction, no flag". A flag with zero callers is dead code.

**SERIOUS-6: Vocabulary closed over the wrong axis — no totality guard for the composition.** The analogue of `TestBranchTotality` is `dedup_action` × signal = 4 × 3 = 12 cells; the sketch specifies ~3. Reachable and unspecified: escalation fires on BOTH `_bump_row` call sites (:697 bumped, :708 reopened) — `severity_escalated` co-occurring with `dedup_action: "reopened"` is unaddressed; same for category divergence on reopen. "A check that validates elements cannot validate their composition."

**SERIOUS-7: Signal #3 duplicates an existing annotation on exactly the path where it fires.** The similarity resolver fires on the recurrence-insert path (`finding_id is None and annotate`, :739); its pool includes RECURRENCE_STATUSES (`similarity.py:78`); on a derived fingerprint the twin scores ≈1.0 trigram Jaccard and lands in `meta.similar_to` as `{id, score, status}` — the same id and status signal #3 proposes. Caveats in the sketch's favour: caller-supplied fingerprints need no textual similarity; `MIN_TEXT_LEN = 40`. Mostly redundant on its dominant path.

## WEAKNESS

**W-1:** CLI is not a surface: `_cmd_add` prints four fixed one-liners (:2382-2390), never serializes the response; no `batch-add` CLI verb exists. The audience is silently MCP-only.
**W-2:** `"stored"` becomes ambiguous exactly when T-10 lands (column vs newest ring entry), and T-10 turns the divergence into a recorded readable fact — the sketch's own exclusion rule starts applying to signal #2. "Жёсткой зависимости нет" is true for computability, false for justification.
**W-3:** `prior_status` is a point-in-time read of a different row's mutable column, named as though durable. Honest: "the twin's status at the time of this observation".
**W-4:** Flat namespace: `attention` becomes the third response-only key inside the row dict (:837-838); an eventual `attention` column would be silently overwritten — CB-16 shape relocated to the response layer. A nested envelope or column-impossible name closes it free at design time.
**W-5:** The `PostCommitCorruptionError` path (:828-836) raises instead of returning — the observation landed, may have escalated, and the block is dropped exactly where the filer most needs it.

## NITPICK

**N-1:** Cheapest competing option unpriced: name `recurrence_of_closed` in the MCP add docstring (:2062-2065 omits it) — one line, golden moves, done. Deserves a row (г) if only to be rejected.
**N-2:** `dismissed_twin` mis-names half its population: `not_a_bug` is "no defect existed", not a dismissal.
**N-3:** `batch_add` already returns a list (:2117), diverging from "All MCP tools return dict" — batch channel is not symmetric with add, as the sketch prices it.
**N-4:** Credit: `_escalated_severity` :505-518 citation exact and correct; CB-113 tracelessness claim correct (no cross-check anywhere; `_occurrence_entry` :490-502 has no category).

## Summary Scorecard

| Category | Count |
|---|---|
| FATAL | 3 |
| SERIOUS | 7 |
| WEAKNESS | 5 |
| NITPICK | 4 |

**Bottom line.** Two of five premises factually wrong (dedup_action vocabulary; "both severities in hand"), both errors making the design look cheaper and better-founded than it is. The strongest structural objection: the three-signal set collapses under the sketch's own exclusion rule — `recurrence_of_closed` names signal #3 in the same field used to exclude reopen, and `meta.similar_to` already carries its payload on the same path. What survives is signal #1 alone — one genuinely dissolved fact whose retrieval costs two contract changes the module explicitly declined once, in writing (:1400-1402). A defensible one-signal design; not the three-record block described.
