# ARCH-004: CLI Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move CLI command handlers from the central cli.py into domain modules so each module owns its schema, MCP tools, AND CLI commands.

**Architecture:** A `CliProvider` registry in `db.py` (parallel to `ToolProvider`). Each domain module defines `register_cli(sub, commands)` and calls `register_cli_provider()`. `cli.py` discovers providers and dispatches. New `fmt.py` holds shared formatting utilities.

**Tech Stack:** Python 3.11+, argparse

**Spec:** `docs/superpowers/specs/2026-04-06-cli-unification-design.md`

---

### Task 1: Add CliProvider registry + fmt.py

**Files:**
- Modify: `src/codebugs/db.py` (add CliProvider registry API)
- Create: `src/codebugs/fmt.py` (shared formatting utility)
- Modify: `tests/test_registry.py` (add TestCliProviderRegistry)

- [ ] **Step 1: Add CliProvider registry to db.py**

After the existing `get_tool_providers` function, add:

```python
@dataclass
class CliProvider:
    """A registered CLI command provider."""
    name: str
    register_fn: Callable  # Callable[[argparse subparser, dict], None]


_cli_providers: list[CliProvider] = []


def register_cli_provider(name: str, register_fn: Callable) -> None:
    """Register a CLI command provider. Called at module level by domain modules."""
    if any(p.name == name for p in _cli_providers):
        raise ValueError(f"CLI provider '{name}' is already registered")
    _cli_providers.append(CliProvider(name, register_fn))


def get_cli_providers(*, mode: str = "all") -> list[CliProvider]:
    """Return registered CLI providers, optionally filtered by mode."""
    _ensure_modules_loaded()
    if mode == "all":
        return list(_cli_providers)
    return [p for p in _cli_providers if p.name == mode]
```

- [ ] **Step 2: Create `src/codebugs/fmt.py`**

Move `_format_table` from cli.py into a new shared module:

```python
"""Shared formatting utilities for CLI output."""

from __future__ import annotations


def format_table(rows: list[dict], columns: list[str], max_widths: dict | None = None) -> str:
    """Format rows as an ASCII table with optional column width limits."""
    # Copy exact implementation from cli.py:14-40
    ...
```

The implementer must read and copy the exact `_format_table` function from `cli.py:14-40`. Rename to `format_table` (public, no underscore — it's now a shared API).

- [ ] **Step 3: Add registry tests**

Add to `tests/test_registry.py`:

```python
from codebugs.db import (
    ...,
    CliProvider, register_cli_provider, _cli_providers,
)

class TestCliProviderRegistry:
    @pytest.fixture(autouse=True)
    def _clean_providers(self):
        original = _cli_providers.copy()
        _cli_providers.clear()
        yield
        _cli_providers.clear()
        _cli_providers.extend(original)

    def test_register_adds_provider(self):
        fn = MagicMock()
        register_cli_provider("test_domain", fn)
        assert len(_cli_providers) == 1
        assert _cli_providers[0].name == "test_domain"

    def test_duplicate_name_raises(self):
        fn = MagicMock()
        register_cli_provider("dup", fn)
        with pytest.raises(ValueError, match="already registered"):
            register_cli_provider("dup", fn)
```

- [ ] **Step 4: Run tests, commit**

Run: `uv run python -m pytest tests/ -v` — all pass.

```bash
git add src/codebugs/db.py src/codebugs/fmt.py tests/test_registry.py
git commit -m "feat(db): add CLI provider registry and fmt.py formatting utility"
```

---

### Task 2: Migrate bench CLI (simplest — 4 commands)

**Files:**
- Modify: `src/codebugs/bench.py` (add register_cli + handlers)
- Modify: `src/codebugs/cli.py` (remove bench handlers + registration)
- Modify: `tests/test_registry.py` (add smoke test)

- [ ] **Step 1: Move bench CLI into bench.py**

Read the exact code for these functions from `cli.py`:
- `_register_bench_subcommands` (lines 799-833)
- `cmd_bench_import` (lines 672-704)
- `cmd_bench_query` (lines 705-742)
- `cmd_bench_list` (lines 743-779)
- `cmd_bench_delete` (lines 780-797)

Copy them into `bench.py`. Wrap in a `register_cli(sub, commands)` function. Rename handlers to `_cmd_*` (private). Add `register_cli_provider("bench", register_cli)`.

Key changes inside the handlers:
- Replace `bench.function()` calls with direct `function()` calls (same module)
- The `import json` needed by bench query handlers may need to be added to bench.py imports

- [ ] **Step 2: Remove from cli.py**

Delete `_register_bench_subcommands`, `cmd_bench_import`, `cmd_bench_query`, `cmd_bench_list`, `cmd_bench_delete` from cli.py. Remove `bench` from the import line.

Update cli.py `main()` — change the bench mode handling to use:
```python
if pre_args.mode in ("bench", "all"):
    from codebugs.bench import register_cli as bench_cli
    bench_cli(sub, commands)
```

- [ ] **Step 3: Add smoke test, run full suite, commit**

```bash
git commit -m "refactor: migrate bench CLI from cli.py to bench.py"
```

---

### Task 3: Migrate sweep CLI (7 commands)

Same pattern as Task 2. Move from cli.py to sweep.py:
- `_register_sweep_subcommands` (lines 627-666)
- `_parse_tags` helper (lines 502-505) — move alongside sweep handlers or inline
- `cmd_sweep_create` through `cmd_sweep_list` (lines 507-625)

Note: `_parse_tags` is a small helper (`[t.strip() for t in args.tags.split(",")]`) — inline it where used rather than making it shared.

Commit: `git commit -m "refactor: migrate sweep CLI from cli.py to sweep.py"`

---

### Task 4: Migrate merge CLI (4 commands)

Move from cli.py to merge.py:
- `_register_merge_subcommands` (lines 478-496)
- `cmd_merge_sessions` through `cmd_merge_claims` (lines 414-476)

Note: merge handlers use `from codebugs import merge` inline — since they'll be IN merge.py, replace with direct function calls.

Commit: `git commit -m "refactor: migrate merge CLI from cli.py to merge.py"`

---

### Task 5: Migrate reqs CLI (8 commands, uses format_table)

Move from cli.py to reqs.py:
- `_register_reqs_subcommands` (lines 886-938)
- `cmd_reqs_add` through `cmd_reqs_export` (lines 263-412)

Key: `cmd_reqs_query` uses `_format_table` — change to `from codebugs.fmt import format_table`.

Also update `cmd_reqs_stats` and `cmd_reqs_summary` which reference `types.REQUIREMENT_STATUSES` and `types.PRIORITIES` (already imported in reqs.py).

Commit: `git commit -m "refactor: migrate reqs CLI from cli.py to reqs.py"`

---

### Task 6: Migrate findings CLI (8 commands, uses format_table, largest)

Move from cli.py to db.py:
- `_register_findings_subcommands` (lines 836-883)
- `cmd_add` through `cmd_export_csv` (lines 42-258)

Key: `cmd_query` uses `_format_table` — change to `from codebugs.fmt import format_table`.
Also: `cmd_import_csv` and `cmd_export_csv` use `csv` and `json` modules — ensure db.py has these imports (it already has json, add csv).

Commit: `git commit -m "refactor: migrate findings CLI from cli.py to db.py"`

---

### Task 7: Add blockers CLI (new — closing known debt)

**Files:**
- Modify: `src/codebugs/blockers.py` (add register_cli + 4 handlers)
- Modify: `tests/test_registry.py` (add smoke test)

- [ ] **Step 1: Implement blockers CLI commands**

Add `register_cli(sub, commands)` to blockers.py with 4 commands matching the MCP tools:

```python
def register_cli(sub, commands):
    p = sub.add_parser("blockers-add", help="Defer an item by adding a blocker")
    p.add_argument("item_id", help="The blocked entity (e.g. CB-5, FR-012)")
    p.add_argument("reason", help="Why it's blocked")
    p.add_argument("--blocked-by", help="Dependency entity")
    p.add_argument("--trigger-type", choices=["entity_resolved", "date", "manual"])
    p.add_argument("--trigger-at", help="Date for date triggers")
    commands["blockers-add"] = _cmd_blockers_add

    p = sub.add_parser("blockers-query", help="List blockers with filters")
    p.add_argument("--item-id", help="Filter by blocked item")
    p.add_argument("--blocked-by", help="Filter by dependency")
    p.add_argument("--trigger-type", choices=["entity_resolved", "date", "manual"])
    p.add_argument("--active-only", type=bool, default=True)
    commands["blockers-query"] = _cmd_blockers_query

    p = sub.add_parser("blockers-check", help="Scan for actionable items")
    commands["blockers-check"] = _cmd_blockers_check

    p = sub.add_parser("blockers-resolve", help="Cancel or resolve a blocker")
    p.add_argument("blocker_id", type=int, help="Blocker row ID")
    p.add_argument("action", choices=["cancel", "resolve"])
    commands["blockers-resolve"] = _cmd_blockers_resolve
```

Handlers follow the same pattern as other domains: connect, call domain function, print result.

- [ ] **Step 2: Register**

```python
register_cli_provider("blockers", register_cli)
```

- [ ] **Step 3: Run tests, commit**

```bash
git commit -m "feat: add blockers CLI commands (closing known debt)"
```

---

### Task 8: Rewrite cli.py main() to use registry

**Files:**
- Modify: `src/codebugs/cli.py` (rewrite to ~30 lines)

- [ ] **Step 1: Replace cli.py with registry-driven version**

At this point, all handlers and registration functions have been moved. cli.py should only contain the `main()` function:

```python
"""Codebugs CLI — command-line interface."""

from __future__ import annotations

import argparse
import sys

from codebugs import db


def main() -> None:
    """CLI entry point with mode-based command discovery."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "bench", "blockers", "all"],
        default="all",
    )
    pre_args, _ = pre_parser.parse_known_args()

    parser = argparse.ArgumentParser(
        prog="codebugs",
        description="AI-native code finding & requirements tracker",
    )
    parser.add_argument(
        "--mode",
        choices=["findings", "reqs", "merge", "sweep", "bench", "blockers", "all"],
        default="all",
        help="Which commands to expose (default: all)",
    )
    sub = parser.add_subparsers(dest="command")
    commands: dict = {}

    for provider in db.get_cli_providers(mode=pre_args.mode):
        provider.register_fn(sub, commands)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    commands[args.command](args)
```

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m pytest tests/ -v` — all pass.

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check src/codebugs/cli.py`

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(cli): replace cli.py with registry-driven CLI (~40 lines)"
```

---

### Task 9: Add "all providers registered" test + cleanup + docs

**Files:**
- Modify: `tests/test_registry.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add integration test**

```python
class TestAllCliProvidersRegistered:
    @pytest.fixture(autouse=True)
    def _import_all(self):
        import codebugs.reqs  # noqa: F401
        import codebugs.merge  # noqa: F401
        import codebugs.sweep  # noqa: F401
        import codebugs.bench  # noqa: F401
        import codebugs.blockers  # noqa: F401

    def test_all_cli_providers_registered(self):
        from codebugs.db import _cli_providers
        names = {p.name for p in _cli_providers}
        assert names >= {"findings", "reqs", "merge", "sweep", "bench", "blockers"}
```

- [ ] **Step 2: Update CLAUDE.md**

Update architecture section: cli.py is now a thin orchestrator. Remove "No blockers CLI" from known debt.

- [ ] **Step 3: Update ARCH-004 status**

`reqs_update(req_id="ARCH-004", status="Implemented")`

- [ ] **Step 4: Run full suite, ruff, commit and push**

```bash
git commit -m "docs: update CLAUDE.md for ARCH-004 completion"
git push
```
