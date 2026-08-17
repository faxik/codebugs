# CB-67 — bench import/delete: the "exactly one of two" contract lives only in the MCP wrappers, and one wrapper's dispatch disagrees with its own validation

Iteration: bugfix-loop 2026-08-17 (iteration 2), focus `codebugs` (pure/simple/confident).
Branch: `fix/cb-67-bench-exclusive-args`.

**Revision 2** — revision 1 was reviewed by Codex/GPT-5.6 and **FAILED**, correctly. Its
unifying rule ("supplied means `is not None`, everywhere") would have introduced at least five
undeclared behavior changes and one outright regression. What that rule got wrong, and the
redesign it forced, are recorded in *Review history* at the bottom; this section is the plan as
it now stands.

## Reproducers — all three run on main (`38313b6`) this session

**D1 — CLI import silently discards the CSV.** `bench-import data.csv --json-file other.json -b X`
→ `Imported: BE-1 (1 rows, 1 values)`, exit 0; `bench-query X` returns `dense/0.99` from
`other.json`. The CSV never reached the database. The MCP twin raises
`ValueError("Provide csv_data or json_data, not both")`.

**D2 — CLI delete silently ignores an argument.** `bench-delete --run-id BE-2 --benchmark X`
→ `Deleted run BE-2`, exit 0, and benchmark `X` is still listed. The MCP twin raises
`ValueError("Provide run_id or benchmark, not both")`.

**D3 — MCP import crashes on a legal empty payload.** `codebench_import(benchmark="b", csv_data="")`
→ `TypeError: the JSON object must be str, bytes or bytearray, not NoneType`. The contracted
path is `import_csv(csv_data="")` → `ValueError: CSV must have at least 2 columns`.

## Root cause — two distinct faults, not one
1. **The XOR *structure* — "refuse when neither, refuse when both" — was never given to the CLI.**
   `_cmd_bench_import` guards only the neither-given case (`bench.py:658-660`) and then *picks a
   winner*: `path = args.json_file or args.file` (`:672`). `_cmd_bench_delete` picks with
   `if/elif/else` (`:760-771`). Both surfaces implement the same contract; only one was told the
   "not both" half. That is D1 and D2.
2. **Inside `codebench_import`, validation and dispatch use different predicates.** It validates
   with `is not None` (`:551-553`) and dispatches with `if csv_data:` (`:555`), so an empty string
   passes validation as supplied and then fails dispatch as absent, falling into the json branch
   with `json_data=None`. That is D3, and it is a self-contradiction *within one function* — no
   other site has it (`codebench_delete` validates and dispatches on truthiness consistently).

## Evidence (read directly this session)
- `src/codebugs/bench.py:551-563` — import wrapper: `is not None` validation, truthy dispatch.
- `src/codebugs/bench.py:634-641` — delete wrapper: truthy validation, truthy dispatch (self-consistent).
- `src/codebugs/bench.py:658-660, 672-673` — CLI import: neither-guard only, then `or`.
- `src/codebugs/bench.py:760-775` — CLI delete: `if/elif/else`, and **`except KeyError` alone**.
- Probed, not assumed: `delete_benchmark(conn, "")` → `KeyError`, not destructive;
  `codebench_delete(run_id="", benchmark="X")` **deletes X today**; `bench-import ""` today prints
  `Provide either a file path or --json-file` and exits 1; `delete_run`/`delete_benchmark` parse no
  JSON, so no post-commit `JSONDecodeError` arm is needed.
- No test anywhere asserts these error strings; `tests/golden/mcp_schema.json` carries the two tool
  **docstrings**, so docstrings must not change without regenerating the golden.

## Plan

**The technically correct fix, stated before any feasibility filter:** give the *XOR structure*
one home that both surfaces call, and separately make `codebench_import`'s dispatch use the same
predicate as its own validation. Fixing the three faces inline instead would re-spell the same
rule at four sites — this repo's named anti-pattern. Correct and shippable do not diverge, so
there is nothing to escalate.

**What the shared helper owns, and what it deliberately does not.** It owns only "refuse none,
refuse both, tell me which one". It does **not** own what *supplied* means, because that legitimately
differs by argument kind: an empty **data payload** (`csv_data=""`) is supplied content that the
domain layer must judge, while an empty **file path** or **entity id** is not a value at all. Revision 1
tried to unify that too, and that is precisely what produced the regression. Each call site passes
its own already-correct predicate as a boolean:

```python
def _require_exactly_one(first: tuple[str, bool], second: tuple[str, bool]) -> None:
    """Refuse unless exactly one of two mutually exclusive arguments was supplied.

    Owns the XOR structure only; the caller decides what "supplied" means for its
    own argument kind, and dispatches on its own named local afterwards. Returns
    nothing, so no caller can dispatch on display text (CB-67).
    """
```

Each call site then reads:

```python
csv_given = csv_data is not None
_require_exactly_one(("csv_data", csv_given), ("json_data", json_data is not None))
...
if csv_given:      # the predicate that was validated IS the one that dispatches
```

1. Add `_require_exactly_one` at module level in `bench.py`.
2. Route all four sites through it, **each keeping the predicate it already uses**:
   - `codebench_import`: `csv_data is not None` / `json_data is not None` — its existing
     *validation* predicate, now used for dispatch too. **This is the whole of D3's fix**, and
     `csv_data=""` becomes `import_csv("")` → the contracted `ValueError`.
   - `codebench_delete`: truthiness, exactly as today → **zero behavior change**.
   - `_cmd_bench_import` / `_cmd_bench_delete`: truthiness, exactly as their existing
     neither-given guards → the "not both" half is what is new.
3. `_cmd_bench_delete` must catch `ValueError` alongside `KeyError`, or the new refusal escapes as
   a traceback. (`_cmd_bench_import` already catches `ValueError`.) No `JSONDecodeError`-first arm
   is needed here — verified: neither delete function parses stored JSON.
4. **Preserve positional JSON inference explicitly**: `bench-import foo.json` (no flag) must keep
   importing as JSON via `path.endswith(".json")`, which is independent of which argument was
   supplied. Regression test required.

### Complete behavior-change ledger — one row, and it is D3's fix
| Input | Today | After | Why |
|---|---|---|---|
| `codebench_import(csv_data="")` | `TypeError` (uncaught, from `json.loads(None)`) | `ValueError: CSV must have at least 2 columns` | D3 — the contracted domain error |
| everything else on both wrappers | — | **unchanged** | each site keeps its own supplied-predicate |
| `bench-import a.csv --json-file b.json` | imports b.json, exit 0 | refuses, exit 1 | D1 |
| `bench-delete --run-id R --benchmark B` | deletes R, ignores B, exit 0 | refuses, exit 1 | D2 |

Two messages lose one word: the label-generated "neither" text is `Provide csv_data or json_data`
where today's reads `Provide **either** csv_data or json_data`, and likewise for the CLI import
handler. `codebench_delete`'s and CLI delete's texts are reproduced exactly. Nothing pins any of
them.

Explicitly **not** changed, each verified as today's behavior: `bench-import ""` still prints the
friendly neither-given error; `bench-import a.csv --json-file ""` still imports the CSV;
`codebench_delete(benchmark="")` still raises its `ValueError`; `codebench_import(json_data=[])`
still reaches `import_json` and raises `ValueError: JSON must be a non-empty array`; and
`codebench_delete(run_id="", benchmark="X")` **still deletes X**. That last one is a silent pick
of the D2 family and is preserved deliberately: under truthiness `run_id=""` *is* "not supplied",
so the call is not a both-given case at all, and changing it would be the undeclared
delete-path semantics change that sank revision 1.

**Why not `argparse.add_mutually_exclusive_group()`**, which would solve the CLI half natively and
before `db.connect()`: it would put one rule in two homes (argparse for the CLI, the helper for
MCP), which is the defect this card is about, and it changes the CLI exit code from 1 to 2 on a
tool whose exit codes shell callers read. Rejected deliberately, not overlooked.

**In-repo prior art**, worth citing because the pattern was nearly reinvented from scratch:
`sweep.archive_items` already does exactly this — supply is `items is None and where_status is
None and older_than is None` (`sweep.py:637`), and the supplied-but-empty list is then decided
*explicitly* rather than by falling through (`sweep.py:661-663`, returning `archived: 0` instead
of letting an empty list widen into "archive everything").

## Risks & out-of-scope
- CLI "not both" error text is new (nothing to break); the neither-given texts are unchanged.
- Docstrings untouched → `tests/golden/mcp_schema.json` needs no regeneration, guarded by
  `tests/test_boundary.py::TestMcpWireSchema`.
- **Scope of the rule, stated because a review asked for it:** this fixes *XOR contracts* only.
  `list_runs` (`bench.py:426`), `codebench_list` (`:619`) and `_cmd_bench_list` (`:728`) also route
  on bare truthiness, so `benchmark=""` lists everything — but those are optional *filters*, where
  CB-25's ratified convention makes `""` mean "no filter". That population is **CB-29**
  (free-text query filters), not this card.
- **Two pre-existing defects found by the sibling sweep, each verified by running it, each filed as
  its own card rather than fixed here** — different transformation, so the clustering rules send
  them to their own tree: `bench-import missing.csv` raises a raw `FileNotFoundError` traceback
  because `_cmd_bench_import` catches only `(ValueError, JSONDecodeError)` and `OSError` is
  neither; and `import_json(json_data={})` leaks `TypeError` from an unguarded `json.loads`, the
  same class as D3 one door over (in-process only — the wire signature is `str | list | None`).
  Neither is introduced or worsened by this change.
- Out of scope: CB-66's exposure layer (the permanent shared home), CB-55's CLI-handler wrapper.
- **Not clustered with CB-68**: no clustering predicate holds — different file, different root
  cause, different transformation. Shared provenance (both found by the same RFC review) is theme,
  which the clustering rules list under "not reasons to cluster".

## Verification — and the exact vacuity trap in each test
`tests/test_bench.py` today exercises **only domain functions**: no `FakeMCP`, no CLI invocation.
Both harnesses must be added (copy `FakeMCP` from `tests/test_blockers.py:556-568`; subprocess for
the CLI, noting `tests/conftest.py` autouse-clears `CODEBUGS_ROOT`). Every test below is run
against the unfixed tree first and must fail there.

- **D3** — vacuous if written `pytest.raises(Exception)` or `raises((ValueError, TypeError))`: the
  unfixed tree raises `TypeError`, which both spellings swallow. Discriminating form:
  `pytest.raises(ValueError, match="at least 2 columns")`. Second trap: calling the *domain*
  `import_csv(csv_data="")` proves nothing — it already raises correctly on both trees — so the
  test must go through the closure built by `register_tools`.
- **D2** — vacuous if it asserts "benchmark X is gone": seed `BE-1` into `X` and the unfixed tree's
  `delete_run("BE-1")` empties `X` anyway, so that is true on both trees. Discriminating form: seed
  `BE-1` under benchmark `A` and separate runs under `X`, call with both arguments, then assert
  refusal **and** `BE-1` still present **and** `X` still present. Assert stderr carries a message,
  not a traceback — that is what catches a missing `ValueError` arm.
- **D1** — vacuous if it asserts "one run was imported": both trees import exactly one run, only
  the *content* differs. Discriminating form: CSV and JSON fixtures with disjoint row labels;
  assert refusal **and** that nothing landed. Do not name the positional fixture `*.json` or the
  extension sniff confounds the result.
- Plus a positive regression test that `bench-import foo.json` (positional, no flag) still imports
  as JSON, and the "explicitly not changed" rows above pinned so the next revision cannot quietly
  unify the predicate again.
- Full suite + `ruff check` in the worktree via `tools/worktree-finish.sh`.

## Review history
**Revision 1 — Codex/GPT-5.6 verdict FAIL.** Its findings, all accepted:
1. *BLOCKING* — `_cmd_bench_delete` catches only `KeyError` (`bench.py:772`), so a helper raising
   `ValueError` would surface as a traceback. Now step 3.
2. *SERIOUS* — the unified `is not None` rule changed at least five behaviors the plan had not
   declared (MCP `benchmark=""`, `run_id=""`+`benchmark="X"`, `run_id="BE-1"`+`benchmark=""`, and
   the CLI twins). I independently reproduced the `run_id=""`+`benchmark="X"` case: it deletes X
   today. Resolved by not unifying the predicate at all.
3. *SERIOUS* — and one was a real **regression**: `bench-import ""` would have selected `""` and
   reached an uncaught `open("")`, replacing today's friendly error with a traceback. Confirmed by
   running it.
4. *SERIOUS* — `bench-import foo.json` positional inference was neither stated nor tested. Now step 4.
5. *SERIOUS* — returning a display label and branching on it makes error wording a control-flow key.
   Now a boolean discriminator.
6. *MINOR* — narrow the rule to XOR contracts; the `if provided` filter sites are a separate
   population. Now stated under out-of-scope.

**Revision 2 — Codex round 2: PASS WITH CHANGES**, both adopted: (a) the helper returns `None` and
each caller dispatches on its own named local (`_require_exactly_one`, not a boolean "the first
won"), keeping it strictly structural; (b) declare the two "neither" messages that lose the word
*either*, since a label-generated text cannot reproduce both existing wordings.

**Revision 1 — Opus adversary, in parallel: PASS WITH CHANGES.** It attacked revision 1, so its
three BLOCKING findings (delete's `KeyError`-only catch, positional `.json`, and the four
undeclared delete-path changes) were already closed by revision 2 — the third by *design*, since
keeping delete on truthiness means it has no behavior change to declare. What it added, all
verified by me directly rather than taken on trust: the `= args.X or args.Y` shape occurs **exactly
once in the whole package** (`bench.py:672`), which is what makes the "no sweep beyond bench"
claim true; `sweep.archive_items` is in-repo prior art for this contract; and two pre-existing
defects one door over (`bench-import missing.csv` → raw `FileNotFoundError`; `import_json({})` →
leaked `TypeError`), both reproduced and filed as their own cards. Its test-design section is
absorbed verbatim into *Verification* above — it identified a vacuity trap in all three tests I was
about to write.

The lesson worth keeping: **revision 1 unified the wrong axis.** Two surfaces disagreeing about a
*structure* (refuse both) is a defect; two argument *kinds* disagreeing about what an empty value
means is not — it is the difference between a payload and a path. Collapsing both under one rule
looked like removing an inconsistency and was actually removing information.
