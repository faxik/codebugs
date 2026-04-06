# ARCH-004: CLI Unification

**Date:** 2026-04-06
**Status:** Design approved
**Requirement:** ARCH-004
**Depends on:** ARCH-002 (tool registry pattern)

## Problem

After ARCH-002, MCP tool registration is fully decentralized — each domain module owns its tools. But CLI commands are still centralized in `cli.py` (974 lines, 27 handlers, 5 registration functions). Adding a new module still requires editing cli.py. The blockers domain has no CLI support at all.

## Design

### CLI Provider Registry API (`db.py`)

Parallel to `ToolProvider`:

```python
@dataclass
class CliProvider:
    """A registered CLI command provider."""
    name: str
    register_fn: Callable  # Callable[[argparse subparser, dict], None]

_cli_providers: list[CliProvider] = []

def register_cli_provider(name: str, register_fn: Callable) -> None:
    """Register a CLI command provider. Called at module level by domain modules."""
    ...

def get_cli_providers(*, mode: str = "all") -> list[CliProvider]:
    """Return registered CLI providers, optionally filtered by mode."""
    ...
```

### Domain module convention

Each domain module defines `register_cli(sub, commands)` that sets up argparse subcommands and adds handler functions to the `commands` dict:

```python
# bench.py
def register_cli(sub, commands):
    p = sub.add_parser("bench-import", help="Import benchmark results from CSV/JSON")
    p.add_argument("file", nargs="?", help="CSV or JSON file path")
    ...
    commands["bench-import"] = _cmd_bench_import

def _cmd_bench_import(args):
    conn = db.connect()
    ...

register_cli_provider("bench", register_cli)
```

CLI handler functions are module-private (`_cmd_*`) since they're only called via the commands dict.

### What moves where

| Current (cli.py) | New location | Lines moved |
|---|---|---|
| `_register_findings_subcommands` + 8 `cmd_*` handlers | `db.py` | ~240 |
| `_register_reqs_subcommands` + 8 `cmd_reqs_*` handlers | `reqs.py` | ~190 |
| `_register_merge_subcommands` + 4 `cmd_merge_*` handlers | `merge.py` | ~85 |
| `_register_sweep_subcommands` + 7 `cmd_sweep_*` handlers | `sweep.py` | ~165 |
| `_register_bench_subcommands` + 4 `cmd_bench_*` handlers | `bench.py` | ~115 |
| (new) blockers CLI commands | `blockers.py` | ~60 new |
| `_format_table()` | `cli.py` (stays) | ~25 |
| `main()` | `cli.py` (rewritten) | ~35 |

### CLI utilities that stay in cli.py

- `_format_table(rows, columns, max_widths)` — ASCII table formatting for human output
- Domain modules that need table formatting import it: `from codebugs.cli import _format_table`

Wait — this creates a circular import risk (cli imports domain modules, domain modules import cli). Instead, extract `_format_table` to a small `cli_utils.py` or inline it where used. Given it's only used by findings and reqs query handlers, the simplest approach: **move `_format_table` into `db.py`** (it's used by findings query) and have reqs import it from there. Or better: make it a module-level function in a tiny `_fmt.py`.

**Decision**: Keep `_format_table` in `cli.py`. Domain modules that need it call it via a local helper that formats output — they don't import from cli. The handlers receive `args` and print directly, same as today. No circular import because domain modules don't import from cli — the handlers just use stdlib `print()`.

Actually, re-reading the current code: `_format_table` is only called inside `cmd_query` (findings) and `cmd_reqs_query` (reqs). When these handlers move into `db.py` and `reqs.py` respectively, they need access to `_format_table`. **Solution: pass it as a parameter or move it to a shared utility.** Simplest: put it in `types.py` (it has no deps) or a new `_fmt.py`. Since it's ~25 lines and only used by 2 handlers, just duplicate it in each module that needs it. But that violates DRY.

**Final decision: Create `src/codebugs/fmt.py`** — a tiny formatting utilities module (~25 lines). Domain modules import from it. No circular import risk (fmt.py imports nothing from codebugs).

### What cli.py becomes

```python
"""Codebugs CLI — command-line interface."""
import argparse
import sys
from codebugs import db

def main():
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--mode", default="all", ...)
    pre_args, _ = pre_parser.parse_known_args()

    parser = argparse.ArgumentParser(description="Codebugs CLI")
    parser.add_argument("--mode", default="all", ...)
    sub = parser.add_subparsers(dest="command")
    commands = {}

    for provider in db.get_cli_providers(mode=pre_args.mode):
        provider.register_fn(sub, commands)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    commands[args.command](args)
```

~30 lines. Same pattern as server.py.

### New: Blockers CLI commands

Add basic CLI for the blockers domain (closing known debt):
- `blockers-add` — add a blocker
- `blockers-query` — list blockers
- `blockers-check` — scan for actionable items
- `blockers-resolve` — cancel or resolve a blocker

### What does NOT change

- All CLI command names and arguments — unchanged
- CLI output format — unchanged
- `--mode` flag behavior — unchanged
- MCP tools — unaffected
- Domain module public APIs — unaffected

## Testing strategy

### No existing CLI tests

There are currently no CLI tests. This migration doesn't add them either (out of scope — cli.py is a thin presentation layer). The domain module tests cover all business logic.

### New registry tests

Add to `test_registry.py`:
- `TestCliProviderRegistry` — registration, duplicate detection
- `TestAllCliProvidersRegistered` — all 6 domains registered

### Migration safety

Each domain can be migrated independently (same pattern as ARCH-002):
1. Move handlers from cli.py to domain module
2. Add `register_cli_provider()` call
3. Remove from cli.py
4. Run full test suite

## Execution order

1. Add `CliProvider` registry API + `fmt.py` to `db.py`
2. Migrate bench CLI (simplest, 4 commands)
3. Migrate sweep, merge CLI
4. Migrate reqs CLI (uses `_format_table`)
5. Migrate findings CLI (uses `_format_table`, largest)
6. Add blockers CLI (new)
7. Rewrite cli.py main() to use registry
8. Cleanup and documentation
