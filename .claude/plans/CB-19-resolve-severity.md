# CB-19 — severity gets a resolver, like every other vocabulary

**Card:** CB-19 (low, api_parity, `src/codebugs/types.py`) — severity is case-strict while the
sibling priority is case-lenient; `types.py` has no `resolve_severity()`.

## Reproducer

```
add_finding(severity="High")  -> ValueError: Invalid severity: High
resolve_priority("Must")      -> "must"
```

Severity is the only vocabulary in `types.py` without a resolver. `FINDING_STATUSES`,
`REQUIREMENT_STATUSES` and `PRIORITIES` all have one; each delegates to `_resolve()`
(`types.py:74`), which does `.lower().strip()` plus an optional alias dict.

## Root cause

No `resolve_severity`, so `findings.py` open-codes `if severity not in SEVERITIES: raise` at
three sites, and the CLI CSV import open-codes `.strip().lower()` at a fourth.

## THE FIFTH SITE — found by Codex/Sol, not by the card

`query_findings` resolves `status` and does **not** resolve `severity`, two lines apart:

```python
if status:
    params.append(resolve_finding_status(status))   # findings.py:391
if severity:
    params.append(severity)                          # findings.py:394  <-- raw
```

This matters twice over:

1. **It is already a live defect.** `query(severity="HIGH")` silently returns zero rows today,
   while `query(status="OPEN")` on the adjacent line works.
2. **Fixing only the write paths would CREATE a silent wrong answer**: `add(severity="High")`
   would store `high`, and `query(severity="High")` would return nothing. Writing the card's
   "route ALL THREE sites" as written ships that bug.

Routing query through the resolver also makes `query(severity="banana")` raise instead of
returning empty — which is exactly what `status` on the adjacent line already does.

## The five sites

| # | Site | Today |
|---|---|---|
| 1 | `add_finding` `findings.py:155` | `if severity not in SEVERITIES: raise` |
| 2 | `batch_add_findings` `:201` | same, different message |
| 3 | `update_finding` `:287` | same (added by CB-17) |
| 4 | `query_findings` `:394` | **raw, unvalidated** — Codex's find |
| 5 | `_cmd_import_csv` `:977` | `.strip().lower()` inline, becomes redundant |

## This relaxes a DELIBERATELY PINNED contract — and that is sanctioned

`tests/test_findings.py:505` asserts `"HIGH"` raises, with the docstring: *"``HIGH`` also pins
the case-strictness that CB-19 proposes to relax, so relaxing it cannot happen silently."*

That is a tripwire authored in anticipation of this card, not a contract to defend. It fired
(via the Codex pass) and is being retired deliberately, with the changelog updated. Two other
places assert the old contract and must move with it:

- `CHANGELOG.md:30` — "Validation is exact-match lowercase… unlike `status`, severity has no aliases."
- The MCP tool docstring `findings.py:655` — "Exact lowercase only — unlike status, severity has no aliases."

**Still true after this change:** severity has no *aliases*. Only case/whitespace changes.
The docs must say that precisely, not "severity is now lenient".

## Plan

1. `types.py` — `resolve_severity(severity)` = `_resolve(severity, SEVERITIES, None, "severity")`,
   mirroring `resolve_priority` one for one. No aliases (no evidence of callers passing
   "crit"/"P0"; the card says add them only on evidence).
2. Route all five sites. Routing some re-creates the asymmetry one layer down.
3. Update the tripwire test to pin the NEW contract (case-insensitive accepted, garbage still
   raises), the CHANGELOG, and both MCP docstrings.

## Risks & out of scope

- **Widening a public API.** `add_finding(severity="High")` goes from raising to succeeding. No
  caller can depend on a rejection; the DB CHECK only constrains stored values, which stay
  canonical. Codex confirmed no MCP schema enum or SQL invariant relies on the strictness.
- **Out of scope:** aliases; the `requirements` twin's query path, which leaves `priority` and
  `status` raw — that is CB-6/CB-21 territory (surface parity), and touching it here would widen
  this card into the enumeration problem those cards exist to gate.

## Verification

- Reproducer flips: `add_finding(severity="High")` returns a finding stored as `high`.
- Round-trip: write mixed-case at each of the four write/read routes, assert the STORED value is
  canonical and that querying by mixed case finds it.
- Mutation that must fail a test (Codex's warning): restore `add_finding`'s old
  `if severity not in SEVERITIES` check. A CSV-uppercase test would NOT catch it, because CSV
  already lowercases inline — so each CRUD route needs its own direct test.
