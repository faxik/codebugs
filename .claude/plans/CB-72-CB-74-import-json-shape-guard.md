# CB-72 + CB-74 — `import_json` accepts its argument without checking its shape

Base: `3cd23d0`. Branch: `fix/cb-72-cb-74-import-json-shape-guard`.

CLAUDE.md: *"Domain functions raise `ValueError` for invalid input."* `import_json` does not.
Two inputs walk past that contract and out to the caller as a stdlib exception.

## Revision history — this plan is the SECOND draft

The first draft also carried **CB-71** (a missing `except OSError` arm in the CLI handler)
and was reviewed by an Opus adversary and by Codex in parallel. **Both returned FAIL, and
both named the same two things**, so both are taken here rather than argued with:

1. **The cluster was illegitimate.** `bug-clustering.md:8` — one tree *"if and only if at
   least one predicate passes **and** every ceiling passes"*. The draft conceded no
   predicate covered CB-71 and kept it on the ceilings; ceilings are necessary, not
   sufficient, and *"co-location, the same function, or likely merge conflict is not
   enough"* (`:31`). CB-71 is split out, is back to `open` with its full evidence, and gets
   its own tree. **What is left — CB-72 + CB-74 — is a genuine predicate 2**: one guard, one
   function, one before→after transformation, which the Opus reviewer affirmed in those
   words. It is equally predicate 1 under Codex's framing (one normalization mechanism,
   input → non-empty `list[dict]`; remove it and both symptoms return). It does not need
   both, and it holds under either.
2. **The draft specified two opposite behaviours for `bytes`**, a page apart — the guard
   rejected it while "deliberately unchanged" promised it kept working. Resolved below by
   choosing, once.

The split also proved load-bearing rather than procedural: review found that CB-71's fix
leaves the card's own symptom half-open (a post-commit `BrokenPipeError` on the *success*
`print`), which is an unresolved design question. Under the hostage test, two
trivially-correct guards must not wait on it.

## Round 3 — the diff review, and the defect the guard reintroduced

A Codex review of the implemented diff found a **MUST-FIX that the plan's own
claim had ruled out**: "every accepted-list element is checked" was false,
because the check *iterated* while the code after it *indexed*.

```python
class SplitList(list):
    def __iter__(self): return iter([{"method": "bm25", "score": 0.5}])
import_json(json_data=SplitList([1]))   # AttributeError at data[0].keys()
```

CB-74's exact exception, reproduced **through its own fix**. The repair is to
materialize the list once and validate and consume only that snapshot — the
general form being this repo's recurring lesson in a new place: *validating one
view while consuming another is not a guard*, the same shape as "sharing an
implementation does not share a decision if the callers supply different
inputs".

Three smaller corrections from the same round, plus one the run found:

- Refusal messages are anchored **whole** with `re.escape`. The first draft's
  `"element 0 .*not int"` also matches a message that names the accepted set
  wrongly — a regex loose enough to certify a bug it was written to catch.
- `test_the_unfixed_exception_is_not_a_value_error` was **deleted**. It asserted
  `not issubclass(TypeError, ValueError)` — a property of Python, not of this
  code, so it could never fail. A cannot-fail test dressed as a premise pin is
  exactly what this repo treats as worse than no test; the real non-vacuity
  evidence is the recorded run against main, which belongs in the docstring.
- The `Mapping` guard's **narrowing is now stated and pinned** rather than
  implied: a row duck-typing `.keys()`/`.get()` without registering as a
  `Mapping` imports on main and is refused here, deliberately.
- **The regression test's first draft asserted the wrong outcome** — caught by
  running it, not by review. With the snapshot in place `SplitList` no longer
  raises at all: the one materialized view is both validated and consumed, so
  the payload is coherent and imports the row it validated. The discriminator
  is *no `AttributeError`*, not a refusal.

## The `bytes` decision, made once and stated once

**`bytes` and `bytearray` are ACCEPTED, and the annotation widens to say so.** Measured on
`3cd23d0`: `import_json(json_data=b'[{"a":"x","b":2}]')` and the `bytearray` form both
import successfully today, because `json.loads` accepts them. The defect being fixed is
*"a raw exception escapes where the contract promises a refusal"* — **refusing an input
that works today is a different change**, with no reported caller asking for it, and it
would be scope creep wearing a tidiness costume. So the accepted set is exactly what the
parse step already accepts: `str | bytes | bytearray | list`.

The annotation `json_data: str | list` (`bench.py:178`) is therefore corrected to match
reality rather than used as the authority for breaking it. The MCP wire signature
(`bench.py:551`) is a **separate** declaration and is untouched — `bytes` never arrives
over the wire (pydantic coerces `b'x'` to `'x'` against `str | list | None`), so this is
purely the in-process contract, and `tests/golden/mcp_schema.json` must stay
byte-identical.

## The transformation

One positive shape check at the top of `import_json`, producing exactly two refusals:

| # | condition | raises |
|---|---|---|
| 1 | `json_data` is not `str`/`bytes`/`bytearray`/`list` | `ValueError` naming the accepted types and the type received |
| 2 | after parse/adopt, any element is not a mapping | `ValueError` naming the **index** and the type found there |

Existing refusals (`not a list`, `empty`, `fewer than 2 keys`) keep their current text.

**It is a positive shape check, never an exception rewrap**, and this is a hard
constraint, not a style note. Wrapping the body in `except (TypeError, AttributeError):
raise ValueError(...)` would satisfy every test that merely asserts `pytest.raises(
ValueError)` while *also* converting any **post-commit** failure inside
`return import_csv(...)` (`bench.py:216-224`) into a `ValueError` — which
`_cmd_bench_import`'s arm at `:710` then reports as bad input for a write that landed.
That is the CB-15/CB-16 lie, re-entering through the fix for it. The tests below use
`match=` so they cannot certify that shape.

**Every element is checked, not `data[0]`.** Verified: `[{"a":1,"b":2}, 5]` gets past a
first-element-only guard and dies in `writer.writerows(data)` (`bench.py:214`) with the
same `AttributeError`. A `data[0]`-only fix would be the enumeration-shaped fix this repo
keeps relearning about.

**`Mapping`, not `dict`.** `isinstance(el, dict)` would newly refuse
`MappingProxyType`, which works today (Opus N1). `collections.abc.Mapping` accepts every
mapping `csv.DictWriter` can consume — it uses `.keys()` and `.get()`, both guaranteed by
the ABC — so the guard adds no refusal beyond the two above.

## CB-72 — `import_json(json_data={})` leaks `TypeError`

**Reproducer** (run 2026-08-17 against `3cd23d0`): `import_json(conn, benchmark="b",
json_data={})` → `TypeError: the JSON object must be str, bytes or bytearray, not dict`.

**Root cause.** `bench.py:201-202` — the `else` branch calls `json.loads(json_data)` with
no guard, so anything not a `list` is handed to `json.loads` and whatever it raises escapes.

**Reachability: in-process only**, and the card's stated reason for that is right while
its line cite is stale (`:529` → `:551`, CB-67 moved it). Confirmed by both reviewers
against the SDK: the wire model is built from the annotation
(`mcp/server/mcpserver/utilities/func_metadata.py:232`), and `{}` / `5` are refused by
pydantic before the wrapper body runs.

## CB-74 — `import_json(json_data=[1,2])` leaks `AttributeError`, and this door is open on the wire

**Reproducer**, all three run 2026-08-17 against `3cd23d0`:

| call | raises |
|---|---|
| `import_json(…, json_data=[1, 2])` | `AttributeError: 'int' object has no attribute 'keys'` |
| `import_json(…, json_data='[1, 2]')` | same |
| `import_json(…, json_data='[null]')` | `AttributeError: 'NoneType' object has no attribute 'keys'` |

**Root cause.** `bench.py:207` — `keys = list(data[0].keys())`. Both branches above it
check that `data` is a **non-empty list**; neither checks what is *in* it.

**Reachability: over the wire**, which is why it is `medium` where CB-72 is `low`.
`json_data: str | list | None` (`bench.py:551`) is a bare `list`, so `[1,2]` validates.
Codex additionally found the SDK pre-parses a wire *string* `"[1,2]"` into a list
(`func_metadata.py:146`), so the string form is reachable too.

## Verification

Every case below was run against the unfixed tree and its current exception recorded, so
none can be vacuous. New class `TestImportJsonShapeGuard` in `tests/test_bench.py`:

| case | unfixed | fixed |
|---|---|---|
| `{}` | `TypeError` | `ValueError`, `match` names accepted types + `dict` |
| `5` | `TypeError` | `ValueError`, `match` names `int` |
| `[1, 2]` | `AttributeError` | `ValueError`, `match` names index `0` |
| `'[1, 2]'` | `AttributeError` | same |
| `'[null]'` | `AttributeError` | `ValueError`, `match` names index `0` |
| `[{"a":1,"b":2}, 5]` | `AttributeError` (from `writerows`) | `ValueError`, `match` names index **1** |
| `b'[{"a":"x","b":2}]'` | imports | **still imports** (regression pin for the decision above) |
| `bytearray(...)` | imports | **still imports** |
| `MappingProxyType` element | imports | **still imports** (pins `Mapping` over `dict`) |

Plus: the whole suite, `ruff check`, and `tests/golden/mcp_schema.json` unchanged.

**The `match=` argument is not decoration** — without it the guard could be an exception
rewrap and every row still passes. The index in rows 3–6 is what proves the check is
per-element rather than `data[0]`-only.

## Deliberately unchanged

- **`import_csv(csv_data=<non-str>)` leaks `TypeError`** (`bench.py:120`). Reproduced;
  **now filed as CB-75** rather than left as a note, since a reproduced defect that is
  neither fixed nor filed is how a sweep loses its result. Excluded for **one** reason —
  a different accepted-type condition in a different function, i.e. a second
  transformation. The first draft also argued "in-process only" and "the annotation already
  states the contract"; both were deleted because they apply verbatim to CB-72, which is
  *included*, so that reasoning would have justified the opposite conclusion equally well.
- **What this guard does NOT close.** It closes *element-type* leaks. It is not a claim
  that `import_json` can no longer raise anything but `ValueError`: a dict subclass that
  overrides `keys()` to return a `list` still dies inside `writerows`, and non-`str` dict
  keys still stringify through CSV. Both are in-process-only and pathological; enumerated
  in the review record, deliberately not chased.
- **CB-71** — split out, `open`, its own tree, evidence on the card.
- **CB-55** (the `JSONDecodeError`-first arm copied across CLI handlers) — a
  de-duplication refactor across three modules, not a leak on this path.

## Risks

- **Widening `TypeError`/`AttributeError` → `ValueError` is a contract change for any
  caller catching the old types.** Sweep run, not deferred: no caller in `src/` or
  `tests/` catches either around these paths, and no test asserts the current types.
  Neighbouring tests already expect `ValueError` for adjacent invalid shapes
  (`tests/test_bench.py:114`).
- **Vacuity, not regression, is the live risk** — this repo shipped three cannot-fail
  tests in one afternoon. Mitigated by the recorded unfixed behaviour per row and by
  `match=`.

## Commit shape and pre-finish reconciliation

One mechanism, one function, one commit: `Resolves: CB-72, CB-74` — the
`bug-clustering.md:114` exception for a genuine single mechanism, where splitting would
misstate the change. Reconciliation ledger before finish:

```text
ID     | FULL/PARTIAL | in branch name | tracker+status | trailer commit
CB-72  | FULL         | yes            | in_progress    | yes
CB-74  | FULL         | yes            | in_progress    | yes
CB-71  | n/a — split  | no             | open           | no trailer
CB-75  | n/a — filed  | no             | open           | no trailer
```

Notes and status for CB-72 and CB-74 are snapshotted before finish and re-read after,
because a `Resolves:` write is a whole-value `--notes` overwrite.
