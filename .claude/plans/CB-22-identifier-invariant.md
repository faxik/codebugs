# CB-22 — make the interpolated-identifier invariant true

**Card:** CB-22 (low, security, `src/codebugs/entities.py`) — `_SAFE_IDENT` claims to guard every
interpolated SQL identifier and guards almost none of them.

## Reproducer

`/tmp/.../scratchpad/repro_cb22.py`, run with `PYTHONPATH=src uv run python`. Against the unfixed
tree:

```
=== 1. kind.table reaches f-string SQL unvalidated ===
  UNGUARDED: constructed EntityKind(table="findings WHERE 1=1 OR ''='")
  ...and reached SQLite as syntax: unrecognized token: "' WHERE id = ?"
=== 2. readable_cols member reaches f-string SQL unvalidated ===
  UNGUARDED: constructed EntityKind with readable_cols member '(SELECT meta FROM findings)'
  ...and field() returned a column that does not exist: '{"secret":"TOPSECRET"}'
=== 3. sort_col unvalidated on the VOCABULARY branch ===
  GUARDED: Not a bare column identifier: 'severity)--'
```

## Root cause

`EntityKind` is a frozen dataclass whose `table`, `sort_col` and `readable_cols` members are
interpolated into f-string SQL (`entities.py:117`, `:168`, `blockers.py:442`). Nothing validates
them at construction. The comment at `entities.py:19` asserts the invariant; the only enforcement
is `order_by()`'s check on `sort_col`, plus a test (`test_entities.py:150`) that spot-checks the
two literal registry entries. So the invariant holds for the frozen registry by inspection and
holds nowhere for a dynamically constructed kind — which the test suite itself does
(`test_claims.py:197` builds a `widget` kind; `test_entities.py:187` uses `dataclasses.replace`).

Case 2 is the material one: the `readable_cols` membership check guards the **caller's** argument
against the allowlist. It never validates the allowlist's own members, so a malformed kind turns
the allowlist into an exfiltration vector rather than a fence.

## Two corrections to the card

1. **`sort_col` is NOT unguarded on the vocabulary branch.** The card says it is "validated, but by
   `rank_case_sql`'s own regex". Both branches are in fact covered. The real gaps are `table` and
   `readable_cols` only.
2. **`_SAFE_IDENT` was not entirely uncalled.** The card's grep was scoped to `src/`;
   `tests/test_entities.py:150` uses it to assert registry conformance.

The runtime `entities._SAFE_IDENT is types._IDENT` → `True` is an artifact of `re.compile`'s
internal cache keyed on the pattern string, **not** a design guarantee. Two source-level
definitions can still drift independently, so the card's dedup request stands.

## Plan

1. **`types.py`** — add public `is_sql_identifier(name: str) -> bool` over the existing `_IDENT`.
   One source of the pattern in the zero-dependency module. `rank_case_sql` routes through it and
   **keeps** its own check: it is public and has callers that are not `EntityKind`
   (`findings.py:447`, `:502`).
2. **`entities.py`** — delete `_SAFE_IDENT`; add `EntityKind.__post_init__` validating `table`,
   `sort_col`, and **every member** of `readable_cols`, raising `ValueError` naming field and value.
   A malformed kind then dies at construction, including via `dataclasses.replace()`.
3. **`entities.order_by()`** — delete the now-redundant `sort_col` check. `__post_init__`
   guarantees it; leaving both would re-create the two-copies problem this card is closing.
4. **`# noqa: S608` justifications** (`entities.py:117`, `:168`, `blockers.py:442`) — reword to cite
   the enforced invariant instead of promising one.
5. **Tests** — `test_all_registry_identifiers_are_safe` stops reaching into a private and becomes a
   construction test; add the `readable_cols` case Codex named as the likely gap.

Fields deliberately **not** validated: `name`, `result_key`, `terminal`, `id_pattern`,
`sort_vocabulary`, `busy_status` — verified none reach SQL as an identifier (`result_key` is a dict
key; `kind.name` is a bound parameter at `claims.py:272`).

## Risks & out of scope

- `__post_init__` on a frozen dataclass is fine (it assigns nothing). Confirmed by Codex/Sol that
  `replace()` routes through `__init__`; `object.__new__`/pickle bypasses are out of scope.
- **Out of scope:** CB-21 (the update-surface parity gate) is the same *shape* — prose invariant,
  no enforcement — but a different matrix and carries an API-widening decision that is the user's.

## Verification

- Reproducer flips: all three cases print GUARDED.
- Mutations that must each fail a test: delete the `readable_cols` loop; delete the `table` check;
  change the member loop from `all` to `any`.
- `uv run python -m pytest tests/ -q` and `uv run ruff check src/ tests/`.
- Sibling sweep: every f-string-interpolated identifier in the package.
  Already found: `milestones/capacity.py:33,59` build `f"{size}_held"` guarded only by a CHECK
  constraint two layers away — same family, decide fix-here vs file.
