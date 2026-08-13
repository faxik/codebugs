# CB-25 — falsey non-string vocabulary filters silently disable the filter

## Reproducer

`/tmp/.../scratchpad/repro25.py`, against `main` at 53458e5. Two findings and two
requirements in an in-memory DB:

```
severity=0        -> 2 rows   <-- SILENT FULL QUEUE   (expected: ValueError)
severity=False    -> 2 rows   <-- SILENT FULL QUEUE
severity=[]       -> 2 rows   <-- SILENT FULL QUEUE
status=0          -> 2 rows   <-- SILENT FULL QUEUE
priority=0        -> 2 rows   <-- SILENT FULL QUEUE   (reqs)
reqs status=0     -> 2 rows   <-- SILENT FULL QUEUE
severity=None     -> 2 rows   (correct: "no filter", documented)
severity=""       -> 2 rows   (correct: "no filter", documented)
```

## Root cause

Every vocabulary query filter guards with a **truthiness** test — `if severity:` —
which conflates three different things:

| value | meaning | today |
|---|---|---|
| `None` | not supplied | no filter ✔ |
| `""` | documented empty-filter convention | no filter ✔ |
| `0`, `False`, `[]`, `{}` | **wrong input** | no filter ✘ |

CB-19 put the refusal of non-string input inside `types._resolve`, but a falsey value
never reaches it — the guard short-circuits first. The caller asks for a filtered queue
and gets the whole table, which is indistinguishable from a correctly-filtered one. Same
silent shape CB-19 was filed against, arriving through the guard rather than the resolver.

## Evidence (every line read directly this session)

Bug sites — resolver present, bypassed by the truthy guard:

- `src/codebugs/findings.py:425` `if status:` → `resolve_finding_status`
- `src/codebugs/findings.py:428` `if severity:` → `resolve_severity`
- `src/codebugs/reqs.py:316` `if status:` → `resolve_requirement_status`
- `src/codebugs/reqs.py:319` `if priority:` → `resolve_priority`
- `src/codebugs/embeddings.py:101` `if status:` → `resolve_requirement_status`

Sibling found by the sweep, same conflation one layer up:

- `src/codebugs/provenance.py:126` `query_kwargs["status"] = status if status else "open"`
  — so `check_findings(status=0)` silently reports on **open** findings.

Correct already, do not touch — these use `is not None`, which is the shape being adopted:

- `findings.py:289,291` (`update_finding`), `reqs.py:196,198` (`update_requirement`)

## Blast radius (measured, not assumed)

- **MCP: not reachable.** The wrappers type these `str | None`; pydantic's default lax
  mode still rejects `int`/`bool`/`list`/`dict` for `str` — verified by constructing the
  model directly, all four raise `ValidationError`.
- **CLI: not reachable.** argparse yields `str` or `None`.
- **Direct Python API: reachable**, including the internal `provenance.py` caller above.

So `low` severity is right, and the card's implied "all clients" reading is too wide.
Recording this because the card will otherwise be re-read as broader than it is.

## Plan (revised after cross-model review — two findings were mine to own)

1. `types.py` — one predicate, next to the resolvers it protects. **Type-based, and it
   must never invoke equality on the value:**

   ```python
   def is_vocabulary_filter_active(value: object) -> bool:
       if value is None:
           return False
       if isinstance(value, str):
           return str.__len__(value) != 0
       return True          # wrong type — active, so the resolver raises
   ```

   The obvious `value is not None and value != ""` is **wrong** and was rejected: `!=`
   runs arbitrary user code. Verified this session — `unittest.mock.ANY != ""` is `False`
   while `bool(ANY)` is `True`, so that predicate would flip `ANY` from *raises today* to
   *silently no filter*, and a `str` subclass overriding `__ne__` would do the same to a
   perfectly valid `"open"`. `str.__len__` rather than `len()` for the same reason.
   The name is scoped on purpose: it must never be reached for on `ids=[]` / `tags=[]`.

   One definition, not seven inline copies. CB-22's lesson is explicit that a check
   *duplicated* rather than *shared* is one drift away from disagreeing with itself.

2. Replace the guard at the five bug sites with it. The resolver call is unchanged, so
   garbage now reaches it and raises `ValueError` as CB-19 intended.

3. `provenance.py:126` — `status if is_vocabulary_filter_active(status) else "open"`.
   Note its contract differs from the five: `None`/`""` mean **default to open**, not
   "full queue", so its tests assert that and not the domain-site behaviour.

4. **`merge.py:372` and `milestones/foundation.py:102,105` are IN scope after all.** My
   first draft excluded them claiming they fail "silent empty, not silent full". That was
   wrong: for *falsey* values the guard short-circuits identically, so
   `get_sessions(status=0)` and `list_milestones(kind=0)` return **everything** — the exact
   CB-25 defect. Both also violate the vocabulary-both-sides rule outright:
   - milestones already declares `MILESTONE_KINDS` / `MILESTONE_STATES` (`_schema.py:10,13`)
     and validates them on **write** (`foundation.py:42,79`) but not on query.
   - merge's status vocabulary exists only inside a CHECK-constraint string
     (`merge.py:22`); extract it to a constant so the CHECK and the filter share one source.

5. Tests pinning, at every site: falsey non-strings raise; **`mock.ANY` and a custom-`__ne__`
   `str` subclass** behave correctly (these are what kill the rejected predicate — without
   them the wrong version passes every other test); `None` and `""` still mean no filter.

## Risks & out of scope

- **`ids=[]` / `tags=[]` must keep meaning "no filter"** and are deliberately untouched.
  Correction: `id IN ()` is **not** a syntax error in SQLite 3.47.1 — verified, it returns
  zero rows. So the regression from touching them would be full-queue → empty-queue, which
  is quieter than a crash and therefore worse. The scoped name of the predicate is the
  guard against this.
- **Free-text filters are untouched** — `category`, `file`, `source`, `tag`, and also
  `id`, `section`, `search`, `meta_key`/`meta_value`, `commit`, `ref`. They have the same
  truthy guard but no validator to bypass; the defect there is "no validator exists".
  Wider than my first draft said, and to be **filed as its own card**.
- **"Declared filter silently ignored" is a separate defect, also to be filed.**
  `check_findings(finding_id=..., status=0)` drops `status` entirely
  (`provenance.py:122`), and the MCP `query(status="deferred")` early return discards
  `severity` and every other filter (`findings.py:766`, `reqs.py:750`). That is the
  CB-15 family — a success payload with the caller's arguments discarded — not CB-25.
- `reqs.py:578,583` (CSV import) catches `ValueError` and falls back to a default by
  design. Untouched.

## Verification

- The reproducer above must flip from "SILENT FULL QUEUE" to `ValueError` on every row,
  while the `None` / `""` rows keep returning the full queue.
- New tests must be proven to fail against unfixed code (`git stash` the src change).
- Full suite: 793 passing on main before the change.
- `uv run ruff check src/ tests/`.
