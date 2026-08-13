# CB-16 — `update_finding` / `update_requirement` clobber `meta`

Branch `fix/cb-16-meta-clobber`, based on `feature/entity-claims` (`c8f07e6`).
Severity high, category `data_loss`. Claim `CLM-1`.

## Reproducer

`scratchpad/repro_cb16.py`, run against the unfixed tree — 5 failing checks:

| Check | Unfixed result |
|---|---|
| `notes` + `meta_update` keeps both | `meta={'k': 'v'}` — notes destroyed |
| `append_note` + `meta_update` keeps both | `notes='FIRST'` — the appended line destroyed |
| `notes` + `append_note` extends the replacement | `notes='OLD\nEXTRA'` — extended the *prior*, not the replacement |
| reqs `notes` + `meta_update` keeps both | `meta={'k': 'v'}` — notes destroyed |
| no UPDATE assigns `meta` more than once | 4 of 6 UPDATEs assign it twice |

Captured statement (SQLite trace, parameters expanded):

```sql
UPDATE findings SET meta = '{"notes": "INVESTIGATION"}', meta = '{"k": "v"}',
       updated_at = '...' WHERE id = 'CB-1'
```

## Root cause

`src/codebugs/findings.py:264-285` — three branches (`notes` :264, `append_note` :270,
`meta_update` :281) each call `json.loads(row["meta"])` on the row fetched at :252, *before*
any branch ran, and each appends its own `meta = ?` to the shared `updates` list. The
statement built at :299 therefore assigns `meta` up to three times. SQLite accepts duplicate
assignments in `SET` and applies the **last** one, so every earlier branch is discarded.

Two independent faults compose, and both must be fixed:

1. **Stale reads** — later branches cannot see earlier branches' edits.
2. **Duplicate assignment** — only the last `meta = ?` survives.

Fixing only (2) would still lose data, because the surviving branch was itself built from the
stale row. Fixing only (1) would still lose data, because the last assignment still wins.

`src/codebugs/reqs.py:201-213` has the same shape with two branches (`notes`, `meta_update`).
It has no `append_note` parameter.

## Plan

Accumulate into **one** dict and emit **one** `meta = ?`, preserving the existing branch order.

```python
new_meta = json.loads(row["meta"])
meta_changed = False
if notes is not None:          # replaces
    new_meta["notes"] = notes; meta_changed = True
if append_note is not None:    # extends whatever notes now holds
    prior = new_meta.get("notes")
    new_meta["notes"] = f"{prior}\n{append_note}" if prior else append_note
    meta_changed = True
if meta_update is not None:    # merges last; an explicit "notes" key still wins
    new_meta.update(meta_update); meta_changed = True
if meta_changed:
    updates.append("meta = ?"); params.append(json.dumps(new_meta))
```

Ratified ordering semantics, to be documented in both docstrings:
`notes` replaces → `append_note` extends the replacement → `meta_update` merges last and an
explicit `meta_update["notes"]` still wins.

## Behaviour that must NOT change

Verified against the current implementation and pinned by tests:

- `is not None`, never truthiness — `notes=""` and `append_note=""` are real writes.
- `meta_update={}` still counts as a write: it emits `meta = ?` and moves `updated_at`.
- Unrelated pre-existing meta keys survive.
- The no-op path (`if not updates: return db.row_to_dict(row)`) is untouched.
- The status-change hook at `findings.py:304` / `reqs.py:227` and its `rowcount == 1`
  condition are untouched.

## Risks & out of scope

- **Out of scope:** exposing `append_note` over MCP/CLI (CB-18, CB-15) — a different edit
  shape in a different layer. CB-18's own text says fix this clobber first.
- **Out of scope:** adding `append_note` to `update_requirement`; that is new API, not this
  defect. The asymmetry is noted, not closed.
- **Out of scope:** restructuring the `updates`/`params` SET builder into a column-keyed map.
  Codex (gpt-5.6-sol) argued this is a broader refactor with no evidence that any other
  column composes across branches — `status`, `tags`, `reported_at_ref` each appear in
  exactly one branch, so duplicates are structurally impossible for them.
- Semantics of `notes` + `append_note` together are a *choice*. The card ratified
  "append extends the replacement"; this is a behaviour change vs. today, where the append
  silently built on the value the caller just tried to replace. Today's behaviour is
  incoherent, so this is a fix, not a regression — but it is documented.

## Verification

1. `repro_cb16.py` → 0 failing checks (was 5).
2. New combination tests in `tests/test_findings.py` and `tests/test_reqs.py`, 8 of them proven
   to fail against the reverted source. The ordering tests
   (`meta_update["notes"]` wins) and the `meta_update={}` test pass against old *and* new code:
   they pin the contract, they are not reproducers, and they are not counted as evidence.
3. Full suite: 623 passed. `ruff check` clean. `ruff format --check` leaves only the three
   files (`findings.py`, `reqs.py`, `test_reqs.py`) that were already unformatted at the base
   commit — no new drift.

## Cross-model review (Codex / gpt-5.6-sol)

Reviewed the diff and found two real problems, both fixed:

1. **Regression introduced by the first cut.** Hoisting `json.loads(row["meta"])` to the top of
   the function made *every* update depend on the stored meta parsing. `meta` has no
   `json_valid` constraint, so on a malformed legacy row a status-only update changed from
   "write lands, hooks fire, commit, then the result conversion raises" to "raises before the
   SQL, nothing lands". Fixed by parsing lazily, only when a meta argument is present. Pinned by
   `test_non_meta_update_still_writes_when_stored_meta_is_malformed`, which fails against the
   eager version (status stays `open`).
2. **A vacuous assertion.** `test_empty_meta_update_still_counts_as_a_write` compared
   `updated_at` with `>=` at one-second resolution, so it passed even if the call degraded to a
   no-op. Rewritten to assert on the emitted SQL.

Codex confirmed the ordering, the `is not None` handling, the no-op path, and the status-hook
condition are all unchanged, and that the JSON read-modify-write has no row lock in either
version — pre-existing exposure, not introduced here.

Separately, my own first structural guard was vacuous: it searched for the literal `meta = ?`
in SQL captured via `set_trace_callback`, which reports parameters already expanded, so it could
never match. It now matches `\bmeta\s*=`.
