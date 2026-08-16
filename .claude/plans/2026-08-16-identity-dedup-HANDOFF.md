# Hand-off — findings identity function (CB-43 / CB-44), landed 2026-08-16

Written for a reader with no session context. Everything here is verifiable from main.

## What exists now, in one paragraph

The codebugs tracker deduplicates findings at write time. `add` / `batch_add` are upserts
keyed on a `fingerprint`: the same defect observed N times becomes ONE row with
`occurrence_count = N` (plus a bounded per-observation evidence ring in
`meta.occurrences`), a recurrence of a `fixed` card **reopens that card as a regression**
(and its `stream/triage` item reopens with it, ownership and capacity released), and a
recurrence of a `wont_fix`/`not_a_bug` card files a new row linked via
`meta.recurrence_of`. At most one live card per fingerprint is enforced by a partial
unique index, not by convention. Landed as merge `1e06a80`; 1004 tests pass.

## Why it looks the way it does (the three decisions a future reader will question)

1. **CB-44 was closed WITHOUT building the pre-add resolver seam.** The card offered
   "seam + dedup-as-extension" vs "identity in core"; cross-model review was unanimous
   for core and the user ratified it. Do not add a speculative seam — it is designed
   together with its first real consumer, the similarity extension (CB-45).
2. **The fallback fingerprint strips only VOLATILE-keyed meta values** (keys containing
   sha/commit/slug/branch/log/run/time/duration). Stripping every meta value merged
   rule_code E501/F401 defects; stripping none collapsed 0 of the 115-row family the
   card was filed about. Ambiguity errs toward a false split (today's status quo), never
   a false merge (invisible loss).
3. **Honest value numbers, do not oversell:** the repaired fallback collapses 71/3158
   corpus rows, all in one family (~2% corpus-wide; 62% of that family; 44/115 remain
   distinct). The broad win is prospective: filers supplying real fingerprints (the
   primary path) and the similarity layer on top.

## What the next session should do

- **CB-45 (medium, open)** — similarity extension: annotate-only file-time resolver
  (`meta.similar_to`, never auto-merge), offline scrub producing a grouping report,
  lexical detector first with an embedding-ready interface, **and the pre-add seam
  designed against it**. Also decides requirements-entity identity parity.
- **CB-46 (low, open, blocker-linked to CB-45)** — retroactive backfill. It is a MERGE,
  not an update pass: 14 currently-open corpus rows already share one fingerprint, and
  the unique index will refuse them. Needs a survivor/reference/ring policy first; the
  CB-45 scrub report is the dry run.
- **Update the auto-filers** (autosorter's post_merge_gate.sh etc., per CB-1984's
  tail_sig prescription) to pass `fingerprint=` — that is where the measured value is.

## Operational notes

- **The MCP server binary is the pipx install** (`codebugs-mcp`); it was reinstalled from
  this repo after the merge, but any long-running server process serves the OLD code
  until restarted. Symptom of the old server: `add` responses without
  `was_new`/`dedup_action`.
- Filer guidance now in the MCP `add` docstring: fingerprint = invariant failure part
  only; `auto:` prefix is reserved and refused; explicit `finding_id` bypasses dedup.
- `meta` keys `occurrences`, `occurrences_dropped`, `regressed`, `recurrence_of` are
  reserved and refused on add/update.

## Traps rediscovered this iteration (cost real time; do not re-derive)

- `[tool.pytest.ini_options] pythonpath = ["src"]` outranks `$PYTHONPATH`: proving a
  test fails against another tree needs `pytest -o pythonpath=<other-tree>/src`, or the
  proof silently tests the fixed code.
- A test helper that mints N "distinct" entities from identical
  (category, file, description) tuples now creates ONE entity — vary the description
  (see `tests/test_claims.py::_finding`) or pass explicit ids.
- `tests/test_boundary.py::CountingConn` counts BOTH commit seams and forwards
  `__setattr__`; do not simplify it back.

## Where the full history lives

- Design + adversarial-review-x2 verdict appendix: `.claude/plans/CB-44-CB-43-identity-dedup.md`
- Iteration narrative: `.claude/plans/BUGFIX-LOOP-LEDGER.md` (2026-08-16 section)
- Rules distilled into `CLAUDE.md` ("Findings have an identity function", the CB-44
  debt entry, and the post-add-hook note)
- Cards: CB-43/CB-44 closing notes carry the same numbers and the merge SHA.
