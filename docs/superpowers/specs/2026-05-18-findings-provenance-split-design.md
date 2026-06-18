# Findings + Provenance Module Split — Design Spec

**Date:** 2026-05-18
**Goal:** Extract `findings.py` and `provenance.py` from `db.py` (1373 LOC → ~250 LOC infra).
**North star:** Optimize for the 3 common caller paths. Make them one-liners.

---

## 1. Interface signatures

### `findings.py`

```python
# THE common-case API (positional, smart defaults)
def add(conn, spec: str, description: str, **overrides) -> dict:
    """spec = '<severity> <category> <file>', e.g. 'high security:sqli src/db.py'.
    Auto: source='agent', status='open', reported_at_commit=HEAD, tags=[], meta={}.
    overrides: source, tags, meta, finding_id, reported_at_commit, reported_at_ref."""

def list_open(conn, *, file: str | None = None, severity: str | None = None,
              limit: int = 200) -> list[dict]:
    """Shortcut for the dominant triage query: status='open'."""

def get(conn, finding_id: str) -> dict: ...

# Verbose paths (less common — full power)
def query(conn, *, status=None, severity=None, category=None, file=None,
          tags=None, source=None, ids=None, limit=200, offset=0) -> dict: ...
def update(conn, finding_id: str, **fields) -> dict: ...
def batch_add(conn, findings: list[dict]) -> list[dict]: ...
def stats(conn, **filters) -> dict: ...
def summary(conn) -> dict: ...
def categories(conn) -> list[dict]: ...

# Schema + registry registration (module-level side effects)
SCHEMA = "..."
def ensure_schema(conn): ...
register_schema("findings", ensure_schema)
register_tool_provider("findings", register_tools)
register_cli_provider("findings", register_cli)
```

### `provenance.py`

```python
from codebugs import findings  # one-way dep

def check(conn, *, finding_id: str | None = None, status: str | None = "open",
          category: str | None = None, file: str | None = None,
          project_dir: str | None = None) -> dict:
    """The one-liner: provenance.check(conn) → checks every open finding."""

def file_status(file_path: str, reported_at_commit: str | None,
                project_dir: str | None = None) -> dict:
    """Lower-level: was this file changed since the commit? (renamed from _check_file_staleness.)"""

def head_sha(project_dir: str | None = None) -> str | None: ...

register_schema("provenance", _ensure_provenance_columns)  # additive migration only
register_tool_provider("provenance", register_tools)
register_cli_provider("provenance", register_cli)
```

---

## 2. Usage examples

### (a) The 3 one-liners

```python
# 1. Agent finds a bug
f = findings.add(conn, "high security:sqli src/db.py", "user input untrusted")

# 2. Triage open findings
for f in findings.list_open(conn):
    print(f["id"], f["severity"], f["description"])

# 3. Check staleness after files changed
stale = provenance.check(conn)  # default: all open, current project_dir
```

### (b) Rare path — batch ingest from JSON

```python
import json
findings.batch_add(conn, json.loads(Path("scan.json").read_text()))
```

### (c) Adapting to the 7 frozen MCP tool names

```python
# findings.py — register_tools()
def register_tools(mcp, conn_factory):
    @mcp.tool(name="add")  # name pinned, signature pinned
    def _add(severity, category, file, description, source="human",
             tags=None, meta=None, reported_at_commit=None, reported_at_ref=None):
        return findings.batch_add(conn_factory(), [{...}])[0]  # or call internal
    @mcp.tool(name="query")
    def _query(**kw): return findings.query(conn_factory(), **kw)
    @mcp.tool(name="get")
    def _get(finding_id): return findings.get(conn_factory(), finding_id)
    @mcp.tool(name="update")
    def _update(finding_id, **fields): return findings.update(conn_factory(), finding_id, **fields)
    @mcp.tool(name="stats")    def _stats(**kw): ...
    @mcp.tool(name="summary")  def _summary(): ...
    @mcp.tool(name="categories") def _cats(): ...
```

MCP surface unchanged. The python API gets the one-liner; MCP keeps the explicit keyword schema agents already learned.

---

## 3. Hidden complexity (what makes the one-liner work)

| Default | Source |
|---|---|
| `spec` string parsed into `severity / category / file` (3 whitespace tokens, category may contain `:`) | `findings.add` |
| `source='agent'` (was `'human'`) — Python API caller is almost always the agent itself | `findings.add` |
| `reported_at_commit = provenance.head_sha(cwd)` if unset | `findings.add` — lazy import to keep findings independent at import time |
| `status='open'` always on insert (matches today) | `findings.add` |
| `tags=[]`, `meta={}` defaults | `findings.add` |
| `list_open` = `query(status='open')` with column subset for speed | `findings.list_open` |
| `provenance.check(conn)` defaults to `status='open'`, walks every open finding's file | `provenance.check` |
| Post-add hooks fire inside same txn (unchanged contract) | `findings.add` / `batch_add` |

MCP tools keep their explicit signatures — defaults only apply on the Python side.

---

## 4. Dependency strategy — subprocess git, no adapter

`provenance.py` shells out to `git rev-parse`, `git diff --name-only`, `git log -1` directly via `subprocess.run`. **No adapter, no GitGateway protocol.**

Justification:
- Tests already pass with real git repos (`tests/test_staleness.py`, 147 LOC, `tmp_path` + `subprocess` init). Working.
- The git surface is 4 commands. A `Protocol` wraps 4 lines into 40.
- Mocking git is brittle (you re-implement git semantics in the mock). Real repos catch real bugs.
- The only legitimate adapter trigger would be cross-VCS support (hg, jj). YAGNI: codebugs is git-only by spec.
- Extraction itself is the win — provenance is git-coupled by definition. Hiding that fact serves nothing.

If we ever need a non-git backend, `provenance.file_status()` is the seam: swap one function, not a class hierarchy.

---

## 5. Trade-offs

**Where "smart defaults" bite:**

1. **`spec` string ambiguity** — `findings.add(conn, "high security src/x.py", "...")` works, but `findings.add(conn, "high src/x.py user-input bad", "...")` silently parses `user-input` as the file. **Mitigation:** raise `ValueError` if token 0 isn't in `SEVERITIES` or token 1 doesn't look like a category. Fail loud on malformed spec.

2. **`source='agent'` default flip** — today's `add_finding` defaults to `'human'`. The Python-API default flips because the dominant new caller IS the agent. **Mitigation:** MCP tool keeps `source='human'` default (frozen surface). Only the new Python one-liner flips. Document in module docstring.

3. **Auto HEAD-sha** — silently records git state. If caller is mid-rebase or in a dirty tree, `reported_at_commit` may not match what the user thinks. **Mitigation:** `findings.add(conn, ..., reported_at_commit=None)` explicitly disables; document the auto-capture in docstring + CLAUDE.md.

4. **`provenance.check(conn)` default scope** — walks every open finding. On a 10k-finding DB this is slow. **Mitigation:** the function already supports `file=` / `category=` / `finding_id=` filters; document the scaling note inline.

**Verbose path discovery:** module docstring opens with the 3 one-liners, then "for full control, see `query/update/batch_add/stats`." `help(findings)` surfaces all. The one-liners are sugar over the verbose API — never a parallel implementation.

**Net:** `db.py` drops to ~250 LOC of pure infra. Common path is one line. Power users get the full keyword API unchanged. MCP surface frozen.
