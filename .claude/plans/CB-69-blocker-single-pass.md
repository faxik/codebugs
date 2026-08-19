# CB-69 — the deferred path evaluates the blocker set twice

Branch `fix/cb-69-blocker-single-pass` (renamed from `fix/cb-69-cb-84-blocker-batch`), base `c0b963e`.
**Revision 2**: one card, after `adversarial-review-x2` split the cluster.

## The cluster was refused, by both models independently

Revision 1 paired this with CB-84 under predicate 1 (shared root cause: no collection-level
active-blocker evaluation). The Opus adversary and Codex/GPT-5.6-Sol both returned **SPLIT**, Codex
at 0.97 confidence, and they were right on the falsifiable test the predicate itself specifies —
*no card needs a change the other does not*:

- CB-69 wants **everything of one entity_type**. CB-84 wants **a specific set of item refs**. An
  IN-list of thousands of ids is worse for CB-69; a whole-type scan is worse for CB-84. Two call
  shapes, so *sharing an implementation does not share a decision when the callers supply different
  inputs* — this repo's own recorded lesson.
- CB-84 additionally needs a swallow-replacement and a type narrowing that CB-69 does not.
- Row 3 of revision 1's table therefore carried three transformations, putting the count at five
  against a hard ceiling of four.

**And revision 1's CB-84 edit was a measured pessimization**: routing it through this card's
whole-type summary took a 5-item milestone in a 200-blocker tracker from 7 statements to 203, and
did it inside `closegate`'s `BEGIN IMMEDIATE`. CB-84 is back to `open` carrying all of that
evidence, re-scoped to its real three-site population. Nothing was implemented for it.

**No third review round.** This revision only *narrows* to what both reviewers said to land, in the
shape they both proposed. Saying so rather than implying a round happened.

## Reproducer (measured in this worktree)

`deferred_id_restriction` then `blocker_counts_for`, back to back — the real call pattern:

```
entity_resolved blockers=  2 -> 3 + 3  =  6 statements
entity_resolved blockers= 10 -> 11 + 11 = 22
entity_resolved blockers= 30 -> 31 + 31 = 62      # 2 x (1 + B); 31 would do
date / manual   blockers= any -> 1 + 1  =  2      # flat
```

**A refinement the card does not state.** CB-69 says "every entity-type blocker resolution goes
through `EntityRef` status reads". True only for the `entity_resolved` trigger:
`is_blocker_satisfied` (`blockers.py:69-82`) reads no entity for `date` (a timestamp compare) or
`manual` (a `resolved_at` null check). The double **scan** is unconditional; the per-blocker
**reads** are not. Both reviewers confirmed this formula independently.

## Root cause

`get_deferred_item_ids` (`blockers.py:487-491`) and `blocker_counts_for` (`:522-531`) each call
`_get_active_blockers_by_type` (`:421-429`) independently. Both wrappers call them back to back in
one function with a domain query between: `findings.py:2081` then `:2114`; `reqs.py:816` then `:832`.

## Plan — 3 independent edits

| # | Change shape | Locations | Pre-finish verification |
|---|---|---|---|
| 1 | Extract the one-pass evaluation; add the combined accessor; route both helpers through it | `blockers.py` | statement count at 2 blocker counts |
| 2 | Wrapper derives both halves from one pass | `findings.py:2081,:2114` | statement count over the wrapper path |
| 3 | Same for requirements | `reqs.py:816,:832` | statement count over the wrapper path |

### The API — `deferred_ids_and_counts`, NOT a `counts=` cache parameter

Revision 1 proposed a keyword-only `counts=None` on both helpers so a caller could pass a
precomputed summary. **Both reviewers killed it, for the same reason**: a summary is a plain dict
carrying no record of its scope, so handing a `finding`-scoped summary to a `requirement` query
returns the empty set — no error, no signal — and `deferred_id_restriction` then returns `[]`, which
both wrappers short-circuit into a **zero-row page** (`findings.py:2085-2093`, `reqs.py:820-826`).
That is CB-25/CB-28's exact failure shape — an empty queue indistinguishable from a correct one —
installed by the fix for a performance bug.

So the summary is **operation-local and never exposed as a parameter**:

```python
def deferred_ids_and_counts(
    conn, entity_type, *, id=None, ids=None
) -> tuple[list[str], dict[str, int]]:
    """Both halves of the deferred projection from ONE evaluation."""
```

`entity_type` is taken **once**, so a scope mismatch is unrepresentable, and both halves come from
one call so the wrappers cannot pair mismatched ones. `deferred_id_restriction` and
`blocker_counts_for` keep their signatures and behaviour (existing callers and tests untouched) and
both delegate to the same extracted one-pass evaluation.

**`blocker_counts_for`'s missing keyword-only `*` is NOT fixed here** — that is CB-68's nine-site
population question and a one-site fix is what that card explicitly refuses.

### Naming, honestly

"Single pass" means **one pass over the blockers table**, not one SQL statement: entity-trigger
evaluation is still `1 + B` statements because each `entity_resolved` blocker resolves its
dependency's status individually. Batching *that* is a deeper change (Codex suggests the
`EntityKind.terminal_exists_sql` primitive from CB-31 plus `GROUP BY`) and is **out of scope,
named** — it changes the evaluation mechanism rather than removing a duplicate call.

### Snapshot semantics — declared, because this IS a behaviour change

Sharing collapses two independent snapshots into one. Today the two calls can legitimately disagree:
a `date` trigger crossing its deadline between them yields an id in `deferred_ids` with
`blocker_count: 0`, and another connection can resolve or cancel a blocker in the window. After this
change the two halves are always mutually consistent.

That is an **improvement and a behaviour change**, and a single-threaded differential test over one
fixture structurally cannot see it — so it is stated here rather than asserted away. The wrappers
open no encompassing transaction (`server.py:99-104` opens none; both calls sit in one
`with conn_factory()` block), and the intervening domain query is read-only (`query_findings`,
`query_requirements`), so nothing in this code's own actions invalidates the shared summary — both
reviewers verified that independently.

## Risks & out-of-scope, named

- **CB-84's three-site population** (`_spine._items_with_active_blockers` with its two callers,
  `capacity._has_active_blocker` inside `pull_next`'s write lock, `blockers.get_unblocked_by`) — its
  own tree, evidence on the card.
- **`get_deferred_counts` (`blockers.py:534-563`)** is a fourth caller of
  `_get_active_blockers_by_type`, reached from `findings.py:2149` / `reqs.py:868`. It needs
  `trigger_type`/`trigger_at` for `overdue_count`, which a bare count map cannot carry. It therefore
  keeps its own pass, and the extracted helper returns **evaluated rows** so a later card can serve
  it without a third definition. Deliberately not wired here: it is a separate call from a separate
  wrapper branch, so folding it in is a fourth edit and a different data shape.
- **`query_deferred_entities` (`blockers.py:432-457`) already evaluates once** and builds active
  counts (Codex). This tree must *reuse the extracted helper* rather than add a second definition of
  the same aggregation — checked as part of edit 1.
- **CB-68** (`blocker_counts_for`'s signature), **CB-94** (per-connect schema re-verification):
  different mechanisms.

## Verification

1. Statement-count tests at **two different blocker counts**, so a constant is distinguishable from
   a linear term — a single size cannot tell `1+B` from `2(1+B)`.
2. Differential: `deferred_ids_and_counts` agrees with `deferred_id_restriction` +
   `blocker_counts_for` over a fixture spanning **all three trigger types**, plus the
   `id`/`ids` intersection cases and the empty-intersection short-circuit (`TestDeferredEmptyIntersection`
   must still pass — CB-28's defect reappearing inside its own fix is the standing trap).
3. Both wrapper paths (`findings` and `reqs`) exercised through their public entry points.
4. Full suite in the worktree + `ruff check`.
5. Mutation harness: each edit reverted individually must fail a named test.
