# CB-82 — a write path must not invent a value from a falsey wrong type

**Card:** CB-82 (`low`, `missing_validation`, `src/codebugs/bench.py`), filed by a Codex diff review
of the CB-75 guard. **Branch:** `fix/cb-82-import-argument-validation` · **Base:** `2cb5dc2`

## Reproduced, and the card is wrong about two of its own examples

```
import_csv(conn, benchmark="s", csv_data="m,v\na,1\n", date=[], run_id=[])
  -> SUCCEEDS, stores run_id='BE-1', date='2026-08-17'
```

`rid = run_id or _next_run_id(conn)` and `run_date = date or utc_now()[:10]` use **truthiness**, so
a falsey wrong type is indistinguishable from "not supplied" and is silently replaced by a default.
The row lands under a date and id the caller never asked for.

**Correction to the card, measured:** it claims `date={}` and `run_id={}` "reach the INSERT and
raise". They do **not** — `{}` is falsey, so they take the same silent-default path as `[]`. The
card's own list of which inputs fail loudly is wrong, which is a small instance of its own thesis.
Verified:

| input | today |
|---|---|
| `date=[]`, `date={}`, `date=""`, `run_id=[]`, `run_id={}`, `run_id=""` | **silently defaulted** |
| `benchmark=[]` | `ProgrammingError: Error binding parameter 2` |
| `date=["x"]` (truthy wrong type) | reaches the INSERT |
| `tags={1,2}` / unserializable `meta` | `TypeError: Object of type set is not JSON serializable` |

Every one violates `CLAUDE.md`'s "domain functions raise `ValueError` for invalid input".

## Scope — one chokepoint, verified

`import_json` **forwards** `date`/`tags`/`meta`/`run_id` straight to `import_csv` (`bench.py:305-314`),
so a single guard covers both entry points. The MCP wrapper declares `date: str | None` etc., and
pydantic refuses non-strings at the wire, so this is **in-process reachable only** — the same
reachability CB-72/CB-75 had, and the reason the card is `low`.

## The rule, stated once

**On a write path, `None` is the ONLY "not supplied" signal.** That is deliberately *different* from
the query side, where `types.is_vocabulary_filter_active` treats `None` **and** `""` as "no filter" —
because a filter that is absent means *match everything*, while a stored value that is absent means
*invent one*. Inventing a date because the caller passed something falsey is how a row acquires a
timestamp nobody chose.

So, at the top of `import_csv`, before any parsing or writing:

| argument | accepted | on violation |
|---|---|---|
| `benchmark` | non-empty `str` | `ValueError` |
| `date` | `None` → today; otherwise non-empty `str` | `ValueError` |
| `run_id` | `None` → generated; otherwise non-empty `str` | `ValueError` |
| `tags` | `None` → `[]`; otherwise a `list` of `str` | `ValueError` |
| `meta` | `None` → `{}`; otherwise a `dict` with `str` keys, JSON-serializable | `ValueError` |

**Type checks use `issubclass(type(x), …)`, not `isinstance`** — CB-75's landed lesson: CPython
honours a `__class__` property, so `isinstance` is spoofable and the guard's predicate must be
identical to the consumer's requirement.

`tags`/`meta` are validated by **attempting `json.dumps(..., allow_nan=False)`** rather than by
inspecting recursively, because `json.dumps` is exactly what the consumer runs — the same
"validate what the consumer consumes" rule the pre-add resolver seam already applies.

### The one behaviour change, stated

`date=""` and `run_id=""` are **refused** where they used to silently default. An empty string is
not a date and not an id; treating it as "not supplied" is the very conflation this card is about.
Nothing in the repo passes `""` — the CLI's `--date` defaults to `None`, and the MCP wrapper's does
too. Called out because a narrowing must be visible, not discovered.

**Out of scope, deliberately:** `date` FORMAT is not validated (a `str` that is not `YYYY-MM-DD`
still stores). That is a real gap and a different question — the column has no CHECK and no existing
validator — so it is named here rather than smuggled in.

## Verification

Group A (must fail against `2cb5dc2`): each falsey wrong type now raises instead of storing a
default — asserted on **which value was stored**, not merely that a call succeeded, because the
pre-fix behaviour is a *successful* call. Group B (compat, both sides): `None` still defaults;
ordinary imports unchanged; `import_json` inherits the guard.

Mutation-check every clause, verifying each mutation **landed**.

## Risks / out of scope

- Not touching `delete_run` / `delete_benchmark` / `query`, which the card also names: those take
  ids and names on **read/delete** paths, where the failure mode is "nothing matched", not "a row
  stored under an invented value". Different shape, own card if wanted.
- Not validating date format (above).
- No new module, no new API.
