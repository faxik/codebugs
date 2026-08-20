# BT-5 Defender report (2026-08-20)

Condensed from the Defender agent's verbatim return (Opus; probes run against an in-memory tracker at main `88c1966`; T-3 landed, T-9 not yet at probe time). Positions: Opus (19) — 14 CONCEDE, 4 PARTIAL, 1 accept-as-credit, **0 defends**; Codex (30) — 22 CONCEDE, 5 PARTIAL, 3 accept-as-support, **0 defends**.

## Key probes

- Recurrence path: `action=recurrence_of_closed, was_new=True`; returned row already carries `meta.recurrence_of: CB-1` AND `meta.similar_to: [{"id":"CB-1","score":1.0,"status":"wont_fix"}]` (same with not_a_bug) — on the derived-fingerprint path trigram similarity is 1.0 BY CONSTRUCTION (fingerprint hashes the same normalized text), so signal #3's entire payload (id + prior status) has THREE existing carriers. Residue where similar_to is null: supplied-fp dissimilar text; normalized desc < MIN_TEXT_LEN=40 — only the twin's status missing, one mutable scalar about another row.
- Severity: originally filed severity is NEVER written to the ring (insert path writes no occurrence entry); column holds max(filed, observed); a client cannot tell whether THIS observation escalated → signal #1 is the only genuinely dissolved fact.
- Composition matrix is LIVE: escalation on reopen branch (low→critical, action=reopened) ✔; category divergence on reopen ✔; category divergence stacking with recurrence ✔. Correct denominator: **6 cells, not 12** (created admits nothing; recurrence_of_closed cannot escalate — insert path, _bump_row never runs). Derive from the branch table (TestBranchTotality shape), don't enumerate.
- Wire: `add`'s output schema on the wire is `{additionalProperties: True}` — so even adding outputSchema to the golden collector would NOT gate `attention` malformations; the only real gate is a behavioural MCP result test (or narrowing the return annotation — out of scope).
- Gate population (SERIOUS-4 partial): divergence between two EXISTING categories bumps with no flag (probed); invented name refused without new_category=True. Population narrowed, not emptied.
- Import self-protection nuance (SERIOUS-3 partial): import_findings calls _add_one directly — normalize_category never runs, fingerprint derives from the VERBATIM category, so a differently-spelled later observation derives a different fingerprint and never matches. Live exposure: restore_findings (columns verbatim, no derivational relationship) and explicit-id rows.
- Legacy category values: a BLOB category survives SQLite affinity and normalize_category RAISES on it — mirror _existing_categories' ratified skip-don't-raise policy; compare normalize(observed) != normalize(stored), drop the provenance precondition entirely (it reasons about the wrong row).
- _finalize_add mutates the same dict post_add hooks received (hooks run before — pin the ordering, C-MI-5 nuance).
- _add_one is module-private: 3 in-module callers + 1 test monkeypatch. "MCP wrappers are written against" the response dict, NOT the tuple — AddOutcome/BumpOutcome SATISFIES the :1400-1402 contract (its real content is "don't change the response shape"); the tuple slot would reintroduce the positional-splat hazard CB-52 closed structurally. Reversal of the once-declined new-outcome decision must be cited explicitly and that docstring updated in the same unit.

## Disagreement rulings

1. **Absent key vs always-present:** Codex right on the citation (CB-25 is a filter-input rule — misapplied), Opus right on the contract: **always-present `attention: []`**, precedent `claims._response()` (15 unconditional keys) and _finalize_add's own two unconditional response-only keys.
2. **Signal #3:** collapses (Opus right; Codex under-attacked — priced a wire for cargo already at the destination). Replacement: option (г) — one MCP add docstring line naming `recurrence_of_closed` (the docstring currently omits the fourth action string). Goes to the owner as a LETTER-FIX under preserve-the-meaning: the ratified intent (filer learns a decision was already taken) is met by three structural carriers; one-line notification, not a new question.
3. **Matrix:** real and live; 6 cells, derived not enumerated; totality test in TestBranchTotality shape.
4. **Signal #2:** survives ONLY post-T-10 (hard dependency — Codex right: pre-T-10 the asserted divergence leaves no trace anywhere, violating the "already-recorded facts" frame) AND only on a NEW rationale (Opus right: post-T-10 the diff is derivable from two fields of the same response — the honest justification is top-level NAMING of a fact buried three levels deep in a ring filers don't read; weaker than signal #1's).

## What survives honestly

- **One-signal core: `severity_escalated`** — genuinely dissolved fact; transport = BumpOutcome (returns _bump_row's own `escalated` decision — also discharges C-MI-4's single-predicate requirement) + AddOutcome replacing the four-tuple; signals captured inside the transaction, _finalize_add stays mechanical (kills the post-commit failure class, W-5 ≙ C-MR-6). Admits exactly `bumped`/`reopened`.
- **Two-signal form defensible only if T-10 lands first**, re-argued on the naming rationale; cheaper transport (both categories in _add_one's hands), weaker justification, gate-bounded population, normalize-both-sides + skip-non-string policy.
- Signal #3: does not survive in any form → option (г).
- `attention: []` always present; nested envelope or column-impossible name (16 columns today, `attention` not among them — latent collision); MCP-only audience stated; behavioural wire test authored from scratch; import needs NO flag (by construction — no path to _finalize_add; pin with a test); premise block re-sourced from the branch table (4 dedup_action values; was_new:True on recurrence).
