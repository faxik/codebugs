# CB-67 — bench import/delete: XOR validation lives only in the MCP wrapper, and the wrapper's dispatch disagrees with its own validation

Iteration: bugfix-loop 2026-08-17 (iteration 2), focus `codebugs` (pure/simple/confident).
Branch: `fix/cb-67-bench-exclusive-args`.

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

## Root cause — one cause, three faces
The "exactly one of two" contract is **written down only inside the two MCP wrappers**, and
inside `codebench_import` it is written *twice, differently*: validated with `is not None`
(`bench.py:551-553`), dispatched with `if csv_data:` (`bench.py:555`). An empty string passes
validation as supplied and then fails dispatch as absent, falling into the json branch with
`json_data=None`. The CLI handlers never received the contract at all: `_cmd_bench_import`
guards only the neither-given case then picks with `path = args.json_file or args.file`
(`bench.py:672`), and `_cmd_bench_delete` picks with `if args.run_id: … elif args.benchmark:`
(`bench.py:763-767`). Both pick a winner where the contract says refuse.

## Evidence (read directly this session)
- `src/codebugs/bench.py:551-563` — import wrapper: `is not None` validation, truthy dispatch.
- `src/codebugs/bench.py:634-641` — delete wrapper: truthy validation, truthy dispatch (self-consistent, so D3 has no delete twin).
- `src/codebugs/bench.py:658-660, 672-673` — CLI import: neither-guard only, then `or`.
- `src/codebugs/bench.py:760-771` — CLI delete: `if/elif/else`.
- `delete_benchmark(conn, "")` → `KeyError: Benchmark not found:` — probed; empty string is **not** destructive.
- No test anywhere asserts these error strings; `tests/golden/mcp_schema.json` carries the two
  tool **docstrings**, so docstrings must not change without regenerating the golden.

## Plan
**The technically correct fix, stated before any feasibility filter:** give the contract exactly
one home that both surfaces call, rather than re-establishing it at four sites. The card's own
words — "the RFC's op-body pattern gives this validation a shared home; **until then, both fixes
are local**" — authorize a bench-local home, and CB-66's exposure layer later absorbs it. Fixing
the three faces inline instead would re-spell the same rule at four sites, which is this repo's
named anti-pattern ("point-of-use discipline is the wrong enforcement layer"; "a rule expressed
as an enumeration gets fixed at the sites someone enumerated"). Correct and shippable do not
diverge here, so there is nothing to escalate.

1. Add one module-level helper to `bench.py`:
   `_select_exclusive(*candidates: tuple[str, Any]) -> tuple[str, Any]` — returns the single
   supplied `(label, value)`, raises `ValueError` naming the labels when none or both are given.
   Labels are passed by the caller so each surface keeps its own spelling (`csv_data` vs
   `--json-file`).
2. **"Supplied" is `is not None`, never truthiness** — one rule, both ops, both surfaces. This is
   the predicate `codebench_import` already validates with; the bug is that its dispatch used a
   different one.
3. Route all four call sites through it: both MCP wrappers, both CLI handlers.

### The one deliberate behavior change beyond the card
`codebench_delete(run_id="")` currently raises `ValueError("Provide run_id or benchmark")`
because that wrapper validates on truthiness. Under the unified rule it becomes
`KeyError("Run not found: ")`. This **moves toward** the documented convention — "`ValueError`
for invalid input, `KeyError` for missing entities" — because an empty run id was *supplied*,
it just does not exist. Accepted deliberately; recorded here so it is not discovered later as
drift.

## Risks & out-of-scope
- CLI error text changes slightly (`Provide file path or --json-file` in place of
  `Provide either a file path or --json-file`). No test or doc pins it.
- Docstrings are left untouched, so `tests/golden/mcp_schema.json` needs no regeneration —
  verified as part of the suite (`tests/test_boundary.py::TestMcpWireSchema`).
- Out of scope: CB-66's exposure layer (the permanent home), CB-55's shared CLI-handler wrapper,
  and any other domain's surface-drift. No sweep beyond bench's own four sites unless the sibling
  sweep finds the identical `or`-picks-a-winner shape elsewhere.
- **Not clustered with CB-68** (also a bench/blockers convention finding from the same RFC
  review): no clustering predicate holds — different file, different root cause, different
  transformation. Same-review provenance is theme, which the shared clustering rules list under
  "not reasons to cluster".

## Verification
- New tests for D1, D2, D3 proven to fail on the unfixed tree before the fix.
- Both surfaces asserted for each op: MCP and CLI must refuse the same input the same way.
- Full suite + `ruff check` in the worktree via `tools/worktree-finish.sh`.
