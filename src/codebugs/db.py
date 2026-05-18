"""Database layer — connection, registries, and shared utilities for codebugs.

This module owns infrastructure that all domain modules depend on:
- connect(): opens the SQLite DB and runs registered schema initializers
- register_schema / register_tool_provider / register_cli_provider / register_post_add_hook
- Shared utilities: git_rev_parse, row_to_dict, run_post_add_hooks

It must NOT import domain modules at the top level — domain modules import db.
The single exception is _ensure_modules_loaded(), which triggers module imports
at runtime to populate the registries.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from graphlib import CycleError
from pathlib import Path
from typing import Any

DB_DIR = ".codebugs"
DB_FILE = "findings.db"


# --- Schema registry ---


@dataclass
class SchemaEntry:
    """A registered schema initializer with dependency metadata."""

    name: str
    ensure_fn: Callable[[sqlite3.Connection], None]
    depends_on: tuple[str, ...] = ()


_schema_registry: list[SchemaEntry] = []
_cached_order: list[SchemaEntry] | None = None


def register_schema(
    name: str,
    ensure_fn: Callable[[sqlite3.Connection], None],
    *,
    depends_on: tuple[str, ...] = (),
) -> None:
    """Register a schema initializer. Called at module level by domain modules.

    Invalidates the resolved-order cache so post-load registrations are honored.
    Raises ValueError if name is already registered.
    """
    global _cached_order
    if any(e.name == name for e in _schema_registry):
        raise ValueError(f"Schema '{name}' is already registered")
    _schema_registry.append(SchemaEntry(name, ensure_fn, depends_on))
    _cached_order = None


def _resolve_order() -> list[SchemaEntry]:
    """Topological sort of registered schemas.

    Raises ValueError on cycles or missing dependencies.
    """
    from graphlib import TopologicalSorter

    entries = {e.name: e for e in _schema_registry}
    graph = {e.name: set(e.depends_on) for e in _schema_registry}

    for name, deps in graph.items():
        for dep in deps:
            if dep not in entries:
                raise ValueError(f"Schema '{name}' depends on '{dep}' which is not registered")

    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError(f"Cycle detected among schemas: {exc}") from exc

    return [entries[name] for name in order]


def _resolved_order() -> list[SchemaEntry]:
    """Return cached topological order, computing on first call."""
    global _cached_order
    if _cached_order is None:
        _cached_order = _resolve_order()
    return _cached_order


# --- Tool provider registry ---

ConnFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]


@dataclass
class ToolProvider:
    """A registered tool provider with domain metadata."""

    name: str
    register_fn: Callable  # Callable[[FastMCP, ConnFactory], None]


_tool_providers: list[ToolProvider] = []


def register_tool_provider(
    name: str,
    register_fn: Callable,
) -> None:
    """Register a tool provider. Called at module level by domain modules.

    Raises ValueError if name is already registered.
    """
    if any(p.name == name for p in _tool_providers):
        raise ValueError(f"Tool provider '{name}' is already registered")
    _tool_providers.append(ToolProvider(name, register_fn))


def get_tool_providers(*, mode: str = "all") -> list[ToolProvider]:
    """Return registered tool providers, optionally filtered by mode."""
    _ensure_modules_loaded()
    if mode == "all":
        return list(_tool_providers)
    return [p for p in _tool_providers if p.name == mode]


# --- CLI provider registry ---


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


# --- Post-add hook registry ---


@dataclass
class PostAddHook:
    """A registered post-add hook (fires inside findings.add_finding / batch_add_findings)."""

    name: str
    fn: Callable[[sqlite3.Connection, dict[str, Any]], None]


_post_add_hooks: list[PostAddHook] = []


def register_post_add_hook(
    name: str,
    fn: Callable[[sqlite3.Connection, dict[str, Any]], None],
) -> None:
    """Register a hook that runs for every newly-added finding.

    Hooks run inside the same transaction as the INSERT, before the final commit,
    so the finding row and any hook side-effects land atomically. Name-keyed so
    module re-import is a no-op (matches register_schema discipline).
    """
    if any(h.name == name for h in _post_add_hooks):
        return
    _post_add_hooks.append(PostAddHook(name, fn))


def run_post_add_hooks(conn: sqlite3.Connection, finding: dict[str, Any]) -> None:
    """Invoke every registered hook. Failures are logged but never raised —
    finding creation must always succeed.

    Published seam: called by findings.add_finding / batch_add_findings inside
    the same transaction as the INSERT.
    """
    for hook in _post_add_hooks:
        try:
            hook.fn(conn, finding)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[post-add hook '{hook.name}' failed] {e}\n")


# --- Shared utilities (public, used by ≥2 modules) ---


def git_rev_parse(ref: str, *, silent: bool = False, cwd: str | None = None) -> str | None:
    """Run git rev-parse for a ref. Returns SHA or None if silent and git unavailable.

    Used by provenance (head SHA, file staleness) and merge.py.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", ref],
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL if silent else None,
            cwd=cwd,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        if silent:
            return None
        raise


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a dict, parsing JSON-encoded tags/meta columns if present.

    Defensive: rows from tables without tags/meta columns (e.g. blockers, milestones)
    are returned as plain dicts.
    """
    d = dict(row)
    if "tags" in d:
        d["tags"] = json.loads(d["tags"]) if isinstance(d["tags"], str) else d["tags"]
    if "meta" in d:
        d["meta"] = json.loads(d["meta"]) if isinstance(d["meta"], str) else d["meta"]
    return d


# --- Connection + module loading ---


def _find_db_root(start: str | None = None) -> str | None:
    """Walk up from `start` (default cwd) looking for an existing `.codebugs/`.

    Mirrors git's discovery rules: returns the directory containing `.codebugs/`,
    or None if walking hits a `.git/` (repo root — picking the enclosing repo's
    DB when invoked inside a submodule would be worse than auto-creating) or the
    filesystem root.
    """
    cur = Path(start or os.getcwd()).resolve()
    while True:
        if (cur / DB_DIR).is_dir():
            return str(cur)
        if (cur / ".git").exists():
            return None
        if cur.parent == cur:
            return None
        cur = cur.parent


def _db_path(project_dir: str | None = None) -> str:
    root = project_dir
    if root is None:
        root = _find_db_root() or os.getcwd()
    return os.path.join(root, DB_DIR, DB_FILE)


_modules_loaded = False
_modules_lock = threading.Lock()


def _ensure_modules_loaded() -> None:
    """Import all domain modules so their register_schema() / register_tool_provider() /
    register_cli_provider() calls execute."""
    global _modules_loaded
    if _modules_loaded:
        return
    with _modules_lock:
        if _modules_loaded:
            return
        from codebugs import findings, provenance, reqs, merge, sweep, bench, blockers, milestones  # noqa: F401

        _modules_loaded = True


def connect(project_dir: str | None = None) -> sqlite3.Connection:
    """Open (and initialize) the codebugs database."""
    path = _db_path(project_dir)
    is_new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    if is_new and project_dir is None:
        sys.stderr.write(
            f"codebugs: created fresh .codebugs/ at {path} "
            f"(no existing DB found in current dir or parents up to .git/)\n"
        )

    _ensure_modules_loaded()
    for entry in _resolved_order():
        entry.ensure_fn(conn)

    return conn
