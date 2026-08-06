# Milestones split — recommended design (for adversarial review)

**Status:** draft for adversarial review
**Candidate #3** from the improve-codebase-architecture pass: deepen `src/codebugs/milestones.py` (1718 lines, 18 MCP tools, 7 CLI commands, 4 tables).

## Goal

Turn the leakiest module in the codebase into narrow, independently-testable units **without changing any external behavior**. The deepening payoff must be measured in *testability*, not file count.

## The actual friction (the thing the change must fix)

Two specific eligibility branches are awkward to test in isolation today (citations by symbol; line numbers are advisory and will move during the carve):

- `_eligibility_failure` — the `linked_frs` loop does raw `SELECT 1 FROM requirements`, so testing the "large bug in a release must declare resolvable FRs" rule requires a `requirements` table + rows for every case in the matrix.
- `_has_active_blocker` wraps `blockers.query_blockers` in `try/except Exception: return False`. That fail-soft **conflates "no active blocker" with "no blockers schema present"** — so a test that wants to assert *an item WITH an active blocker is ineligible* cannot reach that path without standing up the blockers schema; the swallow masks it.

Note what is *already* testable and does **not** motivate the seam: the capacity-floor math in `_eligibility_failure` (`cap = capacity.get(size,0); used = held.get(size,0); if used >= cap`) is pure dict arithmetic on the 4 milestones tables; `pull_next`'s `BEGIN IMMEDIATE` claim and `milestone_close`'s gate operate only on milestones tables; and `_auto_route_finding` already guards with a `sqlite_master` schema-probe. The seam is therefore scoped narrowly (see below), not a sweeping "buried behind I/O into three domains" claim.

## Hard constraints (a wrong change breaks production)

1. **Public surface frozen.** MCP tool names and CLI command names are called *by name* from autosorter's `worktree-setup.sh` / `worktree-finish.sh`. Internal refactor only — `register_tools` / `register_cli` must emit byte-identical names. **Tool identity has two mechanisms, both load-bearing:**
   - **10 tools** carry an explicit `@mcp.tool(name=...)` kwarg — these are the spec-canonical names: `triage_inbox`, `triage_dismiss`, `triage_promote`, `pull_next`, `release_item`, `wip_status`, `mark_branch_only`, `mark_integrated`, `milestone_close`, `milestone_defer`.
   - **8 foundation tools** use a **bare `@mcp.tool()`** — FastMCP derives the wire name from the **inner `def` name**: `milestone_create`, `milestone_update`, `milestone_list`, `milestone_status`, `milestone_add_item`, `milestone_move_item`, `milestone_set_status`, `milestone_audit_query`. Renaming any of these inner functions during the move **silently changes the wire name**. They must be preserved verbatim (or normalized to explicit `name=` — but only *after* the regression gate below exists).
   - **CLI identity** is the `add_parser("milestone-list", ...)` subcommand string (the `commands` dict key matches by convention only; the argparse subcommand name is what callers type).
2. **Schema is one interrelated unit.** 4 tables, FKs between them (`milestone_items.milestone_id → milestones.id`; audit references both), plus `SEED_MILESTONES`. Stays ONE `MILESTONES_SCHEMA` string under ONE `register_schema("milestones", ensure_schema, depends_on=("findings","reqs","blockers"))`.
3. **No new circular imports.** Cross-domain reaches into blockers/findings/reqs are currently late imports inside functions to dodge cycles.
4. **`_ensure_modules_loaded()` keeps working.** It does `import codebugs.milestones`; a package named `milestones` resolves identically, so the import trigger and `depends_on` ordering are preserved.

## Recommended design: package skeleton + a surgical test seam

Convert `milestones.py` into a `milestones/` package:

```
src/codebugs/milestones/
  __init__.py     # facade: imports the context modules, then the 4 register_* calls VERBATIM
  _schema.py      # MILESTONES_SCHEMA, SEED_MILESTONES, constants, ensure_schema  (ONE schema owner)
  _spine.py       # _audit, _validate_item_ref, _get_item_by_ref, _milestone_exists,
                  #   _get_milestone, _row_to_milestone/_item/_audit
  foundation.py   # create/update/list_milestones, get_milestone_status,
                  #   add/move_milestone_item, set_item_status, query_audit
  triage.py       # triage_inbox/dismiss/promote, _auto_route_finding (post-add hook)
  capacity.py     # THE DEEP ENGINE: _capacity_for/_upsert/_decrement, _eligibility_failure,
                  #   _bucket_query, _candidates, pull_next, release_item, get_wip_status
  closegate.py    # mark_branch_only, mark_integrated, milestone_defer, milestone_close
```

Topology is a star: every context imports `_spine` and `_schema`; no context imports another. `_spine` imports only stdlib + `codebugs.types` at module level (matching today's `milestones.py`, whose sole top-level import is `from codebugs.types import utc_now`). The `register_*` functions and `db.connect` stay **bottom-of-`__init__` / late-inside-CLI-closures**, exactly as the current module does them — do **not** add a top-level `import codebugs.db` to `_spine`.

The real import invariant to preserve (NOT "avoiding a circular import" — there is none; `blockers`/`findings`/`reqs` never import `milestones`): the late `from codebugs import blockers` / `findings` / `reqs` calls exist to avoid **eager cross-domain loading** and to respect the schema registry's `depends_on` load ordering (`db._ensure_modules_loaded()` is the only importer of `milestones`, and it runs inside `connect()` after `db`'s own module body has finished). The split must keep every cross-domain reach late and must add no new top-level sibling/`db` imports.

### The surgical seam (the only part of D we adopt)

`capacity.py`'s eligibility logic is refactored so the **two** cross-domain reads are reached through thin injected accessors, defaulting to the real implementations. The accessors are added as **keyword-only** parameters that preserve the real positional signature (`conn, item, milestone, capacity, held` — verified at the call site in `pull_next`):

```python
# capacity.py — real signature preserved; only the two foreign reads become injectable.
def _eligibility_failure(conn, item, milestone, capacity, held, *,
                         has_active_blocker=_real_has_active_blocker,
                         requirement_exists=_real_requirement_exists) -> str | None:
    ...
```

- Production: `pull_next` keeps calling `_eligibility_failure(conn, item, milestone, capacity, held)` (5 positionals, defaults bind to the real blocker/requirements reads, still late-imported inside the real impls). **The capacity-floor branch (`cap = capacity.get(size,0); used = held.get(size,0)`) stays inline** — it needs no sibling schema and is not injected.
- Tests: pass `has_active_blocker=lambda ref: True/False`, `requirement_exists=lambda id: True/False` to drive the eligibility matrix with **no blockers/reqs schema present**, including the positive-blocker case that the real `try/except: return False` would otherwise mask.

This is NOT full ports & adapters: no `Protocol` classes, no adapter layer, no injection into foundation/triage/closegate. Exactly **two** accessors, scoped to the only two reads that today require a sibling fixture or hide behind a fail-soft swallow. `_validate_item_ref` (used widely, low-risk) stays a plain `_spine` helper and is **not** injected.

### Re-export manifest (mandatory — keeps the `codebugs.milestones.<name>` namespace stable)

The in-repo test suite reaches package attributes directly, so `__init__.py` MUST re-import every symbol any caller touches — not "re-imports" hand-waving. Verified consumers in `tests/test_milestones.py`:

- `milestones._get_item_by_ref` — 6 call sites. Re-export from `_spine` (or promote to public).
- `milestones.AUTO_ROUTER_ACTOR` — 2 call sites. Re-export from wherever the constant lands (`_schema` or `triage`).
- the full public function set (`create_milestone`, `update_milestone`, `list_milestones`, `get_milestone_status`, `add_milestone_item`, `move_milestone_item`, `set_item_status`, `query_audit`, `triage_inbox/dismiss/promote`, `pull_next`, `release_item`, `get_wip_status`, `mark_branch_only`, `mark_integrated`, `milestone_defer`, `milestone_close`).

Add a **guard test** that imports each name from `codebugs.milestones`, so a missing re-export fails at collection rather than mid-suite.

### What stays exactly the same

- `__init__.py` ends with the verbatim block:
  ```python
  register_schema("milestones", ensure_schema, depends_on=("findings","reqs","blockers"))
  register_tool_provider("milestones", register_tools)
  register_cli_provider("milestones", register_cli)
  register_post_add_hook("milestones.auto_route", _auto_route_finding)
  ```
- `register_tools` / `register_cli` fan out to per-context registration helpers but emit identical tool/command names.
- `_ensure_modules_loaded`, `server.py`, `cli.py`, `SERVER_NAMES`, `--mode` allowlist: unchanged.
- One schema, one `register_schema`. No `--mode` sub-splitting.

## Testing strategy

- **New boundary tests:** eligibility-rule matrix (status, blocker, acceptance-for-large, `linked_frs` resolution) driven through `_eligibility_failure` with fake accessors — no sibling schemas. Capacity math (`_capacity_for`/increment/decrement floor) through `pull_next`/`release_item`/`get_wip_status` on the 4 milestones tables only. Close-gate matrix through `milestone_close`.
- **Old tests to delete/replace:** redundant `tests/test_milestones.py` cases that stand up findings+reqs+blockers *solely* to exercise eligibility become fake-driven engine tests.
- **Regression guard (the load-bearing test):** asserts the *exact* 18 emitted MCP tool names (10 explicit `name=` + 8 derived from inner `def` names) AND the 7 argparse subcommand strings after registration. This is the one test that catches a bare-`@mcp.tool()` inner-`def` rename, which no prose rule will. It is migration **step 0** — it lands before any file moves.

## Migration (git-split friendly, strangler-fig order)

**Value/risk ordering (per adversarial review): regression gate → seam → file moves.** The seam is the high-value half; the file split's entire risk surface is the frozen tool names, so the name-regression test must exist *before* the first `git mv`.

0. **Frozen-surface regression test FIRST (hard gate).** Before touching any file, add a test that asserts the *exact* 18 emitted MCP tool names (10 `name=` + 8 derived-from-`def`) and 7 argparse subcommand strings after registration. This catches the bare-`@mcp.tool()` rename hazard that prose rules miss. Nothing below merges if this is red.
1. **Introduce the injected-accessor seam** in `_eligibility_failure` (still in the current single-file module). Behavior-preserving: defaults bind to the real reads. Add the fake-driven eligibility matrix tests + the positive-blocker case. This delivers most of the testability win independent of any file move.
2. `git mv src/codebugs/milestones.py src/codebugs/milestones/__init__.py` (one commit, no logic change — preserves blame on the whole file).
3. Move `_schema.py` constants/schema out of `__init__`.
4. Move `_spine.py` helpers.
5. Move `foundation.py`, `triage.py`, `capacity.py`, `closegate.py` one commit each.

**Hard migration invariant:** every commit that moves a symbol out of `__init__.py` MUST, *in the same commit*, re-import that symbol into `__init__.py` (per the re-export manifest) so `codebugs.milestones.<name>` stays resolvable and `db._ensure_modules_loaded()` + the full suite stay green. No intermediate red commits. This applies to steps 3, 4, and 5 — not just the last move.

Each step keeps `uv run python -m pytest tests/ -v` green.

## Why not the alternatives

- **A alone (pure facade):** reorganizes files but, per its own author, leaves testability unmoved and re-exports the full function surface — nominal deepening only.
- **B (full self-registration, 4 first-class modules):** 10 registration calls + registry/`--mode` sprawl for contexts that don't exist yet; violates "each module owns its schema" anyway since `_core` owns it.
- **C (WorkDispatcher class):** introduces a class + frozen free-function wrapper dual ABI and duplicates item-status mutation across the hot/cold seam.
- **D alone (full P&A):** port ceremony across the whole module; pays off only if every fake-driven test gets written. We take the 20% of D (one injected seam) that yields 80% of the testability win.

## Resolved questions (closed by adversarial review)

1. **Post-add hook contract** — moving `_auto_route_finding` into `triage.py` while registering it from `__init__` is safe; the `sqlite_master` schema-probe guard exists and is preserved. ✅
2. **Single-schema-owner** — defensible. A `register_schema` owner is a *domain*; the 4 tables share FKs (`milestone_items.milestone_id REFERENCES milestones(id)`) + `SEED_MILESTONES` + one `ensure_schema`. They are one domain expressed across files. Exactly one schema owner remains (`_schema.py`); no sub-module calls `register_schema`. CLAUDE.md's rule is satisfied. ✅
3. ~~Seam inside `BEGIN IMMEDIATE`~~ — **non-issue, removed.** Eligibility reads are `SELECT`-only and the fakes do zero I/O; the new tests call `_eligibility_failure` directly, outside any transaction. No concern.

## Open question (real, for the implementer)

- Splitting `register_cli` across context files may duplicate the late `db.connect()` import in each `cmd_*` closure. Acceptable as-is, or extract a tiny `_cli.py` helper. Defer the call to implementation.

## Adversarial Review Corrections (2026-06-18)

A 3-agent adversarial review (adversary → defender → judge) stress-tested this spec against the real codebase. **Design Health: 7/10 — fix mandatory items, then ship.** The approach (package split + one schema owner + minimal 2-accessor seam) took no structural hits; every confirmed finding was a defect in how the spec *described* the approach — the kind that would mislead a faithful implementer into breaking production. All fixed above:

| Finding | Ruling | Correction applied |
|---|---|---|
| Seam signature was 3-arg; real `_eligibility_failure` is 5-positional (`conn, item, milestone, capacity, held`) | was FATAL → spec-text fix | Rewrote seam to keep 5 positionals + 2 keyword-only accessors; capacity-floor stays inline. |
| Frozen-surface rule wrong for 8/18 tools (bare `@mcp.tool()` derives name from inner `def`) | was FATAL → spec-text fix | Constraint #1 now documents both mechanisms; regression test promoted to **step 0 hard gate**. |
| Tests touch `_get_item_by_ref` (6×) + `AUTO_ROUTER_ACTOR` (2×) directly | SERIOUS | Added mandatory re-export manifest + import guard test. |
| Spec claimed `_spine` imports `codebugs.db` at top level | WEAKNESS | `_spine` is stdlib + `codebugs.types` only; `db`/`register_*` stay late/bottom as today. |
| "cycle-dodge sites" / "acyclic by construction" — no cycle actually exists | WEAKNESS | Reworded: late imports avoid eager cross-domain load + respect `depends_on` ordering; stated the true invariant. |
| Seam over-claimed ("buried behind I/O into three domains") | WEAKNESS | Scoped to the 2 branches that genuinely need it (`linked_frs` read; positive-blocker case masked by `except: return False`). |
| Open-question #3 self-answering | NITPICK | Removed. |
| Single-schema-owner under-argued | NITPICK | Closed explicitly (one schema owner = one domain across files). |
| Line-range drift | NITPICK | Citations now by symbol; line numbers advisory. |
| Migration re-import must be same-commit | NITPICK | Added hard migration invariant. |

**Meta-verdict:** splitting `milestones.py` is the *correct* change, not over-engineering — but the **eligibility seam is the high-value half** and the file split's only real risk is the frozen tool names. Sequence is therefore regression-gate → seam → file moves. If forced to ship only one thing, ship the seam.
