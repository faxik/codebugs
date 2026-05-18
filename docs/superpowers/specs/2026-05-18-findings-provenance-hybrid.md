# Findings + Provenance Module Split — Spec (Post-Review)

**Date:** 2026-05-18
**Status:** Ready for implementation (post-adversarial-review revision)
**Strategy:** Pure file relocation. Preserve every existing Python and MCP name.

## Background

`db.py` today (1373 LOC) tangles three roles:

| Lines | Role | Disposition |
|---|---|---|
| 22–183, 931–963 | Shared infra (schema/tool/CLI registries, post-add hooks, `_ensure_modules_loaded`, `connect`) | **stays** |
| 186–326 | Staleness/provenance (git shell-outs, per-file checks, batched query) | **moves to `provenance.py`** (except `_git_rev_parse`) |
| 328–390, 392–929, 965–1373 | Findings (SCHEMA, migrations, CRUD, query, stats, summary, `_row_to_dict`, `_next_id`, `register_tools`, `register_cli`) | **moves to `findings.py`** (except `_row_to_dict`) |
| 186–204 (`_git_rev_parse`), 1368 (`_row_to_dict`) | Truly-shared utilities | **stay in `db.py`, promoted to public** |

This spec extracts `findings.py` and `provenance.py` from `db.py`. **Public Python names and all MCP tool names are preserved verbatim** — this is a file move, not an API redesign. A follow-up spec may beautify names later; this one does not.

## Hard constraints

1. **Hook atomicity** — post-add hooks must fire in the same transaction as the INSERT, before commit. `milestones.auto_route_finding` depends on this. `batch_add_findings` runs N INSERTs, one bulk SELECT, N hook fires, then exactly ONE `conn.commit()`.
2. **One-way import**: `provenance` imports `findings`; never the reverse.
3. **`db.py` does NOT import domain modules at the top level.** No re-export shims.
4. **Every existing public symbol — Python and MCP — keeps its current name and signature.**
5. **No breaking changes for external consumers.** External MCP clients see byte-identical tool schemas.

## Preserved behaviors (no change required)

- Tests for staleness use real `tmp_path` git repos — no mocking layer added.
- The current MCP `add` default `source="claude"` stays `"claude"`.
- The MCP `query` tool keeps its 15 explicit kwargs (`id, ids, status, severity, category, file, source, tag, meta_key, meta_value, commit, ref, group_by, limit, offset`) and the `if status == "deferred": return blockers.query_deferred_entities(...)` dispatch.

## Module split

### `findings.py` — preserves the historical API

Public names match `db.py` today verbatim:

```python
from __future__ import annotations
from codebugs import db, types

SCHEMA = """CREATE TABLE IF NOT EXISTS findings (...)"""

def ensure_schema(conn): ...

def _next_id(conn): ...                 # findings-only, stays private to findings.py
def _migrate_statuses(conn): ...
def _migrate_findings_add_provenance_columns(conn): ...   # renamed from _migrate_provenance

def add_finding(
    conn, *,
    severity, category, file, description,
    source="human", tags=None, meta=None,
    status="open", reported_at_commit=None, reported_at_ref=None,
) -> dict: ...

def batch_add_findings(conn, findings: list[dict]) -> list[dict]:
    """N INSERTs, one bulk SELECT, N hook fires, exactly ONE commit.
    MUST NOT call add_finding() in a loop (that produces N commits)."""

def update_finding(conn, finding_id, **fields) -> dict: ...
def get_finding(conn, finding_id) -> dict: ...

def query_findings(
    conn, *,
    status=None, severity=None, category=None, file=None,
    tags=None, source=None, ids=None,
    tag=None, meta_key=None, meta_value=None,
    commit=None, ref=None, group_by=None,
    limit=200, offset=0,
) -> dict: ...

def get_stats(conn, *, group_by="severity") -> dict: ...
def get_summary(conn) -> dict: ...
def get_categories(conn) -> list[dict]: ...

def register_tools(mcp, conn_factory): ...        # 8 MCP tools (see below)
def register_cli(sub, commands): ...

# Self-registration at module load:
db.register_schema("findings", ensure_schema)
db.register_tool_provider("findings", register_tools)
db.register_cli_provider("findings", register_cli)
```

**Schema entry name change**: `register_schema("db", ...)` → `register_schema("findings", ...)`. This requires:
- `blockers.py:533` — `depends_on=("db", "reqs")` → `depends_on=("findings", "reqs")`
- `milestones.py:1715` — `depends_on=("db", "reqs", "blockers")` → `depends_on=("findings", "reqs", "blockers")`
- `tests/test_registry.py` — all assertions referencing the literal `"db"` schema name (verify and update lines that match)

### `provenance.py` — pure relocation + public renames for the moved functions

```python
from __future__ import annotations
from codebugs import db, findings   # one-way dep

def file_status(
    *, file_path, reported_at_commit, project_dir=None,
) -> dict:
    """Per-file staleness against a commit. Returns {file_status, reason}.
    Renamed from db._check_file_staleness."""

def check_findings(
    conn, *,
    finding_id=None, project_dir=None,
    status=None, category=None, file=None,
) -> dict:
    """Batched staleness check. Forwards filters to findings.query_findings.
    Renamed from db._staleness_check_impl."""

def head_sha(*, project_dir=None) -> str | None:
    """Current HEAD SHA. Returns None if git unavailable.
    Thin wrapper over db.git_rev_parse('HEAD', silent=True).
    Renamed from db._get_head_sha."""

def register_tools(mcp, conn_factory): ...        # 1 MCP tool: staleness_check
def register_cli(sub, commands): ...

db.register_tool_provider("provenance", register_tools)
db.register_cli_provider("provenance", register_cli)
```

**No schema registration.** Provenance owns no table today. The `reported_at_commit` / `reported_at_ref` columns + `idx_findings_reported_at_ref` index live on the findings table; their migration (`_migrate_findings_add_provenance_columns`) lives in `findings.py`. Rule: **schema ownership follows table ownership, not column-purpose ownership.**

### `db.py` — pure infra + the third bucket (shared utilities)

Keeps:
- `connect()`, `_find_db_root`, `_db_path`, `DB_DIR`, `DB_FILE`
- Registries: `SchemaEntry`/`register_schema`/`_resolve_order`/`_resolved_order`
- `ToolProvider`/`register_tool_provider`/`get_tool_providers`
- `CliProvider`/`register_cli_provider`/`get_cli_providers`
- `PostAddHook`/`register_post_add_hook`/`run_post_add_hooks` *(renamed from `_run_post_add_hooks` — the published seam)*
- `_ensure_modules_loaded` (adds `findings` and `provenance` to the import list)
- **NEW: `git_rev_parse(ref, *, silent=False, cwd=None)`** — promoted from `_git_rev_parse`. Used by `provenance.head_sha`, `provenance.file_status`, and `merge.py:436` (`merge.py` import updates to `db.git_rev_parse`).
- **NEW: `row_to_dict(row)`** — promoted from `_row_to_dict`. Used by `findings.py`, `reqs.py`, `embeddings.py`, `blockers.py`. Eliminates the 4-way duplication and the `blockers.py:479,482` cross-module private reach. Per-module copies are deleted in this PR.

Loses:
- All findings code → `findings.py`
- All staleness/provenance code (except `_git_rev_parse`) → `provenance.py`
- `_row_to_dict` is replaced by the published `db.row_to_dict`; the previously-duplicated copies in `reqs.py`, `embeddings.py`, `blockers.py` are deleted

**Defensive registry patch** (4 lines): `register_schema` and `register_post_add_hook` set `_cached_order = None` if a schema/hook registers after the first `_resolved_order()` call. Production path is safe today (`_ensure_modules_loaded` precedes `_resolved_order` in `connect()`), but the patch removes a test-only foot-gun.

## Hook contract — published seam

`db.run_post_add_hooks(conn, finding)` (no underscore) is the official contract. `findings.add_finding` and `findings.batch_add_findings` call it inside the transaction, after the INSERT(s) and bulk re-SELECT, before commit:

```python
# findings.batch_add_findings (preserves db.py:1080-1119 semantics)
ids = []
for spec in items:
    fid = _next_id(conn)
    conn.execute("INSERT INTO findings ...", (...))
    ids.append(fid)
placeholders = ",".join("?" * len(ids))
rows = conn.execute(f"SELECT * FROM findings WHERE id IN ({placeholders})", ids).fetchall()
finding_dicts = [db.row_to_dict(r) for r in rows]
for fd in finding_dicts:
    db.run_post_add_hooks(conn, fd)
conn.commit()
return finding_dicts
```

The hook *registry* (`register_post_add_hook`) stays in `db.py` — it's infra; moving it to findings would couple infra to a single domain.

## MCP tool shim — preserves all 9 tools

The findings MCP surface today has 9 tools: `add, batch_add, update, query, get, stats, summary, categories, staleness_check`. The split keeps 8 in findings; `staleness_check` migrates to the provenance tool provider.

### `findings.py` register_tools — 8 tools, signatures byte-identical to today

```python
def register_tools(mcp, conn_factory):
    @mcp.tool()
    def add(severity, category, file, description, source="claude",
            tags=None, meta=None, reported_at_commit=None, reported_at_ref=None):
        return findings.add_finding(conn_factory(), severity=severity, ...)

    @mcp.tool()
    def batch_add(findings_payload):
        return findings.batch_add_findings(conn_factory(), findings_payload)

    @mcp.tool()
    def query(id=None, ids=None, status=None, severity=None, category=None,
              file=None, source=None, tag=None, meta_key=None, meta_value=None,
              commit=None, ref=None, group_by=None, limit=200, offset=0):
        # IMPORTANT: deferred-status delegation preserved verbatim
        if status == "deferred":
            from codebugs import blockers
            return blockers.query_deferred_entities(conn_factory(), ...)
        return findings.query_findings(conn_factory(), ...)

    @mcp.tool()
    def get(finding_id): return findings.get_finding(conn_factory(), finding_id)

    @mcp.tool()
    def update(finding_id, **fields):
        return findings.update_finding(conn_factory(), finding_id, **fields)

    @mcp.tool()
    def stats(group_by="severity"):
        return findings.get_stats(conn_factory(), group_by=group_by)

    @mcp.tool()
    def summary(): return findings.get_summary(conn_factory())

    @mcp.tool()
    def categories(): return findings.get_categories(conn_factory())
```

### `provenance.py` register_tools — 1 tool, signature byte-identical to today

```python
def register_tools(mcp, conn_factory):
    @mcp.tool()
    def staleness_check(finding_id=None, status=None, category=None,
                        file=None, project_dir=None):
        return provenance.check_findings(conn_factory(), ...)
```

**Acceptance**: MCP wire-schema for every tool is byte-identical before and after this PR. CI gate: dump tool schemas to JSON pre/post and diff.

## Mode-slug registration

Per CLAUDE.md, every new module must register its mode slug:

- `src/codebugs/server.py:SERVER_NAMES` — add `"provenance": "codeprovenance"` (or equivalent).
- `src/codebugs/cli.py` — extend `--mode` choices to include `"provenance"`.

`findings` mode slug already exists today as `"findings"` and stays unchanged.

## Dependency strategy

| Dependency | Category | Treatment |
|---|---|---|
| `sqlite3.Connection` (passed in) | in-process | No port. `db.connect()` is the factory. |
| Registries in `db.py` | in-process | Plain function calls. `findings.py` and `provenance.py` import `db`. |
| Post-add hooks | in-process callback | `db.run_post_add_hooks` published as the seam. |
| Git (`subprocess`) | local-substitutable | `db.git_rev_parse` is the only published primitive (used by provenance + merge). `provenance.py` makes additional direct `subprocess` calls for `git cat-file`, `git log`, `git diff` — tests use real `tmp_path` repos. No protocol. |
| `findings` ← `provenance` import | one-way | Enforced by hard constraint #2. |
| `db.row_to_dict` | in-process | Published utility shared by findings/reqs/embeddings/blockers. |

## Testing strategy

**Test file disposition** (three distinct operations):

| Today | Becomes | Operation | Approx. callsites to rewrite |
|---|---|---|---|
| `tests/test_db.py` (140 `db.*` refs) | `tests/test_findings.py` + residual `tests/test_db_infra.py` | Split + namespace rewrite. Move findings-CRUD tests; keep `_resolve_order`, `_ensure_modules_loaded`, registry assertions in `test_db_infra.py`. | ~130 findings + ~10 infra |
| `tests/test_staleness.py` (19 private imports + 4 public calls) | `tests/test_provenance.py` | File rename + function rename (`_check_file_staleness` → `provenance.file_status`, `_staleness_check_impl` → `provenance.check_findings`, `_get_head_sha` → `provenance.head_sha`). | 23 |
| `tests/test_blockers.py` (54 `db.*` refs) | unchanged path | In-place namespace updates (`db.add_finding` → `findings.add_finding`). | 54 |
| `tests/test_milestones.py` (79 `db.*` refs + 1 `db._ensure_findings_schema`) | unchanged path | In-place namespace updates. The `_ensure_findings_schema` call becomes `findings.ensure_schema`. | 80 |
| `tests/test_registry.py` | unchanged path | Update schema-name assertions where they reference literal `"db"` (must verify line list during implementation). | tbd, small |

**New boundary tests** (added in this PR):
- `findings.add_finding` + post-add hook fires inside the same transaction (assert milestone routing happens before commit; assert exactly one `conn.commit()` call).
- `findings.batch_add_findings`: N hook fires, one commit. Use a `unittest.mock.patch` on `conn.commit` to count invocations.
- `provenance.check_findings` with `finding_id` vs filter-based vs no-filter.
- MCP wire-schema regression: dump tool schemas pre/post and diff (golden file).

**Test environment**: unchanged — in-memory SQLite for findings logic, `tmp_path` real-git repos for provenance.

## Migration steps

This is a single atomic PR. No multi-release shim, no compatibility window.

1. **Create `src/codebugs/findings.py`** with the relocated code. Keep historical names. Register schema as `"findings"`.
2. **Create `src/codebugs/provenance.py`** with the three renamed functions (`file_status`, `check_findings`, `head_sha`). Import `findings` at top level.
3. **Promote `db._git_rev_parse` → `db.git_rev_parse`** (public). Promote `db._row_to_dict` → `db.row_to_dict` (public). Both stay in `db.py`.
4. **Delete duplicate `_row_to_dict` copies** in `reqs.py`, `embeddings.py`, `blockers.py`. Update those modules to call `db.row_to_dict`.
5. **Rename `db._run_post_add_hooks` → `db.run_post_add_hooks`** (the published seam).
6. **Apply defensive registry patch**: `register_schema` and `register_post_add_hook` invalidate `_cached_order` if called after first resolve.
7. **Update `blockers.py:533` and `milestones.py:1715`** `depends_on` tuples from `("db", ...)` to `("findings", ...)`.
8. **Update `merge.py:436`** to import `from codebugs.db import git_rev_parse` (no underscore).
9. **Update `_ensure_modules_loaded`** to import `findings` and `provenance`.
10. **Add `"provenance"` to `server.py:SERVER_NAMES` and `cli.py:--mode` choices.**
11. **Move tests** per the disposition table above. Split `test_db.py`. Rename `test_staleness.py`. Rewrite namespace references in `test_blockers.py` and `test_milestones.py`. Update `test_registry.py` schema-name assertions.
12. **Add new boundary tests** for hook-firing semantics and MCP wire-schema regression.
13. **Verify**: `uv run python -m pytest tests/ -v` is green; `uv run ruff check src/ tests/` is clean; MCP schema diff is empty.

## Acceptance criteria

- `db.py` ≤ 350 LOC (raised from the original 300 to honestly accommodate the published `git_rev_parse` + `row_to_dict` utilities).
- `findings.py` exposes the historical public API verbatim: `add_finding`, `batch_add_findings`, `update_finding`, `get_finding`, `query_findings`, `get_stats`, `get_summary`, `get_categories`, `ensure_schema`, `SCHEMA`, `register_tools`, `register_cli`.
- `provenance.py` exposes exactly 3 public functions: `file_status`, `check_findings`, `head_sha`. Plus `register_tools` and `register_cli`.
- `findings.py` has 0 imports of `merge`, `blockers`, `milestones`, `sweep`, `bench`, `reqs`, `embeddings`.
- `provenance.py` has exactly 1 same-package domain import: `findings`. Plus `db` for `git_rev_parse`.
- `db.py` has 0 imports from the `codebugs.*` namespace at top level (`_ensure_modules_loaded` exempted — it's the runtime registration trigger).
- No module imports another module's private `_row_to_dict` — all callers use `db.row_to_dict`.
- MCP wire-schema for every tool is byte-identical before and after (CI gate).
- `milestones.auto_route_finding` continues to fire inside the same transaction as `findings.add_finding` and `findings.batch_add_findings` (assertion test included).
- `tests/test_milestones.py::test_batch_add_routes_each` (or equivalent) passes — per-row hook firing in batches preserved.

---

## Appendix: Adversarial Review Corrections (2026-05-18)

This spec was stress-tested by a 3-agent adversarial review (Adversary / Defender / Judge). The Judge produced 12 mandatory fixes. All 12 are folded into this revision. Summary of what changed from the initial draft:

| Finding | Original draft | Revised |
|---|---|---|
| F1 — re-export shim violates CLAUDE.md | Spec proposed `from codebugs.findings import add_finding` re-exported in `db.py` | **Dropped entirely.** No shim. Single atomic PR rewrites all callsites. |
| F2 + S8 — schema name `"db"` cascade | Silent | Rename to `"findings"`; update `blockers.py:533`, `milestones.py:1715`, `tests/test_registry.py` in same commit. |
| F3 — `add_batch` rename incoherence | Spec proposed `findings.add_batch` while also re-exporting `add_finding` | **Strategy decided**: preserve historical names. `add_finding`, `batch_add_findings`, `query_findings`, etc. all retained. |
| F4 — MCP `add` default regressed | Shim showed `source="human"` | Fixed to `source="claude"` (matches `db.py:402`). |
| F5 — `query` shim `**kw` dropped 15-kwarg signature + deferred dispatch | `def query(**kw)` | Full explicit 15-kwarg signature preserved; `if status == "deferred"` blockers dispatch retained. |
| F6 — shim omitted `batch_add` + `staleness_check` | 7 tools listed | All 9 preserved. `batch_add` stays in findings register_tools; `staleness_check` migrates to provenance register_tools. |
| F7 — `_git_rev_parse` shared with `merge.py:436` | Spec moved all git to provenance | Promoted to public `db.git_rev_parse`, stays in `db.py`. Only staleness-specific git logic moves to provenance. |
| F8 — private-name imports in tests | Spec hand-waved "move tests" | Enumerated: ~287 test callsites across 5 files, three distinct rewrite operations (split, file-rename+function-rename, in-place namespace updates). |
| S1 — `_build_where` unification claim FALSE | Spec claimed 4 functions share filter logic | **Claim dropped.** Verified: only `query_findings` accepts filter kwargs today (`get_stats` accepts only `group_by`; `get_summary` and `get_categories` accept none). The "deepening" framing was fabricated. Spec no longer claims it. |
| S2 — `batch_add_findings` hook semantics undocumented | Silent on per-row vs. per-batch | Documented: N INSERTs, one bulk SELECT, N hook fires, ONE commit. Acceptance test counts `conn.commit` invocations. |
| S3 — `_row_to_dict` 4-way duplication | Unaddressed; would have broken `blockers.py:479,482` after move | Promoted to public `db.row_to_dict`. All four duplicate copies deleted. Cross-module private reach eliminated. |
| S4 — `provenance` mode slug missing | Silent | Added to `server.py:SERVER_NAMES` and `cli.py --mode` allowlist. |
| S5 — `_cached_order` stale-window | Risk noted | Defensive 4-line patch: invalidate cache when schema/hook registers after first resolve. |
| S6 — `_migrate_provenance` ownership mismatch | Spec acknowledged but didn't resolve | Renamed `_migrate_findings_add_provenance_columns`. Rule documented: schema ownership follows table ownership. |
| S7 — test-rewrite scope hand-waved | "Move tests" | Three operations enumerated with callsite counts (140/79/54/19+4). `tests/test_db.py` splits into `test_findings.py` + `test_db_infra.py` to preserve registry tests. |
| W1 — naming drift in prose | Spec drifted between `findings.add` and `add_finding` | Strategy locked: historical names everywhere. |
| W2 — LOC budget math hidden | "~250 LOC" without accounting | Budget raised to ≤ 350 LOC honestly, accommodating published `git_rev_parse` + `row_to_dict`. |
| W3 — "delete or move" ambiguous | One line | Explicit table of test-file dispositions. |
| W4 — keyword/positional discipline | Not declared | Provenance public API switched to keyword-only after `conn`/required first arg, matching project convention. |
| W5 — no measurement plan | Only LOC | Acceptance criteria now include: import-fence checks, MCP wire-schema diff CI gate, hook-firing assertion tests, zero private-`_row_to_dict` reaches. |
| N1–N4 | Style/wording nits | All addressed in this revision. |

**Strategic choice locked**: file-relocation, not API beautification. Historical Python names preserved. MCP wire-schema preserved verbatim. A follow-up spec may rename later; this one does not.

**Reframing of the deepening claim**: the original "deep module" justification was filter unification across `query_findings`/`get_stats`/`get_summary`/`get_categories`. Adversarial review verified that claim is false — only `query_findings` accepts filter kwargs today. The genuine architectural wins delivered by this revision are:

1. **`db.py` shrinks** from 1373 LOC to ≤ 350 LOC of pure infra + shared utilities.
2. **The `findings ↔ provenance ↔ db` boundary is clean**, with one-way imports and an explicit "shared utility" bucket published in `db.py`.
3. **Cross-module private reaches eliminated** — `blockers.py:479,482` no longer reach `db._row_to_dict` and `reqs._row_to_dict`; everyone uses `db.row_to_dict`.
4. **The post-add hook seam is published** as `db.run_post_add_hooks`, no longer a private call.
5. **Provenance gets its own MCP mode slug**, callable in isolation.

These are real, smaller-than-originally-claimed wins. Worth doing. Not transformational.
