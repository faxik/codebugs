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
DOT_GIT = ".git"


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


class DatabaseNotFoundError(RuntimeError):
    """No `.codebugs/` tracker was found, and one must not be created implicitly.

    Auto-creating is what makes a wrong-directory bind silent: the caller gets an
    empty DB instead of an error, and every finding written into it is invisible
    to everyone else. `init_project()` is the only function that may create one.
    """


class TrackerExistsError(RuntimeError):
    """A tracker already covers this directory, so creating another would shadow it.

    `_find_db_root` binds to the NEAREST `.codebugs/`, so a nested tracker
    permanently hides the project's real one from everything beneath it.
    """


class WorktreeTrackerError(TrackerExistsError):
    """Refuses to create a tracker inside a linked git worktree.

    A worktree-local tracker is worse than a nested one: it is deleted with the
    worktree, so every finding filed into it is destroyed rather than merely
    hidden. Subclasses `TrackerExistsError` so existing callers keep catching it.
    """


def _abs_from(base: Path, raw: str) -> Path:
    """Resolve a git pointer path, which may be absolute or relative to `base`."""
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (base / p).resolve()


def _linked_worktree_gitdir(git_file: Path) -> Path | None:
    """Return the gitdir `git_file` points at, but only for a LINKED WORKTREE.

    Both linked worktrees and submodules present `.git` as a file holding
    `gitdir: <path>`, so the pointer must be followed to tell them apart.
    A worktree's gitdir (`<main>/.git/worktrees/<id>`) contains a `commondir`
    file pointing back at the main repo; a submodule's (`<parent>/.git/modules/<name>`)
    does not, and stays a discovery boundary.

    Split out from `_worktree_main_root` because "is this a worktree?" and "where
    is its main checkout?" have different answers: the main checkout is often
    unrecoverable (bare and `--separate-git-dir` mains record no back-pointer),
    but the worktree itself is always recognizable. Creation guards need the
    former question, discovery needs the latter.
    """
    try:
        text = git_file.read_text(errors="replace")
    except OSError:
        return None

    pointer = next(
        (ln[len("gitdir:") :].strip() for ln in text.splitlines() if ln.startswith("gitdir:")),
        "",
    )
    if not pointer:
        return None

    gitdir = _abs_from(git_file.parent, pointer)
    return gitdir if (gitdir / "commondir").is_file() else None


def _worktree_main_root(git_file: Path) -> str | None:
    """Resolve a `.git` FILE to the main repo's root, or None if it can't be known.

    Returning None is common and correct: git records no back-pointer from a
    worktree to its main CHECKOUT, only to the common git DIR. Where that dir is
    not literally `<checkout>/.git` — a bare main, or `--separate-git-dir` — the
    checkout path is genuinely unrecoverable (git's own `worktree list` reports
    the git dir in these layouts), so we refuse rather than guess. Callers that
    only need to know they are IN a worktree must use `_linked_worktree_gitdir`.
    """
    gitdir = _linked_worktree_gitdir(git_file)
    if gitdir is None:
        return None
    try:
        common = (gitdir / "commondir").read_text().strip()
    except OSError:
        return None
    if not common:
        return None

    common_git = _abs_from(gitdir, common)
    if common_git.name != DOT_GIT:
        # Bare or otherwise unconventional main repo — refuse rather than guess.
        return None
    return str(common_git.parent)


def _enclosing_worktree_root(start: str) -> str | None:
    """Return the root of the linked worktree containing `start`, if any.

    Walks up on the same rules as `_find_db_root` so the two agree on where a
    repository begins: a `.git` DIRECTORY is a normal checkout and ends the
    search, a `.git` FILE is a worktree only if it carries a `commondir`.
    """
    cur = Path(start).resolve()
    while True:
        git = cur / DOT_GIT
        if git.is_dir():
            return None
        if git.is_file():
            return str(cur) if _linked_worktree_gitdir(git) is not None else None
        if cur.parent == cur:
            return None
        cur = cur.parent


def _find_db_root(start: str | None = None) -> str | None:
    """Walk up from `start` (default cwd) looking for an existing `.codebugs/`.

    Mirrors git's discovery rules: returns the directory containing `.codebugs/`,
    or None if walking hits a repo boundary or the filesystem root.

    A `.git/` DIRECTORY is a repo root and stops the walk (picking the enclosing
    repo's DB when invoked inside a submodule would be worse than refusing). A
    `.git` FILE is ambiguous: in a linked worktree it points at the main repo, so
    discovery continues from there; in a submodule it stays a boundary.
    """
    cur = Path(start or os.getcwd()).resolve()
    seen: set[Path] = set()
    while cur not in seen:
        seen.add(cur)
        if (cur / DB_DIR).is_dir():
            return str(cur)
        git = cur / DOT_GIT
        if git.is_dir():
            return None
        if git.is_file():
            main_root = _worktree_main_root(git)
            if main_root is None:
                return None
            cur = Path(main_root)
            continue
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _db_path(project_dir: str | None = None) -> str:
    """Locate an EXISTING tracker's DB file. Never invents one.

    A named `project_dir` is not an opt-in to creation: it routinely carries user
    input (`--repo <path>`), where a typo must fail loudly rather than quietly
    become a second, empty tracker.
    """
    if project_dir is None:
        root = _find_db_root()
        if root is None:
            cwd = os.getcwd()
            worktree = _enclosing_worktree_root(cwd)
            if worktree is not None:
                # Never advise `init` here: it would create a tracker that dies
                # with the worktree. Name the main checkout when we can find it.
                main = _worktree_main_root(Path(worktree) / DOT_GIT)
                where = f"in the main checkout ({main})" if main else "in the main checkout"
                raise DatabaseNotFoundError(
                    f"no {DB_DIR}/ found from {cwd}; this is a git worktree ({worktree}) "
                    f"and its main checkout has no tracker either. Run `codebugs init` "
                    f"{where} — a tracker created inside a worktree is deleted with it"
                )
            raise DatabaseNotFoundError(
                f"no {DB_DIR}/ found in {cwd} or any parent; "
                f"run `codebugs init` here, or cd into a project that has one"
            )
    else:
        root = project_dir
        if not os.path.isdir(os.path.join(root, DB_DIR)):
            raise DatabaseNotFoundError(
                f"no {DB_DIR}/ in {root}; "
                f"check the path, or run `codebugs init {root}` to create a tracker there"
            )
    return os.path.join(root, DB_DIR, DB_FILE)


def init_project(project_dir: str | None = None, *, force: bool = False) -> dict[str, Any]:
    """Create a `.codebugs/` tracker in `project_dir` (default cwd).

    The only function that creates a tracker, so that creation is always a
    deliberate act. Idempotent — an existing tracker here is left alone.

    Refuses when an enclosing tracker already covers this directory: a nested
    `.codebugs/` wins the upward walk forever after, silently orphaning every
    finding written beneath it. `force=True` allows the deliberate nested case.
    """
    root = os.path.abspath(project_dir or os.getcwd())
    if not os.path.isdir(root):
        raise ValueError(f"cannot initialize {root}: no such directory")

    tracker_dir = os.path.join(root, DB_DIR)
    if os.path.exists(tracker_dir) and not os.path.isdir(tracker_dir):
        raise ValueError(f"cannot initialize {root}: {tracker_dir} exists and is not a directory")

    if not force:
        enclosing = _find_db_root(root)
        if enclosing is not None and os.path.realpath(enclosing) != os.path.realpath(root):
            raise TrackerExistsError(
                f"{enclosing} already has a {DB_DIR}/ covering {root}; "
                f"initializing here would hide it from everything below. "
                f"Use that tracker, or pass --force to nest deliberately"
            )
        # The check above cannot catch a worktree whose main checkout has no
        # tracker yet: discovery returns None, so nothing looks shadowed. The
        # tracker would still be created inside the worktree and die with it.
        worktree = _enclosing_worktree_root(root)
        if worktree is not None:
            main = _worktree_main_root(Path(worktree) / DOT_GIT)
            where = f" Run it in the main checkout ({main}) instead." if main else ""
            place = (
                f"{root} is a git worktree"
                if os.path.realpath(worktree) == os.path.realpath(root)
                else f"{root} is inside the git worktree {worktree}"
            )
            raise WorktreeTrackerError(
                f"{place}; a tracker created here is deleted along with the worktree, "
                f"taking its findings with it."
                f"{where} Pass --force to accept a worktree-local tracker anyway"
            )

    path = os.path.join(tracker_dir, DB_FILE)
    created = not os.path.exists(path)
    os.makedirs(tracker_dir, exist_ok=True)
    connect(root).close()
    return {"root": root, "path": path, "created": created}


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
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    _ensure_modules_loaded()
    for entry in _resolved_order():
        entry.ensure_fn(conn)

    return conn
