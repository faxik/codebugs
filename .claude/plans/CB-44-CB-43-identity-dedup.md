# CB-43 — findings identity function (CB-44 closes as a ratified decision)

Status: REVISED after adversarial-review-x2 (FAIL-REVISE, health 5/10, 13 mandatory fixes —
all encoded below). Branch `fix/cb-44-cb-43-identity-dedup`, worktree `.worktrees/cb-44-cb-43`,
base `4088bdc`.

## Ratified decisions (user, 2026-08-16)

1. Exact identity ships NOW, in core (`findings.py`) — Approach A, which IS CB-44's option (b).
   **CB-44 closes as a decision, with no seam code**: the pre-add resolver seam is designed
   later, alongside the similarity extension, its first real consumer. (User answered the
   explicit "changes the letter of what you asked" question: close as decided.)
2. Iteration sizing: substrate now; similarity extension + offline scrub + backfill are the
   NEXT card. Ledger must record honest numbers: repaired fallback collapses 71/3158 corpus
   rows, all in one family (~2% corpus-wide); value elsewhere is prospective (filer-supplied
   fingerprints + similarity on top).
3. Regression semantics: fingerprint hit on `fixed` REOPENS the card; `wont_fix`/`not_a_bug`
   stay closed, new row linked via `meta.recurrence_of`. NO stale-reopen: `stale` is
   NON-terminal (`types.py:37`) and already queue-visible, so `stale` joins the LIVE set and
   bumps.
4. Fallback fingerprint: ON, repaired per review (meaning of "conservative fallback ON"
   preserved; the original normalization recipe measured 0/115 and was replaced).

## Identity design (final)

### Schema
- Columns on `findings`: `fingerprint TEXT`, `occurrence_count INTEGER NOT NULL DEFAULT 1`,
  `last_seen_at TEXT`. In the `SCHEMA` string for fresh DBs AND added by a new additive
  migration for legacy DBs (columns only).
- Index: `CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_fingerprint_live ON
  findings(fingerprint) WHERE fingerprint IS NOT NULL AND status IN
  ('open','in_progress','stale')` — in a `_POST_MIGRATION_INDEXES` list applied AFTER all
  migrations (sweep.py:65-70 precedent), NEVER in `SCHEMA` (mandatory fix 6: `SCHEMA` runs
  before `_migrate_statuses`' hardcoded rebuild; an index in `SCHEMA` breaks legacy DBs).
- Migration ordering: `_migrate_statuses` (rebuild, legacy only) → provenance columns →
  NEW fingerprint columns → post-migration indexes. A rebuilt legacy DB gets the columns
  after the rebuild; a modern DB ALTERs in place. Test starts from a REAL legacy table and
  runs full `ensure_schema` (not the helper alone).

### Fingerprint
- Supplied via new `fingerprint` arg (validated: must be str, non-empty after strip, ≤ 256
  chars, must NOT start with reserved prefix `auto:` — ValueError otherwise; mandatory fix 9).
- Fallback derivation (only when NO explicit `finding_id` was passed — mandatory fix 4):
  `"auto:v1:" + sha256(canonical_json([category, file, norm(description)])).hexdigest()[:32]`
  - canonical JSON array kills the `|`-delimiter ambiguity;
  - `norm`: strip every string value ≥ 3 chars found in the observation's own `meta`
    (the filer's declared variable tokens — sha, slug, log path; measured to take the
    family from 0/115 to 71/115), lowercase, collapse whitespace, strip hex-runs ≥ 7
    REQUIRING at least one digit (W-4), strip ISO-8601 timestamps. General numbers KEPT
    (rc=124 vs rc=1 must stay distinct).
  - `auto:v1:` versions the algorithm.
- **Explicit `finding_id` is an assertion of identity**: it suppresses fallback derivation
  AND dedup matching entirely (158/173 existing call sites create fixtures with identical
  tuples; CB-23 named-vs-discovered asymmetry). A supplied fingerprint alongside an explicit
  id is stored after a live-collision pre-check (ValueError naming the blocking row).

### Add flow (`add_finding`) — whole body in `db.txn`; trailing `conn.commit()` DELETED
1. Validate inputs (severity resolver as today; fingerprint validation above).
2. LIVE hit (`status IN ('open','in_progress','stale')`, same fingerprint): one UPDATE with
   `occurrence_count = occurrence_count + 1` (SQL-side), `last_seen_at = ?`, `updated_at`,
   and `meta` composed ONCE in Python then assigned ONCE (CB-16; mandatory fix 10):
   occurrences ring keep-first-10 + keep-last-10 (W-7), each entry
   `{at, source, severity, file, tags, reported_at_commit, reported_at_ref,
   description(≤2000 chars)}` (Codex#7 — enough evidence to un-merge). Statement uses
   `RETURNING *`, outcome read via `fetchone`, NEVER rowcount. Post-add hooks DO NOT fire.
   → `was_new: False, dedup_action: "bumped"`.
3. Newest terminal row by fingerprint (`ORDER BY updated_at DESC, id DESC` — deterministic
   tie-break; newest row's status class decides the branch):
   - `fixed` → REOPEN: same single-UPDATE shape (status='open', bump, ring append + 
     `meta.regressed` entry composed into the ONE meta assignment), `RETURNING *`; then
     `db.run_status_change_hooks(conn, fid, 'fixed', 'open')`. → `dedup_action: "reopened"`.
   - `wont_fix` / `not_a_bug` → INSERT new row, same fingerprint (old row is outside the
     partial index), `meta.recurrence_of: <old id>` → `was_new: True,
     dedup_action: "recurrence_of_closed"`. Post-add hooks fire (genuine insert).
4. No match → plain INSERT → `was_new: True, dedup_action: "created"`. Hooks fire.
5. Row conversion (`row_to_dict`) AFTER the txn block (CB-16/CB-24). Response dicts carry
   `was_new` + `dedup_action` (response-only keys).

### Reopen projection (mandatory fix 1 — FATAL-1, corroborated)
New `_reconcile_on_nonterminal` in `milestones/reconcile.py`, registered beside
`_reconcile_on_terminal`: on terminal→open for kind='stream' items, set item status back to
'open', clear done_at-equivalent fields, write an audit row, under the same SAVEPOINT
discipline. `_reconcile_on_terminal`'s early return at reconcile.py:210 stays.
**Acceptance asserts `triage_inbox` RETURNS the reopened card**, not that a hook fired.

### Public-update collision (mandatory fix 2 — FATAL-2, corroborated)
`update_finding`: when the requested status moves a fingerprinted row INTO the live set
(terminal→live), pre-check inside its existing `db.txn` for another live row with the same
fingerprint → domain `ValueError` naming the blocking row. A raw `IntegrityError` must never
escape. `EntityRef.set_status`: documented exemption — no caller performs terminal→live
(claims refuses terminal entities at claims.py:243; stale is live now), docstring states the
invariant.

### Batch (`batch_add_findings`) — mandatory fix 7
Same per-member logic, ONE `db.txn`, one commit. Results built FROM THE PER-MEMBER LOOP
(no bulk re-SELECT — today's SELECT..IN returns PK order and shrinks on duplicate ids),
input-ordered by construction, one result per input (duplicate members: second bumps first).
Member dict keys validated against an allowlist (unknown key → ValueError; mandatory fix 9,
CB-15's shape). Contract restated in docstring: "one logical transaction, one result per
input" — the old four-clause contract is explicitly repealed.

### Surfaces
- MCP `add` / `batch_add`: `fingerprint` param (str | None; batch members via allowlist).
- `query_findings(fingerprint=...)`: filtered through NEW `types.is_text_filter_active`
  (None/'' = no filter, non-str → ValueError) — names the predicate instead of adding a
  CB-29 truthiness site (W-2). Only the new filter uses it; sweeping existing free-text
  filters stays CB-29's card.
- CLI `add --fingerprint`; `_cmd_add` branches on `dedup_action`
  ("Added:"/"Bumped (occurrence N):"/"Reopened:"/"Recurrence of <id>, new:") — mandatory
  fix 12. `add_finding` docstring updated ("returns the created OR matched finding, see
  was_new"), ambient-transaction contract stated like update_finding's.
- CSV: export gains `fingerprint` column; import passes it through (mandatory fix 11 —
  otherwise every round-trip mass-reopens invisibly).
- `get_stats` / `get_summary`: add `total_occurrences` (SUM) alongside row counts
  (recommended 2, cheap).
- Golden schema regenerated via `PYTHONPATH=src uv run python tests/dump_schema.py`.

### Branch-table totality (mandatory fix 5 — judge-only finding)
A test iterates `types.FINDING_STATUSES` and asserts every status maps to exactly one
branch: live-bump {open, in_progress, stale} ∪ reopen {fixed} ∪ recurrence {wont_fix,
not_a_bug} — so a future vocabulary addition fails the test instead of silently falling
through to plain insert.

### Test plan (beyond the above acceptance tests)
- `tests/test_boundary.py` `CountingConn`: count BOTH commit seams and forward
  `__setattr__` (mandatory fix 13) — else the single-commit guarantee is pinned by a test
  that cannot fail.
- Fallback stability: identical adds collapse; meta-token/hex/timestamp variance collapses;
  rc=124 vs rc=1 does NOT; supplied `auto:` prefix refused; empty/non-str refused;
  explicit-id adds NEVER collapse (fixture-shaped regression test).
- Reopen: hook fires exactly once with ('fixed','open'); triage_inbox shows the card;
  claims released stay released.
- Recurrence: wont_fix hit files new linked row; partial index tolerates shared fingerprint.
- Two-connection concurrency: with `db.txn`, writer B blocks at BEGIN IMMEDIATE then BUMPS
  after A commits (the index converts duplicate creation into IntegrityError only for
  non-txn writers — pinned as such).
- Legacy migration through full `ensure_schema`.
- Ambient-transaction reentrancy: add inside caller's txn commits nothing of the caller's.
- Every new test proven to fail against unfixed code (commit first, mutate second).

### At landing
- Close CB-43 (`Resolves`-style note with honest numbers) and CB-44 (ratified decision,
  option (b), pointer to this review).
- File follow-up cards: (i) similarity extension + seam design + offline scrub;
  (ii) backfill — blocked on a merge policy: 14 currently-open corpus rows already share
  one meta-aware fingerprint (judge's own check).
- MCP server restart note for the ledger.

## Adversarial Review x2 Corrections (appendix)

Verdict FAIL-REVISE, 13 mandatory fixes, all encoded above. Corroborated by BOTH models
(settled): reopen invisibility; partial-index IntegrityError via public update; migration
ordering; batch ordering/contract; batch-member typo hole; CLI wording; stale semantics;
resolver savepoint class (mooted by dropping the seam); seam-zero-consumers + A verdict.
Codex-only catches: explicit-ID collapse (would break the suite day one); auto: partition
falsifiable; tuple-hash ambiguity; unversioned algorithm; PreAddOutcome insufficiency (the
REAL reason B fails — the spec's import-order rationale was dismissed). Opus-only catches:
the corpus measurement (spec fallback = 0 collapses anywhere; led to the meta-aware repair);
CSV round-trip mass-reopen; CountingConn harness blindness; dashboard under-reporting;
fabricated rc=999. Judge-only: stale falls through to plain insert once stale-reopen is
dropped (43/115 rows); backfill needs a merge policy; honest value framing (71 rows, one
family, ~2%). Dismissed: "similarity first" sequencing (substrate dependency runs the other
way — both attackers' own recommended shapes are identity-first); Opus's 6509-row figure
(3158 actual); import-order rationale for A.
