# Hand-off — CB-45 similarity extension + pre-add seam, IN FLIGHT (2026-08-16)

Written for a reader with no session context. State as of writing: **implementation complete and
green on the branch; the pre-merge Codex diff review is still running; nothing is merged.**

## Where the work lives

- **Worktree**: `/home/faxik/w/codebugs/.claude/worktrees/cb-45-similarity-seam`, branch
  `worktree-cb-45-similarity-seam`, based on main `1186314`. Commits `7c0a65f..` (seven feature/
  docs commits, then this handoff).
- **Plan + full adversarial-review appendix**: `.claude/plans/CB-45-similarity-seam.md` — exists
  BOTH in the main checkout (uncommitted) and committed on the branch (identical). The plan is
  the spec; its appendix records the 6.5/10 verdict, 14 mandatory fixes (all encoded), and the
  cross-model scorecard.
- **Claim**: CB-45 is claimed (CLM-6, holder `session-2026-08-16-cb45-similarity`), projected
  `in_progress`. Release it at close.
- **Ledger section draft** (pre-written, needs only the merge SHA):
  `/tmp/claude-1000/-home-faxik-w-codebugs/6bf14b6d-3c6c-4af1-a404-dcbe55870678/scratchpad/ledger-section-draft.md`
  — scratchpad is session-scoped; if it is gone, the same facts are all in this handoff and the
  plan appendix.

## What is DONE (verified: 1076 tests pass, `ruff check` clean at pinned 0.15.7)

1. **Pre-add resolver seam** in `db.py` (`register_pre_add_resolver`, `run_pre_add_resolvers`,
   `resolver_reserved_meta_keys`): annotate-only (redirect inexpressible by type), never-commit
   ENFORCED — entry guard raises outside an open transaction (bare SAVEPOINT/RELEASE *is* a
   commit), post-resolver check catches a resolver that closed the caller's transaction (raises
   OUTSIDE the failure swallow), cleanup guarded so it never masks the real error; outcome
   validated inside the savepoint (dict, str keys, `json.dumps(allow_nan=False)`); failures
   stamped queryably into `meta.resolver_errors`.
2. **findings.py**: public `similarity_candidates` accessor (similarity issues ZERO SQL — the
   module-ownership fix), `LIVE_STATUSES` + `normalized_identity_text` public, reserved-key union
   dynamic via `db.resolver_reserved_meta_keys()` (loads modules first), `similar_to` reserved on
   ADD only (update writable — CB-26 repair path), `add_finding(annotate=)` with CSV import
   passing `annotate=False` and stripping the dynamic reserved union.
3. **similarity.py**: trigram-Jaccard detector over identity normalization + ANSI strip;
   `DEFAULT_THRESHOLD=0.7`, `MIN_TEXT_LEN=40` in the scoring layer; file-time resolver
   `similarity.annotate` stamps `meta.similar_to=[{id,score,status}]`, pool = live ∪
   {wont_fix,not_a_bug} same-category newest-500, content-keyed trigram memo; `group_report` with
   per-family **diameter** `min_pair_score` (over ALL pairs, not edges), edge lists, excerpts,
   live-default population + `status="all"` sentinel; MCP `similarity_check`/`similarity_report`;
   CLI `similarity-check`/`similarity-report` (full bodies, JSONDecodeError arm ordering); mode
   slug `similarity`; golden schema regenerated.
4. **Calibration is a committed artifact**: `tests/manual/verify_similarity_corpus.py` reproduces
   11 families / 102 collapse / gate 111-of-115 EXACTLY against
   `/home/faxik/w/autosorter/.codebugs/findings.db`; the 43-row family shows diameter 0.392.
5. **CLAUDE.md** on the branch: CB-44 debt entry replaced (seam now exists), new similarity
   bullet, requirements-parity decision recorded (NO identity function for reqs — the card
   delegated the decision; revisit trigger: an automated reqs filer).

## Decisions already ratified/notified — do NOT re-litigate

- **0.95 → 0.7 threshold**: the card's 0.95 was measured false (77 rows collapse, family never
  unifies — it is ~10 distinct defects); 0.7 shipped under the letter-fix protocol, user notified
  in-session. The "collapse the whole 115-row family" target is dropped as a false merge.
- **Requirements get NO identity function** — authorized outcome of the card's own scope item 5.
- Full review history (who caught what): plan appendix. Judge dismissed the "scope reversal"
  attack on both decisions.

## What REMAINS (in order)

1. **Converge the Codex diff review.** Started in background FROM THE WORKTREE via
   `codex-code-review/scripts/start.sh ... main..HEAD`. State dir:
   `/home/faxik/.claude/skills/codex-code-review/state/` — look for `main..HEAD.review.txt`
   (verdict at the bottom: APPROVED / REQUEST_CHANGES). If REQUEST_CHANGES: fix findings yourself
   in the worktree, then `resume.sh --notes "<what was fixed>" main..HEAD`; loop to APPROVED.
   `start.sh` exit 2 = thread exists → use resume.
2. **Merge to main.** Then re-run the full suite on main.
3. **On main, docs commit**: (a) prepend a ledger table row + insert the drafted 2026-08-16 CB-45
   section (before the CB-43/CB-44 section) into `.claude/plans/BUGFIX-LOOP-LEDGER.md`, filling
   the merge SHA; (b) add a "superseded" pointer at the top of
   `.claude/plans/2026-08-16-identity-dedup-HANDOFF.md`; (c) mark THIS handoff done or delete it.
4. **Cards**: close CB-45 (`fixed`, note: merge SHA, measured numbers — 102 vs 71 collapse, gate
   111/115 at 0.7, 0.95 rejected, diameter 0.392 — the reqs-parity decision, review verdict
   6.5/10 with 14 fixes encoded); annotate CB-46 (still blocked by design; its dry run now exists:
   MCP `similarity_report` with edges/min_pair_score/excerpts; D6's proposed-NOT-ratified merge
   policy in the plan — survivor=oldest, losers terminal with meta.merged_into; open: loser
   status vocabulary, dangling refs incl. blocker links, ring capacity). Release claim CLM-6
   (`claims_release`, holder `session-2026-08-16-cb45-similarity`).
5. **Operational**: reinstall the pipx server from the repo — a long-running MCP server serves
   OLD code until restarted; symptom of old code: no `similarity_*` tools in `--mode all`, adds
   never carrying `similar_to`.
6. **Named in the prior handoff, still undone (not part of CB-45)**: update autosorter's
   auto-filers (post_merge_gate.sh, per CB-1984's tail_sig prescription) to pass `fingerprint=` —
   that is where the measured dedup value is.

## Traps this iteration hit (do not re-derive)

- The worktree guard blocks compound Bash with heredocs/redirects and writes to the shared
  checkout — use Write/Edit on worktree paths, plain commands otherwise.
- Fresh `uv pip install ruff` grabs 0.16.x which flags the whole repo; the repo is clean under
  **ruff 0.15.7**. `ruff format` is NOT a gate (baseline non-conformant); `ruff check` is.
- The trigram memo MUST be content-keyed: an `(id, created_at)` key collides across databases
  within one whole-second timestamp (test suites and re-created trackers both hit it).
- `min_pair_score` must be computed over ALL member pairs — recorded edges are ≥ threshold by
  construction, so an edge-minimum silently hides chaining (first implementation had exactly
  this; the corpus run exposed it: 0.701 where 0.392 was true).
- Chain-test fixtures: repeated text dedups its own trigrams (`"x " * 3` adds no weight); build
  diverse-word fixtures and pin an explicit threshold (0.65) with measured pair scores.
- `pytest -o pythonpath=<tree>/src` is still needed to prove a test against another tree
  (pyproject's pythonpath outranks $PYTHONPATH).
