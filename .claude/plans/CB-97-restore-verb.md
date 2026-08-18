# CB-97 — `restore`: a backup you can actually restore

Branch `feature/cb-97-restore-verb`, based on main `8d3d9ce`. Bugfix-loop iteration 6.

Split out of CB-51 in iteration 5 after adversarial review returned FAIL on the design that
tried to build restore over the add path. **The semantics were ratified by the user on
2026-08-18** (two verbs; `restore` honours id/status/occurrence_count/tags/created_at
verbatim, bypasses dedup, refuses on id collision). This card is the implementation, and
its constraints were all established and reproduced during that review — so there is no new
design question, only the build.

## Why the add path cannot be used (established, not re-derived)

Three independent refusals, each measured in iteration 5:

1. `_validate_fingerprint` refuses the `auto:` prefix — which **every** server-derived
   fingerprint carries. Stripping it lands `fingerprint = NULL`, i.e. a restored tracker
   with no identity function, whose every card duplicates on its next observation.
2. `_validate_meta_keys` refuses `recurrence_of` / `occurrences` — the exact evidence a
   restore exists to preserve.
3. `status`, `occurrence_count`, `created_at` are not insertable (`INSERT` hardcodes
   `'open'` and `now`), and `update_finding` cannot reach two of them and re-stamps
   `updated_at` besides.

And the two-step (insert `open`, then UPDATE) does not merely lose data — **it refuses a
legitimate export**: a `wont_fix` card and its `recurrence_of` twin share a fingerprint by
design, so whichever lands second collides on `ux_findings_fingerprint_live`. A raw INSERT
of the FINAL statuses satisfies the partial index by construction.

## Design

`findings.restore_findings(conn, rows)` — a **raw multi-row INSERT inside one `db.txn`**,
writing all sixteen columns verbatim, bypassing the identity machinery, the pre-add
resolvers and the post-add hooks.

**All-or-nothing, and it refuses rather than merges.** Inside the same transaction (so
`BEGIN IMMEDIATE` holds the write lock and check-then-act has no window):

- every row must carry an `id`;
- no id may already exist locally — collisions are listed and the whole file refused;
- no id may repeat within the file.

A refusal raises `ValueError` naming the ids. Today a duplicate id leaks a raw
`sqlite3.IntegrityError`, which is outside this module's contract and unclassifiable by
`db.is_contention` (code 19, not 5/6).

**Hooks are bypassed deliberately, and that has a stated cost.** Post-add hooks would
fabricate one `stream/triage` item and two `milestone_audit` rows per restored card,
asserting a triage-and-reconcile history that never happened. Not firing them means a
restored tracker has **no milestone projections at all** — which is honest (they were never
exported) rather than fabricated, but it must be said out loud, not discovered. Recorded in
the CHANGELOG and in the command's own output.

**Vocabulary is resolved, not trusted.** `severity` and `status` go through
`resolve_severity` / `resolve_finding_status` so a legacy spelling normalises instead of
leaking an `IntegrityError` from the CHECK constraint. That is spelling, not meaning — the
same rule CB-19 established.

## Two fidelity gaps fixed here, because a backup that silently loses data is the defect

1. **The export omits `reported_at_commit` / `reported_at_ref`** — the provenance columns
   every staleness operation runs on. A restore that drops them leaves every card
   unanswerable by `staleness_check`. The export gains both columns; this is additive, and
   `import`/`restore` ignore unknown columns, so old CSVs still read.
2. **`_cmd_export_csv` exports `query_findings(limit=100000)`** — a hard cap that silently
   truncates on the disaster-recovery path. It now pages until exhausted, so the cap is
   gone rather than merely raised.

## Independent edits

| # | Change | Cards | Verification |
|---|---|---|---|
| 1 | `restore_findings` + `RestoreReport`, raw INSERT in one `db.txn`, refusing on collision | CB-97 | `pytest tests/test_findings.py -q` |
| 2 | `restore-csv` CLI verb + handler (parse, call, print, state the milestone gap) | CB-97 | CLI round-trip test |
| 3 | Export: add the two provenance columns; page instead of capping | CB-97 | round-trip + truncation test |

Three rows, ceiling four. They land together: a restore that cannot round-trip its own
export is not testable, so edit 3 is what makes edits 1–2 verifiable.

## Out of scope / stated

- **Milestone projections are not restored** (never exported). Said in the CHANGELOG and by
  the command.
- **`import` is untouched.** Its contract landed in iteration 5.
- Restoring into a NON-empty tracker is permitted when no id collides — that is what the
  ratified wording says ("refuses unless the target is empty or ids don't collide").
- `_next_id` is max-numeric + 1 (read, not assumed), so a restore of `CB-1..CB-500` leaves
  the next add at `CB-501`. Pinned by a test, because restore silently depends on it.
