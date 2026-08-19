# CB-52 — a dedup bump must escalate severity

**Scope ratified by the user twice, 2026-08-19.** First the field-freshness contract (the CB-63
decision: option (a) for `severity`, option (b) for `reported_at_commit` → CB-63 split, CB-53 is a
separate tree). Then, after adversarial review surfaced facts I had not put in the first question,
the scope was narrowed: **this tree ships the severity column only.** Milestone re-routing goes back
to CB-35, which already owns that question.

## Reproducer

`tests/manual/repro_cb52_severity_freeze.py`, in this worktree, against unfixed code:

```
1st observation: CB-1 severity='low'
2nd observation: CB-1 severity='low' (was_new=False, action=bumped)

STORED severity        : 'low'
occurrence_count       : 2
ring severities        : ['critical']
milestone projection   : stream/triage
query(severity=critical) hits: []
```

The live, user-visible half is the last line: the tracker's primary read path cannot find a card
that was just observed as critical.

## Root cause

`findings._bump_row` (`src/codebugs/findings.py:395-443`) issues exactly one UPDATE whose SET clause
is built as:

```python
sets = "occurrence_count = occurrence_count + 1, last_seen_at = ?, updated_at = ?"
...
sets += ", status = 'open'"          # reopen only, a LITERAL — consumes no parameter
params.append(json.dumps(meta))
params.append(row["id"])
return conn.execute(
    f"UPDATE findings SET {sets}, meta = ? WHERE id = ? RETURNING *", params
).fetchone()
```

`severity` is absent, so the newly observed assessment survives only in the `meta.occurrences` ring
(`_occurrence_entry:360-392`, which does record per-occurrence `severity`).

## Ratified contract

**Severity is monotonic under observation.** A bump writes the MORE SEVERE of (stored, observed);
re-observing a `critical` card at `low` leaves it `critical`.

**The algorithm is stated explicitly, because "max" invites the backwards implementation.**
`types.SEVERITIES = ("critical", "high", "medium", "low")` — **index 0 is MOST severe**, so the
escalated value is

```python
SEVERITIES[min(severity_rank(stored), severity_rank(observed))]
```

`max()` over the strings, or over the ranks, is wrong in both spellings. Both reviewers flagged the
original wording as admitting a backwards implementation.

## Out of scope — named, not silently dropped

- **Milestone re-routing → CB-35.** That card already owns "should a severity change re-route a
  finding between `stream/security` and `stream/triage`", enumerates three options, and recommends
  **against** the hook-based option (a) that an earlier draft of this plan proposed. Measured and
  re-verified in this session: `milestone_status("stream/security")` → `total_items: 0`, against 94
  in `stream/triage` — the `critical AND category.startswith("security:")` predicate has never fired
  in this tracker's life, so the routing symptom is entirely latent. CB-35 gets the new evidence
  appended, including that this tree makes option (a) cheap to wire if it is ever wanted.
- **`update_finding`** — the only other writer of `severity` on an existing row
  (`findings.py:1259-1404`). It permits both escalation and downgrade, and re-routing on manual
  retriage is the same CB-35 question. Not touched here.
- **`restore_findings`** (`findings.py:1022+`) — bypasses dedup, resolvers and post-add hooks by
  design, so it never reaches `_bump_row` and is unaffected.
- **CB-53** (`reported_at_commit` freshness), **CB-60** (category is uncurated free text).

## Independent edits

| # | Change shape | Locations | Pre-finish verification |
|---|---|---|---|
| 1 | Derive a severity rank from the vocabulary tuple; add `severity_rank()` | `types.py` (beside `rank_case_sql:216`) | new `tests/test_types.py` rank tests, incl. one pinning it against `SEVERITIES` order |
| 2 | `_bump_row` takes the observed severity and an `escalate` flag, computes the escalated value, and appends `severity = ?` to the built SET clause exactly once with its parameter in the right position; `_add_one` passes both through; `import_findings` passes `escalate=False` | `findings.py:395-443`, `_add_one:490-560`, `import_findings:928-942` | reproducer becomes an executable gate; new `tests/test_dedup.py` class |
| 3 | Docs: the dedup section of `CLAUDE.md`, `_bump_row`/`add_finding`/`batch_add_findings` docstrings, CHANGELOG fragment | `CLAUDE.md`, `findings.py` docstrings | read-through |

Two code edits. The earlier four-row table (a new `db` hook registry + a `milestones` consumer) is
withdrawn with the routing scope.

## Design decisions, each naming the rule it obeys

1. **The rank derives from `SEVERITIES`, never a second hand-written table.** `types.rank_case_sql`
   already establishes this for the SQL side precisely so ordering cannot drift from the vocabulary
   (CB-20), and a duplicated-rather-than-shared check is one drift from disagreeing with itself
   (CB-22). `severity_rank` returns `len(SEVERITIES)` for an unknown value — same "unknown sorts
   last" convention `rank_case_sql` uses — so a legacy or corrupt stored value can never be treated
   as more severe than a real one and can never win the escalation.
2. **One assignment per column in the built SET clause** (CB-16). `severity = ?` is appended once,
   from one computed value.
3. **Parameter position is load-bearing and is stated, not left to the implementer.** `meta = ?` is
   NOT part of `sets` — it is spliced after `{sets}` in the f-string and its parameter is appended
   last. The only existing extension of `sets` is the literal `status = 'open'`, which consumes no
   parameter, so this code has never had to get parameter ordering right. The severity parameter is
   appended **immediately after the two `sets` params and before `json.dumps(meta)`**. Getting it
   wrong binds the meta JSON into `severity` and raises `IntegrityError` from the CHECK constraint
   (`findings.py:30`) — outside the module's documented `ValueError`/`KeyError` contract, and not
   caught by `import_findings`' per-row `except ValueError` (`findings.py:941`). A structural test
   asserts the SQL template, not the executed statement (CB-16's rule), via the existing
   `RecordingConnection` pattern: exactly one `severity = ?` and exactly one `meta = ?`.
4. **The write stays inside the existing single UPDATE**, which already carries `RETURNING *` and is
   already inside `_add_one`'s open transaction. No new statement, no new read-modify-write window,
   no new commit (CB-24), and nothing may read `rowcount` off it (the RETURNING rule).
5. **An import is not an observation, so an import does not escalate (CB-51).** `import_findings`
   reaches `_bump_row` through `_add_one` (`findings.py:928-942`, `finding_id=None`), so without a
   carve-out a peer's CSV rating *their* card `critical` would silently re-rate the local card on
   foreign evidence — reversing a decision that was measured and ratified two cards ago. The carve-out
   is an explicit `escalate: bool = True` parameter, defaulted to the observation semantics and
   passed `False` from exactly one call site. **A default that must be overridden at one site is the
   right shape here** — the observation paths (`add_finding`, `batch_add_findings`) are the majority
   and the rule is theirs; import is the documented exception, and it already passes `annotate=False`
   at the same call for the same reason.
6. **`batch_add_findings` escalates, deliberately.** It loops `_add_one` (`findings.py:1239-1254`),
   its members are observations, and the same contract applies. Named here because the previous draft
   left it unnamed — which is this repo's own enumeration failure (CB-24 → CB-27 → CB-36 went
   4 → "a fifth" → 19 that way), and both reviewers caught it.
7. **The reopen path escalates too.** A `fixed` card re-observed at `critical` reopens via
   `_bump_row(reopen=True)` and takes the same escalation. Correct and desired: a regression observed
   as more severe than the original is exactly when the severity should move.
8. **The `wont_fix`/`not_a_bug` recurrence path needs no change.** It files a NEW row carrying the
   observed severity already (`_add_one:542-546`), and its post-add hooks fire normally.

## Corrections to the previous draft (adversarial-review-x2, both attackers)

Corroborated by BOTH models — treated as high-confidence and all adopted:

- The import path was swept into the change unnamed (**FATAL**) → decision 5.
- Parameter placement in `_bump_row` was under-specified → decision 3.
- `_bump_row` has no observed-severity parameter today; the plan required an unmentioned signature
  change → now explicit in edit 2.
- Two of the four listed verification cases were **vacuous** — "a critical row re-observed at low
  stays critical" and "a non-security critical re-observation does not move milestones" both pass
  against unfixed code, since `_bump_row` writes no severity at all and nothing moves milestones
  today → see Verification.
- The reproducer was not an executable gate: it always exits 0 and decided "reproduced" from
  severity alone → see Verification.
- `batch_add_findings` was unnamed → decision 6.
- "max(current, observed)" admitted a backwards implementation → algorithm now spelled out.

Withdrawn with the routing scope (they were fatal to the routing design, and are recorded on CB-35
so the next attempt does not re-derive them): the reopen firing-order bug that would have written a
success-shaped "decision stays decided" audit row on the card's own motivating path; the
contradiction between "one statement" and per-outcome auditing, since one UPDATE cannot classify its
own `rowcount == 0` across six distinct states; the both-streams / no-projection / external-kind
attachment shapes; `restore_findings` rows having no projection at all; registration living in
`milestones/__init__.py` rather than `triage.py`; and that `reconcile._run_guarded:171-208` already
implements the SAVEPOINT-plus-failure-audit policy the draft proposed to rewrite.

Three false claims in the previous draft, corrected rather than quietly deleted:

- *"`agent_capacity` is keyed on `(agent_id, size)`"* — **false.** It is `agent_id TEXT PRIMARY KEY`
  with `large_held`/`small_held`/`triage_held` columns (`_schema.py:148-155`). Moot now, but it was
  wrong.
- *"severity is already validated before the transaction opens"* — **false for the import path**:
  `import_findings` opens `db.txn` at `findings.py:881` and calls `resolve_severity` at `:915`,
  inside it. True for `add_finding` and `batch_add_findings`.
- *"`add()` returning a row whose severity differs from both the stored and the submitted value is
  new"* — impossible by construction, since the written value is always one of those two. The real
  risk is stated below instead.

## Risks

- **`add()` now mutates a column the caller did not ask to modify, and the returned `severity` may
  differ from the submitted one** whenever the stored value is more severe. An automated filer that
  reads back its own submission will see this. Mitigated by the response already carrying
  `was_new: False` and `dedup_action: "bumped"`, and by documenting it on `add_finding`.
- **No existing test pins a bumped row's stored severity** — checked: the only severity assertion in
  `tests/test_dedup.py` is `:306`, on the ring entry, not the row. So the change should not break a
  hidden ratchet, but the full suite is the real check.

## Verification

- **The reproducer becomes an executable gate** — assertions and a nonzero exit, not prints, and its
  severity check is conjoined with the `query(severity="critical")` hit. Its milestone read is
  dropped with the routing scope (it also lacked an `item_kind` filter, CB-26 trap 1).
- **Every new test proven to fail against the unfixed code**, with each mutation verified to parse,
  to have landed, and to run under the worktree's own interpreter — the three checks whose absence
  produced three bogus kills in an earlier iteration.
- **The non-escalation case is labelled a NEGATIVE CONTROL in its name and docstring**, because it
  passes on both sides by construction: unfixed code writes no severity at all, so "a critical row
  stays critical" cannot fail against it. Per CLAUDE.md's corollary, a test that passes on both sides
  is legitimate only when it pins behaviour the change deliberately preserves — and it must say so,
  or a reader cannot tell it from a broken test.
- **CB-43 branch coverage**: `open`/`in_progress`/`stale` escalate in place; `fixed` reopens **and**
  escalates; `wont_fix`/`not_a_bug` file a new row at the observed severity (unchanged).
- **Import carve-out test**: a foreign CSV row rating a card `critical` leaves a local `low` card at
  `low`, and the row still bumps (`occurrence_count` moves) — proving the carve-out is scoped to
  escalation and did not disable dedup.
- **CB-16 structural test**: `RecordingConnection` over `_bump_row`'s SQL template — exactly one
  `severity = ?`, exactly one `meta = ?`.
- `uv run --extra dev python -m pytest tests/ -q` and `uv run --extra dev ruff check src/ tests/`,
  both in the worktree.
