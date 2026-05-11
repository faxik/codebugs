# Milestones & Streams — Implementation Plan

**Spec source:** `~/w/autosorter/.claude/plans/codebugs-milestones-streams-v1.md` (approved)
**Repo:** `~/w/codebugs/`
**Date:** 2026-05-11
**Strategy:** new self-registering domain module `milestones.py`, three commits (one per spec phase).

---

## 1. Module layout

New file `src/codebugs/milestones.py` — single domain module following the established pattern (cf. `blockers.py`, `sweep.py`):

- module-level `MILESTONES_SCHEMA` string with CHECK constraints (see §2)
- `ensure_schema(conn)` creates tables + seeds the 4 required rows (idempotent `INSERT OR IGNORE`)
- `register_schema("milestones", ensure_schema, depends_on=("db", "reqs", "blockers"))` — depends on `db` (findings) for phantom-ID checks, on `reqs` (requirements) for FR-row linking, and on `blockers` because `pull_next` reads blocker rows
- public Python API (functions) for each operation
- `register_tools(mcp, conn_factory)` thin wrappers
- `register_cli(sub, commands)` for shell-callable subset
- registration calls at module bottom

Add `milestones` to `db._ensure_modules_loaded()` import list (the only edit to `db.py` aside from the post-add hook below).

**Server mode flag**: add `"milestones": "codemilestones"` to `SERVER_NAMES` in `server.py`, add `"milestones"` to `--mode` choices in `cli.py`. (Both files already use registry-driven dispatch; only the mode allowlist is local.)

Test file `tests/test_milestones.py` — file-based `tmp_path` DBs because schemas span findings + reqs + milestones + blockers.

---

## 2. Schema (4 tables)

Exactly per spec §5, with the following additions / clarifications:

### 2.1 `milestones` — per spec + CHECK constraints
Add CHECK constraints (codebase discipline):
- `CHECK(kind IN ('release','stream'))`
- `CHECK(state IN ('open','closing','shipped','archived'))`

Seed rows on `ensure_schema`:
- `stream/triage`, `stream/maintenance`, `stream/security` (`kind=stream`, `state=open`)
- `release/1.1` (`kind=release`, `state=open`)

Use `INSERT OR IGNORE` keyed on `id` so re-initialization is safe.

### 2.2 `milestone_items` — per spec + extensions

| extension | reason |
|---|---|
| `meta_json TEXT NOT NULL DEFAULT '{}'` | required to store `linked_frs` (§7.2 eligibility). Spec §5.2 omits this; meta_json is the lowest-commitment extension. |
| `CHECK(item_kind IN ('bug','requirement','external'))` | match codebase CHECK discipline (cf. `db.py:297-302`, `blockers.py:18-24`). |
| `CHECK(status IN ('open','in_progress','done','deferred','dismissed'))` | same. |
| `CHECK(size IN ('large','small','triage'))` | same. |

The `linked_frs` field is a JSON array of FR IDs (e.g. `["FR-123"]`) stored inside `meta_json`. Helper `_get_linked_frs(item)` reads it.

Unique constraint `(milestone_id, item_kind, item_ref)`. Reverse-lookup (which items link FR-X) deferred — codebase precedent for JSON-in-meta (`reqs.meta`) accepts this trade.

Indexes:
- `idx_mi_milestone_status` on `(milestone_id, status)`
- `idx_mi_ref` on `item_ref`
- `idx_mi_assigned` partial: `ON milestone_items(assigned_agent) WHERE assigned_agent IS NOT NULL`

**Terminal status set** (module-level constant): `MILESTONE_ITEM_TERMINAL = frozenset({"done", "dismissed"})`. Used by `pull_next` eligibility and close-gate.

### 2.3 `milestone_audit` — per spec exactly

Indexes: `idx_audit_milestone_at`, `idx_audit_item_ref`.

### 2.4 `agent_capacity` — per spec exactly (Phase 2)

---

## 3. Tool surface (20 MCP tools, mapping to spec §6)

Tool names use spec names verbatim (some break the "domain prefix" convention; see §10 below). Acceptable because the spec is the contract and tools like `pull_next`/`release_item` are more readable unprefixed.

| Phase | Tool | Notes |
|---|---|---|
| 1 | `milestone_create` | `kind ∈ {release, stream}` |
| 1 | `milestone_update` | partial update, only `description`/`target_date`/`state` mutable; immutable `id`/`kind`/`created_at` |
| 1 | `milestone_list` | filters: `kind`, `state` |
| 1 | `milestone_status` | counts by status/size, blockers, branch_only count, target-date countdown |
| 1 | `milestone_add_item` | enforces phantom-ID check (§7.5), acceptance required for `size=large` |
| 1 | `milestone_move_item` | rewrites the row, writes audit row with `action=move` |
| 1 | `milestone_set_status` | sets `done_commit` if `commit` given and status terminal |
| 1 | `milestone_audit_query` | filters: milestone, item, actor, since |
| 2 | `triage_inbox` | items in `stream/triage` ordered oldest-first |
| 2 | `triage_dismiss` | one-line reason mandatory; sets `status=dismissed` on milestone_item; propagation branches on `item_kind`: `bug` → finding `status='not_a_bug'`; `requirement` → requirement `status='obsolete'`; `external` → no propagation |
| 2 | `triage_promote` | moves from `stream/triage` to target milestone; acceptance required for `size=large` |
| 2 | `pull_next` | full eligibility chain — see §4 below |
| 2 | `release_item` | frees `agent_capacity`, sets `done_commit` if applicable |
| 2 | `wip_status` | snapshot of `agent_capacity` |
| 3 | `mark_branch_only` | sets `branch_only=1`, stores branch name in `meta_json.branch` |
| 3 | `mark_integrated` | sets `branch_only=0`, `done_commit=…`, `status=done` |
| 3 | `milestone_close` | close gate per §7.3, `force=True` override logged to audit |
| 3 | `milestone_defer` | move to `stream/maintenance` (or other) with reason |

**Subset exposed as CLI commands** (most useful interactively, using flat `domain-action` pattern per `sweep-create`, `blockers-add`, `bench-import`): `milestone-list`, `milestone-status <id>`, `triage-inbox`, `wip-status`, `milestone-audit <id>`. Mutating ops stay MCP-only in v1.

---

## 4. `pull_next` eligibility (§7.2)

**Cursor-driven, row-by-row.** Capacity is a Python dict; blocker checks are per-row Python calls. Not a single SELECT.

Outer flow:

```python
def pull_next(conn, *, agent_id, capacity):
    # Save and clear isolation_level (merge.py:239-289 pattern)
    saved = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            item = _find_eligible(conn, agent_id, capacity)
            if item is None:
                conn.execute("ROLLBACK")
                return None
            _claim(conn, item, agent_id)
            conn.execute("COMMIT")
            return item
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = saved
```

`_find_eligible(conn, agent_id, capacity)` iterates candidates in four ordered buckets:

1. `stream/security` items, ordered by `priority ASC, created_at ASC`.
2. `release/*` items: join with `milestones` ORDER BY `milestones.target_date ASC NULLS LAST, milestone_items.priority ASC, milestone_items.created_at ASC`.
3. `stream/triage` items.
4. `stream/maintenance` items.

Each bucket is one `SELECT` returning a cursor; loop in Python over rows. For each candidate:

- `status == 'open'`
- If `item_kind != 'external'`: no active blockers (call `blockers.query_blockers(conn, item_id=item_ref, active_only=True)["blockers"]` is empty — `query_blockers` returns a `dict`, not a list). For `item_kind=='external'`, skip blocker check (`blockers._detect_entity_type` raises on non-CB/FR IDs).
- If `size == 'large'`: `acceptance` is non-empty
- If `size == 'large' AND item_kind == 'bug' AND milestone.kind == 'release'`: `meta_json["linked_frs"]` is non-empty AND each FR exists in `requirements`
- Agent's `capacity[size]` > current `agent_capacity.<size>_held` for this agent

First passing row wins. Returns the row dict (or `None`).

`_claim` updates `milestone_items` (status, assigned_agent, pulled_at), upserts `agent_capacity`, writes audit row with `action='pull'`.

---

## 5. Default routing for `codebugs.add` (§7.1)

Extension to `db.py`:

```python
@dataclass
class PostAddHook:
    name: str
    fn: Callable[[sqlite3.Connection, dict], None]

_post_add_hooks: list[PostAddHook] = []

def register_post_add_hook(name: str, fn) -> None:
    """Register a post-add hook. Name-keyed to prevent duplicate registration
    on module re-import (matches register_schema discipline at db.py:34-46)."""
    if any(h.name == name for h in _post_add_hooks):
        return  # idempotent — re-registration is a no-op
    _post_add_hooks.append(PostAddHook(name, fn))


def _run_post_add_hooks(conn: sqlite3.Connection, result: dict) -> None:
    for hook in _post_add_hooks:
        try:
            hook.fn(conn, result)
        except Exception as e:
            sys.stderr.write(f"[post-add hook '{hook.name}' failed] {e}\n")
```

**Both** `add_finding` AND `batch_add_findings` call `_run_post_add_hooks` for each row **before the final `conn.commit()`** so the finding row and the milestone_items row land atomically.

`milestones.py` registers (at module top-level):

```python
AUTO_ROUTER_ACTOR = "auto-router"

def _auto_route_finding(conn, finding: dict) -> None:
    # Defensive schema probe — raw sqlite3.connect() callers in tests/test_sweep.py
    # may invoke add_finding on a connection that didn't get milestone_items.
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='milestone_items'"
    ).fetchone()
    if not row:
        return

    sev = finding.get("severity", "")
    cat = finding.get("category", "")
    if sev == "critical" and cat.startswith("security:"):
        target = "stream/security"
    else:
        target = "stream/triage"

    # INSERT OR IGNORE — idempotent if the same finding lands twice (e.g. retry).
    conn.execute(
        """INSERT OR IGNORE INTO milestone_items
           (milestone_id, item_kind, item_ref, size, priority, status,
            acceptance, meta_json, created_at, updated_at)
           VALUES (?, 'bug', ?, 'triage', 100, 'open', '', '{}', ?, ?)""",
        (target, finding["id"], utc_now(), utc_now()),
    )
    _audit(conn, milestone_id=target, item_ref=finding["id"],
           actor=AUTO_ROUTER_ACTOR, action="create",
           from_state=None, to_state="open", reason="auto-routed")

db.register_post_add_hook("milestones.auto_route", _auto_route_finding)
```

**Note:** the auto-router does NOT call `_validate_item_ref` — the finding row was just INSERTed in the same connection (same transaction after this change), so the lookup would be tautological.

**`security:` category convention:** this plan introduces a new convention. Findings with `severity="critical"` and `category` starting with `"security:"` route to `stream/security`. Document in CLAUDE.md once §7.1 lands. Existing findings without this prefix route to `stream/triage` as before.

---

## 6. Audit invariants (§7.6)

Every state-changing function calls a single private helper:

```python
def _audit(conn, *, milestone_id, item_ref=None, actor, action,
           from_state=None, to_state=None, reason=""):
    conn.execute("INSERT INTO milestone_audit (...) VALUES (...)",
                 (milestone_id, item_ref, actor, action,
                  from_state, to_state, reason, utc_now()))
```

Callers pass `actor` explicitly. Default `"user"` only at the MCP tool boundary. Audit row is written in the same transaction as the state change.

---

## 7. Phantom-ID validation (§7.5)

Single helper:
```python
def _validate_item_ref(conn, item_kind: str, item_ref: str) -> None:
    if item_kind == "bug":
        row = conn.execute("SELECT 1 FROM findings WHERE id = ?", (item_ref,)).fetchone()
        if not row: raise ValueError(f"Unknown bug: {item_ref}")
    elif item_kind == "requirement":
        row = conn.execute("SELECT 1 FROM requirements WHERE id = ?", (item_ref,)).fetchone()
        if not row: raise ValueError(f"Unknown requirement: {item_ref}")
    elif item_kind == "external":
        return  # free-form
    else:
        raise ValueError(f"Unknown item_kind: {item_ref}")
```

Called from `milestone_add_item`, `triage_promote`, and the auto-router hook (where the bug just got inserted, so the check is a sanity assertion).

---

## 8. Close gate (§7.3)

`milestone_close(id, force=False, reason="")`:

1. Resolve milestone. **If `kind=stream` → reject with `ValueError("streams cannot be closed")` and terminate. `force` does not bypass this.**
2. Otherwise (`kind=release`), without `force`:
   - Reject if any item has `status ∈ {'open', 'in_progress'}` → name them.
   - Reject if any item has `branch_only = 1` → name them plus branch.
   - Reject if any item with `item_kind IN ('bug', 'requirement')` has unresolved blockers → name them. (Skip blocker check for `item_kind='external'` — `blockers._detect_entity_type` raises on non-CB/FR IDs.)
3. With `force=True` (release only): skip the three checks, audit `action='close', reason='force:<reason>'`, proceed.
4. Set `state='shipped'`, `closed_at=now`. Audit `action='close'`.

Error messages are actionable (list specific item refs).

---

## 9. Test plan

`tests/test_milestones.py` — fixtures use `tmp_path` (file DB, all schemas auto-register via `db.connect`). Test classes:

- `TestSchema` — tables created, seeds inserted, idempotent re-init, CHECK constraints reject bad values.
- `TestMilestoneCRUD` — create/list/update/status rollup, target-date countdown.
- `TestItemCRUD` — add/move/set_status, unique constraint handling, phantom-ID rejection (`CB-99999` rejected).
- `TestAutoRouting` — `add_finding` lands in `stream/triage`; severity=critical + category=security:* lands in `stream/security`; `batch_add` also routes; duplicate add is INSERT OR IGNORE no-op; raw `sqlite3.connect` without milestones schema doesn't crash (defensive probe works).
- `TestTriage` — inbox ordering; dismiss propagation by item_kind (bug→not_a_bug, req→obsolete, external→none); promote → milestone move.
- `TestPullNext` — priority order across all four buckets; eligibility (blocker, missing FR, missing acceptance, capacity full); `item_kind=external` skips blocker check; **threaded concurrency test** (`tests/test_sweep.py:763-790` style) — two threads, two file-DB connections, BEGIN IMMEDIATE serializes, no double-claim.
- `TestReleaseItem` — capacity decrements, `done_commit` recorded.
- `TestBranchTracking` — `mark_branch_only` + `mark_integrated` round-trip.
- `TestCloseGate` — refuses on open/in_progress/branch_only/blocked items, succeeds clean, refuses streams entirely (even with `force=True`), release `force=True` overrides + audits, external item doesn't crash blocker scan.
- `TestAudit` — every state-changing operation writes exactly one audit row; query filters work.

Target: **35–45 test cases across 10 classes**.

---

## 10. Naming convention exception (CLAUDE.md domain-prefix rule)

Tool names like `pull_next`, `release_item`, `triage_dismiss`, `mark_branch_only`, `wip_status` lack a domain prefix. CLAUDE.md says "MCP tool functions are prefixed with the domain". I'm taking spec names verbatim because:

1. The spec is approved and lists exact names.
2. Findings already break this rule (`add`, `query`, `categories`, etc.).
3. `pull_next` is the agent-facing primitive — readability matters for tools called dozens of times per sprint.

If the user wants strict prefix compliance, names become `milestone_pull_next` etc. — trivial rename, not a structural change.

---

## 11. Acceptance check (§13 of spec)

After all three commits land, demonstrate by running these in a clean tmp project:

1. `milestone_status(id="release/1.1")` returns snapshot ✓
2. `add(severity="high", category="bug", file="x.py", description=...)` → item appears in `stream/triage` ✓
3. `triage_dismiss(bug_id="CB-N", reason=...)` → status=dismissed in <1s ✓
4. Two agents call `pull_next(agent_id="A", capacity={"large":1,"small":2,"triage":5})` and `pull_next(agent_id="B", capacity=...)` — get different items ✓
5. `milestone_close(id="release/1.1")` with one `branch_only=1` item refuses, names the item + branch ✓
6. `milestone_audit_query(milestone_id="release/1.1")` shows every transition ✓
7. `milestone_add_item(milestone_id="release/1.1", item_kind="bug", item_ref="CB-99999", size="small")` (non-existent) rejected ✓

---

## 12. Decisions made vs spec

| Spec § | Decision | Rationale |
|---|---|---|
| §5.2 | Added `meta_json TEXT` column to `milestone_items` | Required to store `linked_frs` for §7.2 eligibility. Spec listed no field for this; meta_json is the lowest-commitment extension. |
| §6 | Used spec tool names verbatim (no domain prefix on `pull_next` etc.) | See §10 above. |
| §7.1 | Implemented as post-insert hook list in `db.py` | Honors "do not break existing signature". Single 5-line addition to db.py. |
| §7.2 | "Linked FR" = `meta_json.linked_frs` is non-empty AND each FR exists | Concrete interpretation of the spec's narrative rule. |
| §7.5 | Phantom-ID check on `milestone_add_item` and `triage_promote`. Skipped for `item_kind=external`. | External by design has no source-of-truth table. |
| §10 | No migration of existing codesweep items | Spec explicit. |

---

## 13. Commits

- `feat(milestones): phase 1 — schema + CRUD + auto-routing` (~600 LOC + ~250 LOC tests)
- `feat(milestones): phase 2 — triage + pull_next + WIP` (~250 LOC + ~200 LOC tests)
- `feat(milestones): phase 3 — close gate + branch tracking` (~150 LOC + ~150 LOC tests)

Each commit runs lint + full test suite green before landing.

---

## 14. Adversarial Review Corrections (2026-05-11)

Three-agent review (adversary / defender / judge) found 7 FATAL + 10 SERIOUS issues. Judge ruled 6/10 pre-revision, all 17 mandatory fixes folded in above. Key changes from v1 plan:

| # | Where | Fix |
|---|---|---|
| 1 | §4 | `pull_next` cursor-driven loop, not single SELECT |
| 2 | §4 | `BEGIN IMMEDIATE` uses `merge.py:239-289` isolation_level save/restore |
| 3 | §5 | Hook runs **before** `conn.commit()` in `add_finding` AND `batch_add_findings` (atomic) |
| 4 | §5 | Hook schema-probes `sqlite_master` before INSERT (raw connections in `tests/test_sweep.py`) |
| 5 | §1 | `depends_on=("db", "reqs", "blockers")` — pull_next reads blocker rows |
| 6 | §5 | Auto-router uses `INSERT OR IGNORE`; `milestone_move_item` pre-checks target |
| 7 | §5 | `register_post_add_hook(name, fn)` is name-keyed and idempotent |
| 8 | §2 | CHECK constraints on `kind`, `state`, `item_kind`, `size`, `status` |
| 9 | §5 | Auto-router no longer calls `_validate_item_ref` (tautological after move-into-txn) |
| 10 | §9 | `TestPullNext` uses two threads + two file-DB connections |
| 11 | §5 | `security:` category convention documented as new |
| 12 | §3 | `triage_dismiss` propagation branches by `item_kind` |
| 13 | §4, §8 | Blocker checks skipped for `item_kind='external'` |
| 14 | §2.2 | `MILESTONE_ITEM_TERMINAL = frozenset({"done", "dismissed"})` |
| 15 | §8 | Stream-reject is absolute, before any item check or `force` bypass |
| 16 | §3 | Flat CLI names (`milestone-list`, not `milestone list`) |
| 17 | §9 | Test target 35-45 (was 60, over-promise) |

Recommended fixes also applied: `AUTO_ROUTER_ACTOR` constant, partial index on `assigned_agent`, `query_blockers` returns `dict` not list, acceptance examples use kwargs. Agent reaper deferred to v2 (explicit deferral).
