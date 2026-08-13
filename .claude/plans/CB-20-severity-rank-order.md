# CB-20 — `ORDER BY severity` sorts the queue lexically

**Card:** CB-20 (medium, `src/codebugs/findings.py`)
**Branch:** `fix/cb-20-severity-rank-order` off `feature/entity-claims` (1892b80)

## The technically correct fix, stated before any feasibility filter

`severity` is a TEXT column, so SQL sorts it alphabetically: `critical, high, low, medium`.
The declared precedence is `critical, high, medium, low`. The correct fix is to sort by the
**declared rank**, derived from the `SEVERITIES` tuple in `types.py` — which is already
written in precedence order — so the SQL cannot drift from the vocabulary. Hardcoding a
CASE literal per site would work today and rot the moment a severity is added or reordered.

Fully shippable in this tree. No divergence between the correct fix and the shippable one.

## Reproducer (run before planning)

```
query_findings order  -> ['critical', 'high', 'low', 'medium']
expected              -> ['critical', 'high', 'medium', 'low']

THE HARM, with LIMIT:
3 medium + 3 low cards, query_findings(limit=3) -> ['low', 'low', 'low']
                                                   ^ every medium card truncated

get_summary()["open_by_severity"] keys -> ['critical', 'high', 'low', 'medium']
```

## Which sites are actually affected — the card overstates the blast radius

Five `ORDER BY` sites touch a vocabulary column. Only **two** are real defects:

| site | verdict |
|---|---|
| `findings.py:432` `query_findings` | **REAL** — the primary read path; truncates under LIMIT |
| `findings.py:481` `get_summary` | **REAL** — `open_by_severity` key order follows the rows |
| `findings.py:458` `get_stats` | inert — the output dict is pre-seeded `{"critical":0,"high":0,"medium":0,"low":0,...}`, so row order cannot reach the caller |
| `reqs.py:343` `get_reqs_stats` | inert — same pre-seeding with `{"must":0,"should":0,"could":0,...}` |
| `milestones/capacity.py:139` `mi.priority ASC` | not a defect — `priority INTEGER` (`_schema.py:60`), numerically ordered |

**The card's "RELATED: reqs.py sorts by priority which is ALSO lexical" lead is wrong at the
output level** — verified by running: `get_reqs_stats` returns `['must','should','could']`
correctly despite the lexical `ORDER BY`. Correct the card rather than carrying the claim.

The two inert sites are **latent**, not harmless: their correctness depends entirely on the
pre-seeded dict literal, which itself duplicates the vocabulary. Out of scope here; noted.

## Plan

1. **`types.py`** — add `rank_case_sql(column, vocabulary) -> tuple[str, list[str]]`, returning
   a `CASE <column> WHEN ? THEN 0 ... ELSE <len> END` fragment plus the values to bind.
   `ELSE len(vocabulary)` so a legacy or corrupt value sorts **last** rather than first.
   The column name is interpolated (it is an identifier, not a value), so it is validated
   against an identifier regex first — the discipline `entities.py` already uses for its
   allowlisted identifiers.
2. **`findings.py:432`** — `query_findings`: order by the fragment, then `created_at DESC`.
3. **`findings.py:481`** — `get_summary`: same fragment.
4. **Tests** in `tests/test_findings.py` and `tests/test_types.py`.

## The trap, named by the cross-model pass

**Parameter ORDER, not just parameter count.** `query_findings` builds
`SELECT * FROM findings {where} ORDER BY ... LIMIT ? OFFSET ?` and passes
`[*where_params, limit, offset]`. The CASE placeholders appear textually **after** the WHERE
fragment and **before** `LIMIT`/`OFFSET`, so the severity values must be **spliced in that
position** — `[*where_params, *severities, limit, offset]`. Blindly prepending them would
bind severities to the WHERE placeholders and silently corrupt every *filtered* query while
unfiltered ones kept passing. A test must therefore cover ordering **with a WHERE filter
active**, not just the bare query.

Rejected alternatives and their failure modes:
- **f-string interpolation of the values** — violates the project's "never interpolate values
  into SQL" rule, and breaks outright if a canonical value ever contains a quote.
- **Hardcoded CASE literal per site** — duplicates the precedence; adding or reordering
  `SEVERITIES` leaves one site stale and the two sites disagreeing.

## The sibling sweep turned one fix into three sites — including a worse one

Sweeping every `ORDER BY` in the package found a **third** real site the card never
mentioned: `blockers.py:438`, the deferred-entity query, orders by
`EntityKind.sort_col` with `LIMIT`/`OFFSET`. That is `severity` for findings and
`priority` for requirements — and the requirements case is worse than the one the card
describes, because `PRIORITIES = ("must","should","could")` sorts lexically to
`could, must, should`, putting the **highest** priority **last**. Verified by running:

```
deferred REQUIREMENTS, before -> ['could', 'must', 'should']
deferred REQUIREMENTS, after  -> ['must', 'should', 'could']
```

Fixed at the registry rather than at the call site: `EntityKind` already declares
`sort_col`, so its precedence now sits beside it as `sort_vocabulary`, and
`EntityKind.order_by()` returns the fragment. The pair cannot drift apart, and
`blockers` has no branch.

`sort_vocabulary` is **required but nullable**. A synthetic kind in
`tests/test_claims.py` sorts by `"id"`, which is not a vocabulary column, so `None` has
to be expressible — but giving the field a default would let a future kind with a TEXT
vocabulary column inherit "no precedence" silently, which is the whole defect. Requiring
it forces the author to make the call; that is why adding it broke
`test_5a_third_kind_by_declaration_only`, and why fixing that test by declaring
`sort_vocabulary=None` is the right repair rather than adding a default.

`order_by()`'s null branch is also the first real use of `entities._SAFE_IDENT`, which
was declared with a comment claiming it guarded "every interpolated SQL identifier" and
had **no callers at all**.

## Verification

- New tests fail against the unfixed code.
- The LIMIT regression (3 medium + 3 low → top 3 must all be medium).
- Ordering correct **with a WHERE filter active** (the parameter-splice trap).
- `get_summary` key order.
- Unknown/legacy severity value sorts last, not first.
- Full suite + `ruff check`.
