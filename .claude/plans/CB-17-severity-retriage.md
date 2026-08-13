# CB-17 — a finding's severity is immutable after filing

**Card:** CB-17 (medium, filed 2026-08-13 by the human author, `src/codebugs/findings.py`)
**Branch:** `fix/cb-17-severity-retriage` off `feature/entity-claims` (b579963)

## The technically correct fix, stated before any feasibility filter

Severity is the tracker's ranking input. It is necessarily assigned when the least is known
about a finding, and evidence arrives later. The correct behaviour is that re-triage is a
first-class, in-place edit of the structured field — exactly as the sibling entity already
does with `priority` — so that the queue never lies to a reader and the correction never has
to be carried as prose inside a note body.

That is achievable in this tree, in this iteration, with no design round: `update_requirement`
is a worked example sitting in the same package. So the correct fix and the shippable fix do
not diverge, and no escalation artifact is needed.

## Reproducer (run before planning, `scratchpad/repro_cb17.py`)

```
1. filed severity = medium
2. update_finding(severity=) raises TypeError: unexpected keyword argument 'severity'
3. severity after attempted retriage = medium
4. add_finding(severity='High') REJECTED: Invalid severity: High
5. resolve_priority('Must') -> must
```

Lines 1–3 are the card. Lines 4–5 are a **second, independent asymmetry** found while
reproducing (see "Out of scope").

## Root cause

`update_finding` (`findings.py:235-245`) accepts `status, notes, append_note, tags,
meta_update, reported_at_ref`. There is no `severity`, and there is no UPDATE path to that
column anywhere in the package — grep finds `severity` only in the schema/insert
(`findings.py:18,148,157`), in query filters, and in `stats`.

The sibling proves this is an oversight rather than a design choice:
`update_requirement` (`reqs.py:201-204`) accepts `priority`, validates it with
`resolve_priority()`, and appends `priority = ?` to the same kind of built `SET` list.

The card's other complaint — that an unknown kwarg was silently absorbed at the MCP
boundary, so the failed retriage returned a success-shaped payload — **is already fixed**
by `server.install_strict_arguments()` (CB-15, `d6ce8de`). That half needs verification,
not work.

## Plan

Fix shape (a) from the card, which its own author recommends.

1. **`findings.py` — `update_finding`**: add a keyword-only `severity: str | None = None`.
   Validate against `SEVERITIES` with the *same* strictness `add_finding` already uses
   (`:148-149`), then append `severity = ?` to the shared `updates` list.
2. **`findings.py` — MCP `update` wrapper** (`:594`): declare and forward `severity`.
   Without this the parameter is unreachable from the MCP surface, and — post-CB-15 —
   passing it would now be *refused* rather than dropped.
3. **`findings.py` — CLI**: `--severity/-s` on the `update` parser (`:1000`) and forward it
   in `_cmd_update` (`:773`).
4. **Golden schema**: regenerate `tests/golden/mcp_schema.json`.
5. **Tests** in `tests/test_findings.py`: retriage up and down; invalid value raises and
   leaves the row untouched; severity composes with a status change in one call without
   either being lost; and a structural guard that the built `SET` clause contains
   `severity = ?` exactly once.

## Risks, and the traps named by the cross-model pass

- **Re-introducing CB-16.** The sharpest risk: this same function was just fixed for a bug
  where two branches each appended their own assignment to the built `SET` clause and
  silently clobbered each other. `severity` must append to the **shared** `updates` list,
  exactly once, and must **not** live inside the `meta` block. Pinned by a template-level
  guard test using the existing `RecordingConnection`.
- **The CHECK constraint is a backstop, not the validator.** `severity TEXT NOT NULL
  CHECK(severity IN (...))` would catch a bad value with an `IntegrityError` after the
  write is attempted. Validate in Python first so the caller gets `ValueError`, matching
  `add_finding` and the project's stated error contract.
- **Status-hook coupling.** `run_status_change_hooks` fires on `status is not None and
  rowcount == 1 and status != old_status`. Adding a column must not widen that condition —
  a severity-only update fires no hook. Covered by a test.
- **No audit hook.** Deliberately not built: nothing consumes a severity-change event, and
  a speculative hook family enlarges the semantics for no gain. Recorded here so the
  omission is a decision rather than an oversight.

## Out of scope, filed separately

`types.py` defines `SEVERITIES` but has **no `resolve_severity()`**; `findings.py` does an
ad-hoc `if severity not in SEVERITIES: raise` at `:148` and `:194` which does not
`lower()/strip()`. So `add_finding(severity="High")` raises while `resolve_priority("Must")`
returns `must` — findings are case-strict, requirements case-lenient.

Same family, but fixing it **widens `add_finding`'s accepted input set**, which is a
behaviour change beyond closing CB-17 and carries its own regression surface. Filed as its
own card. This tree therefore validates severity with the strictness the field *already*
has at insert, so findings stay internally consistent; the new card proposes moving insert
and update onto a shared `resolve_severity()` together.

## Cross-model review of the finished diff (Codex / gpt-5.6-sol)

Run on the diff rather than on the plan: the design question had already gone to Codex
(which is what produced the "file the case-asymmetry separately" decision above), and the
last iteration's real regression was caught at the diff stage, not the plan stage.

Verdict: domain and MCP plumbing correct, CB-16 not reintroduced, the structural guard is
real rather than vacuous. It found **one regression I had introduced** and **two gaps that
would have let a broken implementation ship**:

1. **Regression, fixed.** My CLI `except (KeyError, ValueError)` swallows
   `json.JSONDecodeError`, which subclasses `ValueError`. On a row with malformed stored
   meta, `update_finding` commits the severity write and *then* raises during result
   serialization — so the CLI would print a clean "invalid input" error and exit 1 for a
   write that already landed. Now re-raised in an arm ahead of the ValueError arm, with a
   test that fails against the broad catch.
2. **Gap, closed.** The MCP test used a recorder for `call_next`, so it proved the argument
   was *declared*, never *forwarded* — deleting `severity=severity` from the wrapper would
   have passed it and the golden schema both. Added a test that drives the real tool and
   reads the row back; verified it fails under exactly that mutation.
3. **Gap, closed.** Every fixture held one row, so an implementation with a missing `WHERE`
   would have passed all 12 tests. Added a bystander row and asserted it is untouched.
4. **Also fixed:** `test_the_escalation_is_durable` read back through the same connection,
   so it would pass with `conn.commit()` removed. It now reopens the database.
5. **Added:** a real CLI contract class (`TestRetriageCliContract`) driving `codebugs update`
   as a subprocess — long flag, short flag, invalid value, and the committed-write case.

Codex also flagged, and I filed rather than fixed:
- **CB-20** — `ORDER BY severity` is lexical, so `low` sorts above `medium` in
  `query_findings` and `get_summary`. Pre-existing, verified by running SQLite directly, and
  it undermines this card's own "severity ranks the queue" premise one layer down.

Not acted on, deliberately: it noted `test_escalate`/`test_downgrade` are subsumed by
`test_every_canonical_severity_is_reachable`, and that a fully generic "every SET column is
assigned once" guard would encode the CB-16 rule more completely than a per-column one.
The first is harmless redundancy; the second is a genuinely better guard but is a change to
shared test infrastructure, not to this card.

## Verification

- New tests fail against the unfixed code (`git show feature/entity-claims:src/...`).
- `uv run python -m pytest tests/ -v`
- `uv run ruff check src/ tests/` and `ruff format --check`
- Golden schema diff shows exactly the `severity` addition to `update`.
- MCP surface exercised for real, not just the domain function.
