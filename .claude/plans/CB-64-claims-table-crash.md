# CB-64 — `codebugs claims --format table` crashes (swapped format_table args)

Iteration: bugfix-loop 2026-08-17, focus `codebugs` (pure/simple/confident).
Branch: `fix/cb-64-claims-table-format`.

## Reproducer
In a scratch tracker (worktree venv, 2026-08-17):
- `add` a finding, `claim` it, then `codebugs claims` (default `--format table`) →
  `AttributeError: 'str' object has no attribute 'get'` at `fmt.py:13`, raw traceback, exit 1.
- With zero live claims → seven blank lines instead of `(no results)`, exit 0.

## Root cause
`claims.py:781` calls `fmt.format_table([headers], rows)` — arguments swapped against the
signature `format_table(rows: list[dict], columns: list[str])` (`fmt.py:6`) — AND builds the
rows as lists, not dicts. With claims present, `format_table` iterates the header strings as
"rows" and calls `.get()` on them. With none, `columns` is the empty rows list, so the
header list is treated as rows of zero columns → blank lines; the `(no results)` early-return
never fires because the "rows" (headers) are non-empty.

Every other `format_table` call site passes `(rows, columns)` correctly (verified by the card's
filer; the fix re-checks). Structural cause per the card: no tests exercise the CLI table path —
`TestCliContract` covers `--format ids` but not the default `table`.

## Evidence
- `src/codebugs/fmt.py:6` — signature `format_table(rows: list[dict], columns: list[str], ...)`.
- `src/codebugs/claims.py:771-781` — list-of-lists rows, swapped call.
- Reproduction transcript above (this session, direct).

## Plan
1. TDD: add `TestCliContract` tests in `tests/test_claims.py`:
   - live-claims table: claim a finding, run `claims` (no `--format`, i.e. the default);
     assert exit 0, header names present, and the entity id + holder values present
     (per Codex: assert representative values, not just exit code — `dict.get` makes
     misspelled keys silently blank).
   - empty table: assert exit 0 and output is `(no results)` — an intentional behavior
     change from blank lines, consistent with the shared formatter.
   Prove both fail against the unfixed tree before fixing.
2. Fix `_cmd_claims_list`: build dict rows keyed by the column names, call
   `format_table(rows, columns)`.
3. Sibling sweep: grep every `format_table(` call site; verify each passes `(rows, columns)`
   with dict rows.

## Risks & out-of-scope
- Empty-path output changes blank-lines → `(no results)`: desired, matches the formatter
  contract; no shell caller can be parsing seven blank lines meaningfully, and `--format ids`
  is the documented machine surface.
- Out of scope: a general `test_cli.py` for all handlers (the card's structural note);
  CB-55's shared-handler-wrapper refactor.
- Review: adversarial-review-x2 SKIPPED — mechanical fix (single file, <30 lines, no new
  API, no gate); Codex already reviewed the pick and the fix shape in the pick-time pass.

## Verification
- New tests fail on unfixed tree (AttributeError / blank lines), pass after.
- Full suite + `ruff check` in the worktree via `tools/worktree-finish.sh`.
