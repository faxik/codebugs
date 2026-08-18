# CB-51 + CB-77 — `import` and `restore` are two verbs, and neither existed

Branch `feature/cb-51-cb-77-import-restore-verbs`, based on main `8915d10`.
Bugfix-loop iteration 5. CB-51 is the queue's only `high`.

## Why one tree

**Predicate 1 — same root cause.** Every defect below comes from one fact: **all import
semantics live inline in a presentation-layer CLI handler** (`_cmd_import_csv`,
`findings.py:1865-2010`), which calls `add_finding` per row, each committing. There is no
domain function that owns "what does importing mean". One causal change — `import_findings`
owning the loop inside one transaction — is what makes every fix below expressible.

CB-77 is in the same tree because its ratified fix (one outer transaction) is **only
possible** once the loop moves out of the handler; the per-row commits are the defect.
That is predicate 4 (atomic landing) as well: a domain function that still committed per row
would be a worse intermediate state than either endpoint.

## Ratified decisions (user, 2026-08-18)

1. **Two verbs.** `import` = observation (fold findings in, ids are mine). NEW `restore` =
   honour `id`/`status`/`occurrence_count`/`tags`/`created_at` verbatim, bypass dedup, and
   refuse rather than merge when ids collide.
2. **A colliding row must LAND with its origin recorded, never be silently dropped.**
3. **One transaction, all-or-nothing** — which also settles CB-77.

**Letter vs intent on (2), stated because the letter has no home under (1).** The question
was posed about foreign-tracker imports. Under two verbs, `import` does not use ids for
identity at all, so there is no collision to remap there — the intent ("the row lands") is
met by removing the id guard entirely. And `restore` was ratified in (1) as *refusing* on
collision, so it must not remap. The reconciliation: **`restore` refuses and names the
colliding ids, and its error tells the operator to use `import`, which lands every row with
a fresh local id and records the original in `meta.imported_id`.** That is "remap and record
the original" — reached through the verb that owns renumbering, not bolted onto the one
whose whole purpose is id fidelity.

## Reproduced (scratchpad/repro_cb51.py, against `8915d10`)

All four run the real CLI. The reproducer carries a guard that raises if the CLI exits on
an argparse error — **the first draft passed `--file` where the parser takes a positional,
so three "reproductions" were drawn from a command that never ran.** That is the vacuous
evidence this repo keeps catching, caught here before it reached the plan.

| # | Defect | Evidence |
|---|---|---|
| 1 | **An import reopens a decided card.** A foreign row (`CB-9001`, absent locally) whose text matches a local **`fixed`** card merges by fingerprint. | `Imported 0 findings. (1 merged…)`; local `CB-1` went `fixed` → **`open`** |
| 2 | **A foreign tracker's export is silently dropped.** Peer ids `CB-1..CB-3` all exist locally as unrelated findings. | `Imported 0 findings. (3 already present, skipped)`; zero peer rows landed |
| 3 | **Export→import is not a round-trip.** | 3 findings out; 2 back, both `open`, `occurrence_count` reset to 1, tags lost |
| 4 | **NOT ON THE CARD — a restore into an EMPTY tracker silently drops rows.** | see below |

### Defect 4, in full, because it is the most dangerous and nobody had filed it

`export-csv` orders by **severity rank**, not by id, so a normal export is not in ascending
id order — measured: `CB-1(high), CB-3(medium), CB-2(low)`. Importing that into an **empty**
tracker:

- row `CB-1` lands, allocator mints local **`CB-1`**
- row `CB-3` lands, allocator mints local **`CB-2`**
- row `CB-2` → the guard's `SELECT 1 FROM findings WHERE id = 'CB-2'` **hits the row just
  minted for `CB-3`** → skipped, reported as `1 already present`

**The allocator hands out the ids that later rows in the same file still name.** Data loss
on the exact disaster-recovery path, reported as a success with a benign-sounding count. It
fires whenever export order is not ascending, i.e. normally. A first control run missed it
because uniform severities happened to produce ascending order — recorded so the next reader
does not "disprove" it the same way.

## Two facts verified before implementation, one of which changes the design

**1. The one-transaction decision is already implementable — VERIFIED BY RUNNING.**
`add_finding` (`findings.py:664`) already wraps its whole body in `db.txn` (`:710`), and its
docstring (`:701-703`) states the rule: *"Do not restore a `conn.commit()` here: `db.txn`
yields `False` under an ambient transaction, and committing then commits the caller's
work."* Measured: two `add_finding` calls inside an outer `db.txn`, then an exception →
**0 rows survive**; an ordinary call with no ambient transaction still commits. So CB-77's
fix requires **no change to `add_finding` and no change to its ~10 other callers**. This was
the plan's largest correctness risk and it is closed.

**2. `batch_add_findings` already exists and does most of this job — the plan was going to
reinvent it.** `findings.py:751` takes a list of member dicts, validates EVERY member before
the transaction opens, then runs one `db.txn` over `_add_one` per member. It already accepts
`id`, `tags`, `meta`, `fingerprint` per member, and its own docstring forbids the mistake
this plan was about to make: *"MUST NOT delegate to add_finding() in a loop (that produces N
commits)."*

**Revised design:** `import_findings` is a thin POLICY layer over `batch_add_findings`, not a
second loop. CB-51's phrasing ("the honest fix is `findings.import_findings(conn, rows)`
owning id preservation, status restoration, identity bypass, and the one-transaction
question") is therefore half-built already; what is missing is only the *policy*, and the
restore-only columns. This is the card's own census being smaller than reality, in the
useful direction for once.

**What `batch_add_findings` does NOT cover, and must be resolved:**

- **`status`, `occurrence_count`, `created_at`** are not member keys (`_BATCH_MEMBER_KEYS`),
  so `restore` needs either an extension there or a post-insert UPDATE inside the same
  transaction. An UPDATE would fire `register_status_change_hook`, which drives claims
  auto-release and milestone reconciliation — **almost certainly wrong for a card being
  restored rather than resolved.** This is now the plan's top open risk and the adversary's
  first target.
- **"Skip on fingerprint hit" cannot be decided by the caller.** The `auto:v1` derivation is
  deliberately server-side and versioned (CB-43 rule 4), so a pre-filter in `import_findings`
  would have to re-derive it and would drift. The policy has to be expressed INSIDE the
  identity machinery — an `on_match` behaviour on `_add_one` — rather than as a filter above
  it. Open question flagged for review: is that acceptable coupling, or does it widen the
  identity contract too far for one card?

## Independent edits

| # | Change shape | Locations | Cards | Pre-finish verification |
|---|---|---|---|---|
| 1 | New domain function `import_findings(conn, rows, *, mode)` owning the loop, inside ONE `db.txn`; per-row `add_finding` commits removed. | `findings.py` (new public fn) | CB-51 core, CB-77 | `pytest tests/test_findings.py -q` |
| 2 | `_cmd_import_csv` reduced to CSV parsing + printing; new `_cmd_restore_csv` + `restore-csv` parser. | `findings.py` handler + `register_cli` | CB-51 (4) | `pytest tests/ -q` + manual CLI round-trip |
| 3 | Tests: round-trip fidelity, the four reproduced defects, transaction rollback. | `tests/test_findings.py` | both | full suite |

Three rows, ceiling is four. Rows 2–3 depend on row 1, so this lands as **one unit** — the
independence claim is not made this time (iteration 4's was falsified by review and the
lesson is not worth re-learning).

## REVIEW ROUND 1 — FAIL. The design above is dead; this tree is now SPLIT.

The Opus adversary returned **FAIL with three FATALs, every one reproduced**, and it killed the
central decision ("`import_findings` is a thin policy layer over `batch_add_findings`") for the
`restore` half. Verified independently before accepting:

- **`restore` cannot use the add path.** `_validate_fingerprint` (`:276`) refuses the `auto:`
  prefix every real export carries; `_validate_meta_keys` (`:244`) refuses `recurrence_of` and
  `occurrences` — the exact evidence defect 3 exists to preserve; and `status` /
  `occurrence_count` / `created_at` are not insertable (`:586-606` hardcodes `'open'` and `now`).
- **Insert-as-`open`-then-UPDATE refuses a LEGITIMATE export.** A `wont_fix` card and its
  `recurrence_of` twin share a fingerprint by design — measured, two rows, one `auto:v1:257cf9a3`
  — so whichever restores second collides on `ux_findings_fingerprint_live`. **My own answer to
  defect 3 was the input that broke my own design.**
- **Post-add hooks fire on the INSERT** (`:609`, unconditional), so an N-row restore fabricates N
  `stream/triage` items and 2N audit rows asserting a history that never happened. Claims are
  NOT affected (measured: 0 rows) — that risk came off the list, which is worth recording because
  the plan had asserted it.
- **"Import is idempotent" was FALSE**, and it was my own invention rather than anything ratified.
  Measured: an explicit-id row stores `fingerprint = NULL`, and re-observing identical text creates
  a second row — so a fingerprint-only skip structurally cannot see it, and the bare-id guard I
  proposed deleting is the only thing covering that population.

**DECISION: `restore` leaves this tree and becomes CB-97**, which carries every constraint above
plus three fidelity gaps the review surfaced (the export omits `reported_at_commit` /
`reported_at_ref`; it is capped at `limit=100000`; milestone projections are not exported at all).
The user's ratified semantics are unchanged — this is sequencing, not substitution. This tree ships
the half that is correct and shippable today: the three data-loss defects plus CB-77.

## Semantics to implement — NARROWED after review

The original plan changed more than the defects required. It is cut back to one change per
reproduced defect, because the extra change ("a fingerprint hit always skips") was unrequested,
was not ratified, and was shown false.

| Defect | Change | Not changed |
|---|---|---|
| 1 — import reopens a decided card | A fingerprint hit on a `_REOPEN_STATUSES` row is **skipped**, never reopened. Uses `_derive_fingerprint` + `_match_fingerprint` directly, inside the transaction — no new `_add_one` outcome, no widening of the identity contract (review finding 10). | A hit on a LIVE row still **bumps**. That is today's behaviour, no card reports it, and an import of a finding you already have genuinely is another sighting. A `wont_fix` hit still files a recurrence row. |
| 2 — foreign export silently dropped | The bare-id guard becomes an **id + content** guard: skip only when the id exists locally AND it is the same finding (category, file, description). A foreign row whose id merely collides now LANDS with a fresh local id and `meta.imported_id` recording where it came from. | — |
| 4 — allocator self-collision | Closed by the same guard: the CSV's `CB-2` no longer matches a freshly minted local `CB-2` holding different content. | The export's ordering and column set are untouched. |
| CB-77 — partial import on read failure | One `db.txn` around the whole loop, in `findings.import_findings`. | — |

**Why the id guard survives, against the ratified answer's letter.** The user chose "remap on
collision"; review proved that deleting the id check entirely makes every `fingerprint IS NULL` row
(pre-CB-43 rows, every explicit-id row) duplicate on re-import. The intent — *a foreign row must
land, not vanish* — is fully served by the id+content form, which drops only genuine re-imports of
the same row. Second letter-vs-intent adjustment on this card; both are recorded on CB-51 itself.

**`annotate=False` is preserved** (review finding 4): `batch_add_findings` has no such parameter, so
routing through it would have silently re-enabled the similarity resolver for every imported row,
inside the held write lock. `import_findings` therefore calls `_add_one` directly, which is also
what gives per-row error partitioning that `batch_add_findings`' validate-everything-up-front
contract forbids (review finding 7).

**Rollback reporting** (review finding 8): under one transaction a failure means NOTHING landed, so
the handler must not print "Imported N" on that path, and the error must say so. Printing a count
after a rollback is the CB-15/CB-16 lie in a new place.

## Superseded — original semantics section, kept for the record

**`import` (observe)** — ids in the CSV are NOT identity.
- The bare-id skip guard is **deleted**. It was a proxy for "do not resurrect my own export",
  and it was both too strong (dropped foreign rows, defect 2) and too weak (defect 1 walked
  straight past it with an id that did not collide).
- Replaced by the rule it stood in for: **a fingerprint hit on an existing row is SKIPPED,
  never bumped, reopened, or filed as a recurrence.** An import is not an observation
  (`annotate=False` already says so; this finishes the thought). Consequence: **import is
  idempotent**, which is the property the id guard was groping for.
- A row carrying an `id` records it as `meta.imported_id`, so provenance survives renumbering.

**`restore`** — ids ARE identity.
- Honours `id`, `status`, `occurrence_count`, `tags`, `created_at`, `fingerprint`.
- An explicit id already bypasses dedup (CB-43 rule 3), which is what keeps a `wont_fix`
  decision and its `recurrence_of` twin from collapsing (CB-51 defect 3).
- **Refuses the whole file** if any id already exists locally, naming them, and points at
  `import`. All-or-nothing is free here — we are already in one transaction.
- Defect 4 cannot occur, because no id is ever allocated.

**Both** — one `db.txn` around the whole loop (CB-77). A malformed row is still counted and
skipped rather than raised, so the all-or-nothing cost falls only on genuine failures.

## Risks / out of scope

- **`restore` is CLI-only.** No MCP tool: restoring a tracker is an operator action, and the
  repo's own rule is that a new tool is a client-visible contract. Stated, not forgotten.
- **The write lock is held for the whole import.** Ratified cost of decision 3.
- **`created_at` becomes settable on the restore path only.** It is INSERT-only elsewhere;
  the plan does not make it mutable at update, so CB-21's parity debt is not widened.
- **Not attempted:** changing `export-csv`'s column set or its ordering. Defect 4 is fixed by
  `restore` preserving ids, not by sorting the export — sorting would only hide it for
  `import`.
- **Open question for review:** whether `import` skipping a fingerprint hit (rather than
  bumping `occurrence_count`) loses information a user wants. It makes import idempotent,
  but it does mean "I imported the same finding from three agents" no longer raises the
  count. Flagged deliberately for the adversary.
