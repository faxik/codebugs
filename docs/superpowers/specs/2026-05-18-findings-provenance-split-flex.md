# Findings / Provenance split — flexibility-first design

**Goal**: extract `findings.py` (~700 LOC) and `provenance.py` (~270 LOC) out of `db.py` (1373 LOC). Keep `db.py` as pure infra. Design extension points for: more entity kinds, pluggable VCS backends, richer hook lifecycle, composable query filters.

## 1. Interface signatures

### `provenance.py` — VCS port + staleness

```python
# ---- VCS port (the seam) ----
class VCS(Protocol):
    name: str                                              # "git" | "hg" | "none" | "memory"
    def head(self, cwd: str) -> str | None: ...
    def rev_exists(self, ref: str, cwd: str) -> bool: ...
    def files_changed(self, from_ref: str, to_ref: str, path: str, cwd: str) -> list[str]: ...
    def file_exists(self, path: str, cwd: str) -> bool: ...
    def fingerprint(self, path: str, cwd: str) -> str | None: ...   # SHA, mtime, or hash

class GitVCS:  ...      # subprocess git, today's behaviour
class NoneVCS: ...      # never-stale fallback; head() -> None
class MtimeVCS: ...     # fingerprint = mtime; for non-VCS dirs
class MemoryVCS: ...    # test adapter, in-process

_vcs_registry: dict[str, VCS] = {"git": GitVCS(), "none": NoneVCS(), "mtime": MtimeVCS()}
def register_vcs(vcs: VCS) -> None: ...
def detect_vcs(cwd: str) -> VCS:  ...   # tries each registered backend; falls back to NoneVCS

# ---- Public API ----
@dataclass(frozen=True)
class FileStatus:
    file_status: Literal["current", "stale", "deleted", "unknown"]
    reason: str
    head: str | None = None

def check_file(path: str, reported_at: str | None, *, cwd: str, vcs: VCS | None = None) -> FileStatus: ...

def check_findings(
    conn, *, project_dir: str | None = None, vcs: VCS | None = None,
    finding_id: str | None = None, **query_filters,
) -> dict: ...     # query_filters forwarded to findings.query via FilterSpec
```

### `findings.py` — CRUD + composable filters + lifecycle hooks

```python
# ---- Filter composition ----
@dataclass(frozen=True)
class FilterSpec:
    """Renders to SQL fragment + params. Stackable, no kwarg explosion."""
    where_sql: str
    params: tuple
    def __and__(self, other: "FilterSpec") -> "FilterSpec": ...

def f_status(v): ...       # built-ins return FilterSpec
def f_severity(v): ...
def f_category(v): ...
def f_file(v, *, glob=False): ...
def f_tags(v, *, mode="any"): ...      # "any" | "all"
def f_ids(ids): ...
def f_raw(sql, params): ...            # escape hatch

def register_filter(name: str, fn: Callable[..., FilterSpec]) -> None: ...   # third-party filters

# ---- Lifecycle hooks (taxonomy) ----
HookKind = Literal["pre_add", "post_add", "pre_update", "post_update", "pre_query", "post_query"]

@dataclass
class Hook:
    name: str
    kind: HookKind
    fn: Callable
    depends_on: tuple[str, ...] = ()
    fails_open: bool = True   # post_add/post_update default; pre_* default False

def register_hook(name, kind, fn, *, depends_on=(), fails_open=...) -> None: ...
def _run_hooks(kind, conn, payload) -> dict | None: ...   # topo-sorted; pre_* may mutate payload

# back-compat shim:
def register_post_add_hook(name, fn):
    register_hook(name, "post_add", fn)

# ---- CRUD (preserved signatures) ----
def add_finding(conn, *, severity, category, file, description,
                source="human", tags=None, meta=None, status="open",
                reported_at_commit=None, vcs: VCS | None = None) -> dict: ...
def batch_add_findings(conn, findings: list[dict], *, vcs: VCS | None = None) -> list[dict]: ...
def update_finding(conn, finding_id, **fields) -> dict: ...
def get_finding(conn, finding_id) -> dict: ...
def query_findings(conn, *filters: FilterSpec, limit=200, offset=0, order_by="created_at DESC",
                   **legacy_kwargs) -> dict: ...   # kwargs auto-convert to FilterSpec for back-compat
def get_stats(conn, *filters: FilterSpec, **legacy_kwargs) -> dict: ...
def get_summary(conn) -> dict: ...
def get_categories(conn) -> list[dict]: ...
```

`db.py` keeps only: `connect()`, `_next_id()`, registries (`register_schema`/`tool_provider`/`cli_provider`), and `_find_db_root`/`_db_path`. The post-add hook registry moves into `findings.py`; `db.py` re-exports `register_post_add_hook` as a thin alias for migration.

## 2. Usage examples

**(a) Boring default — milestones today:**
```python
from codebugs import findings
findings.register_hook("milestones.route", "post_add", auto_route_finding)
fid = findings.add_finding(conn, severity="high", category="bug", file="x.py", description="...")
```

**(b) Third-party query filter — "findings whose file is in the current PR":**
```python
from codebugs.findings import FilterSpec, query_findings, register_filter

def f_in_pr(branch: str) -> FilterSpec:
    files = subprocess.check_output(["git", "diff", "--name-only", f"main...{branch}"]).split()
    placeholders = ",".join("?" * len(files))
    return FilterSpec(f"file IN ({placeholders})", tuple(files))

register_filter("in_pr", f_in_pr)
query_findings(conn, f_in_pr("feature-x"), f_status("open"))
```

**(c) Provenance against mercurial:**
```python
class HgVCS:
    name = "hg"
    def head(self, cwd): return subprocess.check_output(["hg","id","-i"], cwd=cwd).strip().decode()
    # ... files_changed via `hg status --rev`, etc.

provenance.register_vcs(HgVCS())
provenance.check_findings(conn, project_dir="/repo")   # detect_vcs picks hg
```

## 3. Complexity hidden vs. surfaced

**Hidden**: VCS shell-outs behind `VCS.files_changed`; SQL stringification behind `FilterSpec.__and__`; hook ordering via `depends_on` topo-sort (reuses `db._resolve_order`'s pattern); back-compat kwarg-to-filter shim.

**Surfaced**: three new registries (filters, VCS, hooks-by-kind); two ways to query (kwargs + filters); hook failure semantics differ per kind (`fails_open`); plugin authors must know which kind to register.

## 4. Dependency strategy

- **VCS port**: `Protocol`, not ABC — duck-typed, easy to mock. `MemoryVCS(head="abc", changed={"x.py":["abc..HEAD"]})` replaces git for tests. `test_staleness.py`'s real-git fixtures stay green via `GitVCS` default; new tests use `MemoryVCS` and skip the `tmp_path + git init` dance.
- **Hook registry**: dict keyed by `HookKind`; per-kind topo-sort cached. `pre_*` returns mutated payload (or raises to abort); `post_*` is fire-and-log (today's semantics). `depends_on` lets `milestones.route` declare `depends_on=("security.escalate",)`.
- **`db.py`**: imports nothing from `findings`/`provenance`. Hook registry lives in `findings.py`; `db.connect()` still calls `_ensure_modules_loaded()` (unchanged) so `findings.py` self-registers its schema and `milestones` self-registers its hook.

## 5. Trade-offs / YAGNI risks

| Extension point | Risk | Cut if YAGNI bites |
|---|---|---|
| `VCS` port w/ 3 impls | Medium — only git used today. `MtimeVCS`/`NoneVCS` may rot. | Keep `VCS` Protocol + `GitVCS` + `MemoryVCS` (test adapter pays for itself). Drop `MtimeVCS`/`NoneVCS` until a user appears. |
| `FilterSpec` composition | **High** — kwargs cover 95% of queries; nobody composes filters today. | Keep kwargs as primary API. Ship `FilterSpec` only when 2nd caller wants `f_raw`. Until then: leave kwargs, skip the abstraction. |
| `register_filter` registry | High — no caller. | Cut. Third parties can build `FilterSpec` directly without registry. |
| 6-kind hook taxonomy | Medium — only `post_add` has a caller. | Ship `post_add` + `post_update` (mirror symmetry, cheap). Defer `pre_*` and `pre_query`/`post_query` until a use case appears. Keep `HookKind` typed so growth is additive. |
| `depends_on` on hooks | Low — reuses existing topo-sort code. | Keep. |
| `register_vcs` | Low — one-liner, enables (c). | Keep. |

**Honest verdict**: the VCS port and the hook taxonomy are worth the complexity (clear seams, real test wins). `FilterSpec` + `register_filter` is the YAGNI hotspot — design supports it but **MVP ships kwargs-only**, with `FilterSpec` as a documented escape hatch (`f_raw`) reachable from `query_findings` via a single `extra_where: tuple[str, tuple] | None = None` parameter. Promote to full registry only when a second caller materialises.
